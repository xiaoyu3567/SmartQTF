import json
from pathlib import Path
from typing import Iterable, Type

from quant.schemas import (
    AIDecisionSuggestionLogRecord,
    DecisionLogRecord,
    FillLogRecord,
    OrderLogRecord,
    RiskDecisionLogRecord,
    SmartQTFModel,
)


RECORD_TYPES = {
    "ai_decision_suggestion": AIDecisionSuggestionLogRecord,
    "decision": DecisionLogRecord,
    "order": OrderLogRecord,
    "fill": FillLogRecord,
    "risk": RiskDecisionLogRecord,
}


class JsonlTradeLogger:
    def __init__(self, path):
        self.path = Path(path)

    def append(self, record: SmartQTFModel) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_payload(), sort_keys=True) + "\n")

    def read_all(self) -> list[SmartQTFModel]:
        if not self.path.exists():
            return []

        records = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = json.loads(line)
                records.append(self._from_payload(payload))
        return records

    def iter_records(self) -> Iterable[SmartQTFModel]:
        yield from self.read_all()

    def read_by_type(self, record_type: str) -> list[SmartQTFModel]:
        return [record for record in self.read_all() if record.record_type == record_type]

    def _from_payload(self, payload: dict) -> SmartQTFModel:
        record_type = payload.get("record_type")
        model: Type[SmartQTFModel] | None = RECORD_TYPES.get(record_type)
        if model is None:
            raise ValueError(f"unknown log record type: {record_type}")
        return model.from_payload(payload)
