from quant.data.schemas.market import Trade
from quant.data.storage import JsonlTradeStore
from quant.data.trade_sync import (
    TradeStoreNetflowService,
    TradeWindowSyncRequest,
    get_netflow_from_trade_store,
    sync_trade_window,
)


def _trade(timestamp: int, side: str, *, trade_id: str | None = None, price: float = 100.0) -> Trade:
    return Trade(
        timestamp=timestamp,
        price=price,
        size=1.0,
        side=side,
        trade_id=trade_id,
    )


class FakePublicTradeSource:
    def __init__(self):
        self.calls = []

    def get_trades(self, symbol: str, limit: int = 100):
        self.calls.append(("recent", symbol, limit, None))
        return [
            _trade(1700003560, "buy", trade_id="recent-2", price=105.0),
            _trade(1700003550, "sell", trade_id="recent-1", price=101.0),
        ][-limit:]

    def get_history_trades(self, symbol: str, *, limit: int = 100, after_ts: int | None = None):
        self.calls.append(("history", symbol, limit, after_ts))
        history = {
            1700003550: [
                _trade(1700003000, "buy", trade_id="history-2", price=99.0),
                _trade(1700002500, "sell", trade_id="history-1", price=98.0),
            ],
            1700002500: [
                _trade(1700001000, "buy", trade_id="history-0", price=97.0),
                _trade(1699999961, "sell", trade_id="history-start", price=96.0),
            ],
        }
        return history.get(after_ts, [])[:limit]


def test_jsonl_trade_store_round_trip_dedupes_and_persists_coverage(tmp_path):
    store = JsonlTradeStore(tmp_path)
    trades = [
        _trade(1700000002, "sell", trade_id="2"),
        _trade(1700000001, "buy", trade_id="1"),
        _trade(1700000001, "buy", trade_id="1"),
        _trade(1700000003, "buy"),
        _trade(1700000003, "buy"),
    ]

    saved_count = store.save_trades(
        symbol="BTC-USDT",
        trades=trades,
        coverage_start=1700000000,
        coverage_end=1700000060,
        source="fixture",
    )
    reloaded = JsonlTradeStore(tmp_path)

    assert saved_count == 3
    assert [trade.trade_id for trade in reloaded.load_trades("BTC-USDT")] == ["1", "2", None]
    assert [trade.timestamp for trade in reloaded.load_trades("BTC-USDT")] == [
        1700000001,
        1700000002,
        1700000003,
    ]
    coverage = reloaded.coverage_for_window("BTC-USDT", 1700000000, 1700000060)
    assert coverage.coverage_complete
    assert coverage.coverage_start == 1700000000
    assert coverage.coverage_end == 1700000060


def test_jsonl_trade_store_reports_partial_coverage_gap(tmp_path):
    store = JsonlTradeStore(tmp_path)
    store.save_trades(
        symbol="BTC-USDT",
        trades=[_trade(1700000020, "buy", trade_id="1")],
        coverage_start=1700000000,
        coverage_end=1700000030,
        source="fixture",
    )

    coverage = store.coverage_for_window("BTC-USDT", 1700000000, 1700000060)

    assert not coverage.coverage_complete
    assert coverage.coverage_gap_reason == "local_trade_store_window_incomplete"
    assert coverage.coverage_start == 1700000000
    assert coverage.coverage_end == 1700000030


def test_sync_trade_window_uses_public_recent_and_history_without_broker(tmp_path):
    store = JsonlTradeStore(tmp_path)
    source = FakePublicTradeSource()

    result = sync_trade_window(
        store=store,
        source=source,
        request=TradeWindowSyncRequest(
            symbol="BTC-USDT",
            start_ts=1699999961,
            end_ts=1700003560,
            limit=2,
            max_history_pages=3,
        ),
    )

    assert result.coverage_complete
    assert result.coverage_gap_reason is None
    assert result.public_only
    assert not result.broker_called
    assert not result.live_orders_sent
    assert result.recent_pages_fetched == 1
    assert result.history_pages_fetched == 2
    assert [call[0] for call in source.calls] == ["recent", "history", "history"]
    assert store.coverage_for_window("BTC-USDT", 1699999961, 1700003560).coverage_complete
    assert [trade.trade_id for trade in store.load_trades("BTC-USDT", 1699999961, 1700003560)] == [
        "history-start",
        "history-0",
        "history-1",
        "history-2",
        "recent-1",
        "recent-2",
    ]


def test_trade_store_netflow_service_prefers_local_complete_window(tmp_path):
    store = JsonlTradeStore(tmp_path)
    store.save_trades(
        symbol="BTC-USDT",
        trades=[
            _trade(1700000001, "buy", trade_id="buy-1", price=100.0),
            _trade(1700000010, "sell", trade_id="sell-1", price=40.0),
            _trade(1700000059, "buy", trade_id="buy-2", price=25.0),
        ],
        coverage_start=1700000000,
        coverage_end=1700000059,
        source="fixture",
    )
    source = FakePublicTradeSource()

    snapshot = TradeStoreNetflowService(
        store,
        backfill_source=source,
    ).get_netflow("BTC-USDT", timeframe="1m", end_ts=1700000059)

    assert snapshot.inflow == 125.0
    assert snapshot.outflow == 40.0
    assert snapshot.netflow == 85.0
    assert snapshot.trade_records_in_window == 3
    assert snapshot.coverage_complete
    assert source.calls == []


def test_trade_store_netflow_backfills_partial_window_then_marks_complete(tmp_path):
    store = JsonlTradeStore(tmp_path)
    source = FakePublicTradeSource()

    snapshot = get_netflow_from_trade_store(
        store=store,
        symbol="BTC-USDT",
        timeframe="1h",
        end_ts=1700003560,
        backfill_source=source,
        limit=2,
        max_history_pages=3,
    )

    assert snapshot.window_start_timestamp == 1699999961
    assert snapshot.trade_records_in_window == 6
    assert snapshot.coverage_complete
    assert snapshot.coverage_gap_reason is None
    assert snapshot.inflow == 301.0
    assert snapshot.outflow == 295.0
    assert snapshot.netflow == 6.0
    assert [call[0] for call in source.calls] == ["recent", "history", "history"]
