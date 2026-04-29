import random
from dataclasses import dataclass, field

from quant.execution.state_machine import ExecutionEvent, ExecutionState, ExecutionStateMachine
from quant.schemas.enums import OrderKind, TimeInForce, TradeSide
from quant.schemas.execution import (
    ExecutionFillEvent,
    OrderIntent,
    ProtectiveExitPlan,
    ProtectiveExitTriggerEvent,
)


@dataclass
class Order:
    id: int
    client_order_id: str
    symbol: str
    side: str
    status: str
    qty: float
    filled_qty: float
    price: float
    created_index: int
    execute_index: int
    fill_price: float | None = None
    slippage: float = 0.0
    fill_index: int | None = None
    fill_events: list[ExecutionFillEvent] = field(default_factory=list)


@dataclass
class Position:
    size: float = 0.0
    entry_price: float = 0.0
    pnl: float = 0.0

    @property
    def avg_price(self):
        return self.entry_price

    def update(self, side, fill_price, filled_qty):
        signed_qty = filled_qty if side == "buy" else -filled_qty

        if self.size == 0.0 or self.size * signed_qty > 0:
            new_size = self.size + signed_qty
            total_cost = abs(self.size) * self.entry_price + abs(signed_qty) * fill_price
            self.entry_price = total_cost / abs(new_size)
            self.size = new_size
            return

        if abs(signed_qty) < abs(self.size):
            self.size += signed_qty
            return

        if abs(signed_qty) == abs(self.size):
            self.size = 0.0
            self.entry_price = 0.0
            return

        remaining_qty = signed_qty + self.size
        self.size = remaining_qty
        self.entry_price = fill_price


class ExecutionEngine:
    IDLE = ExecutionState.IDLE
    ORDER_PENDING = ExecutionState.ORDER_PENDING
    FILLED = ExecutionState.FILLED
    PARTIAL = ExecutionState.PARTIAL
    REJECTED = ExecutionState.REJECTED
    POSITION_OPEN = ExecutionState.POSITION_OPEN
    EXIT = ExecutionState.EXIT

    def __init__(self, execution_delay=0, seed=1, account=None, delay_across_bars=False):
        if execution_delay < 0 or execution_delay > 1:
            raise ValueError("execution_delay must be 0 or 1")

        self.execution_delay = execution_delay
        self.delay_across_bars = delay_across_bars
        self.random = random.Random(seed)
        self.state_machine = ExecutionStateMachine()
        self.state = self.state_machine.state
        self.orders = []
        self.orders_by_client_order_id = {}
        self.pending_orders = []
        self.protective_exit_plans = {}
        self.protective_exit_events = []
        self.position = Position()
        self.account = account
        self.last_order_status = None
        self.next_order_id = 1

    def on_signal(self, signal, price, index):
        client_order_id = self._resolve_client_order_id(signal)
        existing_order = self.orders_by_client_order_id.get(client_order_id)
        if existing_order is not None:
            return self._order_result(existing_order)

        order = self._create_order(signal, price, index)

        if order is None:
            self._transition(ExecutionEvent.ORDER_REJECTED, self.REJECTED)
            self.last_order_status = self.REJECTED
            return {
                "status": "rejected",
            }

        self.orders.append(order)
        self.orders_by_client_order_id[order.client_order_id] = order
        self._transition(ExecutionEvent.SIGNAL_ACCEPTED, self.ORDER_PENDING)

        if self.execution_delay == 1:
            self.pending_orders.append(order)
            return self._order_result(order)

        return self._match_order(order, price, index)

    def on_order_intent(self, order_intent, price, index, protective_exit_plan=None):
        plan = None
        if protective_exit_plan is not None:
            plan = self._coerce_protective_exit_plan(protective_exit_plan)
            if plan.parent_client_order_id != order_intent.client_order_id:
                raise ValueError("protective exit plan must reference the parent order client_order_id")

        result = self.on_signal(order_intent, price=price, index=index)
        if plan is not None and result.get("filled_qty", 0.0) > 0.0:
            plan = self._plan_with_quantity(plan, result["filled_qty"])
            result["protective_exit_plan"] = self.register_protective_exit(plan)
        return result

    def on_bar(self, price, index):
        due_orders = [order for order in self.pending_orders if order.execute_index == index]
        self.pending_orders = [order for order in self.pending_orders if order.execute_index > index]

        if not due_orders:
            return self.evaluate_protective_exits(price, index)

        return self._match_order(due_orders[0], price, index)

    def register_protective_exit(self, protective_exit_plan):
        plan = self._coerce_protective_exit_plan(protective_exit_plan)
        self.protective_exit_plans[plan.exit_plan_id] = plan
        return plan.to_payload()

    def cancel_protective_exit(self, exit_plan_id, reason="manual_cancel"):
        plan = self.protective_exit_plans.get(exit_plan_id)
        if plan is None:
            return {
                "status": "not_found",
                "exit_plan_id": exit_plan_id,
                "reason": reason,
            }

        plan.active = False
        return {
            "status": "cancelled",
            "exit_plan_id": exit_plan_id,
            "reason": reason,
            "exit_plan": plan.to_payload(),
        }

    def evaluate_protective_exits(self, price, index):
        for plan in list(self.protective_exit_plans.values()):
            if not plan.active:
                continue

            trigger_type = self._protective_trigger_type(plan, price)
            if trigger_type is not None:
                return self._trigger_protective_exit(plan, trigger_type, price, index)

        return None

    def _create_order(self, signal, price, index):
        side = self._resolve_side(signal)
        if side not in ["buy", "sell"]:
            return None

        order = Order(
            id=self.next_order_id,
            client_order_id=self._resolve_client_order_id(signal),
            symbol=self._resolve_symbol(signal),
            side=side,
            status="pending",
            qty=self._resolve_quantity(signal),
            filled_qty=0.0,
            price=price,
            created_index=index,
            execute_index=index + 1 if self.delay_across_bars and self.execution_delay == 1 else index,
        )
        self.next_order_id += 1
        return order

    def _match_order(self, order, price, index):
        outcome = self._draw_outcome()

        if outcome == "rejected":
            order.status = "rejected"
            self._transition(ExecutionEvent.ORDER_REJECTED, self.REJECTED)
            self.last_order_status = self.REJECTED
            return self._order_result(order)

        slippage = self._calculate_slippage(price, order.side)
        fill_price = price + slippage

        if outcome == "partial":
            fill_ratio = self.random.uniform(0.1, 0.9)
            filled_qty = order.qty * fill_ratio
            order.status = "partial"
            order.filled_qty = filled_qty
            self._transition(ExecutionEvent.ORDER_PARTIALLY_FILLED, self.PARTIAL)
            self.last_order_status = self.PARTIAL
        else:
            filled_qty = order.qty
            order.status = "filled"
            order.filled_qty = filled_qty
            self._transition(ExecutionEvent.ORDER_FILLED, self.FILLED)
            self.last_order_status = self.FILLED

        order.fill_price = fill_price
        order.slippage = slippage
        order.fill_index = index
        self._record_fill_event(order, filled_qty, fill_price, index)
        previous_position_size = self.position.size
        self.position.update(order.side, fill_price, filled_qty)
        if self.position.size != 0.0:
            self._transition(ExecutionEvent.POSITION_OPENED, self.POSITION_OPEN)
        elif previous_position_size != 0.0:
            self._transition(ExecutionEvent.POSITION_CLOSED, self.EXIT)

        result = self._order_result(order)
        if self.account is not None:
            self.account.on_fill(result)
        return result

    def _order_result(self, order):
        result = {
            "order_id": order.id,
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side,
            "status": order.status,
            "filled_qty": order.filled_qty,
            "remaining_qty": order.qty - order.filled_qty,
        }

        if order.fill_price is not None:
            result["fill_price"] = order.fill_price
            result["slippage"] = order.slippage
            result["fill_index"] = order.fill_index
            result["fill_event"] = order.fill_events[-1].to_payload()
            result["fill_events"] = [event.to_payload() for event in order.fill_events]

        return result

    def _record_fill_event(self, order, fill_qty, fill_price, index):
        fill_event = ExecutionFillEvent(
            fill_event_id=f"{order.client_order_id}-fill-{len(order.fill_events) + 1}",
            client_order_id=order.client_order_id,
            broker_order_id=str(order.id),
            symbol=order.symbol,
            side=order.side,
            status=order.status,
            fill_qty=fill_qty,
            fill_price=fill_price,
            cumulative_filled_qty=order.filled_qty,
            remaining_qty=order.qty - order.filled_qty,
            fill_index=index,
        )
        order.fill_events.append(fill_event)
        return fill_event

    def _trigger_protective_exit(self, plan, trigger_type, price, index):
        quantity = min(plan.quantity, abs(self.position.size)) if self.position.size != 0.0 else plan.quantity
        if quantity <= 0.0:
            plan.active = False
            return {
                "status": "skipped",
                "exit_plan_id": plan.exit_plan_id,
                "reason": "no_position_to_protect",
            }

        exit_side = self._protective_exit_side(plan)
        trigger_price = plan.stop_loss_price if trigger_type == "stop_loss" else plan.take_profit_price
        client_order_id = f"{plan.exit_plan_id}:{trigger_type}:{index}"
        order_intent = OrderIntent(
            order_intent_id=f"order-intent-{client_order_id}",
            decision_id=f"protective-exit:{plan.exit_plan_id}",
            client_order_id=client_order_id,
            symbol=plan.symbol,
            side=exit_side,
            order_type=OrderKind.MARKET,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            risk_approved=True,
            created_at=index,
            trace=plan.trace,
        )
        event = ProtectiveExitTriggerEvent(
            trigger_event_id=f"{plan.exit_plan_id}:trigger:{trigger_type}:{index}",
            exit_plan_id=plan.exit_plan_id,
            parent_client_order_id=plan.parent_client_order_id,
            symbol=plan.symbol,
            trigger_type=trigger_type,
            trigger_price=trigger_price,
            market_price=price,
            quantity=quantity,
            exit_side=exit_side,
            triggered_at=index,
            order_intent=order_intent,
            trace=plan.trace,
        )

        plan.active = False
        self.protective_exit_events.append(event)
        execution_result = self.on_order_intent(order_intent, price=price, index=index)
        return {
            "status": "triggered",
            "exit_plan": plan.to_payload(),
            "trigger_event": event.to_payload(),
            "execution_result": execution_result,
        }

    def _coerce_protective_exit_plan(self, protective_exit_plan):
        if isinstance(protective_exit_plan, ProtectiveExitPlan):
            return protective_exit_plan
        return ProtectiveExitPlan.from_payload(protective_exit_plan)

    def _plan_with_quantity(self, plan, quantity):
        if quantity == plan.quantity:
            return plan
        if hasattr(plan, "model_copy"):
            return plan.model_copy(update={"quantity": quantity})
        return plan.copy(update={"quantity": quantity})

    def _protective_trigger_type(self, plan, price):
        entry_side = self._side_value(plan.entry_side)
        if entry_side == TradeSide.BUY.value:
            if price <= plan.stop_loss_price:
                return "stop_loss"
            if plan.take_profit_price is not None and price >= plan.take_profit_price:
                return "take_profit"
            return None

        if price >= plan.stop_loss_price:
            return "stop_loss"
        if plan.take_profit_price is not None and price <= plan.take_profit_price:
            return "take_profit"
        return None

    def _protective_exit_side(self, plan):
        entry_side = self._side_value(plan.entry_side)
        if entry_side == TradeSide.BUY.value:
            return TradeSide.SELL
        return TradeSide.BUY

    def _side_value(self, side):
        return side.value if hasattr(side, "value") else side

    def _resolve_client_order_id(self, signal):
        if isinstance(signal, OrderIntent):
            return signal.client_order_id
        return signal.get("client_order_id") or f"sim-{self.next_order_id}"

    def _resolve_side(self, signal):
        if isinstance(signal, OrderIntent):
            return signal.side.value if hasattr(signal.side, "value") else signal.side
        return signal.get("signal")

    def _resolve_symbol(self, signal):
        if isinstance(signal, OrderIntent):
            return signal.symbol
        return signal.get("symbol", "DEFAULT")

    def _resolve_quantity(self, signal):
        if isinstance(signal, OrderIntent):
            return signal.quantity
        return signal.get("quantity", 1.0)

    def _draw_outcome(self):
        value = self.random.random()

        if value < 0.8:
            return "filled"
        if value < 0.95:
            return "partial"
        return "rejected"

    def _calculate_slippage(self, price, side):
        slippage = price * self.random.uniform(0.0005, 0.002)
        if side == "buy":
            return slippage
        return -slippage

    def exit_position(self):
        self.position = Position()
        self._transition(ExecutionEvent.POSITION_CLOSED, self.EXIT)

    def _transition(self, event, next_state):
        self.state = self.state_machine.transition(event, next_state)
        return self.state
