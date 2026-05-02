from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Set


class ExecutionState:
    # Legacy engine states kept for compatibility with the existing simulator.
    IDLE = "IDLE"
    ORDER_PENDING = "ORDER_PENDING"
    PARTIAL = "PARTIAL"
    POSITION_OPEN = "POSITION_OPEN"
    EXIT = "EXIT"

    # Production order lifecycle states.
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    RETRYING = "RETRYING"
    ERROR = "ERROR"
    RECOVERY = "RECOVERY"


class ExecutionEvent:
    ORDER_CREATED = "order_created"
    ORDER_VALIDATED = "order_validated"
    ORDER_SUBMITTING = "order_submitting"
    ORDER_SUBMITTED = "order_submitted"
    SIGNAL_ACCEPTED = "signal_accepted"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"
    ORDER_TIMEOUT = "order_timeout"
    RETRY_STARTED = "retry_started"
    RECOVERY_STARTED = "recovery_started"
    ORDER_ERROR = "order_error"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    RESET = "reset"


ORDER_LIFECYCLE_STATES = {
    ExecutionState.CREATED,
    ExecutionState.VALIDATED,
    ExecutionState.SUBMITTING,
    ExecutionState.SUBMITTED,
    ExecutionState.PARTIALLY_FILLED,
    ExecutionState.FILLED,
    ExecutionState.CANCELLED,
    ExecutionState.REJECTED,
    ExecutionState.TIMEOUT,
    ExecutionState.RETRYING,
    ExecutionState.ERROR,
    ExecutionState.RECOVERY,
}

OPEN_ORDER_STATES = {
    ExecutionState.CREATED,
    ExecutionState.VALIDATED,
    ExecutionState.SUBMITTING,
    ExecutionState.SUBMITTED,
    ExecutionState.PARTIALLY_FILLED,
    ExecutionState.TIMEOUT,
    ExecutionState.RETRYING,
    ExecutionState.RECOVERY,
}

TERMINAL_ORDER_STATES = {
    ExecutionState.FILLED,
    ExecutionState.CANCELLED,
    ExecutionState.REJECTED,
    ExecutionState.ERROR,
}


TRANSITIONS: Dict[str, Dict[str, Set[str]]] = {
    ExecutionState.IDLE: {
        ExecutionEvent.ORDER_CREATED: {ExecutionState.CREATED},
        ExecutionEvent.SIGNAL_ACCEPTED: {ExecutionState.ORDER_PENDING},
        ExecutionEvent.ORDER_REJECTED: {ExecutionState.REJECTED},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.CREATED: {
        ExecutionEvent.ORDER_VALIDATED: {ExecutionState.VALIDATED},
        ExecutionEvent.ORDER_REJECTED: {ExecutionState.REJECTED},
        ExecutionEvent.ORDER_ERROR: {ExecutionState.ERROR},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.VALIDATED: {
        ExecutionEvent.ORDER_SUBMITTING: {ExecutionState.SUBMITTING},
        ExecutionEvent.ORDER_REJECTED: {ExecutionState.REJECTED},
        ExecutionEvent.ORDER_ERROR: {ExecutionState.ERROR},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.SUBMITTING: {
        ExecutionEvent.ORDER_SUBMITTED: {ExecutionState.SUBMITTED},
        ExecutionEvent.ORDER_TIMEOUT: {ExecutionState.TIMEOUT},
        ExecutionEvent.ORDER_REJECTED: {ExecutionState.REJECTED},
        ExecutionEvent.ORDER_ERROR: {ExecutionState.ERROR},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.SUBMITTED: {
        ExecutionEvent.ORDER_PARTIALLY_FILLED: {ExecutionState.PARTIALLY_FILLED},
        ExecutionEvent.ORDER_FILLED: {ExecutionState.FILLED},
        ExecutionEvent.ORDER_CANCELLED: {ExecutionState.CANCELLED},
        ExecutionEvent.ORDER_REJECTED: {ExecutionState.REJECTED},
        ExecutionEvent.ORDER_TIMEOUT: {ExecutionState.TIMEOUT},
        ExecutionEvent.RECOVERY_STARTED: {ExecutionState.RECOVERY},
        ExecutionEvent.ORDER_ERROR: {ExecutionState.ERROR},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.PARTIALLY_FILLED: {
        ExecutionEvent.ORDER_PARTIALLY_FILLED: {ExecutionState.PARTIALLY_FILLED},
        ExecutionEvent.ORDER_FILLED: {ExecutionState.FILLED},
        ExecutionEvent.ORDER_CANCELLED: {ExecutionState.CANCELLED},
        ExecutionEvent.ORDER_TIMEOUT: {ExecutionState.TIMEOUT},
        ExecutionEvent.RECOVERY_STARTED: {ExecutionState.RECOVERY},
        ExecutionEvent.ORDER_ERROR: {ExecutionState.ERROR},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.TIMEOUT: {
        ExecutionEvent.RECOVERY_STARTED: {ExecutionState.RECOVERY},
        ExecutionEvent.RETRY_STARTED: {ExecutionState.RETRYING},
        ExecutionEvent.ORDER_CANCELLED: {ExecutionState.CANCELLED},
        ExecutionEvent.ORDER_ERROR: {ExecutionState.ERROR},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.RECOVERY: {
        ExecutionEvent.ORDER_SUBMITTED: {ExecutionState.SUBMITTED},
        ExecutionEvent.ORDER_PARTIALLY_FILLED: {ExecutionState.PARTIALLY_FILLED},
        ExecutionEvent.ORDER_FILLED: {ExecutionState.FILLED},
        ExecutionEvent.ORDER_CANCELLED: {ExecutionState.CANCELLED},
        ExecutionEvent.ORDER_REJECTED: {ExecutionState.REJECTED},
        ExecutionEvent.RETRY_STARTED: {ExecutionState.RETRYING},
        ExecutionEvent.ORDER_ERROR: {ExecutionState.ERROR},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.RETRYING: {
        ExecutionEvent.ORDER_SUBMITTED: {ExecutionState.SUBMITTED},
        ExecutionEvent.ORDER_TIMEOUT: {ExecutionState.TIMEOUT},
        ExecutionEvent.RECOVERY_STARTED: {ExecutionState.RECOVERY},
        ExecutionEvent.ORDER_ERROR: {ExecutionState.ERROR},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.CANCELLED: {
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.ERROR: {
        ExecutionEvent.RECOVERY_STARTED: {ExecutionState.RECOVERY},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.ORDER_PENDING: {
        ExecutionEvent.ORDER_FILLED: {ExecutionState.FILLED},
        ExecutionEvent.ORDER_PARTIALLY_FILLED: {ExecutionState.PARTIAL},
        ExecutionEvent.ORDER_REJECTED: {ExecutionState.REJECTED},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.FILLED: {
        ExecutionEvent.POSITION_OPENED: {ExecutionState.POSITION_OPEN},
        ExecutionEvent.POSITION_CLOSED: {ExecutionState.EXIT},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.PARTIAL: {
        ExecutionEvent.POSITION_OPENED: {ExecutionState.POSITION_OPEN},
        ExecutionEvent.POSITION_CLOSED: {ExecutionState.EXIT},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.REJECTED: {
        ExecutionEvent.SIGNAL_ACCEPTED: {ExecutionState.ORDER_PENDING},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.POSITION_OPEN: {
        ExecutionEvent.SIGNAL_ACCEPTED: {ExecutionState.ORDER_PENDING},
        ExecutionEvent.POSITION_CLOSED: {ExecutionState.EXIT},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
    ExecutionState.EXIT: {
        ExecutionEvent.SIGNAL_ACCEPTED: {ExecutionState.ORDER_PENDING},
        ExecutionEvent.RESET: {ExecutionState.IDLE},
    },
}


@dataclass(frozen=True)
class ExecutionTransitionRecord:
    sequence: int
    event: str
    from_state: str
    to_state: str
    client_order_id: str | None = None
    broker_order_id: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event": self.event,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ExecutionTransitionRecord":
        return cls(
            sequence=int(payload["sequence"]),
            event=str(payload["event"]),
            from_state=str(payload["from_state"]),
            to_state=str(payload["to_state"]),
            client_order_id=payload.get("client_order_id"),
            broker_order_id=payload.get("broker_order_id"),
            reason=payload.get("reason"),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass
class ExecutionStateMachine:
    state: str = ExecutionState.IDLE
    history: list[str] = field(default_factory=list)
    audit_trail: list[ExecutionTransitionRecord] = field(default_factory=list)
    active_client_order_id: str | None = None
    broker_submit_client_order_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.history:
            self.history.append(self.state)

    def transition(
        self,
        event: str,
        next_state: str,
        *,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        allowed = TRANSITIONS.get(self.state, {}).get(event, set())
        if next_state not in allowed:
            raise ValueError(
                f"invalid execution transition: {self.state} --{event}--> {next_state}"
            )

        self._validate_client_order_id(client_order_id, next_state)
        from_state = self.state
        self.state = next_state
        self.history.append(next_state)
        if event == ExecutionEvent.ORDER_SUBMITTING and client_order_id:
            self.broker_submit_client_order_ids.add(client_order_id)
        if next_state == ExecutionState.IDLE:
            self.active_client_order_id = None

        self.audit_trail.append(
            ExecutionTransitionRecord(
                sequence=len(self.audit_trail) + 1,
                event=event,
                from_state=from_state,
                to_state=next_state,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                reason=reason,
                metadata=dict(metadata or {}),
            )
        )
        return self.state

    def apply_event(
        self,
        event: str,
        *,
        next_state: str | None = None,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        allowed = self.next_states(event)
        if next_state is None:
            if len(allowed) != 1:
                raise ValueError(
                    f"execution event requires explicit next_state: {self.state} --{event}"
                )
            next_state = next(iter(allowed))

        return self.transition(
            event,
            next_state,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            reason=reason,
            metadata=metadata,
        )

    def can_transition(self, event: str, next_state: str) -> bool:
        return next_state in self.next_states(event)

    def next_states(self, event: str) -> set[str]:
        return set(TRANSITIONS.get(self.state, {}).get(event, set()))

    def broker_submit_intent_count(self, client_order_id: str | None = None) -> int:
        if client_order_id is None:
            return len(self.broker_submit_client_order_ids)
        return 1 if client_order_id in self.broker_submit_client_order_ids else 0

    def to_audit_log(self) -> list[dict[str, Any]]:
        return [record.to_payload() for record in self.audit_trail]

    @classmethod
    def replay(
        cls,
        audit_log: Iterable[dict[str, Any] | ExecutionTransitionRecord],
        *,
        initial_state: str = ExecutionState.IDLE,
    ) -> "ExecutionStateMachine":
        machine = cls(state=initial_state)
        for item in audit_log:
            record = (
                item
                if isinstance(item, ExecutionTransitionRecord)
                else ExecutionTransitionRecord.from_payload(item)
            )
            if record.from_state != machine.state:
                raise ValueError(
                    "execution audit replay mismatch: "
                    f"expected {machine.state}, got {record.from_state}"
                )
            machine.transition(
                record.event,
                record.to_state,
                client_order_id=record.client_order_id,
                broker_order_id=record.broker_order_id,
                reason=record.reason,
                metadata=record.metadata,
            )
        return machine

    def _validate_client_order_id(
        self,
        client_order_id: str | None,
        next_state: str,
    ) -> None:
        if client_order_id is None or next_state not in ORDER_LIFECYCLE_STATES:
            return

        if self.active_client_order_id is None:
            self.active_client_order_id = client_order_id
            return

        if (
            self.state in OPEN_ORDER_STATES
            and client_order_id != self.active_client_order_id
        ):
            raise ValueError(
                "client_order_id mismatch during open order lifecycle: "
                f"{self.active_client_order_id} != {client_order_id}"
            )
