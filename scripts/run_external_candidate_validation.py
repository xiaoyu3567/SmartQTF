#!/usr/bin/env python
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import hashlib
import json
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.optimization.external_candidates import (
    external_candidate_key,
    external_candidate_version,
    load_external_candidate_file,
)
from scripts.generate_strategy_validation_source_reports import (
    run_strategy_validation_source_report_generation,
)


DEFAULT_UNIVERSE_MATRIX_PATH = (
    PROJECT_ROOT / "logs" / "public-market-data" / "public-universe-matrix-latest.json"
)
DEFAULT_EXTERNAL_CANDIDATES_PATH = (
    PROJECT_ROOT / "config" / "examples" / "external-candidates.example.json"
)
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT
    / "logs"
    / "strategy-validation-artifacts"
    / "external-candidate-validation-latest.json"
)
DEFAULT_PROGRESS_JSONL_PATH = (
    PROJECT_ROOT
    / "logs"
    / "strategy-validation-artifacts"
    / "external-candidate-validation-progress-latest.jsonl"
)
DEFAULT_EXTERNAL_CANDIDATE_SOURCE_REPORT_DIR = (
    PROJECT_ROOT
    / "logs"
    / "strategy-validation-artifacts"
    / "external-candidate-source-reports"
)
DEFAULT_EXTERNAL_CANDIDATE_GATE_REPORT_DIR = (
    PROJECT_ROOT
    / "logs"
    / "strategy-validation-artifacts"
    / "external-candidate-gate-reports"
)
DEFAULT_EXTERNAL_CANDIDATE_ARTIFACT_DIR = (
    PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "artifacts"
)
MIN_WALK_FORWARD_PASS_RATE = 0.67
MIN_MONTE_CARLO_SURVIVAL_RATE = 0.8
DEFAULT_MIN_WALK_FORWARD_WINDOWS = 3
DEFAULT_MONTE_CARLO_RUN_COUNT = 500


_ARTIFACT_PUBLICATION_LOCK = Lock()


def run_external_candidate_validation(
    *,
    universe_matrix: str | Path = DEFAULT_UNIVERSE_MATRIX_PATH,
    external_candidates: str | Path = DEFAULT_EXTERNAL_CANDIDATES_PATH,
    output_path: str | Path | None = DEFAULT_OUTPUT_PATH,
    progress_jsonl: str | Path | None = DEFAULT_PROGRESS_JSONL_PATH,
    workers: int = 1,
    max_trials: int = 20,
    progress_interval_seconds: float = 10.0,
    max_runtime_seconds: float | None = None,
    stop_on_first_pass: bool = False,
    keep_running_until_pass_with_timeout: bool = False,
    strategy_ids: list[str] | None = None,
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    resume_from: str | Path | None = None,
    source_report_dir: str | Path = DEFAULT_EXTERNAL_CANDIDATE_SOURCE_REPORT_DIR,
    gate_report_dir: str | Path = DEFAULT_EXTERNAL_CANDIDATE_GATE_REPORT_DIR,
    artifact_dir: str | Path = DEFAULT_EXTERNAL_CANDIDATE_ARTIFACT_DIR,
    monte_carlo_run_count: int = DEFAULT_MONTE_CARLO_RUN_COUNT,
    min_walk_forward_pass_rate: float = MIN_WALK_FORWARD_PASS_RATE,
    min_net_pnl: float = 0.0,
    min_out_of_sample_net_pnl: float = 0.0,
    timestamp: int | None = None,
) -> dict[str, Any]:
    generated_at = int(time.time()) if timestamp is None else timestamp
    effective_min_walk_forward_pass_rate = float(min_walk_forward_pass_rate)
    effective_min_net_pnl = float(min_net_pnl)
    effective_min_out_of_sample_net_pnl = float(min_out_of_sample_net_pnl)
    relaxed_gate_for_flow_only = (
        effective_min_walk_forward_pass_rate < MIN_WALK_FORWARD_PASS_RATE
        or effective_min_net_pnl < 0.0
        or effective_min_out_of_sample_net_pnl < 0.0
    )
    started_monotonic = time.monotonic()
    universe_matrix_path = Path(universe_matrix)
    external_candidates_path = Path(external_candidates)
    output = Path(output_path) if output_path is not None else None
    progress_path = Path(progress_jsonl) if progress_jsonl is not None else None
    source_report_root = Path(source_report_dir)
    gate_report_root = Path(gate_report_dir)
    artifact_root = Path(artifact_dir)

    candidate_parse_report = load_external_candidate_file(external_candidates_path)
    matrix_payload, source_lookup, matrix_reason_codes = _load_universe_sources(
        universe_matrix_path
    )
    filters = _candidate_filters(
        strategy_ids=strategy_ids,
        symbols=symbols,
        timeframes=timeframes,
    )
    resumed_versions = _load_resumed_candidate_versions(resume_from)
    effective_stop_on_first_pass = bool(
        stop_on_first_pass or keep_running_until_pass_with_timeout
    )
    filtered_candidates: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []
    ready_trials: list[dict[str, Any]] = []

    for candidate in candidate_parse_report.get("valid_candidates") or []:
        filter_reason = _candidate_filter_reason(candidate, filters)
        if filter_reason is not None:
            filtered_candidates.append(_candidate_rejection(candidate, filter_reason))
            continue

        source = source_lookup.get((candidate["symbol"], candidate["timeframe"]))
        rejection_reason = _source_rejection_reason(candidate, source)
        if rejection_reason is not None:
            rejected_candidates.append(_candidate_rejection(candidate, rejection_reason))
            continue

        trial = _candidate_trial(
            candidate=candidate,
            source=source,
            generated_at=generated_at,
        )
        if trial["candidate_version"] in resumed_versions:
            trial["status"] = "RESUMED"
            trial["reason_codes"] = ["external_candidate_resumed_from_previous_summary"]
        ready_trials.append(trial)

    limited_trials = ready_trials[:max_trials] if max_trials > 0 else []
    reason_codes = set(candidate_parse_report.get("reason_codes") or [])
    reason_codes.update(matrix_reason_codes)
    if filtered_candidates:
        reason_codes.add("external_candidates_filtered")
    if rejected_candidates:
        reason_codes.add("external_candidates_rejected_by_universe_coverage")
    if max_trials <= 0:
        reason_codes.add("max_trials_not_positive")
    if len(ready_trials) > len(limited_trials):
        reason_codes.add("external_candidate_trials_limited")
    if not limited_trials:
        reason_codes.add("no_external_candidate_trials_ready")
    else:
        reason_codes.add("external_candidate_ingestion_ready")
    ingestion_status = "PASS" if limited_trials and max_trials > 0 else "SKIPPED"
    scheduler_result = _run_validation_scheduler(
        trials=limited_trials,
        generated_at=generated_at,
        started_monotonic=started_monotonic,
        workers=max(1, int(workers)),
        progress_interval_seconds=progress_interval_seconds,
        max_runtime_seconds=max_runtime_seconds,
        stop_on_first_pass=effective_stop_on_first_pass,
        progress_jsonl=progress_path,
        ingestion_status=ingestion_status,
        initial_reason_codes=sorted(reason_codes),
        source_report_dir=source_report_root,
        gate_report_dir=gate_report_root,
        artifact_dir=artifact_root,
        monte_carlo_run_count=max(1, int(monte_carlo_run_count)),
        min_walk_forward_pass_rate=effective_min_walk_forward_pass_rate,
        min_net_pnl=effective_min_net_pnl,
        min_out_of_sample_net_pnl=effective_min_out_of_sample_net_pnl,
    )
    reason_codes.update(scheduler_result["reason_codes"])
    stopped_reason = scheduler_result["stopped_reason"]
    if stopped_reason:
        reason_codes.add(stopped_reason)
    completed_trials = scheduler_result["completed_trials"]
    all_trials = scheduler_result["all_trials"]
    passing_candidates = [
        trial
        for trial in completed_trials
        if trial.get("status") == "PASS"
        and trial.get("artifact_publication_allowed") is True
    ]
    official_passing_candidates = [] if relaxed_gate_for_flow_only else passing_candidates
    if passing_candidates and relaxed_gate_for_flow_only:
        reason_codes.add("relaxed_flow_artifact_generated_not_official_h_opt_gate")
    artifact_paths = [
        path
        for trial in passing_candidates
        for path in trial.get("artifact_paths", [])
        if path
    ]
    source_report_paths = [
        path
        for trial in passing_candidates
        for path in trial.get("source_report_paths", [])
        if path
    ]
    best_candidate = _best_candidate(completed_trials)
    if keep_running_until_pass_with_timeout:
        reason_codes.add("stop_until_pass_workflow_enabled")
        if max_runtime_seconds is None:
            reason_codes.add("stop_until_pass_timeout_not_configured")
        if passing_candidates:
            reason_codes.add("stop_until_pass_workflow_pass_found")
        else:
            reason_codes.add("stop_until_pass_resume_command_available")
    if completed_trials and not passing_candidates:
        reason_codes.add("no_external_candidate_passed")
    elapsed_seconds = time.monotonic() - started_monotonic
    status = "PASS" if passing_candidates else "SKIPPED"
    if stopped_reason and "max_runtime_seconds_reached" in stopped_reason:
        status = "TIMEOUT"
    message = (
        "external candidate validation found a candidate that passed gates"
        if passing_candidates
        else (
            "external candidate validation stopped at the configured runtime limit"
            if status == "TIMEOUT"
            else "external candidate scheduler completed without a passing candidate"
        )
    )
    report = {
        "generated_at": generated_at,
        "status": status,
        "success": status == "PASS",
        "message": message
        if limited_trials
        else "external candidate ingestion found no runnable trials",
        "task_scope": "H-OPT-020",
        "implemented_subtasks": [
            "H-OPT-020A",
            "H-OPT-020B",
            "H-OPT-020C",
            "H-OPT-020D",
            "H-OPT-020E",
            "H-OPT-020F",
            "H-OPT-020G",
            "H-OPT-020H",
        ],
        "pending_subtasks": [],
        "scheduler_mode": "bounded_checkpoint_worker_pool",
        "scheduler_summary": scheduler_result["scheduler_summary"],
        "ingestion_status": ingestion_status,
        "reason_codes": sorted(reason_codes),
        "universe_matrix_path": str(universe_matrix_path),
        "universe_matrix_fingerprint": _file_fingerprint(universe_matrix_path),
        "matrix_status": matrix_payload.get("status") if matrix_payload else None,
        "matrix_symbol_count": len(
            matrix_payload.get("symbols") or {}
        )
        if isinstance(matrix_payload, dict)
        else 0,
        "external_candidates_path": str(external_candidates_path),
        "external_candidate_parse_report": candidate_parse_report,
        "filters": filters,
        "workers": max(1, int(workers)),
        "max_trials": max_trials,
        "progress_interval_seconds": progress_interval_seconds,
        "max_runtime_seconds": max_runtime_seconds,
        "stop_on_first_pass": stop_on_first_pass,
        "effective_stop_on_first_pass": effective_stop_on_first_pass,
        "keep_running_until_pass_with_timeout": keep_running_until_pass_with_timeout,
        "source_report_dir": str(source_report_root),
        "gate_report_dir": str(gate_report_root),
        "artifact_dir": str(artifact_root),
        "gate_thresholds": {
            "min_walk_forward_windows": DEFAULT_MIN_WALK_FORWARD_WINDOWS,
            "min_walk_forward_pass_rate": effective_min_walk_forward_pass_rate,
            "official_min_walk_forward_pass_rate": MIN_WALK_FORWARD_PASS_RATE,
            "relaxed_gate_for_flow_only": relaxed_gate_for_flow_only,
            "official_h_opt_gate_satisfied": not relaxed_gate_for_flow_only,
            "min_monte_carlo_survival_rate": MIN_MONTE_CARLO_SURVIVAL_RATE,
            "min_net_pnl": effective_min_net_pnl,
            "official_min_net_pnl": 0.0,
            "min_out_of_sample_net_pnl": effective_min_out_of_sample_net_pnl,
            "official_min_out_of_sample_net_pnl": 0.0,
            "monte_carlo_run_count": max(1, int(monte_carlo_run_count)),
        },
        "resume_from": str(resume_from) if resume_from is not None else None,
        "resumed_candidate_version_count": len(resumed_versions),
        "total_external_candidate_count": candidate_parse_report.get("candidate_count", 0),
        "valid_external_candidate_count": candidate_parse_report.get(
            "valid_candidate_count",
            0,
        ),
        "invalid_external_candidate_count": candidate_parse_report.get(
            "invalid_candidate_count",
            0,
        ),
        "filtered_candidate_count": len(filtered_candidates),
        "rejected_candidate_count": len(rejected_candidates),
        "ready_candidate_count": len(ready_trials),
        "planned_trial_count": len(limited_trials),
        "completed_trial_count": len(completed_trials),
        "executed_trial_count": scheduler_result["executed_trial_count"],
        "resumed_trial_count": scheduler_result["resumed_trial_count"],
        "pass_count": len(passing_candidates),
        "generated_artifact_count": sum(
            int(trial.get("generated_artifact_count") or 0)
            for trial in completed_trials
        ),
        "artifact_count": len(artifact_paths),
        "artifact_paths": artifact_paths,
        "source_report_paths": source_report_paths,
        "validator_status": "PASS" if passing_candidates else None,
        "publication_lock": {
            "lock_scope": "thread_safe_pass_artifact_publication",
            "min_walk_forward_pass_rate": effective_min_walk_forward_pass_rate,
            "official_min_walk_forward_pass_rate": MIN_WALK_FORWARD_PASS_RATE,
            "relaxed_gate_for_flow_only": relaxed_gate_for_flow_only,
            "official_h_opt_gate_satisfied": not relaxed_gate_for_flow_only,
            "min_monte_carlo_survival_rate": MIN_MONTE_CARLO_SURVIVAL_RATE,
            "min_net_pnl": effective_min_net_pnl,
            "official_min_net_pnl": 0.0,
            "min_out_of_sample_net_pnl": effective_min_out_of_sample_net_pnl,
            "official_min_out_of_sample_net_pnl": 0.0,
            "non_passing_candidates_publish_artifacts": False,
        },
        "h_opt_005_ready": bool(official_passing_candidates),
        "h_opt_010_ready": bool(official_passing_candidates),
        "relaxed_flow_artifact_count": len(artifact_paths) if relaxed_gate_for_flow_only else 0,
        "official_pass_count": len(official_passing_candidates),
        "h_opt_005_blockers": sorted(reason_codes)
        if not official_passing_candidates
        else [],
        "rejected_candidates": rejected_candidates[:50],
        "filtered_candidates": filtered_candidates[:50],
        "planned_trials": limited_trials,
        "best_candidate": best_candidate,
        "passing_candidates": passing_candidates,
        "all_trials": all_trials,
        "stopped_reason": stopped_reason,
        "elapsed_seconds": elapsed_seconds,
        "progress_summary": scheduler_result["progress_summary"],
        "resume_command": _resume_command(
            universe_matrix_path=universe_matrix_path,
            external_candidates_path=external_candidates_path,
            output_path=output,
            workers=workers,
            max_trials=max_trials,
            progress_interval_seconds=progress_interval_seconds,
            max_runtime_seconds=max_runtime_seconds,
            stop_on_first_pass=stop_on_first_pass,
            keep_running_until_pass_with_timeout=keep_running_until_pass_with_timeout,
            source_report_dir=source_report_root,
            gate_report_dir=gate_report_root,
            artifact_dir=artifact_root,
            monte_carlo_run_count=max(1, int(monte_carlo_run_count)),
            min_walk_forward_pass_rate=effective_min_walk_forward_pass_rate,
            min_net_pnl=effective_min_net_pnl,
            min_out_of_sample_net_pnl=effective_min_out_of_sample_net_pnl,
        ),
        "safety_flags": _default_safety_flags(),
        "network_access_used": False,
        "real_credentials_read": False,
        "broker_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
        "public_market_data_only": True,
    }
    report["stop_until_pass_workflow"] = _stop_until_pass_workflow_summary(
        enabled=keep_running_until_pass_with_timeout,
        max_runtime_seconds=max_runtime_seconds,
        effective_stop_on_first_pass=effective_stop_on_first_pass,
        passing_candidates=passing_candidates,
        all_trials=all_trials,
        stopped_reason=stopped_reason,
        resume_command=report["resume_command"],
    )
    return _write_report(report, output)


def _load_universe_sources(
    universe_matrix_path: Path,
) -> tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]], list[str]]:
    try:
        payload = json.loads(universe_matrix_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, {}, ["universe_matrix_missing"]
    except json.JSONDecodeError:
        return {}, {}, ["universe_matrix_invalid_json"]

    source_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    symbols_payload = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    if symbols_payload:
        for symbol, symbol_payload in symbols_payload.items():
            if not isinstance(symbol_payload, dict):
                continue
            timeframes = (
                symbol_payload.get("timeframes")
                if isinstance(symbol_payload.get("timeframes"), dict)
                else {}
            )
            for timeframe, timeframe_payload in timeframes.items():
                if isinstance(timeframe_payload, dict):
                    source_lookup[(str(symbol).upper(), str(timeframe))] = (
                        _source_entry(
                            symbol=str(symbol).upper(),
                            timeframe=str(timeframe),
                            payload=timeframe_payload,
                            matrix_path=universe_matrix_path,
                        )
                    )
    elif isinstance(payload.get("timeframes"), dict):
        symbol = str(payload.get("symbol") or "BTCUSDT").upper()
        for timeframe, timeframe_payload in payload["timeframes"].items():
            if isinstance(timeframe_payload, dict):
                source_lookup[(symbol, str(timeframe))] = _source_entry(
                    symbol=symbol,
                    timeframe=str(timeframe),
                    payload=timeframe_payload,
                    matrix_path=universe_matrix_path,
                )
    reason_codes = list(payload.get("reason_codes") or [])
    if not source_lookup:
        reason_codes.append("universe_matrix_has_no_timeframe_sources")
    return payload, source_lookup, sorted(set(reason_codes))


def _source_entry(
    *,
    symbol: str,
    timeframe: str,
    payload: dict[str, Any],
    matrix_path: Path,
) -> dict[str, Any]:
    output_path = _resolve_output_path(payload.get("output_path"), matrix_path)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": payload.get("status"),
        "bar_count": _coerce_int(payload.get("bar_count")),
        "output_path": output_path,
        "sha256": payload.get("sha256"),
        "quality_report": payload.get("quality_report")
        if isinstance(payload.get("quality_report"), dict)
        else {},
        "reason_codes": list(payload.get("reason_codes") or []),
        "data_fingerprint": _file_fingerprint(output_path) if output_path else {},
    }


def _source_rejection_reason(
    candidate: dict[str, Any],
    source: dict[str, Any] | None,
) -> str | None:
    if source is None:
        return "candidate_universe_timeframe_missing"
    if source.get("status") != "PASS":
        return "candidate_universe_timeframe_not_pass"
    quality_report = source.get("quality_report") if isinstance(source.get("quality_report"), dict) else {}
    if quality_report.get("passed") is False:
        return "candidate_universe_timeframe_quality_failed"
    output_path = source.get("output_path")
    if output_path is None or not Path(output_path).exists():
        return "candidate_universe_output_missing"
    bar_count = int(source.get("bar_count") or 0)
    if bar_count < int(candidate.get("required_bar_count") or 0):
        return "candidate_bar_count_below_window_requirement"
    return None


def _candidate_trial(
    *,
    candidate: dict[str, Any],
    source: dict[str, Any],
    generated_at: int,
) -> dict[str, Any]:
    data_fingerprint = source.get("data_fingerprint") or {}
    candidate_version = external_candidate_version(
        candidate,
        data_fingerprint=data_fingerprint,
        phase="confirm",
    )
    return {
        "generated_at": generated_at,
        "status": "PENDING",
        "success": False,
        "reason_codes": ["external_candidate_ingested_validation_pending"],
        "candidate_key": external_candidate_key(candidate),
        "candidate_version": candidate_version,
        "phase": "confirm",
        "phase_role": "independent_confirm",
        "artifact_publication_allowed": False,
        "artifact_publication_lock_status": "pending_h_opt_020f",
        "symbol": candidate["symbol"],
        "timeframe": candidate["timeframe"],
        "input_path": str(source["output_path"]),
        "bar_count": source.get("bar_count"),
        "data_fingerprint": data_fingerprint,
        "strategy_id": candidate["strategy_id"],
        "strategy_parameters": dict(candidate["parameters"]),
        "strategy_metadata": dict(candidate.get("strategy_metadata") or {}),
        "window": dict(candidate["window_config"]),
        "external_candidate": candidate,
        "artifact_paths": [],
        "generated_artifact_count": 0,
        "validator_status": None,
        "h_opt_005_ready": False,
        "h_opt_005_blockers": ["external_candidate_validation_gate_execution_pending"],
    }


def _candidate_rejection(candidate: dict[str, Any], reason_code: str) -> dict[str, Any]:
    return {
        "raw_index": candidate.get("raw_index"),
        "symbol": candidate.get("symbol"),
        "timeframe": candidate.get("timeframe"),
        "strategy_id": candidate.get("strategy_id"),
        "fingerprint": candidate.get("fingerprint"),
        "status": "SKIPPED",
        "reason_codes": [reason_code],
    }


def _run_validation_scheduler(
    *,
    trials: list[dict[str, Any]],
    generated_at: int,
    started_monotonic: float,
    workers: int,
    progress_interval_seconds: float,
    max_runtime_seconds: float | None,
    stop_on_first_pass: bool,
    progress_jsonl: Path | None,
    ingestion_status: str,
    initial_reason_codes: list[str],
    source_report_dir: Path,
    gate_report_dir: Path,
    artifact_dir: Path,
    monte_carlo_run_count: int,
    min_walk_forward_pass_rate: float = MIN_WALK_FORWARD_PASS_RATE,
    min_net_pnl: float = 0.0,
    min_out_of_sample_net_pnl: float = 0.0,
) -> dict[str, Any]:
    planned_count = len(trials)
    worker_count = max(1, min(int(workers), planned_count or 1))
    progress_interval = max(0.0, float(progress_interval_seconds))
    reason_codes = set(initial_reason_codes)
    pending = deque((index, dict(trial)) for index, trial in enumerate(trials, start=1))
    running: dict[Future[dict[str, Any]], int] = {}
    completed_by_index: dict[int, dict[str, Any]] = {}
    executed_trial_count = 0
    resumed_trial_count = 0
    stopped_reason: str | None = None
    last_progress_monotonic = 0.0

    if not trials:
        reason_codes.add("no_external_candidate_trials_ready")
        progress_summary = _validation_progress_summary(
            planned_trial_count=0,
            completed_trials=[],
            active_worker_count=0,
            started_monotonic=started_monotonic,
        )
        progress_report = _scheduler_progress_report(
            generated_at=generated_at,
            ingestion_status=ingestion_status,
            planned_trial_count=0,
            progress_summary=progress_summary,
            reason_codes=sorted(reason_codes),
        )
        _write_progress(progress_report, progress_jsonl)
        _print_progress(progress_report)
        return {
            "completed_trials": [],
            "all_trials": [],
            "executed_trial_count": 0,
            "resumed_trial_count": 0,
            "stopped_reason": None,
            "reason_codes": sorted(reason_codes),
            "progress_summary": progress_summary,
            "scheduler_summary": {
                "worker_count": worker_count,
                "planned_trial_count": 0,
                "completed_trial_count": 0,
                "pending_trial_count": 0,
                "active_worker_count": 0,
                "bounded_worker_pool_used": False,
                "checkpoint_written": True,
            },
        }

    reason_codes.add("external_candidate_scheduler_started")
    progress_report = _scheduler_progress_report(
        generated_at=generated_at,
        ingestion_status=ingestion_status,
        planned_trial_count=planned_count,
        progress_summary=_validation_progress_summary(
            planned_trial_count=planned_count,
            completed_trials=[],
            active_worker_count=0,
            started_monotonic=started_monotonic,
        ),
        reason_codes=sorted(reason_codes),
    )
    _write_progress(progress_report, progress_jsonl)
    _print_progress(progress_report)
    last_progress_monotonic = time.monotonic()

    executor = ThreadPoolExecutor(max_workers=worker_count)
    shutdown_wait = True
    try:
        while pending or running:
            while pending and len(running) < worker_count:
                if _runtime_exhausted(started_monotonic, max_runtime_seconds):
                    stopped_reason = "max_runtime_seconds_reached"
                    reason_codes.add(stopped_reason)
                    break
                index, trial = pending.popleft()
                if trial.get("status") == "RESUMED":
                    completed_by_index[index] = _resume_validation_trial(
                        index=index,
                        generated_at=generated_at,
                        trial=trial,
                    )
                    resumed_trial_count += 1
                    continue
                future = executor.submit(
                    _execute_validation_trial,
                    index=index,
                    generated_at=generated_at,
                    trial=trial,
                    source_report_dir=source_report_dir,
                    gate_report_dir=gate_report_dir,
                    artifact_dir=artifact_dir,
                    monte_carlo_run_count=monte_carlo_run_count,
                    min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                    min_net_pnl=min_net_pnl,
                    min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
                )
                running[future] = index
                executed_trial_count += 1

            completed_trials = _ordered_completed_trials(completed_by_index)
            now = time.monotonic()
            if (
                progress_interval == 0.0
                or now - last_progress_monotonic >= progress_interval
            ):
                progress_report = _scheduler_progress_report(
                    generated_at=generated_at,
                    ingestion_status=ingestion_status,
                    planned_trial_count=planned_count,
                    progress_summary=_validation_progress_summary(
                        planned_trial_count=planned_count,
                        completed_trials=completed_trials,
                        active_worker_count=len(running),
                        started_monotonic=started_monotonic,
                    ),
                    reason_codes=sorted(reason_codes),
                )
                _write_progress(progress_report, progress_jsonl)
                _print_progress(progress_report)
                last_progress_monotonic = now

            if stopped_reason:
                for future in running:
                    future.cancel()
                shutdown_wait = False
                break
            if not running:
                continue

            wait_timeout = progress_interval if progress_interval > 0.0 else 0.1
            done, _ = wait(
                running.keys(),
                timeout=wait_timeout,
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                index = running.pop(future)
                try:
                    trial_summary = future.result()
                except Exception as exc:
                    trial_summary = _failed_validation_trial(
                        index=index,
                        generated_at=generated_at,
                        trial=trials[index - 1],
                        message=str(exc),
                    )
                completed_by_index[index] = trial_summary
                if (
                    stop_on_first_pass
                    and trial_summary.get("status") == "PASS"
                    and trial_summary.get("artifact_publication_allowed") is True
                ):
                    stopped_reason = "first_passing_external_candidate_found"
                    reason_codes.add(stopped_reason)
                    break
    except KeyboardInterrupt:
        stopped_reason = "sigint_graceful_shutdown"
        reason_codes.add(stopped_reason)
        shutdown_wait = False
        for future in running:
            future.cancel()
    finally:
        executor.shutdown(wait=shutdown_wait, cancel_futures=not shutdown_wait)

    completed_trials = _ordered_completed_trials(completed_by_index)
    all_trials = _merge_completed_and_pending_trials(
        trials=trials,
        completed_by_index=completed_by_index,
        generated_at=generated_at,
        stopped_reason=stopped_reason,
    )
    for trial in completed_trials:
        reason_codes.update(trial.get("reason_codes") or [])
    if len(completed_trials) == planned_count and stopped_reason is None:
        reason_codes.add("external_candidate_scheduler_completed")
    if any(
        "external_candidate_validation_gate_execution_pending"
        in (trial.get("reason_codes") or [])
        for trial in completed_trials
    ):
        reason_codes.add("external_candidate_validation_gate_execution_pending")
    progress_summary = _validation_progress_summary(
        planned_trial_count=planned_count,
        completed_trials=completed_trials,
        active_worker_count=0,
        started_monotonic=started_monotonic,
    )
    progress_report = _scheduler_progress_report(
        generated_at=generated_at,
        ingestion_status=ingestion_status,
        planned_trial_count=planned_count,
        progress_summary=progress_summary,
        reason_codes=sorted(reason_codes),
    )
    _write_progress(progress_report, progress_jsonl)
    _print_progress(progress_report)
    return {
        "completed_trials": completed_trials,
        "all_trials": all_trials,
        "executed_trial_count": executed_trial_count,
        "resumed_trial_count": resumed_trial_count,
        "stopped_reason": stopped_reason,
        "reason_codes": sorted(reason_codes),
        "progress_summary": progress_summary,
        "scheduler_summary": {
            "worker_count": worker_count,
            "planned_trial_count": planned_count,
            "completed_trial_count": len(completed_trials),
            "pending_trial_count": max(0, planned_count - len(completed_trials)),
            "active_worker_count": 0,
            "bounded_worker_pool_used": worker_count > 1,
            "checkpoint_written": True,
            "stop_on_first_pass": stop_on_first_pass,
            "max_runtime_seconds": max_runtime_seconds,
        },
    }


def _execute_validation_trial(
    *,
    index: int,
    generated_at: int,
    trial: dict[str, Any],
    source_report_dir: Path,
    gate_report_dir: Path,
    artifact_dir: Path,
    monte_carlo_run_count: int,
    min_walk_forward_pass_rate: float = MIN_WALK_FORWARD_PASS_RATE,
    min_net_pnl: float = 0.0,
    min_out_of_sample_net_pnl: float = 0.0,
) -> dict[str, Any]:
    summary = dict(trial)
    reason_codes = set(summary.get("reason_codes") or [])
    reason_codes.discard("external_candidate_ingested_validation_pending")
    reason_codes.add("external_candidate_real_gate_executed")
    gate_report_path = _trial_gate_report_path(
        root=gate_report_dir,
        trial=trial,
    )
    source_report_output_dir = _trial_source_report_dir(
        root=source_report_dir,
        trial=trial,
    )
    artifact_generation_output_path = _trial_artifact_generation_report_path(
        gate_report_path,
    )
    validator_output_path = _trial_validator_report_path(gate_report_path)
    window = trial.get("window") if isinstance(trial.get("window"), dict) else {}

    with _ARTIFACT_PUBLICATION_LOCK:
        gate_report = run_strategy_validation_source_report_generation(
            source_paths=[trial["input_path"]],
            strategy_id=str(trial["strategy_id"]),
            candidate_version=str(trial["candidate_version"]),
            symbol=str(trial["symbol"]),
            timeframe=str(trial["timeframe"]),
            input_kind="klines",
            output_dir=source_report_output_dir,
            report_output_path=gate_report_path,
            timestamp=generated_at,
            holdout_ratio=float(window.get("holdout_ratio") or 0.3),
            min_train_bars=int(window.get("train_bars") or 40),
            min_holdout_bars=int(window.get("test_bars") or 20),
            min_trades=int(window.get("min_trade_count") or 1),
            strategy_parameters=dict(trial.get("strategy_parameters") or {}),
            overwrite_existing_source_report=True,
            generation_kind="aggregate",
            train_bars=int(window.get("train_bars") or 40),
            test_bars=int(window.get("test_bars") or 20),
            step_bars=int(window.get("step_bars") or 20),
            min_walk_forward_windows=DEFAULT_MIN_WALK_FORWARD_WINDOWS,
            min_walk_forward_pass_rate=float(min_walk_forward_pass_rate),
            monte_carlo_run_count=max(1, int(monte_carlo_run_count)),
            min_monte_carlo_trades=int(window.get("min_trade_count") or 1),
            min_monte_carlo_survival_rate=MIN_MONTE_CARLO_SURVIVAL_RATE,
            artifact_dir=artifact_dir,
            artifact_generation_output_path=artifact_generation_output_path,
            validator_output_path=validator_output_path,
            require_gate_pass=True,
            min_net_pnl=float(min_net_pnl),
            min_out_of_sample_net_pnl=float(min_out_of_sample_net_pnl),
            overwrite_existing_artifacts=False,
        )

    artifact_paths = [
        path
        for path in gate_report.get("artifact_paths", [])
        if path
    ]
    source_report_paths = [
        path
        for path in gate_report.get("source_report_paths", [])
        if path
    ]
    artifact_generation_report = gate_report.get("artifact_generation_report")
    validator_report = (
        artifact_generation_report.get("validator_report")
        if isinstance(artifact_generation_report, dict)
        else None
    )
    h_opt_005_ready = bool(gate_report.get("h_opt_005_ready"))
    validator_status = (
        validator_report.get("status")
        if isinstance(validator_report, dict)
        else gate_report.get("validator_status")
    )
    if h_opt_005_ready and artifact_paths and validator_status == "PASS":
        status = "PASS"
        success = True
        publication_allowed = True
        publication_lock_status = "published_after_strict_validator_pass"
        message = "external candidate passed OOS/WF/MC gates and strict validator"
        reason_codes.add("pass_artifact_publication_lock_released")
    else:
        status = "FAIL" if gate_report.get("status") == "FAIL" else "SKIPPED"
        success = False
        publication_allowed = False
        publication_lock_status = "blocked_until_gate_and_strict_validator_pass"
        message = "external candidate did not pass OOS/WF/MC gates and strict validator"
        reason_codes.add("pass_artifact_publication_lock_blocked")
    reason_codes.update(gate_report.get("reason_codes") or [])
    if gate_report.get("artifact_generation_status") not in {None, "PASS"}:
        reason_codes.add("artifact_generation_status_not_pass")
    if validator_status not in {None, "PASS"}:
        reason_codes.add("strict_validator_not_pass")

    summary.update(
        {
            "trial_index": index,
            "generated_at": generated_at,
            "status": status,
            "success": success,
            "message": message,
            "reason_codes": sorted(reason_codes),
            "artifact_publication_allowed": publication_allowed,
            "artifact_publication_lock_status": publication_lock_status,
            "artifact_paths": artifact_paths,
            "source_report_paths": source_report_paths,
            "source_report_generation_report_path": str(gate_report_path),
            "artifact_generation_report_path": str(artifact_generation_output_path),
            "validator_report_path": str(validator_output_path),
            "generated_artifact_count": int(
                gate_report.get("generated_artifact_count") or 0
            ),
            "artifact_count": len(artifact_paths),
            "validator_status": validator_status,
            "h_opt_005_ready": h_opt_005_ready,
            "h_opt_010_ready": h_opt_005_ready,
            "h_opt_005_blockers": gate_report.get("h_opt_005_blockers") or [],
            "components": gate_report.get("results") or [],
            "metrics": _trial_metrics_from_gate_report(gate_report),
            "gate_report": _compact_gate_report(gate_report),
        }
    )
    return summary


def _resume_validation_trial(
    *,
    index: int,
    generated_at: int,
    trial: dict[str, Any],
) -> dict[str, Any]:
    summary = dict(trial)
    summary.update(
        {
            "trial_index": index,
            "generated_at": generated_at,
            "success": False,
            "artifact_publication_allowed": False,
            "artifact_paths": trial.get("artifact_paths") or [],
            "source_report_paths": trial.get("source_report_paths") or [],
            "generated_artifact_count": int(
                trial.get("generated_artifact_count") or 0
            ),
            "artifact_count": int(trial.get("artifact_count") or 0),
            "validator_status": trial.get("validator_status"),
            "h_opt_005_ready": False,
            "h_opt_010_ready": False,
            "h_opt_005_blockers": trial.get("h_opt_005_blockers") or [
                "external_candidate_resumed_from_previous_summary"
            ],
            "components": trial.get("components") or [],
            "metrics": trial.get("metrics") or {},
        }
    )
    summary["status"] = "RESUMED"
    summary["message"] = "external candidate trial reused from resume checkpoint"
    summary["reason_codes"] = ["external_candidate_resumed_from_previous_summary"]
    return summary


def _failed_validation_trial(
    *,
    index: int,
    generated_at: int,
    trial: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    summary = dict(trial)
    summary.update(
        {
            "trial_index": index,
            "generated_at": generated_at,
            "status": "FAIL",
            "success": False,
            "message": message,
            "reason_codes": ["external_candidate_validation_scheduler_failed"],
            "artifact_paths": [],
            "source_report_paths": [],
            "generated_artifact_count": 0,
            "artifact_count": 0,
            "validator_status": None,
            "h_opt_005_ready": False,
            "h_opt_010_ready": False,
            "h_opt_005_blockers": [
                "external_candidate_validation_scheduler_failed"
            ],
            "components": [],
            "metrics": {},
        }
    )
    return summary


def _trial_source_report_dir(*, root: Path, trial: dict[str, Any]) -> Path:
    return (
        root
        / _safe_path_token(trial.get("symbol"))
        / _safe_path_token(trial.get("strategy_id"))
        / _safe_path_token(trial.get("candidate_version"))
    )


def _trial_gate_report_path(*, root: Path, trial: dict[str, Any]) -> Path:
    return (
        root
        / _safe_path_token(trial.get("symbol"))
        / _safe_path_token(trial.get("strategy_id"))
        / f"{_safe_path_token(trial.get('candidate_version'))}.json"
    )


def _trial_artifact_generation_report_path(gate_report_path: Path) -> Path:
    return gate_report_path.with_name(f"{gate_report_path.stem}-artifact-generation.json")


def _trial_validator_report_path(gate_report_path: Path) -> Path:
    return gate_report_path.with_name(f"{gate_report_path.stem}-validator.json")


def _trial_metrics_from_gate_report(gate_report: dict[str, Any]) -> dict[str, Any]:
    component_metrics = _component_metrics(gate_report)
    return {
        "oos_trade_count": _first_metric(component_metrics, "oos", "trade_count"),
        "oos_total_net_pnl": _first_metric(
            component_metrics,
            "oos",
            "total_net_pnl",
        ),
        "walk_forward_window_count": gate_report.get("walk_forward_window_count")
        or _first_metric(
            component_metrics,
            "walk_forward",
            "walk_forward_window_count",
        ),
        "walk_forward_pass_count": gate_report.get("walk_forward_pass_count")
        or _first_metric(
            component_metrics,
            "walk_forward",
            "walk_forward_pass_count",
        ),
        "walk_forward_pass_rate": gate_report.get("walk_forward_pass_rate")
        or _first_metric(
            component_metrics,
            "walk_forward",
            "walk_forward_pass_rate",
        ),
        "monte_carlo_survival_rate": gate_report.get("monte_carlo_survival_rate")
        or _first_metric(
            component_metrics,
            "monte_carlo",
            "monte_carlo_survival_rate",
        ),
    }


def _component_metrics(gate_report: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    pairs = []
    for result in gate_report.get("results") or []:
        if not isinstance(result, dict):
            continue
        source_path = str(result.get("source_report_path") or "")
        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            continue
        if source_path.endswith("-wf.json") or "walk_forward" in metrics:
            pairs.append(("walk_forward", metrics.get("walk_forward") or metrics))
        elif source_path.endswith("-mc.json") or "monte_carlo_survival_rate" in metrics:
            pairs.append(("monte_carlo", metrics))
        elif source_path.endswith("-oos.json"):
            pairs.append(("oos", metrics))
        else:
            pairs.append(("unknown", metrics))
    return pairs


def _first_metric(
    component_metrics: list[tuple[str, dict[str, Any]]],
    component: str,
    metric_name: str,
) -> Any:
    for item_component, metrics in component_metrics:
        if item_component != component:
            continue
        if metric_name in metrics:
            return metrics[metric_name]
    return None


def _compact_gate_report(gate_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": gate_report.get("status"),
        "message": gate_report.get("message"),
        "generation_kind": gate_report.get("generation_kind"),
        "reason_codes": gate_report.get("reason_codes") or [],
        "source_report_paths": gate_report.get("source_report_paths") or [],
        "generated_source_report_count": gate_report.get(
            "generated_source_report_count",
        ),
        "artifact_generation_status": gate_report.get("artifact_generation_status"),
        "artifact_generation_report_path": gate_report.get(
            "artifact_generation_report_path",
        ),
        "artifact_count": gate_report.get("artifact_count"),
        "artifact_paths": gate_report.get("artifact_paths") or [],
        "validator_status": gate_report.get("validator_status"),
        "validator_artifact_count": gate_report.get("validator_artifact_count"),
        "h_opt_005_ready": gate_report.get("h_opt_005_ready"),
        "h_opt_005_blockers": gate_report.get("h_opt_005_blockers") or [],
    }


def _timeout_pending_trial(
    *,
    index: int,
    generated_at: int,
    trial: dict[str, Any],
    stopped_reason: str | None,
) -> dict[str, Any]:
    summary = dict(trial)
    reason_codes = set(summary.get("reason_codes") or [])
    if stopped_reason:
        reason_codes.add(stopped_reason)
    summary.update(
        {
            "trial_index": index,
            "generated_at": generated_at,
            "status": "PENDING",
            "success": False,
            "message": "external candidate trial was not started before scheduler stop",
            "reason_codes": sorted(reason_codes),
            "artifact_paths": [],
            "source_report_paths": [],
            "generated_artifact_count": 0,
            "artifact_count": 0,
            "validator_status": None,
            "h_opt_005_ready": False,
            "h_opt_010_ready": False,
            "h_opt_005_blockers": sorted(reason_codes),
            "components": [],
            "metrics": {},
        }
    )
    return summary


def _ordered_completed_trials(
    completed_by_index: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        completed_by_index[index]
        for index in sorted(completed_by_index)
    ]


def _merge_completed_and_pending_trials(
    *,
    trials: list[dict[str, Any]],
    completed_by_index: dict[int, dict[str, Any]],
    generated_at: int,
    stopped_reason: str | None,
) -> list[dict[str, Any]]:
    merged = []
    for index, trial in enumerate(trials, start=1):
        if index in completed_by_index:
            merged.append(completed_by_index[index])
            continue
        merged.append(
            _timeout_pending_trial(
                index=index,
                generated_at=generated_at,
                trial=trial,
                stopped_reason=stopped_reason,
            )
        )
    return merged


def _validation_progress_summary(
    *,
    planned_trial_count: int,
    completed_trials: list[dict[str, Any]],
    active_worker_count: int,
    started_monotonic: float,
) -> dict[str, Any]:
    completed_count = len(completed_trials)
    pass_count = sum(1 for trial in completed_trials if trial.get("status") == "PASS")
    elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
    if planned_trial_count <= 0:
        validation_percent = 100.0
        eta_seconds = None
    else:
        validation_percent = min(100.0, completed_count / planned_trial_count * 100.0)
        remaining = max(0, planned_trial_count - completed_count)
        eta_seconds = (
            elapsed_seconds / completed_count * remaining
            if completed_count > 0 and remaining > 0
            else None
        )
    return {
        "ingestion_percent_complete": 100.0,
        "validation_percent_complete": validation_percent,
        "completed_trial_count": completed_count,
        "planned_trial_count": planned_trial_count,
        "pass_count": pass_count,
        "best_walk_forward_pass_rate": _best_metric(
            completed_trials,
            "walk_forward_pass_rate",
        ),
        "best_monte_carlo_survival_rate": _best_metric(
            completed_trials,
            "monte_carlo_survival_rate",
        ),
        "eta_seconds": eta_seconds,
        "elapsed_seconds": elapsed_seconds,
        "active_worker_count": active_worker_count,
    }


def _scheduler_progress_report(
    *,
    generated_at: int,
    ingestion_status: str,
    planned_trial_count: int,
    progress_summary: dict[str, Any],
    reason_codes: list[str],
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "task_scope": "H-OPT-020",
        "ingestion_status": ingestion_status,
        "planned_trial_count": planned_trial_count,
        "completed_trial_count": progress_summary["completed_trial_count"],
        "pass_count": progress_summary["pass_count"],
        "artifact_count": 0,
        "progress_summary": progress_summary,
        "reason_codes": reason_codes,
    }


def _best_candidate(trials: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not trials:
        return None
    return sorted(trials, key=_trial_score, reverse=True)[0]


def _trial_score(trial: dict[str, Any]) -> tuple[Any, ...]:
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
    return (
        1 if trial.get("status") == "PASS" else 0,
        1 if trial.get("validator_status") == "PASS" else 0,
        int(trial.get("generated_artifact_count") or 0),
        _score_float(metrics.get("monte_carlo_survival_rate")),
        _score_float(metrics.get("walk_forward_pass_rate")),
        _score_int(metrics.get("walk_forward_pass_count")),
        _score_float(metrics.get("oos_total_net_pnl")),
        -len(trial.get("reason_codes") or []),
        -int(trial.get("trial_index") or 0),
    )


def _best_metric(trials: list[dict[str, Any]], metric_name: str) -> float | None:
    values = []
    for trial in trials:
        metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
        value = _coerce_float(metrics.get(metric_name))
        if value is not None:
            values.append(value)
    return max(values) if values else None


def _score_float(value: Any) -> float:
    parsed = _coerce_float(value)
    return float("-inf") if parsed is None else parsed


def _score_int(value: Any) -> int:
    parsed = _coerce_int(value)
    return -1 if parsed is None else parsed


def _candidate_filters(
    *,
    strategy_ids: list[str] | None,
    symbols: list[str] | None,
    timeframes: list[str] | None,
) -> dict[str, Any]:
    return {
        "strategy_ids": sorted({item.strip().lower() for item in strategy_ids or [] if item.strip()}),
        "symbols": sorted({item.strip().upper() for item in symbols or [] if item.strip()}),
        "timeframes": sorted({item.strip() for item in timeframes or [] if item.strip()}),
    }


def _candidate_filter_reason(
    candidate: dict[str, Any],
    filters: dict[str, Any],
) -> str | None:
    if filters["strategy_ids"] and candidate["strategy_id"] not in filters["strategy_ids"]:
        return "candidate_filtered_by_strategy_id"
    if filters["symbols"] and candidate["symbol"] not in filters["symbols"]:
        return "candidate_filtered_by_symbol"
    if filters["timeframes"] and candidate["timeframe"] not in filters["timeframes"]:
        return "candidate_filtered_by_timeframe"
    return None


def _load_resumed_candidate_versions(resume_from: str | Path | None) -> set[str]:
    if resume_from is None:
        return set()
    resume_path = Path(resume_from)
    try:
        payload = json.loads(resume_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    trials = payload.get("all_trials") if isinstance(payload, dict) else []
    if not isinstance(trials, list):
        return set()
    return {
        str(trial.get("candidate_version"))
        for trial in trials
        if isinstance(trial, dict)
        and trial.get("candidate_version")
        and _trial_is_resume_reusable(trial)
    }


def _trial_is_resume_reusable(trial: dict[str, Any]) -> bool:
    status = str(trial.get("status") or "").upper()
    if status in {"PASS", "FAIL", "SKIPPED", "RESUMED"}:
        return True
    if trial.get("artifact_publication_allowed") is True:
        return True
    if trial.get("source_report_generation_report_path"):
        return True
    if trial.get("gate_report"):
        return True
    return False


def _stop_until_pass_workflow_summary(
    *,
    enabled: bool,
    max_runtime_seconds: float | None,
    effective_stop_on_first_pass: bool,
    passing_candidates: list[dict[str, Any]],
    all_trials: list[dict[str, Any]],
    stopped_reason: str | None,
    resume_command: str,
) -> dict[str, Any]:
    pending_trials = [
        trial
        for trial in all_trials
        if str(trial.get("status") or "").upper() == "PENDING"
    ]
    reusable_trials = [
        trial for trial in all_trials if _trial_is_resume_reusable(trial)
    ]
    pass_found = bool(passing_candidates)
    return {
        "enabled": bool(enabled),
        "mode": "keep_running_until_pass_with_timeout"
        if enabled
        else "single_run",
        "requires_timeout": bool(enabled),
        "timeout_configured": max_runtime_seconds is not None,
        "max_runtime_seconds": max_runtime_seconds,
        "effective_stop_on_first_pass": effective_stop_on_first_pass,
        "pass_found": pass_found,
        "should_resume": bool(enabled and not pass_found),
        "resume_command": resume_command if enabled and not pass_found else None,
        "pending_trial_count": len(pending_trials),
        "resume_reusable_trial_count": len(reusable_trials),
        "stopped_reason": stopped_reason,
        "reason_codes": _stop_until_pass_reason_codes(
            enabled=enabled,
            max_runtime_seconds=max_runtime_seconds,
            pass_found=pass_found,
            pending_trial_count=len(pending_trials),
        ),
    }


def _stop_until_pass_reason_codes(
    *,
    enabled: bool,
    max_runtime_seconds: float | None,
    pass_found: bool,
    pending_trial_count: int,
) -> list[str]:
    reason_codes = set()
    if not enabled:
        reason_codes.add("stop_until_pass_workflow_disabled")
    else:
        reason_codes.add("stop_until_pass_workflow_enabled")
        if max_runtime_seconds is None:
            reason_codes.add("stop_until_pass_timeout_not_configured")
        if pass_found:
            reason_codes.add("stop_until_pass_workflow_pass_found")
        else:
            reason_codes.add("stop_until_pass_resume_command_available")
        if pending_trial_count:
            reason_codes.add("stop_until_pass_pending_trials_remaining")
    return sorted(reason_codes)


def _resume_command(
    *,
    universe_matrix_path: Path,
    external_candidates_path: Path,
    output_path: Path | None,
    workers: int,
    max_trials: int,
    progress_interval_seconds: float,
    max_runtime_seconds: float | None,
    stop_on_first_pass: bool,
    keep_running_until_pass_with_timeout: bool,
    source_report_dir: Path,
    gate_report_dir: Path,
    artifact_dir: Path,
    monte_carlo_run_count: int,
    min_walk_forward_pass_rate: float = MIN_WALK_FORWARD_PASS_RATE,
    min_net_pnl: float = 0.0,
    min_out_of_sample_net_pnl: float = 0.0,
) -> str:
    parts = [
        "SMARTQTF_USE_PROXY=1",
        "python",
        "scripts/run_external_candidate_validation.py",
        "--universe-matrix",
        str(universe_matrix_path),
        "--external-candidates",
        str(external_candidates_path),
        "--workers",
        str(workers),
        "--max-trials",
        str(max_trials),
        "--progress-interval-seconds",
        str(progress_interval_seconds),
    ]
    if output_path is not None:
        parts.extend(["--resume-from", str(output_path), "--output", str(output_path)])
    if max_runtime_seconds is not None:
        parts.extend(["--max-runtime-seconds", str(max_runtime_seconds)])
    if stop_on_first_pass:
        parts.append("--stop-on-first-pass")
    if keep_running_until_pass_with_timeout:
        parts.append("--keep-running-until-pass-with-timeout")
    parts.extend(["--source-report-dir", str(source_report_dir)])
    parts.extend(["--gate-report-dir", str(gate_report_dir)])
    parts.extend(["--artifact-dir", str(artifact_dir)])
    parts.extend(["--monte-carlo-run-count", str(monte_carlo_run_count)])
    if float(min_walk_forward_pass_rate) != MIN_WALK_FORWARD_PASS_RATE:
        parts.extend(["--min-walk-forward-pass-rate", str(min_walk_forward_pass_rate)])
    if float(min_net_pnl) != 0.0:
        parts.extend(["--min-net-pnl", str(min_net_pnl)])
    if float(min_out_of_sample_net_pnl) != 0.0:
        parts.extend(["--min-out-of-sample-net-pnl", str(min_out_of_sample_net_pnl)])
    return " ".join(parts)


def _resolve_output_path(value: Any, matrix_path: Path) -> Path | None:
    if value is None:
        return None
    candidate = Path(str(value))
    if candidate.is_absolute():
        return candidate
    project_candidate = PROJECT_ROOT / candidate
    if project_candidate.exists():
        return project_candidate
    matrix_candidate = matrix_path.parent / candidate
    if matrix_candidate.exists():
        return matrix_candidate
    return project_candidate


def _file_fingerprint(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "exists": False, "size_bytes": None, "sha256": None}
    candidate = Path(path)
    try:
        stat_result = candidate.stat()
    except OSError:
        return {
            "path": str(candidate),
            "exists": False,
            "size_bytes": None,
            "sha256": None,
        }
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(candidate),
        "exists": True,
        "size_bytes": stat_result.st_size,
        "sha256": digest.hexdigest(),
    }


def _write_progress(report: dict[str, Any], progress_jsonl: Path | None) -> None:
    if progress_jsonl is None:
        return
    progress_jsonl.parent.mkdir(parents=True, exist_ok=True)
    progress = {
        "generated_at": report["generated_at"],
        "task_scope": report["task_scope"],
        "ingestion_status": report["ingestion_status"],
        "planned_trial_count": report["planned_trial_count"],
        "completed_trial_count": report["completed_trial_count"],
        "pass_count": report["pass_count"],
        "artifact_count": report["artifact_count"],
        "progress_summary": report["progress_summary"],
        "reason_codes": report["reason_codes"],
    }
    with progress_jsonl.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(progress, ensure_ascii=False, sort_keys=True) + "\n")


def _print_progress(report: dict[str, Any]) -> None:
    summary = report["progress_summary"]
    eta_seconds = summary.get("eta_seconds")
    elapsed_seconds = summary.get("elapsed_seconds")
    best_wf = summary.get("best_walk_forward_pass_rate")
    best_mc = summary.get("best_monte_carlo_survival_rate")
    print(
        "external-candidate-progress "
        f"ingest={summary['ingestion_percent_complete']:.1f}% "
        f"validation={summary['validation_percent_complete']:.1f}% "
        f"completed={summary['completed_trial_count']}/"
        f"{summary['planned_trial_count']} "
        f"pass_count={summary['pass_count']} "
        f"best_wf={_format_progress_value(best_wf)} "
        f"best_mc={_format_progress_value(best_mc)} "
        f"eta={_format_progress_seconds(eta_seconds)} "
        f"elapsed={_format_progress_seconds(elapsed_seconds)} "
        f"artifacts={report['artifact_count']} "
        f"workers={summary['active_worker_count']}"
    )


def _format_progress_value(value: Any) -> str:
    parsed = _coerce_float(value)
    if parsed is None:
        return "n/a"
    return f"{parsed:.4f}"


def _format_progress_seconds(value: Any) -> str:
    parsed = _coerce_float(value)
    if parsed is None:
        return "n/a"
    return f"{parsed:.1f}s"


def _write_report(report: dict[str, Any], output_path: Path | None) -> dict[str, Any]:
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def _default_safety_flags() -> dict[str, bool]:
    return {
        "network_access_used": False,
        "public_market_data_only": True,
        "real_credentials_read": False,
        "broker_called": False,
        "account_or_order_endpoint_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
    }


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_path_token(value: Any) -> str:
    token = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in str(value)
    ).strip("_")
    return token or "unknown"


def _runtime_exhausted(
    started_monotonic: float,
    max_runtime_seconds: float | None,
) -> bool:
    if max_runtime_seconds is None:
        return False
    return time.monotonic() - started_monotonic >= max_runtime_seconds


def _parse_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in str(value).split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest external strategy candidates for H-OPT-020 validation.",
    )
    parser.add_argument("--universe-matrix", default=str(DEFAULT_UNIVERSE_MATRIX_PATH))
    parser.add_argument("--external-candidates", default=str(DEFAULT_EXTERNAL_CANDIDATES_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--progress-jsonl", default=str(DEFAULT_PROGRESS_JSONL_PATH))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-trials", type=int, default=20)
    parser.add_argument("--progress-interval-seconds", type=float, default=10.0)
    parser.add_argument("--max-runtime-seconds", type=float, default=None)
    parser.add_argument("--stop-on-first-pass", action="store_true")
    parser.add_argument("--keep-running-until-pass-with-timeout", action="store_true")
    parser.add_argument(
        "--source-report-dir",
        default=str(DEFAULT_EXTERNAL_CANDIDATE_SOURCE_REPORT_DIR),
    )
    parser.add_argument(
        "--gate-report-dir",
        default=str(DEFAULT_EXTERNAL_CANDIDATE_GATE_REPORT_DIR),
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(DEFAULT_EXTERNAL_CANDIDATE_ARTIFACT_DIR),
    )
    parser.add_argument(
        "--monte-carlo-run-count",
        type=int,
        default=DEFAULT_MONTE_CARLO_RUN_COUNT,
    )
    parser.add_argument(
        "--min-walk-forward-pass-rate",
        type=float,
        default=MIN_WALK_FORWARD_PASS_RATE,
        help="Explicit relaxed-flow override. Default remains official 0.67; values below 0.67 are flow-only and do not satisfy H-OPT official gate.",
    )
    parser.add_argument(
        "--min-net-pnl",
        type=float,
        default=0.0,
        help="Explicit relaxed-flow override for aggregate artifact net PnL. Default 0.0; negative values are flow-only and do not satisfy H-OPT official gate.",
    )
    parser.add_argument(
        "--min-out-of-sample-net-pnl",
        type=float,
        default=0.0,
        help="Explicit relaxed-flow override for OOS net PnL. Default 0.0; negative values are flow-only and do not satisfy H-OPT official gate.",
    )
    parser.add_argument("--strategy-ids", type=_parse_csv, default=None)
    parser.add_argument("--symbols", type=_parse_csv, default=None)
    parser.add_argument("--timeframes", type=_parse_csv, default=None)
    parser.add_argument("--resume-from", default=None)
    parser.add_argument("--timestamp", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_external_candidate_validation(
        universe_matrix=args.universe_matrix,
        external_candidates=args.external_candidates,
        output_path=args.output,
        progress_jsonl=args.progress_jsonl,
        workers=args.workers,
        max_trials=args.max_trials,
        progress_interval_seconds=args.progress_interval_seconds,
        max_runtime_seconds=args.max_runtime_seconds,
        stop_on_first_pass=args.stop_on_first_pass,
        keep_running_until_pass_with_timeout=args.keep_running_until_pass_with_timeout,
        strategy_ids=args.strategy_ids,
        symbols=args.symbols,
        timeframes=args.timeframes,
        resume_from=args.resume_from,
        source_report_dir=args.source_report_dir,
        gate_report_dir=args.gate_report_dir,
        artifact_dir=args.artifact_dir,
        monte_carlo_run_count=args.monte_carlo_run_count,
        min_walk_forward_pass_rate=args.min_walk_forward_pass_rate,
        min_net_pnl=args.min_net_pnl,
        min_out_of_sample_net_pnl=args.min_out_of_sample_net_pnl,
        timestamp=args.timestamp,
    )
    if report.get("ingestion_status") == "PASS":
        return 0
    return 2 if report.get("status") == "SKIPPED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
