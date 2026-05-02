import pytest

from quant.data.multi_timeframe import (
    MultiTimeframeDataRequest,
    MultiTimeframeKlineBatch,
    MultiTimeframeKlineProvider,
    TimeframeKlineBatch,
)
from quant.data.schemas.market import Kline, KlineBatch


def _kline(timestamp: int) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
        is_complete=True,
    )


def _batch(timeframe: str, timestamps, *, role: str = "context") -> TimeframeKlineBatch:
    return TimeframeKlineBatch(
        symbol="BTCUSDT",
        timeframe=timeframe,
        venue="fixture",
        role=role,
        klines=[_kline(timestamp) for timestamp in timestamps],
    )


class FixtureProvider:
    def __init__(self):
        self.calls = []

    def get_kline_batch(self, symbol, timeframe, start_ts=None, end_ts=None, limit=100):
        self.calls.append((symbol, timeframe, start_ts, end_ts, limit))
        interval = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[timeframe]
        start = 1700000000
        return KlineBatch(
            symbol=symbol,
            timeframe=timeframe,
            venue="fixture",
            klines=[_kline(start), _kline(start + interval)],
        )


def test_multi_timeframe_request_builds_execution_and_context_requests():
    request = MultiTimeframeDataRequest(
        symbol=" BTCUSDT ",
        venue="fixture",
        execution_timeframe="5m",
        context_timeframes=["15m", "1h", "4h"],
        start_ts=1700000000,
        end_ts=1700014400,
        limit=50,
    )

    timeframe_requests = request.to_timeframe_requests()

    assert request.symbol == "BTCUSDT"
    assert request.timeframes == ["5m", "15m", "1h", "4h"]
    assert [item.timeframe for item in timeframe_requests] == ["5m", "15m", "1h", "4h"]
    assert all(item.symbol == "BTCUSDT" for item in timeframe_requests)
    assert all(item.limit == 50 for item in timeframe_requests)


def test_multi_timeframe_request_rejects_duplicate_timeframes():
    with pytest.raises(ValueError, match="unique"):
        MultiTimeframeDataRequest(
            symbol="BTCUSDT",
            execution_timeframe="5m",
            context_timeframes=["15m", "5m"],
        )


def test_multi_timeframe_batch_expresses_execution_and_context_klines():
    envelope = MultiTimeframeKlineBatch(
        symbol="BTCUSDT",
        venue="fixture",
        execution_timeframe="5m",
        execution=_batch("5m", [1700000000, 1700000300], role="execution"),
        contexts=[
            _batch("15m", [1699999200, 1700000100]),
            _batch("1h", [1699996400, 1700000000]),
            _batch("4h", [1699985600, 1700000000]),
        ],
        as_of_timestamp=1700000300,
    )

    assert envelope.execution is not None
    assert envelope.execution.timeframe == "5m"
    assert envelope.execution.role == "execution"
    assert envelope.context_timeframes == ["15m", "1h", "4h"]
    assert sorted(envelope.timeframe_batches) == ["15m", "1h", "4h", "5m"]
    assert envelope.as_of_timestamp == 1700000300


def test_multi_timeframe_batch_rejects_duplicate_context_timeframe():
    with pytest.raises(ValueError, match="unique"):
        MultiTimeframeKlineBatch(
            symbol="BTCUSDT",
            venue="fixture",
            execution_timeframe="5m",
            execution=_batch("5m", [1700000000], role="execution"),
            contexts=[
                _batch("15m", [1700000000]),
                _batch("15m", [1700000900]),
            ],
        )


def test_provider_wrapper_builds_typed_envelope_from_single_timeframe_provider():
    provider = FixtureProvider()
    wrapper = MultiTimeframeKlineProvider(provider, venue="fixture")
    request = MultiTimeframeDataRequest(
        symbol="BTCUSDT",
        venue="fixture",
        execution_timeframe="5m",
        context_timeframes=["15m", "1h"],
        start_ts=1700000000,
        end_ts=1700014400,
        limit=2,
    )

    envelope = wrapper.get_multi_timeframe_klines(request)

    assert provider.calls == [
        ("BTCUSDT", "5m", 1700000000, 1700014400, 2),
        ("BTCUSDT", "15m", 1700000000, 1700014400, 2),
        ("BTCUSDT", "1h", 1700000000, 1700014400, 2),
    ]
    assert envelope.execution is not None
    assert envelope.execution.role == "execution"
    assert [context.role for context in envelope.contexts] == ["context", "context"]
    assert envelope.as_of_timestamp == 1700000300
