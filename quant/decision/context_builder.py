from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from quant.data.quality import KlineQualityReport
from quant.data.schemas.market import Kline
from quant.decision.ai_sandbox import DISALLOWED_AI_DIRECTIVE_KEYS
from quant.schemas import (
    AIDecisionAdvisorRequest,
    AssetClass,
    FeatureSnapshot,
    MarketType,
    RegimeSnapshot,
    StrategySignal,
    TraceContext,
)
from quant.utils.time_format import add_display_times


CONTEXT_BUILDER_VERSION = "1.0"


@dataclass(frozen=True)
class AIDecisionContextBuildInput:
    request_id: str
    timestamp: int
    symbol: str
    asset_class: AssetClass
    model_name: str
    trace: TraceContext
    klines: Sequence[Kline]
    quality_report: KlineQualityReport
    feature_snapshot: FeatureSnapshot
    market_type: MarketType = MarketType.SPOT
    timeframe: Optional[str] = None
    advisor_name: str = "smartqtf_ai_decision_advisor"
    prompt_id: str = "smartqtf-ai-decision-context-v1"
    regime_snapshot: Optional[RegimeSnapshot] = None
    strategy_signal: Optional[StrategySignal] = None
    portfolio_context: Mapping[str, Any] = field(default_factory=dict)
    risk_constraints: Mapping[str, Any] = field(default_factory=dict)
    portfolio_constraints: Mapping[str, Any] = field(default_factory=dict)
    safety_constraints: Mapping[str, Any] = field(default_factory=dict)
    market_context: Mapping[str, Any] = field(default_factory=dict)
    feature_context: Mapping[str, Any] = field(default_factory=dict)
    regime_context: Mapping[str, Any] = field(default_factory=dict)
    strategy_context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


class AIDecisionContextBuilder:
    """Build read-only AI advisor requests from typed pipeline artifacts."""

    def build(self, request: AIDecisionContextBuildInput) -> AIDecisionAdvisorRequest:
        self._validate_request(request)
        timeframe = self._timeframe(request)
        selected_index = self._selected_index(request)
        market_context = self._market_context(request, selected_index, timeframe)
        feature_context = self._feature_context(request)
        regime_context = self._regime_context(request)
        strategy_context = self._strategy_context(request)
        portfolio_context = _plain_payload(request.portfolio_context)
        constraints = self._constraints(request)
        metadata = {
            "context_builder": "ai_decision_context_builder",
            "context_builder_version": CONTEXT_BUILDER_VERSION,
            "advice_only": True,
            **_plain_payload(request.metadata),
        }

        payload = {
            "request_id": request.request_id,
            "timestamp": request.timestamp,
            "symbol": request.symbol,
            "asset_class": request.asset_class,
            "market_type": request.market_type,
            "timeframe": timeframe,
            "advisor_name": request.advisor_name,
            "model_name": request.model_name,
            "prompt_id": request.prompt_id,
            "trace": request.trace,
            "market_context": market_context,
            "feature_context": feature_context,
            "regime_context": regime_context,
            "strategy_context": strategy_context,
            "portfolio_context": portfolio_context,
            "constraints": constraints,
            "metadata": metadata,
        }
        self._reject_disallowed_directives(payload)
        return AIDecisionAdvisorRequest(**payload)

    def _market_context(
        self,
        request: AIDecisionContextBuildInput,
        selected_index: int,
        timeframe: str,
    ) -> dict[str, Any]:
        klines_window = [kline.to_display_payload() for kline in request.klines]
        selected_kline = request.klines[selected_index].to_display_payload()
        feature = request.feature_snapshot
        quality = request.quality_report

        payload = {
            "symbol": request.symbol,
            "timeframe": timeframe,
            "asset_class": _enum_value(request.asset_class),
            "market_type": _enum_value(request.market_type),
            "klines_window": klines_window,
            "window_start_timestamp": request.klines[0].timestamp,
            "window_end_timestamp": request.klines[-1].timestamp,
            "selected_kline": selected_kline,
            "selected_index": selected_index,
            "requested_index": feature.requested_index,
            "effective_index": feature.effective_index,
            "input_bar_count": feature.input_bar_count or len(request.klines),
            "quality_report": quality.to_display_payload(),
            "incomplete_bar_policy": {
                "feature_include_incomplete_last_bar": feature.include_incomplete_last_bar,
                "feature_skipped_incomplete_last_bar": feature.skipped_incomplete_last_bar,
                "feature_skipped_incomplete_bar_timestamp": feature.skipped_incomplete_bar_timestamp,
                "feature_selected_bar_is_complete": feature.is_complete_bar,
                "quality_has_incomplete_last_bar": quality.has_incomplete_last_bar,
                "quality_incomplete_last_bar_timestamp": quality.incomplete_last_bar_timestamp,
            },
            **_plain_payload(request.market_context),
        }
        return add_display_times(payload)

    def _feature_context(self, request: AIDecisionContextBuildInput) -> dict[str, Any]:
        feature = request.feature_snapshot
        payload = {
            "feature_snapshot": feature.to_display_payload(),
            "feature_snapshot_id": feature.snapshot_id,
            "feature_set_id": feature.feature_set_id,
            "feature_set_version": feature.feature_set_version,
            "selected_index": feature.effective_index,
            "requested_index": feature.requested_index,
            "feature_availability": _plain_payload(feature.feature_availability),
            "feature_parameters": _plain_payload(feature.feature_parameters),
            **_plain_payload(request.feature_context),
        }
        return add_display_times(payload)

    def _regime_context(self, request: AIDecisionContextBuildInput) -> dict[str, Any]:
        payload = _plain_payload(request.regime_context)
        if request.regime_snapshot is None:
            return payload
        return {
            "regime_snapshot": request.regime_snapshot.to_display_payload(),
            "regime_id": request.regime_snapshot.regime_id,
            "regime": _enum_value(request.regime_snapshot.regime),
            "tradability": request.regime_snapshot.tradability,
            **payload,
        }

    def _strategy_context(self, request: AIDecisionContextBuildInput) -> dict[str, Any]:
        payload = _plain_payload(request.strategy_context)
        if request.strategy_signal is None:
            return payload
        return {
            "strategy_signal": request.strategy_signal.to_display_payload(),
            "signal_id": request.strategy_signal.signal_id,
            "strategy_id": request.strategy_signal.strategy_id,
            "strategy_version": request.strategy_signal.strategy_version,
            "action": _enum_value(request.strategy_signal.action),
            "should_send_order": request.strategy_signal.should_send_order,
            "trade_now": request.strategy_signal.trade_now,
            **payload,
        }

    def _constraints(self, request: AIDecisionContextBuildInput) -> dict[str, Any]:
        return {
            "advice_only": True,
            "ai_may_suggest_decision_intent_candidate": True,
            "ai_may_not_approve_risk": True,
            "ai_may_not_allocate_portfolio": True,
            "ai_may_not_submit_orders": True,
            "risk_gate_required_after_ai": True,
            "portfolio_gate_required_after_risk": True,
            "live_order_gate_required_before_broker": True,
            "kill_switch_has_priority": True,
            "risk": _plain_payload(request.risk_constraints),
            "portfolio": _plain_payload(request.portfolio_constraints),
            "safety": {
                "network_used_by_builder": False,
                "broker_called_by_builder": False,
                "live_orders_sent_by_builder": False,
                **_plain_payload(request.safety_constraints),
            },
        }

    def _validate_request(self, request: AIDecisionContextBuildInput) -> None:
        if not request.klines:
            raise ValueError("AI decision context requires a non-empty kline window")
        timeframe = self._timeframe(request)
        self._assert_match("quality_report symbol", request.quality_report.symbol, request.symbol)
        self._assert_match("quality_report timeframe", request.quality_report.timeframe, timeframe)
        self._assert_match("feature_snapshot symbol", request.feature_snapshot.symbol, request.symbol)
        self._assert_match("feature_snapshot timeframe", request.feature_snapshot.timeframe, timeframe)
        if request.regime_snapshot is not None:
            self._assert_match("regime_snapshot symbol", request.regime_snapshot.symbol, request.symbol)
            self._assert_match("regime_snapshot timeframe", request.regime_snapshot.timeframe, timeframe)
        if request.strategy_signal is not None:
            if request.strategy_signal.symbol is not None:
                self._assert_match("strategy_signal symbol", request.strategy_signal.symbol, request.symbol)
            if request.strategy_signal.timeframe is not None:
                self._assert_match("strategy_signal timeframe", request.strategy_signal.timeframe, timeframe)
        if request.quality_report.checked_count != len(request.klines):
            raise ValueError("quality_report checked_count must match AI context kline window length")

    def _selected_index(self, request: AIDecisionContextBuildInput) -> int:
        feature = request.feature_snapshot
        selected_index = feature.effective_index
        if selected_index is None:
            selected_index = request.trace.bar_index
        if selected_index is None:
            selected_index = len(request.klines) - 1
        if selected_index < 0 or selected_index >= len(request.klines):
            raise ValueError("selected/effective index must be within the kline window")
        return selected_index

    def _timeframe(self, request: AIDecisionContextBuildInput) -> str:
        timeframe = request.timeframe or request.feature_snapshot.timeframe or request.quality_report.timeframe
        if not timeframe:
            raise ValueError("AI decision context requires timeframe")
        return timeframe

    def _assert_match(self, field_name: str, actual: str, expected: str) -> None:
        if actual != expected:
            raise ValueError(f"{field_name} must match request")

    def _reject_disallowed_directives(self, value: Any, path: str = "request") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                if key_text in DISALLOWED_AI_DIRECTIVE_KEYS:
                    raise ValueError(f"AI decision context cannot include execution directive: {path}.{key_text}")
                self._reject_disallowed_directives(item, f"{path}.{key_text}")
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                self._reject_disallowed_directives(item, f"{path}[{index}]")


def _plain_payload(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_payload(item) for item in value]
    if hasattr(value, "to_payload"):
        return _plain_payload(value.to_payload())
    if hasattr(value, "model_dump"):
        return _plain_payload(value.model_dump(mode="json"))
    if hasattr(value, "dict"):
        return _plain_payload(value.dict())
    return value


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value
