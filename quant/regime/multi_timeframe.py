from typing import Dict, Mapping, Optional

from quant.regime.rule_detector import RuleBasedRegimeDetector
from quant.schemas import RegimeKind, RegimeSnapshot
from quant.schemas.feature import FeatureQualityReportRef, MultiTimeframeFeatureSnapshot
from quant.schemas.regime import MultiTimeframeRegimeInput, MultiTimeframeRegimeSnapshot


class MultiTimeframeRegimeDetector:
    def __init__(
        self,
        *,
        detector=None,
        detector_id: str = "multi_timeframe_regime",
        detector_version: str = "1.0.0",
    ):
        self.detector = detector or RuleBasedRegimeDetector()
        self.detector_id = detector_id
        self.detector_version = detector_version

    def detect(
        self,
        request: MultiTimeframeRegimeInput | MultiTimeframeFeatureSnapshot,
    ) -> MultiTimeframeRegimeSnapshot:
        feature_snapshot = (
            request.feature_snapshot
            if isinstance(request, MultiTimeframeRegimeInput)
            else request
        )
        if feature_snapshot.execution_timeframe not in feature_snapshot.timeframe_snapshots:
            raise ValueError("execution_timeframe must exist in timeframe_snapshots")

        regimes = self._detect_timeframes(feature_snapshot)
        execution_regime = regimes[feature_snapshot.execution_timeframe]
        context_regimes = {
            timeframe: regime
            for timeframe, regime in regimes.items()
            if timeframe != feature_snapshot.execution_timeframe
        }

        confirmation_timeframes = self._confirmation_timeframes(
            execution_regime,
            context_regimes,
        )
        conflict_timeframes = self._conflict_timeframes(
            execution_regime,
            context_regimes,
        )
        quality_failed_timeframes = self._quality_failed_timeframes(
            feature_snapshot.quality_report_refs
        )
        high_volatility_timeframes = self._volatility_timeframes(
            context_regimes,
            "high",
        )
        extreme_volatility_timeframes = self._volatility_timeframes(
            context_regimes,
            "extreme",
        )
        higher_timeframe_bias = self._combined_bias(
            [
                regime.direction
                for regime in context_regimes.values()
                if regime.direction in {"bullish", "bearish", "neutral"}
            ]
        )
        tradability = self._aggregate_tradability(
            execution_regime=execution_regime,
            context_regimes=context_regimes,
            confirmation_timeframes=confirmation_timeframes,
            conflict_timeframes=conflict_timeframes,
            quality_failed_timeframes=quality_failed_timeframes,
            high_volatility_timeframes=high_volatility_timeframes,
            extreme_volatility_timeframes=extreme_volatility_timeframes,
        )
        reason_codes, reasons = self._aggregate_reasons(
            execution_regime=execution_regime,
            context_regimes=context_regimes,
            confirmation_timeframes=confirmation_timeframes,
            conflict_timeframes=conflict_timeframes,
            quality_failed_timeframes=quality_failed_timeframes,
            high_volatility_timeframes=high_volatility_timeframes,
            extreme_volatility_timeframes=extreme_volatility_timeframes,
            tradability=tradability,
        )
        input_refs = self._input_refs(
            feature_snapshot=feature_snapshot,
            regimes=regimes,
        )
        aggregate_regime = self._aggregate_regime(
            feature_snapshot=feature_snapshot,
            execution_regime=execution_regime,
            context_regimes=context_regimes,
            higher_timeframe_bias=higher_timeframe_bias,
            confirmation_timeframes=confirmation_timeframes,
            conflict_timeframes=conflict_timeframes,
            quality_failed_timeframes=quality_failed_timeframes,
            high_volatility_timeframes=high_volatility_timeframes,
            extreme_volatility_timeframes=extreme_volatility_timeframes,
            tradability=tradability,
            reason_codes=reason_codes,
            reasons=reasons,
            input_refs=input_refs,
        )

        return MultiTimeframeRegimeSnapshot(
            snapshot_id=f"{self.detector_id}:{feature_snapshot.snapshot_id}",
            timestamp=feature_snapshot.timestamp,
            symbol=feature_snapshot.symbol,
            execution_timeframe=feature_snapshot.execution_timeframe,
            execution_regime=execution_regime,
            aggregate_regime=aggregate_regime,
            context_regimes=context_regimes,
            higher_timeframe_bias=higher_timeframe_bias,
            confirmation_timeframes=confirmation_timeframes,
            conflict_timeframes=conflict_timeframes,
            quality_failed_timeframes=quality_failed_timeframes,
            high_volatility_timeframes=high_volatility_timeframes,
            extreme_volatility_timeframes=extreme_volatility_timeframes,
            tradability=tradability,
            reason_codes=reason_codes,
            reasons=reasons,
            input_refs=input_refs,
            trace=feature_snapshot.trace or execution_regime.trace,
        )

    def _detect_timeframes(
        self,
        feature_snapshot: MultiTimeframeFeatureSnapshot,
    ) -> Dict[str, RegimeSnapshot]:
        regimes = {}
        for timeframe, snapshot in feature_snapshot.timeframe_snapshots.items():
            quality_ref = feature_snapshot.quality_report_refs.get(timeframe)
            if quality_ref is not None and not quality_ref.passed:
                regimes[timeframe] = self._quality_failed_regime(snapshot, quality_ref)
                continue
            regimes[timeframe] = self.detector.detect(snapshot)
        return regimes

    def _quality_failed_regime(
        self,
        snapshot,
        quality_ref: FeatureQualityReportRef,
    ) -> RegimeSnapshot:
        reason_codes = ["regime_quality_gate_blocked", "quality_report_failed"]
        reason_codes.extend(
            f"quality_issue:{issue_code}"
            for issue_code in quality_ref.fatal_issue_codes or quality_ref.issue_codes
        )
        reasons = [
            "Regime classification was blocked by the timeframe quality gate.",
            f"{quality_ref.timeframe} quality report did not pass.",
        ]
        reasons.extend(
            f"{quality_ref.timeframe} quality issue {issue_code} was fatal for regime context."
            for issue_code in quality_ref.fatal_issue_codes or quality_ref.issue_codes
        )
        return RegimeSnapshot(
            regime_id=f"quality_gate:{snapshot.snapshot_id}",
            timestamp=snapshot.timestamp,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            as_of_timestamp=snapshot.as_of_timestamp,
            detector_id="multi_timeframe_regime_quality_gate",
            detector_version=self.detector_version,
            regime=RegimeKind.UNKNOWN,
            confidence=0.0,
            reason_codes=reason_codes,
            reasons=reasons,
            metrics={"quality_gate_passed": 0.0},
            scores={
                "trend": 0.0,
                "volatility": 0.0,
                "liquidity_activity": 0.0,
                "orderflow": 0.0,
            },
            score_inputs={"quality_gate": quality_ref.to_payload()},
            source_window_start=snapshot.source_window_start,
            source_window_end=snapshot.source_window_end,
            input_refs={
                "feature_snapshot_id": snapshot.snapshot_id,
                "quality_report_ref": quality_ref.to_payload(),
            },
            direction="unknown",
            volatility_state="unknown",
            tradability="avoid",
            trace=snapshot.trace,
        )

    def _aggregate_regime(
        self,
        *,
        feature_snapshot: MultiTimeframeFeatureSnapshot,
        execution_regime: RegimeSnapshot,
        context_regimes: Mapping[str, RegimeSnapshot],
        higher_timeframe_bias: str,
        confirmation_timeframes: list[str],
        conflict_timeframes: list[str],
        quality_failed_timeframes: list[str],
        high_volatility_timeframes: list[str],
        extreme_volatility_timeframes: list[str],
        tradability: str,
        reason_codes: list[str],
        reasons: list[str],
        input_refs: Mapping[str, object],
    ) -> RegimeSnapshot:
        return RegimeSnapshot(
            regime_id=f"{self.detector_id}:aggregate:{feature_snapshot.snapshot_id}",
            timestamp=feature_snapshot.timestamp,
            symbol=feature_snapshot.symbol,
            timeframe=feature_snapshot.execution_timeframe,
            as_of_timestamp=execution_regime.as_of_timestamp,
            detector_id=self.detector_id,
            detector_version=self.detector_version,
            regime=execution_regime.regime,
            confidence=self._aggregate_confidence(
                execution_regime=execution_regime,
                context_regimes=context_regimes,
                tradability=tradability,
            ),
            reason_codes=reason_codes,
            reasons=reasons,
            metrics={
                "context_timeframe_count": float(len(context_regimes)),
                "confirmation_timeframe_count": float(len(confirmation_timeframes)),
                "conflict_timeframe_count": float(len(conflict_timeframes)),
                "quality_failed_timeframe_count": float(len(quality_failed_timeframes)),
                "high_volatility_timeframe_count": float(len(high_volatility_timeframes)),
                "extreme_volatility_timeframe_count": float(len(extreme_volatility_timeframes)),
            },
            scores=execution_regime.scores,
            score_inputs={
                "execution_regime_id": execution_regime.regime_id,
                "context_regime_ids": {
                    timeframe: regime.regime_id
                    for timeframe, regime in context_regimes.items()
                },
                "higher_timeframe_bias": higher_timeframe_bias,
            },
            source_window_start=execution_regime.source_window_start,
            source_window_end=execution_regime.source_window_end,
            input_refs=dict(input_refs),
            direction=execution_regime.direction,
            volatility_state=execution_regime.volatility_state,
            tradability=tradability,
            trace=feature_snapshot.trace or execution_regime.trace,
        )

    @staticmethod
    def _confirmation_timeframes(
        execution_regime: RegimeSnapshot,
        context_regimes: Mapping[str, RegimeSnapshot],
    ) -> list[str]:
        execution_direction = execution_regime.direction
        if execution_direction not in {"bullish", "bearish", "neutral"}:
            return []
        return [
            timeframe
            for timeframe, regime in context_regimes.items()
            if regime.direction == execution_direction
        ]

    @staticmethod
    def _conflict_timeframes(
        execution_regime: RegimeSnapshot,
        context_regimes: Mapping[str, RegimeSnapshot],
    ) -> list[str]:
        execution_direction = execution_regime.direction
        if execution_direction not in {"bullish", "bearish"}:
            return []
        opposite = "bearish" if execution_direction == "bullish" else "bullish"
        return [
            timeframe
            for timeframe, regime in context_regimes.items()
            if regime.direction == opposite
        ]

    @staticmethod
    def _quality_failed_timeframes(
        quality_report_refs: Mapping[str, FeatureQualityReportRef],
    ) -> list[str]:
        return [
            timeframe
            for timeframe, quality_ref in quality_report_refs.items()
            if not quality_ref.passed
        ]

    @staticmethod
    def _volatility_timeframes(
        context_regimes: Mapping[str, RegimeSnapshot],
        volatility_state: str,
    ) -> list[str]:
        return [
            timeframe
            for timeframe, regime in context_regimes.items()
            if regime.volatility_state == volatility_state
        ]

    @staticmethod
    def _combined_bias(directions: list[str]) -> str:
        if not directions:
            return "unknown"
        unique_directions = set(directions)
        if len(unique_directions) == 1:
            return directions[0]
        return "mixed"

    @staticmethod
    def _aggregate_tradability(
        *,
        execution_regime: RegimeSnapshot,
        context_regimes: Mapping[str, RegimeSnapshot],
        confirmation_timeframes: list[str],
        conflict_timeframes: list[str],
        quality_failed_timeframes: list[str],
        high_volatility_timeframes: list[str],
        extreme_volatility_timeframes: list[str],
    ) -> str:
        if execution_regime.tradability == "avoid":
            return "avoid"
        if quality_failed_timeframes or extreme_volatility_timeframes:
            return "avoid"
        if (
            execution_regime.tradability == "observe_only"
            or conflict_timeframes
            or high_volatility_timeframes
        ):
            return "observe_only"
        if context_regimes and not confirmation_timeframes:
            return "observe_only"
        return "tradable"

    @staticmethod
    def _aggregate_confidence(
        *,
        execution_regime: RegimeSnapshot,
        context_regimes: Mapping[str, RegimeSnapshot],
        tradability: str,
    ) -> float:
        confidences = [execution_regime.confidence]
        confidences.extend(regime.confidence for regime in context_regimes.values())
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        if tradability == "avoid":
            return min(confidence, 0.25)
        if tradability == "observe_only":
            return min(confidence, 0.5)
        return confidence

    @staticmethod
    def _aggregate_reasons(
        *,
        execution_regime: RegimeSnapshot,
        context_regimes: Mapping[str, RegimeSnapshot],
        confirmation_timeframes: list[str],
        conflict_timeframes: list[str],
        quality_failed_timeframes: list[str],
        high_volatility_timeframes: list[str],
        extreme_volatility_timeframes: list[str],
        tradability: str,
    ) -> tuple[list[str], list[str]]:
        pairs = [
            (
                "execution_regime_primary",
                f"{execution_regime.timeframe} execution regime remained the primary classification.",
            )
        ]
        if confirmation_timeframes:
            pairs.append(
                (
                    "higher_timeframe_confirmed",
                    "Higher timeframe context confirmed the execution direction: "
                    f"{', '.join(confirmation_timeframes)}.",
                )
            )
        if conflict_timeframes:
            pairs.append(
                (
                    "higher_timeframe_conflict",
                    "Higher timeframe context conflicted with the execution direction: "
                    f"{', '.join(conflict_timeframes)}.",
                )
            )
        if quality_failed_timeframes:
            pairs.append(
                (
                    "higher_timeframe_quality_failed",
                    "Regime context was downgraded because quality failed for: "
                    f"{', '.join(quality_failed_timeframes)}.",
                )
            )
        if high_volatility_timeframes:
            pairs.append(
                (
                    "higher_timeframe_high_volatility",
                    "High volatility was present in higher timeframe context: "
                    f"{', '.join(high_volatility_timeframes)}.",
                )
            )
        if extreme_volatility_timeframes:
            pairs.append(
                (
                    "higher_timeframe_extreme_volatility",
                    "Extreme volatility was present in higher timeframe context: "
                    f"{', '.join(extreme_volatility_timeframes)}.",
                )
            )
        if context_regimes and not confirmation_timeframes and not conflict_timeframes:
            pairs.append(
                (
                    "higher_timeframe_not_confirmed",
                    "Higher timeframe context did not confirm the execution direction.",
                )
            )
        if not context_regimes:
            pairs.append(
                (
                    "higher_timeframe_context_missing",
                    "No higher timeframe context was available for confirmation.",
                )
            )
        pairs.append(
            (
                f"aggregate_tradability:{tradability}",
                f"Aggregated multi-timeframe tradability is {tradability}.",
            )
        )
        return [code for code, _ in pairs], [reason for _, reason in pairs]

    @staticmethod
    def _input_refs(
        *,
        feature_snapshot: MultiTimeframeFeatureSnapshot,
        regimes: Mapping[str, RegimeSnapshot],
    ) -> Dict[str, object]:
        return {
            "multi_timeframe_feature_snapshot_id": feature_snapshot.snapshot_id,
            "execution_timeframe": feature_snapshot.execution_timeframe,
            "feature_snapshot_ids": {
                timeframe: snapshot.snapshot_id
                for timeframe, snapshot in feature_snapshot.timeframe_snapshots.items()
            },
            "quality_report_refs": {
                timeframe: quality_ref.to_payload()
                for timeframe, quality_ref in feature_snapshot.quality_report_refs.items()
            },
            "regime_ids": {
                timeframe: regime.regime_id
                for timeframe, regime in regimes.items()
            },
            "alignment_features": dict(feature_snapshot.alignment_features),
        }
