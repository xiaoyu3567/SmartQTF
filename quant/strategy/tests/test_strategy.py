import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.strategy.ma_crossover import MACrossoverStrategy
from quant.strategy.stateless import StatelessStrategyValidator, validate_stateless_strategy
from quant.schemas import StrategySignal, TradeSide


def test_signal_delay():
    features = {
        "fast_ma": [None, 1.0, 3.0, 4.0],
        "slow_ma": [None, 2.0, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    executions_at_index_2 = strategy.on_bar(features, 2)

    assert executions_at_index_2 == []

    executions_at_index_3 = strategy.on_bar(features, 3)

    assert len(executions_at_index_3) == 1
    assert executions_at_index_3[0].to_legacy_signal() == {
        "signal": "buy",
        "signal_index": 2,
        "execute_index": 3,
    }


def test_no_same_bar_execution():
    features = {
        "fast_ma": [None, 1.0, 3.0, 4.0],
        "slow_ma": [None, 2.0, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    executions = strategy.on_bar(features, 2)

    assert executions == []


def test_no_duplicate_execution():
    features = {
        "fast_ma": [None, 1.0, 3.0, 4.0, 5.0],
        "slow_ma": [None, 2.0, 2.0, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    assert strategy.on_bar(features, 2) == []
    executions = strategy.on_bar(features, 3)
    assert len(executions) == 1
    assert executions[0].to_legacy_signal() == {
        "signal": "buy",
        "signal_index": 2,
        "execute_index": 3,
    }
    assert strategy.on_bar(features, 4) == []


def test_execute_exactly_next_bar():
    features = {
        "fast_ma": [None, 1.0, 3.0, 4.0, 5.0],
        "slow_ma": [None, 2.0, 2.0, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    assert strategy.on_bar(features, 2) == []
    assert strategy.on_bar(features, 4) == []

    strategy = MACrossoverStrategy()

    assert strategy.on_bar(features, 2) == []
    executions = strategy.on_bar(features, 3)
    assert len(executions) == 1
    assert executions[0].to_legacy_signal() == {
        "signal": "buy",
        "signal_index": 2,
        "execute_index": 3,
    }


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

    assert buy_strategy.generate_signal(buy_features, 2).to_legacy_signal() == {
        "signal": "buy",
        "signal_index": 2,
    }
    assert sell_strategy.generate_signal(sell_features, 2).to_legacy_signal() == {
        "signal": "sell",
        "signal_index": 2,
    }


def test_ma_crossover_is_stateless_contract_strategy():
    features = {
        "fast_ma": [None, 1.0, 3.0],
        "slow_ma": [None, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    result = validate_stateless_strategy(strategy, features, 2)

    assert result.passed, result.errors
    assert vars(strategy) == {
        "strategy_id": "ma_crossover",
        "strategy_version": "1.0",
    }


def test_stateless_validator_rejects_mutating_strategy():
    class MutatingStrategy:
        strategy_id = "mutating"
        strategy_version = "1.0"

        def __init__(self):
            self.calls = 0

        def generate_signal(self, features, index):
            self.calls += 1
            return None

    result = StatelessStrategyValidator().validate(MutatingStrategy(), {}, 0)

    assert not result.passed
    assert "generate_signal mutated strategy instance state" in result.errors


def test_strategy_signal_schema_validation():
    signal = StrategySignal(
        signal_id="custom:1:buy",
        strategy_id="custom",
        strategy_version="1.0",
        side=TradeSide.BUY,
        signal_index=1,
        confidence=0.5,
    )

    assert signal.to_payload()["side"] == "buy"
    assert signal.to_legacy_signal() == {"signal": "buy", "signal_index": 1}


def test_strategy_signal_rejects_invalid_values():
    try:
        StrategySignal(
            signal_id="bad",
            strategy_id="custom",
            strategy_version="1.0",
            side=TradeSide.BUY,
            signal_index=-1,
        )
    except ValueError:
        return

    raise AssertionError("StrategySignal should reject negative signal_index")
