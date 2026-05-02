import dataclasses
import json
import logging

import pytest

from quant.data.quality import validate_klines
from quant.data.schemas.market import Kline, KlineBatch, Trade
from quant.features.pipeline import (
    AdvancedFeaturePipeline,
    FeaturePipelineConfig,
    FeaturePipelineInput,
)
from quant.regime import RuleBasedRegimeDetector
from quant.schemas import RegimeKind, StrategySignal, TradeSide
from quant.schemas.feature import OrderBookLevel, OrderBookSnapshot
from quant.strategy.ma_crossover import MACrossoverStrategy
from quant.strategy.router import RegimeStrategyRouter


SYMBOL = "BTCUSDT"
TIMEFRAME = "5m"
VENUE = "unit-test"
START_TS = 1_710_000_000
INTERVAL_SECONDS = 5 * 60
LOGGER = logging.getLogger(__name__)


class BTCUSDTThirtyBarProvider:
    """Data-layer fixture that returns exactly 30 contiguous 5m BTCUSDT candles."""

    closes = [
        120.0,
        119.0,
        118.0,
        117.0,
        116.0,
        115.0,
        114.0,
        113.0,
        112.0,
        111.0,
        110.0,
        109.0,
        108.0,
        107.0,
        106.0,
        105.0,
        104.0,
        103.0,
        102.0,
        101.0,
        100.0,
        99.0,
        98.0,
        97.0,
        96.0,
        95.0,
        94.0,
        93.0,
        92.0,
        120.0,
    ]

    def get_kline_batch(self, symbol: str, timeframe: str) -> KlineBatch:
        assert symbol == SYMBOL
        assert timeframe == TIMEFRAME
        klines = []
        for index, close in enumerate(self.closes):
            open_ = close - 0.35
            high = close + 0.9
            low = close - 1.1
            klines.append(
                Kline(
                    timestamp=START_TS + index * INTERVAL_SECONDS,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=1_000.0 + index * 25.0,
                    is_complete=True,
                )
            )
        return KlineBatch(symbol=symbol, timeframe=timeframe, venue=VENUE, klines=klines)

    def get_trades(self, symbol: str) -> list[Trade]:
        assert symbol == SYMBOL
        last_ts = START_TS + (len(self.closes) - 1) * INTERVAL_SECONDS
        return [
            Trade(timestamp=last_ts - 60, price=118.9, size=12.0, side="buy"),
            Trade(timestamp=last_ts - 30, price=119.4, size=4.0, side="sell"),
        ]

    def get_orderbook(self, symbol: str) -> OrderBookSnapshot:
        assert symbol == SYMBOL
        last_ts = START_TS + (len(self.closes) - 1) * INTERVAL_SECONDS
        return OrderBookSnapshot(
            snapshot_id="btcusdt-book-30x5m",
            timestamp=last_ts,
            symbol=symbol,
            venue=VENUE,
            as_of_timestamp=last_ts,
            bids=[OrderBookLevel(price=119.8, quantity=15.0)],
            asks=[OrderBookLevel(price=120.0, quantity=5.0)],
            depth=1,
        )


def test_btcusdt_30x5m_data_quality_feature_regime_strategy_pipeline_logs_every_layer(caplog):
    caplog.set_level(logging.INFO, logger=__name__)
    io_logs = []
    provider = BTCUSDTThirtyBarProvider()
    feature_config = FeaturePipelineConfig(
        fast_ma_window=3,
        slow_ma_window=5,
        rsi_window=14,
        atr_window=14,
        market_structure_lookback=20,
        large_trade_threshold=10.0,
        orderbook_depth=1,
    )

    # DATA: 获取真实 KlineBatch schema 对象，而不是手工构造 Feature/Regime 快照。
    data_input = {"symbol": SYMBOL, "timeframe": TIMEFRAME, "provider": provider.__class__.__name__}
    batch = provider.get_kline_batch(SYMBOL, TIMEFRAME)
    _log_layer_io(io_logs, "data", data_input, batch)

    assert batch.symbol == SYMBOL
    assert batch.timeframe == TIMEFRAME
    assert len(batch.klines) == 30
    assert [kline.timestamp for kline in batch.klines] == [
        START_TS + index * INTERVAL_SECONDS for index in range(30)
    ]

    # Quality: 用真实 validate_klines 校验 30 根 5m K 线。
    quality_input = {
        "symbol": batch.symbol,
        "timeframe": batch.timeframe,
        "kline_count": len(batch.klines),
        "expected_start_ts": batch.klines[0].timestamp,
        "expected_end_ts": batch.klines[-1].timestamp,
    }
    quality_report = validate_klines(
        klines=batch.klines,
        symbol=batch.symbol,
        timeframe=batch.timeframe,
        expected_start_ts=batch.klines[0].timestamp,
        expected_end_ts=batch.klines[-1].timestamp,
    )
    _log_layer_io(io_logs, "data_quality", quality_input, quality_report)

    assert quality_report.passed is True
    assert quality_report.checked_count == 30
    assert quality_report.interval_seconds == INTERVAL_SECONDS
    assert quality_report.issues == []

    # Feature: 将 quality_report 传入真实 AdvancedFeaturePipeline，保证质量闸参与计算。
    feature_input = FeaturePipelineInput(
        klines=batch.klines,
        symbol=batch.symbol,
        timeframe=batch.timeframe,
        venue=batch.venue,
        trades=provider.get_trades(batch.symbol),
        orderbook=provider.get_orderbook(batch.symbol),
        quality_report=quality_report,
        snapshot_id="btcusdt-30x5m-feature",
    )
    feature_snapshot = AdvancedFeaturePipeline(feature_config).compute(feature_input)
    _log_layer_io(io_logs, "feature", feature_input, feature_snapshot)

    assert feature_snapshot.symbol == SYMBOL
    assert feature_snapshot.timeframe == TIMEFRAME
    assert feature_snapshot.input_bar_count == 30
    assert feature_snapshot.effective_index == 29
    assert feature_snapshot.timestamp == batch.klines[-1].timestamp
    assert feature_snapshot.source_window_start == batch.klines[0].timestamp
    assert feature_snapshot.source_window_end == batch.klines[-1].timestamp
    assert feature_snapshot.is_complete_bar is True
    assert feature_snapshot.values["close"] == 120.0
    assert feature_snapshot.values["fast_ma"] == pytest.approx((93.0 + 92.0 + 120.0) / 3)
    assert feature_snapshot.values["slow_ma"] == pytest.approx(
        (95.0 + 94.0 + 93.0 + 92.0 + 120.0) / 5
    )
    assert feature_snapshot.values["rsi"] is not None
    assert feature_snapshot.values["atr"] is not None
    assert feature_snapshot.values["orderflow.buy_volume"] == 12.0
    assert feature_snapshot.values["orderflow.sell_volume"] == 4.0
    assert feature_snapshot.values["orderflow.large_imbalance"] == 12.0
    assert feature_snapshot.values["orderflow.orderbook_imbalance"] == pytest.approx(0.5)
    assert feature_snapshot.values["market_structure.breakout_direction"] == "up"

    # Regime: 使用真实 RuleBasedRegimeDetector 消费 FeatureSnapshot + QualityReport。
    regime_input = {"feature_snapshot": feature_snapshot, "quality_report": quality_report}
    regime = RuleBasedRegimeDetector(
        trend_threshold=0.01,
        volatility_threshold=0.03,
    ).detect(feature_snapshot, quality_report=quality_report)
    _log_layer_io(io_logs, "regime", regime_input, regime)

    assert regime.symbol == SYMBOL
    assert regime.timeframe == TIMEFRAME
    assert regime.timestamp == feature_snapshot.timestamp
    assert regime.regime == RegimeKind.UPTREND_LOW_VOL
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.TREND
    assert regime.direction == "bullish"
    # AdvancedFeaturePipeline 当前输出 atr/close，但 RuleBasedRegimeDetector 只直接读取 atr_pct/volatility，
    # 因此波动率状态保持 unknown；这条断言锁定真实代码链路的当前合约。
    assert regime.volatility_state == "unknown"
    assert regime.tradability == "observe_only"
    assert regime.input_refs["feature_snapshot_id"] == feature_snapshot.snapshot_id
    assert regime.input_refs["quality_report"]["passed"] is True
    assert regime.input_refs["quality_report"]["checked_count"] == 30
    assert regime.metrics["trend_score"] > 0.01
    assert regime.scores["trend"] == 1.0
    assert "trend_threshold_exceeded" in regime.reason_codes

    # Strategy Route: 使用真实 RegimeStrategyRouter，从细分 regime 回落到 legacy trend 路由。
    router = RegimeStrategyRouter(
        {RegimeKind.TREND: MACrossoverStrategy(strategy_version="1.0.0")},
        router_id="btc-5m-router",
    )
    routed = router.route(regime)
    _log_layer_io(io_logs, "strategy_route", regime, {"route": routed.route, "decision": routed.decision})

    assert routed.route.symbol == SYMBOL
    assert routed.route.timeframe == TIMEFRAME
    assert routed.route.strategy_id == "ma_crossover"
    assert routed.decision["legacy_route_used"] is True
    assert routed.decision["resolved_regime"] == RegimeKind.TREND.value

    # Strategy Signal: 使用真实策略信号层消费来自 FeaturePipeline 的 fast/slow MA 序列。
    feature_series = _feature_series_from_pipeline(batch, quality_report, feature_config)
    signal = routed.strategy.generate_signal(feature_series, index=29)
    assert signal is not None
    signal = StrategySignal.from_payload(
        {
            **signal.to_payload(),
            "symbol": batch.symbol,
            "timeframe": batch.timeframe,
            "trace": routed.route.trace.to_payload() if routed.route.trace is not None else None,
        }
    )
    _log_layer_io(
        io_logs,
        "strategy_signal",
        {"route": routed.route, "feature_series": feature_series, "index": 29},
        signal,
    )

    assert signal.symbol == SYMBOL
    assert signal.timeframe == TIMEFRAME
    assert signal.side == TradeSide.BUY
    assert signal.is_orderable is True
    assert signal.reason_codes == ["ma_cross"]

    assert [entry["layer"] for entry in io_logs] == [
        "data",
        "data_quality",
        "feature",
        "regime",
        "strategy_route",
        "strategy_signal",
    ]
    assert all("input" in entry and "output" in entry for entry in io_logs)
    assert all(f"PIPELINE_IO {entry['layer']}" in caplog.text for entry in io_logs)
    print("PIPELINE_IO_LOGS=\n" + json.dumps(io_logs, ensure_ascii=False, indent=2, sort_keys=True))


def _feature_series_from_pipeline(batch: KlineBatch, quality_report, config: FeaturePipelineConfig):
    pipeline = AdvancedFeaturePipeline(config)
    snapshots = [
        pipeline.compute(
            FeaturePipelineInput(
                klines=batch.klines,
                index=index,
                symbol=batch.symbol,
                timeframe=batch.timeframe,
                venue=batch.venue,
                quality_report=quality_report,
                snapshot_id=f"{batch.symbol}-{batch.timeframe}-{index}-features",
            )
        )
        for index in range(len(batch.klines))
    ]
    return {
        "fast_ma": [snapshot.values["fast_ma"] for snapshot in snapshots],
        "slow_ma": [snapshot.values["slow_ma"] for snapshot in snapshots],
    }


def _log_layer_io(logs: list[dict], layer: str, input_payload, output_payload) -> None:
    entry = {
        "layer": layer,
        "input": _to_log_payload(input_payload),
        "output": _to_log_payload(output_payload),
    }
    logs.append(entry)
    LOGGER.info(
        "PIPELINE_IO %s\n%s",
        layer,
        json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True),
    )


def _to_log_payload(value):
    if hasattr(value, "to_payload"):
        return value.to_payload()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return _to_log_payload(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_log_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_log_payload(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value
