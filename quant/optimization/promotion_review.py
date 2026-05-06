import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from quant.optimization.validation_artifacts import build_strategy_validation_index
from quant.schemas import (
    StrategyPromotionAction,
    StrategyPromotionReviewRecord,
    StrategyPromotionReviewStatus,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMOTION_REVIEW_LOG_PATH = (
    PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "promotion-reviews.jsonl"
)


class StrategyPromotionReviewStore:
    """Append-only dry-run audit log for manual strategy promotion review."""

    def __init__(
        self,
        path: Path | str = DEFAULT_PROMOTION_REVIEW_LOG_PATH,
        *,
        clock=None,
    ):
        self.path = Path(path)
        self.clock = clock or time.time

    def append(self, record) -> Path:
        record = self._record(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_payload(), sort_keys=True) + "\n")
        return self.path

    def records(self, *, limit: Optional[int] = None) -> list[StrategyPromotionReviewRecord]:
        if not self.path.exists():
            return []
        records = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                records.append(StrategyPromotionReviewRecord.from_payload(json.loads(line)))
        if limit is None:
            return records
        return records[-max(0, int(limit)) :]

    def latest_by_candidate(self) -> dict[tuple[str, str, Optional[str]], StrategyPromotionReviewRecord]:
        latest = {}
        for record in self.records():
            latest[(record.strategy_id, record.candidate_version, record.symbol)] = record
        return latest

    def build_detail(
        self,
        *,
        artifact_dir=None,
        latest_report_path=None,
        latest_report: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        index = build_strategy_validation_index(
            artifact_dir=artifact_dir,
            latest_report_path=latest_report_path,
            latest_report=latest_report,
        )
        latest_reviews = self.latest_by_candidate()
        candidates = []
        for artifact in index.get("artifact_summaries") or []:
            key = (
                artifact.get("strategy_id"),
                artifact.get("candidate_version"),
                artifact.get("symbol"),
            )
            latest_record = latest_reviews.get(key)
            candidates.append(_review_candidate(artifact, latest_record=latest_record))

        manual_reviews = [record.to_payload() for record in self.records(limit=20)]
        detail = {
            **index,
            "review_status": _detail_review_status(index, candidates),
            "review_log_path": str(self.path),
            "manual_review_required": index.get("review_status")
            in {"READY_FOR_REVIEW", "READY_FOR_MANUAL_REVIEW"},
            "manual_reviews": manual_reviews,
            "review_candidates": candidates,
            "safety": {
                **dict(index.get("safety") or {}),
                "manual_review_dry_run_only": True,
                "live_deployment_triggered": False,
                "broker_called": False,
                "live_orders_sent": False,
            },
        }
        return detail

    def record_decision(
        self,
        *,
        action: StrategyPromotionAction | str,
        artifact_id: str,
        reviewer_note: str,
        reviewer: Optional[str] = None,
        dry_run: bool = True,
        manual_review: bool = False,
        artifact_dir=None,
        latest_report_path=None,
        latest_report: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if not dry_run or not manual_review:
            raise ValueError("promotion review writes require dry_run=true and manual_review=true")
        action = StrategyPromotionAction(action)
        detail = self.build_detail(
            artifact_dir=artifact_dir,
            latest_report_path=latest_report_path,
            latest_report=latest_report,
        )
        candidate = _find_review_candidate(detail, artifact_id)
        if action == StrategyPromotionAction.APPROVE and not candidate.get("approve_enabled"):
            raise ValueError("approval is disabled until validation artifacts pass the promotion gate")

        reviewed_at = int(self.clock())
        record = StrategyPromotionReviewRecord(
            review_id=_review_id(
                action=action,
                artifact_id=artifact_id,
                reviewed_at=reviewed_at,
            ),
            strategy_id=str(candidate["strategy_id"]),
            candidate_version=str(candidate["candidate_version"]),
            symbol=candidate.get("symbol"),
            artifact_ids=[artifact_id],
            artifact_paths=[str(path) for path in [candidate.get("path")] if path],
            candidate={
                "strategy_id": candidate.get("strategy_id"),
                "candidate_version": candidate.get("candidate_version"),
                "symbol": candidate.get("symbol"),
            },
            gate_decision=dict(candidate.get("gate_decision") or {}),
            manual_decision=action,
            reviewer_note=reviewer_note or "",
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            reason_codes=list(candidate.get("reason_codes") or []),
            evidence=dict(candidate.get("evidence") or {}),
            safety_flags=_safety_flags(),
            dry_run=True,
            live_deployment_triggered=False,
        )
        self.append(record)
        updated_detail = self.build_detail(
            artifact_dir=artifact_dir,
            latest_report_path=latest_report_path,
            latest_report=latest_report,
        )
        return {
            "ok": True,
            "status": "RECORDED",
            "action": action.value,
            "record": record.to_payload(),
            "review_log_path": str(self.path),
            "optimization": updated_detail,
            "safety": _safety_flags(),
        }

    def _record(self, record):
        if isinstance(record, StrategyPromotionReviewRecord):
            return record
        return StrategyPromotionReviewRecord.from_payload(record)


def _review_candidate(
    artifact: dict[str, Any],
    *,
    latest_record: Optional[StrategyPromotionReviewRecord],
) -> dict[str, Any]:
    status = artifact.get("status") or "UNKNOWN"
    promotion_decision = dict(artifact.get("promotion_decision") or {})
    gate_action = promotion_decision.get("action")
    gate_passed = status == "PASS" and gate_action == StrategyPromotionAction.APPROVE.value
    latest_payload = latest_record.to_payload() if latest_record is not None else None
    manual_status = None
    approve_enabled = gate_passed
    if latest_record is not None:
        manual_status = (
            StrategyPromotionReviewStatus.APPROVED_DRY_RUN.value
            if latest_record.manual_decision == StrategyPromotionAction.APPROVE
            else StrategyPromotionReviewStatus.REJECTED_DRY_RUN.value
        )
        approve_enabled = gate_passed and latest_record.manual_decision != StrategyPromotionAction.REJECT
    return {
        "artifact_id": artifact.get("artifact_id"),
        "path": artifact.get("path"),
        "strategy_id": artifact.get("strategy_id"),
        "candidate_version": artifact.get("candidate_version"),
        "symbol": artifact.get("symbol"),
        "status": status,
        "review_status": manual_status
        or (
            StrategyPromotionReviewStatus.READY_FOR_MANUAL_REVIEW.value
            if gate_passed
            else StrategyPromotionReviewStatus.FAIL.value
        ),
        "approve_enabled": approve_enabled,
        "reject_enabled": bool(artifact.get("artifact_id")),
        "gate_decision": promotion_decision,
        "evidence": artifact.get("evidence") or {},
        "reason_codes": list(artifact.get("reason_codes") or []),
        "latest_manual_review": latest_payload,
    }


def _detail_review_status(index: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return str(index.get("review_status") or StrategyPromotionReviewStatus.SKIPPED.value)
    if any(
        candidate.get("review_status") == StrategyPromotionReviewStatus.APPROVED_DRY_RUN.value
        for candidate in candidates
    ):
        return StrategyPromotionReviewStatus.APPROVED_DRY_RUN.value
    if any(
        candidate.get("review_status") == StrategyPromotionReviewStatus.READY_FOR_MANUAL_REVIEW.value
        for candidate in candidates
    ):
        return StrategyPromotionReviewStatus.READY_FOR_MANUAL_REVIEW.value
    if any(candidate.get("status") == "FAIL" for candidate in candidates):
        return StrategyPromotionReviewStatus.FAIL.value
    return str(index.get("review_status") or StrategyPromotionReviewStatus.PASS.value)


def _find_review_candidate(detail: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    for candidate in detail.get("review_candidates") or []:
        if candidate.get("artifact_id") == artifact_id:
            return candidate
    raise ValueError(f"unknown strategy validation artifact_id: {artifact_id}")


def _review_id(
    *,
    action: StrategyPromotionAction,
    artifact_id: str,
    reviewed_at: int,
) -> str:
    return "promotion-review:" + ":".join(
        [
            _safe_token(artifact_id),
            _safe_token(action.value),
            str(reviewed_at),
        ]
    )


def _safe_token(value) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return token or "unknown"


def _safety_flags() -> dict[str, bool]:
    return {
        "dry_run": True,
        "manual_review_required": True,
        "live_deployment_triggered": False,
        "live_orders_sent": False,
        "broker_called": False,
        "network_used": False,
        "contains_real_credentials": False,
    }
