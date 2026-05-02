from quant.schemas import (
    MultiTimeframeRegimeSnapshot,
    RegimeKind,
    RegimeSnapshot,
    StrategyAction,
    StrategyRoute,
    StrategySignal,
    TradeSide,
)
from quant.strategy.multi_timeframe import (
    HigherTimeframeConfirmationFilter,
    MultiTimeframeStrategySignalInput,
)


TIMESTAMP = 1700001200
SYMBOL = "BTCUSDT"


def test_buy_signal_keeps_orderable_when_higher_timeframes_confirm():
    result = _filter_signal(
        _signal(TradeSide.BUY),
        _mtf_regime(
            execution_direction="bullish",
            context_directions={"1h": "bullish", "4h": "bullish"},
            higher_timeframe_bias="bullish",
            confirmation_timeframes=["1h", "4h"],
            tradability="tradable",
        ),
    )

    assert result.is_orderable is True
    assert result.side == "buy"
    assert result.trade_now is True
    assert result.should_send_order is True
    assert "ma_cross" in result.reason_codes
    assert "higher_timeframe_confirmed" in result.reason_codes
    assert result.watch_plan["confirmation_timeframes"] == ["1h", "4h"]
    assert result.watch_plan["multi_timeframe_regime_snapshot_id"] == "mtf-regime"


def test_buy_signal_blocks_when_one_hour_context_is_bearish():
    result = _filter_signal(
        _signal(TradeSide.BUY),
        _mtf_regime(
            execution_direction="bullish",
            context_directions={"1h": "bearish", "4h": "bullish"},
            higher_timeframe_bias="mixed",
            confirmation_timeframes=["4h"],
            conflict_timeframes=["1h"],
            tradability="observe_only",
        ),
    )

    assert result.is_orderable is False
    assert result.action == "no_trade"
    assert result.side is None
    assert result.trade_now is False
    assert result.should_send_order is False
    assert "signal_blocked_by_higher_timeframe_conflict" in result.reason_codes
    assert result.watch_plan["original_side"] == "buy"
    assert result.watch_plan["conflict_timeframes"] == ["1h"]
    assert result.watch_plan["confirmation_timeframes"] == ["4h"]


def test_sell_signal_blocks_when_four_hour_context_is_bullish():
    result = _filter_signal(
        _signal(TradeSide.SELL),
        _mtf_regime(
            execution_direction="bearish",
            context_directions={"1h": "bearish", "4h": "bullish"},
            higher_timeframe_bias="mixed",
            confirmation_timeframes=["1h"],
            conflict_timeframes=["4h"],
            tradability="observe_only",
        ),
    )

    assert result.is_orderable is False
    assert result.action == "no_trade"
    assert "signal_blocked_by_higher_timeframe_conflict" in result.reason_codes
    assert result.watch_plan["original_side"] == "sell"
    assert result.watch_plan["conflict_timeframes"] == ["4h"]


def test_unknown_context_downgrades_orderable_signal_to_observe_only():
    result = _filter_signal(
        _signal(TradeSide.BUY),
        _mtf_regime(
            execution_direction="bullish",
            context_directions={"1h": "unknown", "4h": "unknown"},
            higher_timeframe_bias="unknown",
            confirmation_timeframes=[],
            conflict_timeframes=[],
            tradability="observe_only",
        ),
    )

    assert result.is_orderable is False
    assert result.action == "wait"
    assert result.signal_type == "OBSERVE_ONLY"
    assert result.trade_now is False
    assert result.should_send_order is False
    assert "signal_observe_only_by_higher_timeframe_context" in result.reason_codes
    assert "higher_timeframe_context_unknown" in result.reason_codes
    assert result.watch_plan["higher_timeframe_bias"] == "unknown"
    assert result.watch_plan["tradability"] == "observe_only"


def test_non_orderable_signal_passes_through_without_filter_mutation():
    signal = StrategySignal(
        signal_id="strategy:12:wait",
        strategy_id="strategy",
        strategy_version="1.0",
        action=StrategyAction.WAIT,
        signal_type="WAIT_FOR_PULLBACK",
        signal_index=12,
        symbol=SYMBOL,
        timeframe="5m",
        reason_codes=["wait_for_pullback"],
    )
    result = _filter_signal(
        signal,
        _mtf_regime(
            execution_direction="bullish",
            context_directions={"1h": "bearish"},
            higher_timeframe_bias="bearish",
            conflict_timeframes=["1h"],
            tradability="observe_only",
        ),
    )

    assert result == signal


def _filter_signal(signal, mtf_regime):
    return HigherTimeframeConfirmationFilter().filter(
        MultiTimeframeStrategySignalInput(
            route=_route(),
            raw_signal=signal,
            execution_feature_series={"fast_ma": [99.0, 101.0]},
            context_features={"1h": {"trend_strength": 0.03}},
            multi_timeframe_regime=mtf_regime,
        )
    )


def _signal(side):
    return StrategySignal(
        signal_id=f"ma_crossover:7:{side.value}",
        strategy_id="ma_crossover",
        strategy_version="1.0",
        side=side,
        signal_index=7,
        symbol=SYMBOL,
        timeframe="5m",
        reason_codes=["ma_cross"],
    )


def _route():
    return StrategyRoute(
        route_id="router:mtf:BTCUSDT",
        timestamp=TIMESTAMP,
        symbol=SYMBOL,
        timeframe="5m",
        regime=RegimeKind.TREND,
        strategy_id="ma_crossover",
        strategy_version="1.0",
        router_id="regime_strategy_router",
        router_version="1.0.0",
        reason_codes=["regime:trend"],
    )


def _mtf_regime(
    *,
    execution_direction,
    context_directions,
    higher_timeframe_bias,
    confirmation_timeframes=None,
    conflict_timeframes=None,
    tradability="tradable",
):
    execution = _regime("5m", execution_direction, "execution-regime", tradability="tradable")
    contexts = {
        timeframe: _regime(timeframe, direction, f"context-regime-{timeframe}")
        for timeframe, direction in context_directions.items()
    }
    aggregate = _regime("5m", execution_direction, "aggregate-regime", tradability=tradability)
    reason_codes = ["execution_regime_primary", f"aggregate_tradability:{tradability}"]
    reasons = [
        "5m execution regime remained the primary classification.",
        f"Aggregated multi-timeframe tradability is {tradability}.",
    ]
    return MultiTimeframeRegimeSnapshot(
        snapshot_id="mtf-regime",
        timestamp=TIMESTAMP,
        symbol=SYMBOL,
        execution_timeframe="5m",
        execution_regime=execution,
        aggregate_regime=aggregate,
        context_regimes=contexts,
        higher_timeframe_bias=higher_timeframe_bias,
        confirmation_timeframes=confirmation_timeframes or [],
        conflict_timeframes=conflict_timeframes or [],
        tradability=tradability,
        reason_codes=reason_codes,
        reasons=reasons,
        input_refs={"fixture": "multi_timeframe_strategy_filter"},
    )


def _regime(timeframe, direction, regime_id, *, tradability="tradable"):
    return RegimeSnapshot(
        regime_id=regime_id,
        timestamp=TIMESTAMP,
        symbol=SYMBOL,
        timeframe=timeframe,
        as_of_timestamp=TIMESTAMP,
        detector_id="fixture_regime_detector",
        detector_version="1.0.0",
        regime=RegimeKind.TREND if direction in {"bullish", "bearish"} else RegimeKind.UNKNOWN,
        confidence=0.7 if direction in {"bullish", "bearish"} else 0.2,
        reason_codes=[f"direction:{direction}"],
        reasons=[f"{timeframe} direction is {direction}."],
        scores={
            "trend": 0.7 if direction in {"bullish", "bearish"} else 0.0,
            "volatility": 0.2,
            "liquidity_activity": 0.5,
            "orderflow": 0.5,
        },
        source_window_start=TIMESTAMP - 600,
        source_window_end=TIMESTAMP,
        direction=direction,
        volatility_state="normal" if direction != "unknown" else "unknown",
        tradability=tradability if direction != "unknown" else "observe_only",
    )
