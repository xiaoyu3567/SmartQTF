import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.capital_allocator import CapitalBudgetAllocator
from quant.account.models.crypto import CryptoAccount
from quant.logging import JsonlTradeLogger
from quant.risk.risk_manager import RiskManager
from quant.schemas import (
    AssetClass,
    CapitalBudgetRequest,
    DecisionAction,
    MarketType,
    OrderKind,
    PayloadSource,
    RiskEngineV2Request,
    RiskMarketConstraints,
    RiskPolicy,
    TraceContext,
    TradeIntent,
    TradeSide,
)


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


def test_risk_engine_v2_sizes_from_trade_intent_and_capital_budget():
    request = make_risk_v2_request()

    decision = RiskManager().evaluate_v2(request)

    assert decision.approved is True
    assert decision.risk_decision_id == "risk:decision-001"
    assert decision.sizing.raw_quantity == 0.08
    assert decision.sizing.adjusted_quantity == 0.08
    assert decision.sizing.max_loss_usdt == 80.0
    assert decision.sizing.risk_reward == 3.0
    assert decision.order_intent.quantity == 0.08
    assert decision.order_intent.client_order_id == "decision-001:risk-v2:buy"
    assert decision.order_intent.risk_approved is True
    assert decision.protective_exit_plan.stop_loss_price == 64000.0
    assert decision.protective_exit_plan.take_profit_price == 68000.0
    assert decision.execution_order_plan.risk_decision_id == "risk:decision-001"
    assert decision.execution_order_plan.allocation_id == "capital-budget-001"
    assert decision.execution_order_plan.entry_order.quantity == 0.08
    assert decision.execution_order_plan.stop_loss_order.price == 64000.0
    assert decision.execution_order_plan.take_profit_order.price == 68000.0
    assert decision.execution_order_plan.idempotency_key == "decision-001:risk-v2:buy"
    assert decision.sizing.safety == {
        "network_used": False,
        "ai_provider_called": False,
        "broker_called": False,
        "live_orders_sent": False,
        "legacy_signal_used": False,
    }
    assert "legacy_signal_used" not in decision.to_payload()


def test_risk_engine_v2_caps_quantity_by_symbol_notional():
    request = make_risk_v2_request(
        free_margin=100000.0,
        current_symbol_notional=2400.0,
        max_symbol_weight=0.25,
    )

    decision = RiskManager().evaluate_v2(request)

    assert decision.approved is True
    assert decision.sizing.raw_quantity == 0.08
    assert decision.sizing.adjusted_quantity == 0.001
    assert decision.sizing.notional == 65.0
    assert "quantity_capped" in decision.sizing.reason_codes


def test_risk_engine_v2_rejects_low_risk_reward():
    request = make_risk_v2_request(
        trade_intent=make_trade_intent(take_profit=65500.0),
        risk_policy=RiskPolicy(min_risk_reward=1.0),
    )

    decision = RiskManager().evaluate_v2(request)

    assert decision.approved is False
    assert decision.reason_codes == ["risk_reward_below_minimum"]
    assert decision.order_intent is None
    assert decision.execution_order_plan is None


def test_risk_engine_v2_rejects_exchange_min_notional_before_order_creation():
    request = make_risk_v2_request(min_notional=6000.0)

    decision = RiskManager().evaluate_v2(request)

    assert decision.approved is False
    assert decision.reason_codes == ["notional_below_minimum"]
    assert decision.order_intent is None
    assert decision.execution_order_plan is None


def test_risk_engine_v2_rejects_price_tick_mismatch():
    request = make_risk_v2_request(price_tick=3.0)

    decision = RiskManager().evaluate_v2(request)

    assert decision.approved is False
    assert decision.reason_codes == ["price_tick_mismatch"]
    assert decision.order_intent is None
    assert decision.execution_order_plan is None


def test_risk_engine_v2_rejects_legacy_signal_fields():
    payload = make_risk_v2_request().to_payload()

    try:
        RiskEngineV2Request.from_payload({**payload, "signal": {"signal": "buy"}})
    except Exception as exc:
        assert "legacy or executable fields" in str(exc)
    else:
        raise AssertionError("risk v2 request must reject legacy signal fields")


def test_risk_engine_v2_respects_kill_switch_before_order_creation():
    risk = RiskManager()
    risk.enable_kill_switch("manual halt")

    decision = risk.evaluate_v2(make_risk_v2_request())

    assert decision.approved is False
    assert decision.reason_codes == ["kill_switch_enabled"]
    assert decision.rejections[0].fatal is True
    assert decision.order_intent is None
    assert decision.execution_order_plan is None


def make_risk_v2_request(**overrides):
    trade_intent = overrides.pop("trade_intent", make_trade_intent())
    budget = CapitalBudgetAllocator().allocate(
        CapitalBudgetRequest(
            **make_capital_budget_request(
                trade_intent=trade_intent,
                free_margin=overrides.pop("free_margin", 10000.0),
                current_symbol_notional=overrides.pop("current_symbol_notional", 0.0),
                current_total_notional=overrides.pop("current_total_notional", 0.0),
                current_correlation_group_notional=overrides.pop(
                    "current_correlation_group_notional",
                    2500.0,
                ),
                max_symbol_weight=overrides.pop("max_symbol_weight", 1.0),
                max_total_weight=overrides.pop("max_total_weight", 1.0),
                max_correlation_group_weight=overrides.pop("max_correlation_group_weight", 1.0),
            )
        )
    )
    payload = {
        "request_id": "risk-v2-request-001",
        "timestamp": 1710000000,
        "trade_intent": trade_intent,
        "capital_budget": budget,
        "market_constraints": RiskMarketConstraints(
            symbol=trade_intent.symbol,
            entry_price=65000.0,
            min_notional=overrides.pop("min_notional", 10.0),
            min_quantity=overrides.pop("min_quantity", 0.0001),
            quantity_step=overrides.pop("quantity_step", 0.001),
            price_tick=overrides.pop("price_tick", None),
            max_leverage=overrides.pop("max_leverage", 3.0),
        ),
        "risk_policy": overrides.pop(
            "risk_policy",
            RiskPolicy(
                desired_leverage=overrides.pop("desired_leverage", 1.0),
                max_slippage_pct=overrides.pop("max_slippage_pct", 0.001),
                min_risk_reward=overrides.pop("min_risk_reward", 1.0),
                liquidation_buffer_pct=overrides.pop("liquidation_buffer_pct", 0.01),
            ),
        ),
        "trace": trade_intent.trace,
    }
    payload.update(overrides)
    return RiskEngineV2Request(**payload)


def make_capital_budget_request(**overrides):
    trade_intent = overrides.pop("trade_intent", make_trade_intent())
    payload = {
        "budget_id": "capital-budget-001",
        "timestamp": 1710000000,
        "trade_intent": trade_intent,
        "account_equity": 10000.0,
        "free_margin": 700.0,
        "base_risk_budget_pct": 0.02,
        "min_risk_budget_usdt": 10.0,
        "current_symbol_notional": 1500.0,
        "current_total_notional": 4000.0,
        "current_correlation_group_notional": 2500.0,
        "max_symbol_weight": 0.25,
        "max_total_weight": 0.80,
        "max_correlation_group_weight": 0.40,
        "correlation_group": "crypto-major",
        "reason_codes": ["decision_approved"],
    }
    payload.update(overrides)
    return payload


def make_trade_intent(**overrides):
    payload = {
        "trade_intent_id": "trade-intent-001",
        "decision_id": "decision-001",
        "timestamp": 1710000000,
        "symbol": "BTCUSDT",
        "asset_class": AssetClass.CRYPTO,
        "market_type": MarketType.PERPETUAL,
        "side": TradeSide.BUY,
        "action": DecisionAction.OPEN_LONG,
        "strategy_id": "trend_pullback_long_v1",
        "strategy_version": "1.0.0",
        "timeframe": "5m",
        "regime": "trend",
        "entry_price": 65000.0,
        "stop_loss": 64000.0,
        "take_profit": 68000.0,
        "confidence": 0.80,
        "source_signal_id": "signal-001",
        "reason_codes": ["breakout_confirmed"],
        "trace": TraceContext(
            run_id="paper-001",
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="5m",
            timestamp=1710000000,
            bar_index=100,
        ),
    }
    payload.update(overrides)
    return TradeIntent(**payload)
