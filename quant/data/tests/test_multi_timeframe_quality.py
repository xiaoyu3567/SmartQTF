from typing import Optional

from quant.data.multi_timeframe import MultiTimeframeKlineBatch, TimeframeKlineBatch
from quant.data.quality import DataQualityIssueCode, validate_multi_timeframe_klines
from quant.data.schemas.market import Kline


def _kline(
    timestamp: int,
    *,
    close: float = 100.5,
    is_complete: Optional[bool] = True,
    validate_schema: bool = True,
) -> Kline:
    payload = {
        "timestamp": timestamp,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": close,
        "volume": 1000.0,
        "is_complete": is_complete,
    }
    if validate_schema:
        return Kline(**payload)
    if hasattr(Kline, "model_construct"):
        return Kline.model_construct(**payload)
    return Kline.construct(**payload)


def _batch(timeframe: str, timestamps, *, role: str = "context") -> TimeframeKlineBatch:
    return TimeframeKlineBatch(
        symbol="BTCUSDT",
        timeframe=timeframe,
        venue="fixture",
        role=role,
        klines=[_kline(timestamp) for timestamp in timestamps],
    )


def _envelope(
    *,
    execution=None,
    contexts=None,
    as_of_timestamp: Optional[int] = 1700000300,
) -> MultiTimeframeKlineBatch:
    return MultiTimeframeKlineBatch(
        symbol="BTCUSDT",
        venue="fixture",
        execution_timeframe="5m",
        execution=execution,
        contexts=contexts or [],
        as_of_timestamp=as_of_timestamp,
    )


def test_validate_multi_timeframe_klines_passes_aligned_fixture_data():
    report = validate_multi_timeframe_klines(
        _envelope(
            execution=_batch("5m", [1700000000, 1700000300], role="execution"),
            contexts=[
                _batch("15m", [1699999400, 1700000300]),
                _batch("1h", [1699996700, 1700000300]),
            ],
        )
    )

    assert report.passed
    assert report.fatal_timeframes == []
    assert report.alignment_issues == []
    assert sorted(report.timeframe_reports) == ["15m", "1h", "5m"]
    assert report.timeframe_reports["5m"].passed
    assert report.as_of_timestamp == 1700000300
    assert report.to_payload()["passed"] is True


def test_validate_multi_timeframe_klines_blocks_missing_execution_batch():
    report = validate_multi_timeframe_klines(
        _envelope(
            execution=None,
            contexts=[_batch("15m", [1700000000, 1700000900])],
        )
    )

    assert not report.passed
    assert report.fatal_timeframes == []
    assert [issue.code for issue in report.alignment_issues] == [
        DataQualityIssueCode.MISSING_EXECUTION_TIMEFRAME
    ]


def test_validate_multi_timeframe_klines_marks_single_timeframe_quality_failure():
    invalid_context = TimeframeKlineBatch(
        symbol="BTCUSDT",
        timeframe="15m",
        venue="fixture",
        role="context",
        klines=[
            _kline(1700000000),
            _kline(1700000900, close=102.0, validate_schema=False),
        ],
    )

    report = validate_multi_timeframe_klines(
        _envelope(
            execution=_batch("5m", [1700000000, 1700000300], role="execution"),
            contexts=[invalid_context],
        )
    )

    assert not report.passed
    assert report.fatal_timeframes == ["15m"]
    assert DataQualityIssueCode.INVALID_OHLC in [
        issue.code for issue in report.timeframe_reports["15m"].issues
    ]
    assert DataQualityIssueCode.TIMEFRAME_QUALITY_FAILED in [
        issue.code for issue in report.alignment_issues
    ]


def test_validate_multi_timeframe_klines_detects_future_context_bar():
    report = validate_multi_timeframe_klines(
        _envelope(
            execution=_batch("5m", [1700000000, 1700000300], role="execution"),
            contexts=[_batch("15m", [1700000000, 1700001200])],
            as_of_timestamp=1700000300,
        )
    )

    assert not report.passed
    assert [(issue.code, issue.timestamp) for issue in report.alignment_issues] == [
        (DataQualityIssueCode.FUTURE_CONTEXT_BAR, 1700001200),
        (DataQualityIssueCode.TIMEFRAME_ALIGNMENT, 1700001200),
    ]


def test_validate_multi_timeframe_klines_detects_empty_execution_batch():
    report = validate_multi_timeframe_klines(
        _envelope(
            execution=TimeframeKlineBatch(
                symbol="BTCUSDT",
                timeframe="5m",
                venue="fixture",
                role="execution",
                klines=[],
            ),
            contexts=[],
            as_of_timestamp=1700000300,
        )
    )

    assert not report.passed
    assert [issue.code for issue in report.alignment_issues] == [
        DataQualityIssueCode.MISSING_EXECUTION_TIMEFRAME
    ]
