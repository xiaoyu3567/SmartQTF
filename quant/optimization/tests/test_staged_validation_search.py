import json
from pathlib import Path

from scripts import run_expanded_public_btcusdt_validation_search as search


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def build_timeframe_file(path, *, timeframe):
    return write_json(
        path,
        {
            "status": "PASS",
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "timeframe": timeframe,
            "klines": [
                {
                    "timestamp": 1700000000 + index * 60,
                    "open": 100.0 + index,
                    "high": 101.0 + index,
                    "low": 99.0 + index,
                    "close": 100.5 + index,
                    "volume": 1000.0 + index,
                }
                for index in range(180)
            ],
        },
    )


def build_matrix(path):
    one_minute_path = build_timeframe_file(
        path.parent / "btcusdt-1m.json",
        timeframe="1m",
    )
    return write_json(
        path,
        {
            "status": "PASS",
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "pass_timeframes": ["1m"],
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
            "timeframes": {
                "1m": {
                    "status": "PASS",
                    "bar_count": 180,
                    "first_timestamp": 1700000000,
                    "last_timestamp": 1700010740,
                    "output_path": str(one_minute_path),
                    "sha256": "declared-1m",
                    "reason_codes": [],
                    "quality_report": {"passed": True},
                },
            },
        },
    )


def fake_aggregate_report(*, status="PASS", artifact_paths=None):
    artifact_paths = artifact_paths or ["/tmp/screening-or-confirm-artifact.json"]
    return {
        "status": status,
        "success": status == "PASS",
        "message": "fake aggregate report",
        "reason_codes": [],
        "safety_flags": {
            "analytics_modified_live_state": False,
            "broker_called": False,
            "contains_real_credentials": False,
            "live_orders_sent": False,
            "network_access_used": False,
            "real_credentials_read": False,
        },
        "results": [
            {
                "status": "PASS",
                "message": "oos generated",
                "reason_codes": [],
                "metrics": {
                    "trade_count": 8,
                    "total_net_pnl": 18.0,
                    "max_drawdown": 1.0,
                    "win_rate": 0.55,
                },
            },
            {
                "status": "PASS",
                "message": "walk-forward generated",
                "reason_codes": [],
                "metrics": {
                    "walk_forward": {
                        "walk_forward_window_count": 3,
                        "walk_forward_pass_count": 3,
                        "walk_forward_pass_rate": 1.0,
                    }
                },
            },
            {
                "status": "PASS",
                "message": "monte carlo generated",
                "reason_codes": [],
                "metrics": {
                    "trade_count": 8,
                    "total_net_pnl": 18.0,
                    "monte_carlo_survival_rate": 0.95,
                    "monte_carlo_run_pass_count": 19,
                    "monte_carlo_run_fail_count": 1,
                },
            },
        ],
        "aggregate_source_report_path": "/tmp/source-report.json",
        "source_report_paths": ["/tmp/source-report.json"],
        "artifact_paths": artifact_paths,
        "generated_artifact_count": len(artifact_paths),
        "validator_status": "PASS",
        "h_opt_005_ready": status == "PASS",
        "h_opt_005_blockers": [],
    }


def fake_walk_forward_failure_report(*, walk_forward_pass_rate):
    return {
        "status": "SKIPPED",
        "success": False,
        "message": "walk-forward gate failed",
        "reason_codes": ["walk_forward_pass_rate_below_threshold"],
        "safety_flags": {
            "analytics_modified_live_state": False,
            "broker_called": False,
            "contains_real_credentials": False,
            "live_orders_sent": False,
            "network_access_used": False,
            "real_credentials_read": False,
        },
        "results": [
            {
                "status": "PASS",
                "message": "oos generated",
                "reason_codes": [],
                "metrics": {
                    "trade_count": 8,
                    "total_net_pnl": 18.0,
                    "max_drawdown": 1.0,
                    "win_rate": 0.55,
                },
            },
            {
                "status": "SKIPPED",
                "message": "walk-forward pass rate is below threshold",
                "reason_codes": ["walk_forward_pass_rate_below_threshold"],
                "metrics": {
                    "walk_forward": {
                        "walk_forward_window_count": 3,
                        "walk_forward_pass_count": int(walk_forward_pass_rate * 3),
                        "walk_forward_pass_rate": walk_forward_pass_rate,
                    }
                },
            },
            {
                "status": "PASS",
                "message": "monte carlo generated",
                "reason_codes": [],
                "metrics": {
                    "trade_count": 8,
                    "total_net_pnl": 18.0,
                    "monte_carlo_survival_rate": 0.95,
                    "monte_carlo_run_pass_count": 19,
                    "monte_carlo_run_fail_count": 1,
                },
            },
        ],
        "aggregate_source_report_path": None,
        "source_report_paths": [],
        "artifact_paths": [],
        "generated_artifact_count": 0,
        "validator_status": None,
        "h_opt_005_ready": False,
        "h_opt_005_blockers": ["walk_forward_pass_rate_below_threshold"],
    }


def test_staged_search_records_phases_and_only_confirms_publish_artifacts(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    calls = []

    def fake_generation(**kwargs):
        calls.append(kwargs)
        return fake_aggregate_report(
            artifact_paths=[
                str(Path(kwargs["artifact_dir"]) / f"artifact-{len(calls)}.json")
            ]
        )

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=6,
        top_k=2,
        phases=["coarse", "fine", "confirm"],
        train_bars_values=[30, 40],
        test_bars_values=[10, 20],
        step_bars_values=[10],
        holdout_ratios=[0.2, 0.3],
        min_trade_counts=[5],
        monte_carlo_run_count=20,
        timestamp=1777827600,
    )

    assert report["status"] == "PASS"
    assert report["staged_search_task_scope"] == "H-OPT-019"
    assert report["phase_order"] == ["coarse", "fine", "confirm"]
    assert report["phase_budgets"] == {"coarse": 2, "fine": 2, "confirm": 2}
    assert report["search_parameters"]["phase_selection_contract"] == {
        "coarse": "wide_grid_screen",
        "fine": "score_and_walk_forward_ranked_neighbor_expansion",
        "confirm": "promoted_candidate_independent_recheck_with_walk_forward_rescue",
    }
    assert report["phase_selection_contract"] == report["search_parameters"][
        "phase_selection_contract"
    ]
    assert report["search_parameters"]["promotion_feedback_contract"] == {
        "uses_walk_forward_failure_feedback": True,
        "does_not_lower_gate_thresholds": True,
        "preserves_strategy_timeframe_diversity": True,
        "diversity_bucket": "timeframe_then_strategy",
        "diversity_fill_policy": (
            "one_per_timeframe_then_one_per_strategy_timeframe_before_ranked_fill"
        ),
        "phase_trial_selection_policy": "round_robin_promoted_buckets_before_neighbor_fill",
        "avoids_completed_base_trial_repeats_when_alternatives_exist": True,
        "oos_mc_rescue_ranking_policy": "walk_forward_first_after_oos_mc_gate_pass",
        "uses_walk_forward_threshold_gap_bucket_priority": True,
        "deprioritizes_large_gap_oos_mc_failures": True,
        "uses_confirm_phase_rescue_memory_for_confirm_resume": True,
        "threshold_gap_selection_policy": (
            "near_miss_then_moderate_then_broad_walk_forward_before_large_gap_resume"
        ),
        "large_gap_selection_policy": (
            "prefer_broader_strategy_feature_candidates_before_large_gap_parameter_resume"
        ),
        "promotion_basis_sources": [
            "score_ranked_candidate",
            "walk_forward_rescue_candidate",
            "oos_mc_walk_forward_failure_recheck",
        ],
    }
    assert report["promotion_feedback_contract"] == report["search_parameters"][
        "promotion_feedback_contract"
    ]
    assert report["search_parameters"]["walk_forward_window_rescue_contract"] == {
        "uses_walk_forward_failure_distribution": True,
        "fine_prefers_independent_windows_after_walk_forward_failure": True,
        "confirm_prefers_independent_windows_after_walk_forward_failure": True,
        "confirm_prefers_dual_independent_rechecks_after_walk_forward_failure": True,
        "does_not_lower_gate_thresholds": True,
    }
    assert report["walk_forward_window_rescue_contract"] == report[
        "search_parameters"
    ]["walk_forward_window_rescue_contract"]
    assert report["search_parameters"]["walk_forward_window_profile_contract"] == {
        "emits_window_profile_failure_summary": True,
        "emits_window_profile_selection_summary": True,
        "uses_window_profile_failure_feedback": True,
        "avoids_repeating_failed_window_profiles": True,
        "expands_default_rescue_window_profiles": True,
        "records_repetition_when_profile_space_exhausted": True,
        "does_not_lower_gate_thresholds": True,
    }
    assert report["walk_forward_window_profile_contract"] == report[
        "search_parameters"
    ]["walk_forward_window_profile_contract"]
    assert report["search_parameters"]["walk_forward_rescue_plan_contract"] == {
        "emits_next_rescue_plan": True,
        "uses_oos_mc_walk_forward_failure_candidates": True,
        "uses_best_walk_forward_failure_candidates": True,
        "preserves_focus_candidate_diversity": True,
        "requires_independent_confirm_recheck": True,
        "does_not_lower_gate_thresholds": True,
    }
    assert report["walk_forward_rescue_plan_contract"] == report[
        "search_parameters"
    ]["walk_forward_rescue_plan_contract"]
    assert report["search_parameters"]["walk_forward_threshold_gap_contract"] == {
        "emits_oos_mc_walk_forward_threshold_gap_analysis": True,
        "uses_gap_buckets_for_next_rescue_budget": True,
        "uses_gap_buckets_for_oos_mc_rescue_selection": True,
        "distinguishes_near_miss_from_large_gap_candidates": True,
        "does_not_lower_gate_thresholds": True,
    }
    assert report["walk_forward_threshold_gap_contract"] == report[
        "search_parameters"
    ]["walk_forward_threshold_gap_contract"]
    assert report["search_parameters"]["walk_forward_parameter_rescue_contract"] == {
        "uses_same_strategy_timeframe_parameter_neighbors": True,
        "requires_independent_holdout_or_walk_forward_window": True,
        "prioritizes_parameter_neighbors_for_moderate_gap_candidates": True,
        "prefers_dual_independent_recheck_before_parameter_neighbor": True,
        "prefers_nearest_parameter_neighbors": True,
        "does_not_lower_gate_thresholds": True,
    }
    assert report["walk_forward_parameter_rescue_contract"] == report[
        "search_parameters"
    ]["walk_forward_parameter_rescue_contract"]
    assert report["search_parameters"]["confirm_recheck_contract"] == {
        "prefers_independent_holdout": True,
        "prefers_independent_walk_forward_window": True,
        "prefers_dual_independent_holdout_and_window": True,
        "profile_avoidance_precedes_repeated_dual_recheck": True,
        "uses_independent_monte_carlo_seed": True,
        "artifact_publication_allowed": True,
    }
    assert report["confirm_recheck_contract"] == report["search_parameters"][
        "confirm_recheck_contract"
    ]
    assert [entry["phase"] for entry in report["phase_summaries"]] == [
        "coarse",
        "fine",
        "confirm",
    ]
    assert [entry["selection_policy"] for entry in report["phase_summaries"]] == [
        "wide_grid_screen",
        "score_and_walk_forward_ranked_neighbor_expansion",
        "promoted_candidate_independent_recheck_with_walk_forward_rescue",
    ]
    assert report["completed_trial_count"] == 6
    assert report["pass_count"] == 2
    assert report["artifact_count"] == 2
    assert report["h_opt_005_ready"] is True
    assert report["h_opt_010_ready"] is True
    assert report["confirm_phase_rescue_memory_summary"][
        "uses_confirm_phase_rescue_memory"
    ] is True

    trials_by_phase = {
        phase: [
            trial
            for trial in report["all_trials"]
            if trial["phase"] == phase
        ]
        for phase in report["phase_order"]
    }
    assert all(
        "coarse" in trial["candidate_version"]
        for trial in trials_by_phase["coarse"]
    )
    assert all(
        "fine" in trial["candidate_version"]
        for trial in trials_by_phase["fine"]
    )
    assert all(
        "confirm" in trial["candidate_version"]
        for trial in trials_by_phase["confirm"]
    )
    assert all(
        trial["artifact_publication_allowed"] is False
        and trial["generated_artifact_count"] == 0
        and trial["screening_artifact_count"] == 1
        for trial in trials_by_phase["coarse"] + trials_by_phase["fine"]
    )
    assert all(
        trial["artifact_publication_allowed"] is True
        and trial["generated_artifact_count"] == 1
        for trial in trials_by_phase["confirm"]
    )
    assert all(
        trial["monte_carlo_seed_policy"] == "phase_screen_seed"
        for trial in trials_by_phase["coarse"] + trials_by_phase["fine"]
    )
    assert all(
        trial["monte_carlo_seed_policy"] == "independent_confirm_seed"
        for trial in trials_by_phase["confirm"]
    )
    assert all(
        isinstance(trial["promoted_candidate_version"], str)
        for trial in trials_by_phase["confirm"]
    )
    assert any(
        trial["independent_confirm_recheck"]["independent_holdout_ratio"] is True
        or trial["independent_confirm_recheck"]["independent_walk_forward_window"] is True
        for trial in trials_by_phase["confirm"]
    )
    assert all(
        trial["independent_confirm_recheck"]["independent_monte_carlo_seed"] is True
        for trial in trials_by_phase["confirm"]
    )
    confirm_phase_summary = report["phase_summaries"][-1]
    assert confirm_phase_summary["confirm_recheck_summary"][
        "independent_monte_carlo_seed_count"
    ] == 2
    assert (
        confirm_phase_summary["confirm_recheck_summary"][
            "dual_independent_holdout_window_count"
        ]
        >= 1
    )
    assert report["confirm_recheck_summary"] == confirm_phase_summary[
        "confirm_recheck_summary"
    ]
    assert "screening-artifacts" in str(calls[0]["artifact_dir"])
    assert "screening-artifacts" in str(calls[2]["artifact_dir"])
    assert Path(calls[-1]["artifact_dir"]).name == "published-artifacts"
    assert calls[0]["monte_carlo_seed"] == 42
    assert calls[2]["monte_carlo_seed"] == 43
    assert calls[-1]["monte_carlo_seed"] == 10044

    diagnostics = report["overfit_diagnostics"]
    assert diagnostics["trial_count"] == 6
    assert diagnostics["parameter_count"]["max"] == 2
    assert diagnostics["neighbor_stability"]["neighbor_count"] >= 1
    assert diagnostics["timeframe_consistency"]["passing_timeframe_count"] == 1
    assert diagnostics["seed_consistency"]["confirm_trial_count"] == 2
    assert report["search_parameters"]["candidate_version_contract"] == {
        "includes_phase": True,
        "includes_strategy": True,
        "includes_timeframe": True,
        "includes_data_fingerprint": True,
        "includes_parameter_hash": True,
    }


def test_staged_search_records_walk_forward_failure_analysis(monkeypatch, tmp_path):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    pass_rates = iter([0.0, 0.5])

    def fake_generation(**kwargs):
        return fake_walk_forward_failure_report(
            walk_forward_pass_rate=next(pass_rates)
        )

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=2,
        top_k=2,
        phases=["confirm"],
        train_bars_values=[30],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    analysis = report["walk_forward_failure_analysis"]
    assert report["status"] == "SKIPPED"
    assert report["pass_count"] == 0
    assert analysis == report["overfit_diagnostics"]["walk_forward_failure_analysis"]
    assert analysis["evidence_trial_count"] == 2
    assert analysis["below_threshold_count"] == 2
    assert analysis["below_threshold_ratio"] == 1.0
    assert analysis["oos_mc_pass_but_walk_forward_fail_count"] == 2
    assert analysis["pass_rate_summary"] == {
        "count": 2,
        "min": 0.0,
        "median": 0.25,
        "p90": 0.45,
        "max": 0.5,
    }
    assert analysis["by_phase"]["confirm"]["trial_count"] == 2
    assert analysis["by_timeframe"]["1m"]["below_threshold_count"] == 2
    assert analysis["by_strategy_id"]["ma_crossover"]["best_walk_forward_pass_rate"] == 0.5
    window_profiles = analysis["by_window_profile"]
    assert len(window_profiles) == 1
    window_profile = next(iter(window_profiles.values()))
    assert window_profile["window"] == {
        "train_bars": 30,
        "test_bars": 10,
        "step_bars": 10,
        "holdout_ratio": 0.2,
        "min_trade_count": 5,
    }
    assert window_profile["trial_count"] == 2
    assert window_profile["below_threshold_count"] == 2
    assert window_profile["all_trials_below_threshold"] is True
    assert analysis["failed_window_profile_keys"] == list(window_profiles)
    assert analysis["top_failure_reason_codes"][0] == {
        "reason_code": "walk_forward_pass_rate_below_threshold",
        "count": 2,
    }
    assert analysis["best_failed_candidate"]["metrics"]["walk_forward_pass_rate"] == 0.5
    assert analysis["diagnostic_reason_codes"] == [
        "all_walk_forward_trials_below_threshold",
        "walk_forward_gate_failed_despite_oos_and_monte_carlo",
    ]
    rescue_plan = report["walk_forward_rescue_plan"]
    gap_analysis = report["walk_forward_threshold_gap_analysis"]
    assert rescue_plan["task_scope"] == "H-OPT-019"
    assert rescue_plan["status"] == "RECOMMENDED"
    assert (
        rescue_plan["recommended_action"]
        == "rescue_oos_mc_candidates_with_independent_walk_forward_windows"
    )
    assert rescue_plan["candidate_pool_reason"] == "oos_mc_walk_forward_failures"
    assert rescue_plan["candidate_pool_count"] == 2
    assert rescue_plan["focus_candidate_count"] == 2
    assert rescue_plan["focus_candidates"][0]["metrics"]["walk_forward_pass_rate"] == 0.5
    assert rescue_plan["recommended_confirm_recheck_contract"] == {
        "prefer_independent_holdout_ratio": True,
        "prefer_independent_walk_forward_window": True,
        "prefer_dual_independent_holdout_and_window": True,
        "use_independent_monte_carlo_seed": True,
        "artifact_publication_phase": "confirm",
    }
    assert rescue_plan["recommended_parameter_neighbor_policy"] == {
        "same_strategy_timeframe_only": True,
        "prefer_nearest_parameter_neighbors": True,
        "requires_independent_holdout_or_walk_forward_window": True,
        "prefer_dual_independent_recheck_before_parameter_neighbor": True,
        "preserve_cross_timeframe_strategy_candidates": True,
        "avoid_failed_window_profiles": True,
    }
    assert rescue_plan["focus_candidate_diversity"] == {
        "bucket": "timeframe_then_strategy",
        "bucket_count": 1,
        "timeframe_count": 1,
        "buckets": [{"timeframe": "1m", "strategy_id": "ma_crossover"}],
    }
    assert gap_analysis == report["overfit_diagnostics"][
        "walk_forward_threshold_gap_analysis"
    ]
    assert gap_analysis["eligible_oos_mc_failure_count"] == 2
    assert gap_analysis["gap_distribution"] == {
        "count": 2,
        "min": 0.17,
        "median": 0.42,
        "p90": 0.62,
        "max": 0.67,
    }
    assert gap_analysis["gap_bucket_counts"] == {
        "near_miss": 0,
        "moderate_gap": 1,
        "large_gap": 1,
    }
    assert gap_analysis["best_gap_candidates"][0]["metrics"][
        "walk_forward_pass_rate"
    ] == 0.5
    assert gap_analysis["best_gap_candidates"][0][
        "walk_forward_threshold_gap_bucket"
    ] == "moderate_gap"
    assert gap_analysis["recommended_next_budget_policy"] == {
        "recommended_focus": "moderate_gap_parameter_neighbor_and_window_rescue",
        "prioritize_near_miss_candidates": False,
        "prioritize_parameter_neighbors": True,
        "avoid_mechanical_resume_only": True,
        "keep_confirm_gate_required": True,
    }
    assert gap_analysis["does_not_lower_gate_thresholds"] is True
    assert gap_analysis["reason_codes"] == [
        "large_walk_forward_gap_candidates_available",
        "moderate_walk_forward_gap_candidates_available",
    ]
    assert rescue_plan["threshold_gap_analysis"] == gap_analysis
    assert (
        rescue_plan["recommended_next_budget_policy"]
        == gap_analysis["recommended_next_budget_policy"]
    )
    assert rescue_plan["does_not_lower_gate_thresholds"] is True
    assert rescue_plan["failed_window_profile_keys"] == analysis[
        "failed_window_profile_keys"
    ]
    assert rescue_plan["reason_codes"] == [
        "walk_forward_gate_failed_despite_oos_and_monte_carlo",
        "walk_forward_rescue_plan_emitted",
    ]


def test_staged_search_promotes_walk_forward_rescue_candidates(monkeypatch, tmp_path):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    reports = [
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.1),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.1),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
    ]
    call_candidates = []

    def fake_generation(**kwargs):
        call_candidates.append(kwargs["candidate_version"])
        return reports[len(call_candidates) - 1]

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=4,
        top_k=1,
        phases=["coarse", "confirm"],
        train_bars_values=[30, 40],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    coarse_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "coarse"
    ]
    confirm_summary = report["phase_summaries"][-1]
    promoted_versions = confirm_summary["promotion_basis_candidate_versions"]

    assert len(coarse_trials) == 2
    assert coarse_trials[1]["metrics"]["walk_forward_pass_rate"] == 0.6
    assert promoted_versions == [coarse_trials[1]["candidate_version"]]
    assert report["search_parameters"]["promotion_feedback_contract"][
        "does_not_lower_gate_thresholds"
    ] is True
    assert report["walk_forward_failure_analysis"]["below_threshold_count"] == 4


def test_staged_search_ranks_oos_mc_rescue_by_walk_forward_before_pnl(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    reports = [
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.2),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
    ]
    reports[0]["results"][0]["metrics"]["total_net_pnl"] = 100.0
    reports[1]["results"][0]["metrics"]["total_net_pnl"] = 10.0
    call_candidates = []

    def fake_generation(**kwargs):
        call_candidates.append(kwargs["candidate_version"])
        return reports[len(call_candidates) - 1]

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=3,
        top_k=1,
        phases=["coarse", "confirm"],
        train_bars_values=[30, 40],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    coarse_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "coarse"
    ]
    confirm_summary = report["phase_summaries"][-1]

    assert coarse_trials[0]["metrics"]["oos_total_net_pnl"] == 100.0
    assert coarse_trials[1]["metrics"]["walk_forward_pass_rate"] == 0.6
    assert confirm_summary["promotion_basis_candidate_versions"] == [
        coarse_trials[1]["candidate_version"]
    ]
    assert report["promotion_feedback_contract"][
        "oos_mc_rescue_ranking_policy"
    ] == "walk_forward_first_after_oos_mc_gate_pass"


def test_staged_search_uses_threshold_gap_bucket_priority_for_rescue(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    reports = [
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.2),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.2),
    ]
    reports[0]["results"][0]["metrics"]["total_net_pnl"] = 100.0
    reports[1]["results"][0]["metrics"]["total_net_pnl"] = 10.0
    call_candidates = []

    def fake_generation(**kwargs):
        call_candidates.append(kwargs["candidate_version"])
        return reports[len(call_candidates) - 1]

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=3,
        top_k=1,
        phases=["coarse", "confirm"],
        train_bars_values=[30, 40],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    coarse_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "coarse"
    ]
    confirm_summary = report["phase_summaries"][-1]
    gap_analysis = report["walk_forward_threshold_gap_analysis"]

    assert coarse_trials[0]["metrics"]["oos_total_net_pnl"] == 100.0
    assert gap_analysis["gap_bucket_counts"] == {
        "near_miss": 1,
        "moderate_gap": 0,
        "large_gap": 2,
    }
    assert gap_analysis["eligible_oos_mc_failure_count"] == 3
    assert confirm_summary["promotion_basis_candidate_versions"] == [
        coarse_trials[1]["candidate_version"]
    ]
    assert gap_analysis["best_gap_candidates"][0]["candidate_version"] == (
        coarse_trials[1]["candidate_version"]
    )
    assert report["promotion_feedback_contract"][
        "uses_walk_forward_threshold_gap_bucket_priority"
    ] is True
    assert report["walk_forward_threshold_gap_contract"][
        "uses_gap_buckets_for_oos_mc_rescue_selection"
    ] is True


def test_staged_search_deprioritizes_large_gap_oos_mc_rescue_candidates(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    reports = [
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.2),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.55),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.25),
    ]
    reports[0]["results"][0]["metrics"]["total_net_pnl"] = 100.0
    reports[0]["results"][2]["metrics"]["monte_carlo_survival_rate"] = 1.0
    reports[1]["results"][0]["metrics"]["total_net_pnl"] = -1.0
    reports[1]["results"][2]["metrics"]["monte_carlo_survival_rate"] = 0.7
    reports[2]["results"][0]["metrics"]["total_net_pnl"] = 80.0
    reports[2]["results"][2]["metrics"]["monte_carlo_survival_rate"] = 1.0
    call_candidates = []

    def fake_generation(**kwargs):
        call_candidates.append(kwargs["candidate_version"])
        return reports[len(call_candidates) - 1]

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=3,
        top_k=1,
        phases=["coarse", "confirm"],
        train_bars_values=[30, 40],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    coarse_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "coarse"
    ]
    confirm_summary = report["phase_summaries"][-1]

    assert report["walk_forward_threshold_gap_analysis"]["gap_bucket_counts"] == {
        "near_miss": 0,
        "moderate_gap": 0,
        "large_gap": 2,
    }
    assert report["walk_forward_threshold_gap_analysis"][
        "eligible_oos_mc_failure_count"
    ] == 2
    assert confirm_summary["promotion_basis_candidate_versions"] == [
        coarse_trials[1]["candidate_version"]
    ]
    assert report["promotion_feedback_contract"][
        "deprioritizes_large_gap_oos_mc_failures"
    ] is True
    assert report["promotion_feedback_contract"]["large_gap_selection_policy"] == (
        "prefer_broader_strategy_feature_candidates_before_large_gap_parameter_resume"
    )


def test_confirm_rescue_prioritizes_parameter_neighbors_for_moderate_gap():
    promoted_trial = {
        "phase": "coarse",
        "candidate_version": "promoted",
        "timeframe": "1m",
        "strategy_id": "ma_crossover",
        "strategy_parameters": {"fast_window": 1, "slow_window": 8},
        "window": {
            "train_bars": 30,
            "test_bars": 10,
            "step_bars": 10,
            "holdout_ratio": 0.2,
            "min_trade_count": 5,
        },
        "metrics": {"walk_forward_pass_rate": 0.5},
    }
    same_parameter_dual_recheck = {
        "timeframe": "1m",
        "strategy_id": "ma_crossover",
        "strategy_parameters": {"fast_window": 1, "slow_window": 8},
        "window": {
            "train_bars": 40,
            "test_bars": 20,
            "step_bars": 10,
            "holdout_ratio": 0.3,
            "min_trade_count": 5,
        },
    }
    parameter_neighbor_holdout_recheck = {
        "timeframe": "1m",
        "strategy_id": "ma_crossover",
        "strategy_parameters": {"fast_window": 2, "slow_window": 8},
        "window": {
            "train_bars": 30,
            "test_bars": 10,
            "step_bars": 10,
            "holdout_ratio": 0.3,
            "min_trade_count": 5,
        },
    }

    moderate_gap_order = sorted(
        [same_parameter_dual_recheck, parameter_neighbor_holdout_recheck],
        key=lambda trial: search._confirm_recheck_rank(
            trial,
            promoted_trial,
            min_walk_forward_pass_rate=0.67,
        ),
    )

    assert moderate_gap_order[0]["strategy_parameters"] == {
        "fast_window": 2,
        "slow_window": 8,
    }

    near_miss_promoted = dict(promoted_trial)
    near_miss_promoted["metrics"] = {"walk_forward_pass_rate": 0.6}
    near_miss_order = sorted(
        [same_parameter_dual_recheck, parameter_neighbor_holdout_recheck],
        key=lambda trial: search._confirm_recheck_rank(
            trial,
            near_miss_promoted,
            min_walk_forward_pass_rate=0.67,
        ),
    )

    assert near_miss_order[0]["strategy_parameters"] == {
        "fast_window": 1,
        "slow_window": 8,
    }
    assert search._walk_forward_rescue_gap_bucket(
        promoted_trial,
        min_walk_forward_pass_rate=0.67,
    ) == "moderate_gap"
    assert search._walk_forward_rescue_gap_bucket(
        near_miss_promoted,
        min_walk_forward_pass_rate=0.67,
    ) == "near_miss"


def test_confirm_resume_uses_confirm_phase_rescue_memory(monkeypatch, tmp_path):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    data_fingerprint = {"sha256": "resume-history-data-fingerprint"}
    promoted_trial = {
        "phase": "confirm",
        "candidate_version": search._candidate_version(
            symbol="BTCUSDT",
            timeframe="1m",
            strategy_id="ma_crossover",
            data_fingerprint=data_fingerprint,
            strategy_parameters={"fast_window": 1, "slow_window": 8},
            window={
                "train_bars": 30,
                "test_bars": 10,
                "step_bars": 10,
                "holdout_ratio": 0.2,
                "min_trade_count": 5,
            },
            phase="confirm",
        ),
        "timeframe": "1m",
        "strategy_id": "ma_crossover",
        "strategy_parameters": {"fast_window": 1, "slow_window": 8},
        "window": {
            "train_bars": 30,
            "test_bars": 10,
            "step_bars": 10,
            "holdout_ratio": 0.2,
            "min_trade_count": 5,
        },
        "metrics": {
            "oos_total_net_pnl": 18.0,
            "walk_forward_window_count": 10,
            "walk_forward_pass_count": 5,
            "walk_forward_pass_rate": 0.5,
            "monte_carlo_survival_rate": 0.95,
        },
        "reason_codes": ["walk_forward_pass_rate_below_threshold"],
        "status": "SKIPPED",
        "artifact_publication_allowed": True,
    }
    resume_path = write_json(
        tmp_path / "resume.json",
        {"status": "SKIPPED", "all_trials": [promoted_trial]},
    )
    call_details = []

    def fake_generation(**kwargs):
        call_details.append(
            {
                "strategy_parameters": dict(kwargs["strategy_parameters"]),
                "holdout_ratio": kwargs["holdout_ratio"],
                "train_bars": kwargs["train_bars"],
                "test_bars": kwargs["test_bars"],
                "step_bars": kwargs["step_bars"],
            }
        )
        return fake_walk_forward_failure_report(walk_forward_pass_rate=0.5)

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        resume_from=resume_path,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=2,
        top_k=1,
        phases=["coarse", "confirm"],
        train_bars_values=[30, 40],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2, 0.3],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    confirm_call = call_details[1]
    confirm_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "confirm"
    ]
    recheck = confirm_trials[0]["independent_confirm_recheck"]
    rescue_memory = report["confirm_phase_rescue_memory_summary"]

    assert report["phase_summaries"][1]["promotion_basis_candidate_versions"] == [
        promoted_trial["candidate_version"]
    ]
    assert confirm_call["strategy_parameters"] == {"fast_window": 2, "slow_window": 8}
    assert confirm_call["holdout_ratio"] == 0.3
    assert confirm_call["train_bars"] == 40
    assert recheck["source_candidate_version"] == promoted_trial["candidate_version"]
    assert recheck["strategy_parameter_neighbor"] is True
    assert recheck["independent_holdout_ratio"] is True
    assert recheck["independent_walk_forward_window"] is True
    assert rescue_memory["confirm_oos_mc_walk_forward_failure_count"] == 2
    assert rescue_memory["gap_bucket_counts"]["moderate_gap"] == 2
    assert (
        rescue_memory["recommended_profile_focus"]
        == "reuse_best_moderate_gap_confirm_profiles_for_parameter_neighbor_rescue"
    )
    assert report["promotion_feedback_contract"][
        "uses_confirm_phase_rescue_memory_for_confirm_resume"
    ] is True


def test_staged_search_avoids_repeating_completed_base_trials_when_possible(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    reports = [
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
    ]
    call_base_keys = []

    def fake_generation(**kwargs):
        base_key = (
            kwargs["timeframe"],
            kwargs["strategy_id"],
            tuple(sorted(dict(kwargs["strategy_parameters"]).items())),
            kwargs["holdout_ratio"],
            kwargs["train_bars"],
            kwargs["test_bars"],
            kwargs["step_bars"],
        )
        call_base_keys.append(base_key)
        return reports[len(call_base_keys) - 1]

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=4,
        top_k=1,
        phases=["coarse", "confirm"],
        train_bars_values=[30],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2, 0.3],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    coarse_keys = {
        call_base_keys[0],
        call_base_keys[1],
    }
    confirm_keys = {
        call_base_keys[2],
        call_base_keys[3],
    }

    assert confirm_keys.isdisjoint(coarse_keys)
    assert report["promotion_feedback_contract"][
        "avoids_completed_base_trial_repeats_when_alternatives_exist"
    ] is True
    assert report["phase_summaries"][1]["base_trial_repeat_selection_summary"][
        "planned_repeated_base_trial_count"
    ] == 0
    assert report["base_trial_repeat_selection_summary"][
        "planned_repeated_base_trial_count"
    ] == 0
    assert report["base_trial_repeat_selection_summary"][
        "avoids_completed_base_trial_repeats"
    ] is True


def test_staged_search_preserves_strategy_timeframe_diversity_in_rescue(
    monkeypatch,
    tmp_path,
):
    one_minute_path = build_timeframe_file(
        tmp_path / "public" / "btcusdt-1m.json",
        timeframe="1m",
    )
    four_hour_path = build_timeframe_file(
        tmp_path / "public" / "btcusdt-4h.json",
        timeframe="4h",
    )
    matrix_path = write_json(
        tmp_path / "public" / "matrix.json",
        {
            "status": "PASS",
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "pass_timeframes": ["1m", "4h"],
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
            "timeframes": {
                "1m": {
                    "status": "PASS",
                    "bar_count": 180,
                    "output_path": str(one_minute_path),
                    "sha256": "declared-1m",
                    "reason_codes": [],
                    "quality_report": {"passed": True},
                },
                "4h": {
                    "status": "PASS",
                    "bar_count": 180,
                    "output_path": str(four_hour_path),
                    "sha256": "declared-4h",
                    "reason_codes": [],
                    "quality_report": {"passed": True},
                },
            },
        },
    )
    call_details = []

    def fake_generation(**kwargs):
        call_details.append(
            {
                "phase": "coarse" if len(call_details) < 4 else "confirm",
                "candidate_version": kwargs["candidate_version"],
                "timeframe": kwargs["timeframe"],
                "strategy_id": kwargs["strategy_id"],
            }
        )
        is_one_minute_ema = (
            kwargs["timeframe"] == "1m"
            and kwargs["strategy_id"] == "ema_trend_filter"
        )
        is_four_hour_ma = (
            kwargs["timeframe"] == "4h" and kwargs["strategy_id"] == "ma_crossover"
        )
        return fake_walk_forward_failure_report(
            walk_forward_pass_rate=(
                0.66 if is_one_minute_ema else 0.65 if is_four_hour_ma else 0.6
            )
        )

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m", "4h"],
        strategy_ids=["ema_trend_filter", "ma_crossover"],
        max_trials=8,
        top_k=2,
        phases=["coarse", "confirm"],
        train_bars_values=[30],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    confirm_summary = report["phase_summaries"][-1]
    promoted_trials = [
        trial
        for trial in report["all_trials"]
        if trial["candidate_version"]
        in set(confirm_summary["promotion_basis_candidate_versions"])
    ]
    promoted_buckets = {
        (trial["timeframe"], trial["strategy_id"]) for trial in promoted_trials
    }
    confirm_calls = call_details[4:]
    confirm_buckets = {
        (call["timeframe"], call["strategy_id"]) for call in confirm_calls
    }

    assert promoted_buckets == {
        ("1m", "ema_trend_filter"),
        ("4h", "ma_crossover"),
    }
    assert ("4h", "ma_crossover") in confirm_buckets
    assert len(confirm_buckets) == 2
    assert report["walk_forward_rescue_plan"]["focus_candidate_diversity"][
        "bucket_count"
    ] == 2
    assert report["promotion_feedback_contract"][
        "preserves_strategy_timeframe_diversity"
    ] is True


def test_staged_search_prefers_independent_windows_for_walk_forward_rescue(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    reports = [
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
    ]
    call_windows = []

    def fake_generation(**kwargs):
        call_windows.append(
            {
                "candidate_version": kwargs["candidate_version"],
                "holdout_ratio": kwargs["holdout_ratio"],
                "train_bars": kwargs["train_bars"],
                "test_bars": kwargs["test_bars"],
                "step_bars": kwargs["step_bars"],
            }
        )
        return reports[len(call_windows) - 1]

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=3,
        top_k=1,
        phases=["coarse", "fine", "confirm"],
        train_bars_values=[30, 40],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2, 0.3],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    coarse_window, fine_window, confirm_window = call_windows
    fine_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "fine"
    ]
    confirm_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "confirm"
    ]

    assert coarse_window["holdout_ratio"] == 0.2
    assert coarse_window["train_bars"] == 30
    assert fine_window["holdout_ratio"] == 0.3
    assert fine_window["train_bars"] == 40
    assert (
        confirm_window["holdout_ratio"],
        confirm_window["train_bars"],
    ) not in {
        (coarse_window["holdout_ratio"], coarse_window["train_bars"]),
        (fine_window["holdout_ratio"], fine_window["train_bars"]),
    }
    assert fine_trials[0]["window"]["holdout_ratio"] == 0.3
    assert fine_trials[0]["window"]["train_bars"] == 40
    assert confirm_trials[0]["independent_confirm_recheck"][
        "independent_holdout_ratio"
    ] is True
    assert (
        confirm_trials[0]["independent_confirm_recheck"][
            "independent_holdout_ratio"
        ]
        is True
        or confirm_trials[0]["independent_confirm_recheck"][
            "independent_walk_forward_window"
        ]
        is True
    )
    assert report["walk_forward_window_rescue_contract"][
        "does_not_lower_gate_thresholds"
    ] is True


def test_staged_search_confirm_rescue_tries_parameter_neighbors(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    reports = [
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
    ]
    call_details = []

    def fake_generation(**kwargs):
        call_details.append(
            {
                "candidate_version": kwargs["candidate_version"],
                "strategy_parameters": dict(kwargs["strategy_parameters"]),
                "holdout_ratio": kwargs["holdout_ratio"],
                "train_bars": kwargs["train_bars"],
                "test_bars": kwargs["test_bars"],
                "step_bars": kwargs["step_bars"],
            }
        )
        return reports[len(call_details) - 1]

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=2,
        top_k=1,
        phases=["coarse", "confirm"],
        train_bars_values=[30, 40],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2, 0.3],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    coarse_call, confirm_call = call_details
    confirm_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "confirm"
    ]

    assert coarse_call["strategy_parameters"] == {"fast_window": 1, "slow_window": 8}
    assert confirm_call["strategy_parameters"] == {"fast_window": 2, "slow_window": 8}
    assert confirm_call["holdout_ratio"] == 0.3
    assert confirm_call["train_bars"] == 40
    assert confirm_trials[0]["independent_confirm_recheck"][
        "strategy_parameter_neighbor"
    ] is True
    assert confirm_trials[0]["independent_confirm_recheck"][
        "parameter_distance"
    ] == {"changed_key_count": 1, "numeric_distance": 1.0}
    assert confirm_trials[0]["independent_confirm_recheck"][
        "independent_holdout_ratio"
    ] is True
    assert confirm_trials[0]["independent_confirm_recheck"][
        "independent_walk_forward_window"
    ] is True
    assert report["confirm_recheck_summary"]["strategy_parameter_neighbor_count"] == 1
    assert (
        report["confirm_recheck_summary"]["dual_independent_holdout_window_count"]
        == 1
    )
    assert report["walk_forward_parameter_rescue_contract"][
        "prefers_dual_independent_recheck_before_parameter_neighbor"
    ] is True
    assert report["confirm_recheck_contract"][
        "prefers_dual_independent_holdout_and_window"
    ] is True
    assert report["walk_forward_parameter_rescue_contract"][
        "does_not_lower_gate_thresholds"
    ] is True


def test_staged_search_confirm_rescue_prefers_nonfailed_dual_recheck_when_available(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    reports = [
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
    ]
    call_windows = []

    def fake_generation(**kwargs):
        call_windows.append(
            {
                "holdout_ratio": kwargs["holdout_ratio"],
                "train_bars": kwargs["train_bars"],
                "test_bars": kwargs["test_bars"],
                "step_bars": kwargs["step_bars"],
            }
        )
        return reports[len(call_windows) - 1]

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=2,
        top_k=1,
        phases=["coarse", "confirm"],
        train_bars_values=[30],
        test_bars_values=[10, 20],
        step_bars_values=[10],
        holdout_ratios=[0.2, 0.3],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    confirm_window = call_windows[1]
    confirm_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "confirm"
    ]
    recheck = confirm_trials[0]["independent_confirm_recheck"]

    assert confirm_window["holdout_ratio"] == 0.3
    assert confirm_window["test_bars"] == 20
    assert recheck["independent_holdout_ratio"] is True
    assert recheck["independent_walk_forward_window"] is True
    assert (
        report["confirm_recheck_summary"]["dual_independent_holdout_window_count"]
        == 1
    )
    assert report["phase_summaries"][1]["window_profile_selection_summary"][
        "planned_repeated_failed_window_profile_count"
    ] == 0
    assert report["walk_forward_window_profile_selection_summary"][
        "avoids_repeating_failed_window_profiles"
    ] is True
    assert report["confirm_recheck_contract"][
        "prefers_dual_independent_holdout_and_window"
    ] is True
    assert report["confirm_recheck_contract"][
        "profile_avoidance_precedes_repeated_dual_recheck"
    ] is True
    assert report["walk_forward_parameter_rescue_contract"][
        "prefers_dual_independent_recheck_before_parameter_neighbor"
    ] is True


def test_staged_search_rescue_avoids_repeating_failed_window_profiles(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    reports = [
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
        fake_walk_forward_failure_report(walk_forward_pass_rate=0.6),
    ]
    call_windows = []

    def fake_generation(**kwargs):
        call_windows.append(
            {
                "candidate_version": kwargs["candidate_version"],
                "holdout_ratio": kwargs["holdout_ratio"],
                "train_bars": kwargs["train_bars"],
                "test_bars": kwargs["test_bars"],
                "step_bars": kwargs["step_bars"],
            }
        )
        return reports[len(call_windows) - 1]

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=2,
        top_k=1,
        phases=["coarse", "confirm"],
        train_bars_values=[30, 40],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2, 0.3],
        min_trade_counts=[5],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    coarse_window, confirm_window = call_windows
    analysis = report["walk_forward_failure_analysis"]
    confirm_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "confirm"
    ]

    assert coarse_window["holdout_ratio"] == 0.2
    assert coarse_window["train_bars"] == 30
    assert confirm_window["holdout_ratio"] == 0.3
    assert confirm_window["train_bars"] == 40
    assert confirm_trials[0]["independent_confirm_recheck"][
        "independent_holdout_ratio"
    ] is True
    assert confirm_trials[0]["independent_confirm_recheck"][
        "independent_walk_forward_window"
    ] is True
    assert len(analysis["failed_window_profile_keys"]) == 2
    assert report["phase_summaries"][1]["window_profile_selection_summary"][
        "failed_window_profile_count_considered"
    ] == 1
    assert report["phase_summaries"][1]["window_profile_selection_summary"][
        "planned_repeated_failed_window_profile_count"
    ] == 0
    assert report["walk_forward_window_profile_selection_summary"][
        "task_scope"
    ] == "H-OPT-019"
    assert report["walk_forward_window_profile_selection_summary"][
        "planned_repeated_failed_window_profile_count"
    ] == 0
    assert report["walk_forward_window_profile_selection_summary"][
        "avoids_repeating_failed_window_profiles"
    ] is True
    assert report["walk_forward_window_profile_contract"][
        "avoids_repeating_failed_window_profiles"
    ] is True


def test_staged_search_default_window_grid_expands_rescue_profiles(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    call_windows = []

    def fake_generation(**kwargs):
        call_windows.append(
            {
                "phase": kwargs["candidate_version"].split("-")[3],
                "holdout_ratio": kwargs["holdout_ratio"],
                "train_bars": kwargs["train_bars"],
                "test_bars": kwargs["test_bars"],
                "step_bars": kwargs["step_bars"],
            }
        )
        return fake_walk_forward_failure_report(walk_forward_pass_rate=0.6)

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=4,
        top_k=1,
        phases=["coarse", "confirm"],
        min_walk_forward_pass_rate=0.67,
        min_monte_carlo_survival_rate=0.8,
        timestamp=1777827600,
    )

    coarse_windows = [
        window for window in call_windows if window["phase"] == "coarse"
    ]
    confirm_windows = [
        window for window in call_windows if window["phase"] == "confirm"
    ]
    coarse_profiles = {
        (
            window["holdout_ratio"],
            window["train_bars"],
            window["test_bars"],
            window["step_bars"],
        )
        for window in coarse_windows
    }
    confirm_profiles = {
        (
            window["holdout_ratio"],
            window["train_bars"],
            window["test_bars"],
            window["step_bars"],
        )
        for window in confirm_windows
    }

    assert report["search_parameters"]["train_bars_values"] == [240, 300, 450, 600, 900]
    assert report["search_parameters"]["test_bars_values"] == [80, 100, 150]
    assert report["search_parameters"]["step_bars_values"] == [80, 100]
    assert report["search_parameters"]["holdout_ratios"] == [0.15, 0.2, 0.25, 0.3, 0.35]
    assert report["window_grid_count"] == 150
    assert len(coarse_windows) == 2
    assert len(confirm_windows) == 2
    assert confirm_profiles.isdisjoint(coarse_profiles)
    assert report["phase_summaries"][1]["window_profile_selection_summary"][
        "planned_repeated_failed_window_profile_count"
    ] == 0
    assert report["walk_forward_window_profile_selection_summary"][
        "avoids_repeating_failed_window_profiles"
    ] is True
    assert report["walk_forward_window_profile_contract"][
        "expands_default_rescue_window_profiles"
    ] is True


def test_staged_search_default_strategy_pool_includes_new_breakout_candidates(tmp_path):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=None,
        latest_validator_output_path=None,
        max_trials=1,
        phases=["coarse"],
        timestamp=1777827600,
    )

    assert "keltner_breakout" in report["search_parameters"]["strategy_ids"]
    assert "volume_breakout" in report["search_parameters"]["strategy_ids"]
    assert "roc_momentum" in report["search_parameters"]["strategy_ids"]
    assert "stochastic_reversion" in report["search_parameters"]["strategy_ids"]
    assert "ema_pullback_reentry" in report["search_parameters"]["strategy_ids"]
    assert "atr_channel_reversion" in report["search_parameters"]["strategy_ids"]
    assert "gap_reversal" in report["search_parameters"]["strategy_ids"]
    assert "gap_continuation_breakout" in report["search_parameters"]["strategy_ids"]
    assert "liquidity_sweep_reversal" in report["search_parameters"]["strategy_ids"]
    assert "volatility_squeeze_breakout" in report["search_parameters"]["strategy_ids"]
    assert "range_compression_breakout" in report["search_parameters"]["strategy_ids"]
    assert "trend_pullback_breakout" in report["search_parameters"]["strategy_ids"]
    assert "chandelier_breakout" in report["search_parameters"]["strategy_ids"]
    assert "rolling_vwap_reversion" in report["search_parameters"]["strategy_ids"]
    assert report["all_trials"][0]["strategy_metadata"]["cross_symbol"] is True
    assert (
        report["all_trials"][0]["strategy_metadata"][
            "does_not_call_broker_risk_execution_or_portfolio"
        ]
        is True
    )
    assert report["strategy_parameter_grid_counts"]["keltner_breakout"] == 54
    assert report["strategy_parameter_grid_counts"]["volume_breakout"] == 108
    assert report["strategy_parameter_grid_counts"]["roc_momentum"] == 216
    assert report["strategy_parameter_grid_counts"]["stochastic_reversion"] == 144
    assert report["strategy_parameter_grid_counts"]["ema_pullback_reentry"] == 288
    assert report["strategy_parameter_grid_counts"]["atr_channel_reversion"] == 216
    assert report["strategy_parameter_grid_counts"]["gap_reversal"] == 432
    assert report["strategy_parameter_grid_counts"]["gap_continuation_breakout"] == 1296
    assert report["strategy_parameter_grid_counts"]["liquidity_sweep_reversal"] == 1728
    assert report["strategy_parameter_grid_counts"]["volatility_squeeze_breakout"] == 1152
    assert report["strategy_parameter_grid_counts"]["range_compression_breakout"] == 2592
    assert report["strategy_parameter_grid_counts"]["trend_pullback_breakout"] == 10368
    assert report["strategy_parameter_grid_counts"]["chandelier_breakout"] == 3888
    assert report["strategy_parameter_grid_counts"]["rolling_vwap_reversion"] == 1296
    assert report["total_base_candidate_count"] > 22400


def test_staged_search_coarse_phase_samples_each_default_strategy(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")

    def fake_generation(**kwargs):
        return fake_walk_forward_failure_report(walk_forward_pass_rate=0.0)

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=tmp_path / "expanded-search-latest.json",
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "published-artifacts",
        latest_validator_output_path=None,
        timeframes=["1m"],
        max_trials=len(search.DEFAULT_STRATEGY_IDS) * 2,
        phases=["coarse"],
        train_bars_values=[30],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        monte_carlo_run_count=20,
        timestamp=1777827600,
    )

    coarse_trials = [
        trial for trial in report["all_trials"] if trial["phase"] == "coarse"
    ]

    assert report["phase_order"] == ["coarse", "confirm"]
    assert report["phase_budgets"]["coarse"] == len(search.DEFAULT_STRATEGY_IDS)
    assert report["coarse_sampling_contract"] == {
        "uses_strategy_timeframe_diversified_sampling": True,
        "covers_each_strategy_before_ranked_fill_when_budget_allows": True,
        "covers_each_timeframe_before_ranked_fill_when_budget_allows": True,
        "uses_strategy_timeframe_round_robin_fill": True,
        "does_not_lower_gate_thresholds": True,
    }
    assert [trial["strategy_id"] for trial in coarse_trials] == list(
        search.DEFAULT_STRATEGY_IDS
    )
    assert "atr_channel_reversion" in {
        trial["strategy_id"] for trial in coarse_trials
    }
    assert "gap_reversal" in {
        trial["strategy_id"] for trial in coarse_trials
    }
    assert "gap_continuation_breakout" in {
        trial["strategy_id"] for trial in coarse_trials
    }
    assert "liquidity_sweep_reversal" in {
        trial["strategy_id"] for trial in coarse_trials
    }
    assert "volatility_squeeze_breakout" in {
        trial["strategy_id"] for trial in coarse_trials
    }
    assert "trend_pullback_breakout" in {
        trial["strategy_id"] for trial in coarse_trials
    }
    assert "chandelier_breakout" in {
        trial["strategy_id"] for trial in coarse_trials
    }
    assert "rolling_vwap_reversion" in {
        trial["strategy_id"] for trial in coarse_trials
    }
    assert all(trial["artifact_publication_allowed"] is False for trial in coarse_trials)


def test_staged_search_keeps_confirm_phase_when_user_omits_it(tmp_path):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=None,
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=0,
        phases=["coarse"],
        timestamp=1777827600,
    )

    assert report["status"] == "SKIPPED"
    assert report["search_parameters"]["phases"] == ["coarse", "confirm"]
    assert report["reason_codes"] == ["max_trials_not_positive"]
