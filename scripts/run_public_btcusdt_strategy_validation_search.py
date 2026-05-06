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
from scripts.generate_strategy_validation_source_reports import (
    run_strategy_validation_source_report_generation,
)
from scripts.validate_strategy_validation_artifacts import (
    DEFAULT_OUTPUT_PATH as DEFAULT_VALIDATOR_OUTPUT_PATH,
    run_strategy_validation_artifacts_validation,
)


DEFAULT_INPUT_PATH = (
    PROJECT_ROOT / "logs" / "public-market-data" / "btcusdt-5m-latest.json"
)
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT
    / "logs"
    / "strategy-validation-artifacts"
    / "public-btcusdt-search-latest.json"
)
DEFAULT_TRIAL_OUTPUT_DIR = (
    PROJECT_ROOT / "logs" / "strategy-validation-artifacts" / "public-btcusdt-search"
)


def run_public_btcusdt_strategy_validation_search(
    *,
    input_path: str | Path = DEFAULT_INPUT_PATH,
    output_path: str | Path | None = DEFAULT_OUTPUT_PATH,
    trial_output_dir: str | Path = DEFAULT_TRIAL_OUTPUT_DIR,
    source_report_dir: str | Path = DEFAULT_SOURCE_REPORT_DIR,
    artifact_dir: str | Path = DEFAULT_ARTIFACT_DIR,
    latest_validator_output_path: str | Path | None = DEFAULT_VALIDATOR_OUTPUT_PATH,
    strategy_id: str = "ma_crossover",
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
    max_trials: int = 24,
    fast_windows: list[int] | None = None,
    slow_windows: list[int] | None = None,
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
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path is not None else None
    trial_output_dir = Path(trial_output_dir)
    source_report_dir = Path(source_report_dir)
    artifact_dir = Path(artifact_dir)
    data_fingerprint = _file_fingerprint(input_path)

    if max_trials <= 0:
        report = _base_report(
            generated_at=generated_at,
            input_path=input_path,
            data_fingerprint=data_fingerprint,
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            search_parameters={},
            status="SKIPPED",
            message="max_trials must be positive",
            reason_codes=["max_trials_not_positive"],
        )
        return _write_report(report, output_path)

    if not input_path.exists():
        report = _base_report(
            generated_at=generated_at,
            input_path=input_path,
            data_fingerprint=data_fingerprint,
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            search_parameters={},
            status="SKIPPED",
            message="public BTCUSDT kline input does not exist",
            reason_codes=["source_input_missing"],
        )
        return _write_report(report, output_path)

    fast_windows = fast_windows or [1, 2, 3, 5]
    slow_windows = slow_windows or [8, 13, 21, 34]
    train_bars_values = train_bars_values or [300]
    test_bars_values = test_bars_values or [100]
    step_bars_values = step_bars_values or [100]
    holdout_ratios = holdout_ratios or [0.3]
    min_trade_counts = min_trade_counts or [10]
    search_parameters = {
        "fast_windows": list(fast_windows),
        "slow_windows": list(slow_windows),
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
    }

    trials = _build_trials(
        fast_windows=fast_windows,
        slow_windows=slow_windows,
        train_bars_values=train_bars_values,
        test_bars_values=test_bars_values,
        step_bars_values=step_bars_values,
        holdout_ratios=holdout_ratios,
        min_trade_counts=min_trade_counts,
        max_trials=max_trials,
    )

    completed_trials: list[dict[str, Any]] = []
    for index, trial in enumerate(trials, start=1):
        candidate_version = _candidate_version(
            symbol=symbol,
            timeframe=timeframe,
            fingerprint=data_fingerprint,
            trial=trial,
        )
        trial_dir = trial_output_dir / candidate_version
        report_output = trial_dir / "source-report-generation.json"
        artifact_generation_output = trial_dir / "artifact-generation.json"
        validator_output = trial_dir / "validator.json"
        trial_monte_carlo_trades = (
            int(min_monte_carlo_trades)
            if min_monte_carlo_trades is not None
            else int(trial["min_trade_count"])
        )

        try:
            trial_report = run_strategy_validation_source_report_generation(
                source_paths=[input_path],
                strategy_id=strategy_id,
                candidate_version=candidate_version,
                symbol=symbol,
                timeframe=timeframe,
                input_kind="klines",
                output_dir=source_report_dir,
                report_output_path=report_output,
                timestamp=generated_at,
                holdout_ratio=float(trial["holdout_ratio"]),
                min_train_bars=min_train_bars,
                min_holdout_bars=min_holdout_bars,
                min_trades=int(trial["min_trade_count"]),
                initial_balance=initial_balance,
                fast_window=int(trial["fast_window"]),
                slow_window=int(trial["slow_window"]),
                overwrite_existing_source_report=overwrite_existing_source_reports,
                generation_kind="aggregate",
                train_bars=int(trial["train_bars"]),
                test_bars=int(trial["test_bars"]),
                step_bars=int(trial["step_bars"]),
                min_walk_forward_windows=min_walk_forward_windows,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                monte_carlo_run_count=monte_carlo_run_count,
                monte_carlo_seed=monte_carlo_seed,
                min_monte_carlo_trades=trial_monte_carlo_trades,
                min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
                monte_carlo_max_drawdown_limit=monte_carlo_max_drawdown_limit,
                artifact_dir=artifact_dir,
                artifact_generation_output_path=artifact_generation_output,
                validator_output_path=validator_output,
                require_gate_pass=require_gate_pass,
                min_net_pnl=min_net_pnl,
                max_drawdown=max_drawdown,
                min_win_rate=min_win_rate,
                min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
                overwrite_existing_artifacts=overwrite_existing_artifacts,
            )
        except BaseException as exc:
            trial_report = {
                "status": "FAIL",
                "success": False,
                "message": str(exc),
                "reason_codes": ["public_btcusdt_search_trial_failed"],
                "safety_flags": _default_safety_flags(),
                "results": [],
                "artifact_paths": [],
                "generated_artifact_count": 0,
                "validator_status": None,
                "h_opt_005_ready": False,
                "h_opt_005_blockers": ["public_btcusdt_search_trial_failed"],
            }

        completed_trials.append(
            _summarize_trial(
                index=index,
                candidate_version=candidate_version,
                trial=trial,
                report=trial_report,
                report_output_path=report_output,
                artifact_generation_output_path=artifact_generation_output,
                validator_output_path=validator_output,
            )
        )
        if completed_trials[-1]["status"] == "PASS":
            break

    best_candidate = _best_trial(completed_trials)
    passing_candidates = [
        trial for trial in completed_trials if trial.get("status") == "PASS"
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

    status = "PASS" if passing_candidates else "SKIPPED"
    message = (
        "public BTCUSDT search found a candidate that passed OOS/WF/MC gates"
        if passing_candidates
        else "public BTCUSDT search completed without a passing candidate"
    )
    reason_codes = sorted(
        {
            reason
            for trial in completed_trials
            for reason in trial.get("reason_codes", [])
        }
    )
    h_opt_005_blockers = []
    if not passing_candidates:
        h_opt_005_blockers.extend(
            best_candidate.get("h_opt_005_blockers", []) if best_candidate else []
        )
        if not h_opt_005_blockers:
            h_opt_005_blockers.append("no_public_btcusdt_candidate_passed")

    report = _base_report(
        generated_at=generated_at,
        input_path=input_path,
        data_fingerprint=data_fingerprint,
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        search_parameters=search_parameters,
        status=status,
        message=message,
        reason_codes=reason_codes,
    )
    report.update(
        {
            "max_trials": max_trials,
            "planned_trial_count": len(trials),
            "completed_trial_count": len(completed_trials),
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
            "h_opt_005_blockers": sorted(set(h_opt_005_blockers)),
            "best_candidate": best_candidate,
            "passing_candidates": passing_candidates,
            "all_trials": completed_trials,
        }
    )
    return _write_report(report, output_path)


def _build_trials(
    *,
    fast_windows: list[int],
    slow_windows: list[int],
    train_bars_values: list[int],
    test_bars_values: list[int],
    step_bars_values: list[int],
    holdout_ratios: list[float],
    min_trade_counts: list[int],
    max_trials: int,
) -> list[dict[str, Any]]:
    trials = []
    for fast, slow, train, test, step, holdout, min_trades in itertools.product(
        fast_windows,
        slow_windows,
        train_bars_values,
        test_bars_values,
        step_bars_values,
        holdout_ratios,
        min_trade_counts,
    ):
        if int(slow) <= int(fast):
            continue
        trials.append(
            {
                "fast_window": int(fast),
                "slow_window": int(slow),
                "train_bars": int(train),
                "test_bars": int(test),
                "step_bars": int(step),
                "holdout_ratio": float(holdout),
                "min_trade_count": int(min_trades),
            }
        )
        if len(trials) >= max_trials:
            break
    return trials


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
    return {
        "trial_index": index,
        "candidate_version": candidate_version,
        "parameters": dict(trial),
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
        "artifact_paths": list(report.get("artifact_paths") or []),
        "generated_artifact_count": int(report.get("generated_artifact_count") or 0),
        "validator_status": report.get("validator_status"),
        "h_opt_005_ready": bool(report.get("h_opt_005_ready")),
        "h_opt_005_blockers": list(report.get("h_opt_005_blockers") or []),
        "components": components,
        "metrics": _trial_metrics_from_components(components),
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


def _best_trial(trials: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not trials:
        return None
    return max(trials, key=_trial_score)


def _trial_score(trial: dict[str, Any]) -> tuple[Any, ...]:
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else {}
    return (
        1 if trial.get("status") == "PASS" else 0,
        1 if trial.get("validator_status") == "PASS" else 0,
        int(trial.get("generated_artifact_count") or 0),
        _score_float(metrics.get("monte_carlo_survival_rate")),
        _score_float(metrics.get("walk_forward_pass_rate")),
        _score_int(metrics.get("walk_forward_pass_count")),
        _score_float(metrics.get("oos_total_net_pnl")),
        -len(trial.get("reason_codes") or []),
        -int(trial.get("trial_index") or 0),
    )


def _base_report(
    *,
    generated_at: int,
    input_path: Path,
    data_fingerprint: dict[str, Any],
    strategy_id: str,
    symbol: str,
    timeframe: str,
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
        "task_scope": "H-OPT-017",
        "strategy_id": strategy_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "input_path": str(input_path),
        "data_fingerprint": data_fingerprint,
        "search_parameters": search_parameters,
        "reason_codes": sorted(set(reason_codes)),
        "safety_flags": _default_safety_flags(),
        "network_access_used": False,
        "real_credentials_read": False,
        "broker_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
    }


def _candidate_version(
    *,
    symbol: str,
    timeframe: str,
    fingerprint: dict[str, Any],
    trial: dict[str, Any],
) -> str:
    digest = str(fingerprint.get("sha256") or "missing")[:12]
    holdout_token = str(int(round(float(trial["holdout_ratio"]) * 100)))
    return _safe_token(
        f"public-{symbol.lower()}-{timeframe}-data-{digest}-"
        f"fw{trial['fast_window']}-sw{trial['slow_window']}-"
        f"tr{trial['train_bars']}-te{trial['test_bars']}-st{trial['step_bars']}-"
        f"ho{holdout_token}-mt{trial['min_trade_count']}"
    )


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
            "Search public BTCUSDT MA crossover validation parameters and publish "
            "artifacts only when OOS, walk-forward, Monte Carlo, and strict gates pass."
        )
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--trial-output-dir", default=str(DEFAULT_TRIAL_OUTPUT_DIR))
    parser.add_argument("--source-report-dir", default=str(DEFAULT_SOURCE_REPORT_DIR))
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument(
        "--latest-validator-output",
        default=str(DEFAULT_VALIDATOR_OUTPUT_PATH),
    )
    parser.add_argument("--strategy-id", default="ma_crossover")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--max-trials", type=int, default=24)
    parser.add_argument("--fast-window", "--fast-windows", type=_parse_int_grid, default="1,2,3,5")
    parser.add_argument("--slow-window", "--slow-windows", type=_parse_int_grid, default="8,13,21,34")
    parser.add_argument("--train-bars", type=_parse_int_grid, default="300")
    parser.add_argument("--test-bars", type=_parse_int_grid, default="100")
    parser.add_argument("--step-bars", type=_parse_int_grid, default="100")
    parser.add_argument("--holdout-ratio", type=_parse_float_grid, default="0.3")
    parser.add_argument("--min-trade-count", type=_parse_int_grid, default="10")
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
    parser.add_argument("--require-gate-pass", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-net-pnl", type=float, default=0.0)
    parser.add_argument("--max-drawdown", type=float)
    parser.add_argument("--min-win-rate", type=float)
    parser.add_argument("--min-out-of-sample-net-pnl", type=float, default=0.0)
    parser.add_argument("--overwrite-existing-source-reports", action="store_true")
    parser.add_argument("--overwrite-existing-artifacts", action="store_true")
    parser.add_argument("--timestamp", type=int)
    args = parser.parse_args(argv)

    report = run_public_btcusdt_strategy_validation_search(
        input_path=args.input,
        output_path=args.output,
        trial_output_dir=args.trial_output_dir,
        source_report_dir=args.source_report_dir,
        artifact_dir=args.artifact_dir,
        latest_validator_output_path=args.latest_validator_output,
        strategy_id=args.strategy_id,
        symbol=args.symbol,
        timeframe=args.timeframe,
        max_trials=args.max_trials,
        fast_windows=args.fast_window,
        slow_windows=args.slow_window,
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
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] == "SKIPPED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
