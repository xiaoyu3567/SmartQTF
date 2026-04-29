import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.data.schemas.market import Kline
from quant.logging.jsonl import JsonlTradeLogger
from quant.orchestration import TradingRuntimeOrchestrator
from quant.schemas import PayloadSource, PipelineRuntimeRequest, PipelineStageStatus


EXPECTED_COMPLETE_STAGES = [
    "data",
    "data_quality",
    "feature",
    "regime",
    "strategy",
    "decision",
    "risk",
    "portfolio",
    "execution",
    "logging",
]


class EndToEndCrossingProvider:
    def get_klines(self, symbol, timeframe):
        closes = [10.0, 9.0, 8.0, 7.0, 12.0]
        return [
            Kline(
                timestamp=1700000000 + index * 60,
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1000.0 + index,
            )
            for index, close in enumerate(closes)
        ]

    def get_trades(self, symbol):
        return []


def test_backtest_and_paper_runtime_run_complete_pipeline_to_logging(tmp_path):
    trade_log = JsonlTradeLogger(tmp_path / "trades.jsonl")
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        backtest_provider=EndToEndCrossingProvider(),
        paper_provider=EndToEndCrossingProvider(),
        feature_windows=(2, 3),
        logger=trade_log,
    )

    backtest_report = runtime.run(
        PipelineRuntimeRequest(
            source=PayloadSource.BACKTEST,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id="e2e-backtest",
        )
    )
    paper_report = runtime.run(
        PipelineRuntimeRequest(
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id="e2e-paper",
        )
    )

    _assert_complete_pipeline_report(backtest_report, PayloadSource.BACKTEST)
    _assert_complete_pipeline_report(paper_report, PayloadSource.PAPER)

    records = trade_log.read_all()
    assert [record.run_id for record in records] == [
        "e2e-backtest",
        "e2e-backtest",
        "e2e-backtest",
        "e2e-paper",
        "e2e-paper",
        "e2e-paper",
    ]
    assert [record.record_type for record in records] == [
        "decision",
        "order",
        "fill",
        "decision",
        "order",
        "fill",
    ]


def _assert_complete_pipeline_report(report, source):
    assert report.success is True
    assert report.context.source == source
    assert report.context.symbol == "BTCUSDT"
    assert report.context.timeframe == "1m"
    assert [stage.stage for stage in report.stages] == EXPECTED_COMPLETE_STAGES
    assert all(stage.status == PipelineStageStatus.SUCCEEDED for stage in report.stages)
    assert report.errors == []
    assert report.metadata["runtime_health"]["status"] == "healthy"

    stages = {stage.stage: stage for stage in report.stages}
    assert stages["data"].output_payload["selected_bar"]["close"] == 12.0
    assert stages["data_quality"].output_payload["quality_report"]["passed"] is True
    assert stages["feature"].output_payload["snapshot"]["values"]["fast_ma"] == 9.5
    assert stages["feature"].output_payload["snapshot"]["values"]["slow_ma"] == 9.0
    assert stages["regime"].output_payload["regime"]["symbol"] == "BTCUSDT"
    assert stages["strategy"].output_payload["signal"]["side"] == "buy"
    assert stages["decision"].output_payload["decision"]["symbol"] == "BTCUSDT"
    assert stages["risk"].output_payload["risk_decision"]["approved"] is True
    assert stages["portfolio"].output_payload["allocation_decision"]["approved"] is True
    assert stages["execution"].output_payload["execution_result"]["status"] == "filled"
    assert stages["logging"].output_payload["records_written"] == 3
    assert report.final_output["execution_result"]["client_order_id"].startswith(
        f"{report.context.run_id}:BTCUSDT:4:"
    )
