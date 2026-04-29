from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

try:
    from pydantic import field_validator, model_validator
except ImportError:
    field_validator = None
    model_validator = None
    from pydantic import root_validator, validator

from quant.schemas.base import SmartQTFModel
from quant.schemas.enums import PayloadSource


class RuntimeHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class RuntimeHealthSnapshotBase(SmartQTFModel):
    run_id: str
    source: PayloadSource = PayloadSource.PAPER
    observed_at: int
    status: RuntimeHealthStatus
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    data_latency_ms: int = 0
    pipeline_stage_durations_ms: Dict[str, int] = Field(default_factory=dict)
    order_failure_rate: float = 0.0
    risk_rejection_rate: float = 0.0
    broker_reconciliation_anomalies: int = 0
    kill_switch_active: bool = False
    alerts: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value

    @classmethod
    def non_negative_int(cls, value):
        if value < 0:
            raise ValueError("value must be greater than or equal to 0")
        return value

    @classmethod
    def rate_between_zero_and_one(cls, value):
        if value < 0 or value > 1:
            raise ValueError("rate must be between 0 and 1")
        return value

    @classmethod
    def validate_stage_durations(cls, value):
        for stage, duration_ms in value.items():
            if not stage:
                raise ValueError("stage name must not be empty")
            if duration_ms < 0:
                raise ValueError("stage duration must be greater than or equal to 0")
        return value

    @classmethod
    def validate_snapshot(cls, values):
        status = values.get("status")
        alerts = values.get("alerts") or []
        kill_switch_active = values.get("kill_switch_active")
        broker_reconciliation_anomalies = values.get("broker_reconciliation_anomalies")

        if status == RuntimeHealthStatus.HEALTHY and alerts:
            raise ValueError("healthy snapshots must not include alerts")
        if status == RuntimeHealthStatus.HEALTHY and kill_switch_active:
            raise ValueError("healthy snapshots cannot have kill switch active")
        if status == RuntimeHealthStatus.HEALTHY and broker_reconciliation_anomalies:
            raise ValueError("healthy snapshots cannot include broker reconciliation anomalies")
        if status == RuntimeHealthStatus.CRITICAL and not alerts:
            raise ValueError("critical snapshots require at least one alert")
        return values


if hasattr(BaseModel, "model_validate"):

    class RuntimeHealthSnapshot(RuntimeHealthSnapshotBase):
        @field_validator("run_id")
        @classmethod
        def validate_run_id(cls, value):
            return cls.non_empty_string(value)

        @field_validator("symbol", "timeframe")
        @classmethod
        def validate_optional_non_empty_string(cls, value):
            if value is None:
                return value
            return cls.non_empty_string(value)

        @field_validator("observed_at", "data_latency_ms", "broker_reconciliation_anomalies")
        @classmethod
        def validate_non_negative_int(cls, value):
            return cls.non_negative_int(value)

        @field_validator("order_failure_rate", "risk_rejection_rate")
        @classmethod
        def validate_rate(cls, value):
            return cls.rate_between_zero_and_one(value)

        @field_validator("pipeline_stage_durations_ms")
        @classmethod
        def validate_pipeline_stage_durations(cls, value):
            return cls.validate_stage_durations(value)

        @model_validator(mode="after")
        def validate_runtime_health_snapshot(self):
            values = self.__dict__.copy()
            self.validate_snapshot(values)
            return self

else:

    class RuntimeHealthSnapshot(RuntimeHealthSnapshotBase):
        @validator("run_id")
        def validate_run_id(cls, value):
            return cls.non_empty_string(value)

        @validator("symbol", "timeframe")
        def validate_optional_non_empty_string(cls, value):
            if value is None:
                return value
            return cls.non_empty_string(value)

        @validator("observed_at", "data_latency_ms", "broker_reconciliation_anomalies")
        def validate_non_negative_int(cls, value):
            return cls.non_negative_int(value)

        @validator("order_failure_rate", "risk_rejection_rate")
        def validate_rate(cls, value):
            return cls.rate_between_zero_and_one(value)

        @validator("pipeline_stage_durations_ms")
        def validate_pipeline_stage_durations(cls, value):
            return cls.validate_stage_durations(value)

        @root_validator
        def validate_runtime_health_snapshot(cls, values):
            return cls.validate_snapshot(values)
