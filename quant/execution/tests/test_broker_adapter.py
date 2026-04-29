import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.broker import BrokerAdapter
from quant.schemas.enums import OrderKind, OrderStatus, TimeInForce, TradeSide
from quant.schemas.execution import (
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerReplaceOrderRequest,
    InstrumentOrderRules,
    LiveOrderGateDecision,
)


class InMemoryBroker(BrokerAdapter):
    def __init__(self):
        self.orders = {}

    @property
    def name(self):
        return "memory"

    def place_order(self, request):
        result = BrokerOrderResult(
            client_order_id=request.client_order_id,
            broker_order_id=f"broker-{len(self.orders) + 1}",
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.ACCEPTED,
            requested_qty=request.quantity,
        )
        self.orders[result.client_order_id] = result
        return result

    def cancel_order(self, client_order_id):
        existing = self.orders[client_order_id]
        result = BrokerOrderResult(
            client_order_id=existing.client_order_id,
            broker_order_id=existing.broker_order_id,
            symbol=existing.symbol,
            side=existing.side,
            status=OrderStatus.CANCELLED,
            requested_qty=existing.requested_qty,
            filled_qty=existing.filled_qty,
        )
        self.orders[client_order_id] = result
        return result

    def replace_order(self, request):
        self.cancel_order(request.original_client_order_id)
        result = BrokerOrderResult(
            client_order_id=request.replacement_client_order_id,
            broker_order_id=f"broker-{len(self.orders) + 1}",
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.ACCEPTED,
            requested_qty=request.quantity,
        )
        self.orders[result.client_order_id] = result
        return result

    def get_order(self, client_order_id):
        return self.orders[client_order_id]

    def list_open_orders(self, symbol=None):
        orders = [
            order
            for order in self.orders.values()
            if order.status in {OrderStatus.CREATED, OrderStatus.PENDING, OrderStatus.ACCEPTED}
        ]
        if symbol is not None:
            return [order for order in orders if order.symbol == symbol]
        return orders


def test_broker_adapter_contract_places_queries_and_cancels_order():
    broker = InMemoryBroker()
    request = BrokerOrderRequest(
        client_order_id="order-1",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.MARKET,
        quantity=0.5,
        time_in_force=TimeInForce.GTC,
    )

    placed = broker.place_order(request)
    queried = broker.get_order("order-1")
    open_orders = broker.list_open_orders("BTCUSDT")
    cancelled = broker.cancel_order("order-1")

    assert broker.name == "memory"
    assert placed.status == OrderStatus.ACCEPTED
    assert queried == placed
    assert open_orders == [placed]
    assert cancelled.status == OrderStatus.CANCELLED
    assert broker.list_open_orders("BTCUSDT") == []


def test_broker_adapter_contract_replaces_order_with_new_client_order_id():
    broker = InMemoryBroker()
    placed = broker.place_order(
        BrokerOrderRequest(
            client_order_id="order-1",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.LIMIT,
            quantity=0.5,
            limit_price=100.0,
        )
    )

    replacement = broker.replace_order(
        BrokerReplaceOrderRequest(
            original_client_order_id="order-1",
            replacement_client_order_id="order-1-r1",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.LIMIT,
            quantity=0.4,
            limit_price=99.5,
        )
    )

    assert placed.status == OrderStatus.ACCEPTED
    assert broker.get_order("order-1").status == OrderStatus.CANCELLED
    assert replacement.client_order_id == "order-1-r1"
    assert replacement.status == OrderStatus.ACCEPTED
    assert replacement.requested_qty == 0.4
    assert broker.list_open_orders("BTCUSDT") == [replacement]


def test_broker_order_request_requires_positive_quantity():
    try:
        BrokerOrderRequest(
            client_order_id="bad-order",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.0,
        )
    except ValueError as exc:
        assert "quantity must be greater than 0.0" in str(exc)
    else:
        raise AssertionError("expected invalid quantity to raise ValueError")


def test_limit_order_request_requires_limit_price():
    try:
        BrokerOrderRequest(
            client_order_id="bad-limit",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.LIMIT,
            quantity=1.0,
        )
    except ValueError as exc:
        assert "limit orders require limit_price" in str(exc)
    else:
        raise AssertionError("expected missing limit_price to raise ValueError")


def test_replace_order_request_requires_distinct_replacement_client_order_id():
    try:
        BrokerReplaceOrderRequest(
            original_client_order_id="same",
            replacement_client_order_id="same",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=1.0,
        )
    except ValueError as exc:
        assert "replacement_client_order_id must differ" in str(exc)
    else:
        raise AssertionError("expected duplicate replacement client order id to raise ValueError")


def test_limit_replace_order_request_requires_limit_price():
    try:
        BrokerReplaceOrderRequest(
            original_client_order_id="order-1",
            replacement_client_order_id="order-1-r1",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.LIMIT,
            quantity=1.0,
        )
    except ValueError as exc:
        assert "limit orders require limit_price" in str(exc)
    else:
        raise AssertionError("expected missing replacement limit_price to raise ValueError")


def test_broker_order_result_rejects_overfill():
    try:
        BrokerOrderResult(
            client_order_id="overfill",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            status=OrderStatus.FILLED,
            requested_qty=1.0,
            filled_qty=1.1,
            avg_fill_price=100.0,
        )
    except ValueError as exc:
        assert "filled_qty cannot exceed requested_qty" in str(exc)
    else:
        raise AssertionError("expected overfill to raise ValueError")


def test_live_order_gate_decision_requires_reason_codes():
    try:
        LiveOrderGateDecision(
            approved=False,
            reason_codes=[],
            message="blocked",
            checked_at=1710000000,
        )
    except ValueError as exc:
        assert "reason_codes must not be empty" in str(exc)
    else:
        raise AssertionError("expected empty live gate reason codes to raise ValueError")


def test_live_order_gate_decision_round_trips_replay_context():
    decision = LiveOrderGateDecision(
        approved=True,
        reason_codes=["live_order_gate_approved"],
        message="live order gate approved",
        checked_at=1710000000,
        allow_live_orders=True,
        preflight_artifact_path="logs/production-rehearsals/latest.json",
        preflight_generated_at=1709999900,
        preflight_artifact_age_seconds=100,
        preflight_max_age_seconds=86400,
        kill_switch_active=False,
        metadata={"client_order_id": "order-1"},
    )

    payload = decision.to_payload()

    assert payload["approved"] is True
    assert payload["allow_live_orders"] is True
    assert payload["metadata"]["client_order_id"] == "order-1"


def test_instrument_order_rules_accept_aligned_limit_order():
    rules = InstrumentOrderRules(
        symbol="BTCUSDT",
        quantity_step=0.001,
        min_quantity=0.001,
        price_tick=0.1,
        min_notional=10.0,
    )
    request = BrokerOrderRequest(
        client_order_id="order-1",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.LIMIT,
        quantity=0.125,
        limit_price=100.1,
    )

    assert rules.validate_order_request(request) == []


def test_instrument_order_rules_reject_quantity_and_price_precision():
    rules = InstrumentOrderRules(
        symbol="BTCUSDT",
        quantity_step=0.001,
        min_quantity=0.001,
        price_tick=0.1,
        min_notional=10.0,
    )
    request = BrokerOrderRequest(
        client_order_id="bad-precision",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.LIMIT,
        quantity=0.1255,
        limit_price=100.15,
    )

    violations = rules.validate_order_request(request)

    assert [violation.code for violation in violations] == [
        "quantity_step_mismatch",
        "price_tick_mismatch",
    ]


def test_instrument_order_rules_validate_market_min_notional_with_reference_price():
    rules = InstrumentOrderRules(
        symbol="BTCUSDT",
        quantity_step=0.001,
        min_quantity=0.001,
        min_notional=10.0,
    )
    request = BrokerOrderRequest(
        client_order_id="market-1",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.MARKET,
        quantity=0.002,
    )

    missing_price = rules.validate_order_request(request)
    below_notional = rules.validate_order_request(request, reference_price=4000.0)
    valid = rules.validate_order_request(request, reference_price=6000.0)

    assert [violation.code for violation in missing_price] == ["reference_price_required"]
    assert [violation.code for violation in below_notional] == ["notional_below_minimum"]
    assert valid == []


def test_instrument_order_rules_reject_invalid_rule_bounds():
    try:
        InstrumentOrderRules(
            symbol="BTCUSDT",
            quantity_step=0.001,
            min_quantity=1.0,
            max_quantity=0.5,
        )
    except ValueError as exc:
        assert "max_quantity must be greater than or equal to min_quantity" in str(exc)
    else:
        raise AssertionError("expected invalid rule bounds to raise ValueError")
