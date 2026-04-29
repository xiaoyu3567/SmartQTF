import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from adapters.exchange.binance import BinanceAdapter, BinanceAdapterError
from layers.execution.live import BinanceBrokerAdapter
from quant.schemas.enums import ExchangeErrorCategory, OrderKind, OrderStatus, TradeSide
from quant.schemas.execution import (
    BrokerOrderRequest,
    BrokerProtectiveOrderRequest,
    BrokerReplaceOrderRequest,
    InstrumentOrderRules,
    LiveOrderGateDecision,
)


class FakeBinanceRestAdapter:
    def __init__(self):
        self.placed = []
        self.oco = []
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
        time_in_force="GTC",
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
                "time_in_force": time_in_force,
                "reduce_only": reduce_only,
            }
        )
        return {
            "success": True,
            "data": [
                {
                    "symbol": symbol,
                    "clientOrderId": client_order_id,
                    "orderId": len(self.placed),
                    "side": str(getattr(side, "value", side)).upper(),
                    "status": "NEW",
                    "origQty": str(size),
                    "executedQty": "0",
                }
            ],
        }

    def place_oco_order(
        self,
        *,
        symbol,
        side,
        size,
        client_order_id=None,
        stop_loss_price=None,
        take_profit_price=None,
        stop_loss_client_order_id=None,
        take_profit_client_order_id=None,
    ):
        self.oco.append(
            {
                "symbol": symbol,
                "side": side,
                "size": size,
                "client_order_id": client_order_id,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
                "stop_loss_client_order_id": stop_loss_client_order_id,
                "take_profit_client_order_id": take_profit_client_order_id,
            }
        )
        return {
            "success": True,
            "data": [
                {
                    "symbol": symbol,
                    "listClientOrderId": client_order_id,
                    "orderListId": len(self.oco),
                    "listOrderStatus": "EXECUTING",
                    "orders": [
                        {"symbol": symbol, "clientOrderId": take_profit_client_order_id, "orderId": 101},
                        {"symbol": symbol, "clientOrderId": stop_loss_client_order_id, "orderId": 102},
                    ],
                }
            ],
        }

    def cancel_order(self, client_order_id):
        self.cancelled.append(client_order_id)
        return {
            "success": True,
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "clientOrderId": client_order_id,
                    "orderId": 10,
                    "side": "BUY",
                    "status": "CANCELED",
                    "origQty": "0.1",
                    "executedQty": "0",
                }
            ],
        }

    def get_order(self, client_order_id, symbol=None):
        return {
            "success": True,
            "data": [
                {
                    "symbol": symbol or "BTCUSDT",
                    "clientOrderId": client_order_id,
                    "orderId": 11,
                    "side": "BUY",
                    "status": "PARTIALLY_FILLED",
                    "origQty": "0.1",
                    "executedQty": "0.04",
                    "cummulativeQuoteQty": "2004",
                }
            ],
        }

    def list_open_orders(self, symbol=None):
        return {
            "success": True,
            "data": [
                {
                    "symbol": symbol or "BTCUSDT",
                    "clientOrderId": "open-1",
                    "orderId": 12,
                    "side": "BUY",
                    "status": "NEW",
                    "origQty": "0.2",
                    "executedQty": "0",
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


def test_binance_broker_adapter_places_typed_limit_order():
    rest = FakeBinanceRestAdapter()
    broker = BinanceBrokerAdapter(adapter=rest)

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-1",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.LIMIT,
            quantity=0.1,
            limit_price=50000.0,
        )
    )

    assert broker.name == "binance"
    assert result.status == OrderStatus.ACCEPTED
    assert result.client_order_id == "client-1"
    assert result.broker_order_id == "1"
    assert rest.placed == [
        {
            "symbol": "BTCUSDT",
            "side": TradeSide.BUY,
            "size": 0.1,
            "type": "limit",
            "client_order_id": "client-1",
            "price": 50000.0,
            "time_in_force": "gtc",
            "reduce_only": False,
        }
    ]


def test_binance_broker_adapter_rejects_order_rule_violations_before_rest_call():
    rest = FakeBinanceRestAdapter()
    broker = BinanceBrokerAdapter(
        adapter=rest,
        instrument_rules={
            "BTCUSDT": InstrumentOrderRules(
                symbol="BTCUSDT",
                quantity_step=0.01,
                min_quantity=0.01,
                min_notional=10.0,
            )
        },
        reference_prices={"BTCUSDT": 50000.0},
    )

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="too-small",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.001,
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.rejection_code == "quantity_below_minimum"
    assert rest.placed == []


def test_binance_broker_adapter_loads_instrument_rules_before_order():
    rest = FakeBinanceRestAdapter()
    broker = BinanceBrokerAdapter(adapter=rest, reference_prices={"BTCUSDT": 50000.0})

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="too-small",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.001,
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.rejection_code == "quantity_below_minimum"
    assert rest.rule_requests == ["BTCUSDT"]
    assert rest.placed == []


def test_binance_broker_adapter_queries_lists_cancels_and_replaces_orders():
    rest = FakeBinanceRestAdapter()
    broker = BinanceBrokerAdapter(adapter=rest)
    broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-1",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.1,
        )
    )

    queried = broker.get_order("client-1")
    open_orders = broker.list_open_orders("BTCUSDT")
    cancelled = broker.cancel_order("client-1")
    replacement = broker.replace_order(
        BrokerReplaceOrderRequest(
            original_client_order_id="client-1",
            replacement_client_order_id="client-1-r1",
            symbol="BTCUSDT",
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


def test_binance_broker_adapter_validates_replacement_before_cancelling_original():
    rest = FakeBinanceRestAdapter()
    broker = BinanceBrokerAdapter(
        adapter=rest,
        instrument_rules={
            "BTCUSDT": InstrumentOrderRules(
                symbol="BTCUSDT",
                quantity_step=0.01,
                min_quantity=0.01,
                min_notional=10.0,
            )
        },
        reference_prices={"BTCUSDT": 50000.0},
    )
    broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-1",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.1,
        )
    )

    result = broker.replace_order(
        BrokerReplaceOrderRequest(
            original_client_order_id="client-1",
            replacement_client_order_id="client-1-r1",
            symbol="BTCUSDT",
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


def test_binance_broker_adapter_places_native_protective_oco_after_live_gate_approval():
    rest = FakeBinanceRestAdapter()
    broker = BinanceBrokerAdapter(
        adapter=rest,
        instrument_rules={
            "BTCUSDT": InstrumentOrderRules(
                symbol="BTCUSDT",
                quantity_step=0.01,
                min_quantity=0.01,
                price_tick=0.1,
                min_notional=10.0,
            )
        },
        reference_prices={"BTCUSDT": 50000.0},
    )

    result = broker.place_native_protective_order(
        BrokerProtectiveOrderRequest(
            protective_client_order_id="protective-1",
            parent_client_order_id="entry-1",
            symbol="BTCUSDT",
            entry_side=TradeSide.BUY,
            quantity=0.1,
            stop_loss_price=49000.0,
            take_profit_price=51000.0,
            stop_loss_client_order_id="protective-1-sl",
            take_profit_client_order_id="protective-1-tp",
            live_order_gate=_live_gate(),
        )
    )

    assert result.status == OrderStatus.ACCEPTED
    assert result.native_order_type == "orderList/oco"
    assert result.exit_side == "sell"
    assert result.broker_order_id == "1"
    assert rest.oco == [
        {
            "symbol": "BTCUSDT",
            "side": "sell",
            "size": 0.1,
            "client_order_id": "protective-1",
            "stop_loss_price": 49000.0,
            "take_profit_price": 51000.0,
            "stop_loss_client_order_id": "protective-1-sl",
            "take_profit_client_order_id": "protective-1-tp",
        }
    ]


def test_binance_broker_adapter_rejects_native_protective_order_before_rule_lookup_when_gate_blocks():
    rest = FakeBinanceRestAdapter()
    broker = BinanceBrokerAdapter(adapter=rest)

    result = broker.place_native_protective_order(
        BrokerProtectiveOrderRequest(
            protective_client_order_id="protective-1",
            parent_client_order_id="entry-1",
            symbol="BTCUSDT",
            entry_side=TradeSide.BUY,
            quantity=0.1,
            stop_loss_price=49000.0,
            take_profit_price=51000.0,
            live_order_gate=_live_gate(approved=False),
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.rejection_code == "live_order_gate_rejected"
    assert rest.oco == []
    assert rest.rule_requests == []


def test_binance_broker_adapter_rejects_native_oco_without_take_profit_before_rest_call():
    rest = FakeBinanceRestAdapter()
    broker = BinanceBrokerAdapter(adapter=rest)

    result = broker.place_native_protective_order(
        BrokerProtectiveOrderRequest(
            protective_client_order_id="protective-1",
            parent_client_order_id="entry-1",
            symbol="BTCUSDT",
            entry_side=TradeSide.BUY,
            quantity=0.1,
            stop_loss_price=49000.0,
            live_order_gate=_live_gate(),
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.rejection_code == "native_oco_requires_take_profit"
    assert rest.oco == []


class FailingBinanceRestAdapter(FakeBinanceRestAdapter):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc
        self.get_instrument_rules = None

    def place_order(self, **kwargs):
        raise self.exc


def test_binance_broker_adapter_classifies_recoverable_exchange_error_result():
    broker = BinanceBrokerAdapter(adapter=FailingBinanceRestAdapter(BinanceAdapterError("unknown order")))

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-missing",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.1,
        )
    )

    assert result.status == OrderStatus.UNKNOWN
    assert result.exchange_error_category == ExchangeErrorCategory.RECOVERABLE
    assert result.rejection_code == "recoverable"


def test_binance_broker_adapter_classifies_fatal_exchange_error_result():
    broker = BinanceBrokerAdapter(adapter=FailingBinanceRestAdapter(BinanceAdapterError("-1013 invalid quantity")))

    result = broker.place_order(
        BrokerOrderRequest(
            client_order_id="client-invalid",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.1,
        )
    )

    assert result.status == OrderStatus.REJECTED
    assert result.exchange_error_category == ExchangeErrorCategory.FATAL
    assert "-1013" in result.exchange_error_message


class CapturingBinanceAdapter(BinanceAdapter):
    def __init__(self):
        super().__init__(api_key="key", secret="secret", require_credentials=False)
        self.calls = []

    def _request(self, method, path, *, params=None):
        self.calls.append({"method": method, "path": path, "params": params})
        return {
            "success": True,
            "data": [
                {
                    "symbol": params["symbol"],
                    "clientOrderId": params.get("newClientOrderId"),
                    "listClientOrderId": params.get("listClientOrderId"),
                    "orderId": 99,
                    "orderListId": 199,
                    "side": params["side"],
                    "status": "NEW",
                    "listOrderStatus": "EXECUTING",
                    "origQty": params["quantity"],
                    "executedQty": "0",
                }
            ],
        }


def test_binance_rest_adapter_sends_native_cancel_replace_payload():
    adapter = CapturingBinanceAdapter()

    response = adapter.cancel_replace_order(
        symbol="BTCUSDT",
        side="buy",
        size=0.08,
        type="limit",
        original_client_order_id="client-1",
        replacement_client_order_id="client-1-r1",
        price=49900.0,
        time_in_force="gtc",
    )

    assert response["data"][0]["clientOrderId"] == "client-1-r1"
    assert adapter.calls == [
        {
            "method": "POST",
            "path": "/api/v3/order/cancelReplace",
            "params": {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "quantity": "0.08",
                "cancelReplaceMode": "STOP_ON_FAILURE",
                "cancelOrigClientOrderId": "client-1",
                "newClientOrderId": "client-1-r1",
                "price": "49900",
                "timeInForce": "GTC",
            },
        }
    ]


def test_binance_rest_adapter_rejects_unknown_cancel_replace_mode():
    adapter = CapturingBinanceAdapter()

    with pytest.raises(BinanceAdapterError):
        adapter.cancel_replace_order(
            symbol="BTCUSDT",
            side="buy",
            size=0.08,
            type="market",
            original_client_order_id="client-1",
            replacement_client_order_id="client-1-r1",
            cancel_replace_mode="never",
        )


def test_binance_rest_adapter_sends_native_oco_payload_for_sell_exit():
    adapter = CapturingBinanceAdapter()

    response = adapter.place_oco_order(
        symbol="BTCUSDT",
        side="sell",
        size=0.1,
        client_order_id="protective-1",
        stop_loss_price=49000.0,
        take_profit_price=51000.0,
        stop_loss_client_order_id="protective-1-sl",
        take_profit_client_order_id="protective-1-tp",
    )

    assert response["data"][0]["listClientOrderId"] == "protective-1"
    assert adapter.calls[-1] == {
        "method": "POST",
        "path": "/api/v3/orderList/oco",
        "params": {
            "symbol": "BTCUSDT",
            "side": "SELL",
            "quantity": "0.1",
            "listClientOrderId": "protective-1",
            "aboveType": "LIMIT_MAKER",
            "abovePrice": "51000",
            "belowType": "STOP_LOSS",
            "belowStopPrice": "49000",
            "aboveClientOrderId": "protective-1-tp",
            "belowClientOrderId": "protective-1-sl",
        },
    }


def test_binance_rest_adapter_sends_native_oco_payload_for_buy_exit():
    adapter = CapturingBinanceAdapter()

    adapter.place_oco_order(
        symbol="BTCUSDT",
        side="buy",
        size=0.1,
        client_order_id="protective-1",
        stop_loss_price=51000.0,
        take_profit_price=49000.0,
        stop_loss_client_order_id="protective-1-sl",
        take_profit_client_order_id="protective-1-tp",
    )

    assert adapter.calls[-1]["params"] == {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "0.1",
        "listClientOrderId": "protective-1",
        "aboveType": "STOP_LOSS",
        "aboveStopPrice": "51000",
        "belowType": "LIMIT_MAKER",
        "belowPrice": "49000",
        "aboveClientOrderId": "protective-1-sl",
        "belowClientOrderId": "protective-1-tp",
    }
