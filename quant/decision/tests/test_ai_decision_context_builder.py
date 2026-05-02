import json

import pytest

from quant.data.quality import validate_klines
from quant.data.schemas.market import Kline
from quant.decision import AIDecisionAdvisor, AIDecisionContextBuilder, AIDecisionContextBuildInput
from quant.schemas import (
    AssetClass,
    DecisionAction,
    DecisionIntent,
    FeatureSnapshot,
    MarketType,
    OrderKind,
    PayloadSource,
    RegimeKind,
    RegimeSnapshot,
    StrategyAction,
    StrategySignal,
    TraceContext,
    TradeSide,
)


def test_ai_decision_context_builder_combines_replayable_market_window_and_typed_contexts():
    request = AIDecisionContextBuilder().build(
        AIDecisionContextBuildInput(
            request_id="ai-context-request-001",
            timestamp=1710000120,
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            market_type=MarketType.SPOT,
            timeframe="1m",
            model_name="fixture-json-model",
            trace=_trace(),
            klines=_klines(),
            quality_report=validate_klines(_klines(), "BTCUSDT", "1m"),
            feature_snapshot=_feature_snapshot(),
            regime_snapshot=_regime_snapshot(),
            strategy_signal=_strategy_signal(),
            portfolio_context={"position_side": "flat", "available_cash": 10000.0},
            risk_constraints={"max_notional": 500.0, "max_position_pct": 0.1},
            safety_constraints={"kill_switch_active": False, "live_trading_enabled": False},
        )
    )

    payload = request.to_payload()
    market_context = payload["market_context"]

    assert len(market_context["klines_window"]) == 3
    assert market_context["klines_window"][0]["time"] == "2024-3-9 16:00"
    assert market_context["selected_index"] == 1
    assert market_context["effective_index"] == 1
    assert market_context["requested_index"] == 2
    assert market_context["selected_kline"]["timestamp"] == 1710000060
    assert market_context["quality_report"]["passed"] is True
    assert market_context["quality_report"]["has_incomplete_last_bar"] is True
    assert market_context["incomplete_bar_policy"]["feature_skipped_incomplete_last_bar"] is True
    assert market_context["incomplete_bar_policy"]["feature_include_incomplete_last_bar"] is False

    feature_context = payload["feature_context"]
    assert feature_context["feature_snapshot_id"] == "feature-ai-context-001"
    assert "klines_window" not in feature_context["feature_snapshot"]
    assert feature_context["feature_snapshot"]["effective_index"] == 1
    assert payload["regime_context"]["regime"] == "trend"
    assert payload["strategy_context"]["signal_id"] == "trend-following:btc:001"
    assert payload["constraints"]["advice_only"] is True
    assert payload["constraints"]["risk_gate_required_after_ai"] is True
    assert payload["constraints"]["portfolio_gate_required_after_risk"] is True
    assert payload["constraints"]["safety"]["broker_called_by_builder"] is False
    assert payload["metadata"]["context_builder"] == "ai_decision_context_builder"


def test_ai_decision_context_builder_output_passes_advisor_boundary_without_real_ai():
    request = AIDecisionContextBuilder().build(
        AIDecisionContextBuildInput(
            request_id="ai-context-request-002",
            timestamp=1710000120,
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            model_name="fixture-json-model",
            trace=_trace(),
            klines=_klines(),
            quality_report=validate_klines(_klines(), "BTCUSDT", "1m"),
            feature_snapshot=_feature_snapshot(),
            regime_snapshot=_regime_snapshot(),
            strategy_signal=_strategy_signal(),
        )
    )
    client = _RecordingAIClient(_suggestion_payload())
    suggestion = AIDecisionAdvisor(client).request_suggestion(request)
    user_payload = json.loads(client.calls[0]["messages"][1]["content"])["request"]

    assert suggestion.candidate.decision_id == "ai-decision-context-001"
    assert user_payload["market_context"]["klines_window"][2]["is_complete"] is False
    assert user_payload["feature_context"]["feature_snapshot"]["snapshot_id"] == "feature-ai-context-001"
    assert user_payload["constraints"]["ai_may_not_submit_orders"] is True


def test_ai_decision_context_builder_rejects_context_execution_directives():
    with pytest.raises(ValueError, match="execution directive"):
        AIDecisionContextBuilder().build(
            AIDecisionContextBuildInput(
                request_id="ai-context-request-003",
                timestamp=1710000120,
                symbol="BTCUSDT",
                asset_class=AssetClass.CRYPTO,
                model_name="fixture-json-model",
                trace=_trace(),
                klines=_klines(),
                quality_report=validate_klines(_klines(), "BTCUSDT", "1m"),
                feature_snapshot=_feature_snapshot(),
                safety_constraints={"place_order": True},
            )
        )


def _klines():
    return [
        Kline(timestamp=1710000000, open=100.0, high=102.0, low=99.0, close=101.0, volume=10.0, is_complete=True),
        Kline(timestamp=1710000060, open=101.0, high=104.0, low=100.5, close=103.0, volume=12.0, is_complete=True),
        Kline(timestamp=1710000120, open=103.0, high=105.0, low=102.0, close=104.0, volume=9.0, is_complete=False),
    ]


def _feature_snapshot():
    return FeatureSnapshot(
        snapshot_id="feature-ai-context-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000060,
        feature_set_id="advanced_alpha",
        feature_set_version="1.0",
        values={"close": 103.0, "rsi": None, "atr": None, "ma_fast": 102.0, "ma_slow": 101.5},
        feature_availability={
            "rsi": {"feature_name": "rsi", "available": False, "reason": "insufficient_history", "required_bars": 15, "actual_bars": 2},
            "atr": {"feature_name": "atr", "available": False, "reason": "insufficient_history", "required_bars": 15, "actual_bars": 2},
        },
        feature_parameters={"rsi": {"window": 14}, "atr": {"window": 14}},
        source_window_start=1710000000,
        source_window_end=1710000060,
        is_complete_bar=True,
        requested_index=2,
        effective_index=1,
        input_bar_count=3,
        include_incomplete_last_bar=False,
        skipped_incomplete_last_bar=True,
        skipped_incomplete_bar_timestamp=1710000120,
        trace=_trace(),
    )


def _regime_snapshot():
    return RegimeSnapshot(
        regime_id="regime-ai-context-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000060,
        detector_id="rule_based_regime",
        detector_version="1.0",
        regime=RegimeKind.TREND,
        confidence=0.7,
        reason_codes=["trend_threshold_exceeded"],
        metrics={"trend_strength": 0.8},
        source_window_start=1710000000,
        source_window_end=1710000060,
        input_refs={"feature_snapshot_id": "feature-ai-context-001"},
        tradability="tradable",
        trace=_trace(),
    )


def _strategy_signal():
    return StrategySignal(
        signal_id="trend-following:btc:001",
        strategy_id="trend_following",
        strategy_version="1.0",
        side=TradeSide.BUY,
        action=StrategyAction.BUY,
        signal_type="EXECUTE",
        signal_index=1,
        symbol="BTCUSDT",
        timeframe="1m",
        confidence=0.62,
        reason_codes=["trend_confirmed"],
        trade_now=True,
        should_send_order=True,
        trace=_trace(),
    )


def _suggestion_payload():
    return {
        "suggestion_id": "ai-suggestion-context-001",
        "timestamp": 1710000120,
        "candidate": DecisionIntent(
            decision_id="ai-decision-context-001",
            timestamp=1710000120,
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            market_type=MarketType.SPOT,
            strategy_id="ai_decision_advisor",
            strategy_version="1.0",
            regime="trend",
            action=DecisionAction.HOLD,
            order_type=OrderKind.MARKET,
            quantity=0.000001,
            confidence=0.25,
            reason_codes=["AI_CONTEXT_REVIEW_ONLY"],
            trace=_trace(),
        ).to_payload(),
    }


def _trace():
    return TraceContext(
        run_id="ai-context-run-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000120,
        bar_index=2,
    )


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
