#!/usr/bin/env python
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.exchange.binance import BinanceAdapter, BinanceAdapterError
from adapters.exchange.okx import OKXAdapter, OKXAdapterError
from quant.data.quality import timeframe_to_seconds, validate_klines
from quant.data.schemas.market import Kline, KlineBatch


DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "logs" / "public-market-data" / "btcusdt-5m-latest.json"
PUBLIC_ENDPOINTS = {
    "binance": "https://api.binance.com/api/v3/klines",
    "okx": "https://www.okx.com/api/v5/market/candles",
}


def run_public_kline_fetch(
    *,
    exchange: str = "binance",
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
    limit: int = 1000,
    min_bars: int = 500,
    output: str | Path = DEFAULT_OUTPUT_PATH,
    timestamp: int | None = None,
    timeout: float = 10.0,
    max_retries: int = 3,
    adapter: Any | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    generated_at = int(time.time()) if timestamp is None else timestamp
    exchange = exchange.strip().lower()
    output_path = Path(output)

    validation_error = _validate_fetch_config(
        exchange=exchange,
        timeframe=timeframe,
        limit=limit,
        min_bars=min_bars,
    )
    if validation_error is not None:
        report = _build_report(
            status="FAIL",
            message=validation_error["message"],
            reason_codes=[validation_error["reason_code"]],
            generated_at=generated_at,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            source_url_or_endpoint=PUBLIC_ENDPOINTS.get(exchange),
            klines=[],
            quality_report=None,
            limit=limit,
            min_bars=min_bars,
            error_category=None,
            error_message=None,
            dropped_incomplete_bar_count=0,
        )
        return _write_report(report, output_path)

    try:
        active_adapter = adapter or _build_adapter(
            exchange=exchange,
            timeout=timeout,
            max_retries=max_retries,
            base_url=base_url,
        )
        batch = active_adapter.get_klines(symbol, timeframe, limit=limit)
    except (BinanceAdapterError, OKXAdapterError, OSError, TimeoutError) as exc:
        report = _build_report(
            status="SKIPPED",
            message="public market-data endpoint was unavailable",
            reason_codes=["public_market_data_unavailable"],
            generated_at=generated_at,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            source_url_or_endpoint=PUBLIC_ENDPOINTS[exchange],
            klines=[],
            quality_report=None,
            limit=limit,
            min_bars=min_bars,
            error_category=exc.__class__.__name__,
            error_message=str(exc),
            dropped_incomplete_bar_count=0,
        )
        return _write_report(report, output_path)

    klines, dropped_incomplete_bar_count = _complete_klines(batch.klines)
    quality_report = validate_klines(
        klines=klines,
        symbol=batch.symbol,
        timeframe=timeframe,
        expected_start_ts=klines[0].timestamp if klines else None,
        expected_end_ts=klines[-1].timestamp if klines else None,
    )
    reason_codes: list[str] = []
    if len(klines) < min_bars:
        reason_codes.append("insufficient_public_klines")
    if not quality_report.passed:
        reason_codes.append("public_kline_quality_failed")

    status = "PASS" if not reason_codes else "FAIL"
    message = (
        "public BTCUSDT klines fetched and validated"
        if status == "PASS"
        else "public BTCUSDT klines failed local validation"
    )

    report = _build_report(
        status=status,
        message=message,
        reason_codes=reason_codes,
        generated_at=generated_at,
        exchange=exchange,
        symbol=batch.symbol,
        timeframe=timeframe,
        source_url_or_endpoint=PUBLIC_ENDPOINTS[exchange],
        klines=klines,
        quality_report=quality_report.to_payload(),
        limit=limit,
        min_bars=min_bars,
        error_category=None,
        error_message=None,
        dropped_incomplete_bar_count=dropped_incomplete_bar_count,
    )
    return _write_report(report, output_path)


def _validate_fetch_config(
    *,
    exchange: str,
    timeframe: str,
    limit: int,
    min_bars: int,
) -> dict[str, str] | None:
    if exchange not in PUBLIC_ENDPOINTS:
        return {
            "reason_code": "unsupported_exchange",
            "message": f"unsupported exchange: {exchange}",
        }
    try:
        timeframe_to_seconds(timeframe)
    except ValueError:
        return {
            "reason_code": "unsupported_timeframe",
            "message": f"unsupported timeframe: {timeframe}",
        }
    if limit <= 0:
        return {
            "reason_code": "invalid_limit",
            "message": "limit must be positive",
        }
    if min_bars <= 0:
        return {
            "reason_code": "invalid_min_bars",
            "message": "min-bars must be positive",
        }
    return None


def _build_adapter(
    *,
    exchange: str,
    timeout: float,
    max_retries: int,
    base_url: str | None,
):
    if exchange == "binance":
        return BinanceAdapter(
            base_url=base_url or BinanceAdapter.BASE_URL,
            timeout=timeout,
            max_retries=max_retries,
            require_credentials=False,
        )
    return OKXAdapter(
        base_url=base_url or OKXAdapter.BASE_URL,
        timeout=timeout,
        max_retries=max_retries,
        require_credentials=False,
    )


def _complete_klines(klines: list[Kline]) -> tuple[list[Kline], int]:
    complete = [kline for kline in klines if kline.is_complete is not False]
    return complete, len(klines) - len(complete)


def _build_report(
    *,
    status: str,
    message: str,
    reason_codes: list[str],
    generated_at: int,
    exchange: str,
    symbol: str,
    timeframe: str,
    source_url_or_endpoint: str | None,
    klines: list[Kline],
    quality_report: dict[str, Any] | None,
    limit: int,
    min_bars: int,
    error_category: str | None,
    error_message: str | None,
    dropped_incomplete_bar_count: int,
) -> dict[str, Any]:
    kline_payloads = [_kline_payload(kline) for kline in klines]
    fingerprint = _sha256_payload(kline_payloads) if kline_payloads else None
    return {
        "schema_version": "1.0",
        "status": status,
        "message": message,
        "reason_codes": reason_codes,
        "generated_at": generated_at,
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "source_url_or_endpoint": source_url_or_endpoint,
        "bar_count": len(kline_payloads),
        "first_timestamp": klines[0].timestamp if klines else None,
        "last_timestamp": klines[-1].timestamp if klines else None,
        "sha256": fingerprint,
        "klines": kline_payloads,
        "quality_report": quality_report,
        "provenance": {
            "requested_limit": limit,
            "min_bars": min_bars,
            "public_market_data": True,
            "dropped_incomplete_bar_count": dropped_incomplete_bar_count,
        },
        "safety_flags": {
            "network_access_used": status != "FAIL" or bool(kline_payloads),
            "public_market_data_only": True,
            "real_credentials_read": False,
            "contains_real_credentials": False,
            "account_or_order_endpoint_called": False,
            "broker_called": False,
            "live_orders_sent": False,
            "analytics_modified_live_state": False,
        },
        "error": {
            "category": error_category,
            "message": error_message[:500] if error_message else None,
        },
    }


def _kline_payload(kline: Kline) -> dict[str, Any]:
    if hasattr(kline, "model_dump"):
        return kline.model_dump(mode="json")
    return kline.dict()


def _sha256_payload(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _write_report(report: dict[str, Any], output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", choices=sorted(PUBLIC_ENDPOINTS), default="binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--min-bars", type=int, default=500)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--timestamp", type=int)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--base-url")
    args = parser.parse_args(argv)

    report = run_public_kline_fetch(
        exchange=args.exchange,
        symbol=args.symbol,
        timeframe=args.timeframe,
        limit=args.limit,
        min_bars=args.min_bars,
        output=args.output,
        timestamp=args.timestamp,
        timeout=args.timeout,
        max_retries=args.max_retries,
        base_url=args.base_url,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] == "SKIPPED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
