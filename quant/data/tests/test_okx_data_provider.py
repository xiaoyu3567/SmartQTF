import pytest

from adapters.exchange.okx import OKXAdapter
from quant.data.providers.okx_provider import OKXDataProvider
from quant.data.schemas.market import KlineBatch, Trade
from quant.data.storage import JsonlTradeStore
from quant.features.pipeline import AdvancedFeaturePipeline, FeaturePipelineConfig, FeaturePipelineInput
from quant.schemas import (
    FundingRateSnapshot,
    NetflowSnapshot,
    OpenInterestSnapshot,
    OrderBookSnapshot,
    UniverseFilterConfig,
    UniverseSnapshot,
)
from quant.schemas.universe import UniverseInstrument


class FakeOKXPublicAdapter(OKXAdapter):
    def __init__(self):
        self.requests = []

    def _request(self, method, path, *, params=None, body=None, auth=True):
        self.requests.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "body": body,
                "auth": auth,
            }
        )
        if path == "/api/v5/market/candles":
            return {
                "data": [
                    ["1700000060000", "101", "103", "100", "102", "8", "0", "0", "1"],
                    ["1700000000000", "100", "102", "99", "101", "5", "0", "0", "1"],
                ]
            }
        if path == "/api/v5/public/open-interest":
            return {
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "oi": "123.45",
                        "oiCcy": "12345.67",
                        "ts": "1700000123000",
                    }
                ]
            }
        if path == "/api/v5/public/funding-rate":
            return {
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "fundingRate": "0.0001",
                        "fundingTime": "1700000400000",
                        "nextFundingTime": "1700029200000",
                    }
                ]
            }
        if path == "/api/v5/market/trades":
            trades = [
                {
                    "instId": "BTC-USDT",
                    "tradeId": "3",
                    "px": "90",
                    "sz": "2.0",
                    "side": "sell",
                    "ts": "1699999800000",
                },
                {
                    "instId": "BTC-USDT",
                    "tradeId": "2",
                    "px": "101",
                    "sz": "0.4",
                    "side": "sell",
                    "ts": "1700000055000",
                },
                {
                    "instId": "BTC-USDT",
                    "tradeId": "1",
                    "px": "100",
                    "sz": "1.0",
                    "side": "buy",
                    "ts": "1700000050000",
                },
            ]
            limit = (params or {}).get("limit")
            if limit is not None:
                trades = trades[-int(limit):]
            return {"data": trades}
        if path == "/api/v5/market/history-trades":
            trades = [
                {
                    "instId": "BTC-USDT",
                    "tradeId": "3",
                    "px": "90",
                    "sz": "2.0",
                    "side": "sell",
                    "ts": "1699999800000",
                },
                {
                    "instId": "BTC-USDT",
                    "tradeId": "4",
                    "px": "100",
                    "sz": "0.1",
                    "side": "buy",
                    "ts": "1699999750000",
                },
                {
                    "instId": "BTC-USDT",
                    "tradeId": "5",
                    "px": "50",
                    "sz": "2.0",
                    "side": "buy",
                    "ts": "1699999200000",
                },
                {
                    "instId": "BTC-USDT",
                    "tradeId": "6",
                    "px": "60",
                    "sz": "1.0",
                    "side": "sell",
                    "ts": "1699999100000",
                },
                {
                    "instId": "BTC-USDT",
                    "tradeId": "7",
                    "px": "70",
                    "sz": "1.0",
                    "side": "sell",
                    "ts": "1699996500000",
                },
                {
                    "instId": "BTC-USDT",
                    "tradeId": "8",
                    "px": "80",
                    "sz": "1.0",
                    "side": "buy",
                    "ts": "1699996400000",
                },
            ]
            after = (params or {}).get("after")
            if after is not None:
                after_seconds = int(after) // 1000
                trades = [trade for trade in trades if int(trade["ts"]) // 1000 < after_seconds]
            limit = (params or {}).get("limit")
            if limit is not None:
                trades = trades[: int(limit)]
            return {"data": trades}
        if path == "/api/v5/market/books":
            return {
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "ts": "1700000060000",
                        "bids": [["99", "2", "0", "1"], ["98", "1", "0", "1"]],
                        "asks": [["101", "3", "0", "1"], ["102", "1", "0", "1"]],
                    }
                ]
            }
        if path == "/api/v5/public/instruments":
            return {
                "data": [
                    {
                        "instType": "SPOT",
                        "instId": "BTC-USDT",
                        "baseCcy": "BTC",
                        "quoteCcy": "USDT",
                        "state": "live",
                        "lotSz": "0.0001",
                        "minSz": "0.0001",
                        "tickSz": "0.1",
                        "minNotional": "10",
                        "maxMktSz": "10",
                    },
                    {
                        "instType": "SPOT",
                        "instId": "ETH-BTC",
                        "baseCcy": "ETH",
                        "quoteCcy": "BTC",
                        "state": "live",
                        "lotSz": "0.0001",
                        "minSz": "0.0001",
                        "tickSz": "0.00001",
                    },
                    {
                        "instType": "SPOT",
                        "instId": "DOGE-USDT",
                        "baseCcy": "DOGE",
                        "quoteCcy": "USDT",
                        "state": "suspend",
                        "lotSz": "1",
                        "minSz": "1",
                        "tickSz": "0.0001",
                    },
                    {
                        "instType": "SPOT",
                        "instId": "TINY-USDT",
                        "baseCcy": "TINY",
                        "quoteCcy": "USDT",
                        "state": "live",
                        "lotSz": "1",
                        "minSz": "1000",
                        "tickSz": "0.01",
                    },
                    {
                        "instType": "SPOT",
                        "instId": "ILLQ-USDT",
                        "baseCcy": "ILLQ",
                        "quoteCcy": "USDT",
                        "state": "live",
                        "lotSz": "1",
                        "minSz": "1",
                        "tickSz": "0.01",
                    },
                    {
                        "instType": "SPOT",
                        "instId": "BLACK-USDT",
                        "baseCcy": "BLACK",
                        "quoteCcy": "USDT",
                        "state": "live",
                        "lotSz": "1",
                        "minSz": "1",
                        "tickSz": "0.01",
                    },
                ]
            }
        if path == "/api/v5/market/tickers":
            return {
                "data": [
                    {"instId": "BTC-USDT", "vol24h": "2500", "volCcy24h": "100000000", "last": "40000"},
                    {"instId": "ETH-BTC", "vol24h": "100", "volCcy24h": "10", "last": "0.05"},
                    {"instId": "DOGE-USDT", "vol24h": "1000000", "volCcy24h": "80000", "last": "0.08"},
                    {"instId": "TINY-USDT", "vol24h": "100000", "volCcy24h": "50000", "last": "0.05"},
                    {"instId": "ILLQ-USDT", "vol24h": "10", "volCcy24h": "5", "last": "0.5"},
                    {"instId": "BLACK-USDT", "vol24h": "100000", "volCcy24h": "50000", "last": "0.5"},
                ]
            }
        raise AssertionError(f"unexpected path: {path}")


def test_okx_adapter_returns_sorted_typed_kline_batch():
    adapter = FakeOKXPublicAdapter()

    batch = adapter.get_klines(
        "BTCUSDT",
        "1m",
        start_ts=1700000000,
        end_ts=1700000060,
        limit=2,
    )

    assert isinstance(batch, KlineBatch)
    assert batch.symbol == "BTC-USDT"
    assert [kline.timestamp for kline in batch.klines] == [1700000000, 1700000060]
    assert batch.klines[0].close == 101.0
    assert adapter.requests[0]["auth"] is False
    assert adapter.requests[0]["params"] == {
        "instId": "BTC-USDT",
        "bar": "1m",
        "limit": 2,
        "before": 1699999999999,
        "after": 1700000060001,
    }


class ConfirmZeroOKXPublicAdapter(FakeOKXPublicAdapter):
    def _request(self, method, path, *, params=None, body=None, auth=True):
        if path == "/api/v5/market/candles":
            self.requests.append(
                {
                    "method": method,
                    "path": path,
                    "params": params,
                    "body": body,
                    "auth": auth,
                }
            )
            return {
                "data": [
                    ["1700000060000", "101", "103", "100", "102", "8", "0", "0", "0"],
                    ["1700000000000", "100", "102", "99", "101", "5", "0", "0", "1"],
                ]
            }
        return super()._request(method, path, params=params, body=body, auth=auth)


def test_okx_adapter_maps_confirm_zero_to_incomplete_kline():
    adapter = ConfirmZeroOKXPublicAdapter()

    batch = adapter.get_klines("BTCUSDT", "1m", limit=2)

    assert [kline.timestamp for kline in batch.klines] == [1700000000, 1700000060]
    assert [kline.is_complete for kline in batch.klines] == [True, False]
    assert adapter.requests[0]["auth"] is False


def test_okx_adapter_returns_typed_open_interest_and_funding_rate():
    adapter = FakeOKXPublicAdapter()

    open_interest = adapter.get_open_interest("BTCUSDT")
    funding_rate = adapter.get_funding_rate("BTCUSDT")

    assert isinstance(open_interest, OpenInterestSnapshot)
    assert open_interest.symbol == "BTC-USDT-SWAP"
    assert open_interest.open_interest == 123.45
    assert open_interest.open_interest_value == 12345.67

    assert isinstance(funding_rate, FundingRateSnapshot)
    assert funding_rate.symbol == "BTC-USDT-SWAP"
    assert funding_rate.funding_rate == 0.0001
    assert funding_rate.funding_timestamp == 1700000400
    assert funding_rate.next_funding_timestamp == 1700029200
    assert [request["auth"] for request in adapter.requests] == [False, False]


def test_okx_data_provider_exposes_sync_friendly_kline_list_and_snapshots():
    provider = OKXDataProvider(adapter=FakeOKXPublicAdapter())

    klines = provider.get_klines("BTCUSDT", "1m", start_ts=1700000000, end_ts=1700000060, limit=2)
    batch = provider.get_kline_batch("BTCUSDT", "1m", limit=2)
    open_interest = provider.get_open_interest("BTCUSDT")
    funding_rate = provider.get_funding_rate("BTCUSDT")

    assert [kline.timestamp for kline in klines] == [1700000000, 1700000060]
    assert isinstance(batch, KlineBatch)
    assert isinstance(open_interest, OpenInterestSnapshot)
    assert isinstance(funding_rate, FundingRateSnapshot)


def test_okx_adapter_returns_typed_market_microstructure_payloads():
    adapter = FakeOKXPublicAdapter()

    trades = adapter.get_trades("BTCUSDT", limit=2)
    orderbook = adapter.get_orderbook("BTCUSDT", depth=2)
    netflow = adapter.get_netflow("BTCUSDT", timeframe="1m", limit=2)

    assert [request["auth"] for request in adapter.requests] == [False, False, False, False]
    assert [request["path"] for request in adapter.requests] == [
        "/api/v5/market/trades",
        "/api/v5/market/books",
        "/api/v5/market/trades",
        "/api/v5/market/history-trades",
    ]
    assert adapter.requests[0]["params"] == {"instId": "BTC-USDT", "limit": 2}
    assert adapter.requests[1]["params"] == {"instId": "BTC-USDT", "sz": 2}
    assert adapter.requests[3]["params"] == {
        "instId": "BTC-USDT",
        "limit": 2,
        "type": "2",
        "after": 1700000050000,
    }

    assert all(isinstance(trade, Trade) for trade in trades)
    assert [trade.timestamp for trade in trades] == [1700000050, 1700000055]
    assert [trade.side for trade in trades] == ["buy", "sell"]

    assert isinstance(orderbook, OrderBookSnapshot)
    assert orderbook.snapshot_id == "okx-book-BTC-USDT-1700000060"
    assert orderbook.best_bid == 99.0
    assert orderbook.best_ask == 101.0

    assert isinstance(netflow, NetflowSnapshot)
    assert netflow.snapshot_id == "okx-netflow-BTC-USDT-1m-1700000055"
    assert netflow.inflow == 100.0
    assert netflow.outflow == pytest.approx(40.4)
    assert netflow.netflow == pytest.approx(59.6)
    assert netflow.window_start_timestamp == 1699999996
    assert netflow.window_end_timestamp == 1700000055
    assert netflow.trade_records_in_window == 2
    assert netflow.coverage_start == 1699999996
    assert netflow.coverage_end == 1700000055
    assert netflow.coverage_complete is True
    assert netflow.coverage_gap_reason is None


def test_okx_netflow_fetches_history_until_timeframe_window_is_covered():
    adapter = FakeOKXPublicAdapter()

    one_minute = adapter.get_netflow("BTCUSDT", timeframe="1m", limit=2)
    five_minutes = adapter.get_netflow("BTCUSDT", timeframe="5m", limit=2)
    fifteen_minutes = adapter.get_netflow("BTCUSDT", timeframe="15m", limit=2)
    one_hour = adapter.get_netflow("BTCUSDT", timeframe="1h", limit=2)

    assert one_minute.snapshot_id == "okx-netflow-BTC-USDT-1m-1700000055"
    assert one_minute.inflow == 100.0
    assert one_minute.outflow == pytest.approx(40.4)
    assert one_minute.netflow == pytest.approx(59.6)
    assert one_minute.trade_records_in_window == 2
    assert one_minute.coverage_complete is True
    assert one_minute.coverage_gap_reason is None

    assert five_minutes.snapshot_id == "okx-netflow-BTC-USDT-5m-1700000055"
    assert five_minutes.inflow == 100.0
    assert five_minutes.outflow == pytest.approx(220.4)
    assert five_minutes.netflow == pytest.approx(-120.4)
    assert five_minutes.window_start_timestamp == 1699999756
    assert five_minutes.trade_records_in_window == 3
    assert five_minutes.coverage_complete is True
    assert five_minutes.coverage_gap_reason is None

    assert fifteen_minutes.snapshot_id == "okx-netflow-BTC-USDT-15m-1700000055"
    assert fifteen_minutes.inflow == 210.0
    assert fifteen_minutes.outflow == pytest.approx(220.4)
    assert fifteen_minutes.netflow == pytest.approx(-10.4)
    assert fifteen_minutes.window_start_timestamp == 1699999156
    assert fifteen_minutes.trade_records_in_window == 5
    assert fifteen_minutes.coverage_complete is True
    assert fifteen_minutes.coverage_gap_reason is None

    assert one_hour.snapshot_id == "okx-netflow-BTC-USDT-1h-1700000055"
    assert one_hour.inflow == 210.0
    assert one_hour.outflow == pytest.approx(350.4)
    assert one_hour.netflow == pytest.approx(-140.4)
    assert one_hour.window_start_timestamp == 1699996456
    assert one_hour.trade_records_in_window == 7
    assert one_hour.coverage_complete is True
    assert one_hour.coverage_gap_reason is None

    history_requests = [
        request for request in adapter.requests if request["path"] == "/api/v5/market/history-trades"
    ]
    assert history_requests
    assert all(request["auth"] is False for request in history_requests)
    assert all(request["params"]["type"] == "2" for request in history_requests)


class PartialHistoryOKXPublicAdapter(FakeOKXPublicAdapter):
    def _request(self, method, path, *, params=None, body=None, auth=True):
        if path == "/api/v5/market/history-trades":
            self.requests.append(
                {
                    "method": method,
                    "path": path,
                    "params": params,
                    "body": body,
                    "auth": auth,
                }
            )
            return {"data": []}
        return super()._request(method, path, params=params, body=body, auth=auth)


def test_okx_netflow_marks_partial_when_history_cannot_cover_window():
    adapter = PartialHistoryOKXPublicAdapter()

    netflow = adapter.get_netflow("BTCUSDT", timeframe="1h", limit=2)

    assert netflow.inflow == 100.0
    assert netflow.outflow == pytest.approx(40.4)
    assert netflow.netflow == pytest.approx(59.6)
    assert netflow.trade_records_in_window == 2
    assert netflow.coverage_start == 1700000050
    assert netflow.coverage_end == 1700000055
    assert netflow.coverage_complete is False
    assert netflow.coverage_gap_reason == "history_exhausted_before_window_start"
    assert adapter.requests[-1]["path"] == "/api/v5/market/history-trades"
    assert adapter.requests[-1]["auth"] is False


def test_okx_data_provider_uses_trade_store_for_netflow_and_public_backfill(tmp_path):
    adapter = FakeOKXPublicAdapter()
    store = JsonlTradeStore(tmp_path)
    provider = OKXDataProvider(adapter=adapter, trade_store=store)

    netflow = provider.get_netflow(
        "BTCUSDT",
        timeframe="1h",
        limit=2,
        end_ts=1700000055,
        max_history_pages=4,
    )

    assert netflow.venue == "okx_trade_store"
    assert netflow.window_start_timestamp == 1699996456
    assert netflow.window_end_timestamp == 1700000055
    assert netflow.coverage_complete is True
    assert netflow.coverage_gap_reason is None
    assert netflow.trade_records_in_window == 7
    assert netflow.inflow == 210.0
    assert netflow.outflow == pytest.approx(350.4)
    assert netflow.netflow == pytest.approx(-140.4)

    assert [request["path"] for request in adapter.requests] == [
        "/api/v5/market/trades",
        "/api/v5/market/history-trades",
        "/api/v5/market/history-trades",
        "/api/v5/market/history-trades",
    ]
    assert all(request["auth"] is False for request in adapter.requests)
    assert store.coverage_for_window("BTCUSDT", 1699996456, 1700000055).coverage_complete

    adapter.requests.clear()
    cached_netflow = provider.get_netflow(
        "BTCUSDT",
        timeframe="1h",
        limit=2,
        end_ts=1700000055,
        max_history_pages=4,
    )
    assert cached_netflow.coverage_complete is True
    assert cached_netflow.netflow == pytest.approx(netflow.netflow)
    assert adapter.requests == []


def test_okx_adapter_deduplicates_raw_trades_without_trade_id_by_tuple():
    adapter = FakeOKXPublicAdapter()
    trades = [
        Trade(timestamp=1700000000, price=100.0, size=1.0, side="buy"),
        Trade(timestamp=1700000000, price=100.0, size=1.0, side="buy"),
        Trade(timestamp=1700000000, price=100.0, size=2.0, side="buy"),
        Trade(timestamp=1700000001, price=100.0, size=1.0, side="sell"),
    ]

    deduped = adapter._deduplicate_trades(trades)

    assert len(deduped) == 3
    assert [(trade.timestamp, trade.price, trade.size, trade.side) for trade in deduped] == [
        (1700000000, 100.0, 1.0, "buy"),
        (1700000000, 100.0, 2.0, "buy"),
        (1700000001, 100.0, 1.0, "sell"),
    ]


def test_okx_data_provider_microstructure_inputs_feed_advanced_feature_pipeline():
    provider = OKXDataProvider(adapter=FakeOKXPublicAdapter())

    klines = provider.get_klines("BTCUSDT", "1m", limit=2)
    trades = provider.get_trades("BTCUSDT", limit=2)
    orderbook = provider.get_orderbook("BTCUSDT", depth=1)
    netflow = provider.get_netflow("BTCUSDT", timeframe="1m", limit=2)

    snapshot = AdvancedFeaturePipeline(
        FeaturePipelineConfig(
            fast_ma_window=1,
            slow_ma_window=2,
            large_trade_threshold=0.5,
            orderbook_depth=1,
        )
    ).compute(
        FeaturePipelineInput(
            klines=klines,
            index=1,
            symbol="BTC-USDT",
            timeframe="1m",
            venue="okx",
            trades=trades,
            orderbook=orderbook,
        )
    )

    assert isinstance(netflow, NetflowSnapshot)
    assert snapshot.values["orderflow.buy_volume"] == 1.0
    assert snapshot.values["orderflow.sell_volume"] == 0.4
    assert snapshot.values["orderflow.imbalance"] == pytest.approx(0.6)
    assert snapshot.values["orderflow.orderbook_imbalance"] == pytest.approx(-0.2)


def test_okx_adapter_returns_typed_universe_instruments_from_public_metadata():
    adapter = FakeOKXPublicAdapter()

    instruments = adapter.get_universe_instruments(instrument_type="SPOT")

    assert [request["auth"] for request in adapter.requests] == [False, False]
    assert adapter.requests[0]["path"] == "/api/v5/public/instruments"
    assert adapter.requests[1]["path"] == "/api/v5/market/tickers"
    assert all(isinstance(instrument, UniverseInstrument) for instrument in instruments)
    btc = next(instrument for instrument in instruments if instrument.symbol == "BTC-USDT")
    assert btc.quote_currency == "USDT"
    assert btc.status == "live"
    assert btc.quantity_step == 0.0001
    assert btc.min_notional == 10.0
    assert btc.turnover_24h == 100000000.0
    assert btc.last_price == 40000.0


def test_okx_data_provider_discovers_filtered_replayable_universe_snapshot():
    provider = OKXDataProvider(adapter=FakeOKXPublicAdapter())
    config = UniverseFilterConfig(
        quote_currencies=["USDT"],
        blacklist=["BLACK-USDT"],
        min_turnover_24h=10000,
        max_min_quantity=10,
        max_min_notional=20,
    )

    snapshot = provider.discover_universe(config, as_of_timestamp=1700001000)

    assert isinstance(snapshot, UniverseSnapshot)
    assert snapshot.snapshot_id == "okx-spot-universe-1700001000"
    assert [instrument.symbol for instrument in snapshot.instruments] == ["BTC-USDT"]
    rejection_codes = {(item.symbol, item.reason_code) for item in snapshot.rejected}
    assert ("ETH-BTC", "quote_currency_not_allowed") in rejection_codes
    assert ("DOGE-USDT", "status_not_allowed") in rejection_codes
    assert ("TINY-USDT", "min_quantity_too_large") in rejection_codes
    assert ("ILLQ-USDT", "turnover_below_minimum") in rejection_codes
    assert ("BLACK-USDT", "blacklisted_symbol") in rejection_codes

    restored = UniverseSnapshot.from_payload(snapshot.to_payload())
    assert restored.instruments[0].symbol == "BTC-USDT"
    assert restored.rejected[0].symbol


def test_universe_schema_rejects_invalid_filter_and_instrument_values():
    with pytest.raises(ValueError):
        UniverseFilterConfig(min_turnover_24h=-1.0)

    with pytest.raises(ValueError):
        UniverseInstrument(
            symbol="BAD-USDT",
            venue="okx",
            instrument_type="SPOT",
            base_currency="BAD",
            quote_currency="USDT",
            status="live",
            min_quantity=-1.0,
        )
