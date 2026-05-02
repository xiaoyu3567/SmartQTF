import json

import pytest
from pydantic import ValidationError

from quant.decision import AIDecisionAdvisor, AIDecisionSuggestionSandbox, FixtureAIClient
from quant.schemas import (
    AIDecisionAdvisorRequest,
    AIDecisionSuggestion,
    AssetClass,
    DecisionAction,
    DecisionIntent,
    DecisionStopLossTarget,
    DecisionTakeProfitTarget,
    FeatureSnapshot,
    MarketType,
    OrderKind,
    PayloadSource,
    RegimeKind,
    RegimeSnapshot,
    StrategySignal,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def test_decision_intent_accepts_open_close_and_hold_actions_with_replayable_targets():
    open_long = _decision(DecisionAction.OPEN_LONG)
    close_long = _decision(DecisionAction.CLOSE_LONG, reduce_only=True)
    open_short = _decision(DecisionAction.OPEN_SHORT)
    close_short = _decision(DecisionAction.CLOSE_SHORT, reduce_only=True)
    hold = _decision(DecisionAction.HOLD, quantity=0.000001)

    assert DecisionIntent.from_payload(open_long.to_payload()) == open_long
    assert close_long.to_order_intent(risk_approved=True).reduce_only is True
    assert close_long.to_order_intent(risk_approved=True).side == _value(TradeSide.SELL)
    assert close_short.to_order_intent(risk_approved=True).side == _value(TradeSide.BUY)
    assert open_short.to_order_intent(risk_approved=True).side == _value(TradeSide.SELL)

    with pytest.raises(ValueError, match="hold decisions"):
        hold.to_order_intent(risk_approved=True)


def test_decision_intent_rejects_invalid_action_confidence_quantity_and_targets():
    payload = _decision(DecisionAction.OPEN_LONG).to_payload()

    with pytest.raises(ValidationError):
        DecisionIntent.from_payload({**payload, "action": "moon"})

    with pytest.raises(ValidationError):
        DecisionIntent.from_payload({**payload, "confidence": -0.1})

    with pytest.raises(ValidationError):
        DecisionIntent.from_payload({**payload, "quantity": -1.0})

    with pytest.raises(ValidationError):
        DecisionIntent.from_payload(
            {
                **payload,
                "take_profit_targets": [
                    DecisionTakeProfitTarget(price=105.0, quantity_pct=0.7).to_payload(),
                    DecisionTakeProfitTarget(price=110.0, quantity_pct=0.6).to_payload(),
                ],
            }
        )

    with pytest.raises(ValidationError):
        DecisionStopLossTarget(price=0.0, quantity_pct=0.5)

    with pytest.raises(ValidationError):
        DecisionIntent.from_payload({**payload, "order_type": OrderKind.LIMIT, "limit_price": None})


def test_ai_decision_suggestion_schema_and_sandbox_reject_non_advice_payloads():
    sandbox = AIDecisionSuggestionSandbox()
    suggestion = sandbox.evaluate(_suggestion_payload())

    assert AIDecisionSuggestion.from_payload(suggestion.to_payload()) == suggestion
    assert suggestion.candidate.trace.run_id == "decision-contract-001"

    for key in ("risk_approved", "execution", "order_intent"):
        payload = _suggestion_payload()
        payload["candidate"][key] = True
        with pytest.raises(ValueError, match="execution directive"):
            sandbox.evaluate(payload)

    for missing in ("confidence", "trace"):
        payload = _suggestion_payload()
        payload["candidate"].pop(missing)
        with pytest.raises(ValidationError):
            sandbox.evaluate(payload)

    payload = _suggestion_payload()
    payload["candidate"]["reason_codes"] = []
    with pytest.raises(ValidationError):
        sandbox.evaluate(payload)


def test_ai_advisor_rejects_symbol_and_trace_drift_from_model_output():
    advisor = AIDecisionAdvisor(_RecordingAIClient(_suggestion_payload(symbol="ETHUSDT")))

    with pytest.raises(ValueError, match="symbol does not match"):
        advisor.request_suggestion(_advisor_request())

    advisor = AIDecisionAdvisor(
        _RecordingAIClient(_suggestion_payload(trace_overrides={"run_id": "other-run"}))
    )

    with pytest.raises(ValueError, match="trace run_id does not match"):
        advisor.request_suggestion(_advisor_request())


def test_strategy_signal_regime_and_feature_context_build_read_only_ai_request():
    signal = StrategySignal(
        signal_id="ma_crossover:2:buy",
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        side=TradeSide.BUY,
        signal_index=2,
        symbol="BTCUSDT",
        timeframe="1m",
        confidence=0.68,
        reason_codes=["ma_cross"],
        trace=_trace(),
    )
    feature = FeatureSnapshot(
        snapshot_id="feature-decision-contract-001",
        timestamp=1710000000,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000000,
        feature_set_id="technical",
        feature_set_version="1.0.0",
        values={"ma_fast": 101.0, "ma_slow": 100.0},
        trace=_trace(),
    )
    regime = RegimeSnapshot(
        regime_id="regime-decision-contract-001",
        timestamp=1710000000,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000000,
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        regime=RegimeKind.TREND,
        confidence=0.7,
        reason_codes=["trend_threshold_exceeded"],
        trace=_trace(),
    )
    request = _advisor_request(
        feature_context=feature.to_payload(),
        regime_context=regime.to_payload(),
        strategy_context=signal.to_payload(),
        portfolio_context={"position_side": "flat", "available_cash": 10000.0},
    )
    client = _RecordingAIClient(_suggestion_payload())
    advisor = AIDecisionAdvisor(client)

    advisor.request_suggestion(request)
    user_payload = json.loads(client.calls[0]["messages"][1]["content"])["request"]

    assert user_payload["strategy_context"]["signal_id"] == "ma_crossover:2:buy"
    assert user_payload["feature_context"]["snapshot_id"] == "feature-decision-contract-001"
    assert user_payload["regime_context"]["regime"] == "trend"
    assert user_payload["portfolio_context"]["position_side"] == "flat"
    assert user_payload["trace"]["run_id"] == "decision-contract-001"


def test_fixture_ai_client_keeps_decision_advisor_boundary_deterministic(tmp_path):
    fixture_path = tmp_path / "ai-suggestion.json"
    fixture_path.write_text(json.dumps(_suggestion_payload()), encoding="utf-8")

    suggestion = AIDecisionAdvisor(FixtureAIClient.from_path(fixture_path)).request_suggestion(
        _advisor_request()
    )

    assert suggestion.candidate.decision_id == "ai-decision-contract-001"
    assert suggestion.metadata["response_source"] == "ai_model"


def _decision(action, *, quantity=0.25, reduce_only=False):
    return DecisionIntent(
        decision_id=f"decision-contract-{_value(action)}",
        timestamp=1710000000,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.SPOT,
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        regime="trend",
        action=action,
        order_type=OrderKind.MARKET,
        quantity=quantity,
        stop_loss=95.0,
        take_profit=110.0,
        stop_loss_targets=[DecisionStopLossTarget(price=95.0, quantity_pct=1.0)],
        take_profit_targets=[
            DecisionTakeProfitTarget(price=105.0, quantity_pct=0.5, reason_code="tp1"),
            DecisionTakeProfitTarget(price=110.0, quantity_pct=0.5, reason_code="tp2"),
        ],
        time_in_force=TimeInForce.GTC,
        reduce_only=reduce_only,
        confidence=0.72,
        reason_codes=["strategy_signal_confirmed"],
        trace=_trace(),
    )


def _suggestion_payload(**candidate_overrides):
    trace_overrides = candidate_overrides.pop("trace_overrides", {})
    trace = _trace().to_payload()
    trace.update(trace_overrides)
    candidate = _decision(DecisionAction.OPEN_LONG).to_payload()
    candidate.update(
        {
            "decision_id": "ai-decision-contract-001",
            "strategy_id": "ai_decision_advisor",
            "strategy_version": "1.0.0",
            "trace": trace,
        }
    )
    candidate.update(candidate_overrides)
    return {
        "suggestion_id": "ai-suggestion-contract-001",
        "timestamp": 1710000000,
        "candidate": candidate,
        "metadata": {"review_only": True},
    }


def _advisor_request(**overrides):
    payload = {
        "request_id": "ai-request-contract-001",
        "timestamp": 1710000000,
        "symbol": "BTCUSDT",
        "asset_class": AssetClass.CRYPTO,
        "market_type": MarketType.SPOT,
        "timeframe": "1m",
        "model_name": "fixture-json-model",
        "trace": _trace(),
        "constraints": {"must_remain_advice_only": True},
    }
    payload.update(overrides)
    return AIDecisionAdvisorRequest(**payload)


def _trace():
    return TraceContext(
        run_id="decision-contract-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000000,
        bar_index=2,
    )


def _value(value):
    return getattr(value, "value", value)


class _RecordingAIClient:
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
