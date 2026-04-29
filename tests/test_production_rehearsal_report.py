import json

from scripts import build_production_rehearsal_report as rehearsal


def _write_config(tmp_path):
    payload = {
        "name": "live-rehearsal-test",
        "source": "live",
        "markets": [
            {
                "symbol": "BTC-USDT",
                "timeframe": "1m",
                "enabled": True,
                "provider": "okx_public",
            }
        ],
        "strategies": [
            {
                "symbol": "BTC-USDT",
                "strategy": "ma_crossover",
                "route": "default",
                "parameters": {"fast_window": 3, "slow_window": 5},
            }
        ],
        "risk": {
            "risk_plugin": "default",
            "kill_switch_enabled": True,
            "max_position_size": 0.1,
            "max_drawdown": 0.05,
        },
        "broker": {
            "mode": "live",
            "broker_plugin": "okx_broker",
            "account_id": "rehearsal-account",
            "settings": {
                "allow_live_orders": False,
                "require_manual_preflight": True,
                "api_key_env": "OKX_API_KEY",
                "api_secret_env": "OKX_API_SECRET",
                "api_passphrase_env": "OKX_API_PASSPHRASE",
            },
        },
        "metadata": {"contains_real_credentials": False},
    }
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def test_rehearsal_report_aggregates_preflight_connectivity_and_dry_run_skip(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")
    monkeypatch.setattr(
        rehearsal,
        "run_diagnostics",
        lambda **_: {
            "success": True,
            "failed_count": 0,
            "warning_count": 0,
            "proxy": {"enabled": True},
            "checks": [
                {
                    "exchange": "okx",
                    "scope": "public",
                    "status": "PASS",
                    "category": "ok",
                    "message": "public endpoint reachable",
                    "latency_ms": 12.3,
                }
            ],
        },
    )

    report = rehearsal.build_production_rehearsal_report(config_path, generated_at=1700000000)
    payload = report.to_payload()
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["success"] is True
    assert payload["metadata"]["contains_real_credentials"] is False
    assert payload["metadata"]["live_orders_sent"] is False
    assert payload["preflight_summary"]["failed_count"] == 0
    assert payload["connectivity_summary"]["check_count"] == 1
    assert checks["connectivity:okx:public"]["status"] == "PASS"
    assert checks["dry_run:report"]["status"] == "SKIPPED"


def test_rehearsal_report_fails_when_preflight_fails(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    monkeypatch.setattr(
        rehearsal,
        "run_diagnostics",
        lambda **_: {
            "success": True,
            "failed_count": 0,
            "warning_count": 0,
            "proxy": {"enabled": False},
            "checks": [],
        },
    )

    report = rehearsal.build_production_rehearsal_report(config_path, generated_at=1700000000)
    checks = {check.name: check for check in report.checks}

    assert report.success is False
    assert checks["preflight:qtf_environment"].status == "FAIL"


def test_rehearsal_report_can_summarize_supplied_dry_run_payload(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    dry_run_path = tmp_path / "dry-run.json"
    dry_run_path.write_text(
        json.dumps(
            {
                "context": {"run_id": "dry-run-1", "source": "live"},
                "success": False,
                "stages": [
                    {"stage": "data", "status": "succeeded"},
                    {"stage": "execution", "status": "rejected"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")

    report = rehearsal.build_production_rehearsal_report(
        config_path,
        run_connectivity=False,
        dry_run_report_path=dry_run_path,
        generated_at=1700000000,
    )

    assert report.success is False
    assert report.dry_run_summary["failed_stages"] == ["execution"]


def test_rehearsal_artifacts_write_stable_json_and_markdown(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    output_dir = tmp_path / "artifacts"
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")
    monkeypatch.setattr(
        rehearsal,
        "run_diagnostics",
        lambda **_: {
            "success": True,
            "failed_count": 0,
            "warning_count": 0,
            "proxy": {"enabled": True},
            "checks": [],
        },
    )

    report = rehearsal.build_production_rehearsal_report(config_path, generated_at=1700000000)
    paths = rehearsal.write_rehearsal_artifacts(report, output_dir)

    assert (output_dir / "production-rehearsal-1700000000.json").exists()
    assert (output_dir / "production-rehearsal-1700000000.md").exists()
    assert (output_dir / "latest.json").exists()
    assert (output_dir / "latest.md").exists()
    assert json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))["metadata"][
        "artifact_paths"
    ] == paths
    markdown = (output_dir / "latest.md").read_text(encoding="utf-8")
    assert "SmartQTF Production Rehearsal Report" in markdown
    assert "Failure Reasons" in markdown
    assert "contains_real_credentials=`False`" in markdown


def test_rehearsal_cli_writes_default_artifacts(tmp_path, monkeypatch, capsys):
    config_path = _write_config(tmp_path)
    output_dir = tmp_path / "cli-artifacts"
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")
    monkeypatch.setattr(
        rehearsal,
        "run_diagnostics",
        lambda **_: {
            "success": True,
            "failed_count": 0,
            "warning_count": 0,
            "proxy": {"enabled": True},
            "checks": [],
        },
    )

    exit_code = rehearsal.main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--artifact-stem",
            "stable-rehearsal",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "stable-rehearsal.json").exists()
    assert (output_dir / "stable-rehearsal.md").exists()
    assert (output_dir / "latest.json").exists()
    assert (output_dir / "latest.md").exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["metadata"]["artifact_paths"]["json_path"].endswith("stable-rehearsal.json")
