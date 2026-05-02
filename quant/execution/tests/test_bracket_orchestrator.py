import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.bracket_orchestrator import BracketExecutionOrchestrator
from quant.execution.idempotency import JsonIdempotencyRegistry
from quant.execution.order_store import SQLiteOrderStore
from quant.schemas.execution import (
    BracketExecutionPlan,
    BracketExecutionPolicy,
    BracketExecutionStatus,
    BracketProtectiveLeg,
)
from quant.execution.broker import BrokerAdapter
from quant.schemas.enums import OrderKind, OrderStatus, TimeInForce, TradeSide
from quant.schemas.execution import (
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerProtectiveOrderResult,
    ExchangeReadinessRequest,
    InstrumentOrderRules,
    LiveOrderGateDecision,
)


class FixtureBroker(BrokerAdapter):
    def __init__(
        self,
        *,
        entry_status=OrderStatus.FILLED,
        entry_filled_qty=1.0,
        emergency_exit_status=OrderStatus.FILLED,
        emergency_exit_filled_qty=None,
        protective_status=OrderStatus.ACCEPTED,
        poll_results=None,
        raise_on_missing_lookup=False,
        existing_orders=None,
    ):
        self.entry_status = entry_status
        self.entry_filled_qty = entry_filled_qty
        self.emergency_exit_status = emergency_exit_status
        self.emergency_exit_filled_qty = emergency_exit_filled_qty
        self.protective_status = protective_status
        self.poll_results = list(poll_results or [])
        self.raise_on_missing_lookup = raise_on_missing_lookup
        self.orders = {order.client_order_id: order for order in existing_orders or []}
        self.entry_requests = []
        self.emergency_exit_requests = []
        self.protective_requests = []
        self.cancel_requests = []
        self.get_order_requests = []

    @property
    def name(self):
        return "fixture-bracket-broker"

    def place_order(self, request):
        if request.reduce_only:
            self.emergency_exit_requests.append(request)
            result = BrokerOrderResult(
                client_order_id=request.client_order_id,
                broker_order_id=f"broker-{request.client_order_id}",
                symbol=request.symbol,
                side=request.side,
                status=self.emergency_exit_status,
                requested_qty=request.quantity,
                filled_qty=(
                    request.quantity
                    if self.emergency_exit_filled_qty is None
                    else self.emergency_exit_filled_qty
                ),
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
        raise AssertionError("replace_order should not be called by H-EXEC-025 bracket flow")

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
        if client_order_id in self.orders:
            return self.orders[client_order_id]
        if self.raise_on_missing_lookup:
            raise KeyError(client_order_id)
        result = BrokerOrderResult(
            client_order_id=client_order_id,
            broker_order_id=f"broker-{client_order_id}",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            status=OrderStatus.ACCEPTED,
            requested_qty=1.0,
            filled_qty=0.0,
            avg_fill_price=100.0,
        )
        self.orders[client_order_id] = result
        return result

    def list_open_orders(self, symbol=None):
        return []


class StaticLiveOrderGate:
    def __init__(self, *, approved=True, reason_codes=None):
        self.approved = approved
        self.reason_codes = reason_codes or (
            ["live_order_gate_approved"] if approved else ["live_order_gate_rejected"]
        )
        self.evaluations = []

    def evaluate(self, order_intent, *, portfolio_allocation=None, dry_run=None):
        self.evaluations.append(
            {
                "client_order_id": order_intent.client_order_id,
                "portfolio_allocation": portfolio_allocation,
                "dry_run": dry_run,
            }
        )
        return LiveOrderGateDecision(
            approved=self.approved,
            reason_codes=list(self.reason_codes),
            message="approved" if self.approved else "rejected",
            checked_at=1710000000,
            live_mode_enabled=True,
            allow_live_orders=True,
            risk_approved=getattr(order_intent, "risk_approved", False),
            portfolio_allocation_approved=bool(getattr(portfolio_allocation, "approved", False)),
            dry_run=bool(dry_run),
            credential_mode="env",
            kill_switch_active=False,
            metadata={
                "client_order_id": order_intent.client_order_id,
                "portfolio_allocation_id": getattr(portfolio_allocation, "allocation_id", None),
                "portfolio_client_order_id": getattr(portfolio_allocation, "client_order_id", None),
                "portfolio_allocated_quantity": getattr(portfolio_allocation, "allocated_quantity", None),
                "risk_decision_id": getattr(portfolio_allocation, "risk_decision_id", None),
            },
        )


def test_bracket_orchestrator_submits_entry_then_native_protection_after_live_gate():
    broker = FixtureBroker()
    gate = StaticLiveOrderGate()
    orchestrator = BracketExecutionOrchestrator(broker, gate, clock=lambda: 1710000001)
    plan = _execution_plan()
    allocation = _portfolio_allocation(plan)

    result = orchestrator.execute(plan, portfolio_allocation=allocation, dry_run=False)

    assert result["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert result["broker_called"] is True
    assert result["live_orders_sent"] is True
    assert result["entry_orders_sent"] == 1
    assert result["protective_orders_sent"] == 1
    assert result["risk_decision_id"] == "risk-001"
    assert result["allocation_id"] == "allocation-001"
    assert result["live_order_gate"]["approved"] is True
    assert result["safety_flags"]["risk_approved"] is True
    assert result["safety_flags"]["portfolio_approved"] is True
    assert result["safety_flags"]["protection_submitted"] is True
    assert result["entry_order_result"]["status"] == "filled"
    assert result["protective_order_result"]["status"] == "accepted"
    assert result["protective_order_result"]["requested_qty"] == 1.0
    assert result["lifecycle"]["broker_submit_intent_count"] == 1
    assert broker.entry_requests[0].client_order_id == "entry-001"
    assert broker.protective_requests[0].parent_client_order_id == "entry-001"
    assert broker.protective_requests[0].stop_loss_client_order_id == "entry-001-sl"
    assert broker.protective_requests[0].take_profit_client_order_id == "entry-001-tp"


def test_bracket_orchestrator_rejects_when_live_gate_blocks_without_broker_call():
    broker = FixtureBroker()
    gate = StaticLiveOrderGate(approved=False, reason_codes=["manual_preflight_required"])
    orchestrator = BracketExecutionOrchestrator(broker, gate)
    plan = _execution_plan()

    result = orchestrator.execute(plan, portfolio_allocation=_portfolio_allocation(plan), dry_run=False)

    assert result["status"] == BracketExecutionStatus.REJECTED
    assert result["broker_called"] is False
    assert result["live_orders_sent"] is False
    assert result["entry_order_result"] is None
    assert result["protective_order_result"] is None
    assert "manual_preflight_required" in result["reason_codes"]
    assert broker.entry_requests == []
    assert broker.protective_requests == []


def test_bracket_orchestrator_duplicate_retry_replays_without_second_broker_submit():
    broker = FixtureBroker()
    orchestrator = BracketExecutionOrchestrator(broker, StaticLiveOrderGate())
    plan = _execution_plan()
    allocation = _portfolio_allocation(plan)

    first = orchestrator.execute(plan, portfolio_allocation=allocation, dry_run=False)
    second = orchestrator.execute(plan, portfolio_allocation=allocation, dry_run=False)

    assert first["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert second["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert second["duplicate_replay"] is True
    assert second["broker_called"] is False
    assert second["live_orders_sent"] is False
    assert second["replay_reason"] == "idempotency_key_already_completed"
    assert len(broker.entry_requests) == 1
    assert len(broker.protective_requests) == 1


def test_bracket_orchestrator_persistent_registry_replays_after_process_restart(tmp_path):
    registry_path = tmp_path / "idempotency.json"
    broker = FixtureBroker(raise_on_missing_lookup=True)
    first_registry = JsonIdempotencyRegistry(registry_path)
    first_orchestrator = BracketExecutionOrchestrator(
        broker,
        StaticLiveOrderGate(),
        idempotency_registry=first_registry,
        clock=lambda: 1710000001,
    )
    plan = _execution_plan()
    allocation = _portfolio_allocation(plan)

    first = first_orchestrator.execute(plan, portfolio_allocation=allocation, dry_run=False)
    second_orchestrator = BracketExecutionOrchestrator(
        broker,
        StaticLiveOrderGate(),
        idempotency_registry=JsonIdempotencyRegistry(registry_path),
        clock=lambda: 1710000002,
    )
    second = second_orchestrator.execute(plan, portfolio_allocation=allocation, dry_run=False)

    assert first["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert second["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert second["duplicate_replay"] is True
    assert second["broker_called"] is False
    assert second["live_orders_sent"] is False
    assert second["entry_orders_sent"] == 0
    assert second["protective_orders_sent"] == 0
    assert second["replay_reason"] == "persistent_idempotency_registry_completed"
    assert second["metadata"]["idempotency"]["persistent_registry_attached"] is True
    assert second["metadata"]["idempotency"]["idempotent_replay"] is True
    assert len(broker.entry_requests) == 1
    assert len(broker.protective_requests) == 1


def test_bracket_orchestrator_persistent_registry_rejects_payload_drift(tmp_path):
    registry = JsonIdempotencyRegistry(tmp_path / "idempotency.json")
    broker = FixtureBroker(raise_on_missing_lookup=True)
    orchestrator = BracketExecutionOrchestrator(
        broker,
        StaticLiveOrderGate(),
        idempotency_registry=registry,
        clock=lambda: 1710000001,
    )
    plan = _execution_plan()
    allocation = _portfolio_allocation(plan)
    orchestrator.execute(plan, portfolio_allocation=allocation, dry_run=False)

    drifted_plan = plan.copy(update={"idempotency_key": "idem-002"})
    drifted_plan.entry_order = drifted_plan.entry_order.copy(update={"quantity": 2.0})
    drifted_allocation = _portfolio_allocation(drifted_plan)
    result = BracketExecutionOrchestrator(
        broker,
        StaticLiveOrderGate(),
        idempotency_registry=JsonIdempotencyRegistry(tmp_path / "idempotency.json"),
    ).execute(drifted_plan, portfolio_allocation=drifted_allocation, dry_run=False)

    assert result["status"] == BracketExecutionStatus.REJECTED
    assert "persistent_idempotency_payload_mismatch" in result["reason_codes"]
    assert result["broker_called"] is False
    assert result["live_orders_sent"] is False
    assert len(broker.entry_requests) == 1
    assert len(broker.protective_requests) == 1


def test_bracket_orchestrator_imports_existing_broker_order_before_submit(tmp_path):
    plan = _execution_plan()
    existing_order = BrokerOrderResult(
        client_order_id=plan.entry_order.client_order_id,
        broker_order_id="broker-existing-entry",
        symbol=plan.entry_order.symbol,
        side=plan.entry_order.side,
        status=OrderStatus.FILLED,
        requested_qty=plan.entry_order.quantity,
        filled_qty=plan.entry_order.quantity,
        avg_fill_price=100.0,
    )
    broker = FixtureBroker(
        existing_orders=[existing_order],
        raise_on_missing_lookup=True,
    )
    store = SQLiteOrderStore(tmp_path / "orders.sqlite")
    registry = JsonIdempotencyRegistry(tmp_path / "idempotency.json")
    orchestrator = BracketExecutionOrchestrator(
        broker,
        StaticLiveOrderGate(),
        order_store=store,
        idempotency_registry=registry,
        clock=lambda: 1710000001,
    )

    result = orchestrator.execute(plan, portfolio_allocation=_portfolio_allocation(plan), dry_run=False)
    replayed = store.replay_order(plan.entry_order.client_order_id)
    key = store.get_idempotency_key(plan.entry_order.client_order_id)
    store.close()

    assert result["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert result["broker_called"] is True
    assert result["live_orders_sent"] is True
    assert result["entry_orders_sent"] == 0
    assert result["protective_orders_sent"] == 1
    assert result["metadata"]["idempotency"]["action"] == "import_existing_broker_order"
    assert result["metadata"]["idempotency"]["broker_lookup_called"] is True
    assert result["lifecycle"]["persistent_submit_intent_count"] == 0
    assert result["safety_flags"]["persistent_idempotency_registry_attached"] is True
    assert len(broker.entry_requests) == 0
    assert len(broker.protective_requests) == 1
    assert key is not None
    assert key.submit_intent_count == 0
    assert key.result is not None
    assert key.result.broker_order_id == "broker-existing-entry"
    assert replayed.idempotency_key is not None


def test_bracket_orchestrator_existing_unknown_submit_intent_blocks_resubmit(tmp_path):
    plan = _execution_plan()
    registry = JsonIdempotencyRegistry(tmp_path / "idempotency.json")
    registry.register_submit_intent(
        plan.entry_order,
        now=1710000000,
        metadata={
            "source": "bracket_execution_orchestrator",
            "execution_plan_id": plan.execution_plan_id,
            "idempotency_key": plan.idempotency_key,
            "bracket_plan_payload_hash": BracketExecutionOrchestrator._payload_hash(plan),
        },
    )
    broker = FixtureBroker(raise_on_missing_lookup=True)
    orchestrator = BracketExecutionOrchestrator(
        broker,
        StaticLiveOrderGate(),
        idempotency_registry=JsonIdempotencyRegistry(tmp_path / "idempotency.json"),
        clock=lambda: 1710000001,
    )

    result = orchestrator.execute(plan, portfolio_allocation=_portfolio_allocation(plan), dry_run=False)

    assert result["status"] == BracketExecutionStatus.ENTRY_SUBMITTED_UNPROTECTED
    assert result["broker_called"] is False
    assert result["live_orders_sent"] is False
    assert result["entry_orders_sent"] == 0
    assert result["protective_orders_sent"] == 0
    assert result["metadata"]["idempotency"]["action"] == "hold_unknown_without_resubmit"
    assert "existing_submit_intent_requires_recovery_before_resubmit" in result["reason_codes"]
    assert result["safety_flags"]["persistent_idempotency_registry_attached"] is True
    assert len(broker.entry_requests) == 0
    assert broker.get_order_requests == ["entry-001"]


def test_bracket_orchestrator_partial_fill_submits_protection_for_filled_quantity_only():
    broker = FixtureBroker(entry_status=OrderStatus.PARTIAL, entry_filled_qty=0.4)
    orchestrator = BracketExecutionOrchestrator(broker, StaticLiveOrderGate())
    plan = _execution_plan()

    result = orchestrator.execute(plan.to_payload(), portfolio_allocation=_portfolio_allocation(plan), dry_run=False)

    assert result["status"] == BracketExecutionStatus.PARTIALLY_EXECUTED_PROTECTED
    assert result["entry_order_result"]["requested_qty"] == 1.0
    assert result["entry_order_result"]["filled_qty"] == 0.4
    assert result["protective_order_result"]["requested_qty"] == 0.4
    assert result["safety_flags"]["protection_submitted"] is True
    assert broker.protective_requests[0].quantity == 0.4


def test_bracket_orchestrator_polls_until_entry_filled_before_protection():
    poll_fill = _broker_result(OrderStatus.FILLED, filled_qty=1.0)
    broker = FixtureBroker(entry_status=OrderStatus.ACCEPTED, entry_filled_qty=0.0, poll_results=[poll_fill])
    orchestrator = BracketExecutionOrchestrator(
        broker,
        StaticLiveOrderGate(),
        clock=lambda: 1710000001.0,
        fill_poll_interval_ms=1,
    )
    plan = _execution_plan(max_fill_wait_ms=1000)

    result = orchestrator.execute(plan, portfolio_allocation=_portfolio_allocation(plan), dry_run=False)

    assert result["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert result["fill_resolution"]["source"] == "poll"
    assert result["fill_resolution"]["poll_count"] == 1
    assert result["fill_resolution"]["timeout"] is False
    assert result["cancel_orders_sent"] == 0
    assert result["protective_orders_sent"] == 1
    assert broker.get_order_requests == ["entry-001"]
    assert broker.cancel_requests == []
    assert broker.protective_requests[0].quantity == 1.0


def test_bracket_orchestrator_timeout_cancels_unfilled_entry_without_protection():
    clock = IncrementingClock([1710000001.0, 1710000002.2, 1710000002.2, 1710000002.2])
    broker = FixtureBroker(entry_status=OrderStatus.ACCEPTED, entry_filled_qty=0.0)
    orchestrator = BracketExecutionOrchestrator(
        broker,
        StaticLiveOrderGate(),
        clock=clock,
        fill_poll_interval_ms=1,
    )
    plan = _execution_plan(max_fill_wait_ms=1000, cancel_if_not_filled=True)

    result = orchestrator.execute(plan, portfolio_allocation=_portfolio_allocation(plan), dry_run=False)

    assert result["status"] == BracketExecutionStatus.CANCELLED_NOT_FILLED
    assert result["fill_resolution"]["timeout"] is True
    assert result["cancel_orders_sent"] == 1
    assert result["protective_orders_sent"] == 0
    assert result["safety_flags"]["remaining_entry_cancelled"] is True
    assert "entry_fill_timeout_cancelled" in result["reason_codes"]
    assert broker.cancel_requests == ["entry-001"]
    assert broker.protective_requests == []


def test_bracket_orchestrator_partial_poll_cancels_remaining_and_persists_lifecycle(tmp_path):
    poll_partial = _broker_result(OrderStatus.PARTIAL, filled_qty=0.4)
    broker = FixtureBroker(entry_status=OrderStatus.ACCEPTED, entry_filled_qty=0.0, poll_results=[poll_partial])
    gate = StaticLiveOrderGate()
    store = SQLiteOrderStore(tmp_path / "orders.sqlite")
    orchestrator = BracketExecutionOrchestrator(
        broker,
        gate,
        clock=lambda: 1710000001.0,
        order_store=store,
        fill_poll_interval_ms=1,
    )
    plan = _execution_plan(max_fill_wait_ms=1000, cancel_if_not_filled=True)

    result = orchestrator.execute(plan, portfolio_allocation=_portfolio_allocation(plan), dry_run=False)
    replayed = store.replay_order("entry-001")
    store.close()

    assert result["status"] == BracketExecutionStatus.PARTIALLY_EXECUTED_PROTECTED
    assert result["entry_order_result"]["filled_qty"] == 0.4
    assert result["protective_order_result"]["requested_qty"] == 0.4
    assert result["cancel_orders_sent"] == 1
    assert result["fill_resolution"]["poll_count"] == 1
    assert result["safety_flags"]["order_store_attached"] is True
    assert broker.cancel_requests == ["entry-001"]
    assert broker.protective_requests[0].quantity == 0.4
    assert result["lifecycle"]["broker_submit_intent_count"] == 1
    assert "PARTIALLY_FILLED" in result["lifecycle"]["path"]
    assert "CANCELLED" in result["lifecycle"]["path"]
    assert replayed.replay_status == OrderStatus.CANCELLED
    assert any(event.event_type == "entry_order_poll_result" for event in replayed.events)
    assert any(event.event_type == "entry_order_cancel_result" for event in replayed.events)


def test_bracket_orchestrator_protective_rejection_triggers_reduce_only_emergency_exit():
    broker = FixtureBroker(protective_status=OrderStatus.REJECTED)
    orchestrator = BracketExecutionOrchestrator(broker, StaticLiveOrderGate())
    plan = _execution_plan()

    result = orchestrator.execute(plan, portfolio_allocation=_portfolio_allocation(plan), dry_run=False)

    assert result["status"] == BracketExecutionStatus.EMERGENCY_EXIT
    assert result["broker_called"] is True
    assert result["live_orders_sent"] is True
    assert result["entry_orders_sent"] == 1
    assert result["protective_orders_sent"] == 1
    assert result["emergency_exit_orders_sent"] == 1
    assert result["entry_order_result"]["status"] == "filled"
    assert result["protective_order_result"]["status"] == "rejected"
    assert result["emergency_exit_result"]["status"] == "filled"
    assert result["emergency_exit_result"]["requested_qty"] == 1.0
    assert result["emergency_exit_result"]["side"] == "sell"
    assert "protective_order_rejected" in result["reason_codes"]
    assert "emergency_exit_submitted" in result["reason_codes"]
    assert result["safety_flags"]["protection_submitted"] is True
    assert result["safety_flags"]["emergency_exit_submitted"] is True
    assert result["safety_flags"]["naked_position_resolved"] is True
    assert result["metadata"]["alert"]["severity"] == "critical"
    assert result["metadata"]["reconciliation"]["emergency_exit_client_order_id"] == (
        "entry-001:emergency-exit"
    )
    assert broker.emergency_exit_requests[0].client_order_id == "entry-001:emergency-exit"
    assert broker.emergency_exit_requests[0].side == TradeSide.SELL
    assert broker.emergency_exit_requests[0].order_type == OrderKind.MARKET
    assert broker.emergency_exit_requests[0].quantity == 1.0
    assert broker.emergency_exit_requests[0].reduce_only is True


def test_bracket_orchestrator_emergency_exit_duplicate_retry_replays_without_second_exit():
    broker = FixtureBroker(protective_status=OrderStatus.REJECTED)
    orchestrator = BracketExecutionOrchestrator(broker, StaticLiveOrderGate())
    plan = _execution_plan()
    allocation = _portfolio_allocation(plan)

    first = orchestrator.execute(plan, portfolio_allocation=allocation, dry_run=False)
    second = orchestrator.execute(plan, portfolio_allocation=allocation, dry_run=False)

    assert first["status"] == BracketExecutionStatus.EMERGENCY_EXIT
    assert second["status"] == BracketExecutionStatus.EMERGENCY_EXIT
    assert second["duplicate_replay"] is True
    assert second["broker_called"] is False
    assert second["live_orders_sent"] is False
    assert second["entry_orders_sent"] == 0
    assert second["protective_orders_sent"] == 0
    assert second["emergency_exit_orders_sent"] == 0
    assert len(broker.entry_requests) == 1
    assert len(broker.protective_requests) == 1
    assert len(broker.emergency_exit_requests) == 1


def test_bracket_orchestrator_partial_protective_failure_exits_only_filled_quantity():
    broker = FixtureBroker(
        entry_status=OrderStatus.PARTIAL,
        entry_filled_qty=0.4,
        protective_status=OrderStatus.REJECTED,
    )
    orchestrator = BracketExecutionOrchestrator(broker, StaticLiveOrderGate())
    plan = _execution_plan()

    result = orchestrator.execute(plan, portfolio_allocation=_portfolio_allocation(plan), dry_run=False)

    assert result["status"] == BracketExecutionStatus.EMERGENCY_EXIT
    assert result["entry_order_result"]["filled_qty"] == 0.4
    assert result["protective_order_result"]["requested_qty"] == 0.4
    assert result["emergency_exit_result"]["requested_qty"] == 0.4
    assert result["safety_flags"]["naked_position_resolved"] is True
    assert broker.emergency_exit_requests[0].quantity == 0.4


def test_bracket_orchestrator_emergency_exit_rejection_preserves_protection_failed_state():
    broker = FixtureBroker(
        protective_status=OrderStatus.REJECTED,
        emergency_exit_status=OrderStatus.REJECTED,
        emergency_exit_filled_qty=0.0,
    )
    orchestrator = BracketExecutionOrchestrator(broker, StaticLiveOrderGate())
    plan = _execution_plan()

    result = orchestrator.execute(plan, portfolio_allocation=_portfolio_allocation(plan), dry_run=False)

    assert result["status"] == BracketExecutionStatus.PROTECTION_FAILED
    assert result["emergency_exit_orders_sent"] == 1
    assert result["emergency_exit_result"]["status"] == "rejected"
    assert "emergency_exit_rejected" in result["reason_codes"]
    assert result["safety_flags"]["naked_position_resolved"] is False
    assert result["metadata"]["alert"]["requires_manual_review"] is True


def test_bracket_orchestrator_rejects_portfolio_mismatch_before_broker_call():
    broker = FixtureBroker()
    orchestrator = BracketExecutionOrchestrator(broker, StaticLiveOrderGate())
    plan = _execution_plan()
    allocation = _portfolio_allocation(plan)
    allocation.allocated_quantity = 0.5

    result = orchestrator.execute(plan, portfolio_allocation=allocation, dry_run=False)

    assert result["status"] == BracketExecutionStatus.REJECTED
    assert result["broker_called"] is False
    assert result["live_orders_sent"] is False
    assert "portfolio_allocated_quantity_mismatch" in result["reason_codes"]
    assert broker.entry_requests == []
    assert broker.protective_requests == []


def test_bracket_orchestrator_rejects_exchange_readiness_failure_before_broker_call():
    broker = FixtureBroker()
    broker.instrument_rules = {
        "BTCUSDT": InstrumentOrderRules(
            symbol="BTCUSDT",
            quantity_step=0.01,
            min_quantity=0.01,
            price_tick=0.1,
            min_notional=10.0,
        )
    }
    orchestrator = BracketExecutionOrchestrator(broker, StaticLiveOrderGate())
    plan = _execution_plan()
    readiness = ExchangeReadinessRequest(
        request_id="readiness-001",
        broker_name=broker.name,
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

    result = orchestrator.execute(
        plan,
        portfolio_allocation=_portfolio_allocation(plan),
        dry_run=False,
        exchange_readiness_request=readiness,
    )

    assert result["status"] == BracketExecutionStatus.REJECTED
    assert result["broker_called"] is False
    assert result["live_orders_sent"] is False
    assert "exchange_readiness_rejected" in result["reason_codes"]
    assert "leverage_above_exchange_max" in result["reason_codes"]
    assert "margin_mode_mismatch" in result["reason_codes"]
    assert "spread_above_limit" in result["reason_codes"]
    assert result["metadata"]["exchange_readiness"]["approved"] is False
    assert result["metadata"]["exchange_readiness"]["metadata"]["live_orders_sent"] is False
    assert broker.entry_requests == []
    assert broker.protective_requests == []


def test_bracket_orchestrator_records_exchange_readiness_success_on_live_submit():
    broker = FixtureBroker()
    broker.instrument_rules = {
        "BTCUSDT": InstrumentOrderRules(
            symbol="BTCUSDT",
            quantity_step=0.01,
            min_quantity=0.01,
            price_tick=0.1,
            min_notional=10.0,
        )
    }
    orchestrator = BracketExecutionOrchestrator(broker, StaticLiveOrderGate())
    plan = _execution_plan()
    readiness = {
        "request_id": "readiness-002",
        "broker_name": broker.name,
        "symbol": "BTCUSDT",
        "requested_at": 1710000000,
        "desired_leverage": 2.0,
        "max_leverage": 3.0,
        "td_mode": "cross",
        "max_server_time_drift_ms": 1000,
        "max_spread_bps": 5.0,
        "min_rate_limit_remaining": 3,
        "market_snapshot": {"best_bid": 99.99, "best_ask": 100.01},
        "exchange_state": {
            "trading_status": "trading",
            "server_time_ms": 1710000000000,
            "local_time_ms": 1710000000000,
            "leverage": 2.0,
            "td_mode": "cross",
            "rate_limit_remaining": 10,
        },
    }

    result = orchestrator.execute(
        plan,
        portfolio_allocation=_portfolio_allocation(plan),
        dry_run=False,
        exchange_readiness_request=readiness,
    )

    assert result["status"] == BracketExecutionStatus.OPEN_PROTECTED
    assert result["exchange_readiness"]["approved"] is True
    assert result["exchange_readiness"]["reason_codes"] == ["exchange_readiness_approved"]
    assert result["safety_flags"]["exchange_readiness_approved"] is True
    assert result["entry_orders_sent"] == 1
    assert result["protective_orders_sent"] == 1


def _execution_plan(max_fill_wait_ms=0, cancel_if_not_filled=False):
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
        risk_approved=True,
    )


def _portfolio_allocation(plan):
    return SimpleNamespace(
        allocation_id=plan.allocation_id,
        client_order_id=plan.entry_order.client_order_id,
        allocated_quantity=plan.entry_order.quantity,
        risk_decision_id=plan.risk_decision_id,
        approved=True,
    )


def _broker_result(status, *, filled_qty):
    return BrokerOrderResult(
        client_order_id="entry-001",
        broker_order_id="broker-entry-001",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        status=status,
        requested_qty=1.0,
        filled_qty=filled_qty,
        avg_fill_price=100.0 if filled_qty else None,
    )


class IncrementingClock:
    def __init__(self, values):
        self.values = list(values)
        self.last = self.values[-1]

    def __call__(self):
        if self.values:
            self.last = self.values.pop(0)
        return self.last
