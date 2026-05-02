import pytest
from pydantic import ValidationError

from quant.decision import DecisionEngine
from quant.schemas import (
    AssetClass,
    DecisionAction,
    DecisionEngineRequest,
    DecisionEngineResult,
    DecisionPolicy,
    DecisionPortfolioState,
    MarketType,
    PayloadSource,
    PositionSide,
    RegimeKind,
    RegimeSnapshot,
    StrategyAction,
    StrategySignal,
    TraceContext,
    TradeIntent,
    TradeSide,
)


def test_decision_engine_approves_orderable_buy_as_trade_intent_without_quantity():
    request = _request(
        candidate_order={
            "entry_price": 65000.0,
            "stop_loss": 64000.0,
            "take_profit": 68000.0,
        }
    )

    result = DecisionEngine().evaluate(request)

    assert result.decision_action == "APPROVE_TRADE_INTENT"
    assert result.forward_to_capital_allocation is True
    assert result.trade_intent is not None
    assert result.trade_intent.action == _value(DecisionAction.OPEN_LONG)
    assert result.trade_intent.side == _value(TradeSide.BUY)
    assert result.trade_intent.entry_price == 65000.0
    assert result.trade_intent.source_signal_id == "signal-decision-engine-001"
    assert result.safety == {
        "network_used": False,
        "ai_provider_called": False,
        "broker_called": False,
        "live_orders_sent": False,
        "risk_bypassed": False,
    }
    payload = result.trade_intent.to_payload()
    assert "quantity" not in payload
    assert "client_order_id" not in payload
    assert "risk_approved" not in payload


def test_decision_engine_keeps_wait_and_no_trade_out_of_capital_and_risk():
    for action in (StrategyAction.WAIT, StrategyAction.NO_TRADE):
        result = DecisionEngine().evaluate(
            _request(
                signal=_signal(
                    action=action,
                    side=None,
                    trade_now=False,
                    should_send_order=False,
                )
            )
        )

        assert result.decision_action == "WATCH"
        assert result.trade_intent is None
        assert result.forward_to_capital_allocation is False
        assert f"strategy_action_{_value(action)}" in result.reason_codes


def test_decision_engine_rejects_policy_and_portfolio_gate_failures():
    result = DecisionEngine().evaluate(
        _request(
            timestamp=1_710_000_005_000,
            signal=_signal(confidence=0.40),
            policy=DecisionPolicy(
                min_confidence=0.7,
                cooldown_ms=10_000,
                daily_trade_limit=2,
                max_signal_age_ms=1_000,
                blocked_symbols=["BTCUSDT"],
                blocked_regimes=["trend"],
            ),
            portfolio_state=DecisionPortfolioState(
                symbol="BTCUSDT",
                position_side=PositionSide.LONG,
                open_position_quantity=0.5,
                last_trade_timestamp=1_710_000_004_500,
                trades_today=2,
                open_order_client_ids=["existing-client-order"],
            ),
        )
    )

    assert result.decision_action == "REJECT"
    assert result.trade_intent is None
    assert result.forward_to_capital_allocation is False
    assert set(result.reason_codes) >= {
        "symbol_blocked",
        "confidence_below_minimum",
        "regime_blocked",
        "signal_expired",
        "cooldown_active",
        "daily_trade_limit_reached",
        "open_order_conflict",
        "position_conflict_long_already_open",
    }


def test_decision_engine_rejects_kill_switch_and_bearish_regime_alignment():
    result = DecisionEngine().evaluate(
        _request(
            kill_switch_active=True,
            regime_snapshot=_regime(direction="bearish"),
        )
    )

    assert result.decision_action == "REJECT"
    assert "kill_switch_active" in result.reason_codes
    assert "regime_alignment_failed" in result.reason_codes
    assert result.safety["broker_called"] is False
    assert result.safety["live_orders_sent"] is False


def test_decision_engine_rejects_candidate_order_executable_fields():
    result = DecisionEngine().evaluate(
        _request(candidate_order={"quantity": 1.0, "client_order_id": "bad"})
    )

    assert result.decision_action == "REJECT"
    assert result.trade_intent is None
    assert result.forward_to_capital_allocation is False
    assert result.reason_codes == ["candidate_order_contains_executable_fields"]

    payload = _trade_intent().to_payload()
    with pytest.raises(ValidationError, match="executable order fields"):
        TradeIntent.from_payload({**payload, "quantity": 1.0})


def test_decision_engine_result_contract_rejects_forward_without_trade_intent():
    with pytest.raises(ValidationError):
        DecisionEngineResult(
            result_id="bad-result",
            timestamp=1_710_000_000_000,
            symbol="BTCUSDT",
            decision_action="WATCH",
            forward_to_capital_allocation=True,
        )


def _request(
    *,
    timestamp=1_710_000_000_000,
    signal=None,
    regime_snapshot=None,
    portfolio_state=None,
    policy=None,
    candidate_order=None,
    kill_switch_active=False,
):
    return DecisionEngineRequest(
        request_id="decision-engine-request-001",
        timestamp=timestamp,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.PERPETUAL,
        timeframe="5m",
        signal=signal or _signal(),
        regime_snapshot=regime_snapshot or _regime(),
        portfolio_state=portfolio_state
        or DecisionPortfolioState(symbol="BTCUSDT", position_side=PositionSide.FLAT),
        policy=policy or DecisionPolicy(min_confidence=0.6, allowed_regimes=["trend"]),
        candidate_order=candidate_order or {},
        kill_switch_active=kill_switch_active,
        trace=_trace(),
    )


def _signal(
    *,
    action=StrategyAction.BUY,
    side=TradeSide.BUY,
    confidence=0.74,
    trade_now=True,
    should_send_order=True,
):
    return StrategySignal(
        signal_id="signal-decision-engine-001",
        strategy_id="trend_pullback_long_v1",
        strategy_version="1.0.0",
        side=side,
        action=action,
        signal_type="BREAKOUT_CONFIRMED",
        signal_index=10,
        execute_index=11,
        symbol="BTCUSDT",
        timeframe="5m",
        confidence=confidence,
        reason_codes=["breakout_confirmed"],
        trade_now=trade_now,
        should_send_order=should_send_order,
        trace=_trace(),
    )


def _regime(*, direction="bullish", tradability="tradable", regime=RegimeKind.TREND):
    return RegimeSnapshot(
        regime_id="regime-decision-engine-001",
        timestamp=1_710_000_000_000,
        symbol="BTCUSDT",
        timeframe="5m",
        as_of_timestamp=1_710_000_000_000,
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        regime=regime,
        confidence=0.7,
        reason_codes=["trend_confirmed"],
        direction=direction,
        tradability=tradability,
        trace=_trace(),
    )


def _trade_intent():
    return TradeIntent(
        trade_intent_id="trade-intent-001",
        decision_id="decision-001",
        timestamp=1_710_000_000_000,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.PERPETUAL,
        side=TradeSide.BUY,
        action=DecisionAction.OPEN_LONG,
        strategy_id="trend_pullback_long_v1",
        strategy_version="1.0.0",
        timeframe="5m",
        regime="trend",
        confidence=0.74,
        source_signal_id="signal-decision-engine-001",
        reason_codes=["decision_policy_approved"],
        trace=_trace(),
    )


def _trace():
    return TraceContext(
        run_id="decision-engine-test-run",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="5m",
        timestamp=1_710_000_000_000,
        bar_index=10,
    )


def _value(value):
    return getattr(value, "value", value)
