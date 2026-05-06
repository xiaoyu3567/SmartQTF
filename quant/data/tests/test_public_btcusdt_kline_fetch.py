import json

import pytest

from adapters.exchange.binance import BinanceAdapter, BinanceAdapterError
from quant.data.schemas.market import Kline, KlineBatch
from scripts.fetch_public_btcusdt_klines import run_public_kline_fetch


class FakeBinancePublicAdapter(BinanceAdapter):
    def __init__(self):
        self.requests = []

    def _public_request(self, method, path, *, params=None):
        self.requests.append({"method": method, "path": path, "params": params})
        if path != "/api/v3/klines":
            raise AssertionError(f"unexpected path: {path}")
        return {
            "data": [
                [
                    1700000300000,
                    "102",
                    "104",
                    "101",
                    "103",
                    "7.5",
                    1700000599999,
                    "0",
                    10,
                    "0",
                    "0",
                    "0",
                ],
                [
                    1700000000000,
                    "100",
                    "103",
                    "99",
                    "102",
                    "5.0",
                    1700000299999,
                    "0",
                    10,
                    "0",
                    "0",
                    "0",
                ],
            ]
        }


class FakeSuccessfulFetchAdapter:
    def get_klines(self, symbol, timeframe, *, limit=100, start_ts=None, end_ts=None):
        return KlineBatch(
            symbol=symbol,
            timeframe=timeframe,
            venue="binance",
            klines=[
                _kline(1700000000, 100.0),
                _kline(1700000300, 101.0),
                _kline(1700000600, 102.0),
            ],
        )


class FakeGappyFetchAdapter:
    def get_klines(self, symbol, timeframe, *, limit=100, start_ts=None, end_ts=None):
        return KlineBatch(
            symbol=symbol,
            timeframe=timeframe,
            venue="binance",
            klines=[
                _kline(1700000000, 100.0),
                _kline(1700000600, 102.0),
            ],
        )


class FailingFetchAdapter:
    def get_klines(self, symbol, timeframe, *, limit=100, start_ts=None, end_ts=None):
        raise BinanceAdapterError("DNS lookup failed")


def test_binance_adapter_returns_sorted_typed_public_kline_batch():
    adapter = FakeBinancePublicAdapter()

    batch = adapter.get_klines(
        "BTC-USDT",
        "5m",
        start_ts=1700000000,
        end_ts=1700000300,
        limit=2,
    )

    assert isinstance(batch, KlineBatch)
    assert batch.symbol == "BTCUSDT"
    assert [kline.timestamp for kline in batch.klines] == [1700000000, 1700000300]
    assert batch.klines[0].close == 102.0
    assert batch.klines[0].is_complete is True
    assert adapter.requests == [
        {
            "method": "GET",
            "path": "/api/v3/klines",
            "params": {
                "symbol": "BTCUSDT",
                "interval": "5m",
                "limit": 2,
                "startTime": 1700000000000,
                "endTime": 1700000300000,
            },
        }
    ]


def test_public_kline_fetch_writes_pass_report_with_fingerprint(tmp_path):
    output = tmp_path / "btcusdt-5m-latest.json"

    report = run_public_kline_fetch(
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="5m",
        limit=3,
        min_bars=3,
        output=output,
        timestamp=1700000900,
        adapter=FakeSuccessfulFetchAdapter(),
    )

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert report == saved
    assert saved["status"] == "PASS"
    assert saved["bar_count"] == 3
    assert saved["first_timestamp"] == 1700000000
    assert saved["last_timestamp"] == 1700000600
    assert len(saved["sha256"]) == 64
    assert saved["quality_report"]["passed"] is True
    assert saved["safety_flags"] == {
        "network_access_used": True,
        "public_market_data_only": True,
        "real_credentials_read": False,
        "contains_real_credentials": False,
        "account_or_order_endpoint_called": False,
        "broker_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
    }
    assert saved["klines"][0]["timestamp"] == 1700000000


def test_public_kline_fetch_fails_quality_without_faking_success(tmp_path):
    output = tmp_path / "btcusdt-5m-latest.json"

    report = run_public_kline_fetch(
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="5m",
        limit=2,
        min_bars=2,
        output=output,
        timestamp=1700000900,
        adapter=FakeGappyFetchAdapter(),
    )

    assert report["status"] == "FAIL"
    assert report["reason_codes"] == ["public_kline_quality_failed"]
    assert report["quality_report"]["passed"] is False
    assert report["bar_count"] == 2


def test_public_kline_fetch_skips_when_public_endpoint_unavailable(tmp_path):
    output = tmp_path / "btcusdt-5m-latest.json"

    report = run_public_kline_fetch(
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="5m",
        limit=500,
        min_bars=500,
        output=output,
        timestamp=1700000900,
        adapter=FailingFetchAdapter(),
    )

    assert report["status"] == "SKIPPED"
    assert report["reason_codes"] == ["public_market_data_unavailable"]
    assert report["bar_count"] == 0
    assert report["klines"] == []
    assert report["error"]["category"] == "BinanceAdapterError"
    assert "DNS lookup failed" in report["error"]["message"]
    assert report["safety_flags"]["real_credentials_read"] is False
    assert report["safety_flags"]["live_orders_sent"] is False


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
