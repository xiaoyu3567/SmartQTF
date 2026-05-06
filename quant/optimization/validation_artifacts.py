import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from quant.schemas import (
    DailyReviewBucket,
    DailyReviewReport,
    StrategyValidationArtifact,
    StrategyValidationMetrics,
    StrategyVersion,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRATEGY_VALIDATION_ARTIFACT_DIR = (
    PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "artifacts"
)
DEFAULT_STRATEGY_VALIDATION_LATEST_REPORT_PATH = (
    PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "latest.json"
)


class StrategyValidationArtifactStore:
    """Load replayable external validation artifacts for optimization candidates."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def write_artifact(self, artifact) -> Path:
        artifact = self._artifact(artifact)
        path = self.artifact_path_for(artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = artifact.to_payload()
        payload.setdefault("source_path", str(path))
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def artifact_path_for(self, artifact) -> Path:
        artifact = self._artifact(artifact)
        return self._path(
            symbol=artifact.symbol,
            strategy_id=artifact.strategy_id,
            candidate_version=artifact.candidate_version,
        )

    def artifact_path_for_candidate(
        self,
        *,
        symbol: str,
        strategy_id: str,
        candidate_version: str,
    ) -> Path:
        return self._path(
            symbol=symbol,
            strategy_id=strategy_id,
            candidate_version=candidate_version,
        )

    def load_artifact(self, path: Path | str) -> StrategyValidationArtifact:
        path = Path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("source_path", str(path))
        return StrategyValidationArtifact.from_payload(payload)

    def load_for_candidate(
        self,
        *,
        symbol: str,
        strategy_id: str,
        candidate_version: str,
        source_report_id: Optional[str] = None,
    ) -> StrategyValidationArtifact:
        for path in self._candidate_paths(symbol, strategy_id, candidate_version):
            if not path.exists():
                continue
            artifact = self.load_artifact(path)
            if self._matches(
                artifact,
                symbol=symbol,
                strategy_id=strategy_id,
                candidate_version=candidate_version,
                source_report_id=source_report_id,
            ):
                return artifact

        for path in self._search_paths():
            try:
                artifact = self.load_artifact(path)
            except (OSError, ValueError, TypeError):
                continue
            if self._matches(
                artifact,
                symbol=symbol,
                strategy_id=strategy_id,
                candidate_version=candidate_version,
                source_report_id=source_report_id,
            ):
                return artifact

        raise FileNotFoundError(
            "missing strategy validation artifact for "
            f"{symbol}/{strategy_id}/{candidate_version}"
        )

    def metrics_for_candidate(
        self,
        report: DailyReviewReport,
        symbol_bucket: DailyReviewBucket,
        strategy_bucket: DailyReviewBucket,
        candidate: StrategyVersion,
    ) -> StrategyValidationMetrics:
        artifact = self.load_for_candidate(
            symbol=symbol_bucket.bucket_value,
            strategy_id=strategy_bucket.bucket_value,
            candidate_version=candidate.version,
            source_report_id=report.report_id,
        )
        return artifact.metrics

    def artifact_summaries(
        self,
        *,
        latest_report: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        check_by_path = _checks_by_path(latest_report)
        summaries = []
        for path in self._search_paths():
            check = check_by_path.get(str(path)) or check_by_path.get(str(path.resolve()))
            try:
                artifact = self.load_artifact(path)
            except (OSError, ValueError, TypeError) as exc:
                summaries.append(
                    {
                        "path": str(path),
                        "status": "FAIL",
                        "category": "schema",
                        "message": str(exc),
                        "symbol": None,
                        "strategy_id": None,
                        "candidate_version": None,
                        "generated_at": None,
                        "artifact_id": None,
                        "source_report_id": None,
                        "evidence": {},
                        "reason_codes": ["invalid_validation_artifact"],
                    }
                )
                continue
            summaries.append(_artifact_summary(artifact, path=path, check=check))
        return summaries

    def _candidate_paths(
        self,
        symbol: str,
        strategy_id: str,
        candidate_version: str,
    ) -> Iterable[Path]:
        yield self._path(
            symbol=symbol,
            strategy_id=strategy_id,
            candidate_version=candidate_version,
        )

    def _path(
        self,
        *,
        symbol: str,
        strategy_id: str,
        candidate_version: str,
    ) -> Path:
        return (
            self.root
            / self._safe_token(symbol)
            / self._safe_token(strategy_id)
            / f"{self._safe_token(candidate_version)}.json"
        )

    def _search_paths(self):
        if not self.root.exists():
            return []
        return sorted(self.root.rglob("*.json"))

    def _matches(
        self,
        artifact: StrategyValidationArtifact,
        *,
        symbol: str,
        strategy_id: str,
        candidate_version: str,
        source_report_id: Optional[str],
    ) -> bool:
        if artifact.symbol != symbol:
            return False
        if artifact.strategy_id != strategy_id:
            return False
        if artifact.candidate_version != candidate_version:
            return False
        if (
            source_report_id is not None
            and artifact.source_report_id
            and artifact.source_report_id != source_report_id
        ):
            return False
        return True

    def _artifact(self, artifact):
        if isinstance(artifact, StrategyValidationArtifact):
            return artifact
        return StrategyValidationArtifact.from_payload(artifact)

    def _safe_token(self, value) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
        return token or "unknown"


def build_strategy_validation_index(
    *,
    artifact_dir: Path | str | None = DEFAULT_STRATEGY_VALIDATION_ARTIFACT_DIR,
    latest_report_path: Path | str | None = DEFAULT_STRATEGY_VALIDATION_LATEST_REPORT_PATH,
    latest_report: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a read-only Worker/Web index for strategy validation artifacts."""

    artifact_dir = artifact_dir or DEFAULT_STRATEGY_VALIDATION_ARTIFACT_DIR
    latest_report_path = latest_report_path or DEFAULT_STRATEGY_VALIDATION_LATEST_REPORT_PATH
    resolved_latest_report = latest_report
    if resolved_latest_report is None:
        resolved_latest_report = load_latest_validation_report(latest_report_path)

    store = StrategyValidationArtifactStore(
        artifact_dir if artifact_dir is not None else DEFAULT_STRATEGY_VALIDATION_ARTIFACT_DIR
    )
    artifact_summaries = store.artifact_summaries(latest_report=resolved_latest_report)
    latest_report_found = resolved_latest_report is not None
    if resolved_latest_report is None:
        resolved_latest_report = _empty_latest_report(
            artifact_dir=artifact_dir,
            latest_report_path=latest_report_path,
            artifact_count=len(artifact_summaries),
        )
    status = _validation_status(resolved_latest_report, artifact_summaries)
    review_status = _review_status(status, resolved_latest_report, artifact_summaries)
    reason_codes = _aggregate_reason_codes(resolved_latest_report, artifact_summaries)

    return {
        "available": bool(artifact_summaries),
        "reason": None if artifact_summaries else "strategy_validation_artifacts_skipped",
        "status": status,
        "review_status": review_status,
        "artifact_dir": str(artifact_dir) if artifact_dir is not None else None,
        "latest_report_path": str(latest_report_path) if latest_report_path is not None else None,
        "latest_report_found": latest_report_found,
        "latest_report": resolved_latest_report,
        "artifact_count": len(artifact_summaries),
        "failed_count": _failed_count(resolved_latest_report, artifact_summaries),
        "artifact_summaries": artifact_summaries,
        "evidence_summary": _aggregate_evidence(resolved_latest_report, artifact_summaries),
        "reason_codes": reason_codes,
        "safety": {
            "live_orders_sent": False,
            "analytics_modified_live_state": False,
            "contains_real_credentials": False,
            "network_used": False,
            "broker_called": False,
        },
    }


def load_latest_validation_report(path: Path | str | None) -> Optional[dict[str, Any]]:
    if path is None:
        return None
    report_path = Path(path)
    if not report_path.exists():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {
            "status": "FAIL",
            "success": False,
            "message": "latest validation report could not be parsed",
            "artifact_count": 0,
            "failed_count": 1,
            "checks": [],
            "source_path": str(report_path),
            "live_orders_sent": False,
            "analytics_modified_live_state": False,
            "contains_real_credentials": False,
        }
    payload.setdefault("source_path", str(report_path))
    return payload


def _empty_latest_report(
    *,
    artifact_dir: Path | str | None,
    latest_report_path: Path | str | None,
    artifact_count: int,
) -> dict[str, Any]:
    status = "SKIPPED" if artifact_count == 0 else "UNKNOWN"
    return {
        "success": False,
        "status": status,
        "generated_at": None,
        "message": (
            "no strategy validation latest report was found"
            if artifact_count == 0
            else "strategy validation artifacts exist without a latest validation report"
        ),
        "artifact_dir": str(artifact_dir) if artifact_dir is not None else None,
        "source_path": str(latest_report_path) if latest_report_path is not None else None,
        "artifact_count": artifact_count,
        "failed_count": 0,
        "require_gate_pass": False,
        "required_evidence": {
            "out_of_sample_required": True,
            "min_walk_forward_windows": 1,
            "min_walk_forward_pass_rate": 0.0,
            "monte_carlo_required": True,
            "min_monte_carlo_survival_rate": 0.0,
            "source_provenance_required": True,
        },
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
        "checks": [],
    }


def _artifact_summary(
    artifact: StrategyValidationArtifact,
    *,
    path: Path,
    check: Optional[dict[str, Any]],
) -> dict[str, Any]:
    promotion_decision = (check or {}).get("promotion_decision") or {}
    reason_codes = list((check or {}).get("missing_evidence_reason_codes") or [])
    reason_codes.extend(promotion_decision.get("reason_codes") or [])
    return {
        "path": str(path),
        "status": (check or {}).get("status", "UNKNOWN"),
        "category": (check or {}).get("category"),
        "message": (check or {}).get("message"),
        "artifact_id": artifact.artifact_id,
        "source_report_id": artifact.source_report_id,
        "symbol": artifact.symbol,
        "strategy_id": artifact.strategy_id,
        "candidate_version": artifact.candidate_version,
        "generated_at": artifact.generated_at,
        "metrics_report_id": artifact.metrics.report_id,
        "trade_count": artifact.metrics.trade_count,
        "total_net_pnl": artifact.metrics.total_net_pnl,
        "max_drawdown": artifact.metrics.max_drawdown,
        "win_rate": artifact.metrics.win_rate,
        "sharpe_ratio": artifact.metrics.sharpe_ratio,
        "evidence": (check or {}).get("evidence") or _artifact_evidence(artifact),
        "promotion_decision": promotion_decision or None,
        "reason_codes": sorted(set(str(code) for code in reason_codes if code)),
    }


def _artifact_evidence(artifact: StrategyValidationArtifact) -> dict[str, Any]:
    slices = list(artifact.metrics.validation_slices or [])
    out_of_sample = [
        item
        for item in slices
        if item.kind == "out_of_sample" or getattr(item.kind, "value", None) == "out_of_sample"
    ]
    walk_forward = [
        item
        for item in slices
        if item.kind == "walk_forward" or getattr(item.kind, "value", None) == "walk_forward"
    ]
    monte_carlo_validation = artifact.metrics.monte_carlo_validation
    return {
        "has_out_of_sample": bool(out_of_sample),
        "out_of_sample_count": len(out_of_sample),
        "walk_forward_count": len(walk_forward),
        "walk_forward_window_names": [item.name for item in walk_forward],
        "has_monte_carlo": (
            artifact.metrics.monte_carlo_survival_rate is not None
            and monte_carlo_validation is not None
        ),
        "monte_carlo_survival_rate": artifact.metrics.monte_carlo_survival_rate,
    }


def _checks_by_path(latest_report: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    checks = {}
    for check in (latest_report or {}).get("checks") or []:
        path = check.get("path")
        if not path:
            continue
        checks[str(path)] = check
        try:
            checks[str(Path(path).resolve())] = check
        except OSError:
            pass
    return checks


def _validation_status(
    latest_report: Optional[dict[str, Any]],
    artifact_summaries: list[dict[str, Any]],
) -> str:
    if latest_report and latest_report.get("status"):
        return str(latest_report["status"])
    if not artifact_summaries:
        return "SKIPPED"
    if any(item.get("status") == "FAIL" for item in artifact_summaries):
        return "FAIL"
    return "UNKNOWN"


def _review_status(
    status: str,
    latest_report: Optional[dict[str, Any]],
    artifact_summaries: list[dict[str, Any]],
) -> str:
    if status == "SKIPPED" or not artifact_summaries:
        return "SKIPPED"
    if status == "FAIL":
        return "FAIL"
    if status == "PASS" and (latest_report or {}).get("require_gate_pass") is True:
        return "READY_FOR_REVIEW"
    if status == "PASS":
        return "PASS"
    return status


def _failed_count(
    latest_report: Optional[dict[str, Any]],
    artifact_summaries: list[dict[str, Any]],
) -> int:
    if latest_report and "failed_count" in latest_report:
        return int(latest_report.get("failed_count") or 0)
    return sum(1 for item in artifact_summaries if item.get("status") == "FAIL")


def _aggregate_evidence(
    latest_report: Optional[dict[str, Any]],
    artifact_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    checks = list((latest_report or {}).get("checks") or [])
    evidence_items = [check.get("evidence") or {} for check in checks]
    if not evidence_items:
        evidence_items = [item.get("evidence") or {} for item in artifact_summaries]
    return {
        "has_out_of_sample": any(item.get("has_out_of_sample") for item in evidence_items),
        "out_of_sample_count": sum(int(item.get("out_of_sample_count") or 0) for item in evidence_items),
        "walk_forward_count": sum(int(item.get("walk_forward_count") or 0) for item in evidence_items),
        "walk_forward_pass_count": sum(int(item.get("walk_forward_pass_count") or 0) for item in evidence_items),
        "has_monte_carlo": any(item.get("has_monte_carlo") for item in evidence_items),
        "monte_carlo_survival_rate_min": _min_present(
            item.get("monte_carlo_survival_rate") for item in evidence_items
        ),
        "required_evidence": (latest_report or {}).get("required_evidence") or {},
    }


def _aggregate_reason_codes(
    latest_report: Optional[dict[str, Any]],
    artifact_summaries: list[dict[str, Any]],
) -> list[str]:
    codes = []
    for check in (latest_report or {}).get("checks") or []:
        codes.extend(check.get("missing_evidence_reason_codes") or [])
        decision = check.get("promotion_decision") or {}
        codes.extend(decision.get("reason_codes") or [])
    for item in artifact_summaries:
        codes.extend(item.get("reason_codes") or [])
    if not codes and _validation_status(latest_report, artifact_summaries) == "SKIPPED":
        codes.append("missing_strategy_validation_artifacts")
    return sorted(set(str(code) for code in codes if code))


def _min_present(values: Iterable[Any]) -> Optional[float]:
    numbers = [float(value) for value in values if value is not None]
    return min(numbers) if numbers else None
