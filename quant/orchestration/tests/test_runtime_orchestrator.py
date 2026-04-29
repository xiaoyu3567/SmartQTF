import json
from pathlib import Path

from quant.logging.jsonl import JsonlTradeLogger
from quant.data.schemas.market import Kline
from quant.config import BrokerConfig, MarketConfig, RuntimeConfig, StrategyBinding
from quant.execution.broker import BrokerAdapter
from quant.orchestration import TradingRuntimeOrchestrator
from quant.orchestration.runtime import BrokerExecutionHandler, LiveOrderGate
from quant.registry import PluginKind, PluginRegistry
from quant.schemas import (
    BrokerOrderResult,
    OrderIntent,
    OrderKind,
    OrderStatus,
    PayloadSource,
    PipelineRuntimeRequest,
    PipelineRunReport,
    PipelineStageStatus,
    TimeInForce,
    TradeSide,
)


class CrossingProvider:
    def get_klines(self, symbol, timeframe):
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


def test_runtime_entrypoint_uses_same_pipeline_contract_for_backtest_and_paper():
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        backtest_provider=CrossingProvider(),
        paper_provider=CrossingProvider(),
        feature_windows=(2, 3),
    )

    backtest_report = runtime.run(
        PipelineRuntimeRequest(
            source=PayloadSource.BACKTEST,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id="bt-runtime",
        )
    )
    paper_report = runtime.run(
        PipelineRuntimeRequest(
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id="paper-runtime",
        )
    )

    expected_stages = [
        "data",
        "data_quality",
        "feature",
        "regime",
        "strategy",
        "decision",
        "risk",
        "portfolio",
        "execution",
        "logging",
    ]
    assert backtest_report.success is True
    assert paper_report.success is True
    assert backtest_report.context.source == PayloadSource.BACKTEST
    assert paper_report.context.source == PayloadSource.PAPER
    assert [stage.stage for stage in backtest_report.stages] == expected_stages
    assert [stage.stage for stage in paper_report.stages] == expected_stages
    assert all(stage.status == PipelineStageStatus.SUCCEEDED for stage in backtest_report.stages)
    assert all(stage.status == PipelineStageStatus.SUCCEEDED for stage in paper_report.stages)
    assert backtest_report.final_output["execution_result"]["status"] == "filled"
    assert paper_report.final_output["execution_result"]["status"] == "filled"


def test_runtime_entrypoint_fails_safely_when_live_handler_is_not_configured():
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        backtest_provider=CrossingProvider(),
        paper_provider=CrossingProvider(),
        feature_windows=(2, 3),
    )

    report = runtime.run(
        {
            "source": PayloadSource.LIVE,
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "index": 4,
            "run_id": "live-runtime",
            "metadata": {"requested_at": 1700000000},
        }
    )

    assert report.success is False
    assert report.context.source == PayloadSource.LIVE
    assert report.stages[0].stage == "orchestration"
    assert report.stages[0].status == PipelineStageStatus.ERROR
    assert "live runtime handler is not configured" in report.errors[0]


def test_runtime_entrypoint_can_build_paper_pipeline_from_runtime_config(tmp_path):
    config = RuntimeConfig(
        name="config-paper",
        source=PayloadSource.PAPER,
        markets=[MarketConfig(symbol="BTCUSDT", timeframe="1m", provider="mock")],
        strategies=[
            StrategyBinding(
                symbol="BTCUSDT",
                strategy="ma_crossover",
                route="default",
                parameters={"fast_window": 2, "slow_window": 3},
            )
        ],
        broker=BrokerConfig(mode=PayloadSource.PAPER, broker_plugin="simulated"),
        logging={"pipeline_report_dir": str(tmp_path / "pipeline-runs")},
    )
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "mock", lambda: CrossingProvider())
    runtime = TradingRuntimeOrchestrator.from_config(config, registry=registry)

    report = runtime.run(
        PipelineRuntimeRequest(
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id="config-runtime",
        )
    )

    assert report.success is True
    assert report.context.source == PayloadSource.PAPER
    assert report.stages[0].stage == "data"
    assert report.stages[-1].stage == "logging"
    assert report.final_output["execution_result"]["status"] == "filled"


def test_runtime_from_config_persists_pipeline_run_report(tmp_path):
    report_dir = tmp_path / "pipeline-runs"
    config = RuntimeConfig(
        name="config-paper",
        source=PayloadSource.PAPER,
        markets=[MarketConfig(symbol="BTCUSDT", timeframe="1m", provider="mock")],
        strategies=[
            StrategyBinding(
                symbol="BTCUSDT",
                strategy="ma_crossover",
                route="default",
                parameters={"fast_window": 2, "slow_window": 3},
            )
        ],
        broker=BrokerConfig(mode=PayloadSource.PAPER, broker_plugin="simulated"),
        logging={"pipeline_report_dir": str(report_dir)},
    )
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "mock", lambda: CrossingProvider())
    runtime = TradingRuntimeOrchestrator.from_config(config, registry=registry)

    report = runtime.run(
        PipelineRuntimeRequest(
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id="persist-runtime",
        )
    )

    artifact = report.metadata["pipeline_report_artifact"]
    report_path = Path(artifact["report_path"])
    latest_path = Path(artifact["latest_report_path"])
    restored = PipelineRunReport.from_payload(json.loads(report_path.read_text(encoding="utf-8")))

    assert artifact["type"] == "run"
    assert report_path.exists()
    assert latest_path.exists()
    assert restored.context.run_id == "persist-runtime"
    assert json.loads(latest_path.read_text(encoding="utf-8"))["context"]["run_id"] == "persist-runtime"


def test_runtime_config_uses_registry_plugins_for_runtime_components(tmp_path):
    created = []
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "crossing", lambda: _record(created, "data", CrossingProvider()))
    registry.register(PluginKind.FEATURE, "rule_regime_detector", lambda: _record(created, "feature", None) or None)
    registry.register(PluginKind.STRATEGY, "spy_strategy", lambda **_: _record(created, "strategy", SpyStrategy()))
    registry.register(PluginKind.RISK, "spy_risk", lambda **_: _record(created, "risk", SpyRiskManager()))
    registry.register(PluginKind.EXECUTION, "spy_execution", lambda **_: _record(created, "execution", SpyExecutionEngine()))
    config = RuntimeConfig(
        name="registry-paper",
        source=PayloadSource.PAPER,
        markets=[MarketConfig(symbol="BTCUSDT", timeframe="1m", provider="crossing")],
        strategies=[StrategyBinding(symbol="BTCUSDT", strategy="spy_strategy", route="default")],
        broker=BrokerConfig(mode=PayloadSource.PAPER, broker_plugin="spy_execution"),
        risk={"risk_plugin": "spy_risk"},
        logging={"pipeline_report_dir": str(tmp_path / "pipeline-runs")},
    )

    runtime = TradingRuntimeOrchestrator.from_config(config, registry=registry)
    report = runtime.run({"source": "paper", "symbol": "BTCUSDT", "timeframe": "1m", "index": 4})

    assert report.success is True
    assert {"data", "feature", "strategy", "risk", "execution"}.issubset(set(created))


def test_live_runtime_from_config_requires_explicit_live_handlers():
    config = RuntimeConfig(
        name="unsafe-live",
        source=PayloadSource.LIVE,
        markets=[MarketConfig(symbol="BTCUSDT", timeframe="1m", provider="mock")],
        strategies=[
            StrategyBinding(
                symbol="BTCUSDT",
                strategy="ma_crossover",
                route="default",
                parameters={"fast_window": 2, "slow_window": 3},
            )
        ],
        broker=BrokerConfig(mode=PayloadSource.LIVE, account_id="live-account", broker_plugin="simulated"),
    )

    try:
        TradingRuntimeOrchestrator.from_config(config, registry=PluginRegistry())
    except ValueError as exc:
        assert "live runtime requires an explicit non-mock data provider" in str(exc)
    else:
        raise AssertionError("live runtime should reject mock/simulated handlers")


def test_live_runtime_from_config_wraps_broker_adapter(tmp_path):
    registry = PluginRegistry()
    broker = SpyBrokerAdapter()
    preflight_artifact_path = _write_live_preflight_artifact(tmp_path)
    registry.register(PluginKind.DATA, "live_crossing", lambda: CrossingProvider())
    registry.register(PluginKind.EXECUTION, "spy_live_broker", lambda **_: broker)
    config = RuntimeConfig(
        name="live-runtime",
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
            broker_plugin="spy_live_broker",
            settings={
                "allow_live_orders": True,
                "require_manual_preflight": True,
                "preflight_artifact_path": str(preflight_artifact_path),
                "preflight_max_age_seconds": 999999999,
            },
        ),
        logging={"pipeline_report_dir": str(tmp_path / "pipeline-runs")},
    )

    runtime = TradingRuntimeOrchestrator.from_config(config, registry=registry)
    report = runtime.run({"source": "live", "symbol": "BTCUSDT", "timeframe": "1m", "index": 4})

    assert report.success is True
    assert report.context.source == PayloadSource.LIVE
    assert broker.requests[0].client_order_id == report.final_output["execution_result"]["client_order_id"]
    assert report.final_output["execution_result"]["status"] == "filled"
    assert report.final_output["execution_result"]["broker_order_id"] == "broker-1"
    assert report.final_output["execution_result"]["live_orders_sent"] is True
    assert report.final_output["execution_result"]["live_order_gate"]["approved"] is True


def test_live_runtime_from_config_blocks_broker_without_live_gate_approval(tmp_path):
    registry = PluginRegistry()
    broker = SpyBrokerAdapter()
    registry.register(PluginKind.DATA, "live_crossing", lambda: CrossingProvider())
    registry.register(PluginKind.EXECUTION, "spy_live_broker", lambda **_: broker)
    config = RuntimeConfig(
        name="live-runtime-blocked",
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
            broker_plugin="spy_live_broker",
            settings={"allow_live_orders": False, "require_manual_preflight": True},
        ),
        logging={"pipeline_report_dir": str(tmp_path / "pipeline-runs")},
    )

    runtime = TradingRuntimeOrchestrator.from_config(config, registry=registry)
    report = runtime.run({"source": "live", "symbol": "BTCUSDT", "timeframe": "1m", "index": 4})
    execution_result = report.final_output["execution_result"]

    assert broker.requests == []
    assert execution_result["status"] == "rejected"
    assert execution_result["live_orders_sent"] is False
    assert execution_result["rejection_code"] == "live_order_gate_rejected"
    assert "allow_live_orders_disabled" in execution_result["live_order_gate"]["reason_codes"]


def test_live_dry_run_from_config_runs_pipeline_without_broker_order(tmp_path):
    created = []
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "live_crossing", lambda: CrossingProvider())
    registry.register(
        PluginKind.EXECUTION,
        "real_live_broker",
        lambda **_: _record(created, "broker_created", SpyBrokerAdapter()),
    )
    config = RuntimeConfig(
        name="live-dry-run",
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
        logging={"pipeline_report_dir": str(tmp_path / "pipeline-runs")},
    )

    runtime = TradingRuntimeOrchestrator.from_config_dry_run(config, registry=registry)
    report = runtime.run({"source": "live", "symbol": "BTCUSDT", "timeframe": "1m", "index": 4})

    assert report.success is True
    assert report.context.source == PayloadSource.LIVE
    assert [stage.stage for stage in report.stages] == [
        "data",
        "data_quality",
        "feature",
        "regime",
        "strategy",
        "decision",
        "risk",
        "portfolio",
        "execution",
        "logging",
    ]
    assert "broker_created" not in created
    execution_result = report.final_output["execution_result"]
    assert execution_result["dry_run"] is True
    assert execution_result["live_orders_sent"] is False
    assert execution_result["status"] == "accepted"
    assert execution_result["filled_qty"] == 0.0
    assert execution_result["remaining_qty"] > 0.0


def test_live_dry_run_requires_live_orders_disabled():
    config = RuntimeConfig(
        name="unsafe-live-dry-run",
        source=PayloadSource.LIVE,
        markets=[MarketConfig(symbol="BTCUSDT", timeframe="1m", provider="live_crossing")],
        strategies=[StrategyBinding(symbol="BTCUSDT", strategy="ma_crossover", route="default")],
        broker=BrokerConfig(
            mode=PayloadSource.LIVE,
            account_id="live-account",
            broker_plugin="real_live_broker",
            settings={"allow_live_orders": True},
        ),
    )
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "live_crossing", lambda: CrossingProvider())

    try:
        TradingRuntimeOrchestrator.from_config_dry_run(config, registry=registry)
    except ValueError as exc:
        assert "allow_live_orders=false" in str(exc)
    else:
        raise AssertionError("live dry-run must reject configs that allow live orders")


def test_broker_execution_handler_preserves_reduce_only_close_intent(tmp_path):
    broker = SpyBrokerAdapter()
    handler = BrokerExecutionHandler(broker, live_order_gate=_passing_live_order_gate(tmp_path))
    order_intent = OrderIntent(
        order_intent_id="close-intent",
        decision_id="kill-switch",
        client_order_id="close-001",
        symbol="BTCUSDT",
        side=TradeSide.SELL,
        order_type=OrderKind.MARKET,
        quantity=2.0,
        time_in_force=TimeInForce.GTC,
        reduce_only=True,
        risk_approved=True,
        created_at=1710000000,
    )

    result = handler.on_order_intent(order_intent, price=12.0, index=4)

    assert result["status"] == "filled"
    assert broker.requests[0].reduce_only is True


def test_broker_execution_handler_rejects_stale_preflight_without_broker_call(tmp_path):
    broker = SpyBrokerAdapter()
    artifact_path = _write_live_preflight_artifact(tmp_path, generated_at=1700000000)
    handler = BrokerExecutionHandler(
        broker,
        live_order_gate=LiveOrderGate(
            {
                "allow_live_orders": True,
                "require_manual_preflight": True,
                "preflight_artifact_path": str(artifact_path),
                "preflight_max_age_seconds": 60,
            },
            risk_manager=HealthyRiskManager(),
            clock=lambda: 1700003600,
        ),
    )
    order_intent = OrderIntent(
        order_intent_id="stale-intent",
        decision_id="decision-stale",
        client_order_id="stale-001",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        risk_approved=True,
        created_at=1710000000,
    )

    result = handler.on_order_intent(order_intent, price=12.0, index=4)

    assert broker.requests == []
    assert result["status"] == "rejected"
    assert "preflight_artifact_expired" in result["live_order_gate"]["reason_codes"]


def test_broker_execution_handler_rejects_active_kill_switch_without_broker_call(tmp_path):
    broker = SpyBrokerAdapter()
    artifact_path = _write_live_preflight_artifact(tmp_path)
    handler = BrokerExecutionHandler(
        broker,
        live_order_gate=LiveOrderGate(
            {
                "allow_live_orders": True,
                "require_manual_preflight": True,
                "preflight_artifact_path": str(artifact_path),
                "preflight_max_age_seconds": 60,
            },
            risk_manager=ActiveKillSwitchRiskManager(),
            clock=lambda: 1700000010,
        ),
    )
    order_intent = OrderIntent(
        order_intent_id="kill-intent",
        decision_id="decision-kill",
        client_order_id="kill-001",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        risk_approved=True,
        created_at=1710000000,
    )

    result = handler.on_order_intent(order_intent, price=12.0, index=4)

    assert broker.requests == []
    assert result["status"] == "rejected"
    assert "kill_switch_active" in result["live_order_gate"]["reason_codes"]


def test_runtime_generates_daily_review_on_daily_close(tmp_path):
    trade_log_path = tmp_path / "trade-log.jsonl"
    review_dir = tmp_path / "reviews"
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        paper_provider=CrossingProvider(),
        feature_windows=(2, 3),
        logger=JsonlTradeLogger(trade_log_path),
        daily_review_log_path=trade_log_path,
        daily_review_output_dir=review_dir,
    )

    report = runtime.run(
        PipelineRuntimeRequest(
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id="paper-daily-close",
            metadata={"daily_close": True, "trading_date": "2023-11-14"},
        )
    )

    daily_review = report.metadata["daily_review"]
    assert daily_review["status"] == "generated"
    assert daily_review["record_count"] == 3
    assert daily_review["report"]["run_id"] == "paper-daily-close"
    assert daily_review["report"]["trading_date"] == "2023-11-14"
    assert daily_review["report"]["fill_count"] == 1
    assert (review_dir / "paper-daily-close-daily-review.json").exists()
    assert (review_dir / "paper-daily-close-daily-review.md").exists()


def test_runtime_marks_daily_review_skipped_when_log_path_is_missing():
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        paper_provider=CrossingProvider(),
        feature_windows=(2, 3),
    )

    report = runtime.run(
        PipelineRuntimeRequest(
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id="paper-daily-close-no-log",
            metadata={"daily_close": True},
        )
    )

    assert report.metadata["daily_review"] == {
        "status": "skipped",
        "reason": "daily_review_log_path is not configured",
    }


def _record(created, name, value):
    created.append(name)
    return value


def _write_live_preflight_artifact(tmp_path, *, generated_at=1700000000, success=True):
    artifact_path = tmp_path / "latest-preflight.json"
    artifact_path.write_text(
        json.dumps(
            {
                "report_id": f"production-rehearsal:{generated_at}",
                "generated_at": generated_at,
                "success": success,
                "preflight_summary": {"failed_count": 0, "warning_count": 0, "check_count": 5},
                "metadata": {"live_orders_sent": False, "contains_real_credentials": False},
                "checks": [
                    {
                        "name": "preflight:qtf_environment",
                        "status": "PASS",
                        "category": "qtf_environment",
                        "message": "QTF conda environment is active",
                        "source": "preflight",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return artifact_path


def _passing_live_order_gate(tmp_path):
    return LiveOrderGate(
        {
            "allow_live_orders": True,
            "require_manual_preflight": True,
            "preflight_artifact_path": str(_write_live_preflight_artifact(tmp_path)),
            "preflight_max_age_seconds": 60,
        },
        risk_manager=HealthyRiskManager(),
        clock=lambda: 1700000010,
    )


class HealthyRiskManager:
    kill_switch_enabled = False


class ActiveKillSwitchRiskManager:
    kill_switch_enabled = True


class SpyStrategy:
    strategy_id = "spy_strategy"
    strategy_version = "1.0"

    def generate_signal(self, features, index):
        from quant.schemas import StrategySignal, TradeSide

        return StrategySignal(
            signal_id=f"spy:{index}",
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            side=TradeSide.BUY,
            signal_index=index,
            reason_codes=["spy"],
        )


class SpyRiskManager:
    def evaluate(self, signal, account, price):
        from quant.schemas import OrderIntent, OrderKind, RiskDecision, TimeInForce, TradeSide

        return RiskDecision.approve(
            order_payload=signal,
            reason_codes=["spy_risk"],
            order_intent=OrderIntent(
                order_intent_id="spy-order-intent",
                decision_id=signal["decision_id"],
                client_order_id=signal["client_order_id"],
                symbol=signal["symbol"],
                side=TradeSide.BUY,
                order_type=OrderKind.MARKET,
                quantity=1.0,
                time_in_force=TimeInForce.GTC,
                risk_approved=True,
                created_at=signal["timestamp"],
                trace=signal["trace"],
            ),
        )


class SpyExecutionEngine:
    def on_order_intent(self, order_intent, price, index):
        return {
            "order_id": 1,
            "client_order_id": order_intent.client_order_id,
            "symbol": order_intent.symbol,
            "side": order_intent.side.value if hasattr(order_intent.side, "value") else order_intent.side,
            "status": "filled",
            "filled_qty": order_intent.quantity,
            "remaining_qty": 0.0,
            "fill_price": price,
        }


class SpyBrokerAdapter(BrokerAdapter):
    name = "spy_live_broker"

    def __init__(self):
        self.requests = []

    def place_order(self, request):
        self.requests.append(request)
        return BrokerOrderResult(
            client_order_id=request.client_order_id,
            broker_order_id="broker-1",
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.FILLED,
            requested_qty=request.quantity,
            filled_qty=request.quantity,
            avg_fill_price=12.0,
            trace=request.trace,
        )

    def cancel_order(self, client_order_id):
        raise NotImplementedError

    def replace_order(self, request):
        raise NotImplementedError

    def get_order(self, client_order_id):
        raise NotImplementedError

    def list_open_orders(self, symbol=None):
        return []
