import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from quant.optimization.validation_artifacts import StrategyValidationArtifactStore
from quant.schemas import (
    MonteCarloValidation,
    StrategyValidationArtifact,
    StrategyValidationMetrics,
    StrategyValidationSlice,
    TraceContext,
)
from quant.schemas.base import SmartQTFModel


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_DIR = (
    PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "artifacts"
)
DEFAULT_SOURCE_REPORT_DIR = (
    PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "source-reports"
)

_SECRET_KEY_PATTERNS = (
    "api_key",
    "apikey",
    "api_secret",
    "secret_key",
    "access_key",
    "access_secret",
    "passphrase",
    "password",
    "private_key",
    "credential",
    "credentials",
    "auth_token",
    "bearer_token",
)
_DANGEROUS_TRUE_FLAGS = {
    "live_orders_sent",
    "live_order_submission",
    "live_deployment_triggered",
    "analytics_modified_live_state",
    "contains_real_credentials",
    "broker_called",
    "exchange_order_submitted",
    "real_order_submitted",
}


class StrategyValidationSourceSummary(SmartQTFModel):
    trade_count: int
    total_net_pnl: float
    max_drawdown: float
    win_rate: float
    sharpe_ratio: Optional[float] = None


class StrategyValidationArtifactSourceReport(SmartQTFModel):
    report_id: str
    strategy_id: str
    candidate_version: str
    symbol: str
    generated_at: int
    summary: StrategyValidationSourceSummary
    validation_slices: list[StrategyValidationSlice]
    monte_carlo_survival_rate: Optional[float] = None
    monte_carlo_validation: Optional[MonteCarloValidation] = None
    artifact_id: Optional[str] = None
    source_report_id: Optional[str] = None
    source_path: Optional[str] = None
    trace: Optional[TraceContext] = None


def load_source_report(path: Path | str) -> StrategyValidationArtifactSourceReport:
    path = Path(path)
    _guard_source_report_path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "metrics" in payload and "artifact_id" in payload:
        raise ValueError(
            "source report must not be a prebuilt StrategyValidationArtifact payload"
        )
    _guard_source_report_payload_safety(payload)
    source_report = StrategyValidationArtifactSourceReport.from_payload(payload)
    _guard_source_report_provenance(source_report)
    return source_report


def discover_source_reports(
    *,
    source_reports: list[str | Path] | None = None,
    source_report_dirs: list[str | Path] | None = None,
) -> list[Path]:
    paths = [Path(path) for path in source_reports or []]
    for source_report_dir in source_report_dirs or []:
        root = Path(source_report_dir)
        if not root.exists():
            continue
        if root.is_file():
            paths.append(root)
            continue
        paths.extend(sorted(root.rglob("*.json")))
    return _dedupe_paths(paths)


def build_strategy_validation_artifact(
    source_report: StrategyValidationArtifactSourceReport,
    *,
    source_path: Path | str | None = None,
) -> StrategyValidationArtifact:
    resolved_source_path = _resolved_source_path(source_report, source_path=source_path)
    metrics = StrategyValidationMetrics(
        report_id=source_report.report_id,
        generated_at=source_report.generated_at,
        trade_count=source_report.summary.trade_count,
        total_net_pnl=source_report.summary.total_net_pnl,
        max_drawdown=source_report.summary.max_drawdown,
        win_rate=source_report.summary.win_rate,
        sharpe_ratio=source_report.summary.sharpe_ratio,
        validation_slices=list(source_report.validation_slices),
        monte_carlo_survival_rate=source_report.monte_carlo_survival_rate,
        monte_carlo_validation=source_report.monte_carlo_validation,
    )
    return StrategyValidationArtifact(
        artifact_id=source_report.artifact_id
        or _default_artifact_id(
            strategy_id=source_report.strategy_id,
            candidate_version=source_report.candidate_version,
            symbol=source_report.symbol,
        ),
        strategy_id=source_report.strategy_id,
        candidate_version=source_report.candidate_version,
        symbol=source_report.symbol,
        generated_at=source_report.generated_at,
        metrics=metrics,
        source_report_id=source_report.source_report_id or source_report.report_id,
        source_path=resolved_source_path,
        trace=source_report.trace,
    )


def write_generated_artifact(
    source_report: StrategyValidationArtifactSourceReport,
    *,
    artifact_store: StrategyValidationArtifactStore | Path | str | None = None,
    source_path: Path | str | None = None,
) -> tuple[StrategyValidationArtifact, Path]:
    active_store = _artifact_store(artifact_store)
    artifact = build_strategy_validation_artifact(
        source_report,
        source_path=source_path,
    )
    artifact_path = active_store.write_artifact(artifact)
    return artifact, artifact_path


def _artifact_store(
    artifact_store: StrategyValidationArtifactStore | Path | str | None,
) -> StrategyValidationArtifactStore:
    if artifact_store is None:
        return StrategyValidationArtifactStore(DEFAULT_ARTIFACT_DIR)
    if isinstance(artifact_store, StrategyValidationArtifactStore):
        return artifact_store
    return StrategyValidationArtifactStore(artifact_store)


def _resolved_source_path(
    source_report: StrategyValidationArtifactSourceReport,
    *,
    source_path: Path | str | None,
) -> str | None:
    if source_path is not None:
        _guard_source_path_reference(source_path)
        return str(Path(source_path))
    if source_report.source_path is not None:
        _guard_source_path_reference(source_report.source_path)
    return source_report.source_path


def _default_artifact_id(
    *,
    strategy_id: str,
    candidate_version: str,
    symbol: str,
) -> str:
    parts = [
        _safe_token(strategy_id),
        _safe_token(candidate_version),
        _safe_token(symbol),
    ]
    return "strategy-validation-artifact:" + ":".join(parts)


def _safe_token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return token or "unknown"


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    unique_paths = []
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(path)
    return unique_paths


def _guard_source_report_path(path: Path) -> None:
    _guard_not_example_path(
        path,
        message=(
            "example fixture inputs under config/examples must not be treated as real validation source reports"
        ),
    )


def _guard_source_report_provenance(
    source_report: StrategyValidationArtifactSourceReport,
) -> None:
    if source_report.source_path is None:
        return
    _guard_source_path_reference(source_report.source_path)


def _guard_source_report_payload_safety(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("source report payload must be a JSON object")

    for path, key, value in _walk_payload(payload):
        normalized_key = _normalize_key(key)
        if normalized_key in _DANGEROUS_TRUE_FLAGS and _is_truthy_side_effect_value(value):
            raise ValueError(
                "source report safety flag must not indicate live/broker side effects: "
                f"{path}"
            )
        if _is_secret_key(normalized_key) and _has_meaningful_secret_value(value):
            raise ValueError(
                "source report must not contain credential-like fields: "
                f"{path}"
            )


def _guard_source_path_reference(path: Path | str) -> None:
    _guard_not_example_path(
        path,
        message=(
            "source report provenance under config/examples must not be treated as real validation evidence"
        ),
    )


def _guard_not_example_path(path: Path | str, *, message: str) -> None:
    resolved = _resolve_project_path(path)
    examples_root = (PROJECT_ROOT / "config" / "examples").resolve()
    if resolved.is_relative_to(examples_root):
        raise ValueError(message)


def _resolve_project_path(path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (PROJECT_ROOT / candidate).resolve()


def _walk_payload(value: Any, *, prefix: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{prefix}.{key}"
            yield child_path, str(key), child
            yield from _walk_payload(child, prefix=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_payload(child, prefix=f"{prefix}[{index}]")


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _is_secret_key(normalized_key: str) -> bool:
    return any(pattern in normalized_key for pattern in _SECRET_KEY_PATTERNS)


def _has_meaningful_secret_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _is_truthy_side_effect_value(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False
