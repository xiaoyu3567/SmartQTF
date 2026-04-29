#!/usr/bin/env python
import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.config import load_runtime_config
from quant.data.providers.okx_provider import OKXDataProvider
from quant.orchestration import TradingRuntimeOrchestrator
from quant.registry import PluginKind, PluginRegistry


def run_live_dry_run(
    config_path: str | Path,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    index: int | None = None,
    run_id: str | None = None,
):
    loaded_config = load_runtime_config(config_path)
    runtime = TradingRuntimeOrchestrator.from_config_file_dry_run(
        config_path,
        registry=_dry_run_registry(),
    )
    request = {
        "source": loaded_config.source,
        "symbol": symbol,
        "timeframe": timeframe,
        "index": index,
        "run_id": run_id,
    }
    request = {key: value for key, value in request.items() if value is not None}
    if "symbol" not in request or "timeframe" not in request:
        market = loaded_config.enabled_markets()[0]
        request.setdefault("symbol", market.symbol)
        request.setdefault("timeframe", market.timeframe)
    return runtime.run(request)


def _dry_run_registry():
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "okx_public", lambda **_: OKXDataProvider())
    return registry


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a SmartQTF live dry-run without sending broker orders.")
    parser.add_argument("--config", default="config/examples/live-runtime.example.json", help="Runtime config JSON path.")
    parser.add_argument("--symbol", help="Symbol to dry-run. Defaults to the first enabled market in the config.")
    parser.add_argument("--timeframe", help="Timeframe to dry-run. Defaults to the first enabled market in the config.")
    parser.add_argument("--index", type=int, help="Kline index to evaluate. Defaults to the latest provider bar.")
    parser.add_argument("--run-id", help="Optional PipelineRunReport run_id.")
    parser.add_argument("--output", help="Optional JSON output path.")
    args = parser.parse_args(argv)

    report = run_live_dry_run(
        args.config,
        symbol=args.symbol,
        timeframe=args.timeframe,
        index=args.index,
        run_id=args.run_id,
    )
    text = json.dumps(report.to_payload(), ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
