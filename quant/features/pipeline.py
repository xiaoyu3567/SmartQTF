from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from quant.data.quality import KlineQualityReport
from quant.data.schemas.market import Kline, Trade
from quant.features.indicators.cross_market import CrossMarketFeature
from quant.features.indicators.market_structure import MarketStructureFeature
from quant.features.indicators.moving_average import MovingAverage
from quant.features.indicators.orderflow import OrderFlowAlphaFeature
from quant.features.indicators.technical import (
    AverageTrueRange,
    MovingAverageConvergenceDivergence,
    RelativeStrengthIndex,
)
from quant.schemas.feature import (
    FeatureAvailability,
    FeatureSnapshot,
    FundingRateSnapshot,
    OrderBookSnapshot,
)
from quant.schemas.base import LayerRejection
from quant.schemas.enums import LayerName


@dataclass(frozen=True)
class FeaturePipelineConfig:
    feature_set_id: str = "advanced_alpha"
    feature_set_version: str = "1.0"
    fast_ma_window: int = 3
    slow_ma_window: int = 5
    rsi_window: int = 14
    atr_window: int = 14
    macd_fast_window: int = 12
    macd_slow_window: int = 26
    macd_signal_window: int = 9
    market_structure_lookback: int = 20
    include_incomplete_last_bar: bool = False
    large_trade_threshold: float = 0.0
    orderbook_depth: Optional[int] = None


@dataclass(frozen=True)
class FeaturePipelineInput:
    klines: Sequence[Kline]
    index: Optional[int] = None
    symbol: str = ""
    timeframe: str = ""
    venue: str = ""
    trades: Optional[Sequence[Trade]] = None
    orderbook: Optional[OrderBookSnapshot] = None
    spot_klines: Optional[Sequence[Kline]] = None
    perpetual_klines: Optional[Sequence[Kline]] = None
    spot_symbol: str = ""
    perpetual_symbol: str = ""
    funding_rate: Optional[FundingRateSnapshot] = None
    snapshot_id: Optional[str] = None
    quality_report: Optional[KlineQualityReport] = None


class FeatureQualityError(ValueError):
    def __init__(self, quality_report: KlineQualityReport):
        first_issue = next(
            (issue for issue in quality_report.issues if issue.fatal),
            quality_report.issues[0] if quality_report.issues else None,
        )
        issue_code = "unknown"
        issue_message = "quality report did not pass"
        if first_issue is not None:
            issue_code = getattr(first_issue.code, "value", first_issue.code)
            issue_message = first_issue.message

        self.quality_report = quality_report
        self.rejection = LayerRejection(
            layer=LayerName.FEATURE,
            code="quality_report_failed",
            message=(
                "Quality report failed before feature computation: "
                f"{issue_code} - {issue_message}"
            ),
            fatal=True,
        )
        super().__init__(self.rejection.message)


class AdvancedFeaturePipeline:
    def __init__(self, config: Optional[FeaturePipelineConfig] = None):
        self.config = config or FeaturePipelineConfig()

    def compute(self, request: FeaturePipelineInput) -> FeatureSnapshot:
        self._validate_quality_report(request)
        effective_index = self._effective_index(request)
        self._validate_request(request, effective_index)
        requested_index = self._requested_index(request)
        selected_bar = request.klines[effective_index]
        values = self._base_values(request.klines, effective_index)
        values.update(self._technical_values(request.klines, effective_index))
        values.update(self._advanced_values(request, selected_bar.timestamp, effective_index))
        actual_bars = effective_index + 1
        is_complete_bar = getattr(selected_bar, "is_complete", None) is not False
        last_bar = request.klines[-1]
        skipped_incomplete_last_bar = (
            request.index is None
            and requested_index != effective_index
            and getattr(last_bar, "is_complete", None) is False
        )
        include_incomplete_last_bar = self.config.include_incomplete_last_bar or (
            request.index is not None and is_complete_bar is False
        )

        return FeatureSnapshot(
            snapshot_id=request.snapshot_id
            or f"{request.symbol}:{request.timeframe}:{effective_index}:features",
            timestamp=selected_bar.timestamp,
            symbol=request.symbol,
            timeframe=request.timeframe,
            as_of_timestamp=selected_bar.timestamp,
            feature_set_id=self.config.feature_set_id,
            feature_set_version=self.config.feature_set_version,
            values=values,
            feature_availability=self._feature_availability(actual_bars),
            feature_parameters=self._feature_parameters(),
            source_window_start=request.klines[0].timestamp,
            source_window_end=selected_bar.timestamp,
            is_complete_bar=is_complete_bar,
            requested_index=requested_index,
            effective_index=effective_index,
            input_bar_count=len(request.klines),
            include_incomplete_last_bar=include_incomplete_last_bar,
            skipped_incomplete_last_bar=skipped_incomplete_last_bar,
            skipped_incomplete_bar_timestamp=last_bar.timestamp if skipped_incomplete_last_bar else None,
        )

    def _requested_index(self, request: FeaturePipelineInput) -> int:
        if request.index is not None:
            return request.index
        return len(request.klines) - 1

    def _effective_index(self, request: FeaturePipelineInput) -> int:
        if not request.klines:
            raise ValueError("klines must not be empty")
        if request.index is not None:
            return request.index
        last_index = len(request.klines) - 1
        last_bar = request.klines[last_index]
        if self.config.include_incomplete_last_bar or getattr(last_bar, "is_complete", None) is not False:
            return last_index
        if last_index == 0:
            raise ValueError("no complete kline available for feature computation")
        return last_index - 1

    def _validate_request(self, request: FeaturePipelineInput, effective_index: int) -> None:
        if not request.klines:
            raise ValueError("klines must not be empty")
        if effective_index < 0 or effective_index >= len(request.klines):
            raise ValueError("index out of kline range")
        if self.config.fast_ma_window >= self.config.slow_ma_window:
            raise ValueError("fast_ma_window must be less than slow_ma_window")

    def _validate_quality_report(self, request: FeaturePipelineInput) -> None:
        quality_report = request.quality_report
        if quality_report is None:
            return
        if not quality_report.passed:
            raise FeatureQualityError(quality_report)
        if request.symbol and quality_report.symbol != request.symbol:
            raise ValueError("quality_report symbol must match feature request symbol")
        if request.timeframe and quality_report.timeframe != request.timeframe:
            raise ValueError("quality_report timeframe must match feature request timeframe")
        if quality_report.checked_count != len(request.klines):
            raise ValueError("quality_report checked_count must match kline count")

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

    def _technical_values(self, klines: Sequence[Kline], index: int) -> Mapping[str, object]:
        values = {
            "rsi": RelativeStrengthIndex(self.config.rsi_window).compute(klines, index),
            "atr": AverageTrueRange(self.config.atr_window).compute(klines, index),
        }
        macd = MovingAverageConvergenceDivergence(
            fast_window=self.config.macd_fast_window,
            slow_window=self.config.macd_slow_window,
            signal_window=self.config.macd_signal_window,
        ).compute(klines, index)
        if macd is None:
            values.update({"macd": None, "macd.signal": None, "macd.histogram": None})
        else:
            values.update(
                {
                    "macd": macd.get("macd"),
                    "macd.signal": macd.get("signal"),
                    "macd.histogram": macd.get("histogram"),
                }
            )
        return values

    def _feature_availability(self, actual_bars: int) -> Mapping[str, FeatureAvailability]:
        return {
            "rsi": self._availability_record(
                "rsi",
                required_bars=self.config.rsi_window + 1,
                actual_bars=actual_bars,
            ),
            "atr": self._availability_record(
                "atr",
                required_bars=self.config.atr_window + 1,
                actual_bars=actual_bars,
            ),
            "macd": self._availability_record(
                "macd",
                required_bars=self.config.macd_slow_window,
                actual_bars=actual_bars,
            ),
            "macd.signal": self._availability_record(
                "macd.signal",
                required_bars=self.config.macd_slow_window + self.config.macd_signal_window - 1,
                actual_bars=actual_bars,
            ),
            "macd.histogram": self._availability_record(
                "macd.histogram",
                required_bars=self.config.macd_slow_window + self.config.macd_signal_window - 1,
                actual_bars=actual_bars,
            ),
        }

    def _availability_record(
        self,
        feature_name: str,
        *,
        required_bars: int,
        actual_bars: int,
    ) -> FeatureAvailability:
        available = actual_bars >= required_bars
        return FeatureAvailability(
            feature_name=feature_name,
            available=available,
            reason=None if available else "insufficient_history",
            required_bars=required_bars,
            actual_bars=actual_bars,
        )

    def _feature_parameters(self) -> Mapping[str, Mapping[str, object]]:
        macd_parameters = {
            "fast_window": self.config.macd_fast_window,
            "slow_window": self.config.macd_slow_window,
            "signal_window": self.config.macd_signal_window,
        }
        return {
            "rsi": {"window": self.config.rsi_window},
            "atr": {"window": self.config.atr_window},
            "macd": macd_parameters,
            "macd.signal": macd_parameters,
            "macd.histogram": macd_parameters,
        }

    def _advanced_values(
        self,
        request: FeaturePipelineInput,
        timestamp: int,
        index: int,
    ) -> Mapping[str, object]:
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
            index,
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
                    index,
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
