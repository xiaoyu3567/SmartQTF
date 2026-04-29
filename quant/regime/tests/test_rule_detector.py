import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.regime import RuleBasedRegimeDetector
from quant.schemas import FeatureSnapshot, RegimeKind, RegimeSnapshot, TraceContext


def _snapshot(values):
    return FeatureSnapshot(
        snapshot_id="features-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000060,
        feature_set_id="technical_v1",
        feature_set_version="1.0.0",
        values=values,
        trace=TraceContext(
            run_id="bt-001",
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=1710000060,
            bar_index=8,
        ),
    )


def test_rule_detector_identifies_trend_from_ma_spread():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(_snapshot({"ma_fast": 103.0, "ma_slow": 100.0}))

    assert regime.regime == RegimeKind.TREND
    assert regime.metrics["trend_score"] == 0.03
    assert regime.reason_codes == ["trend_threshold_exceeded"]
    assert regime.trace.run_id == "bt-001"


def test_rule_detector_identifies_volatile_before_trend():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(
        _snapshot({"ma_fast": 103.0, "ma_slow": 100.0, "atr_pct": 0.05})
    )

    assert regime.regime == RegimeKind.VOLATILE
    assert regime.reason_codes == ["volatility_threshold_exceeded"]


def test_rule_detector_identifies_range_when_thresholds_do_not_fire():
    detector = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.03)

    regime = detector.detect(_snapshot({"ma_fast": 100.2, "ma_slow": 100.0, "atr_pct": 0.01}))

    assert regime.regime == RegimeKind.RANGE
    assert regime.reason_codes == ["no_trend_or_volatility_threshold"]


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
