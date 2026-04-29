import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.data.providers.mock_provider import MockProvider
from quant.data.schemas.market import Kline, Trade
from quant.data.storage import JsonlKlineStore


def test_kline_schema():
    kline = Kline(
        timestamp=1700000000,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
    )

    assert isinstance(kline.timestamp, int)
    assert isinstance(kline.open, float)
    assert isinstance(kline.high, float)
    assert isinstance(kline.low, float)
    assert isinstance(kline.close, float)
    assert isinstance(kline.volume, float)


def test_trade_schema():
    trade = Trade(
        timestamp=1700000001,
        price=100.1,
        size=0.5,
        side="buy",
    )

    assert isinstance(trade.timestamp, int)
    assert isinstance(trade.price, float)
    assert isinstance(trade.size, float)
    assert isinstance(trade.side, str)


def test_mock_provider_klines():
    provider = MockProvider()
    klines = provider.get_klines(symbol="BTCUSDT", timeframe="1m")
    kline_interval = klines[1].timestamp - klines[0].timestamp

    assert len(klines) >= 10
    assert all(isinstance(kline, Kline) for kline in klines)
    assert all(isinstance(kline.timestamp, int) for kline in klines)
    assert all(isinstance(kline.open, float) for kline in klines)
    assert all(isinstance(kline.high, float) for kline in klines)
    assert all(isinstance(kline.low, float) for kline in klines)
    assert all(isinstance(kline.close, float) for kline in klines)
    assert all(isinstance(kline.volume, float) for kline in klines)
    assert all(klines[index].timestamp < klines[index + 1].timestamp for index in range(len(klines) - 1))
    assert all(
        klines[index + 1].timestamp - klines[index].timestamp == kline_interval
        for index in range(len(klines) - 1)
    )
    assert all(kline.low <= kline.open <= kline.high for kline in klines)
    assert all(kline.low <= kline.close <= kline.high for kline in klines)


def test_mock_provider_trades():
    provider = MockProvider()
    trades = provider.get_trades(symbol="BTCUSDT")

    assert len(trades) >= 20
    assert all(isinstance(trade, Trade) for trade in trades)
    assert all(isinstance(trade.timestamp, int) for trade in trades)
    assert all(isinstance(trade.price, float) for trade in trades)
    assert all(isinstance(trade.size, float) for trade in trades)
    assert all(isinstance(trade.side, str) for trade in trades)
    assert all(trades[index].timestamp < trades[index + 1].timestamp for index in range(len(trades) - 1))
    assert all(trade.side in ["buy", "sell"] for trade in trades)


def test_jsonl_kline_store_round_trip(tmp_path):
    provider = MockProvider()
    store = JsonlKlineStore(tmp_path)
    klines = provider.get_klines(symbol="BTCUSDT", timeframe="1m")

    saved_count = store.save_klines(symbol="BTCUSDT", timeframe="1m", klines=reversed(klines))
    loaded = store.load_klines(symbol="BTCUSDT", timeframe="1m")

    assert saved_count == len(klines)
    assert loaded == klines


def test_jsonl_kline_store_filters_timestamp_range(tmp_path):
    provider = MockProvider()
    store = JsonlKlineStore(tmp_path)
    klines = provider.get_klines(symbol="BTCUSDT", timeframe="1m")
    store.save_klines(symbol="BTCUSDT", timeframe="1m", klines=klines)

    loaded = store.load_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        start_ts=klines[2].timestamp,
        end_ts=klines[4].timestamp,
    )

    assert loaded == klines[2:5]


def test_jsonl_kline_store_deduplicates_by_timestamp(tmp_path):
    provider = MockProvider()
    store = JsonlKlineStore(tmp_path)
    klines = provider.get_klines(symbol="BTCUSDT", timeframe="1m")

    saved_count = store.save_klines(symbol="BTCUSDT", timeframe="1m", klines=[klines[0], klines[0], klines[1]])
    loaded = store.load_klines(symbol="BTCUSDT", timeframe="1m")

    assert saved_count == 2
    assert loaded == klines[:2]
