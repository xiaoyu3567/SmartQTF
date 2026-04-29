from typing import ClassVar, List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext
from quant.schemas.enums import TradeSide
from quant.schemas.execution import OrderIntent

if hasattr(BaseModel, "model_validate"):
    from pydantic import model_validator
else:
    from pydantic import root_validator


class CapitalAllocationRequestBase(SmartQTFModel):
    VALID_ALLOCATION_MODES: ClassVar[set] = {
        "fixed_weight",
        "kelly",
        "volatility_target",
        "kelly_volatility_target",
    }

    allocation_id: str
    timestamp: int
    symbol: str
    side: TradeSide
    price: float = Field(gt=0.0)
    account_equity: float = Field(gt=0.0)
    available_cash: float = Field(ge=0.0)
    target_weight: float = Field(gt=0.0, le=1.0)
    strategy_weight: float = Field(default=1.0, gt=0.0, le=1.0)
    current_symbol_notional: float = Field(default=0.0, ge=0.0)
    max_symbol_weight: float = Field(default=0.25, gt=0.0, le=1.0)
    allocation_mode: str = "fixed_weight"
    signal_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    win_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    payoff_ratio: Optional[float] = Field(default=None, gt=0.0)
    max_kelly_fraction: float = Field(default=0.25, gt=0.0, le=1.0)
    atr: Optional[float] = Field(default=None, gt=0.0)
    volatility: Optional[float] = Field(default=None, gt=0.0)
    target_volatility: Optional[float] = Field(default=None, gt=0.0)
    min_notional: float = Field(default=0.0, ge=0.0)
    reason_codes: List[str] = Field(default_factory=list)
    trace: Optional[TraceContext] = None

    def allocation_fields_are_valid(self):
        if self.allocation_mode not in self.VALID_ALLOCATION_MODES:
            raise ValueError("allocation_mode is not supported")

        if self.volatility is not None and self.target_volatility is None:
            raise ValueError("volatility requires target_volatility")

        if self.target_volatility is not None and self.volatility is None and self.atr is None:
            raise ValueError("target_volatility requires volatility or atr")

        if self.allocation_mode in {"kelly", "kelly_volatility_target"}:
            if self.win_rate is None or self.payoff_ratio is None:
                raise ValueError("kelly allocation requires win_rate and payoff_ratio")

        if self.allocation_mode in {"volatility_target", "kelly_volatility_target"}:
            if self.target_volatility is None:
                raise ValueError(
                    "volatility target allocation requires target_volatility"
                )
        return self


class CapitalAllocationDecision(SmartQTFModel):
    allocation_id: str
    approved: bool
    symbol: str
    side: TradeSide
    quantity: float = Field(ge=0.0)
    notional: float = Field(ge=0.0)
    price: float = Field(gt=0.0)
    reason_codes: List[str] = Field(default_factory=list)
    trace: Optional[TraceContext] = None


class PortfolioPositionSnapshot(SmartQTFModel):
    symbol: str
    strategy_id: str
    side: TradeSide
    quantity: float = Field(ge=0.0)
    avg_price: float = Field(gt=0.0)
    market_price: Optional[float] = Field(default=None, gt=0.0)
    correlation_group: Optional[str] = None

    @property
    def notional(self) -> float:
        price = self.market_price if self.market_price is not None else self.avg_price
        return self.quantity * price


class PortfolioOrderRequest(SmartQTFModel):
    strategy_id: str
    order_intent: OrderIntent
    reference_price: float = Field(gt=0.0)
    target_weight: float = Field(default=1.0, gt=0.0, le=1.0)
    risk_budget: float = Field(default=1.0, gt=0.0, le=1.0)
    correlation_group: Optional[str] = None
    reason_codes: List[str] = Field(default_factory=list)


class PortfolioAllocationItem(SmartQTFModel):
    strategy_id: str
    client_order_id: str
    symbol: str
    side: TradeSide
    approved: bool
    requested_quantity: float = Field(ge=0.0)
    allocated_quantity: float = Field(ge=0.0)
    requested_notional: float = Field(ge=0.0)
    allocated_notional: float = Field(ge=0.0)
    reference_price: float = Field(gt=0.0)
    correlation_group: Optional[str] = None
    reason_codes: List[str] = Field(default_factory=list)
    trace: Optional[TraceContext] = None


class PortfolioAllocationDecision(SmartQTFModel):
    allocation_id: str
    timestamp: int
    approved: bool
    account_equity: float = Field(gt=0.0)
    available_cash: float = Field(ge=0.0)
    allocated_notional: float = Field(ge=0.0)
    remaining_cash: float = Field(ge=0.0)
    allocations: List[PortfolioAllocationItem] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)
    trace: Optional[TraceContext] = None


class PortfolioAllocationRequest(SmartQTFModel):
    allocation_id: str
    timestamp: int
    account_equity: float = Field(gt=0.0)
    available_cash: float = Field(ge=0.0)
    orders: List[PortfolioOrderRequest] = Field(default_factory=list)
    positions: List[PortfolioPositionSnapshot] = Field(default_factory=list)
    max_symbol_weight: float = Field(default=0.25, gt=0.0, le=1.0)
    max_strategy_weight: float = Field(default=0.25, gt=0.0, le=1.0)
    max_correlation_group_weight: float = Field(default=0.40, gt=0.0, le=1.0)
    min_notional: float = Field(default=0.0, ge=0.0)
    trace: Optional[TraceContext] = None


if hasattr(BaseModel, "model_validate"):

    class CapitalAllocationRequest(CapitalAllocationRequestBase):
        @model_validator(mode="after")
        def validate_allocation_fields(self):
            return self.allocation_fields_are_valid()

else:

    class CapitalAllocationRequest(CapitalAllocationRequestBase):
        @root_validator
        def validate_allocation_fields(cls, values):
            allocation_mode = values.get("allocation_mode")
            volatility = values.get("volatility")
            target_volatility = values.get("target_volatility")
            atr = values.get("atr")
            win_rate = values.get("win_rate")
            payoff_ratio = values.get("payoff_ratio")

            if allocation_mode not in CapitalAllocationRequestBase.VALID_ALLOCATION_MODES:
                raise ValueError("allocation_mode is not supported")

            if volatility is not None and target_volatility is None:
                raise ValueError("volatility requires target_volatility")

            if target_volatility is not None and volatility is None and atr is None:
                raise ValueError("target_volatility requires volatility or atr")

            if allocation_mode in {"kelly", "kelly_volatility_target"}:
                if win_rate is None or payoff_ratio is None:
                    raise ValueError("kelly allocation requires win_rate and payoff_ratio")

            if allocation_mode in {"volatility_target", "kelly_volatility_target"}:
                if target_volatility is None:
                    raise ValueError(
                        "volatility target allocation requires target_volatility"
                    )
            return values
