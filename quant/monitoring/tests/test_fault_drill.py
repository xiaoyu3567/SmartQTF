import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.monitoring.fault_drill import (
    FAULT_DRILL_SCENARIOS,
    run_fault_drill,
    write_fault_drill_report,
)


def test_fault_drill_emits_alert_action_report_for_each_scenario():
    report = run_fault_drill(run_id="fault-drill-test", observed_at=1710000000)

    assert report.status == "PASS"
    assert report.scenario_count == 6
    assert report.passed_count == 6
    assert report.failed_count == 0
    assert {scenario.scenario_id for scenario in report.scenarios} == set(FAULT_DRILL_SCENARIOS)
    assert report.broker_called is False
    assert report.live_orders_sent is False
    assert report.contains_real_credentials is False

    for scenario in report.scenarios:
        assert scenario.report
        assert scenario.alerts
        assert scenario.actions
        assert scenario.passed is True
        assert scenario.reason_codes == [f"{scenario.scenario_id}:pass"]

        alert_codes = {alert["code"] for alert in scenario.alerts}
        action_types = {action["action_type"] for action in scenario.actions}

        assert set(scenario.report["expected_alert_codes"]).issubset(alert_codes)
        assert set(scenario.report["expected_action_types"]).issubset(action_types)
        assert all(action["broker_called"] is False for action in scenario.actions)
        assert all(action["live_orders_sent"] is False for action in scenario.actions)
        assert all(action["metadata"]["control_effect"]["broker_called"] is False for action in scenario.actions)
        assert all(action["metadata"]["control_effect"]["live_orders_sent"] is False for action in scenario.actions)


def test_fault_drill_proves_no_duplicate_order_path():
    report = run_fault_drill(run_id="fault-drill-duplicate-proof", observed_at=1710000000)

    for scenario in report.scenarios:
        proof = scenario.duplicate_order_proof
        assert proof["duplicate_order_guard_active"] is True
        assert proof["no_duplicate_order"] is True
        assert proof["place_order_calls"] == 0
        assert proof["cancel_order_calls"] == 0
        assert proof["replace_order_calls"] == 0
        assert proof["broker_called"] is False
        assert proof["live_orders_sent"] is False

    assert report.safety_assertions["all_duplicate_order_guards_active"] is True
    assert report.safety_assertions["all_no_duplicate_order"] is True
    assert report.safety_assertions["all_scenarios_emit_alerts"] is True
    assert report.safety_assertions["all_scenarios_emit_actions"] is True
    assert report.safety_assertions["all_scenarios_have_reports"] is True


def test_fault_drill_report_writes_replayable_latest_json(tmp_path):
    report = run_fault_drill(run_id="fault-drill-artifact-test", observed_at=1710000000)
    paths = write_fault_drill_report(report, tmp_path)

    report_path = Path(paths["report_path"])
    latest_path = Path(paths["latest_path"])

    assert report_path.exists()
    assert latest_path.exists()
    assert report_path.name == "fault-drill-artifact-test.json"

    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))
    report_payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert latest_payload == report_payload
    assert latest_payload["status"] == "PASS"
    assert latest_payload["scenario_count"] == 6
    assert latest_payload["broker_called"] is False
    assert latest_payload["live_orders_sent"] is False

    serialized = json.dumps(latest_payload).lower()
    assert "api_key" not in serialized
    assert "passphrase" not in serialized
    assert "authorization" not in serialized
    assert "bearer " not in serialized
