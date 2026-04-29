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

from quant.optimization import StrategyVersionGate
from quant.schemas import (
    StrategyPromotionAction,
    StrategyValidationArtifact,
    StrategyValidationSliceKind,
    StrategyVersion,
    StrategyVersionStatus,
)


DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "artifacts"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "latest.json"


def run_strategy_validation_artifacts_validation(
    *,
    artifact_dir: str | Path | None = DEFAULT_ARTIFACT_DIR,
    artifact_paths: list[str | Path] | None = None,
    output_path: str | Path | None = DEFAULT_OUTPUT_PATH,
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
) -> dict[str, Any]:
    generated_at = int(time.time()) if timestamp is None else timestamp
    paths = _artifact_paths(artifact_dir=artifact_dir, artifact_paths=artifact_paths)
    if not paths:
        report = _build_report(
            checks=[],
            generated_at=generated_at,
            artifact_dir=artifact_dir,
            require_gate_pass=require_gate_pass,
            required_evidence=_required_evidence(
                require_out_of_sample=True,
                min_walk_forward_windows=min_walk_forward_windows,
                min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
            ),
            status="SKIPPED",
        )
        report["message"] = "no strategy validation artifact JSON files were found"
        return _write_report(report, output_path)

    gate = StrategyVersionGate(
        min_trades=min_trades,
        min_net_pnl=min_net_pnl,
        max_drawdown=max_drawdown,
        min_win_rate=min_win_rate,
        require_out_of_sample=True,
        min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
        min_walk_forward_windows=min_walk_forward_windows,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
    )
    checks = [
        _validate_artifact_path(
            path=path,
            gate=gate,
            generated_at=generated_at,
            require_gate_pass=require_gate_pass,
        )
        for path in paths
    ]
    return _write_report(
        _build_report(
            checks=checks,
            generated_at=generated_at,
            artifact_dir=artifact_dir,
            require_gate_pass=require_gate_pass,
            required_evidence=_required_evidence(
                require_out_of_sample=True,
                min_walk_forward_windows=min_walk_forward_windows,
                min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
            ),
        ),
        output_path,
    )


def _validate_artifact_path(
    *,
    path: Path,
    gate: StrategyVersionGate,
    generated_at: int,
    require_gate_pass: bool,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("source_path", str(path))
        artifact = StrategyValidationArtifact.from_payload(payload)
    except BaseException as exc:
        return {
            "path": str(path),
            "status": "FAIL",
            "category": "schema",
            "message": _safe_error_message(exc),
            "live_orders_sent": False,
            "analytics_modified_live_state": False,
        }

    evidence = _evidence_summary(artifact)
    missing_evidence = _missing_evidence_reason_codes(evidence)
    decision = gate.evaluate(
        candidate=_candidate_from_artifact(artifact),
        metrics=artifact.metrics,
        decision_id=f"validation-artifact-gate:{artifact.artifact_id}",
        generated_at=generated_at,
        trace=artifact.trace,
    )
    gate_rejected = decision.action == StrategyPromotionAction.REJECT
    failed = bool(missing_evidence) or (require_gate_pass and gate_rejected)

    if missing_evidence:
        category = "missing_evidence"
        message = "strategy validation artifact is missing required anti-overfit evidence"
    elif require_gate_pass and gate_rejected:
        category = "promotion_gate"
        message = "strategy validation artifact did not pass the configured promotion gate"
    else:
        category = "ok"
        message = "strategy validation artifact parsed and required evidence is present"

    return {
        "path": str(path),
        "status": "FAIL" if failed else "PASS",
        "category": category,
        "message": message,
        "artifact_id": artifact.artifact_id,
        "source_report_id": artifact.source_report_id,
        "symbol": artifact.symbol,
        "strategy_id": artifact.strategy_id,
        "candidate_version": artifact.candidate_version,
        "metrics_report_id": artifact.metrics.report_id,
        "evidence": evidence,
        "missing_evidence_reason_codes": missing_evidence,
        "promotion_decision": decision.to_payload(),
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
    }


def _build_report(
    *,
    checks: list[dict[str, Any]],
    generated_at: int,
    artifact_dir: str | Path | None,
    require_gate_pass: bool,
    required_evidence: dict[str, Any],
    status: str | None = None,
) -> dict[str, Any]:
    failed = [check for check in checks if check["status"] == "FAIL"]
    resolved_status = status or ("FAIL" if failed else "PASS")
    return {
        "success": resolved_status == "PASS",
        "status": resolved_status,
        "generated_at": generated_at,
        "message": _report_message(resolved_status),
        "artifact_dir": str(artifact_dir) if artifact_dir is not None else None,
        "artifact_count": len(checks),
        "failed_count": len(failed),
        "require_gate_pass": require_gate_pass,
        "required_evidence": required_evidence,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
        "proxy": {
            "SMARTQTF_USE_PROXY": os.getenv("SMARTQTF_USE_PROXY"),
            "required_for_external_artifacts": False,
        },
        "checks": checks,
    }


def _report_message(status: str) -> str:
    if status == "PASS":
        return "strategy validation artifact validation passed"
    if status == "SKIPPED":
        return "strategy validation artifact validation was skipped"
    return "strategy validation artifact validation failed"


def _artifact_paths(
    *,
    artifact_dir: str | Path | None,
    artifact_paths: list[str | Path] | None,
) -> list[Path]:
    paths = [Path(path) for path in artifact_paths or []]
    if artifact_dir is not None:
        root = Path(artifact_dir)
        if root.exists():
            paths.extend(sorted(root.rglob("*.json")))
    unique_paths = []
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(path)
    return unique_paths


def _required_evidence(
    *,
    require_out_of_sample: bool,
    min_walk_forward_windows: int,
    min_monte_carlo_survival_rate: float | None,
) -> dict[str, Any]:
    return {
        "out_of_sample_required": require_out_of_sample,
        "min_walk_forward_windows": min_walk_forward_windows,
        "monte_carlo_required": min_monte_carlo_survival_rate is not None,
    }


def _evidence_summary(artifact: StrategyValidationArtifact) -> dict[str, Any]:
    out_of_sample = [
        item
        for item in artifact.metrics.validation_slices
        if item.kind == StrategyValidationSliceKind.OUT_OF_SAMPLE
    ]
    walk_forward = [
        item
        for item in artifact.metrics.validation_slices
        if item.kind == StrategyValidationSliceKind.WALK_FORWARD
    ]
    return {
        "has_out_of_sample": bool(out_of_sample),
        "out_of_sample_count": len(out_of_sample),
        "walk_forward_count": len(walk_forward),
        "has_monte_carlo": artifact.metrics.monte_carlo_survival_rate is not None,
        "monte_carlo_survival_rate": artifact.metrics.monte_carlo_survival_rate,
    }


def _missing_evidence_reason_codes(evidence: dict[str, Any]) -> list[str]:
    reason_codes = []
    if not evidence["has_out_of_sample"]:
        reason_codes.append("missing_out_of_sample_validation")
    if evidence["walk_forward_count"] < 1:
        reason_codes.append("missing_walk_forward_validation")
    if not evidence["has_monte_carlo"]:
        reason_codes.append("missing_monte_carlo_validation")
    return reason_codes


def _candidate_from_artifact(artifact: StrategyValidationArtifact) -> StrategyVersion:
    return StrategyVersion(
        strategy_id=artifact.strategy_id,
        version=artifact.candidate_version,
        status=StrategyVersionStatus.CANDIDATE,
        created_at=artifact.generated_at,
        code_ref=f"validation-artifact:{artifact.artifact_id}",
        config_hash=f"validation-artifact:{artifact.artifact_id}",
        validation_report_id=artifact.metrics.report_id,
        trace=artifact.trace,
    )


def _safe_error_message(exc: BaseException) -> str:
    return str(exc)


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
        description="Validate replayable OOS / walk-forward / Monte Carlo strategy validation artifacts."
    )
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--no-output", action="store_true")
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

    report = run_strategy_validation_artifacts_validation(
        artifact_dir=args.artifact_dir,
        artifact_paths=args.artifact,
        output_path=None if args.no_output else args.output,
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
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] == "SKIPPED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
