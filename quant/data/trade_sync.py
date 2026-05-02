from __future__ import annotations

from typing import Iterable, List, Optional, Protocol, Tuple

from pydantic import Field

from quant.data.schemas.market import Trade
from quant.data.storage import TradeCoverageReport, TradeStore
from quant.data.sync import timeframe_to_seconds
from quant.schemas import NetflowSnapshot
from quant.schemas.base import SmartQTFModel


class PublicTradeSource(Protocol):
    def get_trades(self, symbol: str, limit: int = 100) -> List[Trade]:
        ...


class PublicHistoricalTradeSource(PublicTradeSource, Protocol):
    def get_history_trades(
        self,
        symbol: str,
        *,
        limit: int = 100,
        after_ts: Optional[int] = None,
    ) -> List[Trade]:
        ...


class TradeWindowSyncRequest(SmartQTFModel):
    symbol: str
    start_ts: int
    end_ts: int
    limit: int = Field(default=100, gt=0)
    max_history_pages: int = Field(default=20, ge=0)

    def __init__(self, **data):
        super().__init__(**data)
        if self.start_ts < 0 or self.end_ts < 0:
            raise ValueError("trade sync timestamps must be non-negative")
        if self.end_ts < self.start_ts:
            raise ValueError("end_ts must be greater than or equal to start_ts")


class TradeWindowSyncResult(SmartQTFModel):
    request: TradeWindowSyncRequest
    fetched_count: int
    saved_count: int
    trade_records_in_window: int
    coverage_start: Optional[int] = None
    coverage_end: Optional[int] = None
    coverage_complete: bool = False
    coverage_gap_reason: Optional[str] = None
    recent_pages_fetched: int = 0
    history_pages_fetched: int = 0
    public_only: bool = True
    broker_called: bool = False
    live_orders_sent: bool = False


class TradeStoreNetflowService:
    def __init__(
        self,
        store: TradeStore,
        *,
        backfill_source: Optional[PublicTradeSource] = None,
        venue: str = "local_trade_store",
    ):
        self.store = store
        self.backfill_source = backfill_source
        self.venue = venue

    def get_netflow(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        end_ts: Optional[int] = None,
        limit: int = 100,
        max_history_pages: int = 20,
    ) -> NetflowSnapshot:
        if end_ts is None:
            trades = self.store.load_trades(symbol=symbol)
            if not trades:
                raise ValueError("end_ts is required when the trade store is empty")
            end_ts = trades[-1].timestamp

        window_seconds = timeframe_to_seconds(timeframe)
        window_start = end_ts - window_seconds + 1

        coverage = self.store.coverage_for_window(
            symbol=symbol,
            start_ts=window_start,
            end_ts=end_ts,
        )
        if not coverage.coverage_complete and self.backfill_source is not None:
            sync_trade_window(
                store=self.store,
                source=self.backfill_source,
                request=TradeWindowSyncRequest(
                    symbol=symbol,
                    start_ts=window_start,
                    end_ts=end_ts,
                    limit=limit,
                    max_history_pages=max_history_pages,
                ),
            )
            coverage = self.store.coverage_for_window(
                symbol=symbol,
                start_ts=window_start,
                end_ts=end_ts,
            )

        trades = self.store.load_trades(symbol=symbol, start_ts=window_start, end_ts=end_ts)
        inflow, outflow = _calculate_trade_flows(trades)
        coverage_start, coverage_end = _coverage_bounds(coverage, trades, window_start, end_ts)

        return NetflowSnapshot(
            snapshot_id=f"local-trade-store-netflow-{symbol}-{timeframe}-{end_ts}",
            timestamp=end_ts,
            symbol=symbol,
            venue=self.venue,
            as_of_timestamp=end_ts,
            timeframe=timeframe,
            inflow=inflow,
            outflow=outflow,
            netflow=inflow - outflow,
            window_start_timestamp=window_start,
            window_end_timestamp=end_ts,
            trade_records_in_window=len(trades),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            coverage_complete=coverage.coverage_complete,
            coverage_gap_reason=coverage.coverage_gap_reason,
        )


def sync_trade_window(
    *,
    store: TradeStore,
    source: PublicTradeSource,
    request: TradeWindowSyncRequest,
) -> TradeWindowSyncResult:
    coverage = store.coverage_for_window(
        symbol=request.symbol,
        start_ts=request.start_ts,
        end_ts=request.end_ts,
    )
    if coverage.coverage_complete:
        existing = store.load_trades(
            symbol=request.symbol,
            start_ts=request.start_ts,
            end_ts=request.end_ts,
        )
        return TradeWindowSyncResult(
            request=request,
            fetched_count=0,
            saved_count=len(store.load_trades(request.symbol)),
            trade_records_in_window=len(existing),
            coverage_start=coverage.coverage_start,
            coverage_end=coverage.coverage_end,
            coverage_complete=True,
            coverage_gap_reason=None,
        )

    collected = _dedupe_and_sort(_get_recent_trades(source, request.symbol, request.limit))
    recent_pages_fetched = 1
    history_pages_fetched = 0
    gap_reason: Optional[str] = None

    history_source = source if hasattr(source, "get_history_trades") else None
    while (
        collected
        and collected[0].timestamp > request.start_ts
        and history_source is not None
        and history_pages_fetched < request.max_history_pages
    ):
        previous_earliest = collected[0].timestamp
        history_page = _get_history_trades(
            history_source,
            request.symbol,
            request.limit,
            after_ts=previous_earliest,
        )
        history_pages_fetched += 1
        if not history_page:
            gap_reason = "history_exhausted_before_window_start"
            break
        collected = _dedupe_and_sort(collected + history_page)
        if collected[0].timestamp >= previous_earliest:
            gap_reason = "history_pagination_stalled"
            break

    fetched_count = len(collected)
    window_trades = [
        trade for trade in collected if request.start_ts <= trade.timestamp <= request.end_ts
    ]
    coverage_start, coverage_end, coverage_complete, gap_reason = _infer_sync_coverage(
        collected=collected,
        request=request,
        history_available=history_source is not None,
        history_pages_fetched=history_pages_fetched,
        existing_gap_reason=gap_reason,
    )

    trades_to_save = collected if coverage_complete else window_trades
    saved_count = store.save_trades(
        symbol=request.symbol,
        trades=trades_to_save,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        source="public_trades",
    )

    refreshed = store.coverage_for_window(
        symbol=request.symbol,
        start_ts=request.start_ts,
        end_ts=request.end_ts,
    )

    return TradeWindowSyncResult(
        request=request,
        fetched_count=fetched_count,
        saved_count=saved_count,
        trade_records_in_window=len(window_trades),
        coverage_start=refreshed.coverage_start or coverage_start,
        coverage_end=refreshed.coverage_end or coverage_end,
        coverage_complete=refreshed.coverage_complete,
        coverage_gap_reason=refreshed.coverage_gap_reason or gap_reason,
        recent_pages_fetched=recent_pages_fetched,
        history_pages_fetched=history_pages_fetched,
    )


def get_netflow_from_trade_store(
    *,
    store: TradeStore,
    symbol: str,
    timeframe: str = "1m",
    end_ts: Optional[int] = None,
    backfill_source: Optional[PublicTradeSource] = None,
    venue: str = "local_trade_store",
    limit: int = 100,
    max_history_pages: int = 20,
) -> NetflowSnapshot:
    service = TradeStoreNetflowService(
        store=store,
        backfill_source=backfill_source,
        venue=venue,
    )
    return service.get_netflow(
        symbol=symbol,
        timeframe=timeframe,
        end_ts=end_ts,
        limit=limit,
        max_history_pages=max_history_pages,
    )


def _get_recent_trades(source: PublicTradeSource, symbol: str, limit: int) -> List[Trade]:
    return list(source.get_trades(symbol=symbol, limit=limit))


def _get_history_trades(
    source: PublicHistoricalTradeSource,
    symbol: str,
    limit: int,
    *,
    after_ts: int,
) -> List[Trade]:
    return list(source.get_history_trades(symbol=symbol, limit=limit, after_ts=after_ts))


def _infer_sync_coverage(
    *,
    collected: List[Trade],
    request: TradeWindowSyncRequest,
    history_available: bool,
    history_pages_fetched: int,
    existing_gap_reason: Optional[str],
) -> Tuple[Optional[int], Optional[int], bool, Optional[str]]:
    if not collected:
        return None, None, False, "public_trade_source_returned_no_trades"

    earliest = collected[0].timestamp
    latest = collected[-1].timestamp
    overlaps = earliest <= request.end_ts and latest >= request.start_ts
    if not overlaps:
        return None, None, False, "public_trade_source_did_not_overlap_window"

    coverage_complete = earliest <= request.start_ts and latest >= request.end_ts
    if coverage_complete:
        return request.start_ts, request.end_ts, True, None

    coverage_start = max(earliest, request.start_ts)
    coverage_end = min(latest, request.end_ts)
    gap_reason = existing_gap_reason
    if gap_reason is None and not history_available:
        gap_reason = "history_trades_not_available"
    if gap_reason is None and history_pages_fetched >= request.max_history_pages:
        gap_reason = "history_page_limit_reached"
    if gap_reason is None:
        gap_reason = "public_trade_source_partial_window"
    return coverage_start, coverage_end, False, gap_reason


def _calculate_trade_flows(trades: Iterable[Trade]) -> Tuple[float, float]:
    inflow = 0.0
    outflow = 0.0
    for trade in trades:
        notional = trade.price * trade.size
        if trade.side.lower() == "buy":
            inflow += notional
        elif trade.side.lower() == "sell":
            outflow += notional
        else:
            raise ValueError("trade side must be buy or sell")
    return inflow, outflow


def _coverage_bounds(
    coverage: TradeCoverageReport,
    trades: List[Trade],
    window_start: int,
    window_end: int,
) -> Tuple[Optional[int], Optional[int]]:
    if coverage.coverage_start is not None and coverage.coverage_end is not None:
        return coverage.coverage_start, coverage.coverage_end
    if not trades:
        return None, None
    return (
        max(min(trade.timestamp for trade in trades), window_start),
        min(max(trade.timestamp for trade in trades), window_end),
    )


def _dedupe_and_sort(trades: Iterable[Trade]) -> List[Trade]:
    unique = {}
    for trade in trades:
        if trade.trade_id:
            key = ("id", trade.trade_id)
        else:
            key = ("raw", trade.timestamp, trade.price, trade.size, trade.side.lower())
        unique[key] = trade
    return sorted(unique.values(), key=lambda trade: (trade.timestamp, trade.trade_id or ""))
