import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

import pytest

from quant.optimization import (
    StrategyLifecycleManager,
    StrategyVersionGate,
    SymbolOptimizationQueue,
)
from quant.schemas import (
    StrategyDeploymentRecord,
    StrategyLifecycleAction,
    StrategyLifecycleStatus,
    StrategyValidationMetrics,
    StrategyVersion,
    StrategyVersionStatus,
)


def make_record(status=StrategyLifecycleStatus.CANDIDATE):
    return StrategyDeploymentRecord(
        deployment_id="deploy-ma-001",
        strategy_id="ma_crossover",
        version="1.1.0",
        status=status,
        environment="paper",
        symbol="BTCUSDT",
        previous_version="1.0.0",
        reason_codes=["created_from_candidate"],
    )


def make_version():
    return StrategyVersion(
        strategy_id="ma_crossover",
        version="1.1.0",
        status=StrategyVersionStatus.CANDIDATE,
        created_at=1710000000,
        code_ref="git:abc123",
        config_hash="sha256:ma-1.1.0",
        parameters={"fast_window": 5.0, "slow_window": 20.0},
        parent_version="1.0.0",
        changelog=["candidate"],
        validation_report_id="validation-001",
    )


def make_metrics(total_net_pnl=120.5):
    return StrategyValidationMetrics(
        report_id="validation-001",
        generated_at=1710003600,
        trade_count=24,
        total_net_pnl=total_net_pnl,
        max_drawdown=0.08,
        win_rate=0.58,
        sharpe_ratio=1.2,
    )


def test_strategy_lifecycle_round_trip_and_version_id():
    record = make_record()

    payload = record.to_payload()
    restored = StrategyDeploymentRecord.from_payload(payload)

    assert payload["status"] == "candidate"
    assert record.version_id == "ma_crossover:1.1.0"
    assert restored == record


def test_strategy_lifecycle_manager_runs_full_status_flow():
    manager = StrategyLifecycleManager()
    record = make_record()

    record, transition = manager.transition(
        record,
        StrategyLifecycleAction.START_BACKTEST,
        transition_id="life-001",
        generated_at=1710000000,
    )

    assert record.status == StrategyLifecycleStatus.BACKTEST
    assert transition.from_status == StrategyLifecycleStatus.CANDIDATE
    assert transition.to_status == StrategyLifecycleStatus.BACKTEST
    assert transition.deployment_id == "deploy-ma-001"

    for action, expected_status in [
        (StrategyLifecycleAction.START_PAPER, StrategyLifecycleStatus.PAPER),
        (StrategyLifecycleAction.APPROVE, StrategyLifecycleStatus.APPROVED),
        (StrategyLifecycleAction.DEPLOY, StrategyLifecycleStatus.DEPLOYED),
    ]:
        record, _ = manager.transition(
            record,
            action,
            transition_id=f"life-{action.value}",
            generated_at=1710000001,
        )
        assert record.status == expected_status

    assert record.deployed_at == 1710000001


def test_strategy_lifecycle_manager_rejects_out_of_order_transition():
    manager = StrategyLifecycleManager()
    record = make_record(status=StrategyLifecycleStatus.CANDIDATE)

    try:
        manager.transition(
            record,
            StrategyLifecycleAction.DEPLOY,
            transition_id="life-invalid",
            generated_at=1710000000,
        )
    except ValueError as exc:
        assert "requires approved" in str(exc)
    else:
        raise AssertionError("deploy must require approved status")


def test_strategy_lifecycle_promotes_approved_optimization_queue_record(tmp_path):
    queue = SymbolOptimizationQueue(tmp_path)
    candidate = make_version()
    metrics = make_metrics()
    decision = StrategyVersionGate(min_trades=10, min_net_pnl=50.0).evaluate(
        candidate=candidate,
        metrics=metrics,
        decision_id="promote-001",
        generated_at=1710007200,
    )
    queue.enqueue_candidate(
        symbol="BTC/USDT",
        candidate=candidate,
        queue_id="candidate-001",
        created_at=1710000000,
    )
    queue.attach_validation("BTC/USDT", "candidate-001", metrics)
    queue.attach_decision("BTC/USDT", "candidate-001", decision)

    record, transitions = StrategyLifecycleManager().promote_from_optimization_queue(
        queue=queue,
        symbol="BTC/USDT",
        queue_id="candidate-001",
        record=make_record(),
        transition_id_prefix="life-promote",
        generated_at=1710007201,
    )

    assert record.status == StrategyLifecycleStatus.APPROVED
    assert [item.action for item in transitions] == [
        StrategyLifecycleAction.START_BACKTEST,
        StrategyLifecycleAction.START_PAPER,
        StrategyLifecycleAction.APPROVE,
    ]
    assert transitions[-1].reason_codes == ["promotion_gate_passed"]


def test_strategy_lifecycle_rejects_failed_optimization_decision(tmp_path):
    queue = SymbolOptimizationQueue(tmp_path)
    candidate = make_version()
    metrics = make_metrics(total_net_pnl=-10.0)
    decision = StrategyVersionGate(min_trades=10, min_net_pnl=50.0).evaluate(
        candidate=candidate,
        metrics=metrics,
        decision_id="reject-001",
        generated_at=1710007200,
    )
    queue.enqueue_candidate(
        symbol="BTC/USDT",
        candidate=candidate,
        queue_id="candidate-001",
        created_at=1710000000,
    )
    queue.attach_decision("BTC/USDT", "candidate-001", decision)

    with pytest.raises(ValueError, match="rejected candidate"):
        StrategyLifecycleManager().promote_from_optimization_queue(
            queue=queue,
            symbol="BTC/USDT",
            queue_id="candidate-001",
            record=make_record(),
            transition_id_prefix="life-promote",
            generated_at=1710007201,
        )


def test_strategy_lifecycle_can_retire_or_rollback_deployed_version():
    manager = StrategyLifecycleManager()
    deployed = make_record(
        status=StrategyLifecycleStatus.DEPLOYED,
    )

    retired, retire_transition = manager.transition(
        deployed,
        StrategyLifecycleAction.RETIRE,
        transition_id="life-retire",
        generated_at=1710001000,
        reason_codes=["superseded"],
    )
    rolled_back, rollback_transition = manager.transition(
        deployed,
        StrategyLifecycleAction.ROLLBACK,
        transition_id="life-rollback",
        generated_at=1710002000,
        reason_codes=["paper_regression"],
    )

    assert retired.status == StrategyLifecycleStatus.RETIRED
    assert retired.retired_at == 1710001000
    assert retire_transition.reason_codes == ["superseded"]
    assert rolled_back.status == StrategyLifecycleStatus.ROLLED_BACK
    assert rolled_back.retired_at == 1710002000
    assert rollback_transition.reason_codes == ["paper_regression"]


def test_strategy_lifecycle_schema_rejects_invalid_values():
    try:
        make_record(status=StrategyLifecycleStatus.RETIRED)
    except ValidationError:
        pass
    else:
        raise AssertionError("retired lifecycle records must include retired_at")

    try:
        StrategyDeploymentRecord(
            deployment_id="",
            strategy_id="ma_crossover",
            version="1.1.0",
            status=StrategyLifecycleStatus.CANDIDATE,
            environment="paper",
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("deployment_id must be non-empty")
