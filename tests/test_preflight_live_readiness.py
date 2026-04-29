import json
from pathlib import Path

from scripts.preflight_live_readiness import run_preflight


def _write_config(tmp_path, overrides=None):
    payload = {
        "name": "live-preflight-test",
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
            "account_id": "preflight-account",
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
    if overrides:
        _deep_update(payload, overrides)
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def _deep_update(target, overrides):
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _checks_by_name(report):
    return {check["name"]: check for check in report["checks"]}


def test_preflight_passes_with_qtf_proxy_credentials_and_read_only_live_gates(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")

    report = run_preflight(config_path)
    checks = _checks_by_name(report)

    assert report["success"] is True
    assert checks["qtf_environment"]["status"] == "PASS"
    assert checks["credential_state"]["status"] == "PASS"
    assert checks["live_safety_state"]["status"] == "PASS"
    assert checks["plugin_registration"]["status"] == "PASS"
    assert checks["runtime_construction"]["status"] == "PASS"


def test_preflight_fails_when_qtf_environment_is_not_active(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")

    report = run_preflight(config_path)

    assert report["success"] is False
    assert _checks_by_name(report)["qtf_environment"]["status"] == "FAIL"


def test_preflight_fails_live_config_missing_credentials_and_safety_gates(tmp_path, monkeypatch):
    config_path = _write_config(
        tmp_path,
        {
            "risk": {"kill_switch_enabled": False},
            "broker": {"settings": {"allow_live_orders": True}},
            "metadata": {"contains_real_credentials": True},
        },
    )
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_API_SECRET", raising=False)
    monkeypatch.delenv("OKX_API_PASSPHRASE", raising=False)

    report = run_preflight(config_path)
    checks = _checks_by_name(report)

    assert report["success"] is False
    assert checks["credential_state"]["status"] == "FAIL"
    assert checks["live_safety_state"]["status"] == "FAIL"
    assert "risk.kill_switch_enabled" in " ".join(checks["live_safety_state"]["details"]["problems"])


def test_preflight_fails_unknown_live_plugins(tmp_path, monkeypatch):
    config_path = _write_config(
        tmp_path,
        {
            "markets": [{"symbol": "BTC-USDT", "timeframe": "1m", "provider": "unknown_data"}],
            "broker": {"broker_plugin": "unknown_broker"},
        },
    )
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")

    report = run_preflight(config_path)
    check = _checks_by_name(report)["plugin_registration"]

    assert report["success"] is False
    assert check["status"] == "FAIL"
    assert "data:unknown_data" in check["details"]["missing"]
    assert "execution:unknown_broker" in check["details"]["missing"]
