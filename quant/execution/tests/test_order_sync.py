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
        self.cancel_requests = []

    @property
    def name(self):
        return "sync"

    def place_order(self, request):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        self.cancel_requests.append(client_order_id)
        return self.orders[client_order_id]

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


def test_rest_poll_syncs_manual_cancel_without_calling_cancel_again():
    local = make_order("client-1", OrderStatus.ACCEPTED)
    cancelled = make_order("client-1", OrderStatus.CANCELLED)
    broker = SyncBroker([cancelled])
    sync = DualChannelOrderSynchronizer(broker=broker)
    sync.track(local)

    results = sync.poll_rest(["client-1"])

    assert results[0].source == "rest"
    assert results[0].previous_status == OrderStatus.ACCEPTED
    assert results[0].status == OrderStatus.CANCELLED
    assert results[0].changed is True
    assert broker.cancel_requests == []
    assert sync.local_orders["client-1"].status == OrderStatus.CANCELLED


def test_stale_websocket_update_does_not_rewind_rest_truth():
    filled = make_order("client-1", OrderStatus.FILLED, filled_qty=1.0, price=100.0)
    stale = make_order("client-1", OrderStatus.ACCEPTED)
    sync = DualChannelOrderSynchronizer(broker=SyncBroker([filled]))

    sync.track(make_order("client-1", OrderStatus.ACCEPTED))
    sync.poll_rest(["client-1"])
    result = sync.apply_websocket_update(stale)

    assert result.changed is False
    assert result.status == OrderStatus.FILLED
    assert sync.local_orders["client-1"].filled_qty == 1.0


def test_websocket_loop_consumes_bounded_adapter_updates():
    class Stream:
        def __init__(self):
            self.connected = False

        def connect(self):
            self.connected = True

        def iter_updates(self):
            yield make_order("client-1", OrderStatus.ACCEPTED)
            yield make_order("client-1", OrderStatus.PARTIAL, filled_qty=0.5, price=100.0)

    stream = Stream()
    sync = DualChannelOrderSynchronizer(broker=SyncBroker([]))

    results = sync.run_websocket_loop(stream, now=10.0, max_updates=2)

    assert stream.connected is True
    assert [result.status for result in results] == [
        OrderStatus.ACCEPTED,
        OrderStatus.PARTIAL,
    ]
    assert sync.websocket_connected is True
    assert sync.polling_fallback_active is False
    assert sync.last_websocket_event_at == 10.0


def test_websocket_loop_keeps_processed_updates_when_stream_disconnects():
    class DisconnectingStream:
        def connect(self):
            return None

        def iter_updates(self):
            yield make_order("client-1", OrderStatus.ACCEPTED)
            raise RuntimeError("ws disconnected")

    sync = DualChannelOrderSynchronizer(
        broker=SyncBroker([]),
        reconnect_backoff_seconds=3.0,
    )

    results = sync.run_websocket_loop(DisconnectingStream(), now=20.0)

    assert len(results) == 1
    assert results[0].status == OrderStatus.ACCEPTED
    assert sync.polling_fallback_active is True
    assert sync.websocket_disconnect_reason == "ws disconnected"
    assert sync.next_reconnect_at == 23.0


def test_websocket_disconnect_activates_polling_fallback_with_backoff():
    local = make_order("client-1", OrderStatus.ACCEPTED)
    filled = make_order("client-1", OrderStatus.FILLED, filled_qty=1.0, price=100.0)
    sync = DualChannelOrderSynchronizer(
        broker=SyncBroker([filled]),
        poll_interval_seconds=5.0,
        websocket_stale_after_seconds=10.0,
        reconnect_backoff_seconds=2.0,
        max_reconnect_backoff_seconds=8.0,
    )
    sync.track(local)
    sync.apply_websocket_update(local, observed_at=0.0)

    results = sync.poll_fallback_if_needed(now=11.0, client_order_ids=["client-1"])

    assert sync.polling_fallback_active is True
    assert sync.websocket_disconnect_reason == "websocket_stale"
    assert sync.next_reconnect_at == 13.0
    assert sync.last_poll_at == 11.0
    assert sync.next_poll_at == 16.0
    assert results[0].source == "rest"
    assert results[0].status == OrderStatus.FILLED
    assert sync.local_orders["client-1"].filled_qty == 1.0


def test_rest_polling_rate_limit_prevents_tight_poll_loop():
    local = make_order("client-1", OrderStatus.ACCEPTED)
    broker_order = make_order("client-1", OrderStatus.PARTIAL, filled_qty=0.5, price=100.0)
    sync = DualChannelOrderSynchronizer(
        broker=SyncBroker([broker_order]),
        poll_interval_seconds=5.0,
    )
    sync.track(local)

    first = sync.poll_rest(["client-1"], now=10.0)
    second = sync.poll_rest(["client-1"], now=12.0)
    skipped_reason = sync.last_poll_skipped_reason
    third = sync.poll_rest(["client-1"], now=15.0)

    assert len(first) == 1
    assert second == []
    assert skipped_reason == "rest_poll_rate_limited"
    assert len(third) == 1
