import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.portfolio_engine import PortfolioEngine
from quant.schemas import (
    OrderIntent,
    OrderKind,
    PayloadSource,
    PortfolioAllocationRequest,
    PortfolioOrderRequest,
    PortfolioPositionSnapshot,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def make_trace(symbol="BTCUSDT"):
    return TraceContext(
        run_id="paper-001",
        source=PayloadSource.PAPER,
        symbol=symbol,
        timeframe="1m",
        timestamp=1710000000,
        bar_index=7,
    )


def make_order(
    client_order_id,
    symbol="BTCUSDT",
    quantity=10.0,
    price=100.0,
    side=TradeSide.BUY,
    strategy_id="trend-ma",
    reduce_only=False,
):
    return PortfolioOrderRequest(
        strategy_id=strategy_id,
        order_intent=OrderIntent(
            order_intent_id=f"intent-{client_order_id}",
            decision_id=f"decision-{client_order_id}",
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type=OrderKind.MARKET,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            reduce_only=reduce_only,
            created_at=1710000000,
            trace=make_trace(symbol),
        ),
        reference_price=price,
        correlation_group="crypto-major",
    )


def make_request(**overrides):
    payload = {
        "allocation_id": "portfolio-001",
        "timestamp": 1710000000,
        "account_equity": 10000.0,
        "available_cash": 5000.0,
        "orders": [make_order("coid-001")],
        "max_symbol_weight": 0.25,
        "max_strategy_weight": 0.25,
        "max_correlation_group_weight": 0.40,
        "min_notional": 10.0,
        "trace": make_trace(),
    }
    payload.update(overrides)
    return PortfolioAllocationRequest(**payload)


def test_portfolio_engine_allocates_multi_strategy_order_intent():
    decision = PortfolioEngine().allocate(make_request())

    assert decision.approved is True
    assert decision.allocated_notional == 1000.0
    assert decision.remaining_cash == 4000.0
    assert decision.reason_codes == ["portfolio_allocation_approved"]

    allocation = decision.allocations[0]
    assert allocation.client_order_id == "coid-001"
    assert allocation.strategy_id == "trend-ma"
    assert allocation.allocated_quantity == 10.0
    assert allocation.reason_codes == ["portfolio_order_approved"]
    assert allocation.trace.run_id == "paper-001"


def test_portfolio_engine_caps_by_symbol_strategy_and_cash_budget():
    decision = PortfolioEngine().allocate(
        make_request(
            available_cash=700.0,
            orders=[make_order("coid-001", quantity=50.0)],
            positions=[
                PortfolioPositionSnapshot(
                    symbol="BTCUSDT",
                    strategy_id="trend-ma",
                    side=TradeSide.BUY,
                    quantity=20.0,
                    avg_price=100.0,
                    market_price=100.0,
                    correlation_group="crypto-major",
                )
            ],
        )
    )

    allocation = decision.allocations[0]
    assert decision.approved is True
    assert allocation.allocated_notional == 500.0
    assert allocation.allocated_quantity == 5.0
    assert "symbol_risk_budget_capped" in allocation.reason_codes
    assert "strategy_risk_budget_capped" in allocation.reason_codes


def test_portfolio_engine_caps_correlation_group_exposure():
    decision = PortfolioEngine().allocate(
        make_request(
            orders=[make_order("coid-001", quantity=20.0)],
            positions=[
                PortfolioPositionSnapshot(
                    symbol="ETHUSDT",
                    strategy_id="range-rsi",
                    side=TradeSide.BUY,
                    quantity=35.0,
                    avg_price=100.0,
                    market_price=100.0,
                    correlation_group="crypto-major",
                )
            ],
        )
    )

    allocation = decision.allocations[0]
    assert allocation.approved is True
    assert allocation.allocated_notional == 500.0
    assert "correlation_group_budget_capped" in allocation.reason_codes


def test_portfolio_engine_rejects_allocations_below_minimum():
    decision = PortfolioEngine().allocate(
        make_request(
            available_cash=5.0,
            orders=[make_order("coid-001", quantity=1.0)],
            min_notional=10.0,
        )
    )

    allocation = decision.allocations[0]
    assert decision.approved is False
    assert allocation.approved is False
    assert allocation.allocated_quantity == 0.0
    assert "portfolio_allocation_below_minimum" in allocation.reason_codes


def test_portfolio_engine_merges_same_symbol_same_direction_orders_under_symbol_limit():
    decision = PortfolioEngine().allocate(
        make_request(
            orders=[
                make_order("coid-001", quantity=20.0, strategy_id="trend-ma"),
                make_order("coid-002", quantity=20.0, strategy_id="breakout"),
            ],
            max_symbol_weight=0.25,
            max_strategy_weight=0.50,
            max_correlation_group_weight=0.80,
        )
    )

    assert decision.approved is True
    assert decision.allocated_notional == 2500.0
    assert decision.remaining_cash == 2500.0

    first, second = decision.allocations
    assert first.approved is True
    assert second.approved is True
    assert first.allocated_notional == 1250.0
    assert second.allocated_notional == 1250.0
    assert first.allocated_quantity == 12.5
    assert second.allocated_quantity == 12.5
    assert "portfolio_same_direction_merged" in first.reason_codes
    assert "portfolio_same_direction_merged" in second.reason_codes
    assert "symbol_risk_budget_capped" in first.reason_codes
    assert "symbol_risk_budget_capped" in second.reason_codes


def test_portfolio_engine_offsets_opposite_open_orders_and_rejects_losing_side():
    decision = PortfolioEngine().allocate(
        make_request(
            orders=[
                make_order("coid-buy", quantity=10.0, strategy_id="trend-ma"),
                make_order(
                    "coid-sell",
                    quantity=7.0,
                    side=TradeSide.SELL,
                    strategy_id="mean-revert",
                ),
            ],
            max_symbol_weight=0.50,
            max_strategy_weight=0.50,
            max_correlation_group_weight=0.80,
        )
    )

    buy_allocation, sell_allocation = decision.allocations
    assert decision.approved is True
    assert decision.allocated_notional == 300.0
    assert buy_allocation.approved is True
    assert buy_allocation.side == TradeSide.BUY
    assert buy_allocation.allocated_quantity == 3.0
    assert "portfolio_opposite_side_netting_applied" in buy_allocation.reason_codes
    assert sell_allocation.approved is False
    assert sell_allocation.side == TradeSide.SELL
    assert sell_allocation.allocated_quantity == 0.0
    assert "portfolio_opposite_side_offset" in sell_allocation.reason_codes
    assert not any(
        allocation.approved and allocation.side == TradeSide.SELL
        for allocation in decision.allocations
    )


def test_portfolio_engine_prioritizes_reduce_only_before_new_exposure():
    decision = PortfolioEngine().allocate(
        make_request(
            available_cash=2000.0,
            orders=[
                make_order("coid-open", quantity=20.0, strategy_id="breakout"),
                make_order(
                    "coid-close",
                    quantity=5.0,
                    side=TradeSide.SELL,
                    strategy_id="trend-ma",
                    reduce_only=True,
                ),
            ],
            positions=[
                PortfolioPositionSnapshot(
                    symbol="BTCUSDT",
                    strategy_id="trend-ma",
                    side=TradeSide.BUY,
                    quantity=25.0,
                    avg_price=100.0,
                    market_price=100.0,
                    correlation_group="crypto-major",
                )
            ],
            max_symbol_weight=0.25,
            max_strategy_weight=0.50,
            max_correlation_group_weight=0.80,
        )
    )

    open_allocation, close_allocation = decision.allocations
    assert decision.approved is True
    assert close_allocation.approved is True
    assert close_allocation.side == TradeSide.SELL
    assert close_allocation.allocated_quantity == 5.0
    assert "portfolio_reduce_only_priority" in close_allocation.reason_codes
    assert open_allocation.approved is True
    assert open_allocation.allocated_quantity == 5.0
    assert "symbol_risk_budget_capped" in open_allocation.reason_codes
