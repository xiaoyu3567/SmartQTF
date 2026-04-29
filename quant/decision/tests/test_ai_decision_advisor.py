import json

from pydantic import ValidationError

from quant.decision import AI_DECISION_OUTPUT_SCHEMA, AIDecisionAdvisor, FixtureAIClient
from quant.schemas import (
    AIDecisionAdvisorRequest,
    AssetClass,
    DecisionAction,
    MarketType,
    OrderKind,
    PayloadSource,
    TraceContext,
)
from scripts import validate_ai_decision_advisor as validate_ai


class RecordingAIClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create_json(self, *, model_name, messages, response_schema):
        self.calls.append(
            {
                "model_name": model_name,
                "messages": messages,
                "response_schema": response_schema,
            }
        )
        return self.payload


def make_request(**overrides):
    payload = {
        "request_id": "ai-advice-request-001",
        "timestamp": 1710000000,
        "symbol": "BTCUSDT",
        "asset_class": AssetClass.CRYPTO,
        "market_type": MarketType.SPOT,
        "timeframe": "1m",
        "model_name": "test-json-model",
        "trace": TraceContext(
            run_id="paper-ai-001",
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=1710000000,
            bar_index=42,
        ),
        "market_context": {"last_price": 65000.0},
        "feature_context": {"ema_fast": 65100.0, "ema_slow": 64950.0},
        "regime_context": {"regime": "trend"},
        "strategy_context": {"strategy_id": "ma_crossover", "signal": "long"},
        "constraints": {"max_confidence": 0.75, "must_remain_advice_only": True},
    }
    payload.update(overrides)
    return AIDecisionAdvisorRequest(**payload)


def make_suggestion_payload(**candidate_overrides):
    candidate = {
        "decision_id": "ai-decision-001",
        "timestamp": 1710000000,
        "symbol": "BTCUSDT",
        "asset_class": AssetClass.CRYPTO,
        "market_type": MarketType.SPOT,
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
    }
    candidate.update(candidate_overrides)
    return {
        "suggestion_id": "ai-suggestion-001",
        "timestamp": 1710000000,
        "candidate": candidate,
        "metadata": {"review_only": True},
    }


def test_ai_decision_advisor_calls_model_and_sandboxes_replayable_suggestion():
    client = RecordingAIClient(make_suggestion_payload())
    advisor = AIDecisionAdvisor(client)

    suggestion = advisor.request_suggestion(make_request())

    assert suggestion.candidate.decision_id == "ai-decision-001"
    assert suggestion.model_name == "test-json-model"
    assert suggestion.prompt_id == "smartqtf-ai-decision-advice-v1"
    assert suggestion.prompt_hash is not None
    assert suggestion.raw_response_hash is not None
    assert suggestion.metadata["request_id"] == "ai-advice-request-001"
    assert suggestion.metadata["response_source"] == "ai_model"
    assert client.calls[0]["model_name"] == "test-json-model"
    assert client.calls[0]["response_schema"] == AI_DECISION_OUTPUT_SCHEMA
    assert "Risk, Portfolio, and Execution" in client.calls[0]["messages"][0]["content"]


def test_ai_decision_advisor_accepts_openai_compatible_json_text_response():
    payload = json.dumps(make_suggestion_payload())
    client = RecordingAIClient({"choices": [{"message": {"content": payload}}]})
    advisor = AIDecisionAdvisor(client)

    suggestion = advisor.request_suggestion(make_request())

    assert suggestion.suggestion_id == "ai-suggestion-001"
    assert suggestion.candidate.trace.run_id == "paper-ai-001"


def test_ai_decision_advisor_rejects_execution_directives_from_model_response():
    payload = make_suggestion_payload(risk_approved=True)
    advisor = AIDecisionAdvisor(RecordingAIClient(payload))

    try:
        advisor.request_suggestion(make_request())
    except ValueError as exc:
        assert "execution directive" in str(exc)
    else:
        raise AssertionError("AI advisor must reject risk/execution directives")


def test_ai_decision_advisor_rejects_candidate_context_mismatch():
    payload = make_suggestion_payload(symbol="ETHUSDT")
    advisor = AIDecisionAdvisor(RecordingAIClient(payload))

    try:
        advisor.request_suggestion(make_request())
    except ValueError as exc:
        assert "symbol does not match" in str(exc)
    else:
        raise AssertionError("AI advisor must reject suggestions for another symbol")


def test_ai_decision_advisor_rejects_request_context_directives():
    advisor_request = make_request(metadata={"client_order_id": "ai-must-not-set-this"})
    advisor = AIDecisionAdvisor(RecordingAIClient(make_suggestion_payload()))

    try:
        advisor.request_suggestion(advisor_request)
    except ValueError as exc:
        assert "request cannot include execution directive" in str(exc)
    else:
        raise AssertionError("AI advisor request context must stay read-only")


def test_ai_decision_advisor_request_requires_trace_and_model():
    try:
        AIDecisionAdvisorRequest(
            request_id="missing-model",
            timestamp=1710000000,
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            trace=TraceContext(run_id="paper-ai-001", source=PayloadSource.PAPER, symbol="BTCUSDT"),
            model_name="",
        )
    except (ValidationError, ValueError):
        pass
    else:
        raise AssertionError("AI advisor requests must declare a model name")


def test_fixture_ai_client_can_rehearse_advisor_boundary(tmp_path):
    fixture_path = tmp_path / "ai-response.json"
    fixture_path.write_text(json.dumps(make_suggestion_payload()), encoding="utf-8")
    advisor = AIDecisionAdvisor(FixtureAIClient.from_path(fixture_path))

    suggestion = advisor.request_suggestion(make_request())

    assert suggestion.candidate.reason_codes == ["ai_trend_confirmation", "ai_risk_reward_ok"]


def test_ai_decision_validation_script_writes_fixture_report(tmp_path):
    fixture_path = tmp_path / "ai-response.json"
    output_path = tmp_path / "ai-validation-report.json"
    fixture_path.write_text(json.dumps(make_suggestion_payload()), encoding="utf-8")

    report = validate_ai.run_ai_decision_advisor_validation(
        symbol="BTCUSDT",
        timeframe="1m",
        model_name="fixture-model",
        response_fixture=fixture_path,
        timestamp=1710000000,
        run_id="paper-ai-001",
        output_path=output_path,
    )

    assert report["status"] == "PASS"
    assert report["read_only"] is True
    assert report["live_orders_sent"] is False
    assert report["risk_bypassed"] is False
    assert report["contains_real_credentials"] is False
    assert report["response_source"] == "fixture"
    assert json.loads(output_path.read_text(encoding="utf-8"))["suggestion"]["suggestion_id"] == "ai-suggestion-001"


def test_ai_decision_validation_script_blocks_real_provider_without_proxy(monkeypatch, tmp_path):
    output_path = tmp_path / "ai-validation-report.json"
    monkeypatch.setenv("SMARTQTF_RUN_AI_DECISION_ADVISOR_TEST", "1")
    monkeypatch.delenv("SMARTQTF_USE_PROXY", raising=False)
    monkeypatch.setenv("SMARTQTF_AI_ADVISOR_ENDPOINT", "https://example.invalid/v1/chat/completions")
    monkeypatch.setenv("SMARTQTF_AI_ADVISOR_API_KEY", "test-api-key")

    report = validate_ai.run_ai_decision_advisor_validation(
        symbol="BTCUSDT",
        timeframe="1m",
        model_name="real-provider-model",
        timestamp=1710000000,
        run_id="paper-ai-001",
        output_path=output_path,
    )

    assert report["status"] == "FAIL"
    assert report["response_source"] == "ai_provider"
    assert report["read_only"] is True
    assert report["live_orders_sent"] is False
    assert report["risk_bypassed"] is False
    assert report["contains_real_credentials"] is False
    assert report["checks"][0]["category"] == "proxy"
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "FAIL"
