import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.strategy.ma_crossover import MACrossoverStrategy


def test_signal_delay():
    features = {
        "fast_ma": [None, 1.0, 3.0, 4.0],
        "slow_ma": [None, 2.0, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    executions_at_index_2 = strategy.on_bar(features, 2)

    assert executions_at_index_2 == []
    assert strategy.signal_buffer == [
        {
            "signal": "buy",
            "signal_index": 2,
            "execute_index": 3,
        }
    ]

    executions_at_index_3 = strategy.on_bar(features, 3)

    assert executions_at_index_3 == [
        {
            "signal": "buy",
            "signal_index": 2,
            "execute_index": 3,
        }
    ]
    assert strategy.signal_buffer == []


def test_no_same_bar_execution():
    features = {
        "fast_ma": [None, 1.0, 3.0, 4.0],
        "slow_ma": [None, 2.0, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    executions = strategy.on_bar(features, 2)

    assert executions == []
    assert len(strategy.signal_buffer) == 1
    assert strategy.signal_buffer[0]["signal_index"] == 2
    assert strategy.signal_buffer[0]["execute_index"] == 3


def test_no_duplicate_execution():
    features = {
        "fast_ma": [None, 1.0, 3.0, 4.0, 5.0],
        "slow_ma": [None, 2.0, 2.0, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    assert strategy.on_bar(features, 2) == []
    assert strategy.on_bar(features, 3) == [
        {
            "signal": "buy",
            "signal_index": 2,
            "execute_index": 3,
        }
    ]
    assert strategy.on_bar(features, 4) == []
    assert strategy.signal_buffer == []


def test_execute_exactly_next_bar():
    features = {
        "fast_ma": [None, 1.0, 3.0, 4.0, 5.0],
        "slow_ma": [None, 2.0, 2.0, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    assert strategy.on_bar(features, 2) == []
    assert strategy.on_bar(features, 4) == []
    assert strategy.signal_buffer == []

    strategy = MACrossoverStrategy()

    assert strategy.on_bar(features, 2) == []
    assert strategy.on_bar(features, 3) == [
        {
            "signal": "buy",
            "signal_index": 2,
            "execute_index": 3,
        }
    ]


def test_signal_direction():
    buy_features = {
        "fast_ma": [None, 1.0, 3.0],
        "slow_ma": [None, 2.0, 2.0],
    }
    sell_features = {
        "fast_ma": [None, 3.0, 1.0],
        "slow_ma": [None, 2.0, 2.0],
    }

    buy_strategy = MACrossoverStrategy()
    sell_strategy = MACrossoverStrategy()

    assert buy_strategy.generate_signal(buy_features, 2) == {
        "signal": "buy",
        "signal_index": 2,
    }
    assert sell_strategy.generate_signal(sell_features, 2) == {
        "signal": "sell",
        "signal_index": 2,
    }
