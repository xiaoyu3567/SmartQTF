import json

from scripts import update_harness_dashboard as dashboard


def test_latest_production_rehearsal_summarizes_latest_artifact(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "logs" / "production-rehearsals"
    artifact_dir.mkdir(parents=True)
    payload = {
        "report_id": "production-rehearsal:1700000000",
        "generated_at": 1700000000,
        "success": False,
        "checks": [
            {
                "name": "preflight:qtf_environment",
                "status": "FAIL",
                "category": "qtf_environment",
                "message": "QTF environment is not active",
                "source": "preflight",
            },
            {
                "name": "connectivity:okx:public",
                "status": "WARN",
                "category": "dns",
                "message": "DNS lookup failed",
                "source": "connectivity",
            },
            {
                "name": "dry_run:report",
                "status": "FAIL",
                "category": "dry_run",
                "message": "dry-run report summarized",
                "source": "dry_run",
            },
        ],
        "dry_run_summary": {
            "status": "FAIL",
            "run_id": "ci-live-dry-run",
            "failed_stages": ["execution"],
        },
        "metadata": {
            "contains_real_credentials": False,
            "live_orders_sent": False,
            "ci_safe": True,
            "external_exchange_access": False,
        },
    }
    (artifact_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(dashboard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "REHEARSAL_DIR", artifact_dir)

    summary = dashboard.latest_production_rehearsal()

    assert summary["path"] == "logs/production-rehearsals/latest.json"
    assert summary["success"] is False
    assert summary["check_count"] == 3
    assert summary["failed_count"] == 2
    assert summary["warning_count"] == 1
    assert summary["failure_categories"] == ["dry_run", "qtf_environment"]
    assert summary["dry_run_status"] == "FAIL"
    assert summary["safety"]["live_orders_sent"] is False
    assert any("QTF" in action for action in summary["next_actions"])
    assert any("execution" in action for action in summary["next_actions"])


def test_latest_production_rehearsal_returns_none_without_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "REHEARSAL_DIR", tmp_path / "missing")

    assert dashboard.latest_production_rehearsal() is None


def test_layer_contract_validation_reports_violation_count(tmp_path, monkeypatch):
    bad_strategy_file = tmp_path / "quant" / "strategy" / "bad_strategy.py"
    bad_strategy_file.parent.mkdir(parents=True)
    bad_strategy_file.write_text(
        "from quant.execution.broker import BrokerAdapter\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "PROJECT_ROOT", tmp_path)

    payload = dashboard.layer_contract_validation()

    assert payload["summary"]["status"] == "VIOLATION"
    assert payload["summary"]["violation_count"] == 1
    assert payload["violations"][0]["rule_id"] == "strategy-no-broker-imports"
