import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.schemas import (
    PayloadSource,
    RegimeKind,
    RegimeSnapshot,
    StrategyAction,
    StrategyPerformanceFeedback,
    StrategySignal,
    TraceContext,
    TradeSide,
)
from quant.strategy.evaluator import MultiStrategyEvaluator, StrategyPerformanceFeedbackStore
from quant.strategy.router import RegimeStrategyRouter


class FixedSignalStrategy:
    def __init__(self, strategy_id, signal=None, version="1.0.0"):
        self.strategy_id = strategy_id
        self.strategy_version = version
        self.signal = signal

    def generate_signal(self, features, index):
        return self.signal


class StrategyWithRiskDependency:
    strategy_id = "bad_risk_strategy"
    strategy_version = "1.0.0"

    def __init__(self):
        self.risk_manager = object()
        self.generate_calls = 0

    def generate_signal(self, features, index):
        self.generate_calls += 1
        return _signal("bad_risk_strategy", TradeSide.BUY, confidence=0.99)


def test_multi_strategy_evaluator_selects_highest_scored_executable_signal():
    trend_follow = FixedSignalStrategy(
        "trend_follow",
        _signal("trend_follow", TradeSide.BUY, confidence=0.62),
    )
    breakout = FixedSignalStrategy(
        "breakout",
        _signal("breakout", TradeSide.BUY, confidence=0.68),
    )
    wait_for_pullback = FixedSignalStrategy(
        "wait_for_pullback",
        StrategySignal(
            signal_id="wait_for_pullback:2:wait",
            strategy_id="wait_for_pullback",
            strategy_version="1.0.0",
            action=StrategyAction.WAIT,
            signal_type="WAIT_FOR_PULLBACK",
            signal_index=2,
            confidence=0.95,
            reason_codes=["price_too_far_above_ema5"],
            watch_plan={"recheck_on": "next_closed_bar"},
        ),
    )
    router = RegimeStrategyRouter(
        {
            RegimeKind.TREND: [
                trend_follow,
                breakout,
                wait_for_pullback,
            ]
        },
        router_id="pool_router",
    )
    routed_pool = router.route_pool(_snapshot())
    feedback_store = StrategyPerformanceFeedbackStore(
        [
            StrategyPerformanceFeedback(
                feedback_id="fb-breakout-btc-trend",
                strategy_id="breakout",
                symbol="BTCUSDT",
                regime=RegimeKind.TREND,
                performance_score=0.84,
                sample_count=25,
                updated_at=1710000100,
            )
        ]
    )
    evaluator = MultiStrategyEvaluator(
        min_score=0.50,
        symbol_calibration={
            "BTCUSDT": {
                "symbol_calibration_weight": 0.95,
                "risk_score": 0.88,
                "liquidity_score": 0.92,
            },
            "breakout:BTCUSDT:trend": {"symbol_calibration_weight": 1.0},
        },
        feedback_store=feedback_store,
    )

    result = evaluator.evaluate(routed_pool, features={}, index=2, regime=_snapshot())

    assert result.status == "SELECTED_EXECUTABLE"
    assert result.selected_executable is True
    assert result.selected_strategy_id == "breakout"
    assert result.selected_signal.strategy_id == "breakout"
    assert result.selected_signal.symbol == "BTCUSDT"
    assert result.selected_signal.timeframe == "5m"
    assert result.selected_signal.is_orderable is True
    assert result.route_decision["candidate_count"] == 3
    assert result.safety == {
        "network_used": False,
        "broker_called": False,
        "live_orders_sent": False,
        "risk_bypassed": False,
    }

    selected = [candidate for candidate in result.candidates if candidate.candidate_status == "SELECTED"]
    observed = [candidate for candidate in result.candidates if candidate.candidate_status == "OBSERVE_ONLY"]
    rejected = [candidate for candidate in result.candidates if candidate.candidate_status == "REJECTED"]
    assert [candidate.strategy_id for candidate in selected] == ["breakout"]
    assert [candidate.strategy_id for candidate in observed] == ["wait_for_pullback"]
    assert any("lower_ranked_duplicate_order_guard" in candidate.rejection_reasons for candidate in rejected)
    assert selected[0].performance_score == 0.84
    assert selected[0].symbol_calibration_weight == 1.0
    assert selected[0].adjusted_final_score >= 0.50
    assert result.route_decision["scoring_contract"]["weight_profile"] == "balanced_signal_first_v1"
    assert result.route_decision["scoring_contract"]["weights"] == MultiStrategyEvaluator.DEFAULT_WEIGHTS
    assert result.route_decision["scoring_contract"]["formula"] == (
        "clamp(weighted_component_sum * symbol_calibration_weight)"
    )


def test_multi_strategy_evaluator_separates_score_rank_from_execution_rank():
    high_score_wait = FixedSignalStrategy(
        "wait_for_pullback",
        StrategySignal(
            signal_id="wait_for_pullback:2:wait",
            strategy_id="wait_for_pullback",
            strategy_version="1.0.0",
            action=StrategyAction.WAIT,
            signal_type="WAIT_FOR_PULLBACK",
            signal_index=2,
            confidence=0.99,
            reason_codes=["wait_for_better_entry"],
            watch_plan={"recheck_on": "next_closed_bar"},
        ),
    )
    lower_score_buy = FixedSignalStrategy(
        "trend_follow",
        _signal("trend_follow", TradeSide.BUY, confidence=0.60),
    )
    routed_pool = RegimeStrategyRouter(
        {RegimeKind.TREND: [high_score_wait, lower_score_buy]},
        router_id="rank_semantics_router",
    ).route_pool(_snapshot())

    result = MultiStrategyEvaluator(min_score=0.50).evaluate(
        routed_pool,
        features={},
        index=2,
        regime=_snapshot(),
    )

    wait_candidate = next(candidate for candidate in result.candidates if candidate.strategy_id == "wait_for_pullback")
    buy_candidate = next(candidate for candidate in result.candidates if candidate.strategy_id == "trend_follow")

    assert result.status == "SELECTED_EXECUTABLE"
    assert result.selected_strategy_id == "trend_follow"
    assert wait_candidate.candidate_status == "OBSERVE_ONLY"
    assert wait_candidate.orderable is False
    assert wait_candidate.score_rank == 1
    assert wait_candidate.rank == wait_candidate.score_rank
    assert wait_candidate.execution_rank is None
    assert buy_candidate.candidate_status == "SELECTED"
    assert buy_candidate.score_rank == 2
    assert buy_candidate.rank == buy_candidate.score_rank
    assert buy_candidate.execution_rank == 1
    assert result.selected_signal.is_orderable is True


def test_multi_strategy_evaluator_rejects_stateful_strategy_without_polluting_pool():
    bad_strategy = StrategyWithRiskDependency()
    good_strategy = FixedSignalStrategy(
        "good_breakout",
        _signal("good_breakout", TradeSide.BUY, confidence=0.70),
    )
    routed_pool = RegimeStrategyRouter(
        {RegimeKind.TREND: [bad_strategy, good_strategy]},
        router_id="stateless_gate_router",
    ).route_pool(_snapshot())

    result = MultiStrategyEvaluator(min_score=0.50).evaluate(routed_pool, {}, 3)

    assert result.status == "SELECTED_EXECUTABLE"
    assert result.selected_strategy_id == "good_breakout"
    assert result.selected_signal.strategy_id == "good_breakout"
    assert bad_strategy.generate_calls == 0

    bad_candidate = next(
        candidate for candidate in result.candidates
        if candidate.strategy_id == "bad_risk_strategy"
    )
    assert bad_candidate.candidate_status == "REJECTED"
    assert bad_candidate.orderable is False
    assert bad_candidate.validation_errors == [
        "strategy holds forbidden cross-layer/state attributes: risk_manager"
    ]
    assert bad_candidate.rejection_reasons == [
        "stateless_validation_failed:strategy holds forbidden cross-layer/state attributes: risk_manager"
    ]
    assert result.safety == {
        "network_used": False,
        "broker_called": False,
        "live_orders_sent": False,
        "risk_bypassed": False,
    }


def test_multi_strategy_evaluator_default_scoring_contract_is_replayable():
    evaluator = MultiStrategyEvaluator(min_score=0.0)
    assert evaluator.weights == {
        "signal_quality_score": 0.35,
        "regime_fit_score": 0.20,
        "risk_score": 0.15,
        "liquidity_score": 0.10,
        "performance_score": 0.20,
    }
    assert sum(evaluator.weights.values()) == 1.0

    routed_pool = RegimeStrategyRouter(
        {RegimeKind.TREND: [_strategy_with_confidence("score_contract", 0.80)]},
        router_id="score_contract_router",
    ).route_pool(_snapshot())
    result = evaluator.evaluate(routed_pool, features={}, index=2, regime=_snapshot())
    candidate = result.candidates[0]
    expected = (
        candidate.signal_quality_score * 0.35
        + candidate.regime_fit_score * 0.20
        + candidate.risk_score * 0.15
        + candidate.liquidity_score * 0.10
        + candidate.performance_score * 0.20
    ) * candidate.symbol_calibration_weight

    assert candidate.adjusted_final_score == expected
    assert result.route_decision["scoring_contract"] == evaluator.scoring_contract_payload()


def test_multi_strategy_evaluator_weight_override_is_replayable():
    evaluator = MultiStrategyEvaluator(
        min_score=0.0,
        weights={
            "signal_quality_score": 1.0,
            "regime_fit_score": 0.0,
            "risk_score": 0.0,
            "liquidity_score": 0.0,
            "performance_score": 0.0,
        },
        symbol_calibration={"BTCUSDT": {"symbol_calibration_weight": 0.5}},
    )
    routed_pool = RegimeStrategyRouter(
        {RegimeKind.TREND: [_strategy_with_confidence("override_contract", 0.80)]},
        router_id="override_contract_router",
    ).route_pool(_snapshot())

    result = evaluator.evaluate(routed_pool, features={}, index=2, regime=_snapshot())
    candidate = result.candidates[0]

    assert candidate.adjusted_final_score == 0.40
    assert result.route_decision["scoring_contract"]["weights"] == evaluator.weights
    assert result.route_decision["scoring_contract"]["default_weights"] == (
        MultiStrategyEvaluator.DEFAULT_WEIGHTS
    )


def test_multi_strategy_evaluator_rejects_unknown_weight_keys():
    with pytest.raises(ValueError, match="unknown strategy score weight keys"):
        MultiStrategyEvaluator(weights={"alpha_magic_score": 0.5})


def test_multi_strategy_evaluator_returns_wait_observation_when_no_signal_is_executable():
    wait_strategy = FixedSignalStrategy(
        "trend_pullback",
        StrategySignal(
            signal_id="trend_pullback:7:wait",
            strategy_id="trend_pullback",
            strategy_version="1.0.0",
            action=StrategyAction.WAIT,
            signal_type="WAIT_FOR_PULLBACK",
            signal_index=7,
            confidence=0.90,
            watch_plan={"recheck_on": "next_closed_bar"},
        ),
    )
    no_signal_strategy = FixedSignalStrategy("capital_protection", None)
    routed_pool = RegimeStrategyRouter(
        {RegimeKind.TREND: [wait_strategy, no_signal_strategy]},
        router_id="pool_router",
    ).route_pool(_snapshot())

    result = MultiStrategyEvaluator(min_score=0.50).evaluate(routed_pool, {}, 7)

    assert result.status == "NO_EXECUTABLE_SIGNAL"
    assert result.selected_executable is False
    assert result.selected_signal.action == _value(StrategyAction.WAIT)
    assert result.selected_signal.should_send_order is False
    assert {candidate.candidate_status for candidate in result.candidates} == {
        "OBSERVE_ONLY",
        "NO_SIGNAL",
    }
    assert all(candidate.orderable is False for candidate in result.candidates)


def test_multi_strategy_evaluator_conflict_selects_one_orderable_signal_only():
    buy_strategy = FixedSignalStrategy(
        "long_breakout",
        _signal("long_breakout", TradeSide.BUY, confidence=0.70),
    )
    sell_strategy = FixedSignalStrategy(
        "short_reversal",
        _signal("short_reversal", TradeSide.SELL, confidence=0.90),
    )
    routed_pool = RegimeStrategyRouter(
        {RegimeKind.VOLATILE: [buy_strategy, sell_strategy]},
        router_id="conflict_router",
    ).route_pool(_snapshot(RegimeKind.VOLATILE))

    result = MultiStrategyEvaluator(min_score=0.50).evaluate(routed_pool, {}, 3)

    selected = [candidate for candidate in result.candidates if candidate.candidate_status == "SELECTED"]
    assert len(selected) == 1
    assert selected[0].strategy_id == "short_reversal"
    assert selected[0].execution_rank == 1
    assert result.selected_signal.side == _value(TradeSide.SELL)
    assert result.selected_signal.is_orderable is True
    assert result.selected_executable is True
    duplicate_guarded = [
        candidate
        for candidate in result.candidates
        if "lower_ranked_duplicate_order_guard" in candidate.rejection_reasons
    ]
    assert [candidate.strategy_id for candidate in duplicate_guarded] == ["long_breakout"]
    assert duplicate_guarded[0].execution_rank == 2


def test_strategy_performance_feedback_store_persists_records(tmp_path):
    path = tmp_path / "strategy-feedback.json"
    store = StrategyPerformanceFeedbackStore()
    store.upsert(
        StrategyPerformanceFeedback(
            feedback_id="fb-001",
            strategy_id="breakout",
            symbol="BTCUSDT",
            regime=RegimeKind.TREND,
            performance_score=0.76,
            sample_count=10,
            updated_at=1710000200,
        )
    )

    store.save_json(path)
    restored = StrategyPerformanceFeedbackStore.from_json(path)

    assert restored.score_for("breakout", "BTCUSDT", RegimeKind.TREND) == 0.76
    assert restored.records[0].to_payload()["symbol"] == "BTCUSDT"


def _snapshot(regime=RegimeKind.TREND):
    return RegimeSnapshot(
        regime_id="regime-pool-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        timeframe="5m",
        as_of_timestamp=1710000060,
        detector_id="adx_atr",
        detector_version="1.0.0",
        regime=regime,
        confidence=0.81,
        reason_codes=["trend_strength_confirmed"],
        trace=TraceContext(
            run_id="strategy-pool-test",
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="5m",
            timestamp=1710000060,
            bar_index=7,
        ),
    )


def _signal(strategy_id, side, *, confidence):
    return StrategySignal(
        signal_id=f"{strategy_id}:2:{_value(side)}",
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        side=side,
        signal_index=2,
        confidence=confidence,
        reason_codes=[f"{strategy_id}_reason"],
    )


def _strategy_with_confidence(strategy_id, confidence):
    return FixedSignalStrategy(
        strategy_id,
        _signal(strategy_id, TradeSide.BUY, confidence=confidence),
    )


def _value(value):
    return getattr(value, "value", value)
