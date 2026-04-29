from quant.data.quality import DataQualityIssueCode, validate_klines
from quant.data.schemas.market import Kline


def _kline(
    timestamp: int,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 1000.0,
) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def test_validate_klines_passes_clean_contiguous_data():
    report = validate_klines(
        symbol="BTCUSDT",
        timeframe="1m",
        klines=[_kline(1700000040), _kline(1700000100), _kline(1700000160)],
    )

    assert report.passed
    assert report.checked_count == 3
    assert report.issues == []


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
            _kline(1700000040, open_=102.0, high=101.0, low=99.0, close=100.5),
            _kline(1700000100, open_=100.0, high=101.0, low=99.0, close=102.0),
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
        klines=[_kline(-60, volume=-1.0)],
    )

    assert not report.passed
    assert [issue.code for issue in report.issues] == [
        DataQualityIssueCode.INVALID_TIMESTAMP,
        DataQualityIssueCode.INVALID_VOLUME,
    ]
