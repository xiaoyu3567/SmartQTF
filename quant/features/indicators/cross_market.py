from typing import Optional, Sequence

from quant.data.schemas.market import Kline
from quant.features.base.feature import Feature
from quant.features.time_guard import TimeGuard
from quant.schemas.feature import CrossMarketSnapshot, FundingRateSnapshot


class CrossMarketFeature(Feature):
    def compute(
        self,
        spot_klines: Sequence[Kline],
        perpetual_klines: Sequence[Kline],
        current_index: Optional[int] = None,
        *,
        snapshot_id: str = "cross-market",
        symbol: str = "",
        venue: str = "",
        spot_symbol: str = "",
        perpetual_symbol: str = "",
        as_of_timestamp: Optional[int] = None,
        funding_rate: Optional[FundingRateSnapshot] = None,
    ) -> CrossMarketSnapshot:
        safe_spot = self._safe_klines(spot_klines, current_index)
        safe_perpetual = self._safe_klines(perpetual_klines, current_index)
        if not safe_spot:
            raise ValueError("spot_klines must not be empty")
        if not safe_perpetual:
            raise ValueError("perpetual_klines must not be empty")

        spot = safe_spot[-1]
        perpetual = safe_perpetual[-1]
        timestamp = min(spot.timestamp, perpetual.timestamp)
        effective_as_of = as_of_timestamp if as_of_timestamp is not None else timestamp
        if funding_rate is not None and funding_rate.as_of_timestamp > effective_as_of:
            raise ValueError("funding_rate as_of_timestamp must be <= snapshot as_of_timestamp")

        return CrossMarketSnapshot(
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            symbol=symbol or perpetual_symbol or spot_symbol,
            venue=venue,
            as_of_timestamp=effective_as_of,
            window_start_timestamp=min(safe_spot[0].timestamp, safe_perpetual[0].timestamp),
            window_end_timestamp=timestamp,
            spot_symbol=spot_symbol or symbol,
            perpetual_symbol=perpetual_symbol or symbol,
            spot_price=spot.close,
            perpetual_price=perpetual.close,
            funding_rate=funding_rate.funding_rate if funding_rate is not None else None,
            next_funding_timestamp=(
                funding_rate.next_funding_timestamp if funding_rate is not None else None
            ),
        )

    def _safe_klines(self, klines: Sequence[Kline], current_index: Optional[int]) -> Sequence[Kline]:
        if current_index is None:
            return klines
        return TimeGuard.enforce(klines, current_index)
