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


class HealthAlertActionType(str, Enum):
    ENTER_SAFE_MODE = "enter_safe_mode"
    PAUSE_NEW_ENTRIES = "pause_new_entries"
    BLOCK_EXECUTION = "block_execution"
    REDUCE_EXPOSURE = "reduce_exposure"
    TRIGGER_KILL_SWITCH = "trigger_kill_switch"


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


class HealthAlertAction(SmartQTFModel):
    action_id: str
    run_id: str
    observed_at: int
    alert_id: str
    alert_code: str
    alert_severity: HealthAlertSeverity
    action_type: HealthAlertActionType
    reason: str
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    requires_human_ack: bool = True
    broker_called: bool = False
    live_orders_sent: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value):
        if not value:
            raise ValueError("value must not be empty")
        return value


if hasattr(BaseModel, "model_validate"):

    class HealthAlertAction(HealthAlertAction):
        @field_validator("action_id", "run_id", "alert_id", "alert_code", "reason")
        @classmethod
        def validate_required_string(cls, value):
            return cls.non_empty_string(value)

else:

    class HealthAlertAction(HealthAlertAction):
        @validator("action_id", "run_id", "alert_id", "alert_code", "reason")
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


class AlertActionJsonlWriter:
    def __init__(self, path):
        self.path = Path(path)

    def append_many(self, actions: Iterable[HealthAlertAction]) -> None:
        actions = list(actions)
        if not actions:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for action in actions:
                handle.write(json.dumps(action.to_payload(), sort_keys=True) + "\n")

    def read_all(self) -> List[HealthAlertAction]:
        if not self.path.exists():
            return []

        actions = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                actions.append(HealthAlertAction.from_payload(json.loads(line)))
        return actions


class HealthAlertActionPolicy:
    policy_name = "default_monitoring_alert_action_policy"

    def __init__(
        self,
        audit_writer: Optional[AlertActionJsonlWriter] = None,
        notifier: Optional[Callable[[HealthAlertAction], None]] = None,
    ):
        self.audit_writer = audit_writer
        self.notifier = notifier

    def evaluate(self, alerts: Iterable[HealthAlert]) -> List[HealthAlertAction]:
        actions = []
        for alert in alerts:
            for action_type in self._action_types_for_alert(alert):
                actions.append(self._action(alert, action_type))

        if self.audit_writer is not None:
            self.audit_writer.append_many(actions)
        if self.notifier is not None:
            for action in actions:
                self.notifier(action)
        return actions

    def _action_types_for_alert(self, alert: HealthAlert) -> List[HealthAlertActionType]:
        if alert.severity != HealthAlertSeverity.CRITICAL:
            return []

        code = alert.code
        metadata = alert.metadata or {}
        connectivity_category = str(metadata.get("connectivity_category") or "").lower()
        exchange_error_category = str(metadata.get("exchange_error_category") or "").lower()

        if code == "api_latency_high":
            return [HealthAlertActionType.ENTER_SAFE_MODE, HealthAlertActionType.PAUSE_NEW_ENTRIES]
        if code == "order_failure_rate_high":
            return [
                HealthAlertActionType.ENTER_SAFE_MODE,
                HealthAlertActionType.BLOCK_EXECUTION,
                HealthAlertActionType.TRIGGER_KILL_SWITCH,
            ]
        if code == "pnl_change_abnormal":
            return [
                HealthAlertActionType.ENTER_SAFE_MODE,
                HealthAlertActionType.REDUCE_EXPOSURE,
                HealthAlertActionType.TRIGGER_KILL_SWITCH,
            ]
        if code == "broker_reconciliation_anomaly":
            return [HealthAlertActionType.ENTER_SAFE_MODE, HealthAlertActionType.BLOCK_EXECUTION]
        if code == "runtime_critical":
            return [HealthAlertActionType.ENTER_SAFE_MODE, HealthAlertActionType.PAUSE_NEW_ENTRIES]
        if code == "kill_switch_active":
            return [HealthAlertActionType.BLOCK_EXECUTION]
        if code.startswith("exchange_error_"):
            return self._exchange_error_actions(exchange_error_category)
        if code.startswith("connectivity_"):
            return self._connectivity_actions(connectivity_category)
        return []

    def _exchange_error_actions(self, category: str) -> List[HealthAlertActionType]:
        if category == ExchangeErrorCategory.CREDENTIAL_CONFIGURATION.value:
            return [
                HealthAlertActionType.ENTER_SAFE_MODE,
                HealthAlertActionType.BLOCK_EXECUTION,
                HealthAlertActionType.TRIGGER_KILL_SWITCH,
            ]
        if category == ExchangeErrorCategory.FATAL.value:
            return [HealthAlertActionType.ENTER_SAFE_MODE, HealthAlertActionType.BLOCK_EXECUTION]
        return []

    def _connectivity_actions(self, category: str) -> List[HealthAlertActionType]:
        if category in {"credential", "configuration", "credential_configuration", "auth", "authentication"}:
            return [
                HealthAlertActionType.ENTER_SAFE_MODE,
                HealthAlertActionType.BLOCK_EXECUTION,
                HealthAlertActionType.TRIGGER_KILL_SWITCH,
            ]
        if category in {"ws_disconnect", "websocket_disconnect", "polling_failure", "rest_polling_failure"}:
            return [HealthAlertActionType.ENTER_SAFE_MODE, HealthAlertActionType.PAUSE_NEW_ENTRIES]
        return [HealthAlertActionType.ENTER_SAFE_MODE, HealthAlertActionType.PAUSE_NEW_ENTRIES]

    def _action(self, alert: HealthAlert, action_type: HealthAlertActionType) -> HealthAlertAction:
        return HealthAlertAction(
            action_id=f"{alert.alert_id}:{action_type.value}",
            run_id=alert.run_id,
            observed_at=alert.observed_at,
            alert_id=alert.alert_id,
            alert_code=alert.code,
            alert_severity=alert.severity,
            action_type=action_type,
            reason=f"{alert.code} {self._enum_value(alert.severity)} requires {action_type.value}",
            symbol=alert.symbol,
            timeframe=alert.timeframe,
            requires_human_ack=True,
            broker_called=False,
            live_orders_sent=False,
            metadata={
                "policy": self.policy_name,
                "source_alert": {
                    "code": alert.code,
                    "severity": self._enum_value(alert.severity),
                    "message": alert.message,
                    "value": alert.value,
                    "threshold": alert.threshold,
                    "metadata": self._redact_sensitive(alert.metadata),
                },
                "control_effect": self._control_effect(action_type),
            },
        )

    @staticmethod
    def _control_effect(action_type: HealthAlertActionType) -> Dict[str, bool]:
        return {
            "safe_mode": action_type == HealthAlertActionType.ENTER_SAFE_MODE,
            "pause_new_entries": action_type == HealthAlertActionType.PAUSE_NEW_ENTRIES,
            "block_execution": action_type == HealthAlertActionType.BLOCK_EXECUTION,
            "reduce_exposure": action_type == HealthAlertActionType.REDUCE_EXPOSURE,
            "kill_switch": action_type == HealthAlertActionType.TRIGGER_KILL_SWITCH,
            "broker_called": False,
            "live_orders_sent": False,
        }

    @classmethod
    def _redact_sensitive(cls, value):
        sensitive_markers = ("api_key", "apikey", "secret", "passphrase", "token", "authorization", "signature")
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                key_text = str(key).lower()
                if any(marker in key_text for marker in sensitive_markers):
                    redacted[key] = "***REDACTED***"
                else:
                    redacted[key] = cls._redact_sensitive(item)
            return redacted
        if isinstance(value, list):
            return [cls._redact_sensitive(item) for item in value]
        if isinstance(value, str):
            lowered = value.lower()
            if any(marker in lowered for marker in ("api_key=", "secret=", "passphrase=", "authorization:", "bearer ")):
                return "***REDACTED***"
        return value

    @staticmethod
    def _enum_value(value):
        return getattr(value, "value", value)


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
        alerts.extend(self._reconciliation_alerts(snapshot, metadata))
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

    def _reconciliation_alerts(self, snapshot, metadata):
        anomalies = snapshot.broker_reconciliation_anomalies
        if anomalies <= 0:
            return []
        return [
            self._alert(
                snapshot,
                "broker_reconciliation_anomaly",
                HealthAlertSeverity.CRITICAL,
                "broker reconciliation reported anomalies",
                anomalies,
                0.0,
                {**metadata, "broker_reconciliation_anomalies": anomalies},
            )
        ]

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
