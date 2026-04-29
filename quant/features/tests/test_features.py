import inspect
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.data.schemas.market import Kline, Trade
from quant.features import time_guard
from quant.features.indicators.moving_average import MovingAverage
from quant.features.indicators.orderflow_imbalance import OrderFlowImbalance
from quant.features.indicators.technical import (
    AverageTrueRange,
    ExponentialMovingAverage,
    MovingAverageConvergenceDivergence,
    RelativeStrengthIndex,
)


def test_moving_average_length():
    klines = [
        Kline(timestamp=1, open=1.0, high=1.0, low=1.0, close=1.0, volume=10.0),
        Kline(timestamp=2, open=2.0, high=2.0, low=2.0, close=2.0, volume=10.0),
        Kline(timestamp=3, open=3.0, high=3.0, low=3.0, close=3.0, volume=10.0),
        Kline(timestamp=4, open=4.0, high=4.0, low=4.0, close=4.0, volume=10.0),
    ]

    moving_average = MovingAverage(window=3)
    values = [moving_average.compute(klines, index) for index in range(len(klines))]

    assert len(values) == len(klines)


def test_moving_average_does_not_use_future_data():
    original_klines = [
        Kline(timestamp=1, open=10.0, high=10.0, low=10.0, close=10.0, volume=10.0),
        Kline(timestamp=2, open=20.0, high=20.0, low=20.0, close=20.0, volume=10.0),
        Kline(timestamp=3, open=30.0, high=30.0, low=30.0, close=30.0, volume=10.0),
        Kline(timestamp=4, open=40.0, high=40.0, low=40.0, close=40.0, volume=10.0),
        Kline(timestamp=5, open=50.0, high=50.0, low=50.0, close=50.0, volume=10.0),
    ]
    changed_future_klines = [
        Kline(timestamp=1, open=10.0, high=10.0, low=10.0, close=10.0, volume=10.0),
        Kline(timestamp=2, open=20.0, high=20.0, low=20.0, close=20.0, volume=10.0),
        Kline(timestamp=3, open=30.0, high=30.0, low=30.0, close=30.0, volume=10.0),
        Kline(timestamp=4, open=4000.0, high=4000.0, low=4000.0, close=4000.0, volume=10.0),
        Kline(timestamp=5, open=5000.0, high=5000.0, low=5000.0, close=5000.0, volume=10.0),
    ]

    moving_average = MovingAverage(window=3)
    original_values = [moving_average.compute(original_klines, index) for index in range(len(original_klines))]
    changed_future_values = [
        moving_average.compute(changed_future_klines, index) for index in range(len(changed_future_klines))
    ]

    assert original_values[:3] == changed_future_values[:3]
    assert original_values[0] is None
    assert original_values[1] is None
    assert original_values[2] == 20.0


def test_feature_length_alignment():
    klines = [
        Kline(timestamp=1, open=1.0, high=1.0, low=1.0, close=1.0, volume=10.0),
        Kline(timestamp=2, open=2.0, high=2.0, low=2.0, close=2.0, volume=10.0),
        Kline(timestamp=3, open=3.0, high=3.0, low=3.0, close=3.0, volume=10.0),
        Kline(timestamp=4, open=4.0, high=4.0, low=4.0, close=4.0, volume=10.0),
    ]

    moving_average = MovingAverage(window=3)
    ma = [moving_average.compute(klines, index) for index in range(len(klines))]

    assert len(ma) == len(klines)
    assert ma == [None, None, 2.0, 3.0]


def test_moving_average_time_guard_blocks_future_data_at_index():
    klines = [
        Kline(timestamp=1, open=1.0, high=1.0, low=1.0, close=1.0, volume=10.0),
        Kline(timestamp=2, open=2.0, high=2.0, low=2.0, close=2.0, volume=10.0),
        Kline(timestamp=3, open=3.0, high=3.0, low=3.0, close=3.0, volume=10.0),
        Kline(timestamp=4, open=1000.0, high=1000.0, low=1000.0, close=1000.0, volume=10.0),
    ]

    values = MovingAverage(window=3).compute(klines, 2)

    assert values == 2.0
    assert values != (1.0 + 2.0 + 3.0 + 1000.0) / 4


def test_future_leak_detection():
    klines = [
        Kline(timestamp=1, open=1.0, high=1.0, low=1.0, close=1.0, volume=10.0),
        Kline(timestamp=2, open=2.0, high=2.0, low=2.0, close=2.0, volume=10.0),
        Kline(timestamp=3, open=3.0, high=3.0, low=3.0, close=3.0, volume=10.0),
        Kline(timestamp=4, open=1000.0, high=1000.0, low=1000.0, close=1000.0, volume=10.0),
    ]

    values = MovingAverage(window=3).compute(klines, 2)

    assert values == (1.0 + 2.0 + 3.0) / 3
    assert values != (1.0 + 2.0 + 3.0 + 1000.0) / 4
    assert values != (2.0 + 3.0 + 1000.0) / 3


def test_time_guard_enforced(monkeypatch):
    calls = []
    klines = [
        Kline(timestamp=1, open=1.0, high=1.0, low=1.0, close=1.0, volume=10.0),
        Kline(timestamp=2, open=2.0, high=2.0, low=2.0, close=2.0, volume=10.0),
        Kline(timestamp=3, open=3.0, high=3.0, low=3.0, close=3.0, volume=10.0),
        Kline(timestamp=4, open=1000.0, high=1000.0, low=1000.0, close=1000.0, volume=10.0),
    ]

    def fake_enforce(data, current_index):
        calls.append((data, current_index))
        return data[: current_index + 1]

    monkeypatch.setattr(time_guard.TimeGuard, "enforce", fake_enforce)

    value = MovingAverage(window=3).compute(klines, 2)

    assert value == 2.0
    assert value != (1.0 + 2.0 + 3.0 + 1000.0) / 4
    assert value != (2.0 + 3.0 + 1000.0) / 3
    assert calls == [(klines, 2)]


def test_moving_average_does_not_index_raw_data():
    source = inspect.getsource(MovingAverage.compute)

    assert not re.search(r"(?<!safe_)data\s*\[", source)


def test_orderflow_imbalance_positive():
    trades = [
        Trade(timestamp=1, price=100.0, size=5.0, side="buy"),
        Trade(timestamp=2, price=100.0, size=2.0, side="sell"),
        Trade(timestamp=3, price=100.0, size=1.0, side="buy"),
    ]

    value = OrderFlowImbalance().compute(trades)

    assert value == 4.0


def test_orderflow_imbalance_negative():
    trades = [
        Trade(timestamp=1, price=100.0, size=1.0, side="buy"),
        Trade(timestamp=2, price=100.0, size=3.0, side="sell"),
        Trade(timestamp=3, price=100.0, size=2.0, side="sell"),
    ]

    value = OrderFlowImbalance().compute(trades)

    assert value == -4.0


def test_ema_computes_after_window_and_ignores_future_data():
    klines = [
        Kline(timestamp=1, open=1.0, high=1.0, low=1.0, close=1.0, volume=10.0),
        Kline(timestamp=2, open=2.0, high=2.0, low=2.0, close=2.0, volume=10.0),
        Kline(timestamp=3, open=3.0, high=3.0, low=3.0, close=3.0, volume=10.0),
        Kline(timestamp=4, open=4.0, high=4.0, low=4.0, close=4.0, volume=10.0),
        Kline(timestamp=5, open=1000.0, high=1000.0, low=1000.0, close=1000.0, volume=10.0),
    ]

    ema = ExponentialMovingAverage(window=3)

    assert ema.compute(klines, 1) is None
    assert ema.compute(klines, 2) == 2.0
    assert ema.compute(klines, 3) == 3.0


def test_rsi_handles_gain_loss_window():
    klines = [
        Kline(timestamp=1, open=10.0, high=10.0, low=10.0, close=10.0, volume=10.0),
        Kline(timestamp=2, open=12.0, high=12.0, low=12.0, close=12.0, volume=10.0),
        Kline(timestamp=3, open=11.0, high=11.0, low=11.0, close=11.0, volume=10.0),
        Kline(timestamp=4, open=14.0, high=14.0, low=14.0, close=14.0, volume=10.0),
    ]

    value = RelativeStrengthIndex(window=3).compute(klines, 3)

    assert round(value, 2) == 83.33


def test_rsi_returns_100_when_no_losses():
    klines = [
        Kline(timestamp=1, open=10.0, high=10.0, low=10.0, close=10.0, volume=10.0),
        Kline(timestamp=2, open=11.0, high=11.0, low=11.0, close=11.0, volume=10.0),
        Kline(timestamp=3, open=12.0, high=12.0, low=12.0, close=12.0, volume=10.0),
        Kline(timestamp=4, open=13.0, high=13.0, low=13.0, close=13.0, volume=10.0),
    ]

    value = RelativeStrengthIndex(window=3).compute(klines, 3)

    assert value == 100.0


def test_macd_returns_replayable_components():
    klines = [
        Kline(timestamp=index, open=float(index), high=float(index), low=float(index), close=float(index), volume=10.0)
        for index in range(1, 8)
    ]

    value = MovingAverageConvergenceDivergence(fast_window=2, slow_window=3, signal_window=2).compute(klines, 6)

    assert set(value) == {"macd", "signal", "histogram"}
    assert value["macd"] is not None
    assert value["signal"] is not None
    assert value["histogram"] == value["macd"] - value["signal"]


def test_atr_uses_true_range_and_ignores_future_data():
    klines = [
        Kline(timestamp=1, open=10.0, high=12.0, low=9.0, close=11.0, volume=10.0),
        Kline(timestamp=2, open=11.0, high=15.0, low=10.0, close=14.0, volume=10.0),
        Kline(timestamp=3, open=14.0, high=16.0, low=13.0, close=15.0, volume=10.0),
        Kline(timestamp=4, open=15.0, high=100.0, low=1.0, close=50.0, volume=10.0),
    ]

    value = AverageTrueRange(window=2).compute(klines, 2)

    assert value == 4.0
