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

from adapters.exchange.binance import BinanceAdapterError
from adapters.exchange.okx import OKXAdapterError
from scripts.fetch_public_btcusdt_klines import PUBLIC_ENDPOINTS, _build_adapter
from scripts.fetch_public_btcusdt_klines_matrix import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REQUIRED_TIMEFRAMES,
    DEFAULT_TIMEFRAMES,
    _file_symbol,
    _parse_csv_list,
    _target_label,
    run_public_kline_matrix_fetch,
)


DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_DISCOVERY_QUOTE_CURRENCIES = ["USDT", "USDC"]
DEFAULT_SUMMARY_OUTPUT_PATH = DEFAULT_OUTPUT_DIR / "public-universe-matrix-latest.json"
H_DATA_015_COMPLETION_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
H_DATA_015_MIN_COMPLETION_TARGET_BARS = 10000


def run_public_market_universe_fetch(
    *,
    exchange: str = "binance",
    symbols: str | list[str] | tuple[str, ...] | None = DEFAULT_SYMBOLS,
    timeframes: str | list[str] | tuple[str, ...] = DEFAULT_TIMEFRAMES,
    required_timeframes: str | list[str] | tuple[str, ...] = DEFAULT_REQUIRED_TIMEFRAMES,
    target_bars: int = 10000,
    page_limit: int = 1000,
    max_pages: int | None = None,
    min_pass_symbols: int = 2,
    market_type: str = "spot",
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    summary_output: str | Path | None = DEFAULT_SUMMARY_OUTPUT_PATH,
    timestamp: int | None = None,
    timeout: float = 10.0,
    max_retries: int = 3,
    adapter: Any | None = None,
    base_url: str | None = None,
    max_runtime_seconds: float | None = None,
    progress_interval_seconds: float | None = None,
    discover_symbols: bool = False,
    max_discovered_symbols: int = 10,
    discovery_quote_currencies: str | list[str] | tuple[str, ...] = DEFAULT_DISCOVERY_QUOTE_CURRENCIES,
    min_discovery_turnover_24h: float = 0.0,
    resume_existing_symbol_summaries: bool = False,
) -> dict[str, Any]:
    generated_at = int(time.time()) if timestamp is None else timestamp
    started_monotonic = time.monotonic()
    last_progress_monotonic = started_monotonic
    exchange = exchange.strip().lower()
    parsed_symbols = [symbol.upper() for symbol in _parse_csv_list(symbols or [])]
    parsed_timeframes = _parse_csv_list(timeframes)
    parsed_required_timeframes = _parse_csv_list(required_timeframes)
    parsed_discovery_quote_currencies = [
        currency.upper() for currency in _parse_csv_list(discovery_quote_currencies)
    ]
    output_dir = Path(output_dir)
    summary_output_path = Path(summary_output) if summary_output is not None else None
    resume_report = _initial_resume_report(
        enabled=resume_existing_symbol_summaries,
        generated_at=generated_at,
    )

    config_error = _validate_universe_config(
        exchange=exchange,
        symbols=parsed_symbols,
        discover_symbols=discover_symbols,
        timeframes=parsed_timeframes,
        required_timeframes=parsed_required_timeframes,
        target_bars=target_bars,
        page_limit=page_limit,
        max_pages=max_pages,
        min_pass_symbols=min_pass_symbols,
        max_runtime_seconds=max_runtime_seconds,
        progress_interval_seconds=progress_interval_seconds,
        max_discovered_symbols=max_discovered_symbols,
        discovery_quote_currencies=parsed_discovery_quote_currencies,
        min_discovery_turnover_24h=min_discovery_turnover_24h,
    )
    if config_error is not None:
        report = _build_universe_report(
            status="FAIL",
            message=config_error["message"],
            reason_codes=[config_error["reason_code"]],
            generated_at=generated_at,
            exchange=exchange,
            market_type=market_type,
            symbols=parsed_symbols,
            timeframes=parsed_timeframes,
            required_timeframes=parsed_required_timeframes,
            target_bars=target_bars,
            page_limit=page_limit,
            max_pages=max_pages,
            min_pass_symbols=min_pass_symbols,
            symbol_reports={},
            elapsed_seconds=0.0,
            runtime_limited=False,
            allowlist_source=_allowlist_source(discover_symbols),
            discovery_report=_config_error_discovery_report(
                generated_at=generated_at,
                exchange=exchange,
                market_type=market_type,
                discover_symbols=discover_symbols,
                reason_code=config_error["reason_code"],
                message=config_error["message"],
            ),
            resume_report=resume_report,
        )
        return _write_report(report, summary_output_path) if summary_output_path else report

    active_adapter = adapter
    discovery_report = _explicit_symbol_discovery_report(
        generated_at=generated_at,
        exchange=exchange,
        market_type=market_type,
        symbols=parsed_symbols,
    )
    if discover_symbols:
        parsed_symbols, discovery_report, active_adapter = _discover_public_symbols(
            generated_at=generated_at,
            exchange=exchange,
            market_type=market_type,
            quote_currencies=parsed_discovery_quote_currencies,
            max_symbols=max_discovered_symbols,
            min_turnover_24h=min_discovery_turnover_24h,
            adapter=active_adapter,
            timeout=timeout,
            max_retries=max_retries,
            base_url=base_url,
        )
        if not parsed_symbols:
            report = _build_universe_report(
                status="SKIPPED",
                message="public symbol discovery did not select any symbols",
                reason_codes=sorted(
                    set(discovery_report.get("reason_codes", []))
                    | {"no_public_symbols_discovered"}
                ),
                generated_at=generated_at,
                exchange=exchange,
                market_type=market_type,
                symbols=[],
                timeframes=parsed_timeframes,
                required_timeframes=parsed_required_timeframes,
                target_bars=target_bars,
                page_limit=page_limit,
                max_pages=max_pages,
                min_pass_symbols=min_pass_symbols,
                symbol_reports={},
                elapsed_seconds=time.monotonic() - started_monotonic,
                runtime_limited=False,
                allowlist_source="public_symbol_discovery",
                discovery_report=discovery_report,
                resume_report=resume_report,
            )
            return _write_report(report, summary_output_path) if summary_output_path else report
    discovery_metadata_by_symbol = _discovery_metadata_by_symbol(discovery_report)

    symbol_reports: dict[str, dict[str, Any]] = {}
    runtime_limited = False
    for index, symbol in enumerate(parsed_symbols, start=1):
        symbol_summary_output = _symbol_summary_output_path(
            output_dir=output_dir,
            exchange=exchange,
            symbol=symbol,
            target_bars=target_bars,
        )
        if resume_existing_symbol_summaries:
            existing_symbol_report, resume_reason_code = _load_resumable_symbol_summary(
                path=symbol_summary_output,
                generated_at=generated_at,
                exchange=exchange,
                symbol=symbol,
                timeframes=parsed_timeframes,
                required_timeframes=parsed_required_timeframes,
                target_bars=target_bars,
                page_limit=page_limit,
                max_pages=max_pages,
            )
            if existing_symbol_report is not None:
                existing_symbol_report["market_type"] = market_type
                existing_symbol_report["symbol_summary_output_path"] = str(symbol_summary_output)
                if symbol in discovery_metadata_by_symbol:
                    existing_symbol_report["discovery_metadata"] = discovery_metadata_by_symbol[symbol]
                symbol_reports[symbol] = existing_symbol_report
                _record_resume_reuse(
                    resume_report=resume_report,
                    symbol=symbol,
                    path=symbol_summary_output,
                )
                if _should_print_progress(
                    progress_interval_seconds=progress_interval_seconds,
                    now_monotonic=time.monotonic(),
                    last_progress_monotonic=last_progress_monotonic,
                    index=index,
                    total=len(parsed_symbols),
                ):
                    last_progress_monotonic = time.monotonic()
                    _print_progress(
                        completed=index,
                        total=len(parsed_symbols),
                        symbol=symbol,
                        elapsed_seconds=last_progress_monotonic - started_monotonic,
                    )
                continue
            _record_resume_miss(
                resume_report=resume_report,
                symbol=symbol,
                path=symbol_summary_output,
                reason_code=resume_reason_code,
            )

        elapsed_seconds = time.monotonic() - started_monotonic
        if max_runtime_seconds is not None and elapsed_seconds >= max_runtime_seconds:
            runtime_limited = True
            symbol_reports[symbol] = _runtime_limited_symbol_report(
                generated_at=generated_at,
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                timeframes=parsed_timeframes,
                required_timeframes=parsed_required_timeframes,
                target_bars=target_bars,
                page_limit=page_limit,
                max_pages=max_pages,
            )
            continue

        symbol_report = run_public_kline_matrix_fetch(
            exchange=exchange,
            symbol=symbol,
            timeframes=parsed_timeframes,
            required_timeframes=parsed_required_timeframes,
            target_bars=target_bars,
            page_limit=page_limit,
            max_pages=max_pages,
            output_dir=output_dir,
            summary_output=symbol_summary_output,
            timestamp=generated_at,
            timeout=timeout,
            max_retries=max_retries,
            adapter=active_adapter,
            base_url=base_url,
        )
        symbol_report["market_type"] = market_type
        symbol_report["symbol_summary_output_path"] = str(symbol_summary_output)
        if symbol in discovery_metadata_by_symbol:
            symbol_report["discovery_metadata"] = discovery_metadata_by_symbol[symbol]
        symbol_reports[symbol] = symbol_report

        if _should_print_progress(
            progress_interval_seconds=progress_interval_seconds,
            now_monotonic=time.monotonic(),
            last_progress_monotonic=last_progress_monotonic,
            index=index,
            total=len(parsed_symbols),
        ):
            last_progress_monotonic = time.monotonic()
            _print_progress(
                completed=index,
                total=len(parsed_symbols),
                symbol=symbol,
                elapsed_seconds=last_progress_monotonic - started_monotonic,
            )

    elapsed_seconds = time.monotonic() - started_monotonic
    report = _summarize_universe(
        generated_at=generated_at,
        exchange=exchange,
        market_type=market_type,
        symbols=parsed_symbols,
        timeframes=parsed_timeframes,
        required_timeframes=parsed_required_timeframes,
        target_bars=target_bars,
        page_limit=page_limit,
        max_pages=max_pages,
        min_pass_symbols=min_pass_symbols,
        symbol_reports=symbol_reports,
        elapsed_seconds=elapsed_seconds,
        runtime_limited=runtime_limited,
        allowlist_source=_allowlist_source(discover_symbols),
        discovery_report=discovery_report,
        resume_report=resume_report,
    )
    return _write_report(report, summary_output_path) if summary_output_path else report


def _summarize_universe(
    *,
    generated_at: int,
    exchange: str,
    market_type: str,
    symbols: list[str],
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
    min_pass_symbols: int,
    symbol_reports: dict[str, dict[str, Any]],
    elapsed_seconds: float,
    runtime_limited: bool,
    allowlist_source: str,
    discovery_report: dict[str, Any],
    resume_report: dict[str, Any],
) -> dict[str, Any]:
    return _build_universe_report(
        status=_universe_status(symbol_reports, min_pass_symbols),
        message=_universe_message(symbol_reports, min_pass_symbols),
        reason_codes=_universe_reason_codes(symbol_reports, min_pass_symbols, runtime_limited),
        generated_at=generated_at,
        exchange=exchange,
        market_type=market_type,
        symbols=symbols,
        timeframes=timeframes,
        required_timeframes=required_timeframes,
        target_bars=target_bars,
        page_limit=page_limit,
        max_pages=max_pages,
        min_pass_symbols=min_pass_symbols,
        symbol_reports=symbol_reports,
        elapsed_seconds=elapsed_seconds,
        runtime_limited=runtime_limited,
        allowlist_source=allowlist_source,
        discovery_report=discovery_report,
        resume_report=resume_report,
    )


def _build_universe_report(
    *,
    status: str,
    message: str,
    reason_codes: list[str],
    generated_at: int,
    exchange: str,
    market_type: str,
    symbols: list[str],
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
    min_pass_symbols: int,
    symbol_reports: dict[str, dict[str, Any]],
    elapsed_seconds: float,
    runtime_limited: bool,
    allowlist_source: str,
    discovery_report: dict[str, Any],
    resume_report: dict[str, Any],
) -> dict[str, Any]:
    ranked_symbols = _rank_symbols(
        symbol_reports=symbol_reports,
        timeframes=timeframes,
        required_timeframes=required_timeframes,
        target_bars=target_bars,
        generated_at=generated_at,
        exchange=exchange,
        market_type=market_type,
    )
    pass_symbols = [item["symbol"] for item in ranked_symbols if item["status"] == "PASS"]
    partial_symbols = [item["symbol"] for item in ranked_symbols if item["status"] == "PARTIAL"]
    skipped_symbols = [item["symbol"] for item in ranked_symbols if item["status"] == "SKIPPED"]
    failed_symbols = [item["symbol"] for item in ranked_symbols if item["status"] == "FAIL"]
    public_universe_matrix_ready = len(pass_symbols) >= min_pass_symbols
    h_data_015_completion_gate = _h_data_015_completion_gate(
        ranked_symbols=ranked_symbols,
        timeframes=timeframes,
        target_bars=target_bars,
        min_pass_symbols=min_pass_symbols,
    )
    return {
        "schema_version": "1.0",
        "status": status,
        "message": message,
        "reason_codes": reason_codes,
        "generated_at": generated_at,
        "exchange": exchange,
        "market_type": market_type,
        "source_url_or_endpoint": PUBLIC_ENDPOINTS.get(exchange),
        "allowlist_source": allowlist_source,
        "discovery": discovery_report,
        "resume": resume_report,
        "symbols_requested": symbols,
        "timeframes_requested": timeframes,
        "required_timeframes": required_timeframes,
        "target_bars": target_bars,
        "page_limit": page_limit,
        "max_pages": max_pages,
        "min_pass_symbols": min_pass_symbols,
        "pass_symbols": pass_symbols,
        "partial_symbols": partial_symbols,
        "skipped_symbols": skipped_symbols,
        "failed_symbols": failed_symbols,
        "public_universe_matrix_ready": public_universe_matrix_ready,
        "h_data_015_ready": h_data_015_completion_gate["ready"],
        "h_data_015_completion_gate": h_data_015_completion_gate,
        "quality_summary": _quality_summary(ranked_symbols),
        "candidate_input_ranking": _candidate_input_ranking(
            ranked_symbols=ranked_symbols,
            min_pass_symbols=min_pass_symbols,
        ),
        "elapsed_seconds": round(elapsed_seconds, 6),
        "runtime_limited": runtime_limited,
        "allowlist": ranked_symbols,
        "symbols": {symbol: _symbol_payload(report) for symbol, report in symbol_reports.items()},
        "fingerprint": _sha256_payload(_fingerprint_payload(ranked_symbols)),
        "safety_flags": {
            "network_access_used": bool(symbol_reports) or bool(
                discovery_report.get("safety_flags", {}).get("network_access_used")
            ),
            "public_market_data_only": True,
            "real_credentials_read": False,
            "contains_real_credentials": False,
            "account_or_order_endpoint_called": False,
            "broker_called": False,
            "live_orders_sent": False,
            "analytics_modified_live_state": False,
        },
    }


def _h_data_015_completion_gate(
    *,
    ranked_symbols: list[dict[str, Any]],
    timeframes: list[str],
    target_bars: int,
    min_pass_symbols: int,
) -> dict[str, Any]:
    requested_timeframes = set(timeframes)
    required_timeframes = list(H_DATA_015_COMPLETION_TIMEFRAMES)
    target_bars_complete = target_bars >= H_DATA_015_MIN_COMPLETION_TARGET_BARS
    requested_timeframes_complete = set(required_timeframes).issubset(requested_timeframes)
    ready_symbols: list[str] = []
    blocked_symbols: list[dict[str, Any]] = []

    for item in ranked_symbols:
        symbol_gate = _h_data_015_symbol_gate(
            item=item,
            required_timeframes=required_timeframes,
            min_target_bars=H_DATA_015_MIN_COMPLETION_TARGET_BARS,
        )
        if symbol_gate["ready"]:
            ready_symbols.append(item["symbol"])
        elif len(blocked_symbols) < 20:
            blocked_symbols.append(symbol_gate)

    ready = (
        target_bars_complete
        and requested_timeframes_complete
        and len(ready_symbols) >= min_pass_symbols
    )
    reason_codes: set[str] = set()
    if not ranked_symbols:
        reason_codes.add("no_symbols_evaluated")
    if not requested_timeframes_complete:
        reason_codes.add("completion_timeframes_not_requested")
    if not target_bars_complete:
        reason_codes.add("target_bars_below_h_data_015_completion_minimum")
    if len(ready_symbols) < min_pass_symbols:
        reason_codes.add("h_data_015_ready_symbol_count_below_minimum")
    if ready:
        reason_codes.add("h_data_015_completion_gate_ready")

    return {
        "schema_version": "1.0",
        "ready": ready,
        "min_ready_symbols": min_pass_symbols,
        "ready_symbol_count": len(ready_symbols),
        "ready_symbols": ready_symbols,
        "required_timeframes": required_timeframes,
        "requested_timeframes_complete": requested_timeframes_complete,
        "min_target_bars": H_DATA_015_MIN_COMPLETION_TARGET_BARS,
        "target_bars_requested": target_bars,
        "target_bars_complete": target_bars_complete,
        "blocked_symbols": blocked_symbols,
        "reason_codes": sorted(reason_codes),
    }


def _h_data_015_symbol_gate(
    *,
    item: dict[str, Any],
    required_timeframes: list[str],
    min_target_bars: int,
) -> dict[str, Any]:
    timeframe_reports = item.get("timeframes") or {}
    missing_timeframes = [
        timeframe for timeframe in required_timeframes if timeframe not in timeframe_reports
    ]
    non_pass_timeframes = [
        timeframe
        for timeframe in required_timeframes
        if timeframe in timeframe_reports
        and timeframe_reports.get(timeframe, {}).get("status") != "PASS"
    ]
    insufficient_timeframes = [
        timeframe
        for timeframe in required_timeframes
        if timeframe in timeframe_reports
        and int(timeframe_reports.get(timeframe, {}).get("bar_count") or 0) < min_target_bars
    ]
    ready = (
        item.get("status") == "PASS"
        and not missing_timeframes
        and not non_pass_timeframes
        and not insufficient_timeframes
    )
    return {
        "symbol": item.get("symbol"),
        "status": item.get("status"),
        "ready": ready,
        "missing_completion_timeframes": missing_timeframes,
        "non_pass_completion_timeframes": non_pass_timeframes,
        "insufficient_completion_timeframes": insufficient_timeframes,
    }


def _rank_symbols(
    *,
    symbol_reports: dict[str, dict[str, Any]],
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    generated_at: int,
    exchange: str,
    market_type: str,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for symbol, report in symbol_reports.items():
        metrics = _symbol_metrics(
            report,
            timeframes=timeframes,
            required_timeframes=required_timeframes,
            target_bars=target_bars,
        )
        ranked.append(
            {
                "symbol": symbol,
                "exchange": exchange,
                "market_type": market_type,
                "source_url_or_endpoint": PUBLIC_ENDPOINTS.get(exchange),
                "fetched_at": generated_at,
                "status": report.get("status"),
                "reason_codes": report.get("reason_codes", []),
                "quality_score": metrics["quality_score"],
                "data_quality_score": metrics["data_quality_score"],
                "coverage_score": metrics["coverage_score"],
                "timeframe_coverage_score": metrics["timeframe_coverage_score"],
                "required_timeframe_coverage_score": metrics[
                    "required_timeframe_coverage_score"
                ],
                "bar_coverage_score": metrics["bar_coverage_score"],
                "tradeability_proxy": metrics["tradeability_proxy"],
                "pass_timeframe_count": metrics["pass_timeframe_count"],
                "required_pass_timeframe_count": metrics["required_pass_timeframe_count"],
                "requested_timeframe_count": len(timeframes),
                "total_bar_count": metrics["total_bar_count"],
                "first_timestamp": metrics["first_timestamp"],
                "last_timestamp": metrics["last_timestamp"],
                "quality_diagnostics": metrics["quality_diagnostics"],
                "sha256": report.get("fingerprint") or _sha256_payload(_symbol_payload(report)),
                "output_path": report.get("symbol_summary_output_path"),
                "discovery_metadata": report.get("discovery_metadata"),
                "timeframes": report.get("timeframes", {}),
            }
        )
    ranked.sort(
        key=lambda item: (
            -item["data_quality_score"],
            -item["quality_score"],
            -item["coverage_score"],
            -item["tradeability_proxy"],
            item["symbol"],
        )
    )
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked


def _symbol_metrics(
    report: dict[str, Any],
    *,
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
) -> dict[str, Any]:
    timeframe_reports = report.get("timeframes", {})
    requested_count = max(1, len(timeframes))
    required_count = max(1, len(required_timeframes))
    pass_count = 0
    required_pass_count = 0
    quality_scores: list[float] = []
    bar_coverage_scores: list[float] = []
    total_bar_count = 0
    first_timestamps: list[int] = []
    last_timestamps: list[int] = []
    issue_code_counts: dict[str, int] = {}
    issue_count = 0
    fatal_issue_count = 0
    min_required_bar_count: int | None = None

    for timeframe in timeframes:
        timeframe_report = timeframe_reports.get(timeframe, {})
        status = timeframe_report.get("status")
        bar_count = int(timeframe_report.get("bar_count") or 0)
        total_bar_count += bar_count
        if target_bars > 0:
            bar_coverage_scores.append(min(1.0, bar_count / target_bars))
        else:
            bar_coverage_scores.append(0.0)
        if status == "PASS":
            pass_count += 1
            quality_scores.append(1.0)
        elif status == "PARTIAL":
            quality_scores.append(0.5)
        elif status == "SKIPPED":
            quality_scores.append(0.25)
        else:
            quality_scores.append(0.0)
        if timeframe in required_timeframes:
            if status == "PASS":
                required_pass_count += 1
            if min_required_bar_count is None:
                min_required_bar_count = bar_count
            else:
                min_required_bar_count = min(min_required_bar_count, bar_count)
        first_timestamp = timeframe_report.get("first_timestamp")
        last_timestamp = timeframe_report.get("last_timestamp")
        if isinstance(first_timestamp, int):
            first_timestamps.append(first_timestamp)
        if isinstance(last_timestamp, int):
            last_timestamps.append(last_timestamp)
        quality_report = timeframe_report.get("quality_report") or {}
        for issue in quality_report.get("issues") or []:
            code = str(issue.get("code") or "unknown_quality_issue")
            issue_code_counts[code] = issue_code_counts.get(code, 0) + 1
            issue_count += 1
            if issue.get("fatal", True):
                fatal_issue_count += 1

    quality_score = sum(quality_scores) / requested_count
    bar_coverage_score = sum(bar_coverage_scores) / requested_count
    coverage_score = pass_count / requested_count
    required_timeframe_coverage_score = required_pass_count / required_count
    fatal_quality_score = 1.0 if fatal_issue_count == 0 else 0.0
    data_quality_score = (
        quality_score
        + bar_coverage_score
        + required_timeframe_coverage_score
        + fatal_quality_score
    ) / 4.0
    # Public coverage is the only tradeability proxy in this script; no private
    # liquidity or account endpoints are queried.
    tradeability_proxy = coverage_score
    quality_reason_codes = _quality_reason_codes(
        pass_count=pass_count,
        requested_count=requested_count,
        required_pass_count=required_pass_count,
        required_count=required_count,
        bar_coverage_score=bar_coverage_score,
        fatal_issue_count=fatal_issue_count,
    )
    return {
        "quality_score": round(quality_score, 6),
        "data_quality_score": round(data_quality_score, 6),
        "coverage_score": round(coverage_score, 6),
        "timeframe_coverage_score": round(coverage_score, 6),
        "required_timeframe_coverage_score": round(required_timeframe_coverage_score, 6),
        "bar_coverage_score": round(bar_coverage_score, 6),
        "tradeability_proxy": round(tradeability_proxy, 6),
        "pass_timeframe_count": pass_count,
        "required_pass_timeframe_count": required_pass_count,
        "total_bar_count": total_bar_count,
        "first_timestamp": min(first_timestamps) if first_timestamps else None,
        "last_timestamp": max(last_timestamps) if last_timestamps else None,
        "quality_diagnostics": {
            "issue_count": issue_count,
            "fatal_issue_count": fatal_issue_count,
            "issue_code_counts": dict(sorted(issue_code_counts.items())),
            "quality_reason_codes": quality_reason_codes,
            "min_required_bar_count": min_required_bar_count,
            "requested_bar_coverage_score": round(bar_coverage_score, 6),
            "required_timeframe_coverage_score": round(
                required_timeframe_coverage_score,
                6,
            ),
            "walk_forward_min_required_bar_count": min_required_bar_count,
            "walk_forward_ready_proxy": (
                required_pass_count == required_count
                and fatal_issue_count == 0
                and min_required_bar_count is not None
                and min_required_bar_count >= target_bars
            ),
        },
    }


def _quality_reason_codes(
    *,
    pass_count: int,
    requested_count: int,
    required_pass_count: int,
    required_count: int,
    bar_coverage_score: float,
    fatal_issue_count: int,
) -> list[str]:
    reason_codes: set[str] = set()
    if pass_count < requested_count:
        reason_codes.add("not_all_requested_timeframes_passed")
    if required_pass_count < required_count:
        reason_codes.add("required_timeframe_quality_incomplete")
    if bar_coverage_score < 1.0:
        reason_codes.add("target_bar_coverage_incomplete")
    if fatal_issue_count:
        reason_codes.add("fatal_kline_quality_issues_present")
    if not reason_codes:
        reason_codes.add("public_kline_quality_ready")
    return sorted(reason_codes)


def _quality_summary(ranked_symbols: list[dict[str, Any]]) -> dict[str, Any]:
    issue_code_counts: dict[str, int] = {}
    ready_symbols: list[str] = []
    for item in ranked_symbols:
        diagnostics = item.get("quality_diagnostics") or {}
        for code, count in (diagnostics.get("issue_code_counts") or {}).items():
            issue_code_counts[str(code)] = issue_code_counts.get(str(code), 0) + int(count)
        if diagnostics.get("walk_forward_ready_proxy") is True:
            ready_symbols.append(item["symbol"])

    reason_codes: set[str] = set()
    if not ranked_symbols:
        reason_codes.add("no_symbols_evaluated")
    if issue_code_counts:
        reason_codes.add("symbol_quality_issues_present")
    if len(ready_symbols) < len(ranked_symbols):
        reason_codes.add("some_symbols_not_walk_forward_ready_proxy")
    if not reason_codes:
        reason_codes.add("public_universe_quality_ready")

    return {
        "schema_version": "1.0",
        "evaluated_symbol_count": len(ranked_symbols),
        "walk_forward_ready_proxy_symbol_count": len(ready_symbols),
        "walk_forward_ready_proxy_symbols": ready_symbols,
        "total_bar_count": sum(int(item.get("total_bar_count") or 0) for item in ranked_symbols),
        "issue_code_counts": dict(sorted(issue_code_counts.items())),
        "reason_codes": sorted(reason_codes),
    }


def _candidate_input_ranking(
    *,
    ranked_symbols: list[dict[str, Any]],
    min_pass_symbols: int,
) -> dict[str, Any]:
    candidates = [
        _candidate_input_summary(item)
        for item in ranked_symbols
        if item.get("status") == "PASS"
    ]
    recommended = [
        item for item in candidates if item.get("walk_forward_ready_proxy") is True
    ]
    reason_codes: set[str] = set()
    if not ranked_symbols:
        reason_codes.add("no_candidate_inputs_evaluated")
    if not candidates:
        reason_codes.add("no_pass_candidate_inputs")
    if len(recommended) < min_pass_symbols:
        reason_codes.add("recommended_symbol_count_below_minimum")
    if candidates and not recommended:
        reason_codes.add("no_walk_forward_ready_proxy_candidates")
    if len(recommended) >= min_pass_symbols:
        reason_codes.add("candidate_input_ranking_ready")

    return {
        "schema_version": "1.0",
        "rank_criteria": [
            "data_quality_score_desc",
            "quality_score_desc",
            "coverage_score_desc",
            "tradeability_proxy_desc",
            "symbol_asc",
        ],
        "min_recommended_symbols": min_pass_symbols,
        "candidate_count": len(candidates),
        "recommended_symbol_count": len(recommended),
        "recommended_symbols": [item["symbol"] for item in recommended],
        "top_candidate": recommended[0] if recommended else (candidates[0] if candidates else None),
        "candidates": candidates,
        "reason_codes": sorted(reason_codes),
        "fingerprint": _sha256_payload(candidates),
    }


def _candidate_input_summary(item: dict[str, Any]) -> dict[str, Any]:
    diagnostics = item.get("quality_diagnostics") or {}
    discovery_metadata = item.get("discovery_metadata") or {}
    summary = {
        "rank": item.get("rank"),
        "symbol": item.get("symbol"),
        "exchange": item.get("exchange"),
        "market_type": item.get("market_type"),
        "status": item.get("status"),
        "data_quality_score": item.get("data_quality_score"),
        "quality_score": item.get("quality_score"),
        "coverage_score": item.get("coverage_score"),
        "timeframe_coverage_score": item.get("timeframe_coverage_score"),
        "required_timeframe_coverage_score": item.get("required_timeframe_coverage_score"),
        "bar_coverage_score": item.get("bar_coverage_score"),
        "tradeability_proxy": item.get("tradeability_proxy"),
        "public_discovery_tradeability_proxy": discovery_metadata.get("tradeability_proxy"),
        "public_discovery_turnover_24h": discovery_metadata.get("turnover_24h"),
        "pass_timeframe_count": item.get("pass_timeframe_count"),
        "required_pass_timeframe_count": item.get("required_pass_timeframe_count"),
        "requested_timeframe_count": item.get("requested_timeframe_count"),
        "total_bar_count": item.get("total_bar_count"),
        "first_timestamp": item.get("first_timestamp"),
        "last_timestamp": item.get("last_timestamp"),
        "walk_forward_ready_proxy": diagnostics.get("walk_forward_ready_proxy") is True,
        "walk_forward_min_required_bar_count": diagnostics.get(
            "walk_forward_min_required_bar_count"
        ),
        "quality_reason_codes": diagnostics.get("quality_reason_codes", []),
        "selection_reason_codes": _candidate_input_reason_codes(
            item=item,
            diagnostics=diagnostics,
            discovery_metadata=discovery_metadata,
        ),
        "output_path": item.get("output_path"),
        "sha256": item.get("sha256"),
    }
    return summary


def _candidate_input_reason_codes(
    *,
    item: dict[str, Any],
    diagnostics: dict[str, Any],
    discovery_metadata: dict[str, Any],
) -> list[str]:
    reason_codes: set[str] = set()
    if item.get("status") == "PASS":
        reason_codes.add("symbol_history_passed")
    else:
        reason_codes.add("symbol_history_not_pass")
    if diagnostics.get("walk_forward_ready_proxy") is True:
        reason_codes.add("walk_forward_ready_proxy")
    else:
        reason_codes.add("not_walk_forward_ready_proxy")
    if float(item.get("required_timeframe_coverage_score") or 0.0) >= 1.0:
        reason_codes.add("required_timeframes_ready")
    else:
        reason_codes.add("required_timeframes_incomplete")
    if float(item.get("bar_coverage_score") or 0.0) >= 1.0:
        reason_codes.add("target_bar_coverage_complete")
    else:
        reason_codes.add("target_bar_coverage_incomplete")
    if int(diagnostics.get("fatal_issue_count") or 0) > 0:
        reason_codes.add("fatal_kline_quality_issues_present")
    if discovery_metadata.get("tradeability_proxy") is not None:
        reason_codes.add("public_discovery_tradeability_available")
    else:
        reason_codes.add("no_public_discovery_tradeability_proxy")
    return sorted(reason_codes)


def _symbol_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "message": report.get("message"),
        "reason_codes": report.get("reason_codes", []),
        "symbol_summary_output_path": report.get("symbol_summary_output_path"),
        "pass_timeframes": report.get("pass_timeframes", []),
        "skipped_timeframes": report.get("skipped_timeframes", []),
        "failed_timeframes": report.get("failed_timeframes", []),
        "minimum_timeframes_passed": report.get("minimum_timeframes_passed", False),
        "timeframes": report.get("timeframes", {}),
        "discovery_metadata": report.get("discovery_metadata"),
        "safety_flags": report.get("safety_flags"),
    }


def _fingerprint_payload(ranked_symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": item["symbol"],
            "exchange": item["exchange"],
            "market_type": item["market_type"],
            "status": item["status"],
            "quality_score": item["quality_score"],
            "coverage_score": item["coverage_score"],
            "discovery_metadata": item.get("discovery_metadata"),
            "timeframes": {
                timeframe: {
                    "status": report.get("status"),
                    "bar_count": report.get("bar_count"),
                    "sha256": report.get("sha256"),
                }
                for timeframe, report in item["timeframes"].items()
            },
        }
        for item in ranked_symbols
    ]


def _universe_status(
    symbol_reports: dict[str, dict[str, Any]],
    min_pass_symbols: int,
) -> str:
    if not symbol_reports:
        return "FAIL"
    pass_count = sum(1 for report in symbol_reports.values() if report.get("status") == "PASS")
    if pass_count >= min_pass_symbols:
        return "PASS"
    if pass_count:
        return "PARTIAL"
    if any(report.get("status") == "FAIL" for report in symbol_reports.values()):
        return "FAIL"
    return "SKIPPED"


def _universe_message(
    symbol_reports: dict[str, dict[str, Any]],
    min_pass_symbols: int,
) -> str:
    status = _universe_status(symbol_reports, min_pass_symbols)
    if status == "PASS":
        return "required public multi-symbol universe history fetched and validated"
    if status == "PARTIAL":
        return "public multi-symbol universe history partially fetched"
    if status == "SKIPPED":
        return "public multi-symbol universe history fetch was skipped"
    return "public multi-symbol universe history fetch failed validation"


def _universe_reason_codes(
    symbol_reports: dict[str, dict[str, Any]],
    min_pass_symbols: int,
    runtime_limited: bool,
) -> list[str]:
    reason_codes: set[str] = set()
    pass_count = 0
    for report in symbol_reports.values():
        if report.get("status") == "PASS":
            pass_count += 1
        reason_codes.update(report.get("reason_codes", []))
    if pass_count < min_pass_symbols:
        reason_codes.add("min_pass_symbols_not_reached")
    if runtime_limited:
        reason_codes.add("max_runtime_seconds_reached")
    return sorted(reason_codes)


def _discover_public_symbols(
    *,
    generated_at: int,
    exchange: str,
    market_type: str,
    quote_currencies: list[str],
    max_symbols: int,
    min_turnover_24h: float,
    adapter: Any | None,
    timeout: float,
    max_retries: int,
    base_url: str | None,
) -> tuple[list[str], dict[str, Any], Any | None]:
    try:
        active_adapter = adapter or _build_adapter(
            exchange=exchange,
            timeout=timeout,
            max_retries=max_retries,
            base_url=base_url,
        )
        raw_instruments = _load_public_discovery_instruments(
            adapter=active_adapter,
            exchange=exchange,
            market_type=market_type,
        )
    except (BinanceAdapterError, OKXAdapterError, OSError, TimeoutError) as exc:
        return [], _unavailable_discovery_report(
            generated_at=generated_at,
            exchange=exchange,
            market_type=market_type,
            quote_currencies=quote_currencies,
            max_symbols=max_symbols,
            min_turnover_24h=min_turnover_24h,
            exc=exc,
        ), adapter

    candidates, rejected = _filter_discovery_instruments(
        raw_instruments=raw_instruments,
        exchange=exchange,
        market_type=market_type,
        quote_currencies=quote_currencies,
        min_turnover_24h=min_turnover_24h,
    )
    selected = candidates[:max_symbols]
    symbols = [candidate["symbol"] for candidate in selected]
    reason_codes: list[str] = []
    if not symbols:
        reason_codes.append("no_public_symbols_discovered")
    if len(symbols) < max_symbols:
        reason_codes.append("discovered_symbol_count_below_requested_max")

    report = {
        "schema_version": "1.0",
        "status": "PASS" if symbols else "SKIPPED",
        "message": (
            "public symbol universe discovered"
            if symbols
            else "public symbol universe discovery returned no matching symbols"
        ),
        "reason_codes": reason_codes,
        "generated_at": generated_at,
        "exchange": exchange,
        "market_type": market_type,
        "source_url_or_endpoint": _discovery_source_endpoint(exchange),
        "source_endpoints": _discovery_source_endpoints(exchange),
        "quote_currencies": quote_currencies,
        "max_symbols": max_symbols,
        "min_turnover_24h": min_turnover_24h,
        "raw_instrument_count": len(raw_instruments),
        "candidate_count": len(candidates),
        "selected_symbol_count": len(symbols),
        "selected_symbols": symbols,
        "selected": selected,
        "rejected": rejected,
        "fingerprint": _sha256_payload(
            {
                "exchange": exchange,
                "market_type": market_type,
                "selected": selected,
            }
        ),
        "safety_flags": _public_only_safety_flags(network_access_used=True),
    }
    return symbols, report, active_adapter


def _load_public_discovery_instruments(
    *,
    adapter: Any,
    exchange: str,
    market_type: str,
) -> list[Any]:
    if hasattr(adapter, "discover_public_symbols"):
        return list(adapter.discover_public_symbols(exchange=exchange, market_type=market_type))
    if exchange == "okx" and hasattr(adapter, "get_universe_instruments"):
        return list(
            adapter.get_universe_instruments(
                instrument_type=_okx_instrument_type(market_type),
                include_tickers=True,
            )
        )
    if exchange == "binance" and hasattr(adapter, "get_exchange_info"):
        response = adapter.get_exchange_info()
        data = response.get("data", [])
        if len(data) == 1 and isinstance(data[0], dict) and isinstance(data[0].get("symbols"), list):
            instruments = [item for item in data[0]["symbols"] if isinstance(item, dict)]
            return _merge_binance_24h_tickers(adapter, instruments)
        if isinstance(response.get("raw"), dict) and isinstance(response["raw"].get("symbols"), list):
            instruments = [item for item in response["raw"]["symbols"] if isinstance(item, dict)]
            return _merge_binance_24h_tickers(adapter, instruments)
        return _merge_binance_24h_tickers(adapter, [item for item in data if isinstance(item, dict)])
    raise RuntimeError(f"adapter does not support public symbol discovery for {exchange}")


def _filter_discovery_instruments(
    *,
    raw_instruments: list[Any],
    exchange: str,
    market_type: str,
    quote_currencies: list[str],
    min_turnover_24h: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    rejected_examples: list[dict[str, Any]] = []
    rejected_counts: dict[str, int] = {}
    for raw in raw_instruments:
        instrument = _normalize_discovery_instrument(
            raw=raw,
            exchange=exchange,
            market_type=market_type,
        )
        if instrument is None:
            continue
        rejection_code = _discovery_rejection_code(
            instrument=instrument,
            quote_currencies=quote_currencies,
            min_turnover_24h=min_turnover_24h,
        )
        if rejection_code is not None:
            rejected_counts[rejection_code] = rejected_counts.get(rejection_code, 0) + 1
            if len(rejected_examples) < 20:
                rejected_examples.append(
                    {
                        "symbol": instrument["symbol"],
                        "reason_code": rejection_code,
                        "status": instrument["status"],
                        "quote_currency": instrument["quote_currency"],
                        "turnover_24h": instrument["turnover_24h"],
                    }
                )
            continue
        candidates.append(instrument)

    candidates.sort(
        key=lambda item: (
            -float(item.get("turnover_24h") or 0.0),
            -float(item.get("volume_24h") or 0.0),
            item["symbol"],
        )
    )
    rejected = [
        {"reason_code": reason_code, "count": count}
        for reason_code, count in sorted(rejected_counts.items())
    ]
    if rejected_examples:
        rejected.append({"reason_code": "examples", "items": rejected_examples})
    return candidates, rejected


def _merge_binance_24h_tickers(adapter: Any, instruments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not hasattr(adapter, "_public_request"):
        return instruments
    try:
        response = adapter._public_request("GET", "/api/v3/ticker/24hr")
    except (BinanceAdapterError, OSError, TimeoutError):
        return instruments
    tickers_by_symbol = {
        str(item.get("symbol") or "").upper(): item
        for item in response.get("data", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    merged: list[dict[str, Any]] = []
    for instrument in instruments:
        symbol = str(instrument.get("symbol") or "").upper()
        ticker = tickers_by_symbol.get(symbol, {})
        item = dict(instrument)
        if ticker:
            item["volume_24h"] = ticker.get("volume")
            item["turnover_24h"] = ticker.get("quoteVolume")
            item["last_price"] = ticker.get("lastPrice")
        merged.append(item)
    return merged


def _normalize_discovery_instrument(
    *,
    raw: Any,
    exchange: str,
    market_type: str,
) -> dict[str, Any] | None:
    if hasattr(raw, "model_dump"):
        payload = raw.model_dump(mode="json")
    elif hasattr(raw, "dict"):
        payload = raw.dict()
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        return None

    if exchange == "binance":
        symbol = _text_value(payload, "symbol").upper()
        quote_currency = _text_value(payload, "quoteAsset", "quote_currency", "quoteCurrency").upper()
        base_currency = _text_value(payload, "baseAsset", "base_currency", "baseCurrency").upper()
        status = _text_value(payload, "status").lower()
        volume_24h = _optional_float(payload, "volume_24h", "volume24h", "quoteVolume")
        turnover_24h = _optional_float(payload, "turnover_24h", "turnover24h", "quoteVolume")
        instrument_type = "SPOT"
    else:
        symbol = _text_value(payload, "symbol", "instId", "inst_id").upper()
        derived_base_currency, derived_quote_currency = _derive_base_quote_from_symbol(symbol)
        quote_currency = (
            _text_value(
                payload,
                "quote_currency",
                "quoteCurrency",
                "quoteCcy",
                "settleCcy",
            ).upper()
            or derived_quote_currency
        )
        base_currency = (
            _text_value(payload, "base_currency", "baseCurrency", "baseCcy").upper()
            or derived_base_currency
        )
        status = _text_value(payload, "status", "state").lower()
        volume_24h = _optional_float(payload, "volume_24h", "volume24h", "vol24h")
        turnover_24h = _optional_float(
            payload,
            "turnover_24h",
            "turnover24h",
            "volCcy24h",
            "volUsd24h",
        )
        instrument_type = _text_value(
            payload,
            "instrument_type",
            "instrumentType",
            "instType",
        ).upper()

    if not symbol:
        return None
    return {
        "symbol": symbol,
        "exchange": exchange,
        "market_type": market_type,
        "instrument_type": instrument_type or market_type.upper(),
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "status": status or "unknown",
        "volume_24h": volume_24h,
        "turnover_24h": turnover_24h,
        "tradeability_proxy": float(turnover_24h or volume_24h or 0.0),
    }


def _discovery_rejection_code(
    *,
    instrument: dict[str, Any],
    quote_currencies: list[str],
    min_turnover_24h: float,
) -> str | None:
    if instrument["status"] not in {"trading", "live"}:
        return "symbol_status_not_tradeable"
    if quote_currencies and instrument["quote_currency"] not in quote_currencies:
        return "quote_currency_not_allowed"
    turnover_24h = instrument.get("turnover_24h")
    if turnover_24h is not None and float(turnover_24h) < min_turnover_24h:
        return "turnover_24h_below_minimum"
    return None


def _discovery_metadata_by_symbol(discovery_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("symbol")).upper(): item
        for item in discovery_report.get("selected", [])
        if item.get("symbol")
    }


def _explicit_symbol_discovery_report(
    *,
    generated_at: int,
    exchange: str,
    market_type: str,
    symbols: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "SKIPPED",
        "message": "symbol discovery not requested; using explicit symbols",
        "reason_codes": ["explicit_symbols_used"],
        "generated_at": generated_at,
        "exchange": exchange,
        "market_type": market_type,
        "source_url_or_endpoint": None,
        "source_endpoints": [],
        "selected_symbol_count": len(symbols),
        "selected_symbols": symbols,
        "selected": [],
        "rejected": [],
        "fingerprint": _sha256_payload(
            {"exchange": exchange, "market_type": market_type, "symbols": symbols}
        ),
        "safety_flags": _public_only_safety_flags(network_access_used=False),
    }


def _config_error_discovery_report(
    *,
    generated_at: int,
    exchange: str,
    market_type: str,
    discover_symbols: bool,
    reason_code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "FAIL",
        "message": message,
        "reason_codes": [reason_code],
        "generated_at": generated_at,
        "exchange": exchange,
        "market_type": market_type,
        "source_url_or_endpoint": _discovery_source_endpoint(exchange) if discover_symbols else None,
        "source_endpoints": _discovery_source_endpoints(exchange) if discover_symbols else [],
        "selected_symbol_count": 0,
        "selected_symbols": [],
        "selected": [],
        "rejected": [],
        "safety_flags": _public_only_safety_flags(network_access_used=False),
    }


def _unavailable_discovery_report(
    *,
    generated_at: int,
    exchange: str,
    market_type: str,
    quote_currencies: list[str],
    max_symbols: int,
    min_turnover_24h: float,
    exc: BaseException,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "SKIPPED",
        "message": "public symbol discovery endpoint was unavailable",
        "reason_codes": ["public_symbol_discovery_unavailable"],
        "generated_at": generated_at,
        "exchange": exchange,
        "market_type": market_type,
        "source_url_or_endpoint": _discovery_source_endpoint(exchange),
        "source_endpoints": _discovery_source_endpoints(exchange),
        "quote_currencies": quote_currencies,
        "max_symbols": max_symbols,
        "min_turnover_24h": min_turnover_24h,
        "selected_symbol_count": 0,
        "selected_symbols": [],
        "selected": [],
        "rejected": [],
        "error": {
            "category": exc.__class__.__name__,
            "message": str(exc)[:500],
        },
        "safety_flags": _public_only_safety_flags(network_access_used=True),
    }


def _allowlist_source(discover_symbols: bool) -> str:
    return "public_symbol_discovery" if discover_symbols else "explicit_symbols"


def _discovery_source_endpoint(exchange: str) -> str | None:
    if exchange == "binance":
        return "https://api.binance.com/api/v3/exchangeInfo"
    if exchange == "okx":
        return "https://www.okx.com/api/v5/public/instruments"
    return None


def _discovery_source_endpoints(exchange: str) -> list[str]:
    if exchange == "binance":
        return [
            "https://api.binance.com/api/v3/exchangeInfo",
            "https://api.binance.com/api/v3/ticker/24hr",
        ]
    if exchange == "okx":
        return [
            "https://www.okx.com/api/v5/public/instruments",
            "https://www.okx.com/api/v5/market/tickers",
        ]
    return []


def _okx_instrument_type(market_type: str) -> str:
    normalized = market_type.strip().upper()
    if normalized in {"SPOT", "SWAP", "FUTURES", "OPTION"}:
        return normalized
    return "SPOT"


def _text_value(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _optional_float(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _derive_base_quote_from_symbol(symbol: str) -> tuple[str, str]:
    parts = [part for part in symbol.split("-") if part]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""


def _public_only_safety_flags(*, network_access_used: bool) -> dict[str, bool]:
    return {
        "network_access_used": network_access_used,
        "public_market_data_only": True,
        "real_credentials_read": False,
        "contains_real_credentials": False,
        "account_or_order_endpoint_called": False,
        "broker_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
    }


def _runtime_limited_symbol_report(
    *,
    generated_at: int,
    exchange: str,
    market_type: str,
    symbol: str,
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "SKIPPED",
        "message": "public market universe fetch stopped by max runtime before symbol fetch",
        "reason_codes": ["max_runtime_seconds_reached"],
        "generated_at": generated_at,
        "exchange": exchange,
        "market_type": market_type,
        "symbol": symbol,
        "source_url_or_endpoint": PUBLIC_ENDPOINTS.get(exchange),
        "timeframes_requested": timeframes,
        "required_timeframes": required_timeframes,
        "target_bars": target_bars,
        "page_limit": page_limit,
        "max_pages": max_pages,
        "pass_timeframes": [],
        "skipped_timeframes": timeframes,
        "failed_timeframes": [],
        "minimum_timeframes_passed": False,
        "timeframes": {},
        "safety_flags": {
            "network_access_used": False,
            "public_market_data_only": True,
            "real_credentials_read": False,
            "contains_real_credentials": False,
            "account_or_order_endpoint_called": False,
            "broker_called": False,
            "live_orders_sent": False,
            "analytics_modified_live_state": False,
        },
    }


def _initial_resume_report(*, enabled: bool, generated_at: int) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "enabled": enabled,
        "generated_at": generated_at,
        "reused_symbol_count": 0,
        "missed_symbol_count": 0,
        "reused_symbols": [],
        "missed_symbols": [],
        "safety_flags": _public_only_safety_flags(network_access_used=False),
    }


def _load_resumable_symbol_summary(
    *,
    path: Path,
    generated_at: int,
    exchange: str,
    symbol: str,
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
) -> tuple[dict[str, Any] | None, str]:
    if not path.exists():
        return None, "resume_summary_not_found"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "resume_summary_unreadable"
    if not isinstance(report, dict):
        return None, "resume_summary_invalid"
    if not _symbol_summary_matches_request(
        report=report,
        exchange=exchange,
        symbol=symbol,
        timeframes=timeframes,
        required_timeframes=required_timeframes,
        target_bars=target_bars,
        page_limit=page_limit,
        max_pages=max_pages,
    ):
        return None, "resume_summary_contract_mismatch"
    if report.get("status") not in {"PASS", "PARTIAL", "SKIPPED", "FAIL"}:
        return None, "resume_summary_invalid_status"

    resumed = dict(report)
    resumed["generated_at"] = generated_at
    reason_codes = list(resumed.get("reason_codes") or [])
    if "resumed_existing_symbol_summary" not in reason_codes:
        reason_codes.append("resumed_existing_symbol_summary")
    resumed["reason_codes"] = sorted(reason_codes)
    return resumed, "resume_summary_reused"


def _symbol_summary_matches_request(
    *,
    report: dict[str, Any],
    exchange: str,
    symbol: str,
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
) -> bool:
    return (
        report.get("exchange") == exchange
        and str(report.get("symbol") or "").upper() == symbol
        and report.get("timeframes_requested") == timeframes
        and report.get("required_timeframes") == required_timeframes
        and report.get("target_bars") == target_bars
        and report.get("page_limit") == page_limit
        and report.get("max_pages") == max_pages
        and bool(report.get("safety_flags", {}).get("public_market_data_only")) is True
        and bool(report.get("safety_flags", {}).get("real_credentials_read")) is False
        and bool(report.get("safety_flags", {}).get("broker_called")) is False
        and bool(report.get("safety_flags", {}).get("live_orders_sent")) is False
    )


def _record_resume_reuse(
    *,
    resume_report: dict[str, Any],
    symbol: str,
    path: Path,
) -> None:
    resume_report["reused_symbol_count"] += 1
    resume_report["reused_symbols"].append(
        {
            "symbol": symbol,
            "path": str(path),
            "reason_code": "resume_summary_reused",
        }
    )


def _record_resume_miss(
    *,
    resume_report: dict[str, Any],
    symbol: str,
    path: Path,
    reason_code: str,
) -> None:
    resume_report["missed_symbol_count"] += 1
    resume_report["missed_symbols"].append(
        {
            "symbol": symbol,
            "path": str(path),
            "reason_code": reason_code,
        }
    )


def _validate_universe_config(
    *,
    exchange: str,
    symbols: list[str],
    discover_symbols: bool,
    timeframes: list[str],
    required_timeframes: list[str],
    target_bars: int,
    page_limit: int,
    max_pages: int | None,
    min_pass_symbols: int,
    max_runtime_seconds: float | None,
    progress_interval_seconds: float | None,
    max_discovered_symbols: int,
    discovery_quote_currencies: list[str],
    min_discovery_turnover_24h: float,
) -> dict[str, str] | None:
    if exchange not in PUBLIC_ENDPOINTS:
        return {
            "reason_code": "unsupported_exchange",
            "message": f"unsupported exchange: {exchange}",
        }
    if not symbols and not discover_symbols:
        return {
            "reason_code": "missing_symbols",
            "message": "at least one symbol is required unless discovery is enabled",
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
    if min_pass_symbols <= 0:
        return {
            "reason_code": "invalid_min_pass_symbols",
            "message": "min-pass-symbols must be positive",
        }
    if max_runtime_seconds is not None and max_runtime_seconds <= 0:
        return {
            "reason_code": "invalid_max_runtime_seconds",
            "message": "max-runtime-seconds must be positive when provided",
        }
    if progress_interval_seconds is not None and progress_interval_seconds <= 0:
        return {
            "reason_code": "invalid_progress_interval_seconds",
            "message": "progress-interval-seconds must be positive when provided",
        }
    if discover_symbols and max_discovered_symbols <= 0:
        return {
            "reason_code": "invalid_max_discovered_symbols",
            "message": "max-discovered-symbols must be positive when discovery is enabled",
        }
    if discover_symbols and not discovery_quote_currencies:
        return {
            "reason_code": "missing_discovery_quote_currencies",
            "message": "at least one discovery quote currency is required when discovery is enabled",
        }
    if min_discovery_turnover_24h < 0.0:
        return {
            "reason_code": "invalid_min_discovery_turnover_24h",
            "message": "min-discovery-turnover-24h must be non-negative",
        }
    return None


def _symbol_summary_output_path(
    *,
    output_dir: Path,
    exchange: str,
    symbol: str,
    target_bars: int,
) -> Path:
    return output_dir / (
        f"{exchange}-{_file_symbol(symbol)}-mtf-{_target_label(target_bars)}-latest.json"
    )


def _should_print_progress(
    *,
    progress_interval_seconds: float | None,
    now_monotonic: float,
    last_progress_monotonic: float,
    index: int,
    total: int,
) -> bool:
    if progress_interval_seconds is None:
        return False
    if index == total:
        return True
    return now_monotonic - last_progress_monotonic >= progress_interval_seconds


def _print_progress(
    *,
    completed: int,
    total: int,
    symbol: str,
    elapsed_seconds: float,
) -> None:
    payload = {
        "event": "public_market_universe_progress",
        "completed": completed,
        "total": total,
        "percent": round((completed / max(1, total)) * 100.0, 2),
        "last_symbol": symbol,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)


def _sha256_payload(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _write_report(report: dict[str, Any], output_path: Path | None) -> dict[str, Any]:
    if output_path is None:
        return report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", choices=sorted(PUBLIC_ENDPOINTS), default="binance")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--required-timeframes", default=",".join(DEFAULT_REQUIRED_TIMEFRAMES))
    parser.add_argument("--target-bars", type=int, default=10000)
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--min-pass-symbols", type=int, default=2)
    parser.add_argument("--market-type", default="spot")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--summary-output", default=str(DEFAULT_SUMMARY_OUTPUT_PATH))
    parser.add_argument("--timestamp", type=int)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--base-url")
    parser.add_argument("--max-runtime-seconds", type=float)
    parser.add_argument("--progress-interval-seconds", type=float)
    parser.add_argument(
        "--discover-symbols",
        action="store_true",
        help="Discover the symbol allowlist from public market metadata before fetching history.",
    )
    parser.add_argument("--max-discovered-symbols", type=int, default=10)
    parser.add_argument(
        "--discovery-quote-currencies",
        default=",".join(DEFAULT_DISCOVERY_QUOTE_CURRENCIES),
    )
    parser.add_argument("--min-discovery-turnover-24h", type=float, default=0.0)
    parser.add_argument(
        "--resume-existing-symbol-summaries",
        action="store_true",
        help=(
            "Reuse matching per-symbol matrix summary files in the output directory "
            "instead of refetching those symbols."
        ),
    )
    args = parser.parse_args(argv)

    report = run_public_market_universe_fetch(
        exchange=args.exchange,
        symbols=None if args.discover_symbols else args.symbols,
        timeframes=args.timeframes,
        required_timeframes=args.required_timeframes,
        target_bars=args.target_bars,
        page_limit=args.page_limit,
        max_pages=args.max_pages,
        min_pass_symbols=args.min_pass_symbols,
        market_type=args.market_type,
        output_dir=args.output_dir,
        summary_output=args.summary_output,
        timestamp=args.timestamp,
        timeout=args.timeout,
        max_retries=args.max_retries,
        base_url=args.base_url,
        max_runtime_seconds=args.max_runtime_seconds,
        progress_interval_seconds=args.progress_interval_seconds,
        discover_symbols=args.discover_symbols,
        max_discovered_symbols=args.max_discovered_symbols,
        discovery_quote_currencies=args.discovery_quote_currencies,
        min_discovery_turnover_24h=args.min_discovery_turnover_24h,
        resume_existing_symbol_summaries=args.resume_existing_symbol_summaries,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] in {"SKIPPED", "PARTIAL"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
