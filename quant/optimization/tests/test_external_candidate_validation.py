import csv
import json
from pathlib import Path

from quant.optimization.external_candidates import load_external_candidate_file
from quant.optimization.artifact_generation import (
    StrategyValidationArtifactSourceReport,
    StrategyValidationSourceSummary,
)
from quant.schemas import (
    MonteCarloSimulationMethod,
    MonteCarloValidation,
    StrategyValidationSlice,
    StrategyValidationSliceKind,
)
from scripts import run_external_candidate_validation as runner


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def write_candidate_csv(path, candidates):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "symbol",
        "timeframe",
        "strategy_id",
        "parameters",
        "window_config",
        "source",
        "notes",
        "fingerprint",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in candidates:
            row = dict(candidate)
            for field in ("parameters", "window_config", "source"):
                row[field] = json.dumps(row[field], sort_keys=True)
            writer.writerow(row)
    return path


def build_timeframe_file(path, *, symbol, timeframe, bars=80):
    return write_json(
        path,
        {
            "status": "PASS",
            "exchange": "binance",
            "symbol": symbol,
            "timeframe": timeframe,
            "klines": [
                {
                    "timestamp": 1700000000 + index * 300,
                    "open": 100.0 + index,
                    "high": 101.0 + index,
                    "low": 99.0 + index,
                    "close": 100.5 + index,
                    "volume": 1000.0 + index,
                }
                for index in range(bars)
            ],
        },
    )


def build_universe_matrix(path):
    btc_5m = build_timeframe_file(
        path.parent / "btcusdt-5m.json",
        symbol="BTCUSDT",
        timeframe="5m",
    )
    eth_15m = build_timeframe_file(
        path.parent / "ethusdt-15m.json",
        symbol="ETHUSDT",
        timeframe="15m",
    )
    return write_json(
        path,
        {
            "status": "PASS",
            "exchange": "binance",
            "market_type": "spot",
            "reason_codes": [],
            "safety_flags": {
                "network_access_used": True,
                "public_market_data_only": True,
                "real_credentials_read": False,
                "broker_called": False,
                "live_orders_sent": False,
                "analytics_modified_live_state": False,
                "contains_real_credentials": False,
            },
            "symbols": {
                "BTCUSDT": {
                    "status": "PASS",
                    "pass_timeframes": ["5m"],
                    "timeframes": {
                        "5m": {
                            "status": "PASS",
                            "bar_count": 80,
                            "first_timestamp": 1700000000,
                            "last_timestamp": 1700023700,
                            "output_path": str(btc_5m),
                            "sha256": "declared-btc-5m",
                            "reason_codes": [],
                            "quality_report": {"passed": True},
                        }
                    },
                },
                "ETHUSDT": {
                    "status": "PASS",
                    "pass_timeframes": ["15m"],
                    "timeframes": {
                        "15m": {
                            "status": "PASS",
                            "bar_count": 80,
                            "first_timestamp": 1700000000,
                            "last_timestamp": 1700023700,
                            "output_path": str(eth_15m),
                            "sha256": "declared-eth-15m",
                            "reason_codes": [],
                            "quality_report": {"passed": True},
                        }
                    },
                },
            },
        },
    )


def external_candidate(
    *,
    symbol="BTCUSDT",
    timeframe="5m",
    strategy_id="ma_crossover",
    parameters=None,
    train_bars=40,
    test_bars=20,
    fingerprint="fixture-candidate",
):
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_id": strategy_id,
        "parameters": parameters or {"fast_window": 1, "slow_window": 8},
        "window_config": {
            "train_bars": train_bars,
            "test_bars": test_bars,
            "step_bars": 20,
            "holdout_ratio": 0.3,
            "min_trade_count": 5,
        },
        "source": {
            "kind": "fixture",
            "name": "unit-test",
        },
        "notes": "fixture only",
        "fingerprint": fingerprint,
    }


def passing_gate_report(tmp_path, *, timestamp, source_paths, strategy_id, candidate_version, symbol, **_kwargs):
    source_report_dir = tmp_path / "source-reports" / symbol / strategy_id
    source_report_path = source_report_dir / f"{candidate_version}.json"
    source_report = StrategyValidationArtifactSourceReport(
        report_id=f"external-candidate-pass:{candidate_version}",
        strategy_id=strategy_id,
        candidate_version=candidate_version,
        symbol=symbol,
        generated_at=timestamp,
        source_report_id=f"external-candidate-pass:{candidate_version}",
        source_path=str(source_paths[0]),
        summary=StrategyValidationSourceSummary(
            trade_count=36,
            total_net_pnl=42.5,
            max_drawdown=1.8,
            win_rate=0.59,
            sharpe_ratio=1.24,
        ),
        validation_slices=[
            StrategyValidationSlice(
                name="oos-fixture",
                kind=StrategyValidationSliceKind.OUT_OF_SAMPLE,
                trade_count=12,
                total_net_pnl=18.0,
                max_drawdown=0.9,
                win_rate=0.58,
                sharpe_ratio=1.1,
            ),
            StrategyValidationSlice(
                name="wf-fixture-1",
                kind=StrategyValidationSliceKind.WALK_FORWARD,
                trade_count=8,
                total_net_pnl=10.0,
                max_drawdown=0.9,
                win_rate=0.60,
                sharpe_ratio=1.0,
            ),
            StrategyValidationSlice(
                name="wf-fixture-2",
                kind=StrategyValidationSliceKind.WALK_FORWARD,
                trade_count=8,
                total_net_pnl=8.0,
                max_drawdown=0.8,
                win_rate=0.60,
                sharpe_ratio=1.0,
            ),
            StrategyValidationSlice(
                name="wf-fixture-3",
                kind=StrategyValidationSliceKind.WALK_FORWARD,
                trade_count=8,
                total_net_pnl=6.5,
                max_drawdown=0.7,
                win_rate=0.60,
                sharpe_ratio=1.0,
            ),
        ],
        monte_carlo_survival_rate=0.83,
        monte_carlo_validation=MonteCarloValidation(
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
    )
    source_report_dir.mkdir(parents=True, exist_ok=True)
    source_report_path.write_text(
        json.dumps(source_report.to_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    artifact_path = tmp_path / "artifacts" / symbol / strategy_id / f"{candidate_version}.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps({"artifact_id": "fixture-artifact"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "PASS",
        "message": "fixture PASS gate report",
        "generation_kind": "aggregate",
        "reason_codes": ["promotion_gate_passed"],
        "source_report_paths": [str(source_report_path)],
        "generated_source_report_count": 1,
        "artifact_generation_status": "PASS",
        "artifact_generation_report_path": str(tmp_path / "artifact-generation.json"),
        "artifact_count": 1,
        "generated_artifact_count": 1,
        "artifact_paths": [str(artifact_path)],
        "validator_status": "PASS",
        "validator_artifact_count": 1,
        "h_opt_005_ready": True,
        "h_opt_005_blockers": [],
        "results": [
            {
                "status": "PASS",
                "source_report_path": str(source_report_path),
                "metrics": {"trade_count": 36, "total_net_pnl": 42.5},
            }
        ],
        "artifact_generation_report": {
            "status": "PASS",
            "generated_artifact_count": 1,
            "generated": [{"artifact_path": str(artifact_path)}],
            "h_opt_005_ready": True,
            "h_opt_005_blockers": [],
            "validator_report": {
                "status": "PASS",
                "artifact_count": 1,
                "failed_count": 0,
            },
        },
    }


def failing_gate_report(tmp_path, *, timestamp, source_paths, strategy_id, candidate_version, symbol, **_kwargs):
    source_report_dir = tmp_path / "source-reports" / symbol / strategy_id
    source_report_path = source_report_dir / f"{candidate_version}-oos.json"
    source_report_dir.mkdir(parents=True, exist_ok=True)
    source_report_path.write_text(
        json.dumps(
            {
                "report_id": f"external-candidate-fail:{candidate_version}",
                "strategy_id": strategy_id,
                "candidate_version": candidate_version,
                "symbol": symbol,
                "generated_at": timestamp,
                "source_path": str(source_paths[0]),
                "summary": {
                    "trade_count": 1,
                    "total_net_pnl": -3.5,
                    "max_drawdown": 2.1,
                    "win_rate": 0.0,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "status": "SKIPPED",
        "message": "fixture failing gate report",
        "generation_kind": "aggregate",
        "reason_codes": [
            "holdout_trade_count_below_minimum",
            "insufficient_walk_forward_windows",
            "missing_walk_forward_validation",
        ],
        "source_report_paths": [str(source_report_path)],
        "generated_source_report_count": 1,
        "artifact_generation_status": "SKIPPED",
        "artifact_generation_report_path": str(tmp_path / "artifact-generation.json"),
        "artifact_count": 0,
        "generated_artifact_count": 0,
        "artifact_paths": [],
        "validator_status": None,
        "validator_artifact_count": 0,
        "h_opt_005_ready": False,
        "h_opt_005_blockers": [
            "holdout_trade_count_below_minimum",
            "missing_walk_forward_validation",
        ],
        "results": [
            {
                "status": "SKIPPED",
                "source_report_path": str(source_report_path),
                "metrics": {
                    "trade_count": 1,
                    "total_net_pnl": -3.5,
                },
            },
            {
                "status": "SKIPPED",
                "source_report_path": str(source_report_path).replace(
                    "-oos.json",
                    "-wf.json",
                ),
                "metrics": {
                    "walk_forward_window_count": 1,
                    "walk_forward_pass_count": 0,
                    "walk_forward_pass_rate": 0.0,
                },
            },
            {
                "status": "PASS",
                "source_report_path": str(source_report_path).replace(
                    "-oos.json",
                    "-mc.json",
                ),
                "metrics": {
                    "monte_carlo_survival_rate": 0.5,
                },
            },
        ],
        "artifact_generation_report": {
            "status": "SKIPPED",
            "generated_artifact_count": 0,
            "h_opt_005_ready": False,
            "h_opt_005_blockers": [
                "holdout_trade_count_below_minimum",
                "missing_walk_forward_validation",
            ],
            "validator_report": {
                "status": "SKIPPED",
                "artifact_count": 0,
                "failed_count": 0,
            },
        },
    }


def test_external_candidate_schema_normalizes_strategy_params_and_metadata(tmp_path):
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {"schema_version": "1.0", "candidates": [external_candidate()]},
    )

    report = load_external_candidate_file(candidate_path)

    assert report["status"] == "PASS"
    assert report["valid_candidate_count"] == 1
    candidate = report["valid_candidates"][0]
    assert candidate["symbol"] == "BTCUSDT"
    assert candidate["parameters"] == {"fast_window": 1, "slow_window": 8}
    assert candidate["window_config"]["train_bars"] == 40
    assert candidate["required_bar_count"] == 60
    assert candidate["strategy_metadata"]["strategy_id"] == "ma_crossover"
    assert candidate["strategy_metadata"]["cross_symbol"] is True
    assert candidate["fingerprint_verification"]["declared_fingerprint"] == (
        "fixture-candidate"
    )


def test_external_candidate_validation_ingests_universe_covered_trials(tmp_path):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {
            "schema_version": "1.0",
            "candidates": [
                external_candidate(fingerprint="candidate-a"),
                external_candidate(
                    symbol="ETHUSDT",
                    timeframe="15m",
                    strategy_id="ema_trend_filter",
                    parameters={
                        "fast_window": 8,
                        "slow_window": 34,
                        "atr_window": 14,
                        "volatility_window": 20,
                        "max_atr_pct": 0.08,
                        "max_volatility_pct": 0.08,
                        "min_trend_strength": 0.0,
                    },
                    fingerprint="candidate-b",
                ),
            ],
        },
    )
    output_path = tmp_path / "external-validation.json"
    progress_path = tmp_path / "progress.jsonl"

    report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=output_path,
        progress_jsonl=progress_path,
        workers=2,
        max_trials=1,
        progress_interval_seconds=1,
        stop_on_first_pass=True,
        timestamp=1777827600,
    )

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    progress = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert report == saved
    assert report["task_scope"] == "H-OPT-020"
    assert report["implemented_subtasks"] == [
        "H-OPT-020A",
        "H-OPT-020B",
        "H-OPT-020C",
        "H-OPT-020D",
        "H-OPT-020E",
        "H-OPT-020F",
        "H-OPT-020G",
        "H-OPT-020H",
    ]
    assert report["pending_subtasks"] == []
    assert report["status"] == "SKIPPED"
    assert report["ingestion_status"] == "PASS"
    assert report["ready_candidate_count"] == 2
    assert report["planned_trial_count"] == 1
    assert report["completed_trial_count"] == 1
    assert report["executed_trial_count"] == 1
    assert report["pass_count"] == 0
    assert report["artifact_count"] == 0
    assert report["scheduler_mode"] == "bounded_checkpoint_worker_pool"
    assert report["scheduler_summary"]["bounded_worker_pool_used"] is False
    assert report["publication_lock"] == {
        "lock_scope": "thread_safe_pass_artifact_publication",
        "min_walk_forward_pass_rate": 0.67,
        "official_min_walk_forward_pass_rate": 0.67,
        "relaxed_gate_for_flow_only": False,
        "official_h_opt_gate_satisfied": True,
        "min_monte_carlo_survival_rate": 0.8,
        "min_net_pnl": 0.0,
        "official_min_net_pnl": 0.0,
        "min_out_of_sample_net_pnl": 0.0,
        "official_min_out_of_sample_net_pnl": 0.0,
        "non_passing_candidates_publish_artifacts": False,
    }
    assert "external_candidate_scheduler_completed" in report["reason_codes"]
    assert (
        "external_candidate_real_gate_executed"
        in report["reason_codes"]
    )
    assert "pass_artifact_publication_lock_blocked" in report["reason_codes"]
    assert "external_candidate_trials_limited" in report["reason_codes"]
    assert report["planned_trials"][0]["candidate_version"].startswith(
        "external-public-btcusdt-confirm-5m-ma_crossover-"
    )
    assert report["planned_trials"][0]["strategy_parameters"] == {
        "fast_window": 1,
        "slow_window": 8,
    }
    assert report["planned_trials"][0]["window"]["train_bars"] == 40
    assert report["planned_trials"][0]["bar_count"] == 80
    trial = report["all_trials"][0]
    assert trial["status"] == "SKIPPED"
    assert trial["artifact_publication_allowed"] is False
    assert trial["artifact_publication_lock_status"] == (
        "blocked_until_gate_and_strict_validator_pass"
    )
    assert trial["source_report_generation_report_path"]
    assert trial["gate_report"]["status"] == "SKIPPED"
    assert trial["gate_report"]["artifact_count"] == 0
    assert trial["gate_report"]["h_opt_005_ready"] is False
    assert trial["metrics"]["monte_carlo_survival_rate"] == 1.0
    assert len(trial["components"]) == 3
    assert report["safety_flags"]["broker_called"] is False
    assert report["safety_flags"]["live_orders_sent"] is False
    assert len(progress) >= 2
    assert progress[-1]["task_scope"] == "H-OPT-020"
    assert progress[-1]["planned_trial_count"] == 1
    assert progress[-1]["completed_trial_count"] == 1


def test_external_candidate_validation_rejects_invalid_and_uncovered_candidates(tmp_path):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {
            "schema_version": "1.0",
            "candidates": [
                external_candidate(
                    parameters={"fast_window": 8, "slow_window": 1},
                    fingerprint="invalid-params",
                ),
                external_candidate(
                    timeframe="1h",
                    fingerprint="missing-timeframe",
                ),
            ],
        },
    )

    report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=tmp_path / "external-validation.json",
        progress_jsonl=None,
        max_trials=5,
        timestamp=1777827600,
    )

    assert report["ingestion_status"] == "SKIPPED"
    assert report["invalid_external_candidate_count"] == 1
    assert report["rejected_candidate_count"] == 1
    assert report["planned_trial_count"] == 0
    assert report["completed_trial_count"] == 0
    assert "invalid_external_candidates_present" in report["reason_codes"]
    assert "external_candidates_rejected_by_universe_coverage" in report["reason_codes"]
    assert report["rejected_candidates"][0]["reason_codes"] == [
        "candidate_universe_timeframe_missing"
    ]


def test_external_candidate_validation_resume_reuses_completed_trials(tmp_path):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {"schema_version": "1.0", "candidates": [external_candidate()]},
    )
    output_path = tmp_path / "external-validation.json"
    first_report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=output_path,
        progress_jsonl=tmp_path / "progress.jsonl",
        workers=2,
        max_trials=1,
        progress_interval_seconds=0,
        timestamp=1777827600,
    )

    second_report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=tmp_path / "external-validation-resume.json",
        progress_jsonl=None,
        workers=2,
        max_trials=1,
        resume_from=output_path,
        timestamp=1777827601,
    )

    assert first_report["completed_trial_count"] == 1
    assert second_report["planned_trial_count"] == 1
    assert second_report["completed_trial_count"] == 1
    assert second_report["executed_trial_count"] == 0
    assert second_report["resumed_trial_count"] == 1
    assert second_report["all_trials"][0]["status"] == "RESUMED"
    assert second_report["all_trials"][0]["candidate_version"] == (
        first_report["all_trials"][0]["candidate_version"]
    )


def test_external_candidate_validation_publishes_only_after_pass_gate(tmp_path, monkeypatch):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {"schema_version": "1.0", "candidates": [external_candidate()]},
    )

    monkeypatch.setattr(
        runner,
        "run_strategy_validation_source_report_generation",
        lambda **kwargs: passing_gate_report(tmp_path, **kwargs),
    )

    report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=tmp_path / "external-validation.json",
        progress_jsonl=None,
        workers=2,
        max_trials=1,
        progress_interval_seconds=0,
        stop_on_first_pass=True,
        source_report_dir=tmp_path / "source-reports",
        gate_report_dir=tmp_path / "gate-reports",
        artifact_dir=tmp_path / "artifacts",
        timestamp=1777827600,
    )

    assert report["status"] == "PASS"
    assert report["success"] is True
    assert report["pass_count"] == 1
    assert report["artifact_count"] == 1
    assert report["h_opt_005_ready"] is True
    assert report["h_opt_010_ready"] is True
    assert report["validator_status"] == "PASS"
    assert "first_passing_external_candidate_found" in report["reason_codes"]
    assert "pass_artifact_publication_lock_released" in report["reason_codes"]
    assert report["stop_until_pass_workflow"]["pass_found"] is True
    trial = report["passing_candidates"][0]
    assert trial["status"] == "PASS"
    assert trial["artifact_publication_allowed"] is True
    assert trial["artifact_publication_lock_status"] == (
        "published_after_strict_validator_pass"
    )
    assert trial["validator_status"] == "PASS"
    assert trial["h_opt_005_blockers"] == []


def test_external_candidate_validation_timeout_writes_checkpoint(tmp_path):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {"schema_version": "1.0", "candidates": [external_candidate()]},
    )

    report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=tmp_path / "external-validation.json",
        progress_jsonl=tmp_path / "progress.jsonl",
        workers=2,
        max_trials=1,
        max_runtime_seconds=0,
        timestamp=1777827600,
    )

    assert report["status"] == "TIMEOUT"
    assert report["completed_trial_count"] == 0
    assert report["executed_trial_count"] == 0
    assert report["planned_trial_count"] == 1
    assert report["stopped_reason"] == "max_runtime_seconds_reached"
    assert report["all_trials"][0]["status"] == "PENDING"
    assert "max_runtime_seconds_reached" in report["all_trials"][0]["reason_codes"]
    assert "--resume-from" in report["resume_command"]


def test_external_candidate_validation_resume_does_not_reuse_pending_timeout_trials(tmp_path):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {"schema_version": "1.0", "candidates": [external_candidate()]},
    )
    timeout_path = tmp_path / "external-validation-timeout.json"
    timeout_report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=timeout_path,
        progress_jsonl=None,
        workers=1,
        max_trials=1,
        max_runtime_seconds=0,
        timestamp=1777827600,
    )

    resumed_report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=tmp_path / "external-validation-resumed.json",
        progress_jsonl=None,
        workers=1,
        max_trials=1,
        resume_from=timeout_path,
        timestamp=1777827601,
    )

    assert timeout_report["all_trials"][0]["status"] == "PENDING"
    assert resumed_report["resumed_candidate_version_count"] == 0
    assert resumed_report["executed_trial_count"] == 1
    assert resumed_report["resumed_trial_count"] == 0
    assert resumed_report["all_trials"][0]["status"] != "RESUMED"


def test_external_candidate_validation_fixture_e2e_covers_scheduler_progress_resume_and_publication_lock(
    tmp_path,
    monkeypatch,
):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_candidate_csv(
        tmp_path / "external-candidates.csv",
        [
            external_candidate(fingerprint="csv-candidate-a"),
            external_candidate(
                symbol="ETHUSDT",
                timeframe="15m",
                strategy_id="ema_trend_filter",
                parameters={
                    "fast_window": 8,
                    "slow_window": 34,
                    "atr_window": 14,
                    "volatility_window": 20,
                    "max_atr_pct": 0.08,
                    "max_volatility_pct": 0.08,
                    "min_trend_strength": 0.0,
                },
                fingerprint="csv-candidate-b",
            ),
        ],
    )
    output_path = tmp_path / "external-validation.json"
    progress_path = tmp_path / "progress.jsonl"
    calls = []

    def fixture_gate_report(**kwargs):
        calls.append(kwargs["candidate_version"])
        return failing_gate_report(tmp_path, **kwargs)

    monkeypatch.setattr(
        runner,
        "run_strategy_validation_source_report_generation",
        fixture_gate_report,
    )

    report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=output_path,
        progress_jsonl=progress_path,
        workers=2,
        max_trials=2,
        progress_interval_seconds=0,
        max_runtime_seconds=60,
        keep_running_until_pass_with_timeout=True,
        source_report_dir=tmp_path / "source-reports",
        gate_report_dir=tmp_path / "gate-reports",
        artifact_dir=tmp_path / "artifacts",
        timestamp=1777827600,
    )

    progress = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert report == json.loads(output_path.read_text(encoding="utf-8"))
    assert report["implemented_subtasks"][-1] == "H-OPT-020H"
    assert report["pending_subtasks"] == []
    assert report["external_candidate_parse_report"]["raw_payload_shape"] == {
        "type": "list",
        "candidate_count": 2,
    }
    assert report["scheduler_summary"]["bounded_worker_pool_used"] is True
    assert report["scheduler_summary"]["worker_count"] == 2
    assert report["planned_trial_count"] == 2
    assert report["completed_trial_count"] == 2
    assert report["executed_trial_count"] == 2
    assert report["resumed_trial_count"] == 0
    assert report["pass_count"] == 0
    assert report["artifact_count"] == 0
    assert report["artifact_paths"] == []
    assert len(calls) == 2
    assert {trial["status"] for trial in report["all_trials"]} == {"SKIPPED"}
    assert all(
        trial["artifact_publication_allowed"] is False
        for trial in report["all_trials"]
    )
    assert all(
        trial["artifact_publication_lock_status"]
        == "blocked_until_gate_and_strict_validator_pass"
        for trial in report["all_trials"]
    )
    assert all(
        trial["gate_report"]["artifact_count"] == 0
        for trial in report["all_trials"]
    )
    assert "pass_artifact_publication_lock_blocked" in report["reason_codes"]
    assert "no_external_candidate_passed" in report["reason_codes"]
    assert report["stop_until_pass_workflow"]["enabled"] is True
    assert report["stop_until_pass_workflow"]["should_resume"] is True
    assert "--resume-from" in report["resume_command"]
    assert any(
        item["progress_summary"]["active_worker_count"] == 2
        for item in progress
    )
    assert progress[-1]["completed_trial_count"] == 2
    assert progress[-1]["progress_summary"]["validation_percent_complete"] == 100.0

    def resume_must_not_execute(**_kwargs):
        raise AssertionError("resume should not re-execute completed fixture trials")

    monkeypatch.setattr(
        runner,
        "run_strategy_validation_source_report_generation",
        resume_must_not_execute,
    )

    resumed_report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=tmp_path / "external-validation-resumed.json",
        progress_jsonl=None,
        workers=2,
        max_trials=2,
        resume_from=output_path,
        progress_interval_seconds=0,
        timestamp=1777827601,
    )

    assert resumed_report["resumed_candidate_version_count"] == 2
    assert resumed_report["executed_trial_count"] == 0
    assert resumed_report["resumed_trial_count"] == 2
    assert {trial["status"] for trial in resumed_report["all_trials"]} == {"RESUMED"}


def test_external_candidate_validation_keep_running_until_pass_outputs_resume_workflow(tmp_path):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {"schema_version": "1.0", "candidates": [external_candidate()]},
    )
    output_path = tmp_path / "external-validation.json"

    report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=output_path,
        progress_jsonl=None,
        workers=1,
        max_trials=1,
        max_runtime_seconds=60,
        keep_running_until_pass_with_timeout=True,
        timestamp=1777827600,
    )

    workflow = report["stop_until_pass_workflow"]
    assert report["keep_running_until_pass_with_timeout"] is True
    assert report["effective_stop_on_first_pass"] is True
    assert report["implemented_subtasks"][-1] == "H-OPT-020H"
    assert report["pending_subtasks"] == []
    assert "stop_until_pass_workflow_enabled" in report["reason_codes"]
    assert "stop_until_pass_resume_command_available" in report["reason_codes"]
    assert "--keep-running-until-pass-with-timeout" in report["resume_command"]
    assert workflow["enabled"] is True
    assert workflow["mode"] == "keep_running_until_pass_with_timeout"
    assert workflow["timeout_configured"] is True
    assert workflow["pass_found"] is False
    assert workflow["should_resume"] is True
    assert workflow["resume_command"] == report["resume_command"]


def test_external_candidate_validation_main_writes_report(tmp_path):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {"schema_version": "1.0", "candidates": [external_candidate()]},
    )
    output_path = tmp_path / "external-validation.json"

    exit_code = runner.main(
        [
            "--universe-matrix",
            str(universe_path),
            "--external-candidates",
            str(candidate_path),
            "--output",
            str(output_path),
            "--progress-jsonl",
            str(tmp_path / "progress.jsonl"),
            "--max-trials",
            "1",
            "--timestamp",
            "1777827600",
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["ingestion_status"] == "PASS"
    assert report["planned_trial_count"] == 1


def test_external_candidate_validation_relaxed_wf_threshold_is_flow_only(tmp_path, monkeypatch):
    universe_path = build_universe_matrix(tmp_path / "public-universe.json")
    candidate_path = write_json(
        tmp_path / "external-candidates.json",
        {"schema_version": "1.0", "candidates": [external_candidate(fingerprint="candidate-relaxed")]},
    )

    def fake_execute(**kwargs):
        assert kwargs["min_walk_forward_pass_rate"] == 0.4
        trial = dict(kwargs["trial"])
        trial.update(
            {
                "status": "SKIPPED",
                "success": False,
                "artifact_publication_allowed": False,
                "artifact_paths": [],
                "source_report_paths": [],
                "reason_codes": ["walk_forward_pass_rate_below_threshold"],
                "metrics": {"walk_forward_pass_rate": 0.4, "monte_carlo_survival_rate": 1.0},
            }
        )
        return trial

    monkeypatch.setattr(runner, "_execute_validation_trial", fake_execute)

    report = runner.run_external_candidate_validation(
        universe_matrix=universe_path,
        external_candidates=candidate_path,
        output_path=tmp_path / "relaxed.json",
        progress_jsonl=tmp_path / "relaxed-progress.jsonl",
        workers=1,
        max_trials=1,
        min_walk_forward_pass_rate=0.4,
        timestamp=1777827600,
    )

    assert report["gate_thresholds"]["min_walk_forward_pass_rate"] == 0.4
    assert report["gate_thresholds"]["official_min_walk_forward_pass_rate"] == 0.67
    assert report["gate_thresholds"]["relaxed_gate_for_flow_only"] is True
    assert report["gate_thresholds"]["official_h_opt_gate_satisfied"] is False
    assert report["publication_lock"]["relaxed_gate_for_flow_only"] is True
    assert "--min-walk-forward-pass-rate 0.4" in report["resume_command"]
