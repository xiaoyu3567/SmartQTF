from typing import Optional

from quant.schemas import (
    DecisionAction,
    DecisionEngineRequest,
    DecisionEngineResult,
    DecisionPolicy,
    DecisionPortfolioState,
    PositionSide,
    RegimeKind,
    StrategyAction,
    TradeIntent,
    TradeSide,
)


class DecisionEngine:
    """Policy gate that upgrades executable strategy signals into trade intents."""

    engine_id = "decision_engine"
    engine_version = "1.0.0"

    def evaluate(self, request: DecisionEngineRequest) -> DecisionEngineResult:
        reason_codes: list[str] = []

        signal = request.signal
        policy = request.policy
        signal_action = self._value(signal.action)

        if request.kill_switch_active:
            reason_codes.append("kill_switch_active")

        if policy.require_orderable_signal and not signal.is_orderable:
            reason_codes.append("signal_not_orderable")

        if signal_action in {
            StrategyAction.WAIT.value,
            StrategyAction.HOLD.value,
            StrategyAction.NO_TRADE.value,
            StrategyAction.INVALID.value,
        }:
            return self._watch(request, [f"strategy_action_{signal_action}"])

        if signal_action == StrategyAction.CANCEL.value:
            reason_codes.append("cancel_signal_not_trade_intent")

        if signal_action == StrategyAction.SELL.value and not policy.allow_short_selling:
            reason_codes.append("short_selling_disabled")

        confidence = signal.confidence if signal.confidence is not None else 0.0
        if confidence < policy.min_confidence:
            reason_codes.append("confidence_below_minimum")

        reason_codes.extend(self._regime_rejections(request))
        reason_codes.extend(self._portfolio_rejections(request))
        reason_codes.extend(self._candidate_order_rejections(request))

        if reason_codes:
            return self._reject(request, reason_codes)

        side = self._signal_side(signal_action)
        action = self._decision_action(signal_action)
        trade_intent = TradeIntent(
            trade_intent_id=f"{request.request_id}:trade-intent",
            decision_id=f"{request.request_id}:decision",
            timestamp=request.timestamp,
            symbol=request.symbol,
            asset_class=request.asset_class,
            market_type=request.market_type,
            side=side,
            action=action,
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            timeframe=request.timeframe or signal.timeframe,
            regime=self._regime_value(request),
            entry_price=self._candidate_price(request),
            stop_loss=self._candidate_positive_float(request, "stop_loss"),
            take_profit=self._candidate_positive_float(request, "take_profit"),
            stop_loss_targets=[],
            take_profit_targets=[],
            confidence=signal.confidence,
            source_signal_id=signal.signal_id,
            reason_codes=self._dedupe(
                list(signal.reason_codes) + ["decision_policy_approved"]
            ),
            trace=request.trace or signal.trace,
            metadata={
                "policy_id": policy.policy_id,
                "decision_engine": self.engine_id,
                "decision_engine_version": self.engine_version,
                "candidate_order_keys": sorted(request.candidate_order.keys()),
            },
        )
        return DecisionEngineResult(
            result_id=f"{request.request_id}:result",
            timestamp=request.timestamp,
            symbol=request.symbol,
            decision_action="APPROVE_TRADE_INTENT",
            trade_intent=trade_intent,
            forward_to_capital_allocation=True,
            reason_codes=trade_intent.reason_codes,
            input_refs=self._input_refs(request),
            policy_snapshot=policy.to_payload(),
            trace=request.trace or signal.trace,
        )

    def _watch(
        self,
        request: DecisionEngineRequest,
        reason_codes: list[str],
    ) -> DecisionEngineResult:
        return DecisionEngineResult(
            result_id=f"{request.request_id}:result",
            timestamp=request.timestamp,
            symbol=request.symbol,
            decision_action="WATCH",
            forward_to_capital_allocation=False,
            reason_codes=self._dedupe(reason_codes),
            input_refs=self._input_refs(request),
            policy_snapshot=request.policy.to_payload(),
            trace=request.trace or request.signal.trace,
        )

    def _reject(
        self,
        request: DecisionEngineRequest,
        reason_codes: list[str],
    ) -> DecisionEngineResult:
        return DecisionEngineResult(
            result_id=f"{request.request_id}:result",
            timestamp=request.timestamp,
            symbol=request.symbol,
            decision_action="REJECT",
            forward_to_capital_allocation=False,
            reason_codes=self._dedupe(reason_codes),
            input_refs=self._input_refs(request),
            policy_snapshot=request.policy.to_payload(),
            trace=request.trace or request.signal.trace,
        )

    def _regime_rejections(self, request: DecisionEngineRequest) -> list[str]:
        regime = request.regime_snapshot
        policy = request.policy
        if regime is None:
            return []

        reason_codes: list[str] = []
        regime_value = self._value(regime.regime)
        legacy_regime_value = self._legacy_regime_value(regime.regime)
        blocked_regimes = {value.lower() for value in policy.blocked_regimes}
        allowed_regimes = {value.lower() for value in policy.allowed_regimes}

        if regime_value.lower() in blocked_regimes or legacy_regime_value.lower() in blocked_regimes:
            reason_codes.append("regime_blocked")

        if allowed_regimes and (
            regime_value.lower() not in allowed_regimes
            and legacy_regime_value.lower() not in allowed_regimes
        ):
            reason_codes.append("regime_not_allowed")

        if self._value(regime.tradability) == "avoid":
            reason_codes.append("regime_tradability_avoid")

        if policy.enforce_regime_alignment:
            signal_action = self._value(request.signal.action)
            direction = self._value(regime.direction)
            if signal_action == StrategyAction.BUY.value and direction == "bearish":
                reason_codes.append("regime_alignment_failed")
            if signal_action == StrategyAction.SELL.value and direction == "bullish":
                reason_codes.append("regime_alignment_failed")
        return reason_codes

    def _portfolio_rejections(self, request: DecisionEngineRequest) -> list[str]:
        policy = request.policy
        portfolio = request.portfolio_state
        reason_codes: list[str] = []
        signal_action = self._value(request.signal.action)
        position_side = self._value(portfolio.position_side)

        if request.symbol.upper() in {symbol.upper() for symbol in policy.blocked_symbols}:
            reason_codes.append("symbol_blocked")

        if (
            policy.max_signal_age_ms is not None
            and request.signal.trace is not None
            and request.signal.trace.timestamp is not None
            and request.timestamp - request.signal.trace.timestamp > policy.max_signal_age_ms
        ):
            reason_codes.append("signal_expired")

        if (
            policy.cooldown_ms is not None
            and portfolio.last_trade_timestamp is not None
            and request.timestamp - portfolio.last_trade_timestamp < policy.cooldown_ms
        ):
            reason_codes.append("cooldown_active")

        if (
            policy.daily_trade_limit is not None
            and portfolio.trades_today >= policy.daily_trade_limit
        ):
            reason_codes.append("daily_trade_limit_reached")

        if portfolio.open_order_client_ids:
            reason_codes.append("open_order_conflict")

        if signal_action == StrategyAction.BUY.value and position_side == PositionSide.LONG.value:
            reason_codes.append("position_conflict_long_already_open")
        if signal_action == StrategyAction.SELL.value and position_side == PositionSide.SHORT.value:
            reason_codes.append("position_conflict_short_already_open")
        return reason_codes

    def _candidate_order_rejections(self, request: DecisionEngineRequest) -> list[str]:
        candidate_order = request.candidate_order
        if not candidate_order:
            return []

        forbidden = {
            "allocated_quantity",
            "broker_order_request",
            "client_order_id",
            "execution_order_plan",
            "order_intent",
            "order_intent_id",
            "quantity",
            "risk_approved",
        }
        found = sorted(forbidden.intersection(candidate_order))
        if found:
            return ["candidate_order_contains_executable_fields"]
        return []

    def _input_refs(self, request: DecisionEngineRequest) -> dict[str, object]:
        refs: dict[str, object] = {
            "request_id": request.request_id,
            "signal_id": request.signal.signal_id,
            "portfolio_symbol": request.portfolio_state.symbol,
        }
        if request.regime_snapshot is not None:
            refs["regime_id"] = request.regime_snapshot.regime_id
        if request.trace is not None:
            refs["run_id"] = request.trace.run_id
        return refs

    def _candidate_price(self, request: DecisionEngineRequest) -> Optional[float]:
        for key in ("entry_price", "reference_price", "price", "limit_price"):
            value = self._candidate_positive_float(request, key)
            if value is not None:
                return value
        return None

    @staticmethod
    def _candidate_positive_float(
        request: DecisionEngineRequest,
        key: str,
    ) -> Optional[float]:
        value = request.candidate_order.get(key)
        if value is None:
            return None
        numeric_value = float(value)
        if numeric_value <= 0.0:
            raise ValueError(f"{key} must be greater than 0.0")
        return numeric_value

    @staticmethod
    def _signal_side(signal_action: str) -> TradeSide:
        if signal_action == StrategyAction.BUY.value:
            return TradeSide.BUY
        if signal_action == StrategyAction.SELL.value:
            return TradeSide.SELL
        raise ValueError("only buy/sell strategy signals can become trade intents")

    @staticmethod
    def _decision_action(signal_action: str) -> DecisionAction:
        if signal_action == StrategyAction.BUY.value:
            return DecisionAction.OPEN_LONG
        if signal_action == StrategyAction.SELL.value:
            return DecisionAction.OPEN_SHORT
        raise ValueError("only buy/sell strategy signals can become trade intents")

    @staticmethod
    def _legacy_regime_value(regime: object) -> str:
        if hasattr(regime, "legacy_kind"):
            return DecisionEngine._value(regime.legacy_kind())
        try:
            return DecisionEngine._value(RegimeKind(regime).legacy_kind())
        except ValueError:
            return DecisionEngine._value(regime)

    @staticmethod
    def _regime_value(request: DecisionEngineRequest) -> Optional[str]:
        if request.regime_snapshot is None:
            return None
        return DecisionEngine._value(request.regime_snapshot.regime)

    @staticmethod
    def _value(value: object) -> object:
        return getattr(value, "value", value)

    @staticmethod
    def _dedupe(reason_codes: list[str]) -> list[str]:
        deduped: list[str] = []
        for reason_code in reason_codes:
            if reason_code not in deduped:
                deduped.append(reason_code)
        return deduped
