from quant.strategy.base.strategy import Strategy
from quant.schemas import StrategySignal, TradeSide


class MACrossoverStrategy(Strategy):
    def __init__(self, strategy_id="ma_crossover", strategy_version="1.0"):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version

    def generate_signal(self, features, index):
        fast_ma = features["fast_ma"]
        slow_ma = features["slow_ma"]

        if index <= 0:
            return None

        previous_fast = fast_ma[index - 1]
        previous_slow = slow_ma[index - 1]
        current_fast = fast_ma[index]
        current_slow = slow_ma[index]

        if None in [previous_fast, previous_slow, current_fast, current_slow]:
            return None

        if previous_fast <= previous_slow and current_fast > current_slow:
            return self._build_signal(TradeSide.BUY, index)

        if previous_fast >= previous_slow and current_fast < current_slow:
            return self._build_signal(TradeSide.SELL, index)

        return None

    def on_bar(self, features, index):
        if index <= 0:
            return []

        signal = self.generate_signal(features, index - 1)
        if signal is None:
            return []

        return [signal.with_execute_index(index)]

    def _build_signal(self, side, index):
        return StrategySignal(
            signal_id=f"{self.strategy_id}:{index}:{side.value}",
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            side=side,
            signal_index=index,
            reason_codes=["ma_cross"],
        )
