from typing import Any, ClassVar, Dict, List, Optional

from pydantic import Field

try:
    from pydantic import field_validator, model_validator
except ImportError:
    field_validator = None
    model_validator = None
    from pydantic import root_validator, validator

from quant.schemas.base import LayerRejection, SmartQTFModel, TraceContext
from quant.schemas.enums import LayerName, OrderKind, TimeInForce
from quant.schemas.execution import BracketExecutionPlan, OrderIntent, ProtectiveExitPlan
from quant.schemas.portfolio import CapitalBudgetDecision
from quant.schemas.decision import TradeIntent


class RiskMarketConstraints(SmartQTFModel):
    symbol: str
    entry_price: float = Field(gt=0.0)
    min_notional: float = Field(default=0.0, ge=0.0)
    min_quantity: float = Field(default=0.0, ge=0.0)
    max_quantity: Optional[float] = Field(default=None, gt=0.0)
    quantity_step: float = Field(default=0.00000001, gt=0.0)
    price_tick: Optional[float] = Field(default=None, gt=0.0)
    max_leverage: float = Field(default=1.0, gt=0.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RiskPolicy(SmartQTFModel):
    policy_id: str = "risk_v2_policy"
    order_type: OrderKind = OrderKind.MARKET
    time_in_force: TimeInForce = TimeInForce.GTC
    desired_leverage: float = Field(default=1.0, gt=0.0)
    max_slippage_pct: float = Field(default=0.0, ge=0.0, lt=1.0)
    min_risk_reward: float = Field(default=1.0, ge=0.0)
    liquidation_buffer_pct: float = Field(default=0.0, ge=0.0, lt=1.0)
    allow_short_selling: bool = False
    kill_switch_active: bool = False
    reason_codes: List[str] = Field(default_factory=list)


class RiskEngineV2RequestBase(SmartQTFModel):
    FORBIDDEN_FIELD_NAMES: ClassVar[set[str]] = {
        "legacy_signal",
        "signal",
        "order_payload",
        "order_intent",
        "quantity",
    }

    request_id: str
    timestamp: int
    trade_intent: TradeIntent
    capital_budget: CapitalBudgetDecision
    market_constraints: RiskMarketConstraints
    risk_policy: RiskPolicy = Field(default_factory=RiskPolicy)
    trace: Optional[TraceContext] = None

    @classmethod
    def reject_legacy_fields(cls, values):
        if not isinstance(values, dict):
            return values
        forbidden = sorted(cls.FORBIDDEN_FIELD_NAMES.intersection(values))
        if forbidden:
            raise ValueError(
                "RiskEngineV2Request must not include legacy or executable fields: "
                + ", ".join(forbidden)
            )
        return values


class RiskSizingResult(SmartQTFModel):
    entry_price: float = Field(gt=0.0)
    stop_loss_price: float = Field(gt=0.0)
    take_profit_price: Optional[float] = Field(default=None, gt=0.0)
    stop_distance: float = Field(gt=0.0)
    risk_budget_usdt: float = Field(ge=0.0)
    raw_quantity: float = Field(ge=0.0)
    adjusted_quantity: float = Field(ge=0.0)
    notional: float = Field(ge=0.0)
    max_loss_usdt: float = Field(ge=0.0)
    unused_risk_budget_usdt: float = Field(ge=0.0)
    leverage: float = Field(gt=0.0)
    risk_reward: Optional[float] = Field(default=None, ge=0.0)
    constraints: Dict[str, float] = Field(default_factory=dict)
    reason_codes: List[str] = Field(default_factory=list)
    safety: Dict[str, bool] = Field(
        default_factory=lambda: {
            "network_used": False,
            "ai_provider_called": False,
            "broker_called": False,
            "live_orders_sent": False,
            "legacy_signal_used": False,
        }
    )


class RiskDecision(SmartQTFModel):
    approved: bool
    reason_codes: List[str]
    risk_decision_id: Optional[str] = None
    order_payload: Optional[Dict] = None
    order_intent: Optional[OrderIntent] = None
    protective_exit_plan: Optional[ProtectiveExitPlan] = None
    execution_order_plan: Optional[BracketExecutionPlan] = None
    sizing: Optional[RiskSizingResult] = None
    rejections: List[LayerRejection] = Field(default_factory=list)

    if field_validator is not None:
        @field_validator("reason_codes")
        @classmethod
        def reason_codes_must_not_be_empty(cls, value):
            if not value:
                raise ValueError("reason_codes must not be empty")
            return value

        @model_validator(mode="after")
        def approved_decisions_need_order(self):
            if (
                self.approved
                and self.order_payload is None
                and self.order_intent is None
                and self.execution_order_plan is None
            ):
                raise ValueError(
                    "approved risk decisions require order_payload, order_intent, or execution_order_plan"
                )
            return self
    else:
        @validator("reason_codes")
        def reason_codes_must_not_be_empty(cls, value):
            if not value:
                raise ValueError("reason_codes must not be empty")
            return value

        @root_validator
        def approved_decisions_need_order(cls, values):
            if (
                values.get("approved")
                and values.get("order_payload") is None
                and values.get("order_intent") is None
                and values.get("execution_order_plan") is None
            ):
                raise ValueError(
                    "approved risk decisions require order_payload, order_intent, or execution_order_plan"
                )
            return values

    @classmethod
    def approve(
        cls,
        order_payload: Optional[Dict],
        reason_codes: List[str],
        order_intent: Optional[OrderIntent] = None,
        protective_exit_plan: Optional[ProtectiveExitPlan] = None,
        execution_order_plan: Optional[BracketExecutionPlan] = None,
        sizing: Optional[RiskSizingResult] = None,
        risk_decision_id: Optional[str] = None,
    ):
        return cls(
            approved=True,
            reason_codes=reason_codes,
            risk_decision_id=risk_decision_id,
            order_payload=order_payload,
            order_intent=order_intent,
            protective_exit_plan=protective_exit_plan,
            execution_order_plan=execution_order_plan,
            sizing=sizing,
        )

    @classmethod
    def reject(
        cls,
        code: str,
        message: str,
        fatal: bool = False,
        risk_decision_id: Optional[str] = None,
    ):
        return cls(
            approved=False,
            reason_codes=[code],
            risk_decision_id=risk_decision_id,
            rejections=[
                LayerRejection(
                    layer=LayerName.RISK,
                    code=code,
                    message=message,
                    fatal=fatal,
                )
            ],
        )


class RiskKillSwitchTriggerInput(SmartQTFModel):
    timestamp: int = 0
    symbol: Optional[str] = None
    daily_loss_pct: Optional[float] = None
    consecutive_losses: int = 0
    api_failure_rate: Optional[float] = None
    metadata: Dict = Field(default_factory=dict)


class RiskKillSwitchDecision(SmartQTFModel):
    triggered: bool
    reason_codes: List[str]
    reason: Optional[str] = None
    trigger_input: RiskKillSwitchTriggerInput


if field_validator is not None:

    class RiskEngineV2Request(RiskEngineV2RequestBase):
        @model_validator(mode="before")
        @classmethod
        def validate_no_legacy_fields(cls, values):
            return cls.reject_legacy_fields(values)

else:

    class RiskEngineV2Request(RiskEngineV2RequestBase):
        @root_validator(pre=True)
        def validate_no_legacy_fields(cls, values):
            return cls.reject_legacy_fields(values)
