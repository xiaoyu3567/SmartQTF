from typing import Dict, List, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator, model_validator
else:
    from pydantic import root_validator, validator


FeatureValue = Union[float, int, bool, str, None]


class MarketAuxiliarySnapshotBase(SmartQTFModel):
    snapshot_id: str
    timestamp: int
    symbol: str
    venue: str
    as_of_timestamp: int
    trace: Optional[TraceContext] = None

    @classmethod
    def as_of_timestamp_must_not_read_future(cls, values):
        timestamp = values.get("timestamp")
        as_of_timestamp = values.get("as_of_timestamp")
        if timestamp is not None and as_of_timestamp is not None and as_of_timestamp > timestamp:
            raise ValueError("as_of_timestamp must be <= timestamp")
        return values


class OpenInterestSnapshotBase(MarketAuxiliarySnapshotBase):
    open_interest: float
    open_interest_value: Optional[float] = None
    funding_rate: Optional[float] = None
    next_funding_timestamp: Optional[int] = None

    @classmethod
    def numeric_values_must_be_valid(cls, values):
        open_interest = values.get("open_interest")
        open_interest_value = values.get("open_interest_value")
        if open_interest is not None and open_interest < 0:
            raise ValueError("open_interest must be >= 0")
        if open_interest_value is not None and open_interest_value < 0:
            raise ValueError("open_interest_value must be >= 0")
        return values


class FundingRateSnapshotBase(MarketAuxiliarySnapshotBase):
    funding_rate: float
    next_funding_timestamp: Optional[int] = None
    funding_timestamp: Optional[int] = None

    @classmethod
    def funding_values_must_be_valid(cls, values):
        funding_timestamp = values.get("funding_timestamp")
        next_funding_timestamp = values.get("next_funding_timestamp")
        if funding_timestamp is not None and funding_timestamp < 0:
            raise ValueError("funding_timestamp must be >= 0")
        if next_funding_timestamp is not None and next_funding_timestamp < 0:
            raise ValueError("next_funding_timestamp must be >= 0")
        if (
            funding_timestamp is not None
            and next_funding_timestamp is not None
            and next_funding_timestamp < funding_timestamp
        ):
            raise ValueError("next_funding_timestamp must be >= funding_timestamp")
        return values


class NetflowSnapshotBase(MarketAuxiliarySnapshotBase):
    timeframe: str
    inflow: float
    outflow: float
    netflow: Optional[float] = None
    exchange_reserve: Optional[float] = None
    large_transfer_count: Optional[int] = None

    @property
    def computed_netflow(self) -> float:
        return self.inflow - self.outflow

    @classmethod
    def numeric_values_must_be_valid(cls, values):
        inflow = values.get("inflow")
        outflow = values.get("outflow")
        exchange_reserve = values.get("exchange_reserve")
        large_transfer_count = values.get("large_transfer_count")
        if inflow is not None and inflow < 0:
            raise ValueError("inflow must be >= 0")
        if outflow is not None and outflow < 0:
            raise ValueError("outflow must be >= 0")
        if exchange_reserve is not None and exchange_reserve < 0:
            raise ValueError("exchange_reserve must be >= 0")
        if large_transfer_count is not None and large_transfer_count < 0:
            raise ValueError("large_transfer_count must be >= 0")
        return values

    @classmethod
    def explicit_netflow_must_match_flows(cls, values):
        inflow = values.get("inflow")
        outflow = values.get("outflow")
        netflow = values.get("netflow")
        if inflow is None or outflow is None or netflow is None:
            return values
        if abs(netflow - (inflow - outflow)) > 1e-9:
            raise ValueError("netflow must equal inflow - outflow")
        return values


class OrderBookLevelBase(SmartQTFModel):
    price: float
    quantity: float

    @classmethod
    def level_values_must_be_valid(cls, values):
        price = values.get("price")
        quantity = values.get("quantity")
        if price is not None and price <= 0:
            raise ValueError("order book level price must be > 0")
        if quantity is not None and quantity < 0:
            raise ValueError("order book level quantity must be >= 0")
        return values


class OrderBookSnapshotBase(MarketAuxiliarySnapshotBase):
    bids: List["OrderBookLevel"]
    asks: List["OrderBookLevel"]
    depth: Optional[int] = None

    @property
    def best_bid(self) -> Optional[float]:
        if not self.bids:
            return None
        return self.bids[0].price

    @property
    def best_ask(self) -> Optional[float]:
        if not self.asks:
            return None
        return self.asks[0].price

    @classmethod
    def book_must_be_valid(cls, values):
        bids = values.get("bids") or []
        asks = values.get("asks") or []
        depth = values.get("depth")
        if not bids:
            raise ValueError("order book bids must not be empty")
        if not asks:
            raise ValueError("order book asks must not be empty")
        if depth is not None and depth <= 0:
            raise ValueError("order book depth must be > 0")
        bid_prices = [level.price for level in bids]
        ask_prices = [level.price for level in asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("order book bids must be sorted from high to low")
        if ask_prices != sorted(ask_prices):
            raise ValueError("order book asks must be sorted from low to high")
        if bid_prices[0] >= ask_prices[0]:
            raise ValueError("order book best bid must be below best ask")
        return values


class OrderFlowSnapshotBase(MarketAuxiliarySnapshotBase):
    window_start_timestamp: int
    window_end_timestamp: int
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    large_buy_volume: float = 0.0
    large_sell_volume: float = 0.0
    buy_trade_count: int = 0
    sell_trade_count: int = 0
    large_trade_count: int = 0
    taker_buy_sell_ratio: Optional[float] = None
    orderbook_imbalance: Optional[float] = None

    @property
    def order_flow_imbalance(self) -> float:
        return self.buy_volume - self.sell_volume

    @property
    def large_order_imbalance(self) -> float:
        return self.large_buy_volume - self.large_sell_volume

    @classmethod
    def order_flow_values_must_be_valid(cls, values):
        window_start = values.get("window_start_timestamp")
        window_end = values.get("window_end_timestamp")
        if window_start is not None and window_end is not None and window_start > window_end:
            raise ValueError("window_start_timestamp must be <= window_end_timestamp")

        non_negative_fields = [
            "buy_volume",
            "sell_volume",
            "large_buy_volume",
            "large_sell_volume",
            "buy_trade_count",
            "sell_trade_count",
            "large_trade_count",
        ]
        for field_name in non_negative_fields:
            value = values.get(field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be >= 0")

        ratio = values.get("taker_buy_sell_ratio")
        if ratio is not None and ratio < 0:
            raise ValueError("taker_buy_sell_ratio must be >= 0")

        imbalance = values.get("orderbook_imbalance")
        if imbalance is not None and not -1.0 <= imbalance <= 1.0:
            raise ValueError("orderbook_imbalance must be between -1 and 1")

        return values


class MarketStructureSnapshotBase(MarketAuxiliarySnapshotBase):
    window_start_timestamp: int
    window_end_timestamp: int
    lookback: int
    previous_high: float
    previous_low: float
    current_high: float
    current_low: float
    close: float
    higher_high: bool = False
    lower_low: bool = False
    breakout_direction: str = "none"
    structure_state: str = "range"

    @property
    def liquidity_range_width(self) -> float:
        return self.previous_high - self.previous_low

    @classmethod
    def market_structure_values_must_be_valid(cls, values):
        window_start = values.get("window_start_timestamp")
        window_end = values.get("window_end_timestamp")
        if window_start is not None and window_end is not None and window_start > window_end:
            raise ValueError("window_start_timestamp must be <= window_end_timestamp")

        lookback = values.get("lookback")
        if lookback is not None and lookback <= 0:
            raise ValueError("lookback must be > 0")

        previous_high = values.get("previous_high")
        previous_low = values.get("previous_low")
        current_high = values.get("current_high")
        current_low = values.get("current_low")
        close = values.get("close")
        if previous_high is not None and previous_low is not None and previous_high < previous_low:
            raise ValueError("previous_high must be >= previous_low")
        if current_high is not None and current_low is not None and current_high < current_low:
            raise ValueError("current_high must be >= current_low")
        if close is not None and current_high is not None and close > current_high:
            raise ValueError("close must be <= current_high")
        if close is not None and current_low is not None and close < current_low:
            raise ValueError("close must be >= current_low")

        breakout_direction = values.get("breakout_direction")
        if breakout_direction not in {"up", "down", "none"}:
            raise ValueError("breakout_direction must be up, down, or none")

        structure_state = values.get("structure_state")
        if structure_state not in {"breakout", "range"}:
            raise ValueError("structure_state must be breakout or range")

        return values


class CrossMarketSnapshotBase(MarketAuxiliarySnapshotBase):
    window_start_timestamp: int
    window_end_timestamp: int
    spot_symbol: str
    perpetual_symbol: str
    spot_price: float
    perpetual_price: float
    funding_rate: Optional[float] = None
    next_funding_timestamp: Optional[int] = None

    @property
    def basis(self) -> float:
        return self.perpetual_price - self.spot_price

    @property
    def basis_rate(self) -> float:
        return self.basis / self.spot_price

    @classmethod
    def cross_market_values_must_be_valid(cls, values):
        window_start = values.get("window_start_timestamp")
        window_end = values.get("window_end_timestamp")
        if window_start is not None and window_end is not None and window_start > window_end:
            raise ValueError("window_start_timestamp must be <= window_end_timestamp")

        spot_price = values.get("spot_price")
        perpetual_price = values.get("perpetual_price")
        if spot_price is not None and spot_price <= 0:
            raise ValueError("spot_price must be > 0")
        if perpetual_price is not None and perpetual_price <= 0:
            raise ValueError("perpetual_price must be > 0")

        spot_symbol = values.get("spot_symbol")
        perpetual_symbol = values.get("perpetual_symbol")
        if spot_symbol is not None and not spot_symbol.strip():
            raise ValueError("spot_symbol must not be empty")
        if perpetual_symbol is not None and not perpetual_symbol.strip():
            raise ValueError("perpetual_symbol must not be empty")

        next_funding_timestamp = values.get("next_funding_timestamp")
        if next_funding_timestamp is not None and next_funding_timestamp < 0:
            raise ValueError("next_funding_timestamp must be >= 0")

        return values


class FeatureSnapshotBase(SmartQTFModel):
    snapshot_id: str
    timestamp: int
    symbol: str
    timeframe: str
    as_of_timestamp: int
    feature_set_id: str
    feature_set_version: str
    values: Dict[str, FeatureValue] = Field(default_factory=dict)
    source_window_start: Optional[int] = None
    source_window_end: Optional[int] = None
    is_complete_bar: bool = True
    trace: Optional[TraceContext] = None

    @property
    def feature_names(self) -> List[str]:
        return sorted(self.values.keys())

    @classmethod
    def from_feature_series(
        cls,
        feature_series: Mapping[str, Sequence[FeatureValue]],
        index: int,
        *,
        snapshot_id: str,
        timestamp: int,
        symbol: str,
        timeframe: str,
        as_of_timestamp: int,
        feature_set_id: str,
        feature_set_version: str,
        source_window_start: Optional[int] = None,
        source_window_end: Optional[int] = None,
        is_complete_bar: bool = True,
        trace: Optional[TraceContext] = None,
    ):
        values = {}
        for feature_name, series in feature_series.items():
            if index < 0 or index >= len(series):
                raise ValueError("feature series index out of range")
            values[feature_name] = series[index]

        return cls(
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            symbol=symbol,
            timeframe=timeframe,
            as_of_timestamp=as_of_timestamp,
            feature_set_id=feature_set_id,
            feature_set_version=feature_set_version,
            values=values,
            source_window_start=source_window_start,
            source_window_end=source_window_end,
            is_complete_bar=is_complete_bar,
            trace=trace,
        )

    @classmethod
    def values_must_have_named_features(cls, value):
        if not value:
            raise ValueError("feature snapshot values must not be empty")
        for feature_name in value:
            if not feature_name or not feature_name.strip():
                raise ValueError("feature names must not be empty")
        return value

    @classmethod
    def source_window_must_be_ordered(cls, values):
        start = values.get("source_window_start")
        end = values.get("source_window_end")
        if start is not None and end is not None and start > end:
            raise ValueError("source_window_start must be <= source_window_end")
        return values

    @classmethod
    def as_of_timestamp_must_not_read_future(cls, values):
        timestamp = values.get("timestamp")
        as_of_timestamp = values.get("as_of_timestamp")
        if timestamp is not None and as_of_timestamp is not None and as_of_timestamp > timestamp:
            raise ValueError("as_of_timestamp must be <= timestamp")
        return values


if hasattr(BaseModel, "model_validate"):

    class OpenInterestSnapshot(OpenInterestSnapshotBase):
        @model_validator(mode="after")
        def validate_snapshot(self):
            values = self.__dict__.copy()
            self.as_of_timestamp_must_not_read_future(values)
            self.numeric_values_must_be_valid(values)
            return self

    class FundingRateSnapshot(FundingRateSnapshotBase):
        @model_validator(mode="after")
        def validate_snapshot(self):
            values = self.__dict__.copy()
            self.as_of_timestamp_must_not_read_future(values)
            self.funding_values_must_be_valid(values)
            return self

    class NetflowSnapshot(NetflowSnapshotBase):
        @model_validator(mode="after")
        def validate_snapshot(self):
            values = self.__dict__.copy()
            self.as_of_timestamp_must_not_read_future(values)
            self.numeric_values_must_be_valid(values)
            self.explicit_netflow_must_match_flows(values)
            return self

    class OrderBookLevel(OrderBookLevelBase):
        @model_validator(mode="after")
        def validate_level(self):
            values = self.__dict__.copy()
            self.level_values_must_be_valid(values)
            return self

    class OrderBookSnapshot(OrderBookSnapshotBase):
        @model_validator(mode="after")
        def validate_snapshot(self):
            values = self.__dict__.copy()
            self.as_of_timestamp_must_not_read_future(values)
            self.book_must_be_valid(values)
            return self

    class OrderFlowSnapshot(OrderFlowSnapshotBase):
        @model_validator(mode="after")
        def validate_snapshot(self):
            values = self.__dict__.copy()
            self.as_of_timestamp_must_not_read_future(values)
            self.order_flow_values_must_be_valid(values)
            return self

    class MarketStructureSnapshot(MarketStructureSnapshotBase):
        @model_validator(mode="after")
        def validate_snapshot(self):
            values = self.__dict__.copy()
            self.as_of_timestamp_must_not_read_future(values)
            self.market_structure_values_must_be_valid(values)
            return self

    class CrossMarketSnapshot(CrossMarketSnapshotBase):
        @model_validator(mode="after")
        def validate_snapshot(self):
            values = self.__dict__.copy()
            self.as_of_timestamp_must_not_read_future(values)
            self.cross_market_values_must_be_valid(values)
            return self

    class FeatureSnapshot(FeatureSnapshotBase):
        @field_validator("values")
        @classmethod
        def validate_values(cls, value):
            return cls.values_must_have_named_features(value)

        @model_validator(mode="after")
        def validate_time_bounds(self):
            values = self.__dict__.copy()
            self.source_window_must_be_ordered(values)
            self.as_of_timestamp_must_not_read_future(values)
            return self

else:

    class OpenInterestSnapshot(OpenInterestSnapshotBase):
        @root_validator
        def validate_snapshot(cls, values):
            cls.as_of_timestamp_must_not_read_future(values)
            cls.numeric_values_must_be_valid(values)
            return values

    class FundingRateSnapshot(FundingRateSnapshotBase):
        @root_validator
        def validate_snapshot(cls, values):
            cls.as_of_timestamp_must_not_read_future(values)
            cls.funding_values_must_be_valid(values)
            return values

    class NetflowSnapshot(NetflowSnapshotBase):
        @root_validator
        def validate_snapshot(cls, values):
            cls.as_of_timestamp_must_not_read_future(values)
            cls.numeric_values_must_be_valid(values)
            cls.explicit_netflow_must_match_flows(values)
            return values

    class OrderBookLevel(OrderBookLevelBase):
        @root_validator
        def validate_level(cls, values):
            cls.level_values_must_be_valid(values)
            return values

    class OrderBookSnapshot(OrderBookSnapshotBase):
        @root_validator
        def validate_snapshot(cls, values):
            cls.as_of_timestamp_must_not_read_future(values)
            cls.book_must_be_valid(values)
            return values

    class OrderFlowSnapshot(OrderFlowSnapshotBase):
        @root_validator
        def validate_snapshot(cls, values):
            cls.as_of_timestamp_must_not_read_future(values)
            cls.order_flow_values_must_be_valid(values)
            return values

    class MarketStructureSnapshot(MarketStructureSnapshotBase):
        @root_validator
        def validate_snapshot(cls, values):
            cls.as_of_timestamp_must_not_read_future(values)
            cls.market_structure_values_must_be_valid(values)
            return values

    class CrossMarketSnapshot(CrossMarketSnapshotBase):
        @root_validator
        def validate_snapshot(cls, values):
            cls.as_of_timestamp_must_not_read_future(values)
            cls.cross_market_values_must_be_valid(values)
            return values

    class FeatureSnapshot(FeatureSnapshotBase):
        @validator("values")
        def validate_values(cls, value):
            return cls.values_must_have_named_features(value)

        @root_validator
        def validate_time_bounds(cls, values):
            cls.source_window_must_be_ordered(values)
            cls.as_of_timestamp_must_not_read_future(values)
            return values
