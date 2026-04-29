from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext
from quant.schemas.enums import RegimeKind

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator, model_validator
else:
    from pydantic import root_validator, validator


class RegimeSnapshotBase(SmartQTFModel):
    regime_id: str
    timestamp: int
    symbol: str
    timeframe: str
    as_of_timestamp: int
    detector_id: str
    detector_version: str
    regime: RegimeKind
    confidence: float = 0.5
    reason_codes: List[str] = Field(default_factory=list)
    metrics: Dict[str, float] = Field(default_factory=dict)
    trace: Optional[TraceContext] = None

    @classmethod
    def confidence_must_be_probability(cls, value):
        if value < 0.0 or value > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @classmethod
    def time_bounds_must_be_replayable(cls, values):
        timestamp = values.get("timestamp")
        as_of_timestamp = values.get("as_of_timestamp")
        if timestamp is not None and as_of_timestamp is not None and as_of_timestamp > timestamp:
            raise ValueError("as_of_timestamp must be <= timestamp")
        return values


if hasattr(BaseModel, "model_validate"):

    class RegimeSnapshot(RegimeSnapshotBase):
        @field_validator("confidence")
        @classmethod
        def validate_confidence(cls, value):
            return cls.confidence_must_be_probability(value)

        @model_validator(mode="after")
        def validate_time_bounds(self):
            self.time_bounds_must_be_replayable(self.__dict__.copy())
            return self

else:

    class RegimeSnapshot(RegimeSnapshotBase):
        @validator("confidence")
        def validate_confidence(cls, value):
            return cls.confidence_must_be_probability(value)

        @root_validator
        def validate_time_bounds(cls, values):
            cls.time_bounds_must_be_replayable(values)
            return values
