from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext
from quant.schemas.enums import (
    AssetClass,
    DecisionAction,
    MarketType,
    OrderKind,
    PositionSide,
    StrategyAction,
    TimeInForce,
    TradeSide,
)
from quant.schemas.regime import RegimeSnapshot
from quant.schemas.strategy import StrategySignal

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator, model_validator
else:
    from pydantic import root_validator, validator


class DecisionExitTargetBase(SmartQTFModel):
    price: float
    quantity_pct: float
    reason_code: Optional[str] = None

    @classmethod
    def price_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("target price must be greater than 0.0")
        return value

    @classmethod
    def quantity_pct_must_be_valid(cls, value):
        if value <= 0.0 or value > 1.0:
            raise ValueError("target quantity_pct must be greater than 0.0 and less than or equal to 1.0")
        return value


if hasattr(BaseModel, "model_validate"):

    class DecisionStopLossTarget(DecisionExitTargetBase):
        @field_validator("price")
        @classmethod
        def validate_price(cls, value):
            return cls.price_must_be_positive(value)

        @field_validator("quantity_pct")
        @classmethod
        def validate_quantity_pct(cls, value):
            return cls.quantity_pct_must_be_valid(value)

    class DecisionTakeProfitTarget(DecisionExitTargetBase):
        @field_validator("price")
        @classmethod
        def validate_price(cls, value):
            return cls.price_must_be_positive(value)

        @field_validator("quantity_pct")
        @classmethod
        def validate_quantity_pct(cls, value):
            return cls.quantity_pct_must_be_valid(value)

else:

    class DecisionStopLossTarget(DecisionExitTargetBase):
        @validator("price")
        def validate_price(cls, value):
            return cls.price_must_be_positive(value)

        @validator("quantity_pct")
        def validate_quantity_pct(cls, value):
            return cls.quantity_pct_must_be_valid(value)

    class DecisionTakeProfitTarget(DecisionExitTargetBase):
        @validator("price")
        def validate_price(cls, value):
            return cls.price_must_be_positive(value)

        @validator("quantity_pct")
        def validate_quantity_pct(cls, value):
            return cls.quantity_pct_must_be_valid(value)


class DecisionIntentBase(SmartQTFModel):
    decision_id: str
    timestamp: int
    symbol: str
    asset_class: AssetClass
    market_type: MarketType = MarketType.SPOT
    strategy_id: str
    strategy_version: str
    regime: Optional[str] = None
    action: DecisionAction
    order_type: OrderKind
    quantity: float
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss_targets: List[DecisionStopLossTarget] = Field(default_factory=list)
    take_profit_targets: List[DecisionTakeProfitTarget] = Field(default_factory=list)
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason_codes: List[str] = Field(default_factory=list)
    trace: Optional[TraceContext] = None

    @classmethod
    def quantity_must_be_positive(cls, value):
        if value <= 0.0:
            raise ValueError("quantity must be greater than 0.0")
        return value

    @classmethod
    def optional_prices_must_be_positive(cls, value):
        if value is not None and value <= 0.0:
            raise ValueError("price fields must be greater than 0.0")
        return value

    @classmethod
    def target_quantity_pct_sum_must_not_exceed_one(cls, targets, field_name):
        total = sum(target.quantity_pct for target in targets)
        if total > 1.0:
            raise ValueError(f"{field_name} quantity_pct sum must be less than or equal to 1.0")
        return targets

    def to_order_intent(
        self,
        client_order_id: Optional[str] = None,
        order_intent_id: Optional[str] = None,
        risk_approved: bool = False,
        created_at: Optional[int] = None,
    ):
        from quant.schemas.execution import OrderIntent

        if risk_approved is not True:
            raise ValueError("DecisionIntent cannot create executable OrderIntent without explicit risk approval")

        action_value = self._enum_value(self.action)
        side = self._order_side()
        return OrderIntent(
            order_intent_id=order_intent_id or f"{self.decision_id}:order-intent",
            decision_id=self.decision_id,
            client_order_id=client_order_id or f"{self.decision_id}:{action_value}",
            symbol=self.symbol,
            side=side,
            order_type=self.order_type,
            quantity=self.quantity,
            limit_price=self.limit_price,
            time_in_force=self.time_in_force,
            reduce_only=self.reduce_only or action_value in {
                DecisionAction.CLOSE_LONG.value,
                DecisionAction.CLOSE_SHORT.value,
            },
            risk_approved=risk_approved,
            created_at=created_at if created_at is not None else self.timestamp,
            trace=self.trace,
        )

    def _order_side(self):
        action_value = self._enum_value(self.action)
        if action_value == DecisionAction.OPEN_LONG.value:
            return TradeSide.BUY
        if action_value == DecisionAction.CLOSE_LONG.value:
            return TradeSide.SELL
        if action_value == DecisionAction.OPEN_SHORT.value:
            return TradeSide.SELL
        if action_value == DecisionAction.CLOSE_SHORT.value:
            return TradeSide.BUY
        raise ValueError("hold decisions cannot be converted to order intent")

    @staticmethod
    def _enum_value(value):
        return getattr(value, "value", value)


class TradeIntentBase(SmartQTFModel):
    EXECUTABLE_FIELD_NAMES: ClassVar[set[str]] = {
        "allocated_quantity",
        "broker_order_request",
        "client_order_id",
        "execution_order_plan",
        "order_intent",
        "order_intent_id",
        "quantity",
        "risk_approved",
    }

    trade_intent_id: str
    decision_id: str
    timestamp: int
    symbol: str
    asset_class: AssetClass
    market_type: MarketType = MarketType.SPOT
    side: TradeSide
    action: DecisionAction
    strategy_id: str
    strategy_version: str
    timeframe: Optional[str] = None
    regime: Optional[str] = None
    entry_price: Optional[float] = Field(default=None, gt=0.0)
    stop_loss: Optional[float] = Field(default=None, gt=0.0)
    take_profit: Optional[float] = Field(default=None, gt=0.0)
    stop_loss_targets: List[DecisionStopLossTarget] = Field(default_factory=list)
    take_profit_targets: List[DecisionTakeProfitTarget] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    source_signal_id: Optional[str] = None
    reason_codes: List[str] = Field(default_factory=list)
    trace: Optional[TraceContext] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def reject_executable_fields(cls, values):
        if not isinstance(values, dict):
            return values
        executable_fields = sorted(cls.EXECUTABLE_FIELD_NAMES.intersection(values))
        if executable_fields:
            raise ValueError(
                "TradeIntent must not include executable order fields: "
                + ", ".join(executable_fields)
            )
        return values

    @classmethod
    def target_quantity_pct_sum_must_not_exceed_one(cls, targets, field_name):
        return DecisionIntentBase.target_quantity_pct_sum_must_not_exceed_one(
            targets,
            field_name,
        )

    @classmethod
    def action_and_side_must_match(cls, values):
        action = cls._enum_value(values.get("action"))
        side = cls._enum_value(values.get("side"))
        if action == DecisionAction.HOLD.value:
            raise ValueError("TradeIntent cannot use hold action")
        if action == DecisionAction.OPEN_LONG.value and side != TradeSide.BUY.value:
            raise ValueError("open_long trade intent requires buy side")
        if action == DecisionAction.CLOSE_LONG.value and side != TradeSide.SELL.value:
            raise ValueError("close_long trade intent requires sell side")
        if action == DecisionAction.OPEN_SHORT.value and side != TradeSide.SELL.value:
            raise ValueError("open_short trade intent requires sell side")
        if action == DecisionAction.CLOSE_SHORT.value and side != TradeSide.BUY.value:
            raise ValueError("close_short trade intent requires buy side")
        return values

    @staticmethod
    def _enum_value(value):
        return getattr(value, "value", value)


class DecisionPolicy(SmartQTFModel):
    policy_id: str = "decision_policy_v1"
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    enforce_regime_alignment: bool = True
    allowed_regimes: List[str] = Field(default_factory=list)
    blocked_symbols: List[str] = Field(default_factory=list)
    blocked_regimes: List[str] = Field(default_factory=list)
    cooldown_ms: Optional[int] = Field(default=None, ge=0)
    daily_trade_limit: Optional[int] = Field(default=None, ge=0)
    max_signal_age_ms: Optional[int] = Field(default=None, ge=0)
    allow_short_selling: bool = False
    require_orderable_signal: bool = True


class DecisionPortfolioState(SmartQTFModel):
    symbol: str
    position_side: PositionSide = PositionSide.FLAT
    open_position_quantity: float = Field(default=0.0, ge=0.0)
    last_trade_timestamp: Optional[int] = None
    trades_today: int = Field(default=0, ge=0)
    open_order_client_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DecisionEngineRequest(SmartQTFModel):
    request_id: str
    timestamp: int
    symbol: str
    asset_class: AssetClass = AssetClass.CRYPTO
    market_type: MarketType = MarketType.SPOT
    timeframe: Optional[str] = None
    signal: StrategySignal
    regime_snapshot: Optional[RegimeSnapshot] = None
    portfolio_state: DecisionPortfolioState
    policy: DecisionPolicy = Field(default_factory=DecisionPolicy)
    candidate_order: Dict[str, Any] = Field(default_factory=dict)
    kill_switch_active: bool = False
    trace: Optional[TraceContext] = None


class DecisionEngineResultBase(SmartQTFModel):
    VALID_ACTIONS: ClassVar[set[str]] = {
        "APPROVE_TRADE_INTENT",
        "WATCH",
        "REJECT",
    }

    result_id: str
    timestamp: int
    symbol: str
    decision_action: str
    trade_intent: Optional["TradeIntent"] = None
    forward_to_capital_allocation: bool = False
    reason_codes: List[str] = Field(default_factory=list)
    input_refs: Dict[str, Any] = Field(default_factory=dict)
    policy_snapshot: Dict[str, Any] = Field(default_factory=dict)
    safety: Dict[str, bool] = Field(
        default_factory=lambda: {
            "network_used": False,
            "ai_provider_called": False,
            "broker_called": False,
            "live_orders_sent": False,
            "risk_bypassed": False,
        }
    )
    trace: Optional[TraceContext] = None

    @classmethod
    def decision_action_must_be_known(cls, value):
        if value not in cls.VALID_ACTIONS:
            raise ValueError("decision_action must be APPROVE_TRADE_INTENT, WATCH, or REJECT")
        return value

    @classmethod
    def forward_contract_must_match_action(cls, values):
        action = values.get("decision_action")
        trade_intent = values.get("trade_intent")
        forward = bool(values.get("forward_to_capital_allocation"))
        if action == "APPROVE_TRADE_INTENT":
            if trade_intent is None:
                raise ValueError("APPROVE_TRADE_INTENT requires trade_intent")
            if forward is not True:
                raise ValueError("APPROVE_TRADE_INTENT requires forward_to_capital_allocation")
        else:
            if trade_intent is not None:
                raise ValueError("WATCH/REJECT decisions must not include trade_intent")
            if forward:
                raise ValueError("WATCH/REJECT decisions must not forward to capital allocation")
        return values


if hasattr(BaseModel, "model_validate"):

    class DecisionIntent(DecisionIntentBase):
        @field_validator("quantity")
        @classmethod
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @field_validator("limit_price", "stop_loss", "take_profit")
        @classmethod
        def validate_optional_prices(cls, value):
            return cls.optional_prices_must_be_positive(value)

        @field_validator("stop_loss_targets")
        @classmethod
        def validate_stop_loss_targets(cls, value):
            return cls.target_quantity_pct_sum_must_not_exceed_one(value, "stop_loss_targets")

        @field_validator("take_profit_targets")
        @classmethod
        def validate_take_profit_targets(cls, value):
            return cls.target_quantity_pct_sum_must_not_exceed_one(value, "take_profit_targets")

        @model_validator(mode="after")
        def validate_limit_price_for_limit_orders(self):
            if self.order_type == OrderKind.LIMIT and self.limit_price is None:
                raise ValueError("limit orders require limit_price")
            return self

    class TradeIntent(TradeIntentBase):
        @model_validator(mode="before")
        @classmethod
        def validate_no_executable_fields(cls, values):
            return cls.reject_executable_fields(values)

        @field_validator("stop_loss_targets")
        @classmethod
        def validate_stop_loss_targets(cls, value):
            return cls.target_quantity_pct_sum_must_not_exceed_one(value, "stop_loss_targets")

        @field_validator("take_profit_targets")
        @classmethod
        def validate_take_profit_targets(cls, value):
            return cls.target_quantity_pct_sum_must_not_exceed_one(value, "take_profit_targets")

        @model_validator(mode="after")
        def validate_trade_intent_contract(self):
            self.action_and_side_must_match(self.__dict__.copy())
            return self

    class DecisionEngineResult(DecisionEngineResultBase):
        @field_validator("decision_action")
        @classmethod
        def validate_decision_action(cls, value):
            return cls.decision_action_must_be_known(value)

        @model_validator(mode="after")
        def validate_forward_contract(self):
            self.forward_contract_must_match_action(self.__dict__.copy())
            return self

else:

    class DecisionIntent(DecisionIntentBase):
        @validator("quantity")
        def validate_quantity(cls, value):
            return cls.quantity_must_be_positive(value)

        @validator("limit_price", "stop_loss", "take_profit")
        def validate_optional_prices(cls, value):
            return cls.optional_prices_must_be_positive(value)

        @validator("stop_loss_targets")
        def validate_stop_loss_targets(cls, value):
            return cls.target_quantity_pct_sum_must_not_exceed_one(value, "stop_loss_targets")

        @validator("take_profit_targets")
        def validate_take_profit_targets(cls, value):
            return cls.target_quantity_pct_sum_must_not_exceed_one(value, "take_profit_targets")

        @root_validator
        def validate_limit_price_for_limit_orders(cls, values):
            if values.get("order_type") == OrderKind.LIMIT and values.get("limit_price") is None:
                raise ValueError("limit orders require limit_price")
            return values

    class TradeIntent(TradeIntentBase):
        @root_validator(pre=True)
        def validate_no_executable_fields(cls, values):
            return cls.reject_executable_fields(values)

        @validator("stop_loss_targets")
        def validate_stop_loss_targets(cls, value):
            return cls.target_quantity_pct_sum_must_not_exceed_one(value, "stop_loss_targets")

        @validator("take_profit_targets")
        def validate_take_profit_targets(cls, value):
            return cls.target_quantity_pct_sum_must_not_exceed_one(value, "take_profit_targets")

        @root_validator
        def validate_trade_intent_contract(cls, values):
            return cls.action_and_side_must_match(values)

    class DecisionEngineResult(DecisionEngineResultBase):
        @validator("decision_action")
        def validate_decision_action(cls, value):
            return cls.decision_action_must_be_known(value)

        @root_validator
        def validate_forward_contract(cls, values):
            return cls.forward_contract_must_match_action(values)


class AIDecisionSuggestionBase(SmartQTFModel):
    suggestion_id: str
    timestamp: int
    candidate: DecisionIntent
    advisor_name: Optional[str] = None
    model_name: Optional[str] = None
    prompt_id: Optional[str] = None
    prompt_hash: Optional[str] = None
    raw_response_hash: Optional[str] = None
    sandbox_version: str = "1.0"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value, field_name):
        if not value:
            raise ValueError(f"{field_name} must not be empty")
        return value

    @classmethod
    def non_negative_timestamp(cls, value):
        if value < 0:
            raise ValueError("timestamp must be greater than or equal to 0")
        return value

    @classmethod
    def candidate_must_be_replayable_ai_advice(cls, value):
        if value.confidence is None:
            raise ValueError("AI decision suggestions require candidate confidence")
        if not value.reason_codes:
            raise ValueError("AI decision suggestions require candidate reason_codes")
        if value.trace is None:
            raise ValueError("AI decision suggestions require candidate trace")
        return value


if hasattr(BaseModel, "model_validate"):

    class AIDecisionSuggestion(AIDecisionSuggestionBase):
        @field_validator("suggestion_id", "sandbox_version")
        @classmethod
        def validate_non_empty_string(cls, value, info):
            return cls.non_empty_string(value, info.field_name)

        @field_validator("timestamp")
        @classmethod
        def validate_timestamp(cls, value):
            return cls.non_negative_timestamp(value)

        @field_validator("candidate")
        @classmethod
        def validate_candidate(cls, value):
            return cls.candidate_must_be_replayable_ai_advice(value)

else:

    class AIDecisionSuggestion(AIDecisionSuggestionBase):
        @validator("suggestion_id", "sandbox_version")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value, "value")

        @validator("timestamp")
        def validate_timestamp(cls, value):
            return cls.non_negative_timestamp(value)

        @validator("candidate")
        def validate_candidate(cls, value):
            return cls.candidate_must_be_replayable_ai_advice(value)


class AIDecisionAdvisorRequestBase(SmartQTFModel):
    request_id: str
    timestamp: int
    symbol: str
    asset_class: AssetClass
    market_type: MarketType = MarketType.SPOT
    timeframe: Optional[str] = None
    advisor_name: str = "smartqtf_ai_decision_advisor"
    model_name: str
    prompt_id: str = "smartqtf-ai-decision-advice-v1"
    trace: TraceContext
    market_context: Dict[str, Any] = Field(default_factory=dict)
    feature_context: Dict[str, Any] = Field(default_factory=dict)
    regime_context: Dict[str, Any] = Field(default_factory=dict)
    strategy_context: Dict[str, Any] = Field(default_factory=dict)
    portfolio_context: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value, field_name):
        if not value:
            raise ValueError(f"{field_name} must not be empty")
        return value

    @classmethod
    def non_negative_timestamp(cls, value):
        if value < 0:
            raise ValueError("timestamp must be greater than or equal to 0")
        return value


if hasattr(BaseModel, "model_validate"):

    class AIDecisionAdvisorRequest(AIDecisionAdvisorRequestBase):
        @field_validator("request_id", "symbol", "advisor_name", "model_name", "prompt_id")
        @classmethod
        def validate_non_empty_string(cls, value, info):
            return cls.non_empty_string(value, info.field_name)

        @field_validator("timestamp")
        @classmethod
        def validate_timestamp(cls, value):
            return cls.non_negative_timestamp(value)

else:

    class AIDecisionAdvisorRequest(AIDecisionAdvisorRequestBase):
        @validator("request_id", "symbol", "advisor_name", "model_name", "prompt_id")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value, "value")

        @validator("timestamp")
        def validate_timestamp(cls, value):
            return cls.non_negative_timestamp(value)
