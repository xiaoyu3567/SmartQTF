import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.schemas import (
    StrategyValidationArtifact,
    StrategyValidationMetrics,
    StrategyValidationSlice,
    StrategyValidationSliceKind,
)
from scripts import validate_strategy_validation_artifacts as validate_artifacts


def make_artifact(*, missing_evidence=False, total_net_pnl=24.0):
    slices = []
    monte_carlo_survival_rate = None
    if not missing_evidence:
        slices = [
            StrategyValidationSlice(
                name="oos-2024-q1",
                kind=StrategyValidationSliceKind.OUT_OF_SAMPLE,
                trade_count=8,
                total_net_pnl=12.0,
                max_drawdown=0.8,
                win_rate=0.62,
                sharpe_ratio=1.1,
            ),
            StrategyValidationSlice(
                name="walk-forward-001",
                kind=StrategyValidationSliceKind.WALK_FORWARD,
                trade_count=8,
                total_net_pnl=10.0,
                max_drawdown=0.9,
                win_rate=0.60,
                sharpe_ratio=1.0,
            ),
        ]
        monte_carlo_survival_rate = 0.91

    return StrategyValidationArtifact(
        artifact_id="artifact-001",
        source_report_id="daily-001",
        strategy_id="ma_crossover",
        candidate_version="review-2024-03-09-BTCUSDT-ma_crossover",
        symbol="BTCUSDT",
        generated_at=1710007200,
        metrics=StrategyValidationMetrics(
            report_id="oos-wf-mc-001",
            generated_at=1710007200,
            trade_count=24,
            total_net_pnl=total_net_pnl,
            max_drawdown=1.1,
            win_rate=0.62,
            sharpe_ratio=1.3,
            validation_slices=slices,
            monte_carlo_survival_rate=monte_carlo_survival_rate,
        ),
    )


def write_artifact(path, artifact):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact.to_payload()), encoding="utf-8")
    return path


def test_strategy_validation_artifacts_script_passes_complete_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "BTCUSDT" / "ma_crossover.json",
        make_artifact(),
    )
    output_path = tmp_path / "latest.json"

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=output_path,
        timestamp=1710007300,
    )

    assert report["status"] == "PASS"
    assert report["success"] is True
    assert report["live_orders_sent"] is False
    assert report["analytics_modified_live_state"] is False
    assert report["contains_real_credentials"] is False
    assert report["checks"][0]["evidence"]["has_out_of_sample"] is True
    assert report["checks"][0]["evidence"]["walk_forward_count"] == 1
    assert report["checks"][0]["evidence"]["has_monte_carlo"] is True
    assert output_path.exists()


def test_strategy_validation_artifacts_script_fails_missing_required_evidence(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "incomplete.json",
        make_artifact(missing_evidence=True),
    )

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=None,
        timestamp=1710007300,
    )

    assert report["status"] == "FAIL"
    assert report["checks"][0]["category"] == "missing_evidence"
    assert report["checks"][0]["missing_evidence_reason_codes"] == [
        "missing_out_of_sample_validation",
        "missing_walk_forward_validation",
        "missing_monte_carlo_validation",
    ]


def test_strategy_validation_artifacts_script_can_require_gate_pass(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "negative.json",
        make_artifact(total_net_pnl=-2.0),
    )

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=None,
        timestamp=1710007300,
        require_gate_pass=True,
    )

    assert report["status"] == "FAIL"
    assert report["checks"][0]["category"] == "promotion_gate"
    assert "net_pnl_below_threshold" in report["checks"][0]["promotion_decision"]["reason_codes"]


def test_strategy_validation_artifacts_script_skips_without_artifacts(tmp_path):
    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_dir=tmp_path / "missing",
        artifact_paths=None,
        output_path=None,
        timestamp=1710007300,
    )

    assert report["status"] == "SKIPPED"
    assert report["artifact_count"] == 0
