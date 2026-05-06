import importlib
import importlib.util
import json

import pytest

from quant.data.tests.test_worker_multitimeframe_kline import MultiTimeframeScheduler
from quant.orchestration.tests.test_worker_runtime import StubScheduler
from quant.orchestration.worker_runtime import SmartQTFWorkerRuntime
from quant.optimization.tests.test_strategy_validation_artifact_validation_script import (
    make_artifact,
    write_artifact,
)
from scripts import validate_strategy_validation_artifacts as validate_artifacts


def test_worker_api_module_imports_without_starting_server():
    module = importlib.import_module("scripts.smartqtf_worker")

    assert hasattr(module, "create_app")
    assert hasattr(module, "runtime")


def test_worker_api_dependency_contract_is_explicit_when_fastapi_is_missing():
    module = importlib.import_module("scripts.smartqtf_worker")

    if module.FastAPI is None:
        try:
            module.create_app(SmartQTFWorkerRuntime(scheduler=StubScheduler()))
        except RuntimeError as exc:
            assert "FastAPI is required" in str(exc)
        else:
            raise AssertionError("expected missing FastAPI contract error")


def test_worker_api_server_dependency_contract_is_explicit_when_server_deps_are_missing():
    module = importlib.import_module("scripts.smartqtf_worker")
    missing_deps = [dep for dep in ("fastapi", "uvicorn") if importlib.util.find_spec(dep) is None]
    if not missing_deps:
        pytest.skip("FastAPI and uvicorn are installed; route tests cover API contract without serving")

    with pytest.raises(SystemExit) as exc:
        module.main(["--host", "127.0.0.1", "--port", "8765"])

    message = str(exc.value)
    if "uvicorn" in missing_deps:
        assert "uvicorn is required" in message
    else:
        assert "FastAPI is required" in message


def test_worker_api_routes_with_fastapi_testclient_when_dependency_exists():
    module = importlib.import_module("scripts.smartqtf_worker")
    if module.FastAPI is None:
        pytest.skip("FastAPI is not installed in this environment")

    from fastapi.testclient import TestClient

    worker = SmartQTFWorkerRuntime(scheduler=StubScheduler(), poll_interval_seconds=0.05)
    app = module.create_app(worker)
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    started = client.post("/start", json={"index": 2, "poll_interval_seconds": 0.05})
    assert started.status_code == 200
    assert started.json()["running"] is True

    stopped = client.post("/stop")
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False

    run_once = client.post("/run-once", json={"requested_at": 1700000600, "index": 4, "batch_id": "api-batch"})
    assert run_once.status_code == 200
    assert run_once.json()["batch_id"] == "api-batch"

    status = client.get("/status")
    assert status.status_code == 200
    status_payload = status.json()
    assert status_payload["latest_batch"]["batch_id"] == "api-batch"
    assert status_payload["latest_report_pointer"]["run_id"] == "api-batch:BTCUSDT:5m"
    assert status_payload["scan_loop_health"]["state"] == "stopped"
    assert status_payload["scan_loop_health"]["safe_mode"] is True
    assert status_payload["latest_run_replay"]["pointer"]["batch_id"] == "api-batch"
    assert status_payload["safety"]["external_exchange_access"] is False
    assert status_payload["safety"]["live_order_submission"] is False
    assert status_payload["safety"]["dry_run"] is True
    assert status_payload["safety"]["safe_mode"] is True

    testflow = client.get("/testflow")
    assert testflow.status_code == 200
    assert testflow.json()["available"] is True
    assert testflow.json()["latest_run_replay"]["pointer"]["run_id"] == "api-batch:BTCUSDT:5m"

    logs = client.get("/logs", params={"limit": 20})
    assert logs.status_code == 200
    assert any(event["type"] == "run_once" for event in logs.json()["events"])

    filtered_logs = client.get("/logs", params={"limit": 20, "run_id": "api-batch:BTCUSDT:5m", "symbol": "BTCUSDT", "timeframe": "5m"})
    assert filtered_logs.status_code == 200
    assert filtered_logs.json()["filters"]["run_id"] == "api-batch:BTCUSDT:5m"
    assert [event["type"] for event in filtered_logs.json()["events"]] == ["run_once"]

    optimization = client.get("/optimization")
    assert optimization.status_code == 200
    assert optimization.json()["available"] is False
    assert optimization.json()["reason"] == "strategy_validation_artifacts_skipped"
    assert optimization.json()["status"] == "SKIPPED"
    assert optimization.json()["artifact_count"] == 0
    assert optimization.json()["safety"]["live_orders_sent"] is False


def test_worker_api_multitimeframe_kline_contract_with_fastapi_testclient():
    module = importlib.import_module("scripts.smartqtf_worker")
    if module.FastAPI is None:
        pytest.skip("FastAPI is not installed in this environment")

    from fastapi.testclient import TestClient

    worker = SmartQTFWorkerRuntime(scheduler=MultiTimeframeScheduler(conflict=True), clock=lambda: 1700001600)
    app = module.create_app(worker)
    client = TestClient(app)

    run_once = client.post("/run-once", json={"requested_at": 1700000600, "index": 5, "batch_id": "api-mtf"})
    assert run_once.status_code == 200
    assert run_once.json()["batch_id"] == "api-mtf"

    kline = client.get("/kline", params={"symbol": "BTCUSDT", "timeframe": "5m"})
    assert kline.status_code == 200
    kline_payload = kline.json()
    assert kline_payload["available"] is True
    assert kline_payload["reason"] is None
    assert kline_payload["execution_timeframe"] == "5m"
    assert kline_payload["context_timeframes"] == ["15m", "1h", "4h"]
    assert kline_payload["requested_channel"]["role"] == "execution"
    assert kline_payload["requested_channel"]["freshness"]["reason"] == "fresh"
    assert kline_payload["worker_cache"]["freshness"]["status"] == "fresh"
    assert kline_payload["worker_cache"]["coverage"]["status"] == "complete"
    assert sorted(kline_payload["worker_cache"]["batches"]) == ["15m", "1h", "4h", "5m"]
    assert kline_payload["provider_rest_fallback"]["available"] is False
    assert kline_payload["provider_rest_fallback"]["reason"] == "disabled_by_default_fixture_mode"
    assert kline_payload["provider_rest_fallback"]["external_exchange_access"] is False
    assert kline_payload["provider_rest_fallback"]["live_order_submission"] is False

    testflow = client.get("/testflow")
    assert testflow.status_code == 200
    testflow_payload = testflow.json()
    assert testflow_payload["available"] is True
    assert testflow_payload["stage_count"] == 5
    assert testflow_payload["multi_timeframe"]["enabled"] is True
    assert testflow_payload["multi_timeframe"]["execution_timeframe"] == "5m"
    assert testflow_payload["multi_timeframe"]["context_timeframes"] == ["15m", "1h", "4h"]
    assert testflow_payload["multi_timeframe"]["higher_timeframe_conflict"]["conflict_timeframes"] == ["1h"]
    assert testflow_payload["failure_reason_timeline"][0]["stage"] == "strategy"
    assert testflow_payload["failure_reason_timeline"][0]["reason"] == "signal_blocked_by_higher_timeframe_conflict"


def test_worker_api_records_promotion_review_as_dry_run_audit(tmp_path):
    module = importlib.import_module("scripts.smartqtf_worker")
    if module.FastAPI is None:
        pytest.skip("FastAPI is not installed in this environment")

    from fastapi.testclient import TestClient

    artifact_path = write_artifact(
        tmp_path / "artifacts" / "BTCUSDT" / "ma_crossover" / "candidate.json",
        make_artifact(),
    )
    validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_paths=[artifact_path],
        artifact_dir=None,
        output_path=tmp_path / "latest.json",
        timestamp=1710007300,
        require_gate_pass=True,
    )
    worker = SmartQTFWorkerRuntime(
        scheduler=StubScheduler(),
        strategy_validation_artifact_dir=tmp_path / "artifacts",
        strategy_validation_latest_report_path=tmp_path / "latest.json",
        promotion_review_log_path=tmp_path / "promotion-reviews.jsonl",
    )
    app = module.create_app(worker)
    client = TestClient(app)

    optimization = client.get("/optimization")
    assert optimization.status_code == 200
    assert optimization.json()["review_status"] == "READY_FOR_MANUAL_REVIEW"
    assert optimization.json()["review_candidates"][0]["approve_enabled"] is True

    missing_flag = client.post(
        "/optimization/review",
        json={
            "action": "approve",
            "artifact_id": "artifact-001",
            "reviewer_note": "missing manual flag",
            "dry_run": True,
            "manual_review": False,
        },
    )
    assert missing_flag.status_code == 400
    assert "dry_run=true and manual_review=true" in missing_flag.json()["detail"]

    review = client.post(
        "/optimization/review",
        json={
            "action": "approve",
            "artifact_id": "artifact-001",
            "reviewer_note": "reviewed in API contract",
            "reviewer": "pytest",
            "dry_run": True,
            "manual_review": True,
        },
    )
    assert review.status_code == 200
    review_payload = review.json()
    assert review_payload["record"]["manual_decision"] == "approve"
    assert review_payload["record"]["dry_run"] is True
    assert review_payload["record"]["live_deployment_triggered"] is False
    assert review_payload["safety"]["live_orders_sent"] is False
    assert review_payload["safety"]["broker_called"] is False

    persisted = [
        json.loads(line)
        for line in (tmp_path / "promotion-reviews.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert persisted[0]["reviewer_note"] == "reviewed in API contract"
