from quant.account.models.crypto import CryptoAccount
from quant.backtest.engine import BacktestEngine
from quant.config import BrokerConfig, MarketConfig, RuntimeConfig, StrategyBinding
from quant.data.schemas.market import Kline
from quant.execution.engine import ExecutionEngine
from quant.orchestration import TradingRuntimeOrchestrator
from quant.registry import PluginKind, PluginRegistry
from quant.risk.risk_manager import RiskManager
from quant.schemas import (
    OrderLifecycleContract,
    PayloadSource,
    PipelineRuntimeRequest,
)
from quant.strategy.ma_crossover import MACrossoverStrategy


class CrossingProvider:
    def get_klines(self, symbol, timeframe):
        closes = [10.0, 9.0, 8.0, 7.0, 12.0]
        return [_kline(index, close) for index, close in enumerate(closes)]

    def get_trades(self, symbol):
        return []


def test_backtest_engine_fills_use_shared_order_lifecycle_contract():
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(execution_delay=0, seed=1, account=account)
    engine = BacktestEngine(
        MACrossoverStrategy(),
        execution,
        account,
        risk=RiskManager(max_position_pct=0.1, symbol="BTCUSDT"),
        fast_window=1,
        slow_window=2,
    )

    result = engine.run([_kline(index, close) for index, close in enumerate([100, 99, 101, 103, 100, 98])])

    fill = result["fills"][0]
    contract = OrderLifecycleContract.from_payload(fill["order_lifecycle"])
    assert fill["order_lifecycle_contract"] == "order_lifecycle_v1"
    assert fill["lifecycle_state"] == contract.lifecycle_state
    assert result["order_lifecycle_reports"][0] == fill["order_lifecycle"]
    assert contract.source == PayloadSource.BACKTEST
    assert contract.execution_mode == "backtest"
    assert _value(contract.order_status) == "filled"
    assert contract.lifecycle_state == "FILLED"
    assert contract.lifecycle_path == ["CREATED", "VALIDATED", "SUBMITTING", "SUBMITTED", "FILLED"]
    assert contract.safety_flags == {
        "backtest": True,
        "paper": False,
        "live": False,
        "simulated": True,
        "dry_run": False,
        "broker_called": False,
        "live_orders_sent": False,
    }


def test_backtest_paper_and_live_dry_run_share_order_lifecycle_contract_shape():
    reports = {
        PayloadSource.BACKTEST: _run_runtime_report(PayloadSource.BACKTEST, "hbt001-backtest"),
        PayloadSource.PAPER: _run_runtime_report(PayloadSource.PAPER, "hbt001-paper"),
        PayloadSource.LIVE: _run_live_dry_run_report("hbt001-live-dry-run"),
    }
    contracts = {
        source: _execution_contract(report)
        for source, report in reports.items()
    }
    expected_keys = set(contracts[PayloadSource.PAPER])

    for source, contract in contracts.items():
        assert set(contract) == expected_keys
        restored = OrderLifecycleContract.from_payload(contract)
        execution_result = _execution_result(reports[source])
        assert execution_result["order_lifecycle_contract"] == "order_lifecycle_v1"
        assert execution_result["lifecycle_state"] == restored.lifecycle_state
        assert restored.source == source
        assert restored.client_order_id == execution_result["client_order_id"]
        assert restored.symbol == "BTCUSDT"
        assert restored.lifecycle_path[0] == "CREATED"

    assert contracts[PayloadSource.BACKTEST]["execution_mode"] == "backtest"
    assert contracts[PayloadSource.PAPER]["execution_mode"] == "paper"
    assert contracts[PayloadSource.LIVE]["execution_mode"] == "live_dry_run"
    assert contracts[PayloadSource.BACKTEST]["lifecycle_state"] == "FILLED"
    assert contracts[PayloadSource.PAPER]["lifecycle_state"] == "FILLED"
    assert contracts[PayloadSource.LIVE]["lifecycle_state"] == "SUBMITTED"
    assert contracts[PayloadSource.LIVE]["safety_flags"]["live"] is True
    assert contracts[PayloadSource.LIVE]["safety_flags"]["dry_run"] is True
    assert contracts[PayloadSource.LIVE]["safety_flags"]["live_orders_sent"] is False
    assert contracts[PayloadSource.BACKTEST]["safety_flags"]["simulated"] is True
    assert contracts[PayloadSource.PAPER]["safety_flags"]["simulated"] is True


def _run_runtime_report(source, run_id):
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        backtest_provider=CrossingProvider(),
        paper_provider=CrossingProvider(),
        feature_windows=(2, 3),
    )
    return runtime.run(
        PipelineRuntimeRequest(
            source=source,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id=run_id,
        )
    )


def _run_live_dry_run_report(run_id):
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "live_crossing", lambda: CrossingProvider())
    registry.register(PluginKind.EXECUTION, "real_live_broker", lambda **_: ExplodingBroker())
    runtime = TradingRuntimeOrchestrator.from_config_dry_run(
        RuntimeConfig(
            name="hbt001-live-dry-run",
            source=PayloadSource.LIVE,
            markets=[MarketConfig(symbol="BTCUSDT", timeframe="1m", provider="live_crossing")],
            strategies=[
                StrategyBinding(
                    symbol="BTCUSDT",
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
        ),
        registry=registry,
    )
    return runtime.run(
        {
            "source": "live",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "index": 4,
            "run_id": run_id,
        }
    )


def _execution_result(report):
    return _stage(report, "execution").output_payload["execution_result"]


def _execution_contract(report):
    return _execution_result(report)["order_lifecycle"]


def _stage(report, name):
    return {stage.stage: stage for stage in report.stages}[name]


def _kline(index, close):
    return Kline(
        timestamp=1700000000 + index * 60,
        open=float(close),
        high=float(close) + 0.5,
        low=float(close) - 0.5,
        close=float(close),
        volume=1000.0 + index,
    )


def _value(value):
    return value.value if hasattr(value, "value") else value


class ExplodingBroker:
    def place_order(self, request):
        raise AssertionError("live dry-run must not call a real broker")
