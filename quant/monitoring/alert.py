import json
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

try:
    from pydantic import field_validator
except ImportError:
    field_validator = None
    from pydantic import validator

from quant.schemas import BrokerOrderResult, ExchangeErrorCategory, RuntimeHealthSnapshot, RuntimeHealthStatus, SmartQTFModel


class HealthAlertSeverity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


class HealthAlertThresholds(SmartQTFModel):
    max_api_latency_ms: int = 5000
    max_order_failure_rate: float = 0.05
    max_abs_pnl_change: float = 1000.0
    critical_api_latency_ms: int = 15000
    critical_order_failure_rate: float = 0.2
    critical_abs_pnl_change: float = 5000.0

    @classmethod
    def non_negative_number(cls, value):
        if value < 0:
            raise ValueError("threshold values must be greater than or equal to 0")
        return value

    @classmethod
    def rate_between_zero_and_one(cls, value):
        if value < 0 or value > 1:
            raise ValueError("rate threshold must be between 0 and 1")
        return value


if hasattr(BaseModel, "model_validate"):

    class HealthAlertThresholds(HealthAlertThresholds):
        @field_validator("max_api_latency_ms", "critical_api_latency_ms")
        @classmethod
        def validate_latency(cls, value):
            return int(cls.non_negative_number(value))

        @field_validator("max_abs_pnl_change", "critical_abs_pnl_change")
        @classmethod
        def validate_pnl_threshold(cls, value):
            return float(cls.non_negative_number(value))

        @field_validator("max_order_failure_rate", "critical_order_failure_rate")
        @classmethod
        def validate_rate(cls, value):
            return cls.rate_between_zero_and_one(value)

else:

    class HealthAlertThresholds(HealthAlertThresholds):
        @validator("max_api_latency_ms", "critical_api_latency_ms")
        def validate_latency(cls, value):
            return int(cls.non_negative_number(value))

        @validator("max_abs_pnl_change", "critical_abs_pnl_change")
        def validate_pnl_threshold(cls, value):
            return float(cls.non_negative_number(value))

        @validator("max_order_failure_rate", "critical_order_failure_rate")
        def validate_rate(cls, value):
            return cls.rate_between_zero_and_one(value)


class HealthAlert(SmartQTFModel):
    alert_id: str
    run_id: str
    observed_at: int
    code: str
    severity: HealthAlertSeverity
    message: str
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    value: float = 0.0
    threshold: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value


if hasattr(BaseModel, "model_validate"):

    class HealthAlert(HealthAlert):
        @field_validator("alert_id", "run_id", "code", "message")
        @classmethod
        def validate_required_string(cls, value):
            return cls.non_empty_string(value)

else:

    class HealthAlert(HealthAlert):
        @validator("alert_id", "run_id", "code", "message")
        def validate_required_string(cls, value):
            return cls.non_empty_string(value)


class AlertJsonlWriter:
    def __init__(self, path):
        self.path = Path(path)

    def append_many(self, alerts: Iterable[HealthAlert]) -> None:
        alerts = list(alerts)
        if not alerts:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for alert in alerts:
                handle.write(json.dumps(alert.to_payload(), sort_keys=True) + "\n")

    def read_all(self) -> List[HealthAlert]:
        if not self.path.exists():
            return []

        alerts = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                alerts.append(HealthAlert.from_payload(json.loads(line)))
        return alerts


class HealthAlertEvaluator:
    def __init__(
        self,
        thresholds: Optional[HealthAlertThresholds] = None,
        alert_writer: Optional[AlertJsonlWriter] = None,
        notifier: Optional[Callable[[HealthAlert], None]] = None,
    ):
        self.thresholds = thresholds or HealthAlertThresholds()
        self.alert_writer = alert_writer
        self.notifier = notifier

    def evaluate(
        self,
        snapshot: RuntimeHealthSnapshot,
        *,
        broker_order_results: Optional[Iterable[BrokerOrderResult]] = None,
        connectivity_report: Optional[Dict[str, Any]] = None,
        pnl_change: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[HealthAlert]:
        metadata = metadata or {}
        alerts = []
        alerts.extend(self._runtime_snapshot_alerts(snapshot, metadata))
        alerts.extend(self._api_latency_alerts(snapshot, metadata))
        alerts.extend(self._order_failure_alerts(snapshot, metadata))
        alerts.extend(self._broker_order_result_alerts(snapshot, broker_order_results or [], metadata))
        if connectivity_report is not None:
            alerts.extend(self._connectivity_alerts(snapshot, connectivity_report, metadata))
        if pnl_change is not None:
            alerts.extend(self._pnl_alerts(snapshot, pnl_change, metadata))

        if self.alert_writer is not None:
            self.alert_writer.append_many(alerts)
        if self.notifier is not None:
            for alert in alerts:
                self.notifier(alert)
        return alerts

    def _runtime_snapshot_alerts(self, snapshot, metadata):
        alerts = []
        if snapshot.status == RuntimeHealthStatus.CRITICAL:
            alerts.append(
                self._alert(
                    snapshot,
                    "runtime_critical",
                    HealthAlertSeverity.CRITICAL,
                    "runtime health snapshot is critical",
                    value=1.0,
                    threshold=1.0,
                    metadata={**metadata, "snapshot_alerts": list(snapshot.alerts)},
                )
            )
        if snapshot.kill_switch_active:
            alerts.append(
                self._alert(
                    snapshot,
                    "kill_switch_active",
                    HealthAlertSeverity.CRITICAL,
                    "kill switch is active",
                    value=1.0,
                    threshold=1.0,
                    metadata=metadata,
                )
            )
        return alerts

    def _api_latency_alerts(self, snapshot, metadata):
        latency = snapshot.data_latency_ms
        if latency >= self.thresholds.critical_api_latency_ms:
            return [
                self._alert(
                    snapshot,
                    "api_latency_high",
                    HealthAlertSeverity.CRITICAL,
                    "api latency exceeded critical threshold",
                    latency,
                    self.thresholds.critical_api_latency_ms,
                    metadata,
                )
            ]
        if latency >= self.thresholds.max_api_latency_ms:
            return [
                self._alert(
                    snapshot,
                    "api_latency_high",
                    HealthAlertSeverity.WARNING,
                    "api latency exceeded warning threshold",
                    latency,
                    self.thresholds.max_api_latency_ms,
                    metadata,
                )
            ]
        return []

    def _order_failure_alerts(self, snapshot, metadata):
        failure_rate = snapshot.order_failure_rate
        if failure_rate >= self.thresholds.critical_order_failure_rate:
            return [
                self._alert(
                    snapshot,
                    "order_failure_rate_high",
                    HealthAlertSeverity.CRITICAL,
                    "order failure rate exceeded critical threshold",
                    failure_rate,
                    self.thresholds.critical_order_failure_rate,
                    metadata,
                )
            ]
        if failure_rate >= self.thresholds.max_order_failure_rate:
            return [
                self._alert(
                    snapshot,
                    "order_failure_rate_high",
                    HealthAlertSeverity.WARNING,
                    "order failure rate exceeded warning threshold",
                    failure_rate,
                    self.thresholds.max_order_failure_rate,
                    metadata,
                )
            ]
        return []

    def _pnl_alerts(self, snapshot, pnl_change, metadata):
        abs_change = abs(pnl_change)
        if abs_change >= self.thresholds.critical_abs_pnl_change:
            return [
                self._alert(
                    snapshot,
                    "pnl_change_abnormal",
                    HealthAlertSeverity.CRITICAL,
                    "absolute pnl change exceeded critical threshold",
                    abs_change,
                    self.thresholds.critical_abs_pnl_change,
                    {**metadata, "pnl_change": pnl_change},
                )
            ]
        if abs_change >= self.thresholds.max_abs_pnl_change:
            return [
                self._alert(
                    snapshot,
                    "pnl_change_abnormal",
                    HealthAlertSeverity.WARNING,
                    "absolute pnl change exceeded warning threshold",
                    abs_change,
                    self.thresholds.max_abs_pnl_change,
                    {**metadata, "pnl_change": pnl_change},
                )
            ]
        return []

    def _broker_order_result_alerts(self, snapshot, broker_order_results, metadata):
        alerts = []
        for result in broker_order_results:
            category = result.exchange_error_category
            if category is None:
                continue

            category_value = self._enum_value(category)
            severity = self._exchange_error_severity(category_value)
            alerts.append(
                self._alert(
                    snapshot,
                    f"exchange_error_{category_value}",
                    severity,
                    f"broker order returned {category_value} exchange error",
                    value=1.0,
                    threshold=1.0,
                    metadata={
                        **metadata,
                        "client_order_id": result.client_order_id,
                        "broker_order_id": result.broker_order_id,
                        "order_status": self._enum_value(result.status),
                        "rejection_code": result.rejection_code,
                        "rejection_reason": result.rejection_reason,
                        "exchange_error_category": category_value,
                        "exchange_error_message": result.exchange_error_message,
                    },
                    alert_key=f"exchange:{category_value}:{result.client_order_id}",
                    symbol=result.symbol or snapshot.symbol,
                )
            )
        return alerts

    def _connectivity_alerts(self, snapshot, connectivity_report, metadata):
        alerts = []
        for check in connectivity_report.get("checks", []):
            status = str(check.get("status", "")).upper()
            if status not in {"FAIL", "WARN"}:
                continue

            category = check.get("category") or "unknown"
            severity = HealthAlertSeverity.CRITICAL if status == "FAIL" else HealthAlertSeverity.WARNING
            alerts.append(
                self._alert(
                    snapshot,
                    f"connectivity_{category}",
                    severity,
                    check.get("message") or "connectivity diagnostics reported a problem",
                    value=1.0,
                    threshold=1.0,
                    metadata={
                        **metadata,
                        "exchange": check.get("exchange"),
                        "scope": check.get("scope"),
                        "connectivity_status": status,
                        "connectivity_category": category,
                        "latency_ms": check.get("latency_ms"),
                        "details": check.get("details", {}),
                        "proxy": connectivity_report.get("proxy", {}),
                    },
                    alert_key=f"connectivity:{check.get('exchange')}:{check.get('scope')}:{category}",
                )
            )
        return alerts

    @staticmethod
    def _exchange_error_severity(category):
        if category in {ExchangeErrorCategory.FATAL.value, ExchangeErrorCategory.CREDENTIAL_CONFIGURATION.value}:
            return HealthAlertSeverity.CRITICAL
        return HealthAlertSeverity.WARNING

    @staticmethod
    def _enum_value(value):
        return getattr(value, "value", value)

    def _alert(self, snapshot, code, severity, message, value, threshold, metadata, alert_key=None, symbol=None):
        return HealthAlert(
            alert_id=f"{snapshot.run_id}:{alert_key or code}:{snapshot.observed_at}:{severity}",
            run_id=snapshot.run_id,
            observed_at=snapshot.observed_at,
            code=code,
            severity=severity,
            message=message,
            symbol=symbol or snapshot.symbol,
            timeframe=snapshot.timeframe,
            value=float(value),
            threshold=float(threshold),
            metadata=metadata,
        )
