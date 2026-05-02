import pytest
from pydantic import ValidationError

from quant.regime import RuleBasedRegimeDetector
from quant.schemas import FeatureSnapshot, PayloadSource, RegimeKind, RegimeSnapshot, TraceContext


def test_regime_detector_emits_replayable_trend_range_and_volatile_snapshots():
    detector = RuleBasedRegimeDetector(trend_threshold=0.02, volatility_threshold=0.04)

    trend = detector.detect(_feature_snapshot({"trend_strength": 0.05, "volatility": 0.01}))
    ranged = detector.detect(_feature_snapshot({"ma_fast": 100.1, "ma_slow": 100.0, "atr_pct": 0.01}))
    volatile = detector.detect(_feature_snapshot({"trend_strength": 0.05, "atr_pct": 0.08}))

    assert trend.regime == _value(RegimeKind.UPTREND_LOW_VOL)
    assert RegimeKind(trend.regime).legacy_kind() == RegimeKind.TREND
    assert trend.reason_codes[:2] == ["trend_threshold_exceeded", "regime_score:trend"]
    assert len(trend.reasons) == len(trend.reason_codes)
    assert "Trend score 0.05 met threshold 0.02" in trend.reasons[0]
    assert trend.metrics["trend_score"] == 0.05
    assert trend.scores == {
        "trend": 1.0,
        "volatility": 0.25,
        "liquidity_activity": 0.0,
        "orderflow": 0.0,
    }
    assert trend.score_inputs["trend"]["fields"]["trend_strength"] == 0.05
    assert trend.score_inputs["volatility"]["fields"]["volatility"] == 0.01
    assert 0.0 <= trend.confidence <= 1.0
    assert trend.trace.run_id == "regime-contract-001"
    assert RegimeSnapshot.from_payload(trend.to_payload()) == trend

    assert ranged.regime == _value(RegimeKind.RANGE_LOW_VOL)
    assert RegimeKind(ranged.regime).legacy_kind() == RegimeKind.RANGE
    assert ranged.reason_codes[:2] == [
        "no_trend_or_volatility_threshold",
        "regime_score:range",
    ]
    assert ranged.confidence == 0.55

    assert volatile.regime == _value(RegimeKind.UPTREND_HIGH_VOL)
    assert RegimeKind(volatile.regime).legacy_kind() == RegimeKind.TREND
    assert volatile.reason_codes[:2] == [
        "volatility_threshold_exceeded",
        "regime_score:volatility",
    ]
    assert volatile.metrics["volatility_score"] == 0.08


def test_regime_detector_falls_back_with_reason_code_when_key_features_are_missing():
    regime = RuleBasedRegimeDetector(trend_threshold=0.02, volatility_threshold=0.04).detect(
        _feature_snapshot({"placeholder": 1.0})
    )

    assert regime.regime == _value(RegimeKind.RANGE_LOW_VOL)
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.RANGE
    assert regime.reason_codes == [
        "no_trend_or_volatility_threshold",
        "regime_score:range",
        "regime_score:liquidity_activity:missing",
        "regime_score:orderflow:missing",
    ]
    assert len(regime.reasons) == len(regime.reason_codes)
    assert "Neither trend nor volatility reached its threshold" in regime.reasons[0]
    assert "Liquidity activity inputs were missing" in regime.reasons[2]
    assert "Orderflow inputs were missing" in regime.reasons[3]
    assert regime.metrics["trend_score"] == 0.0
    assert regime.metrics["volatility_score"] == 0.0
    assert regime.scores == {
        "trend": 0.0,
        "volatility": 0.0,
        "liquidity_activity": 0.0,
        "orderflow": 0.0,
    }


def test_regime_snapshot_rejects_invalid_confidence_and_future_as_of():
    base = _regime_payload()

    with pytest.raises(ValidationError):
        RegimeSnapshot.from_payload({**base, "confidence": 1.01})

    with pytest.raises(ValidationError):
        RegimeSnapshot.from_payload({**base, "as_of_timestamp": base["timestamp"] + 60})


def test_regime_snapshot_validates_direction_volatility_and_tradability_contract():
    snapshot = RegimeSnapshot.from_payload(
        {
            **_regime_payload(),
            "direction": "bearish",
            "volatility_state": "high",
            "tradability": "observe_only",
        }
    )

    payload = snapshot.to_payload()

    assert payload["direction"] == "bearish"
    assert payload["volatility_state"] == "high"
    assert payload["tradability"] == "observe_only"

    for field, value in (
        ("direction", "sideways"),
        ("volatility_state", "panic"),
        ("tradability", "auto_trade"),
    ):
        with pytest.raises(ValidationError):
            RegimeSnapshot.from_payload({**_regime_payload(), field: value})


def test_regime_snapshot_validates_normalized_score_contract():
    snapshot = RegimeSnapshot.from_payload(
        {
            **_regime_payload(),
            "scores": {
                "trend": 0.4,
                "volatility": 0.2,
                "liquidity_activity": 0.0,
                "orderflow": 1.0,
            },
            "score_inputs": {
                "trend": {"fields": {"ema_spread": 0.004}},
                "orderflow": {"fields": {"orderflow.imbalance": 12.0}},
            },
        }
    )

    assert snapshot.scores["orderflow"] == 1.0
    assert snapshot.score_inputs["trend"]["fields"]["ema_spread"] == 0.004

    with pytest.raises(ValidationError):
        RegimeSnapshot.from_payload(
            {
                **_regime_payload(),
                "scores": {
                    "trend": 0.4,
                    "volatility": 0.2,
                    "liquidity_activity": 0.0,
                },
            }
        )

    with pytest.raises(ValidationError):
        RegimeSnapshot.from_payload(
            {
                **_regime_payload(),
                "scores": {
                    "trend": 0.4,
                    "volatility": 1.2,
                    "liquidity_activity": 0.0,
                    "orderflow": 1.0,
                },
            }
        )


def test_regime_snapshot_validates_reasons_contract_and_secret_safety():
    snapshot = RegimeSnapshot.from_payload(
        {
            **_regime_payload(),
            "reason_codes": ["contract"],
            "reasons": ["Regime contract reason is replayable."],
        }
    )

    assert snapshot.to_payload()["reasons"] == ["Regime contract reason is replayable."]

    with pytest.raises(ValidationError):
        RegimeSnapshot.from_payload(
            {
                **_regime_payload(),
                "reason_codes": ["contract", "regime_score:trend"],
                "reasons": ["Only one reason."],
            }
        )

    for sensitive_reason in (
        "api_key appeared in text",
        "credential leaked in text",
        "raw response appeared in text",
        "account id appeared in text",
    ):
        with pytest.raises(ValidationError):
            RegimeSnapshot.from_payload(
                {
                    **_regime_payload(),
                    "reason_codes": ["contract"],
                    "reasons": [sensitive_reason],
                }
            )


def test_regime_snapshot_supports_fine_grained_labels_and_legacy_payloads():
    fine_grained = RegimeSnapshot.from_payload(
        {**_regime_payload(), "regime": "uptrend_high_vol"}
    )

    assert fine_grained.regime == _value(RegimeKind.UPTREND_HIGH_VOL)
    assert fine_grained.to_payload()["regime"] == "uptrend_high_vol"
    assert RegimeKind(fine_grained.regime).legacy_kind() == RegimeKind.TREND
    assert RegimeSnapshot.from_payload(fine_grained.to_payload()) == fine_grained

    for legacy_regime in ("trend", "range", "volatile", "unknown"):
        restored = RegimeSnapshot.from_payload({**_regime_payload(), "regime": legacy_regime})

        assert restored.to_payload()["regime"] == legacy_regime


def _feature_snapshot(values):
    return FeatureSnapshot(
        snapshot_id="features-regime-contract-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000060,
        feature_set_id="technical-contract",
        feature_set_version="1.0.0",
        values=values,
        trace=_trace(),
    )


def _regime_payload():
    return {
        "regime_id": "regime-contract-001",
        "timestamp": 1710000060,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "as_of_timestamp": 1710000060,
        "detector_id": "rule_based_regime",
        "detector_version": "1.0.0",
        "regime": RegimeKind.RANGE,
        "confidence": 0.5,
        "reason_codes": ["contract"],
        "metrics": {"trend_score": 0.0},
        "trace": _trace().to_payload(),
    }


def _trace():
    return TraceContext(
        run_id="regime-contract-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000060,
        bar_index=8,
    )


def _value(value):
    return getattr(value, "value", value)
