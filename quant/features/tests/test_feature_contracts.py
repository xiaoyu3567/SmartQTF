import pytest
from pydantic import ValidationError

from quant.data.quality import DataQualityIssueCode, validate_klines
from quant.data.schemas.market import Kline
from quant.features.indicators.technical import AverageTrueRange, RelativeStrengthIndex
from quant.features.pipeline import (
    AdvancedFeaturePipeline,
    FeaturePipelineConfig,
    FeaturePipelineInput,
    FeatureQualityError,
)
from quant.schemas.feature import (
    FeatureSnapshot,
    FundingRateSnapshot,
    NetflowSnapshot,
    OpenInterestSnapshot,
    OrderBookLevel,
    OrderBookSnapshot,
)


SYMBOL = "BTC-USDT-SWAP"
TIMEFRAME = "1m"
VENUE = "okx"


def _kline(
    timestamp: int,
    close: float,
    high: float | None = None,
    low: float | None = None,
    is_complete: bool | None = None,
) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        volume=100.0,
        is_complete=is_complete,
    )


def test_feature_pipeline_degrades_without_optional_advanced_inputs():
    klines = [
        _kline(1, 100.0, high=101.0, low=99.0),
        _kline(2, 101.0, high=102.0, low=100.0),
        _kline(3, 103.0, high=104.0, low=101.0),
        _kline(4, 102.0, high=103.0, low=100.0),
    ]

    snapshot = AdvancedFeaturePipeline(
        FeaturePipelineConfig(
            fast_ma_window=2,
            slow_ma_window=3,
            market_structure_lookback=2,
        )
    ).compute(
        FeaturePipelineInput(
            klines=klines,
            index=2,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            venue=VENUE,
            snapshot_id="feature-contract-no-optional-inputs",
            quality_report=validate_klines(klines, symbol=SYMBOL, timeframe=TIMEFRAME),
        )
    )

    assert snapshot.snapshot_id == "feature-contract-no-optional-inputs"
    assert snapshot.symbol == SYMBOL
    assert snapshot.timeframe == TIMEFRAME
    assert snapshot.as_of_timestamp == 3
    assert snapshot.source_window_start == 1
    assert snapshot.source_window_end == 3
    assert snapshot.values["close"] == 103.0
    assert snapshot.values["fast_ma"] == 102.0
    assert snapshot.values["slow_ma"] == pytest.approx((100.0 + 101.0 + 103.0) / 3)
    assert snapshot.values["market_structure.structure_state"] in {"breakout", "range"}
    assert not any(name.startswith("orderflow.") for name in snapshot.values)
    assert not any(name.startswith("cross_market.") for name in snapshot.values)


def test_feature_pipeline_rejects_failed_quality_report_before_computation():
    klines = [
        _kline(60, 100.0),
        _kline(180, 102.0),
    ]
    quality_report = validate_klines(klines, symbol=SYMBOL, timeframe=TIMEFRAME)

    assert quality_report.passed is False
    assert quality_report.issues[0].code == DataQualityIssueCode.MISSING_KLINE

    with pytest.raises(FeatureQualityError) as exc_info:
        AdvancedFeaturePipeline().compute(
            FeaturePipelineInput(
                klines=klines,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                venue=VENUE,
                snapshot_id="feature-contract-quality-failed",
                quality_report=quality_report,
            )
        )

    assert exc_info.value.quality_report is quality_report
    assert exc_info.value.rejection.layer == "feature"
    assert exc_info.value.rejection.code == "quality_report_failed"
    assert "missing_kline" in exc_info.value.rejection.message


def test_feature_pipeline_skips_incomplete_last_bar_by_default_with_audit_metadata():
    klines = [
        _kline(1, 100.0, is_complete=True),
        _kline(2, 101.0, is_complete=True),
        _kline(3, 102.0, is_complete=True),
        _kline(4, 999.0, is_complete=False),
    ]

    snapshot = AdvancedFeaturePipeline(
        FeaturePipelineConfig(
            fast_ma_window=2,
            slow_ma_window=3,
            market_structure_lookback=2,
        )
    ).compute(
        FeaturePipelineInput(
            klines=klines,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            venue=VENUE,
            snapshot_id="feature-contract-skip-incomplete",
        )
    )

    assert snapshot.timestamp == 3
    assert snapshot.source_window_end == 3
    assert snapshot.values["close"] == 102.0
    assert snapshot.is_complete_bar is True
    assert snapshot.requested_index == 3
    assert snapshot.effective_index == 2
    assert snapshot.input_bar_count == 4
    assert snapshot.include_incomplete_last_bar is False
    assert snapshot.skipped_incomplete_last_bar is True
    assert snapshot.skipped_incomplete_bar_timestamp == 4

    payload = snapshot.to_payload()
    assert payload["requested_index"] == 3
    assert payload["effective_index"] == 2
    assert payload["skipped_incomplete_bar_timestamp"] == 4


def test_feature_pipeline_can_explicitly_include_incomplete_last_bar_with_safety_metadata():
    klines = [
        _kline(1, 100.0, is_complete=True),
        _kline(2, 101.0, is_complete=True),
        _kline(3, 102.0, is_complete=True),
        _kline(4, 103.0, is_complete=False),
    ]

    snapshot = AdvancedFeaturePipeline(
        FeaturePipelineConfig(
            fast_ma_window=2,
            slow_ma_window=3,
            market_structure_lookback=2,
            include_incomplete_last_bar=True,
        )
    ).compute(
        FeaturePipelineInput(
            klines=klines,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            venue=VENUE,
            snapshot_id="feature-contract-include-incomplete",
        )
    )

    assert snapshot.timestamp == 4
    assert snapshot.source_window_end == 4
    assert snapshot.values["close"] == 103.0
    assert snapshot.is_complete_bar is False
    assert snapshot.requested_index == 3
    assert snapshot.effective_index == 3
    assert snapshot.input_bar_count == 4
    assert snapshot.include_incomplete_last_bar is True
    assert snapshot.skipped_incomplete_last_bar is False
    assert snapshot.skipped_incomplete_bar_timestamp is None


def test_feature_pipeline_records_indicator_availability_for_insufficient_history():
    klines = [_kline(timestamp, 100.0 + timestamp) for timestamp in range(1, 11)]

    snapshot = AdvancedFeaturePipeline().compute(
        FeaturePipelineInput(
            klines=klines,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            venue=VENUE,
            snapshot_id="feature-contract-short-history",
        )
    )

    assert snapshot.values["rsi"] is None
    assert snapshot.values["atr"] is None
    assert snapshot.values["macd"] is None
    assert snapshot.values["macd.signal"] is None
    assert snapshot.values["macd.histogram"] is None

    rsi_availability = snapshot.feature_availability["rsi"]
    assert rsi_availability.available is False
    assert rsi_availability.reason == "insufficient_history"
    assert rsi_availability.required_bars == 15
    assert rsi_availability.actual_bars == 10

    assert snapshot.feature_availability["atr"].required_bars == 15
    assert snapshot.feature_availability["macd"].required_bars == 26
    assert snapshot.feature_availability["macd.signal"].required_bars == 34
    assert snapshot.feature_availability["macd.histogram"].reason == "insufficient_history"
    assert snapshot.feature_parameters["rsi"] == {"window": 14}
    assert snapshot.feature_parameters["atr"] == {"window": 14}
    assert snapshot.feature_parameters["macd"] == {
        "fast_window": 12,
        "slow_window": 26,
        "signal_window": 9,
    }

    payload = snapshot.to_payload()
    assert payload["feature_availability"]["rsi"]["reason"] == "insufficient_history"
    assert payload["feature_parameters"]["macd"]["slow_window"] == 26
    assert snapshot.to_display_payload()["feature_availability"]["rsi"]["actual_bars"] == 10
    assert FeatureSnapshot.from_payload(payload) == snapshot


def test_feature_pipeline_marks_indicator_availability_ready_for_sufficient_history():
    klines = [
        _kline(
            timestamp,
            100.0 + (timestamp * 0.5),
            high=101.0 + (timestamp * 0.5),
            low=99.0 + (timestamp * 0.5),
        )
        for timestamp in range(1, 101)
    ]

    snapshot = AdvancedFeaturePipeline().compute(
        FeaturePipelineInput(
            klines=klines,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            venue=VENUE,
            snapshot_id="feature-contract-long-history",
        )
    )

    assert snapshot.values["rsi"] is not None
    assert snapshot.values["atr"] is not None
    assert snapshot.values["macd"] is not None
    assert snapshot.values["macd.signal"] is not None
    assert snapshot.values["macd.histogram"] is not None

    for feature_name in ["rsi", "atr", "macd", "macd.signal", "macd.histogram"]:
        availability = snapshot.feature_availability[feature_name]
        assert availability.available is True
        assert availability.reason is None
        assert availability.actual_bars == 100


def test_rsi_handles_down_flat_and_insufficient_windows():
    rsi = RelativeStrengthIndex(window=3)

    assert rsi.compute([_kline(1, 10.0), _kline(2, 9.0), _kline(3, 8.0)], 2) is None
    assert rsi.compute([_kline(1, 13.0), _kline(2, 12.0), _kline(3, 11.0), _kline(4, 10.0)], 3) == 0.0
    assert rsi.compute([_kline(1, 10.0), _kline(2, 10.0), _kline(3, 10.0), _kline(4, 10.0)], 3) == 50.0
    assert rsi.compute([_kline(1, 10.0), _kline(2, 11.0), _kline(3, 12.0), _kline(4, 13.0)], 3) == 100.0


def test_atr_returns_non_negative_and_none_until_window_ready():
    atr = AverageTrueRange(window=3)
    klines = [
        _kline(1, 10.0, high=11.0, low=9.0),
        _kline(2, 11.0, high=12.0, low=10.0),
        _kline(3, 12.0, high=13.0, low=11.0),
        _kline(4, 13.0, high=15.0, low=12.0),
    ]

    assert atr.compute(klines[:3], 2) is None
    assert atr.compute(klines, 3) >= 0.0


def test_auxiliary_market_snapshots_reject_future_as_of_timestamps():
    with pytest.raises(ValidationError, match="as_of_timestamp"):
        OpenInterestSnapshot(
            snapshot_id="oi-future",
            timestamp=1,
            symbol=SYMBOL,
            venue=VENUE,
            as_of_timestamp=2,
            open_interest=10.0,
        )

    with pytest.raises(ValidationError, match="as_of_timestamp"):
        FundingRateSnapshot(
            snapshot_id="funding-future",
            timestamp=1,
            symbol=SYMBOL,
            venue=VENUE,
            as_of_timestamp=2,
            funding_rate=0.0001,
        )

    with pytest.raises(ValidationError, match="as_of_timestamp"):
        NetflowSnapshot(
            snapshot_id="netflow-future",
            timestamp=1,
            symbol=SYMBOL,
            venue=VENUE,
            as_of_timestamp=2,
            timeframe=TIMEFRAME,
            inflow=10.0,
            outflow=4.0,
            netflow=6.0,
        )

    with pytest.raises(ValidationError, match="as_of_timestamp"):
        OrderBookSnapshot(
            snapshot_id="book-future",
            timestamp=1,
            symbol=SYMBOL,
            venue=VENUE,
            as_of_timestamp=2,
            bids=[OrderBookLevel(price=99.0, quantity=1.0)],
            asks=[OrderBookLevel(price=101.0, quantity=1.0)],
        )
