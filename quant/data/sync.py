from typing import Iterable, List, Optional

from pydantic import Field

from quant.data.quality import KlineQualityReport, validate_klines
from quant.data.schemas.market import Kline
from quant.utils.time_format import add_display_times
from quant.data.storage import KlineStore
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


class KlineSyncRequest(SmartQTFModel):
    symbol: str
    timeframe: str
    start_ts: int
    end_ts: int
    interval_seconds: int = Field(gt=0)

    def __init__(self, **data):
        super().__init__(**data)
        if self.end_ts < self.start_ts:
            raise ValueError("end_ts must be greater than or equal to start_ts")


class KlineSyncPlan(SmartQTFModel):
    symbol: str
    timeframe: str
    interval_seconds: int = Field(gt=0)
    closed_until_ts: Optional[int]
    requests: List[KlineSyncRequest]

    @property
    def is_empty(self) -> bool:
        return not self.requests


class KlineSyncResult(SmartQTFModel):
    request: KlineSyncRequest
    quality_report: KlineQualityReport
    saved_count: int = 0

    @property
    def passed(self) -> bool:
        return self.quality_report.passed

    def to_display_payload(self):
        return add_display_times(self.to_payload())


class KlineSyncQualityError(ValueError):
    def __init__(self, report: KlineQualityReport):
        self.report = report
        issue_codes = ", ".join(issue.code for issue in report.issues) or "unknown"
        super().__init__(f"kline sync quality check failed: {issue_codes}")


def timeframe_to_seconds(timeframe: str) -> int:
    try:
        return TIMEFRAME_SECONDS[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc


def last_closed_kline_ts(now_ts: int, interval_seconds: int) -> Optional[int]:
    current_open_ts = (now_ts // interval_seconds) * interval_seconds
    closed_ts = current_open_ts - interval_seconds
    if closed_ts < 0:
        return None
    return closed_ts


def build_incremental_kline_sync_plan(
    store: KlineStore,
    symbol: str,
    timeframe: str,
    start_ts: int,
    now_ts: int,
) -> KlineSyncPlan:
    interval_seconds = timeframe_to_seconds(timeframe)
    closed_until_ts = last_closed_kline_ts(now_ts=now_ts, interval_seconds=interval_seconds)
    if closed_until_ts is None or start_ts > closed_until_ts:
        return KlineSyncPlan(
            symbol=symbol,
            timeframe=timeframe,
            interval_seconds=interval_seconds,
            closed_until_ts=closed_until_ts,
            requests=[],
        )

    existing = store.load_klines(
        symbol=symbol,
        timeframe=timeframe,
        start_ts=start_ts,
        end_ts=closed_until_ts,
    )
    existing_timestamps = {kline.timestamp for kline in existing}
    expected_timestamps = range(start_ts, closed_until_ts + interval_seconds, interval_seconds)
    missing_timestamps = [
        timestamp for timestamp in expected_timestamps if timestamp not in existing_timestamps
    ]

    requests = [
        KlineSyncRequest(
            symbol=symbol,
            timeframe=timeframe,
            start_ts=window[0],
            end_ts=window[-1],
            interval_seconds=interval_seconds,
        )
        for window in _group_consecutive_timestamps(missing_timestamps, interval_seconds)
    ]

    return KlineSyncPlan(
        symbol=symbol,
        timeframe=timeframe,
        interval_seconds=interval_seconds,
        closed_until_ts=closed_until_ts,
        requests=requests,
    )


def validate_kline_sync_batch(
    request: KlineSyncRequest,
    klines: Iterable[Kline],
) -> KlineQualityReport:
    return validate_klines(
        klines=klines,
        symbol=request.symbol,
        timeframe=request.timeframe,
        expected_start_ts=request.start_ts,
        expected_end_ts=request.end_ts,
    )


def save_validated_kline_sync_batch(
    store: KlineStore,
    request: KlineSyncRequest,
    klines: Iterable[Kline],
) -> KlineSyncResult:
    batch = list(klines)
    quality_report = validate_kline_sync_batch(request, batch)
    if not quality_report.passed:
        raise KlineSyncQualityError(quality_report)

    saved_count = store.save_klines(
        symbol=request.symbol,
        timeframe=request.timeframe,
        klines=batch,
    )
    return KlineSyncResult(
        request=request,
        quality_report=quality_report,
        saved_count=saved_count,
    )


def _group_consecutive_timestamps(
    timestamps: Iterable[int],
    interval_seconds: int,
) -> List[List[int]]:
    windows: List[List[int]] = []
    current: List[int] = []

    for timestamp in timestamps:
        if not current or timestamp == current[-1] + interval_seconds:
            current.append(timestamp)
            continue

        windows.append(current)
        current = [timestamp]

    if current:
        windows.append(current)

    return windows
