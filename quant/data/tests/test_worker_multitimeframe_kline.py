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


class MultiTimeframeScheduler:
    def __init__(self, *, partial_context=False, conflict=False):
        self.partial_context = partial_context
        self.conflict = conflict
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

    def run_once(self, *, requested_at, index=None, batch_id=None):
        return _make_multitimeframe_batch(
            requested_at=requested_at,
            index=index,
            batch_id=batch_id or "mtf-worker-batch",
            partial_context=self.partial_context,
            conflict=self.conflict,
        )


def test_worker_kline_returns_multitimeframe_channels_from_latest_report():
    runtime = SmartQTFWorkerRuntime(
        scheduler=MultiTimeframeScheduler(),
        clock=StepClock(),
    )

    runtime.run_once(requested_at=1700000600, index=5, batch_id="mtf-batch-001")
    payload = runtime.kline(symbol="BTCUSDT", timeframe="5m")

    assert payload["available"] is True
    assert payload["reason"] is None
    assert payload["execution_timeframe"] == "5m"
    assert payload["context_timeframes"] == ["15m", "1h", "4h"]
    assert payload["provider_rest_fallback"] == {
        "available": False,
        "reason": "disabled_by_default_fixture_mode",
        "source": "provider_rest_fallback",
        "external_exchange_access": False,
        "live_order_submission": False,
    }

    snapshot = payload["worker_cache"]
    assert snapshot["symbol"] == "BTCUSDT"
    assert snapshot["source"] == "latest_pipeline_report"
    assert snapshot["coverage"]["status"] == "complete"
    assert sorted(snapshot["batches"]) == ["15m", "1h", "4h", "5m"]
    assert snapshot["batches"]["5m"]["role"] == "execution"
    assert snapshot["batches"]["15m"]["role"] == "context"
    assert snapshot["batches"]["1h"]["bar_limit"] == 120
    assert snapshot["batches"]["4h"]["quality"]["passed"] is True
    assert snapshot["batches"]["5m"]["freshness"]["reason"] == "fresh"
    assert snapshot["freshness"]["status"] == "fresh"
    assert payload["requested_channel"]["timeframe"] == "5m"
    assert payload["requested_channel"]["coverage"]["status"] == "complete"
    assert payload["requested_channel"]["freshness"]["stale"] is False


def test_worker_kline_marks_partial_context_coverage_with_reason_codes():
    runtime = SmartQTFWorkerRuntime(
        scheduler=MultiTimeframeScheduler(partial_context=True),
        clock=StepClock(),
    )

    runtime.run_once(requested_at=1700000600, index=5, batch_id="mtf-partial")
    payload = runtime.kline(symbol="BTCUSDT", timeframe="1h")
    snapshot = payload["worker_cache"]

    assert payload["available"] is True
    assert snapshot["coverage"]["status"] == "partial"
    assert "timeframe_quality_failed" in snapshot["coverage"]["reason_codes"]
    assert snapshot["batches"]["1h"]["coverage"]["status"] == "partial"
    assert snapshot["batches"]["1h"]["coverage"]["reason_codes"] == [
        "missing_kline",
        "timeframe_quality_failed",
    ]
    assert snapshot["batches"]["1h"]["freshness"]["reason"] == "missing_kline"
    assert snapshot["freshness"]["stale_timeframes"] == ["1h"]
    assert snapshot["quality_report"]["passed"] is False
    assert snapshot["alignment"]["fatal_timeframes"] == ["1h"]


def test_worker_testflow_exposes_stage_inputs_outputs_and_multitimeframe_view():
    runtime = SmartQTFWorkerRuntime(
        scheduler=MultiTimeframeScheduler(conflict=True),
        clock=StepClock(),
    )

    runtime.run_once(requested_at=1700000600, index=5, batch_id="mtf-conflict")
    testflow = runtime.testflow()

    assert testflow["available"] is True
    assert testflow["stage_count"] == 5
    assert testflow["latest_run_replay"]["pointer"]["run_id"] == "mtf-conflict:BTCUSDT:5m"
    stages = {stage["stage"]: stage for stage in testflow["stages"]}
    assert stages["data"]["input_payload"]["execution_timeframe"] == "5m"
    assert stages["data"]["output_payload"]["context_timeframes"] == ["15m", "1h", "4h"]
    assert stages["data_quality"]["output_payload"]["multi_timeframe_quality_report"]["passed"] is True
    assert stages["feature"]["summary"]["input_keys"] == [
        "context_timeframes",
        "execution_timeframe",
        "multi_timeframe_quality_report_id",
        "timeframe_quality_report_ids",
    ]
    assert stages["strategy"]["output_payload"]["filter"]["conflict_timeframes"] == ["1h"]

    mtf = testflow["multi_timeframe"]
    assert mtf["enabled"] is True
    assert mtf["timeframe_roles"] == {
        "5m": "execution",
        "15m": "context",
        "1h": "context",
        "4h": "context",
    }
    assert mtf["coverage"]["status"] == "complete"
    assert mtf["higher_timeframe_conflict"]["downgraded"] is True
    assert mtf["higher_timeframe_conflict"]["conflict_timeframes"] == ["1h"]
    assert "signal_blocked_by_higher_timeframe_conflict" in mtf["higher_timeframe_conflict"]["reason_codes"]
    assert testflow["recent_failed_stage"]["stage"] == "strategy"
    assert testflow["recent_failed_stage"]["reason"] == "signal_blocked_by_higher_timeframe_conflict"


def _make_multitimeframe_batch(*, requested_at, index, batch_id, partial_context, conflict):
    context = PipelineRunContext(
        run_id=f"{batch_id}:BTCUSDT:5m",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="5m",
        started_at=requested_at,
        metadata={
            "multi_timeframe_enabled": True,
            "context_timeframes": ["15m", "1h", "4h"],
        },
    )
    stages = [
        _data_stage(requested_at, index),
        _quality_stage(requested_at, partial_context=partial_context),
        _feature_stage(requested_at),
        _regime_stage(requested_at),
        _strategy_stage(requested_at, conflict=conflict),
    ]
    report = PipelineRunReport(
        context=context,
        stages=stages,
        finished_at=requested_at,
        success=True,
        final_output={"multi_timeframe": True},
    )
    return PipelineBatchRunReport(
        batch_id=batch_id,
        source=PayloadSource.PAPER,
        requested_at=requested_at,
        requests=[
            PipelineSymbolRunRequest(
                symbol="BTCUSDT",
                timeframe="5m",
                index=index,
                metadata={"multi_timeframe_enabled": True},
            )
        ],
        reports=[report],
        success=True,
    )


def _data_stage(requested_at, index):
    request = {
        "symbol": "BTCUSDT",
        "venue": "fixture",
        "execution_timeframe": "5m",
        "context_timeframes": ["15m", "1h", "4h"],
        "limit": 120,
    }
    output = {
        "multi_timeframe_enabled": True,
        "request": request,
        "execution_timeframe": "5m",
        "context_timeframes": ["15m", "1h", "4h"],
        "selected_index": index,
        "selected_bar": {"timestamp": 1700001500, "close": 103.0, "is_complete": True},
        "timeframe_bar_counts": {"5m": 6, "15m": 6, "1h": 6, "4h": 6},
        "timeframe_windows": {
            "5m": {"first_timestamp": 1700000000, "last_timestamp": 1700001500},
            "15m": {"first_timestamp": 1699997000, "last_timestamp": 1700001500},
            "1h": {"first_timestamp": 1699983500, "last_timestamp": 1700001500},
            "4h": {"first_timestamp": 1699929500, "last_timestamp": 1700001500},
        },
    }
    return PipelineStageResult(
        stage="data",
        status=PipelineStageStatus.SUCCEEDED,
        started_at=requested_at,
        ended_at=requested_at,
        input_payload=request,
        output_payload=output,
    )


def _quality_stage(requested_at, *, partial_context):
    report = {
        "symbol": "BTCUSDT",
        "execution_timeframe": "5m",
        "as_of_timestamp": 1700001500,
        "timeframe_reports": {
            "5m": _timeframe_report("5m", 6),
            "15m": _timeframe_report("15m", 6),
            "1h": _timeframe_report(
                "1h",
                0 if partial_context else 6,
                issues=[{"code": "missing_kline", "message": "Context timeframe 1h contains no klines", "fatal": True}]
                if partial_context
                else [],
            ),
            "4h": _timeframe_report("4h", 6),
        },
        "alignment_issues": [
            {
                "code": "timeframe_quality_failed",
                "message": "Timeframe 1h failed single-timeframe quality validation",
                "fatal": True,
            }
        ]
        if partial_context
        else [],
        "fatal_timeframes": ["1h"] if partial_context else [],
        "passed": not partial_context,
    }
    return PipelineStageResult(
        stage="data_quality",
        status=PipelineStageStatus.SUCCEEDED,
        started_at=requested_at,
        ended_at=requested_at,
        input_payload={
            "execution_timeframe": "5m",
            "context_timeframes": ["15m", "1h", "4h"],
            "timeframe_bar_counts": {"5m": 6, "15m": 6, "1h": 0 if partial_context else 6, "4h": 6},
        },
        output_payload={
            "multi_timeframe_quality_report": report,
            "quality_report_id": "quality:mtf",
            "timeframe_quality_report_ids": {
                "5m": "quality:5m",
                "15m": "quality:15m",
                "1h": "quality:1h",
                "4h": "quality:4h",
            },
        },
    )


def _feature_stage(requested_at):
    return PipelineStageResult(
        stage="feature",
        status=PipelineStageStatus.SUCCEEDED,
        started_at=requested_at,
        ended_at=requested_at,
        input_payload={
            "multi_timeframe_quality_report_id": "quality:mtf",
            "execution_timeframe": "5m",
            "context_timeframes": ["15m", "1h", "4h"],
            "timeframe_quality_report_ids": {
                "5m": "quality:5m",
                "15m": "quality:15m",
                "1h": "quality:1h",
                "4h": "quality:4h",
            },
        },
        output_payload={
            "multi_timeframe_feature_snapshot": {
                "snapshot_id": "feature:mtf",
                "execution_timeframe": "5m",
                "context_timeframes": ["15m", "1h", "4h"],
                "alignment_features": {"all_contexts_available": {"value": True}},
            }
        },
    )


def _regime_stage(requested_at):
    return PipelineStageResult(
        stage="regime",
        status=PipelineStageStatus.SUCCEEDED,
        started_at=requested_at,
        ended_at=requested_at,
        input_payload={
            "multi_timeframe_feature_snapshot_id": "feature:mtf",
            "quality_report_id": "quality:mtf",
        },
        output_payload={
            "multi_timeframe_regime": {
                "snapshot_id": "regime:mtf",
                "higher_timeframe_bias": "bullish",
                "confirmation_timeframes": ["15m", "1h", "4h"],
                "conflict_timeframes": [],
                "tradability": "tradable",
                "reason_codes": ["multi_timeframe_confirmed"],
            }
        },
    )


def _strategy_stage(requested_at, *, conflict):
    return PipelineStageResult(
        stage="strategy",
        status=PipelineStageStatus.SUCCEEDED,
        started_at=requested_at,
        ended_at=requested_at,
        input_payload={
            "regime_id": "regime:BTCUSDT",
            "multi_timeframe_regime_snapshot_id": "regime:mtf",
            "raw_signal": {"action": "buy"},
        },
        output_payload={
            "route": {"route_id": "default"},
            "signal": {
                "signal_id": "signal:mtf",
                "action": "no_trade" if conflict else "wait",
                "reason_codes": ["signal_blocked_by_higher_timeframe_conflict"] if conflict else ["wait_for_pullback"],
            },
            "filter": {
                "enabled": True,
                "filter_id": "higher_timeframe_confirmation",
                "higher_timeframe_bias": "mixed" if conflict else "bullish",
                "confirmation_timeframes": ["15m", "1h", "4h"],
                "conflict_timeframes": ["1h"] if conflict else [],
                "tradability": "avoid" if conflict else "tradable",
            },
        },
    )


def _timeframe_report(timeframe, count, *, issues=None):
    return {
        "symbol": "BTCUSDT",
        "timeframe": timeframe,
        "interval_seconds": {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[timeframe],
        "checked_count": count,
        "issues": list(issues or []),
        "first_timestamp": None if count == 0 else 1700000000,
        "last_timestamp": None if count == 0 else 1700001500,
        "has_incomplete_last_bar": False,
        "included_incomplete_bar": False,
        "passed": not issues,
    }
