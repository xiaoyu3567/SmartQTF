import pytest

from quant.data.multi_timeframe import MultiTimeframeKlineBatch, TimeframeKlineBatch
from quant.data.quality import validate_multi_timeframe_klines
from quant.data.schemas.market import Kline
from quant.features.multi_timeframe import (
    MultiTimeframeFeaturePipeline,
    MultiTimeframeFeaturePipelineInput,
    MultiTimeframeFeatureQualityError,
)
from quant.features.pipeline import FeaturePipelineConfig
from quant.schemas.feature import FeatureSnapshot, MultiTimeframeFeatureSnapshot


SYMBOL = "BTCUSDT"
VENUE = "fixture"


def _kline(timestamp: int, close: float, *, validate_schema: bool = True) -> Kline:
    payload = {
        "timestamp": timestamp,
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 100.0,
        "is_complete": True,
    }
    if validate_schema:
        return Kline(**payload)
    if hasattr(Kline, "model_construct"):
        return Kline.model_construct(**payload)
    return Kline.construct(**payload)


def _batch(
    timeframe: str,
    *,
    role: str = "context",
    closes=None,
    start_ts: int = 1700000000,
    step: int = 300,
    validate_schema: bool = True,
) -> TimeframeKlineBatch:
    closes = closes or [100.0, 101.0, 102.0, 103.0, 104.0]
    return TimeframeKlineBatch(
        symbol=SYMBOL,
        timeframe=timeframe,
        venue=VENUE,
        role=role,
        klines=[
            _kline(start_ts + index * step, close, validate_schema=validate_schema)
            for index, close in enumerate(closes)
        ],
    )


def _envelope(
    *,
    execution=None,
    contexts=None,
    as_of_timestamp: int = 1700001200,
) -> MultiTimeframeKlineBatch:
    return MultiTimeframeKlineBatch(
        symbol=SYMBOL,
        venue=VENUE,
        execution_timeframe="5m",
        execution=execution,
        contexts=contexts or [],
        as_of_timestamp=as_of_timestamp,
    )


def _pipeline() -> MultiTimeframeFeaturePipeline:
    return MultiTimeframeFeaturePipeline(
        FeaturePipelineConfig(
            fast_ma_window=2,
            slow_ma_window=3,
            rsi_window=3,
            atr_window=3,
            market_structure_lookback=3,
        )
    )


def _aligned_start(last_ts: int, step: int, count: int) -> int:
    return last_ts - step * (count - 1)


def test_multi_timeframe_feature_pipeline_computes_real_snapshots_and_alignment():
    execution_last_ts = 1700000000 + 4 * 300
    batch = _envelope(
        execution=_batch("5m", role="execution", step=300),
        contexts=[
            _batch("15m", step=900, start_ts=_aligned_start(execution_last_ts, 900, 5)),
            _batch("1h", step=3600, start_ts=_aligned_start(execution_last_ts, 3600, 5)),
            _batch("4h", step=14400, start_ts=_aligned_start(execution_last_ts, 14400, 5)),
        ],
        as_of_timestamp=execution_last_ts,
    )
    quality_report = validate_multi_timeframe_klines(batch, as_of_timestamp=batch.as_of_timestamp)

    snapshot = _pipeline().compute(
        MultiTimeframeFeaturePipelineInput(
            batch=batch,
            quality_report=quality_report,
            snapshot_id="mtf-feature-success",
        )
    )

    assert isinstance(snapshot, MultiTimeframeFeatureSnapshot)
    assert snapshot.snapshot_id == "mtf-feature-success"
    assert snapshot.symbol == SYMBOL
    assert snapshot.execution_timeframe == "5m"
    assert sorted(snapshot.timeframe_snapshots) == ["15m", "1h", "4h", "5m"]
    assert all(
        isinstance(item, FeatureSnapshot)
        for item in snapshot.timeframe_snapshots.values()
    )
    assert snapshot.execution_snapshot is snapshot.timeframe_snapshots["5m"]
    assert snapshot.quality_report_refs["5m"].passed is True
    assert snapshot.quality_report_refs["1h"].checked_count == 5
    assert snapshot.alignment_features["execution_bias"] == "bullish"
    assert snapshot.alignment_features["higher_timeframe_bias"] == "bullish"
    assert snapshot.alignment_features["execution_aligned_with_1h"] is True
    assert snapshot.alignment_features["execution_aligned_with_4h"] is True
    assert snapshot.alignment_features["conflict_count"] == 0
    assert snapshot.alignment_features["timeframe.5m.rsi_available"] is True


def test_multi_timeframe_feature_pipeline_blocks_failed_quality_report():
    execution_last_ts = 1700000000 + 4 * 300
    invalid_context = _batch(
        "15m",
        step=900,
        start_ts=_aligned_start(execution_last_ts, 900, 5),
        closes=[100.0, 101.0, 102.0, 103.0, 104.0],
        validate_schema=True,
    )
    invalid_context.klines[-1].high = invalid_context.klines[-1].low - 1.0
    batch = _envelope(
        execution=_batch("5m", role="execution", step=300),
        contexts=[invalid_context],
        as_of_timestamp=execution_last_ts,
    )
    quality_report = validate_multi_timeframe_klines(batch, as_of_timestamp=batch.as_of_timestamp)

    assert not quality_report.passed
    assert quality_report.fatal_timeframes == ["15m"]

    with pytest.raises(MultiTimeframeFeatureQualityError) as exc_info:
        _pipeline().compute(
            MultiTimeframeFeaturePipelineInput(
                batch=batch,
                quality_report=quality_report,
            )
        )

    assert exc_info.value.quality_report is quality_report
    assert exc_info.value.failed_timeframes == ["15m"]


def test_multi_timeframe_feature_pipeline_does_not_fabricate_alignment_when_history_is_short():
    execution_last_ts = 1700000000 + 300
    batch = _envelope(
        execution=_batch("5m", role="execution", closes=[100.0, 101.0], step=300),
        contexts=[
            _batch(
                "1h",
                closes=[100.0, 101.0],
                step=3600,
                start_ts=_aligned_start(execution_last_ts, 3600, 2),
            ),
        ],
        as_of_timestamp=execution_last_ts,
    )

    snapshot = _pipeline().compute(MultiTimeframeFeaturePipelineInput(batch=batch))

    assert snapshot.timeframe_snapshots["5m"].values["ma_fast"] == 100.5
    assert snapshot.timeframe_snapshots["5m"].values["ma_slow"] is None
    assert snapshot.timeframe_snapshots["1h"].values["ma_slow"] is None
    assert snapshot.alignment_features["execution_bias"] == "unknown"
    assert snapshot.alignment_features["higher_timeframe_bias"] == "unknown"
    assert snapshot.alignment_features["execution_aligned_with_1h"] is None
    assert snapshot.alignment_features["unknown_bias_count"] == 2
    assert snapshot.alignment_features["alignment_available"] is False


def test_multi_timeframe_feature_pipeline_records_context_conflict():
    execution_last_ts = 1700000000 + 3 * 300
    batch = _envelope(
        execution=_batch("5m", role="execution", closes=[100.0, 101.0, 102.0, 103.0], step=300),
        contexts=[
            _batch(
                "1h",
                closes=[110.0, 109.0, 108.0, 107.0],
                step=3600,
                start_ts=_aligned_start(execution_last_ts, 3600, 4),
            ),
            _batch(
                "4h",
                closes=[100.0, 101.0, 102.0, 103.0],
                step=14400,
                start_ts=_aligned_start(execution_last_ts, 14400, 4),
            ),
        ],
        as_of_timestamp=execution_last_ts,
    )

    snapshot = _pipeline().compute(
        MultiTimeframeFeaturePipelineInput(
            batch=batch,
            snapshot_id="mtf-feature-conflict",
        )
    )

    assert snapshot.alignment_features["execution_bias"] == "bullish"
    assert snapshot.alignment_features["timeframe.1h.bias"] == "bearish"
    assert snapshot.alignment_features["timeframe.4h.bias"] == "bullish"
    assert snapshot.alignment_features["higher_timeframe_bias"] == "mixed"
    assert snapshot.alignment_features["execution_aligned_with_1h"] is False
    assert snapshot.alignment_features["execution_aligned_with_4h"] is True
    assert snapshot.alignment_features["conflict_count"] == 1
    assert snapshot.alignment_features["execution_aligned_with_higher_timeframes"] is False

    payload = snapshot.to_payload()
    restored = MultiTimeframeFeatureSnapshot.from_payload(payload)
    assert restored == snapshot
