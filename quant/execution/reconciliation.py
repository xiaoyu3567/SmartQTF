import time
from dataclasses import dataclass
from typing import Any, Iterable

from quant.execution.broker import BrokerAdapter
from quant.execution.order_store import SQLiteOrderStore, sanitize_raw_exchange_response
from quant.monitoring import AlertJsonlWriter, HealthAlert, HealthAlertEvaluator
from quant.schemas import PayloadSource, RuntimeHealthSnapshot, RuntimeHealthStatus
from quant.schemas.enums import OrderStatus
from quant.schemas.execution import (
    BrokerOrderResult,
    OrderStoreReconciliationRunRecord,
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


@dataclass(frozen=True)
class ReconciliationRunOutcome:
    report: ReconciliationReport
    stored_run: OrderStoreReconciliationRunRecord
    alerts: list[HealthAlert]


def reconcile_orders(
    broker: BrokerAdapter,
    local_orders: Iterable[BrokerOrderResult],
    symbols: Iterable[str] | None = None,
    include_history: bool = True,
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
                local_avg_fill_price=local_order.avg_fill_price,
                broker_avg_fill_price=broker_order.avg_fill_price,
                trace=broker_order.trace or local_order.trace,
            )
        )

    for broker_order in _list_broker_orders(broker, symbols, include_history=include_history):
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
                broker_avg_fill_price=broker_order.avg_fill_price,
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


def run_reconciliation_report(
    broker: BrokerAdapter,
    order_store: SQLiteOrderStore,
    *,
    run_id: str,
    symbols: Iterable[str] | None = None,
    observed_at: int | None = None,
    alert_writer: AlertJsonlWriter | None = None,
    metadata: dict[str, Any] | None = None,
    raw_exchange_response: Any | None = None,
    source: PayloadSource = PayloadSource.LIVE,
) -> ReconciliationRunOutcome:
    """Reconcile broker truth against the order store, persist the run, and emit alerts."""

    started_at = observed_at if observed_at is not None else int(time.time())
    local_orders = order_store.list_order_results()
    report = reconcile_orders(
        broker,
        local_orders,
        symbols=symbols,
        include_history=True,
    )
    finished_at = int(time.time())
    if finished_at < started_at:
        finished_at = started_at
    safe_metadata = sanitize_raw_exchange_response(metadata or {})
    stored_run = order_store.record_reconciliation_run(
        run_id,
        report,
        started_at=started_at,
        finished_at=finished_at,
        raw_exchange_response=raw_exchange_response,
        metadata={
            **safe_metadata,
            "broker_called": False,
            "live_orders_sent": False,
            "anomaly_count": _report_anomaly_count(report),
        },
    )
    alerts = _emit_reconciliation_alerts(
        stored_run.report,
        run_id=run_id,
        observed_at=finished_at,
        source=source,
        alert_writer=alert_writer,
        metadata=safe_metadata,
    )
    return ReconciliationRunOutcome(
        report=stored_run.report,
        stored_run=stored_run,
        alerts=alerts,
    )


def _get_broker_order(
    broker: BrokerAdapter,
    client_order_id: str,
) -> BrokerOrderResult | None:
    try:
        return broker.get_order(client_order_id)
    except KeyError:
        return None


def _list_broker_orders(
    broker: BrokerAdapter,
    symbols: Iterable[str] | None,
    *,
    include_history: bool,
) -> list[BrokerOrderResult]:
    orders: dict[str, BrokerOrderResult] = {}
    for order in _list_broker_open_orders(broker, symbols):
        orders[order.client_order_id] = order

    if include_history:
        for order in _list_broker_history_orders(broker, symbols):
            orders[order.client_order_id] = order
    return list(orders.values())


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


def _list_broker_history_orders(
    broker: BrokerAdapter,
    symbols: Iterable[str] | None,
) -> list[BrokerOrderResult]:
    list_history = getattr(broker, "list_order_history", None) or getattr(
        broker,
        "list_history_orders",
        None,
    )
    if list_history is None:
        return []
    if symbols is None:
        return list(list_history())

    orders: list[BrokerOrderResult] = []
    for symbol in symbols:
        orders.extend(list_history(symbol))
    return orders


def _orders_match(local_order: BrokerOrderResult, broker_order: BrokerOrderResult) -> bool:
    return (
        local_order.status == broker_order.status
        and local_order.filled_qty == broker_order.filled_qty
        and local_order.avg_fill_price == broker_order.avg_fill_price
        and local_order.requested_qty == broker_order.requested_qty
    )


def _emit_reconciliation_alerts(
    report: ReconciliationReport,
    *,
    run_id: str,
    observed_at: int,
    source: PayloadSource,
    alert_writer: AlertJsonlWriter | None,
    metadata: dict[str, Any],
) -> list[HealthAlert]:
    anomaly_count = _report_anomaly_count(report)
    if anomaly_count <= 0:
        return []

    snapshot = RuntimeHealthSnapshot(
        run_id=run_id,
        source=source,
        observed_at=observed_at,
        status=RuntimeHealthStatus.DEGRADED,
        broker_reconciliation_anomalies=anomaly_count,
        alerts=["broker_reconciliation_anomaly"],
    )
    return HealthAlertEvaluator(alert_writer=alert_writer).evaluate(
        snapshot,
        metadata={
            **metadata,
            "broker_name": report.broker_name,
            "checked_count": report.checked_count,
            "matched_count": report.matched_count,
            "drift_count": report.drift_count,
            "missing_local_count": report.missing_local_count,
            "missing_broker_count": report.missing_broker_count,
            "broker_called": False,
            "live_orders_sent": False,
        },
    )


def _report_anomaly_count(report: ReconciliationReport) -> int:
    return report.drift_count + report.missing_local_count + report.missing_broker_count
