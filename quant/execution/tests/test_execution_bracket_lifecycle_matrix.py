import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.bracket_orchestrator import BracketExecutionOrchestrator
from quant.execution.broker import BrokerAdapter
from quant.execution.idempotency import JsonIdempotencyRegistry
from quant.execution.order_store import SQLiteOrderStore
from quant.schemas.enums import OrderKind, OrderStatus, TimeInForce, TradeSide
from quant.schemas.execution import (
    BracketExecutionPlan,
    BracketExecutionPolicy,
    BracketExecutionStatus,
    BracketProtectiveLeg,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerProtectiveOrderResult,
    ExchangeReadinessRequest,
    InstrumentOrderRules,
    LiveOrderGateDecision,
)


class MatrixBroker(BrokerAdapter):
    def __init__(
        self,
        *,
        entry_status=OrderStatus.FILLED,
        entry_filled_qty=1.0,
        protective_status=OrderStatus.ACCEPTED,
        emergency_exit_status=OrderStatus.FILLED,
        poll_results=None,
        raise_on_missing_lookup=False,
    ):
        self.entry_status = entry_status
        self.entry_filled_qty = entry_filled_qty
        self.protective_status = protective_status
        self.emergency_exit_status = emergency_exit_status
        self.poll_results = list(poll_results or [])
        self.raise_on_missing_lookup = raise_on_missing_lookup
        self.instrument_rules = {}
        self.orders = {}
        self.entry_requests = []
        self.protective_requests = []
        self.emergency_exit_requests = []
        self.cancel_requests = []
        self.get_order_requests = []

    @property
    def name(self):
        return "fixture-matrix-broker"

    def place_order(self, request):
        if request.reduce_only:
            self.emergency_exit_requests.append(request)
            filled_qty = request.quantity if self.emergency_exit_status == OrderStatus.FILLED else 0.0
            result = BrokerOrderResult(
                client_order_id=request.client_order_id,
                broker_order_id=f"broker-{request.client_order_id}",
                symbol=request.symbol,
                side=request.side,
                status=self.emergency_exit_status,
                requested_qty=request.quantity,
                filled_qty=filled_qty,
                avg_fill_price=request.limit_price or 100.0,
                rejection_code=(
                    "emergency_exit_rejected"
                    if self.emergency_exit_status == OrderStatus.REJECTED
                    else None
                ),
            )
            self.orders[request.client_order_id] = result
            return result

        self.entry_requests.append(request)
        result = BrokerOrderResult(
            client_order_id=request.client_order_id,
            broker_order_id=f"broker-{request.client_order_id}",
            symbol=request.symbol,
            side=request.side,
            status=self.entry_status,
            requested_qty=request.quantity,
            filled_qty=self.entry_filled_qty,
            avg_fill_price=request.limit_price or 100.0,
        )
        self.orders[request.client_order_id] = result
        return result

    def cancel_order(self, client_order_id):
        self.cancel_requests.append(client_order_id)
        return BrokerOrderResult(
            client_order_id=client_order_id,
            broker_order_id=f"broker-{client_order_id}",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            status=OrderStatus.CANCELLED,
            requested_qty=1.0,
            filled_qty=0.0,
        )

    def replace_order(self, request):
        raise AssertionError("replace_order is outside the H-QA-026 bracket lifecycle matrix")

    def place_native_protective_order(self, request):
        self.protective_requests.append(request)
        return BrokerProtectiveOrderResult(
            protective_client_order_id=request.protective_client_order_id,
            parent_client_order_id=request.parent_client_order_id,
            broker_order_id=f"protective-{request.protective_client_order_id}",
            symbol=request.symbol,
            exit_side=request.exit_side(),
            native_order_type=request.metadata["native_order_type"],
            status=self.protective_status,
            requested_qty=request.quantity,
            stop_loss_price=request.stop_loss_price,
            take_profit_price=request.take_profit_price,
            stop_loss_client_order_id=request.stop_loss_client_order_id,
            take_profit_client_order_id=request.take_profit_client_order_id,
            live_order_gate=request.live_order_gate,
            metadata=request.metadata,
        )

    def get_order(self, client_order_id):
        self.get_order_requests.append(client_order_id)
        if self.poll_results:
            result = self.poll_results.pop(0)
            self.orders[client_order_id] = result
            return result
        if self.raise_on_missing_lookup and client_order_id not in self.orders:
            raise KeyError(client_order_id)
        return self.orders.get(
            client_order_id,
            BrokerOrderResult(
                client_order_id=client_order_id,
                broker_order_id=f"broker-{client_order_id}",
                symbol="BTCUSDT",
                side=TradeSide.BUY,
                status=OrderStatus.ACCEPTED,
                requested_qty=1.0,
                filled_qty=0.0,
                avg_fill_price=100.0,
            ),
        )

    def list_open_orders(self, symbol=None):
        return []


class MatrixLiveOrderGate:
    def __init__(self, *, approved=True, reason_codes=None):
        self.approved = approved
        self.reason_codes = reason_codes or (
            ["live_order_gate_approved"] if approved else ["live_order_gate_rejected"]
        )

    def evaluate(self, order_intent, *, portfolio_allocation=None, dry_run=None):
        return LiveOrderGateDecision(
            approved=self.approved,
            reason_codes=list(self.reason_codes),
            message="approved" if self.approved else "rejected",
            checked_at=1710000000,
            live_mode_enabled=True,
            allow_live_orders=True,
            risk_approved=order_intent.risk_approved,
            portfolio_allocation_approved=bool(getattr(portfolio_allocation, "approved", False)),
            dry_run=bool(dry_run),
            credential_mode="env",
            kill_switch_active=False,
            metadata={"client_order_id": order_intent.client_order_id},
        )


def test_h_qa_026_execution_bracket_lifecycle_matrix_fixture_only(tmp_path):
    open_broker = MatrixBroker()
    open_result = _execute(open_broker)

    assert open_result["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert open_result["entry_orders_sent"] == 1
    assert open_result["protective_orders_sent"] == 1
    assert open_result["broker_called"] is True
    assert open_result["live_orders_sent"] is True

    timeout_broker = MatrixBroker(entry_status=OrderStatus.ACCEPTED, entry_filled_qty=0.0)
    timeout_result = _execute(
        timeout_broker,
        plan=_plan(max_fill_wait_ms=1000, cancel_if_not_filled=True),
        clock=IncrementingClock([1710000001.0, 1710000002.2, 1710000002.2, 1710000002.2]),
    )

    assert timeout_result["status"] == BracketExecutionStatus.CANCELLED_NOT_FILLED
    assert timeout_result["cancel_orders_sent"] == 1
    assert timeout_result["protective_orders_sent"] == 0
    assert timeout_broker.protective_requests == []

    partial_broker = MatrixBroker(entry_status=OrderStatus.PARTIAL, entry_filled_qty=0.4)
    partial_result = _execute(partial_broker)

    assert partial_result["status"] == BracketExecutionStatus.PARTIALLY_EXECUTED_PROTECTED
    assert partial_result["protective_order_result"]["requested_qty"] == 0.4
    assert partial_broker.protective_requests[0].quantity == 0.4

    emergency_broker = MatrixBroker(protective_status=OrderStatus.REJECTED)
    emergency_result = _execute(emergency_broker)

    assert emergency_result["status"] == BracketExecutionStatus.EMERGENCY_EXIT
    assert emergency_result["emergency_exit_orders_sent"] == 1
    assert emergency_broker.emergency_exit_requests[0].reduce_only is True
    assert emergency_broker.emergency_exit_requests[0].side == TradeSide.SELL

    duplicate_broker = MatrixBroker()
    duplicate_plan = _plan()
    duplicate_orchestrator = BracketExecutionOrchestrator(
        duplicate_broker,
        MatrixLiveOrderGate(),
    )
    duplicate_orchestrator.execute(
        duplicate_plan,
        portfolio_allocation=_allocation(duplicate_plan),
        dry_run=False,
    )
    duplicate_result = duplicate_orchestrator.execute(
        duplicate_plan,
        portfolio_allocation=_allocation(duplicate_plan),
        dry_run=False,
    )

    assert duplicate_result["duplicate_replay"] is True
    assert duplicate_result["broker_called"] is False
    assert len(duplicate_broker.entry_requests) == 1
    assert len(duplicate_broker.protective_requests) == 1

    gate_broker = MatrixBroker()
    gate_result = _execute(gate_broker, gate=MatrixLiveOrderGate(approved=False))

    assert gate_result["status"] == BracketExecutionStatus.REJECTED
    assert gate_result["broker_called"] is False
    assert gate_broker.entry_requests == []
    assert gate_broker.protective_requests == []

    mismatch_broker = MatrixBroker()
    mismatch_plan = _plan()
    mismatch_allocation = _allocation(mismatch_plan)
    mismatch_allocation.allocated_quantity = 0.5
    mismatch_result = _execute(
        mismatch_broker,
        plan=mismatch_plan,
        allocation=mismatch_allocation,
    )

    assert mismatch_result["status"] == BracketExecutionStatus.REJECTED
    assert "portfolio_allocated_quantity_mismatch" in mismatch_result["reason_codes"]
    assert mismatch_broker.entry_requests == []

    risk_broker = MatrixBroker()
    risk_plan = _plan(risk_approved=False)
    risk_result = _execute(risk_broker, plan=risk_plan, allocation=_allocation(risk_plan))

    assert risk_result["status"] == BracketExecutionStatus.REJECTED
    assert "risk_approval_missing" in risk_result["reason_codes"]
    assert risk_broker.entry_requests == []

    readiness_broker = MatrixBroker()
    readiness_broker.instrument_rules = {
        "BTCUSDT": InstrumentOrderRules(
            symbol="BTCUSDT",
            quantity_step=0.01,
            min_quantity=0.01,
            price_tick=0.1,
            min_notional=10.0,
        )
    }
    readiness_result = _execute(
        readiness_broker,
        exchange_readiness_request=_failing_readiness(readiness_broker.name),
    )

    assert readiness_result["status"] == BracketExecutionStatus.REJECTED
    assert "exchange_readiness_rejected" in readiness_result["reason_codes"]
    assert "leverage_above_exchange_max" in readiness_result["reason_codes"]
    assert "margin_mode_mismatch" in readiness_result["reason_codes"]
    assert "spread_above_limit" in readiness_result["reason_codes"]
    assert readiness_broker.entry_requests == []
    assert readiness_broker.protective_requests == []

    store = SQLiteOrderStore(tmp_path / "orders.sqlite")
    registry = JsonIdempotencyRegistry(tmp_path / "idempotency.json")
    replay_broker = MatrixBroker(raise_on_missing_lookup=True)
    replay_result = _execute(replay_broker, order_store=store, idempotency_registry=registry)
    replay = store.replay_order("entry-001")
    store.close()

    assert replay_result["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert replay_result["safety_flags"]["order_store_attached"] is True
    assert replay_result["safety_flags"]["persistent_idempotency_registry_attached"] is True
    assert replay.idempotency_key is not None
    assert replay.idempotency_key.submit_intent_count == 1
    assert replay.replay_status == OrderStatus.FILLED
    assert "FILLED" in replay.lifecycle_path


def test_h_qa_026_dashboard_gap_task_remains_visible():
    from scripts.update_harness_dashboard import PROJECT_GAP_TASKS, gap_tasks, parse_tasks

    static_gap = next(item for item in PROJECT_GAP_TASKS if item["ID"] == "H-QA-026")
    dashboard_gap = next(item for item in gap_tasks(parse_tasks()) if item["ID"] == "H-QA-026")

    assert "Execution Bracket Lifecycle" in static_gap["layer"]
    assert dashboard_gap["priority"] == "P1"
    assert dashboard_gap["status"] in {"TODO", "REVIEW", "DONE"}
    assert "fixture-only" in dashboard_gap["completion_criteria"]
    assert "Dashboard" in dashboard_gap["completion_criteria"]


def _execute(
    broker,
    *,
    plan=None,
    allocation=None,
    gate=None,
    clock=None,
    order_store=None,
    idempotency_registry=None,
    exchange_readiness_request=None,
):
    plan = plan or _plan()
    orchestrator = BracketExecutionOrchestrator(
        broker,
        gate or MatrixLiveOrderGate(),
        clock=clock,
        order_store=order_store,
        idempotency_registry=idempotency_registry,
        fill_poll_interval_ms=1,
    )
    return orchestrator.execute(
        plan,
        portfolio_allocation=allocation or _allocation(plan),
        dry_run=False,
        exchange_readiness_request=exchange_readiness_request,
    )


def _plan(
    *,
    max_fill_wait_ms=0,
    cancel_if_not_filled=False,
    risk_approved=True,
):
    return BracketExecutionPlan(
        execution_plan_id="plan-001",
        idempotency_key="idem-001",
        risk_decision_id="risk-001",
        allocation_id="allocation-001",
        entry_order=BrokerOrderRequest(
            client_order_id="entry-001",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.LIMIT,
            quantity=1.0,
            limit_price=100.0,
            time_in_force=TimeInForce.GTC,
        ),
        stop_loss_order=BracketProtectiveLeg(client_order_id="entry-001-sl", price=95.0),
        take_profit_order=BracketProtectiveLeg(client_order_id="entry-001-tp", price=110.0),
        policy=BracketExecutionPolicy(
            native_order_type="oco",
            protective_client_order_id="entry-001-protective",
            max_fill_wait_ms=max_fill_wait_ms,
            cancel_if_not_filled=cancel_if_not_filled,
        ),
        risk_approved=risk_approved,
    )


def _allocation(plan):
    return SimpleNamespace(
        allocation_id=plan.allocation_id,
        client_order_id=plan.entry_order.client_order_id,
        allocated_quantity=plan.entry_order.quantity,
        risk_decision_id=plan.risk_decision_id,
        approved=True,
    )


def _failing_readiness(broker_name):
    return ExchangeReadinessRequest(
        request_id="readiness-001",
        broker_name=broker_name,
        symbol="BTCUSDT",
        requested_at=1710000000,
        desired_leverage=5.0,
        max_leverage=3.0,
        margin_mode="cross",
        position_mode="one_way",
        max_server_time_drift_ms=1000,
        max_spread_bps=5.0,
        min_rate_limit_remaining=3,
        market_snapshot={"best_bid": 100.0, "best_ask": 101.0},
        exchange_state={
            "trading_enabled": True,
            "server_time_ms": 1710000000000,
            "local_time_ms": 1710000000000,
            "leverage": 5.0,
            "margin_mode": "isolated",
            "position_mode": "one_way",
            "rate_limit_remaining": 10,
        },
    )


class IncrementingClock:
    def __init__(self, values):
        self.values = list(values)
        self.last = self.values[-1]

    def __call__(self):
        if self.values:
            self.last = self.values.pop(0)
        return self.last
