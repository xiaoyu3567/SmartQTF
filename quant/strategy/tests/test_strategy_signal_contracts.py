import pytest
from pydantic import ValidationError

from quant.schemas import PayloadSource, StrategyAction, StrategySignal, TraceContext, TradeSide
from quant.strategy.ma_crossover import MACrossoverStrategy
from quant.strategy.stateless import StatelessStrategyValidator
from quant.strategy.trend_pullback import TrendPullbackLongStrategy


def test_strategy_signal_schema_round_trips_symbol_confidence_reason_and_trace():
    signal = StrategySignal(
        signal_id="ma_crossover:42:buy",
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        side=TradeSide.BUY,
        signal_index=42,
        execute_index=43,
        symbol="BTCUSDT",
        timeframe="1m",
        confidence=0.71,
        reason_codes=["ma_cross_up"],
        trace=_trace(),
    )

    restored = StrategySignal.from_payload(signal.to_payload())

    assert restored == signal
    assert signal.to_payload()["side"] == "buy"
    assert signal.to_payload()["action"] == "buy"
    assert signal.trade_now is True
    assert signal.should_send_order is True
    assert signal.is_orderable is True
    assert signal.to_legacy_signal() == {
        "signal": "buy",
        "signal_index": 42,
        "execute_index": 43,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
    }
    assert signal.reason_codes == ["ma_cross_up"]
    assert signal.trace.run_id == "strategy-contract-001"


def test_strategy_signal_rejects_invalid_index_and_confidence():
    with pytest.raises((ValidationError, ValueError)):
        StrategySignal(
            signal_id="bad-index",
            strategy_id="ma_crossover",
            strategy_version="1.0.0",
            side=TradeSide.BUY,
            signal_index=-1,
        )

    with pytest.raises((ValidationError, ValueError)):
        StrategySignal(
            signal_id="bad-confidence",
            strategy_id="ma_crossover",
            strategy_version="1.0.0",
            side=TradeSide.BUY,
            signal_index=1,
            confidence=1.5,
        )


def test_strategy_signal_wait_action_is_non_executable_and_not_legacy_order():
    signal = StrategySignal(
        signal_id="trend_pullback_long_v1:7:wait_for_pullback",
        strategy_id="trend_pullback_long_v1",
        strategy_version="1.0.0",
        action=StrategyAction.WAIT,
        signal_type="WAIT_FOR_PULLBACK",
        signal_index=7,
        symbol="BTCUSDT",
        timeframe="5m",
        reason_codes=["UPTREND_HIGH_VOL", "WAIT_FOR_PULLBACK"],
        watch_plan={"recheck_on": "next_closed_bar", "expires_after_bars": 1},
    )

    payload = signal.to_payload()

    assert payload["action"] == "wait"
    assert payload["signal_type"] == "WAIT_FOR_PULLBACK"
    assert payload["trade_now"] is False
    assert payload["should_send_order"] is False
    assert signal.side is None
    assert signal.is_orderable is False
    with pytest.raises(ValueError, match="non-executable strategy signal"):
        signal.to_legacy_signal()


def test_strategy_signal_rejects_wait_that_can_send_order():
    with pytest.raises((ValidationError, ValueError), match="wait strategy signals"):
        StrategySignal(
            signal_id="bad-wait",
            strategy_id="trend_pullback_long_v1",
            strategy_version="1.0.0",
            action=StrategyAction.WAIT,
            signal_type="WAIT_FOR_PULLBACK",
            signal_index=1,
            trade_now=True,
            should_send_order=False,
        )

    with pytest.raises((ValidationError, ValueError), match="wait strategy signals"):
        StrategySignal(
            signal_id="bad-wait-order",
            strategy_id="trend_pullback_long_v1",
            strategy_version="1.0.0",
            action=StrategyAction.WAIT,
            signal_type="WAIT_FOR_PULLBACK",
            signal_index=1,
            trade_now=False,
            should_send_order=True,
        )


def test_trend_pullback_long_outputs_wait_plan_when_price_far_above_ema5():
    strategy = TrendPullbackLongStrategy(max_distance_pct_from_ema=0.01)
    features = {
        "close": [100.0, 112.0, 111.0],
        "ema5": [100.0, 105.0, 106.0],
        "regime": ["UPTREND_HIGH_VOL", "UPTREND_HIGH_VOL", "UPTREND_HIGH_VOL"],
    }

    signal = strategy.generate_signal(features, index=1)

    assert signal.strategy_id == "trend_pullback_long_v1"
    assert signal.action == _value(StrategyAction.WAIT)
    assert signal.signal_type == "WAIT_FOR_PULLBACK"
    assert signal.trade_now is False
    assert signal.should_send_order is False
    assert signal.watch_plan["recheck_on"] == "next_closed_bar"
    assert signal.watch_plan["expires_after_bars"] == 1
    assert "price_too_far_above_ema5" in signal.reason_codes
    assert strategy.on_bar(features, index=2) == []


def test_ma_crossover_outputs_buy_sell_or_no_signal_with_reason_codes():
    strategy = MACrossoverStrategy(strategy_version="1.0.0")

    buy = strategy.generate_signal(
        {"fast_ma": [99.0, 101.0], "slow_ma": [100.0, 100.0]},
        index=1,
    )
    sell = strategy.generate_signal(
        {"fast_ma": [101.0, 99.0], "slow_ma": [100.0, 100.0]},
        index=1,
    )
    hold = strategy.generate_signal(
        {"fast_ma": [101.0, 102.0], "slow_ma": [100.0, 100.0]},
        index=1,
    )

    assert buy.side == _value(TradeSide.BUY)
    assert buy.reason_codes == ["ma_cross"]
    assert buy.strategy_id == "ma_crossover"
    assert sell.side == _value(TradeSide.SELL)
    assert sell.reason_codes == ["ma_cross"]
    assert hold is None


def test_stateless_validator_rejects_cross_layer_dependencies():
    class StrategyWithRiskDependency:
        strategy_id = "bad_strategy"
        strategy_version = "1.0.0"

        def __init__(self):
            self.risk_manager = object()

        def generate_signal(self, features, index):
            return None

    result = StatelessStrategyValidator().validate(StrategyWithRiskDependency(), {}, 0)

    assert not result.passed
    assert "strategy holds forbidden cross-layer/state attributes: risk_manager" in result.errors


def _trace():
    return TraceContext(
        run_id="strategy-contract-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000120,
        bar_index=42,
    )


def _value(value):
    return getattr(value, "value", value)
