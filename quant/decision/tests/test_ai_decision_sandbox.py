import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from quant.decision import AIDecisionSuggestionSandbox
from quant.logging import JsonlTradeLogger
from quant.schemas import AIDecisionSuggestion, AssetClass, DecisionAction, OrderKind, PayloadSource, TraceContext


def make_ai_payload():
    return {
        "suggestion_id": "ai-suggestion-001",
        "timestamp": 1710000000,
        "advisor_name": "ai_decision_sandbox",
        "model_name": "offline-test-advisor",
        "prompt_id": "decision-advice-v1",
        "prompt_hash": "prompt-hash-001",
        "raw_response_hash": "response-hash-001",
        "candidate": {
            "decision_id": "ai-decision-001",
            "timestamp": 1710000000,
            "symbol": "BTCUSDT",
            "asset_class": AssetClass.CRYPTO,
            "strategy_id": "ma_crossover",
            "strategy_version": "1.0.0",
            "regime": "trend",
            "action": DecisionAction.OPEN_LONG,
            "order_type": OrderKind.LIMIT,
            "quantity": 0.01,
            "limit_price": 65000.0,
            "stop_loss": 64000.0,
            "take_profit": 67500.0,
            "confidence": 0.61,
            "reason_codes": ["ai_trend_confirmation", "ai_risk_reward_ok"],
            "trace": TraceContext(
                run_id="paper-ai-001",
                source=PayloadSource.PAPER,
                symbol="BTCUSDT",
                timeframe="1m",
                timestamp=1710000000,
                bar_index=42,
            ).to_payload(),
        },
        "metadata": {"review_only": True},
    }


def test_ai_decision_sandbox_accepts_replayable_candidate():
    sandbox = AIDecisionSuggestionSandbox()

    suggestion = sandbox.evaluate(make_ai_payload())
    restored = AIDecisionSuggestion.from_payload(suggestion.to_payload())

    assert restored == suggestion
    assert suggestion.candidate.decision_id == "ai-decision-001"
    assert suggestion.candidate.confidence == 0.61
    assert suggestion.candidate.reason_codes == ["ai_trend_confirmation", "ai_risk_reward_ok"]
    assert suggestion.candidate.trace.run_id == "paper-ai-001"


def test_ai_decision_sandbox_rejects_execution_directives():
    payload = make_ai_payload()
    payload["candidate"]["risk_approved"] = True
    sandbox = AIDecisionSuggestionSandbox()

    try:
        sandbox.evaluate(payload)
    except ValueError as exc:
        assert "execution directive" in str(exc)
    else:
        raise AssertionError("AI suggestions must not carry risk or execution directives")


def test_ai_decision_sandbox_requires_confidence_reason_codes_and_trace():
    payload = make_ai_payload()
    payload["candidate"].pop("confidence")
    sandbox = AIDecisionSuggestionSandbox()

    try:
        sandbox.evaluate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("AI suggestions must include candidate confidence")

    payload = make_ai_payload()
    payload["candidate"]["reason_codes"] = []

    try:
        sandbox.evaluate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("AI suggestions must include replayable reason codes")

    payload = make_ai_payload()
    payload["candidate"].pop("trace")

    try:
        sandbox.evaluate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("AI suggestions must include trace context")


def test_ai_decision_suggestion_log_round_trip(tmp_path):
    sandbox = AIDecisionSuggestionSandbox()
    suggestion = sandbox.evaluate(make_ai_payload())
    record = sandbox.to_log_record(suggestion, event_id="event-ai-suggestion-001")

    logger = JsonlTradeLogger(tmp_path / "ai-suggestions.jsonl")
    logger.append(record)
    restored = logger.read_all()

    assert restored == [record]
    assert restored[0].record_type == "ai_decision_suggestion"
    assert restored[0].suggestion.candidate.decision_id == "ai-decision-001"
    assert [item.event_id for item in logger.read_by_type("ai_decision_suggestion")] == [
        "event-ai-suggestion-001"
    ]
