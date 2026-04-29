from typing import Dict, List, Optional

from pydantic import Field

try:
    from pydantic import field_validator, model_validator
except ImportError:
    field_validator = None
    model_validator = None
    from pydantic import root_validator, validator

from quant.schemas.base import LayerRejection, SmartQTFModel
from quant.schemas.enums import LayerName
from quant.schemas.execution import OrderIntent, ProtectiveExitPlan


class RiskDecision(SmartQTFModel):
    approved: bool
    reason_codes: List[str]
    order_payload: Optional[Dict] = None
    order_intent: Optional[OrderIntent] = None
    protective_exit_plan: Optional[ProtectiveExitPlan] = None
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
            if self.approved and self.order_payload is None and self.order_intent is None:
                raise ValueError("approved risk decisions require order_payload or order_intent")
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
            ):
                raise ValueError("approved risk decisions require order_payload or order_intent")
            return values

    @classmethod
    def approve(
        cls,
        order_payload: Optional[Dict],
        reason_codes: List[str],
        order_intent: Optional[OrderIntent] = None,
        protective_exit_plan: Optional[ProtectiveExitPlan] = None,
    ):
        return cls(
            approved=True,
            reason_codes=reason_codes,
            order_payload=order_payload,
            order_intent=order_intent,
            protective_exit_plan=protective_exit_plan,
        )

    @classmethod
    def reject(cls, code: str, message: str, fatal: bool = False):
        return cls(
            approved=False,
            reason_codes=[code],
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
