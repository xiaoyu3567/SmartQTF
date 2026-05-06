#!/usr/bin/env python
import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.exchange.binance import BinanceAdapterError
from adapters.exchange.okx import OKXAdapterError
from quant.data.quality import timeframe_to_seconds, validate_klines
from quant.data.schemas.market import Kline
from scripts.fetch_public_btcusdt_klines import (
    PUBLIC_ENDPOINTS,
    _build_adapter,
    _build_report,
    _complete_klines,
    _write_report,
)


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "logs" / "public-market-data"
DEFAULT_SUMMARY_OUTPUT_PATH = DEFAULT_OUTPUT_DIR / "btcusdt-mtf-10k-latest.json"
DEFAULT_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
DEFAULT_REQUIRED_TIMEFRAMES = ["1m", "5m", "15m", "1h"]


def run_public_kline_matrix_fetch(
    *,
    exchange: str = "binance",
    symbol: str = "BTCUSDT",
    timeframes: str | list[str] | tuple[str, ...] = DEFAULT_TIMEFRAMES,
    required_timeframes: str | list[str] | tuple[str, ...] = DEFAULT_REQUIRED_TIMEFRAMES,
    target_bars: int = 10000,
    page_limit: int = 1000,
    max_pages: int | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    summary_output: str | Path | None = DEFAULT_SUMMARY_OUTPUT_PATH,
    timestamp: int | None = None,
    timeout: float = 10.0,
    max_retries: int = 3,
    adapter: Any | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    generated_at = int(time.time()) if timestamp is None else timestamp
    exchange = exchange.strip().lower()
    parsed_timeframes = _parse_csv_list(timeframes)
    parsed_required_timeframes = _parse_csv_list(required_timeframes)
    output_dir = Path(output_dir)
    summary_output_path = Path(summary_output) if summary_output is not None else None

    config_error = _validate_matrix_config(
        exchange=exchange,
        timeframes=parsed_timeframes,
        required_timeframes=parsed_required_timeframes,
        target_bars=target_bars,
        page_limit=page_limit,
        max_pages=max_pages,
    )
    if config_error is not None:
        report = _build_summary_report(
            status="FAIL",
            message=config_error["message"],
            reason_codes=[config_error["reason_code"]],
            generated_at=generated_at,
            exchange=exchange,
            symbol=symbol,
            timeframes=parsed_timeframes,
            required_timeframes=parsed_required_timeframes,
            target_bars=target_bars,
            page_limit=page_limit,
            max_pages=max_pages,
            timeframe_reports={},
        )
        return _write_report(report, summary_output_path) if summary_output_path else report

    try:
        active_adapter = adapter or _build_adapter(
            exchange=exchange,
            timeout=timeout,
            max_retries=max_retries,
            base_url=base_url,
        )
    except (BinanceAdapterError, OKXAdapterError, OSError, TimeoutError) as exc:
        timeframe_reports = {
            timeframe: _unavailable_timeframe_report(
                generated_at=generated_at,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                target_bars=target_bars,
                page_limit=page_limit,
                max_pages=max_pages,
                output_path=_timeframe_output_path(output_dir, symbol, timeframe, target_bars),
                exc=exc,
            )
            for timeframe in parsed_timeframes
        }
    else:
        timeframe_reports = {}
        for timeframe in parsed_timeframes:
            output_path = _timeframe_output_path(output_dir, symbol, timeframe, target_bars)
            timeframe_reports[timeframe] = _fetch_timeframe_matrix_report(
                adapter=active_adapter,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                target_bars=target_bars,
                page_limit=page_limit,
                max_pages=max_pages,
                output_path=output_path,
                generated_at=generated_at,
            )

    summary = _summarize_matrix(
        generated_at=generated_at,
        exchange=exchange,
        symbol=symbol,
        timeframes=parsed_timeframes,
        required_timeframes=parsed_required_timeframes,
        target_bars=target_bars,
        page_limit=page_limit,
        max_pages=max_pages,
        timeframe_reports=timeframe_reports,
    )
    return _write_report(summary, summary_output_path) if summary_output_path else summary


def _fetch_timeframe_matrix_report(
    *,
    adapter: Any,
    exchange: str,
    symbol: str,
    timeframe: str,
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
    output_path: Path,
    generated_at: int,
) -> dict[str, Any]:
    max_page_count = max_pages if max_pages is not None else math.ceil(target_bars / page_limit) + 1
    interval_seconds = timeframe_to_seconds(timeframe)
    cursor_end_ts = _latest_complete_kline_timestamp(generated_at, interval_seconds)
    collected_by_timestamp: dict[int, Kline] = {}
    pages_attempted = 0

    try:
        for _ in range(max_page_count):
            if len(collected_by_timestamp) >= target_bars:
                break
            page_start_ts = max(0, cursor_end_ts - (interval_seconds * (page_limit - 1)))
            batch = adapter.get_klines(
                symbol,
                timeframe,
                start_ts=page_start_ts,
                end_ts=cursor_end_ts,
                limit=page_limit,
            )
            pages_attempted += 1
            page_klines, _ = _complete_klines(batch.klines)
            if not page_klines:
                break

            new_count = 0
            for kline in page_klines:
                if kline.timestamp not in collected_by_timestamp:
                    collected_by_timestamp[kline.timestamp] = kline
                    new_count += 1
            if new_count == 0:
                break

            cursor_end_ts = min(kline.timestamp for kline in page_klines) - interval_seconds
            if cursor_end_ts < 0:
                break
    except (BinanceAdapterError, OKXAdapterError, OSError, TimeoutError) as exc:
        report = _unavailable_timeframe_report(
            generated_at=generated_at,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            target_bars=target_bars,
            page_limit=page_limit,
            max_pages=max_page_count,
            output_path=output_path,
            exc=exc,
            pages_attempted=pages_attempted,
        )
        return _write_report(report, output_path)

    klines = sorted(collected_by_timestamp.values(), key=lambda item: item.timestamp)[-target_bars:]
    quality_report = validate_klines(
        klines=klines,
        symbol=symbol,
        timeframe=timeframe,
        expected_start_ts=klines[0].timestamp if klines else None,
        expected_end_ts=klines[-1].timestamp if klines else None,
    )
    reason_codes: list[str] = []
    if len(klines) < target_bars:
        reason_codes.append("insufficient_public_klines")
    if not quality_report.passed:
        reason_codes.append("public_kline_quality_failed")

    if not reason_codes:
        status = "PASS"
        message = f"public {symbol} timeframe history fetched and validated"
    elif "public_kline_quality_failed" in reason_codes:
        status = "FAIL"
        message = f"public {symbol} timeframe history failed local validation"
    else:
        status = "SKIPPED"
        message = f"public {symbol} timeframe history did not reach target bars"

    report = _build_report(
        status=status,
        message=message,
        reason_codes=reason_codes,
        generated_at=generated_at,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        source_url_or_endpoint=PUBLIC_ENDPOINTS[exchange],
        klines=klines,
        quality_report=quality_report.to_payload(),
        limit=page_limit,
        min_bars=target_bars,
        error_category=None,
        error_message=None,
        dropped_incomplete_bar_count=0,
    )
    report["provenance"].update(
        {
            "target_bars": target_bars,
            "page_limit": page_limit,
            "max_pages": max_page_count,
            "pages_attempted": pages_attempted,
            "pagination": _pagination_label(exchange),
        }
    )
    report["meets_target_bars"] = report["status"] == "PASS" and report["bar_count"] >= target_bars
    report["output_path"] = str(output_path)
    return _write_report(report, output_path)


def _unavailable_timeframe_report(
    *,
    generated_at: int,
    exchange: str,
    symbol: str,
    timeframe: str,
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
    output_path: Path,
    exc: BaseException,
    pages_attempted: int = 0,
) -> dict[str, Any]:
    report = _build_report(
        status="SKIPPED",
        message="public market-data endpoint was unavailable",
        reason_codes=["public_market_data_unavailable"],
        generated_at=generated_at,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        source_url_or_endpoint=PUBLIC_ENDPOINTS.get(exchange),
        klines=[],
        quality_report=None,
        limit=page_limit,
        min_bars=target_bars,
        error_category=exc.__class__.__name__,
        error_message=str(exc),
        dropped_incomplete_bar_count=0,
    )
    report["provenance"].update(
        {
            "target_bars": target_bars,
            "page_limit": page_limit,
            "max_pages": max_pages,
            "pages_attempted": pages_attempted,
            "pagination": _pagination_label(exchange),
        }
    )
    report["meets_target_bars"] = False
    report["output_path"] = str(output_path)
    return _write_report(report, output_path)


def _summarize_matrix(
    *,
    generated_at: int,
    exchange: str,
    symbol: str,
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
    timeframe_reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return _build_summary_report(
        status=_matrix_status(timeframe_reports, required_timeframes),
        message=_matrix_message(timeframe_reports, required_timeframes),
        reason_codes=_matrix_reason_codes(timeframe_reports, required_timeframes),
        generated_at=generated_at,
        exchange=exchange,
        symbol=symbol,
        timeframes=timeframes,
        required_timeframes=required_timeframes,
        target_bars=target_bars,
        page_limit=page_limit,
        max_pages=max_pages,
        timeframe_reports=timeframe_reports,
    )


def _build_summary_report(
    *,
    status: str,
    message: str,
    reason_codes: list[str],
    generated_at: int,
    exchange: str,
    symbol: str,
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
    timeframe_reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    pass_timeframes = [
        timeframe for timeframe, report in timeframe_reports.items() if report.get("status") == "PASS"
    ]
    skipped_timeframes = [
        timeframe for timeframe, report in timeframe_reports.items() if report.get("status") == "SKIPPED"
    ]
    failed_timeframes = [
        timeframe for timeframe, report in timeframe_reports.items() if report.get("status") == "FAIL"
    ]
    minimum_timeframes_passed = all(
        timeframe in pass_timeframes for timeframe in required_timeframes
    )
    summary_timeframes = {
        timeframe: _summary_timeframe_payload(report)
        for timeframe, report in timeframe_reports.items()
    }
    return {
        "schema_version": "1.0",
        "status": status,
        "message": message,
        "reason_codes": reason_codes,
        "generated_at": generated_at,
        "exchange": exchange,
        "symbol": symbol,
        "source_url_or_endpoint": PUBLIC_ENDPOINTS.get(exchange),
        "timeframes_requested": timeframes,
        "required_timeframes": required_timeframes,
        "target_bars": target_bars,
        "page_limit": page_limit,
        "max_pages": max_pages,
        "pass_timeframes": pass_timeframes,
        "skipped_timeframes": skipped_timeframes,
        "failed_timeframes": failed_timeframes,
        "minimum_timeframes_passed": minimum_timeframes_passed,
        "h_opt_018_ready": minimum_timeframes_passed,
        "timeframes": summary_timeframes,
        "safety_flags": {
            "network_access_used": bool(timeframe_reports),
            "public_market_data_only": True,
            "real_credentials_read": False,
            "contains_real_credentials": False,
            "account_or_order_endpoint_called": False,
            "broker_called": False,
            "live_orders_sent": False,
            "analytics_modified_live_state": False,
        },
    }


def _summary_timeframe_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "message": report.get("message"),
        "reason_codes": report.get("reason_codes", []),
        "output_path": report.get("output_path"),
        "bar_count": report.get("bar_count"),
        "first_timestamp": report.get("first_timestamp"),
        "last_timestamp": report.get("last_timestamp"),
        "sha256": report.get("sha256"),
        "quality_report": report.get("quality_report"),
        "meets_target_bars": report.get("meets_target_bars", False),
        "provenance": report.get("provenance"),
        "safety_flags": report.get("safety_flags"),
        "error": report.get("error"),
    }


def _matrix_status(
    timeframe_reports: dict[str, dict[str, Any]],
    required_timeframes: list[str],
) -> str:
    if not timeframe_reports:
        return "FAIL"
    pass_timeframes = {
        timeframe for timeframe, report in timeframe_reports.items() if report.get("status") == "PASS"
    }
    if all(timeframe in pass_timeframes for timeframe in required_timeframes):
        return "PASS"
    if pass_timeframes:
        return "PARTIAL"
    if any(report.get("status") == "FAIL" for report in timeframe_reports.values()):
        return "FAIL"
    return "SKIPPED"


def _matrix_message(
    timeframe_reports: dict[str, dict[str, Any]],
    required_timeframes: list[str],
) -> str:
    status = _matrix_status(timeframe_reports, required_timeframes)
    if status == "PASS":
        return "required public multi-timeframe history fetched and validated"
    if status == "PARTIAL":
        return "public multi-timeframe history partially fetched"
    if status == "SKIPPED":
        return "public multi-timeframe history fetch was skipped"
    return "public multi-timeframe history fetch failed validation"


def _matrix_reason_codes(
    timeframe_reports: dict[str, dict[str, Any]],
    required_timeframes: list[str],
) -> list[str]:
    reason_codes: set[str] = set()
    pass_timeframes = {
        timeframe for timeframe, report in timeframe_reports.items() if report.get("status") == "PASS"
    }
    for report in timeframe_reports.values():
        reason_codes.update(report.get("reason_codes", []))
    missing_required = sorted(
        timeframe for timeframe in required_timeframes if timeframe not in pass_timeframes
    )
    if missing_required:
        reason_codes.add("required_timeframe_not_passed")
    return sorted(reason_codes)


def _validate_matrix_config(
    *,
    exchange: str,
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
) -> dict[str, str] | None:
    if exchange not in PUBLIC_ENDPOINTS:
        return {
            "reason_code": "unsupported_exchange",
            "message": f"unsupported exchange: {exchange}",
        }
    if not timeframes:
        return {
            "reason_code": "missing_timeframes",
            "message": "at least one timeframe is required",
        }
    if not set(required_timeframes).issubset(set(timeframes)):
        return {
            "reason_code": "required_timeframe_not_requested",
            "message": "required timeframes must be included in requested timeframes",
        }
    for timeframe in timeframes:
        try:
            timeframe_to_seconds(timeframe)
        except ValueError:
            return {
                "reason_code": "unsupported_timeframe",
                "message": f"unsupported timeframe: {timeframe}",
            }
    if target_bars <= 0:
        return {
            "reason_code": "invalid_target_bars",
            "message": "target-bars must be positive",
        }
    if page_limit <= 0:
        return {
            "reason_code": "invalid_page_limit",
            "message": "page-limit must be positive",
        }
    if max_pages is not None and max_pages <= 0:
        return {
            "reason_code": "invalid_max_pages",
            "message": "max-pages must be positive when provided",
        }
    return None


def _parse_csv_list(value: str | list[str] | tuple[str, ...]) -> list[str]:
    raw_items = value.split(",") if isinstance(value, str) else list(value)
    parsed: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        item = str(raw_item).strip()
        if item and item not in seen:
            parsed.append(item)
            seen.add(item)
    return parsed


def _timeframe_output_path(
    output_dir: Path,
    symbol: str,
    timeframe: str,
    target_bars: int,
) -> Path:
    return output_dir / f"{_file_symbol(symbol)}-{timeframe}-{_target_label(target_bars)}-latest.json"


def _file_symbol(symbol: str) -> str:
    return "".join(ch for ch in symbol.lower() if ch.isalnum())


def _target_label(target_bars: int) -> str:
    if target_bars % 1000 == 0:
        return f"{target_bars // 1000}k"
    return str(target_bars)


def _latest_complete_kline_timestamp(generated_at: int, interval_seconds: int) -> int:
    current_open_ts = (generated_at // interval_seconds) * interval_seconds
    return max(0, current_open_ts - interval_seconds)


def _pagination_label(exchange: str) -> str:
    if exchange == "binance":
        return "startTime_endTime"
    if exchange == "okx":
        return "before_after"
    return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", choices=sorted(PUBLIC_ENDPOINTS), default="binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--required-timeframes", default=",".join(DEFAULT_REQUIRED_TIMEFRAMES))
    parser.add_argument("--target-bars", type=int, default=10000)
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--summary-output", default=str(DEFAULT_SUMMARY_OUTPUT_PATH))
    parser.add_argument("--timestamp", type=int)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--base-url")
    args = parser.parse_args(argv)

    report = run_public_kline_matrix_fetch(
        exchange=args.exchange,
        symbol=args.symbol,
        timeframes=args.timeframes,
        required_timeframes=args.required_timeframes,
        target_bars=args.target_bars,
        page_limit=args.page_limit,
        max_pages=args.max_pages,
        output_dir=args.output_dir,
        summary_output=args.summary_output,
        timestamp=args.timestamp,
        timeout=args.timeout,
        max_retries=args.max_retries,
        base_url=args.base_url,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] in {"SKIPPED", "PARTIAL"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
