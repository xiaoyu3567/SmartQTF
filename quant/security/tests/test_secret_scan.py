import json

from quant.security import (
    REDACTED_VALUE,
    discover_artifact_paths,
    redact_sensitive_payload,
    scan_artifact_paths,
    scan_payload,
)


def test_secret_scan_detects_and_redacts_unredacted_json_payload(tmp_path):
    artifact = tmp_path / "logs" / "raw-report.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "run_id": "unit-test",
                "metadata": {
                    "api_key": "fake-api-key-123456",
                    "passphrase": "fake-passphrase-123456",
                },
            }
        ),
        encoding="utf-8",
    )

    report = scan_artifact_paths([artifact], root=tmp_path, generated_at=1)

    assert report.status == "FAIL"
    assert report.finding_count == 2
    assert {finding.field_path for finding in report.findings} == {
        "$.metadata.api_key",
        "$.metadata.passphrase",
    }
    assert "fake-api-key-123456" not in json.dumps(report.to_payload())

    redacted = redact_sensitive_payload(json.loads(artifact.read_text(encoding="utf-8")))
    assert redacted["metadata"]["api_key"] == REDACTED_VALUE
    assert redacted["metadata"]["passphrase"] == REDACTED_VALUE
    assert scan_payload(redacted, source="redacted").status == "PASS"


def test_secret_scan_accepts_redacted_dashboard_trade_and_reconciliation_artifacts(tmp_path):
    dashboard = tmp_path / "docs" / "harness" / "web" / "harness-status.json"
    trade_log = tmp_path / "logs" / "trade-journal.jsonl"
    reconciliation = tmp_path / "logs" / "reconciliation" / "latest.json"
    validation = tmp_path / "logs" / "strategy-validation-artifacts" / "latest.json"
    for path in [dashboard, trade_log, reconciliation, validation]:
        path.parent.mkdir(parents=True, exist_ok=True)

    dashboard.write_text(
        json.dumps(
            {
                "project": "SmartQTF",
                "metadata": {"contains_real_credentials": False},
                "note": "API key, secret, passphrase, token fields must be redacted.",
            }
        ),
        encoding="utf-8",
    )
    trade_log.write_text(
        json.dumps(
            {
                "record_type": "trade_journal",
                "trade_id": "trade-001",
                "metadata": {"api_key": REDACTED_VALUE, "live_orders_sent": False},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    reconciliation.write_text(
        json.dumps(
            {
                "run_id": "recon-001",
                "raw_exchange_response": {"secretKey": REDACTED_VALUE},
                "metadata": {"broker_called": False},
            }
        ),
        encoding="utf-8",
    )
    validation.write_text(
        json.dumps(
            {
                "status": "SKIPPED",
                "artifact_count": 0,
                "contains_real_credentials": False,
            }
        ),
        encoding="utf-8",
    )

    paths, truncated = discover_artifact_paths([tmp_path / "docs", tmp_path / "logs"])
    report = scan_artifact_paths(paths, root=tmp_path, generated_at=1, truncated=truncated)

    assert report.status == "PASS"
    assert report.finding_count == 0
    assert "docs/harness/web/harness-status.json" in report.scanned_files
    assert "logs/trade-journal.jsonl" in report.scanned_files


def test_secret_scan_finds_secrets_inside_text_artifacts(tmp_path):
    log_path = tmp_path / "logs" / "heartbeat.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "Authorization: Bearer fake-live-token-123456\n"
        "OKX_API_KEY=fake-api-key-abcdef\n",
        encoding="utf-8",
    )

    report = scan_artifact_paths([log_path], root=tmp_path, generated_at=1)

    assert report.status == "FAIL"
    assert {finding.pattern for finding in report.findings} == {
        "bearer_token",
        "api_key",
    }
    assert "fake-live-token-123456" not in json.dumps(report.to_payload())


def test_dashboard_secret_scan_summary_checks_current_payload_and_logs(tmp_path, monkeypatch):
    from scripts import update_harness_dashboard as dashboard

    output = tmp_path / "docs" / "harness" / "web" / "harness-status.json"
    log_path = tmp_path / "logs" / "trade-journal" / "trade.jsonl"
    output.parent.mkdir(parents=True)
    log_path.parent.mkdir(parents=True)
    output.write_text(json.dumps({"project": "SmartQTF"}), encoding="utf-8")
    log_path.write_text(
        json.dumps({"record_type": "trade_journal", "metadata": {"api_key": REDACTED_VALUE}}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(dashboard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "OUTPUT", output)
    monkeypatch.setattr(dashboard, "SECRET_SCAN_MAX_FILES", 50)

    clean_summary = dashboard.secret_artifact_scan_summary(
        {"project": "SmartQTF", "metadata": {"contains_real_credentials": False}}
    )

    assert clean_summary["status"] == "PASS"
    assert clean_summary["safety"]["broker_called"] is False
    assert clean_summary["safety"]["live_orders_sent"] is False

    dirty_summary = dashboard.secret_artifact_scan_summary(
        {"metadata": {"secretKey": "fake-secret-key-123456"}}
    )

    assert dirty_summary["status"] == "FAIL"
    assert dirty_summary["current_dashboard_payload"]["finding_count"] == 1
    assert "fake-secret-key-123456" not in json.dumps(dirty_summary)
