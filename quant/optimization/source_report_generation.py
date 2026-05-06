import json
import math
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

from quant.account.models.crypto import CryptoAccount
from quant.backtest.engine import BacktestEngine
from quant.data.quality import validate_klines
from quant.data.schemas.market import Kline
from quant.execution.engine import ExecutionEngine
from quant.optimization.artifact_generation import (
    DEFAULT_SOURCE_REPORT_DIR,
    StrategyValidationArtifactSourceReport,
    StrategyValidationSourceSummary,
)
from quant.optimization.candidate_strategies import (
    candidate_strategy_metadata,
    create_candidate_strategy,
    normalize_candidate_strategy_parameters,
)
from quant.risk.risk_manager import RiskManager
from quant.schemas import (
    MonteCarloSimulationMethod,
    MonteCarloValidation,
    PayloadSource,
    StrategyValidationSlice,
    StrategyValidationSliceKind,
)
from quant.schemas.base import SmartQTFModel, TraceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SECRET_KEY_PATTERNS = (
    "api_key",
    "apikey",
    "api_secret",
    "secret_key",
    "access_key",
    "access_secret",
    "passphrase",
    "password",
    "private_key",
    "credential",
    "credentials",
    "auth_token",
    "bearer_token",
)
_DANGEROUS_TRUE_FLAGS = {
    "live_orders_sent",
    "live_order_submission",
    "analytics_modified_live_state",
    "contains_real_credentials",
    "broker_called",
    "exchange_order_submitted",
    "real_order_submitted",
}

_SUPPORTED_MONTE_CARLO_PERTURBATION_DIMENSIONS = {
    "trade_order_shuffle",
    "return_perturbation",
    "slippage_fee_perturbation",
}
_DEFAULT_MONTE_CARLO_PERTURBATION_DIMENSIONS = [
    "trade_order_shuffle",
    "return_perturbation",
    "slippage_fee_perturbation",
]


class HistoricalValidationWindowConfig(SmartQTFModel):
    source_path: str
    strategy_id: str
    candidate_version: str
    symbol: str
    timeframe: str = "1m"
    input_kind: str = "auto"
    output_dir: str = str(DEFAULT_SOURCE_REPORT_DIR)
    holdout_ratio: float = 0.3
    holdout_bars: Optional[int] = None
    min_train_bars: int = 40
    min_holdout_bars: int = 20
    min_trades: int = 1
    initial_balance: float = 10000.0
    fast_window: int = 1
    slow_window: int = 2
    strategy_parameters: dict[str, Any] = {}
    generated_at: Optional[int] = None
    overwrite_existing: bool = False

    def __init__(self, **data):
        super().__init__(**data)
        for field_name in ("source_path", "strategy_id", "candidate_version", "symbol"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.input_kind not in {"auto", "klines", "backtest_result"}:
            raise ValueError("input_kind must be auto, klines, or backtest_result")
        if self.holdout_ratio <= 0.0 or self.holdout_ratio >= 1.0:
            raise ValueError("holdout_ratio must be in (0.0, 1.0)")
        if self.holdout_bars is not None and self.holdout_bars <= 0:
            raise ValueError("holdout_bars must be positive when provided")
        if self.min_train_bars <= 0:
            raise ValueError("min_train_bars must be positive")
        if self.min_holdout_bars <= 0:
            raise ValueError("min_holdout_bars must be positive")
        if self.min_trades < 0:
            raise ValueError("min_trades must be non-negative")
        if self.initial_balance <= 0.0:
            raise ValueError("initial_balance must be positive")
        if self.fast_window <= 0 or self.slow_window <= 0:
            raise ValueError("MA windows must be positive")
        if not isinstance(self.strategy_parameters, dict):
            raise ValueError("strategy_parameters must be a JSON object")

        parameters = dict(self.strategy_parameters)
        if self.strategy_id == "ma_crossover":
            parameters.setdefault("fast_window", self.fast_window)
            parameters.setdefault("slow_window", self.slow_window)
        self.strategy_parameters = normalize_candidate_strategy_parameters(
            self.strategy_id,
            parameters,
        )
        if self.strategy_id == "ma_crossover":
            self.fast_window = int(self.strategy_parameters["fast_window"])
            self.slow_window = int(self.strategy_parameters["slow_window"])


class WalkForwardWindowConfig(HistoricalValidationWindowConfig):
    train_bars: int = 40
    test_bars: int = 20
    step_bars: int = 20
    min_windows: int = 3
    min_pass_rate: float = 0.67

    def __init__(self, **data):
        super().__init__(**data)
        if self.input_kind != "klines":
            raise ValueError("walk-forward input_kind must be klines")
        if self.train_bars <= 0:
            raise ValueError("train_bars must be positive")
        if self.test_bars <= 0:
            raise ValueError("test_bars must be positive")
        if self.step_bars <= 0:
            raise ValueError("step_bars must be positive")
        if self.min_windows <= 0:
            raise ValueError("min_windows must be positive")
        if self.min_pass_rate < 0.0 or self.min_pass_rate > 1.0:
            raise ValueError("min_pass_rate must be between 0.0 and 1.0")


class MonteCarloValidationConfig(HistoricalValidationWindowConfig):
    run_count: int = 500
    seed: int = 42
    min_trade_count: int = 10
    survival_threshold: float = 0.8
    max_drawdown_limit: Optional[float] = None
    perturbation_dimensions: list[str] = _DEFAULT_MONTE_CARLO_PERTURBATION_DIMENSIONS
    return_perturbation_sigma: float = 0.05
    slippage_fee_perturbation_sigma: float = 0.02

    def __init__(self, **data):
        super().__init__(**data)
        if self.run_count <= 0:
            raise ValueError("run_count must be positive")
        if self.min_trade_count <= 0:
            raise ValueError("min_trade_count must be positive")
        if self.survival_threshold < 0.0 or self.survival_threshold > 1.0:
            raise ValueError("survival_threshold must be between 0.0 and 1.0")
        if self.max_drawdown_limit is not None and self.max_drawdown_limit < 0.0:
            raise ValueError("max_drawdown_limit must be non-negative when provided")
        if self.return_perturbation_sigma < 0.0:
            raise ValueError("return_perturbation_sigma must be non-negative")
        if self.slippage_fee_perturbation_sigma < 0.0:
            raise ValueError("slippage_fee_perturbation_sigma must be non-negative")
        if not self.perturbation_dimensions:
            raise ValueError("perturbation_dimensions must not be empty")
        normalized = []
        for dimension in self.perturbation_dimensions:
            token = str(dimension).strip()
            if not token:
                raise ValueError("perturbation_dimensions must not contain empty values")
            normalized.append(token)
        unknown = sorted(
            set(normalized) - _SUPPORTED_MONTE_CARLO_PERTURBATION_DIMENSIONS
        )
        if unknown:
            raise ValueError(
                "unsupported perturbation_dimensions: " + ", ".join(unknown)
            )
        self.perturbation_dimensions = normalized


class SourceReportGenerationResult(SmartQTFModel):
    status: str
    success: bool
    message: str
    generated_at: int
    reason_codes: list[str] = []
    source_path: Optional[str] = None
    input_kind: Optional[str] = None
    source_report_path: Optional[str] = None
    source_report_id: Optional[str] = None
    window: Optional[dict[str, Any]] = None
    metrics: dict[str, Any] = {}
    safety_flags: dict[str, bool] = {}
    source_report: Optional[dict[str, Any]] = None


def generate_oos_source_report(
    config: HistoricalValidationWindowConfig,
) -> SourceReportGenerationResult:
    generated_at = config.generated_at if config.generated_at is not None else int(time.time())
    source_path = Path(config.source_path)
    safety_flags = _default_safety_flags()

    if not source_path.exists():
        return _result(
            status="SKIPPED",
            message="historical validation input does not exist",
            generated_at=generated_at,
            config=config,
            reason_codes=["source_input_missing"],
            safety_flags=safety_flags,
        )

    try:
        _guard_local_input_path(source_path)
        payload = _load_json_or_jsonl(source_path)
        _guard_input_payload_safety(payload)
        input_kind = _resolve_input_kind(payload, config.input_kind)
        if input_kind == "klines":
            return _generate_from_kline_payload(
                payload=payload,
                config=config,
                source_path=source_path,
                generated_at=generated_at,
                safety_flags=safety_flags,
            )
        if input_kind == "backtest_result":
            return _generate_from_backtest_result_payload(
                payload=payload,
                config=config,
                source_path=source_path,
                generated_at=generated_at,
                safety_flags=safety_flags,
            )
        return _result(
            status="SKIPPED",
            message="historical validation input kind is not supported",
            generated_at=generated_at,
            config=config,
            reason_codes=["unsupported_source_input_kind"],
            safety_flags=safety_flags,
            input_kind=input_kind,
        )
    except ValueError as exc:
        return _result(
            status="FAIL",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=["source_report_generation_failed"],
            safety_flags=safety_flags,
        )


def generate_walk_forward_source_report(
    config: WalkForwardWindowConfig,
) -> SourceReportGenerationResult:
    generated_at = config.generated_at if config.generated_at is not None else int(time.time())
    source_path = Path(config.source_path)
    safety_flags = _default_safety_flags()

    if not source_path.exists():
        return _result(
            status="SKIPPED",
            message="historical validation input does not exist",
            generated_at=generated_at,
            config=config,
            reason_codes=["source_input_missing"],
            safety_flags=safety_flags,
        )

    try:
        _guard_local_input_path(source_path)
        payload = _load_json_or_jsonl(source_path)
        _guard_input_payload_safety(payload)
        input_kind = _resolve_input_kind(payload, config.input_kind)
        if input_kind != "klines":
            return _result(
                status="SKIPPED",
                message="walk-forward generation requires local kline input",
                generated_at=generated_at,
                config=config,
                reason_codes=["unsupported_source_input_kind"],
                safety_flags=safety_flags,
                input_kind=input_kind,
            )
        return _generate_walk_forward_from_kline_payload(
            payload=payload,
            config=config,
            source_path=source_path,
            generated_at=generated_at,
            safety_flags=safety_flags,
        )
    except ValueError as exc:
        return _result(
            status="FAIL",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=["source_report_generation_failed"],
            safety_flags=safety_flags,
        )


def generate_monte_carlo_source_report(
    config: MonteCarloValidationConfig,
) -> SourceReportGenerationResult:
    generated_at = config.generated_at if config.generated_at is not None else int(time.time())
    source_path = Path(config.source_path)
    safety_flags = _default_safety_flags()

    if not source_path.exists():
        return _result(
            status="SKIPPED",
            message="historical validation input does not exist",
            generated_at=generated_at,
            config=config,
            reason_codes=["source_input_missing"],
            safety_flags=safety_flags,
        )

    try:
        _guard_local_input_path(source_path)
        payload = _load_json_or_jsonl(source_path)
        _guard_input_payload_safety(payload)
        input_kind = _resolve_input_kind(payload, config.input_kind)
        if input_kind == "klines":
            return _generate_monte_carlo_from_kline_payload(
                payload=payload,
                config=config,
                source_path=source_path,
                generated_at=generated_at,
                safety_flags=safety_flags,
            )
        if input_kind == "backtest_result":
            return _generate_monte_carlo_from_backtest_result_payload(
                payload=payload,
                config=config,
                source_path=source_path,
                generated_at=generated_at,
                safety_flags=safety_flags,
            )
        return _result(
            status="SKIPPED",
            message="historical validation input kind is not supported",
            generated_at=generated_at,
            config=config,
            reason_codes=["unsupported_source_input_kind"],
            safety_flags=safety_flags,
            input_kind=input_kind,
        )
    except ValueError as exc:
        return _result(
            status="FAIL",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=["source_report_generation_failed"],
            safety_flags=safety_flags,
        )


def split_historical_validation_window(
    klines: list[Kline],
    config: HistoricalValidationWindowConfig,
) -> tuple[list[Kline], list[Kline], dict[str, Any]]:
    if len(klines) < config.min_train_bars + config.min_holdout_bars:
        raise ValueError("insufficient_bars_for_train_and_holdout")

    holdout_count = config.holdout_bars
    if holdout_count is None:
        holdout_count = max(
            config.min_holdout_bars,
            int(math.ceil(len(klines) * config.holdout_ratio)),
        )
    holdout_count = min(holdout_count, len(klines) - config.min_train_bars)
    if holdout_count < config.min_holdout_bars:
        raise ValueError("insufficient_holdout_bars")

    train = klines[: len(klines) - holdout_count]
    holdout = klines[len(klines) - holdout_count :]
    if len(train) < config.min_train_bars:
        raise ValueError("insufficient_train_bars")
    if not train or not holdout or train[-1].timestamp >= holdout[0].timestamp:
        raise ValueError("holdout_window_not_independent")

    window = {
        "train": _window_payload(train),
        "holdout": _window_payload(holdout),
        "total_bar_count": len(klines),
        "train_bar_count": len(train),
        "holdout_bar_count": len(holdout),
        "holdout_ratio": config.holdout_ratio,
    }
    return train, holdout, window


def generate_walk_forward_slices(
    klines: list[Kline],
    config: WalkForwardWindowConfig,
) -> tuple[list[StrategyValidationSlice], dict[str, Any]]:
    windows = _build_walk_forward_windows(klines, config)
    validation_slices = []
    window_payloads = []

    for index, window in enumerate(windows, start=1):
        backtest_result = _run_default_holdout_backtest(window["test_klines"], config)
        if backtest_result.get("status") != "completed":
            raise ValueError("walk_forward_backtest_failed")
        metrics = _metrics_from_completed_backtest_result(
            backtest_result,
            initial_balance=config.initial_balance,
        )
        window_name = (
            f"wf-{index:03d}-"
            f"{window['test']['start_timestamp']}-"
            f"{window['test']['end_timestamp']}"
        )
        validation_slices.append(
            StrategyValidationSlice(
                name=window_name,
                kind=StrategyValidationSliceKind.WALK_FORWARD,
                **metrics,
            )
        )
        window_payloads.append(
            {
                "name": window_name,
                "index": index,
                "train": window["train"],
                "test": window["test"],
                "metrics": metrics,
            }
        )

    pass_count = sum(
        1
        for item in validation_slices
        if _validation_slice_passes(item, min_trades=config.min_trades)
    )
    pass_rate = pass_count / len(validation_slices) if validation_slices else 0.0
    metadata = {
        "walk_forward_window_count": len(validation_slices),
        "walk_forward_pass_count": pass_count,
        "walk_forward_pass_rate": pass_rate,
        "walk_forward_windows": window_payloads,
        "train_bars": config.train_bars,
        "test_bars": config.test_bars,
        "step_bars": config.step_bars,
        "min_windows": config.min_windows,
        "min_pass_rate": config.min_pass_rate,
        "total_bar_count": len(klines),
    }
    return validation_slices, metadata


def _generate_from_kline_payload(
    *,
    payload: Any,
    config: HistoricalValidationWindowConfig,
    source_path: Path,
    generated_at: int,
    safety_flags: dict[str, bool],
) -> SourceReportGenerationResult:
    try:
        klines = _load_klines_from_payload(payload)
    except ValueError as exc:
        return _result(
            status="SKIPPED",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=["historical_klines_not_parseable"],
            safety_flags=safety_flags,
            input_kind="klines",
        )

    quality_report = validate_klines(
        klines=klines,
        symbol=config.symbol,
        timeframe=config.timeframe,
    )
    if not quality_report.passed:
        return _result(
            status="SKIPPED",
            message="historical kline quality validation failed",
            generated_at=generated_at,
            config=config,
            reason_codes=["data_quality_failed"],
            safety_flags=safety_flags,
            input_kind="klines",
            metrics={"quality_report": quality_report.to_payload()},
        )

    try:
        _train, holdout, window = split_historical_validation_window(klines, config)
    except ValueError as exc:
        return _result(
            status="SKIPPED",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=[str(exc)],
            safety_flags=safety_flags,
            input_kind="klines",
        )

    backtest_result = _run_default_holdout_backtest(holdout, config)
    if backtest_result.get("status") != "completed":
        return _result(
            status="FAIL",
            message="holdout backtest did not complete",
            generated_at=generated_at,
            config=config,
            reason_codes=["holdout_backtest_failed"],
            safety_flags=safety_flags,
            input_kind="klines",
            window=window,
            metrics={"backtest_result": backtest_result},
        )

    metrics = _metrics_from_completed_backtest_result(
        backtest_result,
        initial_balance=config.initial_balance,
    )
    if metrics["trade_count"] < config.min_trades:
        return _result(
            status="SKIPPED",
            message="holdout backtest produced fewer trades than required",
            generated_at=generated_at,
            config=config,
            reason_codes=["holdout_trade_count_below_minimum"],
            safety_flags=safety_flags,
            input_kind="klines",
            window=window,
            metrics=metrics,
        )

    return _write_source_report_payload(
        metrics=metrics,
        window=window,
        config=config,
        source_path=source_path,
        generated_at=generated_at,
        safety_flags=safety_flags,
        input_kind="klines",
    )


def _generate_walk_forward_from_kline_payload(
    *,
    payload: Any,
    config: WalkForwardWindowConfig,
    source_path: Path,
    generated_at: int,
    safety_flags: dict[str, bool],
) -> SourceReportGenerationResult:
    try:
        klines = _load_klines_from_payload(payload)
    except ValueError as exc:
        return _result(
            status="SKIPPED",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=["historical_klines_not_parseable"],
            safety_flags=safety_flags,
            input_kind="klines",
        )

    quality_report = validate_klines(
        klines=klines,
        symbol=config.symbol,
        timeframe=config.timeframe,
    )
    if not quality_report.passed:
        return _result(
            status="SKIPPED",
            message="historical kline quality validation failed",
            generated_at=generated_at,
            config=config,
            reason_codes=["walk_forward_input_quality_failed"],
            safety_flags=safety_flags,
            input_kind="klines",
            metrics={"quality_report": quality_report.to_payload()},
        )

    try:
        validation_slices, metadata = generate_walk_forward_slices(klines, config)
    except ValueError as exc:
        reason_code = str(exc)
        if reason_code not in {
            "insufficient_walk_forward_windows",
            "walk_forward_windows_overlap",
            "walk_forward_backtest_failed",
        }:
            reason_code = "insufficient_walk_forward_windows"
        return _result(
            status="SKIPPED",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=[reason_code],
            safety_flags=safety_flags,
            input_kind="klines",
            metrics={"walk_forward": {"error": str(exc)}},
        )

    if metadata["walk_forward_window_count"] < config.min_windows:
        return _result(
            status="SKIPPED",
            message="walk-forward generated fewer windows than required",
            generated_at=generated_at,
            config=config,
            reason_codes=["insufficient_walk_forward_windows"],
            safety_flags=safety_flags,
            input_kind="klines",
            window=metadata,
            metrics={"walk_forward": metadata},
        )

    if metadata["walk_forward_pass_rate"] < config.min_pass_rate:
        return _result(
            status="SKIPPED",
            message="walk-forward pass rate is below threshold",
            generated_at=generated_at,
            config=config,
            reason_codes=["walk_forward_pass_rate_below_threshold"],
            safety_flags=safety_flags,
            input_kind="klines",
            window=metadata,
            metrics={"walk_forward": metadata},
        )

    return _write_walk_forward_source_report_payload(
        validation_slices=validation_slices,
        metadata=metadata,
        config=config,
        source_path=source_path,
        generated_at=generated_at,
        safety_flags=safety_flags,
    )


def _generate_monte_carlo_from_kline_payload(
    *,
    payload: Any,
    config: MonteCarloValidationConfig,
    source_path: Path,
    generated_at: int,
    safety_flags: dict[str, bool],
) -> SourceReportGenerationResult:
    try:
        klines = _load_klines_from_payload(payload)
    except ValueError as exc:
        return _result(
            status="SKIPPED",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=["historical_klines_not_parseable"],
            safety_flags=safety_flags,
            input_kind="klines",
        )

    quality_report = validate_klines(
        klines=klines,
        symbol=config.symbol,
        timeframe=config.timeframe,
    )
    if not quality_report.passed:
        return _result(
            status="SKIPPED",
            message="historical kline quality validation failed",
            generated_at=generated_at,
            config=config,
            reason_codes=["monte_carlo_input_quality_failed"],
            safety_flags=safety_flags,
            input_kind="klines",
            metrics={"quality_report": quality_report.to_payload()},
        )

    try:
        _train, holdout, window = split_historical_validation_window(klines, config)
    except ValueError as exc:
        reason_code = str(exc)
        if reason_code not in {
            "insufficient_bars_for_train_and_holdout",
            "insufficient_holdout_bars",
            "insufficient_train_bars",
            "holdout_window_not_independent",
        }:
            reason_code = "insufficient_monte_carlo_trades"
        return _result(
            status="SKIPPED",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=[reason_code],
            safety_flags=safety_flags,
            input_kind="klines",
        )

    backtest_result = _run_default_holdout_backtest(holdout, config)
    if backtest_result.get("status") != "completed":
        return _result(
            status="FAIL",
            message="holdout backtest did not complete",
            generated_at=generated_at,
            config=config,
            reason_codes=["holdout_backtest_failed"],
            safety_flags=safety_flags,
            input_kind="klines",
            window=window,
            metrics={"backtest_result": backtest_result},
        )

    return _generate_and_write_monte_carlo_source_report(
        trade_pnls=_extract_trade_pnls_from_backtest_result(backtest_result),
        base_metrics=_metrics_from_completed_backtest_result(
            backtest_result,
            initial_balance=config.initial_balance,
        ),
        config=config,
        source_path=source_path,
        generated_at=generated_at,
        safety_flags=safety_flags,
        input_kind="klines",
        window=window,
    )


def _generate_monte_carlo_from_backtest_result_payload(
    *,
    payload: dict[str, Any],
    config: MonteCarloValidationConfig,
    source_path: Path,
    generated_at: int,
    safety_flags: dict[str, bool],
) -> SourceReportGenerationResult:
    if not isinstance(payload, dict):
        return _result(
            status="SKIPPED",
            message="backtest result input must be a JSON object",
            generated_at=generated_at,
            config=config,
            reason_codes=["backtest_result_not_parseable"],
            safety_flags=safety_flags,
            input_kind="backtest_result",
        )

    if not _payload_declares_out_of_sample(payload):
        return _result(
            status="SKIPPED",
            message="backtest result is not explicitly marked as out_of_sample",
            generated_at=generated_at,
            config=config,
            reason_codes=["backtest_result_not_marked_out_of_sample"],
            safety_flags=safety_flags,
            input_kind="backtest_result",
        )

    window = _window_from_backtest_result(payload)
    if window is None:
        return _result(
            status="SKIPPED",
            message="out_of_sample backtest result is missing replayable window boundaries",
            generated_at=generated_at,
            config=config,
            reason_codes=["missing_oos_window_boundaries"],
            safety_flags=safety_flags,
            input_kind="backtest_result",
        )

    try:
        base_metrics = _metrics_from_backtest_result_payload(
            payload,
            initial_balance=config.initial_balance,
        )
    except ValueError as exc:
        return _result(
            status="SKIPPED",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=["backtest_result_metrics_missing"],
            safety_flags=safety_flags,
            input_kind="backtest_result",
            window=window,
        )

    return _generate_and_write_monte_carlo_source_report(
        trade_pnls=_extract_trade_pnls_from_backtest_result(payload),
        base_metrics=base_metrics,
        config=config,
        source_path=source_path,
        generated_at=generated_at,
        safety_flags=safety_flags,
        input_kind="backtest_result",
        window=window,
    )


def _generate_from_backtest_result_payload(
    *,
    payload: dict[str, Any],
    config: HistoricalValidationWindowConfig,
    source_path: Path,
    generated_at: int,
    safety_flags: dict[str, bool],
) -> SourceReportGenerationResult:
    if not isinstance(payload, dict):
        return _result(
            status="SKIPPED",
            message="backtest result input must be a JSON object",
            generated_at=generated_at,
            config=config,
            reason_codes=["backtest_result_not_parseable"],
            safety_flags=safety_flags,
            input_kind="backtest_result",
        )

    if not _payload_declares_out_of_sample(payload):
        return _result(
            status="SKIPPED",
            message="backtest result is not explicitly marked as out_of_sample",
            generated_at=generated_at,
            config=config,
            reason_codes=["backtest_result_not_marked_out_of_sample"],
            safety_flags=safety_flags,
            input_kind="backtest_result",
        )

    window = _window_from_backtest_result(payload)
    if window is None:
        return _result(
            status="SKIPPED",
            message="out_of_sample backtest result is missing replayable window boundaries",
            generated_at=generated_at,
            config=config,
            reason_codes=["missing_oos_window_boundaries"],
            safety_flags=safety_flags,
            input_kind="backtest_result",
        )

    try:
        metrics = _metrics_from_backtest_result_payload(
            payload,
            initial_balance=config.initial_balance,
        )
    except ValueError as exc:
        return _result(
            status="SKIPPED",
            message=str(exc),
            generated_at=generated_at,
            config=config,
            reason_codes=["backtest_result_metrics_missing"],
            safety_flags=safety_flags,
            input_kind="backtest_result",
            window=window,
        )

    if metrics["trade_count"] < config.min_trades:
        return _result(
            status="SKIPPED",
            message="out_of_sample backtest result produced fewer trades than required",
            generated_at=generated_at,
            config=config,
            reason_codes=["holdout_trade_count_below_minimum"],
            safety_flags=safety_flags,
            input_kind="backtest_result",
            window=window,
            metrics=metrics,
        )

    return _write_source_report_payload(
        metrics=metrics,
        window=window,
        config=config,
        source_path=source_path,
        generated_at=generated_at,
        safety_flags=safety_flags,
        input_kind="backtest_result",
    )


def _run_default_holdout_backtest(
    holdout: list[Kline],
    config: HistoricalValidationWindowConfig,
) -> dict[str, Any]:
    account = CryptoAccount(initial_balance=config.initial_balance)
    execution = ExecutionEngine(execution_delay=0, seed=1, account=account)
    risk = RiskManager(max_position_pct=0.1, symbol=config.symbol)
    candidate_strategy = create_candidate_strategy(
        config.strategy_id,
        config.strategy_parameters,
    )
    engine = BacktestEngine(
        candidate_strategy.strategy,
        execution,
        account,
        risk=risk,
        symbol=config.symbol,
        fast_window=candidate_strategy.engine_fast_window or config.fast_window,
        slow_window=candidate_strategy.engine_slow_window or config.slow_window,
        feature_pipeline=candidate_strategy.feature_pipeline,
        timeframe=config.timeframe,
    )
    return engine.run(holdout)


def _build_walk_forward_windows(
    klines: list[Kline],
    config: WalkForwardWindowConfig,
) -> list[dict[str, Any]]:
    windows = []
    total_required = config.train_bars + config.test_bars
    if len(klines) < total_required:
        raise ValueError("insufficient_walk_forward_windows")

    start = 0
    while start + total_required <= len(klines):
        train = klines[start : start + config.train_bars]
        test_start = start + config.train_bars
        test_end = test_start + config.test_bars
        test = klines[test_start:test_end]
        if not train or not test or train[-1].timestamp >= test[0].timestamp:
            raise ValueError("walk_forward_windows_overlap")
        windows.append(
            {
                "train": _window_payload(train),
                "test": _window_payload(test),
                "train_klines": train,
                "test_klines": test,
            }
        )
        start += config.step_bars

    if len(windows) < config.min_windows:
        raise ValueError("insufficient_walk_forward_windows")
    return windows


def _write_walk_forward_source_report_payload(
    *,
    validation_slices: list[StrategyValidationSlice],
    metadata: dict[str, Any],
    config: WalkForwardWindowConfig,
    source_path: Path,
    generated_at: int,
    safety_flags: dict[str, bool],
) -> SourceReportGenerationResult:
    start_timestamp = metadata["walk_forward_windows"][0]["test"]["start_timestamp"]
    end_timestamp = metadata["walk_forward_windows"][-1]["test"]["end_timestamp"]
    source_report_id = _walk_forward_source_report_id(
        strategy_id=config.strategy_id,
        candidate_version=config.candidate_version,
        symbol=config.symbol,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    summary = _summary_from_validation_slices(validation_slices)
    source_report = StrategyValidationArtifactSourceReport(
        report_id=source_report_id,
        strategy_id=config.strategy_id,
        candidate_version=config.candidate_version,
        symbol=config.symbol,
        generated_at=generated_at,
        source_report_id=source_report_id,
        source_path=str(source_path),
        summary=StrategyValidationSourceSummary(**summary),
        validation_slices=validation_slices,
        trace=TraceContext(
            run_id=source_report_id,
            source=PayloadSource.BACKTEST,
            symbol=config.symbol,
            timeframe=config.timeframe,
            timestamp=end_timestamp,
        ),
    )
    payload = source_report.to_payload()
    payload["provenance"] = {
        "input_kind": "klines",
        "source_path": str(source_path),
        "window": metadata,
        "strategy_id": config.strategy_id,
        "strategy_parameters": config.strategy_parameters,
        "strategy_metadata": candidate_strategy_metadata(
            config.strategy_id,
            config.strategy_parameters,
        ),
        "candidate_version": config.candidate_version,
        "symbol": config.symbol,
        "timeframe": config.timeframe,
    }
    payload["walk_forward_window_count"] = metadata["walk_forward_window_count"]
    payload["walk_forward_pass_count"] = metadata["walk_forward_pass_count"]
    payload["walk_forward_pass_rate"] = metadata["walk_forward_pass_rate"]
    payload["safety_flags"] = safety_flags

    output_path = _source_report_output_path(config, suffix="wf")
    if output_path.exists() and not config.overwrite_existing:
        return _result(
            status="FAIL",
            message=(
                "source report target already exists; pass overwrite_existing only for "
                "an intentional replayable rerun"
            ),
            generated_at=generated_at,
            config=config,
            reason_codes=["source_report_target_exists"],
            safety_flags=safety_flags,
            input_kind="klines",
            window=metadata,
            metrics={"walk_forward": metadata, "summary": summary},
            source_report=payload,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _result(
        status="PASS",
        message="walk_forward source report generated",
        generated_at=generated_at,
        config=config,
        reason_codes=[],
        safety_flags=safety_flags,
        input_kind="klines",
        window=metadata,
        metrics={"walk_forward": metadata, "summary": summary},
        source_report_path=str(output_path),
        source_report_id=source_report_id,
        source_report=payload,
    )


def _write_monte_carlo_source_report_payload(
    *,
    metrics: dict[str, Any],
    window: dict[str, Any],
    config: MonteCarloValidationConfig,
    source_path: Path,
    generated_at: int,
    safety_flags: dict[str, bool],
    input_kind: str,
) -> SourceReportGenerationResult:
    holdout = window["holdout"]
    source_report_id = _monte_carlo_source_report_id(
        strategy_id=config.strategy_id,
        candidate_version=config.candidate_version,
        symbol=config.symbol,
        start_timestamp=holdout["start_timestamp"],
        end_timestamp=holdout["end_timestamp"],
    )
    source_report = StrategyValidationArtifactSourceReport(
        report_id=source_report_id,
        strategy_id=config.strategy_id,
        candidate_version=config.candidate_version,
        symbol=config.symbol,
        generated_at=generated_at,
        source_report_id=source_report_id,
        source_path=str(source_path),
        summary=StrategyValidationSourceSummary(
            trade_count=metrics["trade_count"],
            total_net_pnl=metrics["total_net_pnl"],
            max_drawdown=metrics["max_drawdown"],
            win_rate=metrics["win_rate"],
            sharpe_ratio=metrics.get("sharpe_ratio"),
        ),
        validation_slices=[
            StrategyValidationSlice(
                name=(
                    "oos-"
                    f"{holdout['start_timestamp']}-"
                    f"{holdout['end_timestamp']}"
                ),
                kind=StrategyValidationSliceKind.OUT_OF_SAMPLE,
                trade_count=metrics["trade_count"],
                total_net_pnl=metrics["total_net_pnl"],
                max_drawdown=metrics["max_drawdown"],
                win_rate=metrics["win_rate"],
                sharpe_ratio=metrics.get("sharpe_ratio"),
            )
        ],
        monte_carlo_survival_rate=metrics["monte_carlo_survival_rate"],
        monte_carlo_validation=MonteCarloValidation(
            method=metrics["monte_carlo_validation"]["method"],
            run_count=metrics["monte_carlo_validation"]["run_count"],
            perturbation_dimensions=metrics["monte_carlo_validation"][
                "perturbation_dimensions"
            ],
            seed=metrics["monte_carlo_validation"]["seed"],
            survival_threshold=metrics["monte_carlo_validation"][
                "survival_threshold"
            ],
        ),
        trace=TraceContext(
            run_id=source_report_id,
            source=PayloadSource.BACKTEST,
            symbol=config.symbol,
            timeframe=config.timeframe,
            timestamp=holdout["end_timestamp"],
        ),
    )
    payload = source_report.to_payload()
    payload["provenance"] = {
        "input_kind": input_kind,
        "source_path": str(source_path),
        "window": window,
        "strategy_id": config.strategy_id,
        "strategy_parameters": config.strategy_parameters,
        "strategy_metadata": candidate_strategy_metadata(
            config.strategy_id,
            config.strategy_parameters,
        ),
        "candidate_version": config.candidate_version,
        "symbol": config.symbol,
        "timeframe": config.timeframe,
    }
    payload["safety_flags"] = safety_flags

    output_path = _source_report_output_path(config, suffix="mc")
    if output_path.exists() and not config.overwrite_existing:
        return _result(
            status="FAIL",
            message=(
                "source report target already exists; pass overwrite_existing only for "
                "an intentional replayable rerun"
            ),
            generated_at=generated_at,
            config=config,
            reason_codes=["source_report_target_exists"],
            safety_flags=safety_flags,
            input_kind=input_kind,
            window=window,
            metrics=metrics,
            source_report=payload,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _result(
        status="PASS",
        message="monte_carlo source report generated",
        generated_at=generated_at,
        config=config,
        reason_codes=[],
        safety_flags=safety_flags,
        input_kind=input_kind,
        window=window,
        metrics=metrics,
        source_report_path=str(output_path),
        source_report_id=source_report_id,
        source_report=payload,
    )


def _write_source_report_payload(
    *,
    metrics: dict[str, Any],
    window: dict[str, Any],
    config: HistoricalValidationWindowConfig,
    source_path: Path,
    generated_at: int,
    safety_flags: dict[str, bool],
    input_kind: str,
) -> SourceReportGenerationResult:
    holdout = window["holdout"]
    source_report_id = _source_report_id(
        strategy_id=config.strategy_id,
        candidate_version=config.candidate_version,
        symbol=config.symbol,
        start_timestamp=holdout["start_timestamp"],
        end_timestamp=holdout["end_timestamp"],
    )
    source_report = StrategyValidationArtifactSourceReport(
        report_id=source_report_id,
        strategy_id=config.strategy_id,
        candidate_version=config.candidate_version,
        symbol=config.symbol,
        generated_at=generated_at,
        source_report_id=source_report_id,
        source_path=str(source_path),
        summary=StrategyValidationSourceSummary(**metrics),
        validation_slices=[
            StrategyValidationSlice(
                name=(
                    "oos-"
                    f"{holdout['start_timestamp']}-"
                    f"{holdout['end_timestamp']}"
                ),
                kind=StrategyValidationSliceKind.OUT_OF_SAMPLE,
                **metrics,
            )
        ],
        trace=TraceContext(
            run_id=source_report_id,
            source=PayloadSource.BACKTEST,
            symbol=config.symbol,
            timeframe=config.timeframe,
            timestamp=holdout["end_timestamp"],
        ),
    )
    payload = source_report.to_payload()
    payload["provenance"] = {
        "input_kind": input_kind,
        "source_path": str(source_path),
        "window": window,
        "strategy_id": config.strategy_id,
        "strategy_parameters": config.strategy_parameters,
        "strategy_metadata": candidate_strategy_metadata(
            config.strategy_id,
            config.strategy_parameters,
        ),
        "candidate_version": config.candidate_version,
        "symbol": config.symbol,
        "timeframe": config.timeframe,
    }
    payload["safety_flags"] = safety_flags

    output_path = _source_report_output_path(config, suffix="oos")
    if output_path.exists() and not config.overwrite_existing:
        return _result(
            status="FAIL",
            message=(
                "source report target already exists; pass overwrite_existing only for "
                "an intentional replayable rerun"
            ),
            generated_at=generated_at,
            config=config,
            reason_codes=["source_report_target_exists"],
            safety_flags=safety_flags,
            input_kind=input_kind,
            window=window,
            metrics=metrics,
            source_report=payload,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _result(
        status="PASS",
        message="out_of_sample source report generated",
        generated_at=generated_at,
        config=config,
        reason_codes=[],
        safety_flags=safety_flags,
        input_kind=input_kind,
        window=window,
        metrics=metrics,
        source_report_path=str(output_path),
        source_report_id=source_report_id,
        source_report=payload,
    )


def _generate_and_write_monte_carlo_source_report(
    *,
    trade_pnls: list[float],
    base_metrics: dict[str, Any],
    config: MonteCarloValidationConfig,
    source_path: Path,
    generated_at: int,
    safety_flags: dict[str, bool],
    input_kind: str,
    window: dict[str, Any],
) -> SourceReportGenerationResult:
    if len(trade_pnls) < config.min_trade_count:
        return _result(
            status="SKIPPED",
            message=(
                "monte carlo validation requires more trades than available in "
                "the local backtest input"
            ),
            generated_at=generated_at,
            config=config,
            reason_codes=["insufficient_monte_carlo_trades"],
            safety_flags=safety_flags,
            input_kind=input_kind,
            window=window,
            metrics=base_metrics,
        )

    validation = run_monte_carlo_validation(
        trade_pnls=trade_pnls,
        config=config,
    )
    metrics = {
        **base_metrics,
        "monte_carlo_survival_rate": validation["survival_rate"],
        "monte_carlo_validation": {
            "method": validation["method"],
            "run_count": validation["run_count"],
            "perturbation_dimensions": validation["perturbation_dimensions"],
            "seed": validation["seed"],
            "survival_threshold": validation["survival_threshold"],
        },
        "monte_carlo_run_pass_count": validation["pass_count"],
        "monte_carlo_run_fail_count": validation["fail_count"],
        "monte_carlo_max_drawdown_limit": validation["max_drawdown_limit"],
    }
    if validation["survival_rate"] < config.survival_threshold:
        return _result(
            status="SKIPPED",
            message="monte carlo survival rate is below threshold",
            generated_at=generated_at,
            config=config,
            reason_codes=["monte_carlo_survival_rate_below_threshold"],
            safety_flags=safety_flags,
            input_kind=input_kind,
            window=window,
            metrics=metrics,
        )

    return _write_monte_carlo_source_report_payload(
        metrics=metrics,
        window=window,
        config=config,
        source_path=source_path,
        generated_at=generated_at,
        safety_flags=safety_flags,
        input_kind=input_kind,
    )


def run_monte_carlo_validation(
    *,
    trade_pnls: list[float],
    config: MonteCarloValidationConfig,
) -> dict[str, Any]:
    if len(trade_pnls) < config.min_trade_count:
        raise ValueError("insufficient_monte_carlo_trades")

    rng = random.Random(config.seed)
    base_total_net_pnl = float(sum(trade_pnls))
    pass_count = 0
    simulated_runs = []
    dimensions = tuple(config.perturbation_dimensions)

    for _ in range(config.run_count):
        perturbed = [float(value) for value in trade_pnls]
        if "trade_order_shuffle" in dimensions:
            rng.shuffle(perturbed)
        if "return_perturbation" in dimensions:
            perturbed = [
                value * (1.0 + rng.gauss(0.0, config.return_perturbation_sigma))
                for value in perturbed
            ]
        if "slippage_fee_perturbation" in dimensions:
            perturbed = [
                value - abs(value) * rng.gauss(0.0, config.slippage_fee_perturbation_sigma)
                for value in perturbed
            ]

        net_pnl = float(sum(perturbed))
        drawdown = _max_drawdown_from_trade_pnls(perturbed)
        passes = net_pnl >= 0.0 and drawdown <= _resolved_max_drawdown_limit(config)
        simulated_runs.append(
            {
                "net_pnl": net_pnl,
                "max_drawdown": drawdown,
                "passes": passes,
            }
        )
        if passes:
            pass_count += 1

    fail_count = config.run_count - pass_count
    survival_rate = pass_count / config.run_count if config.run_count else 0.0
    return {
        "method": _monte_carlo_method_for_dimensions(dimensions),
        "run_count": config.run_count,
        "seed": config.seed,
        "perturbation_dimensions": list(dimensions),
        "survival_threshold": config.survival_threshold,
        "max_drawdown_limit": _resolved_max_drawdown_limit(config),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "survival_rate": _clamp(survival_rate, minimum=0.0, maximum=1.0),
        "base_trade_count": len(trade_pnls),
        "base_total_net_pnl": base_total_net_pnl,
        "base_max_drawdown": _max_drawdown_from_trade_pnls(trade_pnls),
        "simulated_runs": simulated_runs,
    }


def _source_report_output_path(
    config: HistoricalValidationWindowConfig,
    *,
    suffix: str,
) -> Path:
    return (
        Path(config.output_dir)
        / _safe_token(config.symbol)
        / _safe_token(config.strategy_id)
        / f"{_safe_token(config.candidate_version)}-{_safe_token(suffix)}.json"
    )


def _metrics_from_completed_backtest_result(
    result: dict[str, Any],
    *,
    initial_balance: float,
) -> dict[str, Any]:
    return {
        "trade_count": len(result.get("fills") or []),
        "total_net_pnl": _coerce_float(
            result.get("total_net_pnl"),
            default=float(result.get("total_return", 0.0)) * initial_balance,
        ),
        "max_drawdown": _coerce_float(result.get("max_drawdown"), default=0.0),
        "win_rate": _clamp(
            _coerce_float(result.get("win_rate"), default=0.0),
            minimum=0.0,
            maximum=1.0,
        ),
        "sharpe_ratio": _optional_float(result.get("sharpe_ratio")),
    }


def _metrics_from_backtest_result_payload(
    payload: dict[str, Any],
    *,
    initial_balance: float,
) -> dict[str, Any]:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
    trade_count = metrics.get("trade_count")
    if trade_count is None:
        trade_count = len(payload.get("fills") or [])
    total_net_pnl = (
        metrics.get("total_net_pnl")
        if metrics.get("total_net_pnl") is not None
        else metrics.get("net_pnl")
    )
    if total_net_pnl is None and payload.get("equity_curve"):
        curve = payload["equity_curve"]
        total_net_pnl = float(curve[-1]) - initial_balance
    if total_net_pnl is None and metrics.get("total_return") is not None:
        total_net_pnl = float(metrics["total_return"]) * initial_balance

    required = {
        "trade_count": trade_count,
        "total_net_pnl": total_net_pnl,
        "max_drawdown": metrics.get("max_drawdown"),
        "win_rate": metrics.get("win_rate"),
    }
    missing = [key for key, value in required.items() if value is None]
    if missing:
        raise ValueError(
            "backtest result is missing required metrics: " + ", ".join(sorted(missing))
        )

    return {
        "trade_count": int(trade_count),
        "total_net_pnl": float(total_net_pnl),
        "max_drawdown": float(required["max_drawdown"]),
        "win_rate": _clamp(float(required["win_rate"]), minimum=0.0, maximum=1.0),
        "sharpe_ratio": _optional_float(metrics.get("sharpe_ratio")),
    }


def _extract_trade_pnls_from_backtest_result(payload: Any) -> list[float]:
    if not isinstance(payload, dict):
        return []

    explicit_trade_pnls = payload.get("realized_trade_pnls")
    if isinstance(explicit_trade_pnls, list):
        values = _coerce_numeric_list(explicit_trade_pnls)
        if values is not None:
            return values

    fills = payload.get("fills")
    if isinstance(fills, list) and fills:
        fill_pnls = []
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            pnl = _first_present(
                fill,
                "realized_pnl",
                "realizedPnl",
                "pnl",
                "net_pnl",
                "netPnl",
            )
            if pnl is None:
                continue
            try:
                fill_pnls.append(float(pnl))
            except (TypeError, ValueError):
                continue
        if fill_pnls:
            return fill_pnls

    # Fallback for payloads without explicit per-trade pnl:
    # approximate from equity curve deltas when available.
    equity_curve = payload.get("equity_curve")
    if isinstance(equity_curve, list) and len(equity_curve) >= 2:
        values = _coerce_numeric_list(equity_curve)
        if values is not None:
            return [
                values[index] - values[index - 1]
                for index in range(1, len(values))
            ]
    return []


def _coerce_numeric_list(values: list[Any]) -> Optional[list[float]]:
    numeric = []
    for item in values:
        if isinstance(item, bool):
            return None
        if not isinstance(item, (int, float)):
            return None
        numeric.append(float(item))
    return numeric


def _summary_from_validation_slices(
    validation_slices: list[StrategyValidationSlice],
) -> dict[str, Any]:
    trade_count = sum(item.trade_count for item in validation_slices)
    total_net_pnl = sum(item.total_net_pnl for item in validation_slices)
    max_drawdown = max((item.max_drawdown for item in validation_slices), default=0.0)
    if trade_count > 0:
        win_rate = sum(item.win_rate * item.trade_count for item in validation_slices) / trade_count
    else:
        win_rate = 0.0
    sharpe_values = [
        item.sharpe_ratio for item in validation_slices if item.sharpe_ratio is not None
    ]
    sharpe_ratio = (
        sum(sharpe_values) / len(sharpe_values) if sharpe_values else None
    )
    return {
        "trade_count": trade_count,
        "total_net_pnl": total_net_pnl,
        "max_drawdown": max_drawdown,
        "win_rate": _clamp(win_rate, minimum=0.0, maximum=1.0),
        "sharpe_ratio": sharpe_ratio,
    }


def _validation_slice_passes(
    item: StrategyValidationSlice,
    *,
    min_trades: int,
) -> bool:
    if item.trade_count < min_trades:
        return False
    if item.total_net_pnl < 0.0:
        return False
    if item.max_drawdown < 0.0:
        return False
    return True


def _window_from_backtest_result(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    window = payload.get("validation_window") or payload.get("window")
    if not isinstance(window, dict):
        return None

    start_timestamp = _first_present(
        window,
        "start_timestamp",
        "holdout_start_timestamp",
        "start_ts",
    )
    end_timestamp = _first_present(
        window,
        "end_timestamp",
        "holdout_end_timestamp",
        "end_ts",
    )
    if start_timestamp is None or end_timestamp is None:
        return None
    start_timestamp = _normalize_timestamp(start_timestamp)
    end_timestamp = _normalize_timestamp(end_timestamp)
    if start_timestamp >= end_timestamp:
        return None

    holdout = {
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "bar_count": int(window.get("bar_count") or window.get("holdout_bar_count") or 0),
    }
    return {
        "train": window.get("train") or {},
        "holdout": holdout,
        "total_bar_count": window.get("total_bar_count"),
        "train_bar_count": window.get("train_bar_count"),
        "holdout_bar_count": holdout["bar_count"],
        "holdout_ratio": window.get("holdout_ratio"),
    }


def _load_klines_from_payload(payload: Any) -> list[Kline]:
    records = _extract_kline_records(payload)
    if not records:
        raise ValueError("historical input contains no kline records")
    return [_coerce_kline(record) for record in records]


def _extract_kline_records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("klines", "data", "records", "bars", "candles"):
        records = payload.get(key)
        if isinstance(records, list):
            return records
    for path in (
        ("data_layer", "payload", "execution", "klines"),
        ("data_layer", "payload", "klines"),
        ("payload", "execution", "klines"),
        ("execution", "klines"),
    ):
        records = _nested_list(payload, path)
        if records:
            return records
    return []


def _nested_list(payload: dict[str, Any], path: tuple[str, ...]) -> list[Any]:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return []
        current = current.get(key)
    return current if isinstance(current, list) else []


def _coerce_kline(record: Any) -> Kline:
    if isinstance(record, Kline):
        return record
    if isinstance(record, (list, tuple)) and len(record) >= 6:
        return Kline(
            timestamp=_normalize_timestamp(record[0]),
            open=float(record[1]),
            high=float(record[2]),
            low=float(record[3]),
            close=float(record[4]),
            volume=float(record[5]),
        )
    if not isinstance(record, dict):
        raise ValueError("kline record must be an object or OHLCV list")

    return Kline(
        timestamp=_normalize_timestamp(
            _required_first_present(record, "timestamp", "ts", "time", "open_time")
        ),
        open=float(_required_first_present(record, "open", "o")),
        high=float(_required_first_present(record, "high", "h")),
        low=float(_required_first_present(record, "low", "l")),
        close=float(_required_first_present(record, "close", "c")),
        volume=float(_required_first_present(record, "volume", "vol", "v")),
        is_complete=record.get("is_complete"),
    )


def _load_json_or_jsonl(path: Path) -> Any:
    if path.suffix == ".jsonl":
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_input_kind(payload: Any, input_kind: str) -> str:
    if input_kind != "auto":
        return input_kind
    if isinstance(payload, dict):
        if "artifact_id" in payload and "metrics" in payload:
            raise ValueError("prebuilt StrategyValidationArtifact payload is not source input")
        if "summary" in payload and "validation_slices" in payload:
            raise ValueError("existing source report payload must not be regenerated as OOS")
        if _extract_kline_records(payload):
            return "klines"
        if any(key in payload for key in ("metrics", "equity_curve", "fills", "total_return")):
            return "backtest_result"
    if isinstance(payload, list):
        return "klines"
    return "unsupported"


def _payload_declares_out_of_sample(payload: dict[str, Any]) -> bool:
    candidates = [
        payload.get("kind"),
        payload.get("validation_kind"),
        payload.get("slice_kind"),
    ]
    window = payload.get("validation_window")
    if isinstance(window, dict):
        candidates.append(window.get("kind"))
        candidates.append(window.get("validation_kind"))
    return any(str(value).strip().lower() == "out_of_sample" for value in candidates)


def _window_payload(klines: list[Kline]) -> dict[str, Any]:
    return {
        "start_timestamp": klines[0].timestamp,
        "end_timestamp": klines[-1].timestamp,
        "bar_count": len(klines),
    }


def _result(
    *,
    status: str,
    message: str,
    generated_at: int,
    config: HistoricalValidationWindowConfig,
    reason_codes: list[str],
    safety_flags: dict[str, bool],
    source_report_path: Optional[str] = None,
    source_report_id: Optional[str] = None,
    input_kind: Optional[str] = None,
    window: Optional[dict[str, Any]] = None,
    metrics: Optional[dict[str, Any]] = None,
    source_report: Optional[dict[str, Any]] = None,
) -> SourceReportGenerationResult:
    return SourceReportGenerationResult(
        status=status,
        success=status == "PASS",
        message=message,
        generated_at=generated_at,
        reason_codes=reason_codes,
        source_path=config.source_path,
        input_kind=input_kind,
        source_report_path=source_report_path,
        source_report_id=source_report_id,
        window=window,
        metrics=metrics or {},
        safety_flags=safety_flags,
        source_report=source_report,
    )


def _default_safety_flags() -> dict[str, bool]:
    return {
        "network_access_used": False,
        "real_credentials_read": False,
        "broker_called": False,
        "live_orders_sent": False,
        "analytics_modified_live_state": False,
        "contains_real_credentials": False,
    }


def _guard_local_input_path(path: Path) -> None:
    resolved = path.resolve()
    examples_root = (PROJECT_ROOT / "config" / "examples").resolve()
    if resolved.is_relative_to(examples_root):
        raise ValueError(
            "example fixture inputs under config/examples must not be treated as real OOS evidence"
        )


def _guard_input_payload_safety(payload: Any) -> None:
    for path, key, value in _walk_payload(payload):
        normalized_key = _normalize_key(key)
        if normalized_key in _DANGEROUS_TRUE_FLAGS and _is_truthy_side_effect_value(value):
            raise ValueError(
                "historical validation input must not indicate live/broker side effects: "
                f"{path}"
            )
        if _is_secret_key(normalized_key) and _has_meaningful_secret_value(value):
            raise ValueError(
                "historical validation input must not contain credential-like fields: "
                f"{path}"
            )


def _walk_payload(value: Any, *, prefix: str = "$"):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{prefix}.{key}"
            yield child_path, str(key), child
            yield from _walk_payload(child, prefix=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_payload(child, prefix=f"{prefix}[{index}]")


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def _is_secret_key(normalized_key: str) -> bool:
    return any(pattern in normalized_key for pattern in _SECRET_KEY_PATTERNS)


def _has_meaningful_secret_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        stripped = value.strip()
        return bool(stripped) and stripped.lower() not in {
            "false",
            "none",
            "null",
            "redacted",
            "***",
            "<redacted>",
        }
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _is_truthy_side_effect_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "sent", "submitted"}
    return bool(value)


def _source_report_id(
    *,
    strategy_id: str,
    candidate_version: str,
    symbol: str,
    start_timestamp: int,
    end_timestamp: int,
) -> str:
    return (
        "oos-source-report:"
        f"{_safe_token(strategy_id)}:"
        f"{_safe_token(candidate_version)}:"
        f"{_safe_token(symbol)}:"
        f"{start_timestamp}-{end_timestamp}"
    )


def _monte_carlo_source_report_id(
    *,
    strategy_id: str,
    candidate_version: str,
    symbol: str,
    start_timestamp: int,
    end_timestamp: int,
) -> str:
    return (
        "mc-source-report:"
        f"{_safe_token(strategy_id)}:"
        f"{_safe_token(candidate_version)}:"
        f"{_safe_token(symbol)}:"
        f"{start_timestamp}-{end_timestamp}"
    )


def _walk_forward_source_report_id(
    *,
    strategy_id: str,
    candidate_version: str,
    symbol: str,
    start_timestamp: int,
    end_timestamp: int,
) -> str:
    return (
        "wf-source-report:"
        f"{_safe_token(strategy_id)}:"
        f"{_safe_token(candidate_version)}:"
        f"{_safe_token(symbol)}:"
        f"{start_timestamp}-{end_timestamp}"
    )


def _safe_token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return token or "unknown"


def _first_present(values: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return None


def _required_first_present(values: dict[str, Any], *keys: str) -> Any:
    value = _first_present(values, *keys)
    if value is None:
        raise ValueError("missing required kline field: " + "/".join(keys))
    return value


def _normalize_timestamp(value: Any) -> int:
    timestamp = int(float(value))
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    return timestamp


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _coerce_float(value: Any, *, default: float) -> float:
    if value is None:
        return float(default)
    return float(value)


def _clamp(value: float, *, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _max_drawdown_from_trade_pnls(trade_pnls: list[float]) -> float:
    if not trade_pnls:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in trade_pnls:
        equity += float(pnl)
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return max_drawdown


def _resolved_max_drawdown_limit(config: MonteCarloValidationConfig) -> float:
    if config.max_drawdown_limit is not None:
        return float(config.max_drawdown_limit)
    return float("inf")


def _monte_carlo_method_for_dimensions(dimensions: tuple[str, ...]) -> str:
    if len(dimensions) == 1:
        dimension = dimensions[0]
        if dimension == "trade_order_shuffle":
            return MonteCarloSimulationMethod.TRADE_SHUFFLE.value
        if dimension == "return_perturbation":
            return MonteCarloSimulationMethod.RETURN_PERTURBATION.value
        if dimension == "slippage_fee_perturbation":
            return MonteCarloSimulationMethod.SLIPPAGE_FEE_PERTURBATION.value
    return MonteCarloSimulationMethod.HYBRID.value
