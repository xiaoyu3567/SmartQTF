import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.broker import BrokerAdapter
from quant.execution.recovery import recover_timed_out_order
from quant.schemas.enums import OrderStatus, TimeoutRecoveryAction, TradeSide
from quant.schemas.execution import BrokerOrderResult, TimeoutRecoveryDecision


class TimeoutBroker(BrokerAdapter):
    def __init__(self, orders=None, error=None):
        self.orders = {order.client_order_id: order for order in orders or []}
        self.error = error

    @property
    def name(self):
        return "timeout-broker"

    def place_order(self, request):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError

    def get_order(self, client_order_id):
        if self.error is not None:
            raise self.error
        return self.orders[client_order_id]

    def list_open_orders(self, symbol=None):
        return []


def make_order(client_order_id, status=OrderStatus.UNKNOWN, filled_qty=0.0):
    return BrokerOrderResult(
        client_order_id=client_order_id,
        broker_order_id=f"broker-{client_order_id}",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        status=status,
        requested_qty=1.0,
        filled_qty=filled_qty,
        avg_fill_price=100.0 if filled_qty else None,
    )


def test_timeout_recovery_updates_from_broker_truth():
    local = make_order("client-1")
    broker_order = make_order("client-1", OrderStatus.FILLED, filled_qty=1.0)
    broker = TimeoutBroker([broker_order])

    decision = recover_timed_out_order(broker, local)

    assert decision.action == TimeoutRecoveryAction.UPDATE_LOCAL_FROM_BROKER
    assert decision.status == OrderStatus.FILLED
    assert decision.recovered_order == broker_order
    assert decision.reason == "broker_order_found_after_timeout"


def test_timeout_recovery_marks_unknown_when_broker_has_no_order():
    local = make_order("client-1")
    broker = TimeoutBroker([])

    decision = recover_timed_out_order(broker, local)

    assert decision.action == TimeoutRecoveryAction.MARK_UNKNOWN
    assert decision.status == OrderStatus.UNKNOWN
    assert decision.recovered_order is None
    assert decision.reason == "broker_order_missing_after_timeout"


def test_timeout_recovery_retries_later_when_broker_query_fails():
    local = make_order("client-1")
    broker = TimeoutBroker(error=TimeoutError("query timed out"))

    decision = recover_timed_out_order(broker, local)

    assert decision.action == TimeoutRecoveryAction.RETRY_RECOVERY_LATER
    assert decision.status == OrderStatus.UNKNOWN
    assert decision.error == "query timed out"


def test_timeout_recovery_decision_rejects_unknown_action():
    try:
        TimeoutRecoveryDecision(
            client_order_id="client-1",
            action="retry_place_order",
            reason="unsafe_retry",
        )
    except ValueError as exc:
        assert "retry_place_order" in str(exc)
    else:
        raise AssertionError("expected invalid timeout action to raise ValueError")
