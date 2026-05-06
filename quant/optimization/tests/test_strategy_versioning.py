import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from quant.optimization import StrategyVersionGate
from quant.schemas import (
    MonteCarloSimulationMethod,
    MonteCarloValidation,
    StrategyPromotionAction,
    StrategyValidationArtifact,
    StrategyValidationMetrics,
    StrategyValidationSlice,
    StrategyValidationSliceKind,
    StrategyVersion,
    StrategyVersionStatus,
)


def make_version(status=StrategyVersionStatus.CANDIDATE):
    return StrategyVersion(
        strategy_id="ma_crossover",
        version="1.1.0",
        status=status,
        created_at=1710000000,
        code_ref="git:abc123",
        config_hash="sha256:config",
        parameters={"fast_window": 5.0, "slow_window": 20.0},
        parent_version="1.0.0",
        changelog=["tighten slow window"],
        validation_report_id="attr-001",
    )


def make_metrics(**overrides):
    values = {
        "report_id": "validation-001",
        "generated_at": 1710003600,
        "trade_count": 24,
        "total_net_pnl": 120.5,
        "max_drawdown": 0.08,
        "win_rate": 0.58,
        "sharpe_ratio": 1.2,
        "monte_carlo_validation": MonteCarloValidation(
            method=MonteCarloSimulationMethod.HYBRID,
            run_count=500,
            perturbation_dimensions=[
                "trade_order_shuffle",
                "return_perturbation",
                "slippage_fee_perturbation",
            ],
            seed=42,
            survival_threshold=0.8,
        ),
    }
    values.update(overrides)
    return StrategyValidationMetrics(**values)


def make_slice(kind, name, **overrides):
    values = {
        "name": name,
        "kind": kind,
        "trade_count": 12,
        "total_net_pnl": 30.0,
        "max_drawdown": 0.06,
        "win_rate": 0.55,
        "sharpe_ratio": 1.0,
    }
    values.update(overrides)
    return StrategyValidationSlice(**values)


def test_strategy_version_round_trip_and_version_id():
    version = make_version()

    payload = version.to_payload()
    restored = StrategyVersion.from_payload(payload)

    assert payload["status"] == "candidate"
    assert version.version_id == "ma_crossover:1.1.0"
    assert restored == version


def test_strategy_promotion_gate_approves_candidate_that_passes_thresholds():
    gate = StrategyVersionGate(
        min_trades=10,
        min_net_pnl=50.0,
        max_drawdown=0.1,
        min_win_rate=0.5,
    )

    decision = gate.evaluate(
        candidate=make_version(),
        metrics=make_metrics(),
        decision_id="promote-001",
        generated_at=1710007200,
    )

    assert decision.action == StrategyPromotionAction.APPROVE
    assert decision.approved is True
    assert decision.baseline_version == "1.0.0"
    assert decision.reason_codes == ["promotion_gate_passed"]
    assert gate.next_status(decision) == StrategyVersionStatus.APPROVED


def test_strategy_promotion_gate_rejects_failed_candidate():
    gate = StrategyVersionGate(
        min_trades=10,
        min_net_pnl=50.0,
        max_drawdown=0.1,
        min_win_rate=0.5,
    )

    decision = gate.evaluate(
        candidate=make_version(status=StrategyVersionStatus.DRAFT),
        metrics=make_metrics(trade_count=4, total_net_pnl=-5.0, max_drawdown=0.2),
        decision_id="promote-002",
        generated_at=1710007200,
    )

    assert decision.action == StrategyPromotionAction.REJECT
    assert decision.approved is False
    assert decision.reason_codes == [
        "candidate_status_required",
        "insufficient_trades",
        "net_pnl_below_threshold",
        "drawdown_above_threshold",
    ]
    assert gate.next_status(decision) == StrategyVersionStatus.REJECTED


def test_strategy_promotion_gate_requires_anti_overfit_validation():
    gate = StrategyVersionGate(
        min_trades=10,
        min_net_pnl=50.0,
        max_drawdown=0.1,
        min_win_rate=0.5,
        require_out_of_sample=True,
        min_out_of_sample_net_pnl=10.0,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.66,
        min_monte_carlo_survival_rate=0.9,
    )
    metrics = make_metrics(
        validation_slices=[
            make_slice(StrategyValidationSliceKind.OUT_OF_SAMPLE, "oos-2024"),
            make_slice(StrategyValidationSliceKind.WALK_FORWARD, "wf-1"),
            make_slice(StrategyValidationSliceKind.WALK_FORWARD, "wf-2"),
            make_slice(
                StrategyValidationSliceKind.WALK_FORWARD,
                "wf-3",
                total_net_pnl=-2.0,
            ),
        ],
        monte_carlo_survival_rate=0.94,
    )

    decision = gate.evaluate(
        candidate=make_version(),
        metrics=metrics,
        decision_id="promote-anti-overfit-001",
        generated_at=1710007200,
    )

    assert decision.action == StrategyPromotionAction.APPROVE
    assert decision.reason_codes == ["promotion_gate_passed"]


def test_strategy_promotion_gate_rejects_missing_or_failed_anti_overfit_checks():
    gate = StrategyVersionGate(
        require_out_of_sample=True,
        min_walk_forward_windows=2,
        min_walk_forward_pass_rate=0.5,
        min_monte_carlo_survival_rate=0.9,
    )

    decision = gate.evaluate(
        candidate=make_version(),
        metrics=make_metrics(),
        decision_id="promote-anti-overfit-002",
        generated_at=1710007200,
    )

    assert decision.action == StrategyPromotionAction.REJECT
    assert decision.reason_codes == [
        "missing_out_of_sample_validation",
        "insufficient_walk_forward_windows",
        "missing_walk_forward_validation",
        "missing_monte_carlo_validation",
    ]


def test_strategy_promotion_gate_rejects_missing_monte_carlo_metadata():
    gate = StrategyVersionGate(
        require_out_of_sample=True,
        min_walk_forward_windows=1,
        min_walk_forward_pass_rate=0.5,
        min_monte_carlo_survival_rate=0.8,
    )

    decision = gate.evaluate(
        candidate=make_version(),
        metrics=make_metrics(
            validation_slices=[
                make_slice(StrategyValidationSliceKind.OUT_OF_SAMPLE, "oos-2024"),
                make_slice(StrategyValidationSliceKind.WALK_FORWARD, "wf-1"),
            ],
            monte_carlo_survival_rate=0.91,
            monte_carlo_validation=None,
        ),
        decision_id="promote-anti-overfit-003",
        generated_at=1710007200,
    )

    assert decision.action == StrategyPromotionAction.REJECT
    assert decision.reason_codes == ["missing_monte_carlo_validation"]


def test_strategy_promotion_gate_rejects_monte_carlo_survival_rate_below_threshold():
    gate = StrategyVersionGate(
        require_out_of_sample=True,
        min_walk_forward_windows=1,
        min_walk_forward_pass_rate=0.5,
        min_monte_carlo_survival_rate=0.8,
    )

    decision = gate.evaluate(
        candidate=make_version(),
        metrics=make_metrics(
            validation_slices=[
                make_slice(StrategyValidationSliceKind.OUT_OF_SAMPLE, "oos-2024"),
                make_slice(StrategyValidationSliceKind.WALK_FORWARD, "wf-1"),
            ],
            monte_carlo_survival_rate=0.72,
        ),
        decision_id="promote-anti-overfit-004",
        generated_at=1710007200,
    )

    assert decision.action == StrategyPromotionAction.REJECT
    assert decision.reason_codes == ["monte_carlo_survival_rate_below_threshold"]


def test_strategy_validation_slice_rejects_invalid_values():
    try:
        make_slice(StrategyValidationSliceKind.WALK_FORWARD, "")
    except ValidationError:
        pass
    else:
        raise AssertionError("slice name must be non-empty")

    try:
        make_slice(StrategyValidationSliceKind.WALK_FORWARD, "wf", trade_count=-1)
    except ValidationError:
        pass
    else:
        raise AssertionError("slice trade count must be non-negative")


def test_strategy_validation_metrics_reject_invalid_values():
    try:
        make_metrics(trade_count=-1)
    except ValidationError:
        pass
    else:
        raise AssertionError("trade count must be non-negative")

    try:
        make_metrics(max_drawdown=-0.1)
    except ValidationError:
        pass
    else:
        raise AssertionError("drawdown must be non-negative")


def test_strategy_validation_artifact_round_trip_and_rejects_empty_identity():
    artifact = StrategyValidationArtifact(
        artifact_id="artifact-001",
        strategy_id="ma_crossover",
        candidate_version="review-2024-03-09-BTCUSDT-ma_crossover",
        symbol="BTCUSDT",
        generated_at=1710007200,
        source_report_id="daily-001",
        source_path="logs/validation/artifact-001.json",
        metrics=make_metrics(report_id="oos-wf-mc-001"),
    )

    payload = artifact.to_payload()
    restored = StrategyValidationArtifact.from_payload(payload)

    assert payload["metrics"]["report_id"] == "oos-wf-mc-001"
    assert restored.to_payload() == payload

    try:
        StrategyValidationArtifact(
            artifact_id="",
            strategy_id="ma_crossover",
            candidate_version="candidate-001",
            symbol="BTCUSDT",
            generated_at=1710007200,
            metrics=make_metrics(),
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("artifact identity must be non-empty")
