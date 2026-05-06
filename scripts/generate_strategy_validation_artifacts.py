#!/usr/bin/env python
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.optimization.artifact_generation import (
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_SOURCE_REPORT_DIR,
    StrategyValidationArtifactSourceReport,
    build_strategy_validation_artifact,
    discover_source_reports,
    load_source_report,
)
from quant.optimization.validation_artifacts import StrategyValidationArtifactStore
from scripts.validate_strategy_validation_artifacts import (
    DEFAULT_OUTPUT_PATH as DEFAULT_VALIDATOR_OUTPUT_PATH,
    run_strategy_validation_artifacts_validation,
)


DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "generation-latest.json"
)
DEFAULT_SOURCE_REPORT_EXAMPLE_PATH = (
    PROJECT_ROOT / "config" / "examples" / "strategy-validation-source-report.example.json"
)


def run_strategy_validation_artifact_generation(
    *,
    source_reports: list[str | Path] | None,
    source_report_dirs: list[str | Path] | None = None,
    artifact_dir: str | Path | None = DEFAULT_ARTIFACT_DIR,
    output_path: str | Path | None = DEFAULT_OUTPUT_PATH,
    validator_output_path: str | Path | None = DEFAULT_VALIDATOR_OUTPUT_PATH,
    timestamp: int | None = None,
    require_gate_pass: bool = False,
    min_trades: int = 1,
    min_net_pnl: float = 0.0,
    max_drawdown: float | None = None,
    min_win_rate: float | None = None,
    min_out_of_sample_net_pnl: float = 0.0,
    min_walk_forward_windows: int = 1,
    min_walk_forward_pass_rate: float | None = 0.0,
    min_monte_carlo_survival_rate: float | None = 0.0,
    overwrite_existing_artifacts: bool = False,
) -> dict[str, Any]:
    generated_at = int(time.time()) if timestamp is None else timestamp
    active_source_report_dirs = (
        [DEFAULT_SOURCE_REPORT_DIR] if source_report_dirs is None else source_report_dirs
    )
    source_paths = discover_source_reports(
        source_reports=source_reports,
        source_report_dirs=active_source_report_dirs,
    )
    if not source_paths:
        report = _build_report(
            generated_at=generated_at,
            source_report_dirs=active_source_report_dirs,
            discovered_source_reports=[],
            artifact_dir=artifact_dir,
            output_path=output_path,
            status="SKIPPED",
            message="no validation source report JSON files were provided",
            generated=[],
            errors=[],
            validator_report=None,
            overwrite_existing_artifacts=overwrite_existing_artifacts,
            prepared_artifact_count=0,
            prepared_artifacts=[],
        )
        return _write_report(report, output_path)

    artifact_store = StrategyValidationArtifactStore(
        artifact_dir if artifact_dir is not None else DEFAULT_ARTIFACT_DIR
    )
    generated: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    planned_artifact_targets: dict[Path, str] = {}
    prepared_artifacts: list[dict[str, Any]] = []
    artifact_store.root.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="strategy-validation-artifacts-",
        dir=str(artifact_store.root.parent),
    ) as staging_dir:
        staging_store = StrategyValidationArtifactStore(staging_dir)
        for source_path in source_paths:
            try:
                source_report = load_source_report(source_path)
                artifact = build_strategy_validation_artifact(
                    source_report,
                    source_path=source_path,
                )
                artifact_path = artifact_store.artifact_path_for(artifact)
                resolved_artifact_path = artifact_path.resolve()
                first_source_path = planned_artifact_targets.get(resolved_artifact_path)
                if first_source_path is not None:
                    raise ValueError(
                        "duplicate strategy validation artifact target "
                        f"{artifact_path} already planned from {first_source_path}"
                    )
                if artifact_path.exists() and not overwrite_existing_artifacts:
                    raise ValueError(
                        "strategy validation artifact target already exists "
                        f"{artifact_path}; pass --overwrite-existing-artifacts only after "
                        "confirming the replacement is an intentional, replayable rerun"
                    )
                planned_artifact_targets[resolved_artifact_path] = str(source_path)
                stage_path = staging_store.write_artifact(artifact)
                prepared_artifacts.append(
                    {
                        "source_path": str(source_path),
                        "artifact_path": artifact_path,
                        "stage_path": stage_path,
                        "artifact_id": artifact.artifact_id,
                        "source_report_id": artifact.source_report_id,
                        "strategy_id": artifact.strategy_id,
                        "candidate_version": artifact.candidate_version,
                        "symbol": artifact.symbol,
                    }
                )
            except BaseException as exc:
                errors.append(
                    _build_rejected_source_error(
                        source_path=source_path,
                        message=str(exc),
                        source_report=_load_partial_source_report(source_path),
                        artifact_store=artifact_store,
                    )
                )
                continue

        validator_report = None
        status = "FAIL"
        message = "strategy validation artifact generation failed"
        if prepared_artifacts:
            stage_to_final_paths = {
                str(entry["stage_path"]): str(entry["artifact_path"])
                for entry in prepared_artifacts
            }
            validator_report = run_strategy_validation_artifacts_validation(
                artifact_dir=None,
                artifact_paths=[entry["stage_path"] for entry in prepared_artifacts],
                output_path=None,
                timestamp=generated_at,
                require_gate_pass=require_gate_pass,
                min_trades=min_trades,
                min_net_pnl=min_net_pnl,
                max_drawdown=max_drawdown,
                min_win_rate=min_win_rate,
                min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
                min_walk_forward_windows=min_walk_forward_windows,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
            )
            validator_report = _remap_validator_report_paths(
                validator_report,
                path_mapping=stage_to_final_paths,
            )
            if validator_report["status"] == "PASS":
                try:
                    generated = _commit_prepared_artifacts(prepared_artifacts)
                except BaseException as exc:
                    errors.append(
                        {
                            "error_type": "artifact_commit_failed",
                            "source_path": "__commit__",
                            "message": (
                                "failed to commit validated strategy validation artifacts: "
                                f"{exc}"
                            ),
                        }
                    )
                    message = (
                        "strategy validation artifact generation passed strict staging "
                        "validation, but artifact commit failed so no artifact was published"
                    )
                else:
                    committed_artifact_paths = [
                        Path(entry["artifact_path"]) for entry in generated
                    ]
                    if validator_output_path is not None:
                        validator_report = run_strategy_validation_artifacts_validation(
                            artifact_dir=None,
                            artifact_paths=committed_artifact_paths,
                            output_path=validator_output_path,
                            timestamp=generated_at,
                            require_gate_pass=require_gate_pass,
                            min_trades=min_trades,
                            min_net_pnl=min_net_pnl,
                            max_drawdown=max_drawdown,
                            min_win_rate=min_win_rate,
                            min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
                            min_walk_forward_windows=min_walk_forward_windows,
                            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
                        )
                    if not errors:
                        status = "PASS"
                        message = "strategy validation artifact generation passed"
                    else:
                        message = (
                            "strategy validation artifact generation committed validated "
                            "artifacts, but some source reports were rejected"
                        )
            else:
                message = (
                    "strategy validation artifact generation completed staging self-check, "
                    "but strict validation did not pass so no artifact was committed"
                )
        elif errors:
            message = (
                "strategy validation artifact generation failed before any artifact was written"
            )

        report = _build_report(
            generated_at=generated_at,
            source_report_dirs=active_source_report_dirs,
            discovered_source_reports=source_paths,
            artifact_dir=artifact_dir,
            output_path=output_path,
            status=status,
            message=message,
            generated=generated,
            errors=errors,
            validator_report=validator_report,
            overwrite_existing_artifacts=overwrite_existing_artifacts,
            prepared_artifact_count=len(prepared_artifacts),
            prepared_artifacts=prepared_artifacts,
        )
        return _write_report(report, output_path)


def _build_report(
    *,
    generated_at: int,
    source_report_dirs: list[str | Path] | None,
    discovered_source_reports: list[str | Path],
    artifact_dir: str | Path | None,
    output_path: str | Path | None,
    status: str,
    message: str,
    generated: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    validator_report: dict[str, Any] | None,
    overwrite_existing_artifacts: bool,
    prepared_artifact_count: int,
    prepared_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    source_report_errors, pipeline_errors = _split_generation_errors(errors)
    source_report_discovery = _source_report_discovery(
        source_report_dirs=source_report_dirs,
        discovered_source_reports=discovered_source_reports,
    )
    source_report_results = _source_report_results(
        discovered_source_reports=discovered_source_reports,
        generated=generated,
        errors=errors,
        prepared_artifacts=prepared_artifacts,
        validator_report=validator_report,
    )
    source_report_evidence_summaries = _source_report_evidence_summaries(
        discovered_source_reports
    )
    h_opt_005_readiness = _h_opt_005_readiness(
        status=status,
        generated=generated,
        source_report_errors=source_report_errors,
        pipeline_errors=pipeline_errors,
        validator_report=validator_report,
        source_report_discovery=source_report_discovery,
    )
    return {
        "success": status == "PASS",
        "status": status,
        "generated_at": generated_at,
        "message": message,
        "source_report_dirs": [
            str(path) for path in source_report_dirs or []
        ],
        "discovered_source_reports": [
            str(path) for path in discovered_source_reports
        ],
        "artifact_dir": str(artifact_dir) if artifact_dir is not None else None,
        "output_path": str(output_path) if output_path is not None else None,
        "source_report_count": len(discovered_source_reports),
        "source_report_discovery": source_report_discovery,
        "source_report_fingerprints": _file_fingerprints(
            discovered_source_reports,
        ),
        "generation_error_count": len(errors),
        "prepared_artifact_count": prepared_artifact_count,
        "generated_artifact_count": len(generated),
        "generated_artifact_fingerprints": _file_fingerprints(
            [entry.get("artifact_path") for entry in generated],
        ),
        "rejected_source_report_count": len(source_report_errors),
        "pipeline_error_count": len(pipeline_errors),
        "overwrite_existing_artifacts": overwrite_existing_artifacts,
        "h_opt_005_ready": h_opt_005_readiness["ready"],
        "h_opt_005_ready_scope": h_opt_005_readiness["scope"],
        "h_opt_005_blockers": h_opt_005_readiness["blockers"],
        "h_opt_005_next_action": h_opt_005_readiness["next_action"],
        "source_report_results": source_report_results,
        "source_report_evidence_summary": _aggregate_source_report_evidence(
            source_report_evidence_summaries
        ),
        "source_report_evidence_summaries": source_report_evidence_summaries,
        "source_report_status_counts": _count_result_values(
            source_report_results,
            key="status",
        ),
        "validator_status_counts": _count_result_values(
            source_report_results,
            key="validator_status",
            skip_none=True,
        ),
        "validator_category_counts": _count_result_values(
            source_report_results,
            key="validator_category",
            skip_none=True,
        ),
        "validator_reason_code_counts": _count_result_reason_codes(
            source_report_results,
            key="validator_reason_codes",
        ),
        "generated": generated,
        "source_report_errors": source_report_errors,
        "pipeline_errors": pipeline_errors,
        "errors": errors,
        "validator_report": validator_report,
        "source_report_example_path": str(DEFAULT_SOURCE_REPORT_EXAMPLE_PATH),
        "required_source_report_fields": [
            "report_id",
            "strategy_id",
            "candidate_version",
            "symbol",
            "generated_at",
            "summary.trade_count",
            "summary.total_net_pnl",
            "summary.max_drawdown",
            "summary.win_rate",
            "validation_slices[].name",
            "validation_slices[].kind",
            "validation_slices[].trade_count",
            "validation_slices[].total_net_pnl",
            "validation_slices[].max_drawdown",
            "validation_slices[].win_rate",
        ],
        "recommended_source_report_fields": [
            "summary.sharpe_ratio",
            "validation_slices[].sharpe_ratio",
            "monte_carlo_survival_rate",
            "monte_carlo_validation.method",
            "monte_carlo_validation.run_count",
            "monte_carlo_validation.perturbation_dimensions",
            "monte_carlo_validation.seed",
            "monte_carlo_validation.survival_threshold",
            "source_report_id",
            "source_path",
        ],
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
        "proxy": {
            "SMARTQTF_USE_PROXY": os.getenv("SMARTQTF_USE_PROXY"),
            "required_for_external_artifacts": False,
        },
    }


def _h_opt_005_readiness(
    *,
    status: str,
    generated: list[dict[str, Any]],
    source_report_errors: list[dict[str, Any]],
    pipeline_errors: list[dict[str, Any]],
    validator_report: dict[str, Any] | None,
    source_report_discovery: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    if status == "SKIPPED":
        blockers.append("no_source_reports_discovered")
        if int(source_report_discovery.get("missing_dir_count") or 0) > 0:
            blockers.append("source_report_dirs_missing")
        if int(source_report_discovery.get("configured_dir_without_json_count") or 0) > 0:
            blockers.append("source_report_dirs_without_json")
    if status != "PASS":
        blockers.append("generation_status_not_pass")
    if not generated:
        blockers.append("no_artifacts_generated")
    if source_report_errors:
        blockers.append("source_reports_rejected")
    if pipeline_errors:
        blockers.append("artifact_commit_failed")
    if validator_report is None:
        blockers.append("strict_validator_not_run")
    else:
        if validator_report.get("status") != "PASS":
            blockers.append("strict_validator_not_pass")
        if int(validator_report.get("artifact_count") or 0) < 1:
            blockers.append("strict_validator_artifact_count_zero")
        if int(validator_report.get("failed_count") or 0) > 0:
            blockers.append("strict_validator_failed_count_nonzero")

    unique_blockers: list[str] = []
    seen = set()
    for blocker in blockers:
        if blocker in seen:
            continue
        seen.add(blocker)
        unique_blockers.append(blocker)

    ready = not unique_blockers
    return {
        "ready": ready,
        "scope": "artifact_generation_only",
        "blockers": unique_blockers,
        "next_action": (
            "handoff_generated_artifacts_to_h_opt_005_manual_review"
            if ready
            else "provide_real_source_reports_and_rerun_strict_generation"
        ),
    }


def _source_report_discovery(
    *,
    source_report_dirs: list[str | Path] | None,
    discovered_source_reports: list[str | Path],
) -> dict[str, Any]:
    checks = []
    missing_dir_count = 0
    configured_dir_without_json_count = 0
    json_candidate_count = 0
    for configured_path in source_report_dirs or []:
        path = Path(configured_path)
        exists = path.exists()
        kind = "missing"
        json_file_count = 0
        if exists:
            kind = "file" if path.is_file() else "directory"
            json_file_count = _json_file_count(path)
        discovered_source_report_count = _discovered_source_report_count(
            configured_path=path,
            discovered_source_reports=discovered_source_reports,
        )
        if not exists:
            missing_dir_count += 1
            status = "MISSING"
        elif json_file_count == 0:
            configured_dir_without_json_count += 1
            status = "NO_JSON_FILES"
        elif discovered_source_report_count > 0:
            status = "DISCOVERED"
        else:
            status = "JSON_FILES_FOUND"
        json_candidate_count += json_file_count
        checks.append(
            {
                "path": str(path),
                "exists": exists,
                "kind": kind,
                "status": status,
                "json_file_count": json_file_count,
                "discovered_source_report_count": discovered_source_report_count,
            }
        )
    return {
        "configured_dir_count": len(source_report_dirs or []),
        "missing_dir_count": missing_dir_count,
        "configured_dir_without_json_count": configured_dir_without_json_count,
        "json_candidate_count": json_candidate_count,
        "checks": checks,
    }


def _json_file_count(path: Path) -> int:
    if path.is_file():
        return 1 if path.suffix.lower() == ".json" else 0
    return sum(1 for _ in path.rglob("*.json"))


def _discovered_source_report_count(
    *,
    configured_path: Path,
    discovered_source_reports: list[str | Path],
) -> int:
    discovered_count = 0
    resolved_configured_path = _resolved_path(configured_path)
    for source_path in discovered_source_reports:
        resolved_source_path = _resolved_path(source_path)
        if configured_path.exists() and configured_path.is_file():
            if resolved_source_path == resolved_configured_path:
                discovered_count += 1
            continue
        try:
            if resolved_source_path.is_relative_to(resolved_configured_path):
                discovered_count += 1
        except ValueError:
            continue
    return discovered_count


def _resolved_path(path: str | Path) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def _source_report_results(
    *,
    discovered_source_reports: list[str | Path],
    generated: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    prepared_artifacts: list[dict[str, Any]],
    validator_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    generated_by_source = {entry["source_path"]: entry for entry in generated}
    errors_by_source = {entry["source_path"]: entry for entry in errors}
    prepared_by_source = {
        entry["source_path"]: entry
        for entry in _serializable_prepared_artifacts(prepared_artifacts)
    }
    validator_checks_by_artifact_path = _validator_checks_by_artifact_path(
        validator_report
    )
    commit_failed = any(
        entry.get("error_type") == "artifact_commit_failed"
        or entry.get("source_path") == "__commit__"
        for entry in errors
    )
    results = []
    for source_path in discovered_source_reports:
        source_key = str(source_path)
        if source_key in generated_by_source:
            entry = generated_by_source[source_key]
            validator_check = _validator_check_for_artifact_path(
                artifact_path=entry.get("artifact_path"),
                validator_checks_by_artifact_path=validator_checks_by_artifact_path,
            )
            results.append(
                _source_report_result_entry(
                    source_path=source_key,
                    status="GENERATED",
                    message="artifact committed after strict validation",
                    artifact_path=entry.get("artifact_path"),
                    artifact_id=entry.get("artifact_id"),
                    source_report_id=entry.get("source_report_id"),
                    strategy_id=entry.get("strategy_id"),
                    candidate_version=entry.get("candidate_version"),
                    symbol=entry.get("symbol"),
                    validator_check=validator_check,
                )
            )
            continue
        if source_key in errors_by_source:
            entry = errors_by_source[source_key]
            results.append(
                _source_report_result_entry(
                    source_path=source_key,
                    status="REJECTED",
                    message=entry.get("message"),
                    artifact_path=entry.get("artifact_path"),
                    artifact_id=None,
                    source_report_id=entry.get("source_report_id"),
                    strategy_id=entry.get("strategy_id"),
                    candidate_version=entry.get("candidate_version"),
                    symbol=entry.get("symbol"),
                    validator_check=None,
                )
            )
            continue
        if source_key in prepared_by_source:
            entry = prepared_by_source[source_key]
            validator_check = _validator_check_for_artifact_path(
                artifact_path=entry.get("artifact_path"),
                validator_checks_by_artifact_path=validator_checks_by_artifact_path,
            )
            result_status, result_message = _prepared_artifact_result_status(
                validator_check=validator_check,
                commit_failed=commit_failed,
            )
            results.append(
                _source_report_result_entry(
                    source_path=source_key,
                    status=result_status,
                    message=result_message,
                    artifact_path=entry.get("artifact_path"),
                    artifact_id=entry.get("artifact_id"),
                    source_report_id=entry.get("source_report_id"),
                    strategy_id=entry.get("strategy_id"),
                    candidate_version=entry.get("candidate_version"),
                    symbol=entry.get("symbol"),
                    validator_check=validator_check,
                )
            )
            continue
        results.append(
            _source_report_result_entry(
                source_path=source_key,
                status="UNPROCESSED",
                message="source report was discovered but no generation decision was recorded",
                artifact_path=None,
                artifact_id=None,
                source_report_id=None,
                strategy_id=None,
                candidate_version=None,
                symbol=None,
                validator_check=None,
            )
        )
    return results


def _source_report_evidence_summaries(
    discovered_source_reports: list[str | Path],
) -> list[dict[str, Any]]:
    return [
        _source_report_evidence_summary(
            source_path=source_path,
            source_report=_load_partial_source_report(source_path),
        )
        for source_path in discovered_source_reports
    ]


def _source_report_evidence_summary(
    *,
    source_path: str | Path,
    source_report: StrategyValidationArtifactSourceReport | None,
) -> dict[str, Any]:
    if source_report is None:
        return {
            "source_path": str(source_path),
            "parseable": False,
            "report_id": None,
            "source_report_id": None,
            "strategy_id": None,
            "candidate_version": None,
            "symbol": None,
            "summary_trade_count": None,
            "summary_total_net_pnl": None,
            "summary_max_drawdown": None,
            "summary_win_rate": None,
            "summary_sharpe_ratio": None,
            "has_out_of_sample": False,
            "out_of_sample_count": 0,
            "out_of_sample_window_names": [],
            "walk_forward_count": 0,
            "walk_forward_window_names": [],
            "walk_forward_pass_count": 0,
            "walk_forward_pass_rate": None,
            "has_monte_carlo": False,
            "monte_carlo_survival_rate": None,
            "monte_carlo_method": None,
            "monte_carlo_run_count": None,
            "monte_carlo_seed": None,
            "monte_carlo_perturbation_dimensions": [],
            "monte_carlo_survival_threshold": None,
            "missing_evidence_reason_codes": ["source_report_not_parseable"],
            "complete_anti_overfit_evidence": False,
        }

    out_of_sample = [
        item
        for item in source_report.validation_slices
        if item.kind == "out_of_sample"
    ]
    walk_forward = [
        item
        for item in source_report.validation_slices
        if item.kind == "walk_forward"
    ]
    walk_forward_pass_count = sum(
        1 for item in walk_forward if _validation_slice_passes(item)
    )
    walk_forward_pass_rate = (
        walk_forward_pass_count / len(walk_forward) if walk_forward else None
    )
    monte_carlo_validation = source_report.monte_carlo_validation
    has_monte_carlo = (
        source_report.monte_carlo_survival_rate is not None
        and monte_carlo_validation is not None
    )
    missing_evidence_reason_codes = _source_report_missing_evidence_reason_codes(
        has_out_of_sample=bool(out_of_sample),
        walk_forward_count=len(walk_forward),
        has_monte_carlo=has_monte_carlo,
    )
    return {
        "source_path": str(source_path),
        "parseable": True,
        "report_id": source_report.report_id,
        "source_report_id": source_report.source_report_id or source_report.report_id,
        "strategy_id": source_report.strategy_id,
        "candidate_version": source_report.candidate_version,
        "symbol": source_report.symbol,
        "summary_trade_count": source_report.summary.trade_count,
        "summary_total_net_pnl": source_report.summary.total_net_pnl,
        "summary_max_drawdown": source_report.summary.max_drawdown,
        "summary_win_rate": source_report.summary.win_rate,
        "summary_sharpe_ratio": source_report.summary.sharpe_ratio,
        "has_out_of_sample": bool(out_of_sample),
        "out_of_sample_count": len(out_of_sample),
        "out_of_sample_window_names": [item.name for item in out_of_sample],
        "walk_forward_count": len(walk_forward),
        "walk_forward_window_names": [item.name for item in walk_forward],
        "walk_forward_pass_count": walk_forward_pass_count,
        "walk_forward_pass_rate": walk_forward_pass_rate,
        "has_monte_carlo": has_monte_carlo,
        "monte_carlo_survival_rate": source_report.monte_carlo_survival_rate,
        "monte_carlo_method": (
            monte_carlo_validation.method if monte_carlo_validation is not None else None
        ),
        "monte_carlo_run_count": (
            monte_carlo_validation.run_count
            if monte_carlo_validation is not None
            else None
        ),
        "monte_carlo_seed": (
            monte_carlo_validation.seed if monte_carlo_validation is not None else None
        ),
        "monte_carlo_perturbation_dimensions": (
            list(monte_carlo_validation.perturbation_dimensions)
            if monte_carlo_validation is not None
            else []
        ),
        "monte_carlo_survival_threshold": (
            monte_carlo_validation.survival_threshold
            if monte_carlo_validation is not None
            else None
        ),
        "missing_evidence_reason_codes": missing_evidence_reason_codes,
        "complete_anti_overfit_evidence": not missing_evidence_reason_codes,
    }


def _aggregate_source_report_evidence(
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    missing_reason_code_counts: dict[str, int] = {}
    for summary in summaries:
        for code in summary.get("missing_evidence_reason_codes") or []:
            missing_reason_code_counts[code] = missing_reason_code_counts.get(code, 0) + 1
    return {
        "source_report_count": len(summaries),
        "parseable_source_report_count": _count_truthy(
            summaries,
            key="parseable",
        ),
        "complete_anti_overfit_source_report_count": _count_truthy(
            summaries,
            key="complete_anti_overfit_evidence",
        ),
        "out_of_sample_source_report_count": _count_truthy(
            summaries,
            key="has_out_of_sample",
        ),
        "walk_forward_source_report_count": sum(
            1 for summary in summaries if int(summary.get("walk_forward_count") or 0) > 0
        ),
        "monte_carlo_source_report_count": _count_truthy(
            summaries,
            key="has_monte_carlo",
        ),
        "missing_evidence_reason_code_counts": missing_reason_code_counts,
    }


def _source_report_missing_evidence_reason_codes(
    *,
    has_out_of_sample: bool,
    walk_forward_count: int,
    has_monte_carlo: bool,
) -> list[str]:
    reason_codes = []
    if not has_out_of_sample:
        reason_codes.append("missing_out_of_sample_validation")
    if walk_forward_count < 1:
        reason_codes.append("missing_walk_forward_validation")
    if not has_monte_carlo:
        reason_codes.append("missing_monte_carlo_validation")
    return reason_codes


def _validation_slice_passes(item: Any) -> bool:
    return item.trade_count >= 1 and item.total_net_pnl >= 0.0


def _count_truthy(
    entries: list[dict[str, Any]],
    *,
    key: str,
) -> int:
    return sum(1 for entry in entries if entry.get(key))


def _source_report_result_entry(
    *,
    source_path: str,
    status: str,
    message: str | None,
    artifact_path: str | None,
    artifact_id: str | None,
    source_report_id: str | None,
    strategy_id: str | None,
    candidate_version: str | None,
    symbol: str | None,
    validator_check: dict[str, Any] | None,
) -> dict[str, Any]:
    result = {
        "source_path": source_path,
        "status": status,
        "message": message,
        "artifact_path": artifact_path,
        "artifact_id": artifact_id,
        "source_report_id": source_report_id,
        "strategy_id": strategy_id,
        "candidate_version": candidate_version,
        "symbol": symbol,
    }
    result.update(_validator_result_context(validator_check))
    return result


def _count_result_values(
    results: list[dict[str, Any]],
    *,
    key: str,
    skip_none: bool = False,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in results:
        value = entry.get(key)
        if value is None and skip_none:
            continue
        normalized = "null" if value is None else str(value)
        counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _count_result_reason_codes(
    results: list[dict[str, Any]],
    *,
    key: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in results:
        for code in entry.get(key) or []:
            normalized = str(code)
            if not normalized:
                continue
            counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _file_fingerprints(paths: list[Any]) -> list[dict[str, Any]]:
    fingerprints = []
    for path in paths:
        if path is None:
            continue
        path_obj = Path(path)
        try:
            stat_result = path_obj.stat()
        except OSError:
            fingerprints.append(
                {
                    "path": str(path_obj),
                    "exists": False,
                    "size_bytes": None,
                    "sha256": None,
                }
            )
            continue
        fingerprints.append(
            {
                "path": str(path_obj),
                "exists": True,
                "size_bytes": stat_result.st_size,
                "sha256": _file_sha256(path_obj),
            }
        )
    return fingerprints


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepared_artifact_result_status(
    *,
    validator_check: dict[str, Any] | None,
    commit_failed: bool,
) -> tuple[str, str]:
    if validator_check is not None and validator_check.get("status") == "FAIL":
        return (
            "VALIDATION_FAILED",
            "artifact was staged but not committed because strict validation failed",
        )
    if validator_check is not None and validator_check.get("status") == "PASS":
        if commit_failed:
            return (
                "VALIDATED_NOT_COMMITTED",
                "artifact passed strict validation but was not committed because the batch commit failed",
            )
        return (
            "VALIDATED_NOT_COMMITTED",
            "artifact passed strict validation but was not committed because another prepared artifact failed batch validation",
        )
    return (
        "UNPROCESSED",
        "artifact was staged but no strict validation decision was recorded",
    )


def _validator_result_context(
    validator_check: dict[str, Any] | None,
) -> dict[str, Any]:
    if validator_check is None:
        return {
            "validator_status": None,
            "validator_category": None,
            "validator_message": None,
            "validator_reason_codes": [],
        }
    return {
        "validator_status": validator_check.get("status"),
        "validator_category": validator_check.get("category"),
        "validator_message": validator_check.get("message"),
        "validator_reason_codes": _validator_reason_codes(validator_check),
    }


def _validator_reason_codes(validator_check: dict[str, Any]) -> list[str]:
    reason_codes = list(validator_check.get("missing_evidence_reason_codes") or [])
    promotion_decision = validator_check.get("promotion_decision") or {}
    reason_codes.extend(promotion_decision.get("reason_codes") or [])
    unique_codes = []
    seen = set()
    for code in reason_codes:
        normalized = str(code)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_codes.append(normalized)
    return unique_codes


def _validator_checks_by_artifact_path(
    validator_report: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    checks_by_path: dict[str, dict[str, Any]] = {}
    if validator_report is None:
        return checks_by_path
    for check in validator_report.get("checks", []):
        path = check.get("path")
        if not isinstance(path, str):
            continue
        path_obj = Path(path)
        checks_by_path[str(path_obj)] = check
        try:
            checks_by_path[str(path_obj.resolve())] = check
        except OSError:
            pass
    return checks_by_path


def _validator_check_for_artifact_path(
    *,
    artifact_path: str | None,
    validator_checks_by_artifact_path: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if artifact_path is None:
        return None
    path_obj = Path(artifact_path)
    return validator_checks_by_artifact_path.get(
        str(path_obj.resolve()),
        validator_checks_by_artifact_path.get(str(path_obj)),
    )


def _serializable_prepared_artifacts(
    prepared_artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "source_path": str(entry["source_path"]),
            "artifact_path": str(entry["artifact_path"]),
            "stage_path": str(entry["stage_path"]),
            "artifact_id": entry["artifact_id"],
            "source_report_id": entry["source_report_id"],
            "strategy_id": entry["strategy_id"],
            "candidate_version": entry["candidate_version"],
            "symbol": entry["symbol"],
        }
        for entry in prepared_artifacts
    ]


def _build_rejected_source_error(
    *,
    source_path: str | Path,
    message: str,
    source_report: StrategyValidationArtifactSourceReport | None,
    artifact_store: StrategyValidationArtifactStore,
) -> dict[str, Any]:
    error = {
        "error_type": "source_report_rejected",
        "source_path": str(source_path),
        "message": message,
    }
    error.update(
        _source_report_context(
            source_report=source_report,
            artifact_store=artifact_store,
        )
    )
    return error


def _split_generation_errors(
    errors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_report_errors: list[dict[str, Any]] = []
    pipeline_errors: list[dict[str, Any]] = []
    for entry in errors:
        if entry.get("error_type") == "source_report_rejected":
            source_report_errors.append(entry)
            continue
        pipeline_errors.append(entry)
    return source_report_errors, pipeline_errors


def _load_partial_source_report(
    source_path: str | Path,
) -> StrategyValidationArtifactSourceReport | None:
    try:
        payload = json.loads(Path(source_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return StrategyValidationArtifactSourceReport.from_payload(payload)
    except (TypeError, ValueError):
        return None


def _source_report_context(
    *,
    source_report: StrategyValidationArtifactSourceReport | None,
    artifact_store: StrategyValidationArtifactStore,
) -> dict[str, Any]:
    if source_report is None:
        return {
            "artifact_path": None,
            "source_report_id": None,
            "strategy_id": None,
            "candidate_version": None,
            "symbol": None,
        }

    artifact_path = artifact_store.artifact_path_for_candidate(
        symbol=source_report.symbol,
        strategy_id=source_report.strategy_id,
        candidate_version=source_report.candidate_version,
    )
    return {
        "artifact_path": str(artifact_path),
        "source_report_id": source_report.source_report_id or source_report.report_id,
        "strategy_id": source_report.strategy_id,
        "candidate_version": source_report.candidate_version,
        "symbol": source_report.symbol,
    }


def _commit_prepared_artifacts(
    prepared_artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    committed_paths: list[tuple[Path, bytes | None]] = []
    try:
        for entry in prepared_artifacts:
            artifact_path = Path(entry["artifact_path"])
            stage_path = Path(entry["stage_path"])
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            previous_bytes = artifact_path.read_bytes() if artifact_path.exists() else None
            stage_path.replace(artifact_path)
            committed_paths.append((artifact_path, previous_bytes))
    except BaseException:
        for artifact_path, previous_bytes in reversed(committed_paths):
            if previous_bytes is None:
                try:
                    artifact_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            artifact_path.write_bytes(previous_bytes)
        raise

    return [
        {
            "source_path": entry["source_path"],
            "artifact_path": str(entry["artifact_path"]),
            "artifact_id": entry["artifact_id"],
            "source_report_id": entry["source_report_id"],
            "strategy_id": entry["strategy_id"],
            "candidate_version": entry["candidate_version"],
            "symbol": entry["symbol"],
        }
        for entry in prepared_artifacts
    ]


def _remap_validator_report_paths(
    report: dict[str, Any],
    *,
    path_mapping: dict[str, str],
) -> dict[str, Any]:
    if not path_mapping:
        return report
    remapped_report = dict(report)
    remapped_checks = []
    for check in report.get("checks", []):
        remapped_check = dict(check)
        path = remapped_check.get("path")
        if isinstance(path, str):
            remapped_check["path"] = path_mapping.get(path, path)
        remapped_checks.append(remapped_check)
    remapped_report["checks"] = remapped_checks
    return remapped_report


def _write_report(report: dict[str, Any], output_path: str | Path | None) -> dict[str, Any]:
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def _optional_float(value: str) -> float | None:
    if value.strip().lower() in {"none", "null", ""}:
        return None
    return float(value)


def _optional_positive_float(value: str) -> float | None:
    parsed = _optional_float(value)
    if parsed is not None and parsed < 0.0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate replayable OOS / walk-forward / Monte Carlo strategy validation artifacts "
            "from explicit local source reports."
        )
    )
    parser.add_argument(
        "--source-report",
        action="append",
        default=[],
        help="Path to a local validation source report JSON file.",
    )
    parser.add_argument(
        "--source-report-dir",
        action="append",
        default=[],
        help=(
            "Directory to recursively scan for local validation source report JSON files. "
            "Defaults to logs/strategy-validation-artifacts/source-reports."
        ),
    )
    parser.add_argument(
        "--no-default-source-report-dir",
        action="store_true",
        help="Do not scan the default source report inbox directory.",
    )
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument(
        "--validator-output",
        default=str(DEFAULT_VALIDATOR_OUTPUT_PATH),
        help="Where to write the strict validator report for generated artifacts.",
    )
    parser.add_argument("--no-output", action="store_true")
    parser.add_argument("--no-validator-output", action="store_true")
    parser.add_argument(
        "--overwrite-existing-artifacts",
        action="store_true",
        help=(
            "Allow generated artifacts to replace existing artifact targets. "
            "Default is to reject the source report to preserve replayability."
        ),
    )
    parser.add_argument("--timestamp", type=int)
    parser.add_argument("--require-gate-pass", action="store_true")
    parser.add_argument("--min-trades", type=int, default=1)
    parser.add_argument("--min-net-pnl", type=float, default=0.0)
    parser.add_argument("--max-drawdown", type=_optional_positive_float)
    parser.add_argument("--min-win-rate", type=_optional_positive_float)
    parser.add_argument("--min-out-of-sample-net-pnl", type=float, default=0.0)
    parser.add_argument("--min-walk-forward-windows", type=int, default=1)
    parser.add_argument("--min-walk-forward-pass-rate", type=_optional_float, default=0.0)
    parser.add_argument("--min-monte-carlo-survival-rate", type=_optional_float, default=0.0)
    args = parser.parse_args(argv)
    source_report_dirs = list(args.source_report_dir)
    if not args.no_default_source_report_dir:
        source_report_dirs.insert(0, str(DEFAULT_SOURCE_REPORT_DIR))

    report = run_strategy_validation_artifact_generation(
        source_reports=args.source_report,
        source_report_dirs=source_report_dirs,
        artifact_dir=args.artifact_dir,
        output_path=None if args.no_output else args.output,
        validator_output_path=(
            None if args.no_validator_output else args.validator_output
        ),
        timestamp=args.timestamp,
        require_gate_pass=args.require_gate_pass,
        min_trades=args.min_trades,
        min_net_pnl=args.min_net_pnl,
        max_drawdown=args.max_drawdown,
        min_win_rate=args.min_win_rate,
        min_out_of_sample_net_pnl=args.min_out_of_sample_net_pnl,
        min_walk_forward_windows=args.min_walk_forward_windows,
        min_walk_forward_pass_rate=args.min_walk_forward_pass_rate,
        min_monte_carlo_survival_rate=args.min_monte_carlo_survival_rate,
        overwrite_existing_artifacts=args.overwrite_existing_artifacts,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] == "SKIPPED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
