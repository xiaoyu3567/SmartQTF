import copy
import os
import threading
import time
from uuid import uuid4

from quant.optimization.promotion_review import StrategyPromotionReviewStore
from quant.orchestration.scanner import RuntimeScanScheduler


class SmartQTFWorkerRuntime:
    """Stateful worker facade around RuntimeScanScheduler.

    The runtime owns process-local lifecycle state and keeps the scan scheduler
    behind a small, idempotent control surface for web/API callers.
    """

    def __init__(
        self,
        *,
        config_path=None,
        scheduler=None,
        scheduler_factory=None,
        clock=None,
        poll_interval_seconds=1.0,
        max_events=200,
        worker_id=None,
        strategy_validation_artifact_dir=None,
        strategy_validation_latest_report_path=None,
        promotion_review_log_path=None,
        promotion_review_store=None,
    ):
        self.config_path = str(config_path) if config_path is not None else None
        self.scheduler_factory = scheduler_factory or RuntimeScanScheduler.from_config_file
        self.clock = clock or time.time
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.max_events = int(max_events)
        self.worker_id = worker_id or f"smartqtf-worker:{uuid4().hex}"
        self.strategy_validation_artifact_dir = strategy_validation_artifact_dir
        self.strategy_validation_latest_report_path = strategy_validation_latest_report_path
        if promotion_review_store is not None:
            self.promotion_review_store = promotion_review_store
        elif promotion_review_log_path is not None:
            self.promotion_review_store = StrategyPromotionReviewStore(promotion_review_log_path)
        else:
            self.promotion_review_store = StrategyPromotionReviewStore()

        self._scheduler = scheduler
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._loop_index = None

        self.running = False
        self.started_at = None
        self.stopped_at = None
        self.last_error = None
        self.latest_batch = None
        self.latest_report = None
        self.latest_run_cached_at = None
        self.latest_kline_snapshot = None
        self.events = []

        if scheduler is not None:
            self._record_event("runtime_initialized", "worker runtime initialized with injected scheduler")

    def start(self, *, config_path=None, index=None, poll_interval_seconds=None):
        with self._lock:
            if poll_interval_seconds is not None:
                self.poll_interval_seconds = float(poll_interval_seconds)
            if self.running:
                if config_path is not None and self.config_path not in {None, str(config_path)}:
                    raise ValueError("worker is already running with a different config_path")
                self._record_event("start_idempotent", "worker already running")
                return self.status()

            self._ensure_scheduler(config_path=config_path)
            self._loop_index = index
            self._stop_event.clear()
            self.running = True
            self.started_at = self._now()
            self.stopped_at = None
            self.last_error = None
            self._thread = threading.Thread(
                target=self._scan_loop,
                name="SmartQTFWorkerRuntime",
                daemon=True,
            )
            self._thread.start()
            self._record_event("start", "worker scan loop started", {"index": index})
            return self.status()

    def stop(self, *, timeout_seconds=5.0):
        with self._lock:
            if not self.running and not self._thread_alive():
                self._record_event("stop_idempotent", "worker already stopped")
                return self.status()
            thread = self._thread
            self._stop_event.set()

        if thread is not None:
            thread.join(timeout=float(timeout_seconds))

        with self._lock:
            if thread is not None and thread.is_alive():
                self.last_error = "worker scan loop did not stop before timeout"
                self._record_event("stop_timeout", self.last_error)
            else:
                self.running = False
                self.stopped_at = self._now()
                self._record_event("stop", "worker scan loop stopped")
            return self.status()

    def run_once(self, *, requested_at=None, index=None, batch_id=None, config_path=None):
        scheduler = self._ensure_scheduler(config_path=config_path)
        requested_at = self._timestamp(requested_at)
        with self._run_lock:
            try:
                batch = scheduler.run_once(
                    requested_at=requested_at,
                    index=index,
                    batch_id=batch_id,
                )
            except Exception as exc:
                self._set_error(exc, event_type="run_once_error")
                raise
        self._cache_batch(batch)
        self._record_event(
            "run_once",
            "worker completed one scan batch",
            self._batch_event_metadata(batch, requested_at=requested_at),
        )
        return self.batch_payload(batch)

    def status(self):
        with self._lock:
            now = self._now()
            safety = self.safety_summary()
            latest_report_pointer = self.latest_report_pointer(self.latest_batch, self.latest_report)
            return {
                "service": "smartqtf-worker",
                "worker_id": self.worker_id,
                "pid": os.getpid(),
                "running": self.running,
                "thread_alive": self._thread_alive(),
                "config_path": self.config_path,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "last_error": self.last_error,
                "latest_batch": self.batch_summary(self.latest_batch),
                "latest_report": self.report_summary(self.latest_report),
                "latest_report_pointer": latest_report_pointer,
                "scan_loop_health": self._scan_loop_health(now=now, safety=safety),
                "last_run_age_seconds": self._last_run_age_seconds(now),
                "latest_run_replay": self.latest_run_replay(self.latest_batch, self.latest_report),
                "failure_reason_timeline": self._failure_reason_timeline(limit=20),
                "safety": safety,
                "event_count": len(self.events),
            }

    def testflow(self):
        with self._lock:
            latest_batch = self.batch_payload(self.latest_batch) if self.latest_batch is not None else None
            latest_report = self.report_payload(self.latest_report) if self.latest_report is not None else None
            testflow_snapshot = self._build_testflow_snapshot(self.latest_batch, self.latest_report)
            return {
                "available": latest_batch is not None,
                "reason": None if latest_batch is not None else "run_once_required",
                "latest_batch": latest_batch,
                "latest_report": latest_report,
                "latest_run_replay": testflow_snapshot["latest_run_replay"],
                "stage_count": testflow_snapshot["stage_count"],
                "stages": testflow_snapshot["stages"],
                "multi_timeframe": testflow_snapshot["multi_timeframe"],
                "failure_reason_timeline": testflow_snapshot["failure_reason_timeline"],
                "recent_failed_stage": testflow_snapshot["recent_failed_stage"],
                "status": self.status(),
            }

    def kline(self, *, symbol=None, timeframe=None):
        with self._lock:
            snapshot = self._with_kline_freshness(self.latest_kline_snapshot, now=self._now())
            requested_channel = None
            reason = None
            available = snapshot is not None
            if snapshot is None:
                reason = "run_once_required" if self.latest_report is None else "multi_timeframe_snapshot_not_found"
                available = False
            elif symbol is not None and symbol != snapshot.get("symbol"):
                reason = "requested_symbol_not_found"
                available = False
            elif timeframe is not None:
                requested_channel = snapshot.get("batches", {}).get(timeframe)
                if requested_channel is None:
                    reason = "requested_timeframe_not_found"
                    available = False

            return {
                "available": available,
                "reason": reason,
                "symbol": symbol or (None if snapshot is None else snapshot.get("symbol")),
                "timeframe": timeframe,
                "execution_timeframe": None if snapshot is None else snapshot.get("execution_timeframe"),
                "context_timeframes": [] if snapshot is None else list(snapshot.get("context_timeframes", [])),
                "worker_cache": snapshot,
                "requested_channel": requested_channel,
                "latest_batch": self.batch_summary(self.latest_batch),
                "latest_report": self.report_summary(self.latest_report),
                "provider_rest_fallback": self._provider_rest_fallback_summary(),
                "quality_report": None if snapshot is None else snapshot.get("quality_report"),
            }

    def logs(self, *, limit=100, run_id=None, symbol=None, timeframe=None, event_type=None):
        limit = max(0, int(limit))
        with self._lock:
            matching_events = [
                event
                for event in self.events
                if self._event_matches(
                    event,
                    run_id=run_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=event_type,
                )
            ]
            events = matching_events[-limit:] if limit else []
            return {
                "events": list(events),
                "count": len(events),
                "total_matching_count": len(matching_events),
                "filters": {
                    "run_id": run_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "event_type": event_type,
                },
            }

    def optimization(self):
        payload = self.promotion_review_store.build_detail(
            artifact_dir=self.strategy_validation_artifact_dir,
            latest_report_path=self.strategy_validation_latest_report_path,
        )
        payload["latest_batch"] = self.batch_summary(self.latest_batch)
        payload["latest_pipeline_report"] = self.report_summary(self.latest_report)
        payload["worker_status"] = {
            "running": self.running,
            "last_error": self.last_error,
        }
        return payload

    def record_promotion_review(
        self,
        *,
        action,
        artifact_id,
        reviewer_note="",
        reviewer=None,
        dry_run=True,
        manual_review=False,
    ):
        result = self.promotion_review_store.record_decision(
            action=action,
            artifact_id=artifact_id,
            reviewer_note=reviewer_note,
            reviewer=reviewer,
            dry_run=dry_run,
            manual_review=manual_review,
            artifact_dir=self.strategy_validation_artifact_dir,
            latest_report_path=self.strategy_validation_latest_report_path,
        )
        self._record_event(
            "promotion_review",
            "manual strategy promotion review recorded in dry-run audit log",
            {
                "action": result["action"],
                "artifact_id": artifact_id,
                "review_id": result["record"]["review_id"],
                "dry_run": True,
                "live_deployment_triggered": False,
            },
        )
        return result

    def _scan_loop(self):
        self._record_event("loop_entered", "worker scan loop entered")
        try:
            while not self._stop_event.is_set():
                scheduler = self._ensure_scheduler()
                now = self._now()
                try:
                    with self._run_lock:
                        batch = scheduler.run_due(now=now, index=self._loop_index)
                    if batch is not None:
                        self._cache_batch(batch)
                        self._record_event(
                            "run_due",
                            "worker completed due scan batch",
                            self._batch_event_metadata(batch, requested_at=now),
                        )
                except Exception as exc:
                    self._set_error(exc, event_type="run_due_error")
                self._stop_event.wait(self._loop_wait_seconds(scheduler))
        finally:
            with self._lock:
                self.running = False
                self.stopped_at = self._now()
            self._record_event("loop_exited", "worker scan loop exited")

    def _ensure_scheduler(self, *, config_path=None):
        with self._lock:
            if config_path is not None:
                requested_path = str(config_path)
                if self.config_path != requested_path:
                    if self.running:
                        raise ValueError("cannot change config_path while worker is running")
                    self.config_path = requested_path
                    self._scheduler = None
            if self._scheduler is None:
                if not self.config_path:
                    raise ValueError("config_path is required before creating the worker scheduler")
                self._scheduler = self.scheduler_factory(self.config_path)
                self._record_event("scheduler_created", "worker scheduler created", {"config_path": self.config_path})
            return self._scheduler

    def _cache_batch(self, batch):
        with self._lock:
            self.latest_batch = batch
            reports = list(getattr(batch, "reports", []) or [])
            self.latest_report = reports[-1] if reports else None
            self.latest_run_cached_at = self._now()
            self.latest_kline_snapshot = self._build_kline_snapshot(self.latest_report)
            if self.latest_kline_snapshot is not None:
                self.latest_kline_snapshot["cached_at"] = self.latest_run_cached_at
            self.last_error = None

    def _set_error(self, exc, *, event_type):
        message = str(exc)
        with self._lock:
            self.last_error = message
        self._record_event(event_type, message)

    def _record_event(self, event_type, message, metadata=None):
        now = self._now()
        event = {
            "type": event_type,
            "message": message,
            "at": now,
            "created_at": now,
            "metadata": dict(metadata or {}),
        }
        with self._lock:
            self.events.append(event)
            if len(self.events) > self.max_events:
                self.events = self.events[-self.max_events :]

    def _now(self):
        return int(self.clock())

    def _timestamp(self, value):
        if value is None:
            return self._now()
        return int(value)

    def _thread_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _loop_wait_seconds(self, scheduler):
        configured_interval = self._scan_interval_seconds(scheduler)
        poll_interval = max(0.05, self.poll_interval_seconds)
        if configured_interval is None:
            return poll_interval
        return min(poll_interval, max(0.05, float(configured_interval)))

    @staticmethod
    def _scan_interval_seconds(scheduler):
        config = getattr(scheduler, "config", None)
        scan = getattr(config, "scan", None)
        return getattr(scan, "interval_seconds", None)

    def _provider_rest_fallback_summary(self):
        safety = self.safety_summary()
        return {
            "available": False,
            "reason": "disabled_by_default_fixture_mode",
            "source": "provider_rest_fallback",
            "external_exchange_access": safety["external_exchange_access"],
            "live_order_submission": safety["live_order_submission"],
        }

    def safety_summary(self):
        scheduler = self._scheduler
        config = getattr(scheduler, "config", None)
        environment = getattr(config, "environment", None)
        broker = getattr(config, "broker", None)
        external_exchange_access = bool(getattr(environment, "external_exchange_access", False))
        live_order_submission = bool(getattr(environment, "live_order_submission", False))
        dry_run = bool(getattr(environment, "dry_run", True))
        return {
            "source": self._value(getattr(config, "source", None)),
            "environment_tier": self._value(getattr(environment, "tier", None)),
            "external_exchange_access": external_exchange_access,
            "live_order_submission": live_order_submission,
            "dry_run": dry_run,
            "safe_mode": dry_run and not live_order_submission,
            "broker_mode": self._value(getattr(broker, "mode", None)),
        }

    @classmethod
    def batch_payload(cls, batch):
        return cls._payload(batch)

    @classmethod
    def report_payload(cls, report):
        return cls._payload(report)

    @classmethod
    def batch_summary(cls, batch):
        if batch is None:
            return None
        reports = list(getattr(batch, "reports", []) or [])
        return {
            "batch_id": getattr(batch, "batch_id", None),
            "source": cls._value(getattr(batch, "source", None)),
            "requested_at": getattr(batch, "requested_at", None),
            "success": getattr(batch, "success", None),
            "request_count": len(getattr(batch, "requests", []) or []),
            "report_count": len(reports),
            "error_count": len(getattr(batch, "errors", []) or []),
        }

    @classmethod
    def report_summary(cls, report):
        if report is None:
            return None
        context = getattr(report, "context", None)
        return {
            "run_id": getattr(context, "run_id", None),
            "symbol": getattr(context, "symbol", None),
            "timeframe": getattr(context, "timeframe", None),
            "source": cls._value(getattr(context, "source", None)),
            "started_at": getattr(context, "started_at", None),
            "finished_at": getattr(report, "finished_at", None),
            "success": getattr(report, "success", None),
            "stage_count": len(getattr(report, "stages", []) or []),
            "error_count": len(getattr(report, "errors", []) or []),
        }

    @classmethod
    def latest_report_pointer(cls, batch, report):
        if report is None:
            return None
        context = getattr(report, "context", None)
        return {
            "batch_id": getattr(batch, "batch_id", None),
            "run_id": getattr(context, "run_id", None),
            "symbol": getattr(context, "symbol", None),
            "timeframe": getattr(context, "timeframe", None),
            "source": cls._value(getattr(context, "source", None)),
            "started_at": getattr(context, "started_at", None),
            "finished_at": getattr(report, "finished_at", None),
            "success": getattr(report, "success", None),
        }

    @classmethod
    def latest_run_replay(cls, batch, report):
        if report is None:
            return None
        stages = []
        for stage in getattr(report, "stages", []) or []:
            stages.append(cls._stage_summary(cls._payload(stage)))
        return {
            "pointer": cls.latest_report_pointer(batch, report),
            "batch": cls.batch_summary(batch),
            "stage_summaries": stages,
            "failure_reason_timeline": cls._stage_failure_reason_timeline(report),
            "final_output_keys": sorted(dict(getattr(report, "final_output", {}) or {})),
            "error_count": len(getattr(report, "errors", []) or []),
        }

    @classmethod
    def _build_testflow_snapshot(cls, batch, report):
        stages = []
        if report is not None:
            for stage in getattr(report, "stages", []) or []:
                stage_payload = cls._payload(stage)
                stages.append(
                    {
                        "stage": stage_payload.get("stage"),
                        "status": cls._value(stage_payload.get("status")),
                        "started_at": stage_payload.get("started_at"),
                        "ended_at": stage_payload.get("ended_at"),
                        "input_payload": stage_payload.get("input_payload") or {},
                        "output_payload": stage_payload.get("output_payload") or {},
                        "skip_reason": stage_payload.get("skip_reason"),
                        "error": stage_payload.get("error"),
                        "metadata": stage_payload.get("metadata") or {},
                        "summary": cls._stage_summary(stage_payload),
                    }
                )
        failure_reason_timeline = cls._stage_failure_reason_timeline(report)
        return {
            "batch": cls.batch_summary(batch),
            "report": cls.report_summary(report),
            "latest_run_replay": cls.latest_run_replay(batch, report),
            "stage_count": len(stages),
            "stages": stages,
            "multi_timeframe": cls._build_multi_timeframe_testflow_view(report),
            "failure_reason_timeline": failure_reason_timeline,
            "recent_failed_stage": failure_reason_timeline[-1] if failure_reason_timeline else None,
        }

    @classmethod
    def _build_kline_snapshot(cls, report):
        if report is None:
            return None
        data_stage = cls._find_stage(report, "data")
        if data_stage is None:
            return None

        data_payload = dict(cls._payload(data_stage).get("output_payload") or {})
        context = getattr(report, "context", None)
        context_metadata = dict(getattr(context, "metadata", {}) or {})
        execution_timeframe = data_payload.get("execution_timeframe") or getattr(context, "timeframe", None)
        context_timeframes = list(data_payload.get("context_timeframes") or context_metadata.get("context_timeframes") or [])
        if not data_payload.get("multi_timeframe_enabled") and not context_timeframes:
            return None
        if not execution_timeframe:
            return None

        symbol = data_payload.get("symbol") or getattr(context, "symbol", None)
        quality_stage = cls._find_stage(report, "data_quality")
        quality_output = {}
        if quality_stage is not None:
            quality_output = dict(cls._payload(quality_stage).get("output_payload") or {})
        quality_report = quality_output.get("multi_timeframe_quality_report") or {}

        timeframe_reports = dict(quality_report.get("timeframe_reports") or {})
        alignment_issues = list(quality_report.get("alignment_issues") or [])
        alignment_reason_codes = cls._issue_codes(alignment_issues)
        fatal_timeframes = list(quality_report.get("fatal_timeframes") or [])
        counts = dict(data_payload.get("timeframe_bar_counts") or {})
        windows = dict(data_payload.get("timeframe_windows") or {})
        request_payload = dict(data_payload.get("request") or cls._payload(data_stage).get("input_payload") or {})
        bar_limits = cls._extract_bar_limits(data_payload, request_payload, execution_timeframe, context_timeframes)

        batches = {}
        for timeframe in [execution_timeframe] + context_timeframes:
            role = "execution" if timeframe == execution_timeframe else "context"
            timeframe_report = dict(timeframe_reports.get(timeframe) or {})
            batches[timeframe] = cls._build_kline_channel(
                timeframe=timeframe,
                role=role,
                bar_count=counts.get(timeframe),
                window=windows.get(timeframe) or {},
                quality_report=timeframe_report,
                fatal_timeframes=fatal_timeframes,
                alignment_reason_codes=alignment_reason_codes,
                bar_limit=bar_limits.get(timeframe),
            )

        coverage_status = "complete"
        if any(channel["coverage"]["status"] != "complete" for channel in batches.values()):
            coverage_status = "partial"
        if quality_report and quality_report.get("passed") is False:
            coverage_status = "partial"
        if alignment_reason_codes:
            coverage_status = "partial"

        run_id = getattr(context, "run_id", None)
        return {
            "snapshot_id": f"{run_id}:multi-timeframe-kline" if run_id else None,
            "source": "latest_pipeline_report",
            "symbol": symbol,
            "execution_timeframe": execution_timeframe,
            "context_timeframes": context_timeframes,
            "as_of_timestamp": quality_report.get("as_of_timestamp")
            or (data_payload.get("selected_bar") or {}).get("timestamp"),
            "selected_index": data_payload.get("selected_index"),
            "selected_bar": data_payload.get("selected_bar"),
            "bar_limits": bar_limits,
            "batches": batches,
            "coverage": {
                "status": coverage_status,
                "reason_codes": sorted(
                    set(
                        reason
                        for channel in batches.values()
                        for reason in channel["coverage"]["reason_codes"]
                    )
                    | set(alignment_reason_codes)
                ),
            },
            "quality_report": quality_report or None,
            "alignment": {
                "passed": not alignment_reason_codes,
                "issues": alignment_issues,
                "reason_codes": alignment_reason_codes,
                "fatal_timeframes": fatal_timeframes,
            },
            "channel_metadata": {
                "worker_cache": True,
                "latest_report_run_id": run_id,
                "quality_report_id": quality_output.get("quality_report_id"),
                "timeframe_quality_report_ids": quality_output.get("timeframe_quality_report_ids") or {},
                "provider_rest_fallback": "disabled_by_default_fixture_mode",
            },
        }

    @classmethod
    def _build_multi_timeframe_testflow_view(cls, report):
        snapshot = cls._build_kline_snapshot(report)
        if snapshot is None:
            return {"enabled": False, "reason": "multi_timeframe_snapshot_not_found"}

        strategy_conflict = cls._higher_timeframe_conflict(report)
        return {
            "enabled": True,
            "execution_timeframe": snapshot["execution_timeframe"],
            "context_timeframes": list(snapshot["context_timeframes"]),
            "timeframe_roles": {
                timeframe: channel["role"]
                for timeframe, channel in snapshot["batches"].items()
            },
            "bar_limits": dict(snapshot["bar_limits"]),
            "quality": snapshot["quality_report"],
            "alignment": snapshot["alignment"],
            "coverage": snapshot["coverage"],
            "kline_snapshot": snapshot,
            "higher_timeframe_conflict": strategy_conflict,
        }

    @classmethod
    def _build_kline_channel(
        cls,
        *,
        timeframe,
        role,
        bar_count,
        window,
        quality_report,
        fatal_timeframes,
        alignment_reason_codes,
        bar_limit,
    ):
        quality_issues = list(quality_report.get("issues") or [])
        reason_codes = cls._issue_codes(quality_issues)
        if timeframe in fatal_timeframes:
            reason_codes.append("timeframe_quality_failed")
        if bar_count in {None, 0}:
            reason_codes.append("missing_kline")
        if quality_report.get("has_incomplete_last_bar") or quality_report.get("included_incomplete_bar"):
            reason_codes.append("incomplete_last_bar")

        passed = quality_report.get("passed")
        if passed is None:
            passed = not reason_codes
        coverage_status = "complete" if passed and bar_count not in {None, 0} else "partial"
        reason_codes = sorted(set(str(reason) for reason in reason_codes if reason))

        return {
            "timeframe": timeframe,
            "role": role,
            "available": bar_count not in {None, 0},
            "source": "latest_report.data",
            "bar_count": bar_count,
            "bar_limit": bar_limit,
            "window": {
                "first_timestamp": window.get("first_timestamp") or quality_report.get("first_timestamp"),
                "last_timestamp": window.get("last_timestamp") or quality_report.get("last_timestamp"),
            },
            "quality": {
                "passed": bool(passed),
                "interval_seconds": quality_report.get("interval_seconds"),
                "checked_count": quality_report.get("checked_count"),
                "issues": quality_issues,
            },
            "freshness": {
                "reason": "pending_runtime_freshness_check",
                "last_timestamp": window.get("last_timestamp") or quality_report.get("last_timestamp"),
                "age_seconds": None,
                "interval_seconds": quality_report.get("interval_seconds"),
                "stale": None,
            },
            "coverage": {
                "status": coverage_status,
                "reason_codes": reason_codes,
                "alignment_reason_codes": list(alignment_reason_codes),
            },
        }

    @classmethod
    def _extract_bar_limits(cls, data_payload, request_payload, execution_timeframe, context_timeframes):
        explicit_limits = data_payload.get("bar_limits") or request_payload.get("bar_limits")
        if isinstance(explicit_limits, dict):
            return {
                timeframe: explicit_limits.get(timeframe)
                for timeframe in [execution_timeframe] + list(context_timeframes)
            }
        limit = request_payload.get("limit") or data_payload.get("bar_limit")
        return {timeframe: limit for timeframe in [execution_timeframe] + list(context_timeframes)}

    @classmethod
    def _higher_timeframe_conflict(cls, report):
        strategy_stage = cls._find_stage(report, "strategy")
        if strategy_stage is None:
            return {
                "downgraded": False,
                "conflict_timeframes": [],
                "reason_codes": [],
                "action": None,
            }
        output = dict(cls._payload(strategy_stage).get("output_payload") or {})
        filter_payload = dict(output.get("filter") or {})
        signal = dict(output.get("signal") or {})
        reason_codes = list(signal.get("reason_codes") or [])
        conflict_timeframes = list(filter_payload.get("conflict_timeframes") or [])
        action = signal.get("action") or signal.get("signal_type")
        downgraded = bool(
            conflict_timeframes
            or any("conflict" in str(reason) or "higher_timeframe" in str(reason) for reason in reason_codes)
            or action in {"wait", "no_trade", "WAIT", "NO_TRADE"}
        )
        return {
            "downgraded": downgraded,
            "conflict_timeframes": conflict_timeframes,
            "reason_codes": reason_codes,
            "action": action,
            "tradability": filter_payload.get("tradability"),
            "higher_timeframe_bias": filter_payload.get("higher_timeframe_bias"),
        }

    @classmethod
    def _stage_summary(cls, stage_payload):
        output = dict(stage_payload.get("output_payload") or {})
        input_payload = dict(stage_payload.get("input_payload") or {})
        started_at = stage_payload.get("started_at")
        ended_at = stage_payload.get("ended_at")
        duration_seconds = None
        if isinstance(started_at, (int, float)) and isinstance(ended_at, (int, float)):
            duration_seconds = max(0, ended_at - started_at)
        return {
            "stage": stage_payload.get("stage"),
            "status": cls._value(stage_payload.get("status")),
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": duration_seconds,
            "has_input": bool(input_payload),
            "has_output": bool(output),
            "input_keys": sorted(input_payload),
            "output_keys": sorted(output),
            "skip_reason": stage_payload.get("skip_reason"),
            "error": stage_payload.get("error"),
            "reason_codes": cls._stage_reason_codes(stage_payload),
        }

    @classmethod
    def _stage_reason_codes(cls, stage_payload):
        output = dict(stage_payload.get("output_payload") or {})
        codes = []
        for key in ("signal", "decision_result", "risk_decision", "multi_timeframe_regime"):
            payload = output.get(key)
            if isinstance(payload, dict):
                codes.extend(payload.get("reason_codes") or [])
        quality = output.get("multi_timeframe_quality_report")
        if isinstance(quality, dict):
            codes.extend(cls._issue_codes(quality.get("alignment_issues") or []))
        rejection = stage_payload.get("rejection")
        if isinstance(rejection, dict) and rejection.get("code"):
            codes.append(rejection["code"])
        return sorted(set(str(code) for code in codes if code))

    @classmethod
    def _stage_failure_reason_timeline(cls, report):
        if report is None:
            return []
        timeline = []
        for stage in getattr(report, "stages", []) or []:
            stage_payload = cls._payload(stage)
            status = cls._value(stage_payload.get("status"))
            reason_codes = cls._stage_reason_codes(stage_payload)
            failure_reason_codes = [
                reason for reason in reason_codes if cls._is_failure_reason_code(reason)
            ]
            rejection = stage_payload.get("rejection")
            rejection_code = None
            rejection_message = None
            if isinstance(rejection, dict):
                rejection_code = rejection.get("code")
                rejection_message = rejection.get("message")
            reason = (
                stage_payload.get("error")
                or stage_payload.get("skip_reason")
                or rejection_code
                or rejection_message
                or (failure_reason_codes[0] if failure_reason_codes else None)
            )
            if status == "succeeded" and not reason:
                continue
            context = getattr(report, "context", None)
            timeline.append(
                {
                    "stage": stage_payload.get("stage"),
                    "status": status,
                    "reason": reason or "stage_not_succeeded",
                    "reason_codes": failure_reason_codes if status == "succeeded" else reason_codes,
                    "at": stage_payload.get("ended_at"),
                    "run_id": getattr(context, "run_id", None),
                    "symbol": getattr(context, "symbol", None),
                    "timeframe": getattr(context, "timeframe", None),
                }
            )
        return timeline

    def _failure_reason_timeline(self, *, limit):
        timeline = self._stage_failure_reason_timeline(self.latest_report)
        for event in self.events:
            event_type = event.get("type")
            if not event_type:
                continue
            if not (str(event_type).endswith("_error") or event_type in {"stop_timeout"}):
                continue
            metadata = dict(event.get("metadata") or {})
            timeline.append(
                {
                    "stage": metadata.get("stage") or "worker",
                    "status": "error",
                    "reason": event.get("message"),
                    "reason_codes": [event_type],
                    "at": event.get("at") or event.get("created_at"),
                    "run_id": metadata.get("run_id"),
                    "symbol": metadata.get("symbol"),
                    "timeframe": metadata.get("timeframe"),
                }
            )
        return timeline[-limit:] if limit else []

    def _scan_loop_health(self, *, now, safety):
        thread_alive = self._thread_alive()
        if self.last_error:
            state = "error"
        elif self.running and thread_alive:
            state = "running"
        elif self.running and not thread_alive:
            state = "degraded"
        else:
            state = "stopped"
        return {
            "state": state,
            "running": self.running,
            "thread_alive": thread_alive,
            "poll_interval_seconds": self.poll_interval_seconds,
            "scan_interval_seconds": self._scan_interval_seconds(self._scheduler),
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "last_error": self.last_error,
            "latest_run_cached_at": self.latest_run_cached_at,
            "last_run_age_seconds": self._last_run_age_seconds(now),
            "latest_report_pointer": self.latest_report_pointer(self.latest_batch, self.latest_report),
            "failure_count": len(self._failure_reason_timeline(limit=self.max_events)),
            "safe_mode": bool(safety.get("safe_mode")),
        }

    def _last_run_age_seconds(self, now):
        if self.latest_run_cached_at is None:
            return None
        return max(0, int(now) - int(self.latest_run_cached_at))

    @classmethod
    def _with_kline_freshness(cls, snapshot, *, now):
        if snapshot is None:
            return None
        enriched = copy.deepcopy(snapshot)
        cached_at = enriched.get("cached_at")
        stale_timeframes = []
        for timeframe, channel in dict(enriched.get("batches") or {}).items():
            window = dict(channel.get("window") or {})
            quality = dict(channel.get("quality") or {})
            coverage = dict(channel.get("coverage") or {})
            reason_codes = set(str(reason) for reason in coverage.get("reason_codes") or [])
            last_timestamp = window.get("last_timestamp")
            interval_seconds = quality.get("interval_seconds")
            age_seconds = None
            stale = None
            reason = "fresh"
            if "missing_kline" in reason_codes or not channel.get("available"):
                reason = "missing_kline"
                stale = True
            elif not isinstance(last_timestamp, (int, float)):
                reason = "last_timestamp_missing"
                stale = True
            else:
                age_seconds = max(0, int(now) - int(last_timestamp))
                if isinstance(interval_seconds, (int, float)):
                    stale = age_seconds > max(int(interval_seconds) * 2, int(interval_seconds) + 60)
                    reason = "stale_kline" if stale else "fresh"
                else:
                    stale = False
            if stale:
                stale_timeframes.append(timeframe)
            channel["freshness"] = {
                "reason": reason,
                "last_timestamp": last_timestamp,
                "age_seconds": age_seconds,
                "interval_seconds": interval_seconds,
                "stale": stale,
                "cached_at": cached_at,
            }
        enriched["freshness"] = {
            "cached_at": cached_at,
            "as_of_timestamp": enriched.get("as_of_timestamp"),
            "last_run_age_seconds": None
            if cached_at is None
            else max(0, int(now) - int(cached_at)),
            "stale_timeframes": stale_timeframes,
            "status": "stale" if stale_timeframes else "fresh",
        }
        return enriched

    @classmethod
    def _batch_event_metadata(cls, batch, *, requested_at):
        reports = list(getattr(batch, "reports", []) or [])
        report = reports[-1] if reports else None
        pointer = cls.latest_report_pointer(batch, report)
        metadata = {
            "batch_id": getattr(batch, "batch_id", None),
            "requested_at": requested_at,
            "success": getattr(batch, "success", None),
        }
        if pointer:
            metadata.update(
                {
                    "run_id": pointer.get("run_id"),
                    "symbol": pointer.get("symbol"),
                    "timeframe": pointer.get("timeframe"),
                    "source": pointer.get("source"),
                }
            )
        return metadata

    @staticmethod
    def _event_matches(event, *, run_id=None, symbol=None, timeframe=None, event_type=None):
        metadata = dict(event.get("metadata") or {})
        if event_type is not None and event.get("type") != event_type:
            return False
        if run_id is not None and metadata.get("run_id") != run_id:
            return False
        if symbol is not None and metadata.get("symbol") != symbol:
            return False
        if timeframe is not None and metadata.get("timeframe") != timeframe:
            return False
        return True

    @staticmethod
    def _is_failure_reason_code(reason):
        lowered = str(reason).lower()
        fragments = (
            "avoid",
            "block",
            "conflict",
            "error",
            "fail",
            "invalid",
            "missing",
            "reject",
            "skip",
            "stale",
            "timeout",
        )
        return any(fragment in lowered for fragment in fragments)

    @classmethod
    def _find_stage(cls, report, stage_name):
        if report is None:
            return None
        for stage in getattr(report, "stages", []) or []:
            if getattr(stage, "stage", None) == stage_name:
                return stage
        return None

    @classmethod
    def _issue_codes(cls, issues):
        codes = []
        for issue in issues or []:
            if hasattr(issue, "code"):
                codes.append(cls._value(issue.code))
            elif isinstance(issue, dict):
                codes.append(cls._value(issue.get("code")))
        return [str(code) for code in codes if code]

    @staticmethod
    def _payload(value):
        if value is None:
            return None
        if hasattr(value, "to_payload"):
            return value.to_payload()
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "dict"):
            return value.dict()
        if isinstance(value, dict):
            return dict(value)
        return value

    @staticmethod
    def _value(value):
        if hasattr(value, "value"):
            return value.value
        return value
