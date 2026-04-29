import json
from pathlib import Path

from quant.config import BrokerConfig, MarketConfig, RuntimeConfig, StrategyBinding
from quant.data.schemas.market import Kline
from quant.orchestration import RuntimeScanScheduler
from quant.registry import PluginKind, PluginRegistry
from quant.schemas import (
    AccountPositionSnapshot,
    AccountSyncSnapshot,
    PayloadSource,
    PipelineBatchRunReport,
    PipelineRunReport,
    PositionSide,
    UniverseInstrument,
    UniverseSnapshot,
)


class SelectiveProvider:
    def get_klines(self, symbol, timeframe):
        if symbol == "EMPTY":
            return []
        closes = [10.0, 9.0, 8.0, 7.0, 12.0]
        return [
            Kline(
                timestamp=1700000000 + index * 60,
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1000.0 + index,
            )
            for index, close in enumerate(closes)
        ]

    def get_trades(self, symbol):
        return []


class StaticAccountSync:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get_account_snapshot(self):
        return self.snapshot


class StaticUniverseProvider:
    def __init__(self, symbols):
        self.symbols = list(symbols)
        self.last_filter_config = None

    def discover_universe(self, filter_config):
        self.last_filter_config = filter_config
        return UniverseSnapshot(
            snapshot_id="okx-spot-universe-1710000000",
            venue=filter_config.venue,
            instrument_type=filter_config.instrument_type,
            as_of_timestamp=1710000000,
            source="unit_test_universe",
            filters=filter_config,
            instruments=[
                UniverseInstrument(
                    symbol=symbol,
                    venue=filter_config.venue,
                    instrument_type=filter_config.instrument_type,
                    base_currency=symbol.replace("USDT", ""),
                    quote_currency="USDT",
                    status="live",
                    quantity_step=0.001,
                    min_quantity=0.001,
                    turnover_24h=1000000.0 - index,
                )
                for index, symbol in enumerate(self.symbols)
            ],
        )


def make_registry():
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "selective", lambda: SelectiveProvider())
    return registry


def make_config(tmp_path):
    symbols = ["BTCUSDT", "ETHUSDT", "EMPTY"]
    return RuntimeConfig(
        name="scan-runtime",
        source=PayloadSource.PAPER,
        markets=[
            MarketConfig(symbol=symbol, timeframe="1m", provider="selective")
            for symbol in symbols
        ],
        strategies=[
            StrategyBinding(
                symbol=symbol,
                strategy="ma_crossover",
                route="default",
                parameters={"fast_window": 2, "slow_window": 3},
            )
            for symbol in symbols
        ],
        broker=BrokerConfig(mode=PayloadSource.PAPER, broker_plugin="simulated"),
        scan={
            "enabled": True,
            "interval_seconds": 600,
            "candidate_symbols": ["BTCUSDT", "ETHUSDT"],
            "holding_symbols": ["ETHUSDT", "EMPTY"],
        },
        logging={"pipeline_report_dir": str(tmp_path / "pipeline-runs")},
    )


def test_scan_scheduler_builds_deduped_candidate_and_holding_requests(tmp_path):
    scheduler = RuntimeScanScheduler.from_config(make_config(tmp_path), registry=make_registry())

    requests = scheduler.build_requests(index=4)

    assert [request.symbol for request in requests] == ["BTCUSDT", "ETHUSDT", "EMPTY"]
    assert [request.timeframe for request in requests] == ["1m", "1m", "1m"]
    assert requests[0].metadata["scan_sources"] == ["candidate"]
    assert requests[1].metadata["scan_sources"] == ["candidate", "holding"]
    assert requests[2].metadata["scan_sources"] == ["holding"]


def test_scan_scheduler_injects_universe_snapshot_symbols(tmp_path):
    config = make_config(tmp_path)
    update = {
        "universe_enabled": True,
        "universe_max_symbols": 2,
    }
    if hasattr(config.scan, "model_copy"):
        config.scan = config.scan.model_copy(update=update)
    else:
        config.scan = config.scan.copy(update=update)
    universe_provider = StaticUniverseProvider(["SOLUSDT", "BTCUSDT", "XRPUSDT"])
    scheduler = RuntimeScanScheduler.from_config(
        config,
        registry=make_registry(),
        universe_provider=universe_provider,
    )

    requests = scheduler.build_requests(index=4)

    assert universe_provider.last_filter_config.venue == "okx"
    assert [request.symbol for request in requests] == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "EMPTY"]
    assert requests[0].metadata["scan_sources"] == ["candidate", "universe"]
    assert requests[2].metadata["scan_sources"] == ["universe"]
    assert scheduler._last_universe_symbols() == ["SOLUSDT", "BTCUSDT"]


def test_scan_scheduler_runs_due_batch_and_persists_report(tmp_path):
    scheduler = RuntimeScanScheduler.from_config(make_config(tmp_path), registry=make_registry())

    batch = scheduler.run_due(now=1700000600, index=4, batch_id="scan-001")

    assert batch is not None
    assert batch.batch_id == "scan-001"
    assert batch.success is False
    assert [report.context.symbol for report in batch.reports] == ["BTCUSDT", "ETHUSDT", "EMPTY"]
    assert batch.reports[0].success is True
    assert batch.reports[1].success is True
    assert batch.reports[2].success is False
    assert "provider returned no klines" in batch.errors[0]
    assert scheduler.last_scan_at == 1700000600

    report_path = Path(batch.metadata["scan_scheduler"]["report_path"])
    latest_path = Path(batch.metadata["scan_scheduler"]["latest_report_path"])
    assert report_path.exists()
    assert latest_path.exists()
    restored = PipelineBatchRunReport.from_payload(json.loads(report_path.read_text(encoding="utf-8")))
    assert restored.batch_id == batch.batch_id
    assert json.loads(latest_path.read_text(encoding="utf-8"))["batch_id"] == "scan-001"
    run_artifact = batch.reports[0].metadata["pipeline_report_artifact"]
    run_path = Path(run_artifact["report_path"])
    restored_run = PipelineRunReport.from_payload(json.loads(run_path.read_text(encoding="utf-8")))
    assert run_artifact["type"] == "run"
    assert run_path.exists()
    assert restored_run.context.run_id == "scan-001:0:BTCUSDT:1m"

    assert scheduler.run_due(now=1700000800, index=4, batch_id="scan-too-soon") is None
    next_batch = scheduler.run_due(now=1700001200, index=4, batch_id="scan-002")
    assert next_batch.batch_id == "scan-002"


def test_scan_scheduler_can_fall_back_to_configured_markets(tmp_path):
    config = make_config(tmp_path)
    update = {"candidate_symbols": [], "holding_symbols": []}
    if hasattr(config.scan, "model_copy"):
        config.scan = config.scan.model_copy(update=update)
    else:
        config.scan = config.scan.copy(update=update)
    scheduler = RuntimeScanScheduler.from_config(config, registry=make_registry())

    requests = scheduler.build_requests(index=4)

    assert [request.symbol for request in requests] == ["BTCUSDT", "ETHUSDT", "EMPTY"]
    assert all(request.metadata["scan_sources"] == ["configured_market"] for request in requests)


def test_scan_scheduler_merges_account_sync_holding_symbols(tmp_path):
    snapshot = AccountSyncSnapshot(
        account_id="acct-001",
        source=PayloadSource.LIVE,
        observed_at=1710000600,
        equity=10000.0,
        positions=[
            AccountPositionSnapshot(
                symbol="ETHUSDT",
                side=PositionSide.LONG,
                quantity=1.0,
                avg_price=3000.0,
            ),
            AccountPositionSnapshot(
                symbol="SOLUSDT",
                side=PositionSide.LONG,
                quantity=10.0,
                avg_price=100.0,
            ),
        ],
    )
    scheduler = RuntimeScanScheduler.from_config(
        make_config(tmp_path),
        registry=make_registry(),
        account_sync=StaticAccountSync(snapshot),
    )

    requests = scheduler.build_requests(index=4)

    assert [request.symbol for request in requests] == ["BTCUSDT", "ETHUSDT", "EMPTY", "SOLUSDT"]
    assert requests[1].metadata["scan_sources"] == ["candidate", "holding", "account_holding"]
    assert requests[3].metadata["scan_sources"] == ["account_holding"]

    batch = scheduler.run_due(now=1700000600, index=4, batch_id="scan-with-account")
    assert batch.metadata["scan_scheduler"]["account_holding_symbols"] == ["ETHUSDT", "SOLUSDT"]
    assert batch.metadata["scan_scheduler"]["account_sync_observed_at"] == 1710000600


def test_scan_scheduler_persists_universe_snapshot_metadata(tmp_path):
    config = make_config(tmp_path)
    update = {"candidate_symbols": [], "holding_symbols": [], "universe_enabled": True, "universe_max_symbols": 2}
    if hasattr(config.scan, "model_copy"):
        config.scan = config.scan.model_copy(update=update)
    else:
        config.scan = config.scan.copy(update=update)
    scheduler = RuntimeScanScheduler.from_config(
        config,
        registry=make_registry(),
        universe_provider=StaticUniverseProvider(["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
    )

    batch = scheduler.run_due(now=1700000600, index=4, batch_id="scan-with-universe")

    metadata = batch.metadata["scan_scheduler"]
    assert metadata["universe_enabled"] is True
    assert metadata["universe_snapshot_id"] == "okx-spot-universe-1710000000"
    assert metadata["universe_as_of_timestamp"] == 1710000000
    assert metadata["universe_source"] == "unit_test_universe"
    assert metadata["universe_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert metadata["universe_rejected_count"] == 0
    assert metadata["universe_max_symbols"] == 2


def test_scan_scheduler_uses_live_dry_run_for_live_config(tmp_path):
    created = []
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "live_selective", lambda: SelectiveProvider())
    registry.register(PluginKind.EXECUTION, "real_live_broker", lambda **_: created.append("broker"))
    config = RuntimeConfig(
        name="live-scan-runtime",
        source=PayloadSource.LIVE,
        markets=[MarketConfig(symbol="BTC-USDT", timeframe="1m", provider="live_selective")],
        strategies=[
            StrategyBinding(
                symbol="BTC-USDT",
                strategy="ma_crossover",
                route="default",
                parameters={"fast_window": 2, "slow_window": 3},
            )
        ],
        broker=BrokerConfig(
            mode=PayloadSource.LIVE,
            account_id="live-account",
            broker_plugin="real_live_broker",
            settings={"allow_live_orders": False},
        ),
        scan={"candidate_symbols": ["BTC-USDT"], "interval_seconds": 600},
        logging={"pipeline_report_dir": str(tmp_path / "live-pipeline-runs")},
    )

    scheduler = RuntimeScanScheduler.from_config(config, registry=registry)
    batch = scheduler.run_once(requested_at=1700000600, index=4, batch_id="live-scan-001")

    assert created == []
    assert batch.success is True
    execution_result = batch.reports[0].final_output["execution_result"]
    assert execution_result["dry_run"] is True
    assert execution_result["live_orders_sent"] is False
