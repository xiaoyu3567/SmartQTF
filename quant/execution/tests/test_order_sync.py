import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.broker import BrokerAdapter
from quant.execution.order_sync import DualChannelOrderSynchronizer
from quant.execution.state_machine import ExecutionState
from quant.schemas.enums import OrderStatus, TradeSide
from quant.schemas.execution import BrokerOrderResult


class SyncBroker(BrokerAdapter):
    def __init__(self, orders):
        self.orders = {order.client_order_id: order for order in orders}

    @property
    def name(self):
        return "sync"

    def place_order(self, request):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError

    def get_order(self, client_order_id):
        return self.orders[client_order_id]

    def list_open_orders(self, symbol=None):
        return []


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


def test_websocket_update_drives_order_state_machine():
    pending = make_order("client-1", OrderStatus.ACCEPTED)
    filled = make_order("client-1", OrderStatus.FILLED, filled_qty=1.0, price=100.0)
    sync = DualChannelOrderSynchronizer(broker=SyncBroker([]))

    first = sync.apply_websocket_update(pending)
    second = sync.apply_websocket_update(filled)

    assert first.status == OrderStatus.ACCEPTED
    assert first.state == ExecutionState.ORDER_PENDING
    assert second.status == OrderStatus.FILLED
    assert second.state == ExecutionState.POSITION_OPEN
    assert sync.local_orders["client-1"].filled_qty == 1.0


def test_rest_poll_fills_gap_when_websocket_update_is_missing():
    local = make_order("client-1", OrderStatus.ACCEPTED)
    broker_order = make_order("client-1", OrderStatus.PARTIAL, filled_qty=0.4, price=100.0)
    sync = DualChannelOrderSynchronizer(broker=SyncBroker([broker_order]))
    sync.track(local)

    results = sync.poll_rest()

    assert len(results) == 1
    assert results[0].source == "rest"
    assert results[0].previous_status == OrderStatus.ACCEPTED
    assert results[0].status == OrderStatus.PARTIAL
    assert results[0].state == ExecutionState.POSITION_OPEN
    assert sync.local_orders["client-1"].filled_qty == 0.4


def test_rest_unknown_marks_missing_broker_order_without_repeating_order():
    local = make_order("client-1", OrderStatus.ACCEPTED)
    sync = DualChannelOrderSynchronizer(broker=SyncBroker([]))
    sync.track(local)

    results = sync.poll_rest(["client-1"])

    assert results[0].status == OrderStatus.UNKNOWN
    assert results[0].changed is True
    assert len(sync.snapshot()) == 1
    assert sync.local_orders["client-1"].status == OrderStatus.UNKNOWN


def test_stale_websocket_update_does_not_rewind_rest_truth():
    filled = make_order("client-1", OrderStatus.FILLED, filled_qty=1.0, price=100.0)
    stale = make_order("client-1", OrderStatus.ACCEPTED)
    sync = DualChannelOrderSynchronizer(broker=SyncBroker([filled]))

    sync.apply_websocket_update(filled)
    result = sync.apply_websocket_update(stale)

    assert result.changed is False
    assert result.status == OrderStatus.FILLED
    assert sync.local_orders["client-1"].filled_qty == 1.0
