import pytest
from pydantic import ValidationError

from quant.data.quality import DataQualityIssue, DataQualityIssueCode, KlineQualityReport
from quant.data.schemas.market import Kline
from quant.orchestration import PaperTradingOrchestrator
from quant.regime import RuleBasedRegimeDetector
from quant.schemas import (
    FeatureSnapshot,
    PayloadSource,
    PipelineStageStatus,
    RegimeKind,
    RegimeSnapshot,
    RegimeThresholdConfig,
    RegimeThresholds,
    TraceContext,
)
from quant.strategy.router import RegimeStrategyRouter


class _DummyStrategy:
    def __init__(self, strategy_id="dummy_strategy", strategy_version="1.0"):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version

    def generate_signal(self, features, index):
        return None


class _CrossingProvider:
    def get_klines(self, symbol, timeframe):
        closes = [10.0, 9.0, 8.0, 7.0, 12.0]
        return [
            Kline(
                timestamp=1700000000 + index * 60,
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1000.0 + index,
            )
            for index, close in enumerate(closes)
        ]

    def get_trades(self, symbol):
        return []


@pytest.mark.parametrize(
    ("regime", "legacy"),
    [
        (RegimeKind.UPTREND_HIGH_VOL, RegimeKind.TREND),
        (RegimeKind.UPTREND_NORMAL_VOL, RegimeKind.TREND),
        (RegimeKind.UPTREND_LOW_VOL, RegimeKind.TREND),
        (RegimeKind.DOWNTREND_HIGH_VOL, RegimeKind.TREND),
        (RegimeKind.DOWNTREND_NORMAL_VOL, RegimeKind.TREND),
        (RegimeKind.DOWNTREND_LOW_VOL, RegimeKind.TREND),
        (RegimeKind.RANGE_HIGH_VOL, RegimeKind.RANGE),
        (RegimeKind.RANGE_NORMAL_VOL, RegimeKind.RANGE),
        (RegimeKind.RANGE_LOW_VOL, RegimeKind.RANGE),
        (RegimeKind.CHAOS, RegimeKind.VOLATILE),
        (RegimeKind.UNKNOWN, RegimeKind.UNKNOWN),
    ],
)
def test_regime_schema_matrix_covers_fine_labels_and_legacy_replay(regime, legacy):
    snapshot = RegimeSnapshot.from_payload(
        {
            **_regime_payload(),
            "regime": regime.value,
            "direction": "bullish" if regime.value.startswith("uptrend") else "neutral",
            "volatility_state": "high" if "high_vol" in regime.value else "low",
            "tradability": "avoid" if regime == RegimeKind.UNKNOWN else "tradable",
            "reason_codes": ["contract_matrix"],
            "reasons": ["Regime contract matrix entry is replayable."],
        }
    )

    assert RegimeKind(snapshot.regime).legacy_kind() == legacy
    assert RegimeSnapshot.from_payload(snapshot.to_payload()) == snapshot


def test_regime_schema_rejects_missing_scores_bad_explanations_and_old_payloads_survive():
    with pytest.raises(ValidationError):
        RegimeSnapshot.from_payload(
            {
                **_regime_payload(),
                "scores": {
                    "trend": 0.1,
                    "volatility": 0.2,
                    "liquidity_activity": 0.3,
                },
            }
        )

    with pytest.raises(ValidationError):
        RegimeSnapshot.from_payload(
            {
                **_regime_payload(),
                "reason_codes": ["contract_matrix"],
                "reasons": ["raw response contained an account id"],
            }
        )

    restored = RegimeSnapshot.from_payload({**_regime_payload(), "regime": "volatile"})

    assert restored.to_payload()["regime"] == "volatile"
    assert RegimeKind(restored.regime).legacy_kind() == RegimeKind.VOLATILE


@pytest.mark.parametrize(
    ("values", "expected_regime", "direction", "volatility_state", "tradability"),
    [
        (
            {
                "return": 0.024,
                "ema_spread": 0.024,
                "rsi": 68.0,
                "atr_pct": 0.052,
                "volume_z": 2.4,
                "orderflow.buy_volume": 180.0,
                "orderflow.sell_volume": 20.0,
                "orderflow.taker_buy_sell_ratio": 9.0,
                "orderflow.imbalance": 160.0,
            },
            RegimeKind.UPTREND_HIGH_VOL,
            "bullish",
            "high",
            "tradable",
        ),
        (
            {"return": -0.022, "atr_pct": 0.044, "volume_z": 1.2},
            RegimeKind.DOWNTREND_HIGH_VOL,
            "bearish",
            "high",
            "tradable",
        ),
        (
            {"return": 0.002, "atr_pct": 0.006, "volume_z": 0.3},
            RegimeKind.RANGE_LOW_VOL,
            "neutral",
            "low",
            "tradable",
        ),
        (
            {
                "return": 0.002,
                "atr_pct": 0.07,
                "volume_z": 3.0,
                "orderflow.buy_volume": 190.0,
                "orderflow.sell_volume": 10.0,
                "orderflow.orderbook_imbalance": 0.9,
            },
            RegimeKind.CHAOS,
            "neutral",
            "extreme",
            "observe_only",
        ),
    ],
)
def test_rule_detector_fixture_matrix_outputs_labels_scores_refs_and_reasons(
    values,
    expected_regime,
    direction,
    volatility_state,
    tradability,
):
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(_feature_snapshot(values, timeframe="5m"))
    restored = RegimeSnapshot.from_payload(regime.to_payload())

    assert restored == regime
    assert regime.regime == expected_regime
    assert regime.direction == direction
    assert regime.volatility_state == volatility_state
    assert regime.tradability == tradability
    assert set(regime.scores) == {
        "trend",
        "volatility",
        "liquidity_activity",
        "orderflow",
    }
    assert set(regime.score_inputs) == set(regime.scores)
    assert regime.input_refs["feature_snapshot_id"] == "feature-matrix-001"
    assert regime.input_refs["source_window_end"] == 1710000300
    assert len(regime.reasons) == len(regime.reason_codes)
    assert "secret" not in " ".join(regime.reasons).lower()
    assert "raw response" not in " ".join(regime.reasons).lower()


def test_regime_quality_gate_blocks_failed_quality_and_records_lineage():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)
    quality_report = KlineQualityReport(
        symbol="BTCUSDT",
        timeframe="5m",
        interval_seconds=300,
        checked_count=3,
        issues=[
            DataQualityIssue(
                code=DataQualityIssueCode.MISSING_KLINE,
                timestamp=1710000000,
                message="Missing fixture kline",
            )
        ],
    )

    regime = detector.detect(
        _feature_snapshot(
            {"return": 0.08, "atr_pct": 0.01},
            source_window_start=1709999700,
            source_window_end=1710000300,
        ),
        quality_report=quality_report,
    )

    assert regime.regime == RegimeKind.UNKNOWN
    assert regime.direction == "unknown"
    assert regime.volatility_state == "unknown"
    assert regime.tradability == "avoid"
    assert regime.scores == {
        "trend": 0.0,
        "volatility": 0.0,
        "liquidity_activity": 0.0,
        "orderflow": 0.0,
    }
    assert regime.reason_codes[:3] == [
        "regime_quality_gate_blocked",
        "quality_report_failed",
        "quality_issue:missing_kline",
    ]
    assert regime.input_refs["quality_report"]["passed"] is False
    assert regime.input_refs["quality_report"]["issue_codes"] == ["missing_kline"]
    assert regime.source_window_start == 1709999700
    assert regime.source_window_end == 1710000300
    assert any("quality gate blocked" in reason.lower() for reason in regime.reasons)


def test_regime_detector_rejects_future_feature_window_before_classification():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)
    snapshot = _feature_snapshot(
        {"return": 0.08, "atr_pct": 0.01},
        as_of_timestamp=1710000000,
        source_window_end=1710000300,
    )

    with pytest.raises(ValueError, match="source_window_end"):
        detector.detect(snapshot)


def test_regime_threshold_matrix_uses_symbol_timeframe_override_and_fallbacks():
    config = RegimeThresholdConfig(
        threshold_version="regime-thresholds-matrix",
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        default=RegimeThresholds(trend_threshold=0.01, volatility_threshold=0.03),
        symbols={"ETHUSDT": RegimeThresholds(trend_threshold=0.025)},
        timeframes={"15m": RegimeThresholds(volatility_threshold=0.05)},
        symbol_timeframes={
            "BTCUSDT:5m": RegimeThresholds(
                trend_threshold=0.03,
                volatility_threshold=0.07,
            )
        },
    )
    detector = RuleBasedRegimeDetector(threshold_config=config)

    btc = detector.detect(_feature_snapshot({"return": 0.025, "atr_pct": 0.065}, timeframe="5m"))
    eth = detector.detect(
        _feature_snapshot(
            {"return": 0.02, "atr_pct": 0.04},
            symbol="ETHUSDT",
            timeframe="5m",
        )
    )
    sol = detector.detect(
        _feature_snapshot(
            {"return": 0.02, "atr_pct": 0.04},
            symbol="SOLUSDT",
            timeframe="15m",
        )
    )

    assert btc.threshold_scope == "symbol_timeframe"
    assert btc.regime == RegimeKind.RANGE_NORMAL_VOL
    assert btc.metrics["trend_threshold"] == 0.03
    assert btc.metrics["volatility_threshold"] == 0.07

    assert eth.threshold_scope == "symbol"
    assert eth.direction == "neutral"
    assert eth.metrics["trend_threshold"] == 0.025

    assert sol.threshold_scope == "timeframe"
    assert sol.regime == RegimeKind.UPTREND_NORMAL_VOL
    assert sol.metrics["volatility_threshold"] == 0.05


def test_regime_strategy_router_pool_supports_exact_and_legacy_fine_regime_routes():
    exact = _DummyStrategy("exact_uptrend_high_vol", "1.0")
    legacy = _DummyStrategy("legacy_trend_follow", "1.0")
    exact_router = RegimeStrategyRouter({RegimeKind.UPTREND_HIGH_VOL: exact})
    legacy_router = RegimeStrategyRouter({RegimeKind.TREND: legacy})
    snapshot = _regime_snapshot(RegimeKind.UPTREND_HIGH_VOL)

    exact_pool = exact_router.route_pool(snapshot)
    legacy_pool = legacy_router.route_pool(snapshot)

    assert exact_pool.strategies[0].strategy is exact
    assert exact_pool.decision["selected_route"] == "uptrend_high_vol"
    assert exact_pool.decision["legacy_route_used"] is False

    assert legacy_pool.strategies[0].strategy is legacy
    assert legacy_pool.decision["resolved_regime"] == "trend"
    assert legacy_pool.decision["legacy_route_used"] is True
    assert legacy_pool.strategies[0].route.reason_codes == [
        "regime:uptrend_high_vol",
        "route:legacy:trend",
    ]


def test_paper_pipeline_regime_stage_emits_fine_contract_and_legacy_route():
    report = PaperTradingOrchestrator(
        provider=_CrossingProvider(),
        feature_windows=(2, 3),
    ).run_tick(symbol="BTCUSDT", timeframe="1m", index=4, run_id="regime-matrix-pipeline")

    stages = {stage.stage: stage for stage in report.stages}
    regime_payload = stages["regime"].output_payload["regime"]
    route_payload = stages["strategy"].output_payload["route"]

    assert report.success is True
    assert stages["regime"].status == PipelineStageStatus.SUCCEEDED
    assert regime_payload["regime"] == "uptrend_low_vol"
    assert regime_payload["direction"] == "bullish"
    assert regime_payload["tradability"] == "observe_only"
    assert set(regime_payload["scores"]) == {
        "trend",
        "volatility",
        "liquidity_activity",
        "orderflow",
    }
    assert regime_payload["input_refs"]["quality_report"]["passed"] is True
    assert route_payload["regime"] == "uptrend_low_vol"
    assert route_payload["reason_codes"] == ["regime:uptrend_low_vol", "route:legacy:trend"]


def _feature_snapshot(
    values,
    *,
    symbol="BTCUSDT",
    timeframe="5m",
    timestamp=1710000300,
    as_of_timestamp=1710000300,
    source_window_start=1709999700,
    source_window_end=1710000300,
):
    return FeatureSnapshot(
        snapshot_id="feature-matrix-001",
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        as_of_timestamp=as_of_timestamp,
        feature_set_id="regime_contract_matrix",
        feature_set_version="1.0.0",
        values=values,
        source_window_start=source_window_start,
        source_window_end=source_window_end,
        is_complete_bar=True,
        trace=TraceContext(
            run_id="regime-contract-matrix",
            source=PayloadSource.PAPER,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=as_of_timestamp,
            bar_index=42,
        ),
    )


def _regime_snapshot(regime):
    return RegimeSnapshot.from_payload(
        {
            **_regime_payload(),
            "regime": regime.value,
            "direction": "bullish",
            "volatility_state": "high",
            "tradability": "tradable",
            "reason_codes": ["contract_matrix"],
            "reasons": ["Regime contract matrix entry is replayable."],
        }
    )


def _regime_payload():
    return {
        "regime_id": "regime-matrix-001",
        "timestamp": 1710000300,
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "as_of_timestamp": 1710000300,
        "detector_id": "rule_based_regime",
        "detector_version": "1.0.0",
        "regime": "range_low_vol",
        "confidence": 0.6,
        "reason_codes": ["contract_matrix"],
        "reasons": ["Regime contract matrix entry is replayable."],
        "metrics": {"trend_score": 0.0},
        "scores": {
            "trend": 0.0,
            "volatility": 0.0,
            "liquidity_activity": 0.0,
            "orderflow": 0.0,
        },
        "score_inputs": {
            "trend": {"fields": {}, "missing": True},
            "volatility": {"fields": {}, "missing": True},
            "liquidity_activity": {"fields": {}, "missing": True},
            "orderflow": {"fields": {}, "missing": True},
        },
        "trace": {
            "run_id": "regime-contract-matrix",
            "source": "paper",
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "timestamp": 1710000300,
            "bar_index": 42,
        },
    }
