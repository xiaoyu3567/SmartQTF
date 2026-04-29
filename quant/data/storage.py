from abc import ABC, abstractmethod
import json
from pathlib import Path
from typing import Iterable, List, Optional

from quant.data.schemas.market import Kline


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
