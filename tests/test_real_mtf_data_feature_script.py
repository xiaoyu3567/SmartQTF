import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_real_mtf_data_feature import (  # noqa: E402
    _build_downstream_layers,
    _feature_summary,
)


class FakeFeatureSnapshot:
    snapshot_id = "fake-snapshot"
    symbol = "BTC-USDT"
    execution_timeframe = "5m"
    timestamp = 1000
    alignment_features = {
        "execution_timeframe": "5m",
        "execution_bias": "bullish",
        "higher_timeframe_bias": "mixed",
        "computed_timeframe_count": 4,
        "context_timeframe_count": 3,
        "conflict_count": 2,
        "alignment_available": True,
        "timeframe.5m.bias": "bullish",
        "timeframe.15m.bias": "bullish",
        "timeframe.1h.bias": "bearish",
        "timeframe.4h.bias": "bearish",
        "execution_aligned_with_15m": True,
        "execution_aligned_with_1h": False,
        "execution_aligned_with_4h": False,
        "execution_aligned_with_higher_timeframes": False,
    }

    def __init__(self):
        self.timeframe_snapshots = {
            "5m": FakeSnapshot("5m", 1000, 120, 118, True, True, 100.0, 103.0, 101.0, 55.0, 1.2, 0.5, "range", "none"),
            "15m": FakeSnapshot("15m", 900, 120, 118, True, True, 100.0, 102.0, 101.0, 54.0, 1.5, 0.4, "range", "none"),
            "1h": FakeSnapshot("1h", 600, 120, 118, True, True, 98.0, 99.0, 101.0, 50.0, 3.0, -0.2, "range", "none"),
            "4h": FakeSnapshot("4h", 0, 120, 118, True, True, 97.0, 98.0, 101.0, 52.0, 6.0, -0.3, "range", "none"),
        }


class FakeSnapshot:
    def __init__(
        self,
        timeframe,
        timestamp,
        input_bar_count,
        effective_index,
        is_complete_bar,
        skipped_incomplete_last_bar,
        close,
        ma_fast,
        ma_slow,
        rsi,
        atr,
        macd,
        structure_state,
        breakout_direction,
    ):
        self.timeframe = timeframe
        self.timestamp = timestamp
        self.input_bar_count = input_bar_count
        self.effective_index = effective_index
        self.is_complete_bar = is_complete_bar
        self.skipped_incomplete_last_bar = skipped_incomplete_last_bar
        self.values = {
            "close": close,
            "ma_fast": ma_fast,
            "ma_slow": ma_slow,
            "rsi": rsi,
            "atr": atr,
            "macd": macd,
            "market_structure.structure_state": structure_state,
            "market_structure.breakout_direction": breakout_direction,
        }


def test_feature_summary_exposes_feature_timestamp_as_distinct_effective_timestamp():
    summary = _feature_summary(FakeFeatureSnapshot())

    assert summary["timeframes"]["5m"]["feature_timestamp"] == 1000
    assert summary["timeframes"]["5m"]["effective_bar_timestamp"] == 1000
    assert "timestamp" not in summary["timeframes"]["5m"]


def test_regime_uses_range_or_weak_trend_when_ma_direction_conflicts_with_range_structure():
    layers = _build_downstream_layers(FakeFeatureSnapshot(), account_equity=10_000.0)

    regime = layers["regime_layer"]["aggregate_regime"]
    assert regime["direction"] == "bullish"
    assert regime["structure_state"] == "range"
    assert regime["regime"] in {"range", "weak_trend"}


def test_observe_only_route_marks_route_mode_observe_only():
    layers = _build_downstream_layers(FakeFeatureSnapshot(), account_equity=10_000.0)

    route = layers["strategy_route_layer"]
    assert route["route_mode"] == "observe_only"


def test_force_forward_validates_capital_risk_and_dry_run_execution_path():
    layers = _build_downstream_layers(
        FakeFeatureSnapshot(),
        account_equity=10_000.0,
        force_forward_to_capital=True,
    )

    assert layers["decision_layer"]["forward_to_capital_allocation"] is True
    assert layers["decision_layer"]["trade_intent"] is not None
    assert layers["capital_layer"]["status"] == "approved"
    assert layers["risk_layer"]["status"] == "approved"
    assert layers["execution_layer"]["status"] == "dry_run_ready"
    assert layers["execution_layer"]["broker_called"] is False
    assert layers["execution_layer"]["live_orders_sent"] is False
