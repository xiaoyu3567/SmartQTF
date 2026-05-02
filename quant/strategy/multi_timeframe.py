from typing import Any, Dict, List, Optional

from pydantic import Field

from quant.schemas import (
    MultiTimeframeRegimeSnapshot,
    StrategyAction,
    StrategyRoute,
    StrategySignal,
    TradeSide,
)
from quant.schemas.base import SmartQTFModel


class MultiTimeframeStrategySignalInput(SmartQTFModel):
    route: StrategyRoute
    raw_signal: Optional[StrategySignal] = None
    execution_feature_series: Dict[str, List[Any]] = Field(default_factory=dict)
    context_features: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    multi_timeframe_regime: MultiTimeframeRegimeSnapshot

    def __init__(self, **data):
        super().__init__(**data)
        if self.route.symbol != self.multi_timeframe_regime.symbol:
            raise ValueError("route symbol must match multi-timeframe regime symbol")
        if self.route.timeframe != self.multi_timeframe_regime.execution_timeframe:
            raise ValueError("route timeframe must match execution_timeframe")
        if self.raw_signal is not None:
            if self.raw_signal.symbol is not None and self.raw_signal.symbol != self.route.symbol:
                raise ValueError("raw_signal symbol must match route symbol")
            if (
                self.raw_signal.timeframe is not None
                and self.raw_signal.timeframe != self.route.timeframe
            ):
                raise ValueError("raw_signal timeframe must match route timeframe")


class HigherTimeframeConfirmationFilter:
    def filter(
        self,
        request: MultiTimeframeStrategySignalInput,
    ) -> Optional[StrategySignal]:
        signal = request.raw_signal
        if signal is None:
            return None
        if not signal.is_orderable:
            return signal

        desired_bias = self._side_bias(signal.side)
        if desired_bias is None:
            return signal

        regime = request.multi_timeframe_regime
        conflict_timeframes = self._conflict_timeframes(regime, desired_bias)
        confirmation_timeframes = self._confirmation_timeframes(regime, desired_bias)

        if conflict_timeframes:
            return self._downgrade_signal(
                request=request,
                signal=signal,
                action=StrategyAction.NO_TRADE,
                signal_type="BLOCKED_BY_HIGHER_TIMEFRAME",
                reason_codes=[
                    "signal_blocked_by_higher_timeframe_conflict",
                    "higher_timeframe_conflict",
                ],
                conflict_timeframes=conflict_timeframes,
                confirmation_timeframes=confirmation_timeframes,
            )

        if regime.tradability == "avoid":
            return self._downgrade_signal(
                request=request,
                signal=signal,
                action=StrategyAction.NO_TRADE,
                signal_type="BLOCKED_BY_MULTI_TIMEFRAME_CONTEXT",
                reason_codes=[
                    "signal_blocked_by_multi_timeframe_tradability",
                    "aggregate_tradability:avoid",
                ],
                conflict_timeframes=conflict_timeframes,
                confirmation_timeframes=confirmation_timeframes,
            )

        if regime.tradability == "observe_only":
            return self._downgrade_signal(
                request=request,
                signal=signal,
                action=StrategyAction.WAIT,
                signal_type="OBSERVE_ONLY",
                reason_codes=self._observe_only_reason_codes(regime),
                conflict_timeframes=conflict_timeframes,
                confirmation_timeframes=confirmation_timeframes,
            )

        if not confirmation_timeframes:
            return self._downgrade_signal(
                request=request,
                signal=signal,
                action=StrategyAction.WAIT,
                signal_type="OBSERVE_ONLY",
                reason_codes=[
                    "signal_observe_only_by_higher_timeframe_context",
                    "higher_timeframe_not_confirmed",
                ],
                conflict_timeframes=conflict_timeframes,
                confirmation_timeframes=confirmation_timeframes,
            )

        return self._confirm_signal(
            request=request,
            signal=signal,
            confirmation_timeframes=confirmation_timeframes,
        )

    def _confirm_signal(
        self,
        *,
        request: MultiTimeframeStrategySignalInput,
        signal: StrategySignal,
        confirmation_timeframes: List[str],
    ) -> StrategySignal:
        payload = signal.to_payload()
        payload["symbol"] = payload.get("symbol") or request.route.symbol
        payload["timeframe"] = payload.get("timeframe") or request.route.timeframe
        payload["reason_codes"] = self._append_reason_codes(
            signal.reason_codes,
            ["higher_timeframe_confirmed"],
        )
        watch_plan = dict(payload.get("watch_plan") or {})
        watch_plan.update(
            self._context_watch_plan(
                request,
                confirmation_timeframes=confirmation_timeframes,
                conflict_timeframes=[],
            )
        )
        payload["watch_plan"] = watch_plan
        return StrategySignal.from_payload(payload)

    def _downgrade_signal(
        self,
        *,
        request: MultiTimeframeStrategySignalInput,
        signal: StrategySignal,
        action: StrategyAction,
        signal_type: str,
        reason_codes: List[str],
        conflict_timeframes: List[str],
        confirmation_timeframes: List[str],
    ) -> StrategySignal:
        return StrategySignal(
            signal_id=f"{signal.signal_id}:higher_timeframe_filter",
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            action=action,
            signal_type=signal_type,
            signal_index=signal.signal_index,
            execute_index=signal.execute_index,
            symbol=signal.symbol or request.route.symbol,
            timeframe=signal.timeframe or request.route.timeframe,
            confidence=signal.confidence,
            reason_codes=self._append_reason_codes(signal.reason_codes, reason_codes),
            trade_now=False,
            should_send_order=False,
            watch_plan=self._context_watch_plan(
                request,
                confirmation_timeframes=confirmation_timeframes,
                conflict_timeframes=conflict_timeframes,
                original_signal=signal,
            ),
            trace=signal.trace,
        )

    @staticmethod
    def _observe_only_reason_codes(
        regime: MultiTimeframeRegimeSnapshot,
    ) -> List[str]:
        reason_codes = ["signal_observe_only_by_higher_timeframe_context"]
        if regime.higher_timeframe_bias == "unknown":
            reason_codes.append("higher_timeframe_context_unknown")
        elif regime.higher_timeframe_bias == "mixed":
            reason_codes.append("higher_timeframe_bias_mixed")
        else:
            reason_codes.append("aggregate_tradability:observe_only")
        return reason_codes

    @classmethod
    def _conflict_timeframes(
        cls,
        regime: MultiTimeframeRegimeSnapshot,
        desired_bias: str,
    ) -> List[str]:
        opposite = "bearish" if desired_bias == "bullish" else "bullish"
        conflicts = [
            timeframe
            for timeframe, context_regime in regime.context_regimes.items()
            if context_regime.direction == opposite
        ]
        if conflicts:
            return conflicts
        if regime.higher_timeframe_bias == opposite:
            return list(regime.conflict_timeframes) or ["higher_timeframe_bias"]
        return []

    @classmethod
    def _confirmation_timeframes(
        cls,
        regime: MultiTimeframeRegimeSnapshot,
        desired_bias: str,
    ) -> List[str]:
        confirmations = [
            timeframe
            for timeframe, context_regime in regime.context_regimes.items()
            if context_regime.direction == desired_bias
        ]
        if confirmations:
            return confirmations
        if regime.higher_timeframe_bias == desired_bias:
            return list(regime.confirmation_timeframes)
        return []

    @staticmethod
    def _side_bias(side) -> Optional[str]:
        value = side.value if hasattr(side, "value") else side
        if value == TradeSide.BUY.value:
            return "bullish"
        if value == TradeSide.SELL.value:
            return "bearish"
        return None

    @staticmethod
    def _append_reason_codes(existing: List[str], additions: List[str]) -> List[str]:
        reason_codes = list(existing)
        for reason_code in additions:
            if reason_code not in reason_codes:
                reason_codes.append(reason_code)
        return reason_codes

    @staticmethod
    def _context_watch_plan(
        request: MultiTimeframeStrategySignalInput,
        *,
        confirmation_timeframes: List[str],
        conflict_timeframes: List[str],
        original_signal: Optional[StrategySignal] = None,
    ) -> Dict[str, Any]:
        regime = request.multi_timeframe_regime
        watch_plan = {
            "route_id": request.route.route_id,
            "multi_timeframe_regime_snapshot_id": regime.snapshot_id,
            "execution_regime_id": regime.execution_regime.regime_id,
            "aggregate_regime_id": regime.aggregate_regime.regime_id,
            "higher_timeframe_bias": regime.higher_timeframe_bias,
            "tradability": regime.tradability,
            "confirmation_timeframes": list(confirmation_timeframes),
            "conflict_timeframes": list(conflict_timeframes),
            "quality_failed_timeframes": list(regime.quality_failed_timeframes),
            "context_regime_ids": {
                timeframe: context_regime.regime_id
                for timeframe, context_regime in regime.context_regimes.items()
            },
        }
        if original_signal is not None:
            watch_plan["original_signal_id"] = original_signal.signal_id
            original_side = (
                original_signal.side.value
                if hasattr(original_signal.side, "value")
                else original_signal.side
            )
            watch_plan["original_side"] = original_side
        return watch_plan


__all__ = [
    "HigherTimeframeConfirmationFilter",
    "MultiTimeframeStrategySignalInput",
]
