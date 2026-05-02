# Harness web, Layer Interaction Tutor, and live readiness tests
# Auto-merged during repository simplification.



# --- merged from test_harness_dashboard.py ---
import json
from pathlib import Path

from scripts import update_harness_dashboard as dashboard


def test_latest_production_rehearsal_summarizes_latest_artifact(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "logs" / "production-rehearsals"
    artifact_dir.mkdir(parents=True)
    payload = {
        "report_id": "production-rehearsal:1700000000",
        "generated_at": 1700000000,
        "success": False,
        "checks": [
            {
                "name": "preflight:qtf_environment",
                "status": "FAIL",
                "category": "qtf_environment",
                "message": "QTF environment is not active",
                "source": "preflight",
            },
            {
                "name": "connectivity:okx:public",
                "status": "WARN",
                "category": "dns",
                "message": "DNS lookup failed",
                "source": "connectivity",
            },
            {
                "name": "dry_run:report",
                "status": "FAIL",
                "category": "dry_run",
                "message": "dry-run report summarized",
                "source": "dry_run",
            },
        ],
        "dry_run_summary": {
            "status": "FAIL",
            "run_id": "ci-live-dry-run",
            "failed_stages": ["execution"],
        },
        "metadata": {
            "contains_real_credentials": False,
            "live_orders_sent": False,
            "ci_safe": True,
            "external_exchange_access": False,
        },
    }
    (artifact_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(dashboard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "REHEARSAL_DIR", artifact_dir)

    summary = dashboard.latest_production_rehearsal()

    assert summary["path"] == "logs/production-rehearsals/latest.json"
    assert summary["success"] is False
    assert summary["check_count"] == 3
    assert summary["failed_count"] == 2
    assert summary["warning_count"] == 1
    assert summary["failure_categories"] == ["dry_run", "qtf_environment"]
    assert summary["dry_run_status"] == "FAIL"
    assert summary["safety"]["live_orders_sent"] is False
    assert any("QTF" in action for action in summary["next_actions"])
    assert any("execution" in action for action in summary["next_actions"])


def test_latest_production_rehearsal_returns_none_without_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "REHEARSAL_DIR", tmp_path / "missing")

    assert dashboard.latest_production_rehearsal() is None


def test_live_safety_dashboard_summary_reads_operational_artifacts(tmp_path, monkeypatch):
    pipeline_dir = tmp_path / "logs" / "pipeline-runs"
    live_pipeline_dir = tmp_path / "logs" / "live" / "pipeline-runs"
    fault_drill_dir = tmp_path / "logs" / "fault-drills"
    reconciliation_dir = tmp_path / "logs" / "reconciliation"
    for path in [pipeline_dir, live_pipeline_dir, fault_drill_dir, reconciliation_dir]:
        path.mkdir(parents=True)

    (pipeline_dir / "latest-run.json").write_text(
        json.dumps(
            {
                "context": {"run_id": "paper-run", "source": "paper"},
                "metadata": {
                    "runtime_health": {
                        "run_id": "paper-run",
                        "status": "degraded",
                        "alerts": [],
                        "kill_switch_active": False,
                        "broker_reconciliation_anomalies": 0,
                    }
                },
                "final_output": {
                    "execution_result": {
                        "client_order_id": "paper-order-1",
                        "symbol": "BTCUSDT",
                        "side": "buy",
                        "status": "accepted",
                        "lifecycle_state": "RECOVERY",
                        "remaining_qty": 0.5,
                        "filled_qty": 0.5,
                        "broker_called": False,
                        "live_orders_sent": False,
                        "dry_run": True,
                        "order_lifecycle": {
                            "client_order_id": "paper-order-1",
                            "lifecycle_state": "RECOVERY",
                            "lifecycle_path": ["CREATED", "SUBMITTED", "TIMEOUT", "RECOVERY"],
                            "order_status": "accepted",
                            "safety_flags": {
                                "broker_called": False,
                                "live_orders_sent": False,
                                "dry_run": True,
                            },
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (live_pipeline_dir / "latest-run.json").write_text(
        json.dumps(
            {
                "context": {"run_id": "live-dry-run", "source": "live"},
                "metadata": {
                    "runtime_health": {
                        "run_id": "live-dry-run",
                        "status": "critical",
                        "alerts": ["kill_switch_active"],
                        "kill_switch_active": True,
                        "broker_reconciliation_anomalies": 0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (reconciliation_dir / "latest.json").write_text(
        json.dumps(
            {
                "broker_name": "mock-broker",
                "checked_count": 2,
                "matched_count": 0,
                "drift_count": 1,
                "missing_local_count": 1,
                "missing_broker_count": 0,
                "items": [
                    {"client_order_id": "paper-order-1", "action": "update_local_from_broker"},
                    {"client_order_id": "external-order", "action": "import_broker_open_order"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (fault_drill_dir / "latest.json").write_text(
        json.dumps(
            {
                "run_id": "fault-drill-test",
                "generated_at": 1710000000,
                "status": "PASS",
                "scenario_count": 1,
                "passed_count": 1,
                "failed_count": 0,
                "broker_called": False,
                "live_orders_sent": False,
                "contains_real_credentials": False,
                "safety_assertions": {
                    "all_duplicate_order_guards_active": True,
                    "all_no_duplicate_order": True,
                },
                "scenarios": [
                    {
                        "scenario_id": "pnl_abnormal",
                        "passed": True,
                        "alerts": [{"code": "pnl_change_abnormal"}],
                        "actions": [
                            {
                                "action_type": "enter_safe_mode",
                                "alert_code": "pnl_change_abnormal",
                                "alert_severity": "critical",
                                "requires_human_ack": True,
                                "broker_called": False,
                                "live_orders_sent": False,
                                "observed_at": 1710000001,
                                "metadata": {"control_effect": {"safe_mode": True}},
                            },
                            {
                                "action_type": "trigger_kill_switch",
                                "alert_code": "pnl_change_abnormal",
                                "alert_severity": "critical",
                                "requires_human_ack": True,
                                "broker_called": False,
                                "live_orders_sent": False,
                                "observed_at": 1710000002,
                                "metadata": {"control_effect": {"kill_switch": True}},
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(dashboard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "PIPELINE_RUN_DIR", pipeline_dir)
    monkeypatch.setattr(dashboard, "LIVE_PIPELINE_RUN_DIR", live_pipeline_dir)
    monkeypatch.setattr(dashboard, "FAULT_DRILL_DIR", fault_drill_dir)
    monkeypatch.setattr(dashboard, "RECONCILIATION_DIR", reconciliation_dir)
    monkeypatch.setattr(dashboard, "ALERT_ACTION_LOG_CANDIDATES", [])

    summary = dashboard.live_safety_dashboard_summary()

    assert summary["order_lifecycle"]["order_count"] == 1
    assert summary["order_lifecycle"]["state_counts"]["RECOVERY"] == 1
    assert summary["order_lifecycle"]["recovery_order_count"] == 1
    assert summary["order_lifecycle"]["broker_called"] is False
    assert summary["reconciliation"]["status"] == "ANOMALY"
    assert summary["reconciliation"]["anomaly_count"] == 2
    assert summary["alert_actions"]["action_count"] == 2
    assert summary["alert_actions"]["safe_mode_active"] is True
    assert summary["alert_actions"]["kill_switch_active"] is True
    assert summary["runtime_safety"]["kill_switch_active"] is True
    assert summary["fault_drill"]["status"] == "PASS"
    assert summary["fault_drill"]["duplicate_order_guard_active"] is True
    assert summary["safety"]["broker_called"] is False
    assert summary["safety"]["live_orders_sent"] is False


def test_harness_dashboard_page_contains_live_safety_panel():
    html = (Path(__file__).resolve().parents[1] / "docs" / "harness" / "web" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="liveSafetyPanel"' in html
    assert "function renderLiveSafety(summary)" in html
    assert "live_safety_dashboard" in html
    assert "订单生命周期" in html
    assert "Alert actions" in html


def test_layer_contract_validation_reports_violation_count(tmp_path, monkeypatch):
    bad_strategy_file = tmp_path / "quant" / "strategy" / "bad_strategy.py"
    bad_strategy_file.parent.mkdir(parents=True)
    bad_strategy_file.write_text(
        "from quant.execution.broker import BrokerAdapter\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "PROJECT_ROOT", tmp_path)

    payload = dashboard.layer_contract_validation()

    assert payload["summary"]["status"] == "VIOLATION"
    assert payload["summary"]["violation_count"] == 1
    assert payload["violations"][0]["rule_id"] == "strategy-no-broker-imports"


# --- merged from test_layer_interaction_cases.py ---
import json
import re
from pathlib import Path

from scripts import update_harness_dashboard as dashboard


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = PROJECT_ROOT / "docs" / "harness" / "web" / "layer-interaction-cases.json"

REQUIRED_FIELDS = {
    "step_id",
    "from_layer",
    "to_layer",
    "default_input",
    "editable_fields",
    "simulator",
    "example_output",
    "field_explanations",
    "teacher_explanation",
    "teacher_details",
    "diagram",
    "next_step_id",
    "previous_step_id",
    "safety_notes",
}

REQUIRED_EDGES = [
    ("Data", "Quality"),
    ("Quality", "Feature"),
    ("Feature", "Regime"),
    ("Regime", "StrategyRoute"),
    ("StrategyRoute", "StrategySignal"),
    ("StrategySignal", "Execution"),
]

REQUIRED_TEACHER_DETAIL_FIELDS = {
    "current_layer",
    "cannot_skip",
    "input_field_impact",
    "output_handoff",
    "wrong_parameter_effect",
    "safety_boundary",
}

REQUIRED_FIELD_EXPLANATION_FIELDS = {
    "name",
    "type",
    "required",
    "meaning",
    "affects",
    "examples",
    "common_mistakes",
}

SECRET_PATTERNS = [
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"secret[_-]?key", re.IGNORECASE),
    re.compile(r"passphrase", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def load_cases():
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def test_layer_interaction_cases_define_required_schema_and_edges():
    payload = load_cases()
    schema_fields = set(payload["step_schema"]["required_fields"])
    steps = payload["steps"]

    assert REQUIRED_FIELDS <= schema_fields
    assert len(steps) >= len(REQUIRED_EDGES)
    assert [(step["from_layer"], step["to_layer"]) for step in steps] == REQUIRED_EDGES

    for index, step in enumerate(steps):
        assert REQUIRED_FIELDS <= set(step)
        assert step["simulator"] == "browser_layer_simulator"
        assert isinstance(step["default_input"], dict)
        assert isinstance(step["editable_fields"], list) and step["editable_fields"]
        assert isinstance(step["teacher_explanation"], list) and step["teacher_explanation"]
        assert REQUIRED_TEACHER_DETAIL_FIELDS <= set(step["teacher_details"])
        assert isinstance(step["teacher_details"]["input_field_impact"], list)
        assert isinstance(step["teacher_details"]["wrong_parameter_effect"], list)
        assert isinstance(step["safety_notes"], list) and step["safety_notes"]
        assert step["diagram"]["nodes"] == [step["from_layer"], step["to_layer"]]
        assert isinstance(step["diagram"]["steps"], list) and len(step["diagram"]["steps"]) >= 3
        assert isinstance(step["diagram"]["callouts"], list) and step["diagram"]["callouts"]

        expected_previous = steps[index - 1]["step_id"] if index else None
        expected_next = steps[index + 1]["step_id"] if index < len(steps) - 1 else None
        assert step["previous_step_id"] == expected_previous
        assert step["next_step_id"] == expected_next

        for field_name in step["editable_fields"]:
            assert field_name in step["field_explanations"]
            explanation = step["field_explanations"][field_name]
            assert REQUIRED_FIELD_EXPLANATION_FIELDS <= set(explanation)
            assert explanation["name"] == field_name
            assert explanation["type"]
            assert isinstance(explanation["required"], bool)
            assert explanation.get("range") or explanation.get("allowed_values")
            assert explanation["meaning"]
            assert explanation["affects"]
            assert isinstance(explanation["examples"], list) and explanation["examples"]
            assert explanation["common_mistakes"]


def test_layer_interaction_cases_have_full_teacher_panels_for_key_edges():
    payload = load_cases()
    key_edges = {
        ("Data", "Quality"),
        ("Feature", "Regime"),
        ("Regime", "StrategyRoute"),
        ("StrategySignal", "Execution"),
    }

    for step in payload["steps"]:
        details = step["teacher_details"]
        if (step["from_layer"], step["to_layer"]) not in key_edges:
            continue
        assert all(details[field] for field in REQUIRED_TEACHER_DETAIL_FIELDS)
        assert len(details["input_field_impact"]) >= 3
        assert len(details["wrong_parameter_effect"]) >= 2
        assert step["diagram"]["edge_label"]


def test_layer_interaction_cases_are_fixture_only_and_safe():
    payload = load_cases()
    text = CASES_PATH.read_text(encoding="utf-8")

    assert not re.search(r"https?://", text)
    for pattern in SECRET_PATTERNS:
        assert not pattern.search(text)

    assert payload["safety"]["network_required"] is False
    assert payload["safety"]["public_fetch_manual_only"] is True
    assert payload["safety"]["real_credentials_required"] is False
    assert payload["safety"]["broker_calls_allowed"] is False
    assert payload["safety"]["live_orders_allowed"] is False
    assert payload["safety"]["python_runner_enabled_by_default"] is False
    assert payload["multi_timeframe_contract"]["storage_key"] == (
        "smartqtf.layerInteractionTutor.savedMultiTimeframeDataInput.v1"
    )
    assert payload["multi_timeframe_contract"]["batch_path"] == "batches.{timeframe}.klines"

    for step in payload["steps"]:
        output = step["example_output"]
        flags = output["safety_flags"]
        assert output["simulated"] is True
        assert output["source"] == "browser_layer_simulator"
        assert output["reason_codes"]
        assert flags["network_used"] is False
        assert flags["real_credentials_used"] is False
        assert flags["broker_called"] is False
        assert flags["live_orders_sent"] is False


def test_layer_interaction_cases_define_multi_timeframe_data_contract():
    payload = load_cases()
    first_step = payload["steps"][0]
    default_input = first_step["default_input"]

    assert first_step["step_id"] == "data_to_quality"
    assert default_input["schema"] == "MultiTimeframeDataInput"
    assert default_input["execution_timeframe"] == "5m"
    assert {"15m", "1h", "4h"} <= set(default_input["context_timeframes"])
    assert "batches" in default_input
    assert "5m" in default_input["batches"]
    assert default_input["batches"]["5m"]["klines"]
    assert "bar_limits" in default_input
    assert "execution_timeframe" in first_step["editable_fields"]
    assert "context_timeframes" in first_step["editable_fields"]
    assert "batches" in first_step["editable_fields"]

    explanation_text = json.dumps(payload, ensure_ascii=False)
    assert "alignment_features" in explanation_text
    assert "HIGHER_TIMEFRAME_CONFLICT_FILTER" in explanation_text
    assert "tradability" in explanation_text


def test_layer_interaction_cases_feed_dashboard_summary():
    summary = dashboard.layer_interaction_tutor_summary(dashboard.parse_tasks())

    assert summary["cases_exists"] is True
    assert summary["step_count"] == len(REQUIRED_EDGES)
    assert summary["editable_step_count"] == len(REQUIRED_EDGES)
    assert summary["simulator_count"] == 1
    assert summary["simulator_coverage_count"] >= len(REQUIRED_EDGES)
    assert "Data->Quality" in summary["simulator_edges"]
    assert "StrategySignal->Execution" in summary["simulator_edges"]
    assert summary["teacher_panel_count"] == len(REQUIRED_EDGES)
    assert summary["field_explanation_count"] >= len(REQUIRED_EDGES) * 3
    assert summary["safety"]["network_required"] is False
    assert summary["safety"]["real_credentials_required"] is False
    assert summary["safety"]["live_orders_allowed"] is False


# --- merged from test_layer_interaction_simulator_registry.py ---
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TUTOR_JS_PATH = PROJECT_ROOT / "docs" / "harness" / "web" / "layer-interaction-tutor.js"

REQUIRED_SIMULATOR_EDGES = [
    "Data->Quality",
    "Quality->Feature",
    "Feature->Regime",
    "Regime->StrategyRoute",
    "StrategyRoute->StrategySignal",
    "StrategySignal->Execution",
]


def test_layer_interaction_browser_simulator_registry_covers_main_flow():
    js = TUTOR_JS_PATH.read_text(encoding="utf-8")

    assert "const SIMULATOR_SOURCE = \"browser_layer_simulator\"" in js
    assert "const SIMULATOR_EDGE_KEYS" in js
    assert "const SIMULATOR_REGISTRY" in js

    for edge in REQUIRED_SIMULATOR_EDGES:
        assert f'"{edge}"' in js

    assert "function simulateStep(step, input)" in js
    assert "function mapOutputToNextInput(output, nextStep)" in js
    assert "next_input" in js
    assert "reason_codes" in js


def test_layer_interaction_dashboard_reports_simulator_coverage():
    from scripts import update_harness_dashboard as dashboard

    summary = dashboard.layer_interaction_tutor_summary(dashboard.parse_tasks())

    assert summary["simulator_coverage_count"] == len(REQUIRED_SIMULATOR_EDGES)
    assert summary["simulator_edges"] == REQUIRED_SIMULATOR_EDGES


def test_layer_interaction_browser_simulator_is_static_and_safe():
    js = TUTOR_JS_PATH.read_text(encoding="utf-8")

    assert "fetchJson(\"harness-status.json\")" in js
    assert "fetchJson(\"layer-interaction-cases.json\")" in js
    assert "browser_layer_tutor_shell" in js
    assert "PUBLIC_KLINE_URL" in js
    assert "smartqtf.layerInteractionTutor.savedMultiTimeframeDataInput.v1" in js
    assert "function fetchMultiTimeframeKlines()" in js
    assert "network_used: false" in js
    assert "real_credentials_used: false" in js
    assert "local_python_runner_used: false" in js
    assert "broker_called: false" in js
    assert "live_orders_sent: false" in js
    assert "BROKER_NOT_CALLED" in js
    assert "place_order" not in js
    assert "XMLHttpRequest" not in js
    assert "WebSocket" not in js


# --- merged from test_layer_interaction_tutor_page.py ---
import re
from pathlib import Path

from scripts import update_harness_dashboard as dashboard


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TUTOR_HTML_PATH = PROJECT_ROOT / "docs" / "harness" / "web" / "layer-interaction-tutor.html"
TUTOR_CSS_PATH = PROJECT_ROOT / "docs" / "harness" / "web" / "layer-interaction-tutor.css"
TUTOR_JS_PATH = PROJECT_ROOT / "docs" / "harness" / "web" / "layer-interaction-tutor.js"


def test_layer_interaction_tutor_page_shell_is_static_and_single_step_first():
    html = TUTOR_HTML_PATH.read_text(encoding="utf-8")
    css = TUTOR_CSS_PATH.read_text(encoding="utf-8")
    js = TUTOR_JS_PATH.read_text(encoding="utf-8")
    bundle = "\n".join([html, css, js])

    assert 'href="layer-interaction-tutor.css"' in html
    assert 'src="layer-interaction-tutor.js"' in html
    assert 'id="sourceSelect"' in html
    assert 'id="edgeMode"' in html
    assert 'id="layerMode"' in html
    assert 'id="stepList"' in html
    assert 'id="inputEditor"' in html
    assert 'id="outputViewer"' in html
    assert 'id="handoffViewer"' in html
    assert 'id="modeCaption"' in html
    assert "老师讲解和配图" in html
    assert "刷新公开多周期 DATA" in html

    assert "layer-interaction-cases.json" in js
    assert "DEFAULT_FLOW_STEPS" in js
    assert "browser_layer_tutor_shell" in js
    assert "TUTOR_SHELL_ONLY" in js
    assert "MultiTimeframeDataInput" in js
    assert "function setViewMode(mode)" in js
    assert "function renderHandoff(step)" in js
    assert "function renderTeacherDetails(details)" in js
    assert "function renderDiagram(step, layerMode)" in js
    assert "mapOutputToNextInput(output, nextStep)" in js
    assert "broker_called: false" in js
    assert "live_orders_sent: false" in js

    assert 'id="multiSymbol"' in html
    assert 'id="executionTimeframe"' in html
    assert 'id="tf5mEnabled"' in html
    assert 'id="tf15mEnabled"' in html
    assert 'id="tf1hEnabled"' in html
    assert 'id="tf4hEnabled"' in html
    assert 'id="refreshMultiTimeframeData"' in html
    assert 'id="clearSavedMultiTimeframeData"' in html
    assert "全量测试表" not in html
    urls = re.findall(r"https?://[^\"'\s]+", bundle)
    assert urls == ["https://api.binance.com/api/v3/klines"]


def test_layer_interaction_tutor_ui_has_single_edge_and_layer_workbench():
    html = TUTOR_HTML_PATH.read_text(encoding="utf-8")
    css = TUTOR_CSS_PATH.read_text(encoding="utf-8")
    js = TUTOR_JS_PATH.read_text(encoding="utf-8")

    assert 'class="tutor-layout"' in html
    assert 'class="flow-nav"' in html
    assert 'class="step-workbench"' in html
    assert 'class="field-panel"' in html
    assert 'class="mode-switch"' in html
    assert 'class="handoff-panel"' in html

    assert ".mode-switch" in css
    assert ".handoff-panel" in css
    assert ".step-badges" in css
    assert ".teacher-detail-grid" in css
    assert ".diagram-steps" in css
    assert ".diagram-callouts" in css
    assert ".field-item.is-edited" in css
    assert ".field-diff" in css
    assert ".multi-timeframe-bar" in css
    assert ".timeframe-grid" in css
    assert ".timeframe-toggle" in css

    assert 'viewMode: "edge"' in js
    assert 'setViewMode("layer")' in js
    assert 'RUN_CURRENT_STEP_TO_PREVIEW_HANDOFF' in js
    assert 'END_OF_TUTOR_FLOW' in js
    assert "state.inputEdits[targetStep.step_id] = mapOutputToNextInput" in js
    assert "参数改错会怎样" in js
    assert "getPathValue" in js
    assert "function changedEditableFields(step)" in js
    assert "FIELD_CHANGED_AFFECTS_OUTPUT" in js
    assert 'addEventListener("input", () => renderFields(curStep()))' in js
    assert "batches.{timeframe}.klines" in js


def test_layer_interaction_tutor_feeds_dashboard_summary():
    summary = dashboard.layer_interaction_tutor_summary(dashboard.parse_tasks())
    task_by_id = {task["ID"]: task for task in summary["tasks"]}

    assert summary["page"] == "docs/harness/web/layer-interaction-tutor.html"
    assert summary["cases"] == "docs/harness/web/layer-interaction-cases.json"
    assert summary["page_exists"] is True
    assert summary["simulator_coverage_count"] == len(REQUIRED_SIMULATOR_EDGES)
    assert summary["safety"]["network_required"] is False
    assert summary["safety"]["public_fetch_manual_only"] is True
    assert summary["safety"]["real_credentials_required"] is False
    assert summary["safety"]["live_orders_allowed"] is False
    assert set(summary["simulator_edges"]) == set(REQUIRED_SIMULATOR_EDGES)


# --- merged from test_workflow_review_cases.py ---
import json
import re
from pathlib import Path

from scripts import update_harness_dashboard as dashboard


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = PROJECT_ROOT / "docs" / "harness" / "web" / "workflow-test-review-cases.json"
CONSOLE_HTML_PATH = PROJECT_ROOT / "docs" / "harness" / "web" / "workflow-test-review.html"
CONSOLE_JS_PATH = PROJECT_ROOT / "docs" / "harness" / "web" / "workflow-test-review.js"
RUNNER_CONTRACT_PATH = PROJECT_ROOT / "docs" / "harness" / "harness-guide.md"

REQUIRED_CASE_FIELDS = {
    "case_id",
    "task_id",
    "todolist_section",
    "test_type",
    "layer_from",
    "layer_to",
    "test_file",
    "pytest_nodeid",
    "status",
    "input_fixture",
    "replay_output",
    "expected_output",
    "assertions",
    "safety_assertions",
    "editable",
    "simulator",
    "artifact_path",
}

EXPECTED_SIMULATORS = {
    "timeguard_as_of",
    "ai_sandbox",
    "decision_risk_gate",
    "risk_execution_gate",
    "live_order_gate",
    "artifact_secret_scan",
    "pipeline_report_parity",
}

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(r"(?i)(api[_-]?key|secret|passphrase)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
]


def _payload():
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


def test_workflow_review_cases_define_required_schema_and_first_cases():
    payload = _payload()

    assert payload["schema_version"] == "1.0"
    assert set(payload["case_schema"]["required_fields"]) == REQUIRED_CASE_FIELDS
    assert payload["source_archived_tasks"] == [f"H-QA-{number:03d}" for number in range(9, 17)]

    cases = payload["cases"]
    assert len(cases) >= len(EXPECTED_SIMULATORS)
    assert EXPECTED_SIMULATORS <= {case["simulator"] for case in cases}

    for case in cases:
        assert REQUIRED_CASE_FIELDS <= set(case)
        assert case["status"] in payload["case_schema"]["status_values"]
        assert case["test_type"] in payload["case_schema"]["test_type_values"]
        assert case["editable"] is True
        assert case["expected_output"]["simulated"] is True
        assert case["artifact_path"].startswith("docs/harness/archive/")
        _assert_pytest_node_exists(case["pytest_nodeid"], case["test_file"])


def test_workflow_review_cases_are_fixture_only_and_side_effect_safe():
    payload = _payload()
    payload_text = json.dumps(payload, sort_keys=True)

    assert payload["safety"]["network_required"] is False
    assert payload["safety"]["real_credentials_required"] is False
    assert payload["safety"]["live_orders_allowed"] is False
    assert payload["safety"]["local_python_runner_enabled_by_default"] is False

    for pattern in SECRET_PATTERNS:
        assert not pattern.search(payload_text)

    for case in payload["cases"]:
        flags = case["safety_flags"]
        assert flags["network_used"] is False
        assert flags["real_credentials_used"] is False
        assert flags["broker_called"] is False
        assert flags["live_orders_sent"] is False
        assert flags["local_python_runner_used"] is False
        assert any(assertion.endswith("=false") for assertion in case["safety_assertions"])


def test_workflow_review_cases_feed_dashboard_summary():
    payload = _payload()

    summary = dashboard.workflow_test_review_console_summary(dashboard.parse_tasks())
    task_by_id = {task["ID"]: task for task in summary["tasks"]}

    assert summary["page"] == "docs/harness/web/workflow-test-review.html"
    assert summary["cases"] == "docs/harness/web/workflow-test-review-cases.json"
    assert summary["page_exists"] is True
    assert summary["cases_exists"] is True
    assert summary["case_count"] == len(payload["cases"])
    assert summary["editable_case_count"] == len(payload["cases"])
    assert EXPECTED_SIMULATORS <= set(summary["simulators"])
    assert summary["safety"]["network_required"] is False
    assert summary["safety"]["live_orders_allowed"] is False
    assert {"H-QA-017", "H-MON-011", "H-QA-018", "H-QA-019", "H-MON-012"} <= set(task_by_id)
    assert task_by_id["H-MON-012"]["状态"] == "DONE"
    assert task_by_id["H-MON-012"]["archive_path"].endswith("completed-tasks.md")


def test_local_python_runner_contract_is_documented_and_disabled_by_default():
    payload = _payload()
    html = CONSOLE_HTML_PATH.read_text(encoding="utf-8")
    js = CONSOLE_JS_PATH.read_text(encoding="utf-8")
    contract = RUNNER_CONTRACT_PATH.read_text(encoding="utf-8")

    assert payload["safety"]["local_python_runner_enabled_by_default"] is False
    assert 'id="localRunnerButton"' in html
    assert "Run Local Python Case" in html
    assert "disabled aria-disabled=\"true\"" in html
    assert "LOCAL_RUNNER_CONTRACT" in js
    assert "enabled_by_default: false" in js
    assert "requires_local_review_server: true" in js
    assert "network_policy: \"deny\"" in js
    assert "broker_policy: \"stub_only\"" in js
    assert "credentials_policy: \"deny_real_credentials\"" in js
    assert "POST /workflow-review/local-runner/cases/{case_id}:dry-run" in contract
    assert "SMARTQTF_USE_PROXY=1 python -m pytest -q <single pytest_nodeid>" in contract
    assert "状态：DISABLED" in contract


def _assert_pytest_node_exists(nodeid, test_file):
    assert nodeid.startswith(f"{test_file}::")
    file_path = PROJECT_ROOT / test_file
    assert file_path.exists(), test_file
    function_name = nodeid.split("::", 1)[1].split("[", 1)[0]
    assert f"def {function_name}" in file_path.read_text(encoding="utf-8")


# --- merged from test_ci_safety_rehearsal.py ---
import json

from scripts import run_ci_safety_rehearsal as ci_rehearsal


def test_ci_safety_rehearsal_writes_report_without_exchange_access(tmp_path, monkeypatch):
    output_dir = tmp_path / "ci-rehearsal"
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_API_SECRET", raising=False)
    monkeypatch.delenv("OKX_API_PASSPHRASE", raising=False)

    report, artifact_paths, dry_run_path = ci_rehearsal.run_ci_safety_rehearsal(output_dir=output_dir)
    payload = report.to_payload()
    checks = {check["name"]: check for check in payload["checks"]}

    assert report.success is True
    assert dry_run_path.exists()
    assert payload["metadata"]["ci_safe"] is True
    assert payload["metadata"]["external_exchange_access"] is False
    assert payload["metadata"]["contains_real_credentials"] is False
    assert payload["metadata"]["live_orders_sent"] is False
    assert checks["connectivity_diagnostics"]["status"] == "SKIPPED"
    assert checks["dry_run:report"]["status"] == "PASS"
    assert payload["dry_run_summary"]["run_id"] == "ci-live-dry-run"
    dry_run_payload = json.loads(dry_run_path.read_text(encoding="utf-8"))
    dry_run_stages = {stage["stage"]: stage for stage in dry_run_payload["stages"]}
    assert dry_run_payload["success"] is True
    assert dry_run_stages["risk"]["status"] == "rejected"
    assert dry_run_stages["execution"]["status"] == "skipped"
    assert dry_run_payload["final_output"]["risk_decision"]["approved"] is False
    assert (output_dir / "ci-safety-rehearsal.json").exists()
    assert (output_dir / "ci-safety-rehearsal.md").exists()
    assert (output_dir / "latest.json").exists()
    assert artifact_paths["json_path"].endswith("ci-safety-rehearsal.json")


def test_ci_safety_rehearsal_cli_returns_success_and_prints_artifact_paths(tmp_path, monkeypatch, capsys):
    output_dir = tmp_path / "ci-cli"
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")

    exit_code = ci_rehearsal.main(["--output-dir", str(output_dir)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["metadata"]["ci_safe"] is True
    assert payload["metadata"]["artifact_paths"]["latest_json_path"].endswith("latest.json")
    assert (output_dir / "ci-live-dry-run.json").exists()


# --- merged from test_exchange_connectivity_diagnostics.py ---
import socket
from urllib import error

from scripts import diagnose_exchange_connectivity as diag


class FakeResponse:
    def __init__(self, body=b'{"serverTime": 1}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def _checks_by_exchange_and_scope(report):
    return {(check["exchange"], check["scope"]): check for check in report["checks"]}


def test_public_diagnostics_pass_with_fake_endpoint(monkeypatch):
    monkeypatch.setattr(diag.request, "urlopen", lambda *_, **__: FakeResponse())

    report = diag.run_diagnostics(exchanges=["okx"], timeout=0.1, use_proxy=False)
    check = _checks_by_exchange_and_scope(report)[("okx", "public")]

    assert report["success"] is True
    assert check["status"] == "PASS"
    assert check["category"] == "ok"
    assert "latency_ms" in check


def test_default_diagnostics_require_project_proxy_before_external_call(monkeypatch):
    calls = []

    def fail_if_called(*_, **__):
        calls.append("urlopen")
        raise AssertionError("external diagnostics must fail before urlopen without SMARTQTF_USE_PROXY=1")

    monkeypatch.delenv("SMARTQTF_USE_PROXY", raising=False)
    monkeypatch.setattr(diag.request, "urlopen", fail_if_called)

    report = diag.run_diagnostics(exchanges=["okx"], include_private=True, timeout=0.1)
    checks = _checks_by_exchange_and_scope(report)

    assert report["success"] is False
    assert report["proxy"]["enabled"] is False
    assert calls == []
    assert checks[("okx", "public")]["status"] == "FAIL"
    assert checks[("okx", "public")]["category"] == "proxy"
    assert checks[("okx", "private")]["status"] == "FAIL"
    assert checks[("okx", "private")]["category"] == "proxy"


def test_public_diagnostics_classify_dns_failure(monkeypatch):
    def fail_dns(*_, **__):
        raise error.URLError(socket.gaierror("nodename nor servname provided"))

    monkeypatch.setattr(diag.request, "urlopen", fail_dns)

    report = diag.run_diagnostics(exchanges=["binance"], timeout=0.1, use_proxy=False)
    check = _checks_by_exchange_and_scope(report)[("binance", "public")]

    assert report["success"] is False
    assert check["status"] == "FAIL"
    assert check["category"] == "dns"


def test_private_diagnostics_fail_fast_when_credentials_are_missing(monkeypatch):
    for name in diag.PRIVATE_CREDENTIALS["okx"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(diag.request, "urlopen", lambda *_, **__: FakeResponse())

    report = diag.run_diagnostics(
        exchanges=["okx"],
        include_private=True,
        timeout=0.1,
        use_proxy=False,
    )
    checks = _checks_by_exchange_and_scope(report)

    assert checks[("okx", "public")]["status"] == "PASS"
    assert checks[("okx", "private")]["status"] == "FAIL"
    assert checks[("okx", "private")]["category"] == "credential"
    assert set(checks[("okx", "private")]["details"]["missing"]) == set(diag.PRIVATE_CREDENTIALS["okx"])


def test_error_classifier_distinguishes_proxy_rate_limit_and_credentials():
    assert diag._classify_error(error.URLError("Tunnel connection failed: proxy refused")) == "proxy"
    assert diag._classify_error(error.HTTPError("url", 429, "Too Many Requests", {}, None)) == "rate_limit"
    assert diag._classify_error(error.HTTPError("url", 401, "Unauthorized", {}, None)) == "credential"


# --- merged from test_live_safety_external_validation.py ---
import json

from scripts import validate_account_sync
from scripts import validate_ai_decision_advisor as validate_ai
from scripts import validate_strategy_validation_artifacts as validate_artifacts


def _json_text(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def test_account_live_validation_skips_before_adapter_construction_without_manual_gate(monkeypatch):
    calls = []

    def fail_if_called(**_):
        calls.append("adapter")
        raise AssertionError("account live validation must not build private adapters without manual gate")

    monkeypatch.delenv("SMARTQTF_RUN_ACCOUNT_SYNC_TEST", raising=False)
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setattr(validate_account_sync, "_build_account_sync_adapter", fail_if_called)

    report = validate_account_sync.run_account_sync_validation(
        exchanges=["okx"],
        mode="live",
        timestamp=1710000600,
    )

    assert calls == []
    assert report["status"] == "SKIPPED"
    assert report["success"] is False
    assert report["read_only"] is True
    assert report["live_orders_sent"] is False
    assert report["contains_real_credentials"] is False
    assert report["checks"][0]["category"] == "manual_gate"


def test_account_live_validation_requires_proxy_before_private_adapter_construction(monkeypatch):
    calls = []
    secret_values = ["okx-key-secret", "okx-secret-secret", "okx-passphrase-secret"]

    def fail_if_called(**_):
        calls.append("adapter")
        raise AssertionError("account live validation must fail before private adapter construction without proxy")

    monkeypatch.setenv("SMARTQTF_RUN_ACCOUNT_SYNC_TEST", "1")
    monkeypatch.delenv("SMARTQTF_USE_PROXY", raising=False)
    monkeypatch.setenv("OKX_API_KEY", secret_values[0])
    monkeypatch.setenv("OKX_SECRET", secret_values[1])
    monkeypatch.setenv("OKX_PASSPHRASE", secret_values[2])
    monkeypatch.setattr(validate_account_sync, "_build_account_sync_adapter", fail_if_called)

    report = validate_account_sync.run_account_sync_validation(
        exchanges=["okx"],
        mode="live",
        timestamp=1710000600,
    )

    assert calls == []
    assert report["status"] == "FAIL"
    assert report["success"] is False
    assert report["read_only"] is True
    assert report["live_orders_sent"] is False
    assert report["contains_real_credentials"] is False
    assert report["checks"][0]["category"] == "proxy"
    payload_text = _json_text(report)
    for secret in secret_values:
        assert secret not in payload_text


def test_ai_provider_validation_requires_environment_before_client_construction(monkeypatch, tmp_path):
    calls = []

    def fail_if_called(*_, **__):
        calls.append("client")
        raise AssertionError("AI provider validation must fail before client construction without preflight env")

    output_path = tmp_path / "ai-validation-report.json"
    monkeypatch.setenv("SMARTQTF_RUN_AI_DECISION_ADVISOR_TEST", "1")
    monkeypatch.delenv("SMARTQTF_USE_PROXY", raising=False)
    monkeypatch.delenv("SMARTQTF_AI_ADVISOR_ENDPOINT", raising=False)
    monkeypatch.setenv("SMARTQTF_AI_ADVISOR_API_KEY", "ai-api-key-secret")
    monkeypatch.setattr(validate_ai, "ChatCompletionsJSONClient", fail_if_called)

    report = validate_ai.run_ai_decision_advisor_validation(
        symbol="BTCUSDT",
        timeframe="1m",
        model_name="real-provider-model",
        timestamp=1710000600,
        output_path=output_path,
    )

    assert calls == []
    assert report["status"] == "FAIL"
    assert report["read_only"] is True
    assert report["live_orders_sent"] is False
    assert report["risk_bypassed"] is False
    assert report["contains_real_credentials"] is False
    assert {check["category"] for check in report["checks"]} == {"proxy", "configuration"}
    payload_text = output_path.read_text(encoding="utf-8")
    assert "ai-api-key-secret" not in payload_text
    assert json.loads(payload_text)["status"] == "FAIL"


def test_strategy_validation_artifact_report_skips_without_side_effects_or_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setenv("OKX_API_KEY", "okx-key-secret")
    output_path = tmp_path / "strategy-validation-latest.json"

    report = validate_artifacts.run_strategy_validation_artifacts_validation(
        artifact_dir=tmp_path / "missing-artifacts",
        artifact_paths=None,
        output_path=output_path,
        timestamp=1710007300,
    )

    assert report["status"] == "SKIPPED"
    assert report["success"] is False
    assert report["artifact_count"] == 0
    assert report["live_orders_sent"] is False
    assert report["analytics_modified_live_state"] is False
    assert report["contains_real_credentials"] is False
    assert "okx-key-secret" not in output_path.read_text(encoding="utf-8")


# --- merged from test_preflight_live_readiness.py ---
import json
from pathlib import Path

from scripts.preflight_live_readiness import run_preflight


def _write_preflight_config(tmp_path, overrides=None):
    payload = {
        "name": "live-preflight-test",
        "source": "live",
        "markets": [
            {
                "symbol": "BTC-USDT",
                "timeframe": "1m",
                "enabled": True,
                "provider": "okx_public",
            }
        ],
        "strategies": [
            {
                "symbol": "BTC-USDT",
                "strategy": "ma_crossover",
                "route": "default",
                "parameters": {"fast_window": 3, "slow_window": 5},
            }
        ],
        "risk": {
            "risk_plugin": "default",
            "kill_switch_enabled": True,
            "max_position_size": 0.1,
            "max_drawdown": 0.05,
        },
        "broker": {
            "mode": "live",
            "broker_plugin": "okx_broker",
            "account_id": "preflight-account",
            "settings": {
                "allow_live_orders": False,
                "require_manual_preflight": True,
                "api_key_env": "OKX_API_KEY",
                "api_secret_env": "OKX_API_SECRET",
                "api_passphrase_env": "OKX_API_PASSPHRASE",
            },
        },
        "metadata": {"contains_real_credentials": False},
    }
    if overrides:
        _deep_update(payload, overrides)
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def _deep_update(target, overrides):
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _checks_by_name(report):
    return {check["name"]: check for check in report["checks"]}


def test_preflight_passes_with_qtf_proxy_credentials_and_read_only_live_gates(tmp_path, monkeypatch):
    config_path = _write_preflight_config(tmp_path)
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")

    report = run_preflight(config_path)
    checks = _checks_by_name(report)

    assert report["success"] is True
    assert checks["qtf_environment"]["status"] == "PASS"
    assert checks["credential_state"]["status"] == "PASS"
    assert checks["live_safety_state"]["status"] == "PASS"
    assert checks["plugin_registration"]["status"] == "PASS"
    assert checks["runtime_construction"]["status"] == "PASS"


def test_preflight_fails_when_qtf_environment_is_not_active(tmp_path, monkeypatch):
    config_path = _write_preflight_config(tmp_path)
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")

    report = run_preflight(config_path)

    assert report["success"] is False
    assert _checks_by_name(report)["qtf_environment"]["status"] == "FAIL"


def test_preflight_fails_live_config_missing_credentials_and_safety_gates(tmp_path, monkeypatch):
    config_path = _write_preflight_config(
        tmp_path,
        {
            "risk": {"kill_switch_enabled": False},
            "broker": {"settings": {"allow_live_orders": True}},
            "metadata": {"contains_real_credentials": True},
        },
    )
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_API_SECRET", raising=False)
    monkeypatch.delenv("OKX_API_PASSPHRASE", raising=False)

    report = run_preflight(config_path)
    checks = _checks_by_name(report)

    assert report["success"] is False
    assert checks["credential_state"]["status"] == "FAIL"
    assert checks["live_safety_state"]["status"] == "FAIL"
    assert "risk.kill_switch_enabled" in " ".join(checks["live_safety_state"]["details"]["problems"])


def test_preflight_fails_unknown_live_plugins(tmp_path, monkeypatch):
    config_path = _write_preflight_config(
        tmp_path,
        {
            "markets": [{"symbol": "BTC-USDT", "timeframe": "1m", "provider": "unknown_data"}],
            "broker": {"broker_plugin": "unknown_broker"},
        },
    )
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")

    report = run_preflight(config_path)
    check = _checks_by_name(report)["plugin_registration"]

    assert report["success"] is False
    assert check["status"] == "FAIL"
    assert "data:unknown_data" in check["details"]["missing"]
    assert "execution:unknown_broker" in check["details"]["missing"]


# --- merged from test_production_rehearsal_report.py ---
import json

from scripts import build_production_rehearsal_report as rehearsal


def _write_config(tmp_path):
    payload = {
        "name": "live-rehearsal-test",
        "source": "live",
        "markets": [
            {
                "symbol": "BTC-USDT",
                "timeframe": "1m",
                "enabled": True,
                "provider": "okx_public",
            }
        ],
        "strategies": [
            {
                "symbol": "BTC-USDT",
                "strategy": "ma_crossover",
                "route": "default",
                "parameters": {"fast_window": 3, "slow_window": 5},
            }
        ],
        "risk": {
            "risk_plugin": "default",
            "kill_switch_enabled": True,
            "max_position_size": 0.1,
            "max_drawdown": 0.05,
        },
        "broker": {
            "mode": "live",
            "broker_plugin": "okx_broker",
            "account_id": "rehearsal-account",
            "settings": {
                "allow_live_orders": False,
                "require_manual_preflight": True,
                "api_key_env": "OKX_API_KEY",
                "api_secret_env": "OKX_API_SECRET",
                "api_passphrase_env": "OKX_API_PASSPHRASE",
            },
        },
        "metadata": {"contains_real_credentials": False},
    }
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def test_rehearsal_report_aggregates_preflight_connectivity_and_dry_run_skip(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")
    monkeypatch.setattr(
        rehearsal,
        "run_diagnostics",
        lambda **_: {
            "success": True,
            "failed_count": 0,
            "warning_count": 0,
            "proxy": {"enabled": True},
            "checks": [
                {
                    "exchange": "okx",
                    "scope": "public",
                    "status": "PASS",
                    "category": "ok",
                    "message": "public endpoint reachable",
                    "latency_ms": 12.3,
                }
            ],
        },
    )

    report = rehearsal.build_production_rehearsal_report(config_path, generated_at=1700000000)
    payload = report.to_payload()
    checks = {check["name"]: check for check in payload["checks"]}

    assert payload["success"] is True
    assert payload["metadata"]["contains_real_credentials"] is False
    assert payload["metadata"]["live_orders_sent"] is False
    assert payload["preflight_summary"]["failed_count"] == 0
    assert payload["connectivity_summary"]["check_count"] == 1
    assert checks["connectivity:okx:public"]["status"] == "PASS"
    assert checks["dry_run:report"]["status"] == "SKIPPED"


def test_rehearsal_report_fails_when_preflight_fails(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    monkeypatch.setattr(
        rehearsal,
        "run_diagnostics",
        lambda **_: {
            "success": True,
            "failed_count": 0,
            "warning_count": 0,
            "proxy": {"enabled": False},
            "checks": [],
        },
    )

    report = rehearsal.build_production_rehearsal_report(config_path, generated_at=1700000000)
    checks = {check.name: check for check in report.checks}

    assert report.success is False
    assert checks["preflight:qtf_environment"].status == "FAIL"


def test_rehearsal_report_can_summarize_supplied_dry_run_payload(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    dry_run_path = tmp_path / "dry-run.json"
    dry_run_path.write_text(
        json.dumps(
            {
                "context": {"run_id": "dry-run-1", "source": "live"},
                "success": False,
                "stages": [
                    {"stage": "data", "status": "succeeded"},
                    {"stage": "execution", "status": "rejected"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")

    report = rehearsal.build_production_rehearsal_report(
        config_path,
        run_connectivity=False,
        dry_run_report_path=dry_run_path,
        generated_at=1700000000,
    )

    assert report.success is False
    assert report.dry_run_summary["failed_stages"] == ["execution"]


def test_rehearsal_artifacts_write_stable_json_and_markdown(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    output_dir = tmp_path / "artifacts"
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")
    monkeypatch.setattr(
        rehearsal,
        "run_diagnostics",
        lambda **_: {
            "success": True,
            "failed_count": 0,
            "warning_count": 0,
            "proxy": {"enabled": True},
            "checks": [],
        },
    )

    report = rehearsal.build_production_rehearsal_report(config_path, generated_at=1700000000)
    paths = rehearsal.write_rehearsal_artifacts(report, output_dir)

    assert (output_dir / "production-rehearsal-1700000000.json").exists()
    assert (output_dir / "production-rehearsal-1700000000.md").exists()
    assert (output_dir / "latest.json").exists()
    assert (output_dir / "latest.md").exists()
    assert json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))["metadata"][
        "artifact_paths"
    ] == paths
    markdown = (output_dir / "latest.md").read_text(encoding="utf-8")
    assert "SmartQTF Production Rehearsal Report" in markdown
    assert "Failure Reasons" in markdown
    assert "contains_real_credentials=`False`" in markdown


def test_rehearsal_cli_writes_default_artifacts(tmp_path, monkeypatch, capsys):
    config_path = _write_config(tmp_path)
    output_dir = tmp_path / "cli-artifacts"
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "QTF")
    monkeypatch.setenv("SMARTQTF_USE_PROXY", "1")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "passphrase")
    monkeypatch.setattr(
        rehearsal,
        "run_diagnostics",
        lambda **_: {
            "success": True,
            "failed_count": 0,
            "warning_count": 0,
            "proxy": {"enabled": True},
            "checks": [],
        },
    )

    exit_code = rehearsal.main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--artifact-stem",
            "stable-rehearsal",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "stable-rehearsal.json").exists()
    assert (output_dir / "stable-rehearsal.md").exists()
    assert (output_dir / "latest.json").exists()
    assert (output_dir / "latest.md").exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["metadata"]["artifact_paths"]["json_path"].endswith("stable-rehearsal.json")
