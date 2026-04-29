import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Optional

from quant.optimization.strategy_versioning import StrategyVersionGate
from quant.optimization.symbol_queue import SymbolOptimizationQueue
from quant.optimization.validation_artifacts import StrategyValidationArtifactStore
from quant.schemas import (
    DailyReviewBucket,
    DailyReviewReport,
    StrategyValidationMetrics,
    StrategyVersion,
    StrategyVersionStatus,
    TraceContext,
)


ValidationMetricsFactory = Callable[
    [DailyReviewReport, DailyReviewBucket, DailyReviewBucket, StrategyVersion],
    StrategyValidationMetrics,
]


class DailyReviewOptimizationPlanner:
    """Turn replayable daily review buckets into optimization queue records."""

    def __init__(
        self,
        *,
        min_trades: int = 1,
        min_symbol_net_pnl: float = 0.0,
        min_strategy_net_pnl: float = 0.0,
        max_candidates: int = 3,
        gate: Optional[StrategyVersionGate] = None,
        code_ref: str = "daily-review",
        parent_version: Optional[str] = None,
        validation_artifact_store: Optional[
            StrategyValidationArtifactStore | str | Path
        ] = None,
    ):
        if min_trades < 0:
            raise ValueError("min_trades must be non-negative")
        if max_candidates <= 0:
            raise ValueError("max_candidates must be positive")

        self.min_trades = min_trades
        self.min_symbol_net_pnl = min_symbol_net_pnl
        self.min_strategy_net_pnl = min_strategy_net_pnl
        self.max_candidates = max_candidates
        self.gate = gate or StrategyVersionGate(min_trades=min_trades)
        self.code_ref = code_ref
        self.parent_version = parent_version
        self.validation_artifact_store = self._validation_artifact_store(
            validation_artifact_store
        )

    def enqueue_from_report(
        self,
        report,
        queue,
        *,
        gate: Optional[StrategyVersionGate] = None,
        validation_metrics_factory: Optional[ValidationMetricsFactory] = None,
        validation_artifact_store: Optional[
            StrategyValidationArtifactStore | str | Path
        ] = None,
        trace: Optional[TraceContext] = None,
    ):
        report = self._report(report)
        queue = self._queue(queue)
        active_gate = gate or self.gate
        active_validation_artifact_store = (
            self._validation_artifact_store(validation_artifact_store)
            or self.validation_artifact_store
        )

        symbol_buckets = self._eligible_buckets(
            report,
            bucket_type="symbol",
            min_net_pnl=self.min_symbol_net_pnl,
        )
        strategy_buckets = self._eligible_buckets(
            report,
            bucket_type="strategy",
            min_net_pnl=self.min_strategy_net_pnl,
        )
        if not symbol_buckets or not strategy_buckets:
            return []

        records = []
        for symbol_bucket in symbol_buckets:
            for strategy_bucket in strategy_buckets:
                if len(records) >= self.max_candidates:
                    return records
                candidate = self._candidate_from_buckets(
                    report=report,
                    symbol_bucket=symbol_bucket,
                    strategy_bucket=strategy_bucket,
                    trace=trace,
                )
                queue_id = self._queue_id(
                    report=report,
                    symbol=symbol_bucket.bucket_value,
                    strategy_id=strategy_bucket.bucket_value,
                )
                record = queue.get_record(symbol_bucket.bucket_value, queue_id)
                if record is None:
                    record = queue.enqueue_candidate(
                        symbol=symbol_bucket.bucket_value,
                        candidate=candidate,
                        queue_id=queue_id,
                        created_at=report.generated_at,
                        trace=trace,
                    )

                metrics = self._validation_metrics(
                    report=report,
                    symbol_bucket=symbol_bucket,
                    strategy_bucket=strategy_bucket,
                    candidate=record.candidate,
                    validation_metrics_factory=validation_metrics_factory,
                    validation_artifact_store=active_validation_artifact_store,
                )
                record = queue.attach_validation(
                    symbol=symbol_bucket.bucket_value,
                    queue_id=queue_id,
                    metrics=metrics,
                )
                decision = active_gate.evaluate(
                    candidate=record.candidate,
                    metrics=metrics,
                    decision_id=f"{queue_id}:promotion",
                    generated_at=report.generated_at,
                    trace=trace,
                )
                record = queue.attach_decision(
                    symbol=symbol_bucket.bucket_value,
                    queue_id=queue_id,
                    decision=decision,
                )
                records.append(record)
        return records

    def _candidate_from_buckets(
        self,
        *,
        report: DailyReviewReport,
        symbol_bucket: DailyReviewBucket,
        strategy_bucket: DailyReviewBucket,
        trace: Optional[TraceContext],
    ) -> StrategyVersion:
        parameters = self._candidate_parameters(report, symbol_bucket, strategy_bucket)
        strategy_id = strategy_bucket.bucket_value
        symbol = symbol_bucket.bucket_value
        version = (
            f"review-{self._safe_token(report.trading_date)}-"
            f"{self._safe_token(symbol)}-{self._safe_token(strategy_id)}"
        )
        return StrategyVersion(
            strategy_id=strategy_id,
            version=version,
            status=StrategyVersionStatus.CANDIDATE,
            created_at=report.generated_at,
            code_ref=f"{self.code_ref}:{report.report_id}",
            config_hash=self._config_hash(parameters),
            parameters=parameters,
            parent_version=self.parent_version,
            changelog=self._changelog(report, symbol_bucket, strategy_bucket),
            validation_report_id=f"{report.report_id}:{version}:review-validation",
            trace=trace,
        )

    def _candidate_parameters(self, report, symbol_bucket, strategy_bucket):
        positive_regimes = self._positive_buckets(report, "regime")
        positive_features = self._positive_buckets(report, "feature")
        best_regime = self._best_bucket(positive_regimes)
        best_feature = self._best_bucket(positive_features)
        conservative_win_rate = min(symbol_bucket.win_rate, strategy_bucket.win_rate)
        conservative_sharpe = min(symbol_bucket.sharpe, strategy_bucket.sharpe)
        conservative_drawdown = max(symbol_bucket.max_drawdown, strategy_bucket.max_drawdown)
        score = self._bucket_score(symbol_bucket) + self._bucket_score(strategy_bucket)

        return {
            "review_symbol_net_pnl": float(symbol_bucket.net_pnl),
            "review_strategy_net_pnl": float(strategy_bucket.net_pnl),
            "review_win_rate": float(conservative_win_rate),
            "review_sharpe": float(conservative_sharpe),
            "review_max_drawdown": float(conservative_drawdown),
            "review_score": float(score),
            "positive_regime_count": float(len(positive_regimes)),
            "positive_feature_count": float(len(positive_features)),
            "best_regime_score": float(self._bucket_score(best_regime) if best_regime else 0.0),
            "best_feature_score": float(self._bucket_score(best_feature) if best_feature else 0.0),
            "confidence_multiplier": self._confidence_multiplier(
                conservative_win_rate,
                conservative_sharpe,
            ),
            "risk_budget_scale": self._risk_budget_scale(
                symbol_bucket,
                strategy_bucket,
            ),
        }

    def _validation_metrics(
        self,
        *,
        report,
        symbol_bucket,
        strategy_bucket,
        candidate,
        validation_metrics_factory,
        validation_artifact_store,
    ):
        if validation_metrics_factory is not None:
            return validation_metrics_factory(
                report,
                symbol_bucket,
                strategy_bucket,
                candidate,
            )

        if validation_artifact_store is not None:
            return validation_artifact_store.metrics_for_candidate(
                report,
                symbol_bucket,
                strategy_bucket,
                candidate,
            )

        return StrategyValidationMetrics(
            report_id=f"{report.report_id}:{candidate.version}:review-validation",
            generated_at=report.generated_at,
            trade_count=min(symbol_bucket.fill_count, strategy_bucket.fill_count),
            total_net_pnl=min(symbol_bucket.net_pnl, strategy_bucket.net_pnl),
            max_drawdown=max(symbol_bucket.max_drawdown, strategy_bucket.max_drawdown),
            win_rate=min(symbol_bucket.win_rate, strategy_bucket.win_rate),
            sharpe_ratio=min(symbol_bucket.sharpe, strategy_bucket.sharpe),
        )

    def _changelog(self, report, symbol_bucket, strategy_bucket):
        parts = [
            f"generated_from_daily_review={report.report_id}",
            f"symbol_bucket={symbol_bucket.bucket_value}",
            f"strategy_bucket={strategy_bucket.bucket_value}",
        ]
        best_regime = self._best_bucket(self._positive_buckets(report, "regime"))
        best_feature = self._best_bucket(self._positive_buckets(report, "feature"))
        if best_regime is not None:
            parts.append(f"best_regime={best_regime.bucket_value}")
        if best_feature is not None:
            parts.append(f"best_feature={best_feature.bucket_value}")
        return parts

    def _eligible_buckets(self, report, bucket_type, min_net_pnl):
        buckets = [
            bucket
            for bucket in report.buckets_for(bucket_type)
            if bucket.bucket_value != "unknown"
            and bucket.fill_count >= self.min_trades
            and bucket.net_pnl > min_net_pnl
        ]
        return sorted(
            buckets,
            key=lambda bucket: self._bucket_score(bucket),
            reverse=True,
        )

    def _positive_buckets(self, report, bucket_type):
        return [
            bucket
            for bucket in report.buckets_for(bucket_type)
            if bucket.bucket_value != "unknown"
            and bucket.fill_count >= self.min_trades
            and bucket.net_pnl > 0.0
        ]

    def _best_bucket(self, buckets):
        if not buckets:
            return None
        return max(buckets, key=lambda bucket: self._bucket_score(bucket))

    def _bucket_score(self, bucket):
        if bucket is None:
            return 0.0
        return (
            float(bucket.net_pnl)
            + float(bucket.average_net_pnl)
            + float(bucket.sharpe)
            - float(bucket.max_drawdown)
            - float(bucket.rejection_count)
            - float(bucket.anomaly_count)
        )

    def _confidence_multiplier(self, win_rate, sharpe):
        return self._clamp(1.0 + (win_rate - 0.5) + min(sharpe, 3.0) * 0.05, 0.25, 1.5)

    def _risk_budget_scale(self, symbol_bucket, strategy_bucket):
        pnl_floor = max(abs(symbol_bucket.net_pnl), abs(strategy_bucket.net_pnl), 1.0)
        drawdown_penalty = max(symbol_bucket.max_drawdown, strategy_bucket.max_drawdown) / pnl_floor
        return self._clamp(1.0 - drawdown_penalty, 0.25, 1.25)

    def _queue_id(self, *, report, symbol, strategy_id):
        return (
            f"{self._safe_token(report.report_id)}:"
            f"{self._safe_token(symbol)}:"
            f"{self._safe_token(strategy_id)}"
        )

    def _config_hash(self, parameters):
        payload = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _report(self, report):
        if isinstance(report, DailyReviewReport):
            return report
        return DailyReviewReport.from_payload(report)

    def _queue(self, queue):
        if isinstance(queue, SymbolOptimizationQueue):
            return queue
        if isinstance(queue, (str, Path)):
            return SymbolOptimizationQueue(queue)
        return queue

    def _validation_artifact_store(self, store):
        if store is None:
            return None
        if isinstance(store, StrategyValidationArtifactStore):
            return store
        if isinstance(store, (str, Path)):
            return StrategyValidationArtifactStore(store)
        return store

    def _safe_token(self, value):
        token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
        return token or "unknown"

    def _clamp(self, value, lower, upper):
        return max(lower, min(upper, float(value)))
