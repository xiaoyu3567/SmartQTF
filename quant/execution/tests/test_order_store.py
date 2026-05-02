import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.idempotency import IdempotencyRecord, fingerprint_order_request
from quant.execution.order_store import SQLiteOrderStore, sanitize_raw_exchange_response
from quant.execution.state_machine import ExecutionEvent, ExecutionState, ExecutionTransitionRecord
from quant.schemas.enums import OrderKind, OrderStatus, TimeInForce, TradeSide
from quant.schemas.execution import (
    BrokerOrderRequest,
    BrokerOrderResult,
    ExecutionFillEvent,
    OrderIntent,
    ReconciliationItem,
    ReconciliationReport,
)


def make_order_intent(client_order_id="client-1", quantity=2.0):
    return OrderIntent(
        order_intent_id=f"intent-{client_order_id}",
        decision_id="decision-1",
        client_order_id=client_order_id,
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.LIMIT,
        quantity=quantity,
        limit_price=100.0,
        time_in_force=TimeInForce.GTC,
        risk_approved=True,
        created_at=1710000000,
    )


def make_broker_request(client_order_id="client-1", quantity=2.0):
    return BrokerOrderRequest(
        client_order_id=client_order_id,
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.LIMIT,
        quantity=quantity,
        limit_price=100.0,
        time_in_force=TimeInForce.GTC,
    )


def make_fill(fill_index, fill_qty, fill_price, cumulative, remaining, status):
    return ExecutionFillEvent(
        fill_event_id=f"fill-{fill_index}",
        client_order_id="client-1",
        broker_order_id="broker-1",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        status=status,
        fill_qty=fill_qty,
        fill_price=fill_price,
        cumulative_filled_qty=cumulative,
        remaining_qty=remaining,
        fill_index=fill_index,
    )


def status_value(status):
    return status.value if hasattr(status, "value") else status


def test_sqlite_order_store_initializes_required_tables(tmp_path):
    store_path = tmp_path / "orders.sqlite"

    with SQLiteOrderStore(store_path):
        pass

    connection = sqlite3.connect(store_path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    assert {
        "orders",
        "order_events",
        "fills",
        "idempotency_keys",
        "reconciliation_runs",
    }.issubset(tables)


def test_order_store_reconstructs_partial_fill_lifecycle(tmp_path):
    with SQLiteOrderStore(tmp_path / "orders.sqlite") as store:
        intent = make_order_intent()
        store.record_order_intent(intent)
        store.record_transition(
            ExecutionTransitionRecord(
                sequence=1,
                event=ExecutionEvent.ORDER_VALIDATED,
                from_state=ExecutionState.CREATED,
                to_state=ExecutionState.VALIDATED,
                client_order_id="client-1",
            ),
            event_time=1710000001,
        )
        store.record_transition(
            ExecutionTransitionRecord(
                sequence=2,
                event=ExecutionEvent.ORDER_SUBMITTING,
                from_state=ExecutionState.VALIDATED,
                to_state=ExecutionState.SUBMITTING,
                client_order_id="client-1",
            ),
            event_time=1710000002,
        )
        store.record_broker_order_result(
            BrokerOrderResult(
                client_order_id="client-1",
                broker_order_id="broker-1",
                symbol="BTCUSDT",
                side=TradeSide.BUY,
                status=OrderStatus.ACCEPTED,
                requested_qty=2.0,
            ),
            event_time=1710000003,
        )

        store.record_fill(
            make_fill(
                fill_index=1,
                fill_qty=0.75,
                fill_price=100.0,
                cumulative=0.75,
                remaining=1.25,
                status=OrderStatus.PARTIAL,
            ),
            event_time=1710000004,
        )
        store.record_fill(
            make_fill(
                fill_index=2,
                fill_qty=1.25,
                fill_price=102.0,
                cumulative=2.0,
                remaining=0.0,
                status=OrderStatus.FILLED,
            ),
            event_time=1710000005,
        )

        order = store.get_order("client-1")
        reconstructed = store.replay_order("client-1")

    assert order is not None
    assert status_value(order.status) == OrderStatus.FILLED.value
    assert order.filled_qty == 2.0
    assert reconstructed.client_order_id == "client-1"
    assert status_value(reconstructed.replay_status) == OrderStatus.FILLED.value
    assert reconstructed.total_filled_qty == 2.0
    assert reconstructed.avg_fill_price == 101.25
    assert [fill.fill_index for fill in reconstructed.fills] == [1, 2]
    assert "SUBMITTING" in reconstructed.lifecycle_path
    assert len(reconstructed.events) >= 5


def test_order_store_redacts_raw_exchange_responses(tmp_path):
    raw_response = {
        "ordId": "broker-1",
        "apiKey": "okx-key-secret",
        "nested": {
            "passphrase": "okx-passphrase-secret",
            "message": "Authorization: Bearer okx-token-secret",
        },
    }

    with SQLiteOrderStore(tmp_path / "orders.sqlite") as store:
        store.record_order_intent(make_order_intent())
        store.record_broker_order_result(
            BrokerOrderResult(
                client_order_id="client-1",
                broker_order_id="broker-1",
                symbol="BTCUSDT",
                side=TradeSide.BUY,
                status=OrderStatus.ACCEPTED,
                requested_qty=2.0,
            ),
            raw_exchange_response=raw_response,
        )
        reconstructed = store.replay_order("client-1")

    payload_text = json.dumps(reconstructed.to_payload(), sort_keys=True)

    assert "broker-1" in payload_text
    assert "okx-key-secret" not in payload_text
    assert "okx-passphrase-secret" not in payload_text
    assert "okx-token-secret" not in payload_text
    assert "***REDACTED***" in payload_text
    assert sanitize_raw_exchange_response(raw_response)["apiKey"] == "***REDACTED***"


def test_order_store_persists_idempotency_and_reconciliation_records(tmp_path):
    request = make_broker_request()
    broker_result = BrokerOrderResult(
        client_order_id="client-1",
        broker_order_id="broker-1",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        status=OrderStatus.ACCEPTED,
        requested_qty=2.0,
    )
    idempotency_record = IdempotencyRecord(
        client_order_id="client-1",
        request_fingerprint=fingerprint_order_request(request),
        request_payload=request.to_payload(),
        status=OrderStatus.ACCEPTED.value,
        submit_intent_count=1,
        broker_order_id="broker-1",
        result_payload=broker_result.to_payload(),
        created_at=1710000000,
        updated_at=1710000001,
    )
    reconciliation_report = ReconciliationReport(
        broker_name="fixture-broker",
        checked_count=1,
        matched_count=0,
        drift_count=1,
        missing_local_count=0,
        missing_broker_count=0,
        items=[
            ReconciliationItem(
                client_order_id="client-1",
                action="update_local_from_broker",
                reason="broker_truth_differs",
                local_status=OrderStatus.ACCEPTED,
                broker_status=OrderStatus.FILLED,
                broker_order_id="broker-1",
                requested_qty=2.0,
                local_filled_qty=0.0,
                broker_filled_qty=2.0,
            )
        ],
    )
    store_path = tmp_path / "orders.sqlite"

    with SQLiteOrderStore(store_path) as store:
        store.record_order_intent(make_order_intent())
        store.import_idempotency_record(idempotency_record)
        store.record_reconciliation_run(
            "recon-1",
            reconciliation_report,
            started_at=1710000100,
            finished_at=1710000110,
            raw_exchange_response={"secretKey": "raw-secret"},
        )

    with SQLiteOrderStore(store_path) as reloaded:
        key = reloaded.get_idempotency_key("client-1")
        run = reloaded.get_reconciliation_run("recon-1")
        reconstructed = reloaded.replay_order("client-1")

    assert key is not None
    assert key.submit_intent_count == 1
    assert key.result is not None
    assert status_value(key.result.status) == OrderStatus.ACCEPTED.value
    assert run is not None
    assert run.drift_count == 1
    assert run.raw_exchange_response["secretKey"] == "***REDACTED***"
    assert reconstructed.idempotency_key is not None
    assert [item.run_id for item in reconstructed.reconciliation_runs] == ["recon-1"]
