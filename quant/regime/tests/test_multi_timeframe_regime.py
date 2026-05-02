import pytest

from quant.regime import MultiTimeframeRegimeDetector
from quant.schemas import (
    FeatureQualityReportRef,
    FeatureSnapshot,
    MultiTimeframeFeatureSnapshot,
    MultiTimeframeRegimeInput,
    MultiTimeframeRegimeSnapshot,
    RegimeKind,
)


SYMBOL = "BTCUSDT"
TIMESTAMP = 1700001200


def _feature(
    timeframe: str,
    values,
    *,
    source_window_end: int = TIMESTAMP,
) -> FeatureSnapshot:
    return FeatureSnapshot(
        snapshot_id=f"{timeframe}-features",
        timestamp=TIMESTAMP,
        symbol=SYMBOL,
        timeframe=timeframe,
        as_of_timestamp=TIMESTAMP,
        feature_set_id="fixture_features",
        feature_set_version="1.0.0",
        values=values,
        source_window_start=TIMESTAMP - 600,
        source_window_end=source_window_end,
        input_bar_count=5,
    )


def _quality_ref(
    timeframe: str,
    *,
    passed: bool = True,
    issue_codes=None,
    fatal_issue_codes=None,
) -> FeatureQualityReportRef:
    return FeatureQualityReportRef(
        timeframe=timeframe,
        passed=passed,
        checked_count=5,
        issue_codes=issue_codes or [],
        fatal_issue_codes=fatal_issue_codes or [],
        first_timestamp=TIMESTAMP - 600,
        last_timestamp=TIMESTAMP,
    )


def _mtf_feature_snapshot(
    *,
    execution_values=None,
    context_values=None,
    quality_overrides=None,
) -> MultiTimeframeFeatureSnapshot:
    snapshots = {
        "5m": _feature("5m", execution_values or _bullish_low_vol()),
    }
    context_values = context_values or {
        "15m": _bullish_low_vol(),
        "1h": _bullish_low_vol(),
        "4h": _bullish_low_vol(),
    }
    for timeframe, values in context_values.items():
        snapshots[timeframe] = _feature(timeframe, values)

    quality_refs = {
        timeframe: _quality_ref(timeframe)
        for timeframe in snapshots
    }
    for timeframe, override in (quality_overrides or {}).items():
        quality_refs[timeframe] = _quality_ref(timeframe, **override)

    return MultiTimeframeFeatureSnapshot(
        snapshot_id="mtf-feature-fixture",
        timestamp=TIMESTAMP,
        symbol=SYMBOL,
        execution_timeframe="5m",
        timeframe_snapshots=snapshots,
        alignment_features={
            "execution_timeframe": "5m",
            "execution_bias": "bullish",
            "higher_timeframe_bias": "bullish",
        },
        quality_report_refs=quality_refs,
    )


def _bullish_low_vol():
    return {"trend_strength": 0.04, "atr_pct": 0.01}


def _bearish_low_vol():
    return {"trend_strength": -0.04, "atr_pct": 0.01}


def _bullish_high_vol():
    return {"trend_strength": 0.04, "atr_pct": 0.05}


def _bullish_extreme_vol():
    return {"trend_strength": 0.04, "atr_pct": 0.07}


def test_multi_timeframe_regime_confirms_aligned_higher_timeframes():
    snapshot = MultiTimeframeRegimeDetector().detect(
        MultiTimeframeRegimeInput(feature_snapshot=_mtf_feature_snapshot())
    )

    assert isinstance(snapshot, MultiTimeframeRegimeSnapshot)
    assert snapshot.symbol == SYMBOL
    assert snapshot.execution_timeframe == "5m"
    assert snapshot.execution_regime.regime == RegimeKind.UPTREND_LOW_VOL
    assert snapshot.aggregate_regime.regime == RegimeKind.UPTREND_LOW_VOL
    assert snapshot.aggregate_regime.tradability == "tradable"
    assert snapshot.tradability == "tradable"
    assert snapshot.higher_timeframe_bias == "bullish"
    assert snapshot.confirmation_timeframes == ["15m", "1h", "4h"]
    assert snapshot.conflict_timeframes == []
    assert snapshot.quality_failed_timeframes == []
    assert "higher_timeframe_confirmed" in snapshot.reason_codes
    assert snapshot.input_refs["multi_timeframe_feature_snapshot_id"] == "mtf-feature-fixture"
    assert snapshot.input_refs["regime_ids"]["5m"] == snapshot.execution_regime.regime_id

    restored = MultiTimeframeRegimeSnapshot.from_payload(snapshot.to_payload())
    assert restored == snapshot


def test_multi_timeframe_regime_downgrades_conflicting_context_to_observe_only():
    snapshot = MultiTimeframeRegimeDetector().detect(
        _mtf_feature_snapshot(
            context_values={
                "15m": _bullish_low_vol(),
                "1h": _bearish_low_vol(),
                "4h": _bullish_low_vol(),
            }
        )
    )

    assert snapshot.execution_regime.direction == "bullish"
    assert snapshot.context_regimes["1h"].direction == "bearish"
    assert snapshot.higher_timeframe_bias == "mixed"
    assert snapshot.confirmation_timeframes == ["15m", "4h"]
    assert snapshot.conflict_timeframes == ["1h"]
    assert snapshot.tradability == "observe_only"
    assert snapshot.aggregate_regime.tradability == "observe_only"
    assert snapshot.aggregate_regime.confidence <= 0.5
    assert "higher_timeframe_conflict" in snapshot.reason_codes


def test_multi_timeframe_regime_avoids_when_context_quality_failed():
    snapshot = MultiTimeframeRegimeDetector().detect(
        _mtf_feature_snapshot(
            quality_overrides={
                "1h": {
                    "passed": False,
                    "issue_codes": ["gap_detected"],
                    "fatal_issue_codes": ["gap_detected"],
                }
            }
        )
    )

    assert snapshot.context_regimes["1h"].regime == RegimeKind.UNKNOWN
    assert snapshot.context_regimes["1h"].tradability == "avoid"
    assert snapshot.quality_failed_timeframes == ["1h"]
    assert snapshot.tradability == "avoid"
    assert snapshot.aggregate_regime.tradability == "avoid"
    assert snapshot.aggregate_regime.confidence <= 0.25
    assert "higher_timeframe_quality_failed" in snapshot.reason_codes
    assert "quality_report_ref" in snapshot.context_regimes["1h"].input_refs


def test_multi_timeframe_regime_high_and_extreme_volatility_downgrade_safely():
    high_vol_snapshot = MultiTimeframeRegimeDetector().detect(
        _mtf_feature_snapshot(
            context_values={
                "1h": _bullish_high_vol(),
            }
        )
    )

    assert high_vol_snapshot.high_volatility_timeframes == ["1h"]
    assert high_vol_snapshot.extreme_volatility_timeframes == []
    assert high_vol_snapshot.tradability == "observe_only"
    assert "higher_timeframe_high_volatility" in high_vol_snapshot.reason_codes

    extreme_vol_snapshot = MultiTimeframeRegimeDetector().detect(
        _mtf_feature_snapshot(
            context_values={
                "4h": _bullish_extreme_vol(),
            }
        )
    )

    assert extreme_vol_snapshot.high_volatility_timeframes == []
    assert extreme_vol_snapshot.extreme_volatility_timeframes == ["4h"]
    assert extreme_vol_snapshot.tradability == "avoid"
    assert "higher_timeframe_extreme_volatility" in extreme_vol_snapshot.reason_codes


def test_multi_timeframe_regime_rejects_future_feature_window():
    future_snapshot = _feature(
        "1h",
        _bullish_low_vol(),
        source_window_end=TIMESTAMP + 60,
    )
    mtf_snapshot = _mtf_feature_snapshot(
        context_values={"1h": _bullish_low_vol()},
    )
    payload = mtf_snapshot.to_payload()
    payload["timeframe_snapshots"]["1h"] = future_snapshot.to_payload()
    mtf_snapshot = MultiTimeframeFeatureSnapshot.from_payload(payload)

    with pytest.raises(ValueError, match="source_window_end must be <= as_of_timestamp"):
        MultiTimeframeRegimeDetector().detect(mtf_snapshot)
