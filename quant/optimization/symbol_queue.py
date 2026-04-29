import json
from pathlib import Path
from typing import List, Optional

from quant.schemas import (
    StrategyPromotionDecision,
    StrategyValidationMetrics,
    StrategyVersion,
    SymbolOptimizationQueueRecord,
    TraceContext,
)


class SymbolOptimizationQueue:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def enqueue_candidate(
        self,
        symbol: str,
        candidate: StrategyVersion,
        queue_id: str,
        created_at: int,
        trace: Optional[TraceContext] = None,
    ) -> SymbolOptimizationQueueRecord:
        records = self.list_records(symbol)
        if any(item.queue_id == queue_id for item in records):
            raise ValueError(f"queue_id already exists for symbol {symbol}: {queue_id}")

        record = SymbolOptimizationQueueRecord(
            queue_id=queue_id,
            symbol=symbol,
            created_at=created_at,
            candidate=candidate,
            trace=trace,
        )
        records.append(record)
        self._write_records(symbol=symbol, records=records)
        return record

    def attach_validation(
        self,
        symbol: str,
        queue_id: str,
        metrics: StrategyValidationMetrics,
    ) -> SymbolOptimizationQueueRecord:
        record = self._update_record(
            symbol=symbol,
            queue_id=queue_id,
            validation_metrics=metrics,
        )
        return record

    def attach_decision(
        self,
        symbol: str,
        queue_id: str,
        decision: StrategyPromotionDecision,
    ) -> SymbolOptimizationQueueRecord:
        record = self._update_record(
            symbol=symbol,
            queue_id=queue_id,
            promotion_decision=decision,
        )
        return record

    def list_records(self, symbol: str) -> List[SymbolOptimizationQueueRecord]:
        path = self._path(symbol)
        if not path.exists():
            return []

        records = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                records.append(
                    SymbolOptimizationQueueRecord.from_payload(json.loads(line))
                )
        return records

    def get_record(
        self,
        symbol: str,
        queue_id: str,
    ) -> Optional[SymbolOptimizationQueueRecord]:
        for record in self.list_records(symbol):
            if record.queue_id == queue_id:
                return record
        return None

    def _update_record(self, symbol: str, queue_id: str, **updates):
        records = self.list_records(symbol)
        updated = None

        for index, record in enumerate(records):
            if record.queue_id != queue_id:
                continue
            payload = record.to_payload()
            for key, value in updates.items():
                if value is not None:
                    payload[key] = value.to_payload()
            updated = SymbolOptimizationQueueRecord.from_payload(payload)
            records[index] = updated
            break

        if updated is None:
            raise KeyError(f"unknown optimization queue record: {symbol}/{queue_id}")

        self._write_records(symbol=symbol, records=records)
        return updated

    def _write_records(
        self,
        symbol: str,
        records: List[SymbolOptimizationQueueRecord],
    ) -> None:
        path = self._path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.to_payload(), sort_keys=True) + "\n")

    def _path(self, symbol: str) -> Path:
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        return self.root / f"{safe_symbol}.jsonl"
