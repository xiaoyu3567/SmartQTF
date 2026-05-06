from quant.orchestration.worker_runtime import SmartQTFWorkerRuntime
from quant.schemas import (
    PayloadSource,
    PipelineBatchRunReport,
    PipelineRunContext,
    PipelineRunReport,
    PipelineStageResult,
    PipelineStageStatus,
    PipelineSymbolRunRequest,
)


class StepClock:
    def __init__(self, start=1700000000):
        self.value = start

    def __call__(self):
        self.value += 1
        return self.value


class StubScheduler:
    def __init__(self):
        self.config = type(
            "Config",
            (),
            {
                "source": PayloadSource.PAPER,
                "environment": type(
                    "Environment",
                    (),
                    {
                        "tier": "paper",
                        "external_exchange_access": False,
                        "live_order_submission": False,
                        "dry_run": True,
                    },
                )(),
                "broker": type("Broker", (), {"mode": PayloadSource.PAPER})(),
                "scan": type("Scan", (), {"interval_seconds": 600})(),
            },
        )()
        self.run_once_calls = []
        self.run_due_calls = []

    def run_once(self, *, requested_at, index=None, batch_id=None):
        self.run_once_calls.append({"requested_at": requested_at, "index": index, "batch_id": batch_id})
        return make_batch(requested_at=requested_at, index=index, batch_id=batch_id or "stub-batch")

    def run_due(self, *, now, index=None, batch_id=None):
        self.run_due_calls.append({"now": now, "index": index, "batch_id": batch_id})
        return None


def make_batch(*, requested_at, index=None, batch_id="stub-batch"):
    request = PipelineSymbolRunRequest(symbol="BTCUSDT", timeframe="5m", index=index)
    stage = PipelineStageResult(
        stage="data",
        status=PipelineStageStatus.SUCCEEDED,
        started_at=requested_at,
        ended_at=requested_at,
        output_payload={"kline_count": 5},
    )
    report = PipelineRunReport(
        context=PipelineRunContext(
            run_id=f"{batch_id}:BTCUSDT:5m",
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="5m",
            started_at=requested_at,
        ),
        stages=[stage],
        finished_at=requested_at,
        success=True,
        final_output={"execution_result": {"broker_called": False, "live_orders_sent": False}},
    )
    return PipelineBatchRunReport(
        batch_id=batch_id,
        source=PayloadSource.PAPER,
        requested_at=requested_at,
        requests=[request],
        reports=[report],
        success=True,
    )


def test_worker_run_once_caches_latest_batch_and_reports_safety():
    scheduler = StubScheduler()
    runtime = SmartQTFWorkerRuntime(scheduler=scheduler, clock=StepClock())

    payload = runtime.run_once(requested_at=1700000600, index=4, batch_id="worker-batch-001")
    status = runtime.status()

    assert payload["batch_id"] == "worker-batch-001"
    assert scheduler.run_once_calls == [{"requested_at": 1700000600, "index": 4, "batch_id": "worker-batch-001"}]
    assert status["latest_batch"]["batch_id"] == "worker-batch-001"
    assert status["latest_report"]["symbol"] == "BTCUSDT"
    assert status["latest_report_pointer"]["run_id"] == "worker-batch-001:BTCUSDT:5m"
    assert status["scan_loop_health"]["state"] == "stopped"
    assert status["scan_loop_health"]["safe_mode"] is True
    assert status["last_run_age_seconds"] is not None
    assert status["latest_run_replay"]["stage_summaries"][0]["stage"] == "data"
    assert status["safety"]["source"] == "paper"
    assert status["safety"]["external_exchange_access"] is False
    assert status["safety"]["live_order_submission"] is False
    assert status["safety"]["safe_mode"] is True
    assert status["safety"]["broker_mode"] == "paper"

    testflow = runtime.testflow()
    assert testflow["available"] is True
    assert testflow["latest_batch"]["batch_id"] == "worker-batch-001"
    assert testflow["latest_run_replay"]["pointer"]["batch_id"] == "worker-batch-001"


def test_worker_start_and_stop_are_idempotent():
    scheduler = StubScheduler()
    runtime = SmartQTFWorkerRuntime(
        scheduler=scheduler,
        clock=StepClock(),
        poll_interval_seconds=0.05,
    )

    started = runtime.start(index=2)
    started_again = runtime.start(index=2)
    stopped = runtime.stop()
    stopped_again = runtime.stop()

    assert started["running"] is True
    assert started_again["running"] is True
    assert stopped["running"] is False
    assert stopped_again["running"] is False
    assert len([event for event in runtime.logs(limit=20)["events"] if event["type"] == "start"]) == 1
    assert any(event["type"] == "start_idempotent" for event in runtime.logs(limit=20)["events"])
    assert any(event["type"] == "stop_idempotent" for event in runtime.logs(limit=20)["events"])


def test_worker_logs_can_filter_by_latest_run_metadata():
    runtime = SmartQTFWorkerRuntime(scheduler=StubScheduler(), clock=StepClock())

    runtime.run_once(requested_at=1700000600, index=4, batch_id="filter-batch")
    runtime._record_event("custom_event", "not tied to a run")

    filtered = runtime.logs(
        limit=20,
        run_id="filter-batch:BTCUSDT:5m",
        symbol="BTCUSDT",
        timeframe="5m",
    )

    assert filtered["filters"]["run_id"] == "filter-batch:BTCUSDT:5m"
    assert filtered["total_matching_count"] == 1
    assert filtered["events"][0]["type"] == "run_once"


def test_worker_requires_config_path_when_no_scheduler_is_injected():
    runtime = SmartQTFWorkerRuntime(clock=StepClock())

    try:
        runtime.run_once(requested_at=1700000600)
    except ValueError as exc:
        assert "config_path is required" in str(exc)
    else:
        raise AssertionError("expected config_path validation error")


def test_worker_kline_and_optimization_are_safe_pending_surfaces(tmp_path):
    runtime = SmartQTFWorkerRuntime(
        scheduler=StubScheduler(),
        clock=StepClock(),
        strategy_validation_artifact_dir=tmp_path / "missing-artifacts",
        strategy_validation_latest_report_path=tmp_path / "missing-latest.json",
    )

    kline = runtime.kline(symbol="BTCUSDT", timeframe="5m")
    optimization = runtime.optimization()

    assert kline["available"] is False
    assert kline["reason"] == "run_once_required"
    runtime.run_once(requested_at=1700000600, index=4, batch_id="single-timeframe")
    kline_after_single_timeframe_run = runtime.kline(symbol="BTCUSDT", timeframe="5m")

    assert kline_after_single_timeframe_run["available"] is False
    assert kline_after_single_timeframe_run["reason"] == "multi_timeframe_snapshot_not_found"
    assert optimization["available"] is False
    assert optimization["reason"] == "strategy_validation_artifacts_skipped"
    assert optimization["status"] == "SKIPPED"
    assert optimization["review_status"] == "SKIPPED"
    assert optimization["artifact_count"] == 0
    assert optimization["failed_count"] == 0
    assert optimization["latest_report_found"] is False
    assert optimization["latest_report"]["status"] == "SKIPPED"
    assert optimization["reason_codes"] == ["missing_strategy_validation_artifacts"]
    assert optimization["safety"]["live_orders_sent"] is False
    assert optimization["safety"]["analytics_modified_live_state"] is False
