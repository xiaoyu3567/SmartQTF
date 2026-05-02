from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext
from quant.schemas.enums import RegimeKind, StrategyAction, TradeSide

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator, model_validator
else:
    from pydantic import root_validator, validator


class StrategySignalBase(SmartQTFModel):
    ORDERABLE_ACTIONS: ClassVar[set[str]] = {
        StrategyAction.BUY.value,
        StrategyAction.SELL.value,
        StrategyAction.CLOSE.value,
        StrategyAction.CANCEL.value,
    }
    NON_ORDER_ACTIONS: ClassVar[set[str]] = {
        StrategyAction.HOLD.value,
        StrategyAction.WAIT.value,
        StrategyAction.INVALID.value,
        StrategyAction.NO_TRADE.value,
    }

    signal_id: str
    strategy_id: str
    strategy_version: str
    side: Optional[TradeSide] = None
    action: StrategyAction = StrategyAction.NO_TRADE
    signal_type: Optional[str] = None
    signal_index: int
    execute_index: Optional[int] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason_codes: List[str] = Field(default_factory=list)
    trade_now: bool = False
    should_send_order: bool = False
    watch_plan: Optional[Dict[str, Any]] = None
    trace: Optional[TraceContext] = None

    @classmethod
    def indices_must_be_non_negative(cls, value):
        if value is not None and value < 0:
            raise ValueError("indices must be non-negative")
        return value

    @classmethod
    def normalize_action_contract(cls, values):
        if not isinstance(values, dict):
            return values

        payload = dict(values)
        side_value = cls._normalized_value(payload.get("side"))
        action_value = cls._normalized_value(payload.get("action"))
        if action_value is None:
            if side_value in {TradeSide.BUY.value, TradeSide.SELL.value}:
                action_value = side_value
            else:
                action_value = StrategyAction.NO_TRADE.value
        payload["action"] = action_value

        if payload.get("side") is None and action_value in {StrategyAction.BUY.value, StrategyAction.SELL.value}:
            payload["side"] = action_value

        orderable = action_value in cls.ORDERABLE_ACTIONS
        if payload.get("trade_now") is None:
            payload["trade_now"] = orderable
        if payload.get("should_send_order") is None:
            payload["should_send_order"] = orderable
        if not payload.get("signal_type"):
            payload["signal_type"] = "EXECUTE" if orderable else action_value.upper()
        return payload

    @classmethod
    def validate_action_contract_values(cls, values):
        action_value = cls._normalized_value(values.get("action"))
        side_value = cls._normalized_value(values.get("side"))
        trade_now = bool(values.get("trade_now"))
        should_send_order = bool(values.get("should_send_order"))

        if action_value in cls.NON_ORDER_ACTIONS and (trade_now or should_send_order):
            raise ValueError(f"{action_value} strategy signals cannot trade now or send orders")

        if action_value in {StrategyAction.BUY.value, StrategyAction.SELL.value}:
            if side_value is None:
                raise ValueError(f"{action_value} strategy signals require a side")
            if side_value != action_value:
                raise ValueError("buy/sell strategy action must match side")
        return values

    @staticmethod
    def _normalized_value(value):
        if value is None:
            return None
        raw = value.value if hasattr(value, "value") else value
        if isinstance(raw, str):
            return raw.strip().lower()
        return raw

    @property
    def is_orderable(self) -> bool:
        return (
            self._normalized_value(self.action) in self.ORDERABLE_ACTIONS
            and bool(self.trade_now)
            and bool(self.should_send_order)
        )

    def with_execute_index(self, execute_index: int):
        payload = self.to_payload()
        payload["execute_index"] = execute_index
        return self.__class__.from_payload(payload)

    def to_legacy_signal(self):
        if not self.is_orderable:
            raise ValueError("non-executable strategy signal cannot be converted to legacy order signal")
        side_value = self._normalized_value(self.side)
        if side_value not in {TradeSide.BUY.value, TradeSide.SELL.value}:
            raise ValueError("legacy order signal requires buy or sell side")
        payload = {
            "signal": side_value,
            "signal_index": self.signal_index,
        }
        if self.execute_index is not None:
            payload["execute_index"] = self.execute_index
        if self.symbol is not None:
            payload["symbol"] = self.symbol
        if self.timeframe is not None:
            payload["timeframe"] = self.timeframe
        return payload


class StrategyRouteBase(SmartQTFModel):
    route_id: str
    timestamp: int
    symbol: str
    timeframe: str
    regime: RegimeKind
    strategy_id: str
    strategy_version: str
    router_id: str
    router_version: str
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason_codes: List[str] = Field(default_factory=list)
    trace: Optional[TraceContext] = None


if hasattr(BaseModel, "model_validate"):

    class StrategySignal(StrategySignalBase):
        @model_validator(mode="before")
        @classmethod
        def normalize_strategy_action_contract(cls, values):
            return cls.normalize_action_contract(values)

        @field_validator("signal_index", "execute_index")
        @classmethod
        def validate_indices(cls, value):
            return cls.indices_must_be_non_negative(value)

        @model_validator(mode="after")
        def validate_strategy_action_contract(self):
            self.validate_action_contract_values(
                {
                    "action": self.action,
                    "side": self.side,
                    "trade_now": self.trade_now,
                    "should_send_order": self.should_send_order,
                }
            )
            return self


    class StrategyRoute(StrategyRouteBase):
        pass

else:

    class StrategySignal(StrategySignalBase):
        @root_validator(pre=True)
        def normalize_strategy_action_contract(cls, values):
            return cls.normalize_action_contract(values)

        @validator("signal_index", "execute_index")
        def validate_indices(cls, value):
            return cls.indices_must_be_non_negative(value)

        @root_validator
        def validate_strategy_action_contract(cls, values):
            return cls.validate_action_contract_values(values)


    class StrategyRoute(StrategyRouteBase):
        pass


class StrategyCandidateScore(SmartQTFModel):
    evaluation_id: str
    strategy_id: str
    strategy_version: str
    signal_id: Optional[str] = None
    symbol: str
    timeframe: str
    regime: RegimeKind
    action: Optional[StrategyAction] = None
    signal_type: Optional[str] = None
    orderable: bool = False
    candidate_status: str
    signal_quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    regime_fit_score: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    liquidity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    performance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    symbol_calibration_weight: float = Field(default=1.0, ge=0.0)
    adjusted_final_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rank: Optional[int] = None
    score_rank: Optional[int] = None
    execution_rank: Optional[int] = None
    rejection_reasons: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    watch_plan: Optional[Dict[str, Any]] = None


class StrategyPerformanceFeedback(SmartQTFModel):
    feedback_id: str
    strategy_id: str
    symbol: str
    regime: RegimeKind
    performance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    sample_count: int = Field(default=0, ge=0)
    updated_at: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StrategyEvaluationResult(SmartQTFModel):
    evaluation_id: str
    timestamp: int
    symbol: str
    timeframe: str
    regime: RegimeKind
    status: str
    selected_strategy_id: Optional[str] = None
    selected_signal: Optional[StrategySignal] = None
    selected_executable: bool = False
    candidates: List[StrategyCandidateScore] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)
    route_decision: Dict[str, Any] = Field(default_factory=dict)
    safety: Dict[str, bool] = Field(
        default_factory=lambda: {
            "network_used": False,
            "broker_called": False,
            "live_orders_sent": False,
            "risk_bypassed": False,
        }
    )
