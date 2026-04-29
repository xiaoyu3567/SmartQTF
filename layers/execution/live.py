import hashlib
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from adapters.exchange.binance import BinanceAdapter
from adapters.exchange.okx import OKXAdapter
from quant.execution.broker import BrokerAdapter
from quant.schemas.enums import ExchangeErrorCategory, OrderKind, OrderStatus, TradeSide
from quant.schemas.execution import (
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerProtectiveOrderRequest,
    BrokerProtectiveOrderResult,
    BrokerReplaceOrderRequest,
    InstrumentOrderRules,
)


LOGGER = logging.getLogger(__name__)


class LiveExecutionError(RuntimeError):
    pass


class OKXBrokerAdapter(BrokerAdapter):
    """BrokerAdapter wrapper around the low-level OKX REST adapter."""

    def __init__(
        self,
        adapter: Optional[object] = None,
        *,
        instrument_rules: Optional[Dict[str, InstrumentOrderRules]] = None,
        reference_prices: Optional[Dict[str, float]] = None,
        td_mode: str = "cash",
        target_currency: Optional[str] = None,
    ):
        self.adapter = adapter or OKXAdapter()
        self.instrument_rules = instrument_rules or {}
        self.reference_prices = reference_prices or {}
        self.td_mode = td_mode
        self.target_currency = target_currency
        self._requests_by_client_order_id: Dict[str, BrokerOrderRequest] = {}

    @property
    def name(self) -> str:
        return "okx"

    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        try:
            rejected = self._validate_rules(request)
            if rejected is not None:
                return rejected

            raw = self.adapter.place_order(
                symbol=request.symbol,
                side=request.side,
                size=request.quantity,
                type=self._okx_order_type(request),
                client_order_id=request.client_order_id,
                price=request.limit_price,
                td_mode=self.td_mode,
                target_currency=self.target_currency,
                reduce_only=request.reduce_only,
            )
            result = self._result_from_raw(raw, fallback_request=request)
            self._requests_by_client_order_id[request.client_order_id] = request
            return result
        except Exception as exc:
            return _classified_error_result("okx", exc, fallback_request=request)

    def cancel_order(self, client_order_id: str) -> BrokerOrderResult:
        fallback = self._requests_by_client_order_id.get(client_order_id)
        try:
            raw = self.adapter.cancel_order(client_order_id)
            return self._result_from_raw(
                raw,
                fallback_request=fallback,
                fallback_client_order_id=client_order_id,
                default_status=OrderStatus.CANCELLED,
            )
        except Exception as exc:
            return _classified_error_result(
                "okx",
                exc,
                fallback_request=fallback,
                fallback_client_order_id=client_order_id,
            )

    def replace_order(self, request: BrokerReplaceOrderRequest) -> BrokerOrderResult:
        replacement = BrokerOrderRequest(
            client_order_id=request.replacement_client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            limit_price=request.limit_price,
            time_in_force=request.time_in_force,
            reduce_only=False,
            trace=request.trace,
        )
        rejected = self._validate_rules(replacement)
        if rejected is not None:
            return rejected

        self.cancel_order(request.original_client_order_id)
        return self.place_order(replacement)

    def place_native_protective_order(
        self,
        request: BrokerProtectiveOrderRequest,
    ) -> BrokerProtectiveOrderResult:
        if not request.live_order_gate.approved:
            return _protective_rejection(
                request,
                "live_order_gate_rejected",
                ", ".join(request.live_order_gate.reason_codes),
            )

        try:
            rejected = _validate_native_protective_order(
                request,
                rules=self._rules_for_symbol(request.symbol),
                reference_price=self.reference_prices.get(request.symbol),
            )
            if rejected is not None:
                return rejected

            raw = self.adapter.place_protective_order(
                symbol=request.symbol,
                side=_protective_exit_side(request.entry_side),
                size=request.quantity,
                client_order_id=request.protective_client_order_id,
                stop_loss_price=request.stop_loss_price,
                take_profit_price=request.take_profit_price,
                td_mode=self.td_mode,
                target_currency=self.target_currency,
                reduce_only=request.reduce_only,
            )
            return _okx_protective_result_from_raw(raw, request)
        except Exception as exc:
            return _classified_protective_error_result("okx", exc, request)

    def get_order(self, client_order_id: str) -> BrokerOrderResult:
        fallback = self._requests_by_client_order_id.get(client_order_id)
        symbol = fallback.symbol if fallback is not None else None
        try:
            raw = self.adapter.get_order(client_order_id, symbol=symbol)
            return self._result_from_raw(
                raw,
                fallback_request=fallback,
                fallback_client_order_id=client_order_id,
                default_status=OrderStatus.UNKNOWN,
            )
        except Exception as exc:
            return _classified_error_result(
                "okx",
                exc,
                fallback_request=fallback,
                fallback_client_order_id=client_order_id,
            )

    def list_open_orders(self, symbol: str | None = None) -> List[BrokerOrderResult]:
        try:
            raw = self.adapter.list_open_orders(symbol=symbol)
            return [
                self._result_from_item(item, fallback_request=self._requests_by_client_order_id.get(item.get("clOrdId")))
                for item in raw.get("data", [])
            ]
        except Exception as exc:
            return [_classified_error_result("okx", exc, symbol=symbol or "")]

    def _validate_rules(self, request: BrokerOrderRequest) -> Optional[BrokerOrderResult]:
        rules = self._rules_for_symbol(request.symbol)
        if rules is None:
            return None
        violations = rules.validate_order_request(
            request,
            reference_price=self.reference_prices.get(request.symbol),
        )
        if not violations:
            return None
        return BrokerOrderResult(
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.REJECTED,
            requested_qty=request.quantity,
            rejection_code=violations[0].code,
            rejection_reason="; ".join(violation.message for violation in violations),
            trace=request.trace,
        )

    def _rules_for_symbol(self, symbol: str) -> Optional[InstrumentOrderRules]:
        rules = self.instrument_rules.get(symbol)
        if rules is not None:
            return rules
        loader = getattr(self.adapter, "get_instrument_rules", None)
        if loader is None:
            return None
        rules = loader(symbol)
        self.instrument_rules[rules.symbol] = rules
        if rules.symbol != symbol:
            self.instrument_rules[symbol] = rules
        return rules

    def _result_from_raw(
        self,
        raw: Dict[str, Any],
        *,
        fallback_request: Optional[BrokerOrderRequest] = None,
        fallback_client_order_id: Optional[str] = None,
        default_status: OrderStatus = OrderStatus.ACCEPTED,
    ) -> BrokerOrderResult:
        items = raw.get("data") if isinstance(raw, dict) else None
        item = items[0] if items else {}
        return self._result_from_item(
            item,
            fallback_request=fallback_request,
            fallback_client_order_id=fallback_client_order_id,
            default_status=default_status,
        )

    def _result_from_item(
        self,
        item: Dict[str, Any],
        *,
        fallback_request: Optional[BrokerOrderRequest] = None,
        fallback_client_order_id: Optional[str] = None,
        default_status: OrderStatus = OrderStatus.ACCEPTED,
    ) -> BrokerOrderResult:
        client_order_id = item.get("clOrdId") or fallback_client_order_id
        if fallback_request is not None:
            client_order_id = client_order_id or fallback_request.client_order_id
            symbol = item.get("instId") or fallback_request.symbol
            side = item.get("side") or fallback_request.side
            requested_qty = self._float_from_item(item, ("sz",), fallback_request.quantity)
            trace = fallback_request.trace
        else:
            symbol = item.get("instId") or ""
            side = item.get("side") or "buy"
            requested_qty = self._float_from_item(item, ("sz",), 0.0)
            trace = None

        status = self._status_from_item(item, default_status)
        filled_qty = self._float_from_item(item, ("accFillSz", "fillSz"), 0.0)
        avg_fill_price = self._optional_float_from_item(item, ("avgPx", "fillPx"))
        rejection_code = self._rejection_code(item, status)
        rejection_reason = item.get("sMsg") or item.get("msg")

        return BrokerOrderResult(
            client_order_id=client_order_id or "",
            broker_order_id=item.get("ordId"),
            symbol=str(symbol),
            side=side,
            status=status,
            requested_qty=requested_qty,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            rejection_code=rejection_code,
            rejection_reason=rejection_reason,
            exchange_error_category=_category_for_exchange_rejection("okx", rejection_code, rejection_reason),
            exchange_error_message=rejection_reason if rejection_code else None,
            trace=trace,
        )

    def _okx_order_type(self, request: BrokerOrderRequest) -> str:
        if request.order_type == OrderKind.MARKET:
            return "market"
        if request.order_type == OrderKind.LIMIT:
            return "limit"
        return str(request.order_type)

    def _status_from_item(self, item: Dict[str, Any], default_status: OrderStatus) -> OrderStatus:
        s_code = str(item.get("sCode", "0"))
        if s_code != "0":
            return OrderStatus.REJECTED

        state = str(item.get("state") or item.get("status") or "").strip().lower()
        mapping = {
            "live": OrderStatus.ACCEPTED,
            "partially_filled": OrderStatus.PARTIAL,
            "partially-filled": OrderStatus.PARTIAL,
            "partial": OrderStatus.PARTIAL,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }
        return mapping.get(state, default_status)

    def _rejection_code(self, item: Dict[str, Any], status: OrderStatus) -> Optional[str]:
        s_code = str(item.get("sCode", "0"))
        if status == OrderStatus.REJECTED and s_code != "0":
            return s_code
        return None

    def _float_from_item(self, item: Dict[str, Any], keys: tuple[str, ...], default: float) -> float:
        value = self._optional_float_from_item(item, keys)
        return default if value is None else value

    def _optional_float_from_item(self, item: Dict[str, Any], keys: tuple[str, ...]) -> Optional[float]:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return float(value)
        return None


class BinanceBrokerAdapter(BrokerAdapter):
    """BrokerAdapter wrapper around the low-level Binance Spot REST adapter."""

    def __init__(
        self,
        adapter: Optional[object] = None,
        *,
        instrument_rules: Optional[Dict[str, InstrumentOrderRules]] = None,
        reference_prices: Optional[Dict[str, float]] = None,
    ):
        self.adapter = adapter or BinanceAdapter()
        self.instrument_rules = instrument_rules or {}
        self.reference_prices = reference_prices or {}
        self._requests_by_client_order_id: Dict[str, BrokerOrderRequest] = {}

    @property
    def name(self) -> str:
        return "binance"

    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        try:
            rejected = self._validate_rules(request)
            if rejected is not None:
                return rejected

            raw = self.adapter.place_order(
                symbol=request.symbol,
                side=request.side,
                size=request.quantity,
                type=self._binance_order_type(request),
                client_order_id=request.client_order_id,
                price=request.limit_price,
                time_in_force=request.time_in_force,
                reduce_only=request.reduce_only,
            )
            result = self._result_from_raw(raw, fallback_request=request)
            self._requests_by_client_order_id[request.client_order_id] = request
            return result
        except Exception as exc:
            return _classified_error_result("binance", exc, fallback_request=request)

    def cancel_order(self, client_order_id: str) -> BrokerOrderResult:
        fallback = self._requests_by_client_order_id.get(client_order_id)
        try:
            raw = self.adapter.cancel_order(client_order_id)
            return self._result_from_raw(
                raw,
                fallback_request=fallback,
                fallback_client_order_id=client_order_id,
                default_status=OrderStatus.CANCELLED,
            )
        except Exception as exc:
            return _classified_error_result(
                "binance",
                exc,
                fallback_request=fallback,
                fallback_client_order_id=client_order_id,
            )

    def replace_order(self, request: BrokerReplaceOrderRequest) -> BrokerOrderResult:
        replacement = BrokerOrderRequest(
            client_order_id=request.replacement_client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            limit_price=request.limit_price,
            time_in_force=request.time_in_force,
            reduce_only=False,
            trace=request.trace,
        )
        rejected = self._validate_rules(replacement)
        if rejected is not None:
            return rejected

        self.cancel_order(request.original_client_order_id)
        return self.place_order(replacement)

    def place_native_protective_order(
        self,
        request: BrokerProtectiveOrderRequest,
    ) -> BrokerProtectiveOrderResult:
        if not request.live_order_gate.approved:
            return _protective_rejection(
                request,
                "live_order_gate_rejected",
                ", ".join(request.live_order_gate.reason_codes),
            )

        if request.take_profit_price is None:
            return _protective_rejection(
                request,
                "native_oco_requires_take_profit",
                "Binance spot OCO requires take_profit_price and stop_loss_price",
            )

        try:
            rejected = _validate_native_protective_order(
                request,
                rules=self._rules_for_symbol(request.symbol),
                reference_price=self.reference_prices.get(request.symbol),
            )
            if rejected is not None:
                return rejected

            raw = self.adapter.place_oco_order(
                symbol=request.symbol,
                side=_protective_exit_side(request.entry_side),
                size=request.quantity,
                client_order_id=request.protective_client_order_id,
                stop_loss_price=request.stop_loss_price,
                take_profit_price=request.take_profit_price,
                stop_loss_client_order_id=request.stop_loss_client_order_id,
                take_profit_client_order_id=request.take_profit_client_order_id,
            )
            return _binance_protective_result_from_raw(raw, request)
        except Exception as exc:
            return _classified_protective_error_result("binance", exc, request)

    def get_order(self, client_order_id: str) -> BrokerOrderResult:
        fallback = self._requests_by_client_order_id.get(client_order_id)
        symbol = fallback.symbol if fallback is not None else None
        try:
            raw = self.adapter.get_order(client_order_id, symbol=symbol)
            return self._result_from_raw(
                raw,
                fallback_request=fallback,
                fallback_client_order_id=client_order_id,
                default_status=OrderStatus.UNKNOWN,
            )
        except Exception as exc:
            return _classified_error_result(
                "binance",
                exc,
                fallback_request=fallback,
                fallback_client_order_id=client_order_id,
            )

    def list_open_orders(self, symbol: str | None = None) -> List[BrokerOrderResult]:
        try:
            raw = self.adapter.list_open_orders(symbol=symbol)
            return [
                self._result_from_item(
                    item,
                    fallback_request=self._requests_by_client_order_id.get(item.get("clientOrderId")),
                )
                for item in raw.get("data", [])
            ]
        except Exception as exc:
            return [_classified_error_result("binance", exc, symbol=symbol or "")]

    def _validate_rules(self, request: BrokerOrderRequest) -> Optional[BrokerOrderResult]:
        rules = self._rules_for_symbol(request.symbol)
        if rules is None:
            return None
        violations = rules.validate_order_request(
            request,
            reference_price=self.reference_prices.get(request.symbol),
        )
        if not violations:
            return None
        return BrokerOrderResult(
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.REJECTED,
            requested_qty=request.quantity,
            rejection_code=violations[0].code,
            rejection_reason="; ".join(violation.message for violation in violations),
            trace=request.trace,
        )

    def _rules_for_symbol(self, symbol: str) -> Optional[InstrumentOrderRules]:
        rules = self.instrument_rules.get(symbol)
        if rules is not None:
            return rules
        loader = getattr(self.adapter, "get_instrument_rules", None)
        if loader is None:
            return None
        rules = loader(symbol)
        self.instrument_rules[rules.symbol] = rules
        if rules.symbol != symbol:
            self.instrument_rules[symbol] = rules
        return rules

    def _result_from_raw(
        self,
        raw: Dict[str, Any],
        *,
        fallback_request: Optional[BrokerOrderRequest] = None,
        fallback_client_order_id: Optional[str] = None,
        default_status: OrderStatus = OrderStatus.ACCEPTED,
    ) -> BrokerOrderResult:
        items = raw.get("data") if isinstance(raw, dict) else None
        item = items[0] if items else {}
        return self._result_from_item(
            item,
            fallback_request=fallback_request,
            fallback_client_order_id=fallback_client_order_id,
            default_status=default_status,
        )

    def _result_from_item(
        self,
        item: Dict[str, Any],
        *,
        fallback_request: Optional[BrokerOrderRequest] = None,
        fallback_client_order_id: Optional[str] = None,
        default_status: OrderStatus = OrderStatus.ACCEPTED,
    ) -> BrokerOrderResult:
        client_order_id = item.get("clientOrderId") or fallback_client_order_id
        if fallback_request is not None:
            client_order_id = client_order_id or fallback_request.client_order_id
            symbol = item.get("symbol") or fallback_request.symbol
            side = self._side_from_item(item, fallback_request.side)
            requested_qty = self._float_from_item(item, ("origQty", "quantity"), fallback_request.quantity)
            trace = fallback_request.trace
        else:
            symbol = item.get("symbol") or ""
            side = self._side_from_item(item, "buy")
            requested_qty = self._float_from_item(item, ("origQty", "quantity"), 0.0)
            trace = None

        filled_qty = self._float_from_item(item, ("executedQty",), 0.0)
        avg_fill_price = self._avg_fill_price(item, filled_qty)

        return BrokerOrderResult(
            client_order_id=client_order_id or "",
            broker_order_id=str(item.get("orderId")) if item.get("orderId") is not None else None,
            symbol=str(symbol),
            side=side,
            status=self._status_from_item(item, default_status),
            requested_qty=requested_qty,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            rejection_code=self._rejection_code(item),
            rejection_reason=item.get("msg"),
            exchange_error_category=_category_for_exchange_rejection(
                "binance",
                self._rejection_code(item),
                item.get("msg"),
            ),
            exchange_error_message=item.get("msg") if self._rejection_code(item) is not None else None,
            trace=trace,
        )

    def _binance_order_type(self, request: BrokerOrderRequest) -> str:
        if request.order_type == OrderKind.MARKET:
            return "market"
        if request.order_type == OrderKind.LIMIT:
            return "limit"
        return str(request.order_type)

    def _status_from_item(self, item: Dict[str, Any], default_status: OrderStatus) -> OrderStatus:
        status = str(item.get("status") or "").strip().upper()
        mapping = {
            "NEW": OrderStatus.ACCEPTED,
            "PARTIALLY_FILLED": OrderStatus.PARTIAL,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "CANCELLED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.REJECTED,
        }
        return mapping.get(status, default_status)

    def _side_from_item(self, item: Dict[str, Any], default_side: Any) -> str:
        side = str(getattr(item.get("side") or default_side, "value", item.get("side") or default_side)).strip().lower()
        return "buy" if side == "buy" else "sell"

    def _rejection_code(self, item: Dict[str, Any]) -> Optional[str]:
        code = item.get("code")
        return str(code) if code is not None else None

    def _float_from_item(self, item: Dict[str, Any], keys: tuple[str, ...], default: float) -> float:
        value = self._optional_float_from_item(item, keys)
        return default if value is None else value

    def _optional_float_from_item(self, item: Dict[str, Any], keys: tuple[str, ...]) -> Optional[float]:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return float(value)
        return None

    def _avg_fill_price(self, item: Dict[str, Any], filled_qty: float) -> Optional[float]:
        avg_price = self._optional_float_from_item(item, ("avgPrice",))
        if avg_price is not None:
            return avg_price
        quote_qty = self._optional_float_from_item(item, ("cummulativeQuoteQty",))
        if quote_qty is not None and filled_qty > 0.0:
            return quote_qty / filled_qty
        return None


def _protective_exit_side(entry_side: Any) -> str:
    side = str(getattr(entry_side, "value", entry_side)).strip().lower()
    return TradeSide.SELL.value if side == TradeSide.BUY.value else TradeSide.BUY.value


def _protective_native_order_type(request: BrokerProtectiveOrderRequest) -> str:
    return "oco" if request.take_profit_price is not None else "conditional"


def _validate_native_protective_order(
    request: BrokerProtectiveOrderRequest,
    *,
    rules: Optional[InstrumentOrderRules],
    reference_price: Optional[float],
) -> Optional[BrokerProtectiveOrderResult]:
    if rules is None:
        return None

    prices = [request.stop_loss_price]
    if request.take_profit_price is not None:
        prices.append(request.take_profit_price)

    violations = []
    for price in prices:
        validation_request = BrokerOrderRequest(
            client_order_id=request.protective_client_order_id,
            symbol=request.symbol,
            side=_protective_exit_side(request.entry_side),
            order_type=OrderKind.LIMIT,
            quantity=request.quantity,
            limit_price=price,
            reduce_only=request.reduce_only,
            trace=request.trace,
        )
        violations.extend(
            rules.validate_order_request(
                validation_request,
                reference_price=reference_price or price,
            )
        )

    if not violations:
        return None
    return _protective_rejection(
        request,
        violations[0].code,
        "; ".join(violation.message for violation in violations),
    )


def _protective_rejection(
    request: BrokerProtectiveOrderRequest,
    code: str,
    reason: str,
    *,
    exchange_error_category: Optional[ExchangeErrorCategory] = None,
    status: OrderStatus = OrderStatus.REJECTED,
) -> BrokerProtectiveOrderResult:
    return BrokerProtectiveOrderResult(
        protective_client_order_id=request.protective_client_order_id,
        parent_client_order_id=request.parent_client_order_id,
        symbol=request.symbol,
        exit_side=_protective_exit_side(request.entry_side),
        native_order_type=_protective_native_order_type(request),
        status=status,
        requested_qty=request.quantity,
        stop_loss_price=request.stop_loss_price,
        take_profit_price=request.take_profit_price,
        stop_loss_client_order_id=request.stop_loss_client_order_id,
        take_profit_client_order_id=request.take_profit_client_order_id,
        rejection_code=code,
        rejection_reason=reason,
        exchange_error_category=exchange_error_category,
        exchange_error_message=reason if exchange_error_category is not None else None,
        live_order_gate=request.live_order_gate,
        trace=request.trace,
    )


def _okx_protective_result_from_raw(
    raw: Dict[str, Any],
    request: BrokerProtectiveOrderRequest,
) -> BrokerProtectiveOrderResult:
    items = raw.get("data") if isinstance(raw, dict) else None
    item = items[0] if items else {}
    s_code = str(item.get("sCode", "0"))
    rejected = s_code != "0"
    rejection_reason = item.get("sMsg") or item.get("msg")
    return BrokerProtectiveOrderResult(
        protective_client_order_id=item.get("algoClOrdId") or request.protective_client_order_id,
        parent_client_order_id=request.parent_client_order_id,
        broker_order_id=item.get("algoId") or item.get("ordId"),
        symbol=item.get("instId") or request.symbol,
        exit_side=_protective_exit_side(request.entry_side),
        native_order_type=_protective_native_order_type(request),
        status=OrderStatus.REJECTED if rejected else OrderStatus.ACCEPTED,
        requested_qty=request.quantity,
        stop_loss_price=request.stop_loss_price,
        take_profit_price=request.take_profit_price,
        stop_loss_client_order_id=request.stop_loss_client_order_id,
        take_profit_client_order_id=request.take_profit_client_order_id,
        rejection_code=s_code if rejected else None,
        rejection_reason=rejection_reason,
        exchange_error_category=_category_for_exchange_rejection("okx", s_code if rejected else None, rejection_reason),
        exchange_error_message=rejection_reason if rejected else None,
        live_order_gate=request.live_order_gate,
        trace=request.trace,
        metadata={"exchange": "okx", "raw": raw},
    )


def _binance_protective_result_from_raw(
    raw: Dict[str, Any],
    request: BrokerProtectiveOrderRequest,
) -> BrokerProtectiveOrderResult:
    items = raw.get("data") if isinstance(raw, dict) else None
    item = items[0] if items else {}
    rejection_code = str(item.get("code")) if item.get("code") is not None else None
    rejection_reason = item.get("msg")
    return BrokerProtectiveOrderResult(
        protective_client_order_id=item.get("listClientOrderId") or request.protective_client_order_id,
        parent_client_order_id=request.parent_client_order_id,
        broker_order_id=str(item.get("orderListId")) if item.get("orderListId") is not None else None,
        symbol=item.get("symbol") or request.symbol,
        exit_side=_protective_exit_side(request.entry_side),
        native_order_type="orderList/oco",
        status=_binance_protective_status_from_item(item),
        requested_qty=request.quantity,
        stop_loss_price=request.stop_loss_price,
        take_profit_price=request.take_profit_price,
        stop_loss_client_order_id=request.stop_loss_client_order_id,
        take_profit_client_order_id=request.take_profit_client_order_id,
        rejection_code=rejection_code,
        rejection_reason=rejection_reason,
        exchange_error_category=_category_for_exchange_rejection("binance", rejection_code, rejection_reason),
        exchange_error_message=rejection_reason if rejection_code is not None else None,
        live_order_gate=request.live_order_gate,
        trace=request.trace,
        metadata={"exchange": "binance", "raw": raw},
    )


def _binance_protective_status_from_item(item: Dict[str, Any]) -> OrderStatus:
    status = str(item.get("listOrderStatus") or item.get("listStatusType") or item.get("status") or "").upper()
    mapping = {
        "EXECUTING": OrderStatus.ACCEPTED,
        "EXEC_STARTED": OrderStatus.ACCEPTED,
        "NEW": OrderStatus.ACCEPTED,
        "ALL_DONE": OrderStatus.FILLED,
        "REJECT": OrderStatus.REJECTED,
        "REJECTED": OrderStatus.REJECTED,
        "CANCELED": OrderStatus.CANCELLED,
        "CANCELLED": OrderStatus.CANCELLED,
    }
    return mapping.get(status, OrderStatus.ACCEPTED)


def _classified_protective_error_result(
    exchange: str,
    exc: BaseException,
    request: BrokerProtectiveOrderRequest,
) -> BrokerProtectiveOrderResult:
    category = classify_exchange_error(exchange, exc)
    status = (
        OrderStatus.REJECTED
        if category in {ExchangeErrorCategory.FATAL, ExchangeErrorCategory.CREDENTIAL_CONFIGURATION}
        else OrderStatus.UNKNOWN
    )
    return _protective_rejection(
        request,
        category.value,
        str(exc),
        exchange_error_category=category,
        status=status,
    )


def _classified_error_result(
    exchange: str,
    exc: BaseException,
    *,
    fallback_request: Optional[BrokerOrderRequest] = None,
    fallback_client_order_id: Optional[str] = None,
    symbol: str = "",
) -> BrokerOrderResult:
    category = classify_exchange_error(exchange, exc)
    message = str(exc)
    status = (
        OrderStatus.REJECTED
        if category in {ExchangeErrorCategory.FATAL, ExchangeErrorCategory.CREDENTIAL_CONFIGURATION}
        else OrderStatus.UNKNOWN
    )
    if fallback_request is not None:
        return BrokerOrderResult(
            client_order_id=fallback_request.client_order_id,
            symbol=fallback_request.symbol,
            side=fallback_request.side,
            status=status,
            requested_qty=fallback_request.quantity,
            rejection_code=category.value,
            rejection_reason=message,
            exchange_error_category=category,
            exchange_error_message=message,
            trace=fallback_request.trace,
        )
    return BrokerOrderResult(
        client_order_id=fallback_client_order_id or "",
        symbol=symbol,
        side="buy",
        status=status,
        requested_qty=0.0,
        rejection_code=category.value,
        rejection_reason=message,
        exchange_error_category=category,
        exchange_error_message=message,
    )


def classify_exchange_error(exchange: str, exc: BaseException) -> ExchangeErrorCategory:
    text = str(exc).lower()
    if any(token in text for token in ("api key", "apikey", "signature", "unauthorized", "permission", "credential")):
        return ExchangeErrorCategory.CREDENTIAL_CONFIGURATION
    if any(token in text for token in ("timeout", "temporarily", "rate limit", "too many requests", "429", "5xx")):
        return ExchangeErrorCategory.RETRYABLE
    if any(token in text for token in ("connection", "dns", "name or service", "nodename", "proxy", "network")):
        return ExchangeErrorCategory.RETRYABLE
    if any(token in text for token in ("not found", "unknown order", "order does not exist", "already canceled", "already cancelled")):
        return ExchangeErrorCategory.RECOVERABLE
    if exchange == "okx" and any(token in text for token in ("51000", "51008", "51010", "51011", "51131")):
        return ExchangeErrorCategory.FATAL
    if exchange == "binance" and any(token in text for token in ("-1013", "-1100", "-1111", "-2010")):
        return ExchangeErrorCategory.FATAL
    return ExchangeErrorCategory.FATAL


def _category_for_exchange_rejection(
    exchange: str,
    code: Optional[str],
    reason: Optional[str],
) -> Optional[ExchangeErrorCategory]:
    if code is None and not reason:
        return None
    return classify_exchange_error(exchange, RuntimeError(f"{code or ''} {reason or ''}".strip()))


class LiveExecutionEngine:
    """Execute strategy decision dictionaries through an exchange adapter."""

    def __init__(
        self,
        adapter: Optional[object] = None,
        *,
        max_retries: int = 2,
        backoff_base: float = 0.25,
        logger: Optional[logging.Logger] = None,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.adapter = adapter or OKXAdapter()
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.logger = logger or LOGGER
        self._orders_by_idempotency_key: Dict[str, Dict[str, Any]] = {}

    def execute(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_decision(decision)
        idempotency_key = self._idempotency_key(normalized)

        existing = self._orders_by_idempotency_key.get(idempotency_key)
        if existing is not None:
            self.logger.info(
                "live_execution_duplicate idempotency_key=%s symbol=%s",
                idempotency_key,
                normalized["symbol"],
            )
            result = dict(existing)
            result["idempotent_replay"] = True
            return result

        self.logger.info(
            "live_execution_order_before idempotency_key=%s symbol=%s side=%s size=%s type=%s",
            idempotency_key,
            normalized["symbol"],
            normalized["side"],
            normalized["size"],
            normalized["order_type"],
        )

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = self.adapter.place_order(
                    symbol=normalized["symbol"],
                    side=normalized["side"],
                    size=normalized["size"],
                    type=normalized["order_type"],
                    client_order_id=idempotency_key,
                    target_currency=normalized["target_currency"],
                )
                result = self._normalize_order_result(raw, normalized, idempotency_key, attempt)
                self._orders_by_idempotency_key[idempotency_key] = result
                self.logger.info(
                    "live_execution_order_after idempotency_key=%s symbol=%s status=%s order_id=%s retries=%s",
                    idempotency_key,
                    normalized["symbol"],
                    result["status"],
                    result.get("order_id"),
                    attempt,
                )
                return result
            except Exception as exc:
                last_error = exc
                self.logger.warning(
                    "live_execution_order_error idempotency_key=%s symbol=%s attempt=%s error=%s",
                    idempotency_key,
                    normalized["symbol"],
                    attempt,
                    exc,
                )
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff_base * (2 ** attempt))

        raise LiveExecutionError(f"live order failed after retries: {last_error}") from last_error

    def _normalize_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(decision, dict):
            raise LiveExecutionError("decision must be a dict")

        symbol = self._required(decision, "symbol")
        action = str(decision.get("action", decision.get("side", ""))).strip().lower()
        side = self._normalize_side(action)
        order_type = str(decision.get("order_type", decision.get("type", "market"))).strip().lower()
        size = self._required(decision, "size")
        numeric_size = float(size)
        if numeric_size <= 0.0:
            raise LiveExecutionError("decision size must be greater than 0")

        return {
            "symbol": str(symbol).strip().upper(),
            "side": side,
            "order_type": order_type,
            "size": size,
            "numeric_size": numeric_size,
            "client_order_id": decision.get("client_order_id")
            or decision.get("order_id")
            or decision.get("idempotency_key"),
            "target_currency": decision.get("target_currency")
            or decision.get("tgtCcy")
            or self._default_target_currency(side, order_type),
            "raw_decision": dict(decision),
        }

    def _normalize_order_result(
        self,
        raw: Dict[str, Any],
        decision: Dict[str, Any],
        idempotency_key: str,
        retry_count: int,
    ) -> Dict[str, Any]:
        items = raw.get("data") if isinstance(raw, dict) else None
        item = items[0] if items else {}
        status = self._status_from_item(item)
        filled_size = self._filled_size(item)
        remaining_size = max(decision["numeric_size"] - filled_size, 0.0)

        return {
            "success": bool(raw.get("success", False)) if isinstance(raw, dict) else False,
            "exchange": "okx",
            "idempotency_key": idempotency_key,
            "client_order_id": item.get("clOrdId") or idempotency_key,
            "order_id": item.get("ordId"),
            "symbol": decision["symbol"],
            "side": decision["side"],
            "order_type": decision["order_type"],
            "size": decision["numeric_size"],
            "filled_size": filled_size,
            "remaining_size": remaining_size,
            "partial": status == "partial",
            "status": status,
            "retry_count": retry_count,
            "raw": raw,
        }

    def _status_from_item(self, item: Dict[str, Any]) -> str:
        state = str(item.get("state") or item.get("status") or "").strip().lower()
        s_code = str(item.get("sCode", "0"))
        if state in {"partially_filled", "partially-filled", "partial"}:
            return "partial"
        if state in {"filled", "canceled", "cancelled", "live"}:
            return "cancelled" if state == "canceled" else state
        if s_code != "0":
            return "rejected"
        return "accepted"

    def _filled_size(self, item: Dict[str, Any]) -> float:
        for key in ("accFillSz", "fillSz", "filled_size", "filledSz"):
            value = item.get(key)
            if value not in (None, ""):
                return float(value)
        return 0.0

    def _idempotency_key(self, decision: Dict[str, Any]) -> str:
        if decision.get("client_order_id"):
            return self._okx_client_order_id(str(decision["client_order_id"]))
        payload = {
            "symbol": decision["symbol"],
            "side": decision["side"],
            "order_type": decision["order_type"],
            "size": str(decision["size"]),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"sqtf{digest[:28]}"

    def _okx_client_order_id(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9]", "", value)
        if not cleaned:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            return f"sqtf{digest[:28]}"
        if len(cleaned) <= 32:
            return cleaned
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"{cleaned[:12]}{digest[:20]}"

    def _normalize_side(self, action: str) -> str:
        mapping = {
            "buy": "buy",
            "long": "buy",
            "open_long": "buy",
            "sell": "sell",
            "short": "sell",
            "close": "sell",
            "close_long": "sell",
        }
        try:
            return mapping[action]
        except KeyError as exc:
            raise LiveExecutionError("decision action must be buy/sell/long/short/close") from exc

    def _default_target_currency(self, side: str, order_type: str) -> Optional[str]:
        if order_type == "market" and side in {"buy", "sell"}:
            return "base_ccy"
        return None

    def _required(self, decision: Dict[str, Any], key: str) -> Any:
        value = decision.get(key)
        if value in (None, ""):
            raise LiveExecutionError(f"decision requires {key}")
        return value
