from typing import Optional, Sequence

from quant.data.schemas.market import Kline
from quant.features.base.feature import Feature
from quant.features.time_guard import TimeGuard
from quant.schemas.feature import MarketStructureSnapshot


class MarketStructureFeature(Feature):
    def __init__(self, lookback: int = 20):
        if lookback <= 0:
            raise ValueError("lookback must be > 0")
        self.lookback = lookback

    def compute(
        self,
        klines: Sequence[Kline],
        current_index: Optional[int] = None,
        *,
        snapshot_id: str = "market-structure",
        symbol: str = "",
        venue: str = "",
        as_of_timestamp: Optional[int] = None,
    ) -> Optional[MarketStructureSnapshot]:
        safe_klines = self._safe_klines(klines, current_index)
        if len(safe_klines) < self.lookback + 1:
            return None

        current = safe_klines[-1]
        previous_window = safe_klines[-self.lookback - 1 : -1]
        current_window = safe_klines[-self.lookback :]

        previous_high = max(kline.high for kline in previous_window)
        previous_low = min(kline.low for kline in previous_window)
        current_high = max(kline.high for kline in current_window)
        current_low = min(kline.low for kline in current_window)
        breakout_direction = self._breakout_direction(current.close, previous_high, previous_low)

        return MarketStructureSnapshot(
            snapshot_id=snapshot_id,
            timestamp=current.timestamp,
            symbol=symbol,
            venue=venue,
            as_of_timestamp=as_of_timestamp if as_of_timestamp is not None else current.timestamp,
            window_start_timestamp=previous_window[0].timestamp,
            window_end_timestamp=current.timestamp,
            lookback=self.lookback,
            previous_high=previous_high,
            previous_low=previous_low,
            current_high=current_high,
            current_low=current_low,
            close=current.close,
            higher_high=current_high > previous_high,
            lower_low=current_low < previous_low,
            breakout_direction=breakout_direction,
            structure_state="breakout" if breakout_direction != "none" else "range",
        )

    def _safe_klines(self, klines: Sequence[Kline], current_index: Optional[int]) -> Sequence[Kline]:
        if current_index is None:
            return klines
        return TimeGuard.enforce(klines, current_index)

    def _breakout_direction(self, close: float, previous_high: float, previous_low: float) -> str:
        if close > previous_high:
            return "up"
        if close < previous_low:
            return "down"
        return "none"
