#!/usr/bin/env python
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.optimization.candidate_strategies import (  # noqa: E402
    SUPPORTED_CANDIDATE_STRATEGY_IDS,
    normalize_candidate_strategy_parameters,
)
from quant.optimization.external_candidates import load_external_candidate_file  # noqa: E402
from scripts.run_expanded_public_btcusdt_validation_search import (  # noqa: E402
    _strategy_parameter_grid,
)
from scripts.run_external_candidate_validation import (  # noqa: E402
    run_external_candidate_validation,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "logs/strategy-validation-artifacts/pass-artifact-discovery-latest.json"
DEFAULT_PROGRESS = PROJECT_ROOT / "logs/strategy-validation-artifacts/pass-artifact-discovery-progress-latest.jsonl"
DEFAULT_READABLE_LOG = PROJECT_ROOT / "logs/strategy-validation-artifacts/pass-artifact-discovery-readable.log"
DEFAULT_GENERATED_CANDIDATES = PROJECT_ROOT / "logs/strategy-validation-artifacts/pass-artifact-discovery-candidates-latest.json"
DEFAULT_FILTERED_MATRIX = PROJECT_ROOT / "logs/public-market-data/pass-artifact-discovery-universe-latest.json"
DEFAULT_EXTERNAL_CANDIDATES = PROJECT_ROOT / "config/examples/external-candidates.example.json"
DEFAULT_SOURCE_REPORT_DIR = PROJECT_ROOT / "logs/strategy-validation-artifacts/pass-artifact-source-reports"
DEFAULT_GATE_REPORT_DIR = PROJECT_ROOT / "logs/strategy-validation-artifacts/pass-artifact-gate-reports"
DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "logs/strategy-validation-artifacts/artifacts"
DEFAULT_MATRIX_PATHS = (
    PROJECT_ROOT / "logs/public-market-data/btcusdt-mtf-10k-latest.json",
    PROJECT_ROOT / "logs/public-market-data/public-universe-matrix-latest.json",
)
DEFAULT_MATRIX_GLOBS = (
    "logs/public-market-data/h-data-015-heartbeat-*/*-mtf-10k-latest.json",
    "logs/public-market-data/*-mtf-10k-latest.json",
)
DEFAULT_STRATEGY_IDS = (
    "ma_crossover",
    "ema_trend_filter",
    "donchian_breakout",
    "keltner_breakout",
    "volume_breakout",
    "macd_momentum",
    "roc_momentum",
    "rsi_mean_reversion",
    "bollinger_reversion",
)
TIMEFRAME_ORDER = {"1m": 1, "5m": 2, "15m": 3, "1h": 4, "4h": 5, "1d": 6}


class ReadableLogger:
    def __init__(self, path: Path, *, quiet: bool = False) -> None:
        self.path = path
        self.quiet = quiet
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self._lock = threading.Lock()

    def line(self, text: str = "") -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
            if not self.quiet:
                print(text, flush=True)

    def section(self, title: str) -> None:
        self.line("")
        self.line("=" * 88)
        self.line(title)
        self.line("=" * 88)


class ProgressMonitor:
    def __init__(self, progress_path: Path, logger: ReadableLogger, interval_seconds: float) -> None:
        self.progress_path = progress_path
        self.logger = logger
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._last_signature: tuple[Any, ...] | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="pass-discovery-progress", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            progress = _read_last_jsonl(self.progress_path)
            if not progress:
                continue
            summary = progress.get("progress_summary") or {}
            signature = (
                progress.get("completed_trial_count"),
                progress.get("pass_count"),
                summary.get("validation_percent_complete"),
            )
            if signature == self._last_signature:
                continue
            self._last_signature = signature
            self.logger.line(_format_progress_block(progress))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = ReadableLogger(Path(args.readable_log), quiet=args.quiet)
    started = time.monotonic()
    generated_at = int(time.time()) if args.timestamp is None else int(args.timestamp)

    logger.section("PASS artifact discovery precheck")
    logger.line(f"started_utc       : {_utc_now()}")
    logger.line(f"min_bars          : {args.min_bars}")
    logger.line(f"min_wf_windows    : {args.min_walk_forward_windows}")
    logger.line(f"workers           : {args.workers}")
    logger.line(f"max_trials        : {args.max_trials}")
    logger.line(f"reject_smoke_input: {not args.allow_smoke_inputs}")

    matrix_args = args.matrix if args.matrix is not None else [str(p) for p in DEFAULT_MATRIX_PATHS]
    matrix_glob_args = args.matrix_glob if args.matrix_glob is not None else list(DEFAULT_MATRIX_GLOBS)
    matrix_paths = _resolve_matrix_paths(matrix_args, matrix_glob_args)
    sources, source_rejections = discover_sources(
        matrix_paths,
        min_bars=args.min_bars,
        allow_smoke_inputs=args.allow_smoke_inputs,
        symbols=_csv_set(args.symbols, upper=True),
        timeframes=_csv_set(args.timeframes),
    )
    logger.line(f"matrix_paths      : {len(matrix_paths)}")
    for path in matrix_paths[:20]:
        logger.line(f"  - {path}")
    if len(matrix_paths) > 20:
        logger.line(f"  ... {len(matrix_paths) - 20} more")

    if not sources:
        report = _base_report(args, generated_at, started, status="SKIPPED", message="no long-history public sources passed precheck")
        report.update({
            "source_count": 0,
            "source_rejection_count": len(source_rejections),
            "source_rejection_reason_counts": dict(Counter(r["reason"] for r in source_rejections)),
            "h_opt_005_ready": False,
            "h_opt_010_ready": False,
        })
        _write_json(Path(args.output), report)
        logger.line("No ready sources. Summary written; nothing dispatched.")
        return 2

    logger.line(f"ready_sources     : {len(sources)}")
    for source in sorted(sources, key=lambda s: (s["symbol"], TIMEFRAME_ORDER.get(s["timeframe"], 99)))[:20]:
        logger.line(
            f"  PASS source {source['symbol']:>10} {source['timeframe']:<3} "
            f"bars={source['bar_count']:<6} path={_rel(source['output_path'])}"
        )

    strategy_ids = _normalize_strategy_ids(args.strategy_ids)
    candidates, candidate_rejections = build_candidates(
        sources=sources,
        strategy_ids=strategy_ids,
        max_params_per_strategy=args.max_params_per_strategy,
        external_candidates_path=Path(args.external_candidates) if args.external_candidates else None,
        include_external=not args.no_external_candidates,
        include_generated=not args.no_generated_candidates,
        min_walk_forward_windows=args.min_walk_forward_windows,
        default_train_bars=args.train_bars,
        default_test_bars=args.test_bars,
        default_step_bars=args.step_bars,
        default_holdout_ratio=args.holdout_ratio,
        default_min_trade_count=args.min_trade_count,
        max_candidates=args.max_candidate_pool,
    )
    logger.line(f"candidate_ready   : {len(candidates)}")
    logger.line(f"candidate_rejected: {len(candidate_rejections)}")
    if candidate_rejections:
        logger.line("top_candidate_rejections:")
        for reason, count in Counter(r["reason"] for r in candidate_rejections).most_common(10):
            logger.line(f"  {reason:<48} {count}")

    if not candidates:
        report = _base_report(args, generated_at, started, status="SKIPPED", message="no candidates passed walk-forward precheck")
        report.update({
            "source_count": len(sources),
            "candidate_count": 0,
            "candidate_rejection_count": len(candidate_rejections),
            "candidate_rejection_reason_counts": dict(Counter(r["reason"] for r in candidate_rejections)),
            "h_opt_005_ready": False,
            "h_opt_010_ready": False,
        })
        _write_json(Path(args.output), report)
        logger.line("No ready candidates. Summary written; nothing dispatched.")
        return 2

    filtered_matrix = build_filtered_matrix(sources, generated_at=generated_at)
    _write_json(Path(args.filtered_matrix), filtered_matrix)
    candidate_payload = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "message": "PASS artifact discovery generated long-history candidates",
        "candidates": candidates,
    }
    _write_json(Path(args.generated_candidates), candidate_payload)
    logger.line(f"filtered_matrix   : {args.filtered_matrix}")
    logger.line(f"generated_candidates: {args.generated_candidates}")

    Path(args.progress_jsonl).parent.mkdir(parents=True, exist_ok=True)
    Path(args.progress_jsonl).write_text("", encoding="utf-8")
    monitor = ProgressMonitor(Path(args.progress_jsonl), logger, args.progress_interval_seconds)
    monitor.start()
    logger.section("H-OPT-020 validation dispatch")
    try:
        validation_report = run_external_candidate_validation(
            universe_matrix=args.filtered_matrix,
            external_candidates=args.generated_candidates,
            output_path=args.output,
            progress_jsonl=args.progress_jsonl,
            workers=args.workers,
            max_trials=args.max_trials,
            progress_interval_seconds=args.progress_interval_seconds,
            max_runtime_seconds=args.max_runtime_seconds,
            stop_on_first_pass=args.stop_on_first_pass,
            keep_running_until_pass_with_timeout=args.keep_running_until_pass_with_timeout,
            strategy_ids=None,
            symbols=None,
            timeframes=None,
            resume_from=args.resume_from,
            source_report_dir=args.source_report_dir,
            gate_report_dir=args.gate_report_dir,
            artifact_dir=args.artifact_dir,
            monte_carlo_run_count=args.monte_carlo_run_count,
            min_walk_forward_pass_rate=args.min_walk_forward_pass_rate,
            min_net_pnl=args.min_net_pnl,
            min_out_of_sample_net_pnl=args.min_out_of_sample_net_pnl,
            timestamp=generated_at,
        )
    finally:
        monitor.stop()

    final_report = augment_report(
        validation_report,
        args=args,
        started_monotonic=started,
        sources=sources,
        source_rejections=source_rejections,
        candidate_rejections=candidate_rejections,
        generated_candidate_path=Path(args.generated_candidates),
        filtered_matrix_path=Path(args.filtered_matrix),
        readable_log_path=Path(args.readable_log),
    )
    _write_json(Path(args.output), final_report)
    logger.section("PASS artifact discovery final summary")
    logger.line(_format_final_summary(final_report))
    return 0 if final_report.get("status") == "PASS" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Long-history precheck + readable H-OPT-020 PASS artifact discovery runner.",
    )
    parser.add_argument("--matrix", action="append", default=None)
    parser.add_argument("--matrix-glob", action="append", default=None)
    parser.add_argument("--external-candidates", default=str(DEFAULT_EXTERNAL_CANDIDATES))
    parser.add_argument("--no-external-candidates", action="store_true")
    parser.add_argument("--no-generated-candidates", action="store_true")
    parser.add_argument("--generated-candidates", default=str(DEFAULT_GENERATED_CANDIDATES))
    parser.add_argument("--filtered-matrix", default=str(DEFAULT_FILTERED_MATRIX))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--progress-jsonl", default=str(DEFAULT_PROGRESS))
    parser.add_argument("--readable-log", default=str(DEFAULT_READABLE_LOG))
    parser.add_argument("--source-report-dir", default=str(DEFAULT_SOURCE_REPORT_DIR))
    parser.add_argument("--gate-report-dir", default=str(DEFAULT_GATE_REPORT_DIR))
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-trials", type=int, default=100)
    parser.add_argument("--max-candidate-pool", type=int, default=1000)
    parser.add_argument("--max-params-per-strategy", type=int, default=3)
    parser.add_argument("--progress-interval-seconds", type=float, default=15.0)
    parser.add_argument("--max-runtime-seconds", type=float, default=1800.0)
    parser.add_argument("--stop-on-first-pass", action="store_true")
    parser.add_argument("--keep-running-until-pass-with-timeout", action="store_true")
    parser.add_argument("--resume-from", default=None)
    parser.add_argument("--strategy-ids", default=",".join(DEFAULT_STRATEGY_IDS))
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--timeframes", default=None)
    parser.add_argument("--min-bars", type=int, default=1000)
    parser.add_argument("--allow-smoke-inputs", action="store_true")
    parser.add_argument("--min-walk-forward-windows", type=int, default=3)
    parser.add_argument(
        "--min-walk-forward-pass-rate",
        type=float,
        default=0.67,
        help="Official default is 0.67. Passing lower values, e.g. 0.4, is a relaxed flow-only run and does not satisfy H-OPT official promotion gate.",
    )
    parser.add_argument(
        "--min-net-pnl",
        type=float,
        default=0.0,
        help="Official default is 0.0. Negative values are relaxed flow-only and do not satisfy H-OPT official promotion gate.",
    )
    parser.add_argument(
        "--min-out-of-sample-net-pnl",
        type=float,
        default=0.0,
        help="Official default is 0.0. Negative values are relaxed flow-only and do not satisfy H-OPT official promotion gate.",
    )
    parser.add_argument("--train-bars", type=int, default=600)
    parser.add_argument("--test-bars", type=int, default=100)
    parser.add_argument("--step-bars", type=int, default=100)
    parser.add_argument("--holdout-ratio", type=float, default=0.2)
    parser.add_argument("--min-trade-count", type=int, default=10)
    parser.add_argument("--monte-carlo-run-count", type=int, default=500)
    parser.add_argument("--timestamp", type=int, default=None)
    parser.add_argument("--quiet", action="store_true")
    return parser


def discover_sources(
    matrix_paths: list[Path],
    *,
    min_bars: int,
    allow_smoke_inputs: bool,
    symbols: set[str] | None,
    timeframes: set[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    rejections: list[dict[str, Any]] = []
    for matrix_path in matrix_paths:
        payload = _load_json_safely(matrix_path)
        if not isinstance(payload, dict):
            rejections.append({"path": str(matrix_path), "reason": "matrix_invalid_or_missing"})
            continue
        for source in _iter_matrix_sources(payload, matrix_path):
            reason = _source_precheck_reason(
                source,
                min_bars=min_bars,
                allow_smoke_inputs=allow_smoke_inputs,
                symbols=symbols,
                timeframes=timeframes,
            )
            if reason:
                rejections.append({"path": str(source.get("output_path") or matrix_path), "reason": reason, "source": source})
                continue
            key = (source["symbol"], source["timeframe"], str(source["output_path"]))
            current = sources_by_key.get(key)
            if current is None or int(source.get("bar_count") or 0) > int(current.get("bar_count") or 0):
                sources_by_key[key] = source
    sources = list(sources_by_key.values())
    sources.sort(key=lambda item: (item["symbol"], TIMEFRAME_ORDER.get(item["timeframe"], 99), -int(item.get("bar_count") or 0)))
    return sources, rejections


def _iter_matrix_sources(payload: dict[str, Any], matrix_path: Path) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if isinstance(payload.get("symbols"), dict):
        for symbol, symbol_payload in payload["symbols"].items():
            if isinstance(symbol_payload, dict) and isinstance(symbol_payload.get("timeframes"), dict):
                for timeframe, tf_payload in symbol_payload["timeframes"].items():
                    if isinstance(tf_payload, dict):
                        sources.append(_source_from_payload(str(symbol), str(timeframe), tf_payload, matrix_path))
    elif isinstance(payload.get("timeframes"), dict):
        symbol = str(payload.get("symbol") or "BTCUSDT")
        for timeframe, tf_payload in payload["timeframes"].items():
            if isinstance(tf_payload, dict):
                sources.append(_source_from_payload(symbol, str(timeframe), tf_payload, matrix_path))
    elif payload.get("symbol") and payload.get("timeframe"):
        sources.append(_source_from_payload(str(payload["symbol"]), str(payload["timeframe"]), payload, matrix_path))
    return sources


def _source_from_payload(symbol: str, timeframe: str, payload: dict[str, Any], matrix_path: Path) -> dict[str, Any]:
    output_path = _resolve_path(payload.get("output_path") or matrix_path, matrix_path)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "status": payload.get("status"),
        "bar_count": int(payload.get("bar_count") or 0),
        "output_path": str(output_path),
        "sha256": payload.get("sha256"),
        "quality_report": payload.get("quality_report") if isinstance(payload.get("quality_report"), dict) else {},
        "reason_codes": list(payload.get("reason_codes") or []),
        "safety_flags": payload.get("safety_flags") if isinstance(payload.get("safety_flags"), dict) else {},
    }


def _source_precheck_reason(
    source: dict[str, Any],
    *,
    min_bars: int,
    allow_smoke_inputs: bool,
    symbols: set[str] | None,
    timeframes: set[str] | None,
) -> str | None:
    if symbols and source["symbol"] not in symbols:
        return "source_symbol_filtered"
    if timeframes and source["timeframe"] not in timeframes:
        return "source_timeframe_filtered"
    if source.get("status") != "PASS":
        return "source_status_not_pass"
    if int(source.get("bar_count") or 0) < min_bars:
        return "source_bar_count_below_minimum"
    output_path = Path(str(source.get("output_path") or ""))
    if not output_path.exists():
        return "source_output_missing"
    if not allow_smoke_inputs and ("-20-latest" in output_path.name or int(source.get("bar_count") or 0) <= 20):
        return "smoke_input_rejected"
    quality_report = source.get("quality_report") if isinstance(source.get("quality_report"), dict) else {}
    if quality_report.get("passed") is False:
        return "source_quality_failed"
    flags = source.get("safety_flags") if isinstance(source.get("safety_flags"), dict) else {}
    if flags.get("real_credentials_read") is True:
        return "source_real_credentials_read"
    if flags.get("broker_called") is True:
        return "source_broker_called"
    if flags.get("account_or_order_endpoint_called") is True:
        return "source_account_or_order_endpoint_called"
    if flags.get("live_orders_sent") is True:
        return "source_live_orders_sent"
    if flags and flags.get("public_market_data_only") is not True:
        return "source_not_public_market_data_only"
    return None


def build_candidates(
    *,
    sources: list[dict[str, Any]],
    strategy_ids: list[str],
    max_params_per_strategy: int,
    external_candidates_path: Path | None,
    include_external: bool,
    include_generated: bool,
    min_walk_forward_windows: int,
    default_train_bars: int,
    default_test_bars: int,
    default_step_bars: int,
    default_holdout_ratio: float,
    default_min_trade_count: int,
    max_candidates: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    source_lookup = {(s["symbol"], s["timeframe"]): s for s in sources}
    seen: set[str] = set()

    def add(raw: dict[str, Any], reason_prefix: str) -> None:
        if len(candidates) >= max_candidates:
            rejections.append({"reason": "candidate_pool_limit_reached", "candidate": raw})
            return
        symbol = str(raw.get("symbol") or "").upper()
        timeframe = str(raw.get("timeframe") or "")
        source = source_lookup.get((symbol, timeframe))
        if source is None:
            rejections.append({"reason": f"{reason_prefix}_source_missing", "candidate": raw})
            return
        window = raw.get("window_config") if isinstance(raw.get("window_config"), dict) else {}
        wf_windows = walk_forward_capacity(
            int(source.get("bar_count") or 0),
            int(window.get("train_bars") or default_train_bars),
            int(window.get("test_bars") or default_test_bars),
            int(window.get("step_bars") or default_step_bars),
        )
        if wf_windows < min_walk_forward_windows:
            rejections.append({
                "reason": "insufficient_walk_forward_capacity_precheck",
                "candidate": raw,
                "available_walk_forward_windows": wf_windows,
                "bar_count": source.get("bar_count"),
            })
            return
        key = json.dumps({k: raw.get(k) for k in ["symbol", "timeframe", "strategy_id", "parameters", "window_config", "fingerprint"]}, sort_keys=True)
        if key in seen:
            return
        seen.add(key)
        candidates.append(raw)

    if include_external and external_candidates_path is not None:
        report = load_external_candidate_file(external_candidates_path)
        for candidate in report.get("valid_candidates") or []:
            raw = {
                "symbol": candidate["symbol"],
                "timeframe": candidate["timeframe"],
                "strategy_id": candidate["strategy_id"],
                "parameters": candidate["parameters"],
                "window_config": candidate["window_config"],
                "source": candidate.get("source", "external"),
                "notes": candidate.get("notes", "external candidate"),
                "fingerprint": candidate.get("fingerprint") or candidate.get("computed_fingerprint") or "external",
            }
            add(raw, "external_candidate")

    if include_generated:
        window_config = {
            "train_bars": default_train_bars,
            "test_bars": default_test_bars,
            "step_bars": default_step_bars,
            "holdout_ratio": default_holdout_ratio,
            "min_trade_count": default_min_trade_count,
        }
        for source in sources:
            for strategy_id in strategy_ids:
                grid = _safe_strategy_grid(strategy_id)[: max(1, max_params_per_strategy)]
                for index, params in enumerate(grid, start=1):
                    payload = {
                        "symbol": source["symbol"],
                        "timeframe": source["timeframe"],
                        "strategy_id": strategy_id,
                        "parameters": params,
                        "window_config": window_config,
                        "source": "pass_artifact_discovery_generated_grid",
                        "notes": f"auto generated long-history candidate #{index} for {source['symbol']} {source['timeframe']} {strategy_id}",
                    }
                    payload["fingerprint"] = _sha256_payload(payload)[:24]
                    add(payload, "generated_candidate")
    return candidates, rejections


def _safe_strategy_grid(strategy_id: str) -> list[dict[str, Any]]:
    raw_grid = _strategy_parameter_grid(strategy_id)
    normalized: list[dict[str, Any]] = []
    for params in raw_grid:
        try:
            normalized.append(normalize_candidate_strategy_parameters(strategy_id, params))
        except Exception:
            continue
    return normalized


def build_filtered_matrix(sources: list[dict[str, Any]], *, generated_at: int) -> dict[str, Any]:
    symbols: dict[str, Any] = {}
    for source in sources:
        symbol_payload = symbols.setdefault(
            source["symbol"],
            {"status": "PASS", "timeframes": {}, "reason_codes": []},
        )
        symbol_payload["timeframes"][source["timeframe"]] = {
            "status": "PASS",
            "bar_count": source["bar_count"],
            "output_path": source["output_path"],
            "sha256": source.get("sha256"),
            "quality_report": source.get("quality_report") or {"passed": True},
            "reason_codes": source.get("reason_codes") or [],
            "safety_flags": source.get("safety_flags") or {},
        }
    return {
        "schema_version": "1.0",
        "status": "PASS" if symbols else "SKIPPED",
        "generated_at": generated_at,
        "message": "filtered long-history public universe for PASS artifact discovery",
        "reason_codes": ["pass_artifact_discovery_long_history_precheck"],
        "symbols": symbols,
    }


def walk_forward_capacity(bar_count: int, train_bars: int, test_bars: int, step_bars: int) -> int:
    if step_bars <= 0 or train_bars <= 0 or test_bars <= 0:
        return 0
    required = train_bars + test_bars
    if bar_count < required:
        return 0
    return math.floor((bar_count - required) / step_bars) + 1


def augment_report(
    report: dict[str, Any],
    *,
    args: argparse.Namespace,
    started_monotonic: float,
    sources: list[dict[str, Any]],
    source_rejections: list[dict[str, Any]],
    candidate_rejections: list[dict[str, Any]],
    generated_candidate_path: Path,
    filtered_matrix_path: Path,
    readable_log_path: Path,
) -> dict[str, Any]:
    augmented = dict(report)
    augmented["pass_artifact_discovery"] = {
        "enabled": True,
        "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
        "filtered_matrix_path": str(filtered_matrix_path),
        "generated_candidates_path": str(generated_candidate_path),
        "readable_log_path": str(readable_log_path),
        "long_history_source_count": len(sources),
        "source_rejection_count": len(source_rejections),
        "source_rejection_reason_counts": dict(Counter(r["reason"] for r in source_rejections)),
        "candidate_rejection_count": len(candidate_rejections),
        "candidate_rejection_reason_counts": dict(Counter(r["reason"] for r in candidate_rejections)),
        "precheck_rules": {
            "min_bars": args.min_bars,
            "reject_smoke_inputs": not args.allow_smoke_inputs,
            "min_walk_forward_windows": args.min_walk_forward_windows,
            "min_walk_forward_pass_rate": args.min_walk_forward_pass_rate,
            "official_min_walk_forward_pass_rate": 0.67,
            "min_net_pnl": args.min_net_pnl,
            "official_min_net_pnl": 0.0,
            "min_out_of_sample_net_pnl": args.min_out_of_sample_net_pnl,
            "official_min_out_of_sample_net_pnl": 0.0,
            "relaxed_gate_for_flow_only": (
                args.min_walk_forward_pass_rate < 0.67
                or args.min_net_pnl < 0.0
                or args.min_out_of_sample_net_pnl < 0.0
            ),
            "official_h_opt_gate_satisfied": (
                args.min_walk_forward_pass_rate >= 0.67
                and args.min_net_pnl >= 0.0
                and args.min_out_of_sample_net_pnl >= 0.0
            ),
            "train_bars": args.train_bars,
            "test_bars": args.test_bars,
            "step_bars": args.step_bars,
        },
    }
    return augmented


def _format_progress_block(progress: dict[str, Any]) -> str:
    summary = progress.get("progress_summary") or {}
    percent = float(summary.get("validation_percent_complete") or 0.0)
    completed = int(summary.get("completed_trial_count") or progress.get("completed_trial_count") or 0)
    planned = int(summary.get("planned_trial_count") or progress.get("planned_trial_count") or 0)
    pass_count = int(summary.get("pass_count") or progress.get("pass_count") or 0)
    best_wf = summary.get("best_walk_forward_pass_rate")
    best_mc = summary.get("best_monte_carlo_survival_rate")
    elapsed = float(summary.get("elapsed_seconds") or 0.0)
    eta = _eta(elapsed, percent)
    return "\n".join([
        "",
        f"[PASS-DISCOVERY] {_utc_now()}",
        f"  progress      : {completed} / {planned} = {percent:.2f}%",
        f"  elapsed       : {_duration(elapsed)}",
        f"  eta           : {eta}",
        f"  pass_count    : {pass_count}",
        f"  best_wf       : {_fmt(best_wf)}",
        f"  best_mc       : {_fmt(best_mc)}",
        f"  reason_codes  : {', '.join((progress.get('reason_codes') or [])[-6:])}",
    ])


def _format_final_summary(report: dict[str, Any]) -> str:
    best = report.get("best_candidate") or {}
    blockers = Counter()
    for trial in report.get("all_trials") or []:
        blockers.update(trial.get("reason_codes") or [])
    lines = [
        f"status         : {report.get('status')}",
        f"message        : {report.get('message')}",
        f"completed      : {report.get('completed_trial_count')} / {report.get('planned_trial_count')}",
        f"pass_count     : {report.get('pass_count')}",
        f"artifact_count : {report.get('artifact_count')}",
        f"h_opt_005_ready: {report.get('h_opt_005_ready')}",
        f"h_opt_010_ready: {report.get('h_opt_010_ready')}",
    ]
    if best:
        lines.extend([
            "best_candidate:",
            f"  symbol      : {best.get('symbol')}",
            f"  timeframe   : {best.get('timeframe')}",
            f"  strategy    : {best.get('strategy_id')}",
            f"  status      : {best.get('status')}",
            f"  reasons     : {', '.join(best.get('reason_codes') or [])}",
        ])
    if blockers:
        lines.append("top_blockers:")
        for reason, count in blockers.most_common(10):
            lines.append(f"  {reason:<48} {count}")
    resume = report.get("resume_command")
    if resume:
        lines.extend(["resume_command:", f"  {resume}"])
    return "\n".join(lines)


def _resolve_matrix_paths(paths: list[str], patterns: list[str]) -> list[Path]:
    resolved: list[Path] = []
    for item in paths or []:
        path = _project_path(item)
        if path.exists():
            resolved.append(path)
    for pattern in patterns or []:
        for match in glob.glob(str(_project_path(pattern))):
            path = Path(match)
            if path.exists():
                resolved.append(path)
    unique: dict[str, Path] = {str(path.resolve()): path for path in resolved}
    return list(unique.values())


def _normalize_strategy_ids(raw: str | None) -> list[str]:
    values = [item.strip().lower() for item in (raw or "").split(",") if item.strip()]
    if not values:
        values = list(DEFAULT_STRATEGY_IDS)
    supported = set(SUPPORTED_CANDIDATE_STRATEGY_IDS)
    return [value for value in values if value in supported]


def _csv_set(raw: str | None, *, upper: bool = False) -> set[str] | None:
    if not raw:
        return None
    values = {item.strip() for item in raw.split(",") if item.strip()}
    if upper:
        values = {item.upper() for item in values}
    return values or None


def _base_report(args: argparse.Namespace, generated_at: int, started: float, *, status: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": status,
        "success": status == "PASS",
        "message": message,
        "generated_at": generated_at,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "configured": vars(args),
    }


def _load_json_safely(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_last_jsonl(path: Path) -> dict[str, Any] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_path(value: Any, base_path: Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    project_candidate = PROJECT_ROOT / path
    if project_candidate.exists():
        return project_candidate
    return (base_path.parent / path).resolve()


def _project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _sha256_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _eta(elapsed: float, percent: float) -> str:
    if percent <= 0:
        return "unknown"
    total = elapsed / (percent / 100.0)
    return _duration(total - elapsed)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _rel(path: Any) -> str:
    try:
        return str(Path(str(path)).resolve().relative_to(PROJECT_ROOT))
    except Exception:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
