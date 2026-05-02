import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quant.execution.broker import BrokerAdapter
from quant.schemas.enums import OrderStatus
from quant.schemas.execution import BrokerOrderRequest, BrokerOrderResult


IDEMPOTENCY_REGISTRY_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class IdempotentSubmitResult:
    client_order_id: str
    action: str
    reason: str
    result: BrokerOrderResult
    broker_place_called: bool
    broker_lookup_called: bool
    idempotent_replay: bool
    submit_intent_count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "action": self.action,
            "reason": self.reason,
            "result": self.result.to_payload(),
            "broker_place_called": self.broker_place_called,
            "broker_lookup_called": self.broker_lookup_called,
            "idempotent_replay": self.idempotent_replay,
            "submit_intent_count": self.submit_intent_count,
        }


@dataclass
class IdempotencyRecord:
    client_order_id: str
    request_fingerprint: str
    request_payload: dict[str, Any]
    status: str = OrderStatus.UNKNOWN.value
    submit_intent_count: int = 0
    broker_order_id: str | None = None
    result_payload: dict[str, Any] | None = None
    last_error: str | None = None
    created_at: int | None = None
    updated_at: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "request_fingerprint": self.request_fingerprint,
            "request_payload": dict(self.request_payload),
            "status": self.status,
            "submit_intent_count": self.submit_intent_count,
            "broker_order_id": self.broker_order_id,
            "result_payload": dict(self.result_payload) if self.result_payload else None,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "IdempotencyRecord":
        return cls(
            client_order_id=str(payload["client_order_id"]),
            request_fingerprint=str(payload["request_fingerprint"]),
            request_payload=dict(payload["request_payload"]),
            status=str(payload.get("status") or OrderStatus.UNKNOWN.value),
            submit_intent_count=int(payload.get("submit_intent_count") or 0),
            broker_order_id=payload.get("broker_order_id"),
            result_payload=dict(payload["result_payload"]) if payload.get("result_payload") else None,
            last_error=payload.get("last_error"),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
            metadata=dict(payload.get("metadata") or {}),
        )

    def result(self) -> BrokerOrderResult | None:
        if self.result_payload is None:
            return None
        return BrokerOrderResult.from_payload(self.result_payload)


class JsonIdempotencyRegistry:
    """Persistent client_order_id registry used before broker submission."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.records: dict[str, IdempotencyRecord] = {}
        self._load()

    def get(self, client_order_id: str) -> IdempotencyRecord | None:
        return self.records.get(client_order_id)

    def register_submit_intent(
        self,
        request: BrokerOrderRequest,
        *,
        now: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IdempotencyRecord:
        existing = self.records.get(request.client_order_id)
        if existing is not None:
            self.ensure_request_matches(request, existing)
            if metadata:
                existing.metadata.update(metadata)
                existing.updated_at = now
                self._save()
            return existing

        record = IdempotencyRecord(
            client_order_id=request.client_order_id,
            request_fingerprint=fingerprint_order_request(request),
            request_payload=stable_order_request_payload(request),
            submit_intent_count=1,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        self.records[record.client_order_id] = record
        self._save()
        return record

    def import_broker_result(
        self,
        request: BrokerOrderRequest,
        result: BrokerOrderResult,
        *,
        now: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IdempotencyRecord:
        existing = self.records.get(request.client_order_id)
        if existing is None:
            record = IdempotencyRecord(
                client_order_id=request.client_order_id,
                request_fingerprint=fingerprint_order_request(request),
                request_payload=stable_order_request_payload(request),
                created_at=now,
            )
            self.records[record.client_order_id] = record
        else:
            self.ensure_request_matches(request, existing)
            record = existing

        record.status = _status_value(result.status)
        record.broker_order_id = result.broker_order_id
        record.result_payload = result.to_payload()
        record.last_error = None
        record.updated_at = now
        if metadata:
            record.metadata.update(metadata)
        self._save()
        return record

    def mark_unknown(
        self,
        request: BrokerOrderRequest,
        *,
        error: str,
        now: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IdempotencyRecord:
        record = self.register_submit_intent(request, now=now, metadata=metadata)
        record.status = OrderStatus.UNKNOWN.value
        record.last_error = error
        record.updated_at = now
        record.result_payload = _unknown_result_from_request(request).to_payload()
        if metadata:
            record.metadata.update(metadata)
        self._save()
        return record

    def ensure_request_matches(
        self,
        request: BrokerOrderRequest,
        record: IdempotencyRecord | None = None,
    ) -> None:
        record = record or self.records.get(request.client_order_id)
        if record is None:
            return

        fingerprint = fingerprint_order_request(request)
        if fingerprint != record.request_fingerprint:
            raise ValueError(
                "client_order_id reuse with different order payload: "
                f"{request.client_order_id}"
            )

    def update_metadata(
        self,
        client_order_id: str,
        metadata: dict[str, Any],
        *,
        now: int | None = None,
    ) -> IdempotencyRecord:
        record = self.records.get(client_order_id)
        if record is None:
            raise KeyError(client_order_id)
        record.metadata.update(metadata)
        record.updated_at = now
        self._save()
        return record

    def _load(self) -> None:
        if not self.path.exists():
            return

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != IDEMPOTENCY_REGISTRY_SCHEMA_VERSION:
            raise ValueError("unsupported idempotency registry schema version")
        self.records = {
            client_order_id: IdempotencyRecord.from_payload(record_payload)
            for client_order_id, record_payload in payload.get("records", {}).items()
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": IDEMPOTENCY_REGISTRY_SCHEMA_VERSION,
            "records": {
                client_order_id: record.to_payload()
                for client_order_id, record in sorted(self.records.items())
            },
        }
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)


def submit_order_idempotently(
    broker: BrokerAdapter,
    request: BrokerOrderRequest,
    registry: JsonIdempotencyRegistry,
    *,
    now: int | None = None,
    check_broker_before_submit: bool = True,
    metadata: dict[str, Any] | None = None,
) -> IdempotentSubmitResult:
    """Submit an order once, then recover/replay by client_order_id on retries."""

    existing = registry.get(request.client_order_id)
    if existing is not None:
        registry.ensure_request_matches(request, existing)
        replayed = existing.result()
        if replayed is not None and _status_value(replayed.status) != OrderStatus.UNKNOWN.value:
            return IdempotentSubmitResult(
                client_order_id=request.client_order_id,
                action="replay_local_result",
                reason="local_idempotency_record_has_broker_result",
                result=replayed,
                broker_place_called=False,
                broker_lookup_called=False,
                idempotent_replay=True,
                submit_intent_count=existing.submit_intent_count,
            )

        broker_order, lookup_called, lookup_error = _lookup_broker_order(broker, request.client_order_id)
        if broker_order is not None:
            record = registry.import_broker_result(
                request,
                broker_order,
                now=now,
                metadata={"recovered_after": existing.status},
            )
            return IdempotentSubmitResult(
                client_order_id=request.client_order_id,
                action="recover_broker_result",
                reason="broker_order_found_for_existing_client_order_id",
                result=broker_order,
                broker_place_called=False,
                broker_lookup_called=lookup_called,
                idempotent_replay=True,
                submit_intent_count=record.submit_intent_count,
            )

        record = registry.mark_unknown(
            request,
            error=lookup_error or "broker_order_not_found_for_existing_client_order_id",
            now=now,
            metadata=metadata,
        )
        return IdempotentSubmitResult(
            client_order_id=request.client_order_id,
            action="hold_unknown_without_resubmit",
            reason="existing_submit_intent_requires_recovery_before_resubmit",
            result=_unknown_result_from_request(request),
            broker_place_called=False,
            broker_lookup_called=lookup_called,
            idempotent_replay=True,
            submit_intent_count=record.submit_intent_count,
        )

    if check_broker_before_submit:
        broker_order, lookup_called, lookup_error = _lookup_broker_order(broker, request.client_order_id)
        if broker_order is not None:
            record = registry.import_broker_result(
                request,
                broker_order,
                now=now,
                metadata={**dict(metadata or {}), "source": "broker_precheck"},
            )
            return IdempotentSubmitResult(
                client_order_id=request.client_order_id,
                action="import_existing_broker_order",
                reason="broker_order_found_before_local_submit",
                result=broker_order,
                broker_place_called=False,
                broker_lookup_called=lookup_called,
                idempotent_replay=True,
                submit_intent_count=record.submit_intent_count,
            )
        if lookup_error is not None:
            return IdempotentSubmitResult(
                client_order_id=request.client_order_id,
                action="block_submit_precheck_failed",
                reason="broker_precheck_failed_before_submit",
                result=_unknown_result_from_request(request),
                broker_place_called=False,
                broker_lookup_called=lookup_called,
                idempotent_replay=False,
                submit_intent_count=0,
            )

    record = registry.register_submit_intent(request, now=now, metadata=metadata)
    try:
        broker_result = broker.place_order(request)
    except Exception as exc:
        record = registry.mark_unknown(request, error=str(exc), now=now, metadata=metadata)
        return IdempotentSubmitResult(
            client_order_id=request.client_order_id,
            action="submit_result_unknown",
            reason="broker_place_order_raised_after_submit_intent",
            result=_unknown_result_from_request(request),
            broker_place_called=True,
            broker_lookup_called=check_broker_before_submit,
            idempotent_replay=False,
            submit_intent_count=record.submit_intent_count,
        )

    record = registry.import_broker_result(request, broker_result, now=now, metadata=metadata)
    return IdempotentSubmitResult(
        client_order_id=request.client_order_id,
        action="submitted",
        reason="broker_order_submitted_once",
        result=broker_result,
        broker_place_called=True,
        broker_lookup_called=check_broker_before_submit,
        idempotent_replay=False,
        submit_intent_count=record.submit_intent_count,
    )


def fingerprint_order_request(request: BrokerOrderRequest) -> str:
    payload = stable_order_request_payload(request)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_order_request_payload(request: BrokerOrderRequest) -> dict[str, Any]:
    payload = request.to_payload()
    return {
        "client_order_id": payload["client_order_id"],
        "symbol": payload["symbol"],
        "side": payload["side"],
        "order_type": payload["order_type"],
        "quantity": payload["quantity"],
        "limit_price": payload.get("limit_price"),
        "time_in_force": payload.get("time_in_force"),
        "reduce_only": payload.get("reduce_only", False),
    }


def _lookup_broker_order(
    broker: BrokerAdapter,
    client_order_id: str,
) -> tuple[BrokerOrderResult | None, bool, str | None]:
    try:
        return broker.get_order(client_order_id), True, None
    except KeyError:
        return None, True, None
    except Exception as exc:
        return None, True, str(exc)


def _unknown_result_from_request(request: BrokerOrderRequest) -> BrokerOrderResult:
    return BrokerOrderResult(
        client_order_id=request.client_order_id,
        symbol=request.symbol,
        side=request.side,
        status=OrderStatus.UNKNOWN,
        requested_qty=request.quantity,
    )


def _status_value(status: OrderStatus | str) -> str:
    return status.value if hasattr(status, "value") else str(status)
