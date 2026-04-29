#!/usr/bin/env python
import argparse
import json
import os
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.exchange.binance import BinanceAdapter, BinanceAdapterError
from adapters.exchange.okx import OKXAdapter, OKXAdapterError
from quant.proxy import build_proxy_opener, proxy_enabled, proxy_url


PUBLIC_ENDPOINTS = {
    "okx": "https://www.okx.com/api/v5/public/time",
    "binance": "https://api.binance.com/api/v3/time",
}

PRIVATE_CREDENTIALS = {
    "okx": ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE"),
    "binance": ("BINANCE_API_KEY", "BINANCE_SECRET"),
}


@dataclass(frozen=True)
class ConnectivityCheck:
    exchange: str
    scope: str
    status: str
    category: str
    message: str
    latency_ms: float | None = None
    details: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "exchange": self.exchange,
            "scope": self.scope,
            "status": self.status,
            "category": self.category,
            "message": self.message,
        }
        if self.latency_ms is not None:
            payload["latency_ms"] = round(self.latency_ms, 3)
        if self.details:
            payload["details"] = self.details
        return payload


def run_diagnostics(
    *,
    exchanges: list[str] | None = None,
    include_private: bool = False,
    timeout: float = 5.0,
    use_proxy: bool | None = None,
) -> dict[str, Any]:
    selected_exchanges = exchanges or ["okx", "binance"]
    selected_use_proxy = proxy_enabled() if use_proxy is None else use_proxy
    checks: list[ConnectivityCheck] = []

    for exchange in selected_exchanges:
        exchange = exchange.lower()
        if exchange not in PUBLIC_ENDPOINTS:
            checks.append(
                ConnectivityCheck(
                    exchange=exchange,
                    scope="config",
                    status="FAIL",
                    category="configuration",
                    message="unsupported exchange",
                    details={"supported": sorted(PUBLIC_ENDPOINTS)},
                )
            )
            continue

        checks.append(_check_public_endpoint(exchange, timeout=timeout, use_proxy=selected_use_proxy))
        if include_private:
            checks.append(_check_private_endpoint(exchange, timeout=timeout, use_proxy=selected_use_proxy))

    failed = [check for check in checks if check.status == "FAIL"]
    warnings = [check for check in checks if check.status == "WARN"]
    return {
        "success": not failed,
        "failed_count": len(failed),
        "warning_count": len(warnings),
        "proxy": {
            "enabled": selected_use_proxy,
            "url": proxy_url() if selected_use_proxy else None,
            "SMARTQTF_USE_PROXY": os.getenv("SMARTQTF_USE_PROXY"),
        },
        "checks": [check.to_payload() for check in checks],
    }


def _check_public_endpoint(exchange: str, *, timeout: float, use_proxy: bool) -> ConnectivityCheck:
    started = time.monotonic()
    try:
        payload = _open_json(PUBLIC_ENDPOINTS[exchange], timeout=timeout, use_proxy=use_proxy)
    except BaseException as exc:
        return _failed_check(exchange, "public", exc)
    latency_ms = (time.monotonic() - started) * 1000
    return ConnectivityCheck(
        exchange=exchange,
        scope="public",
        status="PASS",
        category="ok",
        message="public endpoint reachable",
        latency_ms=latency_ms,
        details={"payload_keys": sorted(payload) if isinstance(payload, dict) else []},
    )


def _check_private_endpoint(exchange: str, *, timeout: float, use_proxy: bool) -> ConnectivityCheck:
    missing = [name for name in PRIVATE_CREDENTIALS[exchange] if not os.getenv(name)]
    if missing:
        return ConnectivityCheck(
            exchange=exchange,
            scope="private",
            status="FAIL",
            category="credential",
            message="missing required credential environment variables",
            details={"missing": missing},
        )

    started = time.monotonic()
    try:
        if exchange == "okx":
            OKXAdapter(timeout=timeout, max_retries=0, use_proxy=use_proxy).get_balance()
        else:
            BinanceAdapter(timeout=timeout, max_retries=0, use_proxy=use_proxy)._request("GET", "/api/v3/account")
    except (OKXAdapterError, BinanceAdapterError) as exc:
        return _failed_check(exchange, "private", exc)
    latency_ms = (time.monotonic() - started) * 1000
    return ConnectivityCheck(
        exchange=exchange,
        scope="private",
        status="PASS",
        category="ok",
        message="private read-only endpoint reachable",
        latency_ms=latency_ms,
    )


def _open_json(url: str, *, timeout: float, use_proxy: bool) -> Any:
    http_request = request.Request(url, headers={"Accept": "application/json", "User-Agent": "SmartQTF/1.0"})
    opener = build_proxy_opener() if use_proxy else None
    open_fn = opener.open if opener is not None else request.urlopen
    with open_fn(http_request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _failed_check(exchange: str, scope: str, exc: BaseException) -> ConnectivityCheck:
    category = _classify_error(exc)
    return ConnectivityCheck(
        exchange=exchange,
        scope=scope,
        status="FAIL",
        category=category,
        message=_safe_message(exc),
    )


def _classify_error(exc: BaseException) -> str:
    text = _safe_message(exc).lower()
    if isinstance(exc, error.HTTPError):
        if exc.code in {401, 403}:
            return "credential"
        if exc.code in {418, 429}:
            return "rate_limit"
        if 500 <= exc.code < 600:
            return "exchange_response"
        return "exchange_response"
    if isinstance(exc, error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror) or "name or service not known" in text or "nodename" in text:
            return "dns"
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "timeout"
        if "proxy" in text or "tunnel" in text:
            return "proxy"
        return "network"
    if isinstance(exc, (socket.gaierror,)):
        return "dns"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, ssl.SSLError):
        return "network"
    if "missing" in text and "credential" in text:
        return "credential"
    if "rate limit" in text or "too many requests" in text or "429" in text:
        return "rate_limit"
    if "proxy" in text or "tunnel" in text:
        return "proxy"
    if "dns" in text or "nodename" in text:
        return "dns"
    return "exchange_response"


def _safe_message(exc: BaseException) -> str:
    return str(exc).replace(os.getenv("OKX_API_KEY", "") or "\0", "***").replace(
        os.getenv("BINANCE_API_KEY", "") or "\0", "***"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run explicit read-only exchange connectivity diagnostics.")
    parser.add_argument(
        "--exchange",
        action="append",
        choices=sorted(PUBLIC_ENDPOINTS),
        help="Exchange to check. Can be supplied multiple times. Defaults to okx and binance.",
    )
    parser.add_argument("--include-private", action="store_true", help="Also check read-only private endpoints.")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds.")
    parser.add_argument("--no-proxy", action="store_true", help="Ignore SMARTQTF_USE_PROXY for this run.")
    args = parser.parse_args(argv)

    report = run_diagnostics(
        exchanges=args.exchange,
        include_private=args.include_private,
        timeout=args.timeout,
        use_proxy=False if args.no_proxy else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
