import pytest

from quant.data.schemas.market import Trade
from quant.features.indicators.orderflow import OrderFlowAlphaFeature
from quant.schemas.feature import OrderBookLevel, OrderBookSnapshot


def _raw_trade(timestamp: int, price: float, size: float, side: str) -> Trade:
    payload = {"timestamp": timestamp, "price": price, "size": size, "side": side}
    if hasattr(Trade, "model_construct"):
        return Trade.model_construct(**payload)
    return Trade.construct(**payload)


def test_orderflow_alpha_computes_large_trade_and_taker_ratio():
    trades = [
        Trade(timestamp=1, price=100.0, size=1.0, side="buy"),
        Trade(timestamp=2, price=101.0, size=4.0, side="buy"),
        Trade(timestamp=3, price=100.5, size=2.0, side="sell"),
        Trade(timestamp=4, price=100.2, size=5.0, side="sell"),
    ]

    snapshot = OrderFlowAlphaFeature(large_trade_threshold=3.0).compute(
        trades,
        snapshot_id="ofi-1",
        symbol="BTC-USDT",
        venue="okx",
    )

    assert snapshot.buy_volume == 5.0
    assert snapshot.sell_volume == 7.0
    assert snapshot.order_flow_imbalance == -2.0
    assert snapshot.large_buy_volume == 4.0
    assert snapshot.large_sell_volume == 5.0
    assert snapshot.large_order_imbalance == -1.0
    assert snapshot.large_trade_count == 2
    assert snapshot.taker_buy_sell_ratio == pytest.approx(5.0 / 7.0)
    assert snapshot.window_start_timestamp == 1
    assert snapshot.window_end_timestamp == 4
    assert snapshot.as_of_timestamp == 4


def test_orderflow_alpha_computes_orderbook_imbalance_by_depth():
    trades = [Trade(timestamp=10, price=100.0, size=1.0, side="buy")]
    orderbook = OrderBookSnapshot(
        snapshot_id="book-1",
        timestamp=10,
        symbol="BTC-USDT",
        venue="okx",
        as_of_timestamp=10,
        bids=[
            OrderBookLevel(price=99.0, quantity=3.0),
            OrderBookLevel(price=98.0, quantity=2.0),
        ],
        asks=[
            OrderBookLevel(price=101.0, quantity=1.0),
            OrderBookLevel(price=102.0, quantity=4.0),
        ],
    )

    snapshot = OrderFlowAlphaFeature(orderbook_depth=1).compute(trades, orderbook=orderbook)

    assert snapshot.orderbook_imbalance == pytest.approx(0.5)


def test_orderflow_alpha_ignores_future_trades_at_index():
    trades = [
        Trade(timestamp=1, price=100.0, size=1.0, side="buy"),
        Trade(timestamp=2, price=100.0, size=2.0, side="sell"),
        Trade(timestamp=3, price=100.0, size=100.0, side="buy"),
    ]

    snapshot = OrderFlowAlphaFeature(large_trade_threshold=3.0).compute(trades, current_index=1)

    assert snapshot.buy_volume == 1.0
    assert snapshot.sell_volume == 2.0
    assert snapshot.large_trade_count == 0
    assert snapshot.window_end_timestamp == 2


def test_orderflow_alpha_rejects_unknown_trade_side():
    trades = [_raw_trade(timestamp=1, price=100.0, size=1.0, side="unknown")]

    with pytest.raises(ValueError, match="trade side"):
        OrderFlowAlphaFeature().compute(trades)
