import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from quant.optimization import (
    DailyReviewOptimizationPlanner,
    StrategyValidationArtifactStore,
    StrategyVersionGate,
    SymbolOptimizationQueue,
)
from quant.schemas import (
    DailyReviewBucket,
    DailyReviewReport,
    StrategyValidationArtifact,
    StrategyPromotionAction,
    StrategyValidationMetrics,
    StrategyValidationSlice,
    StrategyValidationSliceKind,
    StrategyVersionStatus,
)


def make_bucket(bucket_type, bucket_value, net_pnl, *, fill_count=4, win_rate=0.75, sharpe=1.2, max_drawdown=2.0):
    return DailyReviewBucket(
        bucket_type=bucket_type,
        bucket_value=bucket_value,
        gross_pnl=net_pnl + 0.2,
        fees=0.2,
        net_pnl=net_pnl,
        average_net_pnl=net_pnl / fill_count if fill_count else 0.0,
        win_rate=win_rate,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        fill_count=fill_count,
        winning_trades=3 if net_pnl > 0 else 0,
        losing_trades=1 if net_pnl > 0 else fill_count,
    )


def make_report():
    return DailyReviewReport(
        report_id="daily-001",
        run_id="paper-review-001",
        trading_date="2024-03-09",
        generated_at=1710003600,
        buckets=[
            make_bucket("symbol", "BTCUSDT", 20.0),
            make_bucket("strategy", "ma_crossover", 18.0),
            make_bucket("regime", "trend", 16.0),
            make_bucket("feature", "funding_rate:positive", 14.0),
            make_bucket("feature", "spread:negative", -3.0, win_rate=0.25),
        ],
        total_net_pnl=20.0,
        fill_count=4,
        winning_trades=3,
        losing_trades=1,
    )


def test_daily_review_planner_enqueues_candidate_and_triggers_strict_validation_gate(tmp_path):
    gate = StrategyVersionGate(
        min_trades=2,
        min_net_pnl=5.0,
        min_win_rate=0.5,
        require_out_of_sample=True,
        min_walk_forward_windows=1,
        min_walk_forward_pass_rate=0.5,
        min_monte_carlo_survival_rate=0.8,
    )
    planner = DailyReviewOptimizationPlanner(gate=gate)
    queue = SymbolOptimizationQueue(tmp_path)

    records = planner.enqueue_from_report(make_report(), queue)

    assert len(records) == 1
    record = records[0]
    assert record.symbol == "BTCUSDT"
    assert record.candidate.strategy_id == "ma_crossover"
    assert record.candidate.status == StrategyVersionStatus.CANDIDATE
    assert record.candidate.code_ref == "daily-review:daily-001"
    assert record.candidate.config_hash.startswith("sha256:")
    assert record.candidate.parameters["review_symbol_net_pnl"] == 20.0
    assert record.candidate.parameters["review_strategy_net_pnl"] == 18.0
    assert record.candidate.parameters["positive_regime_count"] == 1.0
    assert record.candidate.parameters["positive_feature_count"] == 1.0
    assert "best_regime=trend" in record.candidate.changelog
    assert "best_feature=funding_rate:positive" in record.candidate.changelog

    assert record.validation_metrics.trade_count == 4
    assert record.validation_metrics.total_net_pnl == 18.0
    assert record.promotion_decision.action == StrategyPromotionAction.REJECT
    assert "missing_out_of_sample_validation" in record.promotion_decision.reason_codes
    assert "insufficient_walk_forward_windows" in record.promotion_decision.reason_codes
    assert "missing_walk_forward_validation" in record.promotion_decision.reason_codes
    assert "missing_monte_carlo_validation" in record.promotion_decision.reason_codes

    restored = queue.get_record("BTCUSDT", record.queue_id)
    assert restored == record


def test_daily_review_planner_accepts_external_validation_metrics_for_gate(tmp_path):
    gate = StrategyVersionGate(
        min_trades=2,
        min_net_pnl=5.0,
        min_win_rate=0.5,
        require_out_of_sample=True,
        min_walk_forward_windows=1,
        min_walk_forward_pass_rate=0.5,
        min_monte_carlo_survival_rate=0.8,
    )
    planner = DailyReviewOptimizationPlanner(gate=gate)

    def validation_factory(report, symbol_bucket, strategy_bucket, candidate):
        return StrategyValidationMetrics(
            report_id=f"{report.report_id}:{candidate.version}:robust-validation",
            generated_at=report.generated_at + 60,
            trade_count=24,
            total_net_pnl=120.0,
            max_drawdown=1.5,
            win_rate=0.62,
            sharpe_ratio=1.4,
            validation_slices=[
                StrategyValidationSlice(
                    name="oos",
                    kind=StrategyValidationSliceKind.OUT_OF_SAMPLE,
                    trade_count=8,
                    total_net_pnl=30.0,
                    max_drawdown=0.6,
                    win_rate=0.63,
                    sharpe_ratio=1.1,
                ),
                StrategyValidationSlice(
                    name="walk-forward-001",
                    kind=StrategyValidationSliceKind.WALK_FORWARD,
                    trade_count=8,
                    total_net_pnl=28.0,
                    max_drawdown=0.7,
                    win_rate=0.60,
                    sharpe_ratio=1.0,
                ),
            ],
            monte_carlo_survival_rate=0.91,
        )

    record = planner.enqueue_from_report(
        make_report(),
        tmp_path,
        validation_metrics_factory=validation_factory,
    )[0]

    assert record.validation_metrics.report_id.endswith("robust-validation")
    assert record.promotion_decision.action == StrategyPromotionAction.APPROVE
    assert record.promotion_decision.reason_codes == ["promotion_gate_passed"]


def test_daily_review_planner_uses_real_validation_artifact_store(tmp_path):
    gate = StrategyVersionGate(
        min_trades=2,
        min_net_pnl=5.0,
        min_win_rate=0.5,
        require_out_of_sample=True,
        min_walk_forward_windows=1,
        min_walk_forward_pass_rate=0.5,
        min_monte_carlo_survival_rate=0.8,
    )
    artifact_store = StrategyValidationArtifactStore(tmp_path / "validation-artifacts")
    artifact_store.write_artifact(
        StrategyValidationArtifact(
            artifact_id="validation-artifact-001",
            source_report_id="daily-001",
            strategy_id="ma_crossover",
            candidate_version="review-2024-03-09-BTCUSDT-ma_crossover",
            symbol="BTCUSDT",
            generated_at=1710007200,
            metrics=StrategyValidationMetrics(
                report_id="oos-wf-mc-001",
                generated_at=1710007200,
                trade_count=30,
                total_net_pnl=140.0,
                max_drawdown=1.1,
                win_rate=0.63,
                sharpe_ratio=1.5,
                validation_slices=[
                    StrategyValidationSlice(
                        name="oos-2024-q1",
                        kind=StrategyValidationSliceKind.OUT_OF_SAMPLE,
                        trade_count=10,
                        total_net_pnl=36.0,
                        max_drawdown=0.5,
                        win_rate=0.6,
                        sharpe_ratio=1.2,
                    ),
                    StrategyValidationSlice(
                        name="walk-forward-001",
                        kind=StrategyValidationSliceKind.WALK_FORWARD,
                        trade_count=10,
                        total_net_pnl=34.0,
                        max_drawdown=0.6,
                        win_rate=0.61,
                        sharpe_ratio=1.1,
                    ),
                ],
                monte_carlo_survival_rate=0.92,
            ),
        )
    )

    record = DailyReviewOptimizationPlanner(gate=gate).enqueue_from_report(
        make_report(),
        tmp_path / "queue",
        validation_artifact_store=artifact_store,
    )[0]

    assert record.validation_metrics.report_id == "oos-wf-mc-001"
    assert record.promotion_decision.action == StrategyPromotionAction.APPROVE
    assert record.promotion_decision.reason_codes == ["promotion_gate_passed"]


def test_daily_review_planner_requires_configured_validation_artifact(tmp_path):
    planner = DailyReviewOptimizationPlanner(
        validation_artifact_store=tmp_path / "missing-validation-artifacts"
    )

    with pytest.raises(FileNotFoundError, match="missing strategy validation artifact"):
        planner.enqueue_from_report(make_report(), tmp_path / "queue")


def test_daily_review_planner_is_idempotent_per_report_symbol_and_strategy(tmp_path):
    planner = DailyReviewOptimizationPlanner()
    queue = SymbolOptimizationQueue(tmp_path)
    report = make_report()

    first = planner.enqueue_from_report(report, queue)
    second = planner.enqueue_from_report(report, queue)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].queue_id == second[0].queue_id
    assert len(queue.list_records("BTCUSDT")) == 1


def test_daily_review_planner_skips_reports_without_profitable_symbol_buckets(tmp_path):
    report = DailyReviewReport(
        report_id="daily-002",
        run_id="paper-review-002",
        trading_date="2024-03-10",
        generated_at=1710090000,
        buckets=[
            make_bucket("symbol", "BTCUSDT", -2.0, win_rate=0.25),
            make_bucket("strategy", "ma_crossover", 8.0),
        ],
        total_net_pnl=-2.0,
        fill_count=4,
        losing_trades=4,
    )

    records = DailyReviewOptimizationPlanner().enqueue_from_report(report, tmp_path)

    assert records == []
