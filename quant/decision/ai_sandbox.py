from typing import Any, Mapping

from quant.schemas import AIDecisionSuggestion, AIDecisionSuggestionLogRecord, TraceContext


DISALLOWED_AI_DIRECTIVE_KEYS = {
    "approved",
    "broker",
    "broker_order_id",
    "client_order_id",
    "execution",
    "execution_handler",
    "order_intent",
    "order_payload",
    "place_order",
    "portfolio_allocation",
    "risk_approved",
}

ALLOWED_AI_SUGGESTION_KEYS = {
    "advisor_name",
    "candidate",
    "metadata",
    "model_name",
    "prompt_hash",
    "prompt_id",
    "raw_response_hash",
    "sandbox_version",
    "schema_version",
    "suggestion_id",
    "timestamp",
}


class AIDecisionSuggestionSandbox:
    """Validate AI output as replayable advice, never as executable order state."""

    def __init__(self, sandbox_version: str = "1.0"):
        if not sandbox_version:
            raise ValueError("sandbox_version must not be empty")
        self.sandbox_version = sandbox_version

    def evaluate(self, payload: Mapping[str, Any]) -> AIDecisionSuggestion:
        if not isinstance(payload, Mapping):
            raise ValueError("AI decision suggestion payload must be a mapping")

        self._reject_disallowed_directives(payload)
        unknown_keys = set(payload) - ALLOWED_AI_SUGGESTION_KEYS
        if unknown_keys:
            names = ", ".join(sorted(unknown_keys))
            raise ValueError(f"unknown AI decision suggestion fields: {names}")

        normalized = dict(payload)
        normalized.setdefault("sandbox_version", self.sandbox_version)
        return AIDecisionSuggestion.from_payload(normalized)

    def to_log_record(
        self,
        suggestion: AIDecisionSuggestion,
        event_id: str,
        run_id: str | None = None,
        timestamp: int | None = None,
        trace: TraceContext | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AIDecisionSuggestionLogRecord:
        record_trace = trace or suggestion.candidate.trace
        if record_trace is None:
            raise ValueError("AI decision suggestion log requires trace")
        return AIDecisionSuggestionLogRecord(
            event_id=event_id,
            run_id=run_id or record_trace.run_id,
            timestamp=timestamp if timestamp is not None else suggestion.timestamp,
            trace=record_trace,
            suggestion=suggestion,
            metadata=dict(metadata or {}),
        )

    def _reject_disallowed_directives(self, value: Any, path: str = "payload") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                if key_text in DISALLOWED_AI_DIRECTIVE_KEYS:
                    raise ValueError(f"AI suggestion cannot include execution directive: {path}.{key_text}")
                self._reject_disallowed_directives(item, f"{path}.{key_text}")
            return

        if isinstance(value, list):
            for index, item in enumerate(value):
                self._reject_disallowed_directives(item, f"{path}[{index}]")
