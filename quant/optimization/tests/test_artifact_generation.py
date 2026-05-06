import json
import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_REPORT_EXAMPLE_PATH = (
    PROJECT_ROOT / "config" / "examples" / "strategy-validation-source-report.example.json"
)

from quant.optimization.artifact_generation import (
    StrategyValidationArtifactSourceReport,
    StrategyValidationSourceSummary,
    build_strategy_validation_artifact,
    discover_source_reports,
    load_source_report,
)
from quant.schemas import (
    MonteCarloSimulationMethod,
    MonteCarloValidation,
    StrategyValidationSlice,
    StrategyValidationSliceKind,
)
from scripts import generate_strategy_validation_artifacts as generate_artifacts


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


def make_source_report():
    return StrategyValidationArtifactSourceReport(
        report_id="review-validation-001",
        strategy_id="ma_crossover",
        candidate_version="review-2026-05-04-BTCUSDT-ma_crossover",
        symbol="BTCUSDT",
        generated_at=1777827600,
        source_report_id="daily-review-2026-05-04",
        summary=StrategyValidationSourceSummary(
            trade_count=36,
            total_net_pnl=42.5,
            max_drawdown=1.8,
            win_rate=0.59,
            sharpe_ratio=1.24,
        ),
        validation_slices=[
            make_slice(
                "oos-2026-q1",
                StrategyValidationSliceKind.OUT_OF_SAMPLE,
                trade_count=12,
                total_net_pnl=18.0,
                max_drawdown=0.9,
                win_rate=0.58,
                sharpe_ratio=1.1,
            ),
            make_slice("wf-2026-01", StrategyValidationSliceKind.WALK_FORWARD),
            make_slice("wf-2026-02", StrategyValidationSliceKind.WALK_FORWARD),
            make_slice("wf-2026-03", StrategyValidationSliceKind.WALK_FORWARD),
        ],
        monte_carlo_survival_rate=0.83,
        monte_carlo_validation=MonteCarloValidation(
            method=MonteCarloSimulationMethod.HYBRID,
            run_count=500,
            perturbation_dimensions=[
                "trade_order_shuffle",
                "return_perturbation",
                "slippage_fee_perturbation",
            ],
            seed=42,
            survival_threshold=0.8,
        ),
    )


def make_source_report_payload(**overrides):
    payload = make_source_report().to_payload()
    payload.update(overrides)
    return payload


def write_source_report(path, source_report):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        source_report
        if isinstance(source_report, dict)
        else source_report.to_payload()
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_strategy_validation_artifact_preserves_source_provenance(tmp_path):
    source_report = make_source_report()
    source_path = tmp_path / "inputs" / "source-report.json"

    artifact = build_strategy_validation_artifact(
        source_report,
        source_path=source_path,
    )

    assert artifact.strategy_id == "ma_crossover"
    assert artifact.candidate_version == "review-2026-05-04-BTCUSDT-ma_crossover"
    assert artifact.metrics.report_id == "review-validation-001"
    assert artifact.metrics.trade_count == 36
    assert artifact.metrics.validation_slices[0].kind == "out_of_sample"
    assert artifact.metrics.monte_carlo_survival_rate == 0.83
    assert artifact.source_report_id == "daily-review-2026-05-04"
    assert artifact.source_path == str(source_path)


def test_generation_script_writes_artifact_and_runs_strict_validator(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    source_path = write_source_report(
        tmp_path / "inputs" / "validation-source.json",
        make_source_report(),
    )
    artifact_dir = tmp_path / "artifacts"
    output_path = tmp_path / "generation-latest.json"
    validator_output_path = tmp_path / "validator-latest.json"

    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[source_path],
        artifact_dir=artifact_dir,
        output_path=output_path,
        validator_output_path=validator_output_path,
        timestamp=1777827601,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )

    artifact_paths = sorted(artifact_dir.rglob("*.json"))
    assert report["status"] == "PASS"
    assert report["success"] is True
    assert report["source_report_count"] == 1
    assert report["generated_artifact_count"] == 1
    assert report["rejected_source_report_count"] == 0
    assert report["h_opt_005_ready"] is True
    assert report["h_opt_005_ready_scope"] == "artifact_generation_only"
    assert report["h_opt_005_blockers"] == []
    assert (
        report["h_opt_005_next_action"]
        == "handoff_generated_artifacts_to_h_opt_005_manual_review"
    )
    assert report["discovered_source_reports"] == [str(source_path)]
    assert report["source_report_fingerprints"] == [
        {
            "path": str(source_path),
            "exists": True,
            "size_bytes": source_path.stat().st_size,
            "sha256": file_sha256(source_path),
        }
    ]
    assert report["generated_artifact_fingerprints"] == [
        {
            "path": str(artifact_paths[0]),
            "exists": True,
            "size_bytes": artifact_paths[0].stat().st_size,
            "sha256": file_sha256(artifact_paths[0]),
        }
    ]
    assert report["source_report_evidence_summary"] == {
        "source_report_count": 1,
        "parseable_source_report_count": 1,
        "complete_anti_overfit_source_report_count": 1,
        "out_of_sample_source_report_count": 1,
        "walk_forward_source_report_count": 1,
        "monte_carlo_source_report_count": 1,
        "missing_evidence_reason_code_counts": {},
    }
    assert report["source_report_evidence_summaries"] == [
        {
            "source_path": str(source_path),
            "parseable": True,
            "report_id": "review-validation-001",
            "source_report_id": "daily-review-2026-05-04",
            "strategy_id": "ma_crossover",
            "candidate_version": "review-2026-05-04-BTCUSDT-ma_crossover",
            "symbol": "BTCUSDT",
            "summary_trade_count": 36,
            "summary_total_net_pnl": 42.5,
            "summary_max_drawdown": 1.8,
            "summary_win_rate": 0.59,
            "summary_sharpe_ratio": 1.24,
            "has_out_of_sample": True,
            "out_of_sample_count": 1,
            "out_of_sample_window_names": ["oos-2026-q1"],
            "walk_forward_count": 3,
            "walk_forward_window_names": [
                "wf-2026-01",
                "wf-2026-02",
                "wf-2026-03",
            ],
            "walk_forward_pass_count": 3,
            "walk_forward_pass_rate": 1.0,
            "has_monte_carlo": True,
            "monte_carlo_survival_rate": 0.83,
            "monte_carlo_method": "hybrid",
            "monte_carlo_run_count": 500,
            "monte_carlo_seed": 42,
            "monte_carlo_perturbation_dimensions": [
                "trade_order_shuffle",
                "return_perturbation",
                "slippage_fee_perturbation",
            ],
            "monte_carlo_survival_threshold": 0.8,
            "missing_evidence_reason_codes": [],
            "complete_anti_overfit_evidence": True,
        }
    ]
    assert report["source_report_status_counts"] == {"GENERATED": 1}
    assert report["validator_status_counts"] == {"PASS": 1}
    assert report["validator_category_counts"] == {"ok": 1}
    assert report["validator_reason_code_counts"] == {"promotion_gate_passed": 1}
    assert report["live_orders_sent"] is False
    assert report["analytics_modified_live_state"] is False
    assert report["contains_real_credentials"] is False
    assert report["source_report_example_path"].endswith(
        "config/examples/strategy-validation-source-report.example.json"
    )
    assert "report_id" in report["required_source_report_fields"]
    assert "monte_carlo_validation.method" in report["recommended_source_report_fields"]
    assert report["validator_report"]["status"] == "PASS"
    assert report["validator_report"]["artifact_count"] == 1
    assert report["source_report_results"] == [
        {
            "source_path": str(source_path),
            "status": "GENERATED",
            "message": "artifact committed after strict validation",
            "artifact_path": str(artifact_paths[0]),
            "artifact_id": "strategy-validation-artifact:ma_crossover:review-2026-05-04-BTCUSDT-ma_crossover:BTCUSDT",
            "source_report_id": "daily-review-2026-05-04",
            "strategy_id": "ma_crossover",
            "candidate_version": "review-2026-05-04-BTCUSDT-ma_crossover",
            "symbol": "BTCUSDT",
            "validator_status": "PASS",
            "validator_category": "ok",
            "validator_message": "strategy validation artifact parsed and required evidence is present",
            "validator_reason_codes": ["promotion_gate_passed"],
        }
    ]
    assert len(artifact_paths) == 1
    artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))
    assert artifact["source_path"] == str(source_path)
    assert artifact["source_report_id"] == "daily-review-2026-05-04"
    assert output_path.exists()
    assert validator_output_path.exists()


def test_generation_script_discovers_source_reports_from_inbox_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    source_path = write_source_report(
        tmp_path / "source-reports" / "BTCUSDT" / "validation-source.json",
        make_source_report(),
    )
    artifact_dir = tmp_path / "artifacts"
    output_path = tmp_path / "generation-latest.json"
    validator_output_path = tmp_path / "validator-latest.json"

    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[],
        source_report_dirs=[tmp_path / "source-reports"],
        artifact_dir=artifact_dir,
        output_path=output_path,
        validator_output_path=validator_output_path,
        timestamp=1777827604,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )

    assert report["status"] == "PASS"
    assert report["source_report_dirs"] == [str(tmp_path / "source-reports")]
    assert report["source_report_count"] == 1
    assert report["prepared_artifact_count"] == 1
    assert report["rejected_source_report_count"] == 0
    assert report["generated"][0]["source_path"] == str(source_path)
    assert report["validator_report"]["artifact_count"] == 1
    assert sorted(artifact_dir.rglob("*.json"))


def test_generation_script_rejects_duplicate_artifact_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    source_report_dir = tmp_path / "source-reports"
    first_source_path = write_source_report(
        source_report_dir / "source-a.json",
        make_source_report_payload(report_id="review-validation-a"),
    )
    second_source_path = write_source_report(
        source_report_dir / "source-b.json",
        make_source_report_payload(
            report_id="review-validation-b",
            source_report_id="daily-review-2026-05-04-b",
        ),
    )
    artifact_dir = tmp_path / "artifacts"

    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[first_source_path, second_source_path],
        source_report_dirs=[],
        artifact_dir=artifact_dir,
        output_path=None,
        validator_output_path=None,
        timestamp=1777827605,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )

    artifact_paths = sorted(artifact_dir.rglob("*.json"))
    assert report["status"] == "FAIL"
    assert report["source_report_count"] == 2
    assert report["generated_artifact_count"] == 1
    assert report["rejected_source_report_count"] == 1
    assert report["generated"][0]["source_path"] == str(first_source_path)
    assert report["errors"][0]["source_path"] == str(second_source_path)
    assert "duplicate strategy validation artifact target" in report["errors"][0][
        "message"
    ]
    assert str(first_source_path) in report["errors"][0]["message"]
    assert [entry["status"] for entry in report["source_report_results"]] == [
        "GENERATED",
        "REJECTED",
    ]
    assert report["source_report_results"][0]["source_path"] == str(first_source_path)
    assert report["source_report_results"][0]["artifact_path"] == str(artifact_paths[0])
    assert report["source_report_results"][1]["source_path"] == str(second_source_path)
    assert "duplicate strategy validation artifact target" in report[
        "source_report_results"
    ][1]["message"]
    assert report["validator_report"]["status"] == "PASS"
    assert report["validator_report"]["artifact_count"] == 1
    assert len(artifact_paths) == 1
    artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))
    assert artifact["metrics"]["report_id"] == "review-validation-a"


def test_generation_script_rejects_existing_artifact_target_by_default(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    first_source_path = write_source_report(
        tmp_path / "inputs" / "source-a.json",
        make_source_report_payload(report_id="review-validation-a"),
    )
    second_source_path = write_source_report(
        tmp_path / "inputs" / "source-b.json",
        make_source_report_payload(report_id="review-validation-b"),
    )
    artifact_dir = tmp_path / "artifacts"

    first_report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[first_source_path],
        source_report_dirs=[],
        artifact_dir=artifact_dir,
        output_path=None,
        validator_output_path=None,
        timestamp=1777827606,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )
    second_report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[second_source_path],
        source_report_dirs=[],
        artifact_dir=artifact_dir,
        output_path=None,
        validator_output_path=None,
        timestamp=1777827607,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )

    artifact_paths = sorted(artifact_dir.rglob("*.json"))
    assert first_report["status"] == "PASS"
    assert second_report["status"] == "FAIL"
    assert second_report["prepared_artifact_count"] == 0
    assert second_report["generated_artifact_count"] == 0
    assert second_report["rejected_source_report_count"] == 1
    assert second_report["overwrite_existing_artifacts"] is False
    assert "target already exists" in second_report["errors"][0]["message"]
    assert "--overwrite-existing-artifacts" in second_report["errors"][0]["message"]
    assert second_report["errors"][0]["artifact_path"] == str(
        artifact_dir
        / "BTCUSDT"
        / "ma_crossover"
        / "review-2026-05-04-BTCUSDT-ma_crossover.json"
    )
    assert second_report["errors"][0]["source_report_id"] == "daily-review-2026-05-04"
    assert second_report["errors"][0]["strategy_id"] == "ma_crossover"
    assert (
        second_report["errors"][0]["candidate_version"]
        == "review-2026-05-04-BTCUSDT-ma_crossover"
    )
    assert second_report["errors"][0]["symbol"] == "BTCUSDT"
    assert second_report["source_report_results"] == [
        {
            "source_path": str(second_source_path),
            "status": "REJECTED",
            "message": second_report["errors"][0]["message"],
            "artifact_path": str(
                artifact_dir
                / "BTCUSDT"
                / "ma_crossover"
                / "review-2026-05-04-BTCUSDT-ma_crossover.json"
            ),
            "artifact_id": None,
            "source_report_id": "daily-review-2026-05-04",
            "strategy_id": "ma_crossover",
            "candidate_version": "review-2026-05-04-BTCUSDT-ma_crossover",
            "symbol": "BTCUSDT",
            "validator_status": None,
            "validator_category": None,
            "validator_message": None,
            "validator_reason_codes": [],
        }
    ]
    assert len(artifact_paths) == 1
    artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))
    assert artifact["metrics"]["report_id"] == "review-validation-a"


def test_generation_script_can_explicitly_overwrite_existing_artifact_target(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    first_source_path = write_source_report(
        tmp_path / "inputs" / "source-a.json",
        make_source_report_payload(report_id="review-validation-a"),
    )
    second_source_path = write_source_report(
        tmp_path / "inputs" / "source-b.json",
        make_source_report_payload(report_id="review-validation-b"),
    )
    artifact_dir = tmp_path / "artifacts"

    generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[first_source_path],
        source_report_dirs=[],
        artifact_dir=artifact_dir,
        output_path=None,
        validator_output_path=None,
        timestamp=1777827608,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )
    overwrite_report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[second_source_path],
        source_report_dirs=[],
        artifact_dir=artifact_dir,
        output_path=None,
        validator_output_path=None,
        timestamp=1777827609,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        overwrite_existing_artifacts=True,
    )

    artifact_paths = sorted(artifact_dir.rglob("*.json"))
    assert overwrite_report["status"] == "PASS"
    assert overwrite_report["overwrite_existing_artifacts"] is True
    assert overwrite_report["generated_artifact_count"] == 1
    assert overwrite_report["rejected_source_report_count"] == 0
    assert len(artifact_paths) == 1
    artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))
    assert artifact["metrics"]["report_id"] == "review-validation-b"


def test_discover_source_reports_dedupes_explicit_and_directory_paths(tmp_path):
    source_path = write_source_report(
        tmp_path / "source-reports" / "validation-source.json",
        make_source_report(),
    )

    discovered = discover_source_reports(
        source_reports=[source_path],
        source_report_dirs=[tmp_path / "source-reports"],
    )

    assert discovered == [source_path]


def test_generation_script_rejects_example_fixture_inputs(tmp_path):
    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[
            PROJECT_ROOT / "config" / "examples" / "strategy-validation-artifact.example.json"
        ],
        source_report_dirs=[],
        artifact_dir=tmp_path / "artifacts",
        output_path=None,
        validator_output_path=None,
        timestamp=1777827602,
        require_gate_pass=True,
    )

    assert report["status"] == "FAIL"
    assert report["source_report_count"] == 1
    assert report["generated_artifact_count"] == 0
    assert report["rejected_source_report_count"] == 1
    assert report["discovered_source_reports"] == [
        str(PROJECT_ROOT / "config" / "examples" / "strategy-validation-artifact.example.json")
    ]
    assert report["errors"][0]["source_path"].endswith(
        "config/examples/strategy-validation-artifact.example.json"
    )
    assert "must not be treated as real validation source reports" in report["errors"][0][
        "message"
    ]


def test_source_report_example_payload_matches_contract():
    payload = json.loads(SOURCE_REPORT_EXAMPLE_PATH.read_text(encoding="utf-8"))
    payload.pop("_comment", None)

    source_report = StrategyValidationArtifactSourceReport.from_payload(payload)

    assert source_report.report_id == "example-review-validation-001"
    assert source_report.summary.trade_count == 36
    assert source_report.validation_slices[0].kind == "out_of_sample"
    assert source_report.monte_carlo_validation is not None


def test_generation_script_skipped_without_inputs_still_reports_contract(tmp_path):
    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[],
        source_report_dirs=[tmp_path / "empty-source-reports"],
        artifact_dir=tmp_path / "artifacts",
        output_path=None,
        validator_output_path=None,
        timestamp=1777827603,
    )

    assert report["status"] == "SKIPPED"
    assert report["source_report_count"] == 0
    assert report["prepared_artifact_count"] == 0
    assert report["generated_artifact_count"] == 0
    assert report["rejected_source_report_count"] == 0
    assert report["source_report_discovery"] == {
        "configured_dir_count": 1,
        "missing_dir_count": 1,
        "configured_dir_without_json_count": 0,
        "json_candidate_count": 0,
        "checks": [
            {
                "path": str(tmp_path / "empty-source-reports"),
                "exists": False,
                "kind": "missing",
                "status": "MISSING",
                "json_file_count": 0,
                "discovered_source_report_count": 0,
            }
        ],
    }
    assert report["source_report_status_counts"] == {}
    assert report["source_report_fingerprints"] == []
    assert report["generated_artifact_fingerprints"] == []
    assert report["source_report_evidence_summary"] == {
        "source_report_count": 0,
        "parseable_source_report_count": 0,
        "complete_anti_overfit_source_report_count": 0,
        "out_of_sample_source_report_count": 0,
        "walk_forward_source_report_count": 0,
        "monte_carlo_source_report_count": 0,
        "missing_evidence_reason_code_counts": {},
    }
    assert report["source_report_evidence_summaries"] == []
    assert report["validator_status_counts"] == {}
    assert report["validator_category_counts"] == {}
    assert report["validator_reason_code_counts"] == {}
    assert report["h_opt_005_ready"] is False
    assert report["h_opt_005_blockers"] == [
        "no_source_reports_discovered",
        "source_report_dirs_missing",
        "generation_status_not_pass",
        "no_artifacts_generated",
        "strict_validator_not_run",
    ]
    assert (
        report["h_opt_005_next_action"]
        == "provide_real_source_reports_and_rerun_strict_generation"
    )
    assert report["discovered_source_reports"] == []
    assert report["source_report_dirs"] == [str(tmp_path / "empty-source-reports")]
    assert report["source_report_example_path"].endswith(
        "config/examples/strategy-validation-source-report.example.json"
    )
    assert "validation_slices[].kind" in report["required_source_report_fields"]


def test_generation_script_skipped_with_empty_existing_source_report_dir_reports_discovery(
    tmp_path,
):
    source_report_dir = tmp_path / "empty-source-reports"
    source_report_dir.mkdir(parents=True)

    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[],
        source_report_dirs=[source_report_dir],
        artifact_dir=tmp_path / "artifacts",
        output_path=None,
        validator_output_path=None,
        timestamp=1777827604,
    )

    assert report["status"] == "SKIPPED"
    assert report["source_report_count"] == 0
    assert report["source_report_discovery"] == {
        "configured_dir_count": 1,
        "missing_dir_count": 0,
        "configured_dir_without_json_count": 1,
        "json_candidate_count": 0,
        "checks": [
            {
                "path": str(source_report_dir),
                "exists": True,
                "kind": "directory",
                "status": "NO_JSON_FILES",
                "json_file_count": 0,
                "discovered_source_report_count": 0,
            }
        ],
    }
    assert report["h_opt_005_blockers"] == [
        "no_source_reports_discovered",
        "source_report_dirs_without_json",
        "generation_status_not_pass",
        "no_artifacts_generated",
        "strict_validator_not_run",
    ]


def test_load_source_report_rejects_prebuilt_artifact_payload(tmp_path):
    source_report = make_source_report()
    artifact = build_strategy_validation_artifact(source_report)
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(
        json.dumps(artifact.to_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    try:
        load_source_report(artifact_path)
    except ValueError as exc:
        assert "must not be a prebuilt StrategyValidationArtifact payload" in str(exc)
    else:
        raise AssertionError("expected load_source_report() to reject artifact payloads")


def test_generation_report_summarizes_source_report_evidence_gaps(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    source_path = write_source_report(
        tmp_path / "inputs" / "validation-source.json",
        make_source_report_payload(
            validation_slices=[
                make_slice(
                    "wf-2026-01",
                    StrategyValidationSliceKind.WALK_FORWARD,
                ).to_payload()
            ],
            monte_carlo_survival_rate=None,
            monte_carlo_validation=None,
        ),
    )

    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[source_path],
        source_report_dirs=[],
        artifact_dir=tmp_path / "artifacts",
        output_path=None,
        validator_output_path=None,
        timestamp=1777827613,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )

    assert report["status"] == "FAIL"
    assert report["generated_artifact_count"] == 0
    assert report["source_report_evidence_summary"] == {
        "source_report_count": 1,
        "parseable_source_report_count": 1,
        "complete_anti_overfit_source_report_count": 0,
        "out_of_sample_source_report_count": 0,
        "walk_forward_source_report_count": 1,
        "monte_carlo_source_report_count": 0,
        "missing_evidence_reason_code_counts": {
            "missing_out_of_sample_validation": 1,
            "missing_monte_carlo_validation": 1,
        },
    }
    evidence = report["source_report_evidence_summaries"][0]
    assert evidence["source_path"] == str(source_path)
    assert evidence["parseable"] is True
    assert evidence["has_out_of_sample"] is False
    assert evidence["out_of_sample_count"] == 0
    assert evidence["walk_forward_count"] == 1
    assert evidence["walk_forward_window_names"] == ["wf-2026-01"]
    assert evidence["walk_forward_pass_count"] == 1
    assert evidence["walk_forward_pass_rate"] == 1.0
    assert evidence["has_monte_carlo"] is False
    assert evidence["missing_evidence_reason_codes"] == [
        "missing_out_of_sample_validation",
        "missing_monte_carlo_validation",
    ]
    assert evidence["complete_anti_overfit_evidence"] is False


def test_generation_report_marks_unparseable_source_report_evidence(tmp_path):
    source_path = tmp_path / "inputs" / "invalid-source.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        json.dumps({"report_id": "invalid-source"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[source_path],
        source_report_dirs=[],
        artifact_dir=tmp_path / "artifacts",
        output_path=None,
        validator_output_path=None,
        timestamp=1777827614,
        require_gate_pass=True,
    )

    assert report["status"] == "FAIL"
    assert report["rejected_source_report_count"] == 1
    assert report["source_report_evidence_summary"] == {
        "source_report_count": 1,
        "parseable_source_report_count": 0,
        "complete_anti_overfit_source_report_count": 0,
        "out_of_sample_source_report_count": 0,
        "walk_forward_source_report_count": 0,
        "monte_carlo_source_report_count": 0,
        "missing_evidence_reason_code_counts": {
            "source_report_not_parseable": 1,
        },
    }
    evidence = report["source_report_evidence_summaries"][0]
    assert evidence["source_path"] == str(source_path)
    assert evidence["parseable"] is False
    assert evidence["missing_evidence_reason_codes"] == ["source_report_not_parseable"]
    assert evidence["complete_anti_overfit_evidence"] is False


def test_load_source_report_rejects_example_source_path_provenance(tmp_path):
    payload = make_source_report().to_payload()
    payload["source_path"] = "config/examples/strategy-validation-source-report.example.json"
    source_path = tmp_path / "validation-source.json"
    source_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    try:
        load_source_report(source_path)
    except ValueError as exc:
        assert "must not be treated as real validation evidence" in str(exc)
    else:
        raise AssertionError("expected load_source_report() to reject example provenance")


def test_load_source_report_rejects_credential_like_fields(tmp_path):
    payload = make_source_report().to_payload()
    payload["broker_config"] = {"api_key": "should-not-enter-artifacts"}
    source_path = tmp_path / "validation-source.json"
    source_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    try:
        load_source_report(source_path)
    except ValueError as exc:
        assert "must not contain credential-like fields" in str(exc)
        assert "$.broker_config.api_key" in str(exc)
    else:
        raise AssertionError("expected load_source_report() to reject credential fields")


def test_load_source_report_rejects_live_side_effect_flags(tmp_path):
    payload = make_source_report().to_payload()
    payload["safety"] = {"live_orders_sent": True}
    source_path = tmp_path / "validation-source.json"
    source_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    try:
        load_source_report(source_path)
    except ValueError as exc:
        assert "must not indicate live/broker side effects" in str(exc)
        assert "$.safety.live_orders_sent" in str(exc)
    else:
        raise AssertionError("expected load_source_report() to reject live safety flags")


def test_load_source_report_rejects_stringified_live_side_effect_flags(tmp_path):
    payload = make_source_report().to_payload()
    payload["safety"] = {
        "broker_called": "true",
        "exchange_order_submitted": "1",
    }
    source_path = tmp_path / "validation-source.json"
    source_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    try:
        load_source_report(source_path)
    except ValueError as exc:
        assert "must not indicate live/broker side effects" in str(exc)
        assert "$.safety.broker_called" in str(exc)
    else:
        raise AssertionError(
            "expected load_source_report() to reject stringified live safety flags"
        )


def test_generation_script_rolls_back_failed_staging_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    source_path = write_source_report(
        tmp_path / "inputs" / "validation-source.json",
        make_source_report_payload(
            monte_carlo_survival_rate=0.61,
            monte_carlo_validation=MonteCarloValidation(
                method=MonteCarloSimulationMethod.HYBRID,
                run_count=500,
                perturbation_dimensions=[
                    "trade_order_shuffle",
                    "return_perturbation",
                ],
                seed=42,
                survival_threshold=0.8,
            ).to_payload(),
        ),
    )
    artifact_dir = tmp_path / "artifacts"
    validator_output_path = tmp_path / "validator-latest.json"

    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[source_path],
        source_report_dirs=[],
        artifact_dir=artifact_dir,
        output_path=None,
        validator_output_path=validator_output_path,
        timestamp=1777827610,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )

    assert report["status"] == "FAIL"
    assert report["success"] is False
    assert report["prepared_artifact_count"] == 1
    assert report["generated_artifact_count"] == 0
    assert report["rejected_source_report_count"] == 0
    assert report["h_opt_005_ready"] is False
    assert report["h_opt_005_blockers"] == [
        "generation_status_not_pass",
        "no_artifacts_generated",
        "strict_validator_not_pass",
        "strict_validator_failed_count_nonzero",
    ]
    assert report["source_report_status_counts"] == {"VALIDATION_FAILED": 1}
    assert report["validator_status_counts"] == {"FAIL": 1}
    assert report["validator_category_counts"] == {"promotion_gate": 1}
    assert report["validator_reason_code_counts"] == {
        "monte_carlo_survival_rate_below_threshold": 1
    }
    assert "no artifact was committed" in report["message"]
    assert report["validator_report"]["status"] == "FAIL"
    assert report["validator_report"]["artifact_count"] == 1
    assert report["validator_report"]["checks"][0]["path"].endswith(
        "BTCUSDT/ma_crossover/review-2026-05-04-BTCUSDT-ma_crossover.json"
    )
    assert report["validator_report"]["checks"][0]["category"] == "promotion_gate"
    assert report["source_report_results"] == [
        {
            "source_path": str(source_path),
            "status": "VALIDATION_FAILED",
            "message": "artifact was staged but not committed because strict validation failed",
            "artifact_path": str(
                artifact_dir
                / "BTCUSDT"
                / "ma_crossover"
                / "review-2026-05-04-BTCUSDT-ma_crossover.json"
            ),
            "artifact_id": "strategy-validation-artifact:ma_crossover:review-2026-05-04-BTCUSDT-ma_crossover:BTCUSDT",
            "source_report_id": "daily-review-2026-05-04",
            "strategy_id": "ma_crossover",
            "candidate_version": "review-2026-05-04-BTCUSDT-ma_crossover",
            "symbol": "BTCUSDT",
            "validator_status": "FAIL",
            "validator_category": "promotion_gate",
            "validator_message": "strategy validation artifact did not pass the configured promotion gate",
            "validator_reason_codes": ["monte_carlo_survival_rate_below_threshold"],
        }
    ]
    assert not sorted(artifact_dir.rglob("*.json"))
    assert not validator_output_path.exists()


def test_generation_script_marks_passing_prepared_artifact_not_committed_when_batch_has_failure(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    passing_source_path = write_source_report(
        tmp_path / "inputs" / "passing-source.json",
        make_source_report_payload(report_id="review-validation-pass"),
    )
    failing_source_path = write_source_report(
        tmp_path / "inputs" / "failing-source.json",
        make_source_report_payload(
            report_id="review-validation-fail",
            candidate_version="review-2026-05-04-BTCUSDT-ma_crossover-fail",
            monte_carlo_survival_rate=0.61,
            monte_carlo_validation=MonteCarloValidation(
                method=MonteCarloSimulationMethod.HYBRID,
                run_count=500,
                perturbation_dimensions=[
                    "trade_order_shuffle",
                    "return_perturbation",
                ],
                seed=42,
                survival_threshold=0.8,
            ).to_payload(),
        ),
    )
    artifact_dir = tmp_path / "artifacts"

    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[passing_source_path, failing_source_path],
        source_report_dirs=[],
        artifact_dir=artifact_dir,
        output_path=None,
        validator_output_path=None,
        timestamp=1777827611,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )

    assert report["status"] == "FAIL"
    assert report["prepared_artifact_count"] == 2
    assert report["generated_artifact_count"] == 0
    assert report["rejected_source_report_count"] == 0
    assert report["validator_report"]["artifact_count"] == 2
    assert report["source_report_status_counts"] == {
        "VALIDATED_NOT_COMMITTED": 1,
        "VALIDATION_FAILED": 1,
    }
    assert report["validator_status_counts"] == {"PASS": 1, "FAIL": 1}
    assert report["validator_category_counts"] == {
        "ok": 1,
        "promotion_gate": 1,
    }
    assert report["validator_reason_code_counts"] == {
        "promotion_gate_passed": 1,
        "monte_carlo_survival_rate_below_threshold": 1,
    }
    assert report["source_report_results"] == [
        {
            "source_path": str(passing_source_path),
            "status": "VALIDATED_NOT_COMMITTED",
            "message": "artifact passed strict validation but was not committed because another prepared artifact failed batch validation",
            "artifact_path": str(
                artifact_dir
                / "BTCUSDT"
                / "ma_crossover"
                / "review-2026-05-04-BTCUSDT-ma_crossover.json"
            ),
            "artifact_id": "strategy-validation-artifact:ma_crossover:review-2026-05-04-BTCUSDT-ma_crossover:BTCUSDT",
            "source_report_id": "daily-review-2026-05-04",
            "strategy_id": "ma_crossover",
            "candidate_version": "review-2026-05-04-BTCUSDT-ma_crossover",
            "symbol": "BTCUSDT",
            "validator_status": "PASS",
            "validator_category": "ok",
            "validator_message": "strategy validation artifact parsed and required evidence is present",
            "validator_reason_codes": ["promotion_gate_passed"],
        },
        {
            "source_path": str(failing_source_path),
            "status": "VALIDATION_FAILED",
            "message": "artifact was staged but not committed because strict validation failed",
            "artifact_path": str(
                artifact_dir
                / "BTCUSDT"
                / "ma_crossover"
                / "review-2026-05-04-BTCUSDT-ma_crossover-fail.json"
            ),
            "artifact_id": "strategy-validation-artifact:ma_crossover:review-2026-05-04-BTCUSDT-ma_crossover-fail:BTCUSDT",
            "source_report_id": "daily-review-2026-05-04",
            "strategy_id": "ma_crossover",
            "candidate_version": "review-2026-05-04-BTCUSDT-ma_crossover-fail",
            "symbol": "BTCUSDT",
            "validator_status": "FAIL",
            "validator_category": "promotion_gate",
            "validator_message": "strategy validation artifact did not pass the configured promotion gate",
            "validator_reason_codes": ["monte_carlo_survival_rate_below_threshold"],
        },
    ]
    assert not sorted(artifact_dir.rglob("*.json"))


def test_generation_script_reports_commit_failure_as_pipeline_error(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    source_path = write_source_report(
        tmp_path / "inputs" / "validation-source.json",
        make_source_report_payload(report_id="review-validation-pass"),
    )
    artifact_dir = tmp_path / "artifacts"

    def fail_commit(prepared_artifacts):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(generate_artifacts, "_commit_prepared_artifacts", fail_commit)

    report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[source_path],
        source_report_dirs=[],
        artifact_dir=artifact_dir,
        output_path=None,
        validator_output_path=None,
        timestamp=1777827612,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )

    assert report["status"] == "FAIL"
    assert report["message"] == (
        "strategy validation artifact generation passed strict staging validation, "
        "but artifact commit failed so no artifact was published"
    )
    assert report["generation_error_count"] == 1
    assert report["rejected_source_report_count"] == 0
    assert report["pipeline_error_count"] == 1
    assert report["source_report_errors"] == []
    assert report["pipeline_errors"] == [
        {
            "error_type": "artifact_commit_failed",
            "source_path": "__commit__",
            "message": (
                "failed to commit validated strategy validation artifacts: "
                "simulated commit failure"
            ),
        }
    ]
    assert report["errors"] == report["pipeline_errors"]
    assert report["h_opt_005_ready"] is False
    assert report["h_opt_005_blockers"] == [
        "generation_status_not_pass",
        "no_artifacts_generated",
        "artifact_commit_failed",
    ]
    assert report["source_report_status_counts"] == {"VALIDATED_NOT_COMMITTED": 1}
    assert report["validator_status_counts"] == {"PASS": 1}
    assert report["validator_category_counts"] == {"ok": 1}
    assert report["validator_reason_code_counts"] == {"promotion_gate_passed": 1}
    assert report["source_report_results"] == [
        {
            "source_path": str(source_path),
            "status": "VALIDATED_NOT_COMMITTED",
            "message": (
                "artifact passed strict validation but was not committed because the "
                "batch commit failed"
            ),
            "artifact_path": str(
                artifact_dir
                / "BTCUSDT"
                / "ma_crossover"
                / "review-2026-05-04-BTCUSDT-ma_crossover.json"
            ),
            "artifact_id": "strategy-validation-artifact:ma_crossover:review-2026-05-04-BTCUSDT-ma_crossover:BTCUSDT",
            "source_report_id": "daily-review-2026-05-04",
            "strategy_id": "ma_crossover",
            "candidate_version": "review-2026-05-04-BTCUSDT-ma_crossover",
            "symbol": "BTCUSDT",
            "validator_status": "PASS",
            "validator_category": "ok",
            "validator_message": "strategy validation artifact parsed and required evidence is present",
            "validator_reason_codes": ["promotion_gate_passed"],
        }
    ]
    assert not sorted(artifact_dir.rglob("*.json"))
