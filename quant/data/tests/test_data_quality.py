from typing import Optional

from quant.data.quality import DataQualityIssueCode, validate_klines
from quant.data.schemas.market import Kline


def _kline(
    timestamp: int,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 1000.0,
    *,
    is_complete: Optional[bool] = None,
    validate_schema: bool = True,
) -> Kline:
    payload = {
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_complete": is_complete,
    }
    if validate_schema:
        return Kline(**payload)
    if hasattr(Kline, "model_construct"):
        return Kline.model_construct(**payload)
    return Kline.construct(**payload)


def test_validate_klines_passes_clean_contiguous_data():
    report = validate_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[_kline(1700000040), _kline(1700000100), _kline(1700000160)],
    )

    assert report.passed
    assert report.checked_count == 3
    assert report.issues == []
    assert report.first_timestamp == 1700000040
    assert report.last_timestamp == 1700000160
    assert report.has_incomplete_last_bar is False


def test_validate_klines_detects_missing_kline():
    report = validate_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[_kline(1700000040), _kline(1700000160)],
    )

    assert not report.passed
    assert [(issue.code, issue.timestamp) for issue in report.issues] == [
        (DataQualityIssueCode.MISSING_KLINE, 1700000100)
    ]


def test_validate_klines_detects_duplicates_and_out_of_order_data():
    report = validate_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[
            _kline(1700000100),
            _kline(1700000040),
            _kline(1700000040),
        ],
    )

    issue_codes = [issue.code for issue in report.issues]
    assert DataQualityIssueCode.OUT_OF_ORDER in issue_codes
    assert DataQualityIssueCode.DUPLICATE_TIMESTAMP in issue_codes


def test_validate_klines_detects_invalid_ohlc_values():
    report = validate_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[
            _kline(
                1700000040,
                open_=102.0,
                high=101.0,
                low=99.0,
                close=100.5,
                validate_schema=False,
            ),
            _kline(
                1700000100,
                open_=100.0,
                high=101.0,
                low=99.0,
                close=102.0,
                validate_schema=False,
            ),
        ],
    )

    assert not report.passed
    assert [issue.code for issue in report.issues] == [
        DataQualityIssueCode.INVALID_OHLC,
        DataQualityIssueCode.INVALID_OHLC,
    ]


def test_validate_klines_detects_invalid_volume_and_timestamp():
    report = validate_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[_kline(-60, volume=-1.0, validate_schema=False)],
    )

    assert not report.passed
    assert [issue.code for issue in report.issues] == [
        DataQualityIssueCode.INVALID_TIMESTAMP,
        DataQualityIssueCode.INVALID_VOLUME,
    ]


def test_validate_klines_marks_incomplete_last_bar_as_non_fatal_issue():
    report = validate_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[
            _kline(1700000040, is_complete=True),
            _kline(1700000100, is_complete=False),
        ],
    )

    assert report.passed
    assert report.has_incomplete_last_bar is True
    assert report.incomplete_last_bar_timestamp == 1700000100
    assert report.included_incomplete_bar is True
    assert report.incomplete_bar_timestamp == 1700000100
    assert [(issue.code, issue.timestamp, issue.fatal) for issue in report.issues] == [
        (DataQualityIssueCode.INCOMPLETE_LAST_BAR, 1700000100, False)
    ]

    payload = report.to_payload()
    assert payload["passed"] is True
    assert payload["has_incomplete_last_bar"] is True
    assert payload["included_incomplete_bar"] is True

    display_payload = report.to_display_payload()
    assert "incomplete_last_bar_time" in display_payload
    assert "incomplete_bar_time" in display_payload


def test_validate_klines_reports_expected_window_boundaries_and_display_times():
    report = validate_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        expected_start_ts=1700000040,
        expected_end_ts=1700000160,
        klines=[_kline(1700000040), _kline(1700000100), _kline(1700000160)],
    )

    assert report.passed
    assert report.first_timestamp == 1700000040
    assert report.last_timestamp == 1700000160
    assert report.expected_start_ts == 1700000040
    assert report.expected_end_ts == 1700000160

    display_payload = report.to_display_payload()
    assert "first_time" in display_payload
    assert "last_time" in display_payload
    assert "expected_start_time" in display_payload
    assert "expected_end_time" in display_payload


def test_quality_report_keeps_legacy_incomplete_bar_aliases_compatible():
    report = validate_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[_kline(1700000040, is_complete=False)],
    )

    round_trip = type(report).from_payload(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "interval_seconds": 60,
            "checked_count": 1,
            "issues": [],
            "included_incomplete_bar": True,
            "incomplete_bar_timestamp": 1700000040,
        }
    )

    assert report.has_incomplete_last_bar is True
    assert round_trip.has_incomplete_last_bar is True
    assert round_trip.incomplete_last_bar_timestamp == 1700000040
