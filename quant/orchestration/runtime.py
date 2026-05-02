import json
import time
from pathlib import Path
from uuid import uuid4

from quant.account.account import CryptoAccount
from quant.analytics import DailyReviewReporter
from quant.config import RuntimeConfig, load_runtime_config
from quant.execution.bracket_orchestrator import BracketExecutionOrchestrator
from quant.data.providers.mock_provider import MockProvider
from quant.execution.broker import BrokerAdapter
from quant.execution.engine import ExecutionEngine
from quant.logging.jsonl import JsonlTradeLogger
from quant.logging.pipeline_report import PipelineReportStore
from quant.orchestration.paper import PaperTradingOrchestrator
from quant.regime.rule_detector import RuleBasedRegimeDetector
from quant.registry import PluginAlreadyRegistered, PluginKind, PluginNotFound, PluginRegistry, default_registry
from quant.risk.risk_manager import RiskManager
from quant.schemas import (
    PayloadSource,
    BrokerOrderRequest,
    BracketExecutionPlan,
    LiveOrderGateDecision,
    OrderStatus,
    PipelineRunContext,
    PipelineRunReport,
    PipelineRuntimeRequest,
    PipelineStageResult,
    PipelineStageStatus,
    RegimeKind,
)
from quant.strategy.ma_crossover import MACrossoverStrategy
from quant.strategy.router import RegimeStrategyRouter, SymbolRegimeStrategyRouter


class LiveOrderGate:
    """Runtime gate that must approve before any live broker place_order call."""

    DEFAULT_PREFLIGHT_ARTIFACT_PATH = "logs/production-rehearsals/latest.json"
    DEFAULT_PREFLIGHT_MAX_AGE_SECONDS = 24 * 60 * 60
    VALID_CREDENTIAL_MODES = {
        "env",
        "environment",
        "external_secrets",
        "live_env",
        "secret_manager",
        "vault",
    }

    def __init__(self, settings=None, *, risk_manager=None, clock=None, project_root=None):
        self.settings = dict(settings or {})
        self.risk_manager = risk_manager
        self.clock = clock or time.time
        self.project_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]

    def evaluate(self, order_intent, *, portfolio_allocation=None, dry_run=None):
        checked_at = int(self.clock())
        reason_codes = []
        metadata = {
            "client_order_id": order_intent.client_order_id,
            "symbol": order_intent.symbol,
        }

        live_mode_enabled = self._live_mode_enabled()
        if not live_mode_enabled:
            reason_codes.append("live_mode_not_enabled")

        allow_live_orders = self.settings.get("allow_live_orders") is True
        if not allow_live_orders:
            reason_codes.append("allow_live_orders_disabled")

        risk_approved = getattr(order_intent, "risk_approved", False) is True
        if not risk_approved:
            reason_codes.append("risk_approval_missing")

        portfolio_context = self._portfolio_allocation_context(order_intent, portfolio_allocation)
        metadata.update(portfolio_context["metadata"])
        portfolio_allocation_approved = portfolio_context["approved"]
        if not portfolio_context["present"]:
            reason_codes.append("portfolio_allocation_missing")
        elif not portfolio_allocation_approved:
            reason_codes.append("portfolio_allocation_not_approved")
        if portfolio_context["quantity_invalid"]:
            reason_codes.append("portfolio_allocated_quantity_invalid")
        if portfolio_context["quantity_mismatch"]:
            reason_codes.append("portfolio_allocated_quantity_mismatch")
        if portfolio_context["client_order_id_mismatch"]:
            reason_codes.append("portfolio_client_order_id_mismatch")

        dry_run_enabled = self._dry_run_enabled(dry_run)
        if dry_run_enabled:
            reason_codes.append("live_dry_run_enabled")

        credential_mode, credential_error = self._credential_mode()
        if credential_error is not None:
            reason_codes.append(credential_error)
        metadata["credential_mode"] = credential_mode

        require_manual_preflight = self.settings.get("require_manual_preflight") is True
        if not require_manual_preflight:
            reason_codes.append("manual_preflight_required")

        artifact_path = self._preflight_artifact_path()
        max_age_seconds, max_age_error = self._preflight_max_age_seconds()
        if max_age_error is not None:
            reason_codes.append(max_age_error)

        preflight_generated_at = None
        preflight_age_seconds = None
        if require_manual_preflight:
            artifact_payload, artifact_error = self._load_preflight_artifact(artifact_path)
            if artifact_error is not None:
                reason_codes.append(artifact_error)
            else:
                metadata["preflight_report_id"] = artifact_payload.get("report_id")
                preflight_generated_at = self._int_or_none(artifact_payload.get("generated_at"))
                if preflight_generated_at is None:
                    reason_codes.append("preflight_artifact_missing_generated_at")
                else:
                    preflight_age_seconds = max(0, checked_at - preflight_generated_at)
                    if max_age_seconds is not None and preflight_age_seconds > max_age_seconds:
                        reason_codes.append("preflight_artifact_expired")

                if artifact_payload.get("success") is not True:
                    reason_codes.append("preflight_artifact_not_successful")

                preflight_summary = artifact_payload.get("preflight_summary") or {}
                if int(preflight_summary.get("failed_count") or 0) > 0:
                    reason_codes.append("preflight_checks_failed")

                artifact_metadata = artifact_payload.get("metadata") or {}
                if artifact_metadata.get("live_orders_sent") is not False:
                    reason_codes.append("preflight_artifact_live_orders_not_proven_false")
                if artifact_metadata.get("contains_real_credentials") is True:
                    reason_codes.append("preflight_artifact_contains_real_credentials")

        kill_switch_active = False
        if self.risk_manager is None:
            reason_codes.append("kill_switch_health_unavailable")
        else:
            kill_switch_active = bool(getattr(self.risk_manager, "kill_switch_enabled", False))
            if kill_switch_active:
                reason_codes.append("kill_switch_active")

        approved = not reason_codes
        if approved:
            reason_codes = ["live_order_gate_approved"]
        return LiveOrderGateDecision(
            approved=approved,
            reason_codes=reason_codes,
            message=("live order gate approved" if approved else "live order gate rejected order"),
            checked_at=checked_at,
            live_mode_enabled=live_mode_enabled,
            allow_live_orders=allow_live_orders,
            risk_approved=risk_approved,
            portfolio_allocation_approved=portfolio_allocation_approved,
            dry_run=dry_run_enabled,
            credential_mode=credential_mode,
            preflight_artifact_path=str(artifact_path),
            preflight_generated_at=preflight_generated_at,
            preflight_artifact_age_seconds=preflight_age_seconds,
            preflight_max_age_seconds=max_age_seconds,
            kill_switch_active=kill_switch_active,
            metadata=metadata,
        )

    def _preflight_artifact_path(self):
        configured_path = (
            self.settings.get("preflight_artifact_path")
            or self.settings.get("manual_preflight_artifact_path")
            or self.DEFAULT_PREFLIGHT_ARTIFACT_PATH
        )
        artifact_path = Path(configured_path)
        if not artifact_path.is_absolute():
            artifact_path = self.project_root / artifact_path
        return artifact_path

    def _preflight_max_age_seconds(self):
        value = (
            self.settings.get("preflight_max_age_seconds")
            or self.settings.get("manual_preflight_max_age_seconds")
            or self.DEFAULT_PREFLIGHT_MAX_AGE_SECONDS
        )
        try:
            max_age_seconds = int(value)
        except (TypeError, ValueError):
            return None, "preflight_max_age_invalid"
        if max_age_seconds <= 0:
            return None, "preflight_max_age_invalid"
        return max_age_seconds, None

    @staticmethod
    def _load_preflight_artifact(artifact_path):
        try:
            with artifact_path.open("r", encoding="utf-8") as handle:
                return json.load(handle), None
        except FileNotFoundError:
            return None, "preflight_artifact_missing"
        except (OSError, json.JSONDecodeError):
            return None, "preflight_artifact_unreadable"

    @staticmethod
    def _int_or_none(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _live_mode_enabled(self):
        if self._explicit_true(self.settings.get("live_mode")):
            return True
        for key in ("runtime_source", "source", "broker_mode", "mode"):
            value = self.settings.get(key)
            if hasattr(value, "value"):
                value = value.value
            if str(value).strip().lower() == "live":
                return True
        return False

    def _dry_run_enabled(self, dry_run):
        if dry_run is not None:
            return bool(dry_run)
        return not self._explicit_false(self.settings.get("dry_run"))

    def _credential_mode(self):
        raw_mode = self.settings.get("credential_mode")
        if raw_mode is None:
            return "missing", "credential_mode_missing"
        mode = str(raw_mode).strip().lower()
        if mode in self.VALID_CREDENTIAL_MODES:
            return mode, None
        return mode or "missing", "credential_mode_invalid"

    def _portfolio_allocation_context(self, order_intent, portfolio_allocation):
        metadata = {}
        if portfolio_allocation is None:
            return {
                "present": False,
                "approved": False,
                "quantity_invalid": False,
                "quantity_mismatch": False,
                "client_order_id_mismatch": False,
                "metadata": metadata,
            }

        allocation_id = self._field(portfolio_allocation, "allocation_id")
        risk_decision_id = self._field(portfolio_allocation, "risk_decision_id")
        approved = self._field(portfolio_allocation, "approved", False) is True
        allocated_quantity = self._field(portfolio_allocation, "allocated_quantity")
        if allocated_quantity is None:
            allocated_quantity = self._field(portfolio_allocation, "quantity")
        client_order_id = self._field(portfolio_allocation, "client_order_id")

        matching_allocation = self._matching_allocation(order_intent, portfolio_allocation)
        if matching_allocation is not None:
            approved = self._field(matching_allocation, "approved", approved) is True
            allocated_quantity = self._field(matching_allocation, "allocated_quantity", allocated_quantity)
            client_order_id = self._field(matching_allocation, "client_order_id", client_order_id)
            risk_decision_id = self._field(matching_allocation, "risk_decision_id", risk_decision_id)

        metadata["portfolio_allocation_id"] = allocation_id
        metadata["portfolio_client_order_id"] = client_order_id
        metadata["portfolio_allocated_quantity"] = allocated_quantity
        metadata["risk_decision_id"] = risk_decision_id

        quantity_value = self._float_or_none(allocated_quantity)
        quantity_invalid = quantity_value is None or quantity_value <= 0.0
        quantity_mismatch = False
        if not quantity_invalid:
            quantity_mismatch = abs(quantity_value - float(order_intent.quantity)) > 1e-9
        client_order_id_mismatch = client_order_id not in (None, order_intent.client_order_id)

        return {
            "present": True,
            "approved": approved and not quantity_invalid and not quantity_mismatch and not client_order_id_mismatch,
            "quantity_invalid": quantity_invalid,
            "quantity_mismatch": quantity_mismatch,
            "client_order_id_mismatch": client_order_id_mismatch,
            "metadata": metadata,
        }

    def _matching_allocation(self, order_intent, portfolio_allocation):
        allocations = self._field(portfolio_allocation, "allocations", [])
        for allocation in allocations or []:
            if self._field(allocation, "client_order_id") == order_intent.client_order_id:
                return allocation
        return None

    @staticmethod
    def _field(source, name, default=None):
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)

    @staticmethod
    def _float_or_none(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _explicit_true(value):
        if value is True:
            return True
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _explicit_false(value):
        if value is False:
            return True
        return str(value).strip().lower() in {"0", "false", "no", "off"}


class BrokerExecutionHandler:
    """Adapts a live BrokerAdapter to the orchestrator execution contract."""

    production_entrypoint = "execution_order_plan_bracket_orchestrator_v1"

    def __init__(
        self,
        broker: BrokerAdapter,
        live_order_gate=None,
        *,
        bracket_orchestrator=None,
        order_store=None,
        idempotency_registry=None,
    ):
        self.broker = broker
        self.live_order_gate = live_order_gate or LiveOrderGate()
        self.bracket_orchestrator = bracket_orchestrator or BracketExecutionOrchestrator(
            broker,
            self.live_order_gate,
            order_store=order_store,
            idempotency_registry=idempotency_registry,
        )

    def on_execution_order_plan(
        self,
        execution_order_plan,
        price,
        index,
        *,
        portfolio_allocation=None,
        dry_run=None,
        exchange_readiness_request=None,
    ):
        plan = self._coerce_execution_plan(execution_order_plan)
        if exchange_readiness_request is None:
            exchange_readiness_request = self._exchange_readiness_request_from_plan(plan)
        result = self.bracket_orchestrator.execute(
            plan,
            portfolio_allocation=portfolio_allocation,
            dry_run=dry_run,
            reference_price=price,
            exchange_readiness_request=exchange_readiness_request,
        )
        return self._order_result_from_bracket_result(plan, result)

    def on_order_intent(self, order_intent, price, index, *, portfolio_allocation=None, dry_run=None):
        gate_decision = self.live_order_gate.evaluate(
            order_intent,
            portfolio_allocation=portfolio_allocation,
            dry_run=dry_run,
        )
        if not gate_decision.approved:
            return self._blocked_by_live_order_gate(order_intent, gate_decision)
        if not getattr(order_intent, "reduce_only", False):
            return self._blocked_legacy_single_order_path(order_intent, gate_decision)

        request = BrokerOrderRequest(
            client_order_id=order_intent.client_order_id,
            symbol=order_intent.symbol,
            side=order_intent.side,
            order_type=order_intent.order_type,
            quantity=order_intent.quantity,
            limit_price=order_intent.limit_price,
            time_in_force=order_intent.time_in_force,
            reduce_only=order_intent.reduce_only,
            trace=order_intent.trace,
        )
        result = self.broker.place_order(request)
        remaining_qty = max(0.0, result.requested_qty - result.filled_qty)
        return {
            "order_id": result.broker_order_id or result.client_order_id,
            "broker_order_id": result.broker_order_id,
            "client_order_id": result.client_order_id,
            "symbol": result.symbol,
            "side": result.side.value if hasattr(result.side, "value") else result.side,
            "status": result.status.value if hasattr(result.status, "value") else result.status,
            "filled_qty": result.filled_qty,
            "remaining_qty": remaining_qty,
            "fill_price": result.avg_fill_price,
            "rejection_code": result.rejection_code,
            "rejection_reason": result.rejection_reason,
            "live_order_gate": gate_decision.to_payload(),
            "broker_called": True,
            "live_orders_sent": True,
            **self._portfolio_execution_fields(gate_decision),
        }

    @staticmethod
    def _blocked_legacy_single_order_path(order_intent, gate_decision):
        reason_codes = [
            "execution_order_plan_required",
            "legacy_single_order_path_deprecated",
        ]
        return {
            "order_id": f"blocked:{order_intent.client_order_id}",
            "broker_order_id": None,
            "client_order_id": order_intent.client_order_id,
            "symbol": order_intent.symbol,
            "side": order_intent.side.value if hasattr(order_intent.side, "value") else order_intent.side,
            "status": OrderStatus.REJECTED.value,
            "filled_qty": 0.0,
            "remaining_qty": order_intent.quantity,
            "fill_price": None,
            "rejection_code": "execution_order_plan_required",
            "rejection_reason": ", ".join(reason_codes),
            "live_order_gate": gate_decision.to_payload(),
            "broker_called": False,
            "live_orders_sent": False,
            "production_entrypoint": BrokerExecutionHandler.production_entrypoint,
            "legacy_single_order_path_deprecated": True,
            "reason_codes": reason_codes,
            **BrokerExecutionHandler._portfolio_execution_fields(gate_decision),
        }

    @staticmethod
    def _blocked_by_live_order_gate(order_intent, gate_decision):
        return {
            "order_id": f"blocked:{order_intent.client_order_id}",
            "broker_order_id": None,
            "client_order_id": order_intent.client_order_id,
            "symbol": order_intent.symbol,
            "side": order_intent.side.value if hasattr(order_intent.side, "value") else order_intent.side,
            "status": OrderStatus.REJECTED.value,
            "filled_qty": 0.0,
            "remaining_qty": order_intent.quantity,
            "fill_price": None,
            "rejection_code": "live_order_gate_rejected",
            "rejection_reason": ", ".join(gate_decision.reason_codes),
            "live_order_gate": gate_decision.to_payload(),
            "broker_called": False,
            "live_orders_sent": False,
            **BrokerExecutionHandler._portfolio_execution_fields(gate_decision),
        }

    @staticmethod
    def _coerce_execution_plan(execution_order_plan):
        if isinstance(execution_order_plan, BracketExecutionPlan):
            return execution_order_plan
        return BracketExecutionPlan.from_payload(execution_order_plan)

    @staticmethod
    def _exchange_readiness_request_from_plan(plan):
        metadata = dict(plan.metadata or {})
        return metadata.get("exchange_readiness_request")

    def _order_result_from_bracket_result(self, plan, result):
        enriched = dict(result)
        entry_result = dict(enriched.get("entry_order_result") or {})
        bracket_status = enriched.get("status")
        bracket_safety_flags = dict(enriched.get("safety_flags") or {})
        metadata = dict(enriched.get("metadata") or {})
        live_gate = dict(enriched.get("live_order_gate") or {})
        live_gate_metadata = dict(live_gate.get("metadata") or {})
        requested_qty = self._float_or_none(entry_result.get("requested_qty"))
        filled_qty = self._float_or_none(entry_result.get("filled_qty"))
        if requested_qty is None:
            requested_qty = float(plan.entry_order.quantity)
        if filled_qty is None:
            filled_qty = 0.0

        if entry_result.get("status") is not None:
            enriched["status"] = entry_result["status"]
        elif bracket_status == "REJECTED":
            enriched["status"] = OrderStatus.REJECTED.value
        elif bracket_status == "CANCELLED_NOT_FILLED":
            enriched["status"] = OrderStatus.CANCELLED.value
        enriched.setdefault("order_id", entry_result.get("broker_order_id") or plan.entry_order.client_order_id)
        enriched.setdefault("broker_order_id", entry_result.get("broker_order_id"))
        enriched.setdefault("client_order_id", plan.entry_order.client_order_id)
        enriched.setdefault("symbol", plan.entry_order.symbol)
        enriched.setdefault(
            "side",
            plan.entry_order.side.value if hasattr(plan.entry_order.side, "value") else plan.entry_order.side,
        )
        enriched.setdefault("filled_qty", filled_qty)
        enriched.setdefault("remaining_qty", max(0.0, requested_qty - filled_qty))
        enriched.setdefault("fill_price", entry_result.get("avg_fill_price"))
        rejection_code = entry_result.get("rejection_code")
        if rejection_code is None and bracket_status == "REJECTED":
            live_gate = enriched.get("live_order_gate") or {}
            if live_gate.get("approved") is False:
                rejection_code = "live_order_gate_rejected"
            else:
                reason_codes = list(enriched.get("reason_codes") or [])
                rejection_code = reason_codes[0] if reason_codes else "bracket_execution_rejected"
        enriched.setdefault("rejection_code", rejection_code)
        enriched.setdefault(
            "rejection_reason",
            entry_result.get("rejection_reason")
            or ", ".join(enriched.get("reason_codes") or []),
        )
        enriched["bracket_execution_status"] = bracket_status
        enriched["bracket_safety_flags"] = bracket_safety_flags
        enriched["production_entrypoint"] = self.production_entrypoint
        enriched["legacy_single_order_path_deprecated"] = True
        self._attach_bracket_portfolio_fields(enriched, metadata, live_gate_metadata)
        return enriched

    @staticmethod
    def _float_or_none(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _attach_bracket_portfolio_fields(enriched, metadata, live_gate_metadata):
        allocation_id = (
            enriched.get("allocation_id")
            or metadata.get("portfolio_allocation_id")
            or live_gate_metadata.get("portfolio_allocation_id")
        )
        allocated_quantity = (
            metadata.get("portfolio_allocated_quantity")
            if metadata.get("portfolio_allocated_quantity") is not None
            else live_gate_metadata.get("portfolio_allocated_quantity")
        )
        portfolio_client_order_id = (
            metadata.get("portfolio_client_order_id")
            or live_gate_metadata.get("portfolio_client_order_id")
        )
        risk_decision_id = (
            enriched.get("risk_decision_id")
            or metadata.get("portfolio_risk_decision_id")
            or live_gate_metadata.get("risk_decision_id")
        )
        fields = {
            "allocation_id": allocation_id,
            "portfolio_allocation_id": allocation_id,
            "allocated_quantity": allocated_quantity,
            "portfolio_allocated_quantity": allocated_quantity,
            "portfolio_client_order_id": portfolio_client_order_id,
            "risk_decision_id": risk_decision_id,
        }
        for key, value in fields.items():
            if value is not None:
                enriched[key] = value

    @staticmethod
    def _portfolio_execution_fields(gate_decision):
        metadata = dict(getattr(gate_decision, "metadata", {}) or {})
        allocation_id = metadata.get("portfolio_allocation_id")
        allocated_quantity = metadata.get("portfolio_allocated_quantity")
        fields = {
            "allocation_id": allocation_id,
            "portfolio_allocation_id": allocation_id,
            "portfolio_approved": gate_decision.portfolio_allocation_approved,
            "allocated_quantity": allocated_quantity,
            "portfolio_allocated_quantity": allocated_quantity,
            "portfolio_client_order_id": metadata.get("portfolio_client_order_id"),
            "risk_decision_id": metadata.get("risk_decision_id"),
        }
        return {key: value for key, value in fields.items() if value is not None}


class LiveDryRunExecutionHandler:
    """Execution handler for live dry-runs that never sends broker orders."""

    def __init__(self, broker_plugin="dry_run"):
        self.broker_plugin = broker_plugin
        self.requests = []

    def on_order_intent(self, order_intent, price, index):
        self.requests.append(
            {
                "client_order_id": order_intent.client_order_id,
                "symbol": order_intent.symbol,
                "side": order_intent.side.value if hasattr(order_intent.side, "value") else order_intent.side,
                "quantity": order_intent.quantity,
                "index": index,
            }
        )
        return {
            "order_id": f"dry-run:{order_intent.client_order_id}",
            "broker_order_id": None,
            "client_order_id": order_intent.client_order_id,
            "symbol": order_intent.symbol,
            "side": order_intent.side.value if hasattr(order_intent.side, "value") else order_intent.side,
            "status": OrderStatus.ACCEPTED.value,
            "filled_qty": 0.0,
            "remaining_qty": order_intent.quantity,
            "fill_price": None,
            "rejection_code": None,
            "rejection_reason": None,
            "dry_run": True,
            "broker_called": False,
            "live_orders_sent": False,
            "broker_plugin": self.broker_plugin,
        }


class TradingRuntimeOrchestrator:
    """Single entrypoint for backtest, paper, and live runtime modes."""

    def __init__(
        self,
        handlers=None,
        *,
        daily_review_reporter=None,
        daily_review_log_path=None,
        daily_review_output_dir=None,
        pipeline_report_dir=None,
    ):
        self.handlers = dict(handlers or {})
        self.daily_review_reporter = daily_review_reporter or DailyReviewReporter()
        self.daily_review_log_path = Path(daily_review_log_path) if daily_review_log_path else None
        self.daily_review_output_dir = Path(daily_review_output_dir) if daily_review_output_dir else None
        self.pipeline_report_store = PipelineReportStore(pipeline_report_dir) if pipeline_report_dir else None

    @classmethod
    def from_shared_pipeline(
        cls,
        *,
        backtest=None,
        paper=None,
        live=None,
        daily_review_reporter=None,
        daily_review_log_path=None,
        daily_review_output_dir=None,
        pipeline_report_dir=None,
    ):
        return cls(
            handlers={
                PayloadSource.BACKTEST: backtest,
                PayloadSource.PAPER: paper,
                PayloadSource.LIVE: live,
            },
            daily_review_reporter=daily_review_reporter,
            daily_review_log_path=daily_review_log_path,
            daily_review_output_dir=daily_review_output_dir,
            pipeline_report_dir=pipeline_report_dir,
        )

    @classmethod
    def with_default_paper(cls, **paper_kwargs):
        runtime_kwargs = cls._pop_runtime_kwargs(paper_kwargs)
        return cls.from_shared_pipeline(
            paper=PaperTradingOrchestrator(**paper_kwargs),
            **runtime_kwargs,
        )

    @classmethod
    def with_default_simulation(cls, *, backtest_provider=None, paper_provider=None, **shared_kwargs):
        runtime_kwargs = cls._pop_runtime_kwargs(shared_kwargs)
        backtest_kwargs = dict(shared_kwargs)
        paper_kwargs = dict(shared_kwargs)
        if backtest_provider is not None:
            backtest_kwargs["provider"] = backtest_provider
        if paper_provider is not None:
            paper_kwargs["provider"] = paper_provider

        return cls.from_shared_pipeline(
            backtest=PaperTradingOrchestrator(source=PayloadSource.BACKTEST, **backtest_kwargs),
            paper=PaperTradingOrchestrator(source=PayloadSource.PAPER, **paper_kwargs),
            **runtime_kwargs,
        )

    @classmethod
    def from_config(cls, config, *, registry=None):
        runtime_config = cls._runtime_config(config)
        plugin_registry = registry or default_registry
        cls._register_builtin_plugins(plugin_registry)
        cls._validate_configured_runtime(runtime_config)

        account = CryptoAccount(initial_balance=runtime_config.metadata.get("initial_balance", 10000.0))
        provider = cls._build_provider(runtime_config, plugin_registry)
        strategy_router = cls._build_strategy_router(runtime_config, plugin_registry)
        risk_manager = cls._build_risk_manager(runtime_config, plugin_registry)
        execution_engine = cls._build_execution_engine(runtime_config, plugin_registry, account, risk_manager)
        paper_kwargs = {
            "provider": provider,
            "feature_windows": cls._feature_windows(runtime_config),
            "regime_detector": plugin_registry.create(PluginKind.FEATURE, "rule_regime_detector"),
            "strategy_router": strategy_router,
            "risk_manager": risk_manager,
            "execution_engine": execution_engine,
            "account": account,
            "source": runtime_config.source,
            "multi_timeframe_config": runtime_config.multi_timeframe,
        }
        handler = PaperTradingOrchestrator(**paper_kwargs)

        return cls.from_shared_pipeline(
            backtest=handler if runtime_config.source == PayloadSource.BACKTEST else None,
            paper=handler if runtime_config.source == PayloadSource.PAPER else None,
            live=handler if runtime_config.source == PayloadSource.LIVE else None,
            pipeline_report_dir=runtime_config.logging.pipeline_report_dir,
        )

    @classmethod
    def from_config_file(cls, path, *, registry=None):
        return cls.from_config(load_runtime_config(path), registry=registry)

    @classmethod
    def from_config_dry_run(cls, config, *, registry=None):
        runtime_config = cls._runtime_config(config)
        plugin_registry = registry or default_registry
        cls._register_builtin_plugins(plugin_registry)
        cls._validate_live_dry_run_config(runtime_config)

        account = CryptoAccount(initial_balance=runtime_config.metadata.get("initial_balance", 10000.0))
        provider = cls._build_provider(runtime_config, plugin_registry)
        strategy_router = cls._build_strategy_router(runtime_config, plugin_registry)
        risk_manager = cls._build_risk_manager(runtime_config, plugin_registry)
        execution_engine = LiveDryRunExecutionHandler(runtime_config.broker.broker_plugin)
        handler = PaperTradingOrchestrator(
            provider=provider,
            feature_windows=cls._feature_windows(runtime_config),
            regime_detector=plugin_registry.create(PluginKind.FEATURE, "rule_regime_detector"),
            strategy_router=strategy_router,
            risk_manager=risk_manager,
            execution_engine=execution_engine,
            account=account,
            source=PayloadSource.LIVE,
            multi_timeframe_config=runtime_config.multi_timeframe,
        )
        runtime = cls.from_shared_pipeline(
            live=handler,
            pipeline_report_dir=runtime_config.logging.pipeline_report_dir,
        )
        runtime.dry_run_execution_handler = execution_engine
        return runtime

    @classmethod
    def from_config_file_dry_run(cls, path, *, registry=None):
        return cls.from_config_dry_run(load_runtime_config(path), registry=registry)

    def register(self, source, handler):
        self.handlers[PayloadSource(source)] = handler

    def run(self, request):
        request = self._request(request)
        handler = self.handlers.get(PayloadSource(request.source))
        source = PayloadSource(request.source)
        run_id = request.run_id or f"{source.value}-{uuid4().hex}"
        if handler is None:
            return self._persist_pipeline_report(self._unsupported_report(request, run_id))

        report = handler.run_tick(
            symbol=request.symbol,
            timeframe=request.timeframe,
            index=request.index,
            run_id=run_id,
        )
        report = self._attach_daily_review_if_requested(report, request)
        return self._persist_pipeline_report(report)

    def _request(self, request):
        if isinstance(request, PipelineRuntimeRequest):
            return request
        if hasattr(request, "symbol") and hasattr(request, "timeframe"):
            return PipelineRuntimeRequest(
                source=getattr(request, "source", PayloadSource.PAPER),
                symbol=request.symbol,
                timeframe=request.timeframe,
                index=getattr(request, "index", None),
                run_id=getattr(request, "run_id", None),
                metadata=getattr(request, "metadata", {}),
            )
        return PipelineRuntimeRequest.from_payload(request)

    def _unsupported_report(self, request, run_id):
        message = f"{request.source} runtime handler is not configured"
        timestamp = request.metadata.get("requested_at", 0)
        return PipelineRunReport(
            context=PipelineRunContext(
                run_id=run_id,
                source=request.source,
                symbol=request.symbol,
                timeframe=request.timeframe,
                started_at=timestamp,
                metadata={"runtime_entrypoint": "TradingRuntimeOrchestrator"},
            ),
            stages=[
                PipelineStageResult(
                    stage="orchestration",
                    status=PipelineStageStatus.ERROR,
                    started_at=timestamp,
                    ended_at=timestamp,
                    input_payload=request.to_payload(),
                    error=message,
                )
            ],
            finished_at=timestamp,
            success=False,
            final_output={"symbol": request.symbol, "timeframe": request.timeframe},
            errors=[message],
            metadata={"runtime_entrypoint": "TradingRuntimeOrchestrator"},
        )

    def _attach_daily_review_if_requested(self, report, request):
        if not request.metadata.get("daily_close"):
            return report
        if self.daily_review_log_path is None:
            return self._with_daily_review_metadata(
                report,
                {
                    "status": "skipped",
                    "reason": "daily_review_log_path is not configured",
                },
            )

        records = JsonlTradeLogger(self.daily_review_log_path).read_all()
        generated_at = request.metadata.get("review_generated_at", report.finished_at)
        trading_date = request.metadata.get("trading_date")
        report_id = request.metadata.get("review_id") or f"{report.context.run_id}:daily-review"
        review = self.daily_review_reporter.build_report(
            records,
            report_id=report_id,
            run_id=report.context.run_id,
            trading_date=trading_date,
            generated_at=generated_at,
        )
        review_metadata = {
            "status": "generated",
            "report": review.to_payload(),
            "record_count": len(records),
        }
        if self.daily_review_output_dir is not None:
            review_metadata.update(self._write_daily_review(report.context.run_id, review))
        return self._with_daily_review_metadata(report, review_metadata)

    def _write_daily_review(self, run_id, review):
        self.daily_review_output_dir.mkdir(parents=True, exist_ok=True)
        safe_run_id = str(run_id).replace("/", "_").replace(":", "_")
        json_path = self.daily_review_output_dir / f"{safe_run_id}-daily-review.json"
        markdown_path = self.daily_review_output_dir / f"{safe_run_id}-daily-review.md"
        json_path.write_text(
            json.dumps(review.to_payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        markdown_path.write_text(review.summary_text, encoding="utf-8")
        return {
            "json_path": str(json_path),
            "summary_path": str(markdown_path),
        }

    def _with_daily_review_metadata(self, report, daily_review_metadata):
        metadata = dict(report.metadata)
        metadata["daily_review"] = daily_review_metadata
        if hasattr(report, "model_copy"):
            return report.model_copy(update={"metadata": metadata})
        return report.copy(update={"metadata": metadata})

    def _persist_pipeline_report(self, report):
        if self.pipeline_report_store is None:
            return report
        return self.pipeline_report_store.write_run_report(report)

    @staticmethod
    def _pop_runtime_kwargs(kwargs):
        return {
            key: kwargs.pop(key)
            for key in [
                "daily_review_reporter",
                "daily_review_log_path",
                "daily_review_output_dir",
                "pipeline_report_dir",
            ]
            if key in kwargs
        }

    @staticmethod
    def _runtime_config(config):
        if isinstance(config, RuntimeConfig):
            return config
        return RuntimeConfig.from_payload(config)

    @classmethod
    def _build_provider(cls, config, registry):
        enabled_markets = config.enabled_markets()
        provider_names = {market.provider for market in enabled_markets}
        if len(provider_names) != 1:
            raise ValueError("configured markets must use one provider per runtime handler")

        provider_name = config.registry_plugins.get(PluginKind.DATA) or next(iter(provider_names))
        return registry.create(PluginKind.DATA, provider_name)

    @classmethod
    def _build_strategy_router(cls, config, registry):
        symbol_routes = {}
        for binding in config.strategies:
            routes = {}
            fallback = None
            for route_config in binding.route_configs():
                strategy_factory = cls._strategy_factory(registry, route_config)
                if route_config.route == "default":
                    fallback = strategy_factory
                else:
                    routes[RegimeKind(route_config.route)] = strategy_factory

            symbol_routes[binding.symbol] = RegimeStrategyRouter(
                routes=routes,
                fallback=fallback,
                router_id=f"config_router:{binding.symbol}",
            )

        return SymbolRegimeStrategyRouter(symbol_routes=symbol_routes)

    @staticmethod
    def _strategy_factory(registry, route_config):
        def create_strategy():
            return registry.create(
                PluginKind.STRATEGY,
                route_config.strategy,
                version=route_config.version,
                parameters=dict(route_config.parameters),
            )

        return create_strategy

    @staticmethod
    def _build_risk_manager(config, registry):
        risk_manager = registry.create(
            PluginKind.RISK,
            config.risk.risk_plugin,
            config=config.risk,
        )
        if config.risk.kill_switch_enabled:
            risk_manager.enable_kill_switch("configured kill switch")
        return risk_manager

    @classmethod
    def _build_execution_engine(cls, config, registry, account, risk_manager=None):
        execution = registry.create(
            PluginKind.EXECUTION,
            config.broker.broker_plugin,
            config=config.broker,
            account=account,
        )
        if hasattr(execution, "on_order_intent"):
            return execution
        if isinstance(execution, BrokerAdapter):
            gate_settings = dict(config.broker.settings)
            gate_settings.setdefault("runtime_source", cls._enum_value(config.source))
            gate_settings.setdefault("broker_mode", cls._enum_value(config.broker.mode))
            return BrokerExecutionHandler(
                execution,
                live_order_gate=LiveOrderGate(gate_settings, risk_manager=risk_manager),
            )
        raise TypeError("execution plugin must return an on_order_intent handler or BrokerAdapter")

    @staticmethod
    def _validate_configured_runtime(config):
        if config.source != PayloadSource.LIVE:
            return

        provider_name = config.registry_plugins.get(PluginKind.DATA)
        if provider_name is None:
            enabled_markets = config.enabled_markets()
            provider_names = {market.provider for market in enabled_markets}
            provider_name = next(iter(provider_names)) if len(provider_names) == 1 else None

        if provider_name == "mock":
            raise ValueError("live runtime requires an explicit non-mock data provider")
        if config.broker.broker_plugin == "simulated":
            raise ValueError("live runtime requires an explicit live broker adapter")

    @staticmethod
    def _validate_live_dry_run_config(config):
        if config.source != PayloadSource.LIVE:
            raise ValueError("live dry-run requires a live runtime config")
        if config.broker.settings.get("allow_live_orders") is not False:
            raise ValueError("live dry-run requires broker.settings.allow_live_orders=false")

        provider_name = config.registry_plugins.get(PluginKind.DATA)
        if provider_name is None:
            enabled_markets = config.enabled_markets()
            provider_names = {market.provider for market in enabled_markets}
            provider_name = next(iter(provider_names)) if len(provider_names) == 1 else None
        if provider_name == "mock":
            raise ValueError("live dry-run requires an explicit non-mock data provider")

    @staticmethod
    def _feature_windows(config):
        for binding in config.strategies:
            for route_config in binding.route_configs():
                parameters = route_config.parameters
                if "fast_window" in parameters and "slow_window" in parameters:
                    return (parameters["fast_window"], parameters["slow_window"])
        return (3, 5)

    @staticmethod
    def _enum_value(value):
        return value.value if hasattr(value, "value") else value

    @classmethod
    def _register_builtin_plugins(cls, registry):
        cls._register_if_missing(registry, PluginKind.DATA, "mock", lambda **_: MockProvider())
        cls._register_if_missing(
            registry,
            PluginKind.FEATURE,
            "rule_regime_detector",
            lambda **_: RuleBasedRegimeDetector(),
        )
        cls._register_if_missing(
            registry,
            PluginKind.STRATEGY,
            "ma_crossover",
            cls._create_ma_crossover_strategy,
        )
        cls._register_if_missing(
            registry,
            PluginKind.RISK,
            "default",
            cls._create_risk_manager,
        )
        cls._register_if_missing(
            registry,
            PluginKind.EXECUTION,
            "simulated",
            cls._create_execution_engine,
        )

    @staticmethod
    def _register_if_missing(registry, kind, name, factory):
        try:
            registry.get(kind, name)
        except PluginNotFound:
            try:
                registry.register(kind, name, factory)
            except PluginAlreadyRegistered:
                pass

    @staticmethod
    def _create_ma_crossover_strategy(*, version="1.0", parameters=None):
        parameters = parameters or {}
        return MACrossoverStrategy(
            strategy_id=parameters.get("strategy_id", "ma_crossover"),
            strategy_version=version,
        )

    @staticmethod
    def _create_risk_manager(*, config, **_):
        risk_manager = RiskManager(
            max_position_pct=config.max_position_size,
            max_drawdown_pct=config.max_drawdown,
            daily_loss_limit_pct=config.daily_loss_limit_pct,
            consecutive_loss_limit=config.consecutive_loss_limit,
            api_failure_rate_limit=config.api_failure_rate_limit,
        )
        return risk_manager

    @staticmethod
    def _create_execution_engine(*, account, **_):
        return ExecutionEngine(account=account)
