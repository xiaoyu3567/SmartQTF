from dataclasses import dataclass, field
from typing import Iterable

from quant.execution.broker import BrokerAdapter
from quant.execution.state_machine import ExecutionEvent, ExecutionState, ExecutionStateMachine
from quant.schemas.enums import OrderStatus
from quant.schemas.execution import BrokerOrderResult


STATUS_RANK = {
    OrderStatus.CREATED: 0,
    OrderStatus.PENDING: 1,
    OrderStatus.ACCEPTED: 2,
    OrderStatus.PARTIAL: 3,
    OrderStatus.FILLED: 4,
    OrderStatus.CANCELLED: 4,
    OrderStatus.REJECTED: 4,
    OrderStatus.UNKNOWN: -1,
}

OPEN_STATUSES = {
    OrderStatus.CREATED,
    OrderStatus.PENDING,
    OrderStatus.ACCEPTED,
    OrderStatus.PARTIAL,
    OrderStatus.UNKNOWN,
}


@dataclass
class OrderSyncResult:
    client_order_id: str
    source: str
    previous_status: OrderStatus | None
    status: OrderStatus
    changed: bool
    state: str
    order: BrokerOrderResult


@dataclass
class DualChannelOrderSynchronizer:
    """Merge WebSocket pushes and REST polling into one deterministic order state."""

    broker: BrokerAdapter
    local_orders: dict[str, BrokerOrderResult] = field(default_factory=dict)
    state_machine: ExecutionStateMachine = field(default_factory=ExecutionStateMachine)

    @property
    def state(self) -> str:
        return self.state_machine.state

    def track(self, order: BrokerOrderResult) -> OrderSyncResult:
        return self._merge(order, source="local")

    def apply_websocket_update(self, order: BrokerOrderResult) -> OrderSyncResult:
        return self._merge(order, source="websocket")

    def poll_rest(self, client_order_ids: Iterable[str] | None = None) -> list[OrderSyncResult]:
        if client_order_ids is None:
            client_order_ids = self._open_client_order_ids()

        results: list[OrderSyncResult] = []
        for client_order_id in client_order_ids:
            try:
                broker_order = self.broker.get_order(client_order_id)
            except KeyError:
                broker_order = self._mark_unknown(client_order_id)
            results.append(self._merge(broker_order, source="rest"))
        return results

    def snapshot(self) -> list[BrokerOrderResult]:
        return list(self.local_orders.values())

    def _open_client_order_ids(self) -> list[str]:
        return [
            order.client_order_id
            for order in self.local_orders.values()
            if order.status in OPEN_STATUSES
        ]

    def _mark_unknown(self, client_order_id: str) -> BrokerOrderResult:
        existing = self.local_orders[client_order_id]
        return BrokerOrderResult(
            client_order_id=existing.client_order_id,
            broker_order_id=existing.broker_order_id,
            symbol=existing.symbol,
            side=existing.side,
            status=OrderStatus.UNKNOWN,
            requested_qty=existing.requested_qty,
            filled_qty=existing.filled_qty,
            avg_fill_price=existing.avg_fill_price,
            trace=existing.trace,
        )

    def _merge(self, incoming: BrokerOrderResult, source: str) -> OrderSyncResult:
        existing = self.local_orders.get(incoming.client_order_id)
        previous_status = existing.status if existing is not None else None
        order = self._choose_order(existing, incoming)
        self.local_orders[order.client_order_id] = order
        changed = existing != order
        if changed:
            self._drive_state_machine(order.status)

        return OrderSyncResult(
            client_order_id=order.client_order_id,
            source=source,
            previous_status=previous_status,
            status=order.status,
            changed=changed,
            state=self.state,
            order=order,
        )

    def _choose_order(
        self,
        existing: BrokerOrderResult | None,
        incoming: BrokerOrderResult,
    ) -> BrokerOrderResult:
        if existing is None:
            return incoming
        if incoming.status == OrderStatus.UNKNOWN:
            return incoming
        if existing.status == OrderStatus.UNKNOWN:
            return incoming
        if STATUS_RANK[incoming.status] > STATUS_RANK[existing.status]:
            return incoming
        if incoming.filled_qty > existing.filled_qty:
            return incoming
        return existing

    def _drive_state_machine(self, status: OrderStatus) -> None:
        if status in {OrderStatus.CREATED, OrderStatus.PENDING, OrderStatus.ACCEPTED}:
            self._accept_if_possible()
            return
        if status == OrderStatus.PARTIAL:
            self._accept_if_possible()
            self._transition_if_possible(ExecutionEvent.ORDER_PARTIALLY_FILLED, ExecutionState.PARTIAL)
            self._transition_if_possible(ExecutionEvent.POSITION_OPENED, ExecutionState.POSITION_OPEN)
            return
        if status == OrderStatus.FILLED:
            self._accept_if_possible()
            self._transition_if_possible(ExecutionEvent.ORDER_FILLED, ExecutionState.FILLED)
            self._transition_if_possible(ExecutionEvent.POSITION_OPENED, ExecutionState.POSITION_OPEN)
            return
        if status == OrderStatus.REJECTED:
            self._accept_if_possible()
            self._transition_if_possible(ExecutionEvent.ORDER_REJECTED, ExecutionState.REJECTED)

    def _accept_if_possible(self) -> None:
        self._transition_if_possible(ExecutionEvent.SIGNAL_ACCEPTED, ExecutionState.ORDER_PENDING)

    def _transition_if_possible(self, event: str, next_state: str) -> bool:
        try:
            self.state_machine.transition(event, next_state)
        except ValueError:
            return False
        return True
