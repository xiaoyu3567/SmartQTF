#!/usr/bin/env python
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.exchange.binance import BinanceAdapter
from adapters.exchange.okx import OKXAdapter
from quant.account.exchange_sync import (
    BinanceAccountSyncAdapter,
    ExchangeAccountParseError,
    OKXAccountSyncAdapter,
)
from quant.schemas.account import AccountSyncSnapshot


DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "logs" / "account-sync-validation" / "latest.json"
LIVE_VALIDATION_ENV = "SMARTQTF_RUN_ACCOUNT_SYNC_TEST"
SUPPORTED_EXCHANGES = ("okx", "binance")
PRIVATE_CREDENTIALS = {
    "okx": ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE"),
    "binance": ("BINANCE_API_KEY", "BINANCE_SECRET"),
}


class FixtureOKXAccountRestAdapter:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def get_balance(self) -> dict[str, Any]:
        return self.payload["balance"]

    def get_positions(self) -> dict[str, Any]:
        return self.payload.get("positions", {"data": []})


class FixtureBinanceAccountRestAdapter:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def get_account(self) -> dict[str, Any]:
        return self.payload["account"]


def run_account_sync_validation(
    *,
    exchanges: list[str] | None = None,
    mode: str = "fixture",
    fixture_path: str | Path | None = None,
    output_path: str | Path | None = None,
    account_id_prefix: str = "account-sync-validation",
    timestamp: int | None = None,
    timeout: float = 5.0,
    market_prices: dict[str, float] | None = None,
    include_sensitive_snapshot: bool = False,
) -> dict[str, Any]:
    selected_exchanges = _normalize_exchanges(exchanges)
    mode = mode.strip().lower()
    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")

    generated_at = int(time.time()) if timestamp is None else timestamp
    fixture_payload = _load_fixture_payload(fixture_path)
    if mode == "live" and os.getenv(LIVE_VALIDATION_ENV) != "1":
        report = _build_report(
            checks=[
                _skipped_check(
                    exchange,
                    f"set {LIVE_VALIDATION_ENV}=1 to run read-only live account validation",
                    source="live",
                )
                for exchange in selected_exchanges
            ],
            mode=mode,
            generated_at=generated_at,
            fixture_path=fixture_path,
        )
        return _write_report(report, output_path)
    if mode == "live" and os.getenv("SMARTQTF_USE_PROXY") != "1":
        report = _build_report(
            checks=[
                _failed_check(
                    exchange,
                    "set SMARTQTF_USE_PROXY=1 to run read-only live account validation through the project proxy",
                    category="proxy",
                    source="live",
                )
                for exchange in selected_exchanges
            ],
            mode=mode,
            generated_at=generated_at,
            fixture_path=fixture_path,
        )
        return _write_report(report, output_path)

    checks = [
        _validate_exchange_account_sync(
            exchange=exchange,
            mode=mode,
            fixture_payload=fixture_payload,
            account_id=f"{account_id_prefix}:{exchange}",
            timestamp=generated_at,
            timeout=timeout,
            market_prices=market_prices or {},
            include_sensitive_snapshot=include_sensitive_snapshot,
        )
        for exchange in selected_exchanges
    ]
    report = _build_report(
        checks=checks,
        mode=mode,
        generated_at=generated_at,
        fixture_path=fixture_path,
    )
    return _write_report(report, output_path)


def _validate_exchange_account_sync(
    *,
    exchange: str,
    mode: str,
    fixture_payload: dict[str, Any] | None,
    account_id: str,
    timestamp: int,
    timeout: float,
    market_prices: dict[str, float],
    include_sensitive_snapshot: bool,
) -> dict[str, Any]:
    try:
        adapter = _build_account_sync_adapter(
            exchange=exchange,
            mode=mode,
            fixture_payload=fixture_payload,
            account_id=account_id,
            timestamp=timestamp,
            timeout=timeout,
            market_prices=market_prices,
        )
        snapshot = adapter.get_account_snapshot()
        restored = AccountSyncSnapshot.from_payload(snapshot.to_payload())
        summary = _snapshot_summary(restored)
        check = {
            "exchange": exchange,
            "status": "PASS",
            "category": "ok",
            "message": "account sync snapshot parsed and schema round-tripped",
            "source": mode,
            "read_only": True,
            "live_orders_sent": False,
            "snapshot_summary": summary,
        }
        if include_sensitive_snapshot:
            check["snapshot_payload"] = restored.to_payload()
        return check
    except BaseException as exc:
        return {
            "exchange": exchange,
            "status": "FAIL",
            "category": _classify_error(exc),
            "message": _safe_error_message(exc),
            "source": mode,
            "read_only": True,
            "live_orders_sent": False,
        }


def _build_account_sync_adapter(
    *,
    exchange: str,
    mode: str,
    fixture_payload: dict[str, Any] | None,
    account_id: str,
    timestamp: int,
    timeout: float,
    market_prices: dict[str, float],
):
    if mode == "fixture":
        payload = _exchange_fixture_payload(exchange, fixture_payload)
        if exchange == "okx":
            return OKXAccountSyncAdapter(
                FixtureOKXAccountRestAdapter(payload),
                account_id=account_id,
                observed_at=timestamp,
            )
        return BinanceAccountSyncAdapter(
            FixtureBinanceAccountRestAdapter(payload),
            account_id=account_id,
            market_prices={**payload.get("market_prices", {}), **market_prices},
            observed_at=timestamp,
        )

    missing = [name for name in PRIVATE_CREDENTIALS[exchange] if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"missing credential environment variables: {', '.join(missing)}")

    if exchange == "okx":
        return OKXAccountSyncAdapter(
            OKXAdapter(timeout=timeout, max_retries=0),
            account_id=account_id,
        )
    return BinanceAccountSyncAdapter(
        BinanceAdapter(timeout=timeout, max_retries=0),
        account_id=account_id,
        market_prices=market_prices,
    )


def _build_report(
    *,
    checks: list[dict[str, Any]],
    mode: str,
    generated_at: int,
    fixture_path: str | Path | None,
) -> dict[str, Any]:
    failed = [check for check in checks if check["status"] == "FAIL"]
    skipped = [check for check in checks if check["status"] == "SKIPPED"]
    if failed:
        status = "FAIL"
    elif len(skipped) == len(checks):
        status = "SKIPPED"
    else:
        status = "PASS"

    return {
        "success": status == "PASS",
        "status": status,
        "mode": mode,
        "generated_at": generated_at,
        "message": _report_message(status),
        "live_orders_sent": False,
        "read_only": True,
        "contains_real_credentials": False,
        "fixture_path": str(fixture_path) if fixture_path is not None else None,
        "proxy": {
            "SMARTQTF_USE_PROXY": os.getenv("SMARTQTF_USE_PROXY"),
            "SMARTQTF_PROXY_URL": os.getenv("SMARTQTF_PROXY_URL"),
            "required_for_live": mode == "live",
        },
        "checks": checks,
        "failed_count": len(failed),
        "skipped_count": len(skipped),
    }


def _report_message(status: str) -> str:
    if status == "PASS":
        return "account sync validation passed"
    if status == "SKIPPED":
        return "live account sync validation was not explicitly enabled"
    return "account sync validation failed"


def _skipped_check(exchange: str, message: str, *, source: str) -> dict[str, Any]:
    return {
        "exchange": exchange,
        "status": "SKIPPED",
        "category": "manual_gate",
        "message": message,
        "source": source,
        "read_only": True,
        "live_orders_sent": False,
    }


def _failed_check(exchange: str, message: str, *, category: str, source: str) -> dict[str, Any]:
    return {
        "exchange": exchange,
        "status": "FAIL",
        "category": category,
        "message": message,
        "source": source,
        "read_only": True,
        "live_orders_sent": False,
    }


def _snapshot_summary(snapshot: AccountSyncSnapshot) -> dict[str, Any]:
    return {
        "account_id": snapshot.account_id,
        "source": snapshot.source,
        "venue": snapshot.venue,
        "observed_at": snapshot.observed_at,
        "base_asset": snapshot.base_asset,
        "balance_count": len(snapshot.balances),
        "balance_assets": [balance.asset for balance in snapshot.balances],
        "position_count": len(snapshot.positions),
        "holding_symbols": snapshot.holding_symbols,
        "portfolio_position_count": len(snapshot.to_portfolio_positions()),
        "equity_present": snapshot.equity >= 0.0,
        "parser": snapshot.metadata.get("parser"),
        "read_only": snapshot.metadata.get("read_only"),
    }


def _normalize_exchanges(exchanges: list[str] | None) -> list[str]:
    selected = exchanges or list(SUPPORTED_EXCHANGES)
    normalized = []
    for exchange in selected:
        value = exchange.strip().lower()
        if value not in SUPPORTED_EXCHANGES:
            raise ValueError(f"unsupported exchange: {exchange}")
        if value not in normalized:
            normalized.append(value)
    return normalized


def _load_fixture_payload(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("account sync fixture must be a JSON object")
    return payload


def _exchange_fixture_payload(exchange: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return _builtin_fixture_payload(exchange)
    if exchange in payload:
        exchange_payload = payload[exchange]
    else:
        exchange_payload = payload
    if not isinstance(exchange_payload, dict):
        raise ValueError(f"{exchange} fixture payload must be an object")
    return exchange_payload


def _builtin_fixture_payload(exchange: str) -> dict[str, Any]:
    if exchange == "okx":
        return {
            "balance": {
                "success": True,
                "exchange": "okx",
                "path": "/api/v5/account/balance",
                "data": [
                    {
                        "uTime": "1710000600123",
                        "totalEq": "10125.5",
                        "details": [
                            {"ccy": "USDT", "eq": "10000", "availBal": "9500"},
                            {"ccy": "BTC", "eq": "0.1", "availBal": "0.1"},
                        ],
                    }
                ],
            },
            "positions": {
                "success": True,
                "exchange": "okx",
                "path": "/api/v5/account/positions",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "posSide": "long",
                        "pos": "0.2",
                        "avgPx": "50000",
                        "markPx": "51000",
                        "upl": "200",
                        "uTime": "1710000601123",
                    }
                ],
            },
        }
    return {
        "account": {
            "success": True,
            "exchange": "binance",
            "path": "/api/v3/account",
            "data": [
                {
                    "updateTime": 1710000600123,
                    "balances": [
                        {"asset": "USDT", "free": "9000", "locked": "1000"},
                        {"asset": "BTC", "free": "0.2", "locked": "0"},
                    ],
                }
            ],
        },
        "market_prices": {"BTCUSDT": 50500.0},
    }


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, ExchangeAccountParseError):
        return "parse"
    text = _safe_error_message(exc).lower()
    if "missing credential" in text:
        return "credential"
    if "proxy" in text or "tunnel" in text:
        return "proxy"
    if "dns" in text or "nodename" in text or "name or service not known" in text:
        return "dns"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "exchange_response"


def _safe_error_message(exc: BaseException) -> str:
    message = str(exc)
    for names in PRIVATE_CREDENTIALS.values():
        for name in names:
            secret = os.getenv(name)
            if secret:
                message = message.replace(secret, "***")
    return message


def _parse_market_price(value: str) -> tuple[str, float]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("market prices must use SYMBOL=PRICE")
    symbol, price = value.split("=", 1)
    symbol = symbol.strip().upper().replace("-", "").replace("_", "")
    if not symbol:
        raise argparse.ArgumentTypeError("market price symbol must not be empty")
    return symbol, float(price)


def _write_report(report: dict[str, Any], output_path: str | Path | None) -> dict[str, Any]:
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate read-only exchange account parsing into AccountSyncSnapshot."
    )
    parser.add_argument("--exchange", action="append", choices=SUPPORTED_EXCHANGES)
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--fixture", help="Optional fixture JSON path. May contain top-level okx/binance objects.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--no-output", action="store_true")
    parser.add_argument("--account-id-prefix", default="account-sync-validation")
    parser.add_argument("--timestamp", type=int)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--market-price",
        action="append",
        type=_parse_market_price,
        default=[],
        help="Optional Binance spot holding mark, e.g. BTCUSDT=65000.",
    )
    parser.add_argument(
        "--include-sensitive-snapshot",
        action="store_true",
        help="Include full AccountSyncSnapshot payload in the report. Off by default.",
    )
    args = parser.parse_args(argv)

    report = run_account_sync_validation(
        exchanges=args.exchange,
        mode=args.mode,
        fixture_path=args.fixture,
        output_path=None if args.no_output else args.output,
        account_id_prefix=args.account_id_prefix,
        timestamp=args.timestamp,
        timeout=args.timeout,
        market_prices=dict(args.market_price),
        include_sensitive_snapshot=args.include_sensitive_snapshot,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] == "SKIPPED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
