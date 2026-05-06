import importlib
import json
import re
from pathlib import Path

import pytest

from adapters.exchange.binance import BinanceAdapterError
from quant.data.schemas.market import Kline, KlineBatch
from quant.optimization.tests.test_artifact_generation import (
    make_source_report,
    write_source_report,
)
from quant.orchestration.tests.test_worker_runtime import StubScheduler
from quant.orchestration.worker_runtime import SmartQTFWorkerRuntime
from scripts import generate_strategy_validation_artifacts as generate_artifacts
from scripts import run_expanded_public_btcusdt_validation_search as search
from scripts.fetch_public_btcusdt_klines_matrix import run_public_kline_matrix_fetch


SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"\b[A-Za-z0-9_]*(?:api[_-]?key|secret|passphrase|password)[A-Za-z0-9_]*="),
)
DANGEROUS_TRUE_FLAGS = {
    "analytics_modified_live_state",
    "broker_called",
    "contains_real_credentials",
    "exchange_order_submitted",
    "external_exchange_access",
    "live_deployment_triggered",
    "live_order_submission",
    "live_orders_sent",
    "real_credentials_read",
    "real_order_submitted",
}


class FakePagedPublicAdapter:
    def __init__(self, klines_by_timeframe, failures_by_timeframe=None):
        self.klines_by_timeframe = klines_by_timeframe
        self.failures_by_timeframe = failures_by_timeframe or {}

    def get_klines(self, symbol, timeframe, *, limit=100, start_ts=None, end_ts=None):
        if timeframe in self.failures_by_timeframe:
            raise self.failures_by_timeframe[timeframe]
        klines = [
            kline
            for kline in self.klines_by_timeframe.get(timeframe, [])
            if (start_ts is None or kline.timestamp >= start_ts)
            and (end_ts is None or kline.timestamp <= end_ts)
        ]
        return KlineBatch(
            symbol=symbol,
            timeframe=timeframe,
            venue="binance",
            klines=sorted(klines, key=lambda item: item.timestamp)[:limit],
        )


def test_h_qa_034_public_matrix_classifies_pass_partial_and_network_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    generated_at = 200000
    pass_adapter = FakePagedPublicAdapter(
        {
            timeframe: _complete_timeframe_klines(timeframe, generated_at, count=4)
            for timeframe in ("1m", "5m", "15m", "1h")
        }
    )

    pass_report = run_public_kline_matrix_fetch(
        exchange="binance",
        symbol="BTCUSDT",
        timeframes=["1m", "5m", "15m", "1h", "1d"],
        required_timeframes=["1m", "5m", "15m", "1h"],
        target_bars=4,
        page_limit=4,
        max_pages=1,
        output_dir=tmp_path / "public-pass",
        summary_output=tmp_path / "public-pass" / "matrix.json",
        timestamp=generated_at,
        adapter=pass_adapter,
    )

    assert pass_report["status"] == "PASS"
    assert pass_report["minimum_timeframes_passed"] is True
    assert set(pass_report["pass_timeframes"]) == {"1m", "5m", "15m", "1h"}
    assert pass_report["timeframes"]["1d"]["status"] == "SKIPPED"
    assert pass_report["timeframes"]["1d"]["bar_count"] == 0
    assert "insufficient_public_klines" in pass_report["timeframes"]["1d"]["reason_codes"]
    assert pass_report["safety_flags"]["public_market_data_only"] is True
    _assert_no_secret_like_values(pass_report)
    _assert_no_live_side_effect_flags(pass_report)

    partial_adapter = FakePagedPublicAdapter(
        {"1m": _complete_timeframe_klines("1m", generated_at, count=2)},
        failures_by_timeframe={"5m": BinanceAdapterError("DNS lookup failed")},
    )
    partial_report = run_public_kline_matrix_fetch(
        exchange="binance",
        symbol="BTCUSDT",
        timeframes=["1m", "5m"],
        required_timeframes=["1m", "5m"],
        target_bars=2,
        page_limit=2,
        max_pages=1,
        output_dir=tmp_path / "public-partial",
        summary_output=tmp_path / "public-partial" / "matrix.json",
        timestamp=generated_at,
        adapter=partial_adapter,
    )

    assert partial_report["status"] == "PARTIAL"
    assert partial_report["minimum_timeframes_passed"] is False
    assert partial_report["pass_timeframes"] == ["1m"]
    assert partial_report["skipped_timeframes"] == ["5m"]
    assert "required_timeframe_not_passed" in partial_report["reason_codes"]
    assert partial_report["timeframes"]["5m"]["reason_codes"] == [
        "public_market_data_unavailable"
    ]
    _assert_no_secret_like_values(partial_report)
    _assert_no_live_side_effect_flags(partial_report)


def test_h_qa_034_expanded_search_no_pass_keeps_review_and_artifacts_blocked(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    worker_module = importlib.import_module("scripts.smartqtf_worker")
    if worker_module.FastAPI is None:
        pytest.skip("FastAPI is not installed in this environment")

    matrix_path = _write_matrix(
        tmp_path / "public" / "matrix.json",
        pass_timeframes=("1m", "5m", "15m", "1h"),
        skipped_timeframes=("1d",),
    )
    output_path = tmp_path / "expanded-search-latest.json"
    latest_validator_output = tmp_path / "latest-validator.json"
    calls = []

    def fake_generation(**kwargs):
        calls.append(kwargs)
        rates = {
            "ma_crossover": 0.52,
            "donchian_breakout": 0.35,
            "rsi_mean_reversion": 0.12,
        }
        return _fake_aggregate_report(
            status="SKIPPED",
            reason_codes=[
                "missing_walk_forward_validation",
                "walk_forward_pass_rate_below_threshold",
            ],
            survival_rate=1.0,
            walk_forward_pass_rate=rates[str(kwargs["strategy_id"])],
            artifact_paths=[],
        )

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=output_path,
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "artifacts",
        latest_validator_output_path=latest_validator_output,
        timeframes=["1m", "5m", "15m", "1h", "1d"],
        strategy_ids=["ma_crossover", "donchian_breakout", "rsi_mean_reversion"],
        max_trials=36,
        top_k=6,
        phases=["coarse", "fine", "confirm"],
        train_bars_values=[80],
        test_bars_values=[20],
        step_bars_values=[20],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        monte_carlo_run_count=20,
        timestamp=1777827900,
    )

    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted == report
    assert report["status"] == "SKIPPED"
    assert report["pass_count"] == 0
    assert report["artifact_count"] == 0
    assert report["generated_artifact_count"] == 0
    assert report["artifact_paths"] == []
    assert report["h_opt_005_ready"] is False
    assert report["h_opt_010_ready"] is False
    assert latest_validator_output.exists() is False
    assert "no_expanded_public_btcusdt_candidate_passed" in report["reason_codes"]
    assert {item["timeframe"] for item in report["source_inputs"]} == {
        "1m",
        "5m",
        "15m",
        "1h",
    }
    assert report["skipped_timeframes"][0]["timeframe"] == "1d"
    assert {call["strategy_id"] for call in calls} >= {
        "ma_crossover",
        "donchian_breakout",
        "rsi_mean_reversion",
    }
    assert len(report["top_candidates"]) == 6
    assert report["best_candidate"]["metrics"]["walk_forward_pass_rate"] < 0.67
    assert report["walk_forward_threshold_gap_analysis"][
        "eligible_oos_mc_failure_count"
    ] >= 1
    assert report["walk_forward_rescue_plan"][
        "recommended_action"
    ] == "rescue_oos_mc_candidates_with_independent_walk_forward_windows"
    assert report["overfit_diagnostics"]["walk_forward_failure_analysis"][
        "passing_threshold_count"
    ] == 0
    assert report["promotion_feedback_contract"]["does_not_lower_gate_thresholds"] is True

    from fastapi.testclient import TestClient

    worker = SmartQTFWorkerRuntime(
        scheduler=StubScheduler(),
        strategy_validation_artifact_dir=tmp_path / "artifacts",
        strategy_validation_latest_report_path=latest_validator_output,
        promotion_review_log_path=tmp_path / "promotion-reviews.jsonl",
    )
    client = TestClient(worker_module.create_app(worker))
    optimization = client.get("/optimization").json()
    assert optimization["status"] == "SKIPPED"
    assert optimization["review_status"] == "SKIPPED"
    assert optimization["artifact_count"] == 0
    assert optimization["review_candidates"] == []
    assert optimization["safety"]["manual_review_dry_run_only"] is True
    assert optimization["safety"]["broker_called"] is False
    assert optimization["safety"]["live_orders_sent"] is False

    review_response = client.post(
        "/optimization/review",
        json={
            "action": "approve",
            "artifact_id": "missing-artifact",
            "reviewer_note": "approval must stay disabled while artifact_count is zero",
            "reviewer": "pytest",
            "dry_run": True,
            "manual_review": True,
        },
    )
    assert review_response.status_code == 400
    assert "unknown strategy validation artifact_id" in review_response.text

    for payload in (report, optimization):
        _assert_no_secret_like_values(payload)
        _assert_no_live_side_effect_flags(payload)


def test_h_qa_034_expanded_search_pass_path_reaches_worker_review_dry_run(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    worker_module = importlib.import_module("scripts.smartqtf_worker")
    if worker_module.FastAPI is None:
        pytest.skip("FastAPI is not installed in this environment")

    matrix_path = _write_matrix(
        tmp_path / "public" / "matrix.json",
        pass_timeframes=("1m",),
        skipped_timeframes=(),
    )
    artifact_dir = tmp_path / "artifacts"
    latest_validator_output = tmp_path / "latest-validator.json"

    def fake_generation(**kwargs):
        source_report = make_source_report().to_payload()
        source_report.update(
            {
                "report_id": f"h-qa-034-{kwargs['candidate_version']}",
                "source_report_id": f"h-qa-034-source-{kwargs['candidate_version']}",
                "strategy_id": kwargs["strategy_id"],
                "candidate_version": kwargs["candidate_version"],
                "symbol": kwargs["symbol"],
                "source_path": str(kwargs["source_paths"][0]),
            }
        )
        source_path = write_source_report(
            tmp_path
            / "source-reports"
            / kwargs["symbol"]
            / f"{kwargs['candidate_version']}.json",
            source_report,
        )
        generation_report = generate_artifacts.run_strategy_validation_artifact_generation(
            source_reports=[source_path],
            source_report_dirs=[],
            artifact_dir=kwargs["artifact_dir"],
            output_path=kwargs["artifact_generation_output_path"],
            validator_output_path=kwargs["validator_output_path"],
            timestamp=kwargs["timestamp"],
            require_gate_pass=True,
            min_walk_forward_windows=3,
            min_walk_forward_pass_rate=0.67,
            min_monte_carlo_survival_rate=0.8,
        )
        artifact_paths = [
            str(entry["artifact_path"]) for entry in generation_report["generated"]
        ]
        generation_report.update(
            {
                "results": _passing_validation_results(),
                "reason_codes": [],
                "aggregate_source_report_path": str(source_path),
                "source_report_paths": [str(source_path)],
                "artifact_paths": artifact_paths,
                "generated_artifact_count": len(artifact_paths),
                "validator_status": generation_report["validator_report"]["status"],
                "h_opt_005_ready": True,
                "h_opt_005_blockers": [],
                "safety_flags": _default_safety_flags(),
                "success": True,
            }
        )
        return generation_report

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-pass-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-report-inbox",
        artifact_dir=artifact_dir,
        latest_validator_output_path=latest_validator_output,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=1,
        top_k=3,
        stop_on_first_pass=True,
        phases=["confirm"],
        train_bars_values=[80],
        test_bars_values=[20],
        step_bars_values=[20],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        monte_carlo_run_count=20,
        timestamp=1777828000,
    )

    assert report["status"] == "PASS"
    assert report["pass_count"] == 1
    assert report["artifact_count"] == 1
    assert report["generated_artifact_count"] == 1
    assert report["validator_status"] == "PASS"
    assert report["latest_validation_report_path"] == str(latest_validator_output)
    assert report["h_opt_005_ready"] is True
    assert report["h_opt_010_ready"] is True
    assert report["source_report_paths"][0].startswith(str(tmp_path))
    assert "config/examples" not in report["source_report_paths"][0]
    latest_report = json.loads(latest_validator_output.read_text(encoding="utf-8"))
    assert latest_report["status"] == "PASS"
    assert latest_report["artifact_count"] == 1

    from fastapi.testclient import TestClient

    worker = SmartQTFWorkerRuntime(
        scheduler=StubScheduler(),
        strategy_validation_artifact_dir=artifact_dir,
        strategy_validation_latest_report_path=latest_validator_output,
        promotion_review_log_path=tmp_path / "promotion-reviews.jsonl",
    )
    client = TestClient(worker_module.create_app(worker))
    optimization = client.get("/optimization").json()
    assert optimization["status"] == "PASS"
    assert optimization["review_status"] == "READY_FOR_MANUAL_REVIEW"
    assert optimization["artifact_count"] == 1
    assert optimization["evidence_summary"]["has_out_of_sample"] is True
    assert optimization["evidence_summary"]["walk_forward_count"] == 3
    assert optimization["evidence_summary"]["has_monte_carlo"] is True
    assert optimization["safety"]["manual_review_dry_run_only"] is True
    assert optimization["safety"]["broker_called"] is False
    assert optimization["safety"]["live_orders_sent"] is False
    candidate = optimization["review_candidates"][0]
    assert candidate["approve_enabled"] is True

    review_response = client.post(
        "/optimization/review",
        json={
            "action": "approve",
            "artifact_id": candidate["artifact_id"],
            "reviewer_note": "H-QA-034 fixture pass path approved in dry-run only",
            "reviewer": "pytest",
            "dry_run": True,
            "manual_review": True,
        },
    )
    assert review_response.status_code == 200
    review = review_response.json()
    assert review["record"]["manual_decision"] == "approve"
    assert review["record"]["dry_run"] is True
    assert review["record"]["live_deployment_triggered"] is False
    assert review["optimization"]["review_status"] == "APPROVED_DRY_RUN"
    assert review["safety"]["broker_called"] is False
    assert review["safety"]["live_orders_sent"] is False
    assert review["safety"]["network_used"] is False

    for payload in (report, latest_report, optimization, review):
        _assert_no_secret_like_values(payload)
        _assert_no_live_side_effect_flags(payload)


def _complete_timeframe_klines(timeframe: str, generated_at: int, *, count: int):
    seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }[timeframe]
    current_open_ts = (generated_at // seconds) * seconds
    latest_ts = max(0, current_open_ts - seconds)
    first_ts = latest_ts - seconds * (count - 1)
    return [
        _kline(first_ts + index * seconds, close=100.0 + index)
        for index in range(count)
        if first_ts + index * seconds >= 0
    ]


def _write_matrix(path, *, pass_timeframes, skipped_timeframes):
    timeframe_payloads = {}
    for timeframe in pass_timeframes:
        timeframe_file = _write_json(
            path.parent / f"btcusdt-{timeframe}.json",
            {
                "status": "PASS",
                "exchange": "binance",
                "symbol": "BTCUSDT",
                "timeframe": timeframe,
                "klines": _synthetic_klines(),
            },
        )
        timeframe_payloads[timeframe] = {
            "status": "PASS",
            "bar_count": 180,
            "first_timestamp": 1700000000,
            "last_timestamp": 1700010740,
            "output_path": str(timeframe_file),
            "sha256": f"declared-{timeframe}",
            "reason_codes": [],
            "quality_report": {"passed": True},
        }
    for timeframe in skipped_timeframes:
        timeframe_payloads[timeframe] = {
            "status": "SKIPPED",
            "bar_count": 0,
            "output_path": str(path.parent / f"missing-{timeframe}.json"),
            "reason_codes": ["insufficient_public_klines"],
            "quality_report": {"passed": True},
        }
    return _write_json(
        path,
        {
            "status": "PASS",
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "pass_timeframes": list(pass_timeframes),
            "skipped_timeframes": list(skipped_timeframes),
            "reason_codes": (
                ["insufficient_public_klines"] if skipped_timeframes else []
            ),
            "safety_flags": {
                "network_access_used": True,
                "public_market_data_only": True,
                "real_credentials_read": False,
                "broker_called": False,
                "live_orders_sent": False,
                "analytics_modified_live_state": False,
                "contains_real_credentials": False,
            },
            "timeframes": timeframe_payloads,
        },
    )


def _synthetic_klines(count=180):
    return [
        {
            "timestamp": 1700000000 + index * 60,
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 1000.0 + index,
        }
        for index in range(count)
    ]


def _fake_aggregate_report(
    *,
    status,
    reason_codes,
    survival_rate,
    walk_forward_pass_rate,
    artifact_paths,
):
    return {
        "status": status,
        "success": status == "PASS",
        "message": "fake aggregate report for H-QA-034 expanded E2E",
        "reason_codes": reason_codes,
        "safety_flags": _default_safety_flags(),
        "results": [
            {
                "status": "PASS",
                "message": "out-of-sample generated",
                "reason_codes": [],
                "metrics": {
                    "trade_count": 9,
                    "total_net_pnl": 14.0,
                    "max_drawdown": 1.0,
                    "win_rate": 0.56,
                    "sharpe_ratio": 1.1,
                },
            },
            {
                "status": "SKIPPED" if walk_forward_pass_rate < 0.67 else "PASS",
                "message": "walk-forward generated",
                "reason_codes": (
                    ["walk_forward_pass_rate_below_threshold"]
                    if walk_forward_pass_rate < 0.67
                    else []
                ),
                "metrics": {
                    "walk_forward": {
                        "walk_forward_window_count": 5,
                        "walk_forward_pass_count": int(walk_forward_pass_rate * 5),
                        "walk_forward_pass_rate": walk_forward_pass_rate,
                    }
                },
            },
            {
                "status": "SKIPPED" if survival_rate < 0.8 else "PASS",
                "message": "monte carlo generated",
                "reason_codes": (
                    ["monte_carlo_survival_rate_below_threshold"]
                    if survival_rate < 0.8
                    else []
                ),
                "metrics": {
                    "trade_count": 9,
                    "total_net_pnl": 14.0,
                    "monte_carlo_survival_rate": survival_rate,
                    "monte_carlo_run_pass_count": int(survival_rate * 20),
                    "monte_carlo_run_fail_count": 20 - int(survival_rate * 20),
                },
            },
        ],
        "aggregate_source_report_path": (
            "/tmp/source-report.json" if status == "PASS" else None
        ),
        "source_report_paths": ["/tmp/source-report.json"] if status == "PASS" else [],
        "artifact_paths": artifact_paths,
        "generated_artifact_count": len(artifact_paths),
        "validator_status": "PASS" if status == "PASS" else None,
        "h_opt_005_ready": status == "PASS",
        "h_opt_005_blockers": [] if status == "PASS" else reason_codes,
    }


def _passing_validation_results():
    return _fake_aggregate_report(
        status="PASS",
        reason_codes=[],
        survival_rate=0.83,
        walk_forward_pass_rate=1.0,
        artifact_paths=["placeholder"],
    )["results"]


def _default_safety_flags():
    return {
        "network_access_used": False,
        "real_credentials_read": False,
        "broker_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
    }


def _kline(timestamp: int, close: float) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1.0,
        is_complete=True,
    )


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _assert_no_secret_like_values(value):
    if isinstance(value, dict):
        for item in value.values():
            _assert_no_secret_like_values(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_no_secret_like_values(item)
        return
    if not isinstance(value, str):
        return
    assert not any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS), value


def _assert_no_live_side_effect_flags(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalize_key(key) in DANGEROUS_TRUE_FLAGS:
                assert item is False, f"{key} must remain false in H-QA-034 E2E"
            _assert_no_live_side_effect_flags(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_no_live_side_effect_flags(item)


def _normalize_key(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
