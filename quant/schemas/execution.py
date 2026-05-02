from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext
from quant.schemas.enums import (
    ExchangeErrorCategory,
    OrderKind,
    OrderStatus,
    PayloadSource,
    TimeInForce,
    TimeoutFailureKind,
    TimeoutRecoveryAction,
    TradeSide,
)

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator, model_validator
else:
    from pydantic import root_validator, validator


class OrderIntentBase(SmartQTFModel):
    order_intent_id: str
    decision_id: str
    client_order_id: str
    symbol: str
    side: TradeSide
    order_type: OrderKind
    quantity: float
    limit_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    post_only: bool = False
    risk_approved: bool = True
    created_at: int
    trace: Optional[TraceContext] = None

    @classmethod
    def quantity_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("quantity must be greater than 0.0")
        return value

    @classmethod
    def optional_price_must_be_positive(cls, value):
        if value is not None and value <= 0.0:
            raise ValueError("limit_price must be greater than 0.0")
        return value

    @classmethod
    def risk_must_be_approved(cls, value):
        if value is not True:
            raise ValueError("order intent must be risk approved before execution")
        return value


class BrokerOrderRequestBase(SmartQTFModel):
    client_order_id: str
    symbol: str
    side: TradeSide
    order_type: OrderKind
    quantity: float
    limit_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    trace: Optional[TraceContext] = None

    @classmethod
    def quantity_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("quantity must be greater than 0.0")
        return value

    @classmethod
    def optional_price_must_be_positive(cls, value):
        if value is not None and value <= 0.0:
            raise ValueError("limit_price must be greater than 0.0")
        return value


class BrokerReplaceOrderRequestBase(SmartQTFModel):
    original_client_order_id: str
    replacement_client_order_id: str
    symbol: str
    side: TradeSide
    order_type: OrderKind
    quantity: float
    limit_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    trace: Optional[TraceContext] = None

    @classmethod
    def quantity_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("quantity must be greater than 0.0")
        return value

    @classmethod
    def optional_price_must_be_positive(cls, value):
        if value is not None and value <= 0.0:
            raise ValueError("limit_price must be greater than 0.0")
        return value


class BrokerOrderResult(SmartQTFModel):
    client_order_id: str
    broker_order_id: Optional[str] = None
    symbol: str
    side: TradeSide
    status: OrderStatus
    requested_qty: float
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    rejection_code: Optional[str] = None
    rejection_reason: Optional[str] = None
    exchange_error_category: Optional[ExchangeErrorCategory] = None
    exchange_error_message: Optional[str] = None
    trace: Optional[TraceContext] = None

    @classmethod
    def qty_must_be_non_negative(cls, value):
        if value < 0.0:
            raise ValueError("quantity fields must be non-negative")
        return value

    @classmethod
    def optional_price_must_be_positive(cls, value):
        if value is not None and value <= 0.0:
            raise ValueError("avg_fill_price must be greater than 0.0")
        return value


class ExecutionFillEventBase(SmartQTFModel):
    fill_event_id: str
    client_order_id: str
    broker_order_id: Optional[str] = None
    symbol: str
    side: TradeSide
    status: OrderStatus
    fill_qty: float
    fill_price: float
    cumulative_filled_qty: float
    remaining_qty: float
    fill_index: int
    trace: Optional[TraceContext] = None

    @classmethod
    def fill_qty_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("fill_qty must be greater than 0.0")
        return value

    @classmethod
    def fill_price_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("fill_price must be greater than 0.0")
        return value

    @classmethod
    def qty_must_be_non_negative(cls, value):
        if value < 0.0:
            raise ValueError("cumulative and remaining quantities must be non-negative")
        return value


class ProtectiveExitPlanBase(SmartQTFModel):
    exit_plan_id: str
    parent_client_order_id: str
    symbol: str
    entry_side: TradeSide
    quantity: float
    stop_loss_price: float
    take_profit_price: Optional[float] = None
    created_at: int
    active: bool = True
    trace: Optional[TraceContext] = None
    metadata: Dict = Field(default_factory=dict)

    @classmethod
    def quantity_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("quantity must be greater than 0.0")
        return value

    @classmethod
    def price_must_be_positive(cls, value, field_name):
        if value <= 0.0:
            raise ValueError(f"{field_name} must be greater than 0.0")
        return value

    @classmethod
    def optional_price_must_be_positive(cls, value, field_name):
        if value is not None and value <= 0.0:
            raise ValueError(f"{field_name} must be greater than 0.0")
        return value

    @classmethod
    def exit_prices_must_match_entry_side(cls, entry_side, stop_loss_price, take_profit_price):
        if take_profit_price is None:
            return

        side = entry_side.value if hasattr(entry_side, "value") else entry_side
        if side == TradeSide.BUY.value and stop_loss_price >= take_profit_price:
            raise ValueError("long protective exits require stop_loss_price below take_profit_price")
        if side == TradeSide.SELL.value and stop_loss_price <= take_profit_price:
            raise ValueError("short protective exits require stop_loss_price above take_profit_price")


class ProtectiveOrderValidationMixin:
    @classmethod
    def non_empty_string(cls, value, field_name):
        if not value:
            raise ValueError(f"{field_name} must not be empty")
        return value

    @classmethod
    def quantity_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("quantity must be greater than 0.0")
        return value

    @classmethod
    def price_must_be_positive(cls, value, field_name):
        if value <= 0.0:
            raise ValueError(f"{field_name} must be greater than 0.0")
        return value

    @classmethod
    def optional_price_must_be_positive(cls, value, field_name):
        if value is not None and value <= 0.0:
            raise ValueError(f"{field_name} must be greater than 0.0")
        return value

    @classmethod
    def exit_prices_must_match_entry_side(cls, entry_side, stop_loss_price, take_profit_price):
        ProtectiveExitPlanBase.exit_prices_must_match_entry_side(
            entry_side,
            stop_loss_price,
            take_profit_price,
        )


class OrderConstraintViolation(SmartQTFModel):
    code: str
    message: str
    field: Optional[str] = None


class InstrumentOrderRulesBase(SmartQTFModel):
    symbol: str
    quantity_step: float
    min_quantity: float = 0.0
    max_quantity: Optional[float] = None
    price_tick: Optional[float] = None
    min_notional: float = 0.0
    trace: Optional[TraceContext] = None

    @classmethod
    def value_must_be_positive(cls, value, field_name):
        if value <= 0.0:
            raise ValueError(f"{field_name} must be greater than 0.0")
        return value

    @classmethod
    def value_must_be_non_negative(cls, value, field_name):
        if value < 0.0:
            raise ValueError(f"{field_name} must be non-negative")
        return value

    def validate_order_request(
        self,
        request: BrokerOrderRequestBase,
        reference_price: Optional[float] = None,
    ) -> List[OrderConstraintViolation]:
        violations: List[OrderConstraintViolation] = []

        if request.symbol != self.symbol:
            violations.append(
                OrderConstraintViolation(
                    code="symbol_mismatch",
                    field="symbol",
                    message=f"request symbol {request.symbol} does not match rules symbol {self.symbol}",
                )
            )

        if request.quantity < self.min_quantity:
            violations.append(
                OrderConstraintViolation(
                    code="quantity_below_minimum",
                    field="quantity",
                    message="quantity is below exchange minimum quantity",
                )
            )

        if self.max_quantity is not None and request.quantity > self.max_quantity:
            violations.append(
                OrderConstraintViolation(
                    code="quantity_above_maximum",
                    field="quantity",
                    message="quantity is above exchange maximum quantity",
                )
            )

        if not _is_multiple(request.quantity, self.quantity_step):
            violations.append(
                OrderConstraintViolation(
                    code="quantity_step_mismatch",
                    field="quantity",
                    message="quantity is not aligned to exchange quantity_step",
                )
            )

        if (
            self.price_tick is not None
            and request.limit_price is not None
            and not _is_multiple(request.limit_price, self.price_tick)
        ):
            violations.append(
                OrderConstraintViolation(
                    code="price_tick_mismatch",
                    field="limit_price",
                    message="limit_price is not aligned to exchange price_tick",
                )
            )

        notional_price = request.limit_price if request.limit_price is not None else reference_price
        if self.min_notional > 0.0 and notional_price is None:
            violations.append(
                OrderConstraintViolation(
                    code="reference_price_required",
                    field="reference_price",
                    message="reference_price is required to validate market order min_notional",
                )
            )
        elif notional_price is not None and request.quantity * notional_price < self.min_notional:
            violations.append(
                OrderConstraintViolation(
                    code="notional_below_minimum",
                    field="min_notional",
                    message="order notional is below exchange min_notional",
                )
            )

        return violations


class LiveOrderGateDecisionBase(SmartQTFModel):
    approved: bool
    reason_codes: List[str]
    message: str
    checked_at: int
    live_mode_enabled: bool = False
    allow_live_orders: bool = False
    risk_approved: bool = False
    portfolio_allocation_approved: bool = False
    dry_run: bool = True
    credential_mode: str = "missing"
    preflight_artifact_path: Optional[str] = None
    preflight_generated_at: Optional[int] = None
    preflight_artifact_age_seconds: Optional[int] = None
    preflight_max_age_seconds: Optional[int] = None
    kill_switch_active: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def reason_codes_must_not_be_empty(cls, value):
        if not value:
            raise ValueError("reason_codes must not be empty")
        return value

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value

    @classmethod
    def non_negative_int(cls, value, field_name):
        if value is not None and value < 0:
            raise ValueError(f"{field_name} must be greater than or equal to 0")
        return value


class ExchangeReadinessRequest(SmartQTFModel):
    request_id: str
    broker_name: str
    symbol: str
    requested_at: int
    desired_leverage: Optional[float] = Field(default=None, gt=0.0)
    max_leverage: Optional[float] = Field(default=None, gt=0.0)
    margin_mode: Optional[str] = None
    position_mode: Optional[str] = None
    td_mode: Optional[str] = None
    max_server_time_drift_ms: Optional[int] = Field(default=1000, ge=0)
    max_spread_bps: Optional[float] = Field(default=None, ge=0.0)
    max_slippage_bps: Optional[float] = Field(default=None, ge=0.0)
    min_rate_limit_remaining: Optional[int] = Field(default=None, ge=0)
    require_trading_enabled: bool = True
    require_instrument_rules: bool = True
    require_market_snapshot: bool = True
    reference_price: Optional[float] = Field(default=None, gt=0.0)
    instrument_rules: Optional[InstrumentOrderRulesBase] = None
    market_snapshot: Dict[str, Any] = Field(default_factory=dict)
    exchange_state: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExchangeReadinessCheck(SmartQTFModel):
    name: str
    passed: bool
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "error"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExchangeReadinessReport(SmartQTFModel):
    report_id: str
    broker_name: str
    symbol: str
    checked_at: int
    approved: bool
    reason_codes: List[str]
    checks: List[ExchangeReadinessCheck] = Field(default_factory=list)
    instrument_rules: Optional[InstrumentOrderRulesBase] = None
    market_snapshot: Dict[str, Any] = Field(default_factory=dict)
    exchange_state: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _is_multiple(value: float, step: float) -> bool:
    return Decimal(str(value)) % Decimal(str(step)) == Decimal("0")


if hasattr(BaseModel, "model_validate"):

    class OrderIntent(OrderIntentBase):
        @field_validator("quantity")
        @classmethod
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @field_validator("limit_price")
        @classmethod
        def validate_limit_price(cls, value):
            return cls.optional_price_must_be_positive(value)

        @field_validator("risk_approved")
        @classmethod
        def validate_risk_approved(cls, value):
            return cls.risk_must_be_approved(value)

        @model_validator(mode="after")
        def validate_limit_price_for_limit_orders(self):
            if self.order_type == OrderKind.LIMIT and self.limit_price is None:
                raise ValueError("limit orders require limit_price")
            return self

    class BrokerOrderRequest(BrokerOrderRequestBase):
        @field_validator("quantity")
        @classmethod
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @field_validator("limit_price")
        @classmethod
        def validate_limit_price(cls, value):
            return cls.optional_price_must_be_positive(value)

        @model_validator(mode="after")
        def validate_limit_price_for_limit_orders(self):
            if self.order_type == OrderKind.LIMIT and self.limit_price is None:
                raise ValueError("limit orders require limit_price")
            return self

    class BrokerReplaceOrderRequest(BrokerReplaceOrderRequestBase):
        @field_validator("quantity")
        @classmethod
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @field_validator("limit_price")
        @classmethod
        def validate_limit_price(cls, value):
            return cls.optional_price_must_be_positive(value)

        @model_validator(mode="after")
        def validate_replace_request(self):
            if self.order_type == OrderKind.LIMIT and self.limit_price is None:
                raise ValueError("limit orders require limit_price")
            if self.replacement_client_order_id == self.original_client_order_id:
                raise ValueError("replacement_client_order_id must differ from original_client_order_id")
            return self

    class ValidatedBrokerOrderResult(BrokerOrderResult):
        @field_validator("requested_qty", "filled_qty")
        @classmethod
        def validate_qty(cls, value):
            return cls.qty_must_be_non_negative(value)

        @field_validator("avg_fill_price")
        @classmethod
        def validate_avg_fill_price(cls, value):
            return cls.optional_price_must_be_positive(value)

        @model_validator(mode="after")
        def validate_fill_bounds(self):
            if self.filled_qty > self.requested_qty:
                raise ValueError("filled_qty cannot exceed requested_qty")
            return self

    BrokerOrderResult = ValidatedBrokerOrderResult

    class ExecutionFillEvent(ExecutionFillEventBase):
        @field_validator("fill_qty")
        @classmethod
        def validate_fill_qty(cls, value):
            return cls.fill_qty_must_be_positive(value)

        @field_validator("fill_price")
        @classmethod
        def validate_fill_price(cls, value):
            return cls.fill_price_must_be_positive(value)

        @field_validator("cumulative_filled_qty", "remaining_qty")
        @classmethod
        def validate_non_negative_qty(cls, value):
            return cls.qty_must_be_non_negative(value)

        @model_validator(mode="after")
        def validate_fill_totals(self):
            if self.fill_qty > self.cumulative_filled_qty:
                raise ValueError("fill_qty cannot exceed cumulative_filled_qty")
            return self

    class ProtectiveExitPlan(ProtectiveExitPlanBase):
        @field_validator("quantity")
        @classmethod
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @field_validator("stop_loss_price")
        @classmethod
        def validate_stop_loss_price(cls, value):
            return cls.price_must_be_positive(value, "stop_loss_price")

        @field_validator("take_profit_price")
        @classmethod
        def validate_take_profit_price(cls, value):
            return cls.optional_price_must_be_positive(value, "take_profit_price")

        @model_validator(mode="after")
        def validate_exit_price_direction(self):
            self.exit_prices_must_match_entry_side(
                self.entry_side,
                self.stop_loss_price,
                self.take_profit_price,
            )
            return self

    class InstrumentOrderRules(InstrumentOrderRulesBase):
        @field_validator("quantity_step")
        @classmethod
        def validate_quantity_step(cls, value):
            return cls.value_must_be_positive(value, "quantity_step")

        @field_validator("min_quantity", "min_notional")
        @classmethod
        def validate_non_negative(cls, value, info):
            return cls.value_must_be_non_negative(value, info.field_name)

        @field_validator("max_quantity", "price_tick")
        @classmethod
        def validate_optional_positive(cls, value, info):
            if value is None:
                return value
            return cls.value_must_be_positive(value, info.field_name)

        @model_validator(mode="after")
        def validate_quantity_bounds(self):
            if self.max_quantity is not None and self.max_quantity < self.min_quantity:
                raise ValueError("max_quantity must be greater than or equal to min_quantity")
            return self

    class LiveOrderGateDecision(LiveOrderGateDecisionBase):
        @field_validator("reason_codes")
        @classmethod
        def validate_reason_codes(cls, value):
            return cls.reason_codes_must_not_be_empty(value)

        @field_validator("message")
        @classmethod
        def validate_message(cls, value):
            return cls.non_empty_string(value)

        @field_validator(
            "checked_at",
            "preflight_generated_at",
            "preflight_artifact_age_seconds",
            "preflight_max_age_seconds",
        )
        @classmethod
        def validate_non_negative_int(cls, value, info):
            return cls.non_negative_int(value, info.field_name)

else:

    class OrderIntent(OrderIntentBase):
        @validator("quantity")
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @validator("limit_price")
        def validate_limit_price(cls, value):
            return cls.optional_price_must_be_positive(value)

        @validator("risk_approved")
        def validate_risk_approved(cls, value):
            return cls.risk_must_be_approved(value)

        @root_validator
        def validate_limit_price_for_limit_orders(cls, values):
            if values.get("order_type") == OrderKind.LIMIT and values.get("limit_price") is None:
                raise ValueError("limit orders require limit_price")
            return values

    class BrokerOrderRequest(BrokerOrderRequestBase):
        @validator("quantity")
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @validator("limit_price")
        def validate_limit_price(cls, value):
            return cls.optional_price_must_be_positive(value)

        @root_validator
        def validate_limit_price_for_limit_orders(cls, values):
            if values.get("order_type") == OrderKind.LIMIT and values.get("limit_price") is None:
                raise ValueError("limit orders require limit_price")
            return values

    class BrokerReplaceOrderRequest(BrokerReplaceOrderRequestBase):
        @validator("quantity")
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @validator("limit_price")
        def validate_limit_price(cls, value):
            return cls.optional_price_must_be_positive(value)

        @root_validator
        def validate_replace_request(cls, values):
            if values.get("order_type") == OrderKind.LIMIT and values.get("limit_price") is None:
                raise ValueError("limit orders require limit_price")
            if values.get("replacement_client_order_id") == values.get("original_client_order_id"):
                raise ValueError("replacement_client_order_id must differ from original_client_order_id")
            return values

    class ValidatedBrokerOrderResult(BrokerOrderResult):
        @validator("requested_qty", "filled_qty")
        def validate_qty(cls, value):
            return cls.qty_must_be_non_negative(value)

        @validator("avg_fill_price")
        def validate_avg_fill_price(cls, value):
            return cls.optional_price_must_be_positive(value)

        @root_validator
        def validate_fill_bounds(cls, values):
            if values.get("filled_qty", 0.0) > values.get("requested_qty", 0.0):
                raise ValueError("filled_qty cannot exceed requested_qty")
            return values

    BrokerOrderResult = ValidatedBrokerOrderResult

    class ExecutionFillEvent(ExecutionFillEventBase):
        @validator("fill_qty")
        def validate_fill_qty(cls, value):
            return cls.fill_qty_must_be_positive(value)

        @validator("fill_price")
        def validate_fill_price(cls, value):
            return cls.fill_price_must_be_positive(value)

        @validator("cumulative_filled_qty", "remaining_qty")
        def validate_non_negative_qty(cls, value):
            return cls.qty_must_be_non_negative(value)

        @root_validator
        def validate_fill_totals(cls, values):
            if values.get("fill_qty", 0.0) > values.get("cumulative_filled_qty", 0.0):
                raise ValueError("fill_qty cannot exceed cumulative_filled_qty")
            return values

    class ProtectiveExitPlan(ProtectiveExitPlanBase):
        @validator("quantity")
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @validator("stop_loss_price")
        def validate_stop_loss_price(cls, value):
            return cls.price_must_be_positive(value, "stop_loss_price")

        @validator("take_profit_price")
        def validate_take_profit_price(cls, value):
            return cls.optional_price_must_be_positive(value, "take_profit_price")

        @root_validator
        def validate_exit_price_direction(cls, values):
            cls.exit_prices_must_match_entry_side(
                values.get("entry_side"),
                values.get("stop_loss_price"),
                values.get("take_profit_price"),
            )
            return values

    class InstrumentOrderRules(InstrumentOrderRulesBase):
        @validator("quantity_step")
        def validate_quantity_step(cls, value):
            return cls.value_must_be_positive(value, "quantity_step")

        @validator("min_quantity", "min_notional")
        def validate_non_negative(cls, value, field):
            return cls.value_must_be_non_negative(value, field.name)

        @validator("max_quantity", "price_tick")
        def validate_optional_positive(cls, value, field):
            if value is None:
                return value
            return cls.value_must_be_positive(value, field.name)

        @root_validator
        def validate_quantity_bounds(cls, values):
            max_quantity = values.get("max_quantity")
            min_quantity = values.get("min_quantity", 0.0)
            if max_quantity is not None and max_quantity < min_quantity:
                raise ValueError("max_quantity must be greater than or equal to min_quantity")
            return values

    class LiveOrderGateDecision(LiveOrderGateDecisionBase):
        @validator("reason_codes")
        def validate_reason_codes(cls, value):
            return cls.reason_codes_must_not_be_empty(value)

        @validator("message")
        def validate_message(cls, value):
            return cls.non_empty_string(value)

        @validator(
            "checked_at",
            "preflight_generated_at",
            "preflight_artifact_age_seconds",
            "preflight_max_age_seconds",
        )
        def validate_non_negative_int(cls, value, field):
            return cls.non_negative_int(value, field.name)


class BrokerProtectiveOrderRequestBase(ProtectiveOrderValidationMixin, SmartQTFModel):
    protective_client_order_id: str
    parent_client_order_id: str
    symbol: str
    entry_side: TradeSide
    quantity: float
    stop_loss_price: float
    take_profit_price: Optional[float] = None
    stop_loss_client_order_id: Optional[str] = None
    take_profit_client_order_id: Optional[str] = None
    reduce_only: bool = True
    live_order_gate: LiveOrderGateDecision
    trace: Optional[TraceContext] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def exit_side(self) -> TradeSide:
        side = self.entry_side.value if hasattr(self.entry_side, "value") else self.entry_side
        return TradeSide.SELL if side == TradeSide.BUY.value else TradeSide.BUY


class BrokerProtectiveOrderResultBase(ProtectiveOrderValidationMixin, SmartQTFModel):
    protective_client_order_id: str
    parent_client_order_id: str
    broker_order_id: Optional[str] = None
    symbol: str
    exit_side: TradeSide
    native_order_type: str
    status: OrderStatus
    requested_qty: float
    stop_loss_price: float
    take_profit_price: Optional[float] = None
    stop_loss_client_order_id: Optional[str] = None
    take_profit_client_order_id: Optional[str] = None
    rejection_code: Optional[str] = None
    rejection_reason: Optional[str] = None
    exchange_error_category: Optional[ExchangeErrorCategory] = None
    exchange_error_message: Optional[str] = None
    live_order_gate: LiveOrderGateDecision
    trace: Optional[TraceContext] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BracketExecutionStatus:
    OPEN_PROTECTED = "OPEN_PROTECTED"
    PARTIALLY_EXECUTED_PROTECTED = "PARTIALLY_EXECUTED_PROTECTED"
    CANCELLED_NOT_FILLED = "CANCELLED_NOT_FILLED"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"
    NO_POSITION = "NO_POSITION"
    ENTRY_SUBMITTED_UNPROTECTED = "ENTRY_SUBMITTED_UNPROTECTED"
    ENTRY_REJECTED = "ENTRY_REJECTED"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    REJECTED = "REJECTED"


class BracketProtectiveLeg(SmartQTFModel):
    client_order_id: str
    price: float


class BracketExecutionPolicy(SmartQTFModel):
    native_order_type: str = "oco"
    protective_client_order_id: Optional[str] = None
    max_fill_wait_ms: int = 0
    cancel_if_not_filled: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BracketExecutionPlan(SmartQTFModel):
    execution_plan_id: str
    idempotency_key: str
    risk_decision_id: str
    allocation_id: str
    entry_order: BrokerOrderRequest
    stop_loss_order: BracketProtectiveLeg
    take_profit_order: Optional[BracketProtectiveLeg] = None
    policy: BracketExecutionPolicy = Field(default_factory=BracketExecutionPolicy)
    risk_approved: bool = True
    trace: Optional[TraceContext] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


if hasattr(BaseModel, "model_validate"):

    class BrokerProtectiveOrderRequest(BrokerProtectiveOrderRequestBase):
        @field_validator("protective_client_order_id", "parent_client_order_id", "symbol")
        @classmethod
        def validate_non_empty(cls, value, info):
            return cls.non_empty_string(value, info.field_name)

        @field_validator("quantity")
        @classmethod
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @field_validator("stop_loss_price")
        @classmethod
        def validate_stop_loss_price(cls, value):
            return cls.price_must_be_positive(value, "stop_loss_price")

        @field_validator("take_profit_price")
        @classmethod
        def validate_take_profit_price(cls, value):
            return cls.optional_price_must_be_positive(value, "take_profit_price")

        @model_validator(mode="after")
        def validate_exit_price_direction(self):
            self.exit_prices_must_match_entry_side(
                self.entry_side,
                self.stop_loss_price,
                self.take_profit_price,
            )
            return self

    class BrokerProtectiveOrderResult(BrokerProtectiveOrderResultBase):
        @field_validator("protective_client_order_id", "parent_client_order_id", "symbol", "native_order_type")
        @classmethod
        def validate_non_empty(cls, value, info):
            return cls.non_empty_string(value, info.field_name)

        @field_validator("requested_qty")
        @classmethod
        def validate_requested_qty(cls, value):
            return cls.quantity_must_be_positive(value)

        @field_validator("stop_loss_price")
        @classmethod
        def validate_stop_loss_price(cls, value):
            return cls.price_must_be_positive(value, "stop_loss_price")

        @field_validator("take_profit_price")
        @classmethod
        def validate_take_profit_price(cls, value):
            return cls.optional_price_must_be_positive(value, "take_profit_price")

        @model_validator(mode="after")
        def validate_exit_price_direction(self):
            self.exit_prices_must_match_entry_side(
                TradeSide.BUY if self.exit_side == TradeSide.SELL.value else TradeSide.SELL,
                self.stop_loss_price,
                self.take_profit_price,
            )
            return self

else:

    class BrokerProtectiveOrderRequest(BrokerProtectiveOrderRequestBase):
        @validator("protective_client_order_id", "parent_client_order_id", "symbol")
        def validate_non_empty(cls, value, field):
            return cls.non_empty_string(value, field.name)

        @validator("quantity")
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @validator("stop_loss_price")
        def validate_stop_loss_price(cls, value):
            return cls.price_must_be_positive(value, "stop_loss_price")

        @validator("take_profit_price")
        def validate_take_profit_price(cls, value):
            return cls.optional_price_must_be_positive(value, "take_profit_price")

        @root_validator
        def validate_exit_price_direction(cls, values):
            if values.get("entry_side") is not None and values.get("stop_loss_price") is not None:
                cls.exit_prices_must_match_entry_side(
                    values.get("entry_side"),
                    values.get("stop_loss_price"),
                    values.get("take_profit_price"),
                )
            return values

    class BrokerProtectiveOrderResult(BrokerProtectiveOrderResultBase):
        @validator("protective_client_order_id", "parent_client_order_id", "symbol", "native_order_type")
        def validate_non_empty(cls, value, field):
            return cls.non_empty_string(value, field.name)

        @validator("requested_qty")
        def validate_requested_qty(cls, value):
            return cls.quantity_must_be_positive(value)

        @validator("stop_loss_price")
        def validate_stop_loss_price(cls, value):
            return cls.price_must_be_positive(value, "stop_loss_price")

        @validator("take_profit_price")
        def validate_take_profit_price(cls, value):
            return cls.optional_price_must_be_positive(value, "take_profit_price")

        @root_validator
        def validate_exit_price_direction(cls, values):
            if values.get("exit_side") is not None and values.get("stop_loss_price") is not None:
                entry_side = TradeSide.BUY if values.get("exit_side") == TradeSide.SELL.value else TradeSide.SELL
                cls.exit_prices_must_match_entry_side(
                    entry_side,
                    values.get("stop_loss_price"),
                    values.get("take_profit_price"),
                )
            return values


class ProtectiveExitTriggerEventBase(SmartQTFModel):
    trigger_event_id: str
    exit_plan_id: str
    parent_client_order_id: str
    symbol: str
    trigger_type: Literal["stop_loss", "take_profit"]
    trigger_price: float
    market_price: float
    quantity: float
    exit_side: TradeSide
    triggered_at: int
    order_intent: OrderIntent
    trace: Optional[TraceContext] = None
    metadata: Dict = Field(default_factory=dict)

    @classmethod
    def quantity_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("quantity must be greater than 0.0")
        return value

    @classmethod
    def price_must_be_positive(cls, value, field_name):
        if value <= 0.0:
            raise ValueError(f"{field_name} must be greater than 0.0")
        return value


if hasattr(BaseModel, "model_validate"):

    class ProtectiveExitTriggerEvent(ProtectiveExitTriggerEventBase):
        @field_validator("quantity")
        @classmethod
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @field_validator("trigger_price", "market_price")
        @classmethod
        def validate_prices(cls, value, info):
            return cls.price_must_be_positive(value, info.field_name)

else:

    class ProtectiveExitTriggerEvent(ProtectiveExitTriggerEventBase):
        @validator("quantity")
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @validator("trigger_price", "market_price")
        def validate_prices(cls, value, field):
            return cls.price_must_be_positive(value, field.name)


class ReconciliationItem(SmartQTFModel):
    client_order_id: str
    action: str
    reason: str
    local_status: Optional[OrderStatus] = None
    broker_status: Optional[OrderStatus] = None
    broker_order_id: Optional[str] = None
    requested_qty: Optional[float] = None
    local_filled_qty: Optional[float] = None
    broker_filled_qty: Optional[float] = None
    local_avg_fill_price: Optional[float] = None
    broker_avg_fill_price: Optional[float] = None
    trace: Optional[TraceContext] = None


class ReconciliationReport(SmartQTFModel):
    broker_name: str
    checked_count: int
    matched_count: int
    drift_count: int
    missing_local_count: int
    missing_broker_count: int
    items: List[ReconciliationItem] = []


class TimeoutRecoveryDecision(SmartQTFModel):
    client_order_id: str
    action: TimeoutRecoveryAction
    reason: str
    status: OrderStatus = OrderStatus.UNKNOWN
    failure_kind: Optional[TimeoutFailureKind] = None
    recovery_attempt: int = 1
    max_recovery_attempts: Optional[int] = None
    recovery_query_attempted: bool = True
    broker_place_called: bool = False
    duplicate_order_guard_active: bool = True
    retry_after_seconds: Optional[int] = None
    recovered_order: Optional[BrokerOrderResult] = None
    error: Optional[str] = None
    trace: Optional[TraceContext] = None


class OrderLifecycleContract(SmartQTFModel):
    contract_version: str = "order_lifecycle_v1"
    source: PayloadSource
    execution_mode: str
    client_order_id: str
    symbol: str
    side: TradeSide
    order_status: OrderStatus
    lifecycle_state: str
    lifecycle_path: List[str] = Field(default_factory=list)
    requested_qty: float
    filled_qty: float = 0.0
    remaining_qty: float = 0.0
    order_intent: Optional[OrderIntent] = None
    transition_audit: List[Dict[str, Any]] = Field(default_factory=list)
    safety_flags: Dict[str, bool] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    trace: Optional[TraceContext] = None


class OrderStoreOrderRecord(SmartQTFModel):
    client_order_id: str
    broker_order_id: Optional[str] = None
    symbol: str
    side: TradeSide
    status: OrderStatus
    requested_qty: float
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    order_intent: Optional[OrderIntent] = None
    broker_result: Optional[BrokerOrderResult] = None
    raw_exchange_response: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    trace: Optional[TraceContext] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OrderStoreEventRecord(SmartQTFModel):
    sequence: int
    event_id: str
    client_order_id: str
    event_type: str
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    broker_order_id: Optional[str] = None
    event_time: Optional[int] = None
    reason: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    raw_exchange_response: Dict[str, Any] = Field(default_factory=dict)
    trace: Optional[TraceContext] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OrderStoreFillRecord(SmartQTFModel):
    fill_event_id: str
    client_order_id: str
    broker_order_id: Optional[str] = None
    symbol: str
    side: TradeSide
    status: OrderStatus
    fill_qty: float
    fill_price: float
    cumulative_filled_qty: float
    remaining_qty: float
    fill_index: int
    event_time: Optional[int] = None
    fill_event: ExecutionFillEvent
    raw_exchange_response: Dict[str, Any] = Field(default_factory=dict)
    trace: Optional[TraceContext] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OrderStoreIdempotencyKeyRecord(SmartQTFModel):
    client_order_id: str
    request_fingerprint: str
    request_payload: Dict[str, Any]
    status: OrderStatus = OrderStatus.UNKNOWN
    submit_intent_count: int = 0
    broker_order_id: Optional[str] = None
    result: Optional[BrokerOrderResult] = None
    last_error: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OrderStoreReconciliationRunRecord(SmartQTFModel):
    run_id: str
    broker_name: str
    checked_count: int
    matched_count: int
    drift_count: int
    missing_local_count: int
    missing_broker_count: int
    started_at: Optional[int] = None
    finished_at: Optional[int] = None
    report: ReconciliationReport
    raw_exchange_response: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OrderStoreReconstruction(SmartQTFModel):
    client_order_id: str
    order: OrderStoreOrderRecord
    events: List[OrderStoreEventRecord] = Field(default_factory=list)
    fills: List[OrderStoreFillRecord] = Field(default_factory=list)
    idempotency_key: Optional[OrderStoreIdempotencyKeyRecord] = None
    reconciliation_runs: List[OrderStoreReconciliationRunRecord] = Field(default_factory=list)
    replay_status: OrderStatus
    total_filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    lifecycle_path: List[str] = Field(default_factory=list)
