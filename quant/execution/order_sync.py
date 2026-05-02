from dataclasses import dataclass, field
from typing import Iterable, Protocol

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

SOURCE_RANK = {
    "local": 0,
    "websocket": 1,
    "rest": 2,
}

TERMINAL_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
}

OPEN_STATUSES = {
    OrderStatus.CREATED,
    OrderStatus.PENDING,
    OrderStatus.ACCEPTED,
    OrderStatus.PARTIAL,
    OrderStatus.UNKNOWN,
}


class OrderWebSocketAdapter(Protocol):
    """Minimal live order update stream consumed by the synchronizer."""

    def connect(self) -> None:
        ...

    def iter_updates(self) -> Iterable[BrokerOrderResult]:
        ...


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
    order_sources: dict[str, str] = field(default_factory=dict)
    state_machine: ExecutionStateMachine = field(default_factory=ExecutionStateMachine)
    poll_interval_seconds: float = 1.0
    websocket_stale_after_seconds: float = 30.0
    reconnect_backoff_seconds: float = 1.0
    max_reconnect_backoff_seconds: float = 60.0
    websocket_connected: bool = False
    polling_fallback_active: bool = False
    websocket_disconnect_reason: str | None = None
    websocket_disconnect_count: int = 0
    reconnect_attempts: int = 0
    last_websocket_event_at: float | None = None
    last_poll_at: float | None = None
    next_poll_at: float = 0.0
    next_reconnect_at: float = 0.0
    last_poll_skipped_reason: str | None = None

    @property
    def state(self) -> str:
        return self.state_machine.state

    def track(self, order: BrokerOrderResult) -> OrderSyncResult:
        return self._merge(order, source="local")

    def apply_websocket_update(
        self,
        order: BrokerOrderResult,
        *,
        observed_at: float | None = None,
    ) -> OrderSyncResult:
        self.websocket_connected = True
        self.polling_fallback_active = False
        self.websocket_disconnect_reason = None
        if observed_at is not None:
            self.last_websocket_event_at = observed_at
        return self._merge(order, source="websocket")

    def run_websocket_loop(
        self,
        adapter: OrderWebSocketAdapter,
        *,
        now: float = 0.0,
        max_updates: int | None = None,
    ) -> list[OrderSyncResult]:
        """Consume a bounded batch of WS updates, falling back to polling on failure."""

        if now < self.next_reconnect_at:
            return []

        results: list[OrderSyncResult] = []
        try:
            adapter.connect()
        except Exception as exc:
            self.mark_websocket_disconnected(str(exc), now=now)
            return results

        self.websocket_connected = True
        self.polling_fallback_active = False
        self.websocket_disconnect_reason = None
        self.reconnect_attempts = 0

        try:
            for update in adapter.iter_updates():
                results.append(self.apply_websocket_update(update, observed_at=now))
                if max_updates is not None and len(results) >= max_updates:
                    break
        except Exception as exc:
            self.mark_websocket_disconnected(str(exc), now=now)
        return results

    def mark_websocket_disconnected(self, reason: str, *, now: float) -> None:
        self.websocket_connected = False
        self.polling_fallback_active = True
        self.websocket_disconnect_reason = reason
        self.websocket_disconnect_count += 1
        self.reconnect_attempts += 1
        delay = min(
            self.reconnect_backoff_seconds * (2 ** (self.reconnect_attempts - 1)),
            self.max_reconnect_backoff_seconds,
        )
        self.next_reconnect_at = now + delay

    def refresh_websocket_health(self, *, now: float) -> bool:
        if not self.websocket_connected or self.last_websocket_event_at is None:
            return self.polling_fallback_active

        if now - self.last_websocket_event_at >= self.websocket_stale_after_seconds:
            self.mark_websocket_disconnected("websocket_stale", now=now)
        return self.polling_fallback_active

    def poll_fallback_if_needed(
        self,
        *,
        now: float,
        client_order_ids: Iterable[str] | None = None,
    ) -> list[OrderSyncResult]:
        if not self.refresh_websocket_health(now=now):
            return []
        return self.poll_rest(client_order_ids, now=now)

    def poll_rest(
        self,
        client_order_ids: Iterable[str] | None = None,
        *,
        now: float | None = None,
        force: bool = False,
    ) -> list[OrderSyncResult]:
        if now is not None and not force and now < self.next_poll_at:
            self.last_poll_skipped_reason = "rest_poll_rate_limited"
            return []

        self.last_poll_skipped_reason = None
        if now is not None:
            self.last_poll_at = now
            self.next_poll_at = now + self.poll_interval_seconds

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
        existing_source = self.order_sources.get(incoming.client_order_id)
        previous_status = existing.status if existing is not None else None
        order = self._choose_order(existing, incoming, existing_source, source)
        selected_source = source if order is incoming else existing_source or source
        self.local_orders[order.client_order_id] = order
        self.order_sources[order.client_order_id] = selected_source
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
        existing_source: str | None,
        incoming_source: str,
    ) -> BrokerOrderResult:
        if existing is None:
            return incoming

        if (
            existing_source == "rest"
            and incoming_source == "websocket"
            and self._is_terminal(existing.status)
        ):
            return existing

        if incoming.status == OrderStatus.UNKNOWN:
            return incoming
        if existing.status == OrderStatus.UNKNOWN:
            return incoming

        incoming_rank = self._status_rank(incoming.status)
        existing_rank = self._status_rank(existing.status)
        incoming_source_rank = SOURCE_RANK.get(incoming_source, -1)
        existing_source_rank = SOURCE_RANK.get(existing_source or "local", -1)

        if incoming_rank > existing_rank:
            return incoming
        if incoming_rank < existing_rank:
            return existing
        if incoming.filled_qty > existing.filled_qty:
            return incoming
        if incoming_source_rank > existing_source_rank:
            return incoming
        return existing

    def _status_rank(self, status: OrderStatus) -> int:
        return STATUS_RANK.get(status, STATUS_RANK.get(OrderStatus(status), -1))

    def _is_terminal(self, status: OrderStatus) -> bool:
        return status in TERMINAL_STATUSES or status in {item.value for item in TERMINAL_STATUSES}

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
        if status == OrderStatus.CANCELLED:
            self._accept_if_possible()
            self._transition_if_possible(ExecutionEvent.ORDER_CANCELLED, ExecutionState.CANCELLED)

    def _accept_if_possible(self) -> None:
        self._transition_if_possible(ExecutionEvent.SIGNAL_ACCEPTED, ExecutionState.ORDER_PENDING)

    def _transition_if_possible(self, event: str, next_state: str) -> bool:
        try:
            self.state_machine.transition(event, next_state)
        except ValueError:
            return False
        return True
