from dataclasses import dataclass

from quant.schemas import (
    OrderIntent,
    OrderKind,
    ProtectiveExitPlan,
    RiskDecision,
    RiskKillSwitchDecision,
    RiskKillSwitchTriggerInput,
    RiskDecisionLogRecord,
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

        applied_rules = []
        for rule in self.rules:
            decision = rule.evaluate(context)
            if decision is not None:
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
        )
        self._log_risk_decision(decision, signal_payload, context.price)
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
