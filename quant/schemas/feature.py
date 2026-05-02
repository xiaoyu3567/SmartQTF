from typing import Dict, List, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator, model_validator
else:
    from pydantic import root_validator, validator


FeatureValue = Union[float, int, bool, str, None]


class FeatureAvailability(SmartQTFModel):
    feature_name: str
    available: bool
    reason: Optional[str] = None
    required_bars: Optional[int] = None
    actual_bars: Optional[int] = None


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
    window_start_timestamp: Optional[int] = None
    window_end_timestamp: Optional[int] = None
    trade_records_in_window: Optional[int] = None
    coverage_start: Optional[int] = None
    coverage_end: Optional[int] = None
    coverage_complete: Optional[bool] = None
    coverage_gap_reason: Optional[str] = None

    @property
    def computed_netflow(self) -> float:
        return self.inflow - self.outflow

    @classmethod
    def numeric_values_must_be_valid(cls, values):
        inflow = values.get("inflow")
        outflow = values.get("outflow")
        exchange_reserve = values.get("exchange_reserve")
        large_transfer_count = values.get("large_transfer_count")
        trade_records_in_window = values.get("trade_records_in_window")
        if inflow is not None and inflow < 0:
            raise ValueError("inflow must be >= 0")
        if outflow is not None and outflow < 0:
            raise ValueError("outflow must be >= 0")
        if exchange_reserve is not None and exchange_reserve < 0:
            raise ValueError("exchange_reserve must be >= 0")
        if large_transfer_count is not None and large_transfer_count < 0:
            raise ValueError("large_transfer_count must be >= 0")
        if trade_records_in_window is not None and trade_records_in_window < 0:
            raise ValueError("trade_records_in_window must be >= 0")
        return values

    @classmethod
    def coverage_window_must_be_valid(cls, values):
        window_start = values.get("window_start_timestamp")
        window_end = values.get("window_end_timestamp")
        coverage_start = values.get("coverage_start")
        coverage_end = values.get("coverage_end")

        if window_start is not None and window_end is not None and window_start > window_end:
            raise ValueError("window_start_timestamp must be <= window_end_timestamp")
        if coverage_start is not None and coverage_end is not None and coverage_start > coverage_end:
            raise ValueError("coverage_start must be <= coverage_end")
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
    feature_availability: Dict[str, FeatureAvailability] = Field(default_factory=dict)
    feature_parameters: Dict[str, Dict[str, FeatureValue]] = Field(default_factory=dict)
    source_window_start: Optional[int] = None
    source_window_end: Optional[int] = None
    is_complete_bar: bool = True
    requested_index: Optional[int] = None
    effective_index: Optional[int] = None
    input_bar_count: Optional[int] = None
    include_incomplete_last_bar: bool = False
    skipped_incomplete_last_bar: bool = False
    skipped_incomplete_bar_timestamp: Optional[int] = None
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
        requested_index: Optional[int] = None,
        effective_index: Optional[int] = None,
        input_bar_count: Optional[int] = None,
        include_incomplete_last_bar: bool = False,
        skipped_incomplete_last_bar: bool = False,
        skipped_incomplete_bar_timestamp: Optional[int] = None,
        feature_availability: Optional[Dict[str, FeatureAvailability]] = None,
        feature_parameters: Optional[Dict[str, Dict[str, FeatureValue]]] = None,
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
            feature_availability=feature_availability or {},
            feature_parameters=feature_parameters or {},
            source_window_start=source_window_start,
            source_window_end=source_window_end,
            is_complete_bar=is_complete_bar,
            requested_index=index if requested_index is None else requested_index,
            effective_index=index if effective_index is None else effective_index,
            input_bar_count=input_bar_count,
            include_incomplete_last_bar=include_incomplete_last_bar,
            skipped_incomplete_last_bar=skipped_incomplete_last_bar,
            skipped_incomplete_bar_timestamp=skipped_incomplete_bar_timestamp,
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

    @classmethod
    def audit_indexes_must_be_valid(cls, values):
        requested_index = values.get("requested_index")
        effective_index = values.get("effective_index")
        input_bar_count = values.get("input_bar_count")
        skipped_incomplete_last_bar = values.get("skipped_incomplete_last_bar")
        skipped_incomplete_bar_timestamp = values.get("skipped_incomplete_bar_timestamp")
        include_incomplete_last_bar = values.get("include_incomplete_last_bar")

        for field_name, value in {
            "requested_index": requested_index,
            "effective_index": effective_index,
        }.items():
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be >= 0")
        if input_bar_count is not None and input_bar_count <= 0:
            raise ValueError("input_bar_count must be > 0")
        if requested_index is not None and input_bar_count is not None and requested_index >= input_bar_count:
            raise ValueError("requested_index must be < input_bar_count")
        if effective_index is not None and input_bar_count is not None and effective_index >= input_bar_count:
            raise ValueError("effective_index must be < input_bar_count")
        if skipped_incomplete_last_bar:
            if skipped_incomplete_bar_timestamp is None:
                raise ValueError("skipped_incomplete_bar_timestamp is required when skipping incomplete bar")
            if include_incomplete_last_bar:
                raise ValueError("skipped incomplete bar cannot also be included")
            if requested_index is not None and effective_index is not None and effective_index >= requested_index:
                raise ValueError("effective_index must be before requested_index when skipping incomplete bar")
        return values

    @classmethod
    def feature_metadata_must_be_named(cls, values):
        feature_availability = values.get("feature_availability") or {}
        feature_parameters = values.get("feature_parameters") or {}

        for feature_name, availability in feature_availability.items():
            if not feature_name or not feature_name.strip():
                raise ValueError("feature availability names must not be empty")
            availability_name = (
                availability.feature_name
                if isinstance(availability, FeatureAvailability)
                else availability.get("feature_name")
            )
            if availability_name != feature_name:
                raise ValueError("feature availability key must match feature_name")

            available = (
                availability.available
                if isinstance(availability, FeatureAvailability)
                else availability.get("available")
            )
            reason = availability.reason if isinstance(availability, FeatureAvailability) else availability.get("reason")
            required_bars = (
                availability.required_bars
                if isinstance(availability, FeatureAvailability)
                else availability.get("required_bars")
            )
            actual_bars = (
                availability.actual_bars
                if isinstance(availability, FeatureAvailability)
                else availability.get("actual_bars")
            )

            if not available and not reason:
                raise ValueError("unavailable features must include a reason")
            if required_bars is not None and required_bars <= 0:
                raise ValueError("required_bars must be > 0")
            if actual_bars is not None and actual_bars < 0:
                raise ValueError("actual_bars must be >= 0")

        for feature_name, parameters in feature_parameters.items():
            if not feature_name or not feature_name.strip():
                raise ValueError("feature parameter names must not be empty")
            for parameter_name in parameters:
                if not parameter_name or not parameter_name.strip():
                    raise ValueError("feature parameter keys must not be empty")
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
            self.coverage_window_must_be_valid(values)
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
            self.audit_indexes_must_be_valid(values)
            self.feature_metadata_must_be_named(values)
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
            cls.coverage_window_must_be_valid(values)
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
            cls.audit_indexes_must_be_valid(values)
            cls.feature_metadata_must_be_named(values)
            return values


class FeatureQualityReportRef(SmartQTFModel):
    timeframe: str
    passed: bool
    checked_count: int
    issue_codes: List[str] = Field(default_factory=list)
    fatal_issue_codes: List[str] = Field(default_factory=list)
    first_timestamp: Optional[int] = None
    last_timestamp: Optional[int] = None
    has_incomplete_last_bar: bool = False

    def __init__(self, **data):
        super().__init__(**data)
        timeframe = self.timeframe.strip()
        if not timeframe:
            raise ValueError("timeframe must not be empty")
        if self.checked_count < 0:
            raise ValueError("checked_count must be >= 0")
        if self.first_timestamp is not None and self.first_timestamp < 0:
            raise ValueError("first_timestamp must be >= 0")
        if self.last_timestamp is not None and self.last_timestamp < 0:
            raise ValueError("last_timestamp must be >= 0")
        if (
            self.first_timestamp is not None
            and self.last_timestamp is not None
            and self.first_timestamp > self.last_timestamp
        ):
            raise ValueError("first_timestamp must be <= last_timestamp")
        if any(not issue_code or not issue_code.strip() for issue_code in self.issue_codes):
            raise ValueError("issue_codes must not contain empty values")
        if any(
            not issue_code or not issue_code.strip()
            for issue_code in self.fatal_issue_codes
        ):
            raise ValueError("fatal_issue_codes must not contain empty values")
        object.__setattr__(self, "timeframe", timeframe)


class MultiTimeframeFeatureSnapshot(SmartQTFModel):
    snapshot_id: str
    timestamp: int
    symbol: str
    execution_timeframe: str
    timeframe_snapshots: Dict[str, FeatureSnapshot] = Field(default_factory=dict)
    alignment_features: Dict[str, FeatureValue] = Field(default_factory=dict)
    quality_report_refs: Dict[str, FeatureQualityReportRef] = Field(default_factory=dict)
    trace: Optional[TraceContext] = None

    def __init__(self, **data):
        super().__init__(**data)
        snapshot_id = self.snapshot_id.strip()
        symbol = self.symbol.strip()
        execution_timeframe = self.execution_timeframe.strip()

        if not snapshot_id:
            raise ValueError("snapshot_id must not be empty")
        if self.timestamp < 0:
            raise ValueError("timestamp must be >= 0")
        if not symbol:
            raise ValueError("symbol must not be empty")
        if not execution_timeframe:
            raise ValueError("execution_timeframe must not be empty")
        if not self.timeframe_snapshots:
            raise ValueError("timeframe_snapshots must not be empty")
        if execution_timeframe not in self.timeframe_snapshots:
            raise ValueError("execution_timeframe must exist in timeframe_snapshots")

        normalized_snapshots: Dict[str, FeatureSnapshot] = {}
        for timeframe, snapshot in self.timeframe_snapshots.items():
            normalized_timeframe = timeframe.strip()
            if not normalized_timeframe:
                raise ValueError("timeframe snapshot keys must not be empty")
            if snapshot.symbol != symbol:
                raise ValueError("feature snapshot symbol must match multi-timeframe symbol")
            if snapshot.timeframe != normalized_timeframe:
                raise ValueError("timeframe snapshot key must match snapshot timeframe")
            normalized_snapshots[normalized_timeframe] = snapshot

        normalized_quality_refs: Dict[str, FeatureQualityReportRef] = {}
        for timeframe, ref in self.quality_report_refs.items():
            normalized_timeframe = timeframe.strip()
            if not normalized_timeframe:
                raise ValueError("quality_report_refs keys must not be empty")
            if ref.timeframe != normalized_timeframe:
                raise ValueError("quality_report_refs key must match ref timeframe")
            if normalized_timeframe not in normalized_snapshots:
                raise ValueError("quality_report_refs must reference computed timeframes")
            normalized_quality_refs[normalized_timeframe] = ref

        missing_quality_refs = set(normalized_snapshots) - set(normalized_quality_refs)
        if missing_quality_refs:
            raise ValueError("quality_report_refs must include every computed timeframe")

        for feature_name in self.alignment_features:
            if not feature_name or not feature_name.strip():
                raise ValueError("alignment feature names must not be empty")

        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "execution_timeframe", execution_timeframe)
        object.__setattr__(self, "timeframe_snapshots", normalized_snapshots)
        object.__setattr__(self, "quality_report_refs", normalized_quality_refs)

    @property
    def execution_snapshot(self) -> FeatureSnapshot:
        return self.timeframe_snapshots[self.execution_timeframe]

    @property
    def context_timeframes(self) -> List[str]:
        return [
            timeframe
            for timeframe in self.timeframe_snapshots
            if timeframe != self.execution_timeframe
        ]
