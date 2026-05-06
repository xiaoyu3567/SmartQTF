import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.schemas import (
    MonteCarloSimulationMethod,
    MonteCarloValidation,
    StrategyValidationArtifact,
    StrategyValidationMetrics,
    StrategyValidationSlice,
    StrategyValidationSliceKind,
)
from quant.optimization.validation_artifacts import (
    build_strategy_validation_index,
)
from scripts import validate_strategy_validation_artifacts as validate_artifacts


def make_slice(name, kind, **overrides):
    values = {
        "name": name,
        "kind": kind,
        "trade_count": 8,
        "total_net_pnl": 10.0,
        "max_drawdown": 0.9,
        "win_rate": 0.60,
        "sharpe_ratio": 1.0,
    }
    values.update(overrides)
    return StrategyValidationSlice(**values)


def make_artifact(
    *,
    missing_evidence=False,
    total_net_pnl=24.0,
    validation_slices=None,
    monte_carlo_survival_rate=None,
):
    slices = []
    if not missing_evidence:
        slices = validation_slices or [
            make_slice(
                "oos-2024-q1",
                StrategyValidationSliceKind.OUT_OF_SAMPLE,
                total_net_pnl=12.0,
                max_drawdown=0.8,
                win_rate=0.62,
                sharpe_ratio=1.1,
            ),
            make_slice("walk-forward-001", StrategyValidationSliceKind.WALK_FORWARD),
        ]
        if monte_carlo_survival_rate is None:
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
            monte_carlo_validation=(
                None
                if monte_carlo_survival_rate is None
                else MonteCarloValidation(
                    method=MonteCarloSimulationMethod.HYBRID,
                    run_count=500,
                    perturbation_dimensions=[
                        "trade_order_shuffle",
                        "return_perturbation",
                        "slippage_fee_perturbation",
                    ],
                    seed=42,
                    survival_threshold=0.8,
                )
            ),
        ),
    )


def make_artifact_without_provenance():
    artifact = make_artifact()
    payload = artifact.to_payload()
    payload.pop("source_report_id", None)
    payload.pop("source_path", None)
    return StrategyValidationArtifact.from_payload(payload)


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
    assert report["checks"][0]["evidence"]["walk_forward_window_names"] == [
        "walk-forward-001"
    ]
    assert report["checks"][0]["evidence"]["walk_forward_pass_count"] == 1
    assert report["checks"][0]["evidence"]["walk_forward_pass_rate"] == 1.0
    assert report["checks"][0]["evidence"]["has_monte_carlo"] is True
    assert report["checks"][0]["evidence"]["monte_carlo_method"] == "hybrid"
    assert report["checks"][0]["evidence"]["monte_carlo_run_count"] == 500
    assert report["checks"][0]["evidence"]["monte_carlo_seed"] == 42
    assert report["checks"][0]["evidence"]["monte_carlo_perturbation_dimensions"] == [
        "trade_order_shuffle",
        "return_perturbation",
        "slippage_fee_perturbation",
    ]
    assert report["checks"][0]["evidence"]["monte_carlo_survival_threshold"] == 0.8
    assert report["required_evidence"]["min_walk_forward_pass_rate"] == 0.0
    assert report["required_evidence"]["min_monte_carlo_survival_rate"] == 0.0
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


def test_strategy_validation_artifacts_script_rejects_insufficient_walk_forward_windows(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "insufficient-wf.json",
        make_artifact(),
    )

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=None,
        timestamp=1710007300,
        require_gate_pass=True,
        min_walk_forward_windows=2,
        min_walk_forward_pass_rate=0.5,
    )

    assert report["status"] == "FAIL"
    assert report["checks"][0]["category"] == "promotion_gate"
    assert "insufficient_walk_forward_windows" in report["checks"][0]["promotion_decision"]["reason_codes"]


def test_strategy_validation_artifacts_script_rejects_walk_forward_pass_rate_below_threshold(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "wf-pass-rate-low.json",
        make_artifact(
            validation_slices=[
                make_slice(
                    "oos-2024-q1",
                    StrategyValidationSliceKind.OUT_OF_SAMPLE,
                    total_net_pnl=12.0,
                    max_drawdown=0.8,
                    win_rate=0.62,
                    sharpe_ratio=1.1,
                ),
                make_slice("wf-2024-01", StrategyValidationSliceKind.WALK_FORWARD),
                make_slice("wf-2024-02", StrategyValidationSliceKind.WALK_FORWARD),
                make_slice(
                    "wf-2024-03",
                    StrategyValidationSliceKind.WALK_FORWARD,
                    total_net_pnl=-1.0,
                ),
            ],
            monte_carlo_survival_rate=0.91,
        ),
    )

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=None,
        timestamp=1710007300,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.8,
    )

    assert report["status"] == "FAIL"
    assert report["checks"][0]["category"] == "promotion_gate"
    assert report["checks"][0]["evidence"]["walk_forward_count"] == 3
    assert report["checks"][0]["evidence"]["walk_forward_pass_count"] == 2
    assert report["checks"][0]["evidence"]["walk_forward_pass_rate"] == 2 / 3
    assert "walk_forward_pass_rate_below_threshold" in report["checks"][0]["promotion_decision"]["reason_codes"]


def test_strategy_validation_artifacts_script_passes_multiple_walk_forward_windows(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "wf-pass.json",
        make_artifact(
            validation_slices=[
                make_slice(
                    "oos-2024-q1",
                    StrategyValidationSliceKind.OUT_OF_SAMPLE,
                    total_net_pnl=12.0,
                    max_drawdown=0.8,
                    win_rate=0.62,
                    sharpe_ratio=1.1,
                ),
                make_slice("wf-2024-01", StrategyValidationSliceKind.WALK_FORWARD),
                make_slice("wf-2024-02", StrategyValidationSliceKind.WALK_FORWARD),
                make_slice("wf-2024-03", StrategyValidationSliceKind.WALK_FORWARD),
            ],
            monte_carlo_survival_rate=0.91,
        ),
    )

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=None,
        timestamp=1710007300,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
    )

    assert report["status"] == "PASS"
    assert report["checks"][0]["evidence"]["walk_forward_window_names"] == [
        "wf-2024-01",
        "wf-2024-02",
        "wf-2024-03",
    ]
    assert report["checks"][0]["evidence"]["walk_forward_pass_count"] == 3
    assert report["checks"][0]["evidence"]["walk_forward_pass_rate"] == 1.0


def test_strategy_validation_artifacts_script_rejects_missing_provenance(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "no-provenance.json",
        make_artifact_without_provenance(),
    )

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=None,
        timestamp=1710007300,
    )

    assert report["status"] == "FAIL"
    assert report["checks"][0]["category"] == "missing_evidence"
    assert "missing_source_provenance" in report["checks"][0]["missing_evidence_reason_codes"]


def test_strategy_validation_artifacts_script_rejects_missing_monte_carlo_metadata(tmp_path):
    artifact = make_artifact()
    payload = artifact.to_payload()
    payload["metrics"].pop("monte_carlo_validation", None)
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "missing-mc-metadata.json",
        StrategyValidationArtifact.from_payload(payload),
    )

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=None,
        timestamp=1710007300,
    )

    assert report["status"] == "FAIL"
    assert report["checks"][0]["category"] == "missing_evidence"
    assert "missing_monte_carlo_validation" in report["checks"][0]["missing_evidence_reason_codes"]


def test_strategy_validation_artifacts_script_rejects_monte_carlo_survival_rate_below_threshold(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "mc-survival-low.json",
        make_artifact(monte_carlo_survival_rate=0.72),
    )

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=None,
        timestamp=1710007300,
        require_gate_pass=True,
        min_monte_carlo_survival_rate=0.8,
    )

    assert report["status"] == "FAIL"
    assert report["checks"][0]["category"] == "promotion_gate"
    assert report["checks"][0]["evidence"]["monte_carlo_survival_rate"] == 0.72
    assert "monte_carlo_survival_rate_below_threshold" in report["checks"][0]["promotion_decision"]["reason_codes"]


def test_strategy_validation_artifacts_script_skips_without_artifacts(tmp_path):
    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_dir=tmp_path / "missing",
        artifact_paths=None,
        output_path=None,
        timestamp=1710007300,
    )

    assert report["status"] == "SKIPPED"
    assert report["artifact_count"] == 0
    assert report["failed_count"] == 0
    assert report["required_evidence"]["out_of_sample_required"] is True
    assert report["required_evidence"]["source_provenance_required"] is True
    assert report["live_orders_sent"] is False
    assert report["analytics_modified_live_state"] is False
    assert report["contains_real_credentials"] is False
    assert report["checks"] == []


def test_strategy_validation_artifacts_script_fail_report_keeps_safety_flags(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "incomplete.json",
        make_artifact(missing_evidence=True),
    )
    output_path = tmp_path / "latest.json"

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=output_path,
        timestamp=1710007300,
    )

    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert persisted["status"] == "FAIL"
    assert persisted["artifact_count"] == 1
    assert persisted["failed_count"] == 1
    assert persisted["live_orders_sent"] is False
    assert persisted["analytics_modified_live_state"] is False
    assert persisted["contains_real_credentials"] is False
    assert persisted["checks"][0]["evidence"]["has_out_of_sample"] is False
    assert persisted["checks"][0]["evidence"]["walk_forward_count"] == 0
    assert persisted["checks"][0]["evidence"]["has_monte_carlo"] is False


def test_strategy_validation_artifact_store_builds_worker_index_from_latest_report(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "BTCUSDT" / "ma_crossover" / "candidate.json",
        make_artifact(),
    )
    latest_report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=tmp_path / "latest.json",
        timestamp=1710007300,
        require_gate_pass=True,
    )

    index = build_strategy_validation_index(
        artifact_dir=tmp_path / "artifacts",
        latest_report_path=tmp_path / "latest.json",
    )

    assert index["available"] is True
    assert index["status"] == "PASS"
    assert index["review_status"] == "READY_FOR_REVIEW"
    assert index["artifact_count"] == 1
    assert index["failed_count"] == 0
    assert index["latest_report"]["status"] == latest_report["status"]
    assert index["latest_report"]["artifact_count"] == latest_report["artifact_count"]
    assert index["latest_report"]["source_path"] == str(tmp_path / "latest.json")
    assert index["artifact_summaries"][0]["symbol"] == "BTCUSDT"
    assert index["artifact_summaries"][0]["strategy_id"] == "ma_crossover"
    assert index["artifact_summaries"][0]["candidate_version"] == "review-2024-03-09-BTCUSDT-ma_crossover"
    assert index["artifact_summaries"][0]["status"] == "PASS"
    assert index["artifact_summaries"][0]["evidence"]["has_out_of_sample"] is True
    assert index["artifact_summaries"][0]["evidence"]["walk_forward_count"] == 1
    assert index["artifact_summaries"][0]["evidence"]["has_monte_carlo"] is True
    assert index["reason_codes"] == ["promotion_gate_passed"]
    assert index["safety"]["network_used"] is False
    assert index["safety"]["broker_called"] is False
    assert index["safety"]["live_orders_sent"] is False


def test_strategy_validation_artifact_store_index_skips_missing_artifacts(tmp_path):
    index = build_strategy_validation_index(
        artifact_dir=tmp_path / "missing-artifacts",
        latest_report_path=tmp_path / "missing-latest.json",
    )

    assert index["available"] is False
    assert index["reason"] == "strategy_validation_artifacts_skipped"
    assert index["status"] == "SKIPPED"
    assert index["review_status"] == "SKIPPED"
    assert index["artifact_count"] == 0
    assert index["latest_report_found"] is False
    assert index["latest_report"]["artifact_count"] == 0
    assert index["reason_codes"] == ["missing_strategy_validation_artifacts"]


def test_strategy_validation_artifacts_script_cli_require_gate_pass_fails(tmp_path):
    artifact_path = write_artifact(
        tmp_path / "artifacts" / "negative.json",
        make_artifact(total_net_pnl=-2.0),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "validate_strategy_validation_artifacts.py"),
            "--artifact",
            str(artifact_path),
            "--artifact-dir",
            str(tmp_path / "missing"),
            "--no-output",
            "--timestamp",
            "1710007300",
            "--require-gate-pass",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["status"] == "FAIL"
    assert report["failed_count"] == 1
    assert report["require_gate_pass"] is True
    assert report["checks"][0]["category"] == "promotion_gate"
    assert (
        "net_pnl_below_threshold"
        in report["checks"][0]["promotion_decision"]["reason_codes"]
    )
    assert report["live_orders_sent"] is False
    assert report["analytics_modified_live_state"] is False
    assert report["contains_real_credentials"] is False


def test_strategy_validation_artifacts_script_cli_skipped_without_artifacts(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "validate_strategy_validation_artifacts.py"),
            "--artifact-dir",
            str(tmp_path / "missing"),
            "--no-output",
            "--timestamp",
            "1710007300",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["status"] == "SKIPPED"
    assert report["artifact_count"] == 0
    assert report["failed_count"] == 0
    assert report["message"] == "no strategy validation artifact JSON files were found"
    assert report["live_orders_sent"] is False
    assert report["analytics_modified_live_state"] is False
    assert report["contains_real_credentials"] is False
