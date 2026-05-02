import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.config import (
    BrokerConfig,
    MarketConfig,
    MultiTimeframeConfig,
    RuntimeConfig,
    StrategyBinding,
)
from quant.data.schemas.market import Kline
from quant.orchestration import PaperTradingOrchestrator, TradingRuntimeOrchestrator
from quant.schemas import PayloadSource, PipelineStageStatus


class FixtureMultiTimeframeProvider:
    def __init__(self, *, conflicting_1h=False):
        self.conflicting_1h = conflicting_1h
        self.calls = []

    def get_klines(self, symbol, timeframe, limit=100):
        self.calls.append((symbol, timeframe, limit))
        closes_by_timeframe = {
            "5m": [100.0, 101.0, 99.0, 98.0, 100.0, 103.0],
            "15m": [80.0, 82.0, 86.0, 94.0, 108.0, 125.0],
            "1h": (
                [130.0, 124.0, 116.0, 104.0, 92.0, 80.0]
                if self.conflicting_1h
                else [82.0, 84.0, 88.0, 96.0, 110.0, 128.0]
            ),
            "4h": [84.0, 86.0, 90.0, 98.0, 112.0, 130.0],
        }
        step_by_timeframe = {
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
        }
        execution_last_ts = 1700001500
        closes = closes_by_timeframe[timeframe]
        step = step_by_timeframe[timeframe]
        start_ts = execution_last_ts - step * (len(closes) - 1)
        return [
            _kline(start_ts + position * step, close)
            for position, close in enumerate(closes)
        ]


class RecordingExecutionEngine:
    def __init__(self):
        self.orders = []
        self.state_machine = None

    def on_order_intent(self, order_intent, price, index, **_):
        self.orders.append(order_intent)
        return {
            "order_id": f"fixture:{order_intent.client_order_id}",
            "client_order_id": order_intent.client_order_id,
            "symbol": order_intent.symbol,
            "side": order_intent.side,
            "status": "filled",
            "filled_qty": order_intent.quantity,
            "remaining_qty": 0.0,
            "fill_price": price,
        }


def test_runtime_config_accepts_explicit_multi_timeframe_opt_in():
    config = _runtime_config()

    payload = config.to_payload()
    restored = RuntimeConfig.from_payload(payload)

    assert payload["multi_timeframe"]["enabled"] is True
    assert restored.multi_timeframe.execution_timeframe == "5m"
    assert restored.multi_timeframe.context_timeframes == ["15m", "1h", "4h"]
    assert restored.multi_timeframe.bar_limits == {"5m": 120, "1h": 80}


def test_multi_timeframe_pipeline_records_layer_inputs_outputs_and_keeps_watch_out_of_execution():
    provider = FixtureMultiTimeframeProvider()
    execution = RecordingExecutionEngine()
    orchestrator = PaperTradingOrchestrator(
        provider=provider,
        feature_windows=(2, 3),
        execution_engine=execution,
        multi_timeframe_config=_multi_timeframe_config(),
    )

    report = orchestrator.run_tick(symbol="BTCUSDT", timeframe="5m", index=5, run_id="mtf-confirmed")

    stages = _stages(report)
    assert report.success is True
    assert report.context.timeframe == "5m"
    assert report.context.metadata["multi_timeframe_enabled"] is True
    assert stages["data"].output_payload["execution_timeframe"] == "5m"
    assert stages["data"].output_payload["context_timeframes"] == ["15m", "1h", "4h"]
    assert stages["data_quality"].output_payload["multi_timeframe_quality_report"]["passed"] is True
    assert "multi_timeframe_feature_snapshot" in stages["feature"].output_payload
    assert stages["regime"].output_payload["multi_timeframe_regime"]["higher_timeframe_bias"] == "bullish"
    assert stages["strategy"].output_payload["filter"]["confirmation_timeframes"] == ["15m", "1h", "4h"]
    assert stages["strategy"].output_payload["signal"]["action"] == "wait"
    assert stages["decision"].output_payload["decision_result"]["decision_action"] == "WATCH"
    assert stages["decision"].output_payload["decision_result"]["forward_to_capital_allocation"] is False
    assert stages["portfolio"].status == PipelineStageStatus.SKIPPED
    assert stages["risk"].status == PipelineStageStatus.SKIPPED
    assert stages["execution"].status == PipelineStageStatus.SKIPPED
    assert not execution.orders
    assert provider.calls == [
        ("BTCUSDT", "5m", 120),
        ("BTCUSDT", "15m", 120),
        ("BTCUSDT", "1h", 120),
        ("BTCUSDT", "4h", 120),
    ]


def test_multi_timeframe_conflict_downgrades_signal_before_risk_and_execution():
    provider = FixtureMultiTimeframeProvider(conflicting_1h=True)
    execution = RecordingExecutionEngine()
    orchestrator = PaperTradingOrchestrator(
        provider=provider,
        feature_windows=(2, 3),
        execution_engine=execution,
        multi_timeframe_config=_multi_timeframe_config(),
    )

    report = orchestrator.run_tick(symbol="BTCUSDT", timeframe="5m", index=5, run_id="mtf-conflict")

    stages = _stages(report)
    signal = stages["strategy"].output_payload["signal"]
    assert signal["action"] == "no_trade"
    assert "signal_blocked_by_higher_timeframe_conflict" in signal["reason_codes"]
    assert stages["decision"].output_payload["decision_result"]["decision_action"] == "WATCH"
    assert stages["portfolio"].status == PipelineStageStatus.SKIPPED
    assert stages["risk"].status == PipelineStageStatus.SKIPPED
    assert stages["execution"].status == PipelineStageStatus.SKIPPED
    assert not execution.orders


def test_default_single_timeframe_pipeline_stays_compatible_when_multi_timeframe_disabled():
    provider = FixtureMultiTimeframeProvider()
    orchestrator = PaperTradingOrchestrator(
        provider=provider,
        feature_windows=(2, 3),
        multi_timeframe_config=MultiTimeframeConfig(enabled=False),
    )

    report = orchestrator.run_tick(symbol="BTCUSDT", timeframe="5m", index=5, run_id="single-tf")

    stages = _stages(report)
    assert report.context.metadata == {}
    assert stages["data"].output_payload["timeframe"] == "5m"
    assert "context_timeframes" not in stages["data"].output_payload
    assert provider.calls == [("BTCUSDT", "5m", 100)]


def test_runtime_orchestrator_from_config_passes_multi_timeframe_config_to_handler():
    config = _runtime_config()

    runtime = TradingRuntimeOrchestrator.from_config(config)

    handler = runtime.handlers[PayloadSource.PAPER]
    assert handler.multi_timeframe_config.enabled is True
    assert handler.multi_timeframe_config.execution_timeframe == "5m"
    assert handler.multi_timeframe_config.context_timeframes == ["15m", "1h", "4h"]


def _runtime_config():
    return RuntimeConfig(
        name="mtf-paper",
        source=PayloadSource.PAPER,
        markets=[MarketConfig(symbol="BTCUSDT", timeframe="5m", provider="mock")],
        strategies=[
            StrategyBinding(
                symbol="BTCUSDT",
                strategy="ma_crossover",
                route="default",
                parameters={"fast_window": 2, "slow_window": 3},
            )
        ],
        broker=BrokerConfig(mode=PayloadSource.PAPER, broker_plugin="simulated"),
        multi_timeframe=_multi_timeframe_config(),
    )


def _multi_timeframe_config():
    return MultiTimeframeConfig(
        enabled=True,
        execution_timeframe="5m",
        context_timeframes=["15m", "1h", "4h"],
        bar_limits={"5m": 120, "1h": 80},
        default_bar_limit=120,
        venue="fixture",
    )


def _stages(report):
    return {stage.stage: stage for stage in report.stages}


def _kline(timestamp, close):
    return Kline(
        timestamp=timestamp,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1000.0,
        is_complete=True,
    )
