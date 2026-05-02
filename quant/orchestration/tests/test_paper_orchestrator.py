from quant.data.schemas.market import Kline
from quant.logging.jsonl import JsonlTradeLogger
from quant.orchestration import PaperTradingOrchestrator
from quant.account.account import CryptoAccount
from quant.risk.risk_manager import RiskManager
from quant.schemas import (
    PipelineStageStatus,
    PipelineSymbolRunRequest,
    RuntimeHealthSnapshot,
    RuntimeHealthStatus,
)


class CrossingProvider:
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


class MissingKlineProvider:
    def get_klines(self, symbol, timeframe):
        return [
            Kline(
                timestamp=1700000000,
                open=10.0,
                high=10.5,
                low=9.5,
                close=10.0,
                volume=1000.0,
            ),
            Kline(
                timestamp=1700000120,
                open=12.0,
                high=12.5,
                low=11.5,
                close=12.0,
                volume=1000.0,
            ),
        ]


def test_paper_orchestrator_runs_signal_to_execution_and_logging(tmp_path):
    logger = JsonlTradeLogger(tmp_path / "trades.jsonl")
    orchestrator = PaperTradingOrchestrator(
        provider=CrossingProvider(),
        feature_windows=(2, 3),
        logger=logger,
    )

    report = orchestrator.run_tick(symbol="BTCUSDT", timeframe="1m", index=4, run_id="test-run")

    assert report.success is True
    assert [stage.stage for stage in report.stages] == [
        "data",
        "data_quality",
        "feature",
        "regime",
        "strategy",
        "decision",
        "portfolio",
        "risk",
        "execution",
        "logging",
    ]
    assert all(stage.status == PipelineStageStatus.SUCCEEDED for stage in report.stages)
    portfolio_stage = next(stage for stage in report.stages if stage.stage == "portfolio")
    capital_budget = portfolio_stage.output_payload["capital_budget"]
    risk_stage = next(stage for stage in report.stages if stage.stage == "risk")
    risk_decision = risk_stage.output_payload["risk_decision"]
    allocation_decision = risk_stage.output_payload["allocation_decision"]
    assert allocation_decision["approved"] is True
    assert allocation_decision["symbol"] == "BTCUSDT"
    assert allocation_decision["quantity"] == risk_decision["order_intent"]["quantity"]
    assert capital_budget["approved"] is True
    assert "capital_budget_approved" in capital_budget["reason_codes"]
    assert risk_stage.output_payload["portfolio_execution_context"]["client_order_id"] == (
        risk_decision["order_intent"]["client_order_id"]
    )
    assert report.final_output["execution_result"]["status"] == "filled"
    assert report.final_output["execution_result"]["client_order_id"] == (
        risk_decision["order_intent"]["client_order_id"]
    )
    assert report.final_output["execution_result"]["client_order_id"].endswith(":risk-v2:buy")
    protective_exit_plan = report.final_output["execution_result"]["protective_exit_plan"]
    assert protective_exit_plan["parent_client_order_id"] == report.final_output["execution_result"]["client_order_id"]
    assert protective_exit_plan["quantity"] == report.final_output["execution_result"]["filled_qty"]
    assert protective_exit_plan["stop_loss_price"] == 11.76
    assert protective_exit_plan["take_profit_price"] == 12.48
    feature_stage = next(stage for stage in report.stages if stage.stage == "feature")
    assert feature_stage.input_payload["quality_passed"] is True
    assert feature_stage.input_payload["quality_report_id"].startswith("quality:BTCUSDT:1m:5:")
    regime_stage = next(stage for stage in report.stages if stage.stage == "regime")
    assert regime_stage.input_payload["quality_passed"] is True
    assert regime_stage.input_payload["quality_report_id"].startswith("quality:BTCUSDT:1m:5:")
    assert regime_stage.output_payload["regime"]["input_refs"]["feature_snapshot_id"] == (
        "test-run:features:4"
    )
    assert regime_stage.output_payload["regime"]["input_refs"]["quality_report"]["passed"] is True
    runtime_health = RuntimeHealthSnapshot.from_payload(report.metadata["runtime_health"])
    assert runtime_health.status == RuntimeHealthStatus.HEALTHY
    assert runtime_health.symbol == "BTCUSDT"
    assert runtime_health.timeframe == "1m"
    assert runtime_health.risk_rejection_rate == 0.0
    assert runtime_health.order_failure_rate == 0.0
    assert runtime_health.kill_switch_active is False
    assert "execution" in runtime_health.pipeline_stage_durations_ms
    assert list(runtime_health.pipeline_stage_durations_ms) == [
        "data",
        "data_quality",
        "feature",
        "regime",
        "strategy",
        "decision",
        "portfolio",
        "risk",
        "execution",
        "logging",
    ]

    records = logger.read_all()
    assert [record.record_type for record in records] == ["decision", "order", "fill"]
    assert records[0].feature_snapshot is not None
    assert records[0].feature_snapshot.snapshot_id == "test-run:features:4"
    assert records[0].feature_snapshot.values["fast_ma"] == 9.5
    assert records[0].metadata["feature_snapshot"]["snapshot_id"] == "test-run:features:4"


def test_paper_orchestrator_skips_trade_stages_when_strategy_has_no_signal():
    report = PaperTradingOrchestrator().run_tick(run_id="no-signal")

    statuses = {stage.stage: stage.status for stage in report.stages}
    assert report.success is True
    assert statuses["data"] == PipelineStageStatus.SUCCEEDED
    assert statuses["data_quality"] == PipelineStageStatus.SUCCEEDED
    assert statuses["feature"] == PipelineStageStatus.SUCCEEDED
    assert statuses["regime"] == PipelineStageStatus.SUCCEEDED
    assert statuses["strategy"] == PipelineStageStatus.SUCCEEDED
    assert statuses["decision"] == PipelineStageStatus.SKIPPED
    assert statuses["portfolio"] == PipelineStageStatus.SKIPPED
    assert statuses["risk"] == PipelineStageStatus.SKIPPED
    assert statuses["execution"] == PipelineStageStatus.SKIPPED
    assert report.final_output["signal"] is None
    runtime_health = RuntimeHealthSnapshot.from_payload(report.metadata["runtime_health"])
    assert runtime_health.status == RuntimeHealthStatus.HEALTHY
    assert runtime_health.risk_rejection_rate == 0.0


def test_paper_orchestrator_records_critical_health_when_kill_switch_rejects_signal():
    orchestrator = PaperTradingOrchestrator(
        provider=CrossingProvider(),
        feature_windows=(2, 3),
    )
    orchestrator.risk_manager.enable_kill_switch("heartbeat test")

    report = orchestrator.run_tick(symbol="BTCUSDT", timeframe="1m", index=4, run_id="kill-switch-run")

    statuses = {stage.stage: stage.status for stage in report.stages}
    runtime_health = RuntimeHealthSnapshot.from_payload(report.metadata["runtime_health"])
    assert report.success is True
    assert statuses["risk"] == PipelineStageStatus.REJECTED
    assert runtime_health.status == RuntimeHealthStatus.CRITICAL
    assert runtime_health.kill_switch_active is True
    assert runtime_health.risk_rejection_rate == 1.0
    assert "kill_switch_active" in runtime_health.alerts


def test_paper_orchestrator_auto_kill_switch_closes_open_position():
    account = CryptoAccount(initial_balance=10000.0)
    position = account.get_position("BTCUSDT")
    position.size = 2.0
    position.avg_price = 10.0
    position.side = "long"
    account.equity = 9400.0
    risk_manager = RiskManager(daily_loss_limit_pct=0.05)
    orchestrator = PaperTradingOrchestrator(
        provider=CrossingProvider(),
        feature_windows=(2, 3),
        account=account,
        risk_manager=risk_manager,
    )

    report = orchestrator.run_tick(symbol="BTCUSDT", timeframe="1m", index=4, run_id="auto-kill")

    statuses = {stage.stage: stage.status for stage in report.stages}
    execution_stage = next(stage for stage in report.stages if stage.stage == "execution")
    assert statuses["risk"] == PipelineStageStatus.REJECTED
    assert risk_manager.kill_switch_enabled is True
    assert report.final_output["kill_switch_decision"]["triggered"] is True
    assert execution_stage.status == PipelineStageStatus.SUCCEEDED
    assert execution_stage.output_payload["close_results"][0]["client_order_id"] == (
        "auto-kill:BTCUSDT:4:kill-switch-close"
    )
    assert execution_stage.output_payload["close_results"][0]["side"] == "sell"
    assert account.get_position("BTCUSDT").size == 0.0


def test_paper_orchestrator_runs_symbol_batch_sequentially_with_failure_isolation(tmp_path):
    loggers = {}

    def logger_factory(symbol):
        logger = JsonlTradeLogger(tmp_path / f"{symbol}.jsonl")
        loggers[symbol] = logger
        return logger

    orchestrator = PaperTradingOrchestrator(
        provider=CrossingProvider(),
        feature_windows=(2, 3),
        logger_factory=logger_factory,
    )

    batch = orchestrator.run_symbols(
        [
            PipelineSymbolRunRequest(symbol="BTCUSDT", timeframe="1m", index=4),
            PipelineSymbolRunRequest(symbol="EMPTY", timeframe="1m", index=0),
        ],
        batch_id="batch-001",
        requested_at=1700000000,
    )

    assert batch.success is False
    assert batch.metadata["execution_mode"] == "sequential"
    assert [report.context.symbol for report in batch.reports] == ["BTCUSDT", "EMPTY"]
    assert batch.reports[0].success is True
    assert batch.reports[1].success is False
    assert batch.reports[1].stages[0].status == PipelineStageStatus.ERROR
    runtime_health = RuntimeHealthSnapshot.from_payload(batch.reports[1].metadata["runtime_health"])
    assert runtime_health.status == RuntimeHealthStatus.CRITICAL
    assert "pipeline_error" in runtime_health.alerts
    assert "provider returned no klines" in batch.errors[0]
    assert [record.record_type for record in loggers["BTCUSDT"].read_all()] == ["decision", "order", "fill"]


def test_paper_orchestrator_blocks_pipeline_when_data_quality_fails():
    report = PaperTradingOrchestrator(provider=MissingKlineProvider()).run_tick(
        symbol="BTCUSDT",
        timeframe="1m",
        index=1,
        run_id="bad-data",
    )

    statuses = {stage.stage: stage.status for stage in report.stages}
    assert statuses["data"] == PipelineStageStatus.SUCCEEDED
    assert statuses["data_quality"] == PipelineStageStatus.REJECTED
    assert statuses["feature"] == PipelineStageStatus.SKIPPED
    feature_stage = next(stage for stage in report.stages if stage.stage == "feature")
    assert feature_stage.skip_reason == "quality_report_failed: data quality rejected klines"
    assert report.final_output["quality_report"]["passed"] is False
    runtime_health = RuntimeHealthSnapshot.from_payload(report.metadata["runtime_health"])
    assert runtime_health.status == RuntimeHealthStatus.DEGRADED
