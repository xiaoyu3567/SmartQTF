# Pipeline flow tests
# Auto-merged during repository simplification.



# --- merged from test_pipeline_daily_review_optimization.py ---
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.analytics import DailyReviewReporter
from quant.optimization import DailyReviewOptimizationPlanner, StrategyValidationArtifactStore, StrategyVersionGate
from quant.schemas import (
    AssetClass,
    DecisionAction,
    DecisionIntent,
    DecisionLogRecord,
    FillLogRecord,
    OrderKind,
    PayloadSource,
    StrategyPromotionAction,
    StrategyValidationArtifact,
    StrategyValidationMetrics,
    StrategyValidationSlice,
    StrategyValidationSliceKind,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def make_trade_records():
    trace = TraceContext(
        run_id="daily-review-optimization-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000000,
        bar_index=40,
    )
    decision = DecisionIntent(
        decision_id="decision-001",
        timestamp=1710000000,
        symbol=trace.symbol,
        asset_class=AssetClass.CRYPTO,
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        regime="trend",
        action=DecisionAction.OPEN_LONG,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        time_in_force=TimeInForce.GTC,
        confidence=0.78,
        reason_codes=["ma_cross_up"],
        trace=trace,
    )
    return [
        DecisionLogRecord(
            event_id="event-decision-001",
            run_id=trace.run_id,
            timestamp=1710000000,
            trace=trace,
            decision=decision,
        ),
        FillLogRecord(
            event_id="event-fill-001",
            run_id=trace.run_id,
            timestamp=1710000060,
            trace=trace,
            fill_id="fill-001",
            order_id="order-001",
            client_order_id="client-001",
            symbol=trace.symbol,
            side=TradeSide.BUY,
            filled_quantity=1.0,
            fill_price=100.0,
            commission=0.1,
            decision_id=decision.decision_id,
            metadata={"realized_pnl": 10.0},
        ),
        FillLogRecord(
            event_id="event-fill-002",
            run_id=trace.run_id,
            timestamp=1710000120,
            trace=trace,
            fill_id="fill-002",
            order_id="order-002",
            client_order_id="client-002",
            symbol=trace.symbol,
            side=TradeSide.BUY,
            filled_quantity=1.0,
            fill_price=102.0,
            commission=0.1,
            decision_id=decision.decision_id,
            metadata={"realized_pnl": 7.0},
        ),
    ]


def make_validation_artifact(report, candidate):
    return StrategyValidationArtifact(
        artifact_id="artifact-001",
        source_report_id=report.report_id,
        strategy_id=candidate.strategy_id,
        candidate_version=candidate.version,
        symbol="BTCUSDT",
        generated_at=report.generated_at + 120,
        metrics=StrategyValidationMetrics(
            report_id="oos-wf-mc-001",
            generated_at=report.generated_at + 120,
            trade_count=30,
            total_net_pnl=120.0,
            max_drawdown=1.0,
            win_rate=0.62,
            sharpe_ratio=1.4,
            validation_slices=[
                StrategyValidationSlice(
                    name="oos-2024-q1",
                    kind=StrategyValidationSliceKind.OUT_OF_SAMPLE,
                    trade_count=10,
                    total_net_pnl=32.0,
                    max_drawdown=0.4,
                    win_rate=0.6,
                    sharpe_ratio=1.1,
                ),
                StrategyValidationSlice(
                    name="walk-forward-001",
                    kind=StrategyValidationSliceKind.WALK_FORWARD,
                    trade_count=10,
                    total_net_pnl=30.0,
                    max_drawdown=0.5,
                    win_rate=0.61,
                    sharpe_ratio=1.0,
                ),
            ],
            monte_carlo_survival_rate=0.91,
        ),
    )


def test_daily_review_optimization_uses_real_validation_artifact_before_approval(tmp_path):
    report = DailyReviewReporter().build_report(
        make_trade_records(),
        report_id="daily-artifact-001",
        trading_date="2024-03-09",
    )
    planner = DailyReviewOptimizationPlanner(
        gate=StrategyVersionGate(
            min_trades=2,
            min_net_pnl=5.0,
            require_out_of_sample=True,
            min_walk_forward_windows=1,
            min_walk_forward_pass_rate=0.5,
            min_monte_carlo_survival_rate=0.8,
        )
    )
    rejected_without_artifact = planner.enqueue_from_report(
        report,
        tmp_path / "queue-rejected",
    )[0]
    artifact_store = StrategyValidationArtifactStore(tmp_path / "validation-artifacts")
    artifact_store.write_artifact(make_validation_artifact(report, rejected_without_artifact.candidate))

    approved = planner.enqueue_from_report(
        report,
        tmp_path / "queue-approved",
        validation_artifact_store=artifact_store,
    )[0]

    assert rejected_without_artifact.promotion_decision.action == StrategyPromotionAction.REJECT
    assert "missing_out_of_sample_validation" in rejected_without_artifact.promotion_decision.reason_codes
    assert approved.validation_metrics.report_id == "oos-wf-mc-001"
    assert approved.promotion_decision.action == StrategyPromotionAction.APPROVE
    assert approved.promotion_decision.reason_codes == ["promotion_gate_passed"]


# --- merged from test_pipeline_data_quality_feature.py ---
import pytest

from quant.data.quality import DataQualityIssueCode, KlineQualityReport, validate_klines
from quant.data.schemas.market import Kline, KlineBatch, Trade
from quant.data.universe import build_universe_snapshot
from quant.features.pipeline import AdvancedFeaturePipeline, FeaturePipelineConfig, FeaturePipelineInput
from quant.schemas.feature import (
    FeatureSnapshot,
    FundingRateSnapshot,
    NetflowSnapshot,
    OpenInterestSnapshot,
    OrderBookLevel,
    OrderBookSnapshot,
)
from quant.schemas.universe import UniverseFilterConfig, UniverseInstrument, UniverseSnapshot


SYMBOL = "BTC-USDT-SWAP"
TIMEFRAME = "1m"
VENUE = "okx"
START_TS = 1700000000


def _kline(timestamp: int, close: float) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1000.0,
    )


def _contract_klines() -> list[Kline]:
    return [
        _kline(START_TS, 100.0),
        _kline(START_TS + 60, 101.0),
        _kline(START_TS + 120, 102.0),
        _kline(START_TS + 180, 103.0),
    ]


class ContractMarketDataProvider:
    def get_kline_batch(self, symbol: str, timeframe: str) -> KlineBatch:
        return KlineBatch(symbol=symbol, timeframe=timeframe, venue=VENUE, klines=_contract_klines())

    def get_trades(self, symbol: str) -> list[Trade]:
        return [
            Trade(timestamp=START_TS + 120, price=102.1, size=2.0, side="buy"),
            Trade(timestamp=START_TS + 180, price=103.0, size=100.0, side="sell"),
        ]

    def get_open_interest(self, symbol: str) -> OpenInterestSnapshot:
        return OpenInterestSnapshot(
            snapshot_id="oi-1",
            timestamp=START_TS + 120,
            symbol=symbol,
            venue=VENUE,
            as_of_timestamp=START_TS + 120,
            open_interest=123.0,
            open_interest_value=123000.0,
        )

    def get_funding_rate(self, symbol: str) -> FundingRateSnapshot:
        return FundingRateSnapshot(
            snapshot_id="funding-1",
            timestamp=START_TS + 120,
            symbol=symbol,
            venue=VENUE,
            as_of_timestamp=START_TS + 120,
            funding_rate=0.0001,
            funding_timestamp=START_TS + 120,
            next_funding_timestamp=START_TS + 8 * 60 * 60,
        )

    def get_orderbook(self, symbol: str) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            snapshot_id="book-1",
            timestamp=START_TS + 120,
            symbol=symbol,
            venue=VENUE,
            as_of_timestamp=START_TS + 120,
            bids=[OrderBookLevel(price=101.9, quantity=4.0)],
            asks=[OrderBookLevel(price=102.2, quantity=1.0)],
            depth=1,
        )

    def get_netflow(self, symbol: str, timeframe: str) -> NetflowSnapshot:
        return NetflowSnapshot(
            snapshot_id="netflow-1",
            timestamp=START_TS + 120,
            symbol=symbol,
            venue=VENUE,
            as_of_timestamp=START_TS + 120,
            timeframe=timeframe,
            inflow=30.0,
            outflow=12.0,
            netflow=18.0,
        )


def test_data_quality_feature_contract_builds_replayable_snapshot():
    provider = ContractMarketDataProvider()
    batch = provider.get_kline_batch(SYMBOL, TIMEFRAME)

    quality_report = validate_klines(
        klines=batch.klines,
        symbol=batch.symbol,
        timeframe=batch.timeframe,
    )
    assert quality_report.passed
    assert quality_report.to_payload()["passed"] is True

    selected_index = 2
    selected_bar = batch.klines[selected_index]
    funding_rate = provider.get_funding_rate(batch.symbol)
    open_interest = provider.get_open_interest(batch.symbol)
    netflow = provider.get_netflow(batch.symbol, batch.timeframe)
    snapshot = AdvancedFeaturePipeline(
        FeaturePipelineConfig(
            fast_ma_window=2,
            slow_ma_window=3,
            market_structure_lookback=2,
            large_trade_threshold=1.0,
            orderbook_depth=1,
        )
    ).compute(
        FeaturePipelineInput(
            klines=batch.klines,
            index=selected_index,
            symbol=batch.symbol,
            timeframe=batch.timeframe,
            venue=batch.venue,
            trades=provider.get_trades(batch.symbol),
            orderbook=provider.get_orderbook(batch.symbol),
            spot_klines=[_kline(kline.timestamp, kline.close - 0.5) for kline in batch.klines],
            perpetual_klines=batch.klines,
            spot_symbol="BTC-USDT",
            perpetual_symbol=batch.symbol,
            funding_rate=funding_rate,
            snapshot_id="contract-feature-1",
        )
    )

    assert isinstance(snapshot, FeatureSnapshot)
    assert snapshot.symbol == batch.symbol
    assert snapshot.timeframe == batch.timeframe
    assert snapshot.timestamp == selected_bar.timestamp
    assert snapshot.as_of_timestamp == selected_bar.timestamp
    assert snapshot.source_window_start == batch.klines[0].timestamp
    assert snapshot.source_window_end == selected_bar.timestamp
    assert snapshot.values["close"] == selected_bar.close
    assert snapshot.values["orderflow.buy_volume"] == 2.0
    assert snapshot.values["orderflow.sell_volume"] == 0.0
    assert snapshot.values["orderflow.orderbook_imbalance"] == pytest.approx(0.6)
    assert snapshot.values["cross_market.funding_rate"] == funding_rate.funding_rate
    assert open_interest.as_of_timestamp <= snapshot.as_of_timestamp
    assert netflow.netflow == pytest.approx(netflow.inflow - netflow.outflow)

    restored = FeatureSnapshot.from_payload(snapshot.to_payload())
    assert restored.snapshot_id == snapshot.snapshot_id
    assert restored.values == snapshot.values


def test_quality_failure_blocks_quality_to_feature_contract():
    klines = [_kline(START_TS, 100.0), _kline(START_TS + 120, 102.0)]

    quality_report = validate_klines(klines=klines, symbol=SYMBOL, timeframe=TIMEFRAME)

    assert isinstance(quality_report, KlineQualityReport)
    assert not quality_report.passed
    assert quality_report.to_payload()["passed"] is False
    assert [(issue.code, issue.timestamp) for issue in quality_report.issues] == [
        (DataQualityIssueCode.MISSING_KLINE, START_TS + 60)
    ]
    assert not _quality_allows_feature(quality_report)


def test_universe_snapshot_filtering_is_replayable_and_stably_sorted():
    instruments = [
        _instrument("ETH-USDT", base_currency="ETH", turnover_24h=25_000.0),
        _instrument("BTC-USDT", base_currency="BTC", turnover_24h=100_000.0),
        _instrument("ETH-BTC", base_currency="ETH", quote_currency="BTC", turnover_24h=80_000.0),
        _instrument("DOGE-USDT", base_currency="DOGE", status="suspend", turnover_24h=50_000.0),
        _instrument("TINY-USDT", base_currency="TINY", min_notional=50.0, turnover_24h=50_000.0),
        _instrument("ILLQ-USDT", base_currency="ILLQ", turnover_24h=5.0),
        _instrument("BAD-USDT", base_currency="BAD", turnover_24h=50_000.0),
    ]
    config = UniverseFilterConfig(
        venue=VENUE,
        instrument_type="SPOT",
        quote_currencies=["USDT"],
        blacklist=["BAD-USDT"],
        min_turnover_24h=1_000.0,
        max_min_notional=20.0,
    )

    first_snapshot = build_universe_snapshot(instruments, config, as_of_timestamp=START_TS)
    second_snapshot = build_universe_snapshot(list(reversed(instruments)), config, as_of_timestamp=START_TS)

    assert [item.symbol for item in first_snapshot.instruments] == ["BTC-USDT", "ETH-USDT"]
    assert [item.symbol for item in second_snapshot.instruments] == ["BTC-USDT", "ETH-USDT"]
    rejection_codes = {(item.symbol, item.reason_code) for item in first_snapshot.rejected}
    assert ("ETH-BTC", "quote_currency_not_allowed") in rejection_codes
    assert ("DOGE-USDT", "status_not_allowed") in rejection_codes
    assert ("TINY-USDT", "min_notional_too_large") in rejection_codes
    assert ("ILLQ-USDT", "turnover_below_minimum") in rejection_codes
    assert ("BAD-USDT", "blacklisted_symbol") in rejection_codes

    restored = UniverseSnapshot.from_payload(first_snapshot.to_payload())
    assert restored.snapshot_id == "okx-spot-universe-1700000000"
    assert [item.symbol for item in restored.instruments] == ["BTC-USDT", "ETH-USDT"]


def _quality_allows_feature(report: KlineQualityReport) -> bool:
    return report.passed


def _instrument(symbol: str, **overrides) -> UniverseInstrument:
    payload = {
        "symbol": symbol,
        "venue": VENUE,
        "instrument_type": "SPOT",
        "base_currency": symbol.split("-")[0],
        "quote_currency": "USDT",
        "status": "live",
        "quantity_step": 0.0001,
        "min_quantity": 0.0001,
        "price_tick": 0.01,
        "min_notional": 10.0,
        "volume_24h": 1_000.0,
        "turnover_24h": 10_000.0,
        "last_price": 100.0,
    }
    payload.update(overrides)
    return UniverseInstrument(**payload)


# --- merged from test_pipeline_execution_logging_analytics.py ---
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.analytics import DailyReviewReporter, TradeAttributionAnalyzer
from quant.logging import JsonlTradeLogger
from quant.schemas import (
    AssetClass,
    DecisionAction,
    DecisionIntent,
    DecisionLogRecord,
    FillLogRecord,
    OrderKind,
    OrderLogRecord,
    OrderStatus,
    PayloadSource,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def make_decision(decision_id, reason_codes):
    trace = TraceContext(
        run_id="execution-log-pipeline-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000000,
        bar_index=30,
    )
    return DecisionIntent(
        decision_id=decision_id,
        timestamp=1710000000,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        regime="trend",
        action=DecisionAction.OPEN_LONG,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        time_in_force=TimeInForce.GTC,
        confidence=0.7,
        reason_codes=reason_codes,
        trace=trace,
    )


def bucket(report, bucket_type, bucket_value):
    matches = [
        item
        for item in report.buckets
        if item.bucket_type == bucket_type and item.bucket_value == bucket_value
    ]
    assert len(matches) == 1
    return matches[0]


def test_execution_order_and_fill_logs_feed_attribution_and_daily_review(tmp_path):
    logger = JsonlTradeLogger(tmp_path / "execution-events.jsonl")
    filled_decision = make_decision("decision-filled", ["ma_cross_up"])
    rejected_decision = make_decision("decision-rejected", ["risk_rejected"])
    trace = filled_decision.trace
    for record in [
        DecisionLogRecord(
            event_id="event-decision-filled",
            run_id=trace.run_id,
            timestamp=1710000000,
            trace=trace,
            decision=filled_decision,
        ),
        DecisionLogRecord(
            event_id="event-decision-rejected",
            run_id=trace.run_id,
            timestamp=1710000001,
            trace=trace,
            decision=rejected_decision,
        ),
        OrderLogRecord(
            event_id="event-order-accepted",
            run_id=trace.run_id,
            timestamp=1710000002,
            trace=trace,
            order_id="order-filled",
            client_order_id="client-filled",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            status=OrderStatus.FILLED,
            quantity=1.0,
            filled_quantity=1.0,
            price=100.0,
            decision_id=filled_decision.decision_id,
        ),
        FillLogRecord(
            event_id="event-fill",
            run_id=trace.run_id,
            timestamp=1710000003,
            trace=trace,
            fill_id="fill-001",
            order_id="order-filled",
            client_order_id="client-filled",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            filled_quantity=1.0,
            fill_price=101.0,
            commission=0.1,
            decision_id=filled_decision.decision_id,
            metadata={"realized_pnl": 4.0},
        ),
        OrderLogRecord(
            event_id="event-order-rejected",
            run_id=trace.run_id,
            timestamp=1710000004,
            trace=trace,
            order_id="order-rejected",
            client_order_id="client-rejected",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            status=OrderStatus.REJECTED,
            quantity=1.0,
            remaining_quantity=1.0,
            decision_id=rejected_decision.decision_id,
            metadata={"error": "risk rejected"},
        ),
    ]:
        logger.append(record)

    restored = logger.read_all()
    attribution = TradeAttributionAnalyzer().build_report(
        restored,
        report_id="attribution-001",
    )
    daily = DailyReviewReporter().build_report(
        restored,
        report_id="daily-001",
        trading_date="2024-03-09",
    )

    assert attribution.total_net_pnl == 4.0
    assert bucket(attribution, "rule", "ma_cross_up").net_pnl == 4.0
    assert daily.fill_count == 1
    assert daily.rejection_count == 1
    assert bucket(daily, "reason", "risk_rejected").rejection_count == 1


# --- merged from test_pipeline_feature_regime_strategy.py ---
from quant.regime import RuleBasedRegimeDetector
from quant.schemas import FeatureSnapshot, PayloadSource, RegimeKind, TraceContext, TradeSide
from quant.strategy.ma_crossover import MACrossoverStrategy
from quant.strategy.router import RegimeStrategyRouter


def test_feature_snapshot_flows_through_regime_router_and_strategy_signal():
    trace = TraceContext(
        run_id="pipeline-feature-regime-strategy-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000120,
        bar_index=2,
    )
    feature_series = {
        "fast_ma": [99.0, 99.5, 101.0],
        "slow_ma": [100.0, 100.0, 100.0],
        "trend_strength": [0.0, 0.0, 0.03],
        "volatility": [0.01, 0.01, 0.01],
    }
    snapshot = FeatureSnapshot.from_feature_series(
        feature_series,
        2,
        snapshot_id="features-pipeline-001",
        timestamp=1710000120,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000120,
        feature_set_id="technical",
        feature_set_version="1.0.0",
        source_window_start=1710000000,
        source_window_end=1710000120,
        trace=trace,
    )

    regime = RuleBasedRegimeDetector(trend_threshold=0.01, volatility_threshold=0.05).detect(
        snapshot
    )
    routed = RegimeStrategyRouter(
        {RegimeKind.TREND: MACrossoverStrategy(strategy_version="1.0.0")}
    ).route(regime)
    signal = routed.strategy.generate_signal(feature_series, index=2)

    assert regime.regime == _value(RegimeKind.UPTREND_LOW_VOL)
    assert RegimeKind(regime.regime).legacy_kind() == RegimeKind.TREND
    assert routed.decision["legacy_route_used"] is True
    assert routed.decision["resolved_regime"] == _value(RegimeKind.TREND)
    assert regime.trace == trace
    assert routed.route.strategy_id == "ma_crossover"
    assert routed.route.trace == trace
    assert signal.side == _value(TradeSide.BUY)
    assert signal.reason_codes == ["ma_cross"]
    assert signal.strategy_id == routed.route.strategy_id


def _value(value):
    return getattr(value, "value", value)


# --- merged from test_pipeline_live_dry_run_safety.py ---
import json
from pathlib import Path

from quant.config import BrokerConfig, MarketConfig, RuntimeConfig, StrategyBinding
from quant.data.schemas.market import Kline
from quant.orchestration import TradingRuntimeOrchestrator
from quant.registry import PluginKind, PluginRegistry
from quant.schemas import (
    PayloadSource,
    PipelineRunReport,
    PipelineRuntimeRequest,
    PipelineStageStatus,
    RuntimeHealthSnapshot,
)


EXPECTED_RUNTIME_STAGES = [
    "data",
    "data_quality",
    "feature",
    "regime",
    "strategy",
    "decision",
    "portfolio",
    "risk",
    "execution",
    "logging",
]


class CrossingProvider:
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


def test_backtest_paper_and_live_dry_run_share_pipeline_report_contract(tmp_path):
    reports = {
        PayloadSource.BACKTEST: _run_simulation_report(
            PayloadSource.BACKTEST,
            "hqa015-backtest",
            tmp_path / "backtest-reports",
        ),
        PayloadSource.PAPER: _run_simulation_report(
            PayloadSource.PAPER,
            "hqa015-paper",
            tmp_path / "paper-reports",
        ),
        PayloadSource.LIVE: _run_live_dry_run_report(
            "hqa015-live-dry-run",
            tmp_path / "live-dry-run-reports",
        ),
    }

    signatures = {
        source: _report_signature(report)
        for source, report in reports.items()
    }

    assert signatures[PayloadSource.BACKTEST] == signatures[PayloadSource.PAPER]
    assert signatures[PayloadSource.LIVE] == signatures[PayloadSource.PAPER]

    for source, report in reports.items():
        _assert_report_artifact_is_replayable(report, source)
        _assert_runtime_health_matches_report(report)
        _assert_all_stage_boundaries_are_replayable(report)

    live_execution = _stage(reports[PayloadSource.LIVE], "execution")
    live_result = live_execution.output_payload["execution_result"]
    assert live_result["dry_run"] is True
    assert live_result["live_orders_sent"] is False
    assert live_result["status"] == "accepted"
    assert live_result["filled_qty"] == 0.0
    assert live_result["remaining_qty"] > 0.0


def test_live_dry_run_does_not_create_or_call_real_broker(tmp_path):
    created = []
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "live_crossing", lambda: CrossingProvider())
    registry.register(
        PluginKind.EXECUTION,
        "real_live_broker",
        lambda **_: _record(created, "broker_created", ExplodingBroker()),
    )
    config = _live_dry_run_config(tmp_path / "live-dry-run-reports")

    runtime = TradingRuntimeOrchestrator.from_config_dry_run(config, registry=registry)
    report = runtime.run(
        {
            "source": "live",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "index": 4,
            "run_id": "hqa015-live-no-broker",
        }
    )

    assert report.success is True
    assert "broker_created" not in created
    assert runtime.dry_run_execution_handler.requests == [
        {
            "client_order_id": report.final_output["execution_result"]["client_order_id"],
            "symbol": "BTCUSDT",
            "side": "buy",
            "quantity": report.final_output["execution_result"]["remaining_qty"],
            "index": 4,
        }
    ]
    assert report.final_output["execution_result"]["live_orders_sent"] is False


def _run_simulation_report(source, run_id, report_dir):
    runtime = TradingRuntimeOrchestrator.with_default_simulation(
        backtest_provider=CrossingProvider(),
        paper_provider=CrossingProvider(),
        feature_windows=(2, 3),
        pipeline_report_dir=report_dir,
    )
    return runtime.run(
        PipelineRuntimeRequest(
            source=source,
            symbol="BTCUSDT",
            timeframe="1m",
            index=4,
            run_id=run_id,
        )
    )


def _run_live_dry_run_report(run_id, report_dir):
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "live_crossing", lambda: CrossingProvider())
    registry.register(PluginKind.EXECUTION, "real_live_broker", lambda **_: ExplodingBroker())
    runtime = TradingRuntimeOrchestrator.from_config_dry_run(
        _live_dry_run_config(report_dir),
        registry=registry,
    )
    return runtime.run(
        {
            "source": "live",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "index": 4,
            "run_id": run_id,
        }
    )


def _live_dry_run_config(report_dir):
    return RuntimeConfig(
        name="hqa015-live-dry-run",
        source=PayloadSource.LIVE,
        markets=[MarketConfig(symbol="BTCUSDT", timeframe="1m", provider="live_crossing")],
        strategies=[
            StrategyBinding(
                symbol="BTCUSDT",
                strategy="ma_crossover",
                route="default",
                parameters={"fast_window": 2, "slow_window": 3},
            )
        ],
        broker=BrokerConfig(
            mode=PayloadSource.LIVE,
            account_id="live-account",
            broker_plugin="real_live_broker",
            settings={"allow_live_orders": False},
        ),
        logging={"pipeline_report_dir": str(report_dir)},
    )


def _report_signature(report):
    assert isinstance(report, PipelineRunReport)
    assert report.success is True
    assert [stage.stage for stage in report.stages] == EXPECTED_RUNTIME_STAGES
    assert all(stage.status == PipelineStageStatus.SUCCEEDED for stage in report.stages)
    return [
        {
            "stage": stage.stage,
            "status": _status_value(stage.status),
            "has_input": bool(stage.input_payload),
            "has_output": bool(stage.output_payload),
            "has_rejection": stage.rejection is not None,
            "has_error": stage.error is not None,
            "has_skip_reason": stage.skip_reason is not None,
            "metadata_keys": sorted(stage.metadata),
        }
        for stage in report.stages
    ]


def _assert_report_artifact_is_replayable(report, source):
    artifact = report.metadata["pipeline_report_artifact"]
    report_path = Path(artifact["report_path"])
    latest_path = Path(artifact["latest_report_path"])
    restored = PipelineRunReport.from_payload(json.loads(report_path.read_text(encoding="utf-8")))
    latest = PipelineRunReport.from_payload(json.loads(latest_path.read_text(encoding="utf-8")))

    assert artifact["type"] == "run"
    assert artifact["format"] == "json"
    assert restored.context.source == source
    assert restored.context.run_id == report.context.run_id
    assert latest.context.run_id == report.context.run_id
    assert [stage.stage for stage in restored.stages] == EXPECTED_RUNTIME_STAGES


def _assert_runtime_health_matches_report(report):
    health = RuntimeHealthSnapshot.from_payload(report.metadata["runtime_health"])

    assert health.run_id == report.context.run_id
    assert health.source == report.context.source
    assert health.symbol == report.context.symbol
    assert health.timeframe == report.context.timeframe
    assert set(health.pipeline_stage_durations_ms) == set(EXPECTED_RUNTIME_STAGES)
    assert health.metadata["stage_count"] == len(EXPECTED_RUNTIME_STAGES)


def _assert_all_stage_boundaries_are_replayable(report):
    for stage in report.stages:
        assert stage.input_payload, stage.stage
        assert stage.output_payload, stage.stage
        assert stage.rejection is None
        assert stage.error is None
        assert stage.skip_reason is None


def _stage(report, name):
    return {stage.stage: stage for stage in report.stages}[name]


def _record(created, name, value):
    created.append(name)
    return value


class ExplodingBroker:
    def place_order(self, request):
        raise AssertionError("live dry-run must not call a real broker")


def _status_value(status):
    return status.value if hasattr(status, "value") else str(status)


# --- merged from test_pipeline_scan_scheduler_universe_account.py ---
import json
from pathlib import Path

from quant.config import BrokerConfig, MarketConfig, RuntimeConfig, StrategyBinding
from quant.data.schemas.market import Kline
from quant.orchestration import RuntimeScanScheduler
from quant.registry import PluginKind, PluginRegistry
from quant.schemas import (
    AccountPositionSnapshot,
    AccountSyncSnapshot,
    PayloadSource,
    PipelineBatchRunReport,
    PipelineRunReport,
    PositionSide,
    RuntimeHealthSnapshot,
    RuntimeHealthStatus,
    UniverseInstrument,
    UniverseSnapshot,
)


class MixedScanProvider:
    def get_klines(self, symbol, timeframe):
        if symbol == "EMPTY":
            return []
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


class StaticUniverseProvider:
    def __init__(self, symbols):
        self.symbols = list(symbols)
        self.last_filter_config = None

    def discover_universe(self, filter_config):
        self.last_filter_config = filter_config
        return UniverseSnapshot(
            snapshot_id="scan-universe-1710000000",
            venue=filter_config.venue,
            instrument_type=filter_config.instrument_type,
            as_of_timestamp=1710000000,
            source="unit_scan_universe",
            filters=filter_config,
            instruments=[
                UniverseInstrument(
                    symbol=symbol,
                    venue=filter_config.venue,
                    instrument_type=filter_config.instrument_type,
                    base_currency=symbol.replace("USDT", ""),
                    quote_currency="USDT",
                    status="live",
                    quantity_step=0.001,
                    min_quantity=0.001,
                    turnover_24h=1_000_000.0 - index,
                )
                for index, symbol in enumerate(self.symbols)
            ],
        )


class StaticAccountSync:
    def fetch_snapshot(self):
        return AccountSyncSnapshot(
            account_id="account-001",
            source=PayloadSource.LIVE,
            observed_at=1710000600,
            equity=10000.0,
            positions=[
                AccountPositionSnapshot(
                    symbol="SOLUSDT",
                    side=PositionSide.LONG,
                    quantity=3.0,
                    avg_price=100.0,
                )
            ],
        )


def test_scan_scheduler_merges_sources_persists_batch_and_isolates_symbol_failures(tmp_path):
    universe_provider = StaticUniverseProvider(["ETHUSDT", "BTCUSDT"])
    scheduler = RuntimeScanScheduler.from_config(
        _scan_config(tmp_path),
        registry=_registry(),
        account_sync=StaticAccountSync(),
        universe_provider=universe_provider,
    )

    requests = scheduler.build_requests(index=4)

    assert universe_provider.last_filter_config.venue == "okx"
    assert [request.symbol for request in requests] == [
        "BTCUSDT",
        "ETHUSDT",
        "EMPTY",
        "SOLUSDT",
    ]
    assert requests[0].metadata["scan_sources"] == ["candidate", "universe"]
    assert requests[1].metadata["scan_sources"] == ["universe"]
    assert requests[2].metadata["scan_sources"] == ["holding"]
    assert requests[3].metadata["scan_sources"] == ["account_holding"]

    batch = scheduler.run_once(
        requested_at=1700000600,
        index=4,
        batch_id="scan-contract-universe-account",
    )

    assert batch.success is False
    assert batch.metadata["execution_mode"] == "sequential"
    assert batch.metadata["failure_isolation"] == "per_symbol"
    assert batch.errors == ["EMPTY/1m: provider returned no klines"]
    assert [report.context.symbol for report in batch.reports] == [
        "BTCUSDT",
        "ETHUSDT",
        "EMPTY",
        "SOLUSDT",
    ]
    assert [report.success for report in batch.reports] == [True, True, False, True]
    assert batch.requests[0].metadata["scan_sources"] == ["candidate", "universe"]
    assert batch.requests[3].metadata["scan_sources"] == ["account_holding"]

    scan_metadata = batch.metadata["scan_scheduler"]
    assert scan_metadata["candidate_symbols"] == ["BTCUSDT"]
    assert scan_metadata["holding_symbols"] == ["EMPTY"]
    assert scan_metadata["account_holding_symbols"] == ["SOLUSDT"]
    assert scan_metadata["account_sync_observed_at"] == 1710000600
    assert scan_metadata["universe_enabled"] is True
    assert scan_metadata["universe_snapshot_id"] == "scan-universe-1710000000"
    assert scan_metadata["universe_source"] == "unit_scan_universe"
    assert scan_metadata["universe_symbols"] == ["ETHUSDT", "BTCUSDT"]

    failed_report = batch.reports[2]
    failed_stage = failed_report.stages[0]
    failed_health = RuntimeHealthSnapshot.from_payload(failed_report.metadata["runtime_health"])
    assert failed_stage.stage == "orchestration"
    assert failed_stage.error == "provider returned no klines"
    assert failed_health.status == RuntimeHealthStatus.CRITICAL
    assert "pipeline_error" in failed_health.alerts

    batch_artifact = batch.metadata["pipeline_report_artifact"]
    restored_batch = PipelineBatchRunReport.from_payload(_read_json(batch_artifact["report_path"]))
    assert restored_batch.batch_id == "scan-contract-universe-account"
    assert Path(batch_artifact["latest_report_path"]).exists()
    for report in batch.reports:
        artifact = report.metadata["pipeline_report_artifact"]
        restored_report = PipelineRunReport.from_payload(_read_json(artifact["report_path"]))
        assert artifact["type"] == "run"
        assert Path(artifact["latest_report_path"]).exists()
        assert restored_report.context.run_id == report.context.run_id


def _scan_config(tmp_path):
    symbols = ["BTCUSDT", "ETHUSDT", "EMPTY", "SOLUSDT"]
    return RuntimeConfig(
        name="scan-contract",
        source=PayloadSource.PAPER,
        markets=[
            MarketConfig(symbol=symbol, timeframe="1m", provider="mixed_scan")
            for symbol in symbols
        ],
        strategies=[
            StrategyBinding(
                symbol=symbol,
                strategy="ma_crossover",
                route="default",
                parameters={"fast_window": 2, "slow_window": 3},
            )
            for symbol in symbols
        ],
        broker=BrokerConfig(mode=PayloadSource.PAPER, broker_plugin="simulated"),
        scan={
            "enabled": True,
            "interval_seconds": 600,
            "candidate_symbols": ["BTCUSDT"],
            "holding_symbols": ["EMPTY"],
            "universe_enabled": True,
        },
        logging={"pipeline_report_dir": str(tmp_path / "pipeline-runs")},
    )


def _registry():
    registry = PluginRegistry()
    registry.register(PluginKind.DATA, "mixed_scan", lambda: MixedScanProvider())
    return registry


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- merged from test_end_to_end_pipeline.py ---
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
    "portfolio",
    "risk",
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
    assert stages["portfolio"].output_payload["capital_budget"]["approved"] is True
    assert stages["risk"].output_payload["risk_decision"]["approved"] is True
    assert stages["risk"].output_payload["allocation_decision"]["approved"] is True
    assert stages["execution"].output_payload["execution_result"]["status"] == "filled"
    assert stages["logging"].output_payload["records_written"] == 3
    assert report.final_output["execution_result"]["client_order_id"].endswith(":risk-v2:buy")


# --- merged from test_execution_flow.py ---
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from layers.execution.live import LiveExecutionEngine


class FlakyFakeOKXAdapter:
    def __init__(self):
        self.calls = []
        self.submitted_orders = []
        self.failures_left = 1

    def place_order(self, symbol, side, size, type, client_order_id=None, target_currency=None):
        call = {
            "symbol": symbol,
            "side": side,
            "size": size,
            "type": type,
            "client_order_id": client_order_id,
            "target_currency": target_currency,
        }
        self.calls.append(call)
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeError("temporary exchange error")
        self.submitted_orders.append(call)
        return {
            "success": True,
            "exchange": "okx",
            "code": "0",
            "data": [
                {
                    "ordId": "okx-order-1",
                    "clOrdId": client_order_id,
                    "sCode": "0",
                    "state": "partially_filled",
                    "accFillSz": "0.0004",
                }
            ],
        }


def test_execution():
    adapter = FlakyFakeOKXAdapter()
    engine = LiveExecutionEngine(adapter=adapter, max_retries=1, backoff_base=0)

    decision = {
        "symbol": "BTC-USDT",
        "action": "buy",
        "order_type": "market",
        "size": 0.001,
    }

    result = engine.execute(decision)
    duplicate = engine.execute(decision)
    print(result)

    assert len(adapter.submitted_orders) == 1
    assert len(adapter.calls) == 2
    assert adapter.calls[0]["size"] == 0.001
    assert adapter.calls[1]["size"] == 0.001
    assert adapter.calls[1]["target_currency"] == "base_ccy"
    assert result["retry_count"] == 1
    assert result["partial"] is True
    assert result["filled_size"] == 0.0004
    assert duplicate["idempotent_replay"] is True
    assert len(adapter.calls) == 2
