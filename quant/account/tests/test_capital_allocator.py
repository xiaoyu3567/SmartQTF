import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from pydantic import ValidationError

from quant.account.capital_allocator import CapitalAllocator
from quant.schemas import CapitalAllocationRequest, PayloadSource, TraceContext, TradeSide


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
