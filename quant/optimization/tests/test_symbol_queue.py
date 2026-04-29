import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from pydantic import ValidationError

from quant.optimization import StrategyVersionGate, SymbolOptimizationQueue
from quant.schemas import (
    StrategyValidationMetrics,
    StrategyVersion,
    StrategyVersionStatus,
    SymbolOptimizationQueueRecord,
)


def make_version(version="1.1.0"):
    return StrategyVersion(
        strategy_id="ma_crossover",
        version=version,
        status=StrategyVersionStatus.CANDIDATE,
        created_at=1710000000,
        code_ref="git:abc123",
        config_hash=f"sha256:{version}",
        parameters={"fast_window": 5.0, "slow_window": 20.0},
        parent_version="1.0.0",
        changelog=["candidate"],
        validation_report_id="validation-001",
    )


def make_metrics(report_id="validation-001", total_net_pnl=120.5):
    return StrategyValidationMetrics(
        report_id=report_id,
        generated_at=1710003600,
        trade_count=24,
        total_net_pnl=total_net_pnl,
        max_drawdown=0.08,
        win_rate=0.58,
        sharpe_ratio=1.2,
    )


def test_symbol_optimization_queue_keeps_symbols_isolated(tmp_path):
    queue = SymbolOptimizationQueue(tmp_path)

    btc = queue.enqueue_candidate(
        symbol="BTC/USDT",
        candidate=make_version("1.1.0"),
        queue_id="btc-candidate-001",
        created_at=1710000000,
    )
    eth = queue.enqueue_candidate(
        symbol="ETH/USDT",
        candidate=make_version("2.1.0"),
        queue_id="eth-candidate-001",
        created_at=1710000100,
    )

    assert btc.symbol == "BTC/USDT"
    assert eth.symbol == "ETH/USDT"
    assert [item.queue_id for item in queue.list_records("BTC/USDT")] == [
        "btc-candidate-001"
    ]
    assert [item.queue_id for item in queue.list_records("ETH/USDT")] == [
        "eth-candidate-001"
    ]
    assert queue.get_record("ETH/USDT", "btc-candidate-001") is None
    assert (tmp_path / "BTC_USDT.jsonl").exists()
    assert (tmp_path / "ETH_USDT.jsonl").exists()


def test_symbol_optimization_queue_persists_validation_and_decision(tmp_path):
    queue = SymbolOptimizationQueue(tmp_path)
    candidate = make_version()
    metrics = make_metrics()
    gate = StrategyVersionGate(min_trades=10, min_net_pnl=50.0)

    queue.enqueue_candidate(
        symbol="BTC/USDT",
        candidate=candidate,
        queue_id="candidate-001",
        created_at=1710000000,
    )
    validated = queue.attach_validation(
        symbol="BTC/USDT",
        queue_id="candidate-001",
        metrics=metrics,
    )
    decision = gate.evaluate(
        candidate=candidate,
        metrics=metrics,
        decision_id="promote-001",
        generated_at=1710007200,
    )
    decided = queue.attach_decision(
        symbol="BTC/USDT",
        queue_id="candidate-001",
        decision=decision,
    )

    restored_queue = SymbolOptimizationQueue(tmp_path)
    restored = restored_queue.get_record("BTC/USDT", "candidate-001")

    assert validated.validation_metrics == metrics
    assert decided.promotion_decision == decision
    assert restored == decided


def test_symbol_optimization_queue_rejects_duplicate_ids_per_symbol(tmp_path):
    queue = SymbolOptimizationQueue(tmp_path)
    queue.enqueue_candidate("BTC/USDT", make_version(), "candidate-001", 1710000000)

    with pytest.raises(ValueError):
        queue.enqueue_candidate("BTC/USDT", make_version(), "candidate-001", 1710000001)

    queue.enqueue_candidate("ETH/USDT", make_version(), "candidate-001", 1710000002)
    assert len(queue.list_records("ETH/USDT")) == 1


def test_symbol_optimization_queue_record_rejects_empty_symbol():
    with pytest.raises(ValidationError):
        SymbolOptimizationQueueRecord(
            queue_id="candidate-001",
            symbol="",
            created_at=1710000000,
            candidate=make_version(),
        )
