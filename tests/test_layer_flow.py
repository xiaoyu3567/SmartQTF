# Layer flow contract tests
# Auto-merged during repository simplification.



# --- merged from test_contract_analytics_to_optimization.py ---
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.analytics import DailyReviewReporter
from quant.optimization import DailyReviewOptimizationPlanner, StrategyVersionGate, SymbolOptimizationQueue
from quant.schemas import (
    AssetClass,
    DecisionAction,
    DecisionIntent,
    DecisionLogRecord,
    FillLogRecord,
    OrderKind,
    PayloadSource,
    StrategyPromotionAction,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def make_trace(symbol="BTCUSDT"):
    return TraceContext(
        run_id="analytics-optimization-001",
        source=PayloadSource.PAPER,
        symbol=symbol,
        timeframe="1m",
        timestamp=1710000000,
        bar_index=20,
    )


def make_analytics_decision(decision_id="decision-001"):
    trace = make_trace()
    return DecisionIntent(
        decision_id=decision_id,
        timestamp=1710000000,
        symbol=trace.symbol,
        asset_class=AssetClass.CRYPTO,
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        regime="trend",
        action=DecisionAction.OPEN_LONG,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        time_in_force=TimeInForce.GTC,
        confidence=0.8,
        reason_codes=["ma_cross_up"],
        trace=trace,
    )


def make_records():
    decision = make_analytics_decision()
    trace = decision.trace
    return [
        DecisionLogRecord(
            event_id="event-decision-001",
            run_id=trace.run_id,
            timestamp=decision.timestamp,
            trace=trace,
            decision=decision,
        ),
        FillLogRecord(
            event_id="event-fill-001",
            run_id=trace.run_id,
            timestamp=1710000060,
            trace=trace,
            fill_id="fill-001",
            order_id="order-001",
            client_order_id="client-001",
            symbol=trace.symbol,
            side=TradeSide.BUY,
            filled_quantity=1.0,
            fill_price=100.0,
            commission=0.1,
            decision_id=decision.decision_id,
            metadata={"realized_pnl": 12.0},
        ),
        FillLogRecord(
            event_id="event-fill-002",
            run_id=trace.run_id,
            timestamp=1710000120,
            trace=trace,
            fill_id="fill-002",
            order_id="order-002",
            client_order_id="client-002",
            symbol=trace.symbol,
            side=TradeSide.BUY,
            filled_quantity=1.0,
            fill_price=101.0,
            commission=0.1,
            decision_id=decision.decision_id,
            metadata={"realized_pnl": 8.0},
        ),
    ]


def test_daily_review_report_enqueues_replayable_optimization_candidate(tmp_path):
    report = DailyReviewReporter().build_report(
        make_records(),
        report_id="daily-optimization-001",
        trading_date="2024-03-09",
    )
    gate = StrategyVersionGate(
        min_trades=2,
        min_net_pnl=5.0,
        require_out_of_sample=True,
        min_walk_forward_windows=1,
        min_walk_forward_pass_rate=0.5,
        min_monte_carlo_survival_rate=0.8,
    )
    queue = SymbolOptimizationQueue(tmp_path / "optimization-queue")

    records = DailyReviewOptimizationPlanner(gate=gate).enqueue_from_report(report, queue)

    assert len(records) == 1
    record = records[0]
    assert record.symbol == "BTCUSDT"
    assert record.candidate.strategy_id == "ma_crossover"
    assert record.candidate.code_ref == "daily-review:daily-optimization-001"
    assert record.candidate.parameters["review_symbol_net_pnl"] == 20.0
    assert record.validation_metrics.trade_count == 2
    assert record.promotion_decision.action == StrategyPromotionAction.REJECT
    assert "missing_out_of_sample_validation" in record.promotion_decision.reason_codes
    assert "missing_walk_forward_validation" in record.promotion_decision.reason_codes
    assert "missing_monte_carlo_validation" in record.promotion_decision.reason_codes
    assert queue.get_record("BTCUSDT", record.queue_id) == record


# --- merged from test_contract_data_scanner_logging.py ---
import json
from pathlib import Path

from quant.config import BrokerConfig, MarketConfig, RuntimeConfig, StrategyBinding
from quant.data.schemas.market import Kline
from quant.orchestration import RuntimeScanScheduler
from quant.registry import PluginKind, PluginRegistry
from quant.schemas import (
    PayloadSource,
    PipelineBatchRunReport,
    PipelineRunReport,
    PipelineStageStatus,
    RuntimeHealthSnapshot,
    RuntimeHealthStatus,
    UniverseInstrument,
    UniverseSnapshot,
)


START_TS = 1700000000


class UniverseQualityFailureProvider:
    def __init__(self):
        self.last_universe_filter = None

    def discover_universe(self, filter_config):
        self.last_universe_filter = filter_config
        return UniverseSnapshot(
            snapshot_id="unit-universe-1700000000",
            venue=filter_config.venue,
            instrument_type=filter_config.instrument_type,
            as_of_timestamp=START_TS,
            source="unit_contract_provider",
            filters=filter_config,
            instruments=[
                UniverseInstrument(
                    symbol="BTCUSDT",
                    venue=filter_config.venue,
                    instrument_type=filter_config.instrument_type,
                    base_currency="BTC",
                    quote_currency="USDT",
                    status="live",
                    quantity_step=0.001,
                    min_quantity=0.001,
                    price_tick=0.1,
                    min_notional=10.0,
                    turnover_24h=1_000_000.0,
                )
            ],
        )

    def get_klines(self, symbol, timeframe):
        assert symbol == "BTCUSDT"
        assert timeframe == "1m"
        return [
            Kline(
                timestamp=START_TS,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000.0,
            ),
            Kline(
                timestamp=START_TS + 120,
                open=102.0,
                high=103.0,
                low=101.0,
                close=102.0,
                volume=1000.0,
            ),
        ]

    def get_trades(self, symbol):
        return []


def test_universe_data_quality_failure_is_scanned_and_logged_to_pipeline_report(tmp_path):
    provider = UniverseQualityFailureProvider()
    scheduler = RuntimeScanScheduler.from_config(
        _runtime_config(tmp_path),
        registry=_registry(provider),
        universe_provider=provider,
    )

    requests = scheduler.build_requests(index=1)

    assert provider.last_universe_filter.venue == "okx"
    assert [request.symbol for request in requests] == ["BTCUSDT"]
    assert requests[0].metadata["scan_sources"] == ["universe"]

    batch = scheduler.run_once(
        requested_at=START_TS + 600,
        index=1,
        batch_id="contract-data-scanner-logging",
    )

    assert batch.metadata["scan_scheduler"]["universe_snapshot_id"] == "unit-universe-1700000000"
    assert batch.metadata["scan_scheduler"]["universe_symbols"] == ["BTCUSDT"]
    assert batch.requests[0].metadata["scan_sources"] == ["universe"]

    report = batch.reports[0]
    stages = {stage.stage: stage for stage in report.stages}
    assert stages["data"].status == PipelineStageStatus.SUCCEEDED
    assert stages["data_quality"].status == PipelineStageStatus.REJECTED
    assert stages["data_quality"].rejection.code == "missing_kline"
    assert stages["feature"].status == PipelineStageStatus.SKIPPED
    assert stages["feature"].skip_reason == "quality_report_failed: data quality rejected klines"
    assert report.final_output["quality_report"]["issues"][0]["timestamp"] == START_TS + 60

    runtime_health = RuntimeHealthSnapshot.from_payload(report.metadata["runtime_health"])
    assert runtime_health.status == RuntimeHealthStatus.DEGRADED
    assert "data_quality_rejection" in runtime_health.alerts

    run_artifact = report.metadata["pipeline_report_artifact"]
    restored_run = PipelineRunReport.from_payload(_read_json(run_artifact["report_path"]))
    assert run_artifact["type"] == "run"
    assert restored_run.context.run_id == "contract-data-scanner-logging:0:BTCUSDT:1m"
    assert {
        stage.stage: stage.status for stage in restored_run.stages
    }["data_quality"] == PipelineStageStatus.REJECTED
    assert Path(run_artifact["latest_report_path"]).exists()

    batch_artifact = batch.metadata["pipeline_report_artifact"]
    restored_batch = PipelineBatchRunReport.from_payload(_read_json(batch_artifact["report_path"]))
    assert batch_artifact["type"] == "batch"
    assert restored_batch.batch_id == "contract-data-scanner-logging"
    assert restored_batch.metadata["scan_scheduler"]["universe_source"] == "unit_contract_provider"
    assert Path(batch_artifact["latest_report_path"]).exists()


def _runtime_config(tmp_path):
    return RuntimeConfig(
        name="contract-data-scanner-logging",
        source=PayloadSource.PAPER,
        markets=[
            MarketConfig(symbol="BTCUSDT", timeframe="1m", provider="contract_provider"),
        ],
        strategies=[
            StrategyBinding(
                symbol="BTCUSDT",
                strategy="ma_crossover",
                route="default",
                parameters={"fast_window": 2, "slow_window": 3},
            )
        ],
        broker=BrokerConfig(mode=PayloadSource.PAPER, broker_plugin="simulated"),
        scan={
            "enabled": True,
            "interval_seconds": 600,
            "candidate_symbols": [],
            "holding_symbols": [],
            "universe_enabled": True,
            "universe_max_symbols": 1,
        },
        logging={"pipeline_report_dir": str(tmp_path / "pipeline-runs")},
    )


def _registry(provider):
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "contract_provider", lambda: provider)
    return registry


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- merged from test_contract_decision_risk_portfolio_execution.py ---
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.models.crypto import CryptoAccount
from quant.account.portfolio_engine import PortfolioEngine
from quant.execution.broker import BrokerAdapter
from quant.execution.engine import ExecutionEngine
from quant.orchestration.runtime import BrokerExecutionHandler, LiveOrderGate
from quant.risk.risk_manager import RiskManager
from quant.schemas import (
    AssetClass,
    BrokerOrderResult,
    DecisionAction,
    DecisionIntent,
    OrderIntent,
    OrderKind,
    OrderStatus,
    PayloadSource,
    PortfolioAllocationRequest,
    PortfolioOrderRequest,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def test_decision_intent_does_not_emit_executable_order_before_risk_approval():
    decision = _decision_intent()

    with pytest.raises(ValueError, match="without explicit risk approval"):
        decision.to_order_intent()


def test_order_intent_schema_rejects_unapproved_execution_payload():
    with pytest.raises(ValueError, match="risk approved before execution"):
        OrderIntent(
            order_intent_id="intent-unapproved",
            decision_id="decision-unapproved",
            client_order_id="client-unapproved",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=1.0,
            time_in_force=TimeInForce.GTC,
            risk_approved=False,
            created_at=1710000000,
            trace=_risk_trace(),
        )


def test_decision_to_execution_contract_requires_risk_then_portfolio_approval():
    decision = _decision_intent()
    account = CryptoAccount(initial_balance=10000.0)
    risk_decision = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02).evaluate(
        _risk_signal_from_decision(decision),
        account,
        price=100.0,
    )

    assert risk_decision.approved is True
    assert risk_decision.order_intent is not None
    assert risk_decision.order_intent.risk_approved is True
    assert "position_sizing" in risk_decision.reason_codes

    allocation_decision = PortfolioEngine().allocate(
        _portfolio_request(risk_decision.order_intent, risk_decision.reason_codes)
    )
    allocation = allocation_decision.allocations[0]

    assert allocation_decision.approved is True
    assert allocation.approved is True
    assert allocation.client_order_id == risk_decision.order_intent.client_order_id
    assert allocation.allocated_quantity == risk_decision.order_intent.quantity
    assert "portfolio_order_approved" in allocation.reason_codes

    execution_result = ExecutionEngine(seed=1).on_order_intent(
        risk_decision.order_intent,
        price=100.0,
        index=5,
    )

    assert execution_result["status"] == "filled"
    assert execution_result["client_order_id"] == risk_decision.order_intent.client_order_id
    assert execution_result["symbol"] == allocation.symbol
    assert execution_result["side"] == _enum_value(allocation.side)
    assert execution_result["filled_qty"] == allocation.allocated_quantity


def test_rejected_risk_decision_has_no_portfolio_or_execution_order():
    account = CryptoAccount(initial_balance=10000.0)
    risk_decision = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02).evaluate(
        {"signal": "hold", "signal_index": 4, "decision_id": "decision-rejected"},
        account,
        price=100.0,
    )

    assert risk_decision.approved is False
    assert risk_decision.order_intent is None
    assert risk_decision.reason_codes == ["invalid_signal"]

    allocation_decision = PortfolioEngine().allocate(
        PortfolioAllocationRequest(
            allocation_id="allocation-rejected-risk",
            timestamp=1710000240,
            account_equity=10000.0,
            available_cash=5000.0,
            orders=[],
            min_notional=10.0,
        )
    )

    assert allocation_decision.approved is False
    assert allocation_decision.allocations == []
    assert allocation_decision.reason_codes == ["no_orders"]


def test_portfolio_rejection_prevents_execution_submission():
    decision = _decision_intent(decision_id="decision-portfolio-reject")
    account = CryptoAccount(initial_balance=10000.0)
    risk_decision = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02).evaluate(
        _risk_signal_from_decision(decision),
        account,
        price=100.0,
    )

    allocation_decision = PortfolioEngine().allocate(
        _portfolio_request(
            risk_decision.order_intent,
            risk_decision.reason_codes,
            min_notional=2000.0,
        )
    )
    allocation = allocation_decision.allocations[0]

    assert risk_decision.approved is True
    assert allocation_decision.approved is False
    assert allocation.approved is False
    assert allocation.allocated_quantity == 0.0
    assert "portfolio_allocation_below_minimum" in allocation.reason_codes


def test_risk_decision_logs_replayable_reason_codes_for_approved_and_rejected_paths():
    account = CryptoAccount(initial_balance=10000.0)
    logger = RecordingRiskLogger()
    manager = RiskManager(
        max_position_pct=0.1,
        stop_loss_pct=0.02,
        risk_logger=logger,
        run_id="contract-hqa011-risk-log",
    )

    approved_decision = manager.evaluate(
        _risk_signal_from_decision(_decision_intent("decision-risk-log-approved")),
        account,
        price=100.0,
    )
    rejected_decision = manager.evaluate(
        {
            "signal": "hold",
            "symbol": "BTCUSDT",
            "signal_index": 7,
            "timestamp": 1710000420,
            "decision_id": "decision-risk-log-rejected",
            "trace": _risk_trace(),
        },
        account,
        price=100.0,
    )

    assert approved_decision.approved is True
    assert rejected_decision.approved is False
    assert rejected_decision.reason_codes == ["invalid_signal"]
    assert len(logger.records) == 2

    approved_record, rejected_record = logger.records
    approved_payload = approved_record.to_payload()
    rejected_payload = rejected_record.to_payload()

    assert approved_payload["approved"] is True
    assert "position_sizing" in approved_payload["reason_codes"]
    assert approved_payload["risk_decision"]["order_intent"]["risk_approved"] is True
    assert approved_payload["trace"]["run_id"] == "contract-hqa011"

    assert rejected_payload["approved"] is False
    assert rejected_payload["reason_codes"] == ["invalid_signal"]
    assert rejected_payload["risk_decision"]["rejections"][0]["fatal"] is True
    assert rejected_payload["decision_id"] == "decision-risk-log-rejected"
    assert rejected_payload["metadata"]["price"] == 100.0


def test_live_order_gate_rejection_blocks_broker_adapter_place_order():
    broker = CountingBrokerAdapter()
    order_intent = _approved_order_intent("live-gate-blocked")
    handler = BrokerExecutionHandler(
        broker,
        live_order_gate=LiveOrderGate(
            {"allow_live_orders": False, "require_manual_preflight": False},
            risk_manager=HealthyRiskManager(),
            clock=lambda: 1710000300,
        ),
    )

    result = handler.on_order_intent(order_intent, price=100.0, index=6)

    assert broker.requests == []
    assert result["status"] == "rejected"
    assert result["live_orders_sent"] is False
    assert result["rejection_code"] == "live_order_gate_rejected"
    assert "allow_live_orders_disabled" in result["live_order_gate"]["reason_codes"]


def _decision_intent(decision_id="decision-001"):
    return DecisionIntent(
        decision_id=decision_id,
        timestamp=1710000000,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        action=DecisionAction.OPEN_LONG,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        confidence=0.72,
        reason_codes=["ma_cross_up"],
        trace=_risk_trace(),
    )


def _risk_signal_from_decision(decision):
    return {
        "signal": "buy",
        "symbol": decision.symbol,
        "signal_index": 4,
        "timestamp": decision.timestamp,
        "decision_id": decision.decision_id,
        "client_order_id": f"{decision.decision_id}:risk-approved",
        "trace": decision.trace,
    }


def _portfolio_request(order_intent, reason_codes, min_notional=10.0):
    return PortfolioAllocationRequest(
        allocation_id="allocation-001",
        timestamp=1710000060,
        account_equity=10000.0,
        available_cash=5000.0,
        orders=[
            PortfolioOrderRequest(
                strategy_id="ma_crossover",
                order_intent=order_intent,
                reference_price=100.0,
                reason_codes=reason_codes,
            )
        ],
        max_symbol_weight=0.25,
        max_strategy_weight=0.25,
        max_correlation_group_weight=0.40,
        min_notional=min_notional,
        trace=order_intent.trace,
    )


def _approved_order_intent(client_order_id):
    return OrderIntent(
        order_intent_id=f"intent-{client_order_id}",
        decision_id=f"decision-{client_order_id}",
        client_order_id=client_order_id,
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        time_in_force=TimeInForce.GTC,
        risk_approved=True,
        created_at=1710000000,
        trace=_risk_trace(),
    )


def _risk_trace():
    return TraceContext(
        run_id="contract-hqa011",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000000,
        bar_index=4,
    )


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


class HealthyRiskManager:
    kill_switch_enabled = False


class RecordingRiskLogger:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


class CountingBrokerAdapter(BrokerAdapter):
    @property
    def name(self):
        return "counting-live-broker"

    def __init__(self):
        self.requests = []

    def place_order(self, request):
        self.requests.append(request)
        return BrokerOrderResult(
            client_order_id=request.client_order_id,
            broker_order_id="broker-1",
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.ACCEPTED,
            requested_qty=request.quantity,
        )

    def cancel_order(self, client_order_id):
        raise NotImplementedError

    def replace_order(self, request):
        raise NotImplementedError

    def get_order(self, client_order_id):
        raise NotImplementedError

    def list_open_orders(self, symbol=None):
        return []


# --- merged from test_contract_feature_regime_logging_analytics.py ---
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.analytics import DailyReviewReporter
from quant.logging import JsonlTradeLogger
from quant.regime.rule_detector import RuleBasedRegimeDetector
from quant.schemas import (
    AssetClass,
    DecisionAction,
    DecisionIntent,
    DecisionLogRecord,
    FeatureSnapshot,
    FillLogRecord,
    OrderKind,
    PayloadSource,
    RegimeKind,
    TimeInForce,
    TraceContext,
    TradeSide,
)
from quant.strategy.ma_crossover import MACrossoverStrategy
from quant.strategy.router import RegimeStrategyRouter


def test_feature_snapshot_replays_through_regime_strategy_decision_log_and_daily_review(tmp_path):
    trace = TraceContext(
        run_id="feature-contract-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000120,
        bar_index=2,
    )
    feature_series = {
        "fast_ma": [100.0, 99.0, 101.0],
        "slow_ma": [100.0, 100.0, 100.0],
        "trend_strength": [0.0, 0.0, 0.02],
        "volatility": [0.01, 0.01, 0.01],
        "funding_rate": [0.0, 0.0, 0.0002],
    }
    snapshot = FeatureSnapshot.from_feature_series(
        feature_series,
        2,
        snapshot_id="features-btc-2",
        timestamp=1710000120,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000120,
        feature_set_id="ma_orderflow_contract",
        feature_set_version="1.0.0",
        source_window_start=1710000000,
        source_window_end=1710000120,
        trace=trace,
    )

    regime = RuleBasedRegimeDetector(
        trend_threshold=0.01,
        volatility_threshold=0.05,
    ).detect(snapshot)
    routed = RegimeStrategyRouter(
        {RegimeKind.TREND: MACrossoverStrategy(strategy_version="1.0.0")}
    ).route(regime)
    signal = routed.strategy.generate_signal(feature_series, index=2)

    assert regime.regime == _value(RegimeKind.UPTREND_LOW_VOL)
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.TREND
    assert routed.decision["legacy_route_used"] is True
    assert routed.decision["resolved_regime"] == _value(RegimeKind.TREND)
    assert regime.trace == trace
    assert routed.route.trace == trace
    assert signal.side == _value(TradeSide.BUY)
    assert signal.reason_codes == ["ma_cross"]

    decision = DecisionIntent(
        decision_id="decision-feature-contract-001",
        timestamp=snapshot.timestamp,
        symbol=snapshot.symbol,
        asset_class=AssetClass.CRYPTO,
        strategy_id=signal.strategy_id,
        strategy_version=signal.strategy_version,
        regime=_value(regime.regime),
        action=DecisionAction.OPEN_LONG,
        order_type=OrderKind.MARKET,
        quantity=0.25,
        time_in_force=TimeInForce.GTC,
        confidence=regime.confidence,
        reason_codes=signal.reason_codes + regime.reason_codes + routed.route.reason_codes,
        trace=snapshot.trace,
    )
    decision_record = DecisionLogRecord(
        event_id="event-decision-feature-contract-001",
        run_id=trace.run_id,
        timestamp=decision.timestamp,
        trace=trace,
        decision=decision,
        feature_snapshot=snapshot,
    )
    fill_record = FillLogRecord(
        event_id="event-fill-feature-contract-001",
        run_id=trace.run_id,
        timestamp=1710000180,
        trace=trace,
        fill_id="fill-feature-contract-001",
        order_id="order-feature-contract-001",
        client_order_id="client-feature-contract-001",
        symbol=snapshot.symbol,
        side=TradeSide.BUY,
        filled_quantity=0.25,
        fill_price=101.0,
        commission=0.02,
        decision_id=decision.decision_id,
        metadata={"realized_pnl": 7.5},
    )

    logger = JsonlTradeLogger(tmp_path / "contract-trade-log.jsonl")
    logger.append(decision_record)
    logger.append(fill_record)
    restored_records = logger.read_all()

    assert restored_records[0].feature_snapshot.snapshot_id == "features-btc-2"
    assert restored_records[0].feature_snapshot.values["trend_strength"] == 0.02
    assert restored_records[0].feature_snapshot.trace.run_id == "feature-contract-001"

    report = DailyReviewReporter().build_report(
        restored_records,
        report_id="daily-feature-contract-001",
        trading_date="2024-03-09",
    )

    assert _bucket(report, "strategy", "ma_crossover").net_pnl == 7.5
    assert _bucket(report, "regime", "uptrend_low_vol").net_pnl == 7.5
    assert _bucket(report, "reason", "ma_cross").net_pnl == 7.5
    assert _bucket(report, "reason", "trend_threshold_exceeded").net_pnl == 7.5
    assert _bucket(report, "feature", "trend_strength:positive").net_pnl == 7.5
    assert _bucket(report, "feature", "funding_rate:positive").winning_trades == 1
    assert "按特征分桶" in report.summary_text


def _bucket(report, bucket_type, bucket_value):
    matches = [
        item
        for item in report.buckets
        if item.bucket_type == bucket_type and item.bucket_value == bucket_value
    ]
    assert len(matches) == 1
    return matches[0]


def _value(value):
    return getattr(value, "value", value)


# --- merged from test_contract_logging_to_analytics.py ---
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.analytics import DailyReviewReporter
from quant.logging import JsonlTradeLogger, PipelineReportStore
from quant.schemas import (
    AIDecisionSuggestion,
    AIDecisionSuggestionLogRecord,
    AssetClass,
    DecisionAction,
    DecisionIntent,
    DecisionLogRecord,
    FillLogRecord,
    OrderKind,
    OrderLogRecord,
    OrderStatus,
    PayloadSource,
    PipelineBatchRunReport,
    PipelineRunContext,
    PipelineRunReport,
    PipelineStageResult,
    PipelineStageStatus,
    PipelineSymbolRunRequest,
    RiskDecision,
    RiskDecisionLogRecord,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def make_trace(run_id="logging-analytics-001", symbol="BTCUSDT"):
    return TraceContext(
        run_id=run_id,
        source=PayloadSource.PAPER,
        symbol=symbol,
        timeframe="1m",
        timestamp=1710000000,
        bar_index=12,
    )


def make_logging_decision(decision_id, reason_codes, *, symbol="BTCUSDT", strategy_id="ma_crossover"):
    return DecisionIntent(
        decision_id=decision_id,
        timestamp=1710000000,
        symbol=symbol,
        asset_class=AssetClass.CRYPTO,
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        regime="trend",
        action=DecisionAction.OPEN_LONG,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        time_in_force=TimeInForce.GTC,
        confidence=0.72,
        reason_codes=reason_codes,
        trace=make_trace(symbol=symbol),
    )


def find_bucket(report, bucket_type, bucket_value):
    matches = [
        bucket
        for bucket in report.buckets
        if bucket.bucket_type == bucket_type and bucket.bucket_value == bucket_value
    ]
    assert len(matches) == 1
    return matches[0]


def assert_no_secret_like_text(payload):
    text = json.dumps(payload, sort_keys=True).lower()
    for forbidden in ("api_key", "secret", "sk-live", "sk_test", "passphrase"):
        assert forbidden not in text


def test_logging_records_replay_into_daily_review_without_secrets(tmp_path):
    log_path = tmp_path / "logs" / "trade-events.jsonl"
    logger = JsonlTradeLogger(log_path)
    trace = make_trace()
    winning_decision = make_logging_decision("decision-win", ["ma_cross_up"])
    rejected_decision = make_logging_decision("decision-risk", ["max_drawdown_exceeded"])
    ai_suggestion = AIDecisionSuggestion(
        suggestion_id="ai-suggestion-001",
        timestamp=1710000000,
        candidate=winning_decision,
        advisor_name="fixture-advisor",
        model_name="fixture-model",
        prompt_id="prompt-v1",
        prompt_hash="sha256:prompt",
        raw_response_hash="sha256:response",
        metadata={"response_source": "fixture"},
    )
    records = [
        DecisionLogRecord(
            event_id="event-decision-win",
            run_id=trace.run_id,
            timestamp=1710000000,
            trace=trace,
            decision=winning_decision,
        ),
        AIDecisionSuggestionLogRecord(
            event_id="event-ai-suggestion",
            run_id=trace.run_id,
            timestamp=1710000000,
            trace=trace,
            suggestion=ai_suggestion,
        ),
        DecisionLogRecord(
            event_id="event-decision-risk",
            run_id=trace.run_id,
            timestamp=1710000001,
            trace=trace,
            decision=rejected_decision,
        ),
        RiskDecisionLogRecord(
            event_id="event-risk-reject",
            run_id=trace.run_id,
            timestamp=1710000002,
            trace=trace,
            symbol="BTCUSDT",
            approved=False,
            reason_codes=["max_drawdown_exceeded"],
            risk_decision=RiskDecision.reject(
                "max_drawdown_exceeded",
                "account drawdown exceeded configured maximum",
                fatal=True,
            ),
            strategy_id="ma_crossover",
            decision_id="decision-risk",
        ),
        OrderLogRecord(
            event_id="event-order-rejected",
            run_id=trace.run_id,
            timestamp=1710000003,
            trace=trace,
            order_id="order-risk",
            client_order_id="client-risk",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            status=OrderStatus.REJECTED,
            quantity=1.0,
            remaining_quantity=1.0,
            decision_id="decision-risk",
            metadata={"error": "risk rejected"},
        ),
        FillLogRecord(
            event_id="event-fill-win",
            run_id=trace.run_id,
            timestamp=1710000004,
            trace=trace,
            fill_id="fill-win",
            order_id="order-win",
            client_order_id="client-win",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            filled_quantity=1.0,
            fill_price=100.0,
            commission=0.2,
            decision_id="decision-win",
            metadata={"realized_pnl": 5.0},
        ),
    ]

    for record in records:
        logger.append(record)

    restored = logger.read_all()
    report = DailyReviewReporter().build_report(
        restored,
        report_id="daily-from-log",
        trading_date="2024-03-09",
    )

    assert log_path.exists()
    assert [item.record_type for item in restored] == [
        "decision",
        "ai_decision_suggestion",
        "decision",
        "risk",
        "order",
        "fill",
    ]
    assert report.fill_count == 1
    assert report.rejection_count == 1
    assert report.total_net_pnl == 5.0
    assert find_bucket(report, "reason", "max_drawdown_exceeded").rejection_count == 1
    assert find_bucket(report, "strategy", "ma_crossover").net_pnl == 5.0
    assert_no_secret_like_text([item.to_payload() for item in restored])


def test_pipeline_report_store_writes_latest_pointers_without_secrets(tmp_path):
    store = PipelineReportStore(tmp_path / "pipeline-reports")
    context = PipelineRunContext(
        run_id="paper-run-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        started_at=1710000000,
    )
    stage = PipelineStageResult(
        stage="logging",
        status=PipelineStageStatus.SUCCEEDED,
        started_at=1710000001,
        ended_at=1710000002,
        input_payload={"record_types": ["decision", "order", "fill"]},
        output_payload={"log_path": "logs/paper-run-001.jsonl"},
    )
    report = PipelineRunReport(
        context=context,
        stages=[stage],
        finished_at=1710000003,
        success=True,
        final_output={"status": "logged"},
    )
    batch = PipelineBatchRunReport(
        batch_id="batch-001",
        source=PayloadSource.PAPER,
        requested_at=1710000000,
        requests=[PipelineSymbolRunRequest(symbol="BTCUSDT", timeframe="1m")],
        reports=[report],
        success=True,
    )

    stored_report = store.write_run_report(report)
    stored_batch = store.write_batch_report(batch)

    run_payload = json.loads(store.latest_run_path.read_text(encoding="utf-8"))
    batch_payload = json.loads(store.latest_batch_path.read_text(encoding="utf-8"))

    assert store.run_report_path("paper-run-001").exists()
    assert store.batch_report_path("batch-001").exists()
    assert stored_report.metadata["pipeline_report_artifact"]["latest_report_path"].endswith(
        "latest-run.json"
    )
    assert stored_batch.metadata["pipeline_report_artifact"]["latest_report_path"].endswith(
        "latest-batch.json"
    )
    assert run_payload["metadata"]["pipeline_report_artifact"]["type"] == "run"
    assert batch_payload["metadata"]["pipeline_report_artifact"]["type"] == "batch"
    assert_no_secret_like_text(run_payload)
    assert_no_secret_like_text(batch_payload)


def test_jsonl_bad_line_and_empty_daily_review_are_replayable_boundaries(tmp_path):
    bad_log_path = tmp_path / "bad-log.jsonl"
    bad_log_path.write_text("{not-json}\n", encoding="utf-8")

    try:
        JsonlTradeLogger(bad_log_path).read_all()
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("malformed JSONL lines must fail replay explicitly")

    empty_report = DailyReviewReporter().build_report(
        [],
        report_id="daily-empty",
        trading_date="2024-03-09",
    )
    empty_payload = empty_report.to_payload()

    assert empty_report.run_id == "unknown"
    assert empty_report.fill_count == 0
    assert empty_report.rejection_count == 0
    assert "# " in empty_report.summary_text
    assert "无记录" in empty_report.summary_text
    assert empty_payload["summary_text"] == empty_report.summary_text
    assert_no_secret_like_text(empty_payload)


# --- merged from test_contract_optimization_to_lifecycle.py ---
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.optimization import (
    DailyReviewOptimizationPlanner,
    StrategyLifecycleManager,
    StrategyVersionGate,
    SymbolOptimizationQueue,
)
from quant.schemas import (
    DailyReviewBucket,
    DailyReviewReport,
    StrategyDeploymentRecord,
    StrategyLifecycleAction,
    StrategyLifecycleStatus,
    StrategyPromotionAction,
    StrategyValidationMetrics,
    StrategyValidationSlice,
    StrategyValidationSliceKind,
)


def make_bucket(bucket_type, bucket_value, net_pnl, *, fill_count=4):
    return DailyReviewBucket(
        bucket_type=bucket_type,
        bucket_value=bucket_value,
        gross_pnl=net_pnl + 0.2,
        fees=0.2,
        net_pnl=net_pnl,
        average_net_pnl=net_pnl / fill_count,
        win_rate=0.75,
        sharpe=1.4,
        max_drawdown=1.0,
        fill_count=fill_count,
        winning_trades=3,
        losing_trades=1,
    )


def make_report():
    return DailyReviewReport(
        report_id="daily-life-001",
        run_id="paper-life-001",
        trading_date="2024-03-09",
        generated_at=1710003600,
        buckets=[
            make_bucket("symbol", "BTCUSDT", 20.0),
            make_bucket("strategy", "ma_crossover", 18.0),
            make_bucket("regime", "trend", 16.0),
            make_bucket("feature", "funding_rate:positive", 14.0),
        ],
        total_net_pnl=20.0,
        fill_count=4,
        winning_trades=3,
        losing_trades=1,
    )


def make_passing_metrics(report, symbol_bucket, strategy_bucket, candidate):
    return StrategyValidationMetrics(
        report_id=f"{report.report_id}:{candidate.version}:oos-wf-mc",
        generated_at=report.generated_at + 60,
        trade_count=30,
        total_net_pnl=120.0,
        max_drawdown=1.0,
        win_rate=0.62,
        sharpe_ratio=1.5,
        validation_slices=[
            StrategyValidationSlice(
                name="oos-2024-q1",
                kind=StrategyValidationSliceKind.OUT_OF_SAMPLE,
                trade_count=10,
                total_net_pnl=32.0,
                max_drawdown=0.4,
                win_rate=0.6,
                sharpe_ratio=1.1,
            ),
            StrategyValidationSlice(
                name="walk-forward-001",
                kind=StrategyValidationSliceKind.WALK_FORWARD,
                trade_count=10,
                total_net_pnl=30.0,
                max_drawdown=0.5,
                win_rate=0.61,
                sharpe_ratio=1.0,
            ),
        ],
        monte_carlo_survival_rate=0.91,
    )


def make_deployment(candidate):
    return StrategyDeploymentRecord(
        deployment_id="deploy-review-candidate",
        strategy_id=candidate.strategy_id,
        version=candidate.version,
        status=StrategyLifecycleStatus.CANDIDATE,
        environment="paper",
        symbol="BTCUSDT",
    )


def test_approved_optimization_queue_record_promotes_lifecycle_to_approved(tmp_path):
    gate = StrategyVersionGate(
        min_trades=10,
        min_net_pnl=50.0,
        require_out_of_sample=True,
        min_walk_forward_windows=1,
        min_walk_forward_pass_rate=0.5,
        min_monte_carlo_survival_rate=0.8,
    )
    queue = SymbolOptimizationQueue(tmp_path / "queue")
    records = DailyReviewOptimizationPlanner(gate=gate).enqueue_from_report(
        make_report(),
        queue,
        validation_metrics_factory=make_passing_metrics,
    )
    queue_record = records[0]

    updated, transitions = StrategyLifecycleManager().promote_from_optimization_queue(
        queue=queue,
        symbol=queue_record.symbol,
        queue_id=queue_record.queue_id,
        record=make_deployment(queue_record.candidate),
        transition_id_prefix="life-from-optimization",
        generated_at=1710007200,
    )

    assert queue_record.promotion_decision.action == StrategyPromotionAction.APPROVE
    assert updated.status == StrategyLifecycleStatus.APPROVED
    assert [transition.action for transition in transitions] == [
        StrategyLifecycleAction.START_BACKTEST,
        StrategyLifecycleAction.START_PAPER,
        StrategyLifecycleAction.APPROVE,
    ]
    assert transitions[-1].reason_codes == ["promotion_gate_passed"]


def test_rejected_optimization_queue_record_cannot_promote_lifecycle(tmp_path):
    gate = StrategyVersionGate(
        require_out_of_sample=True,
        min_walk_forward_windows=1,
        min_walk_forward_pass_rate=0.5,
        min_monte_carlo_survival_rate=0.8,
    )
    queue = SymbolOptimizationQueue(tmp_path / "queue")
    queue_record = DailyReviewOptimizationPlanner(gate=gate).enqueue_from_report(
        make_report(),
        queue,
    )[0]

    with pytest.raises(ValueError, match="rejected candidate"):
        StrategyLifecycleManager().promote_from_optimization_queue(
                queue=queue,
            symbol=queue_record.symbol,
            queue_id=queue_record.queue_id,
            record=make_deployment(queue_record.candidate),
            transition_id_prefix="life-blocked",
            generated_at=1710007200,
        )


# --- merged from test_contract_regime_to_strategy.py ---
import pytest

from quant.schemas import PayloadSource, RegimeKind, RegimeSnapshot, TraceContext
from quant.strategy.router import RegimeStrategyRouter, StrategyRouteNotFound


class _DummyStrategy:
    def __init__(self, strategy_id, strategy_version="1.0.0"):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version

    def generate_signal(self, features, index):
        return None


def test_regime_snapshot_routes_to_strategy_with_trace_and_reason_code():
    strategy = _DummyStrategy("trend_follow", "2.1.0")
    router = RegimeStrategyRouter(
        {RegimeKind.TREND: strategy},
        fallback=_DummyStrategy("capital_protection", "1.0.0"),
    )

    routed = router.route(_regime_route_snapshot(RegimeKind.TREND))

    assert routed.strategy is strategy
    assert routed.route.strategy_id == "trend_follow"
    assert routed.route.strategy_version == "2.1.0"
    assert routed.route.regime == _value(RegimeKind.TREND)
    assert routed.route.reason_codes == ["regime:trend"]
    assert routed.route.trace.run_id == "contract-regime-strategy-001"


def test_regime_router_requires_explicit_fallback_for_unmapped_regime():
    router = RegimeStrategyRouter({RegimeKind.TREND: _DummyStrategy("trend_follow")})

    with pytest.raises(StrategyRouteNotFound):
        router.route(_regime_route_snapshot(RegimeKind.RANGE))


def _regime_route_snapshot(regime):
    return RegimeSnapshot(
        regime_id=f"regime-contract-{_value(regime)}",
        timestamp=1710000120,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000120,
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        regime=regime,
        confidence=0.66,
        reason_codes=[f"{_value(regime)}_contract"],
        trace=TraceContext(
            run_id="contract-regime-strategy-001",
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=1710000120,
            bar_index=3,
        ),
    )


def _value(value):
    return getattr(value, "value", value)


# --- merged from test_contract_runtime_pipeline_report.py ---
import json

from quant.config import BrokerConfig, MarketConfig, RuntimeConfig, StrategyBinding
from quant.data.schemas.market import Kline
from quant.orchestration import TradingRuntimeOrchestrator
from quant.registry import PluginKind, PluginRegistry
from quant.schemas import (
    PayloadSource,
    PipelineRuntimeRequest,
    PipelineStageStatus,
    RuntimeHealthSnapshot,
    RuntimeHealthStatus,
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


class FlatProvider:
    def get_klines(self, symbol, timeframe):
        return [
            Kline(
                timestamp=1700000000 + index * 60,
                open=10.0,
                high=10.5,
                low=9.5,
                close=10.0,
                volume=1000.0,
            )
            for index in range(5)
        ]

    def get_trades(self, symbol):
        return []


def test_runtime_pipeline_report_preserves_stage_io_status_and_runtime_health(tmp_path):
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        paper_provider=CrossingProvider(),
        feature_windows=(2, 3),
        pipeline_report_dir=tmp_path / "pipeline-runs",
    )

    report = runtime.run(
        PipelineRuntimeRequest(
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id="contract-runtime-report",
        )
    )

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
    for stage in report.stages:
        assert stage.status == PipelineStageStatus.SUCCEEDED
        assert stage.input_payload, stage.stage
        assert stage.output_payload, stage.stage
        assert stage.rejection is None
        assert stage.error is None
        assert stage.skip_reason is None

    runtime_health = RuntimeHealthSnapshot.from_payload(report.metadata["runtime_health"])
    assert runtime_health.status == RuntimeHealthStatus.HEALTHY
    assert runtime_health.run_id == "contract-runtime-report"
    assert runtime_health.source == PayloadSource.PAPER
    assert set(runtime_health.pipeline_stage_durations_ms) == {
        stage.stage for stage in report.stages
    }
    assert runtime_health.metadata["stage_count"] == len(report.stages)
    assert report.metadata["pipeline_report_artifact"]["type"] == "run"


def test_runtime_from_config_file_loads_and_runs_pipeline_report(tmp_path):
    config = RuntimeConfig(
        name="contract-runtime-file",
        source=PayloadSource.PAPER,
        markets=[MarketConfig(symbol="BTCUSDT", timeframe="1m", provider="crossing")],
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
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps(config.to_payload()), encoding="utf-8")
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "crossing", lambda: CrossingProvider())

    runtime = TradingRuntimeOrchestrator.from_config_file(config_path, registry=registry)
    report = runtime.run(
        {
            "source": "paper",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "index": 4,
            "run_id": "contract-runtime-file-run",
        }
    )

    assert report.success is True
    assert report.context.run_id == "contract-runtime-file-run"
    assert report.stages[0].stage == "data"
    assert report.stages[-1].stage == "logging"
    assert report.metadata["pipeline_report_artifact"]["type"] == "run"


def test_runtime_pipeline_report_requires_reason_for_skipped_stages():
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        paper_provider=FlatProvider(),
        feature_windows=(2, 3),
    )

    report = runtime.run(
        {
            "source": "paper",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "index": 4,
            "run_id": "contract-runtime-skipped",
        }
    )

    skipped = [stage for stage in report.stages if stage.status == PipelineStageStatus.SKIPPED]

    assert report.success is True
    assert [stage.stage for stage in skipped] == [
        "decision",
        "portfolio",
        "risk",
        "execution",
        "logging",
    ]
    assert skipped[0].skip_reason == "strategy produced no signal"
    assert skipped[1].skip_reason == "no trade intent"
    assert skipped[2].skip_reason == "no capital budget from decision"
    assert skipped[3].skip_reason == "no order intent"
    assert skipped[4].skip_reason == "no trade event to log"
    assert all(stage.error is None for stage in skipped)
    assert all(stage.rejection is None for stage in skipped)


def test_runtime_pipeline_report_requires_error_reason_for_unsupported_source():
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        backtest_provider=CrossingProvider(),
        paper_provider=CrossingProvider(),
        feature_windows=(2, 3),
    )

    report = runtime.run(
        {
            "source": "live",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "index": 4,
            "run_id": "contract-runtime-unsupported",
            "metadata": {"requested_at": 1700000600},
        }
    )

    stage = report.stages[0]
    assert report.success is False
    assert stage.stage == "orchestration"
    assert stage.status == PipelineStageStatus.ERROR
    assert stage.input_payload["symbol"] == "BTCUSDT"
    assert "live runtime handler is not configured" in stage.error
    assert report.errors == [stage.error]
    assert report.metadata["runtime_entrypoint"] == "TradingRuntimeOrchestrator"


# --- merged from test_contract_strategy_to_decision.py ---
import pytest

from quant.schemas import (
    AIDecisionAdvisorRequest,
    AssetClass,
    DecisionAction,
    DecisionIntent,
    FeatureSnapshot,
    MarketType,
    OrderKind,
    PayloadSource,
    RegimeKind,
    RegimeSnapshot,
    StrategySignal,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def test_strategy_signal_can_seed_replayable_decision_intent_candidate():
    signal = _signal(TradeSide.BUY)
    regime = _regime(RegimeKind.TREND)

    decision = _decision_from_signal(signal, regime)

    assert decision.strategy_id == signal.strategy_id
    assert decision.strategy_version == signal.strategy_version
    assert decision.action == _value(DecisionAction.OPEN_LONG)
    assert decision.regime == "trend"
    assert decision.reason_codes == ["ma_cross", "trend_threshold_exceeded"]
    assert decision.trace == signal.trace

    with pytest.raises(ValueError, match="without explicit risk approval"):
        decision.to_order_intent()


def test_sell_strategy_signal_maps_to_close_long_decision_candidate():
    signal = _signal(TradeSide.SELL)
    decision = _decision_from_signal(signal, _regime(RegimeKind.RANGE))

    assert decision.action == _value(DecisionAction.CLOSE_LONG)
    assert decision.reduce_only is True
    assert decision.reason_codes == ["ma_cross", "no_trend_or_volatility_threshold"]


def test_strategy_signal_feature_and_regime_context_can_build_ai_advisor_request():
    signal = _signal(TradeSide.BUY)
    feature = FeatureSnapshot(
        snapshot_id="feature-strategy-decision-001",
        timestamp=1710000120,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000120,
        feature_set_id="technical",
        feature_set_version="1.0.0",
        values={"ma_fast": 101.0, "ma_slow": 100.0},
        trace=_trace(),
    )
    regime = _regime(RegimeKind.TREND)

    request = AIDecisionAdvisorRequest(
        request_id="strategy-ai-request-001",
        timestamp=1710000120,
        symbol=signal.symbol,
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.SPOT,
        timeframe="1m",
        model_name="fixture-json-model",
        trace=signal.trace,
        feature_context=feature.to_payload(),
        regime_context=regime.to_payload(),
        strategy_context=signal.to_payload(),
        portfolio_context={"position_side": "flat"},
        constraints={"must_remain_advice_only": True},
    )

    payload = request.to_payload()

    assert payload["strategy_context"]["signal_id"] == "ma_crossover:2:buy"
    assert payload["feature_context"]["snapshot_id"] == "feature-strategy-decision-001"
    assert payload["regime_context"]["reason_codes"] == ["trend_threshold_exceeded"]
    assert payload["constraints"]["must_remain_advice_only"] is True


def _decision_from_signal(signal, regime):
    is_buy = signal.side == _value(TradeSide.BUY)
    return DecisionIntent(
        decision_id=f"decision-from-{signal.signal_id}",
        timestamp=1710000120,
        symbol=signal.symbol,
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.SPOT,
        strategy_id=signal.strategy_id,
        strategy_version=signal.strategy_version,
        regime=_value(regime.regime),
        action=DecisionAction.OPEN_LONG if is_buy else DecisionAction.CLOSE_LONG,
        order_type=OrderKind.MARKET,
        quantity=0.25,
        time_in_force=TimeInForce.GTC,
        reduce_only=not is_buy,
        confidence=signal.confidence,
        reason_codes=list(signal.reason_codes) + list(regime.reason_codes),
        trace=signal.trace,
    )


def _signal(side):
    side_value = _value(side)
    return StrategySignal(
        signal_id=f"ma_crossover:2:{side_value}",
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        side=side,
        signal_index=2,
        execute_index=3,
        symbol="BTCUSDT",
        timeframe="1m",
        confidence=0.7,
        reason_codes=["ma_cross"],
        trace=_trace(),
    )


def _regime(regime):
    reason = "trend_threshold_exceeded" if regime == RegimeKind.TREND else "no_trend_or_volatility_threshold"
    return RegimeSnapshot(
        regime_id=f"regime-strategy-decision-{_value(regime)}",
        timestamp=1710000120,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000120,
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        regime=regime,
        confidence=0.65,
        reason_codes=[reason],
        trace=_trace(),
    )


def _trace():
    return TraceContext(
        run_id="contract-strategy-decision-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000120,
        bar_index=2,
    )


def _value(value):
    return getattr(value, "value", value)


# --- merged from test_layer_contracts.py ---
from pathlib import Path

from quant.qa.layer_contracts import check_layer_contracts


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_current_codebase_respects_layer_contracts():
    report = check_layer_contracts(PROJECT_ROOT)

    assert report.passed, [violation.to_payload() for violation in report.violations]
    assert report.scanned_files


def test_layer_contract_check_reports_forbidden_imports(tmp_path):
    bad_data_file = tmp_path / "quant" / "data" / "bad_provider.py"
    bad_data_file.parent.mkdir(parents=True)
    bad_data_file.write_text(
        "from quant.strategy.ma_crossover import MACrossoverStrategy\n",
        encoding="utf-8",
    )

    report = check_layer_contracts(tmp_path)

    assert not report.passed
    assert report.violations[0].rule_id == "data-no-upstream-imports"
    assert report.violations[0].path == "quant/data/bad_provider.py"
    assert report.violations[0].target.startswith("quant.strategy")


def test_layer_contract_check_reports_risk_order_calls(tmp_path):
    risk_file = tmp_path / "quant" / "risk" / "bad_risk.py"
    risk_file.parent.mkdir(parents=True)
    risk_file.write_text(
        "def evaluate(broker, request):\n"
        "    return broker.place_order(request)\n",
        encoding="utf-8",
    )

    report = check_layer_contracts(tmp_path)

    assert not report.passed
    violation = report.violations[0]
    assert violation.rule_id == "risk-no-ordering"
    assert violation.kind == "call"
    assert violation.target == "place_order"
