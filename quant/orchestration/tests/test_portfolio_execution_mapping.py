import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.account import CryptoAccount
from quant.data.providers.mock_provider import MockProvider
from quant.execution.broker import BrokerAdapter
from quant.execution.engine import ExecutionEngine
from quant.logging.jsonl import JsonlTradeLogger
from quant.orchestration.paper import PaperTradingOrchestrator
from quant.orchestration.runtime import BrokerExecutionHandler, LiveOrderGate
from quant.risk.risk_manager import RiskManager
from quant.schemas import (
    BracketExecutionPlan,
    BracketExecutionPolicy,
    BracketProtectiveLeg,
    BrokerOrderRequest,
    BrokerOrderResult,
    CapitalBudgetDecision,
    OrderIntent,
    OrderKind,
    OrderStatus,
    PayloadSource,
    PortfolioExecutionContext,
    RegimeKind,
    StrategySignal,
    TimeInForce,
    TraceContext,
    TradeSide,
)
from quant.strategy.router import RegimeStrategyRouter


def test_portfolio_context_flows_to_execution_result_and_trade_logs(tmp_path):
    report, logger, _engine = _run_mapping_pipeline(
        tmp_path,
        run_id="port-exec-approved",
    )

    risk_payload = _stage(report, "risk").output_payload["risk_decision"]
    risk_stage_payload = _stage(report, "risk").output_payload
    portfolio_context = risk_stage_payload["portfolio_execution_context"]
    execution_result = _stage(report, "execution").output_payload["execution_result"]

    assert portfolio_context["approved"] is True
    assert portfolio_context["risk_decision_id"] == risk_payload["risk_decision_id"]
    assert portfolio_context["client_order_id"] == risk_payload["order_intent"]["client_order_id"]
    assert portfolio_context["allocated_quantity"] == risk_payload["order_intent"]["quantity"]

    assert execution_result["allocation_id"] == portfolio_context["allocation_id"]
    assert execution_result["risk_decision_id"] == portfolio_context["risk_decision_id"]
    assert execution_result["client_order_id"] == portfolio_context["client_order_id"]
    assert execution_result["allocated_quantity"] == portfolio_context["allocated_quantity"]
    assert execution_result["portfolio_approved"] is True

    order_record = logger.read_by_type("order")[0]
    fill_record = logger.read_by_type("fill")[0]
    for record in (order_record, fill_record):
        assert record.metadata["allocation_id"] == portfolio_context["allocation_id"]
        assert record.metadata["risk_decision_id"] == portfolio_context["risk_decision_id"]
        assert record.metadata["portfolio_client_order_id"] == portfolio_context["client_order_id"]
        assert record.metadata["allocated_quantity"] == portfolio_context["allocated_quantity"]


def test_capped_portfolio_quantity_is_the_only_quantity_sent_to_execution(tmp_path):
    report, _logger, engine = _run_mapping_pipeline(
        tmp_path,
        run_id="port-exec-capped",
        capital_budget_allocator=HalfSizeCapitalBudgetAllocator(),
    )

    risk_payload = _stage(report, "risk").output_payload["risk_decision"]
    portfolio_payload = _stage(report, "portfolio").output_payload
    risk_stage_payload = _stage(report, "risk").output_payload
    execution_input = _stage(report, "execution").input_payload["order_intent"]
    execution_result = _stage(report, "execution").output_payload["execution_result"]
    portfolio_context = risk_stage_payload["portfolio_execution_context"]

    raw_quantity = risk_payload["sizing"]["raw_quantity"]
    assert risk_stage_payload["allocation_decision"]["approved"] is True
    assert "capital_budget_capped" in portfolio_payload["capital_budget"]["reason_codes"]
    assert 0.0 < portfolio_context["allocated_quantity"] < raw_quantity

    assert execution_input["quantity"] == portfolio_context["allocated_quantity"]
    assert execution_result["allocated_quantity"] == portfolio_context["allocated_quantity"]
    assert engine.orders[0].qty == portfolio_context["allocated_quantity"]


def test_rejected_portfolio_allocation_skips_execution_without_order(tmp_path):
    report, _logger, engine = _run_mapping_pipeline(
        tmp_path,
        run_id="port-exec-rejected",
        capital_budget_allocator=RejectingCapitalBudgetAllocator(),
    )

    portfolio_stage = _stage(report, "portfolio")
    execution_stage = _stage(report, "execution")

    assert _value(portfolio_stage.status) == "rejected"
    assert portfolio_stage.output_payload["capital_budget"]["approved"] is False
    assert portfolio_stage.output_payload["capital_budget"]["adjusted_risk_budget_usdt"] == 0.0
    assert _value(execution_stage.status) == "skipped"
    assert execution_stage.skip_reason == "capital budget rejected trade intent"
    assert engine.orders == []
    assert "execution_result" not in report.final_output


def test_live_broker_rejects_allocation_quantity_mismatch_without_request(tmp_path):
    broker = RecordingBroker()
    order_intent = _live_order_intent("live-port-mismatch")
    context = PortfolioExecutionContext(
        allocation_id="allocation-live-port-mismatch",
        approved=True,
        client_order_id=order_intent.client_order_id,
        risk_decision_id=f"risk:{order_intent.decision_id}",
        symbol=order_intent.symbol,
        side=order_intent.side,
        allocated_quantity=order_intent.quantity / 2,
        allocated_notional=50.0,
        reason_codes=["allocation_capped"],
    )
    handler = BrokerExecutionHandler(
        broker,
        live_order_gate=LiveOrderGate(
            _passing_live_gate_settings(tmp_path),
            risk_manager=HealthyRiskManager(),
            clock=lambda: 1700000010,
        ),
    )

    result = handler.on_order_intent(
        order_intent,
        price=100.0,
        index=1,
        portfolio_allocation=context,
        dry_run=False,
    )

    assert broker.requests == []
    assert result["status"] == "rejected"
    assert result["broker_called"] is False
    assert result["live_orders_sent"] is False
    assert result["allocation_id"] == context.allocation_id
    assert result["allocated_quantity"] == context.allocated_quantity
    assert result["rejection_code"] == "live_order_gate_rejected"
    assert "portfolio_allocated_quantity_mismatch" in result["live_order_gate"]["reason_codes"]


def test_live_bracket_entry_rejects_allocation_quantity_mismatch_before_request(tmp_path):
    broker = RecordingBroker()
    order_intent = _live_order_intent("live-port-mismatch-bracket")
    plan = BracketExecutionPlan(
        execution_plan_id=f"execution-plan-{order_intent.client_order_id}",
        idempotency_key=order_intent.client_order_id,
        risk_decision_id=f"risk:{order_intent.decision_id}",
        allocation_id="allocation-live-port-mismatch-bracket",
        entry_order=BrokerOrderRequest(
            client_order_id=order_intent.client_order_id,
            symbol=order_intent.symbol,
            side=order_intent.side,
            order_type=order_intent.order_type,
            quantity=order_intent.quantity,
            time_in_force=order_intent.time_in_force,
        ),
        stop_loss_order=BracketProtectiveLeg(
            client_order_id=f"{order_intent.client_order_id}:sl",
            price=95.0,
        ),
        take_profit_order=BracketProtectiveLeg(
            client_order_id=f"{order_intent.client_order_id}:tp",
            price=110.0,
        ),
        policy=BracketExecutionPolicy(),
        risk_approved=True,
    )
    context = PortfolioExecutionContext(
        allocation_id=plan.allocation_id,
        approved=True,
        client_order_id=order_intent.client_order_id,
        risk_decision_id=plan.risk_decision_id,
        symbol=order_intent.symbol,
        side=order_intent.side,
        allocated_quantity=order_intent.quantity / 2,
        allocated_notional=50.0,
        reason_codes=["allocation_capped"],
    )
    handler = BrokerExecutionHandler(
        broker,
        live_order_gate=LiveOrderGate(
            _passing_live_gate_settings(tmp_path),
            risk_manager=HealthyRiskManager(),
            clock=lambda: 1700000010,
        ),
    )

    result = handler.on_execution_order_plan(
        plan,
        price=100.0,
        index=1,
        portfolio_allocation=context,
        dry_run=False,
    )

    assert broker.requests == []
    assert result["status"] == "rejected"
    assert result["broker_called"] is False
    assert result["live_orders_sent"] is False
    assert result["allocation_id"] == context.allocation_id
    assert result["allocated_quantity"] == context.allocated_quantity
    assert result["bracket_execution_status"] == "REJECTED"
    assert "portfolio_allocated_quantity_mismatch" in result["reason_codes"]


def _run_mapping_pipeline(tmp_path, *, run_id, capital_budget_allocator=None):
    account = CryptoAccount(initial_balance=10000.0)
    engine = ExecutionEngine(seed=1, account=account)
    logger = JsonlTradeLogger(tmp_path / f"{run_id}.jsonl")
    orchestrator = PaperTradingOrchestrator(
        provider=MockProvider(),
        strategy_router=RegimeStrategyRouter(
            routes={RegimeKind.TREND: AlwaysBuyStrategy()},
            fallback=AlwaysBuyStrategy(),
        ),
        risk_manager=RiskManager(max_position_pct=0.10, stop_loss_pct=0.02),
        execution_engine=engine,
        account=account,
        capital_budget_allocator=capital_budget_allocator,
        logger=logger,
    )
    return orchestrator.run_tick(index=4, run_id=run_id), logger, engine


def _stage(report, name):
    return next(stage for stage in report.stages if stage.stage == name)


def _value(value):
    return value.value if hasattr(value, "value") else value


def _live_order_intent(client_order_id):
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
    )


def _passing_live_gate_settings(tmp_path):
    artifact_path = tmp_path / "latest-preflight.json"
    artifact_path.write_text(
        json.dumps(
            {
                "report_id": "production-rehearsal:1700000000",
                "generated_at": 1700000000,
                "success": True,
                "preflight_summary": {"failed_count": 0, "warning_count": 0, "check_count": 5},
                "metadata": {"live_orders_sent": False, "contains_real_credentials": False},
            }
        ),
        encoding="utf-8",
    )
    return {
        "allow_live_orders": True,
        "live_mode": True,
        "dry_run": False,
        "credential_mode": "env",
        "require_manual_preflight": True,
        "preflight_artifact_path": str(artifact_path),
        "preflight_max_age_seconds": 60,
    }


class AlwaysBuyStrategy:
    strategy_id = "portfolio_mapping_strategy"
    strategy_version = "1.0.0"

    def generate_signal(self, _features, index):
        return StrategySignal(
            signal_id=f"portfolio-map-signal-{index}",
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            side=TradeSide.BUY,
            signal_index=index,
            confidence=1.0,
            reason_codes=["portfolio_mapping_fixture"],
            trace=TraceContext(
                run_id="portfolio-execution-mapping",
                source=PayloadSource.PAPER,
                symbol="BTCUSDT",
                timeframe="1m",
                timestamp=1700000240,
                bar_index=index,
            ),
        )


class HalfSizeCapitalBudgetAllocator:
    def allocate(self, request):
        base = request.account_equity * request.base_risk_budget_pct
        adjusted = base * 0.5
        return CapitalBudgetDecision(
            budget_id=request.budget_id,
            approved=True,
            decision_id=request.trade_intent.decision_id,
            trade_intent_id=request.trade_intent.trade_intent_id,
            symbol=request.trade_intent.symbol,
            side=request.trade_intent.side,
            account_equity=request.account_equity,
            free_margin=request.free_margin,
            base_risk_budget_usdt=base,
            scaled_risk_budget_usdt=base,
            adjusted_risk_budget_usdt=adjusted,
            max_symbol_notional=adjusted,
            max_total_notional=request.account_equity,
            max_group_notional=request.account_equity,
            confidence_multiplier=1.0,
            volatility_multiplier=1.0,
            correlation_multiplier=1.0,
            constraint_caps={"max_symbol_notional": adjusted},
            reason_codes=list(request.reason_codes)
            + ["capital_budget_from_trade_intent", "capital_budget_capped", "capital_budget_approved"],
            input_refs={"trade_intent_id": request.trade_intent.trade_intent_id},
            trace=request.trace,
        )


class RejectingCapitalBudgetAllocator:
    def allocate(self, request):
        base = request.account_equity * request.base_risk_budget_pct
        return CapitalBudgetDecision(
            budget_id=request.budget_id,
            approved=False,
            decision_id=request.trade_intent.decision_id,
            trade_intent_id=request.trade_intent.trade_intent_id,
            symbol=request.trade_intent.symbol,
            side=request.trade_intent.side,
            account_equity=request.account_equity,
            free_margin=request.free_margin,
            base_risk_budget_usdt=base,
            scaled_risk_budget_usdt=base,
            adjusted_risk_budget_usdt=0.0,
            max_symbol_notional=0.0,
            max_total_notional=request.account_equity,
            max_group_notional=request.account_equity,
            confidence_multiplier=1.0,
            volatility_multiplier=1.0,
            correlation_multiplier=1.0,
            constraint_caps={"max_symbol_notional": 0.0},
            reason_codes=list(request.reason_codes) + ["capital_budget_below_minimum"],
            input_refs={"trade_intent_id": request.trade_intent.trade_intent_id},
            trace=request.trace,
        )


class HealthyRiskManager:
    kill_switch_enabled = False


class RecordingBroker(BrokerAdapter):
    @property
    def name(self):
        return "recording-broker"

    def __init__(self):
        self.requests = []

    def place_order(self, request):
        self.requests.append(request)
        return BrokerOrderResult(
            client_order_id=request.client_order_id,
            broker_order_id="broker-portfolio-map",
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
