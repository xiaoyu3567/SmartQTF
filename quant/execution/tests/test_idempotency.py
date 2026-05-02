import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.broker import BrokerAdapter
from quant.execution.idempotency import (
    JsonIdempotencyRegistry,
    fingerprint_order_request,
    submit_order_idempotently,
)
from quant.schemas.enums import OrderKind, OrderStatus, TimeInForce, TradeSide
from quant.schemas.execution import BrokerOrderRequest, BrokerOrderResult


class IdempotencyBroker(BrokerAdapter):
    def __init__(self, orders=None, timeout_first_place=False, lookup_error=None):
        self.orders = {order.client_order_id: order for order in orders or []}
        self.timeout_first_place = timeout_first_place
        self.lookup_error = lookup_error
        self.place_requests = []
        self.lookup_requests = []

    @property
    def name(self):
        return "idempotency-broker"

    def place_order(self, request):
        self.place_requests.append(request)
        result = BrokerOrderResult(
            client_order_id=request.client_order_id,
            broker_order_id=f"broker-{len(self.place_requests)}",
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.ACCEPTED,
            requested_qty=request.quantity,
        )
        self.orders[request.client_order_id] = result
        if self.timeout_first_place and len(self.place_requests) == 1:
            raise TimeoutError("submit response timed out")
        return result

    def cancel_order(self, client_order_id):
        raise NotImplementedError

    def replace_order(self, request):
        raise NotImplementedError

    def get_order(self, client_order_id):
        self.lookup_requests.append(client_order_id)
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.orders[client_order_id]

    def list_open_orders(self, symbol=None):
        return list(self.orders.values())


def make_request(client_order_id="client-1", quantity=1.0):
    return BrokerOrderRequest(
        client_order_id=client_order_id,
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.LIMIT,
        quantity=quantity,
        limit_price=100.0,
        time_in_force=TimeInForce.GTC,
    )


def status_value(status):
    return status.value if hasattr(status, "value") else status


def test_json_idempotency_registry_persists_submit_intent(tmp_path):
    path = tmp_path / "idempotency.json"
    request = make_request("persisted-client-1")
    registry = JsonIdempotencyRegistry(path)

    record = registry.register_submit_intent(request, now=1710000000)
    reloaded = JsonIdempotencyRegistry(path)
    persisted = reloaded.get("persisted-client-1")

    assert record.submit_intent_count == 1
    assert persisted is not None
    assert persisted.submit_intent_count == 1
    assert persisted.request_fingerprint == fingerprint_order_request(request)
    assert persisted.request_payload["client_order_id"] == "persisted-client-1"


def test_idempotent_submit_imports_existing_broker_order_before_submit(tmp_path):
    request = make_request("already-on-exchange")
    broker_order = BrokerOrderResult(
        client_order_id=request.client_order_id,
        broker_order_id="broker-existing",
        symbol=request.symbol,
        side=request.side,
        status=OrderStatus.ACCEPTED,
        requested_qty=request.quantity,
    )
    broker = IdempotencyBroker(orders=[broker_order])
    registry = JsonIdempotencyRegistry(tmp_path / "idempotency.json")

    result = submit_order_idempotently(broker, request, registry, now=1710000000)
    record = registry.get(request.client_order_id)

    assert result.action == "import_existing_broker_order"
    assert result.idempotent_replay is True
    assert result.broker_place_called is False
    assert result.broker_lookup_called is True
    assert result.result.broker_order_id == "broker-existing"
    assert len(broker.place_requests) == 0
    assert record is not None
    assert record.submit_intent_count == 0
    assert status_value(record.status) == OrderStatus.ACCEPTED.value


def test_timeout_retries_reuse_client_order_id_and_only_one_submit_intent(tmp_path):
    request = make_request("timeout-client-1")
    broker = IdempotencyBroker(timeout_first_place=True)
    registry = JsonIdempotencyRegistry(tmp_path / "idempotency.json")

    first = submit_order_idempotently(broker, request, registry, now=1710000000)
    retries = [
        submit_order_idempotently(broker, request, registry, now=1710000001 + index)
        for index in range(3)
    ]
    record = registry.get(request.client_order_id)

    assert first.action == "submit_result_unknown"
    assert status_value(first.result.status) == OrderStatus.UNKNOWN.value
    assert retries[0].action == "recover_broker_result"
    assert [result.result.client_order_id for result in retries] == [
        "timeout-client-1",
        "timeout-client-1",
        "timeout-client-1",
    ]
    assert retries[1].action == "replay_local_result"
    assert retries[2].action == "replay_local_result"
    assert len(broker.place_requests) == 1
    assert [request.client_order_id for request in broker.place_requests] == ["timeout-client-1"]
    assert record is not None
    assert record.submit_intent_count == 1
    assert status_value(record.status) == OrderStatus.ACCEPTED.value
    assert record.broker_order_id == "broker-1"


def test_existing_unknown_submit_intent_is_not_resubmitted_when_broker_lookup_misses(tmp_path):
    request = make_request("unknown-client-1")
    broker = IdempotencyBroker()
    registry = JsonIdempotencyRegistry(tmp_path / "idempotency.json")
    registry.register_submit_intent(request, now=1710000000)

    result = submit_order_idempotently(broker, request, registry, now=1710000001)
    record = registry.get(request.client_order_id)

    assert result.action == "hold_unknown_without_resubmit"
    assert result.idempotent_replay is True
    assert result.broker_place_called is False
    assert len(broker.place_requests) == 0
    assert record is not None
    assert record.submit_intent_count == 1
    assert status_value(record.status) == OrderStatus.UNKNOWN.value


def test_registry_rejects_same_client_order_id_with_different_payload(tmp_path):
    registry = JsonIdempotencyRegistry(tmp_path / "idempotency.json")
    request = make_request("drift-client-1", quantity=1.0)
    changed_request = make_request("drift-client-1", quantity=2.0)

    registry.register_submit_intent(request, now=1710000000)

    try:
        registry.register_submit_intent(changed_request, now=1710000001)
    except ValueError as exc:
        assert "client_order_id reuse with different order payload" in str(exc)
    else:
        raise AssertionError("expected payload drift to raise ValueError")
