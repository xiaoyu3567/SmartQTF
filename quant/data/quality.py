from enum import Enum
from typing import Dict, Iterable, List, Optional

from quant.data.multi_timeframe import MultiTimeframeKlineBatch
from quant.data.schemas.market import Kline
from quant.schemas.base import SmartQTFModel
from quant.utils.time_format import add_display_times, format_timestamp_ymdhm


TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


class DataQualityIssueCode(str, Enum):
    DUPLICATE_TIMESTAMP = "duplicate_timestamp"
    OUT_OF_ORDER = "out_of_order"
    MISSING_KLINE = "missing_kline"
    INVALID_OHLC = "invalid_ohlc"
    INVALID_VOLUME = "invalid_volume"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INCOMPLETE_LAST_BAR = "incomplete_last_bar"
    MISSING_EXECUTION_TIMEFRAME = "missing_execution_timeframe"
    TIMEFRAME_ALIGNMENT = "timeframe_alignment"
    FUTURE_CONTEXT_BAR = "future_context_bar"
    TIMEFRAME_QUALITY_FAILED = "timeframe_quality_failed"


class DataQualityIssue(SmartQTFModel):
    code: DataQualityIssueCode
    timestamp: Optional[int] = None
    message: str
    fatal: bool = True


class KlineQualityReport(SmartQTFModel):
    symbol: str
    timeframe: str
    interval_seconds: int
    checked_count: int
    issues: List[DataQualityIssue]
    first_timestamp: Optional[int] = None
    last_timestamp: Optional[int] = None
    expected_start_ts: Optional[int] = None
    expected_end_ts: Optional[int] = None
    has_incomplete_last_bar: bool = False
    incomplete_last_bar_timestamp: Optional[int] = None
    included_incomplete_bar: bool = False
    incomplete_bar_timestamp: Optional[int] = None

    def __init__(self, **data):
        if "has_incomplete_last_bar" not in data and "included_incomplete_bar" in data:
            data["has_incomplete_last_bar"] = data["included_incomplete_bar"]
        if "included_incomplete_bar" not in data and "has_incomplete_last_bar" in data:
            data["included_incomplete_bar"] = data["has_incomplete_last_bar"]
        if "incomplete_last_bar_timestamp" not in data and "incomplete_bar_timestamp" in data:
            data["incomplete_last_bar_timestamp"] = data["incomplete_bar_timestamp"]
        if "incomplete_bar_timestamp" not in data and "incomplete_last_bar_timestamp" in data:
            data["incomplete_bar_timestamp"] = data["incomplete_last_bar_timestamp"]
        super().__init__(**data)

    @property
    def passed(self) -> bool:
        return not any(issue.fatal for issue in self.issues)

    def to_payload(self):
        payload = super().to_payload()
        payload["passed"] = self.passed
        return payload

    def to_display_payload(self):
        payload = add_display_times(self.to_payload())
        for field_name, display_name in (
            ("expected_start_ts", "expected_start_time"),
            ("expected_end_ts", "expected_end_time"),
        ):
            value = payload.get(field_name)
            if isinstance(value, int) and value >= 0:
                payload[display_name] = format_timestamp_ymdhm(value)
        return payload


class MultiTimeframeQualityReport(SmartQTFModel):
    symbol: str
    execution_timeframe: str
    as_of_timestamp: Optional[int] = None
    timeframe_reports: Dict[str, KlineQualityReport]
    alignment_issues: List[DataQualityIssue]
    fatal_timeframes: List[str]

    @property
    def passed(self) -> bool:
        return not self.fatal_timeframes and not any(issue.fatal for issue in self.alignment_issues)

    def to_payload(self):
        payload = super().to_payload()
        payload["passed"] = self.passed
        return payload

    def to_display_payload(self):
        payload = add_display_times(self.to_payload())
        value = payload.get("as_of_timestamp")
        if isinstance(value, int) and value >= 0:
            payload["as_of_time"] = format_timestamp_ymdhm(value)
        return payload


def timeframe_to_seconds(timeframe: str) -> int:
    try:
        return TIMEFRAME_SECONDS[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc


def validate_klines(
    klines: Iterable[Kline],
    symbol: str,
    timeframe: str,
    expected_start_ts: Optional[int] = None,
    expected_end_ts: Optional[int] = None,
) -> KlineQualityReport:
    interval_seconds = timeframe_to_seconds(timeframe)
    ordered_klines = list(klines)
    issues: List[DataQualityIssue] = []

    _validate_values(ordered_klines, issues)
    _validate_order_and_duplicates(ordered_klines, issues)
    _validate_gaps(ordered_klines, interval_seconds, issues)
    _validate_expected_window(
        ordered_klines,
        interval_seconds,
        expected_start_ts,
        expected_end_ts,
        issues,
    )
    _validate_incomplete_last_bar(ordered_klines, issues)

    incomplete_bar_timestamp = None
    if ordered_klines and getattr(ordered_klines[-1], "is_complete", None) is False:
        incomplete_bar_timestamp = ordered_klines[-1].timestamp
    timestamps = [kline.timestamp for kline in ordered_klines]

    return KlineQualityReport(
        symbol=symbol,
        timeframe=timeframe,
        interval_seconds=interval_seconds,
        checked_count=len(ordered_klines),
        issues=issues,
        first_timestamp=min(timestamps) if timestamps else None,
        last_timestamp=max(timestamps) if timestamps else None,
        expected_start_ts=expected_start_ts,
        expected_end_ts=expected_end_ts,
        has_incomplete_last_bar=incomplete_bar_timestamp is not None,
        incomplete_last_bar_timestamp=incomplete_bar_timestamp,
        included_incomplete_bar=incomplete_bar_timestamp is not None,
        incomplete_bar_timestamp=incomplete_bar_timestamp,
    )


def validate_multi_timeframe_klines(
    batch: MultiTimeframeKlineBatch,
    *,
    as_of_timestamp: Optional[int] = None,
) -> MultiTimeframeQualityReport:
    effective_as_of = as_of_timestamp if as_of_timestamp is not None else batch.as_of_timestamp
    timeframe_reports: Dict[str, KlineQualityReport] = {}
    alignment_issues: List[DataQualityIssue] = []

    if batch.execution is None:
        alignment_issues.append(
            DataQualityIssue(
                code=DataQualityIssueCode.MISSING_EXECUTION_TIMEFRAME,
                message="Multi-timeframe batch is missing the execution timeframe batch",
            )
        )
    else:
        execution_report = validate_klines(
            klines=batch.execution.klines,
            symbol=batch.symbol,
            timeframe=batch.execution_timeframe,
        )
        timeframe_reports[batch.execution_timeframe] = execution_report
        if effective_as_of is None:
            effective_as_of = execution_report.last_timestamp

    for context in batch.contexts:
        timeframe_reports[context.timeframe] = validate_klines(
            klines=context.klines,
            symbol=batch.symbol,
            timeframe=context.timeframe,
        )

    _validate_multi_timeframe_alignment(
        batch=batch,
        as_of_timestamp=effective_as_of,
        alignment_issues=alignment_issues,
    )

    fatal_timeframes = sorted(
        timeframe
        for timeframe, report in timeframe_reports.items()
        if any(issue.fatal for issue in report.issues)
    )

    for timeframe in fatal_timeframes:
        alignment_issues.append(
            DataQualityIssue(
                code=DataQualityIssueCode.TIMEFRAME_QUALITY_FAILED,
                message=f"Timeframe {timeframe} failed single-timeframe quality validation",
            )
        )

    return MultiTimeframeQualityReport(
        symbol=batch.symbol,
        execution_timeframe=batch.execution_timeframe,
        as_of_timestamp=effective_as_of,
        timeframe_reports=timeframe_reports,
        alignment_issues=alignment_issues,
        fatal_timeframes=fatal_timeframes,
    )


def _validate_multi_timeframe_alignment(
    batch: MultiTimeframeKlineBatch,
    as_of_timestamp: Optional[int],
    alignment_issues: List[DataQualityIssue],
) -> None:
    if batch.execution is None:
        return
    if as_of_timestamp is None:
        alignment_issues.append(
            DataQualityIssue(
                code=DataQualityIssueCode.TIMEFRAME_ALIGNMENT,
                message="Unable to evaluate alignment without an as_of timestamp",
            )
        )
        return

    execution_last_ts = batch.execution.last_timestamp
    if execution_last_ts is None:
        alignment_issues.append(
            DataQualityIssue(
                code=DataQualityIssueCode.MISSING_EXECUTION_TIMEFRAME,
                message="Execution timeframe batch contains no klines",
            )
        )
        return

    if execution_last_ts > as_of_timestamp:
        alignment_issues.append(
            DataQualityIssue(
                code=DataQualityIssueCode.TIMEFRAME_ALIGNMENT,
                timestamp=execution_last_ts,
                message="Execution timeframe has a bar after the as_of timestamp",
            )
        )

    for context in batch.contexts:
        for kline in context.klines:
            if kline.timestamp > as_of_timestamp:
                alignment_issues.append(
                    DataQualityIssue(
                        code=DataQualityIssueCode.FUTURE_CONTEXT_BAR,
                        timestamp=kline.timestamp,
                        message=f"Context timeframe {context.timeframe} contains a future bar",
                    )
                )

        context_last_ts = context.last_timestamp
        if context_last_ts is None:
            alignment_issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.TIMEFRAME_ALIGNMENT,
                    message=f"Context timeframe {context.timeframe} contains no klines",
                )
            )
            continue
        if context_last_ts > execution_last_ts:
            alignment_issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.TIMEFRAME_ALIGNMENT,
                    timestamp=context_last_ts,
                    message=f"Context timeframe {context.timeframe} is ahead of execution timeframe",
                )
            )


def _validate_values(
    klines: List[Kline],
    issues: List[DataQualityIssue],
) -> None:
    for kline in klines:
        if kline.timestamp < 0:
            issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.INVALID_TIMESTAMP,
                    timestamp=kline.timestamp,
                    message="Kline timestamp must be non-negative",
                )
            )

        prices = [kline.open, kline.high, kline.low, kline.close]
        if any(price <= 0 for price in prices) or kline.low > kline.high:
            issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.INVALID_OHLC,
                    timestamp=kline.timestamp,
                    message="Kline OHLC values must be positive and satisfy low <= high",
                )
            )
            continue

        if not (kline.low <= kline.open <= kline.high) or not (
            kline.low <= kline.close <= kline.high
        ):
            issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.INVALID_OHLC,
                    timestamp=kline.timestamp,
                    message="Kline open and close must be within low/high range",
                )
            )

        if kline.volume < 0:
            issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.INVALID_VOLUME,
                    timestamp=kline.timestamp,
                    message="Kline volume must be non-negative",
                )
            )


def _validate_order_and_duplicates(
    klines: List[Kline],
    issues: List[DataQualityIssue],
) -> None:
    seen = set()
    previous_ts: Optional[int] = None

    for kline in klines:
        if kline.timestamp in seen:
            issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.DUPLICATE_TIMESTAMP,
                    timestamp=kline.timestamp,
                    message="Duplicate kline timestamp",
                )
            )
        seen.add(kline.timestamp)

        if previous_ts is not None and kline.timestamp <= previous_ts:
            issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.OUT_OF_ORDER,
                    timestamp=kline.timestamp,
                    message="Klines must be strictly increasing by timestamp",
                )
            )
        previous_ts = kline.timestamp


def _validate_gaps(
    klines: List[Kline],
    interval_seconds: int,
    issues: List[DataQualityIssue],
) -> None:
    unique_timestamps = sorted({kline.timestamp for kline in klines})
    for previous_ts, current_ts in zip(unique_timestamps, unique_timestamps[1:]):
        expected_ts = previous_ts + interval_seconds
        if current_ts <= expected_ts:
            continue

        missing_count = (current_ts - previous_ts) // interval_seconds - 1
        for offset in range(1, missing_count + 1):
            missing_ts = previous_ts + interval_seconds * offset
            issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.MISSING_KLINE,
                    timestamp=missing_ts,
                    message="Missing kline for expected timestamp",
                )
            )


def _validate_expected_window(
    klines: List[Kline],
    interval_seconds: int,
    expected_start_ts: Optional[int],
    expected_end_ts: Optional[int],
    issues: List[DataQualityIssue],
) -> None:
    if expected_start_ts is None and expected_end_ts is None:
        return

    timestamps = {kline.timestamp for kline in klines}

    if expected_start_ts is not None and expected_end_ts is not None:
        if expected_end_ts < expected_start_ts:
            issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.INVALID_TIMESTAMP,
                    timestamp=expected_end_ts,
                    message="Expected end timestamp must be greater than or equal to start timestamp",
                )
            )
            return

        for expected_ts in range(expected_start_ts, expected_end_ts + interval_seconds, interval_seconds):
            if expected_ts not in timestamps:
                issues.append(
                    DataQualityIssue(
                        code=DataQualityIssueCode.MISSING_KLINE,
                        timestamp=expected_ts,
                        message="Missing kline for expected sync window timestamp",
                    )
                )
        return

    if expected_start_ts is not None and expected_start_ts not in timestamps:
        issues.append(
            DataQualityIssue(
                code=DataQualityIssueCode.MISSING_KLINE,
                timestamp=expected_start_ts,
                message="Missing kline for expected start timestamp",
            )
        )

    if expected_end_ts is not None and expected_end_ts not in timestamps:
        issues.append(
            DataQualityIssue(
                code=DataQualityIssueCode.MISSING_KLINE,
                timestamp=expected_end_ts,
                message="Missing kline for expected end timestamp",
            )
        )


def _validate_incomplete_last_bar(
    klines: List[Kline],
    issues: List[DataQualityIssue],
) -> None:
    if not klines:
        return
    last_bar = klines[-1]
    if getattr(last_bar, "is_complete", None) is not False:
        return

    issues.append(
        DataQualityIssue(
            code=DataQualityIssueCode.INCOMPLETE_LAST_BAR,
            timestamp=last_bar.timestamp,
            message="Last kline is marked incomplete and must not be treated as a closed bar",
            fatal=False,
        )
    )
