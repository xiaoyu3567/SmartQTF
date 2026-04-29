from typing import Optional, Sequence

from quant.data.schemas.market import Trade
from quant.features.base.feature import Feature
from quant.features.time_guard import TimeGuard
from quant.schemas.feature import OrderBookSnapshot, OrderFlowSnapshot


class OrderFlowAlphaFeature(Feature):
    def __init__(self, large_trade_threshold: float = 0.0, orderbook_depth: Optional[int] = None):
        if large_trade_threshold < 0:
            raise ValueError("large_trade_threshold must be >= 0")
        if orderbook_depth is not None and orderbook_depth <= 0:
            raise ValueError("orderbook_depth must be > 0")

        self.large_trade_threshold = large_trade_threshold
        self.orderbook_depth = orderbook_depth

    def compute(
        self,
        trades: Sequence[Trade],
        current_index: Optional[int] = None,
        *,
        snapshot_id: str = "orderflow",
        symbol: str = "",
        venue: str = "",
        as_of_timestamp: Optional[int] = None,
        orderbook: Optional[OrderBookSnapshot] = None,
    ) -> OrderFlowSnapshot:
        safe_trades = self._safe_trades(trades, current_index)
        if not safe_trades:
            raise ValueError("trades must not be empty")

        buy_volume = 0.0
        sell_volume = 0.0
        large_buy_volume = 0.0
        large_sell_volume = 0.0
        buy_trade_count = 0
        sell_trade_count = 0
        large_trade_count = 0

        for trade in safe_trades:
            side = trade.side.lower()
            if side not in {"buy", "sell"}:
                raise ValueError("trade side must be buy or sell")

            if side == "buy":
                buy_volume += trade.size
                buy_trade_count += 1
                if trade.size >= self.large_trade_threshold:
                    large_buy_volume += trade.size
            else:
                sell_volume += trade.size
                sell_trade_count += 1
                if trade.size >= self.large_trade_threshold:
                    large_sell_volume += trade.size

            if trade.size >= self.large_trade_threshold:
                large_trade_count += 1

        timestamp = safe_trades[-1].timestamp
        return OrderFlowSnapshot(
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            symbol=symbol,
            venue=venue,
            as_of_timestamp=as_of_timestamp if as_of_timestamp is not None else timestamp,
            window_start_timestamp=safe_trades[0].timestamp,
            window_end_timestamp=timestamp,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            large_buy_volume=large_buy_volume,
            large_sell_volume=large_sell_volume,
            buy_trade_count=buy_trade_count,
            sell_trade_count=sell_trade_count,
            large_trade_count=large_trade_count,
            taker_buy_sell_ratio=self._ratio(buy_volume, sell_volume),
            orderbook_imbalance=self._orderbook_imbalance(orderbook),
        )

    def _safe_trades(self, trades: Sequence[Trade], current_index: Optional[int]) -> Sequence[Trade]:
        if current_index is None:
            return trades
        return TimeGuard.enforce(trades, current_index)

    def _ratio(self, buy_volume: float, sell_volume: float) -> Optional[float]:
        if sell_volume == 0:
            return None
        return buy_volume / sell_volume

    def _orderbook_imbalance(self, orderbook: Optional[OrderBookSnapshot]) -> Optional[float]:
        if orderbook is None:
            return None

        bids = orderbook.bids[: self.orderbook_depth]
        asks = orderbook.asks[: self.orderbook_depth]
        bid_quantity = sum(level.quantity for level in bids)
        ask_quantity = sum(level.quantity for level in asks)
        total_quantity = bid_quantity + ask_quantity
        if total_quantity == 0:
            return 0.0
        return (bid_quantity - ask_quantity) / total_quantity
