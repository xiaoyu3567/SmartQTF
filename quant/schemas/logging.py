from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext
from quant.schemas.decision import AIDecisionSuggestion, DecisionIntent
from quant.schemas.enums import OrderStatus, TradeSide
from quant.schemas.feature import FeatureSnapshot
from quant.schemas.risk import RiskDecision

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator
else:
    from pydantic import validator


class LogRecordBase(SmartQTFModel):
    event_id: str
    run_id: str
    timestamp: int
    trace: Optional[TraceContext] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DecisionLogRecord(LogRecordBase):
    record_type: str = "decision"
    decision: DecisionIntent
    feature_snapshot: Optional[FeatureSnapshot] = None


class AIDecisionSuggestionLogRecord(LogRecordBase):
    record_type: str = "ai_decision_suggestion"
    suggestion: AIDecisionSuggestion


class RiskDecisionLogRecord(LogRecordBase):
    record_type: str = "risk"
    symbol: str
    approved: bool
    reason_codes: list[str]
    risk_decision: RiskDecision
    strategy_id: Optional[str] = None
    decision_id: Optional[str] = None


class OrderLogRecordBase(LogRecordBase):
    record_type: str = "order"
    order_id: str
    client_order_id: str
    symbol: str
    side: TradeSide
    status: OrderStatus
    quantity: float
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    price: Optional[float] = None
    decision_id: Optional[str] = None

    @classmethod
    def quantity_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("quantity must be greater than 0.0")
        return value

    @classmethod
    def non_negative_quantity(cls, value):
        if value < 0.0:
            raise ValueError("filled and remaining quantities must be non-negative")
        return value

    @classmethod
    def optional_price_must_be_positive(cls, value):
        if value is not None and value <= 0.0:
            raise ValueError("price must be greater than 0.0")
        return value


class FillLogRecordBase(LogRecordBase):
    record_type: str = "fill"
    fill_id: str
    order_id: str
    client_order_id: str
    symbol: str
    side: TradeSide
    filled_quantity: float
    fill_price: float
    commission: float = 0.0
    liquidity: Optional[str] = None
    decision_id: Optional[str] = None

    @classmethod
    def filled_quantity_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("filled_quantity must be greater than 0.0")
        return value

    @classmethod
    def fill_price_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("fill_price must be greater than 0.0")
        return value

    @classmethod
    def commission_must_be_non_negative(cls, value):
        if value < 0.0:
            raise ValueError("commission must be non-negative")
        return value


if hasattr(BaseModel, "model_validate"):

    class OrderLogRecord(OrderLogRecordBase):
        @field_validator("quantity")
        @classmethod
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @field_validator("filled_quantity", "remaining_quantity")
        @classmethod
        def validate_non_negative_quantities(cls, value):
            return cls.non_negative_quantity(value)

        @field_validator("price")
        @classmethod
        def validate_optional_price(cls, value):
            return cls.optional_price_must_be_positive(value)

    class FillLogRecord(FillLogRecordBase):
        @field_validator("filled_quantity")
        @classmethod
        def validate_filled_quantity(cls, value):
            return cls.filled_quantity_must_be_positive(value)

        @field_validator("fill_price")
        @classmethod
        def validate_fill_price(cls, value):
            return cls.fill_price_must_be_positive(value)

        @field_validator("commission")
        @classmethod
        def validate_commission(cls, value):
            return cls.commission_must_be_non_negative(value)

else:

    class OrderLogRecord(OrderLogRecordBase):
        @validator("quantity")
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @validator("filled_quantity", "remaining_quantity")
        def validate_non_negative_quantities(cls, value):
            return cls.non_negative_quantity(value)

        @validator("price")
        def validate_optional_price(cls, value):
            return cls.optional_price_must_be_positive(value)

    class FillLogRecord(FillLogRecordBase):
        @validator("filled_quantity")
        def validate_filled_quantity(cls, value):
            return cls.filled_quantity_must_be_positive(value)

        @validator("fill_price")
        def validate_fill_price(cls, value):
            return cls.fill_price_must_be_positive(value)

        @validator("commission")
        def validate_commission(cls, value):
            return cls.commission_must_be_non_negative(value)
