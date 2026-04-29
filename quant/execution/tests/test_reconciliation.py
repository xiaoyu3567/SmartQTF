import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.broker import BrokerAdapter
from quant.execution.reconciliation import reconcile_orders
from quant.schemas.enums import OrderStatus, TradeSide
from quant.schemas.execution import BrokerOrderResult


class StaticBroker(BrokerAdapter):
    def __init__(self, orders):
        self.orders = {order.client_order_id: order for order in orders}

    @property
    def name(self):
        return "static"

    def place_order(self, request):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError

    def get_order(self, client_order_id):
        return self.orders[client_order_id]

    def list_open_orders(self, symbol=None):
        orders = [
            order
            for order in self.orders.values()
            if order.status in {OrderStatus.CREATED, OrderStatus.PENDING, OrderStatus.ACCEPTED, OrderStatus.PARTIAL}
        ]
        if symbol is not None:
            return [order for order in orders if order.symbol == symbol]
        return orders


def make_order(client_order_id, status, filled_qty=0.0, price=None):
    return BrokerOrderResult(
        client_order_id=client_order_id,
        broker_order_id=f"broker-{client_order_id}",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        status=status,
        requested_qty=1.0,
        filled_qty=filled_qty,
        avg_fill_price=price,
    )


def test_reconciliation_reports_matching_order_without_action():
    local = make_order("client-1", OrderStatus.ACCEPTED)
    broker = StaticBroker([local])

    report = reconcile_orders(broker, [local])

    assert report.checked_count == 1
    assert report.matched_count == 1
    assert report.drift_count == 0
    assert report.items == []


def test_reconciliation_detects_broker_truth_drift():
    local = make_order("client-1", OrderStatus.ACCEPTED)
    broker_order = make_order("client-1", OrderStatus.FILLED, filled_qty=1.0, price=100.0)
    broker = StaticBroker([broker_order])

    report = reconcile_orders(broker, [local])

    assert report.checked_count == 1
    assert report.matched_count == 0
    assert report.drift_count == 1
    assert report.items[0].action == "update_local_from_broker"
    assert report.items[0].local_status == OrderStatus.ACCEPTED
    assert report.items[0].broker_status == OrderStatus.FILLED
    assert report.items[0].broker_filled_qty == 1.0


def test_reconciliation_detects_local_order_missing_at_broker():
    local = make_order("client-1", OrderStatus.ACCEPTED)
    broker = StaticBroker([])

    report = reconcile_orders(broker, [local])

    assert report.missing_broker_count == 1
    assert report.items[0].action == "mark_unknown"
    assert report.items[0].reason == "broker_order_missing"


def test_reconciliation_detects_open_broker_order_missing_locally():
    broker_order = make_order("client-2", OrderStatus.ACCEPTED)
    broker = StaticBroker([broker_order])

    report = reconcile_orders(broker, [])

    assert report.checked_count == 0
    assert report.missing_local_count == 1
    assert report.items[0].action == "import_broker_open_order"
    assert report.items[0].broker_status == OrderStatus.ACCEPTED
