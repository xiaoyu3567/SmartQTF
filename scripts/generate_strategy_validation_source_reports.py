#!/usr/bin/env python
import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.optimization.artifact_generation import (
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_SOURCE_REPORT_DIR,
    StrategyValidationArtifactSourceReport,
    StrategyValidationSourceSummary,
)
from quant.optimization.candidate_strategies import (
    candidate_strategy_metadata,
    normalize_candidate_strategy_parameters,
)
from quant.optimization.source_report_generation import (
    HistoricalValidationWindowConfig,
    MonteCarloValidationConfig,
    SourceReportGenerationResult,
    WalkForwardWindowConfig,
    generate_monte_carlo_source_report,
    generate_oos_source_report,
    generate_walk_forward_source_report,
)
from quant.schemas import PayloadSource
from quant.schemas.base import TraceContext
from scripts.generate_strategy_validation_artifacts import (
    DEFAULT_OUTPUT_PATH as DEFAULT_ARTIFACT_GENERATION_OUTPUT_PATH,
    run_strategy_validation_artifact_generation,
)
from scripts.validate_strategy_validation_artifacts import (
    DEFAULT_OUTPUT_PATH as DEFAULT_VALIDATOR_OUTPUT_PATH,
)


DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT
    / "logs"
    / "strategy-validation-artifacts"
    / "source-report-generation-latest.json"
)


def run_strategy_validation_source_report_generation(
    *,
    source_paths: list[str | Path] | None,
    strategy_id: str,
    candidate_version: str,
    symbol: str,
    timeframe: str = "1m",
    input_kind: str = "auto",
    output_dir: str | Path = DEFAULT_SOURCE_REPORT_DIR,
    report_output_path: str | Path | None = DEFAULT_OUTPUT_PATH,
    timestamp: int | None = None,
    holdout_ratio: float = 0.3,
    holdout_bars: int | None = None,
    min_train_bars: int = 40,
    min_holdout_bars: int = 20,
    min_trades: int = 1,
    initial_balance: float = 10000.0,
    fast_window: int = 1,
    slow_window: int = 2,
    strategy_parameters: dict[str, Any] | None = None,
    overwrite_existing_source_report: bool = False,
    generation_kind: str = "oos",
    train_bars: int = 40,
    test_bars: int = 20,
    step_bars: int = 20,
    min_walk_forward_windows: int = 3,
    min_walk_forward_pass_rate: float = 0.67,
    monte_carlo_run_count: int = 500,
    monte_carlo_seed: int = 42,
    min_monte_carlo_trades: int = 10,
    min_monte_carlo_survival_rate: float = 0.8,
    monte_carlo_max_drawdown_limit: float | None = None,
    monte_carlo_perturbation_dimensions: list[str] | None = None,
    monte_carlo_return_perturbation_sigma: float = 0.05,
    monte_carlo_slippage_fee_perturbation_sigma: float = 0.02,
    artifact_dir: str | Path | None = DEFAULT_ARTIFACT_DIR,
    artifact_generation_output_path: str | Path | None = (
        DEFAULT_ARTIFACT_GENERATION_OUTPUT_PATH
    ),
    validator_output_path: str | Path | None = DEFAULT_VALIDATOR_OUTPUT_PATH,
    require_gate_pass: bool = False,
    min_net_pnl: float = 0.0,
    max_drawdown: float | None = None,
    min_win_rate: float | None = None,
    min_out_of_sample_net_pnl: float = 0.0,
    overwrite_existing_artifacts: bool = False,
) -> dict[str, Any]:
    generated_at = int(time.time()) if timestamp is None else timestamp
    active_source_paths = [Path(path) for path in source_paths or []]
    try:
        normalized_strategy_parameters = _resolve_strategy_parameters(
            strategy_id=strategy_id,
            strategy_parameters=strategy_parameters,
            fast_window=fast_window,
            slow_window=slow_window,
        )
    except ValueError as exc:
        report = _build_aggregate_report(
            generated_at=generated_at,
            source_paths=active_source_paths,
            output_dir=output_dir,
            report_output_path=report_output_path,
            results=[
                {
                    "status": "FAIL",
                    "message": str(exc),
                    "reason_codes": ["source_report_generation_failed"],
                    "safety_flags": _zero_safety_flags(),
                }
            ],
            status="FAIL",
            message="strategy parameter validation failed",
            generation_kind=generation_kind,
            strategy_id=strategy_id,
            strategy_parameters={},
        )
        return _write_report(report, report_output_path)
    if generation_kind == "aggregate":
        return _run_aggregate_source_report_generation(
            source_paths=active_source_paths,
            strategy_id=strategy_id,
            candidate_version=candidate_version,
            symbol=symbol,
            timeframe=timeframe,
            input_kind=input_kind,
            output_dir=output_dir,
            report_output_path=report_output_path,
            generated_at=generated_at,
            holdout_ratio=holdout_ratio,
            holdout_bars=holdout_bars,
            min_train_bars=min_train_bars,
            min_holdout_bars=min_holdout_bars,
            min_trades=min_trades,
            initial_balance=initial_balance,
            fast_window=fast_window,
            slow_window=slow_window,
            strategy_parameters=normalized_strategy_parameters,
            overwrite_existing_source_report=overwrite_existing_source_report,
            train_bars=train_bars,
            test_bars=test_bars,
            step_bars=step_bars,
            min_walk_forward_windows=min_walk_forward_windows,
            min_walk_forward_pass_rate=min_walk_forward_pass_rate,
            monte_carlo_run_count=monte_carlo_run_count,
            monte_carlo_seed=monte_carlo_seed,
            min_monte_carlo_trades=min_monte_carlo_trades,
            min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
            monte_carlo_max_drawdown_limit=monte_carlo_max_drawdown_limit,
            monte_carlo_perturbation_dimensions=monte_carlo_perturbation_dimensions,
            monte_carlo_return_perturbation_sigma=monte_carlo_return_perturbation_sigma,
            monte_carlo_slippage_fee_perturbation_sigma=(
                monte_carlo_slippage_fee_perturbation_sigma
            ),
            artifact_dir=artifact_dir,
            artifact_generation_output_path=artifact_generation_output_path,
            validator_output_path=validator_output_path,
            require_gate_pass=require_gate_pass,
            min_net_pnl=min_net_pnl,
            max_drawdown=max_drawdown,
            min_win_rate=min_win_rate,
            min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
            overwrite_existing_artifacts=overwrite_existing_artifacts,
        )

    if not active_source_paths:
        report = _build_aggregate_report(
            generated_at=generated_at,
            source_paths=[],
            output_dir=output_dir,
            report_output_path=report_output_path,
            results=[],
            status="SKIPPED",
            message="no local historical validation inputs were provided",
            generation_kind=generation_kind,
            strategy_id=strategy_id,
            strategy_parameters=normalized_strategy_parameters,
        )
        return _write_report(report, report_output_path)

    results: list[SourceReportGenerationResult] = []
    for source_path in active_source_paths:
        if generation_kind == "walk_forward":
            config = WalkForwardWindowConfig(
                source_path=str(source_path),
                strategy_id=strategy_id,
                candidate_version=candidate_version,
                symbol=symbol,
                timeframe=timeframe,
                input_kind="klines" if input_kind == "auto" else input_kind,
                output_dir=str(output_dir),
                holdout_ratio=holdout_ratio,
                holdout_bars=holdout_bars,
                min_train_bars=min_train_bars,
                min_holdout_bars=min_holdout_bars,
                min_trades=min_trades,
                initial_balance=initial_balance,
                fast_window=fast_window,
                slow_window=slow_window,
                strategy_parameters=normalized_strategy_parameters,
                generated_at=generated_at,
                overwrite_existing=overwrite_existing_source_report,
                train_bars=train_bars,
                test_bars=test_bars,
                step_bars=step_bars,
                min_windows=min_walk_forward_windows,
                min_pass_rate=min_walk_forward_pass_rate,
            )
            results.append(generate_walk_forward_source_report(config))
            continue

        if generation_kind == "monte_carlo":
            config_values = {
                "source_path": str(source_path),
                "strategy_id": strategy_id,
                "candidate_version": candidate_version,
                "symbol": symbol,
                "timeframe": timeframe,
                "input_kind": input_kind,
                "output_dir": str(output_dir),
                "holdout_ratio": holdout_ratio,
                "holdout_bars": holdout_bars,
                "min_train_bars": min_train_bars,
                "min_holdout_bars": min_holdout_bars,
                "min_trades": min_trades,
                "initial_balance": initial_balance,
                "fast_window": fast_window,
                "slow_window": slow_window,
                "strategy_parameters": normalized_strategy_parameters,
                "generated_at": generated_at,
                "overwrite_existing": overwrite_existing_source_report,
                "run_count": monte_carlo_run_count,
                "seed": monte_carlo_seed,
                "min_trade_count": min_monte_carlo_trades,
                "survival_threshold": min_monte_carlo_survival_rate,
                "max_drawdown_limit": monte_carlo_max_drawdown_limit,
                "return_perturbation_sigma": monte_carlo_return_perturbation_sigma,
                "slippage_fee_perturbation_sigma": (
                    monte_carlo_slippage_fee_perturbation_sigma
                ),
            }
            if monte_carlo_perturbation_dimensions is not None:
                config_values["perturbation_dimensions"] = list(
                    monte_carlo_perturbation_dimensions
                )
            config = MonteCarloValidationConfig(
                **config_values,
            )
            results.append(generate_monte_carlo_source_report(config))
            continue

        config = HistoricalValidationWindowConfig(
            source_path=str(source_path),
            strategy_id=strategy_id,
            candidate_version=candidate_version,
            symbol=symbol,
            timeframe=timeframe,
            input_kind=input_kind,
            output_dir=str(output_dir),
            holdout_ratio=holdout_ratio,
            holdout_bars=holdout_bars,
            min_train_bars=min_train_bars,
            min_holdout_bars=min_holdout_bars,
            min_trades=min_trades,
            initial_balance=initial_balance,
            fast_window=fast_window,
            slow_window=slow_window,
            strategy_parameters=normalized_strategy_parameters,
            generated_at=generated_at,
            overwrite_existing=overwrite_existing_source_report,
        )
        results.append(generate_oos_source_report(config))

    status, message = _aggregate_status(results)
    report = _build_aggregate_report(
        generated_at=generated_at,
        source_paths=active_source_paths,
        output_dir=output_dir,
        report_output_path=report_output_path,
        results=[result.to_payload() for result in results],
        status=status,
        message=message,
        generation_kind=generation_kind,
        strategy_id=strategy_id,
        strategy_parameters=normalized_strategy_parameters,
    )
    return _write_report(report, report_output_path)


def _run_aggregate_source_report_generation(
    *,
    source_paths: list[Path],
    strategy_id: str,
    candidate_version: str,
    symbol: str,
    timeframe: str,
    input_kind: str,
    output_dir: str | Path,
    report_output_path: str | Path | None,
    generated_at: int,
    holdout_ratio: float,
    holdout_bars: int | None,
    min_train_bars: int,
    min_holdout_bars: int,
    min_trades: int,
    initial_balance: float,
    fast_window: int,
    slow_window: int,
    strategy_parameters: dict[str, Any],
    overwrite_existing_source_report: bool,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    min_walk_forward_windows: int,
    min_walk_forward_pass_rate: float,
    monte_carlo_run_count: int,
    monte_carlo_seed: int,
    min_monte_carlo_trades: int,
    min_monte_carlo_survival_rate: float,
    monte_carlo_max_drawdown_limit: float | None,
    monte_carlo_perturbation_dimensions: list[str] | None,
    monte_carlo_return_perturbation_sigma: float,
    monte_carlo_slippage_fee_perturbation_sigma: float,
    artifact_dir: str | Path | None,
    artifact_generation_output_path: str | Path | None,
    validator_output_path: str | Path | None,
    require_gate_pass: bool,
    min_net_pnl: float,
    max_drawdown: float | None,
    min_win_rate: float | None,
    min_out_of_sample_net_pnl: float,
    overwrite_existing_artifacts: bool,
) -> dict[str, Any]:
    if not source_paths:
        report = _build_aggregate_report(
            generated_at=generated_at,
            source_paths=[],
            output_dir=output_dir,
            report_output_path=report_output_path,
            results=[],
            status="SKIPPED",
            message="no local historical validation inputs were provided",
            generation_kind="aggregate",
            strategy_id=strategy_id,
            strategy_parameters=strategy_parameters,
        )
        return _write_report(
            _with_artifact_publication_fields(
                report,
                generated_source_report_path=None,
                artifact_generation_report=None,
                aggregate_reason_codes=["source_input_missing"],
            ),
            report_output_path,
        )

    results: list[SourceReportGenerationResult] = []
    with tempfile.TemporaryDirectory(
        prefix="strategy-validation-source-report-parts-"
    ) as temp_dir:
        temp_root = Path(temp_dir)
        for index, source_path in enumerate(source_paths, start=1):
            part_output_dir = temp_root / f"input-{index:03d}"
            results.extend(
                _generate_evidence_bundle_for_source_path(
                    source_path=source_path,
                    output_dir=part_output_dir,
                    strategy_id=strategy_id,
                    candidate_version=candidate_version,
                    symbol=symbol,
                    timeframe=timeframe,
                    input_kind=input_kind,
                    generated_at=generated_at,
                    holdout_ratio=holdout_ratio,
                    holdout_bars=holdout_bars,
                    min_train_bars=min_train_bars,
                    min_holdout_bars=min_holdout_bars,
                    min_trades=min_trades,
                    initial_balance=initial_balance,
                    fast_window=fast_window,
                    slow_window=slow_window,
                    strategy_parameters=strategy_parameters,
                    train_bars=train_bars,
                    test_bars=test_bars,
                    step_bars=step_bars,
                    min_walk_forward_windows=min_walk_forward_windows,
                    min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                    monte_carlo_run_count=monte_carlo_run_count,
                    monte_carlo_seed=monte_carlo_seed,
                    min_monte_carlo_trades=min_monte_carlo_trades,
                    min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
                    monte_carlo_max_drawdown_limit=monte_carlo_max_drawdown_limit,
                    monte_carlo_perturbation_dimensions=(
                        monte_carlo_perturbation_dimensions
                    ),
                    monte_carlo_return_perturbation_sigma=(
                        monte_carlo_return_perturbation_sigma
                    ),
                    monte_carlo_slippage_fee_perturbation_sigma=(
                        monte_carlo_slippage_fee_perturbation_sigma
                    ),
                )
            )

    sub_status, sub_message = _aggregate_status(results)
    aggregate_payload, missing_reason_codes = _complete_source_report_payload(
        results=results,
        generated_at=generated_at,
        source_paths=source_paths,
        strategy_id=strategy_id,
        candidate_version=candidate_version,
        symbol=symbol,
        timeframe=timeframe,
        min_walk_forward_windows=min_walk_forward_windows,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
        strategy_parameters=strategy_parameters,
    )
    reason_codes = sorted(
        {
            reason
            for result in results
            for reason in result.reason_codes
        }
        | set(missing_reason_codes)
    )
    source_report_path: Path | None = None
    artifact_generation_report: dict[str, Any] | None = None
    status = sub_status
    message = sub_message

    if aggregate_payload is None:
        status = "FAIL" if sub_status == "FAIL" else "SKIPPED"
        message = "aggregate source report missing required validation evidence"
    else:
        source_report_path = _complete_source_report_output_path(
            output_dir=output_dir,
            symbol=symbol,
            strategy_id=strategy_id,
            candidate_version=candidate_version,
        )
        if source_report_path.exists() and not overwrite_existing_source_report:
            source_report_path = None
            status = "FAIL"
            message = (
                "aggregate source report target already exists; pass "
                "overwrite_existing only for an intentional replayable rerun"
            )
            reason_codes = sorted(set(reason_codes) | {"source_report_target_exists"})
        else:
            source_report_path.parent.mkdir(parents=True, exist_ok=True)
            source_report_path.write_text(
                json.dumps(
                    aggregate_payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            artifact_generation_report = run_strategy_validation_artifact_generation(
                source_reports=[source_report_path],
                source_report_dirs=[],
                artifact_dir=artifact_dir,
                output_path=artifact_generation_output_path,
                validator_output_path=validator_output_path,
                timestamp=generated_at,
                require_gate_pass=require_gate_pass,
                min_trades=min_trades,
                min_net_pnl=min_net_pnl,
                max_drawdown=max_drawdown,
                min_win_rate=min_win_rate,
                min_out_of_sample_net_pnl=min_out_of_sample_net_pnl,
                min_walk_forward_windows=min_walk_forward_windows,
                min_walk_forward_pass_rate=min_walk_forward_pass_rate,
                min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
                overwrite_existing_artifacts=overwrite_existing_artifacts,
            )
            if artifact_generation_report.get("status") == "PASS":
                status = "PASS"
                message = (
                    "aggregate source report generated and artifact generation passed"
                )
            else:
                status = "FAIL"
                message = (
                    "aggregate source report generated, but artifact generation did "
                    "not pass strict validation"
                )

    report = _build_aggregate_report(
        generated_at=generated_at,
        source_paths=source_paths,
        output_dir=output_dir,
        report_output_path=report_output_path,
        results=[result.to_payload() for result in results],
        status=status,
        message=message,
        generation_kind="aggregate",
        strategy_id=strategy_id,
        strategy_parameters=strategy_parameters,
    )
    report["reason_codes"] = reason_codes
    return _write_report(
        _with_artifact_publication_fields(
            report,
            generated_source_report_path=source_report_path,
            artifact_generation_report=artifact_generation_report,
            aggregate_reason_codes=reason_codes,
        ),
        report_output_path,
    )


def _generate_evidence_bundle_for_source_path(
    *,
    source_path: Path,
    output_dir: Path,
    strategy_id: str,
    candidate_version: str,
    symbol: str,
    timeframe: str,
    input_kind: str,
    generated_at: int,
    holdout_ratio: float,
    holdout_bars: int | None,
    min_train_bars: int,
    min_holdout_bars: int,
    min_trades: int,
    initial_balance: float,
    fast_window: int,
    slow_window: int,
    strategy_parameters: dict[str, Any],
    train_bars: int,
    test_bars: int,
    step_bars: int,
    min_walk_forward_windows: int,
    min_walk_forward_pass_rate: float,
    monte_carlo_run_count: int,
    monte_carlo_seed: int,
    min_monte_carlo_trades: int,
    min_monte_carlo_survival_rate: float,
    monte_carlo_max_drawdown_limit: float | None,
    monte_carlo_perturbation_dimensions: list[str] | None,
    monte_carlo_return_perturbation_sigma: float,
    monte_carlo_slippage_fee_perturbation_sigma: float,
) -> list[SourceReportGenerationResult]:
    oos_config = HistoricalValidationWindowConfig(
        source_path=str(source_path),
        strategy_id=strategy_id,
        candidate_version=candidate_version,
        symbol=symbol,
        timeframe=timeframe,
        input_kind=input_kind,
        output_dir=str(output_dir),
        holdout_ratio=holdout_ratio,
        holdout_bars=holdout_bars,
        min_train_bars=min_train_bars,
        min_holdout_bars=min_holdout_bars,
        min_trades=min_trades,
        initial_balance=initial_balance,
        fast_window=fast_window,
        slow_window=slow_window,
        strategy_parameters=strategy_parameters,
        generated_at=generated_at,
        overwrite_existing=True,
    )
    walk_forward_config = WalkForwardWindowConfig(
        source_path=str(source_path),
        strategy_id=strategy_id,
        candidate_version=candidate_version,
        symbol=symbol,
        timeframe=timeframe,
        input_kind="klines" if input_kind == "auto" else input_kind,
        output_dir=str(output_dir),
        holdout_ratio=holdout_ratio,
        holdout_bars=holdout_bars,
        min_train_bars=min_train_bars,
        min_holdout_bars=min_holdout_bars,
        min_trades=min_trades,
        initial_balance=initial_balance,
        fast_window=fast_window,
        slow_window=slow_window,
        strategy_parameters=strategy_parameters,
        generated_at=generated_at,
        overwrite_existing=True,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        min_windows=min_walk_forward_windows,
        min_pass_rate=min_walk_forward_pass_rate,
    )
    config_values = {
        "source_path": str(source_path),
        "strategy_id": strategy_id,
        "candidate_version": candidate_version,
        "symbol": symbol,
        "timeframe": timeframe,
        "input_kind": input_kind,
        "output_dir": str(output_dir),
        "holdout_ratio": holdout_ratio,
        "holdout_bars": holdout_bars,
        "min_train_bars": min_train_bars,
        "min_holdout_bars": min_holdout_bars,
        "min_trades": min_trades,
        "initial_balance": initial_balance,
        "fast_window": fast_window,
        "slow_window": slow_window,
        "strategy_parameters": strategy_parameters,
        "generated_at": generated_at,
        "overwrite_existing": True,
        "run_count": monte_carlo_run_count,
        "seed": monte_carlo_seed,
        "min_trade_count": min_monte_carlo_trades,
        "survival_threshold": min_monte_carlo_survival_rate,
        "max_drawdown_limit": monte_carlo_max_drawdown_limit,
        "return_perturbation_sigma": monte_carlo_return_perturbation_sigma,
        "slippage_fee_perturbation_sigma": (
            monte_carlo_slippage_fee_perturbation_sigma
        ),
    }
    if monte_carlo_perturbation_dimensions is not None:
        config_values["perturbation_dimensions"] = list(
            monte_carlo_perturbation_dimensions
        )
    monte_carlo_config = MonteCarloValidationConfig(**config_values)
    return [
        generate_oos_source_report(oos_config),
        generate_walk_forward_source_report(walk_forward_config),
        generate_monte_carlo_source_report(monte_carlo_config),
    ]


def _complete_source_report_payload(
    *,
    results: list[SourceReportGenerationResult],
    generated_at: int,
    source_paths: list[Path],
    strategy_id: str,
    candidate_version: str,
    symbol: str,
    timeframe: str,
    min_walk_forward_windows: int,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
    strategy_parameters: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    pass_payloads = [
        result.source_report
        for result in results
        if result.status == "PASS" and result.source_report
    ]
    oos_payloads = [
        payload
        for payload in pass_payloads
        if payload.get("monte_carlo_validation") is None
        and _payload_has_slice_kind(payload, "out_of_sample")
    ]
    walk_forward_payloads = [
        payload
        for payload in pass_payloads
        if _payload_has_slice_kind(payload, "walk_forward")
    ]
    monte_carlo_payloads = [
        payload
        for payload in pass_payloads
        if payload.get("monte_carlo_validation") is not None
        and payload.get("monte_carlo_survival_rate") is not None
    ]

    oos_slices = _slices_by_kind(oos_payloads, "out_of_sample")
    if not oos_slices:
        oos_slices = _slices_by_kind(monte_carlo_payloads, "out_of_sample")
    walk_forward_slices = _slices_by_kind(walk_forward_payloads, "walk_forward")
    monte_carlo_payload = monte_carlo_payloads[0] if monte_carlo_payloads else None

    missing_reason_codes = _aggregate_missing_evidence_reason_codes(
        oos_slices=oos_slices,
        walk_forward_slices=walk_forward_slices,
        monte_carlo_payload=monte_carlo_payload,
        min_walk_forward_windows=min_walk_forward_windows,
        min_walk_forward_pass_rate=min_walk_forward_pass_rate,
        min_monte_carlo_survival_rate=min_monte_carlo_survival_rate,
    )
    if missing_reason_codes:
        return None, missing_reason_codes

    validation_slices = oos_slices + walk_forward_slices
    summary = _summary_from_slice_payloads(validation_slices)
    source_report_id = _aggregate_source_report_id(
        strategy_id=strategy_id,
        candidate_version=candidate_version,
        symbol=symbol,
        source_paths=source_paths,
    )
    trace_timestamp = _latest_trace_timestamp(pass_payloads)
    source_report = StrategyValidationArtifactSourceReport(
        report_id=source_report_id,
        strategy_id=strategy_id,
        candidate_version=candidate_version,
        symbol=symbol,
        generated_at=generated_at,
        source_report_id=source_report_id,
        source_path=str(source_paths[0]) if source_paths else None,
        summary=StrategyValidationSourceSummary(**summary),
        validation_slices=validation_slices,
        monte_carlo_survival_rate=monte_carlo_payload.get(
            "monte_carlo_survival_rate"
        ),
        monte_carlo_validation=monte_carlo_payload.get("monte_carlo_validation"),
        trace=TraceContext(
            run_id=source_report_id,
            source=PayloadSource.BACKTEST,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=trace_timestamp,
        ),
    )
    payload = source_report.to_payload()
    walk_forward_pass_count = sum(
        1 for item in walk_forward_slices if _validation_slice_payload_passes(item)
    )
    payload["provenance"] = {
        "generation_scope": "H-OPT-016",
        "input_kind": "aggregate",
        "source_paths": [str(path) for path in source_paths],
        "source_fingerprints": _file_fingerprints(source_paths),
        "component_source_reports": _component_source_reports(results),
        "strategy_id": strategy_id,
        "strategy_parameters": strategy_parameters,
        "strategy_metadata": candidate_strategy_metadata(
            strategy_id,
            strategy_parameters,
        ),
        "candidate_version": candidate_version,
        "symbol": symbol,
        "timeframe": timeframe,
    }
    payload["walk_forward_window_count"] = len(walk_forward_slices)
    payload["walk_forward_pass_count"] = walk_forward_pass_count
    payload["walk_forward_pass_rate"] = (
        walk_forward_pass_count / len(walk_forward_slices)
        if walk_forward_slices
        else None
    )
    payload["safety_flags"] = _aggregate_safety_flags(
        [result.to_payload() for result in results]
    )
    return payload, []


def _payload_has_slice_kind(payload: dict[str, Any], kind: str) -> bool:
    return bool(_slices_by_kind([payload], kind))


def _slices_by_kind(
    payloads: list[dict[str, Any]],
    kind: str,
) -> list[dict[str, Any]]:
    slices: list[dict[str, Any]] = []
    for payload in payloads:
        for item in payload.get("validation_slices") or []:
            if not isinstance(item, dict):
                continue
            if item.get("kind") == kind:
                slices.append(dict(item))
    return slices


def _aggregate_missing_evidence_reason_codes(
    *,
    oos_slices: list[dict[str, Any]],
    walk_forward_slices: list[dict[str, Any]],
    monte_carlo_payload: dict[str, Any] | None,
    min_walk_forward_windows: int,
    min_walk_forward_pass_rate: float,
    min_monte_carlo_survival_rate: float,
) -> list[str]:
    reason_codes = []
    if not oos_slices:
        reason_codes.append("missing_out_of_sample_validation")
    if not walk_forward_slices:
        reason_codes.append("missing_walk_forward_validation")
    elif len(walk_forward_slices) < min_walk_forward_windows:
        reason_codes.append("insufficient_walk_forward_windows")
    walk_forward_pass_rate = _walk_forward_pass_rate(walk_forward_slices)
    if (
        walk_forward_pass_rate is not None
        and walk_forward_pass_rate < min_walk_forward_pass_rate
    ):
        reason_codes.append("walk_forward_pass_rate_below_threshold")
    if monte_carlo_payload is None:
        reason_codes.append("missing_monte_carlo_validation")
    elif (
        float(monte_carlo_payload.get("monte_carlo_survival_rate") or 0.0)
        < min_monte_carlo_survival_rate
    ):
        reason_codes.append("monte_carlo_survival_rate_below_threshold")
    return reason_codes


def _walk_forward_pass_rate(slices: list[dict[str, Any]]) -> float | None:
    if not slices:
        return None
    pass_count = sum(1 for item in slices if _validation_slice_payload_passes(item))
    return pass_count / len(slices)


def _validation_slice_payload_passes(item: dict[str, Any]) -> bool:
    return int(item.get("trade_count") or 0) >= 1 and float(
        item.get("total_net_pnl") or 0.0
    ) >= 0.0


def _summary_from_slice_payloads(slices: list[dict[str, Any]]) -> dict[str, Any]:
    trade_count = sum(int(item.get("trade_count") or 0) for item in slices)
    total_net_pnl = sum(float(item.get("total_net_pnl") or 0.0) for item in slices)
    max_drawdown = max(
        (float(item.get("max_drawdown") or 0.0) for item in slices),
        default=0.0,
    )
    if trade_count > 0:
        win_rate = sum(
            float(item.get("win_rate") or 0.0) * int(item.get("trade_count") or 0)
            for item in slices
        ) / trade_count
    else:
        win_rate = 0.0
    sharpe_values = [
        float(item["sharpe_ratio"])
        for item in slices
        if item.get("sharpe_ratio") is not None
    ]
    return {
        "trade_count": trade_count,
        "total_net_pnl": total_net_pnl,
        "max_drawdown": max_drawdown,
        "win_rate": min(max(win_rate, 0.0), 1.0),
        "sharpe_ratio": (
            sum(sharpe_values) / len(sharpe_values) if sharpe_values else None
        ),
    }


def _component_source_reports(
    results: list[SourceReportGenerationResult],
) -> list[dict[str, Any]]:
    entries = []
    for result in results:
        entries.append(
            {
                "status": result.status,
                "message": result.message,
                "source_path": result.source_path,
                "input_kind": result.input_kind,
                "source_report_path": result.source_report_path,
                "source_report_id": result.source_report_id,
                "reason_codes": list(result.reason_codes),
            }
        )
    return entries


def _latest_trace_timestamp(payloads: list[dict[str, Any]]) -> int | None:
    timestamps = []
    for payload in payloads:
        trace = payload.get("trace")
        if isinstance(trace, dict) and trace.get("timestamp") is not None:
            timestamps.append(int(trace["timestamp"]))
    return max(timestamps) if timestamps else None


def _aggregate_source_report_id(
    *,
    strategy_id: str,
    candidate_version: str,
    symbol: str,
    source_paths: list[Path],
) -> str:
    digest = hashlib.sha256(
        json.dumps(
            _file_fingerprints(source_paths),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return (
        "aggregate-source-report:"
        f"{_safe_token(strategy_id)}:"
        f"{_safe_token(candidate_version)}:"
        f"{_safe_token(symbol)}:"
        f"{digest}"
    )


def _complete_source_report_output_path(
    *,
    output_dir: str | Path,
    symbol: str,
    strategy_id: str,
    candidate_version: str,
) -> Path:
    return (
        Path(output_dir)
        / _safe_token(symbol)
        / _safe_token(strategy_id)
        / f"{_safe_token(candidate_version)}.json"
    )


def _with_artifact_publication_fields(
    report: dict[str, Any],
    *,
    generated_source_report_path: Path | None,
    artifact_generation_report: dict[str, Any] | None,
    aggregate_reason_codes: list[str],
) -> dict[str, Any]:
    validator_report = (
        artifact_generation_report.get("validator_report")
        if artifact_generation_report is not None
        else None
    )
    generated_artifacts = (
        artifact_generation_report.get("generated") or []
        if artifact_generation_report is not None
        else []
    )
    artifact_reason_codes: list[str] = []
    if artifact_generation_report is not None:
        artifact_reason_codes.extend(
            artifact_generation_report.get("h_opt_005_blockers") or []
        )
        if artifact_generation_report.get("status") != "PASS":
            artifact_reason_codes.extend(
                (artifact_generation_report.get("validator_reason_code_counts") or {}).keys()
            )

    reason_codes = sorted(
        set(report.get("reason_codes") or [])
        | set(aggregate_reason_codes)
        | set(artifact_reason_codes)
    )
    report["reason_codes"] = reason_codes
    report["aggregate_source_report_path"] = (
        str(generated_source_report_path)
        if generated_source_report_path is not None
        else None
    )
    report["source_report_paths"] = (
        [str(generated_source_report_path)]
        if generated_source_report_path is not None
        else []
    )
    report["generated_source_report_count"] = (
        1 if generated_source_report_path is not None else 0
    )
    report["source_report_fingerprints"] = _file_fingerprints(
        [generated_source_report_path] if generated_source_report_path else []
    )
    report["artifact_generation_status"] = (
        artifact_generation_report.get("status")
        if artifact_generation_report is not None
        else None
    )
    report["artifact_generation_report_path"] = (
        artifact_generation_report.get("output_path")
        if artifact_generation_report is not None
        else None
    )
    report["artifact_count"] = (
        artifact_generation_report.get("generated_artifact_count")
        if artifact_generation_report is not None
        else 0
    )
    report["generated_artifact_count"] = report["artifact_count"]
    report["artifact_paths"] = [
        entry.get("artifact_path") for entry in generated_artifacts
    ]
    report["artifact_fingerprints"] = (
        artifact_generation_report.get("generated_artifact_fingerprints") or []
        if artifact_generation_report is not None
        else []
    )
    report["validator_status"] = (
        validator_report.get("status")
        if isinstance(validator_report, dict)
        else None
    )
    report["validator_artifact_count"] = (
        validator_report.get("artifact_count")
        if isinstance(validator_report, dict)
        else 0
    )
    report["h_opt_005_ready"] = (
        artifact_generation_report.get("h_opt_005_ready")
        if artifact_generation_report is not None
        else False
    )
    report["h_opt_005_blockers"] = (
        artifact_generation_report.get("h_opt_005_blockers") or []
        if artifact_generation_report is not None
        else reason_codes
    )
    report["artifact_generation_report"] = artifact_generation_report
    return report


def _build_aggregate_report(
    *,
    generated_at: int,
    source_paths: list[Path],
    output_dir: str | Path,
    report_output_path: str | Path | None,
    results: list[dict[str, Any]],
    status: str,
    message: str,
    generation_kind: str = "oos",
    strategy_id: str | None = None,
    strategy_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_results = [result for result in results if result["status"] == "PASS"]
    skipped_results = [result for result in results if result["status"] == "SKIPPED"]
    failed_results = [result for result in results if result["status"] == "FAIL"]
    safety_flags = _aggregate_safety_flags(results)
    return {
        "generated_at": generated_at,
        "status": status,
        "success": status == "PASS",
        "message": message,
        "source_report_generation_scope": (
            "H-OPT-016"
            if generation_kind == "aggregate"
            else "H-OPT-014"
            if generation_kind == "walk_forward"
            else "H-OPT-015"
            if generation_kind == "monte_carlo"
            else "H-OPT-013"
        ),
        "generation_kind": generation_kind,
        "strategy_id": strategy_id,
        "strategy_parameters": dict(strategy_parameters or {}),
        "source_paths": [str(path) for path in source_paths],
        "source_input_count": len(source_paths),
        "output_dir": str(output_dir),
        "report_output_path": str(report_output_path) if report_output_path is not None else None,
        "generated_source_report_count": len(generated_results),
        "skipped_source_input_count": len(skipped_results),
        "failed_source_input_count": len(failed_results),
        "source_report_paths": [
            result["source_report_path"]
            for result in generated_results
            if result.get("source_report_path")
        ],
        "reason_codes": sorted(
            {
                reason
                for result in results
                for reason in result.get("reason_codes", [])
            }
        ),
        "safety_flags": safety_flags,
        "network_access_used": safety_flags["network_access_used"],
        "real_credentials_read": safety_flags["real_credentials_read"],
        "broker_called": safety_flags["broker_called"],
        "live_orders_sent": safety_flags["live_orders_sent"],
        "analytics_modified_live_state": safety_flags["analytics_modified_live_state"],
        "contains_real_credentials": safety_flags["contains_real_credentials"],
        "results": results,
    }


def _aggregate_status(results: list[SourceReportGenerationResult]) -> tuple[str, str]:
    if any(result.status == "FAIL" for result in results):
        return "FAIL", "one or more source report inputs failed"
    if any(result.status == "PASS" for result in results):
        if all(result.status == "PASS" for result in results):
            return "PASS", "source report generation passed"
        return "PASS", "source report generation passed with skipped inputs"
    return "SKIPPED", "no source report was generated"


def _resolve_strategy_parameters(
    *,
    strategy_id: str,
    strategy_parameters: dict[str, Any] | None,
    fast_window: int,
    slow_window: int,
) -> dict[str, Any]:
    parameters = dict(strategy_parameters or {})
    if strategy_id == "ma_crossover":
        parameters.setdefault("fast_window", fast_window)
        parameters.setdefault("slow_window", slow_window)
    return normalize_candidate_strategy_parameters(strategy_id, parameters)


def _parse_strategy_params_json(value: str | None) -> dict[str, Any]:
    if value is None or not str(value).strip():
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(
            "--strategy-params-json must be a valid JSON object"
        ) from exc
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError(
            "--strategy-params-json must be a valid JSON object"
        )
    return payload


def _zero_safety_flags() -> dict[str, bool]:
    return {
        "network_access_used": False,
        "real_credentials_read": False,
        "broker_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
    }


def _aggregate_safety_flags(results: list[dict[str, Any]]) -> dict[str, bool]:
    keys = [
        "network_access_used",
        "real_credentials_read",
        "broker_called",
        "live_orders_sent",
        "analytics_modified_live_state",
        "contains_real_credentials",
    ]
    return {
        key: any(bool(result.get("safety_flags", {}).get(key)) for result in results)
        for key in keys
    }


def _write_report(
    report: dict[str, Any],
    report_output_path: str | Path | None,
) -> dict[str, Any]:
    if report_output_path is not None:
        path = Path(report_output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def _file_fingerprints(paths: list[Any]) -> list[dict[str, Any]]:
    fingerprints = []
    for path in paths:
        if path is None:
            continue
        path_obj = Path(path)
        try:
            stat_result = path_obj.stat()
        except OSError:
            fingerprints.append(
                {
                    "path": str(path_obj),
                    "exists": False,
                    "size_bytes": None,
                    "sha256": None,
                }
            )
            continue
        fingerprints.append(
            {
                "path": str(path_obj),
                "exists": True,
                "size_bytes": stat_result.st_size,
                "sha256": _file_sha256(path_obj),
            }
        )
    return fingerprints


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_token(value: Any) -> str:
    token = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in str(value)
    ).strip("_")
    return token or "unknown"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate local historical strategy validation source reports "
            "without network, broker, or live-order side effects."
        )
    )
    parser.add_argument(
        "--source-path",
        "--input",
        action="append",
        default=[],
        help="Path to a local Kline JSON/JSONL or explicit OOS backtest result JSON.",
    )
    parser.add_argument("--strategy-id", default="ma_crossover")
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument(
        "--input-kind",
        choices=["auto", "klines", "backtest_result"],
        default="auto",
    )
    parser.add_argument(
        "--generation-kind",
        choices=["oos", "walk_forward", "monte_carlo", "aggregate"],
        default="oos",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_SOURCE_REPORT_DIR))
    parser.add_argument("--report-output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--no-report-output", action="store_true")
    parser.add_argument("--timestamp", type=int)
    parser.add_argument("--holdout-ratio", type=float, default=0.3)
    parser.add_argument("--holdout-bars", type=int)
    parser.add_argument("--min-train-bars", type=int, default=40)
    parser.add_argument("--min-holdout-bars", type=int, default=20)
    parser.add_argument("--min-trades", type=int, default=1)
    parser.add_argument("--initial-balance", type=float, default=10000.0)
    parser.add_argument("--fast-window", type=int, default=1)
    parser.add_argument("--slow-window", type=int, default=2)
    parser.add_argument(
        "--strategy-params-json",
        type=_parse_strategy_params_json,
        default={},
        help="JSON object containing strategy-specific candidate parameters.",
    )
    parser.add_argument("--train-bars", type=int, default=40)
    parser.add_argument("--test-bars", type=int, default=20)
    parser.add_argument("--step-bars", type=int, default=20)
    parser.add_argument("--min-walk-forward-windows", type=int, default=3)
    parser.add_argument("--min-walk-forward-pass-rate", type=float, default=0.67)
    parser.add_argument("--monte-carlo-run-count", type=int, default=500)
    parser.add_argument("--monte-carlo-seed", type=int, default=42)
    parser.add_argument("--min-monte-carlo-trades", type=int, default=10)
    parser.add_argument("--min-monte-carlo-survival-rate", type=float, default=0.8)
    parser.add_argument("--monte-carlo-max-drawdown-limit", type=float)
    parser.add_argument(
        "--monte-carlo-perturbation-dimension",
        action="append",
        dest="monte_carlo_perturbation_dimensions",
        default=None,
        help=(
            "Monte Carlo perturbation dimension; repeat for multiple values. "
            "Supported: trade_order_shuffle, return_perturbation, "
            "slippage_fee_perturbation."
        ),
    )
    parser.add_argument(
        "--monte-carlo-return-perturbation-sigma",
        type=float,
        default=0.05,
    )
    parser.add_argument(
        "--monte-carlo-slippage-fee-perturbation-sigma",
        type=float,
        default=0.02,
    )
    parser.add_argument(
        "--overwrite-existing-source-report",
        action="store_true",
        help="Replace an existing generated source report target for an intentional rerun.",
    )
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument(
        "--artifact-generation-output",
        default=str(DEFAULT_ARTIFACT_GENERATION_OUTPUT_PATH),
    )
    parser.add_argument(
        "--validator-output",
        default=str(DEFAULT_VALIDATOR_OUTPUT_PATH),
    )
    parser.add_argument("--no-artifact-generation-output", action="store_true")
    parser.add_argument("--no-validator-output", action="store_true")
    parser.add_argument("--require-gate-pass", action="store_true")
    parser.add_argument("--min-net-pnl", type=float, default=0.0)
    parser.add_argument("--max-drawdown", type=float)
    parser.add_argument("--min-win-rate", type=float)
    parser.add_argument("--min-out-of-sample-net-pnl", type=float, default=0.0)
    parser.add_argument(
        "--overwrite-existing-artifacts",
        action="store_true",
        help="Allow generated artifacts to replace existing artifact targets.",
    )
    args = parser.parse_args(argv)

    report = run_strategy_validation_source_report_generation(
        source_paths=args.source_path,
        strategy_id=args.strategy_id,
        candidate_version=args.candidate_version,
        symbol=args.symbol,
        timeframe=args.timeframe,
        input_kind=args.input_kind,
        output_dir=args.output_dir,
        report_output_path=None if args.no_report_output else args.report_output,
        timestamp=args.timestamp,
        holdout_ratio=args.holdout_ratio,
        holdout_bars=args.holdout_bars,
        min_train_bars=args.min_train_bars,
        min_holdout_bars=args.min_holdout_bars,
        min_trades=args.min_trades,
        initial_balance=args.initial_balance,
        fast_window=args.fast_window,
        slow_window=args.slow_window,
        strategy_parameters=args.strategy_params_json,
        overwrite_existing_source_report=args.overwrite_existing_source_report,
        generation_kind=args.generation_kind,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        step_bars=args.step_bars,
        min_walk_forward_windows=args.min_walk_forward_windows,
        min_walk_forward_pass_rate=args.min_walk_forward_pass_rate,
        monte_carlo_run_count=args.monte_carlo_run_count,
        monte_carlo_seed=args.monte_carlo_seed,
        min_monte_carlo_trades=args.min_monte_carlo_trades,
        min_monte_carlo_survival_rate=args.min_monte_carlo_survival_rate,
        monte_carlo_max_drawdown_limit=args.monte_carlo_max_drawdown_limit,
        monte_carlo_perturbation_dimensions=args.monte_carlo_perturbation_dimensions,
        monte_carlo_return_perturbation_sigma=args.monte_carlo_return_perturbation_sigma,
        monte_carlo_slippage_fee_perturbation_sigma=(
            args.monte_carlo_slippage_fee_perturbation_sigma
        ),
        artifact_dir=args.artifact_dir,
        artifact_generation_output_path=(
            None
            if args.no_artifact_generation_output
            else args.artifact_generation_output
        ),
        validator_output_path=None if args.no_validator_output else args.validator_output,
        require_gate_pass=args.require_gate_pass,
        min_net_pnl=args.min_net_pnl,
        max_drawdown=args.max_drawdown,
        min_win_rate=args.min_win_rate,
        min_out_of_sample_net_pnl=args.min_out_of_sample_net_pnl,
        overwrite_existing_artifacts=args.overwrite_existing_artifacts,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] == "SKIPPED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
