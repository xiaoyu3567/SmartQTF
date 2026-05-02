from dataclasses import dataclass
from typing import Dict, Mapping, Optional

from quant.data.multi_timeframe import MultiTimeframeKlineBatch, TimeframeKlineBatch
from quant.data.quality import (
    KlineQualityReport,
    MultiTimeframeQualityReport,
    validate_multi_timeframe_klines,
)
from quant.features.pipeline import (
    AdvancedFeaturePipeline,
    FeaturePipelineConfig,
    FeaturePipelineInput,
)
from quant.schemas.feature import (
    FeatureQualityReportRef,
    FeatureSnapshot,
    MultiTimeframeFeatureSnapshot,
)


@dataclass(frozen=True)
class MultiTimeframeFeaturePipelineInput:
    batch: MultiTimeframeKlineBatch
    quality_report: Optional[MultiTimeframeQualityReport] = None
    timeframe_indices: Optional[Mapping[str, int]] = None
    snapshot_id: Optional[str] = None


class MultiTimeframeFeatureQualityError(ValueError):
    def __init__(self, quality_report: MultiTimeframeQualityReport):
        issue_codes = [
            _issue_code(issue.code)
            for issue in quality_report.alignment_issues
            if issue.fatal
        ]
        self.quality_report = quality_report
        self.failed_timeframes = list(quality_report.fatal_timeframes)
        self.alignment_issue_codes = issue_codes
        reason = ",".join(self.failed_timeframes + issue_codes) or "quality_report_failed"
        super().__init__(f"multi-timeframe quality report failed before feature computation: {reason}")


class MultiTimeframeFeaturePipeline:
    def __init__(self, config: Optional[FeaturePipelineConfig] = None):
        self.config = config or FeaturePipelineConfig()
        self.pipeline = AdvancedFeaturePipeline(self.config)

    def compute(self, request: MultiTimeframeFeaturePipelineInput) -> MultiTimeframeFeatureSnapshot:
        quality_report = request.quality_report or validate_multi_timeframe_klines(request.batch)
        if not quality_report.passed:
            raise MultiTimeframeFeatureQualityError(quality_report)
        if request.batch.execution is None:
            raise ValueError("multi-timeframe feature input requires an execution timeframe batch")

        snapshots = self._compute_timeframe_snapshots(request, quality_report)
        alignment_features = self._alignment_features(
            snapshots=snapshots,
            execution_timeframe=request.batch.execution_timeframe,
        )
        quality_report_refs = {
            timeframe: _quality_report_ref(timeframe, report)
            for timeframe, report in quality_report.timeframe_reports.items()
            if timeframe in snapshots
        }
        execution_snapshot = snapshots[request.batch.execution_timeframe]

        return MultiTimeframeFeatureSnapshot(
            snapshot_id=request.snapshot_id
            or (
                f"{request.batch.symbol}:{request.batch.execution_timeframe}:"
                f"{execution_snapshot.timestamp}:multi-timeframe-features"
            ),
            timestamp=execution_snapshot.timestamp,
            symbol=request.batch.symbol,
            execution_timeframe=request.batch.execution_timeframe,
            timeframe_snapshots=snapshots,
            alignment_features=alignment_features,
            quality_report_refs=quality_report_refs,
        )

    def _compute_timeframe_snapshots(
        self,
        request: MultiTimeframeFeaturePipelineInput,
        quality_report: MultiTimeframeQualityReport,
    ) -> Dict[str, FeatureSnapshot]:
        snapshots: Dict[str, FeatureSnapshot] = {}
        for batch in _ordered_batches(request.batch):
            timeframe = batch.timeframe
            timeframe_report = quality_report.timeframe_reports.get(timeframe)
            if timeframe_report is None:
                raise ValueError(f"quality_report missing timeframe report for {timeframe}")
            if not timeframe_report.passed:
                raise MultiTimeframeFeatureQualityError(quality_report)

            snapshot_id = f"{request.snapshot_id or request.batch.symbol}:{timeframe}:features"
            snapshots[timeframe] = self.pipeline.compute(
                FeaturePipelineInput(
                    klines=batch.klines,
                    index=(request.timeframe_indices or {}).get(timeframe),
                    symbol=batch.symbol,
                    timeframe=timeframe,
                    venue=batch.venue,
                    snapshot_id=snapshot_id,
                    quality_report=timeframe_report,
                )
            )
        return snapshots

    def _alignment_features(
        self,
        *,
        snapshots: Mapping[str, FeatureSnapshot],
        execution_timeframe: str,
    ) -> Dict[str, object]:
        execution_snapshot = snapshots[execution_timeframe]
        execution_bias = _trend_bias(execution_snapshot)
        context_biases = {
            timeframe: _trend_bias(snapshot)
            for timeframe, snapshot in snapshots.items()
            if timeframe != execution_timeframe
        }
        known_context_biases = [
            bias
            for bias in context_biases.values()
            if bias in {"bullish", "bearish", "neutral"}
        ]
        conflict_count = 0
        if execution_bias in {"bullish", "bearish", "neutral"}:
            conflict_count = sum(
                1
                for bias in known_context_biases
                if bias != execution_bias
            )

        features: Dict[str, object] = {
            "execution_timeframe": execution_timeframe,
            "execution_bias": execution_bias,
            "higher_timeframe_bias": _combined_bias(known_context_biases),
            "computed_timeframe_count": len(snapshots),
            "context_timeframe_count": len(context_biases),
            "conflict_count": conflict_count,
            "unknown_bias_count": sum(
                1
                for bias in [execution_bias, *context_biases.values()]
                if bias == "unknown"
            ),
            "alignment_available": execution_bias != "unknown" and bool(known_context_biases),
        }

        for timeframe, snapshot in snapshots.items():
            bias = _trend_bias(snapshot)
            features[f"timeframe.{timeframe}.bias"] = bias
            features[f"timeframe.{timeframe}.bias_available"] = bias != "unknown"
            features[f"timeframe.{timeframe}.rsi_available"] = _feature_available(snapshot, "rsi")
            features[f"timeframe.{timeframe}.atr_available"] = _feature_available(snapshot, "atr")
            if timeframe == execution_timeframe:
                continue
            features[f"execution_aligned_with_{timeframe}"] = _aligned(execution_bias, bias)

        if context_biases:
            features["execution_aligned_with_higher_timeframes"] = (
                conflict_count == 0
                if execution_bias != "unknown" and len(known_context_biases) == len(context_biases)
                else None
            )
        else:
            features["execution_aligned_with_higher_timeframes"] = None
        return features


def _ordered_batches(batch: MultiTimeframeKlineBatch) -> list[TimeframeKlineBatch]:
    batches = []
    if batch.execution is not None:
        batches.append(batch.execution)
    batches.extend(batch.contexts)
    return batches


def _trend_bias(snapshot: FeatureSnapshot) -> str:
    fast = _numeric_feature(snapshot, "ma_fast", "fast_ma")
    slow = _numeric_feature(snapshot, "ma_slow", "slow_ma")
    if fast is None or slow is None:
        return "unknown"
    if fast > slow:
        return "bullish"
    if fast < slow:
        return "bearish"
    return "neutral"


def _numeric_feature(snapshot: FeatureSnapshot, *names: str) -> Optional[float]:
    for name in names:
        value = snapshot.values.get(name)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _feature_available(snapshot: FeatureSnapshot, feature_name: str) -> bool:
    availability = snapshot.feature_availability.get(feature_name)
    return bool(availability and availability.available and snapshot.values.get(feature_name) is not None)


def _combined_bias(biases: list[str]) -> str:
    if not biases:
        return "unknown"
    unique_biases = set(biases)
    if len(unique_biases) == 1:
        return biases[0]
    return "mixed"


def _aligned(execution_bias: str, context_bias: str) -> Optional[bool]:
    if execution_bias == "unknown" or context_bias == "unknown":
        return None
    return execution_bias == context_bias


def _quality_report_ref(
    timeframe: str,
    report: KlineQualityReport,
) -> FeatureQualityReportRef:
    issue_codes = [_issue_code(issue.code) for issue in report.issues]
    return FeatureQualityReportRef(
        timeframe=timeframe,
        passed=report.passed,
        checked_count=report.checked_count,
        issue_codes=issue_codes,
        fatal_issue_codes=[
            _issue_code(issue.code)
            for issue in report.issues
            if issue.fatal
        ],
        first_timestamp=report.first_timestamp,
        last_timestamp=report.last_timestamp,
        has_incomplete_last_bar=report.has_incomplete_last_bar,
    )


def _issue_code(value: object) -> str:
    return str(getattr(value, "value", value))
