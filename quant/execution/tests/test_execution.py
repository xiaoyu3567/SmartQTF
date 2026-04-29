import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.engine import ExecutionEngine
from quant.execution.state_machine import ExecutionEvent, ExecutionState, ExecutionStateMachine
from quant.schemas import OrderIntent, OrderKind, ProtectiveExitPlan, TimeInForce, TradeSide


def test_full_fill():
    engine = ExecutionEngine(seed=1)
    signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}

    result = engine.on_signal(signal, price=100.0, index=3)

    assert result["status"] == "filled"
    assert result["filled_qty"] == 1.0
    assert result["remaining_qty"] == 0.0
    assert result["fill_price"] > 100.0
    assert engine.last_order_status == ExecutionEngine.FILLED
    assert engine.state == ExecutionEngine.POSITION_OPEN
    assert engine.position.size == 1.0
    assert engine.position.entry_price == result["fill_price"]


def test_reject_case():
    engine = ExecutionEngine(seed=2)
    signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}

    result = engine.on_signal(signal, price=100.0, index=3)

    assert result["status"] == "rejected"
    assert result["filled_qty"] == 0.0
    assert result["remaining_qty"] == 1.0
    assert engine.last_order_status == ExecutionEngine.REJECTED
    assert engine.state == ExecutionEngine.REJECTED
    assert engine.position.size == 0.0


def test_partial_fill():
    engine = ExecutionEngine(seed=0)
    signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}

    result = engine.on_signal(signal, price=100.0, index=3)

    assert result["status"] == "partial"
    assert 0.1 <= result["filled_qty"] <= 0.9
    assert result["remaining_qty"] == 1.0 - result["filled_qty"]
    assert result["fill_price"] > 100.0
    assert result["fill_event"]["status"] == "partial"
    assert result["fill_event"]["fill_qty"] == result["filled_qty"]
    assert result["fill_event"]["cumulative_filled_qty"] == result["filled_qty"]
    assert result["fill_event"]["remaining_qty"] == result["remaining_qty"]
    assert result["fill_events"] == [result["fill_event"]]
    assert engine.last_order_status == ExecutionEngine.PARTIAL
    assert engine.state == ExecutionEngine.POSITION_OPEN
    assert engine.position.size == result["filled_qty"]


def test_slippage_range():
    engine = ExecutionEngine(seed=1)
    signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}

    result = engine.on_signal(signal, price=100.0, index=3)

    assert result["status"] == "filled"
    assert 100.0 * 0.0005 <= result["slippage"] <= 100.0 * 0.002
    assert result["fill_price"] == 100.0 + result["slippage"]


def test_execution_delay():
    engine = ExecutionEngine(execution_delay=1, seed=1)
    signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}

    pending_result = engine.on_signal(signal, price=100.0, index=3)
    filled_result = engine.on_bar(price=101.0, index=3)
    late_result = engine.on_bar(price=102.0, index=4)

    assert pending_result["status"] == "pending"
    assert filled_result["status"] == "filled"
    assert filled_result["fill_index"] == 3
    assert late_result is None
    assert engine.pending_orders == []
    assert engine.position.size == 1.0


def test_position_update():
    engine = ExecutionEngine(seed=1)
    first_signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}
    second_signal = {"signal": "buy", "signal_index": 3, "quantity": 2.0}

    first_result = engine.on_signal(first_signal, price=100.0, index=3)
    second_result = engine.on_signal(second_signal, price=110.0, index=4)
    expected_avg_price = (
        first_result["fill_price"] * first_result["filled_qty"]
        + second_result["fill_price"] * second_result["filled_qty"]
    ) / (first_result["filled_qty"] + second_result["filled_qty"])

    assert first_result["status"] == "filled"
    assert second_result["status"] == "filled"
    assert engine.position.size == 3.0
    assert engine.position.entry_price == expected_avg_price
    assert engine.position.pnl == 0.0


def test_partial_accumulation():
    engine = ExecutionEngine(seed=0)
    partial_signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}
    filled_signal = {"signal": "buy", "signal_index": 3, "quantity": 1.0}

    partial_result = engine.on_signal(partial_signal, price=100.0, index=3)
    filled_result = engine.on_signal(filled_signal, price=110.0, index=4)

    assert partial_result["status"] == "partial"
    assert filled_result["status"] == "filled"
    assert engine.position.size == partial_result["filled_qty"] + filled_result["filled_qty"]


def test_order_lifecycle():
    engine = ExecutionEngine(execution_delay=1, seed=1)
    signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}

    pending_result = engine.on_signal(signal, price=100.0, index=3)
    order = engine.orders[0]

    assert pending_result["status"] == "pending"
    assert order.status == "pending"
    assert order.qty == 1.0
    assert order.filled_qty == 0.0

    filled_result = engine.on_bar(price=100.0, index=3)

    assert filled_result["status"] == "filled"
    assert order.status == "filled"
    assert order.filled_qty == 1.0


def test_client_order_id_retry_returns_existing_fill_without_duplicate_order():
    engine = ExecutionEngine(seed=1)
    signal = {
        "signal": "buy",
        "signal_index": 2,
        "quantity": 1.0,
        "client_order_id": "retry-buy-1",
    }

    first_result = engine.on_signal(signal, price=100.0, index=3)
    position_size = engine.position.size
    retry_result = engine.on_signal(signal, price=120.0, index=4)

    assert first_result["status"] == "filled"
    assert retry_result == first_result
    assert retry_result["client_order_id"] == "retry-buy-1"
    assert len(engine.orders) == 1
    assert engine.position.size == position_size
    assert engine.position.entry_price == first_result["fill_price"]


def test_client_order_id_retry_returns_existing_pending_order():
    engine = ExecutionEngine(execution_delay=1, seed=1, delay_across_bars=True)
    signal = {
        "signal": "buy",
        "signal_index": 2,
        "quantity": 1.0,
        "client_order_id": "pending-buy-1",
    }

    first_result = engine.on_signal(signal, price=100.0, index=3)
    retry_result = engine.on_signal(signal, price=101.0, index=3)
    filled_result = engine.on_bar(price=102.0, index=4)

    assert first_result == retry_result
    assert first_result["status"] == "pending"
    assert first_result["client_order_id"] == "pending-buy-1"
    assert len(engine.orders) == 1
    assert len(engine.pending_orders) == 0
    assert filled_result["status"] == "filled"
    assert filled_result["client_order_id"] == "pending-buy-1"
    assert filled_result["fill_index"] == 4


def test_order_intent_executes_directly_and_preserves_client_order_id():
    engine = ExecutionEngine(seed=1)
    intent = OrderIntent(
        order_intent_id="intent-1",
        decision_id="decision-1",
        client_order_id="intent-buy-1",
        symbol="ETHUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.MARKET,
        quantity=1.5,
        time_in_force=TimeInForce.GTC,
        risk_approved=True,
        created_at=3,
    )

    result = engine.on_order_intent(intent, price=100.0, index=3)
    retry_result = engine.on_order_intent(intent, price=120.0, index=4)

    assert result["status"] == "filled"
    assert retry_result == result
    assert result["client_order_id"] == "intent-buy-1"
    assert result["symbol"] == "ETHUSDT"
    assert result["side"] == "buy"
    assert result["filled_qty"] == 1.5
    assert len(engine.orders) == 1


def test_order_intent_registers_and_triggers_typed_protective_exit():
    engine = ExecutionEngine(seed=1)
    intent = OrderIntent(
        order_intent_id="intent-1",
        decision_id="decision-1",
        client_order_id="entry-buy-1",
        symbol="ETHUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        time_in_force=TimeInForce.GTC,
        risk_approved=True,
        created_at=3,
    )
    plan = ProtectiveExitPlan(
        exit_plan_id="protective-exit-1",
        parent_client_order_id="entry-buy-1",
        symbol="ETHUSDT",
        entry_side=TradeSide.BUY,
        quantity=1.0,
        stop_loss_price=98.0,
        take_profit_price=104.0,
        created_at=3,
    )

    entry_result = engine.on_order_intent(intent, price=100.0, index=3, protective_exit_plan=plan)
    no_trigger_result = engine.evaluate_protective_exits(price=99.0, index=4)
    trigger_result = engine.evaluate_protective_exits(price=98.0, index=5)

    assert entry_result["status"] == "filled"
    assert entry_result["protective_exit_plan"]["exit_plan_id"] == "protective-exit-1"
    assert no_trigger_result is None
    assert trigger_result["status"] == "triggered"
    assert trigger_result["trigger_event"]["trigger_type"] == "stop_loss"
    assert trigger_result["trigger_event"]["order_intent"]["reduce_only"] is True
    assert trigger_result["execution_result"]["side"] == "sell"
    assert trigger_result["execution_result"]["status"] == "filled"
    assert engine.protective_exit_plans["protective-exit-1"].active is False
    assert len(engine.protective_exit_events) == 1
    assert engine.position.size == 0.0
    assert engine.state == ExecutionEngine.EXIT


def test_protective_exit_can_be_cancelled_before_trigger():
    engine = ExecutionEngine(seed=1)
    plan = ProtectiveExitPlan(
        exit_plan_id="protective-exit-1",
        parent_client_order_id="entry-buy-1",
        symbol="ETHUSDT",
        entry_side=TradeSide.BUY,
        quantity=1.0,
        stop_loss_price=98.0,
        take_profit_price=104.0,
        created_at=3,
    )

    engine.register_protective_exit(plan)
    cancel_result = engine.cancel_protective_exit("protective-exit-1", reason="position_closed")
    trigger_result = engine.evaluate_protective_exits(price=98.0, index=5)

    assert cancel_result["status"] == "cancelled"
    assert cancel_result["exit_plan"]["active"] is False
    assert trigger_result is None


def test_no_extra_delay():
    engine = ExecutionEngine(execution_delay=1, seed=1)
    signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}

    pending_result = engine.on_signal(signal, price=100.0, index=3)
    same_bar_result = engine.on_bar(price=100.0, index=3)
    next_bar_result = engine.on_bar(price=100.0, index=4)

    assert pending_result["status"] == "pending"
    assert same_bar_result["status"] == "filled"
    assert same_bar_result["fill_index"] == 3
    assert next_bar_result is None


def test_execution_state_machine_transitions():
    machine = ExecutionStateMachine()

    assert machine.state == ExecutionState.IDLE

    machine.transition(ExecutionEvent.SIGNAL_ACCEPTED, ExecutionState.ORDER_PENDING)
    machine.transition(ExecutionEvent.ORDER_FILLED, ExecutionState.FILLED)
    machine.transition(ExecutionEvent.POSITION_OPENED, ExecutionState.POSITION_OPEN)
    machine.transition(ExecutionEvent.POSITION_CLOSED, ExecutionState.EXIT)

    assert machine.history == [
        ExecutionState.IDLE,
        ExecutionState.ORDER_PENDING,
        ExecutionState.FILLED,
        ExecutionState.POSITION_OPEN,
        ExecutionState.EXIT,
    ]


def test_execution_state_machine_rejects_invalid_transition():
    machine = ExecutionStateMachine()

    try:
        machine.transition(ExecutionEvent.POSITION_OPENED, ExecutionState.POSITION_OPEN)
    except ValueError as exc:
        assert "invalid execution transition" in str(exc)
    else:
        raise AssertionError("expected invalid transition to raise ValueError")
