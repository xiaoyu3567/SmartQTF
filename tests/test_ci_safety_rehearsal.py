import json

from scripts import run_ci_safety_rehearsal as ci_rehearsal


def test_ci_safety_rehearsal_writes_report_without_exchange_access(tmp_path, monkeypatch):
    output_dir = tmp_path / "ci-rehearsal"
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_API_SECRET", raising=False)
    monkeypatch.delenv("OKX_API_PASSPHRASE", raising=False)

    report, artifact_paths, dry_run_path = ci_rehearsal.run_ci_safety_rehearsal(output_dir=output_dir)
    payload = report.to_payload()
    checks = {check["name"]: check for check in payload["checks"]}

    assert report.success is True
    assert dry_run_path.exists()
    assert payload["metadata"]["ci_safe"] is True
    assert payload["metadata"]["external_exchange_access"] is False
    assert payload["metadata"]["contains_real_credentials"] is False
    assert payload["metadata"]["live_orders_sent"] is False
    assert checks["connectivity_diagnostics"]["status"] == "SKIPPED"
    assert checks["dry_run:report"]["status"] == "PASS"
    assert payload["dry_run_summary"]["run_id"] == "ci-live-dry-run"
    dry_run_payload = json.loads(dry_run_path.read_text(encoding="utf-8"))
    dry_run_stages = {stage["stage"]: stage for stage in dry_run_payload["stages"]}
    assert dry_run_payload["success"] is True
    assert dry_run_stages["risk"]["status"] == "rejected"
    assert dry_run_stages["execution"]["status"] == "skipped"
    assert dry_run_payload["final_output"]["risk_decision"]["approved"] is False
    assert (output_dir / "ci-safety-rehearsal.json").exists()
    assert (output_dir / "ci-safety-rehearsal.md").exists()
    assert (output_dir / "latest.json").exists()
    assert artifact_paths["json_path"].endswith("ci-safety-rehearsal.json")


def test_ci_safety_rehearsal_cli_returns_success_and_prints_artifact_paths(tmp_path, monkeypatch, capsys):
    output_dir = tmp_path / "ci-cli"
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")

    exit_code = ci_rehearsal.main(["--output-dir", str(output_dir)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["metadata"]["ci_safe"] is True
    assert payload["metadata"]["artifact_paths"]["latest_json_path"].endswith("latest.json")
    assert (output_dir / "ci-live-dry-run.json").exists()
