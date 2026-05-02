import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from pydantic import Field

from quant.data.schemas.market import Kline, Trade
from quant.schemas.base import SmartQTFModel


class KlineStore(ABC):
    @abstractmethod
    def save_klines(self, symbol: str, timeframe: str, klines: Iterable[Kline]) -> int:
        pass

    @abstractmethod
    def load_klines(
        self,
        symbol: str,
        timeframe: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List[Kline]:
        pass


class JsonlKlineStore(KlineStore):
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def save_klines(self, symbol: str, timeframe: str, klines: Iterable[Kline]) -> int:
        normalized = self._dedupe_and_sort(klines)
        path = self._path(symbol=symbol, timeframe=timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as handle:
            for kline in normalized:
                handle.write(json.dumps(self._to_payload(kline), sort_keys=True) + "\n")

        return len(normalized)

    def load_klines(
        self,
        symbol: str,
        timeframe: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List[Kline]:
        path = self._path(symbol=symbol, timeframe=timeframe)
        if not path.exists():
            return []

        loaded = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                kline = Kline(**json.loads(line))
                if start_ts is not None and kline.timestamp < start_ts:
                    continue
                if end_ts is not None and kline.timestamp > end_ts:
                    continue
                loaded.append(kline)

        return self._dedupe_and_sort(loaded)

    def _path(self, symbol: str, timeframe: str) -> Path:
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        safe_timeframe = timeframe.replace("/", "_").replace(":", "_")
        return self.root / safe_symbol / f"{safe_timeframe}.jsonl"

    @staticmethod
    def _dedupe_and_sort(klines: Iterable[Kline]) -> List[Kline]:
        by_timestamp = {kline.timestamp: kline for kline in klines}
        return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]

    @staticmethod
    def _to_payload(kline: Kline) -> dict:
        if hasattr(kline, "model_dump"):
            return kline.model_dump()
        return kline.dict()


class TradeCoverageInterval(SmartQTFModel):
    symbol: str
    start_ts: int
    end_ts: int
    source: str = "unknown"

    def __init__(self, **data):
        super().__init__(**data)
        if self.start_ts < 0 or self.end_ts < 0:
            raise ValueError("trade coverage timestamps must be non-negative")
        if self.end_ts < self.start_ts:
            raise ValueError("coverage end_ts must be greater than or equal to start_ts")


class TradeCoverageReport(SmartQTFModel):
    symbol: str
    requested_start_ts: int
    requested_end_ts: int
    intervals: List[TradeCoverageInterval] = Field(default_factory=list)
    coverage_start: Optional[int] = None
    coverage_end: Optional[int] = None
    coverage_complete: bool = False
    coverage_gap_reason: Optional[str] = None


class TradeStoreMetadata(SmartQTFModel):
    symbol: str
    intervals: List[TradeCoverageInterval] = Field(default_factory=list)
    last_cursor_ts: Optional[int] = None


class TradeStore(ABC):
    @abstractmethod
    def save_trades(
        self,
        symbol: str,
        trades: Iterable[Trade],
        *,
        coverage_start: Optional[int] = None,
        coverage_end: Optional[int] = None,
        source: str = "unknown",
    ) -> int:
        pass

    @abstractmethod
    def load_trades(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List[Trade]:
        pass

    @abstractmethod
    def coverage_for_window(
        self,
        symbol: str,
        start_ts: int,
        end_ts: int,
    ) -> TradeCoverageReport:
        pass

    @abstractmethod
    def metadata(self, symbol: str) -> TradeStoreMetadata:
        pass


class JsonlTradeStore(TradeStore):
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def save_trades(
        self,
        symbol: str,
        trades: Iterable[Trade],
        *,
        coverage_start: Optional[int] = None,
        coverage_end: Optional[int] = None,
        source: str = "unknown",
    ) -> int:
        incoming = list(trades)
        existing = self.load_trades(symbol=symbol)
        normalized = self._dedupe_and_sort(existing + incoming)

        path = self._trades_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for trade in normalized:
                handle.write(json.dumps(self._to_payload(trade), sort_keys=True) + "\n")

        if coverage_start is None and incoming:
            coverage_start = min(trade.timestamp for trade in incoming)
        if coverage_end is None and incoming:
            coverage_end = max(trade.timestamp for trade in incoming)
        if coverage_start is not None and coverage_end is not None:
            self._save_metadata(
                symbol,
                self._metadata_with_interval(
                    symbol=symbol,
                    start_ts=coverage_start,
                    end_ts=coverage_end,
                    source=source,
                ),
            )

        return len(normalized)

    def load_trades(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List[Trade]:
        path = self._trades_path(symbol)
        if not path.exists():
            return []

        loaded = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                trade = Trade(**json.loads(line))
                if start_ts is not None and trade.timestamp < start_ts:
                    continue
                if end_ts is not None and trade.timestamp > end_ts:
                    continue
                loaded.append(trade)

        return self._dedupe_and_sort(loaded)

    def coverage_for_window(
        self,
        symbol: str,
        start_ts: int,
        end_ts: int,
    ) -> TradeCoverageReport:
        if end_ts < start_ts:
            raise ValueError("end_ts must be greater than or equal to start_ts")

        metadata = self.metadata(symbol)
        overlapping = [
            interval
            for interval in metadata.intervals
            if interval.end_ts >= start_ts and interval.start_ts <= end_ts
        ]
        if not overlapping:
            return TradeCoverageReport(
                symbol=symbol,
                requested_start_ts=start_ts,
                requested_end_ts=end_ts,
                intervals=[],
                coverage_complete=False,
                coverage_gap_reason="local_trade_store_no_coverage",
            )

        intervals = self._merge_intervals(overlapping, symbol=symbol)
        coverage_start = min(max(interval.start_ts, start_ts) for interval in intervals)
        coverage_end = max(min(interval.end_ts, end_ts) for interval in intervals)

        cursor = start_ts
        complete = False
        for interval in intervals:
            if interval.end_ts < cursor:
                continue
            if interval.start_ts > cursor:
                break
            if interval.end_ts >= end_ts:
                complete = True
                break
            cursor = interval.end_ts + 1

        return TradeCoverageReport(
            symbol=symbol,
            requested_start_ts=start_ts,
            requested_end_ts=end_ts,
            intervals=intervals,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            coverage_complete=complete,
            coverage_gap_reason=None if complete else "local_trade_store_window_incomplete",
        )

    def metadata(self, symbol: str) -> TradeStoreMetadata:
        path = self._metadata_path(symbol)
        if not path.exists():
            return TradeStoreMetadata(symbol=symbol)
        return TradeStoreMetadata(**json.loads(path.read_text(encoding="utf-8")))

    def _metadata_with_interval(
        self,
        *,
        symbol: str,
        start_ts: int,
        end_ts: int,
        source: str,
    ) -> TradeStoreMetadata:
        metadata = self.metadata(symbol)
        interval = TradeCoverageInterval(
            symbol=symbol,
            start_ts=start_ts,
            end_ts=end_ts,
            source=source,
        )
        intervals = self._merge_intervals(metadata.intervals + [interval], symbol=symbol)
        last_cursor_ts = max(
            [timestamp for timestamp in [metadata.last_cursor_ts, end_ts] if timestamp is not None],
            default=None,
        )
        return TradeStoreMetadata(
            symbol=symbol,
            intervals=intervals,
            last_cursor_ts=last_cursor_ts,
        )

    def _save_metadata(self, symbol: str, metadata: TradeStoreMetadata) -> None:
        path = self._metadata_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(metadata.to_payload(), sort_keys=True), encoding="utf-8")

    def _trades_path(self, symbol: str) -> Path:
        return self.root / self._safe_symbol(symbol) / "trades.jsonl"

    def _metadata_path(self, symbol: str) -> Path:
        return self.root / self._safe_symbol(symbol) / "coverage.json"

    @staticmethod
    def _safe_symbol(symbol: str) -> str:
        return symbol.replace("/", "_").replace(":", "_")

    @classmethod
    def _dedupe_and_sort(cls, trades: Iterable[Trade]) -> List[Trade]:
        unique: Dict[Tuple[object, ...], Trade] = {}
        for trade in trades:
            if trade.trade_id:
                key = ("id", trade.trade_id)
            else:
                key = (
                    "raw",
                    trade.timestamp,
                    trade.price,
                    trade.size,
                    trade.side.lower(),
                )
            unique[key] = trade
        return [unique[key] for key in sorted(unique, key=lambda item: cls._sort_key(unique[item]))]

    @staticmethod
    def _sort_key(trade: Trade) -> Tuple[object, ...]:
        return (
            trade.timestamp,
            trade.trade_id or "",
            trade.price,
            trade.size,
            trade.side.lower(),
        )

    @classmethod
    def _merge_intervals(
        cls,
        intervals: Iterable[TradeCoverageInterval],
        *,
        symbol: str,
    ) -> List[TradeCoverageInterval]:
        sorted_intervals = sorted(intervals, key=lambda interval: (interval.start_ts, interval.end_ts))
        merged: List[TradeCoverageInterval] = []
        for interval in sorted_intervals:
            if not merged or interval.start_ts > merged[-1].end_ts + 1:
                merged.append(
                    TradeCoverageInterval(
                        symbol=symbol,
                        start_ts=interval.start_ts,
                        end_ts=interval.end_ts,
                        source=interval.source,
                    )
                )
                continue

            previous = merged[-1]
            source = previous.source if previous.source == interval.source else "mixed"
            merged[-1] = TradeCoverageInterval(
                symbol=symbol,
                start_ts=previous.start_ts,
                end_ts=max(previous.end_ts, interval.end_ts),
                source=source,
            )
        return merged

    @staticmethod
    def _to_payload(trade: Trade) -> dict:
        if hasattr(trade, "model_dump"):
            return trade.model_dump()
        return trade.dict()
