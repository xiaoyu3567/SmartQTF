import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.broker import BrokerAdapter
from quant.execution.recovery import classify_timeout_failure, recover_timed_out_order
from quant.schemas.enums import (
    OrderStatus,
    TimeoutFailureKind,
    TimeoutRecoveryAction,
    TradeSide,
)
from quant.schemas.execution import BrokerOrderResult, TimeoutRecoveryDecision


class TimeoutBroker(BrokerAdapter):
    def __init__(self, orders=None, error=None, open_orders=None):
        self.orders = {order.client_order_id: order for order in orders or []}
        self.error = error
        self.open_orders = list(open_orders or [])
        self.place_requests = []
        self.lookup_requests = []
        self.open_order_queries = []

    @property
    def name(self):
        return "timeout-broker"

    def place_order(self, request):
        self.place_requests.append(request)
        raise AssertionError("timeout recovery must not resubmit orders")

    def cancel_order(self, client_order_id):
        raise NotImplementedError

    def get_order(self, client_order_id):
        self.lookup_requests.append(client_order_id)
        if self.error is not None:
            raise self.error
        return self.orders[client_order_id]

    def list_open_orders(self, symbol=None):
        self.open_order_queries.append(symbol)
        return list(self.open_orders)


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
    assert decision.recovery_query_attempted is True
    assert decision.broker_place_called is False
    assert decision.duplicate_order_guard_active is True
    assert broker.place_requests == []


def test_timeout_recovery_marks_unknown_when_broker_has_no_order():
    local = make_order("client-1")
    broker = TimeoutBroker([])

    decision = recover_timed_out_order(broker, local)

    assert decision.action == TimeoutRecoveryAction.MARK_UNKNOWN
    assert decision.status == OrderStatus.UNKNOWN
    assert decision.recovered_order is None
    assert decision.reason == "broker_order_missing_after_timeout"
    assert decision.failure_kind == TimeoutFailureKind.BROKER_ORDER_MISSING
    assert decision.broker_place_called is False
    assert broker.place_requests == []


def test_timeout_recovery_retries_later_when_broker_query_fails():
    local = make_order("client-1")
    broker = TimeoutBroker(error=TimeoutError("query timed out"))

    decision = recover_timed_out_order(broker, local)

    assert decision.action == TimeoutRecoveryAction.RETRY_RECOVERY_LATER
    assert decision.status == OrderStatus.UNKNOWN
    assert decision.error == "query timed out"
    assert decision.failure_kind == TimeoutFailureKind.API_TIMEOUT
    assert decision.broker_place_called is False
    assert broker.place_requests == []


def test_timeout_failure_classifier_splits_api_timeout_and_network_error():
    assert classify_timeout_failure(TimeoutError("request timed out")) == TimeoutFailureKind.API_TIMEOUT
    assert classify_timeout_failure(ConnectionError("network unreachable")) == TimeoutFailureKind.NETWORK_ERROR
    assert classify_timeout_failure("exchange result not yet visible") == TimeoutFailureKind.EXCHANGE_RESPONSE_DELAYED


def test_timeout_recovery_uses_retry_budget_before_unknown_status():
    local = make_order("client-1")
    broker = TimeoutBroker([])

    first = recover_timed_out_order(
        broker,
        local,
        failure=TimeoutError("submit response timed out"),
        recovery_attempt=1,
        max_recovery_attempts=2,
        retry_after_seconds=5,
    )
    second = recover_timed_out_order(
        broker,
        local,
        failure=TimeoutError("submit response timed out"),
        recovery_attempt=2,
        max_recovery_attempts=2,
        retry_after_seconds=5,
    )

    assert first.action == TimeoutRecoveryAction.RETRY_RECOVERY_LATER
    assert first.reason == "broker_order_missing_recovery_budget_remaining"
    assert first.failure_kind == TimeoutFailureKind.EXCHANGE_RESPONSE_DELAYED
    assert first.retry_after_seconds == 5
    assert second.action == TimeoutRecoveryAction.MARK_UNKNOWN
    assert second.reason == "broker_order_missing_after_timeout"
    assert second.status == OrderStatus.UNKNOWN
    assert broker.place_requests == []


def test_timeout_recovery_marks_unknown_when_query_budget_exhausted_after_network_error():
    local = make_order("client-1")
    broker = TimeoutBroker(error=ConnectionError("network unreachable"))

    decision = recover_timed_out_order(
        broker,
        local,
        recovery_attempt=2,
        max_recovery_attempts=2,
    )

    assert decision.action == TimeoutRecoveryAction.MARK_UNKNOWN
    assert decision.reason == "broker_query_failed_recovery_budget_exhausted"
    assert decision.failure_kind == TimeoutFailureKind.NETWORK_ERROR
    assert decision.status == OrderStatus.UNKNOWN
    assert broker.place_requests == []


def test_timeout_recovery_can_find_delayed_exchange_order_from_open_orders():
    local = make_order("client-1")
    broker_order = make_order("client-1", OrderStatus.ACCEPTED)
    broker = TimeoutBroker([], open_orders=[broker_order])

    decision = recover_timed_out_order(
        broker,
        local,
        failure="exchange result delayed",
        max_recovery_attempts=2,
    )

    assert decision.action == TimeoutRecoveryAction.UPDATE_LOCAL_FROM_BROKER
    assert decision.reason == "broker_open_order_found_after_timeout"
    assert decision.status == OrderStatus.ACCEPTED
    assert decision.failure_kind == TimeoutFailureKind.EXCHANGE_RESPONSE_DELAYED
    assert decision.recovered_order == broker_order
    assert broker.open_order_queries == ["BTCUSDT"]
    assert broker.place_requests == []


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
