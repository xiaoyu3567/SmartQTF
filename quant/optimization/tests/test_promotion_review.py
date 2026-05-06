import json

import pytest

from quant.optimization.promotion_review import StrategyPromotionReviewStore
from quant.optimization.tests.test_strategy_validation_artifact_validation_script import (
    make_artifact,
    write_artifact,
)
from scripts import validate_strategy_validation_artifacts as validate_artifacts


def test_promotion_review_detail_marks_gate_passed_candidate_ready(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "BTCUSDT" / "ma_crossover" / "candidate.json",
        make_artifact(),
    )
    validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=tmp_path / "latest.json",
        timestamp=1710007300,
        require_gate_pass=True,
    )
    store = StrategyPromotionReviewStore(tmp_path / "promotion-reviews.jsonl")

    detail = store.build_detail(
        artifact_dir=tmp_path / "artifacts",
        latest_report_path=tmp_path / "latest.json",
    )

    assert detail["review_status"] == "READY_FOR_MANUAL_REVIEW"
    assert detail["manual_review_required"] is True
    assert detail["review_candidates"][0]["approve_enabled"] is True
    assert detail["review_candidates"][0]["reject_enabled"] is True
    assert detail["safety"]["manual_review_dry_run_only"] is True
    assert detail["safety"]["live_deployment_triggered"] is False
    assert detail["safety"]["broker_called"] is False


def test_promotion_review_records_approve_as_dry_run_audit_only(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "BTCUSDT" / "ma_crossover" / "candidate.json",
        make_artifact(),
    )
    validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=tmp_path / "latest.json",
        timestamp=1710007300,
        require_gate_pass=True,
    )
    store = StrategyPromotionReviewStore(
        tmp_path / "promotion-reviews.jsonl",
        clock=lambda: 1710007400,
    )

    result = store.record_decision(
        action="approve",
        artifact_id="artifact-001",
        reviewer_note="gate evidence reviewed",
        reviewer="qa",
        dry_run=True,
        manual_review=True,
        artifact_dir=tmp_path / "artifacts",
        latest_report_path=tmp_path / "latest.json",
    )

    persisted = [
        json.loads(line)
        for line in (tmp_path / "promotion-reviews.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert result["status"] == "RECORDED"
    assert result["record"]["manual_decision"] == "approve"
    assert result["record"]["dry_run"] is True
    assert result["record"]["live_deployment_triggered"] is False
    assert result["safety"]["live_orders_sent"] is False
    assert result["safety"]["broker_called"] is False
    assert persisted[0]["review_id"] == result["record"]["review_id"]
    assert persisted[0]["reviewer_note"] == "gate evidence reviewed"
    assert result["optimization"]["review_status"] == "APPROVED_DRY_RUN"


def test_promotion_review_requires_explicit_dry_run_manual_flag(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "BTCUSDT" / "ma_crossover" / "candidate.json",
        make_artifact(),
    )
    validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=tmp_path / "latest.json",
        timestamp=1710007300,
        require_gate_pass=True,
    )
    store = StrategyPromotionReviewStore(tmp_path / "promotion-reviews.jsonl")

    with pytest.raises(ValueError, match="dry_run=true and manual_review=true"):
        store.record_decision(
            action="approve",
            artifact_id="artifact-001",
            reviewer_note="missing flag",
            dry_run=True,
            manual_review=False,
            artifact_dir=tmp_path / "artifacts",
            latest_report_path=tmp_path / "latest.json",
        )


def test_promotion_review_rejects_approval_when_gate_failed(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "BTCUSDT" / "ma_crossover" / "candidate.json",
        make_artifact(total_net_pnl=-2.0),
    )
    validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=tmp_path / "latest.json",
        timestamp=1710007300,
        require_gate_pass=True,
    )
    store = StrategyPromotionReviewStore(tmp_path / "promotion-reviews.jsonl")

    detail = store.build_detail(
        artifact_dir=tmp_path / "artifacts",
        latest_report_path=tmp_path / "latest.json",
    )
    assert detail["review_candidates"][0]["approve_enabled"] is False

    with pytest.raises(ValueError, match="approval is disabled"):
        store.record_decision(
            action="approve",
            artifact_id="artifact-001",
            reviewer_note="should not approve",
            dry_run=True,
            manual_review=True,
            artifact_dir=tmp_path / "artifacts",
            latest_report_path=tmp_path / "latest.json",
        )
