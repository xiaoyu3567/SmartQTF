import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from quant.execution.idempotency import IdempotencyRecord
from quant.execution.state_machine import ExecutionState, ExecutionTransitionRecord
from quant.schemas.enums import OrderStatus
from quant.schemas.execution import (
    BrokerOrderResult,
    ExecutionFillEvent,
    OrderIntent,
    OrderStoreEventRecord,
    OrderStoreFillRecord,
    OrderStoreIdempotencyKeyRecord,
    OrderStoreOrderRecord,
    OrderStoreReconciliationRunRecord,
    OrderStoreReconstruction,
    ReconciliationReport,
)


ORDER_STORE_SCHEMA_VERSION = "1.0"
REDACTED_VALUE = "***REDACTED***"
SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret|pass(word)?|passphrase|token|authorization|signature)",
    re.IGNORECASE,
)


class SQLiteOrderStore:
    """SQLite-backed order/event store for replayable execution state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteOrderStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def record_order_intent(
        self,
        intent: OrderIntent,
        *,
        status: OrderStatus = OrderStatus.CREATED,
        raw_exchange_response: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrderStoreOrderRecord:
        raw_response = sanitize_raw_exchange_response(raw_exchange_response)
        order_record = OrderStoreOrderRecord(
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            status=status,
            requested_qty=intent.quantity,
            filled_qty=0.0,
            order_intent=intent,
            raw_exchange_response=raw_response,
            created_at=intent.created_at,
            updated_at=intent.created_at,
            trace=intent.trace,
            metadata=metadata or {},
        )
        self.connection.execute(
            """
            INSERT INTO orders (
                client_order_id, broker_order_id, symbol, side, status, requested_qty,
                filled_qty, avg_fill_price, order_intent_payload, broker_result_payload,
                raw_exchange_response, created_at, updated_at, trace_payload, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                broker_order_id=COALESCE(excluded.broker_order_id, broker_order_id),
                symbol=excluded.symbol,
                side=excluded.side,
                status=excluded.status,
                requested_qty=excluded.requested_qty,
                filled_qty=excluded.filled_qty,
                avg_fill_price=excluded.avg_fill_price,
                order_intent_payload=excluded.order_intent_payload,
                raw_exchange_response=excluded.raw_exchange_response,
                updated_at=excluded.updated_at,
                trace_payload=excluded.trace_payload,
                metadata=excluded.metadata
            """,
            (
                order_record.client_order_id,
                order_record.broker_order_id,
                order_record.symbol,
                _value(order_record.side),
                _value(order_record.status),
                order_record.requested_qty,
                order_record.filled_qty,
                order_record.avg_fill_price,
                _json_dumps(intent.to_payload()),
                None,
                _json_dumps(raw_response),
                order_record.created_at,
                order_record.updated_at,
                _json_dumps(intent.trace.to_payload() if intent.trace else None),
                _json_dumps(order_record.metadata),
            ),
        )
        self.connection.commit()
        return self.get_order(intent.client_order_id) or order_record

    def record_broker_order_result(
        self,
        result: BrokerOrderResult,
        *,
        event_type: str = "broker_order_result",
        event_time: int | None = None,
        raw_exchange_response: Any | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrderStoreOrderRecord:
        raw_response = sanitize_raw_exchange_response(raw_exchange_response)
        existing = self.get_order(result.client_order_id)
        created_at = existing.created_at if existing else event_time
        updated_at = event_time if event_time is not None else created_at
        intent_payload = existing.order_intent.to_payload() if existing and existing.order_intent else None
        merged_raw = _merge_raw_response(existing.raw_exchange_response if existing else {}, raw_response)

        self.connection.execute(
            """
            INSERT INTO orders (
                client_order_id, broker_order_id, symbol, side, status, requested_qty,
                filled_qty, avg_fill_price, order_intent_payload, broker_result_payload,
                raw_exchange_response, created_at, updated_at, trace_payload, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                broker_order_id=excluded.broker_order_id,
                symbol=excluded.symbol,
                side=excluded.side,
                status=excluded.status,
                requested_qty=excluded.requested_qty,
                filled_qty=excluded.filled_qty,
                avg_fill_price=excluded.avg_fill_price,
                broker_result_payload=excluded.broker_result_payload,
                raw_exchange_response=excluded.raw_exchange_response,
                updated_at=excluded.updated_at,
                trace_payload=COALESCE(excluded.trace_payload, trace_payload),
                metadata=excluded.metadata
            """,
            (
                result.client_order_id,
                result.broker_order_id,
                result.symbol,
                _value(result.side),
                _value(result.status),
                result.requested_qty,
                result.filled_qty,
                result.avg_fill_price,
                _json_dumps(intent_payload),
                _json_dumps(result.to_payload()),
                _json_dumps(merged_raw),
                created_at,
                updated_at,
                _json_dumps(result.trace.to_payload() if result.trace else None),
                _json_dumps(metadata or (existing.metadata if existing else {})),
            ),
        )
        self._insert_event(
            client_order_id=result.client_order_id,
            event_id=f"{result.client_order_id}:{event_type}:{_value(result.status)}:{event_time or 0}",
            event_type=event_type,
            broker_order_id=result.broker_order_id,
            event_time=event_time,
            reason=reason,
            payload=result.to_payload(),
            raw_exchange_response=raw_response,
            trace_payload=result.trace.to_payload() if result.trace else None,
            metadata=metadata,
        )
        self.connection.commit()
        restored = self.get_order(result.client_order_id)
        if restored is None:
            raise RuntimeError("order result was not persisted")
        return restored

    def record_transition(
        self,
        transition: ExecutionTransitionRecord,
        *,
        raw_exchange_response: Any | None = None,
        event_time: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrderStoreEventRecord:
        if not transition.client_order_id:
            raise ValueError("execution transition requires client_order_id for order store replay")
        raw_response = sanitize_raw_exchange_response(raw_exchange_response)
        sequence = self._insert_event(
            client_order_id=transition.client_order_id,
            event_id=f"{transition.client_order_id}:transition:{transition.sequence}:{transition.event}",
            event_type=transition.event,
            from_state=transition.from_state,
            to_state=transition.to_state,
            broker_order_id=transition.broker_order_id,
            event_time=event_time,
            reason=transition.reason,
            payload=transition.to_payload(),
            raw_exchange_response=raw_response,
            metadata={**transition.metadata, **(metadata or {})},
        )
        mapped_status = _status_from_execution_state(transition.to_state)
        if mapped_status is not None:
            self.connection.execute(
                """
                UPDATE orders
                SET status=?, broker_order_id=COALESCE(?, broker_order_id), updated_at=COALESCE(?, updated_at)
                WHERE client_order_id=?
                """,
                (
                    _value(mapped_status),
                    transition.broker_order_id,
                    event_time,
                    transition.client_order_id,
                ),
            )
        self.connection.commit()
        return self._event_from_row(self._fetch_event(sequence))

    def record_fill(
        self,
        fill_event: ExecutionFillEvent,
        *,
        event_time: int | None = None,
        raw_exchange_response: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrderStoreFillRecord:
        raw_response = sanitize_raw_exchange_response(raw_exchange_response)
        fill_record = OrderStoreFillRecord(
            fill_event_id=fill_event.fill_event_id,
            client_order_id=fill_event.client_order_id,
            broker_order_id=fill_event.broker_order_id,
            symbol=fill_event.symbol,
            side=fill_event.side,
            status=fill_event.status,
            fill_qty=fill_event.fill_qty,
            fill_price=fill_event.fill_price,
            cumulative_filled_qty=fill_event.cumulative_filled_qty,
            remaining_qty=fill_event.remaining_qty,
            fill_index=fill_event.fill_index,
            event_time=event_time,
            fill_event=fill_event,
            raw_exchange_response=raw_response,
            trace=fill_event.trace,
            metadata=metadata or {},
        )
        self.connection.execute(
            """
            INSERT OR REPLACE INTO fills (
                fill_event_id, client_order_id, broker_order_id, symbol, side, status,
                fill_qty, fill_price, cumulative_filled_qty, remaining_qty, fill_index,
                event_time, payload, raw_exchange_response, trace_payload, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill_record.fill_event_id,
                fill_record.client_order_id,
                fill_record.broker_order_id,
                fill_record.symbol,
                _value(fill_record.side),
                _value(fill_record.status),
                fill_record.fill_qty,
                fill_record.fill_price,
                fill_record.cumulative_filled_qty,
                fill_record.remaining_qty,
                fill_record.fill_index,
                fill_record.event_time,
                _json_dumps(fill_event.to_payload()),
                _json_dumps(raw_response),
                _json_dumps(fill_event.trace.to_payload() if fill_event.trace else None),
                _json_dumps(fill_record.metadata),
            ),
        )
        self._insert_event(
            client_order_id=fill_event.client_order_id,
            event_id=f"{fill_event.client_order_id}:fill:{fill_event.fill_index}:{fill_event.fill_event_id}",
            event_type="fill",
            broker_order_id=fill_event.broker_order_id,
            event_time=event_time,
            payload=fill_event.to_payload(),
            raw_exchange_response=raw_response,
            trace_payload=fill_event.trace.to_payload() if fill_event.trace else None,
            metadata=metadata,
        )
        self._apply_fill_to_order(fill_record, event_time=event_time)
        self.connection.commit()
        return self._fill_from_row(self._fetch_fill(fill_event.fill_event_id))

    def import_idempotency_record(
        self,
        record: IdempotencyRecord,
    ) -> OrderStoreIdempotencyKeyRecord:
        result = record.result()
        self.connection.execute(
            """
            INSERT INTO idempotency_keys (
                client_order_id, request_fingerprint, request_payload, status,
                submit_intent_count, broker_order_id, result_payload, last_error,
                created_at, updated_at, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                request_fingerprint=excluded.request_fingerprint,
                request_payload=excluded.request_payload,
                status=excluded.status,
                submit_intent_count=excluded.submit_intent_count,
                broker_order_id=excluded.broker_order_id,
                result_payload=excluded.result_payload,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at,
                metadata=excluded.metadata
            """,
            (
                record.client_order_id,
                record.request_fingerprint,
                _json_dumps(record.request_payload),
                _value(record.status),
                record.submit_intent_count,
                record.broker_order_id,
                _json_dumps(result.to_payload() if result else None),
                record.last_error,
                record.created_at,
                record.updated_at,
                _json_dumps(record.metadata),
            ),
        )
        self.connection.commit()
        restored = self.get_idempotency_key(record.client_order_id)
        if restored is None:
            raise RuntimeError("idempotency record was not persisted")
        return restored

    def record_reconciliation_run(
        self,
        run_id: str,
        report: ReconciliationReport,
        *,
        started_at: int | None = None,
        finished_at: int | None = None,
        raw_exchange_response: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrderStoreReconciliationRunRecord:
        raw_response = sanitize_raw_exchange_response(raw_exchange_response)
        report_payload = sanitize_raw_exchange_response(report.to_payload())
        metadata_payload = sanitize_raw_exchange_response(metadata or {})
        self.connection.execute(
            """
            INSERT OR REPLACE INTO reconciliation_runs (
                run_id, broker_name, checked_count, matched_count, drift_count,
                missing_local_count, missing_broker_count, started_at, finished_at,
                report_payload, raw_exchange_response, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                report.broker_name,
                report.checked_count,
                report.matched_count,
                report.drift_count,
                report.missing_local_count,
                report.missing_broker_count,
                started_at,
                finished_at,
                _json_dumps(report_payload),
                _json_dumps(raw_response),
                _json_dumps(metadata_payload),
            ),
        )
        self.connection.commit()
        restored = self.get_reconciliation_run(run_id)
        if restored is None:
            raise RuntimeError("reconciliation run was not persisted")
        return restored

    def get_order(self, client_order_id: str) -> OrderStoreOrderRecord | None:
        row = self.connection.execute(
            "SELECT * FROM orders WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        if row is None:
            return None
        return self._order_from_row(row)

    def list_order_events(self, client_order_id: str) -> list[OrderStoreEventRecord]:
        rows = self.connection.execute(
            "SELECT * FROM order_events WHERE client_order_id=? ORDER BY sequence",
            (client_order_id,),
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_fills(self, client_order_id: str) -> list[OrderStoreFillRecord]:
        rows = self.connection.execute(
            "SELECT * FROM fills WHERE client_order_id=? ORDER BY fill_index, fill_event_id",
            (client_order_id,),
        ).fetchall()
        return [self._fill_from_row(row) for row in rows]

    def get_idempotency_key(self, client_order_id: str) -> OrderStoreIdempotencyKeyRecord | None:
        row = self.connection.execute(
            "SELECT * FROM idempotency_keys WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        if row is None:
            return None
        return self._idempotency_from_row(row)

    def get_reconciliation_run(self, run_id: str) -> OrderStoreReconciliationRunRecord | None:
        row = self.connection.execute(
            "SELECT * FROM reconciliation_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return self._reconciliation_from_row(row)

    def list_reconciliation_runs_for_order(
        self,
        client_order_id: str,
    ) -> list[OrderStoreReconciliationRunRecord]:
        rows = self.connection.execute(
            "SELECT * FROM reconciliation_runs ORDER BY started_at, run_id"
        ).fetchall()
        runs = [self._reconciliation_from_row(row) for row in rows]
        return [
            run
            for run in runs
            if any(item.client_order_id == client_order_id for item in run.report.items)
        ]

    def list_order_results(self, client_order_ids: Iterable[str] | None = None) -> list[BrokerOrderResult]:
        if client_order_ids is None:
            rows = self.connection.execute("SELECT * FROM orders ORDER BY client_order_id").fetchall()
        else:
            ids = list(client_order_ids)
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = self.connection.execute(
                f"SELECT * FROM orders WHERE client_order_id IN ({placeholders}) ORDER BY client_order_id",
                ids,
            ).fetchall()
        return [self._order_result_from_row(row) for row in rows]

    def replay_order(self, client_order_id: str) -> OrderStoreReconstruction:
        order = self.get_order(client_order_id)
        if order is None:
            raise KeyError(client_order_id)
        events = self.list_order_events(client_order_id)
        fills = self.list_fills(client_order_id)
        idempotency_key = self.get_idempotency_key(client_order_id)
        reconciliation_runs = self.list_reconciliation_runs_for_order(client_order_id)
        total_filled_qty = fills[-1].cumulative_filled_qty if fills else order.filled_qty
        avg_fill_price = _weighted_avg_fill_price(fills) if fills else order.avg_fill_price
        replay_status = _replay_status(order, events, fills)
        return OrderStoreReconstruction(
            client_order_id=client_order_id,
            order=order,
            events=events,
            fills=fills,
            idempotency_key=idempotency_key,
            reconciliation_runs=reconciliation_runs,
            replay_status=replay_status,
            total_filled_qty=total_filled_qty,
            avg_fill_price=avg_fill_price,
            lifecycle_path=[event.to_state or event.event_type for event in events],
        )

    reconstruct_order = replay_order

    def _initialize(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS store_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            INSERT OR REPLACE INTO store_metadata (key, value)
            VALUES ('schema_version', ?)
            """,
            (ORDER_STORE_SCHEMA_VERSION,),
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                client_order_id TEXT PRIMARY KEY,
                broker_order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_qty REAL NOT NULL,
                filled_qty REAL NOT NULL DEFAULT 0,
                avg_fill_price REAL,
                order_intent_payload TEXT,
                broker_result_payload TEXT,
                raw_exchange_response TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                trace_payload TEXT,
                metadata TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS order_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT,
                broker_order_id TEXT,
                event_time INTEGER,
                reason TEXT,
                payload TEXT,
                raw_exchange_response TEXT,
                trace_payload TEXT,
                metadata TEXT
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_events_client_order_id ON order_events(client_order_id, sequence)"
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fills (
                fill_event_id TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL,
                broker_order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                fill_qty REAL NOT NULL,
                fill_price REAL NOT NULL,
                cumulative_filled_qty REAL NOT NULL,
                remaining_qty REAL NOT NULL,
                fill_index INTEGER NOT NULL,
                event_time INTEGER,
                payload TEXT NOT NULL,
                raw_exchange_response TEXT,
                trace_payload TEXT,
                metadata TEXT
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_fills_client_order_id ON fills(client_order_id, fill_index)"
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                client_order_id TEXT PRIMARY KEY,
                request_fingerprint TEXT NOT NULL,
                request_payload TEXT NOT NULL,
                status TEXT NOT NULL,
                submit_intent_count INTEGER NOT NULL DEFAULT 0,
                broker_order_id TEXT,
                result_payload TEXT,
                last_error TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                metadata TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reconciliation_runs (
                run_id TEXT PRIMARY KEY,
                broker_name TEXT NOT NULL,
                checked_count INTEGER NOT NULL,
                matched_count INTEGER NOT NULL,
                drift_count INTEGER NOT NULL,
                missing_local_count INTEGER NOT NULL,
                missing_broker_count INTEGER NOT NULL,
                started_at INTEGER,
                finished_at INTEGER,
                report_payload TEXT NOT NULL,
                raw_exchange_response TEXT,
                metadata TEXT
            )
            """
        )
        self.connection.commit()

    def _insert_event(
        self,
        *,
        client_order_id: str,
        event_id: str,
        event_type: str,
        from_state: str | None = None,
        to_state: str | None = None,
        broker_order_id: str | None = None,
        event_time: int | None = None,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        raw_exchange_response: dict[str, Any] | None = None,
        trace_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO order_events (
                event_id, client_order_id, event_type, from_state, to_state,
                broker_order_id, event_time, reason, payload, raw_exchange_response,
                trace_payload, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                client_order_id,
                event_type,
                from_state,
                to_state,
                broker_order_id,
                event_time,
                reason,
                _json_dumps(payload or {}),
                _json_dumps(raw_exchange_response or {}),
                _json_dumps(trace_payload),
                _json_dumps(metadata or {}),
            ),
        )
        return int(cursor.lastrowid)

    def _apply_fill_to_order(self, fill_record: OrderStoreFillRecord, *, event_time: int | None) -> None:
        existing = self.get_order(fill_record.client_order_id)
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO orders (
                    client_order_id, broker_order_id, symbol, side, status, requested_qty,
                    filled_qty, avg_fill_price, created_at, updated_at, trace_payload, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill_record.client_order_id,
                    fill_record.broker_order_id,
                    fill_record.symbol,
                    _value(fill_record.side),
                    _value(fill_record.status),
                    fill_record.cumulative_filled_qty + fill_record.remaining_qty,
                    fill_record.cumulative_filled_qty,
                    fill_record.fill_price,
                    event_time,
                    event_time,
                    _json_dumps(fill_record.trace.to_payload() if fill_record.trace else None),
                    _json_dumps(fill_record.metadata),
                ),
            )
            return

        fills = self.list_fills(fill_record.client_order_id)
        avg_fill_price = _weighted_avg_fill_price(fills)
        self.connection.execute(
            """
            UPDATE orders
            SET broker_order_id=COALESCE(?, broker_order_id),
                status=?,
                filled_qty=?,
                avg_fill_price=?,
                updated_at=COALESCE(?, updated_at)
            WHERE client_order_id=?
            """,
            (
                fill_record.broker_order_id,
                _value(fill_record.status),
                fill_record.cumulative_filled_qty,
                avg_fill_price,
                event_time,
                fill_record.client_order_id,
            ),
        )

    def _fetch_event(self, sequence: int) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM order_events WHERE sequence=?",
            (sequence,),
        ).fetchone()
        if row is None:
            raise KeyError(sequence)
        return row

    def _fetch_fill(self, fill_event_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM fills WHERE fill_event_id=?",
            (fill_event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(fill_event_id)
        return row

    def _order_from_row(self, row: sqlite3.Row) -> OrderStoreOrderRecord:
        order_intent_payload = _json_loads(row["order_intent_payload"])
        broker_result_payload = _json_loads(row["broker_result_payload"])
        return OrderStoreOrderRecord(
            client_order_id=row["client_order_id"],
            broker_order_id=row["broker_order_id"],
            symbol=row["symbol"],
            side=row["side"],
            status=row["status"],
            requested_qty=row["requested_qty"],
            filled_qty=row["filled_qty"],
            avg_fill_price=row["avg_fill_price"],
            order_intent=OrderIntent.from_payload(order_intent_payload) if order_intent_payload else None,
            broker_result=BrokerOrderResult.from_payload(broker_result_payload) if broker_result_payload else None,
            raw_exchange_response=_json_loads(row["raw_exchange_response"]) or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_json_loads(row["metadata"]) or {},
        )

    def _event_from_row(self, row: sqlite3.Row) -> OrderStoreEventRecord:
        return OrderStoreEventRecord(
            sequence=row["sequence"],
            event_id=row["event_id"],
            client_order_id=row["client_order_id"],
            event_type=row["event_type"],
            from_state=row["from_state"],
            to_state=row["to_state"],
            broker_order_id=row["broker_order_id"],
            event_time=row["event_time"],
            reason=row["reason"],
            payload=_json_loads(row["payload"]) or {},
            raw_exchange_response=_json_loads(row["raw_exchange_response"]) or {},
            metadata=_json_loads(row["metadata"]) or {},
        )

    def _fill_from_row(self, row: sqlite3.Row) -> OrderStoreFillRecord:
        payload = _json_loads(row["payload"]) or {}
        fill_event = ExecutionFillEvent.from_payload(payload)
        return OrderStoreFillRecord(
            fill_event_id=row["fill_event_id"],
            client_order_id=row["client_order_id"],
            broker_order_id=row["broker_order_id"],
            symbol=row["symbol"],
            side=row["side"],
            status=row["status"],
            fill_qty=row["fill_qty"],
            fill_price=row["fill_price"],
            cumulative_filled_qty=row["cumulative_filled_qty"],
            remaining_qty=row["remaining_qty"],
            fill_index=row["fill_index"],
            event_time=row["event_time"],
            fill_event=fill_event,
            raw_exchange_response=_json_loads(row["raw_exchange_response"]) or {},
            metadata=_json_loads(row["metadata"]) or {},
        )

    def _idempotency_from_row(self, row: sqlite3.Row) -> OrderStoreIdempotencyKeyRecord:
        result_payload = _json_loads(row["result_payload"])
        return OrderStoreIdempotencyKeyRecord(
            client_order_id=row["client_order_id"],
            request_fingerprint=row["request_fingerprint"],
            request_payload=_json_loads(row["request_payload"]) or {},
            status=row["status"],
            submit_intent_count=row["submit_intent_count"],
            broker_order_id=row["broker_order_id"],
            result=BrokerOrderResult.from_payload(result_payload) if result_payload else None,
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_json_loads(row["metadata"]) or {},
        )

    def _reconciliation_from_row(self, row: sqlite3.Row) -> OrderStoreReconciliationRunRecord:
        report_payload = _json_loads(row["report_payload"]) or {}
        return OrderStoreReconciliationRunRecord(
            run_id=row["run_id"],
            broker_name=row["broker_name"],
            checked_count=row["checked_count"],
            matched_count=row["matched_count"],
            drift_count=row["drift_count"],
            missing_local_count=row["missing_local_count"],
            missing_broker_count=row["missing_broker_count"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            report=ReconciliationReport.from_payload(report_payload),
            raw_exchange_response=_json_loads(row["raw_exchange_response"]) or {},
            metadata=_json_loads(row["metadata"]) or {},
        )

    def _order_result_from_row(self, row: sqlite3.Row) -> BrokerOrderResult:
        broker_result_payload = _json_loads(row["broker_result_payload"])
        if broker_result_payload:
            return BrokerOrderResult.from_payload(broker_result_payload)
        return BrokerOrderResult(
            client_order_id=row["client_order_id"],
            broker_order_id=row["broker_order_id"],
            symbol=row["symbol"],
            side=row["side"],
            status=row["status"],
            requested_qty=row["requested_qty"],
            filled_qty=row["filled_qty"],
            avg_fill_price=row["avg_fill_price"],
        )


def sanitize_raw_exchange_response(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    sanitized = _sanitize(value)
    if isinstance(sanitized, dict):
        return sanitized
    return {"value": sanitized}


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    if key is not None and SECRET_KEY_PATTERN.search(key):
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and SECRET_KEY_PATTERN.search(value):
        return REDACTED_VALUE
    return value


def _merge_raw_response(existing: dict[str, Any], new_response: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return dict(new_response)
    if not new_response:
        return dict(existing)
    return {"previous": existing, "latest": new_response}


def _status_from_execution_state(state: str | None) -> OrderStatus | None:
    if state is None:
        return None
    mapping = {
        ExecutionState.CREATED: OrderStatus.CREATED,
        ExecutionState.VALIDATED: OrderStatus.PENDING,
        ExecutionState.SUBMITTING: OrderStatus.PENDING,
        ExecutionState.SUBMITTED: OrderStatus.ACCEPTED,
        ExecutionState.ORDER_PENDING: OrderStatus.PENDING,
        ExecutionState.PARTIALLY_FILLED: OrderStatus.PARTIAL,
        ExecutionState.PARTIAL: OrderStatus.PARTIAL,
        ExecutionState.FILLED: OrderStatus.FILLED,
        ExecutionState.POSITION_OPEN: OrderStatus.FILLED,
        ExecutionState.CANCELLED: OrderStatus.CANCELLED,
        ExecutionState.REJECTED: OrderStatus.REJECTED,
        ExecutionState.TIMEOUT: OrderStatus.UNKNOWN,
        ExecutionState.RECOVERY: OrderStatus.UNKNOWN,
        ExecutionState.RETRYING: OrderStatus.UNKNOWN,
        ExecutionState.ERROR: OrderStatus.UNKNOWN,
    }
    return mapping.get(state)


def _replay_status(
    order: OrderStoreOrderRecord,
    events: list[OrderStoreEventRecord],
    fills: list[OrderStoreFillRecord],
) -> OrderStatus:
    if fills:
        last_fill_status = fills[-1].status
        return OrderStatus(last_fill_status)
    for event in reversed(events):
        mapped = _status_from_execution_state(event.to_state)
        if mapped is not None:
            return mapped
    return OrderStatus(order.status)


def _weighted_avg_fill_price(fills: list[OrderStoreFillRecord]) -> float | None:
    total_qty = sum(fill.fill_qty for fill in fills)
    if total_qty <= 0.0:
        return None
    notional = sum(fill.fill_qty * fill.fill_price for fill in fills)
    return notional / total_qty


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _json_loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
