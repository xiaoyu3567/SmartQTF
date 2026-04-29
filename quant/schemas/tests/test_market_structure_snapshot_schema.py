import pytest
from pydantic import ValidationError

from quant.schemas import MarketStructureSnapshot


def test_market_structure_snapshot_exposes_liquidity_range_width():
    snapshot = MarketStructureSnapshot(
        snapshot_id="structure-1",
        timestamp=100,
        symbol="BTC-USDT",
        venue="okx",
        as_of_timestamp=100,
        window_start_timestamp=70,
        window_end_timestamp=100,
        lookback=20,
        previous_high=120.0,
        previous_low=100.0,
        current_high=125.0,
        current_low=103.0,
        close=124.0,
        higher_high=True,
        lower_low=False,
        breakout_direction="up",
        structure_state="breakout",
    )

    assert snapshot.liquidity_range_width == 20.0


def test_market_structure_snapshot_rejects_future_as_of_timestamp():
    with pytest.raises(ValidationError, match="as_of_timestamp"):
        MarketStructureSnapshot(
            snapshot_id="structure-1",
            timestamp=100,
            symbol="BTC-USDT",
            venue="okx",
            as_of_timestamp=101,
            window_start_timestamp=70,
            window_end_timestamp=100,
            lookback=20,
            previous_high=120.0,
            previous_low=100.0,
            current_high=125.0,
            current_low=103.0,
            close=124.0,
        )


def test_market_structure_snapshot_rejects_invalid_breakout_direction():
    with pytest.raises(ValidationError, match="breakout_direction"):
        MarketStructureSnapshot(
            snapshot_id="structure-1",
            timestamp=100,
            symbol="BTC-USDT",
            venue="okx",
            as_of_timestamp=100,
            window_start_timestamp=70,
            window_end_timestamp=100,
            lookback=20,
            previous_high=120.0,
            previous_low=100.0,
            current_high=125.0,
            current_low=103.0,
            close=124.0,
            breakout_direction="sideways",
        )


def test_market_structure_snapshot_rejects_close_outside_current_range():
    with pytest.raises(ValidationError, match="close"):
        MarketStructureSnapshot(
            snapshot_id="structure-1",
            timestamp=100,
            symbol="BTC-USDT",
            venue="okx",
            as_of_timestamp=100,
            window_start_timestamp=70,
            window_end_timestamp=100,
            lookback=20,
            previous_high=120.0,
            previous_low=100.0,
            current_high=125.0,
            current_low=103.0,
            close=126.0,
        )
