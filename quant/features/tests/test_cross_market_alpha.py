import pytest

from quant.data.schemas.market import Kline
from quant.features.indicators.cross_market import CrossMarketFeature
from quant.schemas.feature import FundingRateSnapshot


def _kline(timestamp: int, close: float) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=10.0,
    )


def test_cross_market_feature_computes_perpetual_basis_and_funding():
    spot_klines = [_kline(1, 50000.0), _kline(2, 50100.0)]
    perpetual_klines = [_kline(1, 50020.0), _kline(2, 50250.0)]
    funding_rate = FundingRateSnapshot(
        snapshot_id="funding-1",
        timestamp=2,
        symbol="BTC-USDT-SWAP",
        venue="okx",
        as_of_timestamp=2,
        funding_rate=0.0003,
        next_funding_timestamp=10,
    )

    snapshot = CrossMarketFeature().compute(
        spot_klines,
        perpetual_klines,
        snapshot_id="cross-1",
        symbol="BTC-USDT-SWAP",
        venue="okx",
        spot_symbol="BTC-USDT",
        perpetual_symbol="BTC-USDT-SWAP",
        funding_rate=funding_rate,
    )

    assert snapshot.spot_price == 50100.0
    assert snapshot.perpetual_price == 50250.0
    assert snapshot.basis == 150.0
    assert snapshot.basis_rate == pytest.approx(150.0 / 50100.0)
    assert snapshot.funding_rate == 0.0003
    assert snapshot.next_funding_timestamp == 10
    assert snapshot.window_start_timestamp == 1
    assert snapshot.window_end_timestamp == 2
    assert snapshot.as_of_timestamp == 2


def test_cross_market_feature_ignores_future_klines_at_index():
    spot_klines = [_kline(1, 100.0), _kline(2, 101.0), _kline(3, 10.0)]
    perpetual_klines = [_kline(1, 100.5), _kline(2, 103.0), _kline(3, 1000.0)]

    snapshot = CrossMarketFeature().compute(
        spot_klines,
        perpetual_klines,
        current_index=1,
        symbol="BTC-USDT-SWAP",
        spot_symbol="BTC-USDT",
        perpetual_symbol="BTC-USDT-SWAP",
    )

    assert snapshot.timestamp == 2
    assert snapshot.spot_price == 101.0
    assert snapshot.perpetual_price == 103.0
    assert snapshot.basis == 2.0


def test_cross_market_feature_rejects_future_funding_snapshot():
    spot_klines = [_kline(1, 100.0)]
    perpetual_klines = [_kline(1, 101.0)]
    funding_rate = FundingRateSnapshot(
        snapshot_id="funding-1",
        timestamp=2,
        symbol="BTC-USDT-SWAP",
        venue="okx",
        as_of_timestamp=2,
        funding_rate=0.0003,
    )

    with pytest.raises(ValueError, match="funding_rate as_of_timestamp"):
        CrossMarketFeature().compute(spot_klines, perpetual_klines, funding_rate=funding_rate)


def test_cross_market_feature_rejects_empty_inputs():
    with pytest.raises(ValueError, match="spot_klines"):
        CrossMarketFeature().compute([], [_kline(1, 101.0)])

    with pytest.raises(ValueError, match="perpetual_klines"):
        CrossMarketFeature().compute([_kline(1, 100.0)], [])
