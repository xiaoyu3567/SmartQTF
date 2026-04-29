import pytest
from pydantic import ValidationError

from quant.schemas import OrderFlowSnapshot


def test_orderflow_snapshot_exposes_replayable_imbalances():
    snapshot = OrderFlowSnapshot(
        snapshot_id="ofi-1",
        timestamp=100,
        symbol="BTC-USDT",
        venue="okx",
        as_of_timestamp=100,
        window_start_timestamp=90,
        window_end_timestamp=100,
        buy_volume=12.0,
        sell_volume=7.0,
        large_buy_volume=10.0,
        large_sell_volume=4.0,
        buy_trade_count=3,
        sell_trade_count=2,
        large_trade_count=2,
        taker_buy_sell_ratio=12.0 / 7.0,
        orderbook_imbalance=0.25,
    )

    assert snapshot.order_flow_imbalance == 5.0
    assert snapshot.large_order_imbalance == 6.0


def test_orderflow_snapshot_rejects_future_as_of_timestamp():
    with pytest.raises(ValidationError, match="as_of_timestamp"):
        OrderFlowSnapshot(
            snapshot_id="ofi-1",
            timestamp=100,
            symbol="BTC-USDT",
            venue="okx",
            as_of_timestamp=101,
            window_start_timestamp=90,
            window_end_timestamp=100,
        )


def test_orderflow_snapshot_rejects_invalid_imbalance():
    with pytest.raises(ValidationError, match="orderbook_imbalance"):
        OrderFlowSnapshot(
            snapshot_id="ofi-1",
            timestamp=100,
            symbol="BTC-USDT",
            venue="okx",
            as_of_timestamp=100,
            window_start_timestamp=90,
            window_end_timestamp=100,
            orderbook_imbalance=2.0,
        )


def test_orderflow_snapshot_rejects_unordered_window():
    with pytest.raises(ValidationError, match="window_start_timestamp"):
        OrderFlowSnapshot(
            snapshot_id="ofi-1",
            timestamp=100,
            symbol="BTC-USDT",
            venue="okx",
            as_of_timestamp=100,
            window_start_timestamp=101,
            window_end_timestamp=100,
        )
