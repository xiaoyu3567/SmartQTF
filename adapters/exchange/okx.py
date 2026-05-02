import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, parse, request

from quant.proxy import build_proxy_opener, proxy_enabled, proxy_url
from quant.data.schemas.market import Kline, KlineBatch, Trade
from quant.schemas import (
    FundingRateSnapshot,
    NetflowSnapshot,
    OpenInterestSnapshot,
    OrderBookLevel,
    OrderBookSnapshot,
    UniverseInstrument,
)
from quant.schemas.execution import InstrumentOrderRules


LOGGER = logging.getLogger(__name__)


class OKXAdapterError(RuntimeError):
    """Raised when an OKX request fails after retry handling."""


class OKXAdapter:
    """Small OKX REST v5 adapter with signed requests and normalized dict output."""

    BASE_URL = "https://www.okx.com"
    RATE_LIMIT_CODE = "50011"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        base_url: str = BASE_URL,
        simulated: Optional[bool] = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        min_request_interval: float = 0.22,
        use_proxy: Optional[bool] = None,
        proxy: Optional[str] = None,
        require_credentials: bool = True,
        logger: Optional[logging.Logger] = None,
    ):
        self.api_key = self._clean_secret(api_key if api_key is not None else os.getenv("OKX_API_KEY"))
        self.secret = self._clean_secret(secret if secret is not None else os.getenv("OKX_SECRET"))
        self.passphrase = self._clean_secret(
            passphrase if passphrase is not None else os.getenv("OKX_PASSPHRASE")
        )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.min_request_interval = min_request_interval
        self.logger = logger or LOGGER
        self._last_request_at = 0.0
        self._order_symbols: Dict[str, str] = {}
        self.use_proxy = proxy_enabled() if use_proxy is None else use_proxy
        self.proxy_url = proxy or proxy_url()
        self._opener = build_proxy_opener(self.proxy_url) if self.use_proxy else None

        if simulated is None:
            simulated = os.getenv("OKX_SIMULATED_TRADING", "").strip().lower() in {
                "1",
                "true",
                "yes",
            }
        self.simulated = simulated

        self.require_credentials = require_credentials
        if self.require_credentials:
            self._validate_credentials()

    def get_balance(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v5/account/balance")

    def place_order(
        self,
        symbol: str,
        side: str,
        size: Any,
        type: str,
        client_order_id: Optional[str] = None,
        price: Optional[Any] = None,
        td_mode: str = "cash",
        target_currency: Optional[str] = None,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        payload = {
            "instId": self._normalize_symbol(symbol),
            "tdMode": td_mode,
            "side": self._normalize_side(side),
            "ordType": self._normalize_order_type(type),
            "sz": self._format_decimal(size),
        }
        if client_order_id:
            payload["clOrdId"] = str(client_order_id)
        if price is not None:
            payload["px"] = self._format_decimal(price)
        if target_currency:
            payload["tgtCcy"] = str(target_currency)
        if reduce_only:
            payload["reduceOnly"] = "true"
        response = self._request("POST", "/api/v5/trade/order", body=payload)
        self._remember_order_symbol(response, payload["instId"])
        return response

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        inst_id, resolved_order_id = self._resolve_cancel_target(order_id)
        payload = {"instId": inst_id, "ordId": resolved_order_id}
        return self._request("POST", "/api/v5/trade/cancel-order", body=payload)

    def amend_order(
        self,
        symbol: str,
        *,
        client_order_id: str,
        new_size: Optional[Any] = None,
        new_price: Optional[Any] = None,
        request_id: Optional[str] = None,
        cancel_on_fail: bool = False,
    ) -> Dict[str, Any]:
        if new_size is None and new_price is None:
            raise OKXAdapterError("amend_order requires new_size or new_price")

        inst_id = self._normalize_symbol(symbol)
        payload: Dict[str, Any] = {
            "instId": inst_id,
            "clOrdId": str(client_order_id),
            "cxlOnFail": "true" if cancel_on_fail else "false",
        }
        if request_id:
            payload["reqId"] = str(request_id)
        if new_size is not None:
            payload["newSz"] = self._format_decimal(new_size)
        if new_price is not None:
            payload["newPx"] = self._format_decimal(new_price)

        response = self._request("POST", "/api/v5/trade/amend-order", body=payload)
        self._remember_order_symbol(response, inst_id)
        return response

    def place_protective_order(
        self,
        symbol: str,
        *,
        side: str,
        size: Any,
        client_order_id: Optional[str] = None,
        stop_loss_price: Optional[Any] = None,
        take_profit_price: Optional[Any] = None,
        td_mode: str = "cash",
        target_currency: Optional[str] = None,
        reduce_only: bool = True,
        trigger_price_type: str = "last",
    ) -> Dict[str, Any]:
        if stop_loss_price is None and take_profit_price is None:
            raise OKXAdapterError("place_protective_order requires stop_loss_price or take_profit_price")

        inst_id = self._normalize_symbol(symbol)
        payload: Dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": self._normalize_side(side),
            "ordType": "oco" if take_profit_price is not None else "conditional",
            "sz": self._format_decimal(size),
        }
        if client_order_id:
            payload["algoClOrdId"] = str(client_order_id)
        if target_currency:
            payload["tgtCcy"] = str(target_currency)
        if reduce_only:
            payload["reduceOnly"] = "true"
        if take_profit_price is not None:
            payload["tpTriggerPx"] = self._format_decimal(take_profit_price)
            payload["tpOrdPx"] = "-1"
            payload["tpTriggerPxType"] = str(trigger_price_type)
        if stop_loss_price is not None:
            payload["slTriggerPx"] = self._format_decimal(stop_loss_price)
            payload["slOrdPx"] = "-1"
            payload["slTriggerPxType"] = str(trigger_price_type)

        response = self._request("POST", "/api/v5/trade/order-algo", body=payload)
        self._remember_order_symbol(response, inst_id)
        return response

    def get_order(self, client_order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        inst_id = self._resolve_order_symbol(client_order_id, symbol)
        return self._request(
            "GET",
            "/api/v5/trade/order",
            params={"instId": inst_id, "clOrdId": client_order_id},
        )

    def list_open_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        params = {"instId": self._normalize_symbol(symbol)} if symbol else None
        return self._request("GET", "/api/v5/trade/orders-pending", params=params)

    def get_positions(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v5/account/positions")

    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        *,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: int = 100,
    ) -> KlineBatch:
        params: Dict[str, Any] = {
            "instId": self._normalize_symbol(symbol),
            "bar": self._normalize_timeframe(timeframe),
            "limit": limit,
        }
        if start_ts is not None:
            params["after"] = self._to_milliseconds(start_ts)
        if end_ts is not None:
            params["before"] = self._to_milliseconds(end_ts)

        response = self._request("GET", "/api/v5/market/candles", params=params, auth=False)
        klines = [self._parse_kline(item) for item in response.get("data", [])]
        return KlineBatch(
            symbol=params["instId"],
            timeframe=timeframe,
            venue="okx",
            klines=sorted(klines, key=lambda kline: kline.timestamp),
        )

    def get_trades(self, symbol: str, *, limit: int = 100) -> List[Trade]:
        params = {
            "instId": self._normalize_symbol(symbol),
            "limit": limit,
        }
        response = self._request("GET", "/api/v5/market/trades", params=params, auth=False)
        trades = [self._parse_trade(item) for item in response.get("data", [])]
        return sorted(trades, key=lambda trade: trade.timestamp)

    def get_history_trades(
        self,
        symbol: str,
        *,
        limit: int = 100,
        after_ts: Optional[int] = None,
    ) -> List[Trade]:
        params: Dict[str, Any] = {
            "instId": self._normalize_symbol(symbol),
            "limit": min(max(int(limit), 1), 100),
        }
        if after_ts is not None:
            params["type"] = "2"
            params["after"] = self._to_milliseconds(after_ts)

        response = self._request("GET", "/api/v5/market/history-trades", params=params, auth=False)
        trades = [self._parse_trade(item) for item in response.get("data", [])]
        return sorted(trades, key=lambda trade: trade.timestamp)

    def get_orderbook(self, symbol: str, *, depth: int = 20) -> OrderBookSnapshot:
        if depth <= 0:
            raise OKXAdapterError("orderbook depth must be > 0")

        inst_id = self._normalize_symbol(symbol)
        response = self._request(
            "GET",
            "/api/v5/market/books",
            params={"instId": inst_id, "sz": depth},
            auth=False,
        )
        data = self._first_data_item(response, "orderbook")
        timestamp = self._from_milliseconds(data.get("ts"))
        bids = sorted(
            [self._parse_orderbook_level(item) for item in data.get("bids", [])],
            key=lambda level: level.price,
            reverse=True,
        )
        asks = sorted(
            [self._parse_orderbook_level(item) for item in data.get("asks", [])],
            key=lambda level: level.price,
        )
        return OrderBookSnapshot(
            snapshot_id=f"okx-book-{inst_id}-{timestamp}",
            timestamp=timestamp,
            symbol=inst_id,
            venue="okx",
            as_of_timestamp=timestamp,
            bids=bids,
            asks=asks,
            depth=depth,
        )

    def get_netflow(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        limit: int = 100,
        max_history_pages: int = 20,
    ) -> NetflowSnapshot:
        inst_id = self._normalize_symbol(symbol)
        window_seconds = self._timeframe_seconds(timeframe)
        recent_limit = min(max(int(limit), 1), 500)
        history_page_limit = min(max(int(limit), 1), 100)
        trades = self.get_trades(inst_id, limit=recent_limit)
        if not trades:
            raise OKXAdapterError("OKX returned empty trade data for netflow")

        timestamp = trades[-1].timestamp
        window_start = timestamp - window_seconds + 1
        window_end = timestamp
        coverage_gap_reason: Optional[str] = None

        collected = self._deduplicate_trades(trades)
        history_pages = 0
        while collected and collected[0].timestamp > window_start and history_pages < max_history_pages:
            history_page = self.get_history_trades(
                inst_id,
                limit=history_page_limit,
                after_ts=collected[0].timestamp,
            )
            history_pages += 1
            if not history_page:
                coverage_gap_reason = "history_exhausted_before_window_start"
                break

            previous_earliest = collected[0].timestamp
            collected = self._deduplicate_trades(collected + history_page)
            if collected[0].timestamp >= previous_earliest:
                coverage_gap_reason = "history_pagination_stalled"
                break

        if collected[0].timestamp > window_start and coverage_gap_reason is None:
            coverage_gap_reason = "history_page_limit_reached"

        window_trades = [
            trade for trade in collected if window_start <= trade.timestamp <= window_end
        ]
        if not window_trades:
            raise OKXAdapterError("OKX returned no trade data inside netflow timeframe window")

        inflow = 0.0
        outflow = 0.0
        for trade in window_trades:
            notional = trade.price * trade.size
            if trade.side.lower() == "buy":
                inflow += notional
            elif trade.side.lower() == "sell":
                outflow += notional
            else:
                raise OKXAdapterError("trade side must be buy or sell")

        earliest_collected = collected[0].timestamp
        latest_collected = collected[-1].timestamp
        coverage_complete = earliest_collected <= window_start and latest_collected >= window_end
        coverage_start = max(earliest_collected, window_start)
        coverage_end = min(latest_collected, window_end)
        if coverage_complete:
            coverage_gap_reason = None
        elif coverage_gap_reason is None:
            coverage_gap_reason = "recent_trades_limit_did_not_cover_window"

        return NetflowSnapshot(
            snapshot_id=f"okx-netflow-{inst_id}-{timeframe}-{timestamp}",
            timestamp=timestamp,
            symbol=inst_id,
            venue="okx",
            as_of_timestamp=timestamp,
            timeframe=timeframe,
            inflow=inflow,
            outflow=outflow,
            netflow=inflow - outflow,
            window_start_timestamp=window_start,
            window_end_timestamp=window_end,
            trade_records_in_window=len(window_trades),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            coverage_complete=coverage_complete,
            coverage_gap_reason=coverage_gap_reason,
        )

    def get_open_interest(
        self,
        symbol: str,
        *,
        instrument_type: str = "SWAP",
    ) -> OpenInterestSnapshot:
        inst_id = self._normalize_derivative_symbol(symbol, instrument_type)
        response = self._request(
            "GET",
            "/api/v5/public/open-interest",
            params={"instType": instrument_type.upper(), "instId": inst_id},
            auth=False,
        )
        data = self._first_data_item(response, "open interest")
        timestamp = self._from_milliseconds(data.get("ts"))
        return OpenInterestSnapshot(
            snapshot_id=f"okx-oi-{inst_id}-{timestamp}",
            timestamp=timestamp,
            symbol=inst_id,
            venue="okx",
            as_of_timestamp=timestamp,
            open_interest=self._float_value(data, "oi"),
            open_interest_value=self._optional_float_value(data, "oiCcy"),
        )

    def get_funding_rate(self, symbol: str) -> FundingRateSnapshot:
        inst_id = self._normalize_derivative_symbol(symbol, "SWAP")
        response = self._request(
            "GET",
            "/api/v5/public/funding-rate",
            params={"instId": inst_id},
            auth=False,
        )
        data = self._first_data_item(response, "funding rate")
        timestamp = self._from_milliseconds(data.get("ts") or data.get("fundingTime"))
        funding_timestamp = self._optional_timestamp(data, "fundingTime")
        next_funding_timestamp = self._optional_timestamp(data, "nextFundingTime")
        return FundingRateSnapshot(
            snapshot_id=f"okx-funding-{inst_id}-{timestamp}",
            timestamp=timestamp,
            symbol=inst_id,
            venue="okx",
            as_of_timestamp=timestamp,
            funding_rate=self._float_value(data, "fundingRate"),
            funding_timestamp=funding_timestamp,
            next_funding_timestamp=next_funding_timestamp,
        )

    def get_instrument_rules(
        self,
        symbol: str,
        *,
        instrument_type: str = "SPOT",
    ) -> InstrumentOrderRules:
        inst_id = self._normalize_symbol(symbol)
        response = self._request(
            "GET",
            "/api/v5/public/instruments",
            params={"instType": instrument_type.upper(), "instId": inst_id},
            auth=False,
        )
        data = self._first_data_item(response, "instrument rules")
        return InstrumentOrderRules(
            symbol=data.get("instId") or inst_id,
            quantity_step=self._float_value(data, "lotSz"),
            min_quantity=self._optional_float_value(data, "minSz") or 0.0,
            max_quantity=self._optional_float_value(data, "maxMktSz")
            or self._optional_float_value(data, "maxLmtSz"),
            price_tick=self._optional_float_value(data, "tickSz"),
            min_notional=self._optional_float_value(data, "minNotional")
            or self._optional_float_value(data, "minNotionalUsd")
            or 0.0,
        )

    def get_market_tickers(self, *, instrument_type: str = "SPOT") -> List[Dict[str, Any]]:
        response = self._request(
            "GET",
            "/api/v5/market/tickers",
            params={"instType": instrument_type.upper()},
            auth=False,
        )
        return [item for item in response.get("data", []) if isinstance(item, dict)]

    def get_universe_instruments(
        self,
        *,
        instrument_type: str = "SPOT",
        include_tickers: bool = True,
    ) -> List[UniverseInstrument]:
        normalized_type = instrument_type.upper()
        response = self._request(
            "GET",
            "/api/v5/public/instruments",
            params={"instType": normalized_type},
            auth=False,
        )
        tickers_by_symbol: Dict[str, Dict[str, Any]] = {}
        if include_tickers:
            tickers_by_symbol = {
                item.get("instId"): item
                for item in self.get_market_tickers(instrument_type=normalized_type)
                if item.get("instId")
            }
        return [
            self._parse_universe_instrument(
                item,
                instrument_type=normalized_type,
                ticker=tickers_by_symbol.get(item.get("instId"), {}),
            )
            for item in response.get("data", [])
            if isinstance(item, dict)
        ]

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        auth: bool = True,
    ) -> Dict[str, Any]:
        method = method.upper()
        query = self._encode_query(params)
        request_path = f"{path}?{query}" if query else path
        url = f"{self.base_url}{request_path}"
        body_text = "" if body is None else json.dumps(body, separators=(",", ":"))

        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            self._apply_rate_limit()
            timestamp = self._timestamp()
            headers = (
                self._headers(timestamp, method, request_path, body_text)
                if auth
                else self._public_headers()
            )
            self._log_request(method, request_path, attempt)

            http_request = request.Request(
                url,
                data=body_text.encode("utf-8") if body_text else None,
                headers=headers,
                method=method,
            )

            try:
                opener = self._opener.open if self._opener is not None else request.urlopen
                with opener(http_request, timeout=self.timeout) as response:
                    payload = self._decode_response(response.read())
                    if self._should_retry_payload(payload, attempt):
                        self._sleep_before_retry(attempt)
                        continue
                    return self._handle_okx_payload(payload, method, request_path)
            except error.HTTPError as exc:
                last_error = exc
                payload = self._decode_error_response(exc.read())
                if not self._should_retry_http(exc.code, payload, attempt):
                    raise OKXAdapterError(self._format_http_error(exc.code, payload)) from exc
                self._sleep_before_retry(attempt, retry_after=exc.headers.get("Retry-After"))
            except error.URLError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self._sleep_before_retry(attempt)

        raise OKXAdapterError(f"OKX request failed after retries: {last_error}") from last_error

    def _handle_okx_payload(self, payload: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
        code = str(payload.get("code", ""))
        if code == "0":
            return {
                "success": True,
                "exchange": "okx",
                "method": method,
                "path": path,
                "code": code,
                "message": payload.get("msg", ""),
                "data": payload.get("data", []),
                "raw": payload,
            }

        message = payload.get("msg", "OKX business error")
        if code == self.RATE_LIMIT_CODE:
            raise OKXAdapterError(f"OKX rate limit error {code}: {message}")
        details = payload.get("data")
        if details:
            raise OKXAdapterError(f"OKX business error {code}: {message}; details={details}")
        raise OKXAdapterError(f"OKX business error {code}: {message}")

    def _headers(self, timestamp: str, method: str, request_path: str, body_text: str) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SmartQTF/1.0",
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": self._sign(timestamp, method, request_path, body_text),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }
        if self.simulated:
            headers["x-simulated-trading"] = "1"
        return headers

    def _public_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SmartQTF/1.0",
        }

    def _sign(self, timestamp: str, method: str, request_path: str, body_text: str) -> str:
        message = f"{timestamp}{method.upper()}{request_path}{body_text}"
        digest = hmac.new(
            self.secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

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

    def _should_retry_http(self, status: int, payload: Dict[str, Any], attempt: int) -> bool:
        if attempt >= self.max_retries:
            return False
        code = str(payload.get("code", ""))
        return status == 429 or status >= 500 or code == self.RATE_LIMIT_CODE

    def _should_retry_payload(self, payload: Dict[str, Any], attempt: int) -> bool:
        if attempt >= self.max_retries:
            return False
        return str(payload.get("code", "")) == self.RATE_LIMIT_CODE

    def _decode_response(self, raw: bytes) -> Dict[str, Any]:
        if not raw:
            return {}
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise OKXAdapterError("OKX returned non-JSON response") from exc
        if not isinstance(decoded, dict):
            raise OKXAdapterError("OKX returned unexpected response payload")
        return decoded

    def _decode_error_response(self, raw: bytes) -> Dict[str, Any]:
        if not raw:
            return {}
        try:
            return self._decode_response(raw)
        except OKXAdapterError:
            text = raw.decode("utf-8", errors="replace").strip()
            return {
                "code": "non_json",
                "msg": text[:240] if text else "HTTP error with empty body",
            }

    def _format_http_error(self, status: int, payload: Dict[str, Any]) -> str:
        code = payload.get("code", "unknown")
        message = payload.get("msg", "HTTP error")
        return f"OKX HTTP error {status}, business code {code}: {message}"

    def _log_request(self, method: str, path: str, attempt: int) -> None:
        self.logger.info(
            "okx_request method=%s path=%s attempt=%s simulated=%s proxy=%s",
            method,
            path,
            attempt,
            self.simulated,
            "on" if self.use_proxy else "off",
        )

    def _validate_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("OKX_API_KEY", self.api_key),
                ("OKX_SECRET", self.secret),
                ("OKX_PASSPHRASE", self.passphrase),
            )
            if not value
        ]
        if missing:
            raise OKXAdapterError(f"missing OKX credentials: {', '.join(missing)}")

        invalid = [
            name
            for name, value in (
                ("OKX_API_KEY", self.api_key),
                ("OKX_SECRET", self.secret),
                ("OKX_PASSPHRASE", self.passphrase),
            )
            if not self._is_header_safe(value)
        ]
        if invalid:
            raise OKXAdapterError(
                "OKX credentials contain characters that cannot be sent in HTTP headers: "
                + ", ".join(invalid)
            )

    def _clean_secret(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        quote_pairs = {
            '"': '"',
            "'": "'",
            "“": "”",
            "‘": "’",
        }
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

    def _remember_order_symbol(self, response: Dict[str, Any], symbol: str) -> None:
        for item in response.get("data", []):
            order_id = item.get("ordId")
            client_order_id = item.get("clOrdId")
            algo_id = item.get("algoId")
            algo_client_order_id = item.get("algoClOrdId")
            if order_id:
                self._order_symbols[order_id] = symbol
            if client_order_id:
                self._order_symbols[client_order_id] = symbol
            if algo_id:
                self._order_symbols[algo_id] = symbol
            if algo_client_order_id:
                self._order_symbols[algo_client_order_id] = symbol

    def _resolve_cancel_target(self, order_id: str) -> Tuple[str, str]:
        if ":" in order_id:
            symbol, resolved_order_id = order_id.split(":", 1)
            return self._normalize_symbol(symbol), resolved_order_id

        inst_id = self._order_symbols.get(order_id) or os.getenv("OKX_DEFAULT_SYMBOL")
        if not inst_id:
            raise OKXAdapterError(
                "cancel_order requires a known order id from place_order, "
                "OKX_DEFAULT_SYMBOL, or 'SYMBOL:ORDER_ID' format"
            )
        return self._normalize_symbol(inst_id), order_id

    def _resolve_order_symbol(self, client_order_id: str, symbol: Optional[str] = None) -> str:
        if symbol:
            return self._normalize_symbol(symbol)
        inst_id = self._order_symbols.get(client_order_id) or os.getenv("OKX_DEFAULT_SYMBOL")
        if not inst_id:
            raise OKXAdapterError(
                "get_order requires a known client order id from place_order, "
                "OKX_DEFAULT_SYMBOL, or an explicit symbol"
            )
        return self._normalize_symbol(inst_id)

    def _encode_query(self, params: Optional[Dict[str, Any]]) -> str:
        if not params:
            return ""
        clean_params = {key: value for key, value in params.items() if value is not None}
        return parse.urlencode(clean_params)

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _normalize_symbol(self, symbol: str) -> str:
        symbol = str(symbol).strip().upper()
        if "-" in symbol:
            return symbol
        if symbol.endswith("USDT") and len(symbol) > 4:
            return f"{symbol[:-4]}-USDT"
        if symbol.endswith("USD") and len(symbol) > 3:
            return f"{symbol[:-3]}-USD"
        return symbol

    def _normalize_derivative_symbol(self, symbol: str, instrument_type: str) -> str:
        normalized = self._normalize_symbol(symbol)
        suffix = f"-{instrument_type.strip().upper()}"
        if normalized.endswith(suffix):
            return normalized
        if normalized.count("-") == 1:
            return f"{normalized}{suffix}"
        return normalized

    def _normalize_timeframe(self, timeframe: str) -> str:
        aliases = {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1H",
            "4h": "4H",
            "1d": "1D",
        }
        try:
            return aliases[timeframe]
        except KeyError as exc:
            raise OKXAdapterError(f"unsupported OKX timeframe: {timeframe}") from exc

    def _timeframe_seconds(self, timeframe: str) -> int:
        seconds = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }
        try:
            return seconds[timeframe]
        except KeyError as exc:
            raise OKXAdapterError(f"unsupported OKX timeframe: {timeframe}") from exc

    def _deduplicate_trades(self, trades: List[Trade]) -> List[Trade]:
        unique: Dict[Tuple[Any, ...], Trade] = {}
        for trade in trades:
            if trade.trade_id:
                key = ("id", trade.trade_id)
            else:
                key = ("raw", trade.timestamp, trade.price, trade.size, trade.side.lower())
            unique[key] = trade
        return sorted(unique.values(), key=lambda trade: trade.timestamp)

    def _normalize_side(self, side: str) -> str:
        side = str(side).strip().lower()
        aliases = {"buy": "buy", "sell": "sell", "long": "buy", "short": "sell"}
        if side not in aliases:
            raise OKXAdapterError("side must be buy/sell/long/short")
        return aliases[side]

    def _normalize_order_type(self, order_type: str) -> str:
        order_type = str(order_type).strip().lower()
        aliases = {
            "market": "market",
            "limit": "limit",
            "post_only": "post_only",
            "fok": "fok",
            "ioc": "ioc",
        }
        if order_type not in aliases:
            raise OKXAdapterError("type must be market/limit/post_only/fok/ioc")
        return aliases[order_type]

    def _format_decimal(self, value: Any) -> str:
        text = format(Decimal(str(value)), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    def _parse_kline(self, item: List[Any]) -> Kline:
        if len(item) < 6:
            raise OKXAdapterError("OKX kline item has fewer than 6 fields")
        return Kline(
            timestamp=self._from_milliseconds(item[0]),
            open=self._float_text(item[1]),
            high=self._float_text(item[2]),
            low=self._float_text(item[3]),
            close=self._float_text(item[4]),
            volume=self._float_text(item[5]),
            is_complete=(str(item[8]).strip() == "1") if len(item) > 8 else None,
        )

    def _parse_trade(self, payload: Dict[str, Any]) -> Trade:
        if not isinstance(payload, dict):
            raise OKXAdapterError("OKX trade item must be an object")
        side = str(payload.get("side") or "").strip().lower()
        if side not in {"buy", "sell"}:
            raise OKXAdapterError("OKX trade side must be buy or sell")
        return Trade(
            timestamp=self._from_milliseconds(payload.get("ts")),
            price=self._float_value(payload, "px"),
            size=self._float_value(payload, "sz"),
            side=side,
            trade_id=str(payload.get("tradeId") or "").strip() or None,
        )

    def _parse_orderbook_level(self, item: List[Any]) -> OrderBookLevel:
        if len(item) < 2:
            raise OKXAdapterError("OKX orderbook level has fewer than 2 fields")
        return OrderBookLevel(
            price=self._float_text(item[0]),
            quantity=self._float_text(item[1]),
        )

    def _parse_universe_instrument(
        self,
        payload: Dict[str, Any],
        *,
        instrument_type: str,
        ticker: Optional[Dict[str, Any]] = None,
    ) -> UniverseInstrument:
        ticker = ticker or {}
        inst_id = str(payload.get("instId") or "").strip().upper()
        if not inst_id:
            raise OKXAdapterError("OKX instrument payload missing instId")
        base_currency, quote_currency = self._split_symbol_currencies(inst_id)
        return UniverseInstrument(
            symbol=inst_id,
            venue="okx",
            instrument_type=str(payload.get("instType") or instrument_type).upper(),
            base_currency=str(payload.get("baseCcy") or base_currency).upper(),
            quote_currency=str(payload.get("quoteCcy") or quote_currency).upper(),
            status=str(payload.get("state") or "unknown").lower(),
            quantity_step=self._optional_float_value(payload, "lotSz") or 0.0,
            min_quantity=self._optional_float_value(payload, "minSz") or 0.0,
            max_quantity=self._optional_float_value(payload, "maxMktSz")
            or self._optional_float_value(payload, "maxLmtSz"),
            price_tick=self._optional_float_value(payload, "tickSz"),
            min_notional=self._optional_float_value(payload, "minNotional")
            or self._optional_float_value(payload, "minNotionalUsd")
            or 0.0,
            volume_24h=self._optional_float_value(ticker, "vol24h"),
            turnover_24h=self._optional_float_value(ticker, "volCcy24h")
            or self._optional_float_value(ticker, "volUsd24h"),
            last_price=self._optional_float_value(ticker, "last"),
            metadata=self._instrument_metadata(payload),
        )

    def _split_symbol_currencies(self, symbol: str) -> Tuple[str, str]:
        parts = symbol.split("-")
        if len(parts) >= 2:
            return parts[0], parts[1]
        return symbol, ""

    def _instrument_metadata(self, payload: Dict[str, Any]) -> Dict[str, str]:
        metadata_fields = (
            "instFamily",
            "uly",
            "settleCcy",
            "ctVal",
            "ctValCcy",
            "listTime",
            "expTime",
            "alias",
        )
        return {
            field: str(payload[field])
            for field in metadata_fields
            if payload.get(field) not in (None, "")
        }

    def _first_data_item(self, response: Dict[str, Any], label: str) -> Dict[str, Any]:
        data = response.get("data") or []
        if not data:
            raise OKXAdapterError(f"OKX returned empty {label} data")
        first = data[0]
        if not isinstance(first, dict):
            raise OKXAdapterError(f"OKX returned invalid {label} data")
        return first

    def _float_value(self, payload: Dict[str, Any], key: str) -> float:
        value = payload.get(key)
        if value in (None, ""):
            raise OKXAdapterError(f"OKX payload missing numeric field: {key}")
        return self._float_text(value)

    def _optional_float_value(self, payload: Dict[str, Any], key: str) -> Optional[float]:
        value = payload.get(key)
        if value in (None, ""):
            return None
        return self._float_text(value)

    def _float_text(self, value: Any) -> float:
        return float(Decimal(str(value)))

    def _to_milliseconds(self, timestamp: int) -> int:
        timestamp = int(timestamp)
        if timestamp > 10_000_000_000:
            return timestamp
        return timestamp * 1000

    def _from_milliseconds(self, value: Any) -> int:
        if value in (None, ""):
            raise OKXAdapterError("OKX payload missing timestamp")
        return int(Decimal(str(value))) // 1000

    def _optional_timestamp(self, payload: Dict[str, Any], key: str) -> Optional[int]:
        value = payload.get(key)
        if value in (None, ""):
            return None
        return self._from_milliseconds(value)
