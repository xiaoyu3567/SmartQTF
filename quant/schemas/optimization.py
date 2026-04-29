from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator
else:
    from pydantic import validator


class StrategyVersionStatus(str, Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETIRED = "retired"


class StrategyPromotionAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class StrategyValidationSliceKind(str, Enum):
    IN_SAMPLE = "in_sample"
    OUT_OF_SAMPLE = "out_of_sample"
    WALK_FORWARD = "walk_forward"
    MONTE_CARLO = "monte_carlo"


class StrategyVersionBase(SmartQTFModel):
    strategy_id: str
    version: str
    status: StrategyVersionStatus = StrategyVersionStatus.DRAFT
    created_at: int
    code_ref: str
    config_hash: str
    parameters: Dict[str, float] = Field(default_factory=dict)
    parent_version: Optional[str] = None
    changelog: List[str] = Field(default_factory=list)
    validation_report_id: Optional[str] = None
    trace: Optional[TraceContext] = None

    @property
    def version_id(self) -> str:
        return f"{self.strategy_id}:{self.version}"

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value


class StrategyValidationMetricsBase(SmartQTFModel):
    report_id: str
    generated_at: int
    trade_count: int
    total_net_pnl: float
    max_drawdown: float
    win_rate: float = Field(ge=0.0, le=1.0)
    sharpe_ratio: Optional[float] = None
    validation_slices: List["StrategyValidationSliceBase"] = Field(default_factory=list)
    monte_carlo_survival_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @classmethod
    def counts_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError("counts must be non-negative")
        return value

    @classmethod
    def drawdown_must_be_non_negative(cls, value):
        if value < 0.0:
            raise ValueError("max_drawdown must be non-negative")
        return value


class StrategyValidationArtifactBase(SmartQTFModel):
    artifact_id: str
    strategy_id: str
    candidate_version: str
    symbol: str
    generated_at: int
    metrics: StrategyValidationMetricsBase
    source_report_id: Optional[str] = None
    source_path: Optional[str] = None
    trace: Optional[TraceContext] = None

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value


class StrategyPromotionDecisionBase(SmartQTFModel):
    decision_id: str
    strategy_id: str
    candidate_version: str
    baseline_version: Optional[str] = None
    action: StrategyPromotionAction
    generated_at: int
    reason_codes: List[str] = Field(default_factory=list)
    metrics: StrategyValidationMetricsBase
    trace: Optional[TraceContext] = None

    @property
    def approved(self) -> bool:
        return self.action == StrategyPromotionAction.APPROVE


class SymbolOptimizationQueueRecordBase(SmartQTFModel):
    queue_id: str
    symbol: str
    created_at: int
    candidate: StrategyVersionBase
    validation_metrics: Optional[StrategyValidationMetricsBase] = None
    promotion_decision: Optional[StrategyPromotionDecisionBase] = None
    trace: Optional[TraceContext] = None

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value


class StrategyValidationSliceBase(SmartQTFModel):
    name: str
    kind: StrategyValidationSliceKind
    trade_count: int
    total_net_pnl: float
    max_drawdown: float
    win_rate: float = Field(ge=0.0, le=1.0)
    sharpe_ratio: Optional[float] = None

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value

    @classmethod
    def counts_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError("counts must be non-negative")
        return value

    @classmethod
    def drawdown_must_be_non_negative(cls, value):
        if value < 0.0:
            raise ValueError("max_drawdown must be non-negative")
        return value


if hasattr(BaseModel, "model_validate"):

    class StrategyValidationSlice(StrategyValidationSliceBase):
        @field_validator("name")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @field_validator("trade_count")
        @classmethod
        def validate_trade_count(cls, value):
            return cls.counts_must_be_non_negative(value)

        @field_validator("max_drawdown")
        @classmethod
        def validate_max_drawdown(cls, value):
            return cls.drawdown_must_be_non_negative(value)

    class StrategyVersion(StrategyVersionBase):
        @field_validator("strategy_id", "version", "code_ref", "config_hash")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class StrategyValidationMetrics(StrategyValidationMetricsBase):
        @field_validator("trade_count")
        @classmethod
        def validate_trade_count(cls, value):
            return cls.counts_must_be_non_negative(value)

        @field_validator("max_drawdown")
        @classmethod
        def validate_max_drawdown(cls, value):
            return cls.drawdown_must_be_non_negative(value)

    class StrategyValidationArtifact(StrategyValidationArtifactBase):
        metrics: StrategyValidationMetrics

        @field_validator("artifact_id", "strategy_id", "candidate_version", "symbol")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class StrategyPromotionDecision(StrategyPromotionDecisionBase):
        metrics: StrategyValidationMetrics

    class SymbolOptimizationQueueRecord(SymbolOptimizationQueueRecordBase):
        candidate: StrategyVersion
        validation_metrics: Optional[StrategyValidationMetrics] = None
        promotion_decision: Optional[StrategyPromotionDecision] = None

        @field_validator("queue_id", "symbol")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

else:

    class StrategyValidationSlice(StrategyValidationSliceBase):
        @validator("name")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @validator("trade_count")
        def validate_trade_count(cls, value):
            return cls.counts_must_be_non_negative(value)

        @validator("max_drawdown")
        def validate_max_drawdown(cls, value):
            return cls.drawdown_must_be_non_negative(value)

    class StrategyVersion(StrategyVersionBase):
        @validator("strategy_id", "version", "code_ref", "config_hash")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class StrategyValidationMetrics(StrategyValidationMetricsBase):
        @validator("trade_count")
        def validate_trade_count(cls, value):
            return cls.counts_must_be_non_negative(value)

        @validator("max_drawdown")
        def validate_max_drawdown(cls, value):
            return cls.drawdown_must_be_non_negative(value)

    class StrategyValidationArtifact(StrategyValidationArtifactBase):
        metrics: StrategyValidationMetrics

        @validator("artifact_id", "strategy_id", "candidate_version", "symbol")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class StrategyPromotionDecision(StrategyPromotionDecisionBase):
        metrics: StrategyValidationMetrics

    class SymbolOptimizationQueueRecord(SymbolOptimizationQueueRecordBase):
        candidate: StrategyVersion
        validation_metrics: Optional[StrategyValidationMetrics] = None
        promotion_decision: Optional[StrategyPromotionDecision] = None

        @validator("queue_id", "symbol")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)
