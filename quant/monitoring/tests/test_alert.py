import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from quant.monitoring import (
    AlertJsonlWriter,
    HealthAlertEvaluator,
    HealthAlertSeverity,
    HealthAlertThresholds,
)
from quant.schemas import (
    BrokerOrderResult,
    ExchangeErrorCategory,
    OrderStatus,
    PayloadSource,
    RuntimeHealthSnapshot,
    RuntimeHealthStatus,
    TradeSide,
)


def make_snapshot(**overrides):
    payload = {
        "run_id": "paper-alert-001",
        "source": PayloadSource.PAPER,
        "observed_at": 1710000000,
        "status": RuntimeHealthStatus.DEGRADED,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "data_latency_ms": 6000,
        "order_failure_rate": 0.1,
        "risk_rejection_rate": 0.0,
        "broker_reconciliation_anomalies": 0,
        "kill_switch_active": False,
        "alerts": ["order_failure"],
    }
    payload.update(overrides)
    return RuntimeHealthSnapshot(**payload)


def test_health_alert_evaluator_detects_latency_order_failures_and_pnl():
    evaluator = HealthAlertEvaluator(
        thresholds=HealthAlertThresholds(
            max_api_latency_ms=5000,
            max_order_failure_rate=0.05,
            max_abs_pnl_change=100.0,
        )
    )

    alerts = evaluator.evaluate(make_snapshot(), pnl_change=-120.0)

    assert [alert.code for alert in alerts] == [
        "api_latency_high",
        "order_failure_rate_high",
        "pnl_change_abnormal",
    ]
    assert all(alert.severity == HealthAlertSeverity.WARNING for alert in alerts)
    assert alerts[0].symbol == "BTCUSDT"
    assert alerts[2].metadata["pnl_change"] == -120.0


def test_health_alert_evaluator_marks_critical_runtime_and_kill_switch():
    snapshot = make_snapshot(
        status=RuntimeHealthStatus.CRITICAL,
        alerts=["kill_switch_active"],
        kill_switch_active=True,
        data_latency_ms=20000,
        order_failure_rate=0.3,
    )

    alerts = HealthAlertEvaluator().evaluate(snapshot)

    codes = [alert.code for alert in alerts]
    assert "runtime_critical" in codes
    assert "kill_switch_active" in codes
    assert "api_latency_high" in codes
    assert "order_failure_rate_high" in codes
    assert all(alert.severity == HealthAlertSeverity.CRITICAL for alert in alerts)


def test_health_alert_evaluator_links_exchange_error_categories():
    retryable = BrokerOrderResult(
        client_order_id="cid-retry",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        status=OrderStatus.UNKNOWN,
        requested_qty=1.0,
        exchange_error_category=ExchangeErrorCategory.RETRYABLE,
        exchange_error_message="timeout",
    )
    fatal = BrokerOrderResult(
        client_order_id="cid-fatal",
        symbol="ETHUSDT",
        side=TradeSide.SELL,
        status=OrderStatus.REJECTED,
        requested_qty=1.0,
        exchange_error_category=ExchangeErrorCategory.CREDENTIAL_CONFIGURATION,
        exchange_error_message="invalid api key",
    )

    alerts = HealthAlertEvaluator().evaluate(
        make_snapshot(data_latency_ms=0, order_failure_rate=0.0),
        broker_order_results=[retryable, fatal],
    )

    assert [alert.code for alert in alerts] == [
        "exchange_error_retryable",
        "exchange_error_credential_configuration",
    ]
    assert alerts[0].severity == HealthAlertSeverity.WARNING
    assert alerts[1].severity == HealthAlertSeverity.CRITICAL
    assert alerts[1].symbol == "ETHUSDT"
    assert alerts[1].metadata["client_order_id"] == "cid-fatal"
    assert alerts[1].metadata["exchange_error_category"] == "credential_configuration"


def test_health_alert_evaluator_links_connectivity_diagnostics():
    connectivity_report = {
        "success": False,
        "failed_count": 1,
        "warning_count": 1,
        "proxy": {"enabled": True, "SMARTQTF_USE_PROXY": "1"},
        "checks": [
            {
                "exchange": "okx",
                "scope": "public",
                "status": "FAIL",
                "category": "dns",
                "message": "nodename nor servname provided",
            },
            {
                "exchange": "binance",
                "scope": "public",
                "status": "WARN",
                "category": "rate_limit",
                "message": "rate limit warning",
            },
            {
                "exchange": "okx",
                "scope": "private",
                "status": "PASS",
                "category": "ok",
                "message": "reachable",
            },
        ],
    }

    alerts = HealthAlertEvaluator().evaluate(
        make_snapshot(data_latency_ms=0, order_failure_rate=0.0),
        connectivity_report=connectivity_report,
    )

    assert [alert.code for alert in alerts] == ["connectivity_dns", "connectivity_rate_limit"]
    assert alerts[0].severity == HealthAlertSeverity.CRITICAL
    assert alerts[1].severity == HealthAlertSeverity.WARNING
    assert alerts[0].metadata["exchange"] == "okx"
    assert alerts[0].metadata["proxy"]["enabled"] is True


def test_alert_writer_persists_replayable_jsonl(tmp_path):
    path = tmp_path / "alerts.jsonl"
    writer = AlertJsonlWriter(path)
    seen = []
    evaluator = HealthAlertEvaluator(alert_writer=writer, notifier=seen.append)

    alerts = evaluator.evaluate(make_snapshot())
    restored = writer.read_all()

    assert len(alerts) == 2
    assert [alert.to_payload() for alert in restored] == [alert.to_payload() for alert in alerts]
    assert [alert.alert_id for alert in seen] == [alert.alert_id for alert in alerts]


def test_alert_thresholds_reject_invalid_values():
    try:
        HealthAlertThresholds(max_order_failure_rate=1.5)
    except ValidationError:
        pass
    else:
        raise AssertionError("order failure threshold must be a rate")
