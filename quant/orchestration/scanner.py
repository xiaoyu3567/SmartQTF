from uuid import uuid4

from quant.config import RuntimeConfig, load_runtime_config
from quant.logging.pipeline_report import PipelineReportStore
from quant.orchestration.runtime import TradingRuntimeOrchestrator
from quant.schemas import PayloadSource, PipelineSymbolRunRequest, UniverseSnapshot


class RuntimeScanScheduler:
    """Configuration-driven scan scheduler for one-shot heartbeat execution."""

    def __init__(self, runtime, config, *, report_dir=None, account_sync=None, universe_provider=None):
        self.runtime = runtime
        self.config = self._runtime_config(config)
        self.report_store = PipelineReportStore(report_dir or self.config.logging.pipeline_report_dir)
        self.report_dir = self.report_store.report_dir
        self.account_sync = account_sync
        self.universe_provider = universe_provider
        self._last_account_snapshot = None
        self._last_universe_snapshot = None
        self._last_universe_symbol_list = []
        self.last_scan_at = None

    @classmethod
    def from_config(cls, config, *, registry=None, report_dir=None, account_sync=None, universe_provider=None):
        runtime_config = cls._runtime_config(config)
        if runtime_config.source == PayloadSource.LIVE:
            runtime = TradingRuntimeOrchestrator.from_config_dry_run(runtime_config, registry=registry)
        else:
            runtime = TradingRuntimeOrchestrator.from_config(runtime_config, registry=registry)
        return cls(
            runtime,
            runtime_config,
            report_dir=report_dir,
            account_sync=account_sync,
            universe_provider=universe_provider,
        )

    @classmethod
    def from_config_file(cls, path, *, registry=None, report_dir=None, account_sync=None, universe_provider=None):
        return cls.from_config(
            load_runtime_config(path),
            registry=registry,
            report_dir=report_dir,
            account_sync=account_sync,
            universe_provider=universe_provider,
        )

    def should_run(self, now):
        if not self.config.scan.enabled:
            return False
        if self.last_scan_at is None:
            return True
        return now - self.last_scan_at >= self.config.scan.interval_seconds

    def run_due(self, *, now, index=None, batch_id=None):
        if not self.should_run(now):
            return None
        return self.run_once(requested_at=now, index=index, batch_id=batch_id)

    def run_once(self, *, requested_at, index=None, batch_id=None):
        requests = self.build_requests(index=index)
        if not requests:
            raise ValueError("scan scheduler requires at least one symbol request")

        handler = self._handler()
        batch = handler.run_symbols(
            requests,
            batch_id=batch_id or self._batch_id(requested_at),
            requested_at=requested_at,
        )
        batch = self.write_batch_report(batch)
        self.last_scan_at = requested_at
        return batch

    def build_requests(self, *, index=None):
        enabled_markets = self.config.enabled_markets()
        market_by_symbol = {}
        for market in enabled_markets:
            market_by_symbol.setdefault(market.symbol, market)

        scan_sources = {}
        ordered_symbols = []

        def add_symbol(symbol, source):
            if symbol not in scan_sources:
                scan_sources[symbol] = []
                ordered_symbols.append(symbol)
            if source not in scan_sources[symbol]:
                scan_sources[symbol].append(source)

        candidate_symbols = self.config.scan.candidate_symbols
        if candidate_symbols:
            for symbol in candidate_symbols:
                add_symbol(symbol, "candidate")

        universe_symbols = self._universe_symbols()
        for symbol in universe_symbols:
            add_symbol(symbol, "universe")

        if not candidate_symbols and not universe_symbols:
            for market in enabled_markets:
                add_symbol(market.symbol, "configured_market")

        for symbol in self.config.scan.holding_symbols:
            add_symbol(symbol, "holding")

        for symbol in self._account_holding_symbols():
            add_symbol(symbol, "account_holding")

        fallback_timeframe = self.config.scan.default_timeframe
        if fallback_timeframe is None and enabled_markets:
            fallback_timeframe = enabled_markets[0].timeframe

        requests = []
        for symbol in ordered_symbols:
            market = market_by_symbol.get(symbol)
            timeframe = market.timeframe if market is not None else fallback_timeframe
            if timeframe is None:
                raise ValueError(f"no timeframe configured for scan symbol {symbol}")
            requests.append(
                PipelineSymbolRunRequest(
                    symbol=symbol,
                    timeframe=timeframe,
                    index=index,
                    metadata={
                        "scan_sources": list(scan_sources[symbol]),
                        "scan_interval_seconds": self.config.scan.interval_seconds,
                    },
                )
            )
        return requests

    def write_batch_report(self, batch):
        report_path = self.report_store.batch_report_path(batch.batch_id)
        latest_path = self.report_store.latest_batch_path
        metadata = dict(batch.metadata)
        metadata["scan_scheduler"] = {
            "enabled": self.config.scan.enabled,
            "interval_seconds": self.config.scan.interval_seconds,
            "candidate_symbols": list(self.config.scan.candidate_symbols),
            "holding_symbols": list(self.config.scan.holding_symbols),
            "account_holding_symbols": self._last_account_holding_symbols(),
            "account_sync_observed_at": self._last_account_sync_observed_at(),
            "universe_enabled": self.config.scan.universe_enabled,
            "universe_snapshot_id": self._last_universe_snapshot_id(),
            "universe_as_of_timestamp": self._last_universe_as_of_timestamp(),
            "universe_source": self._last_universe_source(),
            "universe_symbols": self._last_universe_symbols(),
            "universe_rejected_count": self._last_universe_rejected_count(),
            "universe_max_symbols": self.config.scan.universe_max_symbols,
            "report_path": str(report_path),
            "latest_report_path": str(latest_path),
        }
        reports = [self.report_store.write_run_report(report) for report in batch.reports]
        batch = self._copy_batch(batch, metadata=metadata, reports=reports)
        return self.report_store.write_batch_report(batch)

    def _handler(self):
        source = PayloadSource(self.config.source)
        handler = self.runtime.handlers.get(source)
        if handler is None:
            raise ValueError(f"{source.value} runtime handler is not configured")
        if not hasattr(handler, "run_symbols"):
            raise TypeError("scan scheduler requires a runtime handler with run_symbols()")
        return handler

    def _account_holding_symbols(self):
        if self.account_sync is None:
            self._last_account_snapshot = None
            return []

        snapshot_getter = getattr(self.account_sync, "fetch_snapshot", None)
        if snapshot_getter is None:
            snapshot_getter = getattr(self.account_sync, "get_account_snapshot", None)
        if snapshot_getter is not None:
            self._last_account_snapshot = snapshot_getter()
            return list(self._last_account_snapshot.holding_symbols)

        list_holding_symbols = getattr(self.account_sync, "list_holding_symbols", None)
        if list_holding_symbols is None:
            raise TypeError("account_sync must provide fetch_snapshot(), get_account_snapshot(), or list_holding_symbols()")
        self._last_account_snapshot = None
        return list(list_holding_symbols())

    def _universe_symbols(self):
        if not self.config.scan.universe_enabled:
            self._last_universe_snapshot = None
            self._last_universe_symbol_list = []
            return []

        provider = self._universe_provider()
        if provider is None or not hasattr(provider, "discover_universe"):
            raise TypeError("universe scan requires a provider with discover_universe()")

        snapshot = provider.discover_universe(filter_config=self.config.scan.universe_filter)
        if not isinstance(snapshot, UniverseSnapshot):
            snapshot = UniverseSnapshot.from_payload(snapshot)

        symbols = [instrument.symbol for instrument in snapshot.instruments]
        if self.config.scan.universe_max_symbols is not None:
            symbols = symbols[: self.config.scan.universe_max_symbols]

        self._last_universe_snapshot = snapshot
        self._last_universe_symbol_list = list(symbols)
        return symbols

    def _universe_provider(self):
        if self.universe_provider is not None:
            return self.universe_provider
        handler = self._handler()
        return getattr(handler, "provider", None)

    def _last_account_holding_symbols(self):
        if self._last_account_snapshot is None:
            return []
        return list(self._last_account_snapshot.holding_symbols)

    def _last_account_sync_observed_at(self):
        if self._last_account_snapshot is None:
            return None
        return self._last_account_snapshot.observed_at

    def _last_universe_snapshot_id(self):
        if self._last_universe_snapshot is None:
            return None
        return self._last_universe_snapshot.snapshot_id

    def _last_universe_as_of_timestamp(self):
        if self._last_universe_snapshot is None:
            return None
        return self._last_universe_snapshot.as_of_timestamp

    def _last_universe_source(self):
        if self._last_universe_snapshot is None:
            return None
        return self._last_universe_snapshot.source

    def _last_universe_symbols(self):
        return list(self._last_universe_symbol_list)

    def _last_universe_rejected_count(self):
        if self._last_universe_snapshot is None:
            return 0
        return len(self._last_universe_snapshot.rejected)

    def _batch_id(self, requested_at):
        safe_name = self._safe_name(self.config.name)
        return f"{safe_name}:scan:{requested_at}:{uuid4().hex}"

    @staticmethod
    def _copy_batch(batch, metadata=None, reports=None):
        update = {}
        if metadata is not None:
            update["metadata"] = metadata
        if reports is not None:
            update["reports"] = reports
        if hasattr(batch, "model_copy"):
            return batch.model_copy(update=update)
        return batch.copy(update=update)

    @staticmethod
    def _runtime_config(config):
        if isinstance(config, RuntimeConfig):
            return config
        return RuntimeConfig.from_payload(config)

    @staticmethod
    def _safe_name(value):
        return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value))
