import importlib
import json
import re
from pathlib import Path

import pytest

from quant.optimization.tests.test_artifact_generation import (
    make_source_report,
    write_source_report,
)
from quant.orchestration.tests.test_worker_runtime import StubScheduler
from quant.orchestration.worker_runtime import SmartQTFWorkerRuntime
from scripts import generate_strategy_validation_artifacts as generate_artifacts
from scripts import generate_strategy_validation_source_reports as generate_reports


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
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
    "network_used",
    "real_order_submitted",
}


def test_strategy_validation_artifact_to_web_review_fixture_e2e(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    worker_module = importlib.import_module("scripts.smartqtf_worker")
    if worker_module.FastAPI is None:
        pytest.skip("FastAPI is not installed in this environment")

    from fastapi.testclient import TestClient

    source_path = write_source_report(
        tmp_path / "source-reports" / "BTCUSDT" / "validation-source.json",
        make_source_report(),
    )
    artifact_dir = tmp_path / "artifacts"
    generation_report_path = tmp_path / "generation-latest.json"
    latest_report_path = tmp_path / "latest.json"
    promotion_review_log_path = tmp_path / "promotion-reviews.jsonl"

    generation_report = generate_artifacts.run_strategy_validation_artifact_generation(
        source_reports=[source_path],
        source_report_dirs=[],
        artifact_dir=artifact_dir,
        output_path=generation_report_path,
        validator_output_path=latest_report_path,
        timestamp=1777827700,
        require_gate_pass=True,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
    )

    assert generation_report["status"] == "PASS"
    assert generation_report["generated_artifact_count"] == 1
    assert generation_report["validator_report"]["status"] == "PASS"
    assert generation_report["validator_report"]["artifact_count"] == 1
    assert latest_report_path.exists()

    worker = SmartQTFWorkerRuntime(
        scheduler=StubScheduler(),
        strategy_validation_artifact_dir=artifact_dir,
        strategy_validation_latest_report_path=latest_report_path,
        promotion_review_log_path=promotion_review_log_path,
    )
    client = TestClient(worker_module.create_app(worker))

    optimization_response = client.get("/optimization")
    assert optimization_response.status_code == 200
    optimization = optimization_response.json()
    assert optimization["available"] is True
    assert optimization["status"] == "PASS"
    assert optimization["review_status"] == "READY_FOR_MANUAL_REVIEW"
    assert optimization["artifact_count"] == 1
    assert optimization["failed_count"] == 0
    assert optimization["latest_report_found"] is True
    assert optimization["manual_review_required"] is True
    assert optimization["evidence_summary"]["has_out_of_sample"] is True
    assert optimization["evidence_summary"]["walk_forward_count"] == 3
    assert optimization["evidence_summary"]["walk_forward_pass_count"] == 3
    assert optimization["evidence_summary"]["has_monte_carlo"] is True
    assert optimization["evidence_summary"]["monte_carlo_survival_rate_min"] == 0.83
    assert optimization["safety"]["manual_review_dry_run_only"] is True
    assert optimization["safety"]["live_orders_sent"] is False
    assert optimization["safety"]["broker_called"] is False
    assert optimization["safety"]["live_deployment_triggered"] is False

    candidate = optimization["review_candidates"][0]
    assert candidate["approve_enabled"] is True
    assert candidate["reject_enabled"] is True
    assert candidate["review_status"] == "READY_FOR_MANUAL_REVIEW"
    assert candidate["gate_decision"]["action"] == "approve"
    assert "promotion_gate_passed" in candidate["gate_decision"]["reason_codes"]

    _assert_web_review_surface_is_wired_for_dry_run_review()

    review_response = client.post(
        "/optimization/review",
        json={
            "action": "approve",
            "artifact_id": candidate["artifact_id"],
            "reviewer_note": "fixture e2e reviewed validation artifact in dry-run",
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
    assert review["safety"]["live_orders_sent"] is False
    assert review["safety"]["broker_called"] is False
    assert review["safety"]["network_used"] is False

    persisted_reviews = [
        json.loads(line)
        for line in promotion_review_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(persisted_reviews) == 1
    assert persisted_reviews[0]["reviewer_note"] == "fixture e2e reviewed validation artifact in dry-run"

    for payload in (generation_report, optimization, review, persisted_reviews[0]):
        _assert_no_secret_like_values(payload)
        _assert_no_live_side_effect_flags(payload)


def test_auto_generated_source_report_to_web_review_dry_run_e2e(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    worker_module = importlib.import_module("scripts.smartqtf_worker")
    if worker_module.FastAPI is None:
        pytest.skip("FastAPI is not installed in this environment")

    from fastapi.testclient import TestClient

    history_path = _write_json(
        tmp_path / "local-history" / "BTCUSDT-1m.json",
        _build_local_klines(),
    )
    source_report_dir = tmp_path / "source-reports"
    artifact_dir = tmp_path / "artifacts"
    source_report_generation_path = tmp_path / "source-report-generation-latest.json"
    artifact_generation_path = tmp_path / "artifact-generation-latest.json"
    latest_report_path = tmp_path / "latest.json"
    promotion_review_log_path = tmp_path / "promotion-reviews.jsonl"

    generation_report = generate_reports.run_strategy_validation_source_report_generation(
        source_paths=[history_path],
        strategy_id="ma_crossover",
        candidate_version="review-2026-05-04-BTCUSDT-ma_crossover",
        symbol="BTCUSDT",
        output_dir=source_report_dir,
        report_output_path=source_report_generation_path,
        timestamp=1777827800,
        generation_kind="aggregate",
        train_bars=30,
        test_bars=20,
        step_bars=20,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        monte_carlo_run_count=40,
        min_monte_carlo_trades=5,
        min_monte_carlo_survival_rate=0.3,
        artifact_dir=artifact_dir,
        artifact_generation_output_path=artifact_generation_path,
        validator_output_path=latest_report_path,
        require_gate_pass=True,
    )

    assert generation_report["status"] == "PASS"
    assert generation_report["source_report_generation_scope"] == "H-OPT-016"
    assert generation_report["generated_source_report_count"] == 1
    assert generation_report["generated_artifact_count"] == 1
    assert generation_report["validator_status"] == "PASS"
    assert generation_report["h_opt_005_ready"] is True
    assert generation_report["reason_codes"] == []
    assert generation_report["safety_flags"]["live_orders_sent"] is False
    assert generation_report["safety_flags"]["broker_called"] is False
    assert source_report_generation_path.exists()
    assert artifact_generation_path.exists()
    assert latest_report_path.exists()

    source_report_path = Path(generation_report["aggregate_source_report_path"])
    assert source_report_path.exists()
    assert "config/examples" not in str(source_report_path)
    source_report_payload = json.loads(source_report_path.read_text(encoding="utf-8"))
    assert source_report_payload["provenance"]["generation_scope"] == "H-OPT-016"
    assert source_report_payload["provenance"]["source_paths"] == [str(history_path)]
    assert source_report_payload["provenance"]["source_fingerprints"][0]["sha256"]
    assert source_report_payload["walk_forward_window_count"] >= 3
    assert source_report_payload["monte_carlo_validation"]["run_count"] == 40

    worker = SmartQTFWorkerRuntime(
        scheduler=StubScheduler(),
        strategy_validation_artifact_dir=artifact_dir,
        strategy_validation_latest_report_path=latest_report_path,
        promotion_review_log_path=promotion_review_log_path,
    )
    client = TestClient(worker_module.create_app(worker))

    optimization_response = client.get("/optimization")
    assert optimization_response.status_code == 200
    optimization = optimization_response.json()
    assert optimization["available"] is True
    assert optimization["status"] == "PASS"
    assert optimization["review_status"] == "READY_FOR_MANUAL_REVIEW"
    assert optimization["artifact_count"] == 1
    assert optimization["failed_count"] == 0
    assert optimization["latest_report_found"] is True
    assert optimization["evidence_summary"]["has_out_of_sample"] is True
    assert optimization["evidence_summary"]["walk_forward_count"] >= 3
    assert optimization["evidence_summary"]["has_monte_carlo"] is True
    assert optimization["safety"]["manual_review_dry_run_only"] is True
    assert optimization["safety"]["live_orders_sent"] is False
    assert optimization["safety"]["broker_called"] is False
    assert optimization["safety"]["live_deployment_triggered"] is False

    candidate = optimization["review_candidates"][0]
    assert candidate["approve_enabled"] is True
    assert candidate["review_status"] == "READY_FOR_MANUAL_REVIEW"
    assert candidate["gate_decision"]["action"] == "approve"
    assert "promotion_gate_passed" in candidate["gate_decision"]["reason_codes"]

    review_response = client.post(
        "/optimization/review",
        json={
            "action": "approve",
            "artifact_id": candidate["artifact_id"],
            "reviewer_note": "auto-generated local history artifact reviewed in dry-run",
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
    assert review["safety"]["live_orders_sent"] is False
    assert review["safety"]["broker_called"] is False
    assert review["safety"]["network_used"] is False

    persisted_reviews = [
        json.loads(line)
        for line in promotion_review_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(persisted_reviews) == 1
    assert persisted_reviews[0]["reviewer_note"] == (
        "auto-generated local history artifact reviewed in dry-run"
    )

    for payload in (
        generation_report,
        source_report_payload,
        optimization,
        review,
        persisted_reviews[0],
    ):
        _assert_no_secret_like_values(payload)
        _assert_no_live_side_effect_flags(payload)


def _assert_web_review_surface_is_wired_for_dry_run_review():
    runtime_console = (WEB_ROOT / "components" / "RuntimeConsole.tsx").read_text(encoding="utf-8")
    api_proxy = (WEB_ROOT / "lib" / "smartqtf-api.ts").read_text(encoding="utf-8")
    optimization_route = (
        WEB_ROOT / "app" / "api" / "smartqtf" / "optimization" / "route.ts"
    ).read_text(encoding="utf-8")
    review_route = (
        WEB_ROOT / "app" / "api" / "smartqtf" / "optimization" / "review" / "route.ts"
    ).read_text(encoding="utf-8")

    for label in ("Optimization", "Promotion Review", "Approve", "Reject", "Review note"):
        assert label in runtime_console
    assert 'callSmartQTF("/api/smartqtf/optimization/review"' in runtime_console
    assert "dry_run: true" in runtime_console
    assert "manual_review: true" in runtime_console
    assert "key_material_detected" in api_proxy
    assert "sanitizeSecrets" in api_proxy
    assert 'path: "/optimization"' in optimization_route
    assert 'path: "/optimization/review"' in review_route


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
                assert item is False, f"{key} must remain false in H-QA-031 fixture E2E"
            _assert_no_live_side_effect_flags(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_no_live_side_effect_flags(item)


def _normalize_key(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _build_local_klines(count=180):
    pattern = [100.0, 103.0, 98.0, 104.0, 97.0, 105.0]
    closes = [pattern[index % len(pattern)] + index * 0.01 for index in range(count)]
    klines = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        klines.append(
            {
                "timestamp": 1701000000 + index * 60,
                "open": previous,
                "high": max(previous, close) + 1.0,
                "low": min(previous, close) - 1.0,
                "close": close,
                "volume": 1000.0 + index,
            }
        )
    return klines


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
