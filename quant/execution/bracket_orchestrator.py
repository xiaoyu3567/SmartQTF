from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from quant.execution.broker import BrokerAdapter
from quant.execution.idempotency import (
    IdempotencyRecord,
    JsonIdempotencyRegistry,
    submit_order_idempotently,
)
from quant.execution.order_store import SQLiteOrderStore
from quant.execution.state_machine import ExecutionEvent, ExecutionState, ExecutionStateMachine
from quant.schemas.enums import OrderKind, OrderStatus, TradeSide
from quant.schemas.execution import (
    BrokerOrderRequest,
    BrokerProtectiveOrderRequest,
    BracketExecutionPlan,
    BracketExecutionStatus,
    ExchangeReadinessReport,
    ExchangeReadinessRequest,
    InstrumentOrderRules,
    LiveOrderGateDecision,
    OrderIntent,
)


@dataclass(frozen=True)
class _EntrySubmitOutcome:
    result: Any
    action: str
    reason: str
    broker_place_called: bool
    broker_lookup_called: bool
    idempotent_replay: bool
    submit_intent_count: int
    persistent_registry_attached: bool
    record: IdempotencyRecord | None = None

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "persistent_registry_attached": self.persistent_registry_attached,
            "action": self.action,
            "reason": self.reason,
            "broker_place_called": self.broker_place_called,
            "broker_lookup_called": self.broker_lookup_called,
            "idempotent_replay": self.idempotent_replay,
            "submit_intent_count": self.submit_intent_count,
        }


class BracketExecutionOrchestrator:
    """Live bracket executor for one Risk-approved execution order plan.

    The orchestrator intentionally does not choose entries or sizing. It only consumes
    a typed execution plan that already carries risk and allocation references.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        live_order_gate,
        *,
        clock=None,
        order_store: SQLiteOrderStore | None = None,
        idempotency_registry: JsonIdempotencyRegistry | None = None,
        fill_poll_interval_ms: int = 250,
    ):
        self.broker = broker
        self.live_order_gate = live_order_gate
        self.clock = clock or time.time
        self.order_store = order_store
        self.idempotency_registry = idempotency_registry
        self.fill_poll_interval_ms = max(1, int(fill_poll_interval_ms))
        self._results_by_idempotency_key: Dict[str, Dict[str, Any]] = {}
        self._payload_hash_by_idempotency_key: Dict[str, str] = {}

    def execute(
        self,
        execution_order_plan: BracketExecutionPlan | Dict[str, Any],
        *,
        portfolio_allocation,
        dry_run: Optional[bool] = None,
        instrument_rules: InstrumentOrderRules | None = None,
        reference_price: Optional[float] = None,
        exchange_readiness_request: ExchangeReadinessRequest | Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        plan = self._coerce_plan(execution_order_plan)
        plan_payload_hash = self._payload_hash(plan)
        replay = self._duplicate_replay(plan, plan_payload_hash)
        if replay is not None:
            return replay

        plan_rejection = self._validate_plan(plan)
        if plan_rejection is not None:
            return plan_rejection

        rule_rejection = self._validate_exchange_rules(plan, instrument_rules, reference_price)
        if rule_rejection is not None:
            return rule_rejection

        allocation_rejection = self._validate_portfolio_allocation(plan, portfolio_allocation)
        if allocation_rejection is not None:
            return allocation_rejection

        order_intent = self._order_intent_from_plan(plan)
        gate_decision = self.live_order_gate.evaluate(
            order_intent,
            portfolio_allocation=portfolio_allocation,
            dry_run=dry_run,
        )
        gate_rejection = self._validate_live_gate(plan, gate_decision)
        if gate_rejection is not None:
            return gate_rejection

        readiness_report = self._evaluate_exchange_readiness(
            plan,
            exchange_readiness_request,
            reference_price=reference_price,
        )
        if isinstance(readiness_report, dict):
            return readiness_report

        machine = ExecutionStateMachine()
        machine.apply_event(
            ExecutionEvent.ORDER_CREATED,
            client_order_id=plan.entry_order.client_order_id,
            metadata={"execution_plan_id": plan.execution_plan_id},
        )
        machine.apply_event(
            ExecutionEvent.ORDER_VALIDATED,
            client_order_id=plan.entry_order.client_order_id,
            metadata={
                "risk_decision_id": plan.risk_decision_id,
                "allocation_id": plan.allocation_id,
                "live_order_gate_approved": True,
            },
        )
        machine.apply_event(
            ExecutionEvent.ORDER_SUBMITTING,
            client_order_id=plan.entry_order.client_order_id,
            reason="entry_order_submit",
        )
        self._persist_order_intent(plan)
        self._persist_transition(machine)

        idempotent_entry = self._submit_entry_order(plan)
        entry_result = idempotent_entry.result
        if idempotent_entry.record is not None:
            self._persist_idempotency_record(idempotent_entry.record)
        self._persist_broker_result(
            entry_result,
            event_type="entry_order_submit_result",
            reason=idempotent_entry.reason,
        )
        if self._status_is(entry_result.status, OrderStatus.UNKNOWN):
            machine.apply_event(
                ExecutionEvent.ORDER_TIMEOUT,
                next_state=ExecutionState.TIMEOUT,
                client_order_id=entry_result.client_order_id,
                broker_order_id=entry_result.broker_order_id,
                reason=idempotent_entry.reason,
                metadata={
                    "idempotent_action": idempotent_entry.action,
                    "broker_lookup_called": idempotent_entry.broker_lookup_called,
                },
            )
            self._persist_transition(machine)
            result = self._result_payload(
                plan,
                BracketExecutionStatus.ENTRY_SUBMITTED_UNPROTECTED,
                gate_decision=gate_decision,
                machine=machine,
                entry_result=entry_result,
                protective_result=None,
                emergency_exit_result=None,
                cancel_result=None,
                broker_called=idempotent_entry.broker_place_called,
                live_orders_sent=idempotent_entry.broker_place_called,
                reason_codes=[idempotent_entry.reason],
                fill_resolution={
                    "source": "persistent_idempotency_registry",
                    "poll_count": 0,
                    "timeout": True,
                },
                idempotency_metadata=idempotent_entry.to_metadata(),
                exchange_readiness_report=readiness_report,
            )
            return self._remember(plan, plan_payload_hash, result)
        if self._status_is(entry_result.status, OrderStatus.REJECTED):
            machine.apply_event(
                ExecutionEvent.ORDER_REJECTED,
                next_state=ExecutionState.REJECTED,
                client_order_id=entry_result.client_order_id,
                broker_order_id=entry_result.broker_order_id,
                reason=entry_result.rejection_code or "entry_rejected",
            )
            self._persist_transition(machine)
            result = self._result_payload(
                plan,
                BracketExecutionStatus.ENTRY_REJECTED,
                gate_decision=gate_decision,
                machine=machine,
                entry_result=entry_result,
                protective_result=None,
                emergency_exit_result=None,
                cancel_result=None,
                broker_called=idempotent_entry.broker_place_called,
                live_orders_sent=idempotent_entry.broker_place_called,
                reason_codes=[entry_result.rejection_code or "entry_rejected"],
                fill_resolution={"source": "place_order", "poll_count": 0, "timeout": False},
                idempotency_metadata=idempotent_entry.to_metadata(),
                exchange_readiness_report=readiness_report,
            )
            return self._remember(plan, plan_payload_hash, result)

        machine.apply_event(
            ExecutionEvent.ORDER_SUBMITTED,
            client_order_id=entry_result.client_order_id,
            broker_order_id=entry_result.broker_order_id,
            reason="entry_order_submitted",
        )
        self._persist_transition(machine)

        fill_resolution = self._resolve_entry_fill(plan, machine, entry_result)
        entry_result = fill_resolution["entry_result"]
        cancel_result = fill_resolution.get("cancel_result")
        filled_qty = float(entry_result.filled_qty or 0.0)
        requested_qty = float(entry_result.requested_qty or 0.0)
        entry_filled = self._status_is(entry_result.status, OrderStatus.FILLED) or (
            requested_qty > 0.0 and filled_qty >= requested_qty
        )
        entry_partial = self._status_is(entry_result.status, OrderStatus.PARTIAL) or (
            0.0 < filled_qty < requested_qty
        )

        if not entry_filled and not entry_partial:
            status = (
                BracketExecutionStatus.CANCELLED_NOT_FILLED
                if cancel_result is not None or self._status_is(entry_result.status, OrderStatus.CANCELLED)
                else BracketExecutionStatus.ENTRY_SUBMITTED_UNPROTECTED
            )
            reason_codes = (
                ["entry_fill_timeout_cancelled"]
                if status == BracketExecutionStatus.CANCELLED_NOT_FILLED
                else ["entry_not_filled_yet"]
            )
            result = self._result_payload(
                plan,
                status,
                gate_decision=gate_decision,
                machine=machine,
                entry_result=entry_result,
                protective_result=None,
                emergency_exit_result=None,
                cancel_result=cancel_result,
                broker_called=True,
                live_orders_sent=True,
                reason_codes=reason_codes,
                fill_resolution=fill_resolution,
                idempotency_metadata=idempotent_entry.to_metadata(),
                exchange_readiness_report=readiness_report,
            )
            return self._remember(plan, plan_payload_hash, result)

        protective_request = self._protective_request(plan, gate_decision, filled_qty)
        protective_result = self.broker.place_native_protective_order(protective_request)
        self._persist_protective_broker_result(protective_result)
        protection_accepted = self._status_in(
            protective_result.status,
            {
                OrderStatus.CREATED,
                OrderStatus.PENDING,
                OrderStatus.ACCEPTED,
                OrderStatus.FILLED,
            },
        )
        if not protection_accepted:
            emergency_exit = self._emergency_exit_after_protection_failure(
                plan,
                machine,
                entry_result,
                protective_result,
                gate_decision,
                filled_qty,
            )
            result = self._result_payload(
                plan,
                emergency_exit["status"],
                gate_decision=gate_decision,
                machine=machine,
                entry_result=entry_result,
                protective_result=protective_result,
                emergency_exit_result=emergency_exit["result"],
                cancel_result=cancel_result,
                broker_called=True,
                live_orders_sent=True,
                reason_codes=emergency_exit["reason_codes"],
                fill_resolution=fill_resolution,
                alert_metadata=emergency_exit["alert_metadata"],
                reconciliation_metadata=emergency_exit["reconciliation_metadata"],
                idempotency_metadata=idempotent_entry.to_metadata(),
                exchange_readiness_report=readiness_report,
            )
            return self._remember(plan, plan_payload_hash, result)

        status = (
            BracketExecutionStatus.OPEN_PROTECTED
            if entry_filled
            else BracketExecutionStatus.PARTIALLY_EXECUTED_PROTECTED
        )
        result = self._result_payload(
            plan,
            status,
            gate_decision=gate_decision,
            machine=machine,
            entry_result=entry_result,
            protective_result=protective_result,
            emergency_exit_result=None,
            cancel_result=cancel_result,
            broker_called=True,
            live_orders_sent=True,
            reason_codes=["entry_filled_and_protection_accepted"],
            fill_resolution=fill_resolution,
            idempotency_metadata=idempotent_entry.to_metadata(),
            exchange_readiness_report=readiness_report,
        )
        return self._remember(plan, plan_payload_hash, result)

    def _coerce_plan(self, execution_order_plan):
        if isinstance(execution_order_plan, BracketExecutionPlan):
            return execution_order_plan
        return BracketExecutionPlan.from_payload(execution_order_plan)

    def _submit_entry_order(self, plan: BracketExecutionPlan) -> _EntrySubmitOutcome:
        if self.idempotency_registry is None:
            result = self.broker.place_order(plan.entry_order)
            return _EntrySubmitOutcome(
                result=result,
                action="submitted_without_persistent_registry",
                reason="broker_order_submitted_without_persistent_registry",
                broker_place_called=True,
                broker_lookup_called=False,
                idempotent_replay=False,
                submit_intent_count=1,
                persistent_registry_attached=False,
            )

        now = int(self.clock())
        metadata = {
            "source": "bracket_execution_orchestrator",
            "execution_plan_id": plan.execution_plan_id,
            "idempotency_key": plan.idempotency_key,
            "bracket_plan_payload_hash": self._payload_hash(plan),
            "risk_decision_id": plan.risk_decision_id,
            "allocation_id": plan.allocation_id,
        }
        submit_result = submit_order_idempotently(
            self.broker,
            plan.entry_order,
            self.idempotency_registry,
            now=now,
            check_broker_before_submit=True,
            metadata=metadata,
        )
        return _EntrySubmitOutcome(
            result=submit_result.result,
            action=submit_result.action,
            reason=submit_result.reason,
            broker_place_called=submit_result.broker_place_called,
            broker_lookup_called=submit_result.broker_lookup_called,
            idempotent_replay=submit_result.idempotent_replay,
            submit_intent_count=submit_result.submit_intent_count,
            persistent_registry_attached=True,
            record=self.idempotency_registry.get(plan.entry_order.client_order_id),
        )

    def _validate_plan(self, plan: BracketExecutionPlan) -> Optional[Dict[str, Any]]:
        reason_codes = []
        if not plan.risk_approved:
            reason_codes.append("risk_approval_missing")
        if not plan.risk_decision_id:
            reason_codes.append("risk_decision_id_missing")
        if not plan.allocation_id:
            reason_codes.append("allocation_id_missing")
        if not plan.idempotency_key:
            reason_codes.append("idempotency_key_missing")
        if plan.entry_order.reduce_only:
            reason_codes.append("entry_order_reduce_only_not_allowed")
        if reason_codes:
            return self._pre_submit_rejection(plan, reason_codes)
        return None

    def _validate_portfolio_allocation(
        self,
        plan: BracketExecutionPlan,
        portfolio_allocation,
    ) -> Optional[Dict[str, Any]]:
        if portfolio_allocation is None:
            return self._pre_submit_rejection(
                plan,
                ["portfolio_allocation_missing"],
                metadata={"portfolio_allocation_present": False},
            )

        allocation = self._matching_portfolio_allocation(plan, portfolio_allocation) or portfolio_allocation
        reason_codes = []
        metadata = {
            "portfolio_allocation_present": True,
            "portfolio_allocation_id": self._field(allocation, "allocation_id")
            or self._field(portfolio_allocation, "allocation_id"),
            "portfolio_client_order_id": self._field(allocation, "client_order_id"),
            "portfolio_allocated_quantity": self._field(allocation, "allocated_quantity")
            if self._field(allocation, "allocated_quantity") is not None
            else self._field(allocation, "quantity"),
            "portfolio_risk_decision_id": self._field(allocation, "risk_decision_id")
            or self._field(portfolio_allocation, "risk_decision_id"),
        }

        if self._field(allocation, "approved", self._field(portfolio_allocation, "approved", False)) is not True:
            reason_codes.append("portfolio_allocation_not_approved")

        if not metadata["portfolio_allocation_id"]:
            reason_codes.append("portfolio_allocation_id_missing")
        elif metadata["portfolio_allocation_id"] != plan.allocation_id:
            reason_codes.append("portfolio_allocation_id_mismatch")

        if not metadata["portfolio_risk_decision_id"]:
            reason_codes.append("portfolio_risk_decision_id_missing")
        elif metadata["portfolio_risk_decision_id"] != plan.risk_decision_id:
            reason_codes.append("portfolio_risk_decision_id_mismatch")

        client_order_id = metadata["portfolio_client_order_id"]
        if client_order_id not in (None, plan.entry_order.client_order_id):
            reason_codes.append("portfolio_client_order_id_mismatch")

        quantity = self._float_or_none(metadata["portfolio_allocated_quantity"])
        if quantity is None or quantity <= 0.0:
            reason_codes.append("portfolio_allocated_quantity_invalid")
        elif abs(quantity - float(plan.entry_order.quantity)) > 1e-9:
            reason_codes.append("portfolio_allocated_quantity_mismatch")

        if reason_codes:
            return self._pre_submit_rejection(plan, reason_codes, metadata=metadata)
        return None

    def _validate_exchange_rules(
        self,
        plan: BracketExecutionPlan,
        instrument_rules: InstrumentOrderRules | None,
        reference_price: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        if instrument_rules is None:
            return None
        violations = instrument_rules.validate_order_request(plan.entry_order, reference_price)
        if not violations:
            return None
        return self._pre_submit_rejection(
            plan,
            ["exchange_rule_violation"],
            metadata={"violations": [violation.to_payload() for violation in violations]},
        )

    def _validate_live_gate(
        self,
        plan: BracketExecutionPlan,
        gate_decision: LiveOrderGateDecision,
    ) -> Optional[Dict[str, Any]]:
        if gate_decision.approved and gate_decision.risk_approved and gate_decision.portfolio_allocation_approved:
            return None
        reason_codes = list(gate_decision.reason_codes or [])
        if gate_decision.approved and not gate_decision.risk_approved:
            reason_codes.append("live_gate_risk_approval_missing")
        if gate_decision.approved and not gate_decision.portfolio_allocation_approved:
            reason_codes.append("live_gate_portfolio_allocation_missing")
        return self._pre_submit_rejection(
            plan,
            reason_codes or ["live_order_gate_rejected"],
            gate_decision=gate_decision,
        )

    def _evaluate_exchange_readiness(
        self,
        plan: BracketExecutionPlan,
        exchange_readiness_request: ExchangeReadinessRequest | Dict[str, Any] | None,
        *,
        reference_price: Optional[float],
    ) -> ExchangeReadinessReport | Dict[str, Any] | None:
        request = self._coerce_exchange_readiness_request(
            plan,
            exchange_readiness_request,
            reference_price=reference_price,
        )
        if request is None:
            return None
        try:
            report = self.broker.evaluate_exchange_readiness(request)
        except Exception as exc:
            return self._pre_submit_rejection(
                plan,
                ["exchange_readiness_error"],
                metadata={
                    "exchange_readiness": {
                        "request": request.to_payload(),
                        "error": str(exc),
                    }
                },
            )
        if isinstance(report, dict):
            report = ExchangeReadinessReport.from_payload(report)
        if report.approved:
            return report
        return self._pre_submit_rejection(
            plan,
            ["exchange_readiness_rejected", *list(report.reason_codes or [])],
            exchange_readiness_report=report,
            metadata={"exchange_readiness": report.to_payload()},
        )

    def _coerce_exchange_readiness_request(
        self,
        plan: BracketExecutionPlan,
        exchange_readiness_request: ExchangeReadinessRequest | Dict[str, Any] | None,
        *,
        reference_price: Optional[float],
    ) -> ExchangeReadinessRequest | None:
        if exchange_readiness_request is None:
            configured = dict(plan.metadata or {}).get("exchange_readiness_request")
            if configured is None:
                return None
            exchange_readiness_request = configured
        if isinstance(exchange_readiness_request, ExchangeReadinessRequest):
            return exchange_readiness_request
        payload = dict(exchange_readiness_request)
        payload.setdefault("request_id", f"{plan.execution_plan_id}:exchange-readiness")
        payload.setdefault("broker_name", self.broker.name)
        payload.setdefault("symbol", plan.entry_order.symbol)
        payload.setdefault("requested_at", int(self.clock()))
        payload.setdefault("reference_price", reference_price or plan.entry_order.limit_price)
        return ExchangeReadinessRequest.from_payload(payload)

    def _resolve_entry_fill(
        self,
        plan: BracketExecutionPlan,
        machine: ExecutionStateMachine,
        entry_result,
    ) -> Dict[str, Any]:
        current_result = entry_result
        poll_results = []
        timed_out = False
        cancel_result = None

        if self._entry_has_terminal_fill_state(current_result) or self._status_is(
            current_result.status,
            OrderStatus.REJECTED,
        ):
            self._apply_fill_state(machine, current_result)
            self._persist_transition(machine)
            cancel_result = self._cancel_remaining_after_partial_fill_if_needed(plan, machine, current_result)
            return {
                "source": "place_order",
                "entry_result": current_result,
                "poll_results": poll_results,
                "poll_count": 0,
                "timeout": False,
                "cancel_result": cancel_result,
            }

        deadline = self.clock() + (plan.policy.max_fill_wait_ms / 1000.0)
        max_poll_attempts = max(1, int(plan.policy.max_fill_wait_ms / self.fill_poll_interval_ms) + 1)
        while (
            plan.policy.max_fill_wait_ms > 0
            and self.clock() < deadline
            and len(poll_results) < max_poll_attempts
        ):
            polled = self.broker.get_order(plan.entry_order.client_order_id)
            poll_results.append(self._payload(polled))
            current_result = polled
            self._persist_broker_result(
                current_result,
                event_type="entry_order_poll_result",
                reason="entry_fill_poll",
            )
            if self._entry_has_terminal_fill_state(current_result) or self._status_in(
                current_result.status,
                {OrderStatus.CANCELLED, OrderStatus.REJECTED},
            ):
                break
            if self.fill_poll_interval_ms > 0:
                time.sleep(min(self.fill_poll_interval_ms / 1000.0, max(0.0, deadline - self.clock())))

        if (
            plan.policy.max_fill_wait_ms > 0
            and not self._entry_has_terminal_fill_state(current_result)
            and not self._status_in(current_result.status, {OrderStatus.CANCELLED, OrderStatus.REJECTED})
        ):
            timed_out = True
            machine.apply_event(
                ExecutionEvent.ORDER_TIMEOUT,
                next_state=ExecutionState.TIMEOUT,
                client_order_id=current_result.client_order_id,
                broker_order_id=current_result.broker_order_id,
                reason="entry_fill_timeout",
                metadata={
                    "max_fill_wait_ms": plan.policy.max_fill_wait_ms,
                    "poll_count": len(poll_results),
                    "filled_qty": float(current_result.filled_qty or 0.0),
                },
            )
            self._persist_transition(machine)
            if plan.policy.cancel_if_not_filled:
                cancel_result = self.broker.cancel_order(plan.entry_order.client_order_id)
                current_result = self._merge_cancel_result(current_result, cancel_result)
                self._persist_broker_result(
                    cancel_result,
                    event_type="entry_order_cancel_result",
                    reason="entry_fill_timeout_cancel_remaining",
                )
                machine.apply_event(
                    ExecutionEvent.ORDER_CANCELLED,
                    next_state=ExecutionState.CANCELLED,
                    client_order_id=cancel_result.client_order_id,
                    broker_order_id=cancel_result.broker_order_id,
                    reason="entry_fill_timeout_cancel_remaining",
                    metadata={
                        "filled_qty": float(current_result.filled_qty or 0.0),
                        "requested_qty": float(current_result.requested_qty or 0.0),
                    },
                )
                self._persist_transition(machine)

        if self._entry_has_terminal_fill_state(current_result):
            self._apply_fill_state(machine, current_result)
            self._persist_transition(machine)
            if cancel_result is None:
                cancel_result = self._cancel_remaining_after_partial_fill_if_needed(
                    plan,
                    machine,
                    current_result,
                )

        return {
            "source": "poll" if poll_results else "place_order",
            "entry_result": current_result,
            "poll_results": poll_results,
            "poll_count": len(poll_results),
            "timeout": timed_out,
            "cancel_result": cancel_result,
        }

    def _cancel_remaining_after_partial_fill_if_needed(
        self,
        plan: BracketExecutionPlan,
        machine: ExecutionStateMachine,
        current_result,
    ):
        filled_qty = float(current_result.filled_qty or 0.0)
        requested_qty = float(current_result.requested_qty or 0.0)
        if not plan.policy.cancel_if_not_filled:
            return None
        if not (0.0 < filled_qty < requested_qty):
            return None
        if machine.state != ExecutionState.PARTIALLY_FILLED:
            return None

        cancel_result = self.broker.cancel_order(plan.entry_order.client_order_id)
        self._persist_broker_result(
            cancel_result,
            event_type="entry_order_cancel_result",
            reason="partial_fill_cancel_remaining",
        )
        machine.apply_event(
            ExecutionEvent.ORDER_CANCELLED,
            next_state=ExecutionState.CANCELLED,
            client_order_id=cancel_result.client_order_id,
            broker_order_id=cancel_result.broker_order_id,
            reason="partial_fill_cancel_remaining",
            metadata={"filled_qty": filled_qty, "requested_qty": requested_qty},
        )
        self._persist_transition(machine)
        return cancel_result

    def _entry_has_terminal_fill_state(self, result) -> bool:
        filled_qty = float(result.filled_qty or 0.0)
        requested_qty = float(result.requested_qty or 0.0)
        return (
            self._status_is(result.status, OrderStatus.FILLED)
            or self._status_is(result.status, OrderStatus.PARTIAL)
            or (requested_qty > 0.0 and filled_qty >= requested_qty)
            or (0.0 < filled_qty < requested_qty)
        )

    def _apply_fill_state(self, machine: ExecutionStateMachine, result) -> None:
        filled_qty = float(result.filled_qty or 0.0)
        requested_qty = float(result.requested_qty or 0.0)
        is_filled = self._status_is(result.status, OrderStatus.FILLED) or (
            requested_qty > 0.0 and filled_qty >= requested_qty
        )
        is_partial = self._status_is(result.status, OrderStatus.PARTIAL) or (
            0.0 < filled_qty < requested_qty
        )
        if not (is_filled or is_partial):
            return
        if machine.state in {ExecutionState.FILLED, ExecutionState.PARTIALLY_FILLED, ExecutionState.CANCELLED}:
            return
        machine.apply_event(
            ExecutionEvent.ORDER_FILLED if is_filled else ExecutionEvent.ORDER_PARTIALLY_FILLED,
            next_state=ExecutionState.FILLED if is_filled else ExecutionState.PARTIALLY_FILLED,
            client_order_id=result.client_order_id,
            broker_order_id=result.broker_order_id,
            metadata={"filled_qty": filled_qty, "requested_qty": requested_qty},
        )

    def _merge_cancel_result(self, current_result, cancel_result):
        if float(current_result.filled_qty or 0.0) <= 0.0:
            return cancel_result
        return current_result.copy(update={"status": OrderStatus.PARTIAL})

    def _persist_order_intent(self, plan: BracketExecutionPlan) -> None:
        if self.order_store is None:
            return
        self.order_store.record_order_intent(
            self._order_intent_from_plan(plan),
            metadata={
                "execution_plan_id": plan.execution_plan_id,
                "idempotency_key": plan.idempotency_key,
                "risk_decision_id": plan.risk_decision_id,
                "allocation_id": plan.allocation_id,
            },
        )

    def _persist_transition(self, machine: ExecutionStateMachine) -> None:
        if self.order_store is None or not machine.audit_trail:
            return
        self.order_store.record_transition(
            machine.audit_trail[-1],
            event_time=int(self.clock()),
            metadata={"source": "bracket_execution_orchestrator"},
        )

    def _persist_broker_result(self, result, *, event_type: str, reason: str) -> None:
        if self.order_store is None:
            return
        self.order_store.record_broker_order_result(
            result,
            event_type=event_type,
            event_time=int(self.clock()),
            reason=reason,
            metadata={"source": "bracket_execution_orchestrator"},
        )

    def _persist_idempotency_record(self, record: IdempotencyRecord) -> None:
        if self.order_store is None:
            return
        self.order_store.import_idempotency_record(record)

    def _persist_protective_broker_result(self, result) -> None:
        if self.order_store is None:
            return
        self._persist_order_store_event(
            client_order_id=result.parent_client_order_id,
            event_id=f"{result.parent_client_order_id}:protective_order_submit_result:{int(self.clock())}",
            event_type="protective_order_submit_result",
            broker_order_id=result.broker_order_id,
            reason="protective_order_submitted",
            payload=result.to_payload(),
        )

    def _persist_order_store_event(
        self,
        *,
        client_order_id: str,
        event_id: str,
        event_type: str,
        broker_order_id: str | None = None,
        reason: str | None = None,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        if self.order_store is None:
            return
        self.order_store._insert_event(
            client_order_id=client_order_id,
            event_id=event_id,
            event_type=event_type,
            broker_order_id=broker_order_id,
            event_time=int(self.clock()),
            reason=reason,
            payload=payload or {},
            metadata={"source": "bracket_execution_orchestrator"},
        )
        self.order_store.connection.commit()

    def _emergency_exit_after_protection_failure(
        self,
        plan: BracketExecutionPlan,
        machine: ExecutionStateMachine,
        entry_result,
        protective_result,
        gate_decision: LiveOrderGateDecision,
        filled_qty: float,
    ) -> Dict[str, Any]:
        base_reason = protective_result.rejection_code or "protective_order_rejected"
        alert_metadata = {
            "alert_type": "protective_order_failure",
            "severity": "critical",
            "requires_manual_review": True,
            "client_order_id": entry_result.client_order_id,
            "protective_client_order_id": protective_result.protective_client_order_id,
            "risk_decision_id": plan.risk_decision_id,
            "allocation_id": plan.allocation_id,
        }
        reconciliation_metadata = {
            "entry_client_order_id": entry_result.client_order_id,
            "entry_broker_order_id": entry_result.broker_order_id,
            "protective_broker_order_id": protective_result.broker_order_id,
            "emergency_exit_required": filled_qty > 0.0,
        }

        if filled_qty <= 0.0:
            return {
                "status": BracketExecutionStatus.NO_POSITION,
                "result": None,
                "reason_codes": [base_reason, "no_filled_position_for_emergency_exit"],
                "alert_metadata": alert_metadata,
                "reconciliation_metadata": {
                    **reconciliation_metadata,
                    "emergency_exit_client_order_id": None,
                    "emergency_exit_status": "not_required",
                },
            }

        emergency_request = self._emergency_exit_request(plan, gate_decision, filled_qty)
        emergency_result = self.broker.place_order(emergency_request)
        self._persist_order_store_event(
            client_order_id=entry_result.client_order_id,
            event_id=f"{entry_result.client_order_id}:emergency_exit_submit_result:{int(self.clock())}",
            event_type="emergency_exit_submit_result",
            broker_order_id=emergency_result.broker_order_id,
            reason="protective_order_failed_emergency_exit",
            payload=emergency_result.to_payload(),
        )
        if self._status_is(emergency_result.status, OrderStatus.REJECTED):
            status = BracketExecutionStatus.PROTECTION_FAILED
            emergency_reason = emergency_result.rejection_code or "emergency_exit_rejected"
            naked_position_resolved = False
        else:
            naked_position_resolved = (
                self._status_is(emergency_result.status, OrderStatus.FILLED)
                or float(emergency_result.filled_qty or 0.0) >= filled_qty
            )
            status = BracketExecutionStatus.EMERGENCY_EXIT
            emergency_reason = "emergency_exit_submitted"

        return {
            "status": status,
            "result": emergency_result,
            "reason_codes": [base_reason, emergency_reason],
            "alert_metadata": {
                **alert_metadata,
                "emergency_exit_client_order_id": emergency_request.client_order_id,
                "naked_position_resolved": naked_position_resolved,
            },
            "reconciliation_metadata": {
                **reconciliation_metadata,
                "emergency_exit_client_order_id": emergency_request.client_order_id,
                "emergency_exit_status": self._status_value(emergency_result.status),
                "emergency_exit_broker_order_id": emergency_result.broker_order_id,
                "naked_position_resolved": naked_position_resolved,
            },
        }

    def _duplicate_replay(self, plan: BracketExecutionPlan, plan_payload_hash: str) -> Optional[Dict[str, Any]]:
        existing_hash = self._payload_hash_by_idempotency_key.get(plan.idempotency_key)
        if existing_hash is None:
            return self._persistent_duplicate_replay(plan, plan_payload_hash)
        if existing_hash != plan_payload_hash:
            return self._pre_submit_rejection(plan, ["idempotency_payload_mismatch"])
        replayed = copy.deepcopy(self._results_by_idempotency_key[plan.idempotency_key])
        replayed["duplicate_replay"] = True
        replayed["broker_called"] = False
        replayed["live_orders_sent"] = False
        replayed["entry_orders_sent"] = 0
        replayed["protective_orders_sent"] = 0
        replayed["cancel_orders_sent"] = 0
        replayed["emergency_exit_orders_sent"] = 0
        replayed["replay_reason"] = "idempotency_key_already_completed"
        return replayed

    def _persistent_duplicate_replay(
        self,
        plan: BracketExecutionPlan,
        plan_payload_hash: str,
    ) -> Optional[Dict[str, Any]]:
        if self.idempotency_registry is None:
            return None
        record = self.idempotency_registry.get(plan.entry_order.client_order_id)
        if record is None:
            return None
        try:
            self.idempotency_registry.ensure_request_matches(plan.entry_order, record)
        except ValueError:
            return self._pre_submit_rejection(plan, ["persistent_idempotency_payload_mismatch"])

        metadata = dict(record.metadata or {})
        persisted_hash = metadata.get("bracket_plan_payload_hash")
        if persisted_hash is not None and persisted_hash != plan_payload_hash:
            return self._pre_submit_rejection(plan, ["persistent_idempotency_payload_mismatch"])

        persisted_result = metadata.get("bracket_result_payload")
        if not isinstance(persisted_result, dict):
            return None

        replayed = copy.deepcopy(persisted_result)
        replayed["duplicate_replay"] = True
        replayed["broker_called"] = False
        replayed["live_orders_sent"] = False
        replayed["entry_orders_sent"] = 0
        replayed["protective_orders_sent"] = 0
        replayed["cancel_orders_sent"] = 0
        replayed["emergency_exit_orders_sent"] = 0
        replayed["replay_reason"] = "persistent_idempotency_registry_completed"
        replayed.setdefault("metadata", {})
        replayed["metadata"]["idempotency"] = {
            **dict(replayed["metadata"].get("idempotency") or {}),
            "persistent_registry_attached": True,
            "idempotent_replay": True,
            "submit_intent_count": record.submit_intent_count,
        }
        return replayed

    def _remember(self, plan: BracketExecutionPlan, plan_payload_hash: str, result: Dict[str, Any]) -> Dict[str, Any]:
        self._payload_hash_by_idempotency_key[plan.idempotency_key] = plan_payload_hash
        self._results_by_idempotency_key[plan.idempotency_key] = copy.deepcopy(result)
        self._persist_bracket_result(plan, plan_payload_hash, result)
        return result

    def _persist_bracket_result(
        self,
        plan: BracketExecutionPlan,
        plan_payload_hash: str,
        result: Dict[str, Any],
    ) -> None:
        if self.idempotency_registry is None:
            return
        try:
            record = self.idempotency_registry.update_metadata(
                plan.entry_order.client_order_id,
                {
                    "source": "bracket_execution_orchestrator",
                    "execution_plan_id": plan.execution_plan_id,
                    "idempotency_key": plan.idempotency_key,
                    "bracket_plan_payload_hash": plan_payload_hash,
                    "bracket_status": result.get("status"),
                    "bracket_result_payload": copy.deepcopy(result),
                    "bracket_completed_at": int(self.clock()),
                },
                now=int(self.clock()),
            )
        except KeyError:
            return
        self._persist_idempotency_record(record)

    def _pre_submit_rejection(
        self,
        plan: BracketExecutionPlan,
        reason_codes: list[str],
        *,
        gate_decision: LiveOrderGateDecision | None = None,
        metadata: Optional[Dict[str, Any]] = None,
        exchange_readiness_report: ExchangeReadinessReport | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "execution_plan_id": plan.execution_plan_id,
            "idempotency_key": plan.idempotency_key,
            "status": BracketExecutionStatus.REJECTED,
            "reason_codes": list(reason_codes),
            "client_order_id": plan.entry_order.client_order_id,
            "symbol": plan.entry_order.symbol,
            "risk_decision_id": plan.risk_decision_id,
            "allocation_id": plan.allocation_id,
            "entry_order_result": None,
            "protective_order_result": None,
            "live_order_gate": None if gate_decision is None else gate_decision.to_payload(),
            "exchange_readiness": None
            if exchange_readiness_report is None
            else exchange_readiness_report.to_payload(),
            "broker_called": False,
            "live_orders_sent": False,
            "duplicate_replay": False,
            "safety_flags": {
                "risk_approved": plan.risk_approved,
                "portfolio_approved": bool(
                    getattr(gate_decision, "portfolio_allocation_approved", False)
                ),
                "live_order_gate_approved": bool(getattr(gate_decision, "approved", False)),
                "exchange_readiness_approved": bool(
                    getattr(exchange_readiness_report, "approved", False)
                ),
                "duplicate_guard_passed": "idempotency_payload_mismatch" not in reason_codes,
                "protection_submitted": False,
            },
            "metadata": dict(metadata or {}),
        }
        return payload

    def _result_payload(
        self,
        plan: BracketExecutionPlan,
        status: str,
        *,
        gate_decision: LiveOrderGateDecision,
        machine: ExecutionStateMachine,
        entry_result,
        protective_result,
        emergency_exit_result,
        cancel_result,
        broker_called: bool,
        live_orders_sent: bool,
        reason_codes: list[str],
        fill_resolution: Dict[str, Any],
        alert_metadata: Optional[Dict[str, Any]] = None,
        reconciliation_metadata: Optional[Dict[str, Any]] = None,
        idempotency_metadata: Optional[Dict[str, Any]] = None,
        exchange_readiness_report: ExchangeReadinessReport | None = None,
    ) -> Dict[str, Any]:
        idempotency = dict(idempotency_metadata or {})
        entry_order_submitted = (
            bool(idempotency.get("broker_place_called", broker_called))
            if idempotency
            else broker_called
        )
        return {
            "execution_plan_id": plan.execution_plan_id,
            "idempotency_key": plan.idempotency_key,
            "status": status,
            "reason_codes": list(reason_codes),
            "client_order_id": entry_result.client_order_id,
            "broker_order_id": entry_result.broker_order_id,
            "symbol": entry_result.symbol,
            "risk_decision_id": plan.risk_decision_id,
            "allocation_id": plan.allocation_id,
            "entry_order_result": self._payload(entry_result),
            "protective_order_result": None if protective_result is None else self._payload(protective_result),
            "emergency_exit_result": None
            if emergency_exit_result is None
            else self._payload(emergency_exit_result),
            "cancel_order_result": None if cancel_result is None else self._payload(cancel_result),
            "live_order_gate": gate_decision.to_payload(),
            "exchange_readiness": None
            if exchange_readiness_report is None
            else exchange_readiness_report.to_payload(),
            "broker_called": broker_called,
            "live_orders_sent": live_orders_sent,
            "entry_orders_sent": 1 if entry_order_submitted else 0,
            "protective_orders_sent": 1 if protective_result is not None else 0,
            "cancel_orders_sent": 1 if cancel_result is not None else 0,
            "emergency_exit_orders_sent": 1 if emergency_exit_result is not None else 0,
            "duplicate_replay": False,
            "fill_resolution": {
                "source": fill_resolution.get("source"),
                "poll_count": int(fill_resolution.get("poll_count") or 0),
                "timeout": bool(fill_resolution.get("timeout", False)),
                "cancel_remaining": cancel_result is not None,
                "poll_results": list(fill_resolution.get("poll_results") or []),
            },
            "lifecycle": {
                "state": machine.state,
                "path": list(machine.history),
                "audit_log": machine.to_audit_log(),
                "broker_submit_intent_count": machine.broker_submit_intent_count(
                    entry_result.client_order_id
                ),
                "persistent_submit_intent_count": idempotency.get("submit_intent_count"),
            },
            "safety_flags": {
                "risk_approved": plan.risk_approved,
                "portfolio_approved": gate_decision.portfolio_allocation_approved,
                "live_order_gate_approved": gate_decision.approved,
                "exchange_readiness_approved": (
                    True if exchange_readiness_report is None else exchange_readiness_report.approved
                ),
                "duplicate_guard_passed": True,
                "protection_submitted": protective_result is not None,
                "remaining_entry_cancelled": cancel_result is not None,
                "emergency_exit_submitted": emergency_exit_result is not None,
                "naked_position_resolved": bool(
                    (reconciliation_metadata or {}).get("naked_position_resolved", False)
                ),
                "order_store_attached": self.order_store is not None,
                "persistent_idempotency_registry_attached": bool(
                    idempotency.get("persistent_registry_attached", False)
                ),
                "persistent_idempotency_replay": bool(idempotency.get("idempotent_replay", False)),
            },
            "policy": plan.policy.to_payload(),
            "metadata": {
                **dict(plan.metadata or {}),
                "broker_name": self.broker.name,
                "submitted_at": int(self.clock()),
                "alert": dict(alert_metadata or {}),
                "reconciliation": dict(reconciliation_metadata or {}),
                "idempotency": idempotency,
            },
        }

    def _protective_request(
        self,
        plan: BracketExecutionPlan,
        gate_decision: LiveOrderGateDecision,
        quantity: float,
    ) -> BrokerProtectiveOrderRequest:
        protective_client_order_id = (
            plan.policy.protective_client_order_id
            or f"{plan.entry_order.client_order_id}:protective"
        )
        return BrokerProtectiveOrderRequest(
            protective_client_order_id=protective_client_order_id,
            parent_client_order_id=plan.entry_order.client_order_id,
            symbol=plan.entry_order.symbol,
            entry_side=plan.entry_order.side,
            quantity=quantity,
            stop_loss_price=plan.stop_loss_order.price,
            take_profit_price=None if plan.take_profit_order is None else plan.take_profit_order.price,
            stop_loss_client_order_id=plan.stop_loss_order.client_order_id,
            take_profit_client_order_id=(
                None if plan.take_profit_order is None else plan.take_profit_order.client_order_id
            ),
            reduce_only=True,
            live_order_gate=gate_decision,
            trace=plan.trace or plan.entry_order.trace,
            metadata={
                "execution_plan_id": plan.execution_plan_id,
                "idempotency_key": plan.idempotency_key,
                "risk_decision_id": plan.risk_decision_id,
                "allocation_id": plan.allocation_id,
                "native_order_type": plan.policy.native_order_type,
            },
        )

    def _emergency_exit_request(
        self,
        plan: BracketExecutionPlan,
        gate_decision: LiveOrderGateDecision,
        quantity: float,
    ):
        return BrokerOrderRequest(
            client_order_id=f"{plan.entry_order.client_order_id}:emergency-exit",
            symbol=plan.entry_order.symbol,
            side=self._opposite_side(plan.entry_order.side),
            order_type=OrderKind.MARKET,
            quantity=quantity,
            limit_price=None,
            time_in_force=plan.entry_order.time_in_force,
            reduce_only=True,
            trace=plan.trace or plan.entry_order.trace,
        )

    def _order_intent_from_plan(self, plan: BracketExecutionPlan):
        return OrderIntent(
            order_intent_id=plan.execution_plan_id,
            decision_id=plan.risk_decision_id,
            client_order_id=plan.entry_order.client_order_id,
            symbol=plan.entry_order.symbol,
            side=plan.entry_order.side,
            order_type=plan.entry_order.order_type,
            quantity=plan.entry_order.quantity,
            limit_price=plan.entry_order.limit_price,
            time_in_force=plan.entry_order.time_in_force,
            reduce_only=plan.entry_order.reduce_only,
            risk_approved=plan.risk_approved,
            created_at=int(self.clock()),
            trace=plan.trace or plan.entry_order.trace,
        )

    def _matching_portfolio_allocation(self, plan: BracketExecutionPlan, portfolio_allocation):
        for allocation in self._field(portfolio_allocation, "allocations", []) or []:
            if self._field(allocation, "client_order_id") == plan.entry_order.client_order_id:
                return allocation
        return None

    @staticmethod
    def _field(source, name, default=None):
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)

    @staticmethod
    def _float_or_none(value) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _payload_hash(plan: BracketExecutionPlan) -> str:
        return json.dumps(plan.to_payload(), sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _payload(model) -> Dict[str, Any]:
        if hasattr(model, "to_payload"):
            return model.to_payload()
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="json")
        return dict(model)

    @staticmethod
    def _status_is(value, expected: OrderStatus) -> bool:
        raw = value.value if hasattr(value, "value") else value
        return raw == expected.value

    @classmethod
    def _status_in(cls, value, expected: set[OrderStatus]) -> bool:
        return any(cls._status_is(value, item) for item in expected)

    @staticmethod
    def _status_value(value) -> str:
        return value.value if hasattr(value, "value") else str(value)

    @staticmethod
    def _opposite_side(side):
        raw = side.value if hasattr(side, "value") else side
        return TradeSide.SELL if raw == TradeSide.BUY.value else TradeSide.BUY
