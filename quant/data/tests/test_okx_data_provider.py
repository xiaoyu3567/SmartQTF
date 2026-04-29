import pytest

from adapters.exchange.okx import OKXAdapter
from quant.data.providers.okx_provider import OKXDataProvider
from quant.data.schemas.market import KlineBatch, Trade
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
            return {
                "data": [
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
            }
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
        "after": 1700000000000,
        "before": 1700000060000,
    }


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

    assert [request["auth"] for request in adapter.requests] == [False, False, False]
    assert [request["path"] for request in adapter.requests] == [
        "/api/v5/market/trades",
        "/api/v5/market/books",
        "/api/v5/market/trades",
    ]
    assert adapter.requests[0]["params"] == {"instId": "BTC-USDT", "limit": 2}
    assert adapter.requests[1]["params"] == {"instId": "BTC-USDT", "sz": 2}

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
