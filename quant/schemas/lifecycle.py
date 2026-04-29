from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator, model_validator
else:
    from pydantic import root_validator, validator


class StrategyLifecycleStatus(str, Enum):
    CANDIDATE = "candidate"
    BACKTEST = "backtest"
    PAPER = "paper"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    RETIRED = "retired"
    ROLLED_BACK = "rolled_back"


class StrategyLifecycleAction(str, Enum):
    START_BACKTEST = "start_backtest"
    START_PAPER = "start_paper"
    APPROVE = "approve"
    DEPLOY = "deploy"
    RETIRE = "retire"
    ROLLBACK = "rollback"


class StrategyDeploymentRecordBase(SmartQTFModel):
    deployment_id: str
    strategy_id: str
    version: str
    status: StrategyLifecycleStatus
    environment: str
    symbol: Optional[str] = None
    previous_version: Optional[str] = None
    deployed_at: Optional[int] = None
    retired_at: Optional[int] = None
    reason_codes: List[str] = Field(default_factory=list)
    metadata: Dict[str, str] = Field(default_factory=dict)
    trace: Optional[TraceContext] = None

    @property
    def version_id(self) -> str:
        return f"{self.strategy_id}:{self.version}"

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value

    @classmethod
    def terminal_timestamps_are_consistent(cls, status, retired_at):
        if status in (
            StrategyLifecycleStatus.RETIRED,
            StrategyLifecycleStatus.ROLLED_BACK,
        ) and retired_at is None:
            raise ValueError("retired_at is required for terminal lifecycle states")


class StrategyLifecycleTransitionBase(SmartQTFModel):
    transition_id: str
    strategy_id: str
    version: str
    action: StrategyLifecycleAction
    from_status: StrategyLifecycleStatus
    to_status: StrategyLifecycleStatus
    generated_at: int
    reason_codes: List[str] = Field(default_factory=list)
    deployment_id: Optional[str] = None
    trace: Optional[TraceContext] = None

    @property
    def version_id(self) -> str:
        return f"{self.strategy_id}:{self.version}"

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value

    @classmethod
    def generated_at_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError("generated_at must be non-negative")
        return value


if hasattr(BaseModel, "model_validate"):

    class StrategyDeploymentRecord(StrategyDeploymentRecordBase):
        @field_validator("deployment_id", "strategy_id", "version", "environment")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @model_validator(mode="after")
        def validate_terminal_timestamps(self):
            self.terminal_timestamps_are_consistent(self.status, self.retired_at)
            return self

    class StrategyLifecycleTransition(StrategyLifecycleTransitionBase):
        @field_validator("transition_id", "strategy_id", "version")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @field_validator("generated_at")
        @classmethod
        def validate_generated_at(cls, value):
            return cls.generated_at_must_be_non_negative(value)

else:

    class StrategyDeploymentRecord(StrategyDeploymentRecordBase):
        @validator("deployment_id", "strategy_id", "version", "environment")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @root_validator
        def validate_terminal_timestamps(cls, values):
            cls.terminal_timestamps_are_consistent(
                values.get("status"), values.get("retired_at")
            )
            return values

    class StrategyLifecycleTransition(StrategyLifecycleTransitionBase):
        @validator("transition_id", "strategy_id", "version")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @validator("generated_at")
        def validate_generated_at(cls, value):
            return cls.generated_at_must_be_non_negative(value)
