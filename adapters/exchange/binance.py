import hashlib
import hmac
import json
import logging
import os
import time
from decimal import Decimal
from typing import Any, Dict, Optional
from urllib import error, parse, request

from quant.proxy import build_proxy_opener, proxy_enabled, proxy_url
from quant.schemas.execution import InstrumentOrderRules


LOGGER = logging.getLogger(__name__)


class BinanceAdapterError(RuntimeError):
    """Raised when a Binance REST request fails."""


class BinanceAdapter:
    """Small Binance Spot REST adapter with signed order endpoints."""

    BASE_URL = "https://api.binance.com"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        base_url: str = BASE_URL,
        timeout: float = 10.0,
        recv_window: int = 5000,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        min_request_interval: float = 0.1,
        use_proxy: Optional[bool] = None,
        proxy: Optional[str] = None,
        require_credentials: bool = True,
        logger: Optional[logging.Logger] = None,
    ):
        self.api_key = self._clean_secret(api_key if api_key is not None else os.getenv("BINANCE_API_KEY"))
        self.secret = self._clean_secret(secret if secret is not None else os.getenv("BINANCE_SECRET"))
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.recv_window = recv_window
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.min_request_interval = min_request_interval
        self.logger = logger or LOGGER
        self._last_request_at = 0.0
        self._order_symbols: Dict[str, str] = {}
        self.use_proxy = proxy_enabled() if use_proxy is None else use_proxy
        self.proxy_url = proxy or proxy_url()
        self._opener = build_proxy_opener(self.proxy_url) if self.use_proxy else None

        self.require_credentials = require_credentials
        if self.require_credentials:
            self._validate_credentials()

    def place_order(
        self,
        symbol: str,
        side: str,
        size: Any,
        type: str,
        client_order_id: Optional[str] = None,
        price: Optional[Any] = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        if reduce_only:
            raise BinanceAdapterError("Binance spot orders do not support reduce_only")

        payload = {
            "symbol": self._normalize_symbol(symbol),
            "side": self._normalize_side(side),
            "type": self._normalize_order_type(type),
            "quantity": self._format_decimal(size),
        }
        if client_order_id:
            payload["newClientOrderId"] = str(client_order_id)
        if payload["type"] == "LIMIT":
            payload["price"] = self._format_decimal(price)
            payload["timeInForce"] = self._normalize_time_in_force(time_in_force)

        response = self._request("POST", "/api/v3/order", params=payload)
        self._remember_order_symbol(response)
        return response

    def cancel_order(self, client_order_id: str) -> Dict[str, Any]:
        symbol = self._resolve_order_symbol(client_order_id)
        response = self._request(
            "DELETE",
            "/api/v3/order",
            params={"symbol": symbol, "origClientOrderId": client_order_id},
        )
        self._remember_order_symbol(response)
        return response

    def cancel_replace_order(
        self,
        *,
        symbol: str,
        side: str,
        size: Any,
        type: str,
        original_client_order_id: str,
        replacement_client_order_id: str,
        price: Optional[Any] = None,
        time_in_force: str = "GTC",
        cancel_replace_mode: str = "STOP_ON_FAILURE",
    ) -> Dict[str, Any]:
        payload = {
            "symbol": self._normalize_symbol(symbol),
            "side": self._normalize_side(side),
            "type": self._normalize_order_type(type),
            "quantity": self._format_decimal(size),
            "cancelReplaceMode": self._normalize_cancel_replace_mode(cancel_replace_mode),
            "cancelOrigClientOrderId": str(original_client_order_id),
            "newClientOrderId": str(replacement_client_order_id),
        }
        if payload["type"] == "LIMIT":
            payload["price"] = self._format_decimal(price)
            payload["timeInForce"] = self._normalize_time_in_force(time_in_force)

        response = self._request("POST", "/api/v3/order/cancelReplace", params=payload)
        self._remember_order_symbol(response)
        return response

    def place_oco_order(
        self,
        *,
        symbol: str,
        side: str,
        size: Any,
        client_order_id: Optional[str] = None,
        stop_loss_price: Optional[Any] = None,
        take_profit_price: Optional[Any] = None,
        stop_loss_client_order_id: Optional[str] = None,
        take_profit_client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if stop_loss_price is None or take_profit_price is None:
            raise BinanceAdapterError("Binance spot OCO requires stop_loss_price and take_profit_price")

        normalized_side = self._normalize_side(side)
        payload = {
            "symbol": self._normalize_symbol(symbol),
            "side": normalized_side,
            "quantity": self._format_decimal(size),
        }
        if client_order_id:
            payload["listClientOrderId"] = str(client_order_id)

        if normalized_side == "SELL":
            payload.update(
                {
                    "aboveType": "LIMIT_MAKER",
                    "abovePrice": self._format_decimal(take_profit_price),
                    "belowType": "STOP_LOSS",
                    "belowStopPrice": self._format_decimal(stop_loss_price),
                }
            )
            if take_profit_client_order_id:
                payload["aboveClientOrderId"] = str(take_profit_client_order_id)
            if stop_loss_client_order_id:
                payload["belowClientOrderId"] = str(stop_loss_client_order_id)
        else:
            payload.update(
                {
                    "aboveType": "STOP_LOSS",
                    "aboveStopPrice": self._format_decimal(stop_loss_price),
                    "belowType": "LIMIT_MAKER",
                    "belowPrice": self._format_decimal(take_profit_price),
                }
            )
            if stop_loss_client_order_id:
                payload["aboveClientOrderId"] = str(stop_loss_client_order_id)
            if take_profit_client_order_id:
                payload["belowClientOrderId"] = str(take_profit_client_order_id)

        response = self._request("POST", "/api/v3/orderList/oco", params=payload)
        self._remember_order_symbol(response)
        return response

    def get_order(self, client_order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        resolved_symbol = self._resolve_order_symbol(client_order_id, symbol)
        return self._request(
            "GET",
            "/api/v3/order",
            params={"symbol": resolved_symbol, "origClientOrderId": client_order_id},
        )

    def list_open_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        params = {"symbol": self._normalize_symbol(symbol)} if symbol else {}
        return self._request("GET", "/api/v3/openOrders", params=params)

    def get_account(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v3/account")

    def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        params = {"symbol": self._normalize_symbol(symbol)} if symbol else None
        return self._public_request("GET", "/api/v3/exchangeInfo", params=params)

    def get_instrument_rules(self, symbol: str) -> InstrumentOrderRules:
        normalized_symbol = self._normalize_symbol(symbol)
        response = self.get_exchange_info(normalized_symbol)
        symbols = response.get("data") or []
        symbol_info = symbols[0] if symbols else {}
        filters = {
            item.get("filterType"): item
            for item in symbol_info.get("filters", [])
            if isinstance(item, dict)
        }
        lot_size = filters.get("LOT_SIZE", {})
        price_filter = filters.get("PRICE_FILTER", {})
        min_notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
        return InstrumentOrderRules(
            symbol=symbol_info.get("symbol") or normalized_symbol,
            quantity_step=self._float_filter(lot_size, "stepSize", default=1.0),
            min_quantity=self._float_filter(lot_size, "minQty", default=0.0),
            max_quantity=self._optional_float_filter(lot_size, "maxQty"),
            price_tick=self._optional_float_filter(price_filter, "tickSize"),
            min_notional=self._float_filter(min_notional_filter, "minNotional", default=0.0),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        method = method.upper()
        request_params = dict(params or {})
        request_params["recvWindow"] = self.recv_window
        request_params["timestamp"] = int(time.time() * 1000)
        request_params["signature"] = self._sign(request_params)
        query = parse.urlencode(request_params)
        url = f"{self.base_url}{path}?{query}"

        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            self._apply_rate_limit()
            self._log_request(method, path, attempt)
            http_request = request.Request(url, headers=self._headers(), method=method)
            try:
                opener = self._opener.open if self._opener is not None else request.urlopen
                with opener(http_request, timeout=self.timeout) as response:
                    payload = self._decode_response(response.read())
                    return self._handle_payload(payload, method, path)
            except error.HTTPError as exc:
                last_error = exc
                payload = self._decode_error_response(exc.read())
                if not self._should_retry_http(exc.code, attempt):
                    raise BinanceAdapterError(self._format_http_error(exc.code, payload)) from exc
                self._sleep_before_retry(attempt, retry_after=exc.headers.get("Retry-After"))
            except error.URLError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self._sleep_before_retry(attempt)

        raise BinanceAdapterError(f"Binance request failed after retries: {last_error}") from last_error

    def _public_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        method = method.upper()
        query = parse.urlencode(params or {})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            self._apply_rate_limit()
            self._log_request(method, path, attempt)
            http_request = request.Request(url, headers=self._public_headers(), method=method)
            try:
                opener = self._opener.open if self._opener is not None else request.urlopen
                with opener(http_request, timeout=self.timeout) as response:
                    payload = self._decode_response(response.read())
                    return self._handle_payload(payload, method, path)
            except error.HTTPError as exc:
                last_error = exc
                payload = self._decode_error_response(exc.read())
                if not self._should_retry_http(exc.code, attempt):
                    raise BinanceAdapterError(self._format_http_error(exc.code, payload)) from exc
                self._sleep_before_retry(attempt, retry_after=exc.headers.get("Retry-After"))
            except error.URLError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self._sleep_before_retry(attempt)

        raise BinanceAdapterError(f"Binance public request failed after retries: {last_error}") from last_error

    def _handle_payload(self, payload: Any, method: str, path: str) -> Dict[str, Any]:
        data = payload if isinstance(payload, list) else [payload]
        return {
            "success": True,
            "exchange": "binance",
            "method": method,
            "path": path,
            "data": data,
            "raw": payload,
        }

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SmartQTF/1.0",
            "X-MBX-APIKEY": self.api_key,
        }

    def _public_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SmartQTF/1.0",
        }

    def _sign(self, params: Dict[str, Any]) -> str:
        query = parse.urlencode(params)
        return hmac.new(self.secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()

    def _apply_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _sleep_before_retry(self, attempt: int, retry_after: Optional[str] = None) -> None:
        if retry_after:
            try:
                time.sleep(float(retry_after))
                return
            except ValueError:
                pass
        time.sleep(self.backoff_base * (2 ** attempt))

    def _should_retry_http(self, status: int, attempt: int) -> bool:
        return attempt < self.max_retries and (status == 429 or status >= 500)

    def _decode_response(self, raw: bytes) -> Any:
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BinanceAdapterError("Binance returned non-JSON response") from exc

    def _decode_error_response(self, raw: bytes) -> Dict[str, Any]:
        if not raw:
            return {}
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            text = raw.decode("utf-8", errors="replace").strip()
            return {"code": "non_json", "msg": text[:240] if text else "HTTP error with empty body"}
        return decoded if isinstance(decoded, dict) else {"code": "unknown", "msg": str(decoded)}

    def _format_http_error(self, status: int, payload: Dict[str, Any]) -> str:
        code = payload.get("code", "unknown")
        message = payload.get("msg", "HTTP error")
        return f"Binance HTTP error {status}, business code {code}: {message}"

    def _log_request(self, method: str, path: str, attempt: int) -> None:
        self.logger.info(
            "binance_request method=%s path=%s attempt=%s proxy=%s",
            method,
            path,
            attempt,
            "on" if self.use_proxy else "off",
        )

    def _validate_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("BINANCE_API_KEY", self.api_key),
                ("BINANCE_SECRET", self.secret),
            )
            if not value
        ]
        if missing:
            raise BinanceAdapterError(f"missing Binance credentials: {', '.join(missing)}")

        invalid = [
            name
            for name, value in (
                ("BINANCE_API_KEY", self.api_key),
                ("BINANCE_SECRET", self.secret),
            )
            if not self._is_header_safe(value)
        ]
        if invalid:
            raise BinanceAdapterError(
                "Binance credentials contain characters that cannot be sent in HTTP headers: "
                + ", ".join(invalid)
            )

    def _clean_secret(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        quote_pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
        if len(cleaned) >= 2:
            expected_end = quote_pairs.get(cleaned[0])
            if expected_end is not None and cleaned[-1] == expected_end:
                return cleaned[1:-1].strip()
        return cleaned

    def _is_header_safe(self, value: str) -> bool:
        try:
            value.encode("latin-1")
        except UnicodeEncodeError:
            return False
        return True

    def _remember_order_symbol(self, response: Dict[str, Any]) -> None:
        items = list(response.get("data", []))
        for item in response.get("data", []):
            for key in ("orders", "orderReports"):
                nested = item.get(key)
                if isinstance(nested, list):
                    items.extend(nested)
        for item in items:
            symbol = item.get("symbol")
            order_id = item.get("orderId")
            client_order_id = item.get("clientOrderId")
            list_client_order_id = item.get("listClientOrderId")
            if symbol and order_id is not None:
                self._order_symbols[str(order_id)] = symbol
            if symbol and client_order_id:
                self._order_symbols[str(client_order_id)] = symbol
            if symbol and list_client_order_id:
                self._order_symbols[str(list_client_order_id)] = symbol

    def _resolve_order_symbol(self, client_order_id: str, symbol: Optional[str] = None) -> str:
        if symbol:
            return self._normalize_symbol(symbol)
        resolved = self._order_symbols.get(client_order_id) or os.getenv("BINANCE_DEFAULT_SYMBOL")
        if not resolved:
            raise BinanceAdapterError(
                "order lookup requires a known client order id from place_order, "
                "BINANCE_DEFAULT_SYMBOL, or an explicit symbol"
            )
        return self._normalize_symbol(resolved)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).strip().upper().replace("-", "").replace("_", "")

    def _normalize_side(self, side: str) -> str:
        side = str(getattr(side, "value", side)).strip().lower()
        aliases = {"buy": "BUY", "sell": "SELL", "long": "BUY", "short": "SELL"}
        if side not in aliases:
            raise BinanceAdapterError("side must be buy/sell/long/short")
        return aliases[side]

    def _normalize_order_type(self, order_type: str) -> str:
        order_type = str(getattr(order_type, "value", order_type)).strip().lower()
        aliases = {"market": "MARKET", "limit": "LIMIT"}
        if order_type not in aliases:
            raise BinanceAdapterError("type must be market/limit")
        return aliases[order_type]

    def _normalize_time_in_force(self, time_in_force: str) -> str:
        value = str(getattr(time_in_force, "value", time_in_force)).strip().upper()
        if value not in {"GTC", "IOC", "FOK"}:
            raise BinanceAdapterError("time_in_force must be GTC/IOC/FOK")
        return value

    def _normalize_cancel_replace_mode(self, mode: str) -> str:
        value = str(mode).strip().upper()
        if value not in {"STOP_ON_FAILURE", "ALLOW_FAILURE"}:
            raise BinanceAdapterError("cancel_replace_mode must be STOP_ON_FAILURE/ALLOW_FAILURE")
        return value

    def _format_decimal(self, value: Any) -> str:
        if value is None:
            raise BinanceAdapterError("numeric order field is required")
        text = format(Decimal(str(value)), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    def _float_filter(self, source: Dict[str, Any], key: str, *, default: float) -> float:
        value = self._optional_float_filter(source, key)
        return default if value is None else value

    def _optional_float_filter(self, source: Dict[str, Any], key: str) -> Optional[float]:
        value = source.get(key)
        if value in (None, ""):
            return None
        return float(value)
