import pytest

from quant.data.schemas.market import Kline, KlineBatch, Trade


def _valid_kline_payload(**overrides):
    payload = {
        "timestamp": 1700000000,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 10.0,
    }
    payload.update(overrides)
    return payload


def _valid_trade_payload(**overrides):
    payload = {
        "timestamp": 1700000001,
        "price": 100.25,
        "size": 0.5,
        "side": "buy",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"timestamp": -1},
        {"open": -100.0},
        {"high": 98.0},
        {"low": 102.0},
        {"close": 102.0},
        {"volume": -1.0},
    ],
)
def test_kline_schema_rejects_invalid_market_values(overrides):
    with pytest.raises(ValueError):
        Kline(**_valid_kline_payload(**overrides))


def test_kline_schema_accepts_valid_ohlcv_payload():
    kline = Kline(**_valid_kline_payload())

    assert kline.timestamp == 1700000000
    assert kline.low <= kline.open <= kline.high
    assert kline.low <= kline.close <= kline.high
    assert kline.volume == 10.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"timestamp": -1},
        {"price": 0.0},
        {"size": 0.0},
        {"side": "hold"},
    ],
)
def test_trade_schema_rejects_invalid_trade_payloads(overrides):
    with pytest.raises(ValueError):
        Trade(**_valid_trade_payload(**overrides))


def test_trade_schema_normalizes_valid_side():
    trade = Trade(**_valid_trade_payload(side=" BUY "))

    assert trade.side == "buy"


def test_kline_batch_rejects_unordered_or_duplicate_klines():
    first = Kline(**_valid_kline_payload(timestamp=1700000000))
    second = Kline(**_valid_kline_payload(timestamp=1700000060))

    with pytest.raises(ValueError, match="sorted"):
        KlineBatch(symbol="BTC-USDT", timeframe="1m", venue="okx", klines=[second, first])

    with pytest.raises(ValueError, match="duplicate"):
        KlineBatch(symbol="BTC-USDT", timeframe="1m", venue="okx", klines=[first, first])
