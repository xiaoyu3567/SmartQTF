import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from adapters.exchange.okx import OKXAdapter, OKXAdapterError
from layers.execution.live import OKXBrokerAdapter
from quant.schemas.enums import ExchangeErrorCategory, OrderKind, OrderStatus, TradeSide
from quant.schemas.execution import (
    BrokerOrderRequest,
    BrokerProtectiveOrderRequest,
    BrokerReplaceOrderRequest,
    InstrumentOrderRules,
    LiveOrderGateDecision,
)


class FakeOKXRestAdapter:
    def __init__(self):
        self.placed = []
        self.protective = []
        self.cancelled = []
        self.rule_requests = []

    def place_order(
        self,
        *,
        symbol,
        side,
        size,
        type,
        client_order_id=None,
        price=None,
        td_mode="cash",
        target_currency=None,
        reduce_only=False,
    ):
        self.placed.append(
            {
                "symbol": symbol,
                "side": side,
                "size": size,
                "type": type,
                "client_order_id": client_order_id,
                "price": price,
                "td_mode": td_mode,
                "target_currency": target_currency,
                "reduce_only": reduce_only,
            }
        )
        return {
            "success": True,
            "data": [
                {
                    "instId": symbol,
                    "clOrdId": client_order_id,
                    "ordId": f"okx-{len(self.placed)}",
                    "side": side,
                    "sCode": "0",
                    "state": "live",
                    "sz": str(size),
                }
            ],
        }

    def place_protective_order(
        self,
        *,
        symbol,
        side,
        size,
        client_order_id=None,
        stop_loss_price=None,
        take_profit_price=None,
        td_mode="cash",
        target_currency=None,
        reduce_only=True,
    ):
        self.protective.append(
            {
                "symbol": symbol,
                "side": side,
                "size": size,
                "client_order_id": client_order_id,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
                "td_mode": td_mode,
                "target_currency": target_currency,
                "reduce_only": reduce_only,
            }
        )
        return {
            "success": True,
            "data": [
                {
                    "instId": symbol,
                    "algoClOrdId": client_order_id,
                    "algoId": f"okx-algo-{len(self.protective)}",
                    "sCode": "0",
                }
            ],
        }

    def cancel_order(self, client_order_id):
        self.cancelled.append(client_order_id)
        return {
            "success": True,
            "data": [
                {
                    "instId": "BTC-USDT",
                    "clOrdId": client_order_id,
                    "ordId": "okx-cancelled",
                    "side": "buy",
                    "sCode": "0",
                    "state": "canceled",
                    "sz": "0.1",
                }
            ],
        }

    def get_order(self, client_order_id, symbol=None):
        return {
            "success": True,
            "data": [
                {
                    "instId": symbol or "BTC-USDT",
                    "clOrdId": client_order_id,
                    "ordId": "okx-queried",
                    "side": "buy",
                    "state": "partially_filled",
                    "sz": "0.1",
                    "accFillSz": "0.04",
                    "avgPx": "50100",
                }
            ],
        }

    def list_open_orders(self, symbol=None):
        return {
            "success": True,
            "data": [
                {
                    "instId": symbol or "BTC-USDT",
                    "clOrdId": "open-1",
                    "ordId": "okx-open-1",
                    "side": "buy",
                    "state": "live",
                    "sz": "0.2",
                }
            ],
        }

    def get_instrument_rules(self, symbol):
        self.rule_requests.append(symbol)
        return InstrumentOrderRules(
            symbol=symbol,
            quantity_step=0.01,
            min_quantity=0.01,
            min_notional=10.0,
        )


def _live_gate(approved=True):
    return LiveOrderGateDecision(
        approved=approved,
        reason_codes=["live_order_gate_approved"] if approved else ["allow_live_orders_disabled"],
        message="live order gate approved" if approved else "live order gate rejected order",
        checked_at=1710000000,
        allow_live_orders=approved,
    )


def test_okx_broker_adapter_places_typed_limit_order():
    rest = FakeOKXRestAdapter()
    broker = OKXBrokerAdapter(adapter=rest, td_mode="cash", target_currency="base_ccy")

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-1",
            symbol="BTC-USDT",
            side=TradeSide.BUY,
            order_type=OrderKind.LIMIT,
            quantity=0.1,
            limit_price=50000.0,
        )
    )

    assert broker.name == "okx"
    assert result.status == OrderStatus.ACCEPTED
    assert result.client_order_id == "client-1"
    assert result.broker_order_id == "okx-1"
    assert rest.placed == [
        {
            "symbol": "BTC-USDT",
            "side": "buy",
            "size": 0.1,
            "type": "limit",
            "client_order_id": "client-1",
            "price": 50000.0,
            "td_mode": "cash",
            "target_currency": "base_ccy",
            "reduce_only": False,
        }
    ]


def test_okx_broker_adapter_rejects_order_rule_violations_before_rest_call():
    rest = FakeOKXRestAdapter()
    broker = OKXBrokerAdapter(
        adapter=rest,
        instrument_rules={
            "BTC-USDT": InstrumentOrderRules(
                symbol="BTC-USDT",
                quantity_step=0.01,
                min_quantity=0.01,
                min_notional=10.0,
            )
        },
        reference_prices={"BTC-USDT": 50000.0},
    )

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="too-small",
            symbol="BTC-USDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.001,
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.rejection_code == "quantity_below_minimum"
    assert rest.placed == []


def test_okx_broker_adapter_loads_instrument_rules_before_order():
    rest = FakeOKXRestAdapter()
    broker = OKXBrokerAdapter(adapter=rest, reference_prices={"BTC-USDT": 50000.0})

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="too-small",
            symbol="BTC-USDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.001,
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.rejection_code == "quantity_below_minimum"
    assert rest.rule_requests == ["BTC-USDT"]
    assert rest.placed == []


def test_okx_broker_adapter_queries_lists_cancels_and_replaces_orders():
    rest = FakeOKXRestAdapter()
    broker = OKXBrokerAdapter(adapter=rest)
    broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-1",
            symbol="BTC-USDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.1,
        )
    )

    queried = broker.get_order("client-1")
    open_orders = broker.list_open_orders("BTC-USDT")
    cancelled = broker.cancel_order("client-1")
    replacement = broker.replace_order(
        BrokerReplaceOrderRequest(
            original_client_order_id="client-1",
            replacement_client_order_id="client-1-r1",
            symbol="BTC-USDT",
            side=TradeSide.BUY,
            order_type=OrderKind.LIMIT,
            quantity=0.08,
            limit_price=49900.0,
        )
    )

    assert queried.status == OrderStatus.PARTIAL
    assert queried.filled_qty == 0.04
    assert queried.avg_fill_price == 50100.0
    assert open_orders[0].client_order_id == "open-1"
    assert open_orders[0].status == OrderStatus.ACCEPTED
    assert cancelled.status == OrderStatus.CANCELLED
    assert replacement.client_order_id == "client-1-r1"
    assert replacement.status == OrderStatus.ACCEPTED
    assert rest.cancelled == ["client-1", "client-1"]


def test_okx_broker_adapter_validates_replacement_before_cancelling_original():
    rest = FakeOKXRestAdapter()
    broker = OKXBrokerAdapter(
        adapter=rest,
        instrument_rules={
            "BTC-USDT": InstrumentOrderRules(
                symbol="BTC-USDT",
                quantity_step=0.01,
                min_quantity=0.01,
                min_notional=10.0,
            )
        },
        reference_prices={"BTC-USDT": 50000.0},
    )
    broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-1",
            symbol="BTC-USDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.1,
        )
    )

    result = broker.replace_order(
        BrokerReplaceOrderRequest(
            original_client_order_id="client-1",
            replacement_client_order_id="client-1-r1",
            symbol="BTC-USDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.001,
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.rejection_code == "quantity_below_minimum"
    assert result.client_order_id == "client-1-r1"
    assert rest.cancelled == []
    assert len(rest.placed) == 1


def test_okx_broker_adapter_places_native_protective_oco_after_live_gate_approval():
    rest = FakeOKXRestAdapter()
    broker = OKXBrokerAdapter(
        adapter=rest,
        td_mode="cross",
        instrument_rules={
            "BTC-USDT": InstrumentOrderRules(
                symbol="BTC-USDT",
                quantity_step=0.01,
                min_quantity=0.01,
                price_tick=0.1,
                min_notional=10.0,
            )
        },
        reference_prices={"BTC-USDT": 50000.0},
    )

    result = broker.place_native_protective_order(
        BrokerProtectiveOrderRequest(
            protective_client_order_id="protective-1",
            parent_client_order_id="entry-1",
            symbol="BTC-USDT",
            entry_side=TradeSide.BUY,
            quantity=0.1,
            stop_loss_price=49000.0,
            take_profit_price=51000.0,
            live_order_gate=_live_gate(),
        )
    )

    assert result.status == OrderStatus.ACCEPTED
    assert result.native_order_type == "oco"
    assert result.exit_side == "sell"
    assert result.broker_order_id == "okx-algo-1"
    assert rest.protective == [
        {
            "symbol": "BTC-USDT",
            "side": "sell",
            "size": 0.1,
            "client_order_id": "protective-1",
            "stop_loss_price": 49000.0,
            "take_profit_price": 51000.0,
            "td_mode": "cross",
            "target_currency": None,
            "reduce_only": True,
        }
    ]


def test_okx_broker_adapter_rejects_native_protective_order_before_rule_lookup_when_gate_blocks():
    rest = FakeOKXRestAdapter()
    broker = OKXBrokerAdapter(adapter=rest)

    result = broker.place_native_protective_order(
        BrokerProtectiveOrderRequest(
            protective_client_order_id="protective-1",
            parent_client_order_id="entry-1",
            symbol="BTC-USDT",
            entry_side=TradeSide.BUY,
            quantity=0.1,
            stop_loss_price=49000.0,
            take_profit_price=51000.0,
            live_order_gate=_live_gate(approved=False),
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.rejection_code == "live_order_gate_rejected"
    assert rest.protective == []
    assert rest.rule_requests == []


class FailingOKXRestAdapter(FakeOKXRestAdapter):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc
        self.get_instrument_rules = None

    def place_order(self, **kwargs):
        raise self.exc


def test_okx_broker_adapter_classifies_retryable_exchange_error_result():
    broker = OKXBrokerAdapter(adapter=FailingOKXRestAdapter(TimeoutError("request timeout")))

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-timeout",
            symbol="BTC-USDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.1,
        )
    )

    assert result.status == OrderStatus.UNKNOWN
    assert result.exchange_error_category == ExchangeErrorCategory.RETRYABLE
    assert result.rejection_code == "retryable"
    assert result.client_order_id == "client-timeout"


def test_okx_broker_adapter_classifies_credential_configuration_error_result():
    broker = OKXBrokerAdapter(adapter=FailingOKXRestAdapter(OKXAdapterError("invalid API key")))

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-credential",
            symbol="BTC-USDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.1,
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.exchange_error_category == ExchangeErrorCategory.CREDENTIAL_CONFIGURATION
    assert "invalid API key" in result.exchange_error_message


class CapturingOKXAdapter(OKXAdapter):
    def __init__(self):
        super().__init__(require_credentials=False)
        self.calls = []

    def _request(self, method, path, *, params=None, body=None, auth=True):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "body": body,
                "auth": auth,
            }
        )
        client_order_id = body.get("clOrdId") or body.get("algoClOrdId")
        return {
            "success": True,
            "data": [
                {
                    "instId": body["instId"],
                    "clOrdId": body.get("clOrdId"),
                    "algoClOrdId": body.get("algoClOrdId"),
                    "reqId": body.get("reqId"),
                    "ordId": "okx-amended" if body.get("clOrdId") else None,
                    "algoId": "okx-oco" if body.get("algoClOrdId") else None,
                    "client_order_id": client_order_id,
                    "sCode": "0",
                }
            ],
        }


def test_okx_rest_adapter_sends_native_amend_order_payload():
    adapter = CapturingOKXAdapter()

    response = adapter.amend_order(
        "BTC-USDT",
        client_order_id="client-1",
        new_size=0.08,
        new_price=49900.0,
        request_id="client-1-r1",
        cancel_on_fail=True,
    )

    assert response["data"][0]["reqId"] == "client-1-r1"
    assert adapter.calls == [
        {
            "method": "POST",
            "path": "/api/v5/trade/amend-order",
            "params": None,
            "body": {
                "instId": "BTC-USDT",
                "clOrdId": "client-1",
                "cxlOnFail": "true",
                "reqId": "client-1-r1",
                "newSz": "0.08",
                "newPx": "49900",
            },
            "auth": True,
        }
    ]


def test_okx_rest_adapter_requires_amend_size_or_price():
    adapter = CapturingOKXAdapter()

    with pytest.raises(OKXAdapterError):
        adapter.amend_order("BTC-USDT", client_order_id="client-1")


def test_okx_rest_adapter_sends_native_protective_oco_payload():
    adapter = CapturingOKXAdapter()

    response = adapter.place_protective_order(
        "BTC-USDT",
        side="sell",
        size=0.1,
        client_order_id="protective-1",
        stop_loss_price=49000.0,
        take_profit_price=51000.0,
        td_mode="cross",
        reduce_only=True,
    )

    assert response["data"][0]["algoClOrdId"] == "protective-1"
    assert adapter.calls[-1] == {
        "method": "POST",
        "path": "/api/v5/trade/order-algo",
        "params": None,
        "body": {
            "instId": "BTC-USDT",
            "tdMode": "cross",
            "side": "sell",
            "ordType": "oco",
            "sz": "0.1",
            "algoClOrdId": "protective-1",
            "reduceOnly": "true",
            "tpTriggerPx": "51000",
            "tpOrdPx": "-1",
            "tpTriggerPxType": "last",
            "slTriggerPx": "49000",
            "slOrdPx": "-1",
            "slTriggerPxType": "last",
        },
        "auth": True,
    }
