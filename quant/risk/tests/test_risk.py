import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.models.crypto import CryptoAccount
from quant.logging import JsonlTradeLogger
from quant.risk.risk_manager import RiskManager
from quant.schemas import OrderKind, TradeSide


def test_position_sizing():
    account = CryptoAccount(initial_balance=10000.0)
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02)
    signal = {"signal": "buy", "signal_index": 2}

    order_signal = risk.apply(signal, account, price=100.0)

    assert order_signal["quantity"] == 10.0
    assert order_signal["stop_loss"] == 98.0
    assert order_signal["take_profit"] == 104.0


def test_stop_loss_trigger():
    account = CryptoAccount(initial_balance=10000.0)
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02)
    signal = {"signal": "buy", "signal_index": 2}
    order_signal = risk.apply(signal, account, price=100.0)

    assert risk.should_stop_loss(order_signal, price=98.0) is True
    assert risk.should_stop_loss(order_signal, price=99.0) is False


def test_max_drawdown_stop():
    account = CryptoAccount(initial_balance=10000.0)
    account.equity = 8900.0
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02, max_drawdown_pct=0.1)
    signal = {"signal": "buy", "signal_index": 2}

    order_signal = risk.apply(signal, account, price=100.0)

    assert order_signal is None


def test_risk_rule_chain_approves_with_explainable_decision():
    account = CryptoAccount(initial_balance=10000.0)
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02)
    signal = {"signal": "buy", "signal_index": 2}

    decision = risk.evaluate(signal, account, price=100.0)

    assert decision.approved is True
    assert decision.reason_codes == [
        "kill_switch",
        "valid_signal",
        "max_drawdown",
        "position_sizing",
        "protective_exit",
    ]
    assert decision.order_payload["quantity"] == 10.0
    assert decision.order_payload["stop_loss"] == 98.0
    assert decision.order_payload["take_profit"] == 104.0
    assert decision.order_intent.symbol == "BTCUSDT"
    assert decision.order_intent.side == TradeSide.BUY
    assert decision.order_intent.order_type == OrderKind.MARKET
    assert decision.order_intent.quantity == 10.0
    assert decision.order_intent.risk_approved is True
    assert decision.order_intent.client_order_id == "risk-BTCUSDT-2-buy"
    assert decision.protective_exit_plan.exit_plan_id == "protective-exit-risk-BTCUSDT-2-buy"
    assert decision.protective_exit_plan.parent_client_order_id == "risk-BTCUSDT-2-buy"
    assert decision.protective_exit_plan.entry_side == TradeSide.BUY
    assert decision.protective_exit_plan.quantity == 10.0
    assert decision.protective_exit_plan.stop_loss_price == 98.0
    assert decision.protective_exit_plan.take_profit_price == 104.0


def test_risk_rule_chain_rejects_invalid_signal():
    account = CryptoAccount(initial_balance=10000.0)
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02)

    decision = risk.evaluate({"signal": "hold"}, account, price=100.0)

    assert decision.approved is False
    assert decision.reason_codes == ["invalid_signal"]
    assert decision.rejections[0].layer == "risk"
    assert decision.rejections[0].fatal is True


def test_kill_switch_blocks_new_orders_before_other_rules():
    account = CryptoAccount(initial_balance=10000.0)
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02)
    risk.enable_kill_switch("exchange maintenance")

    decision = risk.evaluate({"signal": "hold"}, account, price=100.0)

    assert decision.approved is False
    assert decision.reason_codes == ["kill_switch_enabled"]
    assert decision.rejections[0].message == "exchange maintenance"
    assert decision.rejections[0].fatal is True
    assert risk.apply({"signal": "buy", "signal_index": 2}, account, price=100.0) is None


def test_kill_switch_can_be_released():
    account = CryptoAccount(initial_balance=10000.0)
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02)
    risk.enable_kill_switch()
    risk.disable_kill_switch()

    decision = risk.evaluate({"signal": "buy", "signal_index": 2}, account, price=100.0)

    assert decision.approved is True
    assert decision.order_payload["quantity"] == 10.0


def test_kill_switch_auto_triggers_from_loss_and_api_metrics():
    risk = RiskManager(
        daily_loss_limit_pct=0.05,
        consecutive_loss_limit=3,
        api_failure_rate_limit=0.2,
    )

    decision = risk.evaluate_kill_switch_triggers(
        {
            "timestamp": 1710000000,
            "symbol": "BTCUSDT",
            "daily_loss_pct": 0.06,
            "consecutive_losses": 3,
            "api_failure_rate": 0.25,
        }
    )

    assert decision.triggered is True
    assert decision.reason_codes == [
        "daily_loss_limit_exceeded",
        "consecutive_loss_limit_exceeded",
        "api_failure_rate_limit_exceeded",
    ]
    assert risk.kill_switch_enabled is True
    assert risk.kill_switch_reason == ", ".join(decision.reason_codes)


def test_kill_switch_auto_trigger_leaves_switch_off_when_metrics_are_safe():
    risk = RiskManager(daily_loss_limit_pct=0.05, consecutive_loss_limit=3, api_failure_rate_limit=0.2)

    decision = risk.evaluate_kill_switch_triggers(
        {
            "timestamp": 1710000000,
            "symbol": "BTCUSDT",
            "daily_loss_pct": 0.01,
            "consecutive_losses": 1,
            "api_failure_rate": 0.05,
        }
    )

    assert decision.triggered is False
    assert decision.reason_codes == ["kill_switch_not_triggered"]
    assert risk.kill_switch_enabled is False


def test_risk_order_intent_preserves_replay_ids_from_signal():
    account = CryptoAccount(initial_balance=10000.0)
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02)
    signal = {
        "signal": "buy",
        "signal_index": 2,
        "timestamp": 1710000000,
        "decision_id": "decision-001",
        "client_order_id": "client-001",
    }

    decision = risk.evaluate(signal, account, price=100.0)

    assert decision.approved is True
    assert decision.order_intent.decision_id == "decision-001"
    assert decision.order_intent.client_order_id == "client-001"
    assert decision.order_intent.created_at == 1710000000


def test_risk_manager_writes_approved_risk_decision_log(tmp_path):
    account = CryptoAccount(initial_balance=10000.0)
    logger = JsonlTradeLogger(tmp_path / "risk.jsonl")
    risk = RiskManager(
        max_position_pct=0.1,
        stop_loss_pct=0.02,
        risk_logger=logger,
        run_id="risk-run-001",
    )
    signal = {
        "signal": "buy",
        "signal_index": 2,
        "timestamp": 1710000000,
        "decision_id": "decision-001",
        "strategy_id": "ma_crossover",
    }

    decision = risk.evaluate(signal, account, price=100.0)
    records = logger.read_by_type("risk")

    assert decision.approved is True
    assert len(records) == 1
    assert records[0].run_id == "risk-run-001"
    assert records[0].timestamp == 1710000000
    assert records[0].symbol == "BTCUSDT"
    assert records[0].approved is True
    assert records[0].strategy_id == "ma_crossover"
    assert records[0].decision_id == "decision-001"
    assert records[0].risk_decision == decision
    assert records[0].metadata["price"] == 100.0


def test_risk_manager_writes_rejected_risk_decision_log(tmp_path):
    account = CryptoAccount(initial_balance=10000.0)
    logger = JsonlTradeLogger(tmp_path / "risk.jsonl")
    risk = RiskManager(
        max_position_pct=0.1,
        stop_loss_pct=0.02,
        risk_logger=logger,
        run_id="risk-run-001",
    )

    decision = risk.evaluate({"signal": "hold", "signal_index": 3}, account, price=100.0)
    records = logger.read_by_type("risk")

    assert decision.approved is False
    assert len(records) == 1
    assert records[0].timestamp == 3
    assert records[0].approved is False
    assert records[0].reason_codes == ["invalid_signal"]
    assert records[0].risk_decision == decision
