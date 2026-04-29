import pytest

from quant.data.schemas.market import Kline, Trade
from quant.features.pipeline import (
    AdvancedFeaturePipeline,
    FeaturePipelineConfig,
    FeaturePipelineInput,
)
from quant.schemas.feature import FundingRateSnapshot, OrderBookLevel, OrderBookSnapshot


def _kline(timestamp: int, close: float, high: float = None, low: float = None) -> Kline:
    high = close if high is None else high
    low = close if low is None else low
    return Kline(
        timestamp=timestamp,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=10.0,
    )


def test_advanced_feature_pipeline_builds_snapshot_with_auxiliary_features():
    klines = [
        _kline(1, 95.0, high=100.0, low=90.0),
        _kline(2, 98.0, high=102.0, low=91.0),
        _kline(3, 99.0, high=101.0, low=92.0),
        _kline(4, 104.0, high=105.0, low=94.0),
        _kline(5, 106.0, high=108.0, low=95.0),
    ]
    trades = [
        Trade(timestamp=4, price=104.0, size=4.0, side="buy"),
        Trade(timestamp=4, price=104.5, size=1.0, side="sell"),
    ]
    orderbook = OrderBookSnapshot(
        snapshot_id="book-1",
        timestamp=4,
        symbol="BTC-USDT-SWAP",
        venue="okx",
        as_of_timestamp=4,
        bids=[OrderBookLevel(price=103.0, quantity=3.0)],
        asks=[OrderBookLevel(price=105.0, quantity=1.0)],
    )
    funding_rate = FundingRateSnapshot(
        snapshot_id="funding-1",
        timestamp=4,
        symbol="BTC-USDT-SWAP",
        venue="okx",
        as_of_timestamp=4,
        funding_rate=0.0002,
    )

    snapshot = AdvancedFeaturePipeline(
        FeaturePipelineConfig(
            fast_ma_window=2,
            slow_ma_window=3,
            market_structure_lookback=3,
            large_trade_threshold=3.0,
            orderbook_depth=1,
        )
    ).compute(
        FeaturePipelineInput(
            klines=klines,
            index=3,
            symbol="BTC-USDT-SWAP",
            timeframe="1m",
            venue="okx",
            trades=trades,
            orderbook=orderbook,
            spot_klines=[_kline(1, 100.0), _kline(2, 101.0), _kline(3, 102.0), _kline(4, 103.0)],
            perpetual_klines=[_kline(1, 100.5), _kline(2, 102.0), _kline(3, 103.0), _kline(4, 105.0)],
            spot_symbol="BTC-USDT",
            perpetual_symbol="BTC-USDT-SWAP",
            funding_rate=funding_rate,
            snapshot_id="feature-snapshot-1",
        )
    )

    assert snapshot.snapshot_id == "feature-snapshot-1"
    assert snapshot.timestamp == 4
    assert snapshot.as_of_timestamp == 4
    assert snapshot.values["close"] == 104.0
    assert snapshot.values["fast_ma"] == pytest.approx((99.0 + 104.0) / 2)
    assert snapshot.values["slow_ma"] == pytest.approx((98.0 + 99.0 + 104.0) / 3)
    assert snapshot.values["orderflow.imbalance"] == 3.0
    assert snapshot.values["orderflow.large_imbalance"] == 4.0
    assert snapshot.values["orderflow.orderbook_imbalance"] == pytest.approx(0.5)
    assert snapshot.values["market_structure.breakout_direction"] == "up"
    assert snapshot.values["cross_market.basis"] == 2.0
    assert snapshot.values["cross_market.funding_rate"] == 0.0002


def test_advanced_feature_pipeline_ignores_future_primary_bars_and_trades():
    base_klines = [
        _kline(1, 10.0),
        _kline(2, 20.0),
        _kline(3, 30.0),
        _kline(4, 40.0),
    ]
    changed_future_klines = [
        _kline(1, 10.0),
        _kline(2, 20.0),
        _kline(3, 30.0),
        _kline(4, 4000.0),
    ]
    trades = [
        Trade(timestamp=3, price=30.0, size=2.0, side="buy"),
        Trade(timestamp=4, price=4000.0, size=1000.0, side="sell"),
    ]
    pipeline = AdvancedFeaturePipeline(
        FeaturePipelineConfig(fast_ma_window=2, slow_ma_window=3, market_structure_lookback=2)
    )

    original = pipeline.compute(
        FeaturePipelineInput(
            klines=base_klines,
            index=2,
            symbol="BTC-USDT",
            timeframe="1m",
            trades=trades,
        )
    )
    changed = pipeline.compute(
        FeaturePipelineInput(
            klines=changed_future_klines,
            index=2,
            symbol="BTC-USDT",
            timeframe="1m",
            trades=trades,
        )
    )

    assert original.values["close"] == changed.values["close"] == 30.0
    assert original.values["fast_ma"] == changed.values["fast_ma"] == 25.0
    assert original.values["slow_ma"] == changed.values["slow_ma"] == 20.0
    assert original.values["orderflow.imbalance"] == changed.values["orderflow.imbalance"] == 2.0


def test_advanced_feature_pipeline_rejects_invalid_input():
    pipeline = AdvancedFeaturePipeline()

    with pytest.raises(ValueError, match="klines"):
        pipeline.compute(FeaturePipelineInput(klines=[], index=0, symbol="BTC-USDT", timeframe="1m"))

    with pytest.raises(ValueError, match="index"):
        pipeline.compute(
            FeaturePipelineInput(
                klines=[_kline(1, 10.0)],
                index=1,
                symbol="BTC-USDT",
                timeframe="1m",
            )
        )


def test_advanced_feature_pipeline_rejects_future_orderbook():
    future_orderbook = OrderBookSnapshot(
        snapshot_id="book-future",
        timestamp=3,
        symbol="BTC-USDT",
        venue="okx",
        as_of_timestamp=3,
        bids=[OrderBookLevel(price=99.0, quantity=1.0)],
        asks=[OrderBookLevel(price=101.0, quantity=1.0)],
    )

    with pytest.raises(ValueError, match="orderbook as_of_timestamp"):
        AdvancedFeaturePipeline().compute(
            FeaturePipelineInput(
                klines=[_kline(1, 100.0), _kline(2, 101.0)],
                index=1,
                symbol="BTC-USDT",
                timeframe="1m",
                trades=[Trade(timestamp=2, price=101.0, size=1.0, side="buy")],
                orderbook=future_orderbook,
            )
        )


def test_advanced_feature_pipeline_rejects_future_cross_market_snapshot():
    with pytest.raises(ValueError, match="cross market snapshot timestamp"):
        AdvancedFeaturePipeline().compute(
            FeaturePipelineInput(
                klines=[_kline(1, 100.0), _kline(2, 101.0)],
                index=1,
                symbol="BTC-USDT-SWAP",
                timeframe="1m",
                spot_klines=[_kline(1, 100.0), _kline(3, 103.0)],
                perpetual_klines=[_kline(1, 101.0), _kline(3, 104.0)],
            )
        )
