from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext
from quant.schemas.enums import RegimeKind
from quant.schemas.feature import MultiTimeframeFeatureSnapshot

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator, model_validator
else:
    from pydantic import root_validator, validator


def _default_regime_scores() -> Dict[str, float]:
    return {
        "trend": 0.0,
        "volatility": 0.0,
        "liquidity_activity": 0.0,
        "orderflow": 0.0,
    }


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
    reasons: List[str] = Field(default_factory=list)
    metrics: Dict[str, float] = Field(default_factory=dict)
    scores: Dict[str, float] = Field(default_factory=_default_regime_scores)
    score_inputs: Dict[str, object] = Field(default_factory=dict)
    source_window_start: Optional[int] = None
    source_window_end: Optional[int] = None
    input_refs: Dict[str, object] = Field(default_factory=dict)
    threshold_version: Optional[str] = None
    threshold_scope: Optional[str] = None
    direction: Optional[str] = None
    volatility_state: Optional[str] = None
    tradability: Optional[str] = None
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
        source_window_start = values.get("source_window_start")
        source_window_end = values.get("source_window_end")
        if (
            source_window_start is not None
            and source_window_end is not None
            and source_window_start > source_window_end
        ):
            raise ValueError("source_window_start must be <= source_window_end")
        if (
            source_window_end is not None
            and as_of_timestamp is not None
            and source_window_end > as_of_timestamp
        ):
            raise ValueError("source_window_end must be <= as_of_timestamp")
        return values

    @classmethod
    def tradability_must_be_known(cls, value):
        if value is None:
            return value
        if value not in {"tradable", "observe_only", "avoid"}:
            raise ValueError("tradability must be tradable, observe_only, or avoid")
        return value

    @classmethod
    def direction_must_be_known(cls, value):
        if value is None:
            return value
        if value not in {"bullish", "bearish", "neutral", "unknown"}:
            raise ValueError("direction must be bullish, bearish, neutral, or unknown")
        return value

    @classmethod
    def volatility_state_must_be_known(cls, value):
        if value is None:
            return value
        if value not in {"low", "normal", "high", "extreme", "unknown"}:
            raise ValueError(
                "volatility_state must be low, normal, high, extreme, or unknown"
            )
        return value

    @classmethod
    def threshold_scope_must_be_known(cls, value):
        if value is None:
            return value
        if value not in {"symbol_timeframe", "symbol", "timeframe", "default"}:
            raise ValueError(
                "threshold_scope must be symbol_timeframe, symbol, timeframe, or default"
            )
        return value

    @classmethod
    def scores_must_be_normalized(cls, value):
        required_scores = {
            "trend",
            "volatility",
            "liquidity_activity",
            "orderflow",
        }
        missing = required_scores.difference(value)
        if missing:
            raise ValueError(
                "regime scores must include trend, volatility, liquidity_activity, and orderflow"
            )
        for score_name, score_value in value.items():
            if isinstance(score_value, bool) or not isinstance(score_value, (int, float)):
                raise ValueError("regime scores must be numeric")
            if score_value < 0.0 or score_value > 1.0:
                raise ValueError("regime scores must be between 0 and 1")
        return value

    @classmethod
    def reasons_must_be_safe(cls, value):
        forbidden_tokens = (
            "api_key",
            "api-key",
            "api secret",
            "api_secret",
            "secret",
            "secret_key",
            "secret-key",
            "okx_secret",
            "exchange_secret",
            "credential",
            "sk-",
            "passphrase",
            "bearer ",
            "private key",
            "raw_exchange_response",
            "raw exchange response",
            "raw response",
            "account_id",
            "account id",
        )
        for reason in value:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("regime reasons must be non-empty strings")
            lower_reason = reason.lower()
            if any(token in lower_reason for token in forbidden_tokens):
                raise ValueError(
                    "regime reasons must not include credentials, account identifiers, or raw responses"
                )
        return value

    @classmethod
    def reasons_must_match_reason_codes(cls, values):
        reason_codes = values.get("reason_codes") or []
        reasons = values.get("reasons") or []
        if reasons and not reason_codes:
            raise ValueError("regime reasons require matching reason_codes")
        if reasons and len(reasons) != len(reason_codes):
            raise ValueError("regime reasons must align one-to-one with reason_codes")
        return values


if hasattr(BaseModel, "model_validate"):

    class RegimeSnapshot(RegimeSnapshotBase):
        @field_validator("confidence")
        @classmethod
        def validate_confidence(cls, value):
            return cls.confidence_must_be_probability(value)

        @field_validator("tradability")
        @classmethod
        def validate_tradability(cls, value):
            return cls.tradability_must_be_known(value)

        @field_validator("direction")
        @classmethod
        def validate_direction(cls, value):
            return cls.direction_must_be_known(value)

        @field_validator("volatility_state")
        @classmethod
        def validate_volatility_state(cls, value):
            return cls.volatility_state_must_be_known(value)

        @field_validator("threshold_scope")
        @classmethod
        def validate_threshold_scope(cls, value):
            return cls.threshold_scope_must_be_known(value)

        @field_validator("scores")
        @classmethod
        def validate_scores(cls, value):
            return cls.scores_must_be_normalized(value)

        @field_validator("reasons")
        @classmethod
        def validate_reasons(cls, value):
            return cls.reasons_must_be_safe(value)

        @model_validator(mode="after")
        def validate_replay_contract(self):
            values = self.__dict__.copy()
            self.time_bounds_must_be_replayable(values)
            self.reasons_must_match_reason_codes(values)
            return self

else:

    class RegimeSnapshot(RegimeSnapshotBase):
        @validator("confidence")
        def validate_confidence(cls, value):
            return cls.confidence_must_be_probability(value)

        @validator("tradability")
        def validate_tradability(cls, value):
            return cls.tradability_must_be_known(value)

        @validator("direction")
        def validate_direction(cls, value):
            return cls.direction_must_be_known(value)

        @validator("volatility_state")
        def validate_volatility_state(cls, value):
            return cls.volatility_state_must_be_known(value)

        @validator("threshold_scope")
        def validate_threshold_scope(cls, value):
            return cls.threshold_scope_must_be_known(value)

        @validator("scores")
        def validate_scores(cls, value):
            return cls.scores_must_be_normalized(value)

        @validator("reasons")
        def validate_reasons(cls, value):
            return cls.reasons_must_be_safe(value)

        @root_validator
        def validate_replay_contract(cls, values):
            cls.time_bounds_must_be_replayable(values)
            cls.reasons_must_match_reason_codes(values)
            return values


class RegimeThresholds(SmartQTFModel):
    trend_threshold: Optional[float] = None
    volatility_threshold: Optional[float] = None
    adx_trend_threshold: Optional[float] = None
    atr_pct_volatility_threshold: Optional[float] = None

    def __init__(self, **data):
        super().__init__(**data)
        for field_name in (
            "trend_threshold",
            "volatility_threshold",
            "adx_trend_threshold",
            "atr_pct_volatility_threshold",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be >= 0")

    def overlay(self, override: Optional["RegimeThresholds"]) -> "RegimeThresholds":
        if override is None:
            return self
        payload = self.to_payload()
        for key, value in override.to_payload().items():
            if key == "schema_version":
                continue
            if value is not None:
                payload[key] = value
        return RegimeThresholds.from_payload(payload)

    def rule_thresholds(
        self,
        *,
        trend_threshold: float,
        volatility_threshold: float,
    ) -> tuple[float, float]:
        return (
            self.trend_threshold
            if self.trend_threshold is not None
            else trend_threshold,
            self.volatility_threshold
            if self.volatility_threshold is not None
            else volatility_threshold,
        )

    def adx_atr_thresholds(
        self,
        *,
        adx_trend_threshold: float,
        atr_pct_volatility_threshold: float,
    ) -> tuple[float, float]:
        return (
            self.adx_trend_threshold
            if self.adx_trend_threshold is not None
            else adx_trend_threshold,
            self.atr_pct_volatility_threshold
            if self.atr_pct_volatility_threshold is not None
            else atr_pct_volatility_threshold,
        )


class ResolvedRegimeThresholds(SmartQTFModel):
    threshold_version: str
    detector_id: Optional[str] = None
    detector_version: Optional[str] = None
    symbol: str
    timeframe: str
    scope: str
    thresholds: RegimeThresholds

    def __init__(self, **data):
        super().__init__(**data)
        if self.scope not in {"symbol_timeframe", "symbol", "timeframe", "default"}:
            raise ValueError(
                "scope must be symbol_timeframe, symbol, timeframe, or default"
            )


class RegimeThresholdConfig(SmartQTFModel):
    threshold_version: str
    detector_id: Optional[str] = None
    detector_version: Optional[str] = None
    default: RegimeThresholds = Field(default_factory=RegimeThresholds)
    symbols: Dict[str, RegimeThresholds] = Field(default_factory=dict)
    timeframes: Dict[str, RegimeThresholds] = Field(default_factory=dict)
    symbol_timeframes: Dict[str, RegimeThresholds] = Field(default_factory=dict)

    def __init__(self, **data):
        super().__init__(**data)
        if not self.threshold_version:
            raise ValueError("threshold_version is required")

    def resolve(self, *, symbol: str, timeframe: str) -> ResolvedRegimeThresholds:
        override, scope = self._resolve_override(symbol=symbol, timeframe=timeframe)
        thresholds = self.default.overlay(override)
        return ResolvedRegimeThresholds(
            threshold_version=self.threshold_version,
            detector_id=self.detector_id,
            detector_version=self.detector_version,
            symbol=symbol,
            timeframe=timeframe,
            scope=scope,
            thresholds=thresholds,
        )

    def _resolve_override(
        self,
        *,
        symbol: str,
        timeframe: str,
    ) -> tuple[Optional[RegimeThresholds], str]:
        symbol_timeframe = self._find_symbol_timeframe(symbol, timeframe)
        if symbol_timeframe is not None:
            return symbol_timeframe, "symbol_timeframe"

        symbol_override = self._find_key(self.symbols, symbol.upper())
        if symbol_override is not None:
            return symbol_override, "symbol"

        timeframe_override = self._find_key(self.timeframes, timeframe.lower())
        if timeframe_override is not None:
            return timeframe_override, "timeframe"

        return None, "default"

    def _find_symbol_timeframe(
        self,
        symbol: str,
        timeframe: str,
    ) -> Optional[RegimeThresholds]:
        wanted = self._symbol_timeframe_key(symbol, timeframe)
        for key, value in self.symbol_timeframes.items():
            if self._normalise_symbol_timeframe_key(key) == wanted:
                return value
        return None

    @staticmethod
    def _find_key(
        values: Dict[str, RegimeThresholds],
        wanted: str,
    ) -> Optional[RegimeThresholds]:
        wanted_key = wanted.lower()
        for key, value in values.items():
            if key.lower() == wanted_key:
                return value
        return None

    @classmethod
    def _symbol_timeframe_key(cls, symbol: str, timeframe: str) -> str:
        return f"{symbol.upper()}:{timeframe.lower()}"

    @classmethod
    def _normalise_symbol_timeframe_key(cls, key: str) -> str:
        if ":" not in key:
            return key.upper()
        symbol, timeframe = key.split(":", 1)
        return cls._symbol_timeframe_key(symbol, timeframe)


class RegimeThresholdCalibrationFeedback(SmartQTFModel):
    feedback_id: str
    symbol: str
    timeframe: str
    detector_id: str
    detector_version: str
    current_threshold_version: str
    proposed_thresholds: RegimeThresholds
    source: str = "daily_review"
    reason_codes: List[str] = Field(default_factory=list)
    requires_manual_approval: bool = True
    auto_apply: bool = False


class MultiTimeframeRegimeInput(SmartQTFModel):
    feature_snapshot: MultiTimeframeFeatureSnapshot


class MultiTimeframeRegimeSnapshot(SmartQTFModel):
    snapshot_id: str
    timestamp: int
    symbol: str
    execution_timeframe: str
    execution_regime: RegimeSnapshot
    aggregate_regime: RegimeSnapshot
    context_regimes: Dict[str, RegimeSnapshot] = Field(default_factory=dict)
    higher_timeframe_bias: str = "unknown"
    confirmation_timeframes: List[str] = Field(default_factory=list)
    conflict_timeframes: List[str] = Field(default_factory=list)
    quality_failed_timeframes: List[str] = Field(default_factory=list)
    high_volatility_timeframes: List[str] = Field(default_factory=list)
    extreme_volatility_timeframes: List[str] = Field(default_factory=list)
    tradability: str = "observe_only"
    reason_codes: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    input_refs: Dict[str, object] = Field(default_factory=dict)
    trace: Optional[TraceContext] = None

    def __init__(self, **data):
        super().__init__(**data)
        snapshot_id = self.snapshot_id.strip()
        symbol = self.symbol.strip()
        execution_timeframe = self.execution_timeframe.strip()
        if not snapshot_id:
            raise ValueError("snapshot_id must not be empty")
        if self.timestamp < 0:
            raise ValueError("timestamp must be >= 0")
        if not symbol:
            raise ValueError("symbol must not be empty")
        if not execution_timeframe:
            raise ValueError("execution_timeframe must not be empty")
        if self.higher_timeframe_bias not in {
            "bullish",
            "bearish",
            "neutral",
            "mixed",
            "unknown",
        }:
            raise ValueError("higher_timeframe_bias must be bullish, bearish, neutral, mixed, or unknown")
        RegimeSnapshotBase.tradability_must_be_known(self.tradability)
        RegimeSnapshotBase.reasons_must_be_safe(self.reasons)
        RegimeSnapshotBase.reasons_must_match_reason_codes(
            {"reason_codes": self.reason_codes, "reasons": self.reasons}
        )
        self._validate_regime("execution_regime", self.execution_regime, execution_timeframe)
        self._validate_regime("aggregate_regime", self.aggregate_regime, execution_timeframe)

        normalized_contexts: Dict[str, RegimeSnapshot] = {}
        for timeframe, regime in self.context_regimes.items():
            normalized_timeframe = timeframe.strip()
            if not normalized_timeframe:
                raise ValueError("context_regimes keys must not be empty")
            if normalized_timeframe == execution_timeframe:
                raise ValueError("context_regimes must not include execution_timeframe")
            self._validate_regime("context_regimes", regime, normalized_timeframe)
            normalized_contexts[normalized_timeframe] = regime

        known_timeframes = set(normalized_contexts)
        allowed_quality_timeframes = known_timeframes | {execution_timeframe}
        for field_name, values, allowed in (
            ("confirmation_timeframes", self.confirmation_timeframes, known_timeframes),
            ("conflict_timeframes", self.conflict_timeframes, known_timeframes),
            ("high_volatility_timeframes", self.high_volatility_timeframes, known_timeframes),
            ("extreme_volatility_timeframes", self.extreme_volatility_timeframes, known_timeframes),
            ("quality_failed_timeframes", self.quality_failed_timeframes, allowed_quality_timeframes),
        ):
            cleaned = []
            for value in values:
                timeframe = value.strip()
                if not timeframe:
                    raise ValueError(f"{field_name} must not contain empty values")
                if timeframe not in allowed:
                    raise ValueError(f"{field_name} must reference known timeframes")
                cleaned.append(timeframe)
            object.__setattr__(self, field_name, cleaned)

        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "execution_timeframe", execution_timeframe)
        object.__setattr__(self, "context_regimes", normalized_contexts)

    def _validate_regime(
        self,
        field_name: str,
        regime: RegimeSnapshot,
        expected_timeframe: str,
    ) -> None:
        if regime.symbol != self.symbol:
            raise ValueError(f"{field_name} symbol must match multi-timeframe symbol")
        if regime.timeframe != expected_timeframe:
            raise ValueError(f"{field_name} timeframe must match its key")
