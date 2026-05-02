import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from pydantic import ValidationError

from quant.account.capital_allocator import CapitalAllocator, CapitalBudgetAllocator
from quant.schemas import (
    AssetClass,
    CapitalAllocationRequest,
    CapitalBudgetRequest,
    DecisionAction,
    MarketType,
    PayloadSource,
    TraceContext,
    TradeIntent,
    TradeSide,
)


def make_request(**overrides):
    payload = {
        "allocation_id": "alloc-001",
        "timestamp": 1710000000,
        "symbol": "BTCUSDT",
        "side": TradeSide.BUY,
        "price": 100.0,
        "account_equity": 10000.0,
        "available_cash": 1500.0,
        "target_weight": 0.20,
        "strategy_weight": 0.50,
        "max_symbol_weight": 0.25,
        "trace": TraceContext(
            run_id="bt-001",
            source=PayloadSource.BACKTEST,
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=1710000000,
            bar_index=12,
        ),
    }
    payload.update(overrides)
    return CapitalAllocationRequest(**payload)


def test_capital_allocator_uses_account_strategy_and_price():
    decision = CapitalAllocator().allocate(make_request())

    assert decision.approved is True
    assert decision.notional == 1000.0
    assert decision.quantity == 10.0
    assert decision.reason_codes == ["allocation_approved"]
    assert decision.trace.run_id == "bt-001"


def test_capital_allocator_caps_by_available_cash():
    decision = CapitalAllocator().allocate(make_request(available_cash=600.0))

    assert decision.approved is True
    assert decision.notional == 600.0
    assert decision.quantity == 6.0
    assert "allocation_capped" in decision.reason_codes


def test_capital_allocator_scales_by_volatility():
    decision = CapitalAllocator().allocate(
        make_request(volatility=0.40, target_volatility=0.20)
    )

    assert decision.approved is True
    assert decision.notional == 500.0
    assert decision.quantity == 5.0
    assert decision.reason_codes == ["volatility_scaled", "allocation_approved"]


def test_capital_allocator_supports_kelly_sizing_with_confidence():
    decision = CapitalAllocator().allocate(
        make_request(
            allocation_mode="kelly",
            win_rate=0.55,
            payoff_ratio=1.5,
            signal_confidence=0.50,
            max_kelly_fraction=0.50,
        )
    )

    assert decision.approved is True
    assert decision.notional == pytest.approx(625.0)
    assert decision.quantity == pytest.approx(6.25)
    assert decision.reason_codes == ["kelly_scaled", "allocation_approved"]


def test_capital_allocator_supports_atr_based_volatility_target():
    decision = CapitalAllocator().allocate(
        make_request(
            allocation_mode="volatility_target",
            atr=10.0,
            target_volatility=0.05,
        )
    )

    assert decision.approved is True
    assert decision.notional == 500.0
    assert decision.quantity == 5.0
    assert decision.reason_codes == ["volatility_scaled", "allocation_approved"]


def test_capital_allocator_rejects_when_symbol_cap_is_exhausted():
    decision = CapitalAllocator().allocate(
        make_request(current_symbol_notional=2500.0, min_notional=10.0)
    )

    assert decision.approved is False
    assert decision.quantity == 0.0
    assert decision.notional == 0.0
    assert "allocation_below_minimum" in decision.reason_codes


def test_capital_allocation_request_requires_valid_volatility_pair():
    try:
        make_request(volatility=0.25)
    except ValidationError:
        pass
    else:
        raise AssertionError("volatility requires target_volatility")


def test_capital_allocation_request_requires_kelly_inputs():
    try:
        make_request(allocation_mode="kelly")
    except ValidationError:
        pass
    else:
        raise AssertionError("kelly allocation requires win_rate and payoff_ratio")


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


def make_budget_request(**overrides):
    payload = {
        "budget_id": "capital-budget-001",
        "timestamp": 1710000000,
        "trade_intent": make_trade_intent(),
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
    return CapitalBudgetRequest(**payload)


def test_capital_budget_allocator_outputs_risk_budget_and_exposure_caps():
    decision = CapitalBudgetAllocator().allocate(make_budget_request())

    assert decision.approved is True
    assert decision.decision_id == "decision-001"
    assert decision.trade_intent_id == "trade-intent-001"
    assert decision.symbol == "BTCUSDT"
    assert decision.side == _value(TradeSide.BUY)
    assert decision.base_risk_budget_usdt == 200.0
    assert decision.scaled_risk_budget_usdt == 80.0
    assert decision.adjusted_risk_budget_usdt == 80.0
    assert decision.max_symbol_notional == 1000.0
    assert decision.max_total_notional == 4000.0
    assert decision.max_group_notional == 1500.0
    assert decision.confidence_multiplier == 0.80
    assert decision.correlation_multiplier == 0.50
    assert decision.reason_codes == [
        "decision_approved",
        "capital_budget_from_trade_intent",
        "confidence_scaled",
        "correlation_exposure_scaled",
        "capital_budget_approved",
    ]
    assert decision.input_refs == {
        "decision_id": "decision-001",
        "trade_intent_id": "trade-intent-001",
        "source_signal_id": "signal-001",
        "correlation_group": "crypto-major",
    }
    assert decision.safety == {
        "network_used": False,
        "ai_provider_called": False,
        "broker_called": False,
        "live_orders_sent": False,
        "risk_bypassed": False,
        "order_intent_created": False,
    }
    payload = decision.to_payload()
    assert "quantity" not in payload
    assert "client_order_id" not in payload
    assert "order_intent" not in payload


def test_capital_budget_allocator_scales_high_volatility():
    decision = CapitalBudgetAllocator().allocate(
        make_budget_request(
            trade_intent=make_trade_intent(confidence=1.0),
            volatility=0.40,
            target_volatility=0.10,
            current_correlation_group_notional=0.0,
        )
    )

    assert decision.approved is True
    assert decision.scaled_risk_budget_usdt == 50.0
    assert decision.adjusted_risk_budget_usdt == 50.0
    assert decision.volatility_multiplier == 0.25
    assert "volatility_scaled" in decision.reason_codes


def test_capital_budget_allocator_caps_by_cash_margin():
    decision = CapitalBudgetAllocator().allocate(
        make_budget_request(
            trade_intent=make_trade_intent(confidence=1.0),
            free_margin=40.0,
            current_correlation_group_notional=0.0,
        )
    )

    assert decision.approved is True
    assert decision.adjusted_risk_budget_usdt == 40.0
    assert "capital_budget_capped" in decision.reason_codes
    assert "free_margin_capped" in decision.reason_codes


def test_capital_budget_allocator_rejects_when_min_budget_is_not_met():
    decision = CapitalBudgetAllocator().allocate(
        make_budget_request(
            trade_intent=make_trade_intent(confidence=0.20),
            free_margin=5.0,
            min_risk_budget_usdt=10.0,
            current_correlation_group_notional=0.0,
        )
    )

    assert decision.approved is False
    assert decision.adjusted_risk_budget_usdt == 0.0
    assert "capital_budget_below_minimum" in decision.reason_codes


def test_capital_budget_request_rejects_executable_order_fields():
    payload = make_budget_request().to_payload()

    with pytest.raises(ValidationError, match="executable order fields"):
        CapitalBudgetRequest.from_payload({**payload, "quantity": 1.0})


def _value(value):
    return getattr(value, "value", value)
