import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.engine import ExecutionEngine


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
