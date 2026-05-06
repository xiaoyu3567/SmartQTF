import json
from pathlib import Path

from quant.optimization.artifact_generation import load_source_report
from scripts import generate_strategy_validation_source_reports as generate_reports


def build_klines(count=180):
    pattern = [100.0, 103.0, 98.0, 104.0, 97.0, 105.0]
    closes = [pattern[index % len(pattern)] + index * 0.01 for index in range(count)]
    klines = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        klines.append(
            {
                "timestamp": 1701000000 + index * 60,
                "open": previous,
                "high": max(previous, close) + 1.0,
                "low": min(previous, close) - 1.0,
                "close": close,
                "volume": 1000.0 + index,
            }
        )
    return klines


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def test_aggregate_generation_publishes_complete_artifact(tmp_path):
    source_path = write_json(tmp_path / "history" / "BTCUSDT-1m.json", build_klines())
    output_dir = tmp_path / "source-reports"
    artifact_dir = tmp_path / "artifacts"
    report_output_path = tmp_path / "source-report-generation-latest.json"
    artifact_generation_output_path = tmp_path / "artifact-generation-latest.json"
    validator_output_path = tmp_path / "validator-latest.json"

    report = generate_reports.run_strategy_validation_source_report_generation(
        source_paths=[source_path],
        strategy_id="ma_crossover",
        candidate_version="review-2026-05-04-BTCUSDT-ma_crossover",
        symbol="BTCUSDT",
        output_dir=output_dir,
        report_output_path=report_output_path,
        timestamp=1777827600,
        generation_kind="aggregate",
        train_bars=30,
        test_bars=20,
        step_bars=20,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        monte_carlo_run_count=40,
        min_monte_carlo_trades=5,
        min_monte_carlo_survival_rate=0.3,
        artifact_dir=artifact_dir,
        artifact_generation_output_path=artifact_generation_output_path,
        validator_output_path=validator_output_path,
    )

    assert report["status"] == "PASS"
    assert report["source_report_generation_scope"] == "H-OPT-016"
    assert report["generation_kind"] == "aggregate"
    assert report["generated_source_report_count"] == 1
    assert report["generated_artifact_count"] == 1
    assert report["validator_status"] == "PASS"
    assert report["h_opt_005_ready"] is True
    assert report["reason_codes"] == []
    assert report["safety_flags"]["live_orders_sent"] is False
    assert report["safety_flags"]["broker_called"] is False

    source_report_path = Path(report["aggregate_source_report_path"])
    artifact_path = Path(report["artifact_paths"][0])
    assert source_report_path.exists()
    assert artifact_path.exists()
    assert json.loads(report_output_path.read_text(encoding="utf-8"))["status"] == "PASS"
    assert json.loads(artifact_generation_output_path.read_text(encoding="utf-8"))[
        "generated_artifact_count"
    ] == 1
    assert json.loads(validator_output_path.read_text(encoding="utf-8"))[
        "status"
    ] == "PASS"

    source_report = load_source_report(source_report_path)
    kinds = [item.kind for item in source_report.validation_slices]
    assert kinds.count("out_of_sample") >= 1
    assert kinds.count("walk_forward") >= 3
    assert source_report.monte_carlo_validation is not None
    assert source_report.monte_carlo_survival_rate is not None
    payload = json.loads(source_report_path.read_text(encoding="utf-8"))
    assert payload["provenance"]["generation_scope"] == "H-OPT-016"
    assert payload["provenance"]["source_fingerprints"][0]["sha256"]


def test_aggregate_generation_skips_incomplete_evidence_without_artifact(tmp_path):
    source_path = write_json(tmp_path / "history" / "BTCUSDT-1m.json", build_klines())
    output_dir = tmp_path / "source-reports"
    artifact_dir = tmp_path / "artifacts"

    report = generate_reports.run_strategy_validation_source_report_generation(
        source_paths=[source_path],
        strategy_id="ma_crossover",
        candidate_version="review-2026-05-04-BTCUSDT-ma_crossover",
        symbol="BTCUSDT",
        output_dir=output_dir,
        report_output_path=tmp_path / "source-report-generation-latest.json",
        timestamp=1777827600,
        generation_kind="aggregate",
        train_bars=30,
        test_bars=20,
        step_bars=20,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        monte_carlo_run_count=40,
        min_monte_carlo_trades=999,
        min_monte_carlo_survival_rate=0.3,
        artifact_dir=artifact_dir,
        artifact_generation_output_path=tmp_path / "artifact-generation-latest.json",
        validator_output_path=tmp_path / "validator-latest.json",
    )

    assert report["status"] == "SKIPPED"
    assert "insufficient_monte_carlo_trades" in report["reason_codes"]
    assert "missing_monte_carlo_validation" in report["reason_codes"]
    assert report["generated_source_report_count"] == 0
    assert report["generated_artifact_count"] == 0
    assert report["validator_status"] is None
    assert report["h_opt_005_ready"] is False
    assert not list(output_dir.rglob("*.json"))
    assert not list(artifact_dir.rglob("*.json"))
