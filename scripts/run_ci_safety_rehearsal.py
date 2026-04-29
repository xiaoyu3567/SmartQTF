#!/usr/bin/env python
import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.config import load_runtime_config
from quant.data.schemas.market import Kline
from quant.orchestration import TradingRuntimeOrchestrator
from quant.registry import PluginKind, PluginRegistry
from scripts.build_production_rehearsal_report import (
    DEFAULT_ARTIFACT_DIR,
    build_production_rehearsal_report,
    write_rehearsal_artifacts,
)


class CISafeMarketDataProvider:
    """Deterministic provider for CI rehearsals; never calls external exchanges."""

    def get_klines(self, symbol, timeframe):
        closes = [100.0, 99.0, 98.0, 97.0, 104.0, 105.0]
        return [
            Kline(
                timestamp=1700000000 + index * 60,
                open=close,
                high=close + 0.8,
                low=close - 0.8,
                close=close,
                volume=1000.0 + index,
            )
            for index, close in enumerate(closes)
        ]

    def get_trades(self, symbol):
        return []


def run_ci_safety_rehearsal(
    config_path: str | Path = "config/examples/live-runtime.example.json",
    *,
    output_dir: str | Path = DEFAULT_ARTIFACT_DIR,
    artifact_stem: str = "ci-safety-rehearsal",
    dry_run_stem: str = "ci-live-dry-run",
    require_qtf: bool = True,
):
    config_path = Path(config_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_runtime_config(config_path)
    dry_run_path = output_dir / f"{dry_run_stem}.json"

    with _placeholder_credential_env(config):
        dry_run_report = _run_stubbed_live_dry_run(config_path, run_id="ci-live-dry-run")
        dry_run_path.write_text(
            json.dumps(dry_run_report.to_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report = build_production_rehearsal_report(
            config_path,
            run_connectivity=False,
            dry_run_report_path=dry_run_path,
            require_qtf=require_qtf,
        )

    report.metadata.update(
        {
            "ci_safe": True,
            "external_exchange_access": False,
            "stub_data_provider": CISafeMarketDataProvider.__name__,
            "dry_run_report_path": str(dry_run_path),
        }
    )
    artifact_paths = write_rehearsal_artifacts(report, output_dir, stem=artifact_stem)
    return report, artifact_paths, dry_run_path


def _run_stubbed_live_dry_run(config_path: Path, *, run_id: str):
    config = load_runtime_config(config_path)
    runtime = TradingRuntimeOrchestrator.from_config_file_dry_run(
        config_path,
        registry=_ci_dry_run_registry(),
    )
    market = config.enabled_markets()[0]
    return runtime.run(
        {
            "source": config.source,
            "symbol": market.symbol,
            "timeframe": market.timeframe,
            "index": 4,
            "run_id": run_id,
        }
    )


def _ci_dry_run_registry():
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "okx_public", lambda **_: CISafeMarketDataProvider())
    registry.register(PluginKind.DATA, "binance_public", lambda **_: CISafeMarketDataProvider())
    return registry


@contextmanager
def _placeholder_credential_env(config):
    credential_env_names = sorted(
        {
            value
            for key, value in config.broker.settings.items()
            if key.endswith("_env") and isinstance(value, str) and value.strip()
        }
    )
    previous = {name: os.environ.get(name) for name in credential_env_names}
    try:
        for name in credential_env_names:
            os.environ.setdefault(name, "CI_PLACEHOLDER_NOT_A_REAL_SECRET")
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a SmartQTF CI-safe rehearsal without exchange access or live orders."
    )
    parser.add_argument("--config", default="config/examples/live-runtime.example.json", help="Runtime config JSON path.")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_ARTIFACT_DIR),
        help="Directory for CI rehearsal JSON/Markdown artifacts.",
    )
    parser.add_argument("--artifact-stem", default="ci-safety-rehearsal", help="Artifact filename stem.")
    parser.add_argument("--dry-run-stem", default="ci-live-dry-run", help="Dry-run report filename stem.")
    parser.add_argument(
        "--allow-non-qtf",
        action="store_true",
        help="Downgrade missing QTF conda environment from FAIL to WARN in preflight.",
    )
    args = parser.parse_args(argv)

    report, artifact_paths, dry_run_path = run_ci_safety_rehearsal(
        args.config,
        output_dir=args.output_dir,
        artifact_stem=args.artifact_stem,
        dry_run_stem=args.dry_run_stem,
        require_qtf=not args.allow_non_qtf,
    )
    payload = report.to_payload()
    payload["metadata"]["artifact_paths"] = artifact_paths
    payload["metadata"]["dry_run_report_path"] = str(dry_run_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
