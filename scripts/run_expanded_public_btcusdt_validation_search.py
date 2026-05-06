#!/usr/bin/env python
import argparse
import hashlib
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.optimization.artifact_generation import (
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_SOURCE_REPORT_DIR,
)
from quant.optimization.candidate_strategies import (
    SUPPORTED_CANDIDATE_STRATEGY_IDS,
    candidate_strategy_metadata,
    normalize_candidate_strategy_parameters,
)
from scripts.generate_strategy_validation_source_reports import (
    run_strategy_validation_source_report_generation,
)
from scripts.validate_strategy_validation_artifacts import (
    DEFAULT_OUTPUT_PATH as DEFAULT_VALIDATOR_OUTPUT_PATH,
    run_strategy_validation_artifacts_validation,
)


DEFAULT_MATRIX_PATH = (
    PROJECT_ROOT / "logs" / "public-market-data" / "btcusdt-mtf-10k-latest.json"
)
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT
    / "logs"
    / "strategy-validation-artifacts"
    / "expanded-public-btcusdt-search-latest.json"
)
DEFAULT_TRIAL_OUTPUT_DIR = (
    PROJECT_ROOT
    / "logs"
    / "strategy-validation-artifacts"
    / "expanded-public-btcusdt-search"
)
DEFAULT_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
DEFAULT_STRATEGY_IDS = (
    "ma_crossover",
    "ema_trend_filter",
    "keltner_breakout",
    "volume_breakout",
    "macd_momentum",
    "roc_momentum",
    "stochastic_reversion",
    "ema_pullback_reentry",
    "atr_channel_reversion",
    "gap_reversal",
    "gap_continuation_breakout",
    "liquidity_sweep_reversal",
    "volatility_squeeze_breakout",
    "range_compression_breakout",
    "trend_pullback_breakout",
    "chandelier_breakout",
    "rolling_vwap_reversion",
    "donchian_breakout",
    "rsi_mean_reversion",
    "bollinger_reversion",
)
DEFAULT_PHASES = ("confirm",)
SUPPORTED_PHASES = ("coarse", "fine", "confirm")
PHASE_ROLES = {
    "coarse": "train_screen",
    "fine": "validation_screen",
    "confirm": "independent_confirm",
}
DEFAULT_TRAIN_BARS_VALUES = (240, 300, 450, 600, 900)
DEFAULT_TEST_BARS_VALUES = (80, 100, 150)
DEFAULT_STEP_BARS_VALUES = (80, 100)
DEFAULT_HOLDOUT_RATIOS = (0.15, 0.2, 0.25, 0.3, 0.35)
DEFAULT_MIN_TRADE_COUNTS = (10,)


def run_expanded_public_btcusdt_validation_search(
    *,
    matrix_path: str | Path = DEFAULT_MATRIX_PATH,
    output_path: str | Path | None = DEFAULT_OUTPUT_PATH,
    trial_output_dir: str | Path = DEFAULT_TRIAL_OUTPUT_DIR,
    source_report_dir: str | Path = DEFAULT_SOURCE_REPORT_DIR,
    artifact_dir: str | Path = DEFAULT_ARTIFACT_DIR,
    latest_validator_output_path: str | Path | None = DEFAULT_VALIDATOR_OUTPUT_PATH,
    symbol: str = "BTCUSDT",
    timeframes: list[str] | None = None,
    strategy_ids: list[str] | None = None,
    max_trials: int = 500,
    max_runtime_seconds: float | None = None,
    resume_from: str | Path | None = None,
    top_k: int = 20,
    stop_on_first_pass: bool = False,
    phases: list[str] | None = None,
    train_bars_values: list[int] | None = None,
    test_bars_values: list[int] | None = None,
    step_bars_values: list[int] | None = None,
    holdout_ratios: list[float] | None = None,
    min_trade_counts: list[int] | None = None,
    min_train_bars: int = 40,
    min_holdout_bars: int = 20,
    min_walk_forward_windows: int = 3,
    min_walk_forward_pass_rate: float = 0.67,
    monte_carlo_run_count: int = 500,
    monte_carlo_seed: int = 42,
    min_monte_carlo_trades: int | None = None,
    min_monte_carlo_survival_rate: float = 0.8,
    monte_carlo_max_drawdown_limit: float | None = None,
    initial_balance: float = 10000.0,
    require_gate_pass: bool = True,
    min_net_pnl: float = 0.0,
    max_drawdown: float | None = None,
    min_win_rate: float | None = None,
    min_out_of_sample_net_pnl: float = 0.0,
    overwrite_existing_source_reports: bool = False,
    overwrite_existing_artifacts: bool = False,
    timestamp: int | None = None,
) -> dict[str, Any]:
    generated_at = int(time.time()) if timestamp is None else timestamp
    started_monotonic = time.monotonic()
    matrix_path = Path(matrix_path)
    output_path = Path(output_path) if output_path is not None else None
    trial_output_dir = Path(trial_output_dir)
    source_report_dir = Path(source_report_dir)
    artifact_dir = Path(artifact_dir)
    latest_validator_output_path = (
        Path(latest_validator_output_path)
        if latest_validator_output_path is not None
        else None
    )
    matrix_fingerprint = _file_fingerprint(matrix_path)

    normalized_strategy_ids = _normalize_strategy_ids(strategy_ids)
    normalized_timeframes = _normalize_timeframes(timeframes)
    normalized_phases = _normalize_phases(phases)
    train_bars_values = train_bars_values or list(DEFAULT_TRAIN_BARS_VALUES)
    test_bars_values = test_bars_values or list(DEFAULT_TEST_BARS_VALUES)
    step_bars_values = step_bars_values or list(DEFAULT_STEP_BARS_VALUES)
    holdout_ratios = holdout_ratios or list(DEFAULT_HOLDOUT_RATIOS)
    min_trade_counts = min_trade_counts or list(DEFAULT_MIN_TRADE_COUNTS)

    search_parameters = {
        "timeframes": list(normalized_timeframes),
        "strategy_ids": list(normalized_strategy_ids),
        "train_bars_values": list(train_bars_values),
        "test_bars_values": list(test_bars_values),
        "step_bars_values": list(step_bars_values),
        "holdout_ratios": list(holdout_ratios),
        "min_trade_counts": list(min_trade_counts),
        "min_train_bars": min_train_bars,
        "min_holdout_bars": min_holdout_bars,
        "min_walk_forward_windows": min_walk_forward_windows,
        "min_walk_forward_pass_rate": min_walk_forward_pass_rate,
        "monte_carlo_run_count": monte_carlo_run_count,
        "monte_carlo_seed": monte_carlo_seed,
        "min_monte_carlo_trades": min_monte_carlo_trades,
        "min_monte_carlo_survival_rate": min_monte_carlo_survival_rate,
        "monte_carlo_max_drawdown_limit": monte_carlo_max_drawdown_limit,
        "require_gate_pass": require_gate_pass,
        "min_net_pnl": min_net_pnl,
        "max_drawdown": max_drawdown,
        "min_win_rate": min_win_rate,
        "min_out_of_sample_net_pnl": min_out_of_sample_net_pnl,
        "overwrite_existing_source_reports": overwrite_existing_source_reports,
        "overwrite_existing_artifacts": overwrite_existing_artifacts,
        "stop_on_first_pass": stop_on_first_pass,
        "top_k": top_k,
        "max_runtime_seconds": max_runtime_seconds,
        "resume_from": str(resume_from) if resume_from is not None else None,
        "phases": list(normalized_phases),
        "phase_roles": {
            phase: PHASE_ROLES[phase] for phase in normalized_phases
        },
        "artifact_publication_phase": "confirm",
        "phase_selection_contract": {
            "coarse": "wide_grid_screen",
            "fine": "score_and_walk_forward_ranked_neighbor_expansion",
            "confirm": "promoted_candidate_independent_recheck_with_walk_forward_rescue",
        },
        "coarse_sampling_contract": {
            "uses_strategy_timeframe_diversified_sampling": True,
            "covers_each_strategy_before_ranked_fill_when_budget_allows": True,
            "covers_each_timeframe_before_ranked_fill_when_budget_allows": True,
            "uses_strategy_timeframe_round_robin_fill": True,
            "does_not_lower_gate_thresholds": True,
        },
        "promotion_feedback_contract": {
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
        },
        "walk_forward_window_rescue_contract": {
            "uses_walk_forward_failure_distribution": True,
            "fine_prefers_independent_windows_after_walk_forward_failure": True,
            "confirm_prefers_independent_windows_after_walk_forward_failure": True,
            "confirm_prefers_dual_independent_rechecks_after_walk_forward_failure": True,
            "does_not_lower_gate_thresholds": True,
        },
        "walk_forward_window_profile_contract": {
            "emits_window_profile_failure_summary": True,
            "emits_window_profile_selection_summary": True,
            "uses_window_profile_failure_feedback": True,
            "avoids_repeating_failed_window_profiles": True,
            "expands_default_rescue_window_profiles": True,
            "records_repetition_when_profile_space_exhausted": True,
            "does_not_lower_gate_thresholds": True,
        },
        "walk_forward_rescue_plan_contract": {
            "emits_next_rescue_plan": True,
            "uses_oos_mc_walk_forward_failure_candidates": True,
            "uses_best_walk_forward_failure_candidates": True,
            "preserves_focus_candidate_diversity": True,
            "requires_independent_confirm_recheck": True,
            "does_not_lower_gate_thresholds": True,
        },
        "walk_forward_threshold_gap_contract": {
            "emits_oos_mc_walk_forward_threshold_gap_analysis": True,
            "uses_gap_buckets_for_next_rescue_budget": True,
            "uses_gap_buckets_for_oos_mc_rescue_selection": True,
            "distinguishes_near_miss_from_large_gap_candidates": True,
            "does_not_lower_gate_thresholds": True,
        },
        "walk_forward_parameter_rescue_contract": {
            "uses_same_strategy_timeframe_parameter_neighbors": True,
            "requires_independent_holdout_or_walk_forward_window": True,
            "prioritizes_parameter_neighbors_for_moderate_gap_candidates": True,
            "prefers_dual_independent_recheck_before_parameter_neighbor": True,
            "prefers_nearest_parameter_neighbors": True,
            "does_not_lower_gate_thresholds": True,
        },
        "confirm_recheck_contract": {
            "prefers_independent_holdout": True,
            "prefers_independent_walk_forward_window": True,
            "prefers_dual_independent_holdout_and_window": True,
            "profile_avoidance_precedes_repeated_dual_recheck": True,
            "uses_independent_monte_carlo_seed": True,
            "artifact_publication_allowed": True,
        },
        "candidate_version_contract": {
            "includes_phase": True,
            "includes_strategy": True,
            "includes_timeframe": True,
            "includes_data_fingerprint": True,
            "includes_parameter_hash": True,
        },
    }

    if max_trials <= 0:
        report = _base_report(
            generated_at=generated_at,
            matrix_path=matrix_path,
            matrix_fingerprint=matrix_fingerprint,
            symbol=symbol,
            search_parameters=search_parameters,
            status="SKIPPED",
            message="max_trials must be positive",
            reason_codes=["max_trials_not_positive"],
        )
        return _write_report(report, output_path)

    if top_k <= 0:
        report = _base_report(
            generated_at=generated_at,
            matrix_path=matrix_path,
            matrix_fingerprint=matrix_fingerprint,
            symbol=symbol,
            search_parameters=search_parameters,
            status="SKIPPED",
            message="top_k must be positive",
            reason_codes=["top_k_not_positive"],
        )
        return _write_report(report, output_path)

    matrix_payload, source_inputs, skipped_timeframes, matrix_reason_codes = (
        _load_matrix_inputs(matrix_path, normalized_timeframes)
    )
    if not matrix_payload:
        report = _base_report(
            generated_at=generated_at,
            matrix_path=matrix_path,
            matrix_fingerprint=matrix_fingerprint,
            symbol=symbol,
            search_parameters=search_parameters,
            status="SKIPPED",
            message="public BTCUSDT multi-timeframe matrix input is missing or invalid",
            reason_codes=matrix_reason_codes or ["matrix_input_missing"],
        )
        report.update(
            {
                "skipped_timeframes": skipped_timeframes,
                "data_fingerprints": {},
                "source_inputs": [],
                "planned_trial_count": 0,
                "completed_trial_count": 0,
                "executed_trial_count": 0,
                "resumed_trial_count": 0,
                "pass_count": 0,
                "top_candidates": [],
                "all_trials": [],
                "h_opt_005_ready": False,
                "h_opt_010_ready": False,
                "h_opt_005_blockers": matrix_reason_codes or ["matrix_input_missing"],
            }
        )
        return _write_report(report, output_path)

    data_fingerprints = {
        item["timeframe"]: item["data_fingerprint"] for item in source_inputs
    }
    source_input_summaries = [
        {
            "timeframe": item["timeframe"],
            "input_path": str(item["input_path"]),
            "bar_count": item.get("bar_count"),
            "data_fingerprint": item["data_fingerprint"],
        }
        for item in source_inputs
    ]
    if not source_inputs:
        report = _base_report(
            generated_at=generated_at,
            matrix_path=matrix_path,
            matrix_fingerprint=matrix_fingerprint,
            symbol=symbol,
            search_parameters=search_parameters,
            status="SKIPPED",
            message="no PASS timeframe inputs were available for expanded search",
            reason_codes=matrix_reason_codes or ["no_pass_timeframe_inputs"],
        )
        report.update(
            {
                "matrix_status": matrix_payload.get("status"),
                "skipped_timeframes": skipped_timeframes,
                "data_fingerprints": data_fingerprints,
                "source_inputs": source_input_summaries,
                "planned_trial_count": 0,
                "completed_trial_count": 0,
                "executed_trial_count": 0,
                "resumed_trial_count": 0,
                "pass_count": 0,
                "top_candidates": [],
                "all_trials": [],
                "h_opt_005_ready": False,
                "h_opt_010_ready": False,
                "h_opt_005_blockers": matrix_reason_codes
                or ["no_pass_timeframe_inputs"],
            }
        )
        return _write_report(report, output_path)

    window_grid = _build_window_grid(
        train_bars_values=train_bars_values,
        test_bars_values=test_bars_values,
        step_bars_values=step_bars_values,
        holdout_ratios=holdout_ratios,
        min_trade_counts=min_trade_counts,
    )
    trial_plan, invalid_trials = _build_trial_plan(
        source_inputs=source_inputs,
        strategy_ids=normalized_strategy_ids,
        window_grid=window_grid,
    )
    total_base_candidate_count = len(trial_plan)
    phase_budgets = _phase_budgets(normalized_phases, max_trials)
    resumed_trials_by_version = _load_resumed_trials(resume_from)
    resume_history_trials = list(resumed_trials_by_version.values())
    completed_trials: list[dict[str, Any]] = []
    planned_trial_count = 0
    executed_trial_count = 0
    stopped_reason: str | None = None
    phase_summaries: list[dict[str, Any]] = []

    for phase_index, phase in enumerate(normalized_phases):
        if stopped_reason:
            break
        budget = phase_budgets.get(phase, 0)
        phase_failed_window_profile_keys = _walk_forward_failed_window_profile_keys(
            completed_trials,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        )
        if budget <= 0:
            phase_summaries.append(
                _phase_summary(
                    phase=phase,
                    planned_trials=[],
                    completed_trials=[],
                    budget=budget,
                    promotion_basis=[],
                    failed_window_profile_keys=phase_failed_window_profile_keys,
                    completed_base_trial_keys=set(),
                    stopped_reason="phase_budget_empty",
                )
            )
            continue
        phase_completed_base_trial_keys = {
            _base_trial_key(trial) for trial in completed_trials
        }
        promotion_basis = _phase_promotion_basis(
            phase=phase,
            completed_trials=completed_trials,
            resume_history_trials=resume_history_trials,
            top_k=top_k,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        )
        phase_trials = _select_phase_trials(
            phase=phase,
            base_trials=trial_plan,
            completed_trials=completed_trials,
            budget=budget,
            promotion_basis=promotion_basis,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            failed_window_profile_keys=phase_failed_window_profile_keys,
        )
        planned_trial_count += len(phase_trials)
        phase_completed: list[dict[str, Any]] = []
        for base_trial in phase_trials:
            trial = _phase_trial(
                base_trial=base_trial,
                phase=phase,
                symbol=symbol,
            )
            trial_monte_carlo_seed = _phase_monte_carlo_seed(
                base_seed=monte_carlo_seed,
                phase=phase,
                phase_index=phase_index,
            )
            trial["monte_carlo_seed"] = trial_monte_carlo_seed
            trial["monte_carlo_seed_policy"] = _phase_monte_carlo_seed_policy(phase)
            if isinstance(trial.get("independent_confirm_recheck"), dict):
                trial["independent_confirm_recheck"].update(
                    {
                        "monte_carlo_seed": trial_monte_carlo_seed,
                        "independent_monte_carlo_seed": True,
                    }
                )
            resumed_trial = _resumed_trial_for_phase(
                trial=trial,
                resumed_trials_by_version=resumed_trials_by_version,
            )
            if isinstance(resumed_trial, dict):
                completed_trials.append(resumed_trial)
                phase_completed.append(resumed_trial)
                if (
                    stop_on_first_pass
                    and resumed_trial.get("status") == "PASS"
                    and resumed_trial.get("artifact_publication_allowed") is True
                    and resumed_trial.get("phase") == "confirm"
                ):
                    stopped_reason = "resume_contains_passing_candidate"
                    break
                continue

            if _runtime_exhausted(started_monotonic, max_runtime_seconds):
                stopped_reason = "max_runtime_seconds_reached"
                break

            trial_summary = _run_trial(
                index=len(completed_trials) + 1,
                generated_at=generated_at,
                trial=trial,
                trial_output_dir=trial_output_dir,
                source_report_dir=source_report_dir,
                artifact_dir=artifact_dir,
                min_train_bars=min_train_bars,
                min_holdout_bars=min_holdout_bars,
                min_walk_forward_windows=min_walk_forward_windows,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                monte_carlo_run_count=monte_carlo_run_count,
                monte_carlo_seed=trial_monte_carlo_seed,
                min_monte_carlo_trades=min_monte_carlo_trades,
                min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
                monte_carlo_max_drawdown_limit=monte_carlo_max_drawdown_limit,
                initial_balance=initial_balance,
                require_gate_pass=require_gate_pass,
                min_net_pnl=min_net_pnl,
                max_drawdown=max_drawdown,
                min_win_rate=min_win_rate,
                min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
                overwrite_existing_source_reports=overwrite_existing_source_reports,
                overwrite_existing_artifacts=overwrite_existing_artifacts,
            )
            executed_trial_count += 1
            completed_trials.append(trial_summary)
            phase_completed.append(trial_summary)
            if (
                stop_on_first_pass
                and trial_summary.get("status") == "PASS"
                and trial_summary.get("artifact_publication_allowed") is True
                and trial_summary.get("phase") == "confirm"
            ):
                stopped_reason = "first_passing_candidate_found"
                break
        phase_summaries.append(
            _phase_summary(
                phase=phase,
                planned_trials=phase_trials,
                completed_trials=phase_completed,
                budget=budget,
                promotion_basis=promotion_basis,
                failed_window_profile_keys=phase_failed_window_profile_keys,
                completed_base_trial_keys=phase_completed_base_trial_keys,
                stopped_reason=stopped_reason,
            )
        )
        if stopped_reason:
            break

    for trial_index, trial in enumerate(completed_trials, start=1):
        trial["trial_index"] = trial_index

    passing_candidates = [
        trial
        for trial in completed_trials
        if trial.get("status") == "PASS"
        and trial.get("phase") == "confirm"
        and trial.get("artifact_publication_allowed") is True
    ]
    latest_validation_report = None
    if passing_candidates and latest_validator_output_path is not None:
        artifact_paths = [
            path
            for trial in passing_candidates
            for path in trial.get("artifact_paths", [])
            if path
        ]
        if artifact_paths:
            latest_validation_report = run_strategy_validation_artifacts_validation(
                artifact_dir=None,
                artifact_paths=artifact_paths,
                output_path=latest_validator_output_path,
                timestamp=generated_at,
                require_gate_pass=require_gate_pass,
                min_trades=min(min_trade_counts),
                min_net_pnl=min_net_pnl,
                max_drawdown=max_drawdown,
                min_win_rate=min_win_rate,
                min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
                min_walk_forward_windows=min_walk_forward_windows,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
            )

    sorted_candidates = sorted(completed_trials, key=_trial_score, reverse=True)
    top_candidates = sorted_candidates[:top_k]
    best_candidate = top_candidates[0] if top_candidates else None
    reason_codes = _collect_reason_codes(completed_trials)
    reason_codes.extend(matrix_reason_codes)
    if invalid_trials:
        reason_codes.append("invalid_strategy_parameter_trials_skipped")
    if stopped_reason:
        reason_codes.append(stopped_reason)
    if not passing_candidates:
        reason_codes.append("no_expanded_public_btcusdt_candidate_passed")

    h_opt_005_blockers = []
    if not passing_candidates:
        if best_candidate:
            h_opt_005_blockers.extend(best_candidate.get("h_opt_005_blockers") or [])
        if stopped_reason:
            h_opt_005_blockers.append(stopped_reason)
        if not h_opt_005_blockers:
            h_opt_005_blockers.append("no_expanded_public_btcusdt_candidate_passed")

    status = "PASS" if passing_candidates else "SKIPPED"
    message = (
        "expanded public BTCUSDT search found a candidate that passed OOS/WF/MC gates"
        if passing_candidates
        else "expanded public BTCUSDT search completed without a passing candidate"
    )
    overfit_diagnostics = _overfit_diagnostics(
        trials=completed_trials,
        top_candidates=top_candidates,
        top_k=top_k,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
    )
    walk_forward_threshold_gap_analysis = _walk_forward_threshold_gap_analysis(
        trials=completed_trials,
        top_k=top_k,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
    )
    walk_forward_rescue_plan = _walk_forward_rescue_plan(
        trials=completed_trials,
        top_k=top_k,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        threshold_gap_analysis=walk_forward_threshold_gap_analysis,
    )
    report = _base_report(
        generated_at=generated_at,
        matrix_path=matrix_path,
        matrix_fingerprint=matrix_fingerprint,
        symbol=symbol,
        search_parameters=search_parameters,
        status=status,
        message=message,
        reason_codes=reason_codes,
    )
    report.update(
        {
            "matrix_status": matrix_payload.get("status"),
            "matrix_message": matrix_payload.get("message"),
            "matrix_reason_codes": list(matrix_payload.get("reason_codes") or []),
            "input_matrix_network_access_used": bool(
                (matrix_payload.get("safety_flags") or {}).get("network_access_used")
            ),
            "skipped_timeframes": skipped_timeframes,
            "data_fingerprints": data_fingerprints,
            "source_inputs": source_input_summaries,
            "strategy_parameter_grid_counts": {
                strategy_id: len(_strategy_parameter_grid(strategy_id))
                for strategy_id in normalized_strategy_ids
            },
            "window_grid_count": len(window_grid),
            "total_base_candidate_count": total_base_candidate_count,
            "total_candidate_count": total_base_candidate_count
            * len(normalized_phases),
            "max_trials": max_trials,
            "phase_order": list(normalized_phases),
            "phase_budgets": phase_budgets,
            "phase_selection_contract": search_parameters["phase_selection_contract"],
            "coarse_sampling_contract": search_parameters["coarse_sampling_contract"],
            "promotion_feedback_contract": search_parameters[
                "promotion_feedback_contract"
            ],
            "walk_forward_window_rescue_contract": search_parameters[
                "walk_forward_window_rescue_contract"
            ],
            "walk_forward_window_profile_contract": search_parameters[
                "walk_forward_window_profile_contract"
            ],
            "walk_forward_rescue_plan_contract": search_parameters[
                "walk_forward_rescue_plan_contract"
            ],
            "walk_forward_threshold_gap_contract": search_parameters[
                "walk_forward_threshold_gap_contract"
            ],
            "walk_forward_parameter_rescue_contract": search_parameters[
                "walk_forward_parameter_rescue_contract"
            ],
            "confirm_recheck_contract": search_parameters["confirm_recheck_contract"],
            "confirm_recheck_summary": _latest_confirm_recheck_summary(
                phase_summaries
            ),
            "walk_forward_window_profile_selection_summary": (
                _walk_forward_window_profile_selection_summary(phase_summaries)
            ),
            "base_trial_repeat_selection_summary": (
                _base_trial_repeat_selection_summary(phase_summaries)
            ),
            "confirm_phase_rescue_memory_summary": (
                _confirm_phase_rescue_memory_summary(
                    completed_trials,
                    resume_history_trials=resume_history_trials,
                    min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                    min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
                )
            ),
            "phase_summaries": phase_summaries,
            "planned_trial_count": planned_trial_count,
            "completed_trial_count": len(completed_trials),
            "executed_trial_count": executed_trial_count,
            "resumed_trial_count": len(completed_trials) - executed_trial_count,
            "invalid_trial_count": len(invalid_trials),
            "invalid_trials": invalid_trials[:20],
            "stopped_reason": stopped_reason,
            "pass_count": len(passing_candidates),
            "generated_artifact_count": sum(
                int(trial.get("generated_artifact_count") or 0)
                for trial in completed_trials
            ),
            "artifact_count": sum(
                int(trial.get("generated_artifact_count") or 0)
                for trial in completed_trials
            ),
            "artifact_paths": [
                path
                for trial in passing_candidates
                for path in trial.get("artifact_paths", [])
                if path
            ],
            "source_report_paths": [
                trial.get("aggregate_source_report_path")
                for trial in passing_candidates
                if trial.get("aggregate_source_report_path")
            ],
            "validator_status": (
                latest_validation_report.get("status")
                if isinstance(latest_validation_report, dict)
                else (
                    passing_candidates[0].get("validator_status")
                    if passing_candidates
                    else best_candidate.get("validator_status")
                    if best_candidate
                    else None
                )
            ),
            "latest_validation_report_path": (
                str(latest_validator_output_path)
                if latest_validation_report is not None
                else None
            ),
            "latest_validation_report": latest_validation_report,
            "h_opt_005_ready": bool(passing_candidates),
            "h_opt_010_ready": bool(passing_candidates),
            "h_opt_005_blockers": sorted(set(h_opt_005_blockers)),
            "walk_forward_failure_analysis": overfit_diagnostics[
                "walk_forward_failure_analysis"
            ],
            "walk_forward_threshold_gap_analysis": (
                walk_forward_threshold_gap_analysis
            ),
            "walk_forward_rescue_plan": walk_forward_rescue_plan,
            "overfit_diagnostics": overfit_diagnostics,
            "best_candidate": best_candidate,
            "top_candidates": top_candidates,
            "passing_candidates": passing_candidates,
            "all_trials": completed_trials,
        }
    )
    return _write_report(report, output_path)


def _load_matrix_inputs(
    matrix_path: Path,
    requested_timeframes: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    try:
        raw_payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [], [], ["matrix_input_missing"]
    except json.JSONDecodeError:
        return {}, [], [], ["matrix_input_invalid_json"]

    timeframe_payloads = (
        raw_payload.get("timeframes")
        if isinstance(raw_payload.get("timeframes"), dict)
        else {}
    )
    ordered_timeframes = [
        timeframe for timeframe in requested_timeframes if timeframe in timeframe_payloads
    ]

    source_inputs: list[dict[str, Any]] = []
    skipped_timeframes: list[dict[str, Any]] = []
    reason_codes: list[str] = list(raw_payload.get("reason_codes") or [])
    for timeframe in ordered_timeframes:
        payload = timeframe_payloads.get(timeframe)
        if not isinstance(payload, dict):
            skipped_timeframes.append(
                {
                    "timeframe": timeframe,
                    "status": "SKIPPED",
                    "reason_codes": ["timeframe_payload_missing"],
                    "message": "timeframe payload is missing",
                }
            )
            reason_codes.append("timeframe_payload_missing")
            continue

        output_path = _resolve_matrix_output_path(payload.get("output_path"), matrix_path)
        quality_report = (
            payload.get("quality_report")
            if isinstance(payload.get("quality_report"), dict)
            else {}
        )
        timeframe_reason_codes = list(payload.get("reason_codes") or [])
        if (
            payload.get("status") != "PASS"
            or quality_report.get("passed") is False
            or output_path is None
            or not output_path.exists()
        ):
            if output_path is not None and not output_path.exists():
                timeframe_reason_codes.append("timeframe_output_missing")
            skipped_timeframes.append(
                {
                    "timeframe": timeframe,
                    "status": payload.get("status"),
                    "reason_codes": sorted(set(timeframe_reason_codes)),
                    "message": payload.get("message"),
                    "output_path": str(output_path) if output_path is not None else None,
                    "bar_count": payload.get("bar_count"),
                }
            )
            reason_codes.extend(timeframe_reason_codes)
            continue

        fingerprint = _file_fingerprint(output_path)
        fingerprint.update(
            {
                "declared_sha256": payload.get("sha256"),
                "bar_count": payload.get("bar_count"),
                "first_timestamp": payload.get("first_timestamp"),
                "last_timestamp": payload.get("last_timestamp"),
            }
        )
        source_inputs.append(
            {
                "timeframe": timeframe,
                "input_path": output_path,
                "data_fingerprint": fingerprint,
                "bar_count": payload.get("bar_count"),
            }
        )

    return raw_payload, source_inputs, skipped_timeframes, sorted(set(reason_codes))


def _build_trial_plan(
    *,
    source_inputs: list[dict[str, Any]],
    strategy_ids: list[str],
    window_grid: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    trials: list[dict[str, Any]] = []
    invalid_trials: list[dict[str, Any]] = []
    parameter_grids: dict[str, list[dict[str, Any]]] = {}
    for strategy_id in strategy_ids:
        parameter_grids[strategy_id] = []
        for strategy_parameters in _strategy_parameter_grid(strategy_id):
            try:
                normalized_parameters = normalize_candidate_strategy_parameters(
                    strategy_id,
                    strategy_parameters,
                )
            except ValueError as exc:
                invalid_trials.append(
                    {
                        "strategy_id": strategy_id,
                        "strategy_parameters": strategy_parameters,
                        "message": str(exc),
                    }
                )
                continue
            parameter_grids[strategy_id].append(normalized_parameters)

    max_parameter_count = max((len(grid) for grid in parameter_grids.values()), default=0)
    for parameter_index in range(max_parameter_count):
        for window, strategy_id in itertools.product(window_grid, strategy_ids):
            grid = parameter_grids.get(strategy_id) or []
            if parameter_index >= len(grid):
                continue
            for source_input in source_inputs:
                trials.append(
                    {
                        "timeframe": source_input["timeframe"],
                        "input_path": source_input["input_path"],
                        "data_fingerprint": source_input["data_fingerprint"],
                        "bar_count": source_input.get("bar_count"),
                        "strategy_id": strategy_id,
                        "strategy_parameters": grid[parameter_index],
                        "strategy_metadata": candidate_strategy_metadata(
                            strategy_id,
                            grid[parameter_index],
                        ),
                        "window": dict(window),
                    }
                )
    return trials, invalid_trials


def _phase_budgets(phases: list[str], max_trials: int) -> dict[str, int]:
    if not phases:
        return {}
    base_budget = max_trials // len(phases)
    remainder = max_trials % len(phases)
    budgets = {}
    for index, phase in enumerate(phases):
        budgets[phase] = base_budget + (1 if index < remainder else 0)
    return budgets


def _select_phase_trials(
    *,
    phase: str,
    base_trials: list[dict[str, Any]],
    completed_trials: list[dict[str, Any]],
    budget: int,
    promotion_basis: list[dict[str, Any]],
    min_walk_forward_pass_rate: float,
    failed_window_profile_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    if budget <= 0:
        return []
    if phase == "coarse" or not promotion_basis:
        completed_keys = {_base_trial_key(trial) for trial in completed_trials}
        candidates = _prefer_uncompleted_trials(
            base_trials,
            completed_keys=completed_keys,
        )
        if phase == "coarse":
            return _select_diversified_base_trials(candidates, budget=budget)
        return [dict(trial) for trial in candidates[:budget]]

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    completed_keys = {_base_trial_key(trial) for trial in completed_trials}
    failed_window_profile_keys = failed_window_profile_keys or set()
    candidate_lists = [
        _prefer_uncompleted_trials(
            _phase_candidate_trials(
                phase=phase,
                base_trials=base_trials,
                promoted_trial=promoted_trial,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                avoided_window_profile_keys=failed_window_profile_keys,
            ),
            completed_keys=completed_keys,
        )
        for promoted_trial in promotion_basis
    ]
    max_candidate_count = max((len(candidates) for candidates in candidate_lists), default=0)
    for candidate_index in range(max_candidate_count):
        for candidates in candidate_lists:
            if candidate_index >= len(candidates):
                continue
            trial = candidates[candidate_index]
            key = _base_trial_key(trial)
            if key not in selected_keys:
                selected.append(dict(trial))
                selected_keys.add(key)
                if len(selected) >= budget:
                    return selected

    if len(selected) < budget:
        for trial in sorted(
            base_trials,
            key=lambda item: (
                _window_profile_avoidance_rank(item, failed_window_profile_keys),
                1 if _base_trial_key(item) in completed_keys else 0,
                _base_trial_key(item),
            ),
        ):
            key = _base_trial_key(trial)
            if key in selected_keys:
                continue
            selected.append(dict(trial))
            selected_keys.add(key)
            if len(selected) >= budget:
                break
    return selected


def _prefer_uncompleted_trials(
    trials: list[dict[str, Any]],
    *,
    completed_keys: set[str],
) -> list[dict[str, Any]]:
    if not completed_keys:
        return list(trials)
    uncompleted = [
        trial for trial in trials if _base_trial_key(trial) not in completed_keys
    ]
    repeated = [
        trial for trial in trials if _base_trial_key(trial) in completed_keys
    ]
    return uncompleted + repeated


def _select_diversified_base_trials(
    trials: list[dict[str, Any]],
    *,
    budget: int,
) -> list[dict[str, Any]]:
    if budget <= 0:
        return []

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    selected_buckets: set[tuple[str, str]] = set()
    strategy_order = _ordered_trial_values(trials, "strategy_id")
    timeframe_order = _ordered_trial_values(trials, "timeframe")

    def add_first_matching(
        *,
        strategy_id: str | None = None,
        timeframe: str | None = None,
        prefer_new_bucket: bool = False,
    ) -> bool:
        for trial in trials:
            if strategy_id is not None and str(trial.get("strategy_id")) != strategy_id:
                continue
            if timeframe is not None and str(trial.get("timeframe")) != timeframe:
                continue
            key = _base_trial_key(trial)
            if key in selected_keys:
                continue
            bucket = _strategy_timeframe_bucket(trial)
            if prefer_new_bucket and bucket in selected_buckets:
                continue
            selected.append(dict(trial))
            selected_keys.add(key)
            selected_buckets.add(bucket)
            return True
        return False

    for strategy_id in strategy_order:
        add_first_matching(strategy_id=strategy_id, prefer_new_bucket=True)
        if len(selected) >= budget:
            return selected

    for timeframe in timeframe_order:
        if any(str(trial.get("timeframe")) == timeframe for trial in selected):
            continue
        add_first_matching(timeframe=timeframe, prefer_new_bucket=True)
        if len(selected) >= budget:
            return selected

    for timeframe in timeframe_order:
        for strategy_id in strategy_order:
            add_first_matching(
                strategy_id=strategy_id,
                timeframe=timeframe,
                prefer_new_bucket=True,
            )
            if len(selected) >= budget:
                return selected

    for trial in trials:
        key = _base_trial_key(trial)
        if key in selected_keys:
            continue
        selected.append(dict(trial))
        selected_keys.add(key)
        selected_buckets.add(_strategy_timeframe_bucket(trial))
        if len(selected) >= budget:
            break
    return selected


def _ordered_trial_values(trials: list[dict[str, Any]], key: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for trial in trials:
        value = str(trial.get(key))
        if value in seen:
            continue
        ordered.append(value)
        seen.add(value)
    return ordered


def _phase_candidate_trials(
    *,
    phase: str,
    base_trials: list[dict[str, Any]],
    promoted_trial: dict[str, Any],
    min_walk_forward_pass_rate: float,
    avoided_window_profile_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    if phase == "confirm":
        return _confirm_recheck_trials(
            base_trials,
            promoted_trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            avoided_window_profile_keys=avoided_window_profile_keys,
        )
    return _fine_neighbor_trials(
        base_trials,
        promoted_trial,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        avoided_window_profile_keys=avoided_window_profile_keys,
    )


def _fine_neighbor_trials(
    base_trials: list[dict[str, Any]],
    promoted_trial: dict[str, Any],
    *,
    min_walk_forward_pass_rate: float,
    avoided_window_profile_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    same_strategy_and_timeframe = []
    same_strategy = []
    same_timeframe = []
    for trial in base_trials:
        if trial.get("strategy_id") != promoted_trial.get("strategy_id"):
            if trial.get("timeframe") == promoted_trial.get("timeframe"):
                same_timeframe.append(trial)
            continue
        if trial.get("timeframe") == promoted_trial.get("timeframe"):
            same_strategy_and_timeframe.append(trial)
        else:
            same_strategy.append(trial)
    return sorted(
        same_strategy_and_timeframe,
        key=lambda trial: _fine_neighbor_rank(
            trial,
            promoted_trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            avoided_window_profile_keys=avoided_window_profile_keys,
        ),
    ) + same_strategy + same_timeframe


def _confirm_recheck_trials(
    base_trials: list[dict[str, Any]],
    promoted_trial: dict[str, Any],
    *,
    min_walk_forward_pass_rate: float,
    avoided_window_profile_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    rescue_context = _walk_forward_window_rescue_context(
        promoted_trial,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
    )
    if rescue_context["walk_forward_rescue_applicable"]:
        candidates = _confirm_rescue_window_parameter_trials(
            base_trials,
            promoted_trial,
        )
    else:
        candidates = _confirm_holdout_window_trials(base_trials, promoted_trial)
    if not candidates:
        candidates = _fine_neighbor_trials(
            base_trials,
            promoted_trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            avoided_window_profile_keys=avoided_window_profile_keys,
        )
    ordered = sorted(
        candidates,
        key=lambda trial: _confirm_recheck_rank(
            trial,
            promoted_trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            avoided_window_profile_keys=avoided_window_profile_keys,
        ),
    )
    annotated = []
    for trial in ordered:
        candidate = dict(trial)
        candidate["promoted_candidate_version"] = promoted_trial.get(
            "candidate_version"
        )
        candidate["independent_confirm_recheck"] = _independent_confirm_recheck(
            candidate,
            promoted_trial,
        )
        annotated.append(candidate)
    return annotated


def _confirm_holdout_window_trials(
    base_trials: list[dict[str, Any]],
    promoted_trial: dict[str, Any],
) -> list[dict[str, Any]]:
    promoted_strategy = promoted_trial.get("strategy_id")
    promoted_timeframe = promoted_trial.get("timeframe")
    promoted_parameters = dict(promoted_trial.get("strategy_parameters") or {})
    candidates = [
        trial
        for trial in base_trials
        if trial.get("strategy_id") == promoted_strategy
        and trial.get("timeframe") == promoted_timeframe
        and dict(trial.get("strategy_parameters") or {}) == promoted_parameters
    ]
    if not candidates:
        candidates = [
            trial
            for trial in base_trials
            if trial.get("strategy_id") == promoted_strategy
            and trial.get("timeframe") == promoted_timeframe
        ]
    return candidates


def _confirm_rescue_window_parameter_trials(
    base_trials: list[dict[str, Any]],
    promoted_trial: dict[str, Any],
) -> list[dict[str, Any]]:
    promoted_strategy = promoted_trial.get("strategy_id")
    promoted_timeframe = promoted_trial.get("timeframe")
    same_strategy_and_timeframe = [
        trial
        for trial in base_trials
        if trial.get("strategy_id") == promoted_strategy
        and trial.get("timeframe") == promoted_timeframe
    ]
    independent_window_candidates = [
        trial
        for trial in same_strategy_and_timeframe
        if _has_independent_holdout_or_window(trial, promoted_trial)
    ]
    return independent_window_candidates or same_strategy_and_timeframe


def _has_independent_holdout_or_window(
    trial: dict[str, Any],
    promoted_trial: dict[str, Any],
) -> bool:
    recheck = _independent_confirm_recheck(trial, promoted_trial)
    return (
        recheck["independent_holdout_ratio"] is True
        or recheck["independent_walk_forward_window"] is True
    )


def _confirm_recheck_rank(
    trial: dict[str, Any],
    promoted_trial: dict[str, Any],
    *,
    min_walk_forward_pass_rate: float,
    avoided_window_profile_keys: set[str] | None = None,
) -> tuple[Any, ...]:
    recheck = _independent_confirm_recheck(trial, promoted_trial)
    window = trial.get("window") if isinstance(trial.get("window"), dict) else {}
    rescue_context = _walk_forward_window_rescue_context(
        promoted_trial,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
    )
    gap_bucket = _walk_forward_rescue_gap_bucket(
        promoted_trial,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
    )
    return (
        0 if rescue_context["walk_forward_rescue_applicable"] else 1,
        _window_profile_avoidance_rank(trial, avoided_window_profile_keys),
        _moderate_gap_parameter_neighbor_rank(
            recheck,
            gap_bucket=gap_bucket,
        ),
        0 if _is_dual_independent_recheck(recheck) else 1,
        0 if recheck["strategy_parameter_neighbor"] else 1,
        0 if recheck["independent_holdout_ratio"] else 1,
        0 if recheck["independent_walk_forward_window"] else 1,
        -float(window.get("holdout_ratio") or 0.0),
        -int(window.get("test_bars") or 0),
        -int(window.get("train_bars") or 0),
        _parameter_distance(trial, promoted_trial),
        _base_trial_key(trial),
    )


def _independent_confirm_recheck(
    trial: dict[str, Any],
    promoted_trial: dict[str, Any],
) -> dict[str, Any]:
    trial_window = trial.get("window") if isinstance(trial.get("window"), dict) else {}
    promoted_window = (
        promoted_trial.get("window")
        if isinstance(promoted_trial.get("window"), dict)
        else {}
    )
    trial_holdout_ratio = _coerce_float(trial_window.get("holdout_ratio"))
    promoted_holdout_ratio = _coerce_float(promoted_window.get("holdout_ratio"))
    trial_parameters = dict(trial.get("strategy_parameters") or {})
    promoted_parameters = dict(promoted_trial.get("strategy_parameters") or {})
    parameter_distance = _parameter_distance(trial, promoted_trial)
    return {
        "source_phase": promoted_trial.get("phase"),
        "source_candidate_version": promoted_trial.get("candidate_version"),
        "independent_holdout_ratio": (
            trial_holdout_ratio is not None
            and promoted_holdout_ratio is not None
            and trial_holdout_ratio != promoted_holdout_ratio
        ),
        "independent_walk_forward_window": (
            int(trial_window.get("train_bars") or 0)
            != int(promoted_window.get("train_bars") or 0)
            or int(trial_window.get("test_bars") or 0)
            != int(promoted_window.get("test_bars") or 0)
            or int(trial_window.get("step_bars") or 0)
            != int(promoted_window.get("step_bars") or 0)
        ),
        "strategy_parameter_neighbor": trial_parameters != promoted_parameters,
        "parameter_distance": {
            "changed_key_count": parameter_distance[0],
            "numeric_distance": parameter_distance[1],
        },
        "independent_monte_carlo_seed": False,
    }


def _fine_neighbor_rank(
    trial: dict[str, Any],
    promoted_trial: dict[str, Any],
    *,
    min_walk_forward_pass_rate: float,
    avoided_window_profile_keys: set[str] | None = None,
) -> tuple[Any, ...]:
    rescue_context = _walk_forward_window_rescue_context(
        promoted_trial,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
    )
    if not rescue_context["walk_forward_rescue_applicable"]:
        return (1, _parameter_distance(trial, promoted_trial))
    recheck = _independent_confirm_recheck(trial, promoted_trial)
    gap_bucket = _walk_forward_rescue_gap_bucket(
        promoted_trial,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
    )
    return (
        0,
        _window_profile_avoidance_rank(trial, avoided_window_profile_keys),
        _moderate_gap_parameter_neighbor_rank(
            recheck,
            gap_bucket=gap_bucket,
        ),
        0 if recheck["independent_holdout_ratio"] else 1,
        0 if recheck["independent_walk_forward_window"] else 1,
        _parameter_distance(trial, promoted_trial),
    )


def _is_dual_independent_recheck(recheck: dict[str, Any]) -> bool:
    return (
        recheck.get("independent_holdout_ratio") is True
        and recheck.get("independent_walk_forward_window") is True
    )


def _walk_forward_window_rescue_context(
    trial: dict[str, Any],
    *,
    min_walk_forward_pass_rate: float,
) -> dict[str, Any]:
    walk_forward_pass_rate = _trial_metric_float(trial, "walk_forward_pass_rate")
    return {
        "walk_forward_pass_rate": walk_forward_pass_rate,
        "min_walk_forward_pass_rate": min_walk_forward_pass_rate,
        "walk_forward_rescue_applicable": (
            walk_forward_pass_rate is not None
            and walk_forward_pass_rate < min_walk_forward_pass_rate
        ),
    }


def _walk_forward_rescue_gap_bucket(
    trial: dict[str, Any],
    *,
    min_walk_forward_pass_rate: float,
) -> str | None:
    walk_forward_pass_rate = _trial_metric_float(trial, "walk_forward_pass_rate")
    if walk_forward_pass_rate is None:
        return None
    gap = max(0.0, min_walk_forward_pass_rate - float(walk_forward_pass_rate))
    return _walk_forward_threshold_gap_bucket(gap)


def _moderate_gap_parameter_neighbor_rank(
    recheck: dict[str, Any],
    *,
    gap_bucket: str | None,
) -> int:
    if gap_bucket != "moderate_gap":
        return 0
    if recheck.get("strategy_parameter_neighbor") is not True:
        return 1
    return 0 if _has_independent_holdout_or_walk_forward_recheck(recheck) else 1


def _has_independent_holdout_or_walk_forward_recheck(
    recheck: dict[str, Any],
) -> bool:
    return (
        recheck.get("independent_holdout_ratio") is True
        or recheck.get("independent_walk_forward_window") is True
    )


def _parameter_distance(
    trial: dict[str, Any],
    promoted_trial: dict[str, Any],
) -> tuple[Any, ...]:
    trial_parameters = dict(trial.get("strategy_parameters") or {})
    promoted_parameters = dict(promoted_trial.get("strategy_parameters") or {})
    common_keys = sorted(set(trial_parameters) | set(promoted_parameters))
    numeric_distance = 0.0
    changed_keys = 0
    for key in common_keys:
        trial_value = trial_parameters.get(key)
        promoted_value = promoted_parameters.get(key)
        if trial_value == promoted_value:
            continue
        changed_keys += 1
        trial_number = _coerce_float(trial_value)
        promoted_number = _coerce_float(promoted_value)
        if trial_number is None or promoted_number is None:
            numeric_distance += 1.0
        else:
            numeric_distance += abs(trial_number - promoted_number)
    return (changed_keys, numeric_distance, _base_trial_key(trial))


def _window_profile_avoidance_rank(
    trial: dict[str, Any],
    avoided_window_profile_keys: set[str] | None,
) -> int:
    if not avoided_window_profile_keys:
        return 0
    return 1 if _window_profile_key(trial) in avoided_window_profile_keys else 0


def _phase_promotion_basis(
    *,
    phase: str,
    completed_trials: list[dict[str, Any]],
    resume_history_trials: list[dict[str, Any]] | None = None,
    top_k: int,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> list[dict[str, Any]]:
    if phase == "coarse" or not completed_trials:
        return []
    previous_phase = "coarse" if phase == "fine" else "fine"
    previous_trials = [
        trial
        for trial in completed_trials
        if trial.get("phase") == previous_phase
    ]
    if not previous_trials:
        previous_trials = list(completed_trials)
    if phase == "confirm":
        resume_promoted_trials = _confirm_rescue_memory_promoted_trials(
            resume_history_trials or [],
            completed_trials=completed_trials,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        )
        if resume_promoted_trials:
            previous_trials = resume_promoted_trials + previous_trials
    return _rank_promotion_basis(
        trials=previous_trials,
        top_k=top_k,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
    )


def _rank_promotion_basis(
    *,
    trials: list[dict[str, Any]],
    top_k: int,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        return []
    oos_mc_walk_forward_failures = [
        trial
        for trial in trials
        if _oos_and_monte_carlo_pass(
            trial,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        )
        and _trial_metric_float(trial, "walk_forward_pass_rate") is not None
        and _trial_metric_float(trial, "walk_forward_pass_rate")
        < min_walk_forward_pass_rate
    ]
    non_large_gap_failures = [
        trial
        for trial in oos_mc_walk_forward_failures
        if _walk_forward_rescue_gap_bucket(
            trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        )
        != "large_gap"
    ]
    large_gap_failures = [
        trial
        for trial in oos_mc_walk_forward_failures
        if _walk_forward_rescue_gap_bucket(
            trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        )
        == "large_gap"
    ]
    ranking_groups = [
        sorted(
            non_large_gap_failures,
            key=lambda trial: _walk_forward_threshold_gap_selection_score(
                trial,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            ),
        ),
        sorted(trials, key=_walk_forward_rescue_score, reverse=True),
        sorted(trials, key=_trial_score, reverse=True),
        sorted(
            large_gap_failures,
            key=lambda trial: _walk_forward_threshold_gap_selection_score(
                trial,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            ),
        ),
    ]
    return _select_diversified_candidates(ranking_groups, limit=top_k)


def _confirm_rescue_memory_promoted_trials(
    resume_history_trials: list[dict[str, Any]],
    *,
    completed_trials: list[dict[str, Any]],
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> list[dict[str, Any]]:
    if not resume_history_trials:
        return []
    completed_versions = {
        _promotion_candidate_version(trial) for trial in completed_trials
    }
    rescue_trials = [
        trial
        for trial in resume_history_trials
        if trial.get("phase") == "confirm"
        and _promotion_candidate_version(trial) not in completed_versions
        and _oos_and_monte_carlo_pass(
            trial,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        )
        and _trial_metric_float(trial, "walk_forward_pass_rate") is not None
        and _trial_metric_float(trial, "walk_forward_pass_rate")
        < min_walk_forward_pass_rate
    ]
    return sorted(
        rescue_trials,
        key=lambda trial: _walk_forward_threshold_gap_selection_score(
            trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        ),
    )


def _select_diversified_candidates(
    ranking_groups: list[list[dict[str, Any]]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    selected: list[dict[str, Any]] = []
    selected_versions: set[str] = set()
    selected_timeframes: set[str] = set()
    selected_buckets: set[tuple[str, str]] = set()

    for diversity_pass in ("timeframe", "strategy_timeframe", "ranked_fill"):
        for ranking in ranking_groups:
            for trial in ranking:
                version = _promotion_candidate_version(trial)
                if version in selected_versions:
                    continue
                timeframe = str(trial.get("timeframe"))
                bucket = _strategy_timeframe_bucket(trial)
                if diversity_pass == "timeframe" and timeframe in selected_timeframes:
                    continue
                if diversity_pass == "strategy_timeframe" and bucket in selected_buckets:
                    continue
                selected.append(trial)
                selected_versions.add(version)
                selected_timeframes.add(timeframe)
                selected_buckets.add(bucket)
                if len(selected) >= limit:
                    return selected
    return selected


def _promotion_candidate_version(trial: dict[str, Any]) -> str:
    return str(trial.get("candidate_version") or _base_trial_key(trial))


def _strategy_timeframe_bucket(trial: dict[str, Any]) -> tuple[str, str]:
    return (str(trial.get("timeframe")), str(trial.get("strategy_id")))


def _phase_trial(
    *,
    base_trial: dict[str, Any],
    phase: str,
    symbol: str,
) -> dict[str, Any]:
    trial = dict(base_trial)
    trial["symbol"] = symbol
    trial["phase"] = phase
    trial["phase_role"] = PHASE_ROLES[phase]
    trial["artifact_publication_allowed"] = phase == "confirm"
    trial["candidate_version"] = _candidate_version(
        symbol=symbol,
        timeframe=trial["timeframe"],
        strategy_id=trial["strategy_id"],
        data_fingerprint=trial["data_fingerprint"],
        strategy_parameters=trial["strategy_parameters"],
        window=trial["window"],
        phase=phase,
    )
    return trial


def _resumed_trial_for_phase(
    *,
    trial: dict[str, Any],
    resumed_trials_by_version: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    resumed_trial = resumed_trials_by_version.get(trial["candidate_version"])
    if not isinstance(resumed_trial, dict) and trial.get("phase") == "confirm":
        resumed_trial = resumed_trials_by_version.get(_legacy_candidate_version(trial))
    if not isinstance(resumed_trial, dict):
        return None
    enriched = dict(resumed_trial)
    enriched.setdefault("phase", trial["phase"])
    enriched.setdefault("phase_role", trial["phase_role"])
    enriched.setdefault(
        "artifact_publication_allowed",
        trial["artifact_publication_allowed"],
    )
    enriched.setdefault("phase_candidate_version", trial["candidate_version"])
    enriched.setdefault("base_candidate_version", _base_candidate_version(trial))
    enriched.setdefault("promoted_candidate_version", trial.get("promoted_candidate_version"))
    enriched.setdefault("monte_carlo_seed", trial.get("monte_carlo_seed"))
    enriched.setdefault(
        "monte_carlo_seed_policy",
        trial.get("monte_carlo_seed_policy"),
    )
    if trial.get("independent_confirm_recheck") is not None:
        enriched.setdefault(
            "independent_confirm_recheck",
            trial.get("independent_confirm_recheck"),
        )
    if "overfit_diagnostics" not in enriched:
        enriched["overfit_diagnostics"] = _trial_overfit_diagnostics(enriched)
    return enriched


def _phase_summary(
    *,
    phase: str,
    planned_trials: list[dict[str, Any]],
    completed_trials: list[dict[str, Any]],
    budget: int,
    promotion_basis: list[dict[str, Any]],
    failed_window_profile_keys: set[str] | None,
    completed_base_trial_keys: set[str] | None,
    stopped_reason: str | None,
) -> dict[str, Any]:
    pass_trials = [
        trial for trial in completed_trials if trial.get("status") == "PASS"
    ]
    return {
        "phase": phase,
        "phase_role": PHASE_ROLES[phase],
        "budget": budget,
        "planned_trial_count": len(planned_trials),
        "completed_trial_count": len(completed_trials),
        "pass_count": len(pass_trials),
        "artifact_publication_allowed": phase == "confirm",
        "promotion_basis_candidate_versions": [
            trial.get("candidate_version") for trial in promotion_basis
        ],
        "promoted_candidate_count": len(promotion_basis),
        "selection_policy": _phase_selection_policy(phase),
        "window_profile_selection_summary": _phase_window_profile_selection_summary(
            phase=phase,
            planned_trials=planned_trials,
            completed_trials=completed_trials,
            failed_window_profile_keys=failed_window_profile_keys,
        ),
        "base_trial_repeat_selection_summary": _phase_base_trial_repeat_summary(
            phase=phase,
            planned_trials=planned_trials,
            completed_trials=completed_trials,
            completed_base_trial_keys=completed_base_trial_keys,
        ),
        "confirm_recheck_summary": (
            _confirm_recheck_summary(completed_trials)
            if phase == "confirm"
            else None
        ),
        "stopped_reason": stopped_reason,
        "top_candidates": sorted(
            completed_trials,
            key=_trial_score,
            reverse=True,
        )[:5],
        "reason_codes": _collect_reason_codes(completed_trials),
    }


def _build_window_grid(
    *,
    train_bars_values: list[int],
    test_bars_values: list[int],
    step_bars_values: list[int],
    holdout_ratios: list[float],
    min_trade_counts: list[int],
) -> list[dict[str, Any]]:
    grid = []
    for train, test, step, holdout_ratio, min_trade_count in itertools.product(
        train_bars_values,
        test_bars_values,
        step_bars_values,
        holdout_ratios,
        min_trade_counts,
    ):
        grid.append(
            {
                "train_bars": int(train),
                "test_bars": int(test),
                "step_bars": int(step),
                "holdout_ratio": float(holdout_ratio),
                "min_trade_count": int(min_trade_count),
            }
        )
    return grid


def _strategy_parameter_grid(strategy_id: str) -> list[dict[str, Any]]:
    if strategy_id == "ma_crossover":
        return [
            {"fast_window": fast, "slow_window": slow}
            for fast, slow in itertools.product([1, 2, 3, 5, 8, 13], [8, 13, 21, 34, 55])
            if slow > fast
        ]
    if strategy_id == "ema_trend_filter":
        return [
            {
                "fast_window": fast,
                "slow_window": slow,
                "atr_window": 14,
                "volatility_window": 20,
                "max_atr_pct": max_atr_pct,
                "max_volatility_pct": max_volatility_pct,
                "min_trend_strength": min_trend_strength,
            }
            for fast, slow, max_atr_pct, max_volatility_pct, min_trend_strength in itertools.product(
                [3, 5, 8],
                [13, 21, 34],
                [0.04, 0.08],
                [0.04, 0.08],
                [0.0, 0.5],
            )
            if slow > fast
        ]
    if strategy_id == "donchian_breakout":
        return [
            {"channel_window": channel_window, "exit_window": exit_window}
            for channel_window, exit_window in itertools.product(
                [10, 20, 34, 55],
                [5, 10, 20],
            )
        ]
    if strategy_id == "keltner_breakout":
        return [
            {
                "ema_window": ema_window,
                "atr_window": atr_window,
                "atr_multiplier": atr_multiplier,
                "exit_midline": exit_midline,
            }
            for ema_window, atr_window, atr_multiplier, exit_midline in itertools.product(
                [13, 20, 34],
                [10, 14, 20],
                [1.0, 1.5, 2.0],
                [True, False],
            )
        ]
    if strategy_id == "volume_breakout":
        return [
            {
                "price_window": price_window,
                "volume_window": volume_window,
                "min_volume_ratio": min_volume_ratio,
                "exit_window": exit_window,
                "exit_on_breakdown": exit_on_breakdown,
            }
            for price_window, volume_window, min_volume_ratio, exit_window, exit_on_breakdown in itertools.product(
                [10, 20, 34],
                [10, 20, 34],
                [1.1, 1.3, 1.6],
                [5, 10],
                [True, False],
            )
        ]
    if strategy_id == "macd_momentum":
        return [
            {
                "fast_window": fast_window,
                "slow_window": slow_window,
                "signal_window": signal_window,
                "atr_window": 14,
                "min_histogram_pct": min_histogram_pct,
                "max_atr_pct": max_atr_pct,
                "exit_on_signal_cross": exit_on_signal_cross,
            }
            for fast_window, slow_window, signal_window, min_histogram_pct, max_atr_pct, exit_on_signal_cross in itertools.product(
                [8, 12, 16],
                [21, 26, 34],
                [5, 9, 12],
                [0.0, 0.0005],
                [0.04, 0.08],
                [True, False],
            )
            if slow_window > fast_window
        ]
    if strategy_id == "roc_momentum":
        return [
            {
                "roc_window": roc_window,
                "trend_window": trend_window,
                "atr_window": 14,
                "min_roc_pct": min_roc_pct,
                "max_atr_pct": max_atr_pct,
                "exit_roc_pct": exit_roc_pct,
                "exit_on_trend_loss": exit_on_trend_loss,
            }
            for roc_window, trend_window, min_roc_pct, max_atr_pct, exit_roc_pct, exit_on_trend_loss in itertools.product(
                [6, 12, 24],
                [21, 34, 55],
                [0.005, 0.01, 0.02],
                [0.04, 0.08],
                [-0.005, 0.0],
                [True, False],
            )
            if exit_roc_pct < min_roc_pct
        ]
    if strategy_id == "stochastic_reversion":
        return [
            {
                "k_window": k_window,
                "d_window": d_window,
                "oversold": oversold,
                "overbought": overbought,
                "exit_k": exit_k,
                "exit_on_midline": exit_on_midline,
            }
            for k_window, d_window, oversold, overbought, exit_k, exit_on_midline in itertools.product(
                [9, 14, 21],
                [3, 5],
                [15.0, 20.0, 25.0],
                [75.0, 80.0],
                [45.0, 50.0],
                [True, False],
            )
            if oversold < exit_k < overbought
        ]
    if strategy_id == "ema_pullback_reentry":
        return [
            {
                "fast_window": fast_window,
                "slow_window": slow_window,
                "rsi_window": rsi_window,
                "pullback_rsi": pullback_rsi,
                "reentry_rsi": reentry_rsi,
                "exit_rsi": exit_rsi,
                "atr_window": 14,
                "max_atr_pct": max_atr_pct,
                "exit_on_trend_loss": exit_on_trend_loss,
            }
            for fast_window, slow_window, rsi_window, pullback_rsi, reentry_rsi, exit_rsi, max_atr_pct, exit_on_trend_loss in itertools.product(
                [8, 13, 21],
                [34, 55, 89],
                [7, 14],
                [35.0, 40.0],
                [50.0, 55.0],
                [65.0, 70.0],
                [0.04, 0.08],
                [True],
            )
            if slow_window > fast_window and pullback_rsi < reentry_rsi < exit_rsi
        ]
    if strategy_id == "atr_channel_reversion":
        return [
            {
                "ema_window": ema_window,
                "atr_window": atr_window,
                "atr_multiplier": atr_multiplier,
                "min_atr_pct": min_atr_pct,
                "max_atr_pct": max_atr_pct,
                "exit_midline": exit_midline,
            }
            for ema_window, atr_window, atr_multiplier, min_atr_pct, max_atr_pct, exit_midline in itertools.product(
                [13, 20, 34],
                [10, 14, 20],
                [1.0, 1.5, 2.0],
                [0.0, 0.01],
                [0.04, 0.08],
                [True, False],
            )
            if min_atr_pct <= max_atr_pct
        ]
    if strategy_id == "gap_reversal":
        return [
            {
                "atr_window": atr_window,
                "volume_window": volume_window,
                "min_gap_pct": min_gap_pct,
                "min_reclaim_ratio": min_reclaim_ratio,
                "min_volume_ratio": min_volume_ratio,
                "max_atr_pct": max_atr_pct,
                "exit_on_up_gap": exit_on_up_gap,
            }
            for atr_window, volume_window, min_gap_pct, min_reclaim_ratio, min_volume_ratio, max_atr_pct, exit_on_up_gap in itertools.product(
                [10, 14, 20],
                [10, 20, 34],
                [0.0015, 0.0025, 0.004],
                [0.25, 0.4],
                [0.8, 1.0],
                [0.04, 0.08],
                [True, False],
            )
        ]
    if strategy_id == "gap_continuation_breakout":
        return [
            {
                "atr_window": atr_window,
                "volume_window": volume_window,
                "min_gap_pct": min_gap_pct,
                "min_follow_through_ratio": min_follow_through_ratio,
                "min_volume_ratio": min_volume_ratio,
                "min_atr_pct": min_atr_pct,
                "max_atr_pct": max_atr_pct,
                "exit_on_down_gap": exit_on_down_gap,
            }
            for atr_window, volume_window, min_gap_pct, min_follow_through_ratio, min_volume_ratio, min_atr_pct, max_atr_pct, exit_on_down_gap in itertools.product(
                [10, 14, 20],
                [10, 20, 34],
                [0.0015, 0.0025, 0.004],
                [0.25, 0.4, 0.6],
                [0.8, 1.1],
                [0.0, 0.005],
                [0.04, 0.08],
                [True, False],
            )
            if min_atr_pct <= max_atr_pct
        ]
    if strategy_id == "liquidity_sweep_reversal":
        return [
            {
                "range_window": range_window,
                "atr_window": atr_window,
                "volume_window": volume_window,
                "min_sweep_pct": min_sweep_pct,
                "min_close_position": min_close_position,
                "min_volume_ratio": min_volume_ratio,
                "min_atr_pct": min_atr_pct,
                "max_atr_pct": max_atr_pct,
                "exit_on_bearish_sweep": exit_on_bearish_sweep,
            }
            for range_window, atr_window, volume_window, min_sweep_pct, min_close_position, min_volume_ratio, min_atr_pct, max_atr_pct, exit_on_bearish_sweep in itertools.product(
                [10, 20, 34],
                [10, 14],
                [10, 20, 34],
                [0.001, 0.0025, 0.004],
                [0.55, 0.65],
                [0.8, 1.1],
                [0.0, 0.005],
                [0.04, 0.08],
                [True, False],
            )
            if min_atr_pct <= max_atr_pct
        ]
    if strategy_id == "volatility_squeeze_breakout":
        return [
            {
                "breakout_window": breakout_window,
                "bb_window": bb_window,
                "squeeze_window": squeeze_window,
                "bandwidth_stddev": bandwidth_stddev,
                "max_squeeze_ratio": max_squeeze_ratio,
                "min_volume_ratio": min_volume_ratio,
                "atr_window": 14,
                "min_atr_pct": min_atr_pct,
                "max_atr_pct": max_atr_pct,
                "exit_on_midline_loss": exit_on_midline_loss,
            }
            for breakout_window, bb_window, squeeze_window, bandwidth_stddev, max_squeeze_ratio, min_volume_ratio, min_atr_pct, max_atr_pct, exit_on_midline_loss in itertools.product(
                [10, 20, 34],
                [14, 20],
                [10, 20, 34],
                [1.5, 2.0],
                [0.75, 0.9],
                [0.8, 1.1],
                [0.0, 0.005],
                [0.04, 0.08],
                [True, False],
            )
            if min_atr_pct <= max_atr_pct
        ]
    if strategy_id == "range_compression_breakout":
        return [
            {
                "breakout_window": breakout_window,
                "compression_window": compression_window,
                "volume_window": volume_window,
                "atr_window": 14,
                "max_range_width_pct": max_range_width_pct,
                "max_compression_ratio": max_compression_ratio,
                "min_volume_ratio": min_volume_ratio,
                "min_atr_pct": min_atr_pct,
                "max_atr_pct": max_atr_pct,
                "exit_on_midline_loss": exit_on_midline_loss,
            }
            for breakout_window, compression_window, volume_window, max_range_width_pct, max_compression_ratio, min_volume_ratio, min_atr_pct, max_atr_pct, exit_on_midline_loss in itertools.product(
                [10, 20, 34],
                [10, 20, 34],
                [10, 20, 34],
                [0.012, 0.02, 0.035],
                [0.65, 0.8],
                [0.8, 1.1],
                [0.0, 0.005],
                [0.04, 0.08],
                [True, False],
            )
            if min_atr_pct <= max_atr_pct
        ]
    if strategy_id == "trend_pullback_breakout":
        return [
            {
                "fast_window": fast_window,
                "slow_window": slow_window,
                "breakout_window": breakout_window,
                "pullback_window": pullback_window,
                "volume_window": volume_window,
                "atr_window": 14,
                "min_pullback_depth_pct": min_pullback_depth_pct,
                "max_pullback_depth_pct": max_pullback_depth_pct,
                "min_trend_spread_pct": min_trend_spread_pct,
                "min_volume_ratio": min_volume_ratio,
                "min_atr_pct": min_atr_pct,
                "max_atr_pct": max_atr_pct,
                "exit_on_fast_ema_loss": exit_on_fast_ema_loss,
            }
            for fast_window, slow_window, breakout_window, pullback_window, volume_window, min_pullback_depth_pct, max_pullback_depth_pct, min_trend_spread_pct, min_volume_ratio, min_atr_pct, max_atr_pct, exit_on_fast_ema_loss in itertools.product(
                [8, 13, 21],
                [34, 55, 89],
                [10, 20, 34],
                [5, 10, 20],
                [10, 20],
                [0.001, 0.003],
                [0.02, 0.04],
                [0.0, 0.002],
                [0.8, 1.1],
                [0.0, 0.005],
                [0.04, 0.08],
                [True],
            )
            if slow_window > fast_window
            and min_pullback_depth_pct <= max_pullback_depth_pct
            and min_atr_pct <= max_atr_pct
        ]
    if strategy_id == "chandelier_breakout":
        return [
            {
                "entry_window": entry_window,
                "exit_window": exit_window,
                "atr_window": atr_window,
                "atr_multiplier": atr_multiplier,
                "volume_window": volume_window,
                "min_volume_ratio": min_volume_ratio,
                "min_atr_pct": min_atr_pct,
                "max_atr_pct": max_atr_pct,
                "exit_on_chandelier_loss": exit_on_chandelier_loss,
            }
            for entry_window, exit_window, atr_window, atr_multiplier, volume_window, min_volume_ratio, min_atr_pct, max_atr_pct, exit_on_chandelier_loss in itertools.product(
                [10, 20, 34],
                [10, 20, 34],
                [10, 14, 20],
                [1.5, 2.5, 3.5],
                [10, 20, 34],
                [0.8, 1.1],
                [0.0, 0.005],
                [0.04, 0.08],
                [True, False],
            )
            if min_atr_pct <= max_atr_pct
        ]
    if strategy_id == "rolling_vwap_reversion":
        return [
            {
                "vwap_window": vwap_window,
                "volume_window": volume_window,
                "atr_window": atr_window,
                "entry_band_pct": entry_band_pct,
                "min_volume_ratio": min_volume_ratio,
                "min_atr_pct": min_atr_pct,
                "max_atr_pct": max_atr_pct,
                "exit_on_vwap_reclaim": exit_on_vwap_reclaim,
            }
            for vwap_window, volume_window, atr_window, entry_band_pct, min_volume_ratio, min_atr_pct, max_atr_pct, exit_on_vwap_reclaim in itertools.product(
                [10, 20, 34],
                [10, 20, 34],
                [10, 14, 20],
                [0.006, 0.012, 0.02],
                [0.8, 1.1],
                [0.0, 0.005],
                [0.04, 0.08],
                [True, False],
            )
            if min_atr_pct <= max_atr_pct
        ]
    if strategy_id == "rsi_mean_reversion":
        return [
            {
                "rsi_window": rsi_window,
                "oversold": oversold,
                "overbought": overbought,
                "exit_rsi": 50.0,
            }
            for rsi_window, oversold, overbought in itertools.product(
                [7, 14, 21],
                [20.0, 25.0, 30.0],
                [65.0, 70.0],
            )
            if oversold < overbought
        ]
    if strategy_id == "bollinger_reversion":
        return [
            {"window": window, "stddev": stddev, "exit_midline": exit_midline}
            for window, stddev, exit_midline in itertools.product(
                [14, 20, 34],
                [1.5, 2.0, 2.5],
                [True, False],
            )
        ]
    raise ValueError(f"unsupported_candidate_strategy: {strategy_id}")


def _run_trial(
    *,
    index: int,
    generated_at: int,
    trial: dict[str, Any],
    trial_output_dir: Path,
    source_report_dir: Path,
    artifact_dir: Path,
    min_train_bars: int,
    min_holdout_bars: int,
    min_walk_forward_windows: int,
    min_walk_forward_pass_rate: float,
    monte_carlo_run_count: int,
    monte_carlo_seed: int,
    min_monte_carlo_trades: int | None,
    min_monte_carlo_survival_rate: float,
    monte_carlo_max_drawdown_limit: float | None,
    initial_balance: float,
    require_gate_pass: bool,
    min_net_pnl: float,
    max_drawdown: float | None,
    min_win_rate: float | None,
    min_out_of_sample_net_pnl: float,
    overwrite_existing_source_reports: bool,
    overwrite_existing_artifacts: bool,
) -> dict[str, Any]:
    candidate_version = trial["candidate_version"]
    trial_dir = trial_output_dir / candidate_version
    report_output = trial_dir / "source-report-generation.json"
    artifact_generation_output = trial_dir / "artifact-generation.json"
    validator_output = trial_dir / "validator.json"
    strategy_parameters = dict(trial["strategy_parameters"])
    artifact_publication_allowed = bool(
        trial.get("artifact_publication_allowed", True)
    )
    active_artifact_dir = (
        artifact_dir
        if artifact_publication_allowed
        else trial_dir / "screening-artifacts"
    )
    active_validator_output = (
        validator_output
        if artifact_publication_allowed
        else trial_dir / "screening-validator.json"
    )
    fast_window = int(strategy_parameters.get("fast_window", 1))
    slow_window = int(strategy_parameters.get("slow_window", 2))
    if slow_window <= fast_window:
        slow_window = fast_window + 1
    trial_monte_carlo_trades = (
        int(min_monte_carlo_trades)
        if min_monte_carlo_trades is not None
        else int(trial["window"]["min_trade_count"])
    )

    try:
        report = run_strategy_validation_source_report_generation(
            source_paths=[trial["input_path"]],
            strategy_id=trial["strategy_id"],
            candidate_version=candidate_version,
            symbol="BTCUSDT",
            timeframe=trial["timeframe"],
            input_kind="klines",
            output_dir=source_report_dir,
            report_output_path=report_output,
            timestamp=generated_at,
            holdout_ratio=float(trial["window"]["holdout_ratio"]),
            min_train_bars=min_train_bars,
            min_holdout_bars=min_holdout_bars,
            min_trades=int(trial["window"]["min_trade_count"]),
            initial_balance=initial_balance,
            fast_window=fast_window,
            slow_window=slow_window,
            strategy_parameters=strategy_parameters,
            overwrite_existing_source_report=overwrite_existing_source_reports,
            generation_kind="aggregate",
            train_bars=int(trial["window"]["train_bars"]),
            test_bars=int(trial["window"]["test_bars"]),
            step_bars=int(trial["window"]["step_bars"]),
            min_walk_forward_windows=min_walk_forward_windows,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            monte_carlo_run_count=monte_carlo_run_count,
            monte_carlo_seed=monte_carlo_seed,
            min_monte_carlo_trades=trial_monte_carlo_trades,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
            monte_carlo_max_drawdown_limit=monte_carlo_max_drawdown_limit,
            artifact_dir=active_artifact_dir,
            artifact_generation_output_path=artifact_generation_output,
            validator_output_path=active_validator_output,
            require_gate_pass=require_gate_pass,
            min_net_pnl=min_net_pnl,
            max_drawdown=max_drawdown,
            min_win_rate=min_win_rate,
            min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
            overwrite_existing_artifacts=overwrite_existing_artifacts,
        )
    except Exception as exc:
        report = {
            "status": "FAIL",
            "success": False,
            "message": str(exc),
            "reason_codes": ["expanded_public_btcusdt_search_trial_failed"],
            "safety_flags": _default_safety_flags(),
            "results": [],
            "artifact_paths": [],
            "generated_artifact_count": 0,
            "validator_status": None,
            "h_opt_005_ready": False,
            "h_opt_005_blockers": ["expanded_public_btcusdt_search_trial_failed"],
        }

    return _summarize_trial(
        index=index,
        candidate_version=candidate_version,
        trial=trial,
        report=report,
        report_output_path=report_output,
        artifact_generation_output_path=artifact_generation_output,
        validator_output_path=active_validator_output,
    )


def _summarize_trial(
    *,
    index: int,
    candidate_version: str,
    trial: dict[str, Any],
    report: dict[str, Any],
    report_output_path: Path,
    artifact_generation_output_path: Path,
    validator_output_path: Path,
) -> dict[str, Any]:
    results = report.get("results") if isinstance(report.get("results"), list) else []
    component_kinds = ["out_of_sample", "walk_forward", "monte_carlo"]
    components = [
        _summarize_component(kind, result)
        for kind, result in zip(component_kinds, results)
        if isinstance(result, dict)
    ]
    artifact_publication_allowed = bool(
        trial.get("artifact_publication_allowed", True)
    )
    artifact_paths = list(report.get("artifact_paths") or [])
    generated_artifact_count = int(report.get("generated_artifact_count") or 0)
    return {
        "trial_index": index,
        "candidate_version": candidate_version,
        "base_candidate_version": _base_candidate_version(trial),
        "phase_candidate_version": candidate_version,
        "promoted_candidate_version": trial.get("promoted_candidate_version"),
        "phase": trial.get("phase", "confirm"),
        "phase_role": trial.get("phase_role", PHASE_ROLES["confirm"]),
        "artifact_publication_allowed": artifact_publication_allowed,
        "monte_carlo_seed": trial.get("monte_carlo_seed"),
        "monte_carlo_seed_policy": trial.get("monte_carlo_seed_policy"),
        "independent_confirm_recheck": (
            dict(trial.get("independent_confirm_recheck"))
            if isinstance(trial.get("independent_confirm_recheck"), dict)
            else None
        ),
        "timeframe": trial["timeframe"],
        "input_path": str(trial["input_path"]),
        "bar_count": trial.get("bar_count"),
        "data_fingerprint": trial["data_fingerprint"],
        "strategy_id": trial["strategy_id"],
        "strategy_parameters": dict(trial["strategy_parameters"]),
        "strategy_metadata": dict(trial.get("strategy_metadata") or {}),
        "window": dict(trial["window"]),
        "status": report.get("status"),
        "success": bool(report.get("success")),
        "message": report.get("message"),
        "reason_codes": list(report.get("reason_codes") or []),
        "safety_flags": report.get("safety_flags") or _default_safety_flags(),
        "report_output_path": str(report_output_path),
        "artifact_generation_output_path": str(artifact_generation_output_path),
        "validator_output_path": str(validator_output_path),
        "aggregate_source_report_path": report.get("aggregate_source_report_path"),
        "source_report_paths": list(report.get("source_report_paths") or []),
        "artifact_paths": artifact_paths if artifact_publication_allowed else [],
        "screening_artifact_paths": (
            [] if artifact_publication_allowed else artifact_paths
        ),
        "generated_artifact_count": (
            generated_artifact_count if artifact_publication_allowed else 0
        ),
        "screening_artifact_count": (
            0 if artifact_publication_allowed else generated_artifact_count
        ),
        "validator_status": report.get("validator_status"),
        "h_opt_005_ready": bool(report.get("h_opt_005_ready")),
        "h_opt_005_blockers": list(report.get("h_opt_005_blockers") or []),
        "components": components,
        "metrics": _trial_metrics_from_components(components),
        "overfit_diagnostics": _trial_overfit_diagnostics(
            {
                "strategy_parameters": trial.get("strategy_parameters"),
                "metrics": _trial_metrics_from_components(components),
                "components": components,
                "phase": trial.get("phase", "confirm"),
                "status": report.get("status"),
            }
        ),
    }


def _summarize_component(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    source_report = (
        result.get("source_report")
        if isinstance(result.get("source_report"), dict)
        else {}
    )
    summary = (
        source_report.get("summary")
        if isinstance(source_report.get("summary"), dict)
        else {}
    )
    entry = {
        "kind": kind,
        "status": result.get("status"),
        "message": result.get("message"),
        "reason_codes": list(result.get("reason_codes") or []),
        "source_report_path": result.get("source_report_path"),
        "source_report_id": result.get("source_report_id"),
        "trade_count": _coerce_int(
            metrics.get("trade_count", summary.get("trade_count"))
        ),
        "total_net_pnl": _coerce_float(
            metrics.get("total_net_pnl", summary.get("total_net_pnl"))
        ),
        "max_drawdown": _coerce_float(
            metrics.get("max_drawdown", summary.get("max_drawdown"))
        ),
        "win_rate": _coerce_float(metrics.get("win_rate", summary.get("win_rate"))),
        "sharpe_ratio": _coerce_float(
            metrics.get("sharpe_ratio", summary.get("sharpe_ratio"))
        ),
    }
    if kind == "walk_forward":
        walk_forward = (
            metrics.get("walk_forward")
            if isinstance(metrics.get("walk_forward"), dict)
            else source_report
        )
        entry.update(
            {
                "walk_forward_window_count": _coerce_int(
                    walk_forward.get("walk_forward_window_count")
                ),
                "walk_forward_pass_count": _coerce_int(
                    walk_forward.get("walk_forward_pass_count")
                ),
                "walk_forward_pass_rate": _coerce_float(
                    walk_forward.get("walk_forward_pass_rate")
                ),
            }
        )
    if kind == "monte_carlo":
        entry.update(
            {
                "monte_carlo_survival_rate": _coerce_float(
                    metrics.get(
                        "monte_carlo_survival_rate",
                        source_report.get("monte_carlo_survival_rate"),
                    )
                ),
                "monte_carlo_run_pass_count": _coerce_int(
                    metrics.get("monte_carlo_run_pass_count")
                ),
                "monte_carlo_run_fail_count": _coerce_int(
                    metrics.get("monte_carlo_run_fail_count")
                ),
            }
        )
    return entry


def _trial_metrics_from_components(components: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind = {entry.get("kind"): entry for entry in components}
    oos = by_kind.get("out_of_sample") or {}
    walk_forward = by_kind.get("walk_forward") or {}
    monte_carlo = by_kind.get("monte_carlo") or {}
    return {
        "oos_trade_count": oos.get("trade_count"),
        "oos_total_net_pnl": oos.get("total_net_pnl"),
        "walk_forward_window_count": walk_forward.get("walk_forward_window_count"),
        "walk_forward_pass_count": walk_forward.get("walk_forward_pass_count"),
        "walk_forward_pass_rate": walk_forward.get("walk_forward_pass_rate"),
        "monte_carlo_survival_rate": monte_carlo.get("monte_carlo_survival_rate"),
    }


def _trial_score(trial: dict[str, Any]) -> tuple[Any, ...]:
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
    diagnostics = (
        trial.get("overfit_diagnostics")
        if isinstance(trial.get("overfit_diagnostics"), dict)
        else {}
    )
    return (
        1 if trial.get("status") == "PASS" else 0,
        1 if trial.get("validator_status") == "PASS" else 0,
        int(trial.get("generated_artifact_count") or 0),
        _score_float(metrics.get("monte_carlo_survival_rate")),
        _score_float(metrics.get("walk_forward_pass_rate")),
        _score_int(metrics.get("walk_forward_pass_count")),
        _score_float(metrics.get("oos_total_net_pnl")),
        -(_coerce_float(diagnostics.get("parameter_complexity_penalty")) or 0.0),
        -len(trial.get("reason_codes") or []),
        -int(trial.get("trial_index") or 0),
    )


def _walk_forward_rescue_score(trial: dict[str, Any]) -> tuple[Any, ...]:
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
    return (
        _score_float(metrics.get("walk_forward_pass_rate")),
        _score_int(metrics.get("walk_forward_pass_count")),
        _score_float(metrics.get("monte_carlo_survival_rate")),
        _score_float(metrics.get("oos_total_net_pnl")),
        -len(trial.get("reason_codes") or []),
        -int(trial.get("trial_index") or 0),
    )


def _walk_forward_threshold_gap_selection_score(
    trial: dict[str, Any],
    *,
    min_walk_forward_pass_rate: float,
) -> tuple[Any, ...]:
    walk_forward_pass_rate = _trial_metric_float(trial, "walk_forward_pass_rate")
    if walk_forward_pass_rate is None:
        return (3, float("inf"), *_walk_forward_rescue_score(trial))
    gap = max(0.0, min_walk_forward_pass_rate - float(walk_forward_pass_rate))
    bucket_order = {
        "near_miss": 0,
        "moderate_gap": 1,
        "large_gap": 2,
    }
    return (
        bucket_order.get(_walk_forward_threshold_gap_bucket(gap), 3),
        round(gap, 10),
        -_score_int(_trial_metric_int(trial, "walk_forward_pass_count")),
        -_score_float(_trial_metric_float(trial, "monte_carlo_survival_rate")),
        -_score_float(_trial_metric_float(trial, "oos_total_net_pnl")),
        len(trial.get("reason_codes") or []),
        int(trial.get("trial_index") or 0),
    )


def _collect_reason_codes(trials: list[dict[str, Any]]) -> list[str]:
    reason_codes = []
    for trial in trials:
        reason_codes.extend(trial.get("reason_codes") or [])
        for component in trial.get("components") or []:
            reason_codes.extend(component.get("reason_codes") or [])
    return sorted(set(reason_codes))


def _candidate_version(
    *,
    symbol: str,
    timeframe: str,
    strategy_id: str,
    data_fingerprint: dict[str, Any],
    strategy_parameters: dict[str, Any],
    window: dict[str, Any],
    phase: str = "confirm",
) -> str:
    digest = str(data_fingerprint.get("sha256") or "missing")[:12]
    holdout_token = str(int(round(float(window["holdout_ratio"]) * 100)))
    parameter_digest = _dict_digest(strategy_parameters)[:10]
    return _safe_token(
        f"expanded-public-{symbol.lower()}-{phase}-{timeframe}-{strategy_id}-"
        f"data-{digest}-p{parameter_digest}-"
        f"tr{window['train_bars']}-te{window['test_bars']}-"
        f"st{window['step_bars']}-ho{holdout_token}-"
        f"mt{window['min_trade_count']}"
    )


def _base_candidate_version(trial: dict[str, Any]) -> str:
    return _candidate_version(
        symbol=str(trial.get("symbol") or "BTCUSDT"),
        timeframe=trial["timeframe"],
        strategy_id=trial["strategy_id"],
        data_fingerprint=trial["data_fingerprint"],
        strategy_parameters=trial["strategy_parameters"],
        window=trial["window"],
        phase="base",
    )


def _legacy_candidate_version(trial: dict[str, Any]) -> str:
    digest = str((trial.get("data_fingerprint") or {}).get("sha256") or "missing")[:12]
    window = trial["window"]
    holdout_token = str(int(round(float(window["holdout_ratio"]) * 100)))
    parameter_digest = _dict_digest(dict(trial.get("strategy_parameters") or {}))[:10]
    return _safe_token(
        f"expanded-public-{str(trial.get('symbol') or 'BTCUSDT').lower()}-"
        f"{trial['timeframe']}-{trial['strategy_id']}-"
        f"data-{digest}-p{parameter_digest}-"
        f"tr{window['train_bars']}-te{window['test_bars']}-"
        f"st{window['step_bars']}-ho{holdout_token}-"
        f"mt{window['min_trade_count']}"
    )


def _base_trial_key(trial: dict[str, Any]) -> str:
    return "|".join(
        [
            str(trial.get("timeframe")),
            str(trial.get("strategy_id")),
            _dict_digest(dict(trial.get("strategy_parameters") or {})),
            _dict_digest(dict(trial.get("window") or {})),
        ]
    )


def _phase_monte_carlo_seed(*, base_seed: int, phase: str, phase_index: int) -> int:
    if phase == "confirm":
        return int(base_seed) + 10000 + phase_index
    return int(base_seed) + phase_index


def _phase_monte_carlo_seed_policy(phase: str) -> str:
    if phase == "confirm":
        return "independent_confirm_seed"
    return "phase_screen_seed"


def _phase_selection_policy(phase: str) -> str:
    if phase == "coarse":
        return "wide_grid_screen"
    if phase == "fine":
        return "score_and_walk_forward_ranked_neighbor_expansion"
    if phase == "confirm":
        return "promoted_candidate_independent_recheck_with_walk_forward_rescue"
    return "unknown"


def _phase_window_profile_selection_summary(
    *,
    phase: str,
    planned_trials: list[dict[str, Any]],
    completed_trials: list[dict[str, Any]],
    failed_window_profile_keys: set[str] | None,
) -> dict[str, Any]:
    failed_keys = set(failed_window_profile_keys or set())
    planned_keys = [_window_profile_key(trial) for trial in planned_trials]
    completed_keys = [_window_profile_key(trial) for trial in completed_trials]
    avoided_planned_count = sum(1 for key in planned_keys if key not in failed_keys)
    repeated_planned_count = sum(1 for key in planned_keys if key in failed_keys)
    return {
        "phase": phase,
        "failed_window_profile_keys_considered": sorted(failed_keys),
        "failed_window_profile_count_considered": len(failed_keys),
        "planned_trial_count": len(planned_trials),
        "planned_avoided_failed_window_profile_count": avoided_planned_count,
        "planned_repeated_failed_window_profile_count": repeated_planned_count,
        "completed_trial_count": len(completed_trials),
        "completed_repeated_failed_window_profile_count": sum(
            1 for key in completed_keys if key in failed_keys
        ),
        "avoids_repeating_failed_window_profiles": (
            not failed_keys or repeated_planned_count == 0
        ),
    }


def _phase_base_trial_repeat_summary(
    *,
    phase: str,
    planned_trials: list[dict[str, Any]],
    completed_trials: list[dict[str, Any]],
    completed_base_trial_keys: set[str] | None,
) -> dict[str, Any]:
    completed_keys = set(completed_base_trial_keys or set())
    planned_keys = [_base_trial_key(trial) for trial in planned_trials]
    completed_phase_keys = [_base_trial_key(trial) for trial in completed_trials]
    planned_repeated_count = sum(1 for key in planned_keys if key in completed_keys)
    completed_repeated_count = sum(
        1 for key in completed_phase_keys if key in completed_keys
    )
    return {
        "phase": phase,
        "completed_base_trial_count_considered": len(completed_keys),
        "planned_trial_count": len(planned_trials),
        "planned_new_base_trial_count": len(planned_trials) - planned_repeated_count,
        "planned_repeated_base_trial_count": planned_repeated_count,
        "completed_trial_count": len(completed_trials),
        "completed_repeated_base_trial_count": completed_repeated_count,
        "avoids_completed_base_trial_repeats": (
            not completed_keys or planned_repeated_count == 0
        ),
    }


def _walk_forward_window_profile_selection_summary(
    phase_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    phase_entries = []
    failed_keys: set[str] = set()
    for phase_summary in phase_summaries:
        entry = (
            phase_summary.get("window_profile_selection_summary")
            if isinstance(
                phase_summary.get("window_profile_selection_summary"),
                dict,
            )
            else None
        )
        if not entry:
            continue
        phase_entries.append(entry)
        failed_keys.update(entry.get("failed_window_profile_keys_considered") or [])
    planned_count = sum(int(entry.get("planned_trial_count") or 0) for entry in phase_entries)
    repeated_count = sum(
        int(entry.get("planned_repeated_failed_window_profile_count") or 0)
        for entry in phase_entries
    )
    return {
        "task_scope": "H-OPT-019",
        "phase_count": len(phase_entries),
        "failed_window_profile_keys_considered": sorted(failed_keys),
        "failed_window_profile_count_considered": len(failed_keys),
        "planned_trial_count": planned_count,
        "planned_avoided_failed_window_profile_count": sum(
            int(entry.get("planned_avoided_failed_window_profile_count") or 0)
            for entry in phase_entries
        ),
        "planned_repeated_failed_window_profile_count": repeated_count,
        "completed_repeated_failed_window_profile_count": sum(
            int(entry.get("completed_repeated_failed_window_profile_count") or 0)
            for entry in phase_entries
        ),
        "avoids_repeating_failed_window_profiles": repeated_count == 0,
        "by_phase": phase_entries,
    }


def _confirm_phase_rescue_memory_summary(
    trials: list[dict[str, Any]],
    *,
    resume_history_trials: list[dict[str, Any]] | None = None,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> dict[str, Any]:
    merged_trials_by_version: dict[str, dict[str, Any]] = {}
    for trial in list(resume_history_trials or []) + list(trials):
        if not isinstance(trial, dict):
            continue
        merged_trials_by_version[_promotion_candidate_version(trial)] = trial
    confirm_oos_mc_failures = [
        trial
        for trial in merged_trials_by_version.values()
        if trial.get("phase") == "confirm"
        and _oos_and_monte_carlo_pass(
            trial,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        )
        and _trial_metric_float(trial, "walk_forward_pass_rate") is not None
        and _trial_metric_float(trial, "walk_forward_pass_rate")
        < min_walk_forward_pass_rate
    ]
    gap_counts = {"near_miss": 0, "moderate_gap": 0, "large_gap": 0}
    by_profile: dict[str, dict[str, Any]] = {}
    for trial in confirm_oos_mc_failures:
        bucket = _walk_forward_rescue_gap_bucket(
            trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        )
        if bucket in gap_counts:
            gap_counts[bucket] += 1
        profile_key = _window_profile_key(trial)
        entry = by_profile.setdefault(
            profile_key,
            {
                "window_profile_key": profile_key,
                "window": _window_profile_snapshot(trial),
                "candidate_count": 0,
                "best_walk_forward_pass_rate": None,
                "best_walk_forward_pass_count": None,
                "best_candidate_version": None,
                "gap_buckets": {"near_miss": 0, "moderate_gap": 0, "large_gap": 0},
            },
        )
        entry["candidate_count"] += 1
        if bucket in entry["gap_buckets"]:
            entry["gap_buckets"][bucket] += 1
        current_rate = _trial_metric_float(trial, "walk_forward_pass_rate")
        current_count = _trial_metric_int(trial, "walk_forward_pass_count")
        best_rate = _coerce_float(entry.get("best_walk_forward_pass_rate"))
        if best_rate is None or _score_float(current_rate) > _score_float(best_rate):
            entry["best_walk_forward_pass_rate"] = current_rate
            entry["best_walk_forward_pass_count"] = current_count
            entry["best_candidate_version"] = _promotion_candidate_version(trial)
    ranked_profiles = sorted(
        by_profile.values(),
        key=lambda entry: (
            -int(entry["gap_buckets"].get("near_miss") or 0),
            -int(entry["gap_buckets"].get("moderate_gap") or 0),
            -_score_float(entry.get("best_walk_forward_pass_rate")),
            -int(entry.get("candidate_count") or 0),
            str(entry.get("window_profile_key") or ""),
        ),
    )
    return {
        "task_scope": "H-OPT-019",
        "uses_confirm_phase_rescue_memory": True,
        "confirm_oos_mc_walk_forward_failure_count": len(confirm_oos_mc_failures),
        "gap_bucket_counts": gap_counts,
        "moderate_gap_profile_count": sum(
            1 for entry in ranked_profiles if entry["gap_buckets"]["moderate_gap"]
        ),
        "rescue_profile_count": len(ranked_profiles),
        "recommended_profile_focus": (
            "reuse_best_moderate_gap_confirm_profiles_for_parameter_neighbor_rescue"
            if gap_counts["moderate_gap"]
            else "collect_more_confirm_oos_mc_walk_forward_failure_profiles"
            if not confirm_oos_mc_failures
            else "prefer_near_miss_profiles_then_best_moderate_gap_profiles"
        ),
        "best_rescue_profiles": ranked_profiles[:10],
        "does_not_lower_gate_thresholds": True,
    }


def _base_trial_repeat_selection_summary(
    phase_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    phase_entries = []
    considered_count = 0
    for phase_summary in phase_summaries:
        entry = (
            phase_summary.get("base_trial_repeat_selection_summary")
            if isinstance(
                phase_summary.get("base_trial_repeat_selection_summary"),
                dict,
            )
            else None
        )
        if not entry:
            continue
        phase_entries.append(entry)
        considered_count = max(
            considered_count,
            int(entry.get("completed_base_trial_count_considered") or 0),
        )
    planned_count = sum(int(entry.get("planned_trial_count") or 0) for entry in phase_entries)
    repeated_count = sum(
        int(entry.get("planned_repeated_base_trial_count") or 0)
        for entry in phase_entries
    )
    return {
        "task_scope": "H-OPT-019",
        "phase_count": len(phase_entries),
        "completed_base_trial_count_considered": considered_count,
        "planned_trial_count": planned_count,
        "planned_new_base_trial_count": sum(
            int(entry.get("planned_new_base_trial_count") or 0)
            for entry in phase_entries
        ),
        "planned_repeated_base_trial_count": repeated_count,
        "completed_repeated_base_trial_count": sum(
            int(entry.get("completed_repeated_base_trial_count") or 0)
            for entry in phase_entries
        ),
        "avoids_completed_base_trial_repeats": repeated_count == 0,
        "by_phase": phase_entries,
    }


def _confirm_recheck_summary(trials: list[dict[str, Any]]) -> dict[str, Any]:
    rechecks = [
        trial.get("independent_confirm_recheck")
        for trial in trials
        if isinstance(trial.get("independent_confirm_recheck"), dict)
    ]
    return {
        "trial_count": len(trials),
        "recheck_trial_count": len(rechecks),
        "independent_holdout_ratio_count": sum(
            1 for item in rechecks if item.get("independent_holdout_ratio") is True
        ),
        "independent_walk_forward_window_count": sum(
            1
            for item in rechecks
            if item.get("independent_walk_forward_window") is True
        ),
        "dual_independent_holdout_window_count": sum(
            1 for item in rechecks if _is_dual_independent_recheck(item)
        ),
        "independent_monte_carlo_seed_count": sum(
            1 for item in rechecks if item.get("independent_monte_carlo_seed") is True
        ),
        "strategy_parameter_neighbor_count": sum(
            1 for item in rechecks if item.get("strategy_parameter_neighbor") is True
        ),
    }


def _latest_confirm_recheck_summary(
    phase_summaries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for summary in reversed(phase_summaries):
        if summary.get("phase") == "confirm":
            return summary.get("confirm_recheck_summary")
    return None


def _trial_overfit_diagnostics(trial: dict[str, Any]) -> dict[str, Any]:
    parameters = (
        trial.get("strategy_parameters")
        if isinstance(trial.get("strategy_parameters"), dict)
        else {}
    )
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
    parameter_count = len(parameters)
    oos_total_net_pnl = _coerce_float(metrics.get("oos_total_net_pnl"))
    walk_forward_pass_rate = _coerce_float(metrics.get("walk_forward_pass_rate"))
    monte_carlo_survival_rate = _coerce_float(metrics.get("monte_carlo_survival_rate"))
    available_metric_count = sum(
        value is not None
        for value in (
            oos_total_net_pnl,
            walk_forward_pass_rate,
            monte_carlo_survival_rate,
        )
    )
    return {
        "phase": trial.get("phase", "confirm"),
        "parameter_count": parameter_count,
        "parameter_complexity_penalty": round(parameter_count * 0.01, 6),
        "oos_total_net_pnl": oos_total_net_pnl,
        "walk_forward_pass_rate": walk_forward_pass_rate,
        "monte_carlo_survival_rate": monte_carlo_survival_rate,
        "diagnostic_metric_count": available_metric_count,
        "gate_evidence_complete": (
            oos_total_net_pnl is not None
            and walk_forward_pass_rate is not None
            and monte_carlo_survival_rate is not None
        ),
    }


def _overfit_diagnostics(
    *,
    trials: list[dict[str, Any]],
    top_candidates: list[dict[str, Any]],
    top_k: int,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> dict[str, Any]:
    trial_count = len(trials)
    parameter_counts = [
        int(
            (
                trial.get("overfit_diagnostics")
                if isinstance(trial.get("overfit_diagnostics"), dict)
                else {}
            ).get("parameter_count")
            or 0
        )
        for trial in trials
    ]
    scores = [_numeric_trial_score(trial) for trial in trials]
    best_score = max(scores) if scores else None
    median_score = _median(scores) if scores else None
    best_trial = top_candidates[0] if top_candidates else None
    return {
        "trial_count": trial_count,
        "parameter_count": {
            "min": min(parameter_counts) if parameter_counts else 0,
            "max": max(parameter_counts) if parameter_counts else 0,
            "median": _median(parameter_counts) if parameter_counts else 0,
        },
        "best_score": best_score,
        "median_score": median_score,
        "best_vs_median_gap": (
            None
            if best_score is None or median_score is None
            else round(best_score - median_score, 10)
        ),
        "neighbor_stability": _neighbor_stability(
            trials=trials,
            best_trial=best_trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        ),
        "timeframe_consistency": _timeframe_consistency(
            trials=trials,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        ),
        "seed_consistency": _seed_consistency(trials),
        "walk_forward_failure_analysis": _walk_forward_failure_analysis(
            trials=trials,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        ),
        "walk_forward_threshold_gap_analysis": _walk_forward_threshold_gap_analysis(
            trials=trials,
            top_k=top_k,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        ),
        "phase_counts": _count_by_key(trials, "phase"),
        "diagnostic_reason_codes": _overfit_reason_codes(
            trials=trials,
            best_score=best_score,
            median_score=median_score,
        ),
    }


def _numeric_trial_score(trial: dict[str, Any]) -> float:
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
    diagnostics = (
        trial.get("overfit_diagnostics")
        if isinstance(trial.get("overfit_diagnostics"), dict)
        else {}
    )
    score = 0.0
    if trial.get("status") == "PASS":
        score += 1000.0
    score += _score_float(metrics.get("monte_carlo_survival_rate")) * 100.0
    score += _score_float(metrics.get("walk_forward_pass_rate")) * 100.0
    score += _score_float(metrics.get("oos_total_net_pnl"))
    score -= _coerce_float(diagnostics.get("parameter_complexity_penalty")) or 0.0
    return score


def _neighbor_stability(
    *,
    trials: list[dict[str, Any]],
    best_trial: dict[str, Any] | None,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> dict[str, Any]:
    if not best_trial:
        return {"neighbor_count": 0, "stable_neighbor_count": 0, "stable_ratio": None}
    neighbors = [
        trial
        for trial in trials
        if trial.get("strategy_id") == best_trial.get("strategy_id")
        and trial.get("timeframe") == best_trial.get("timeframe")
        and trial.get("candidate_version") != best_trial.get("candidate_version")
    ]
    stable_count = sum(
        1
        for trial in neighbors
        if _passes_metric_floor(
            trial,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        )
    )
    return {
        "neighbor_count": len(neighbors),
        "stable_neighbor_count": stable_count,
        "stable_ratio": (
            None if not neighbors else round(stable_count / len(neighbors), 6)
        ),
    }


def _timeframe_consistency(
    *,
    trials: list[dict[str, Any]],
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> dict[str, Any]:
    by_timeframe: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        by_timeframe.setdefault(str(trial.get("timeframe")), []).append(trial)
    entries = {}
    passing_timeframes = 0
    for timeframe, timeframe_trials in sorted(by_timeframe.items()):
        stable_count = sum(
            1
            for trial in timeframe_trials
            if _passes_metric_floor(
                trial,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
            )
        )
        if stable_count:
            passing_timeframes += 1
        entries[timeframe] = {
            "trial_count": len(timeframe_trials),
            "stable_candidate_count": stable_count,
        }
    return {
        "timeframe_count": len(by_timeframe),
        "passing_timeframe_count": passing_timeframes,
        "by_timeframe": entries,
    }


def _seed_consistency(trials: list[dict[str, Any]]) -> dict[str, Any]:
    confirm_trials = [trial for trial in trials if trial.get("phase") == "confirm"]
    survival_rates = [
        _coerce_float((trial.get("metrics") or {}).get("monte_carlo_survival_rate"))
        for trial in confirm_trials
        if isinstance(trial.get("metrics"), dict)
    ]
    survival_rates = [rate for rate in survival_rates if rate is not None]
    return {
        "confirm_trial_count": len(confirm_trials),
        "monte_carlo_survival_rate_min": (
            min(survival_rates) if survival_rates else None
        ),
        "monte_carlo_survival_rate_max": (
            max(survival_rates) if survival_rates else None
        ),
    }


def _walk_forward_failure_analysis(
    *,
    trials: list[dict[str, Any]],
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> dict[str, Any]:
    evidence_trials = [
        trial
        for trial in trials
        if _trial_metric_float(trial, "walk_forward_pass_rate") is not None
    ]
    rates = [
        _trial_metric_float(trial, "walk_forward_pass_rate")
        for trial in evidence_trials
    ]
    rates = [rate for rate in rates if rate is not None]
    below_threshold = [
        trial
        for trial in evidence_trials
        if (
            _trial_metric_float(trial, "walk_forward_pass_rate") is not None
            and _trial_metric_float(trial, "walk_forward_pass_rate")
            < min_walk_forward_pass_rate
        )
    ]
    passing_threshold = [
        trial
        for trial in evidence_trials
        if (
            _trial_metric_float(trial, "walk_forward_pass_rate") is not None
            and _trial_metric_float(trial, "walk_forward_pass_rate")
            >= min_walk_forward_pass_rate
        )
    ]
    oos_mc_pass_but_walk_forward_fail = [
        trial
        for trial in below_threshold
        if _oos_and_monte_carlo_pass(
            trial,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        )
    ]
    return {
        "min_walk_forward_pass_rate": min_walk_forward_pass_rate,
        "min_monte_carlo_survival_rate": min_monte_carlo_survival_rate,
        "trial_count": len(trials),
        "evidence_trial_count": len(evidence_trials),
        "missing_evidence_trial_count": len(trials) - len(evidence_trials),
        "below_threshold_count": len(below_threshold),
        "passing_threshold_count": len(passing_threshold),
        "below_threshold_ratio": (
            None
            if not evidence_trials
            else round(len(below_threshold) / len(evidence_trials), 6)
        ),
        "zero_pass_window_count": sum(
            1
            for trial in evidence_trials
            if (_trial_metric_int(trial, "walk_forward_pass_count") or 0) == 0
        ),
        "oos_mc_pass_but_walk_forward_fail_count": len(
            oos_mc_pass_but_walk_forward_fail
        ),
        "pass_rate_summary": _numeric_distribution(rates),
        "by_phase": _walk_forward_group_summary(
            evidence_trials,
            group_key="phase",
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        ),
        "by_timeframe": _walk_forward_group_summary(
            evidence_trials,
            group_key="timeframe",
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        ),
        "by_strategy_id": _walk_forward_group_summary(
            evidence_trials,
            group_key="strategy_id",
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        ),
        "by_window_profile": _walk_forward_window_profile_summary(
            evidence_trials,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        ),
        "failed_window_profile_keys": sorted(
            _walk_forward_failed_window_profile_keys(
                evidence_trials,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            )
        ),
        "top_failure_reason_codes": _walk_forward_failure_reason_counts(
            below_threshold
        ),
        "best_failed_candidate": _walk_forward_trial_snapshot(
            _best_walk_forward_trial(below_threshold)
        ),
        "best_overall_candidate_by_walk_forward": _walk_forward_trial_snapshot(
            _best_walk_forward_trial(evidence_trials)
        ),
        "diagnostic_reason_codes": _walk_forward_failure_reason_codes(
            trials=trials,
            evidence_trials=evidence_trials,
            below_threshold=below_threshold,
            oos_mc_pass_but_walk_forward_fail=oos_mc_pass_but_walk_forward_fail,
        ),
    }


def _walk_forward_group_summary(
    trials: list[dict[str, Any]],
    *,
    group_key: str,
    min_walk_forward_pass_rate: float,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        grouped.setdefault(str(trial.get(group_key) or "unknown"), []).append(trial)

    entries: dict[str, dict[str, Any]] = {}
    for key, group_trials in sorted(grouped.items()):
        rates = [
            _trial_metric_float(trial, "walk_forward_pass_rate")
            for trial in group_trials
        ]
        rates = [rate for rate in rates if rate is not None]
        below_threshold_count = sum(
            1 for rate in rates if rate < min_walk_forward_pass_rate
        )
        best_trial = _best_walk_forward_trial(group_trials)
        entries[key] = {
            "trial_count": len(group_trials),
            "below_threshold_count": below_threshold_count,
            "passing_threshold_count": len(rates) - below_threshold_count,
            "pass_rate_summary": _numeric_distribution(rates),
            "best_candidate_version": (
                best_trial.get("candidate_version")
                if isinstance(best_trial, dict)
                else None
            ),
            "best_walk_forward_pass_rate": (
                _trial_metric_float(best_trial, "walk_forward_pass_rate")
                if isinstance(best_trial, dict)
                else None
            ),
        }
    return entries


def _walk_forward_window_profile_summary(
    trials: list[dict[str, Any]],
    *,
    min_walk_forward_pass_rate: float,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        grouped.setdefault(_window_profile_key(trial), []).append(trial)

    entries: dict[str, dict[str, Any]] = {}
    for key, group_trials in sorted(grouped.items()):
        rates = [
            _trial_metric_float(trial, "walk_forward_pass_rate")
            for trial in group_trials
        ]
        rates = [rate for rate in rates if rate is not None]
        below_threshold_count = sum(
            1 for rate in rates if rate < min_walk_forward_pass_rate
        )
        best_trial = _best_walk_forward_trial(group_trials)
        entries[key] = {
            "window": _window_profile_snapshot(group_trials[0]),
            "trial_count": len(group_trials),
            "below_threshold_count": below_threshold_count,
            "passing_threshold_count": len(rates) - below_threshold_count,
            "all_trials_below_threshold": bool(rates)
            and below_threshold_count == len(rates),
            "pass_rate_summary": _numeric_distribution(rates),
            "best_candidate_version": (
                best_trial.get("candidate_version")
                if isinstance(best_trial, dict)
                else None
            ),
            "best_walk_forward_pass_rate": (
                _trial_metric_float(best_trial, "walk_forward_pass_rate")
                if isinstance(best_trial, dict)
                else None
            ),
        }
    return entries


def _walk_forward_failed_window_profile_keys(
    trials: list[dict[str, Any]],
    *,
    min_walk_forward_pass_rate: float,
) -> set[str]:
    entries = _walk_forward_window_profile_summary(
        [
            trial
            for trial in trials
            if _trial_metric_float(trial, "walk_forward_pass_rate") is not None
        ],
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
    )
    return {
        key
        for key, entry in entries.items()
        if entry.get("all_trials_below_threshold") is True
    }


def _window_profile_key(trial: dict[str, Any]) -> str:
    return _dict_digest(_window_profile_snapshot(trial))[:12]


def _window_profile_snapshot(trial: dict[str, Any]) -> dict[str, Any]:
    window = trial.get("window") if isinstance(trial.get("window"), dict) else {}
    return {
        "train_bars": _coerce_int(window.get("train_bars")),
        "test_bars": _coerce_int(window.get("test_bars")),
        "step_bars": _coerce_int(window.get("step_bars")),
        "holdout_ratio": _coerce_float(window.get("holdout_ratio")),
        "min_trade_count": _coerce_int(window.get("min_trade_count")),
    }


def _walk_forward_failure_reason_counts(
    trials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for trial in trials:
        reason_codes = list(trial.get("reason_codes") or [])
        reason_codes.extend(trial.get("h_opt_005_blockers") or [])
        for component in trial.get("components") or []:
            if isinstance(component, dict) and component.get("kind") == "walk_forward":
                reason_codes.extend(component.get("reason_codes") or [])
        for reason_code in set(reason_codes):
            key = str(reason_code)
            counts[key] = counts.get(key, 0) + 1
    return [
        {"reason_code": reason_code, "count": count}
        for reason_code, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:10]
    ]


def _walk_forward_failure_reason_codes(
    *,
    trials: list[dict[str, Any]],
    evidence_trials: list[dict[str, Any]],
    below_threshold: list[dict[str, Any]],
    oos_mc_pass_but_walk_forward_fail: list[dict[str, Any]],
) -> list[str]:
    reason_codes = []
    if not trials:
        reason_codes.append("no_trials_for_walk_forward_analysis")
    if trials and not evidence_trials:
        reason_codes.append("walk_forward_evidence_missing")
    if below_threshold and len(below_threshold) == len(evidence_trials):
        reason_codes.append("all_walk_forward_trials_below_threshold")
    elif below_threshold:
        reason_codes.append("some_walk_forward_trials_below_threshold")
    if oos_mc_pass_but_walk_forward_fail:
        reason_codes.append("walk_forward_gate_failed_despite_oos_and_monte_carlo")
    return sorted(set(reason_codes))


def _walk_forward_threshold_gap_analysis(
    *,
    trials: list[dict[str, Any]],
    top_k: int,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> dict[str, Any]:
    eligible_failures = [
        trial
        for trial in trials
        if _oos_and_monte_carlo_pass(
            trial,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        )
        and _trial_metric_float(trial, "walk_forward_pass_rate") is not None
        and _trial_metric_float(trial, "walk_forward_pass_rate")
        < min_walk_forward_pass_rate
    ]
    gap_entries = [
        {
            "trial": trial,
            "gap": round(
                min_walk_forward_pass_rate
                - float(_trial_metric_float(trial, "walk_forward_pass_rate") or 0.0),
                10,
            ),
            "bucket": _walk_forward_threshold_gap_bucket(
                min_walk_forward_pass_rate
                - float(_trial_metric_float(trial, "walk_forward_pass_rate") or 0.0)
            ),
        }
        for trial in eligible_failures
    ]
    buckets: dict[str, list[dict[str, Any]]] = {
        "near_miss": [],
        "moderate_gap": [],
        "large_gap": [],
    }
    for entry in gap_entries:
        buckets.setdefault(str(entry["bucket"]), []).append(entry)

    sorted_entries = sorted(
        gap_entries,
        key=lambda entry: (
            entry["gap"],
            -_score_float(
                _trial_metric_float(entry["trial"], "monte_carlo_survival_rate")
            ),
            -_score_float(_trial_metric_float(entry["trial"], "oos_total_net_pnl")),
            _promotion_candidate_version(entry["trial"]),
        ),
    )
    focus_limit = max(1, min(top_k, 10))
    near_miss_count = len(buckets.get("near_miss") or [])
    moderate_gap_count = len(buckets.get("moderate_gap") or [])
    large_gap_count = len(buckets.get("large_gap") or [])
    return {
        "task_scope": "H-OPT-019",
        "min_walk_forward_pass_rate": min_walk_forward_pass_rate,
        "min_monte_carlo_survival_rate": min_monte_carlo_survival_rate,
        "eligible_oos_mc_failure_count": len(eligible_failures),
        "gap_distribution": _rounded_numeric_distribution(
            [entry["gap"] for entry in gap_entries]
        ),
        "gap_bucket_counts": {
            "near_miss": near_miss_count,
            "moderate_gap": moderate_gap_count,
            "large_gap": large_gap_count,
        },
        "best_gap_candidates": [
            _walk_forward_gap_candidate_snapshot(entry)
            for entry in sorted_entries[:focus_limit]
        ],
        "recommended_next_budget_policy": _walk_forward_gap_budget_policy(
            near_miss_count=near_miss_count,
            moderate_gap_count=moderate_gap_count,
            large_gap_count=large_gap_count,
        ),
        "does_not_lower_gate_thresholds": True,
        "reason_codes": _walk_forward_gap_reason_codes(
            eligible_count=len(eligible_failures),
            near_miss_count=near_miss_count,
            moderate_gap_count=moderate_gap_count,
            large_gap_count=large_gap_count,
        ),
    }


def _walk_forward_threshold_gap_bucket(gap: float) -> str:
    if gap <= 0.10:
        return "near_miss"
    if gap <= 0.30:
        return "moderate_gap"
    return "large_gap"


def _rounded_numeric_distribution(values: list[int] | list[float]) -> dict[str, Any]:
    distribution = _numeric_distribution(values)
    return {
        key: round(value, 10) if isinstance(value, float) else value
        for key, value in distribution.items()
    }


def _walk_forward_gap_candidate_snapshot(
    entry: dict[str, Any],
) -> dict[str, Any]:
    snapshot = _walk_forward_trial_snapshot(entry.get("trial"))
    if not isinstance(snapshot, dict):
        snapshot = {}
    snapshot["walk_forward_threshold_gap"] = entry.get("gap")
    snapshot["walk_forward_threshold_gap_bucket"] = entry.get("bucket")
    return snapshot


def _walk_forward_gap_budget_policy(
    *,
    near_miss_count: int,
    moderate_gap_count: int,
    large_gap_count: int,
) -> dict[str, Any]:
    if near_miss_count:
        recommended_focus = (
            "near_miss_independent_confirm_recheck_before_parameter_expansion"
        )
    elif moderate_gap_count:
        recommended_focus = "moderate_gap_parameter_neighbor_and_window_rescue"
    elif large_gap_count:
        recommended_focus = "large_gap_strategy_or_feature_search_before_more_resume"
    else:
        recommended_focus = "collect_oos_mc_walk_forward_failure_candidates"
    return {
        "recommended_focus": recommended_focus,
        "prioritize_near_miss_candidates": near_miss_count > 0,
        "prioritize_parameter_neighbors": near_miss_count == 0 and moderate_gap_count > 0,
        "avoid_mechanical_resume_only": large_gap_count > 0 and near_miss_count == 0,
        "keep_confirm_gate_required": True,
    }


def _walk_forward_gap_reason_codes(
    *,
    eligible_count: int,
    near_miss_count: int,
    moderate_gap_count: int,
    large_gap_count: int,
) -> list[str]:
    reason_codes = []
    if eligible_count == 0:
        reason_codes.append("no_oos_mc_walk_forward_gap_candidates")
    if near_miss_count:
        reason_codes.append("near_miss_walk_forward_gap_candidates_available")
    if moderate_gap_count:
        reason_codes.append("moderate_walk_forward_gap_candidates_available")
    if large_gap_count:
        reason_codes.append("large_walk_forward_gap_candidates_available")
    return sorted(reason_codes)


def _walk_forward_rescue_plan(
    *,
    trials: list[dict[str, Any]],
    top_k: int,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
    threshold_gap_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    confirm_passes = [
        trial
        for trial in trials
        if trial.get("status") == "PASS"
        and trial.get("phase") == "confirm"
        and trial.get("artifact_publication_allowed") is True
    ]
    evidence_trials = [
        trial
        for trial in trials
        if _trial_metric_float(trial, "walk_forward_pass_rate") is not None
    ]
    below_threshold = [
        trial
        for trial in evidence_trials
        if (
            _trial_metric_float(trial, "walk_forward_pass_rate") is not None
            and _trial_metric_float(trial, "walk_forward_pass_rate")
            < min_walk_forward_pass_rate
        )
    ]
    oos_mc_failures = [
        trial
        for trial in below_threshold
        if _oos_and_monte_carlo_pass(
            trial,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        )
    ]
    passing_walk_forward = [
        trial
        for trial in evidence_trials
        if (
            _trial_metric_float(trial, "walk_forward_pass_rate") is not None
            and _trial_metric_float(trial, "walk_forward_pass_rate")
            >= min_walk_forward_pass_rate
        )
    ]

    status = "RECOMMENDED"
    recommended_action = "expand_window_grid_for_best_walk_forward_failures"
    candidate_pool = below_threshold
    candidate_pool_reason = "best_walk_forward_failures"
    reason_codes = ["walk_forward_rescue_plan_emitted"]
    if confirm_passes:
        status = "NOT_REQUIRED"
        recommended_action = "confirm_candidate_passed"
        candidate_pool = confirm_passes
        candidate_pool_reason = "confirm_passes"
        reason_codes = ["confirm_candidate_passed"]
    elif not trials:
        status = "PENDING_INPUT"
        recommended_action = "run_initial_staged_search"
        candidate_pool = []
        candidate_pool_reason = "no_trials"
        reason_codes.append("no_trials_for_walk_forward_rescue_plan")
    elif not evidence_trials:
        status = "BLOCKED"
        recommended_action = "collect_walk_forward_evidence"
        candidate_pool = []
        candidate_pool_reason = "walk_forward_evidence_missing"
        reason_codes.append("walk_forward_evidence_missing")
    elif oos_mc_failures:
        recommended_action = (
            "rescue_oos_mc_candidates_with_independent_walk_forward_windows"
        )
        candidate_pool = oos_mc_failures
        candidate_pool_reason = "oos_mc_walk_forward_failures"
        reason_codes.append("walk_forward_gate_failed_despite_oos_and_monte_carlo")
    elif passing_walk_forward:
        recommended_action = "recheck_walk_forward_stable_candidates_for_oos_or_mc"
        candidate_pool = passing_walk_forward
        candidate_pool_reason = "walk_forward_threshold_candidates"
        reason_codes.append("walk_forward_threshold_met_without_confirm_pass")
    elif below_threshold:
        reason_codes.append("all_walk_forward_candidates_below_threshold")

    focus_candidates = _walk_forward_rescue_candidate_snapshots(
        candidate_pool,
        limit=max(1, min(top_k, 10)),
    )
    return {
        "task_scope": "H-OPT-019",
        "status": status,
        "recommended_action": recommended_action,
        "candidate_pool_reason": candidate_pool_reason,
        "candidate_pool_count": len(candidate_pool),
        "focus_candidate_count": len(focus_candidates),
        "focus_candidate_diversity": _candidate_diversity_summary(focus_candidates),
        "focus_candidates": focus_candidates,
        "threshold_gap_analysis": threshold_gap_analysis or {},
        "recommended_next_budget_policy": (
            (threshold_gap_analysis or {}).get("recommended_next_budget_policy")
            or {}
        ),
        "recommended_phase_order": ["coarse", "fine", "confirm"],
        "recommended_trial_selection": {
            "fine": "same_strategy_timeframe_independent_holdout_or_window_neighbors",
            "confirm": (
                "independent_holdout_walk_forward_window_parameter_neighbor_and_monte_carlo_seed_recheck"
            ),
        },
        "recommended_parameter_neighbor_policy": {
            "same_strategy_timeframe_only": True,
            "prefer_nearest_parameter_neighbors": True,
            "requires_independent_holdout_or_walk_forward_window": True,
            "prefer_dual_independent_recheck_before_parameter_neighbor": True,
            "preserve_cross_timeframe_strategy_candidates": True,
            "avoid_failed_window_profiles": True,
        },
        "recommended_confirm_recheck_contract": {
            "prefer_independent_holdout_ratio": True,
            "prefer_independent_walk_forward_window": True,
            "prefer_dual_independent_holdout_and_window": True,
            "use_independent_monte_carlo_seed": True,
            "artifact_publication_phase": "confirm",
        },
        "min_walk_forward_pass_rate": min_walk_forward_pass_rate,
        "min_monte_carlo_survival_rate": min_monte_carlo_survival_rate,
        "failed_window_profile_keys": sorted(
            _walk_forward_failed_window_profile_keys(
                trials,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            )
        ),
        "does_not_lower_gate_thresholds": True,
        "reason_codes": sorted(set(reason_codes)),
    }


def _walk_forward_rescue_candidate_snapshots(
    trials: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    selected = _select_diversified_candidates(
        [sorted(trials, key=_walk_forward_rescue_score, reverse=True)],
        limit=limit,
    )
    return [
        snapshot
        for snapshot in (
            _walk_forward_trial_snapshot(trial)
            for trial in selected
        )
        if isinstance(snapshot, dict)
    ]


def _candidate_diversity_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = {
        (
            str(candidate.get("timeframe")),
            str(candidate.get("strategy_id")),
        )
        for candidate in candidates
    }
    return {
        "bucket": "timeframe_then_strategy",
        "bucket_count": len(buckets),
        "timeframe_count": len({timeframe for timeframe, _ in buckets}),
        "buckets": [
            {"timeframe": timeframe, "strategy_id": strategy_id}
            for timeframe, strategy_id in sorted(buckets)
        ],
    }


def _best_walk_forward_trial(
    trials: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        trial
        for trial in trials
        if _trial_metric_float(trial, "walk_forward_pass_rate") is not None
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda trial: (
            _score_float(_trial_metric_float(trial, "walk_forward_pass_rate")),
            _score_int(_trial_metric_int(trial, "walk_forward_pass_count")),
            _score_float(_trial_metric_float(trial, "monte_carlo_survival_rate")),
            _score_float(_trial_metric_float(trial, "oos_total_net_pnl")),
            -int(trial.get("trial_index") or 0),
        ),
    )


def _walk_forward_trial_snapshot(trial: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(trial, dict):
        return None
    return {
        "candidate_version": trial.get("candidate_version"),
        "phase": trial.get("phase"),
        "timeframe": trial.get("timeframe"),
        "strategy_id": trial.get("strategy_id"),
        "window": trial.get("window") if isinstance(trial.get("window"), dict) else {},
        "metrics": {
            "oos_total_net_pnl": _trial_metric_float(trial, "oos_total_net_pnl"),
            "walk_forward_window_count": _trial_metric_int(
                trial,
                "walk_forward_window_count",
            ),
            "walk_forward_pass_count": _trial_metric_int(
                trial,
                "walk_forward_pass_count",
            ),
            "walk_forward_pass_rate": _trial_metric_float(
                trial,
                "walk_forward_pass_rate",
            ),
            "monte_carlo_survival_rate": _trial_metric_float(
                trial,
                "monte_carlo_survival_rate",
            ),
        },
        "reason_codes": list(trial.get("reason_codes") or []),
    }


def _oos_and_monte_carlo_pass(
    trial: dict[str, Any],
    *,
    min_monte_carlo_survival_rate: float,
) -> bool:
    oos_total_net_pnl = _trial_metric_float(trial, "oos_total_net_pnl")
    monte_carlo_survival_rate = _trial_metric_float(
        trial,
        "monte_carlo_survival_rate",
    )
    return (
        oos_total_net_pnl is not None
        and oos_total_net_pnl >= 0.0
        and monte_carlo_survival_rate is not None
        and monte_carlo_survival_rate >= min_monte_carlo_survival_rate
    )


def _trial_metric_float(trial: dict[str, Any], key: str) -> float | None:
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
    return _coerce_float(metrics.get(key))


def _trial_metric_int(trial: dict[str, Any], key: str) -> int | None:
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
    return _coerce_int(metrics.get(key))


def _passes_metric_floor(
    trial: dict[str, Any],
    *,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> bool:
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
    walk_forward_pass_rate = _coerce_float(metrics.get("walk_forward_pass_rate"))
    monte_carlo_survival_rate = _coerce_float(metrics.get("monte_carlo_survival_rate"))
    return (
        walk_forward_pass_rate is not None
        and walk_forward_pass_rate >= min_walk_forward_pass_rate
        and monte_carlo_survival_rate is not None
        and monte_carlo_survival_rate >= min_monte_carlo_survival_rate
    )


def _overfit_reason_codes(
    *,
    trials: list[dict[str, Any]],
    best_score: float | None,
    median_score: float | None,
) -> list[str]:
    reason_codes = []
    if not trials:
        reason_codes.append("no_trials_for_overfit_diagnostics")
    if best_score is not None and median_score is not None and best_score > median_score:
        reason_codes.append("best_candidate_outperforms_median")
    if not any(trial.get("phase") == "confirm" for trial in trials):
        reason_codes.append("confirm_phase_not_completed")
    if not any(trial.get("status") == "PASS" for trial in trials):
        reason_codes.append("no_gate_passing_candidate_for_stability_check")
    return sorted(set(reason_codes))


def _load_resumed_trials(resume_from: str | Path | None) -> dict[str, dict[str, Any]]:
    if resume_from is None:
        return {}
    resume_path = Path(resume_from)
    try:
        payload = json.loads(resume_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    trials = payload.get("all_trials") if isinstance(payload, dict) else []
    if not isinstance(trials, list):
        return {}
    resumed = {}
    for trial in trials:
        if not isinstance(trial, dict):
            continue
        candidate_version = trial.get("candidate_version")
        if candidate_version:
            resumed[str(candidate_version)] = dict(trial)
    return resumed


def _runtime_exhausted(
    started_monotonic: float,
    max_runtime_seconds: float | None,
) -> bool:
    if max_runtime_seconds is None:
        return False
    return time.monotonic() - started_monotonic >= max_runtime_seconds


def _normalize_strategy_ids(strategy_ids: list[str] | None) -> list[str]:
    raw_ids = strategy_ids or list(DEFAULT_STRATEGY_IDS)
    normalized = []
    supported = set(SUPPORTED_CANDIDATE_STRATEGY_IDS)
    for item in raw_ids:
        strategy_id = str(item).strip().lower()
        if not strategy_id:
            continue
        if strategy_id not in supported:
            raise ValueError(f"unsupported_candidate_strategy: {item}")
        if strategy_id not in normalized:
            normalized.append(strategy_id)
    return normalized


def _normalize_timeframes(timeframes: list[str] | None) -> list[str]:
    raw_timeframes = timeframes or list(DEFAULT_TIMEFRAMES)
    normalized = []
    for item in raw_timeframes:
        timeframe = str(item).strip()
        if timeframe and timeframe not in normalized:
            normalized.append(timeframe)
    return normalized


def _normalize_phases(phases: list[str] | None) -> list[str]:
    raw_phases = phases or list(DEFAULT_PHASES)
    normalized = []
    supported = set(SUPPORTED_PHASES)
    for item in raw_phases:
        phase = str(item).strip().lower()
        if not phase:
            continue
        if phase not in supported:
            raise ValueError(f"unsupported_validation_search_phase: {item}")
        if phase not in normalized:
            normalized.append(phase)
    if not normalized:
        raise ValueError("validation_search_phases_must_not_be_empty")
    if "confirm" not in normalized:
        normalized.append("confirm")
    return normalized


def _resolve_matrix_output_path(value: Any, matrix_path: Path) -> Path | None:
    if value is None:
        return None
    candidate = Path(str(value))
    if candidate.is_absolute():
        return candidate
    project_candidate = PROJECT_ROOT / candidate
    if project_candidate.exists():
        return project_candidate
    matrix_candidate = matrix_path.parent / candidate
    if matrix_candidate.exists():
        return matrix_candidate
    return project_candidate


def _base_report(
    *,
    generated_at: int,
    matrix_path: Path,
    matrix_fingerprint: dict[str, Any],
    symbol: str,
    search_parameters: dict[str, Any],
    status: str,
    message: str,
    reason_codes: list[str],
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "status": status,
        "success": status == "PASS",
        "message": message,
        "task_scope": "H-OPT-018",
        "staged_search_task_scope": "H-OPT-019",
        "symbol": symbol,
        "matrix_path": str(matrix_path),
        "matrix_fingerprint": matrix_fingerprint,
        "search_parameters": search_parameters,
        "reason_codes": sorted(set(reason_codes)),
        "safety_flags": _default_safety_flags(),
        "network_access_used": False,
        "real_credentials_read": False,
        "broker_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
        "public_market_data_only": True,
    }


def _file_fingerprint(path: Path) -> dict[str, Any]:
    try:
        stat_result = path.stat()
    except OSError:
        return {
            "path": str(path),
            "exists": False,
            "size_bytes": None,
            "sha256": None,
        }
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": stat_result.st_size,
        "sha256": digest.hexdigest(),
    }


def _write_report(report: dict[str, Any], output_path: Path | None) -> dict[str, Any]:
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def _parse_csv(value: str) -> list[str]:
    items = [item.strip() for item in str(value).split(",") if item.strip()]
    if not items:
        raise argparse.ArgumentTypeError("list must not be empty")
    return items


def _parse_int_grid(value: str) -> list[int]:
    items = [item.strip() for item in str(value).split(",") if item.strip()]
    if not items:
        raise argparse.ArgumentTypeError("grid must not be empty")
    try:
        parsed = [int(item) for item in items]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("grid must contain integers") from exc
    if any(item <= 0 for item in parsed):
        raise argparse.ArgumentTypeError("grid integers must be positive")
    return parsed


def _parse_float_grid(value: str) -> list[float]:
    items = [item.strip() for item in str(value).split(",") if item.strip()]
    if not items:
        raise argparse.ArgumentTypeError("grid must not be empty")
    try:
        parsed = [float(item) for item in items]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("grid must contain numbers") from exc
    if any(item <= 0.0 for item in parsed):
        raise argparse.ArgumentTypeError("grid values must be positive")
    return parsed


def _safe_token(value: Any) -> str:
    token = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in str(value)
    ).strip("_")
    return token or "unknown"


def _dict_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_float(value: Any) -> float:
    coerced = _coerce_float(value)
    return coerced if coerced is not None else -1.0e18


def _score_int(value: Any) -> int:
    coerced = _coerce_int(value)
    return coerced if coerced is not None else -10**18


def _median(values: list[int] | list[float]) -> float | int | None:
    if not values:
        return None
    sorted_values = sorted(values)
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[middle]
    return (sorted_values[middle - 1] + sorted_values[middle]) / 2


def _percentile(values: list[int] | list[float], percentile: float) -> float | int | None:
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * percentile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return sorted_values[lower_index] + (
        sorted_values[upper_index] - sorted_values[lower_index]
    ) * fraction


def _numeric_distribution(values: list[int] | list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "median": _median(values),
        "p90": _percentile(values, 0.9),
        "max": max(values) if values else None,
    }


def _count_by_key(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _default_safety_flags() -> dict[str, bool]:
    return {
        "network_access_used": False,
        "real_credentials_read": False,
        "broker_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Search public BTCUSDT validation candidates across multiple "
            "timeframes, strategies, and bounded parameter grids."
        )
    )
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--trial-output-dir", default=str(DEFAULT_TRIAL_OUTPUT_DIR))
    parser.add_argument("--source-report-dir", default=str(DEFAULT_SOURCE_REPORT_DIR))
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument(
        "--latest-validator-output",
        default=str(DEFAULT_VALIDATOR_OUTPUT_PATH),
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframes", type=_parse_csv)
    parser.add_argument("--strategy-ids", "--strategy-id", type=_parse_csv)
    parser.add_argument("--max-trials", type=int, default=500)
    parser.add_argument("--max-runtime-seconds", type=float)
    parser.add_argument("--resume-from")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--stop-on-first-pass", action="store_true")
    parser.add_argument("--phases", type=_parse_csv, default="confirm")
    parser.add_argument(
        "--train-bars",
        type=_parse_int_grid,
        default=",".join(str(value) for value in DEFAULT_TRAIN_BARS_VALUES),
    )
    parser.add_argument(
        "--test-bars",
        type=_parse_int_grid,
        default=",".join(str(value) for value in DEFAULT_TEST_BARS_VALUES),
    )
    parser.add_argument(
        "--step-bars",
        type=_parse_int_grid,
        default=",".join(str(value) for value in DEFAULT_STEP_BARS_VALUES),
    )
    parser.add_argument(
        "--holdout-ratio",
        type=_parse_float_grid,
        default=",".join(str(value) for value in DEFAULT_HOLDOUT_RATIOS),
    )
    parser.add_argument(
        "--min-trade-count",
        type=_parse_int_grid,
        default=",".join(str(value) for value in DEFAULT_MIN_TRADE_COUNTS),
    )
    parser.add_argument("--min-train-bars", type=int, default=40)
    parser.add_argument("--min-holdout-bars", type=int, default=20)
    parser.add_argument("--min-walk-forward-windows", type=int, default=3)
    parser.add_argument("--min-walk-forward-pass-rate", type=float, default=0.67)
    parser.add_argument("--monte-carlo-run-count", type=int, default=500)
    parser.add_argument("--monte-carlo-seed", type=int, default=42)
    parser.add_argument("--min-monte-carlo-trades", type=int)
    parser.add_argument("--min-monte-carlo-survival-rate", type=float, default=0.8)
    parser.add_argument("--monte-carlo-max-drawdown-limit", type=float)
    parser.add_argument("--initial-balance", type=float, default=10000.0)
    parser.add_argument(
        "--require-gate-pass",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--min-net-pnl", type=float, default=0.0)
    parser.add_argument("--max-drawdown", type=float)
    parser.add_argument("--min-win-rate", type=float)
    parser.add_argument("--min-out-of-sample-net-pnl", type=float, default=0.0)
    parser.add_argument("--overwrite-existing-source-reports", action="store_true")
    parser.add_argument("--overwrite-existing-artifacts", action="store_true")
    parser.add_argument("--timestamp", type=int)
    parser.add_argument(
        "--print-full-report",
        action="store_true",
        help="Print the complete JSON report instead of the compact CLI summary.",
    )
    args = parser.parse_args(argv)

    try:
        report = run_expanded_public_btcusdt_validation_search(
            matrix_path=args.matrix,
            output_path=args.output,
            trial_output_dir=args.trial_output_dir,
            source_report_dir=args.source_report_dir,
            artifact_dir=args.artifact_dir,
            latest_validator_output_path=args.latest_validator_output,
            symbol=args.symbol,
            timeframes=args.timeframes,
            strategy_ids=args.strategy_ids,
            max_trials=args.max_trials,
            max_runtime_seconds=args.max_runtime_seconds,
            resume_from=args.resume_from,
            top_k=args.top_k,
            stop_on_first_pass=args.stop_on_first_pass,
            phases=args.phases,
            train_bars_values=args.train_bars,
            test_bars_values=args.test_bars,
            step_bars_values=args.step_bars,
            holdout_ratios=args.holdout_ratio,
            min_trade_counts=args.min_trade_count,
            min_train_bars=args.min_train_bars,
            min_holdout_bars=args.min_holdout_bars,
            min_walk_forward_windows=args.min_walk_forward_windows,
            min_walk_forward_pass_rate=args.min_walk_forward_pass_rate,
            monte_carlo_run_count=args.monte_carlo_run_count,
            monte_carlo_seed=args.monte_carlo_seed,
            min_monte_carlo_trades=args.min_monte_carlo_trades,
            min_monte_carlo_survival_rate=args.min_monte_carlo_survival_rate,
            monte_carlo_max_drawdown_limit=args.monte_carlo_max_drawdown_limit,
            initial_balance=args.initial_balance,
            require_gate_pass=args.require_gate_pass,
            min_net_pnl=args.min_net_pnl,
            max_drawdown=args.max_drawdown,
            min_win_rate=args.min_win_rate,
            min_out_of_sample_net_pnl=args.min_out_of_sample_net_pnl,
            overwrite_existing_source_reports=args.overwrite_existing_source_reports,
            overwrite_existing_artifacts=args.overwrite_existing_artifacts,
            timestamp=args.timestamp,
        )
    except ValueError as exc:
        report = {
            "generated_at": int(time.time()) if args.timestamp is None else args.timestamp,
            "status": "FAIL",
            "success": False,
            "message": str(exc),
            "task_scope": "H-OPT-018",
            "staged_search_task_scope": "H-OPT-019",
            "reason_codes": ["expanded_public_btcusdt_search_config_invalid"],
            "safety_flags": _default_safety_flags(),
        }
        output_path = Path(args.output) if args.output else None
        _write_report(report, output_path)
    printable_report = report if args.print_full_report else _stdout_summary(report)
    print(json.dumps(printable_report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] == "SKIPPED":
        return 2
    return 1


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    best_candidate = (
        report.get("best_candidate")
        if isinstance(report.get("best_candidate"), dict)
        else {}
    )
    return {
        "status": report.get("status"),
        "success": report.get("success"),
        "message": report.get("message"),
        "task_scope": report.get("task_scope"),
        "staged_search_task_scope": report.get("staged_search_task_scope"),
        "reason_codes": report.get("reason_codes"),
        "matrix_path": report.get("matrix_path"),
        "planned_trial_count": report.get("planned_trial_count"),
        "completed_trial_count": report.get("completed_trial_count"),
        "pass_count": report.get("pass_count"),
        "artifact_count": report.get("artifact_count"),
        "h_opt_005_ready": report.get("h_opt_005_ready"),
        "h_opt_010_ready": report.get("h_opt_010_ready"),
        "h_opt_005_blockers": report.get("h_opt_005_blockers"),
        "phase_order": report.get("phase_order"),
        "phase_budgets": report.get("phase_budgets"),
        "phase_selection_contract": report.get("phase_selection_contract"),
        "promotion_feedback_contract": report.get("promotion_feedback_contract"),
        "walk_forward_window_rescue_contract": report.get(
            "walk_forward_window_rescue_contract"
        ),
        "walk_forward_window_profile_contract": report.get(
            "walk_forward_window_profile_contract"
        ),
        "walk_forward_rescue_plan_contract": report.get(
            "walk_forward_rescue_plan_contract"
        ),
        "walk_forward_threshold_gap_contract": report.get(
            "walk_forward_threshold_gap_contract"
        ),
        "walk_forward_parameter_rescue_contract": report.get(
            "walk_forward_parameter_rescue_contract"
        ),
        "confirm_recheck_contract": report.get("confirm_recheck_contract"),
        "confirm_recheck_summary": report.get("confirm_recheck_summary"),
        "walk_forward_window_profile_selection_summary": report.get(
            "walk_forward_window_profile_selection_summary"
        ),
        "base_trial_repeat_selection_summary": report.get(
            "base_trial_repeat_selection_summary"
        ),
        "confirm_phase_rescue_memory_summary": report.get(
            "confirm_phase_rescue_memory_summary"
        ),
        "walk_forward_failure_analysis": report.get("walk_forward_failure_analysis"),
        "walk_forward_threshold_gap_analysis": report.get(
            "walk_forward_threshold_gap_analysis"
        ),
        "walk_forward_rescue_plan": report.get("walk_forward_rescue_plan"),
        "overfit_diagnostics": report.get("overfit_diagnostics"),
        "best_candidate": {
            "candidate_version": best_candidate.get("candidate_version"),
            "phase": best_candidate.get("phase"),
            "status": best_candidate.get("status"),
            "timeframe": best_candidate.get("timeframe"),
            "strategy_id": best_candidate.get("strategy_id"),
            "strategy_parameters": best_candidate.get("strategy_parameters"),
            "window": best_candidate.get("window"),
            "metrics": best_candidate.get("metrics"),
            "reason_codes": best_candidate.get("reason_codes"),
        }
        if best_candidate
        else None,
    }


if __name__ == "__main__":
    raise SystemExit(main())
