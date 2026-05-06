import json

from adapters.exchange.binance import BinanceAdapterError
from quant.data.schemas.market import Kline, KlineBatch
from scripts.fetch_public_btcusdt_klines_matrix import run_public_kline_matrix_fetch


class FakePagedPublicAdapter:
    def __init__(self, klines_by_timeframe, failures_by_timeframe=None):
        self.klines_by_timeframe = klines_by_timeframe
        self.failures_by_timeframe = failures_by_timeframe or {}
        self.requests = []

    def get_klines(self, symbol, timeframe, *, limit=100, start_ts=None, end_ts=None):
        self.requests.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "limit": limit,
                "start_ts": start_ts,
                "end_ts": end_ts,
            }
        )
        if timeframe in self.failures_by_timeframe:
            raise self.failures_by_timeframe[timeframe]
        klines = [
            kline
            for kline in self.klines_by_timeframe.get(timeframe, [])
            if (start_ts is None or kline.timestamp >= start_ts)
            and (end_ts is None or kline.timestamp <= end_ts)
        ]
        return KlineBatch(
            symbol=symbol,
            timeframe=timeframe,
            venue="binance",
            klines=sorted(klines, key=lambda item: item.timestamp)[:limit],
        )


def test_public_kline_matrix_fetches_pages_and_writes_summary(tmp_path):
    adapter = FakePagedPublicAdapter(
        {
            "5m": [
                _kline(0, 100.0),
                _kline(300, 101.0),
                _kline(600, 102.0),
                _kline(900, 103.0),
            ]
        }
    )

    report = run_public_kline_matrix_fetch(
        exchange="binance",
        symbol="BTCUSDT",
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=4,
        page_limit=2,
        max_pages=2,
        output_dir=tmp_path,
        summary_output=tmp_path / "btcusdt-mtf-latest.json",
        timestamp=1201,
        adapter=adapter,
    )

    saved_summary = json.loads((tmp_path / "btcusdt-mtf-latest.json").read_text(encoding="utf-8"))
    saved_timeframe = json.loads((tmp_path / "btcusdt-5m-4-latest.json").read_text(encoding="utf-8"))
    assert report == saved_summary
    assert saved_summary["status"] == "PASS"
    assert saved_summary["minimum_timeframes_passed"] is True
    assert saved_summary["h_opt_018_ready"] is True
    assert saved_summary["timeframes"]["5m"]["output_path"].endswith("btcusdt-5m-4-latest.json")
    assert saved_timeframe["status"] == "PASS"
    assert saved_timeframe["bar_count"] == 4
    assert saved_timeframe["first_timestamp"] == 0
    assert saved_timeframe["last_timestamp"] == 900
    assert saved_timeframe["quality_report"]["passed"] is True
    assert len(saved_timeframe["sha256"]) == 64
    assert saved_timeframe["safety_flags"]["public_market_data_only"] is True
    assert saved_timeframe["safety_flags"]["real_credentials_read"] is False
    assert adapter.requests == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "limit": 2,
            "start_ts": 600,
            "end_ts": 900,
        },
        {
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "limit": 2,
            "start_ts": 0,
            "end_ts": 300,
        },
    ]


def test_public_kline_matrix_records_partial_skipped_without_faking_success(tmp_path):
    adapter = FakePagedPublicAdapter(
        {
            "1m": [
                _kline(0, 100.0),
                _kline(60, 101.0),
            ]
        },
        failures_by_timeframe={"5m": BinanceAdapterError("DNS lookup failed")},
    )

    report = run_public_kline_matrix_fetch(
        exchange="binance",
        symbol="BTCUSDT",
        timeframes=["1m", "5m"],
        required_timeframes=["1m", "5m"],
        target_bars=2,
        page_limit=2,
        max_pages=1,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=121,
        adapter=adapter,
    )

    assert report["status"] == "PARTIAL"
    assert report["minimum_timeframes_passed"] is False
    assert report["pass_timeframes"] == ["1m"]
    assert report["skipped_timeframes"] == ["5m"]
    assert "required_timeframe_not_passed" in report["reason_codes"]
    skipped = json.loads((tmp_path / "btcusdt-5m-2-latest.json").read_text(encoding="utf-8"))
    assert skipped["status"] == "SKIPPED"
    assert skipped["bar_count"] == 0
    assert skipped["reason_codes"] == ["public_market_data_unavailable"]
    assert skipped["safety_flags"]["broker_called"] is False


def test_public_kline_matrix_fails_gappy_timeframe_quality(tmp_path):
    adapter = FakePagedPublicAdapter(
        {
            "5m": [
                _kline(0, 100.0),
                _kline(600, 102.0),
            ]
        }
    )

    report = run_public_kline_matrix_fetch(
        exchange="binance",
        symbol="BTCUSDT",
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=2,
        page_limit=3,
        max_pages=1,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=901,
        adapter=adapter,
    )

    saved_timeframe = json.loads((tmp_path / "btcusdt-5m-2-latest.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["failed_timeframes"] == ["5m"]
    assert saved_timeframe["status"] == "FAIL"
    assert saved_timeframe["reason_codes"] == ["public_kline_quality_failed"]
    assert saved_timeframe["quality_report"]["passed"] is False


def _kline(timestamp: int, close: float) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1.0,
        is_complete=True,
    )
