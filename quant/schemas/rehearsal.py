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


class RehearsalCheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class RehearsalCheckResultBase(SmartQTFModel):
    name: str
    status: RehearsalCheckStatus
    category: str
    message: str
    source: str
    details: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value


class ProductionRehearsalReportBase(SmartQTFModel):
    report_id: str
    generated_at: int
    config_path: str
    success: bool
    checks: List[RehearsalCheckResultBase]
    preflight_summary: Dict[str, Any] = Field(default_factory=dict)
    connectivity_summary: Dict[str, Any] = Field(default_factory=dict)
    dry_run_summary: Optional[Dict[str, Any]] = None
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
    def checks_must_not_be_empty(cls, value):
        if not value:
            raise ValueError("production rehearsal reports require at least one check")
        return value

    @classmethod
    def validate_report(cls, values):
        success = values.get("success")
        checks = values.get("checks") or []
        has_failures = any(check.status == RehearsalCheckStatus.FAIL for check in checks)
        if success and has_failures:
            raise ValueError("successful rehearsal reports must not include failed checks")
        return values


if hasattr(BaseModel, "model_validate"):

    class RehearsalCheckResult(RehearsalCheckResultBase):
        @field_validator("name", "category", "message", "source")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class ProductionRehearsalReport(ProductionRehearsalReportBase):
        checks: List[RehearsalCheckResult]

        @field_validator("report_id", "config_path")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @field_validator("generated_at")
        @classmethod
        def validate_non_negative_int(cls, value):
            return cls.non_negative_int(value)

        @field_validator("checks")
        @classmethod
        def validate_checks(cls, value):
            return cls.checks_must_not_be_empty(value)

        @model_validator(mode="after")
        def validate_production_rehearsal_report(self):
            values = self.__dict__.copy()
            self.validate_report(values)
            return self

else:

    class RehearsalCheckResult(RehearsalCheckResultBase):
        @validator("name", "category", "message", "source")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class ProductionRehearsalReport(ProductionRehearsalReportBase):
        checks: List[RehearsalCheckResult]

        @validator("report_id", "config_path")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @validator("generated_at")
        def validate_non_negative_int(cls, value):
            return cls.non_negative_int(value)

        @validator("checks")
        def validate_checks(cls, value):
            return cls.checks_must_not_be_empty(value)

        @root_validator
        def validate_production_rehearsal_report(cls, values):
            return cls.validate_report(values)
