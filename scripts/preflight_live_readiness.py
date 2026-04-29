#!/usr/bin/env python
import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.config import load_runtime_config
from quant.data.providers.okx_provider import OKXDataProvider
from quant.execution.broker import BrokerAdapter
from quant.orchestration import TradingRuntimeOrchestrator
from quant.registry import PluginKind, PluginRegistry
from quant.schemas import BrokerOrderResult, OrderStatus, PayloadSource


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None

    def to_payload(self):
        payload = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ReadOnlyBrokerAdapter(BrokerAdapter):
    """Broker adapter used by preflight to prove registration without live orders."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def place_order(self, request):
        return self._rejected(request)

    def cancel_order(self, client_order_id: str):
        return BrokerOrderResult(
            client_order_id=client_order_id,
            symbol="",
            side="buy",
            status=OrderStatus.REJECTED,
            requested_qty=0.0,
            rejection_code="preflight_read_only",
            rejection_reason="preflight broker never sends live orders",
        )

    def replace_order(self, request):
        return BrokerOrderResult(
            client_order_id=request.replacement_client_order_id,
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.REJECTED,
            requested_qty=request.quantity,
            rejection_code="preflight_read_only",
            rejection_reason="preflight broker never sends live orders",
            trace=request.trace,
        )

    def get_order(self, client_order_id: str):
        return self.cancel_order(client_order_id)

    def list_open_orders(self, symbol: str | None = None):
        return []

    @staticmethod
    def _rejected(request):
        return BrokerOrderResult(
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.REJECTED,
            requested_qty=request.quantity,
            rejection_code="preflight_read_only",
            rejection_reason="preflight broker never sends live orders",
            trace=request.trace,
        )


def run_preflight(config_path: str | Path, *, require_qtf: bool = True) -> dict[str, Any]:
    config_path = Path(config_path)
    checks: list[PreflightCheck] = []
    config = None

    checks.append(_check_qtf_environment(require_qtf=require_qtf))

    try:
        config = load_runtime_config(config_path)
        checks.append(
            PreflightCheck(
                "config_load",
                "PASS",
                "runtime config loaded",
                {"path": str(config_path), "source": _enum_value(config.source)},
            )
        )
    except Exception as exc:
        checks.append(
            PreflightCheck(
                "config_load",
                "FAIL",
                f"runtime config failed to load: {exc}",
                {"path": str(config_path)},
            )
        )
        return _report(config_path, checks)

    checks.extend(
        [
            _check_proxy_state(),
            _check_credential_state(config),
            _check_live_safety_state(config),
            _check_plugin_names(config),
            _check_runtime_construction(config),
        ]
    )
    return _report(config_path, checks)


def _check_qtf_environment(*, require_qtf: bool):
    conda_env = os.getenv("CONDA_DEFAULT_ENV")
    if conda_env == "QTF":
        return PreflightCheck("qtf_environment", "PASS", "QTF conda environment is active")
    status = "FAIL" if require_qtf else "WARN"
    return PreflightCheck(
        "qtf_environment",
        status,
        "QTF conda environment is not active",
        {"CONDA_DEFAULT_ENV": conda_env, "python": sys.executable},
    )


def _check_proxy_state():
    use_proxy = os.getenv("SMARTQTF_USE_PROXY", "").strip()
    proxy_vars = {
        name: os.getenv(name)
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
        if os.getenv(name)
    }
    if use_proxy == "1":
        return PreflightCheck(
            "proxy_state",
            "PASS",
            "SMARTQTF_USE_PROXY is enabled",
            {"proxy_env_vars": sorted(proxy_vars)},
        )
    return PreflightCheck(
        "proxy_state",
        "WARN",
        "SMARTQTF_USE_PROXY is not enabled; external checks may fail without proxy",
        {"SMARTQTF_USE_PROXY": use_proxy or None, "proxy_env_vars": sorted(proxy_vars)},
    )


def _check_credential_state(config):
    env_names = [
        value
        for key, value in config.broker.settings.items()
        if key.endswith("_env") and isinstance(value, str) and value.strip()
    ]
    if not env_names:
        return PreflightCheck("credential_state", "WARN", "no credential env vars are declared in broker settings")

    present = [name for name in env_names if os.getenv(name)]
    missing = [name for name in env_names if not os.getenv(name)]
    if not missing:
        return PreflightCheck(
            "credential_state",
            "PASS",
            "all declared credential env vars are present",
            {"present": present},
        )
    status = "FAIL" if config.source == PayloadSource.LIVE else "WARN"
    return PreflightCheck(
        "credential_state",
        status,
        "some declared credential env vars are missing",
        {"present": present, "missing": missing},
    )


def _check_live_safety_state(config):
    if config.source != PayloadSource.LIVE:
        return PreflightCheck("live_safety_state", "PASS", "non-live runtime does not require live safety gates")

    settings = config.broker.settings
    problems = []
    if settings.get("allow_live_orders") is not False:
        problems.append("broker.settings.allow_live_orders must be false for preflight")
    if settings.get("require_manual_preflight") is not True:
        problems.append("broker.settings.require_manual_preflight must be true")
    if config.risk.kill_switch_enabled is not True:
        problems.append("risk.kill_switch_enabled must be true before live readiness")
    if config.metadata.get("contains_real_credentials") is True:
        problems.append("example/config metadata should not embed real credentials")

    if problems:
        return PreflightCheck("live_safety_state", "FAIL", "live safety gates are not all enabled", {"problems": problems})
    return PreflightCheck("live_safety_state", "PASS", "live safety gates are enabled")


def _check_plugin_names(config):
    registry = _preflight_registry()
    missing = []
    data_plugins = sorted({market.provider for market in config.enabled_markets()})
    for plugin_name in data_plugins:
        if not _registry_has(registry, PluginKind.DATA, plugin_name):
            missing.append(f"data:{plugin_name}")
    if not _registry_has(registry, PluginKind.EXECUTION, config.broker.broker_plugin):
        missing.append(f"execution:{config.broker.broker_plugin}")

    if missing:
        return PreflightCheck("plugin_registration", "FAIL", "configured plugins are not registered", {"missing": missing})
    return PreflightCheck(
        "plugin_registration",
        "PASS",
        "configured data and execution plugins are registered for preflight",
        {"data": data_plugins, "execution": config.broker.broker_plugin},
    )


def _check_runtime_construction(config):
    try:
        runtime = TradingRuntimeOrchestrator.from_config(config, registry=_preflight_registry())
    except Exception as exc:
        return PreflightCheck("runtime_construction", "FAIL", f"runtime construction failed: {exc}")

    source = PayloadSource(config.source)
    if source not in runtime.handlers:
        return PreflightCheck(
            "runtime_construction",
            "FAIL",
            "runtime was constructed but did not register the requested source handler",
            {"source": source.value},
        )
    return PreflightCheck(
        "runtime_construction",
        "PASS",
        "runtime can be constructed with read-only preflight handlers",
        {"source": source.value},
    )


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _preflight_registry():
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "okx_public", lambda **_: OKXDataProvider())
    registry.register(PluginKind.EXECUTION, "okx_broker", lambda **_: ReadOnlyBrokerAdapter("okx_preflight"))
    registry.register(PluginKind.EXECUTION, "binance_broker", lambda **_: ReadOnlyBrokerAdapter("binance_preflight"))
    return registry


def _registry_has(registry, kind, name):
    try:
        registry.get(kind, name)
    except Exception:
        return False
    return True


def _report(config_path, checks):
    failed = [check for check in checks if check.status == "FAIL"]
    warnings = [check for check in checks if check.status == "WARN"]
    return {
        "config_path": str(config_path),
        "success": not failed,
        "failed_count": len(failed),
        "warning_count": len(warnings),
        "checks": [check.to_payload() for check in checks],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run read-only SmartQTF live preflight checks.")
    parser.add_argument(
        "--config",
        default="config/examples/live-runtime.example.json",
        help="Runtime config JSON path.",
    )
    parser.add_argument(
        "--allow-non-qtf",
        action="store_true",
        help="Downgrade missing QTF conda environment from FAIL to WARN.",
    )
    args = parser.parse_args(argv)

    report = run_preflight(args.config, require_qtf=not args.allow_non_qtf)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
