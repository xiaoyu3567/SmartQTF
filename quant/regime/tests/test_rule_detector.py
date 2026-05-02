import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.data.quality import DataQualityIssue, DataQualityIssueCode, KlineQualityReport
from quant.regime import (
    AdxAtrRegimeDetector,
    RegimeThresholdCalibrationFeedback,
    RegimeThresholdConfig,
    RegimeThresholds,
    RuleBasedRegimeDetector,
)
from quant.schemas import FeatureSnapshot, RegimeKind, RegimeSnapshot, TraceContext


def _snapshot(
    values,
    *,
    as_of_timestamp=1710000060,
    source_window_start=None,
    source_window_end=None,
    is_complete_bar=True,
    include_incomplete_last_bar=False,
    symbol="BTCUSDT",
    timeframe="1m",
):
    return FeatureSnapshot(
        snapshot_id="features-001",
        timestamp=1710000060,
        symbol=symbol,
        timeframe=timeframe,
        as_of_timestamp=as_of_timestamp,
        feature_set_id="technical_v1",
        feature_set_version="1.0.0",
        values=values,
        source_window_start=source_window_start,
        source_window_end=source_window_end,
        is_complete_bar=is_complete_bar,
        include_incomplete_last_bar=include_incomplete_last_bar,
        trace=TraceContext(
            run_id="bt-001",
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=as_of_timestamp,
            bar_index=8,
        ),
    )


def test_rule_detector_identifies_trend_from_ma_spread():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(_snapshot({"ma_fast": 103.0, "ma_slow": 100.0}))

    assert regime.regime == RegimeKind.UPTREND_LOW_VOL
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.TREND
    assert regime.metrics["trend_score"] == 0.03
    assert regime.metrics["trend_direction_score"] == 0.03
    assert regime.scores == {
        "trend": 1.0,
        "volatility": 0.0,
        "liquidity_activity": 0.0,
        "orderflow": 0.0,
    }
    assert regime.score_inputs["trend"]["fields"] == {
        "ma_fast": 103.0,
        "ma_slow": 100.0,
    }
    assert regime.score_inputs["liquidity_activity"]["missing"] is True
    assert regime.score_inputs["orderflow"]["missing"] is True
    assert regime.direction == "bullish"
    assert regime.volatility_state == "unknown"
    assert regime.tradability == "observe_only"
    assert regime.reason_codes == [
        "trend_threshold_exceeded",
        "regime_score:trend",
        "regime_score:liquidity_activity:missing",
        "regime_score:orderflow:missing",
    ]
    assert regime.threshold_version == "rule_based_regime:1.0.0:default"
    assert regime.threshold_scope == "default"
    assert regime.trace.run_id == "bt-001"


def test_rule_detector_identifies_volatile_before_trend():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(
        _snapshot({"ma_fast": 103.0, "ma_slow": 100.0, "atr_pct": 0.05})
    )

    assert regime.regime == RegimeKind.UPTREND_HIGH_VOL
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.TREND
    assert regime.scores["trend"] == 1.0
    assert regime.scores["volatility"] == 1.0
    assert regime.direction == "bullish"
    assert regime.volatility_state == "high"
    assert regime.tradability == "tradable"
    assert regime.reason_codes[:2] == [
        "volatility_threshold_exceeded",
        "regime_score:volatility",
    ]


def test_rule_detector_identifies_range_when_thresholds_do_not_fire():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(_snapshot({"ma_fast": 100.2, "ma_slow": 100.0, "atr_pct": 0.01}))

    assert regime.regime == RegimeKind.RANGE_LOW_VOL
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.RANGE
    assert regime.scores["trend"] == pytest.approx(0.2)
    assert regime.scores["volatility"] == pytest.approx(1 / 3)
    assert regime.direction == "neutral"
    assert regime.volatility_state == "low"
    assert regime.tradability == "tradable"
    assert regime.reason_codes[:2] == [
        "no_trend_or_volatility_threshold",
        "regime_score:range",
    ]


def test_rule_detector_preserves_bearish_direction_for_negative_trend():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(_snapshot({"ma_fast": 97.0, "ma_slow": 100.0, "atr_pct": 0.01}))

    assert regime.regime == RegimeKind.DOWNTREND_LOW_VOL
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.TREND
    assert regime.metrics["trend_score"] == 0.03
    assert regime.metrics["trend_direction_score"] == -0.03
    assert regime.scores["trend"] == 1.0
    assert regime.direction == "bearish"
    assert regime.volatility_state == "low"
    assert regime.tradability == "tradable"


def test_rule_detector_marks_missing_regime_inputs_observe_only():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(_snapshot({"placeholder": 1.0}))

    assert regime.regime == RegimeKind.RANGE_LOW_VOL
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.RANGE
    assert regime.direction == "unknown"
    assert regime.volatility_state == "unknown"
    assert regime.tradability == "observe_only"
    assert regime.scores == {
        "trend": 0.0,
        "volatility": 0.0,
        "liquidity_activity": 0.0,
        "orderflow": 0.0,
    }
    assert "regime_score:liquidity_activity:missing" in regime.reason_codes
    assert "regime_score:orderflow:missing" in regime.reason_codes


def test_adx_atr_detector_identifies_trend_range_volatile_deterministically():
    detector = AdxAtrRegimeDetector(
        adx_trend_threshold=25.0,
        atr_pct_volatility_threshold=0.04,
    )

    trend_snapshot = _snapshot(
        {
            "adx": 32.0,
            "atr_pct": 0.015,
            "plus_di": 27.0,
            "minus_di": 11.0,
        }
    )
    trend = detector.detect(trend_snapshot)
    repeated = detector.detect(trend_snapshot)
    ranged = detector.detect(_snapshot({"adx": 14.0, "atr_pct": 0.01}))
    volatile = detector.detect(_snapshot({"adx": 38.0, "atr_pct": 0.065}))

    assert trend == repeated
    assert trend.detector_id == "adx_atr_regime"
    assert trend.regime == RegimeKind.UPTREND_LOW_VOL
    assert RegimeKind(trend.regime).legacy_kind() == RegimeKind.TREND
    assert trend.reason_codes[:2] == ["adx_trend_threshold_exceeded", "regime_score:trend"]
    assert trend.direction == "bullish"
    assert trend.volatility_state == "low"
    assert trend.tradability == "tradable"
    assert trend.metrics["adx"] == 32.0
    assert trend.metrics["atr_pct"] == 0.015
    assert trend.metrics["plus_di"] == 27.0
    assert trend.metrics["minus_di"] == 11.0
    assert trend.scores["trend"] == 1.0
    assert trend.scores["volatility"] == pytest.approx(0.375)
    assert trend.score_inputs["trend"]["fields"]["adx"] == 32.0

    assert ranged.regime == RegimeKind.RANGE_LOW_VOL
    assert RegimeKind(ranged.regime).legacy_kind() == RegimeKind.RANGE
    assert ranged.direction == "neutral"
    assert ranged.volatility_state == "low"
    assert ranged.reason_codes[:2] == [
        "adx_and_atr_thresholds_not_met",
        "regime_score:range",
    ]
    assert ranged.confidence == 0.55

    assert volatile.regime == RegimeKind.RANGE_HIGH_VOL
    assert RegimeKind(volatile.regime).legacy_kind() == RegimeKind.RANGE
    assert volatile.direction == "unknown"
    assert volatile.volatility_state == "high"
    assert volatile.tradability == "observe_only"
    assert volatile.reason_codes[:2] == [
        "atr_volatility_threshold_exceeded",
        "regime_score:volatility",
    ]
    assert volatile.metrics["atr_pct"] == 0.065


def test_adx_atr_detector_preserves_bearish_direction_from_di_fields():
    detector = AdxAtrRegimeDetector(
        adx_trend_threshold=25.0,
        atr_pct_volatility_threshold=0.04,
    )

    regime = detector.detect(
        _snapshot({"adx": 34.0, "atr_pct": 0.018, "plus_di": 9.0, "minus_di": 28.0})
    )

    assert regime.regime == RegimeKind.DOWNTREND_LOW_VOL
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.TREND
    assert regime.direction == "bearish"
    assert regime.volatility_state == "low"
    assert regime.tradability == "tradable"


def test_adx_atr_detector_derives_atr_pct_from_atr_and_close():
    detector = AdxAtrRegimeDetector(
        adx_trend_threshold=25.0,
        atr_pct_volatility_threshold=0.04,
    )

    regime = detector.detect(_snapshot({"adx": 12.0, "atr": 5.0, "close": 100.0}))

    assert regime.regime == RegimeKind.RANGE_HIGH_VOL
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.RANGE
    assert regime.metrics["atr_pct"] == 0.05
    assert regime.direction == "neutral"
    assert regime.volatility_state == "high"


def test_regime_threshold_config_resolves_symbol_timeframe_symbol_timeframe_and_default():
    config = RegimeThresholdConfig(
        threshold_version="regime-thresholds-2026-05-02",
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        default=RegimeThresholds(
            trend_threshold=0.02,
            volatility_threshold=0.08,
        ),
        symbols={
            "ETHUSDT": RegimeThresholds(
                trend_threshold=0.025,
            ),
        },
        timeframes={
            "5m": RegimeThresholds(
                volatility_threshold=0.06,
            ),
        },
        symbol_timeframes={
            "btcusdt:5M": RegimeThresholds(
                trend_threshold=0.03,
                volatility_threshold=0.07,
            ),
        },
    )

    btc = config.resolve(symbol="BTCUSDT", timeframe="5m")
    eth = config.resolve(symbol="ETHUSDT", timeframe="5m")
    sol_5m = config.resolve(symbol="SOLUSDT", timeframe="5m")
    sol_15m = config.resolve(symbol="SOLUSDT", timeframe="15m")

    assert btc.scope == "symbol_timeframe"
    assert btc.thresholds.trend_threshold == 0.03
    assert btc.thresholds.volatility_threshold == 0.07
    assert eth.scope == "symbol"
    assert eth.thresholds.trend_threshold == 0.025
    assert eth.thresholds.volatility_threshold == 0.08
    assert sol_5m.scope == "timeframe"
    assert sol_5m.thresholds.trend_threshold == 0.02
    assert sol_5m.thresholds.volatility_threshold == 0.06
    assert sol_15m.scope == "default"
    assert sol_15m.thresholds.trend_threshold == 0.02
    assert sol_15m.thresholds.volatility_threshold == 0.08

    assert RegimeThresholdConfig.from_payload(config.to_payload()) == config


def test_rule_detector_uses_resolved_thresholds_and_replays_deterministically():
    config = RegimeThresholdConfig(
        threshold_version="regime-thresholds-2026-05-02",
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        default=RegimeThresholds(
            trend_threshold=0.01,
            volatility_threshold=0.03,
        ),
        symbol_timeframes={
            "BTCUSDT:5m": RegimeThresholds(
                trend_threshold=0.03,
                volatility_threshold=0.07,
            ),
        },
    )
    detector = RuleBasedRegimeDetector(
        trend_threshold=0.01,
        volatility_threshold=0.03,
        threshold_config=config,
    )
    snapshot = _snapshot(
        {"return": 0.025, "atr_pct": 0.065},
        timeframe="5m",
    )

    regime = detector.detect(snapshot)
    repeated = detector.detect(snapshot)
    restored = RegimeSnapshot.from_payload(regime.to_payload())

    assert regime == repeated
    assert restored == regime
    assert regime.regime == RegimeKind.RANGE_NORMAL_VOL
    assert regime.direction == "neutral"
    assert regime.volatility_state == "normal"
    assert regime.metrics["trend_threshold"] == 0.03
    assert regime.metrics["volatility_threshold"] == 0.07
    assert regime.metrics["threshold_scope_code"] == 3.0
    assert regime.threshold_version == "regime-thresholds-2026-05-02"
    assert regime.threshold_scope == "symbol_timeframe"
    assert regime.input_refs["threshold_config"]["scope"] == "symbol_timeframe"
    assert regime.input_refs["threshold_config"]["thresholds"]["trend_threshold"] == 0.03


def test_adx_atr_detector_uses_calibrated_thresholds():
    config = RegimeThresholdConfig(
        threshold_version="adx-atr-thresholds-2026-05-02",
        detector_id="adx_atr_regime",
        detector_version="1.0.0",
        default=RegimeThresholds(
            adx_trend_threshold=25.0,
            atr_pct_volatility_threshold=0.04,
        ),
        symbol_timeframes={
            "BTCUSDT:1m": RegimeThresholds(
                adx_trend_threshold=20.0,
                atr_pct_volatility_threshold=0.03,
            ),
        },
    )
    detector = AdxAtrRegimeDetector(threshold_config=config)

    regime = detector.detect(
        _snapshot(
            {
                "adx": 24.0,
                "atr_pct": 0.035,
                "plus_di": 30.0,
                "minus_di": 10.0,
            }
        )
    )

    assert regime.regime == RegimeKind.UPTREND_HIGH_VOL
    assert regime.metrics["adx_trend_threshold"] == 20.0
    assert regime.metrics["atr_pct_volatility_threshold"] == 0.03
    assert regime.threshold_version == "adx-atr-thresholds-2026-05-02"
    assert regime.threshold_scope == "symbol_timeframe"


def test_regime_threshold_config_rejects_invalid_thresholds_and_detector_mismatch():
    with pytest.raises(ValueError, match="trend_threshold"):
        RegimeThresholds(trend_threshold=-0.01)

    config = RegimeThresholdConfig(
        threshold_version="wrong-detector",
        detector_id="other_detector",
        default=RegimeThresholds(trend_threshold=0.01),
    )

    with pytest.raises(ValueError, match="detector_id"):
        RuleBasedRegimeDetector(threshold_config=config)


def test_regime_threshold_feedback_is_manual_candidate_only_by_default():
    feedback = RegimeThresholdCalibrationFeedback(
        feedback_id="feedback-001",
        symbol="BTCUSDT",
        timeframe="5m",
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        current_threshold_version="regime-thresholds-2026-05-02",
        proposed_thresholds=RegimeThresholds(
            trend_threshold=0.028,
            volatility_threshold=0.065,
        ),
        reason_codes=["daily_review:range_false_positive"],
    )

    payload = feedback.to_payload()

    assert payload["requires_manual_approval"] is True
    assert payload["auto_apply"] is False
    assert payload["proposed_thresholds"]["trend_threshold"] == 0.028


def test_regime_scores_use_liquidity_and_orderflow_fields_for_btcusdt_5m_fixture():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(
        _snapshot(
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
                "orderflow.orderbook_imbalance": 0.72,
            }
        )
    )

    assert regime.regime == RegimeKind.UPTREND_HIGH_VOL
    assert regime.direction == "bullish"
    assert regime.volatility_state == "high"
    assert regime.scores["trend"] == 1.0
    assert regime.scores["volatility"] == 1.0
    assert regime.scores["liquidity_activity"] == pytest.approx(0.8)
    assert regime.scores["orderflow"] == pytest.approx(160.0 / 161.0)
    assert regime.score_inputs["trend"]["fields"] == {
        "return": 0.024,
        "ema_spread": 0.024,
        "rsi": 68.0,
    }
    assert regime.score_inputs["volatility"]["fields"]["atr_pct"] == 0.052
    assert regime.score_inputs["liquidity_activity"]["fields"]["volume_z"] == 2.4
    assert regime.score_inputs["orderflow"]["fields"]["orderflow.taker_buy_sell_ratio"] == 9.0
    assert "regime_score:liquidity_activity:missing" not in regime.reason_codes
    assert "regime_score:orderflow:missing" not in regime.reason_codes
    assert len(regime.reasons) == len(regime.reason_codes)
    assert any("Volatility score 0.052 met threshold 0.03" in reason for reason in regime.reasons)
    assert any("Trend evidence was observed" in reason for reason in regime.reasons)
    assert any("fields: return, ema_spread, rsi" in reason for reason in regime.reasons)
    assert any("Volatility was the primary regime driver" in reason for reason in regime.reasons)
    assert any("Liquidity activity evidence was observed" in reason for reason in regime.reasons)
    assert any("Orderflow evidence was observed" in reason for reason in regime.reasons)
    assert "secret" not in " ".join(regime.reasons).lower()
    assert "raw response" not in " ".join(regime.reasons).lower()


def test_regime_scores_degrade_when_orderflow_and_liquidity_are_missing():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(_snapshot({"return": -0.021, "atr_pct": 0.05}))

    assert regime.regime == RegimeKind.DOWNTREND_HIGH_VOL
    assert regime.scores["liquidity_activity"] == 0.0
    assert regime.scores["orderflow"] == 0.0
    assert regime.score_inputs["liquidity_activity"]["missing"] is True
    assert regime.score_inputs["orderflow"]["missing"] is True
    assert "regime_score:liquidity_activity:missing" in regime.reason_codes
    assert "regime_score:orderflow:missing" in regime.reason_codes


def test_regime_quality_gate_outputs_unknown_when_quality_report_failed():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)
    quality_report = KlineQualityReport(
        symbol="BTCUSDT",
        timeframe="1m",
        interval_seconds=60,
        checked_count=3,
        issues=[
            DataQualityIssue(
                code=DataQualityIssueCode.MISSING_KLINE,
                timestamp=1710000000,
                message="Missing kline for expected timestamp",
            )
        ],
    )

    regime = detector.detect(
        _snapshot(
            {"trend_strength": 0.5, "atr_pct": 0.0},
            source_window_start=1709999940,
            source_window_end=1710000060,
        ),
        quality_report=quality_report,
    )

    assert regime.regime == RegimeKind.UNKNOWN
    assert regime.direction == "unknown"
    assert regime.volatility_state == "unknown"
    assert regime.tradability == "avoid"
    assert "regime_quality_gate_blocked" in regime.reason_codes
    assert "quality_report_failed" in regime.reason_codes
    assert "quality_issue:missing_kline" in regime.reason_codes
    assert regime.input_refs["feature_snapshot_id"] == "features-001"
    assert regime.input_refs["quality_report"]["passed"] is False
    assert regime.input_refs["source_window_end"] == 1710000060
    assert regime.source_window_start == 1709999940
    assert regime.source_window_end == 1710000060
    assert len(regime.reasons) == len(regime.reason_codes)
    assert any("quality gate blocked" in reason.lower() for reason in regime.reasons)
    assert any("Quality issue missing_kline" in reason for reason in regime.reasons)


def test_regime_quality_gate_outputs_unknown_for_incomplete_feature_window():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)
    quality_report = KlineQualityReport(
        symbol="BTCUSDT",
        timeframe="1m",
        interval_seconds=60,
        checked_count=3,
        issues=[],
    )

    regime = detector.detect(
        _snapshot(
            {"trend_strength": 0.5, "atr_pct": 0.0},
            is_complete_bar=False,
            include_incomplete_last_bar=True,
            source_window_start=1709999940,
            source_window_end=1710000060,
        ),
        quality_report=quality_report,
    )

    assert regime.regime == RegimeKind.UNKNOWN
    assert regime.direction == "unknown"
    assert regime.volatility_state == "unknown"
    assert regime.tradability == "avoid"
    assert "feature_snapshot_incomplete_bar" in regime.reason_codes
    assert "feature_snapshot_includes_incomplete_last_bar" in regime.reason_codes
    assert regime.input_refs["quality_report"]["passed"] is True


def test_regime_quality_gate_pass_keeps_classification_and_refs():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)
    quality_report = KlineQualityReport(
        symbol="BTCUSDT",
        timeframe="1m",
        interval_seconds=60,
        checked_count=3,
        issues=[],
    )

    regime = detector.detect(
        _snapshot(
            {"trend_strength": 0.5, "atr_pct": 0.0},
            source_window_start=1709999940,
            source_window_end=1710000060,
        ),
        quality_report=quality_report,
    )

    assert regime.regime == RegimeKind.UPTREND_LOW_VOL
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.TREND
    assert regime.direction == "bullish"
    assert regime.volatility_state == "low"
    assert regime.tradability == "tradable"
    assert regime.input_refs["quality_report"]["passed"] is True
    assert regime.input_refs["quality_report_id"].startswith("quality:BTCUSDT:1m:3:")
    assert regime.input_refs["feature_snapshot_id"] == "features-001"


def test_adx_atr_detector_rejects_future_source_window():
    detector = AdxAtrRegimeDetector(
        adx_trend_threshold=25.0,
        atr_pct_volatility_threshold=0.04,
    )
    snapshot = _snapshot(
        {"adx": 30.0, "atr_pct": 0.01},
        source_window_start=1709999940,
        source_window_end=1710000120,
    )

    with pytest.raises(ValueError, match="source_window_end"):
        detector.detect(snapshot)


def test_regime_snapshot_round_trip_and_future_guard():
    regime = RegimeSnapshot(
        regime_id="regime-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000060,
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        regime=RegimeKind.RANGE,
        confidence=0.6,
        reason_codes=["test"],
        metrics={"trend_score": 0.0},
    )

    payload = regime.to_payload()
    restored = RegimeSnapshot.from_payload(payload)

    assert payload["regime"] == "range"
    assert restored == regime

    for field, value in (
        ("direction", "sideways"),
        ("volatility_state", "panic"),
        ("tradability", "auto_trade"),
    ):
        with pytest.raises(ValidationError):
            RegimeSnapshot.from_payload({**payload, field: value})

    try:
        RegimeSnapshot(
            regime_id="regime-002",
            timestamp=1710000060,
            symbol="BTCUSDT",
            timeframe="1m",
            as_of_timestamp=1710000120,
            detector_id="rule_based_regime",
            detector_version="1.0.0",
            regime=RegimeKind.RANGE,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("regime snapshot must not read future data")


def test_regime_snapshot_reasons_align_with_reason_codes_and_reject_sensitive_text():
    base = {
        "regime_id": "regime-reasons-001",
        "timestamp": 1710000060,
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "as_of_timestamp": 1710000060,
        "detector_id": "rule_based_regime",
        "detector_version": "1.0.0",
        "regime": RegimeKind.UPTREND_HIGH_VOL,
        "confidence": 0.75,
        "reason_codes": ["trend_threshold_exceeded"],
        "reasons": ["Trend score met the configured threshold."],
    }

    snapshot = RegimeSnapshot.from_payload(base)

    assert snapshot.reasons == ["Trend score met the configured threshold."]

    with pytest.raises(ValidationError, match="one-to-one"):
        RegimeSnapshot.from_payload(
            {
                **base,
                "reason_codes": [
                    "trend_threshold_exceeded",
                    "regime_score:trend",
                ],
            }
        )

    sensitive_reasons = (
        "Contains api_key value.",
        "Contains OKX secret value.",
        "Contains passphrase value.",
        "Contains raw exchange response.",
        "Contains account_id value.",
    )
    for sensitive_reason in sensitive_reasons:
        with pytest.raises(ValidationError, match="credentials|raw responses"):
            RegimeSnapshot.from_payload({**base, "reasons": [sensitive_reason]})
