from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

try:
    from pydantic import field_validator, model_validator
except ImportError:
    field_validator = None
    model_validator = None
    from pydantic import root_validator, validator

from quant.schemas.base import LayerRejection, SmartQTFModel
from quant.schemas.enums import PayloadSource


class PipelineStageStatus(str, Enum):
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    REJECTED = "rejected"
    ERROR = "error"


class PipelineRunContext(SmartQTFModel):
    run_id: str
    source: PayloadSource = PayloadSource.PAPER
    symbol: str
    timeframe: str
    started_at: int
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value


class PipelineStageResultBase(SmartQTFModel):
    stage: str
    status: PipelineStageStatus
    started_at: int
    ended_at: int
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    rejection: Optional[LayerRejection] = None
    error: Optional[str] = None
    skip_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def stage_must_not_be_empty(cls, value):
        if not value:
            raise ValueError("stage must not be empty")
        return value

    @classmethod
    def validate_stage_result(cls, values):
        status = values.get("status")
        started_at = values.get("started_at")
        ended_at = values.get("ended_at")
        rejection = values.get("rejection")
        error = values.get("error")
        skip_reason = values.get("skip_reason")

        if started_at is not None and ended_at is not None and ended_at < started_at:
            raise ValueError("ended_at must be greater than or equal to started_at")
        if status == PipelineStageStatus.SKIPPED and not skip_reason:
            raise ValueError("skipped stages require skip_reason")
        if status == PipelineStageStatus.REJECTED and rejection is None:
            raise ValueError("rejected stages require rejection")
        if status == PipelineStageStatus.ERROR and not error:
            raise ValueError("error stages require error")
        if status == PipelineStageStatus.SUCCEEDED and (rejection is not None or error):
            raise ValueError("succeeded stages must not include rejection or error")
        return values


class PipelineRunReportBase(SmartQTFModel):
    context: PipelineRunContext
    stages: List[PipelineStageResultBase]
    finished_at: int
    success: bool
    final_output: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def stages_must_not_be_empty(cls, value):
        if not value:
            raise ValueError("pipeline run reports require at least one stage")
        return value

    @classmethod
    def validate_report(cls, values):
        context = values.get("context")
        stages = values.get("stages") or []
        finished_at = values.get("finished_at")
        success = values.get("success")
        errors = values.get("errors") or []

        if context is not None and finished_at is not None and finished_at < context.started_at:
            raise ValueError("finished_at must be greater than or equal to context.started_at")
        if success and errors:
            raise ValueError("successful pipeline reports must not include errors")
        if success and any(stage.status == PipelineStageStatus.ERROR for stage in stages):
            raise ValueError("successful pipeline reports must not include error stages")
        return values


class PipelineSymbolRunRequestBase(SmartQTFModel):
    symbol: str
    timeframe: str
    index: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value

    @classmethod
    def validate_request(cls, values):
        index = values.get("index")
        if index is not None and index < 0:
            raise ValueError("index must be greater than or equal to 0")
        return values


class PipelineRuntimeRequestBase(PipelineSymbolRunRequestBase):
    source: PayloadSource = PayloadSource.PAPER
    run_id: Optional[str] = None

    @classmethod
    def validate_runtime_request(cls, values):
        values = cls.validate_request(values)
        run_id = values.get("run_id")
        if run_id is not None and not run_id:
            raise ValueError("run_id must not be empty")
        return values


class PipelineBatchRunReportBase(SmartQTFModel):
    batch_id: str
    source: PayloadSource = PayloadSource.PAPER
    requested_at: int
    requests: List[PipelineSymbolRunRequestBase]
    reports: List[PipelineRunReportBase]
    success: bool
    errors: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value

    @classmethod
    def validate_batch(cls, values):
        requests = values.get("requests") or []
        reports = values.get("reports") or []
        success = values.get("success")
        errors = values.get("errors") or []

        if not requests:
            raise ValueError("batch reports require at least one request")
        if len(reports) != len(requests):
            raise ValueError("batch reports must include one report per request")
        seen = set()
        for request in requests:
            key = (request.symbol, request.timeframe)
            if key in seen:
                raise ValueError("batch requests must not contain duplicate symbol/timeframe entries")
            seen.add(key)
        if success and errors:
            raise ValueError("successful batch reports must not include errors")
        if success and any(not report.success for report in reports):
            raise ValueError("successful batch reports must not include failed symbol reports")
        return values


if hasattr(BaseModel, "model_validate"):

    class PipelineRunContext(PipelineRunContext):
        @field_validator("run_id", "symbol", "timeframe")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class PipelineStageResult(PipelineStageResultBase):
        @field_validator("stage")
        @classmethod
        def validate_stage(cls, value):
            return cls.stage_must_not_be_empty(value)

        @model_validator(mode="after")
        def validate_result(self):
            values = self.__dict__.copy()
            self.validate_stage_result(values)
            return self

    class PipelineRunReport(PipelineRunReportBase):
        stages: List[PipelineStageResult]

        @field_validator("stages")
        @classmethod
        def validate_stages(cls, value):
            return cls.stages_must_not_be_empty(value)

        @model_validator(mode="after")
        def validate_run_report(self):
            values = self.__dict__.copy()
            self.validate_report(values)
            return self

    class PipelineSymbolRunRequest(PipelineSymbolRunRequestBase):
        @field_validator("symbol", "timeframe")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @model_validator(mode="after")
        def validate_symbol_request(self):
            values = self.__dict__.copy()
            self.validate_request(values)
            return self

    class PipelineRuntimeRequest(PipelineRuntimeRequestBase):
        @field_validator("symbol", "timeframe")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @model_validator(mode="after")
        def validate_runtime_request_model(self):
            values = self.__dict__.copy()
            self.validate_runtime_request(values)
            return self

    class PipelineBatchRunReport(PipelineBatchRunReportBase):
        requests: List[PipelineSymbolRunRequest]
        reports: List[PipelineRunReport]

        @field_validator("batch_id")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @model_validator(mode="after")
        def validate_batch_report(self):
            values = self.__dict__.copy()
            self.validate_batch(values)
            return self

else:

    class PipelineRunContext(PipelineRunContext):
        @validator("run_id", "symbol", "timeframe")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class PipelineStageResult(PipelineStageResultBase):
        @validator("stage")
        def validate_stage(cls, value):
            return cls.stage_must_not_be_empty(value)

        @root_validator
        def validate_result(cls, values):
            return cls.validate_stage_result(values)

    class PipelineRunReport(PipelineRunReportBase):
        stages: List[PipelineStageResult]

        @validator("stages")
        def validate_stages(cls, value):
            return cls.stages_must_not_be_empty(value)

        @root_validator
        def validate_run_report(cls, values):
            return cls.validate_report(values)

    class PipelineSymbolRunRequest(PipelineSymbolRunRequestBase):
        @validator("symbol", "timeframe")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @root_validator
        def validate_symbol_request(cls, values):
            return cls.validate_request(values)

    class PipelineRuntimeRequest(PipelineRuntimeRequestBase):
        @validator("symbol", "timeframe")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @root_validator
        def validate_runtime_request_model(cls, values):
            return cls.validate_runtime_request(values)

    class PipelineBatchRunReport(PipelineBatchRunReportBase):
        requests: List[PipelineSymbolRunRequest]
        reports: List[PipelineRunReport]

        @validator("batch_id")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @root_validator
        def validate_batch_report(cls, values):
            return cls.validate_batch(values)
