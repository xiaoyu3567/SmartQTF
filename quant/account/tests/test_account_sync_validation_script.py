import json

from scripts import validate_account_sync


def test_account_sync_validation_script_writes_fixture_report(tmp_path, monkeypatch):
    output_path = tmp_path / "account-sync-validation.json"
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")

    report = validate_account_sync.run_account_sync_validation(
        exchanges=["okx", "binance"],
        mode="fixture",
        timestamp=1710000600,
        output_path=output_path,
    )

    assert report["status"] == "PASS"
    assert report["success"] is True
    assert report["live_orders_sent"] is False
    assert report["read_only"] is True
    assert report["failed_count"] == 0
    checks = {check["exchange"]: check for check in report["checks"]}
    assert checks["okx"]["snapshot_summary"]["parser"] == "okx_account_sync_v1"
    assert checks["okx"]["snapshot_summary"]["holding_symbols"] == ["BTC-USDT-SWAP"]
    assert checks["binance"]["snapshot_summary"]["parser"] == "binance_account_sync_v1"
    assert checks["binance"]["snapshot_summary"]["holding_symbols"] == ["BTCUSDT"]
    assert "snapshot_payload" not in checks["okx"]
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "PASS"


def test_account_sync_validation_live_mode_requires_explicit_gate(monkeypatch):
    monkeypatch.delenv("SMARTQTF_RUN_ACCOUNT_SYNC_TEST", raising=False)

    report = validate_account_sync.run_account_sync_validation(
        exchanges=["okx"],
        mode="live",
        timestamp=1710000600,
    )

    assert report["status"] == "SKIPPED"
    assert report["success"] is False
    assert report["checks"][0]["category"] == "manual_gate"
    assert "SMARTQTF_RUN_ACCOUNT_SYNC_TEST=1" in report["checks"][0]["message"]


def test_account_sync_validation_live_mode_requires_proxy_gate(monkeypatch):
    monkeypatch.setenv("SMARTQTF_RUN_ACCOUNT_SYNC_TEST", "1")
    monkeypatch.delenv("SMARTQTF_USE_PROXY", raising=False)

    report = validate_account_sync.run_account_sync_validation(
        exchanges=["okx"],
        mode="live",
        timestamp=1710000600,
    )

    assert report["status"] == "FAIL"
    assert report["success"] is False
    assert report["failed_count"] == 1
    assert report["checks"][0]["category"] == "proxy"
    assert "SMARTQTF_USE_PROXY=1" in report["checks"][0]["message"]
    assert report["checks"][0]["read_only"] is True
    assert report["checks"][0]["live_orders_sent"] is False
