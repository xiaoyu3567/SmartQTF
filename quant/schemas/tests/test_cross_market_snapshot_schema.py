import pytest
from pydantic import ValidationError

from quant.schemas import CrossMarketSnapshot


def test_cross_market_snapshot_exposes_replayable_basis():
    snapshot = CrossMarketSnapshot(
        snapshot_id="cross-1",
        timestamp=100,
        symbol="BTC-USDT-SWAP",
        venue="okx",
        as_of_timestamp=100,
        window_start_timestamp=90,
        window_end_timestamp=100,
        spot_symbol="BTC-USDT",
        perpetual_symbol="BTC-USDT-SWAP",
        spot_price=50000.0,
        perpetual_price=50100.0,
        funding_rate=0.0002,
        next_funding_timestamp=200,
    )

    assert snapshot.basis == 100.0
    assert snapshot.basis_rate == pytest.approx(0.002)


def test_cross_market_snapshot_rejects_future_as_of_timestamp():
    with pytest.raises(ValidationError, match="as_of_timestamp"):
        CrossMarketSnapshot(
            snapshot_id="cross-1",
            timestamp=100,
            symbol="BTC-USDT-SWAP",
            venue="okx",
            as_of_timestamp=101,
            window_start_timestamp=90,
            window_end_timestamp=100,
            spot_symbol="BTC-USDT",
            perpetual_symbol="BTC-USDT-SWAP",
            spot_price=50000.0,
            perpetual_price=50100.0,
        )


def test_cross_market_snapshot_rejects_invalid_prices():
    with pytest.raises(ValidationError, match="spot_price"):
        CrossMarketSnapshot(
            snapshot_id="cross-1",
            timestamp=100,
            symbol="BTC-USDT-SWAP",
            venue="okx",
            as_of_timestamp=100,
            window_start_timestamp=90,
            window_end_timestamp=100,
            spot_symbol="BTC-USDT",
            perpetual_symbol="BTC-USDT-SWAP",
            spot_price=0.0,
            perpetual_price=50100.0,
        )


def test_cross_market_snapshot_rejects_unordered_window():
    with pytest.raises(ValidationError, match="window_start_timestamp"):
        CrossMarketSnapshot(
            snapshot_id="cross-1",
            timestamp=100,
            symbol="BTC-USDT-SWAP",
            venue="okx",
            as_of_timestamp=100,
            window_start_timestamp=101,
            window_end_timestamp=100,
            spot_symbol="BTC-USDT",
            perpetual_symbol="BTC-USDT-SWAP",
            spot_price=50000.0,
            perpetual_price=50100.0,
        )
