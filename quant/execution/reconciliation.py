from typing import Iterable

from quant.execution.broker import BrokerAdapter
from quant.schemas.enums import OrderStatus
from quant.schemas.execution import (
    BrokerOrderResult,
    ReconciliationItem,
    ReconciliationReport,
)


OPEN_STATUSES = {
    OrderStatus.CREATED,
    OrderStatus.PENDING,
    OrderStatus.ACCEPTED,
    OrderStatus.PARTIAL,
    OrderStatus.UNKNOWN,
    OrderStatus.CREATED.value,
    OrderStatus.PENDING.value,
    OrderStatus.ACCEPTED.value,
    OrderStatus.PARTIAL.value,
    OrderStatus.UNKNOWN.value,
}


def reconcile_orders(
    broker: BrokerAdapter,
    local_orders: Iterable[BrokerOrderResult],
    symbols: Iterable[str] | None = None,
) -> ReconciliationReport:
    """Compare local order state with broker truth without mutating local state."""

    local_by_client_id = {order.client_order_id: order for order in local_orders}
    items: list[ReconciliationItem] = []
    matched_count = 0

    for local_order in local_by_client_id.values():
        broker_order = _get_broker_order(broker, local_order.client_order_id)
        if broker_order is None:
            items.append(
                ReconciliationItem(
                    client_order_id=local_order.client_order_id,
                    action="mark_unknown",
                    reason="broker_order_missing",
                    local_status=local_order.status,
                    requested_qty=local_order.requested_qty,
                    local_filled_qty=local_order.filled_qty,
                    trace=local_order.trace,
                )
            )
            continue

        if _orders_match(local_order, broker_order):
            matched_count += 1
            continue

        items.append(
            ReconciliationItem(
                client_order_id=local_order.client_order_id,
                action="update_local_from_broker",
                reason="broker_truth_differs",
                local_status=local_order.status,
                broker_status=broker_order.status,
                broker_order_id=broker_order.broker_order_id,
                requested_qty=broker_order.requested_qty,
                local_filled_qty=local_order.filled_qty,
                broker_filled_qty=broker_order.filled_qty,
                trace=broker_order.trace or local_order.trace,
            )
        )

    for broker_order in _list_broker_open_orders(broker, symbols):
        if broker_order.client_order_id in local_by_client_id:
            continue

        items.append(
            ReconciliationItem(
                client_order_id=broker_order.client_order_id,
                action="import_broker_open_order",
                reason="open_order_missing_locally",
                broker_status=broker_order.status,
                broker_order_id=broker_order.broker_order_id,
                requested_qty=broker_order.requested_qty,
                broker_filled_qty=broker_order.filled_qty,
                trace=broker_order.trace,
            )
        )

    missing_local_count = sum(1 for item in items if item.action == "import_broker_open_order")
    missing_broker_count = sum(1 for item in items if item.action == "mark_unknown")
    drift_count = sum(1 for item in items if item.action == "update_local_from_broker")

    return ReconciliationReport(
        broker_name=broker.name,
        checked_count=len(local_by_client_id),
        matched_count=matched_count,
        drift_count=drift_count,
        missing_local_count=missing_local_count,
        missing_broker_count=missing_broker_count,
        items=items,
    )


def _get_broker_order(
    broker: BrokerAdapter,
    client_order_id: str,
) -> BrokerOrderResult | None:
    try:
        return broker.get_order(client_order_id)
    except KeyError:
        return None


def _list_broker_open_orders(
    broker: BrokerAdapter,
    symbols: Iterable[str] | None,
) -> list[BrokerOrderResult]:
    if symbols is None:
        return list(broker.list_open_orders())

    orders: list[BrokerOrderResult] = []
    for symbol in symbols:
        orders.extend(broker.list_open_orders(symbol))
    return orders


def _orders_match(local_order: BrokerOrderResult, broker_order: BrokerOrderResult) -> bool:
    return (
        local_order.status == broker_order.status
        and local_order.filled_qty == broker_order.filled_qty
        and local_order.avg_fill_price == broker_order.avg_fill_price
        and local_order.requested_qty == broker_order.requested_qty
    )
