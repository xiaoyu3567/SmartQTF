import json
import re
from pathlib import Path
from typing import Iterable, Optional

from quant.schemas import (
    DailyReviewBucket,
    DailyReviewReport,
    StrategyValidationArtifact,
    StrategyValidationMetrics,
    StrategyVersion,
)


class StrategyValidationArtifactStore:
    """Load replayable external validation artifacts for optimization candidates."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def write_artifact(self, artifact) -> Path:
        artifact = self._artifact(artifact)
        path = self._path(
            symbol=artifact.symbol,
            strategy_id=artifact.strategy_id,
            candidate_version=artifact.candidate_version,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = artifact.to_payload()
        payload.setdefault("source_path", str(path))
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        return path

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
