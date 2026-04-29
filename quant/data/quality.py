from enum import Enum
from typing import Iterable, List, Optional

from quant.data.schemas.market import Kline
from quant.schemas.base import SmartQTFModel


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

    @property
    def passed(self) -> bool:
        return not any(issue.fatal for issue in self.issues)

    def to_payload(self):
        payload = super().to_payload()
        payload["passed"] = self.passed
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

    return KlineQualityReport(
        symbol=symbol,
        timeframe=timeframe,
        interval_seconds=interval_seconds,
        checked_count=len(ordered_klines),
        issues=issues,
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
