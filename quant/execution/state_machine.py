from dataclasses import dataclass, field
from typing import Dict, Set


class ExecutionState:
    IDLE = "IDLE"
    ORDER_PENDING = "ORDER_PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    POSITION_OPEN = "POSITION_OPEN"
    EXIT = "EXIT"


class ExecutionEvent:
    SIGNAL_ACCEPTED = "signal_accepted"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    ORDER_REJECTED = "order_rejected"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    RESET = "reset"


TRANSITIONS: Dict[str, Dict[str, Set[str]]] = {
    ExecutionState.IDLE: {
        ExecutionEvent.SIGNAL_ACCEPTED: {ExecutionState.ORDER_PENDING},
        ExecutionEvent.ORDER_REJECTED: {ExecutionState.REJECTED},
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


@dataclass
class ExecutionStateMachine:
    state: str = ExecutionState.IDLE
    history: list[str] = field(default_factory=lambda: [ExecutionState.IDLE])

    def transition(self, event: str, next_state: str) -> str:
        allowed = TRANSITIONS.get(self.state, {}).get(event, set())
        if next_state not in allowed:
            raise ValueError(
                f"invalid execution transition: {self.state} --{event}--> {next_state}"
            )

        self.state = next_state
        self.history.append(next_state)
        return self.state
