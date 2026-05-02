from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from quant.schemas import (
    BracketExecutionPlan,
    BracketExecutionPolicy,
    BracketProtectiveLeg,
    BrokerOrderRequest,
    OrderIntent,
    OrderKind,
    ProtectiveExitPlan,
    RiskEngineV2Request,
    RiskDecision,
    RiskKillSwitchDecision,
    RiskKillSwitchTriggerInput,
    RiskDecisionLogRecord,
    RiskSizingResult,
    TimeInForce,
    TradeSide,
)


@dataclass
class RiskContext:
    signal: dict
    account: object
    price: float
    symbol: str
    max_position_pct: float
    stop_loss_pct: float
    take_profit_pct: float | None
    kill_switch_enabled: bool
    kill_switch_reason: str | None


class RiskRule:
    code = "risk_rule"

    def evaluate(self, context):
        raise NotImplementedError


class ValidSignalRule(RiskRule):
    code = "valid_signal"

    def evaluate(self, context):
        if context.signal is None:
            return RiskDecision.reject("missing_signal", "signal is required")

        side = context.signal.get("signal")
        if side not in ["buy", "sell"]:
            return RiskDecision.reject("invalid_signal", "signal must be buy or sell", fatal=True)

        return None


class KillSwitchRule(RiskRule):
    code = "kill_switch"

    def evaluate(self, context):
        if not context.kill_switch_enabled:
            return None

        reason = context.kill_switch_reason or "kill switch is enabled"
        return RiskDecision.reject("kill_switch_enabled", reason, fatal=True)


class MaxDrawdownRule(RiskRule):
    code = "max_drawdown"

    def __init__(self, max_drawdown_pct):
        self.max_drawdown_pct = max_drawdown_pct

    def evaluate(self, context):
        drawdown = (
            context.account.initial_balance - context.account.equity
        ) / context.account.initial_balance
        if drawdown > self.max_drawdown_pct:
            return RiskDecision.reject(
                "max_drawdown_exceeded",
                "account drawdown exceeded configured maximum",
                fatal=True,
            )
        return None


class PositionSizingRule(RiskRule):
    code = "position_sizing"

    def evaluate(self, context):
        side = context.signal["signal"]
        position = context.account.get_position(context.symbol)

        if side == "sell":
            quantity = abs(position.size) if position.size > 0.0 else 0.0
        else:
            quantity = (context.account.balance * context.max_position_pct) / context.price

        if quantity <= 0.0:
            return RiskDecision.reject(
                "zero_quantity",
                "calculated order quantity is zero",
            )

        context.signal["quantity"] = quantity
        return None


class ProtectiveExitRule(RiskRule):
    code = "protective_exit"

    def evaluate(self, context):
        side = context.signal["signal"]

        if side == "buy":
            stop_loss = context.price * (1.0 - context.stop_loss_pct)
            take_profit = (
                context.price * (1.0 + context.take_profit_pct)
                if context.take_profit_pct is not None
                else None
            )
        else:
            stop_loss = context.price * (1.0 + context.stop_loss_pct)
            take_profit = (
                context.price * (1.0 - context.take_profit_pct)
                if context.take_profit_pct is not None
                else None
            )

        context.signal["stop_loss"] = stop_loss
        if take_profit is not None:
            context.signal["take_profit"] = take_profit

        return None


class RiskManager:
    def __init__(
        self,
        max_position_pct=0.1,
        stop_loss_pct=0.02,
        take_profit_pct=0.04,
        max_drawdown_pct=0.1,
        symbol="BTCUSDT",
        risk_logger=None,
        run_id="risk-run",
        daily_loss_limit_pct=None,
        consecutive_loss_limit=None,
        api_failure_rate_limit=None,
    ):
        if max_position_pct <= 0.0 or max_position_pct > 1.0:
            raise ValueError("max_position_pct must be between 0.0 and 1.0")
        if stop_loss_pct <= 0.0:
            raise ValueError("stop_loss_pct must be greater than 0.0")
        if take_profit_pct is not None and take_profit_pct <= 0.0:
            raise ValueError("take_profit_pct must be greater than 0.0")
        if max_drawdown_pct <= 0.0 or max_drawdown_pct > 1.0:
            raise ValueError("max_drawdown_pct must be between 0.0 and 1.0")
        if daily_loss_limit_pct is not None and not 0.0 < daily_loss_limit_pct <= 1.0:
            raise ValueError("daily_loss_limit_pct must be in (0.0, 1.0]")
        if consecutive_loss_limit is not None and consecutive_loss_limit <= 0:
            raise ValueError("consecutive_loss_limit must be greater than 0")
        if api_failure_rate_limit is not None and not 0.0 < api_failure_rate_limit <= 1.0:
            raise ValueError("api_failure_rate_limit must be in (0.0, 1.0]")

        self.max_position_pct = max_position_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.symbol = symbol
        self.risk_logger = risk_logger
        self.run_id = run_id
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.consecutive_loss_limit = consecutive_loss_limit
        self.api_failure_rate_limit = api_failure_rate_limit
        self.kill_switch_enabled = False
        self.kill_switch_reason = None
        self.rules = [
            KillSwitchRule(),
            ValidSignalRule(),
            MaxDrawdownRule(max_drawdown_pct=max_drawdown_pct),
            PositionSizingRule(),
            ProtectiveExitRule(),
        ]

    def apply(self, signal, account, price):
        decision = self.evaluate(signal, account, price)
        if not decision.approved:
            return None

        return decision.order_payload

    def evaluate(self, signal, account, price):
        if signal is None:
            signal_payload = None
        else:
            signal_payload = dict(signal)
            signal_payload["symbol"] = signal.get("symbol", self.symbol)

        context = RiskContext(
            signal=signal_payload,
            account=account,
            price=price,
            symbol=self.symbol if signal_payload is None else signal_payload["symbol"],
            max_position_pct=self.max_position_pct,
            stop_loss_pct=self.stop_loss_pct,
            take_profit_pct=self.take_profit_pct,
            kill_switch_enabled=self.kill_switch_enabled,
            kill_switch_reason=self.kill_switch_reason,
        )
        risk_decision_id = self._risk_decision_id(signal_payload, context.symbol)

        applied_rules = []
        for rule in self.rules:
            decision = rule.evaluate(context)
            if decision is not None:
                decision = self._with_risk_decision_id(decision, risk_decision_id)
                self._log_risk_decision(decision, signal_payload, context.price)
                return decision
            applied_rules.append(rule.code)

        order_intent = self._build_order_intent(context.signal)
        protective_exit_plan = self._build_protective_exit_plan(context.signal, order_intent)
        decision = RiskDecision.approve(
            order_payload=context.signal,
            reason_codes=applied_rules,
            order_intent=order_intent,
            protective_exit_plan=protective_exit_plan,
            risk_decision_id=risk_decision_id,
        )
        self._log_risk_decision(decision, signal_payload, context.price)
        return decision

    def evaluate_v2(self, request):
        request = (
            request
            if isinstance(request, RiskEngineV2Request)
            else RiskEngineV2Request.from_payload(request)
        )
        trace = request.trace or request.trade_intent.trace or request.capital_budget.trace
        risk_decision_id = f"risk:{request.trade_intent.decision_id}"

        rejection = self._v2_request_rejection(request, risk_decision_id)
        if rejection is not None:
            self._log_risk_decision(rejection, self._v2_log_signal(request, trace), request.market_constraints.entry_price)
            return rejection

        sizing = self._calculate_v2_sizing(request)
        rejection = self._v2_sizing_rejection(request, sizing, risk_decision_id)
        if rejection is not None:
            self._log_risk_decision(rejection, self._v2_log_signal(request, trace), request.market_constraints.entry_price)
            return rejection

        order_intent = self._build_v2_order_intent(request, sizing, trace)
        protective_exit_plan = self._build_v2_protective_exit_plan(request, order_intent, sizing, trace)
        execution_order_plan = self._build_v2_execution_order_plan(
            request,
            order_intent,
            sizing,
            risk_decision_id,
            trace,
        )
        order_payload = {
            "symbol": request.trade_intent.symbol,
            "side": request.trade_intent.side,
            "quantity": sizing.adjusted_quantity,
            "entry_price": sizing.entry_price,
            "stop_loss": sizing.stop_loss_price,
            "take_profit": sizing.take_profit_price,
            "risk_budget_usdt": sizing.risk_budget_usdt,
            "max_loss_usdt": sizing.max_loss_usdt,
            "source": "risk_engine_v2",
        }
        decision = RiskDecision.approve(
            order_payload=order_payload,
            reason_codes=sizing.reason_codes + ["risk_v2_approved"],
            order_intent=order_intent,
            protective_exit_plan=protective_exit_plan,
            execution_order_plan=execution_order_plan,
            sizing=sizing,
            risk_decision_id=risk_decision_id,
        )
        self._log_risk_decision(decision, self._v2_log_signal(request, trace), sizing.entry_price)
        return decision

    def enable_kill_switch(self, reason="manual kill switch"):
        self.kill_switch_enabled = True
        self.kill_switch_reason = reason

    def disable_kill_switch(self):
        self.kill_switch_enabled = False
        self.kill_switch_reason = None

    def evaluate_kill_switch_triggers(self, trigger_input):
        trigger = (
            trigger_input
            if isinstance(trigger_input, RiskKillSwitchTriggerInput)
            else RiskKillSwitchTriggerInput.from_payload(trigger_input)
        )

        reason_codes = []
        if (
            self.daily_loss_limit_pct is not None
            and trigger.daily_loss_pct is not None
            and trigger.daily_loss_pct > self.daily_loss_limit_pct
        ):
            reason_codes.append("daily_loss_limit_exceeded")
        if (
            self.consecutive_loss_limit is not None
            and trigger.consecutive_losses >= self.consecutive_loss_limit
        ):
            reason_codes.append("consecutive_loss_limit_exceeded")
        if (
            self.api_failure_rate_limit is not None
            and trigger.api_failure_rate is not None
            and trigger.api_failure_rate > self.api_failure_rate_limit
        ):
            reason_codes.append("api_failure_rate_limit_exceeded")

        if not reason_codes:
            return RiskKillSwitchDecision(
                triggered=False,
                reason_codes=["kill_switch_not_triggered"],
                reason=None,
                trigger_input=trigger,
            )

        reason = ", ".join(reason_codes)
        self.enable_kill_switch(reason)
        return RiskKillSwitchDecision(
            triggered=True,
            reason_codes=reason_codes,
            reason=reason,
            trigger_input=trigger,
        )

    def _legacy_apply(self, signal, account, price):
        if signal is None or self.is_drawdown_exceeded(account):
            return None

        side = signal["signal"]
        qty = self._calculate_quantity(side, account, price)
        if qty <= 0.0:
            return None

        order_signal = dict(signal)
        order_signal["symbol"] = signal.get("symbol", self.symbol)
        order_signal["quantity"] = qty
        order_signal["stop_loss"] = self._calculate_stop_loss(side, price)

        take_profit = self._calculate_take_profit(side, price)
        if take_profit is not None:
            order_signal["take_profit"] = take_profit

        return order_signal

    def _build_order_intent(self, signal):
        created_at = int(signal.get("timestamp", signal.get("signal_index", 0)))
        symbol = signal.get("symbol", self.symbol)
        side = TradeSide.BUY if signal["signal"] == "buy" else TradeSide.SELL
        client_order_id = signal.get(
            "client_order_id",
            f"risk-{symbol}-{created_at}-{side.value}",
        )
        decision_id = signal.get("decision_id", f"legacy-signal-{symbol}-{created_at}")

        return OrderIntent(
            order_intent_id=signal.get("order_intent_id", f"order-intent-{client_order_id}"),
            decision_id=decision_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type=OrderKind.MARKET,
            quantity=signal["quantity"],
            time_in_force=TimeInForce.GTC,
            risk_approved=True,
            created_at=created_at,
            trace=signal.get("trace"),
        )

    def _risk_decision_id(self, signal, symbol):
        if signal is None:
            return f"{self.run_id}:risk:{symbol}:0"

        decision_id = signal.get("decision_id")
        if decision_id:
            return f"risk:{decision_id}"

        client_order_id = signal.get("client_order_id")
        if client_order_id:
            return f"risk:{client_order_id}"

        timestamp = self._risk_log_timestamp(signal)
        return f"{self.run_id}:risk:{symbol}:{timestamp}"

    @staticmethod
    def _with_risk_decision_id(decision, risk_decision_id):
        if getattr(decision, "risk_decision_id", None) == risk_decision_id:
            return decision
        if hasattr(decision, "model_copy"):
            return decision.model_copy(update={"risk_decision_id": risk_decision_id})
        return decision.copy(update={"risk_decision_id": risk_decision_id})

    def _build_protective_exit_plan(self, signal, order_intent):
        stop_loss = signal.get("stop_loss")
        if stop_loss is None:
            return None

        client_order_id = order_intent.client_order_id
        return ProtectiveExitPlan(
            exit_plan_id=signal.get("protective_exit_plan_id", f"protective-exit-{client_order_id}"),
            parent_client_order_id=client_order_id,
            symbol=order_intent.symbol,
            entry_side=order_intent.side,
            quantity=order_intent.quantity,
            stop_loss_price=stop_loss,
            take_profit_price=signal.get("take_profit"),
            created_at=order_intent.created_at,
            trace=signal.get("trace"),
            metadata={"decision_id": order_intent.decision_id},
        )

    def _v2_request_rejection(self, request, risk_decision_id):
        trade_intent = request.trade_intent
        capital_budget = request.capital_budget
        constraints = request.market_constraints
        policy = request.risk_policy

        if self.kill_switch_enabled or policy.kill_switch_active:
            return RiskDecision.reject(
                "kill_switch_enabled",
                self.kill_switch_reason or "kill switch is enabled",
                fatal=True,
                risk_decision_id=risk_decision_id,
            )

        if not capital_budget.approved:
            return RiskDecision.reject(
                "capital_budget_not_approved",
                "capital budget must be approved before risk sizing",
                risk_decision_id=risk_decision_id,
            )

        if trade_intent.trade_intent_id != capital_budget.trade_intent_id:
            return RiskDecision.reject(
                "trade_intent_budget_mismatch",
                "capital budget does not belong to trade intent",
                fatal=True,
                risk_decision_id=risk_decision_id,
            )

        if trade_intent.symbol != capital_budget.symbol or trade_intent.symbol != constraints.symbol:
            return RiskDecision.reject(
                "symbol_mismatch",
                "trade intent, capital budget, and market constraints must use the same symbol",
                fatal=True,
                risk_decision_id=risk_decision_id,
            )

        if trade_intent.side != capital_budget.side:
            return RiskDecision.reject(
                "side_mismatch",
                "capital budget side must match trade intent side",
                fatal=True,
                risk_decision_id=risk_decision_id,
            )

        if trade_intent.side == TradeSide.SELL and not policy.allow_short_selling:
            return RiskDecision.reject(
                "short_selling_disabled",
                "risk policy does not allow short selling",
                risk_decision_id=risk_decision_id,
            )

        if trade_intent.stop_loss is None:
            return RiskDecision.reject(
                "missing_stop_loss",
                "risk v2 requires stop loss from trade intent",
                risk_decision_id=risk_decision_id,
            )

        entry_price = trade_intent.entry_price or constraints.entry_price
        if trade_intent.stop_loss == entry_price:
            return RiskDecision.reject(
                "invalid_stop_distance",
                "entry price and stop loss must differ",
                risk_decision_id=risk_decision_id,
            )

        if trade_intent.side == TradeSide.BUY and trade_intent.stop_loss >= entry_price:
            return RiskDecision.reject(
                "long_stop_loss_not_below_entry",
                "long risk plans require stop loss below entry",
                risk_decision_id=risk_decision_id,
            )

        if trade_intent.side == TradeSide.SELL and trade_intent.stop_loss <= entry_price:
            return RiskDecision.reject(
                "short_stop_loss_not_above_entry",
                "short risk plans require stop loss above entry",
                risk_decision_id=risk_decision_id,
            )

        return None

    def _calculate_v2_sizing(self, request):
        trade_intent = request.trade_intent
        budget = request.capital_budget
        constraints = request.market_constraints
        policy = request.risk_policy

        entry_price = trade_intent.entry_price or constraints.entry_price
        stop_loss_price = trade_intent.stop_loss
        take_profit_price = trade_intent.take_profit
        stop_distance = abs(entry_price - stop_loss_price)
        risk_budget = budget.adjusted_risk_budget_usdt
        raw_quantity = risk_budget / stop_distance if stop_distance > 0.0 else 0.0

        quantity_caps = [
            self._notional_to_quantity(budget.max_symbol_notional, entry_price),
            self._notional_to_quantity(budget.max_total_notional, entry_price),
            self._notional_to_quantity(budget.max_group_notional, entry_price),
            self._notional_to_quantity(budget.free_margin * policy.desired_leverage, entry_price),
        ]
        if constraints.max_quantity is not None:
            quantity_caps.append(constraints.max_quantity)

        capped_quantity = min([raw_quantity] + quantity_caps)
        adjusted_quantity = self._floor_to_step(capped_quantity, constraints.quantity_step)
        notional = adjusted_quantity * entry_price
        max_loss = adjusted_quantity * stop_distance
        unused_risk_budget = max(0.0, risk_budget - max_loss)
        risk_reward = None
        if take_profit_price is not None and stop_distance > 0.0:
            risk_reward = abs(take_profit_price - entry_price) / stop_distance

        reason_codes = list(policy.reason_codes) + ["risk_v2_from_trade_intent_capital_budget"]
        if adjusted_quantity < raw_quantity:
            reason_codes.append("quantity_capped")
        if unused_risk_budget > 0.0:
            reason_codes.append("unused_risk_budget")

        return RiskSizingResult(
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            stop_distance=stop_distance,
            risk_budget_usdt=risk_budget,
            raw_quantity=raw_quantity,
            adjusted_quantity=adjusted_quantity,
            notional=notional,
            max_loss_usdt=max_loss,
            unused_risk_budget_usdt=unused_risk_budget,
            leverage=policy.desired_leverage,
            risk_reward=risk_reward,
            constraints={
                "max_symbol_notional": budget.max_symbol_notional,
                "max_total_notional": budget.max_total_notional,
                "max_group_notional": budget.max_group_notional,
                "free_margin": budget.free_margin,
                "min_notional": constraints.min_notional,
                "min_quantity": constraints.min_quantity,
                "quantity_step": constraints.quantity_step,
                "max_leverage": constraints.max_leverage,
                "max_slippage_pct": policy.max_slippage_pct,
                "liquidation_buffer_pct": policy.liquidation_buffer_pct,
            },
            reason_codes=reason_codes,
        )

    def _v2_sizing_rejection(self, request, sizing, risk_decision_id):
        trade_intent = request.trade_intent
        constraints = request.market_constraints
        policy = request.risk_policy

        if sizing.stop_distance <= 0.0:
            return RiskDecision.reject(
                "invalid_stop_distance",
                "entry price and stop loss must differ",
                risk_decision_id=risk_decision_id,
            )

        if trade_intent.side == TradeSide.BUY and sizing.stop_loss_price >= sizing.entry_price:
            return RiskDecision.reject(
                "long_stop_loss_not_below_entry",
                "long risk plans require stop loss below entry",
                risk_decision_id=risk_decision_id,
            )

        if trade_intent.side == TradeSide.SELL and sizing.stop_loss_price <= sizing.entry_price:
            return RiskDecision.reject(
                "short_stop_loss_not_above_entry",
                "short risk plans require stop loss above entry",
                risk_decision_id=risk_decision_id,
            )

        if sizing.adjusted_quantity <= 0.0:
            return RiskDecision.reject(
                "zero_quantity_after_constraints",
                "risk v2 calculated zero quantity after constraints",
                risk_decision_id=risk_decision_id,
            )

        if sizing.adjusted_quantity < constraints.min_quantity:
            return RiskDecision.reject(
                "quantity_below_minimum",
                "order quantity is below exchange minimum",
                risk_decision_id=risk_decision_id,
            )

        if sizing.notional < constraints.min_notional:
            return RiskDecision.reject(
                "notional_below_minimum",
                "order notional is below exchange minimum",
                risk_decision_id=risk_decision_id,
            )

        if policy.desired_leverage > constraints.max_leverage:
            return RiskDecision.reject(
                "leverage_above_exchange_max",
                "desired leverage exceeds exchange maximum",
                risk_decision_id=risk_decision_id,
            )

        if constraints.price_tick is not None:
            tick_prices = {
                "entry_price": sizing.entry_price,
                "stop_loss": sizing.stop_loss_price,
            }
            if sizing.take_profit_price is not None:
                tick_prices["take_profit"] = sizing.take_profit_price
            for field_name, price in tick_prices.items():
                if not self._is_multiple(price, constraints.price_tick):
                    return RiskDecision.reject(
                        "price_tick_mismatch",
                        f"{field_name} is not aligned to exchange price_tick",
                        risk_decision_id=risk_decision_id,
                    )

        if sizing.risk_reward is not None and sizing.risk_reward < policy.min_risk_reward:
            return RiskDecision.reject(
                "risk_reward_below_minimum",
                "take profit does not satisfy minimum risk reward",
                risk_decision_id=risk_decision_id,
            )

        estimated_slippage_loss = sizing.notional * policy.max_slippage_pct
        if estimated_slippage_loss > sizing.risk_budget_usdt:
            return RiskDecision.reject(
                "slippage_exceeds_risk_budget",
                "configured slippage loss exceeds available risk budget",
                risk_decision_id=risk_decision_id,
            )

        liquidation_buffer_loss = sizing.notional * policy.liquidation_buffer_pct
        if liquidation_buffer_loss > request.capital_budget.free_margin:
            return RiskDecision.reject(
                "liquidation_buffer_insufficient",
                "free margin cannot cover liquidation buffer",
                risk_decision_id=risk_decision_id,
            )

        return None

    def _build_v2_order_intent(self, request, sizing, trace):
        trade_intent = request.trade_intent
        client_order_id = f"{trade_intent.decision_id}:risk-v2:{trade_intent.side}"
        return OrderIntent(
            order_intent_id=f"order-intent-{client_order_id}",
            decision_id=trade_intent.decision_id,
            client_order_id=client_order_id,
            symbol=trade_intent.symbol,
            side=trade_intent.side,
            order_type=request.risk_policy.order_type,
            quantity=sizing.adjusted_quantity,
            time_in_force=request.risk_policy.time_in_force,
            risk_approved=True,
            created_at=request.timestamp,
            trace=trace,
        )

    def _build_v2_protective_exit_plan(self, request, order_intent, sizing, trace):
        return ProtectiveExitPlan(
            exit_plan_id=f"protective-exit-{order_intent.client_order_id}",
            parent_client_order_id=order_intent.client_order_id,
            symbol=order_intent.symbol,
            entry_side=order_intent.side,
            quantity=order_intent.quantity,
            stop_loss_price=sizing.stop_loss_price,
            take_profit_price=sizing.take_profit_price,
            created_at=order_intent.created_at,
            trace=trace,
            metadata={
                "decision_id": order_intent.decision_id,
                "source": "risk_engine_v2",
            },
        )

    def _build_v2_execution_order_plan(self, request, order_intent, sizing, risk_decision_id, trace):
        entry_order = BrokerOrderRequest(
            client_order_id=order_intent.client_order_id,
            symbol=order_intent.symbol,
            side=order_intent.side,
            order_type=order_intent.order_type,
            quantity=order_intent.quantity,
            limit_price=order_intent.limit_price,
            time_in_force=order_intent.time_in_force,
            reduce_only=order_intent.reduce_only,
            trace=trace,
        )
        take_profit_order = None
        if sizing.take_profit_price is not None:
            take_profit_order = BracketProtectiveLeg(
                client_order_id=f"{order_intent.client_order_id}:tp",
                price=sizing.take_profit_price,
            )
        return BracketExecutionPlan(
            execution_plan_id=f"execution-plan-{order_intent.client_order_id}",
            idempotency_key=order_intent.client_order_id,
            risk_decision_id=risk_decision_id,
            allocation_id=request.capital_budget.budget_id,
            entry_order=entry_order,
            stop_loss_order=BracketProtectiveLeg(
                client_order_id=f"{order_intent.client_order_id}:sl",
                price=sizing.stop_loss_price,
            ),
            take_profit_order=take_profit_order,
            policy=BracketExecutionPolicy(
                protective_client_order_id=f"{order_intent.client_order_id}:protective",
                metadata={
                    "native_order_type": "oco",
                    "source": "risk_engine_v2",
                },
            ),
            risk_approved=True,
            trace=trace,
            metadata={
                "trade_intent_id": request.trade_intent.trade_intent_id,
                "budget_id": request.capital_budget.budget_id,
                "max_loss_usdt": sizing.max_loss_usdt,
                "unused_risk_budget_usdt": sizing.unused_risk_budget_usdt,
            },
        )

    def _v2_log_signal(self, request, trace):
        return {
            "signal": request.trade_intent.side,
            "symbol": request.trade_intent.symbol,
            "timestamp": request.timestamp,
            "decision_id": request.trade_intent.decision_id,
            "strategy_id": request.trade_intent.strategy_id,
            "trace": trace,
        }

    @staticmethod
    def _notional_to_quantity(notional, price):
        if price <= 0.0:
            return 0.0
        return max(0.0, notional / price)

    @staticmethod
    def _floor_to_step(value, step):
        if value <= 0.0:
            return 0.0
        value_decimal = Decimal(str(value))
        step_decimal = Decimal(str(step))
        return float((value_decimal / step_decimal).to_integral_value(rounding=ROUND_FLOOR) * step_decimal)

    @staticmethod
    def _is_multiple(value, step):
        return Decimal(str(value)) % Decimal(str(step)) == Decimal("0")

    def _log_risk_decision(self, decision, signal, price):
        if self.risk_logger is None:
            return

        timestamp = self._risk_log_timestamp(signal)
        symbol = self.symbol if signal is None else signal.get("symbol", self.symbol)
        decision_id = None if signal is None else signal.get("decision_id")
        strategy_id = None if signal is None else signal.get("strategy_id")
        trace = None if signal is None else signal.get("trace")
        run_id = self.run_id
        if trace is not None and getattr(trace, "run_id", None):
            run_id = trace.run_id
        elif signal is not None and signal.get("run_id"):
            run_id = signal["run_id"]

        record = RiskDecisionLogRecord(
            event_id=f"{run_id}:risk-log:{symbol}:{timestamp}:{'-'.join(decision.reason_codes)}",
            run_id=run_id,
            timestamp=timestamp,
            trace=trace,
            symbol=symbol,
            approved=decision.approved,
            reason_codes=list(decision.reason_codes),
            risk_decision=decision,
            strategy_id=strategy_id,
            decision_id=decision_id,
            metadata={"price": price},
        )
        self.risk_logger.append(record)

    def _risk_log_timestamp(self, signal):
        if signal is None:
            return 0
        return int(signal.get("timestamp", signal.get("signal_index", 0)))

    def should_stop_loss(self, order_signal, price):
        side = order_signal["signal"]
        stop_loss = order_signal["stop_loss"]

        if side == "buy":
            return price <= stop_loss
        if side == "sell":
            return price >= stop_loss

        raise ValueError("signal must be buy or sell")

    def is_drawdown_exceeded(self, account):
        drawdown = (account.initial_balance - account.equity) / account.initial_balance
        return drawdown > self.max_drawdown_pct

    def _calculate_quantity(self, side, account, price):
        position = account.get_position(self.symbol)

        if side == "sell":
            if position.size > 0.0:
                return abs(position.size)
            return 0.0

        return (account.balance * self.max_position_pct) / price

    def _calculate_stop_loss(self, side, price):
        if side == "buy":
            return price * (1.0 - self.stop_loss_pct)
        if side == "sell":
            return price * (1.0 + self.stop_loss_pct)

        raise ValueError("signal must be buy or sell")

    def _calculate_take_profit(self, side, price):
        if self.take_profit_pct is None:
            return None

        if side == "buy":
            return price * (1.0 + self.take_profit_pct)
        if side == "sell":
            return price * (1.0 - self.take_profit_pct)

        raise ValueError("signal must be buy or sell")
