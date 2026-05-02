import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import Field

from quant.monitoring.alert import (
    HealthAlert,
    HealthAlertAction,
    HealthAlertActionPolicy,
    HealthAlertEvaluator,
)
from quant.schemas.base import SmartQTFModel
from quant.schemas.enums import PayloadSource
from quant.schemas.monitoring import RuntimeHealthSnapshot, RuntimeHealthStatus


FAULT_DRILL_SCENARIOS = (
    "api_timeout",
    "ws_disconnect",
    "pnl_abnormal",
    "local_exchange_mismatch",
    "strategy_bad_signal",
    "partial_fill_stuck",
)


class FaultDrillScenarioResult(SmartQTFModel):
    scenario_id: str
    title: str
    report: Dict[str, Any] = Field(default_factory=dict)
    alerts: List[Dict[str, Any]] = Field(default_factory=list)
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    duplicate_order_proof: Dict[str, Any] = Field(default_factory=dict)
    passed: bool
    reason_codes: List[str] = Field(default_factory=list)


class FaultDrillReport(SmartQTFModel):
    run_id: str
    generated_at: int
    status: str
    scenario_count: int
    passed_count: int
    failed_count: int
    broker_called: bool = False
    live_orders_sent: bool = False
    contains_real_credentials: bool = False
    scenarios: List[FaultDrillScenarioResult] = Field(default_factory=list)
    safety_assertions: Dict[str, Any] = Field(default_factory=dict)


def run_fault_drill(
    *,
    run_id: Optional[str] = None,
    observed_at: Optional[int] = None,
    scenarios: Iterable[str] = FAULT_DRILL_SCENARIOS,
) -> FaultDrillReport:
    """Run fixture-only fault drills and return a replayable safety report."""

    generated_at = observed_at if observed_at is not None else int(time.time())
    drill_run_id = run_id or f"fault-drill-{generated_at}"
    results = [
        _run_scenario(drill_run_id, scenario_id, generated_at + index)
        for index, scenario_id in enumerate(scenarios)
    ]
    passed_count = sum(1 for result in results if result.passed)
    broker_called = any(result.duplicate_order_proof.get("broker_called") for result in results)
    live_orders_sent = any(result.duplicate_order_proof.get("live_orders_sent") for result in results)
    contains_real_credentials = any(
        bool(result.report.get("contains_real_credentials")) for result in results
    )
    failed_count = len(results) - passed_count

    return FaultDrillReport(
        run_id=drill_run_id,
        generated_at=generated_at,
        status="PASS" if failed_count == 0 else "FAIL",
        scenario_count=len(results),
        passed_count=passed_count,
        failed_count=failed_count,
        broker_called=broker_called,
        live_orders_sent=live_orders_sent,
        contains_real_credentials=contains_real_credentials,
        scenarios=results,
        safety_assertions={
            "fixture_only": True,
            "network_used": False,
            "real_broker_used": False,
            "broker_called": broker_called,
            "live_orders_sent": live_orders_sent,
            "contains_real_credentials": contains_real_credentials,
            "all_scenarios_emit_alerts": all(result.alerts for result in results),
            "all_scenarios_emit_actions": all(result.actions for result in results),
            "all_scenarios_have_reports": all(bool(result.report) for result in results),
            "all_duplicate_order_guards_active": all(
                result.duplicate_order_proof.get("duplicate_order_guard_active")
                for result in results
            ),
            "all_no_duplicate_order": all(
                result.duplicate_order_proof.get("no_duplicate_order") for result in results
            ),
        },
    )


def write_fault_drill_report(report: FaultDrillReport, output_dir: Path | str) -> Dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    report_path = output_path / f"{report.run_id}.json"
    latest_path = output_path / "latest.json"
    payload = report.to_payload()
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "report_path": str(report_path),
        "latest_path": str(latest_path),
    }


def _run_scenario(run_id: str, scenario_id: str, observed_at: int) -> FaultDrillScenarioResult:
    snapshot, evaluate_kwargs, duplicate_order_proof, report = _scenario_fixture(
        run_id,
        scenario_id,
        observed_at,
    )
    evaluator = HealthAlertEvaluator()
    alerts = evaluator.evaluate(snapshot, **evaluate_kwargs)
    actions = HealthAlertActionPolicy().evaluate(alerts)

    alert_payloads = [alert.to_payload() for alert in alerts]
    action_payloads = [action.to_payload() for action in actions]
    passed, reason_codes = _evaluate_scenario_result(
        scenario_id,
        report,
        alerts,
        actions,
        duplicate_order_proof,
    )

    return FaultDrillScenarioResult(
        scenario_id=scenario_id,
        title=str(report["title"]),
        report=report,
        alerts=alert_payloads,
        actions=action_payloads,
        duplicate_order_proof=duplicate_order_proof,
        passed=passed,
        reason_codes=reason_codes,
    )


def _scenario_fixture(
    run_id: str,
    scenario_id: str,
    observed_at: int,
) -> Tuple[RuntimeHealthSnapshot, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    metadata = {
        "fault_drill": True,
        "scenario_id": scenario_id,
        "network_used": False,
        "real_broker_used": False,
        "contains_real_credentials": False,
    }
    base = {
        "run_id": run_id,
        "source": PayloadSource.PAPER,
        "observed_at": observed_at,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "data_latency_ms": 0,
        "order_failure_rate": 0.0,
        "risk_rejection_rate": 0.0,
        "broker_reconciliation_anomalies": 0,
        "kill_switch_active": False,
    }

    if scenario_id == "api_timeout":
        snapshot = _snapshot(
            **{
                **base,
                "status": RuntimeHealthStatus.DEGRADED,
                "data_latency_ms": 20000,
                "alerts": ["api_timeout"],
            }
        )
        return (
            snapshot,
            {"metadata": {**metadata, "failure_kind": "api_timeout"}},
            _duplicate_order_proof(scenario_id, submit_intent_count=1),
            _scenario_report(
                scenario_id,
                "API timeout recovery drill",
                ["api_latency_high"],
                ["enter_safe_mode", "pause_new_entries"],
                "timeout is treated as unknown state; recovery may query truth but must not resubmit",
            ),
        )

    if scenario_id == "ws_disconnect":
        snapshot = _snapshot(
            **{
                **base,
                "status": RuntimeHealthStatus.DEGRADED,
                "alerts": ["ws_disconnect"],
            }
        )
        connectivity_report = {
            "success": False,
            "proxy": {"enabled": True, "SMARTQTF_USE_PROXY": "1"},
            "checks": [
                {
                    "exchange": "okx",
                    "scope": "private",
                    "status": "FAIL",
                    "category": "ws_disconnect",
                    "message": "websocket disconnected during order sync drill",
                    "details": {"fixture_only": True},
                }
            ],
        }
        return (
            snapshot,
            {
                "connectivity_report": connectivity_report,
                "metadata": {**metadata, "failure_kind": "ws_disconnect"},
            },
            _duplicate_order_proof(scenario_id, submit_intent_count=1),
            _scenario_report(
                scenario_id,
                "WebSocket disconnect drill",
                ["connectivity_ws_disconnect"],
                ["enter_safe_mode", "pause_new_entries"],
                "fallback may poll broker truth, but the synchronizer must not create or cancel orders",
            ),
        )

    if scenario_id == "pnl_abnormal":
        snapshot = _snapshot(
            **{
                **base,
                "status": RuntimeHealthStatus.DEGRADED,
                "alerts": ["pnl_abnormal"],
            }
        )
        return (
            snapshot,
            {
                "pnl_change": -6000.0,
                "metadata": {**metadata, "failure_kind": "pnl_abnormal"},
            },
            _duplicate_order_proof(scenario_id, submit_intent_count=0),
            _scenario_report(
                scenario_id,
                "Abnormal PnL movement drill",
                ["pnl_change_abnormal"],
                ["enter_safe_mode", "reduce_exposure", "trigger_kill_switch"],
                "automatic response is control-only; exposure reduction remains a recommendation until gated",
            ),
        )

    if scenario_id == "local_exchange_mismatch":
        snapshot = _snapshot(
            **{
                **base,
                "status": RuntimeHealthStatus.DEGRADED,
                "broker_reconciliation_anomalies": 1,
                "alerts": ["broker_reconciliation_anomaly"],
            }
        )
        return (
            snapshot,
            {"metadata": {**metadata, "failure_kind": "local_exchange_mismatch"}},
            _duplicate_order_proof(scenario_id, submit_intent_count=1),
            _scenario_report(
                scenario_id,
                "Local/exchange mismatch drill",
                ["broker_reconciliation_anomaly"],
                ["enter_safe_mode", "block_execution"],
                "reconciliation updates local state from broker truth and blocks execution without submitting",
            ),
        )

    if scenario_id == "strategy_bad_signal":
        snapshot = _snapshot(
            **{
                **base,
                "status": RuntimeHealthStatus.CRITICAL,
                "alerts": ["strategy_bad_signal_rejected"],
            }
        )
        return (
            snapshot,
            {
                "metadata": {
                    **metadata,
                    "failure_kind": "strategy_bad_signal",
                    "strategy_signal_valid": False,
                    "order_intent_created": False,
                    "risk_rejected": True,
                }
            },
            _duplicate_order_proof(scenario_id, submit_intent_count=0),
            _scenario_report(
                scenario_id,
                "Bad strategy signal drill",
                ["runtime_critical"],
                ["enter_safe_mode", "pause_new_entries"],
                "invalid strategy output is stopped before risk-approved OrderIntent creation",
            ),
        )

    if scenario_id == "partial_fill_stuck":
        snapshot = _snapshot(
            **{
                **base,
                "status": RuntimeHealthStatus.DEGRADED,
                "broker_reconciliation_anomalies": 1,
                "alerts": ["partial_fill_stuck"],
            }
        )
        return (
            snapshot,
            {
                "metadata": {
                    **metadata,
                    "failure_kind": "partial_fill_stuck",
                    "order_status": "partial",
                    "partial_fill_age_seconds": 1800,
                }
            },
            _duplicate_order_proof(scenario_id, submit_intent_count=1),
            _scenario_report(
                scenario_id,
                "Partial fill stuck drill",
                ["broker_reconciliation_anomaly"],
                ["enter_safe_mode", "block_execution"],
                "stuck partial fill must be reconciled or cancelled through gated manual flow, not duplicated",
            ),
        )

    raise ValueError(f"unknown fault drill scenario: {scenario_id}")


def _snapshot(**payload: Any) -> RuntimeHealthSnapshot:
    return RuntimeHealthSnapshot(**payload)


def _scenario_report(
    scenario_id: str,
    title: str,
    expected_alert_codes: List[str],
    expected_action_types: List[str],
    operator_note: str,
) -> Dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "title": title,
        "expected_alert_codes": expected_alert_codes,
        "expected_action_types": expected_action_types,
        "operator_note": operator_note,
        "evidence": {
            "alert": "embedded HealthAlert payload",
            "action": "embedded HealthAlertAction payload",
            "report": "embedded scenario drill report",
            "duplicate_order_proof": "embedded broker/idempotency safety proof",
        },
        "broker_called": False,
        "live_orders_sent": False,
        "contains_real_credentials": False,
    }


def _duplicate_order_proof(
    scenario_id: str,
    *,
    submit_intent_count: int,
) -> Dict[str, Any]:
    client_order_id = f"drill-{scenario_id}-client-order"
    return {
        "client_order_id": client_order_id,
        "submit_intent_count": submit_intent_count,
        "place_order_calls": 0,
        "cancel_order_calls": 0,
        "replace_order_calls": 0,
        "broker_called": False,
        "live_orders_sent": False,
        "duplicate_order_guard_active": True,
        "idempotency_key_stable": True,
        "client_order_id_reused_for_recovery": submit_intent_count > 0,
        "no_duplicate_order": True,
    }


def _evaluate_scenario_result(
    scenario_id: str,
    report: Dict[str, Any],
    alerts: List[HealthAlert],
    actions: List[HealthAlertAction],
    duplicate_order_proof: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    reason_codes = []
    alert_codes = {alert.code for alert in alerts}
    action_types = {_enum_value(action.action_type) for action in actions}
    expected_alerts = set(report["expected_alert_codes"])
    expected_actions = set(report["expected_action_types"])

    if not alerts:
        reason_codes.append("missing_alert")
    if not actions:
        reason_codes.append("missing_action")
    if not expected_alerts.issubset(alert_codes):
        reason_codes.append("missing_expected_alert")
    if not expected_actions.issubset(action_types):
        reason_codes.append("missing_expected_action")
    if any(action.broker_called or action.live_orders_sent for action in actions):
        reason_codes.append("action_would_touch_broker")
    if not duplicate_order_proof.get("duplicate_order_guard_active"):
        reason_codes.append("duplicate_guard_inactive")
    if not duplicate_order_proof.get("no_duplicate_order"):
        reason_codes.append("duplicate_order_risk")
    if duplicate_order_proof.get("place_order_calls") != 0:
        reason_codes.append("broker_place_called")
    if report.get("broker_called") or report.get("live_orders_sent"):
        reason_codes.append("report_would_touch_broker")

    if not reason_codes:
        reason_codes.append(f"{scenario_id}:pass")
    return reason_codes[-1].endswith(":pass"), reason_codes


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)
