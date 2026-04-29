from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from quant.data.schemas.market import Kline, Trade
from quant.features.indicators.cross_market import CrossMarketFeature
from quant.features.indicators.market_structure import MarketStructureFeature
from quant.features.indicators.moving_average import MovingAverage
from quant.features.indicators.orderflow import OrderFlowAlphaFeature
from quant.schemas.feature import FeatureSnapshot, FundingRateSnapshot, OrderBookSnapshot


@dataclass(frozen=True)
class FeaturePipelineConfig:
    feature_set_id: str = "advanced_alpha"
    feature_set_version: str = "1.0"
    fast_ma_window: int = 3
    slow_ma_window: int = 5
    market_structure_lookback: int = 20
    large_trade_threshold: float = 0.0
    orderbook_depth: Optional[int] = None


@dataclass(frozen=True)
class FeaturePipelineInput:
    klines: Sequence[Kline]
    index: int
    symbol: str
    timeframe: str
    venue: str = ""
    trades: Optional[Sequence[Trade]] = None
    orderbook: Optional[OrderBookSnapshot] = None
    spot_klines: Optional[Sequence[Kline]] = None
    perpetual_klines: Optional[Sequence[Kline]] = None
    spot_symbol: str = ""
    perpetual_symbol: str = ""
    funding_rate: Optional[FundingRateSnapshot] = None
    snapshot_id: Optional[str] = None


class AdvancedFeaturePipeline:
    def __init__(self, config: Optional[FeaturePipelineConfig] = None):
        self.config = config or FeaturePipelineConfig()

    def compute(self, request: FeaturePipelineInput) -> FeatureSnapshot:
        self._validate_request(request)
        selected_bar = request.klines[request.index]
        values = self._base_values(request.klines, request.index)
        values.update(self._advanced_values(request, selected_bar.timestamp))

        return FeatureSnapshot(
            snapshot_id=request.snapshot_id or f"{request.symbol}:{request.timeframe}:{request.index}:features",
            timestamp=selected_bar.timestamp,
            symbol=request.symbol,
            timeframe=request.timeframe,
            as_of_timestamp=selected_bar.timestamp,
            feature_set_id=self.config.feature_set_id,
            feature_set_version=self.config.feature_set_version,
            values=values,
            source_window_start=request.klines[0].timestamp,
            source_window_end=selected_bar.timestamp,
        )

    def _validate_request(self, request: FeaturePipelineInput) -> None:
        if not request.klines:
            raise ValueError("klines must not be empty")
        if request.index < 0 or request.index >= len(request.klines):
            raise ValueError("index out of kline range")
        if self.config.fast_ma_window >= self.config.slow_ma_window:
            raise ValueError("fast_ma_window must be less than slow_ma_window")

    def _base_values(self, klines: Sequence[Kline], index: int) -> Mapping[str, object]:
        fast = MovingAverage(self.config.fast_ma_window)
        slow = MovingAverage(self.config.slow_ma_window)
        fast_value = fast.compute(klines, index)
        slow_value = slow.compute(klines, index)

        return {
            "close": klines[index].close,
            "fast_ma": fast_value,
            "slow_ma": slow_value,
            "ma_fast": fast_value,
            "ma_slow": slow_value,
        }

    def _advanced_values(self, request: FeaturePipelineInput, timestamp: int) -> Mapping[str, object]:
        values = {}
        safe_trades = self._trades_as_of(request.trades, timestamp)
        if safe_trades:
            orderflow = OrderFlowAlphaFeature(
                large_trade_threshold=self.config.large_trade_threshold,
                orderbook_depth=self.config.orderbook_depth,
            ).compute(
                safe_trades,
                snapshot_id=f"{request.symbol}:{timestamp}:orderflow",
                symbol=request.symbol,
                venue=request.venue,
                orderbook=self._orderbook_as_of(request.orderbook, timestamp),
            )
            values.update(
                {
                    "orderflow.buy_volume": orderflow.buy_volume,
                    "orderflow.sell_volume": orderflow.sell_volume,
                    "orderflow.imbalance": orderflow.order_flow_imbalance,
                    "orderflow.large_imbalance": orderflow.large_order_imbalance,
                    "orderflow.taker_buy_sell_ratio": orderflow.taker_buy_sell_ratio,
                    "orderflow.orderbook_imbalance": orderflow.orderbook_imbalance,
                }
            )

        structure = MarketStructureFeature(self.config.market_structure_lookback).compute(
            request.klines,
            request.index,
            snapshot_id=f"{request.symbol}:{timestamp}:market-structure",
            symbol=request.symbol,
            venue=request.venue,
            as_of_timestamp=timestamp,
        )
        if structure is not None:
            values.update(
                {
                    "market_structure.previous_high": structure.previous_high,
                    "market_structure.previous_low": structure.previous_low,
                    "market_structure.current_high": structure.current_high,
                    "market_structure.current_low": structure.current_low,
                    "market_structure.higher_high": structure.higher_high,
                    "market_structure.lower_low": structure.lower_low,
                    "market_structure.breakout_direction": structure.breakout_direction,
                    "market_structure.structure_state": structure.structure_state,
                    "market_structure.liquidity_range_width": structure.liquidity_range_width,
                }
            )

        if request.spot_klines and request.perpetual_klines:
            cross_market = CrossMarketFeature().compute(
                request.spot_klines,
                request.perpetual_klines,
                current_index=self._aligned_index(
                    request.index,
                    request.spot_klines,
                    request.perpetual_klines,
                ),
                snapshot_id=f"{request.symbol}:{timestamp}:cross-market",
                symbol=request.symbol,
                venue=request.venue,
                spot_symbol=request.spot_symbol,
                perpetual_symbol=request.perpetual_symbol,
                funding_rate=request.funding_rate,
            )
            if cross_market.timestamp > timestamp:
                raise ValueError("cross market snapshot timestamp must be <= selected bar timestamp")
            values.update(
                {
                    "cross_market.spot_price": cross_market.spot_price,
                    "cross_market.perpetual_price": cross_market.perpetual_price,
                    "cross_market.basis": cross_market.basis,
                    "cross_market.basis_rate": cross_market.basis_rate,
                    "cross_market.funding_rate": cross_market.funding_rate,
                }
            )

        return values

    def _aligned_index(self, index: int, *series: Sequence[object]) -> int:
        return min(index, *(len(items) - 1 for items in series))

    def _trades_as_of(self, trades: Optional[Sequence[Trade]], timestamp: int) -> Sequence[Trade]:
        if not trades:
            return []
        return [trade for trade in trades if trade.timestamp <= timestamp]

    def _orderbook_as_of(
        self,
        orderbook: Optional[OrderBookSnapshot],
        timestamp: int,
    ) -> Optional[OrderBookSnapshot]:
        if orderbook is None:
            return None
        if orderbook.as_of_timestamp > timestamp:
            raise ValueError("orderbook as_of_timestamp must be <= selected bar timestamp")
        return orderbook
