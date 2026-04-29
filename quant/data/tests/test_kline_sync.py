import pytest

from quant.data.schemas.market import Kline
from quant.data.storage import JsonlKlineStore
from quant.data.sync import (
    KlineSyncQualityError,
    KlineSyncRequest,
    build_incremental_kline_sync_plan,
    last_closed_kline_ts,
    save_validated_kline_sync_batch,
    timeframe_to_seconds,
    validate_kline_sync_batch,
)


def _kline(timestamp: int) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
    )


def test_incremental_kline_sync_plan_requests_only_missing_closed_windows(tmp_path):
    store = JsonlKlineStore(tmp_path)
    store.save_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[
            _kline(1700000040),
            _kline(1700000100),
            _kline(1700000280),
        ],
    )

    plan = build_incremental_kline_sync_plan(
        store=store,
        symbol="BTCUSDT",
        timeframe="1m",
        start_ts=1700000040,
        now_ts=1700000400,
    )

    assert plan.closed_until_ts == 1700000340
    assert [(request.start_ts, request.end_ts) for request in plan.requests] == [
        (1700000160, 1700000220),
        (1700000340, 1700000340),
    ]


def test_incremental_kline_sync_plan_does_not_request_open_kline(tmp_path):
    store = JsonlKlineStore(tmp_path)

    plan = build_incremental_kline_sync_plan(
        store=store,
        symbol="BTCUSDT",
        timeframe="1m",
        start_ts=1700000400,
        now_ts=1700000400,
    )

    assert plan.closed_until_ts == 1700000340
    assert plan.requests == []
    assert plan.is_empty


def test_incremental_kline_sync_plan_is_empty_when_store_is_complete(tmp_path):
    store = JsonlKlineStore(tmp_path)
    store.save_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[
            _kline(1700000040),
            _kline(1700000100),
            _kline(1700000160),
        ],
    )

    plan = build_incremental_kline_sync_plan(
        store=store,
        symbol="BTCUSDT",
        timeframe="1m",
        start_ts=1700000040,
        now_ts=1700000220,
    )

    assert plan.closed_until_ts == 1700000160
    assert plan.requests == []


def test_timeframe_to_seconds_rejects_unsupported_timeframe():
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        timeframe_to_seconds("7m")


def test_last_closed_kline_ts_returns_previous_bar_open():
    assert last_closed_kline_ts(now_ts=1700000400, interval_seconds=60) == 1700000340


def test_kline_sync_batch_validates_expected_request_window():
    request = KlineSyncRequest(
        symbol="BTCUSDT",
        timeframe="1m",
        start_ts=1700000040,
        end_ts=1700000160,
        interval_seconds=60,
    )

    report = validate_kline_sync_batch(
        request=request,
        klines=[_kline(1700000040), _kline(1700000100)],
    )

    assert not report.passed
    assert report.issues[-1].timestamp == 1700000160


def test_kline_sync_batch_saves_only_after_quality_passes(tmp_path):
    store = JsonlKlineStore(tmp_path)
    request = KlineSyncRequest(
        symbol="BTCUSDT",
        timeframe="1m",
        start_ts=1700000040,
        end_ts=1700000100,
        interval_seconds=60,
    )

    result = save_validated_kline_sync_batch(
        store=store,
        request=request,
        klines=[_kline(1700000040), _kline(1700000100)],
    )

    assert result.passed
    assert result.saved_count == 2
    assert [kline.timestamp for kline in store.load_klines("BTCUSDT", "1m")] == [
        1700000040,
        1700000100,
    ]


def test_kline_sync_batch_does_not_save_failed_quality_batch(tmp_path):
    store = JsonlKlineStore(tmp_path)
    request = KlineSyncRequest(
        symbol="BTCUSDT",
        timeframe="1m",
        start_ts=1700000040,
        end_ts=1700000160,
        interval_seconds=60,
    )

    with pytest.raises(KlineSyncQualityError):
        save_validated_kline_sync_batch(
            store=store,
            request=request,
            klines=[_kline(1700000040), _kline(1700000160)],
        )

    assert store.load_klines("BTCUSDT", "1m") == []
