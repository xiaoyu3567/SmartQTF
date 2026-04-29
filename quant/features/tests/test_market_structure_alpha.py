import pytest

from quant.data.schemas.market import Kline
from quant.features.indicators.market_structure import MarketStructureFeature


def _kline(timestamp: int, high: float, low: float, close: float) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=10.0,
    )


def test_market_structure_detects_higher_high_and_upside_breakout():
    klines = [
        _kline(1, 100.0, 90.0, 95.0),
        _kline(2, 102.0, 91.0, 98.0),
        _kline(3, 101.0, 92.0, 99.0),
        _kline(4, 105.0, 94.0, 104.0),
    ]

    snapshot = MarketStructureFeature(lookback=3).compute(
        klines,
        snapshot_id="structure-1",
        symbol="BTC-USDT",
        venue="okx",
    )

    assert snapshot is not None
    assert snapshot.previous_high == 102.0
    assert snapshot.previous_low == 90.0
    assert snapshot.current_high == 105.0
    assert snapshot.current_low == 91.0
    assert snapshot.higher_high is True
    assert snapshot.lower_low is False
    assert snapshot.breakout_direction == "up"
    assert snapshot.structure_state == "breakout"
    assert snapshot.liquidity_range_width == 12.0
    assert snapshot.as_of_timestamp == 4


def test_market_structure_detects_lower_low_and_downside_breakout():
    klines = [
        _kline(1, 110.0, 100.0, 106.0),
        _kline(2, 112.0, 101.0, 108.0),
        _kline(3, 111.0, 99.0, 104.0),
        _kline(4, 109.0, 95.0, 98.0),
    ]

    snapshot = MarketStructureFeature(lookback=3).compute(klines)

    assert snapshot is not None
    assert snapshot.higher_high is False
    assert snapshot.lower_low is True
    assert snapshot.breakout_direction == "down"
    assert snapshot.structure_state == "breakout"


def test_market_structure_marks_inside_range():
    klines = [
        _kline(1, 110.0, 100.0, 106.0),
        _kline(2, 112.0, 101.0, 108.0),
        _kline(3, 111.0, 99.0, 104.0),
        _kline(4, 111.5, 100.5, 107.0),
    ]

    snapshot = MarketStructureFeature(lookback=3).compute(klines)

    assert snapshot is not None
    assert snapshot.breakout_direction == "none"
    assert snapshot.structure_state == "range"


def test_market_structure_ignores_future_klines_at_index():
    klines = [
        _kline(1, 100.0, 90.0, 95.0),
        _kline(2, 102.0, 91.0, 98.0),
        _kline(3, 101.0, 92.0, 99.0),
        _kline(4, 105.0, 94.0, 104.0),
        _kline(5, 1000.0, 10.0, 999.0),
    ]

    snapshot = MarketStructureFeature(lookback=3).compute(klines, current_index=3)

    assert snapshot is not None
    assert snapshot.timestamp == 4
    assert snapshot.current_high == 105.0
    assert snapshot.current_low == 91.0
    assert snapshot.breakout_direction == "up"


def test_market_structure_returns_none_until_window_is_ready():
    klines = [
        _kline(1, 100.0, 90.0, 95.0),
        _kline(2, 102.0, 91.0, 98.0),
    ]

    assert MarketStructureFeature(lookback=2).compute(klines) is None


def test_market_structure_rejects_invalid_lookback():
    with pytest.raises(ValueError, match="lookback"):
        MarketStructureFeature(lookback=0)
