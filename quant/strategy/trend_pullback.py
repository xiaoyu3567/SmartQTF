from quant.schemas import StrategyAction, StrategySignal, TradeSide
from quant.strategy.base.strategy import Strategy


class TrendPullbackLongStrategy(Strategy):
    def __init__(
        self,
        *,
        strategy_id="trend_pullback_long_v1",
        strategy_version="1.0.0",
        ema_key="ema5",
        close_key="close",
        regime_key="regime",
        required_regime="UPTREND_HIGH_VOL",
        max_distance_pct_from_ema=0.01,
        entry_distance_pct_from_ema=0.002,
    ):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.ema_key = ema_key
        self.close_key = close_key
        self.regime_key = regime_key
        self.required_regime = required_regime
        self.max_distance_pct_from_ema = max_distance_pct_from_ema
        self.entry_distance_pct_from_ema = entry_distance_pct_from_ema

    def generate_signal(self, features, index):
        close = self._value_at(features, self.close_key, index)
        ema = self._value_at(features, self.ema_key, index)
        regime = self._value_at(features, self.regime_key, index, required=False)
        if close is None or ema is None or ema <= 0:
            return None
        if regime is not None and str(regime).upper() != self.required_regime:
            return None

        distance_pct = (float(close) - float(ema)) / float(ema)
        if distance_pct > self.max_distance_pct_from_ema:
            return self._wait_for_pullback(index, close, ema, distance_pct)
        if distance_pct <= self.entry_distance_pct_from_ema:
            return self._entry_signal(index, close, ema, distance_pct)
        return None

    def on_bar(self, features, index):
        if index <= 0:
            return []
        signal = self.generate_signal(features, index - 1)
        if signal is None or not signal.is_orderable:
            return []
        return [signal.with_execute_index(index)]

    def _wait_for_pullback(self, index, close, ema, distance_pct):
        return StrategySignal(
            signal_id=f"{self.strategy_id}:{index}:wait_for_pullback",
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            action=StrategyAction.WAIT,
            signal_type="WAIT_FOR_PULLBACK",
            signal_index=index,
            confidence=0.65,
            reason_codes=[
                "UPTREND_HIGH_VOL",
                "WAIT_FOR_PULLBACK",
                "price_too_far_above_ema5",
            ],
            trade_now=False,
            should_send_order=False,
            watch_plan={
                "plan_type": "next_bar_recheck",
                "recheck_on": "next_closed_bar",
                "expires_after_bars": 1,
                "conditions": {
                    "close": float(close),
                    "ema5": float(ema),
                    "distance_pct": float(distance_pct),
                    "max_distance_pct_from_ema": self.max_distance_pct_from_ema,
                    "entry_distance_pct_from_ema": self.entry_distance_pct_from_ema,
                },
            },
        )

    def _entry_signal(self, index, close, ema, distance_pct):
        return StrategySignal(
            signal_id=f"{self.strategy_id}:{index}:buy",
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            side=TradeSide.BUY,
            action=StrategyAction.BUY,
            signal_type="PULLBACK_LONG_ENTRY",
            signal_index=index,
            confidence=0.72,
            reason_codes=[
                "UPTREND_HIGH_VOL",
                "pullback_near_ema5",
                f"distance_pct:{distance_pct:.6f}",
            ],
            watch_plan={
                "plan_type": "entry_requires_risk_portfolio_execution_gates",
                "ema5": float(ema),
                "close": float(close),
            },
        )

    @staticmethod
    def _value_at(features, key, index, *, required=True):
        if key not in features:
            if required:
                raise KeyError(key)
            return None
        values = features[key]
        if isinstance(values, (list, tuple)):
            if index < 0 or index >= len(values):
                return None
            return values[index]
        return values
