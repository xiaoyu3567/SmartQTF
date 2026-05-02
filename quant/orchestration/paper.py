import inspect
from uuid import uuid4

from quant.account.account import CryptoAccount
from quant.account.capital_allocator import CapitalBudgetAllocator
from quant.data.multi_timeframe import MultiTimeframeDataRequest, build_multi_timeframe_kline_batch
from quant.data.quality import validate_klines
from quant.data.quality import validate_multi_timeframe_klines
from quant.data.providers.mock_provider import MockProvider
from quant.decision.engine import DecisionEngine
from quant.features.multi_timeframe import MultiTimeframeFeaturePipeline, MultiTimeframeFeaturePipelineInput
from quant.features.pipeline import FeaturePipelineConfig
from quant.features.indicators.moving_average import MovingAverage
from quant.logging.jsonl import JsonlTradeLogger
from quant.regime.multi_timeframe import MultiTimeframeRegimeDetector
from quant.regime.rule_detector import RuleBasedRegimeDetector
from quant.risk.risk_manager import RiskManager
from quant.execution.lifecycle_contract import attach_order_lifecycle_contract
from quant.schemas import (
    AssetClass,
    CapitalBudgetRequest,
    DecisionAction,
    DecisionEngineRequest,
    DecisionIntent,
    DecisionPolicy,
    DecisionPortfolioState,
    DecisionLogRecord,
    FeatureSnapshot,
    FillLogRecord,
    OrderKind,
    OrderIntent,
    OrderLogRecord,
    OrderStatus,
    PipelineBatchRunReport,
    PayloadSource,
    PipelineRunContext,
    PipelineRunReport,
    PipelineStageResult,
    PipelineStageStatus,
    PipelineSymbolRunRequest,
    PositionSide,
    PortfolioExecutionContext,
    RegimeKind,
    RiskEngineV2Request,
    RiskMarketConstraints,
    RiskPolicy,
    RuntimeHealthSnapshot,
    RuntimeHealthStatus,
    TimeInForce,
    TraceContext,
    TradeSide,
)
from quant.schemas.base import LayerRejection
from quant.schemas.enums import LayerName
from quant.strategy.multi_timeframe import HigherTimeframeConfirmationFilter, MultiTimeframeStrategySignalInput
from quant.strategy.ma_crossover import MACrossoverStrategy
from quant.strategy.router import RegimeStrategyRouter
from quant.execution.engine import ExecutionEngine


class PaperTradingOrchestrator:
    def __init__(
        self,
        *,
        provider=None,
        feature_windows=(3, 5),
        regime_detector=None,
        strategy_router=None,
        risk_manager=None,
        execution_engine=None,
        account=None,
        capital_allocator=None,
        capital_budget_allocator=None,
        decision_engine=None,
        multi_timeframe_config=None,
        multi_timeframe_feature_pipeline=None,
        multi_timeframe_regime_detector=None,
        multi_timeframe_signal_filter=None,
        logger=None,
        logger_factory=None,
        source=PayloadSource.PAPER,
    ):
        self.provider = provider or MockProvider()
        self.feature_windows = feature_windows
        self.regime_detector = regime_detector or RuleBasedRegimeDetector()
        self.strategy_router = strategy_router or RegimeStrategyRouter(
            routes={RegimeKind.TREND: lambda: MACrossoverStrategy()},
            fallback=lambda: MACrossoverStrategy(),
        )
        self.account = account or CryptoAccount(initial_balance=10000.0)
        self.risk_manager = risk_manager or RiskManager()
        self.execution_engine = execution_engine or ExecutionEngine(account=self.account)
        self.capital_allocator = capital_allocator
        self.capital_budget_allocator = capital_budget_allocator or CapitalBudgetAllocator()
        self.decision_engine = decision_engine or DecisionEngine()
        self.multi_timeframe_config = multi_timeframe_config or {}
        self.multi_timeframe_feature_pipeline = (
            multi_timeframe_feature_pipeline
            or MultiTimeframeFeaturePipeline(
                FeaturePipelineConfig(
                    fast_ma_window=feature_windows[0],
                    slow_ma_window=feature_windows[1],
                    rsi_window=feature_windows[1],
                    atr_window=feature_windows[1],
                    market_structure_lookback=feature_windows[1],
                )
            )
        )
        self.multi_timeframe_regime_detector = multi_timeframe_regime_detector or MultiTimeframeRegimeDetector()
        self.multi_timeframe_signal_filter = multi_timeframe_signal_filter or HigherTimeframeConfirmationFilter()
        self.logger = logger
        self.logger_factory = logger_factory
        self.source = source

    def run_symbols(self, requests, batch_id=None, requested_at=0):
        batch_id = batch_id or f"paper-batch-{uuid4().hex}"
        symbol_requests = [self._symbol_request(request) for request in requests]
        reports = []
        errors = []

        for position, request in enumerate(symbol_requests):
            run_id = f"{batch_id}:{position}:{request.symbol}:{request.timeframe}"
            try:
                report = self.run_tick(
                    symbol=request.symbol,
                    timeframe=request.timeframe,
                    index=request.index,
                    run_id=run_id,
                )
            except Exception as exc:
                error = f"{request.symbol}/{request.timeframe}: {exc}"
                errors.append(error)
                report = self._error_report(run_id, request, requested_at, str(exc))
            reports.append(report)

        return PipelineBatchRunReport(
            batch_id=batch_id,
            source=self.source,
            requested_at=requested_at,
            requests=symbol_requests,
            reports=reports,
            success=not errors and all(report.success for report in reports),
            errors=errors,
            metadata={"execution_mode": "sequential", "failure_isolation": "per_symbol"},
        )

    def run_tick(self, symbol="BTCUSDT", timeframe="1m", index=None, run_id=None):
        if self._multi_timeframe_enabled():
            return self._run_multi_timeframe_tick(
                symbol=symbol,
                timeframe=timeframe,
                index=index,
                run_id=run_id,
            )

        run_id = run_id or f"paper-{uuid4().hex}"
        klines = self.provider.get_klines(symbol, timeframe)
        if not klines:
            raise ValueError("provider returned no klines")

        index = len(klines) - 1 if index is None else index
        if index < 0 or index >= len(klines):
            raise ValueError("index out of kline range")

        started_at = klines[index].timestamp
        context = PipelineRunContext(
            run_id=run_id,
            source=self.source,
            symbol=symbol,
            timeframe=timeframe,
            started_at=started_at,
        )
        trace = TraceContext(
            run_id=run_id,
            source=self.source,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=started_at,
            bar_index=index,
        )
        selected_bar = klines[index]

        stages = []
        data_payload = {
            "symbol": symbol,
            "timeframe": timeframe,
            "bar_count": len(klines),
            "selected_index": index,
            "selected_bar": self._payload(klines[index]),
        }
        stages.append(self._stage("data", started_at, data_payload, data_payload))

        quality_report = validate_klines(klines=klines, symbol=symbol, timeframe=timeframe)
        quality_report_id = self._quality_report_id(
            quality_report,
            source_window_start=klines[0].timestamp,
            source_window_end=selected_bar.timestamp,
        )
        quality_payload = {
            "quality_report": quality_report.to_payload(),
            "quality_report_id": quality_report_id,
        }
        if not quality_report.passed:
            stages.append(
                PipelineStageResult(
                    stage="data_quality",
                    status=PipelineStageStatus.REJECTED,
                    started_at=started_at,
                    ended_at=started_at,
                    input_payload={"symbol": symbol, "timeframe": timeframe, "bar_count": len(klines)},
                    output_payload=quality_payload,
                    rejection=self._quality_rejection(quality_report),
                )
            )
            return self._finish(
                context,
                stages
                + [
                    self._skipped("feature", started_at, "quality_report_failed: data quality rejected klines"),
                    self._skipped("regime", started_at, "data quality rejected klines"),
                    self._skipped("strategy", started_at, "data quality rejected klines"),
                    self._skipped("decision", started_at, "data quality rejected klines"),
                    self._skipped("risk", started_at, "data quality rejected klines"),
                    self._skipped("portfolio", started_at, "data quality rejected klines"),
                    self._skipped("execution", started_at, "data quality rejected klines"),
                    self._skipped("logging", started_at, "data quality rejected klines"),
                ],
                started_at,
                quality_payload,
            )

        stages.append(
            self._stage(
                "data_quality",
                started_at,
                {"symbol": symbol, "timeframe": timeframe, "bar_count": len(klines)},
                quality_payload,
            )
        )

        kill_switch_decision = self._evaluate_runtime_kill_switch(symbol, selected_bar.timestamp)
        if getattr(self.risk_manager, "kill_switch_enabled", False):
            risk_input = {
                "signal": "buy",
                "symbol": symbol,
                "timestamp": selected_bar.timestamp,
                "decision_id": f"{run_id}:kill-switch",
                "client_order_id": f"{run_id}:{symbol}:{index}:kill-switch-block",
                "trace": trace,
            }
            risk_decision = self.risk_manager.evaluate(risk_input, self.account, selected_bar.close)
            close_results = self._close_positions_for_kill_switch(
                run_id,
                selected_bar.timestamp,
                selected_bar.close,
                index,
                trace,
            )
            stages.extend(
                [
                    self._skipped("feature", selected_bar.timestamp, "kill switch active"),
                    self._skipped("regime", selected_bar.timestamp, "kill switch active"),
                    self._skipped("strategy", selected_bar.timestamp, "kill switch active"),
                    self._skipped("decision", selected_bar.timestamp, "kill switch active"),
                    PipelineStageResult(
                        stage="risk",
                        status=PipelineStageStatus.REJECTED,
                        started_at=selected_bar.timestamp,
                        ended_at=selected_bar.timestamp,
                        input_payload={"symbol": symbol, "kill_switch_check": True},
                        output_payload={
                            "risk_decision": risk_decision.to_payload(),
                            "kill_switch_decision": (
                                None if kill_switch_decision is None else kill_switch_decision.to_payload()
                            ),
                        },
                        rejection=risk_decision.rejections[0],
                    ),
                    self._skipped("portfolio", selected_bar.timestamp, "kill switch active"),
                ]
            )
            if close_results:
                stages.append(
                    self._stage(
                        "execution",
                        selected_bar.timestamp,
                        {"reason": "kill_switch_close_positions"},
                        {"close_results": close_results},
                    )
                )
            else:
                stages.append(self._skipped("execution", selected_bar.timestamp, "no open positions to close"))
            stages.append(self._skipped("logging", selected_bar.timestamp, "kill switch blocked new order"))
            return self._finish(
                context,
                stages,
                selected_bar.timestamp,
                {
                    "risk_decision": risk_decision.to_payload(),
                    "kill_switch_decision": (
                        None if kill_switch_decision is None else kill_switch_decision.to_payload()
                    ),
                    "close_results": close_results,
                },
            )

        feature_series = self._compute_feature_series(klines, index)
        feature_snapshot = FeatureSnapshot.from_feature_series(
            feature_series,
            index,
            snapshot_id=f"{run_id}:features:{index}",
            timestamp=selected_bar.timestamp,
            symbol=symbol,
            timeframe=timeframe,
            as_of_timestamp=selected_bar.timestamp,
            feature_set_id="default_ma",
            feature_set_version="1.0",
            source_window_start=klines[0].timestamp,
            source_window_end=selected_bar.timestamp,
            trace=trace,
        )
        stages.append(
            self._stage(
                "feature",
                selected_bar.timestamp,
                {
                    "bar_count": len(klines),
                    "index": index,
                    "quality_report_id": quality_report_id,
                    "quality_passed": quality_report.passed,
                },
                {"snapshot": feature_snapshot.to_payload()},
            )
        )

        regime = self.regime_detector.detect(
            feature_snapshot,
            quality_report=quality_report,
        )
        stages.append(
            self._stage(
                "regime",
                selected_bar.timestamp,
                {
                    "snapshot_id": feature_snapshot.snapshot_id,
                    "quality_report_id": regime.input_refs.get("quality_report_id"),
                    "quality_passed": quality_report.passed,
                },
                {"regime": regime.to_payload()},
            )
        )

        routed = self.strategy_router.route(regime)
        signal = routed.strategy.generate_signal(feature_series, index)
        signal_payload = None if signal is None else signal.to_payload()
        stages.append(
            self._stage(
                "strategy",
                selected_bar.timestamp,
                {"regime_id": regime.regime_id},
                {"route": routed.route.to_payload(), "signal": signal_payload},
            )
        )

        if signal is None:
            return self._finish(
                context,
                stages
                + [
                    self._skipped("decision", selected_bar.timestamp, "strategy produced no signal"),
                    self._skipped("portfolio", selected_bar.timestamp, "no trade intent"),
                    self._skipped("risk", selected_bar.timestamp, "no capital budget from decision"),
                    self._skipped("execution", selected_bar.timestamp, "no order intent"),
                    self._skipped("logging", selected_bar.timestamp, "no trade event to log"),
                ],
                selected_bar.timestamp,
                {"signal": None, "regime": regime.to_payload()},
            )

        candidate_order = self._build_candidate_order(signal, selected_bar.close)
        decision_request = self._build_decision_request(
            signal,
            selected_bar,
            symbol,
            timeframe,
            regime,
            candidate_order,
            trace,
        )
        decision_result = self.decision_engine.evaluate(decision_request)
        decision = self._build_decision_from_result(
            decision_result,
            signal,
            selected_bar,
            symbol,
            regime,
            trace,
        )
        stages.append(
            self._stage(
                "decision",
                selected_bar.timestamp,
                {
                    "signal": signal.to_payload(),
                    "decision_request": decision_request.to_payload(),
                    "candidate_order": candidate_order,
                },
                {
                    "decision": decision.to_payload(),
                    "decision_result": decision_result.to_payload(),
                },
            )
        )
        if not decision_result.forward_to_capital_allocation:
            reason = ", ".join(decision_result.reason_codes) or "decision did not approve trade intent"
            return self._finish(
                context,
                stages
                + [
                    self._skipped("portfolio", selected_bar.timestamp, "decision did not approve trade intent"),
                    self._skipped("risk", selected_bar.timestamp, "no capital budget from decision"),
                    self._skipped("execution", selected_bar.timestamp, "no risk-approved order"),
                    self._skipped("logging", selected_bar.timestamp, "no trade event to log"),
                ],
                selected_bar.timestamp,
                {
                    "signal": signal.to_payload(),
                    "regime": regime.to_payload(),
                    "decision": decision.to_payload(),
                    "decision_result": decision_result.to_payload(),
                    "reason": reason,
                },
            )

        trade_intent = decision_result.trade_intent
        capital_budget_request = self._build_capital_budget_request(
            trade_intent,
            selected_bar.close,
            selected_bar.timestamp,
            trace,
        )
        capital_budget = self.capital_budget_allocator.allocate(capital_budget_request)
        portfolio_payload = {
            "capital_budget_request": capital_budget_request.to_payload(),
            "capital_budget": capital_budget.to_payload(),
            "decision_result_id": decision_result.result_id,
            "trade_intent_id": trade_intent.trade_intent_id,
        }
        if not capital_budget.approved:
            stages.append(
                PipelineStageResult(
                    stage="portfolio",
                    status=PipelineStageStatus.REJECTED,
                    started_at=selected_bar.timestamp,
                    ended_at=selected_bar.timestamp,
                    input_payload={"trade_intent_id": trade_intent.trade_intent_id},
                    output_payload=portfolio_payload,
                    rejection=LayerRejection(
                        layer=LayerName.PORTFOLIO,
                        code=capital_budget.reason_codes[-1],
                        message="capital budget rejected trade intent",
                    ),
                )
            )
            return self._finish(
                context,
                stages
                + [
                    self._skipped("risk", selected_bar.timestamp, "capital budget rejected trade intent"),
                    self._skipped("execution", selected_bar.timestamp, "capital budget rejected trade intent"),
                    self._skipped("logging", selected_bar.timestamp, "capital budget rejected trade intent"),
                ],
                selected_bar.timestamp,
                {
                    "decision": decision.to_payload(),
                    "decision_result": decision_result.to_payload(),
                    "capital_budget": capital_budget.to_payload(),
                },
            )

        stages.append(
            self._stage(
                "portfolio",
                selected_bar.timestamp,
                {"trade_intent_id": trade_intent.trade_intent_id},
                portfolio_payload,
            )
        )

        risk_request = self._build_risk_v2_request(
            trade_intent,
            capital_budget,
            selected_bar.close,
            selected_bar.timestamp,
            trace,
        )
        risk_decision = self.risk_manager.evaluate_v2(risk_request)
        if not risk_decision.approved:
            stages.append(
                PipelineStageResult(
                    stage="risk",
                    status=PipelineStageStatus.REJECTED,
                    started_at=selected_bar.timestamp,
                    ended_at=selected_bar.timestamp,
                    input_payload={"trade_intent_id": trade_intent.trade_intent_id},
                    output_payload={"risk_decision": risk_decision.to_payload()},
                    rejection=risk_decision.rejections[0],
                )
            )
            return self._finish(
                context,
                stages
                + [
                    self._skipped("execution", selected_bar.timestamp, "risk rejected order"),
                    self._skipped("logging", selected_bar.timestamp, "risk rejected order"),
                ],
                selected_bar.timestamp,
                {
                    "decision": decision.to_payload(),
                    "decision_result": decision_result.to_payload(),
                    "capital_budget": capital_budget.to_payload(),
                    "risk_decision": risk_decision.to_payload(),
                },
            )

        order_intent = risk_decision.order_intent
        portfolio_execution_context = self._build_portfolio_execution_context(
            order_intent,
            capital_budget,
            risk_decision,
        )
        protective_exit_plan = risk_decision.protective_exit_plan
        stages.append(
            self._stage(
                "risk",
                selected_bar.timestamp,
                {
                    "trade_intent_id": trade_intent.trade_intent_id,
                    "capital_budget_id": capital_budget.budget_id,
                    "risk_request": risk_request.to_payload(),
                },
                {
                    "risk_decision": risk_decision.to_payload(),
                    "portfolio_execution_context": portfolio_execution_context.to_payload(),
                    "allocation_decision": self._legacy_allocation_payload(
                        capital_budget,
                        order_intent,
                        selected_bar.close,
                    ),
                },
            )
        )

        execution_result = self._execute_order_intent(
            order_intent,
            selected_bar.close,
            index,
            portfolio_allocation=portfolio_execution_context,
            dry_run=False,
            execution_order_plan=risk_decision.execution_order_plan,
        )
        if protective_exit_plan is not None and execution_result.get("filled_qty", 0.0) > 0.0:
            registered_plan = self._register_protective_exit_plan(
                protective_exit_plan,
                execution_result["filled_qty"],
            )
            if registered_plan is not None:
                execution_result = dict(execution_result)
                execution_result["protective_exit_plan"] = registered_plan
        stages.append(
            self._stage(
                "execution",
                selected_bar.timestamp,
                {
                    "order_intent": order_intent.to_payload(),
                    "protective_exit_plan": (
                        None if protective_exit_plan is None else protective_exit_plan.to_payload()
                    ),
                },
                {"execution_result": execution_result},
            )
        )

        log_payload = self._log_trade_event(
            symbol,
            run_id,
            selected_bar.timestamp,
            trace,
            decision,
            execution_result,
            feature_snapshot,
        )
        stages.append(
            self._stage(
                "logging",
                selected_bar.timestamp,
                {"decision_id": decision.decision_id, "client_order_id": order_intent.client_order_id},
                log_payload,
            )
        )

        return self._finish(
            context,
            stages,
            selected_bar.timestamp,
            {
                "decision": decision.to_payload(),
                "decision_result": decision_result.to_payload(),
                "risk_decision": risk_decision.to_payload(),
                "capital_budget": capital_budget.to_payload(),
                "portfolio_execution_context": portfolio_execution_context.to_payload(),
                "execution_result": execution_result,
            },
        )

    def _run_multi_timeframe_tick(self, symbol="BTCUSDT", timeframe="1m", index=None, run_id=None):
        run_id = run_id or f"paper-{uuid4().hex}"
        mtf_config = self._multi_timeframe_payload()
        execution_timeframe = mtf_config["execution_timeframe"]
        if timeframe != execution_timeframe:
            raise ValueError("multi_timeframe run_tick timeframe must match execution_timeframe")

        data_request = self._multi_timeframe_data_request(symbol, execution_timeframe, mtf_config)
        batch = build_multi_timeframe_kline_batch(
            provider=self.provider,
            request=data_request,
            venue=mtf_config.get("venue", "runtime"),
        )
        if batch.execution is None or not batch.execution.klines:
            raise ValueError("multi_timeframe provider returned no execution klines")

        execution_klines = batch.execution.klines
        index = len(execution_klines) - 1 if index is None else index
        if index < 0 or index >= len(execution_klines):
            raise ValueError("index out of kline range")

        selected_bar = execution_klines[index]
        started_at = selected_bar.timestamp
        context = PipelineRunContext(
            run_id=run_id,
            source=self.source,
            symbol=symbol,
            timeframe=execution_timeframe,
            started_at=started_at,
            metadata={
                "multi_timeframe_enabled": True,
                "context_timeframes": list(mtf_config["context_timeframes"]),
            },
        )
        trace = TraceContext(
            run_id=run_id,
            source=self.source,
            symbol=symbol,
            timeframe=execution_timeframe,
            timestamp=started_at,
            bar_index=index,
        )

        stages = []
        data_payload = {
            "multi_timeframe_enabled": True,
            "request": data_request.to_payload(),
            "execution_timeframe": batch.execution_timeframe,
            "context_timeframes": batch.context_timeframes,
            "selected_index": index,
            "selected_bar": self._payload(selected_bar),
            "timeframe_bar_counts": {
                timeframe_key: timeframe_batch.checked_count
                for timeframe_key, timeframe_batch in batch.timeframe_batches.items()
            },
            "timeframe_windows": {
                timeframe_key: {
                    "first_timestamp": timeframe_batch.first_timestamp,
                    "last_timestamp": timeframe_batch.last_timestamp,
                }
                for timeframe_key, timeframe_batch in batch.timeframe_batches.items()
            },
        }
        stages.append(self._stage("data", started_at, data_request.to_payload(), data_payload))

        quality_report = validate_multi_timeframe_klines(batch, as_of_timestamp=selected_bar.timestamp)
        quality_payload = {
            "multi_timeframe_quality_report": quality_report.to_payload(),
            "quality_report_id": self._multi_timeframe_quality_report_id(quality_report),
            "timeframe_quality_report_ids": {
                timeframe_key: self._quality_report_id(timeframe_report)
                for timeframe_key, timeframe_report in quality_report.timeframe_reports.items()
            },
        }
        if not quality_report.passed:
            stages.append(
                PipelineStageResult(
                    stage="data_quality",
                    status=PipelineStageStatus.REJECTED,
                    started_at=started_at,
                    ended_at=started_at,
                    input_payload={
                        "execution_timeframe": batch.execution_timeframe,
                        "context_timeframes": batch.context_timeframes,
                        "timeframe_bar_counts": data_payload["timeframe_bar_counts"],
                    },
                    output_payload=quality_payload,
                    rejection=self._multi_timeframe_quality_rejection(quality_report),
                )
            )
            return self._finish(
                context,
                stages
                + [
                    self._skipped("feature", started_at, "multi-timeframe data quality rejected klines"),
                    self._skipped("regime", started_at, "multi-timeframe data quality rejected klines"),
                    self._skipped("strategy", started_at, "multi-timeframe data quality rejected klines"),
                    self._skipped("decision", started_at, "multi-timeframe data quality rejected klines"),
                    self._skipped("portfolio", started_at, "multi-timeframe data quality rejected klines"),
                    self._skipped("risk", started_at, "multi-timeframe data quality rejected klines"),
                    self._skipped("execution", started_at, "multi-timeframe data quality rejected klines"),
                    self._skipped("logging", started_at, "multi-timeframe data quality rejected klines"),
                ],
                started_at,
                quality_payload,
            )

        stages.append(
            self._stage(
                "data_quality",
                started_at,
                {
                    "execution_timeframe": batch.execution_timeframe,
                    "context_timeframes": batch.context_timeframes,
                    "timeframe_bar_counts": data_payload["timeframe_bar_counts"],
                },
                quality_payload,
            )
        )

        feature_snapshot = self.multi_timeframe_feature_pipeline.compute(
            MultiTimeframeFeaturePipelineInput(
                batch=batch,
                quality_report=quality_report,
                timeframe_indices={batch.execution_timeframe: index},
                snapshot_id=f"{run_id}:multi-timeframe-features:{index}",
            )
        )
        execution_feature_snapshot = feature_snapshot.execution_snapshot
        feature_series = self._compute_feature_series(execution_klines, index)
        stages.append(
            self._stage(
                "feature",
                selected_bar.timestamp,
                {
                    "multi_timeframe_quality_report_id": quality_payload["quality_report_id"],
                    "execution_timeframe": batch.execution_timeframe,
                    "context_timeframes": feature_snapshot.context_timeframes,
                    "timeframe_quality_report_ids": quality_payload["timeframe_quality_report_ids"],
                },
                {"multi_timeframe_feature_snapshot": feature_snapshot.to_payload()},
            )
        )

        mtf_regime = self.multi_timeframe_regime_detector.detect(feature_snapshot)
        regime = mtf_regime.aggregate_regime
        stages.append(
            self._stage(
                "regime",
                selected_bar.timestamp,
                {
                    "multi_timeframe_feature_snapshot_id": feature_snapshot.snapshot_id,
                    "quality_report_id": quality_payload["quality_report_id"],
                },
                {"multi_timeframe_regime": mtf_regime.to_payload()},
            )
        )

        routed = self.strategy_router.route(regime)
        raw_signal = routed.strategy.generate_signal(feature_series, index)
        signal = self.multi_timeframe_signal_filter.filter(
            MultiTimeframeStrategySignalInput(
                route=routed.route,
                raw_signal=raw_signal,
                execution_feature_series=feature_series,
                context_features={
                    timeframe_key: snapshot.to_payload()
                    for timeframe_key, snapshot in feature_snapshot.timeframe_snapshots.items()
                    if timeframe_key != batch.execution_timeframe
                },
                multi_timeframe_regime=mtf_regime,
            )
        )
        stages.append(
            self._stage(
                "strategy",
                selected_bar.timestamp,
                {
                    "regime_id": regime.regime_id,
                    "multi_timeframe_regime_snapshot_id": mtf_regime.snapshot_id,
                    "raw_signal": None if raw_signal is None else raw_signal.to_payload(),
                },
                {
                    "route": routed.route.to_payload(),
                    "signal": None if signal is None else signal.to_payload(),
                    "filter": {
                        "enabled": True,
                        "filter_id": "higher_timeframe_confirmation",
                        "higher_timeframe_bias": mtf_regime.higher_timeframe_bias,
                        "confirmation_timeframes": list(mtf_regime.confirmation_timeframes),
                        "conflict_timeframes": list(mtf_regime.conflict_timeframes),
                        "tradability": mtf_regime.tradability,
                    },
                },
            )
        )

        if signal is None:
            return self._finish(
                context,
                stages
                + [
                    self._skipped("decision", selected_bar.timestamp, "strategy produced no signal"),
                    self._skipped("portfolio", selected_bar.timestamp, "no trade intent"),
                    self._skipped("risk", selected_bar.timestamp, "no capital budget from decision"),
                    self._skipped("execution", selected_bar.timestamp, "no order intent"),
                    self._skipped("logging", selected_bar.timestamp, "no trade event to log"),
                ],
                selected_bar.timestamp,
                {
                    "multi_timeframe": True,
                    "signal": None,
                    "multi_timeframe_regime": mtf_regime.to_payload(),
                },
            )

        candidate_order = self._build_candidate_order(signal, selected_bar.close)
        decision_request = self._build_decision_request(
            signal,
            selected_bar,
            symbol,
            execution_timeframe,
            regime,
            candidate_order,
            trace,
        )
        decision_result = self.decision_engine.evaluate(decision_request)
        decision = self._build_decision_from_result(
            decision_result,
            signal,
            selected_bar,
            symbol,
            regime,
            trace,
        )
        stages.append(
            self._stage(
                "decision",
                selected_bar.timestamp,
                {
                    "signal": signal.to_payload(),
                    "decision_request": decision_request.to_payload(),
                    "candidate_order": candidate_order,
                    "multi_timeframe_regime_snapshot_id": mtf_regime.snapshot_id,
                },
                {
                    "decision": decision.to_payload(),
                    "decision_result": decision_result.to_payload(),
                },
            )
        )
        if not decision_result.forward_to_capital_allocation:
            reason = ", ".join(decision_result.reason_codes) or "decision did not approve trade intent"
            return self._finish(
                context,
                stages
                + [
                    self._skipped("portfolio", selected_bar.timestamp, "decision did not approve trade intent"),
                    self._skipped("risk", selected_bar.timestamp, "no capital budget from decision"),
                    self._skipped("execution", selected_bar.timestamp, "no risk-approved order"),
                    self._skipped("logging", selected_bar.timestamp, "no trade event to log"),
                ],
                selected_bar.timestamp,
                {
                    "multi_timeframe": True,
                    "signal": signal.to_payload(),
                    "multi_timeframe_regime": mtf_regime.to_payload(),
                    "decision": decision.to_payload(),
                    "decision_result": decision_result.to_payload(),
                    "reason": reason,
                },
            )

        return self._run_capital_risk_execution_from_decision(
            context=context,
            stages=stages,
            run_id=run_id,
            symbol=symbol,
            index=index,
            selected_bar=selected_bar,
            trace=trace,
            feature_snapshot=execution_feature_snapshot,
            decision=decision,
            decision_result=decision_result,
            final_output_extra={
                "multi_timeframe": True,
                "multi_timeframe_regime": mtf_regime.to_payload(),
                "multi_timeframe_feature_snapshot": feature_snapshot.to_payload(),
            },
        )

    def _run_capital_risk_execution_from_decision(
        self,
        *,
        context,
        stages,
        run_id,
        symbol,
        index,
        selected_bar,
        trace,
        feature_snapshot,
        decision,
        decision_result,
        final_output_extra=None,
    ):
        final_output_extra = dict(final_output_extra or {})
        trade_intent = decision_result.trade_intent
        capital_budget_request = self._build_capital_budget_request(
            trade_intent,
            selected_bar.close,
            selected_bar.timestamp,
            trace,
        )
        capital_budget = self.capital_budget_allocator.allocate(capital_budget_request)
        portfolio_payload = {
            "capital_budget_request": capital_budget_request.to_payload(),
            "capital_budget": capital_budget.to_payload(),
            "decision_result_id": decision_result.result_id,
            "trade_intent_id": trade_intent.trade_intent_id,
        }
        if not capital_budget.approved:
            stages.append(
                PipelineStageResult(
                    stage="portfolio",
                    status=PipelineStageStatus.REJECTED,
                    started_at=selected_bar.timestamp,
                    ended_at=selected_bar.timestamp,
                    input_payload={"trade_intent_id": trade_intent.trade_intent_id},
                    output_payload=portfolio_payload,
                    rejection=LayerRejection(
                        layer=LayerName.PORTFOLIO,
                        code=capital_budget.reason_codes[-1],
                        message="capital budget rejected trade intent",
                    ),
                )
            )
            final_output = {
                "decision": decision.to_payload(),
                "decision_result": decision_result.to_payload(),
                "capital_budget": capital_budget.to_payload(),
            }
            final_output.update(final_output_extra)
            return self._finish(
                context,
                stages
                + [
                    self._skipped("risk", selected_bar.timestamp, "capital budget rejected trade intent"),
                    self._skipped("execution", selected_bar.timestamp, "capital budget rejected trade intent"),
                    self._skipped("logging", selected_bar.timestamp, "capital budget rejected trade intent"),
                ],
                selected_bar.timestamp,
                final_output,
            )

        stages.append(
            self._stage(
                "portfolio",
                selected_bar.timestamp,
                {"trade_intent_id": trade_intent.trade_intent_id},
                portfolio_payload,
            )
        )

        risk_request = self._build_risk_v2_request(
            trade_intent,
            capital_budget,
            selected_bar.close,
            selected_bar.timestamp,
            trace,
        )
        risk_decision = self.risk_manager.evaluate_v2(risk_request)
        if not risk_decision.approved:
            stages.append(
                PipelineStageResult(
                    stage="risk",
                    status=PipelineStageStatus.REJECTED,
                    started_at=selected_bar.timestamp,
                    ended_at=selected_bar.timestamp,
                    input_payload={"trade_intent_id": trade_intent.trade_intent_id},
                    output_payload={"risk_decision": risk_decision.to_payload()},
                    rejection=risk_decision.rejections[0],
                )
            )
            final_output = {
                "decision": decision.to_payload(),
                "decision_result": decision_result.to_payload(),
                "capital_budget": capital_budget.to_payload(),
                "risk_decision": risk_decision.to_payload(),
            }
            final_output.update(final_output_extra)
            return self._finish(
                context,
                stages
                + [
                    self._skipped("execution", selected_bar.timestamp, "risk rejected order"),
                    self._skipped("logging", selected_bar.timestamp, "risk rejected order"),
                ],
                selected_bar.timestamp,
                final_output,
            )

        order_intent = risk_decision.order_intent
        portfolio_execution_context = self._build_portfolio_execution_context(
            order_intent,
            capital_budget,
            risk_decision,
        )
        protective_exit_plan = risk_decision.protective_exit_plan
        stages.append(
            self._stage(
                "risk",
                selected_bar.timestamp,
                {
                    "trade_intent_id": trade_intent.trade_intent_id,
                    "capital_budget_id": capital_budget.budget_id,
                    "risk_request": risk_request.to_payload(),
                },
                {
                    "risk_decision": risk_decision.to_payload(),
                    "portfolio_execution_context": portfolio_execution_context.to_payload(),
                    "allocation_decision": self._legacy_allocation_payload(
                        capital_budget,
                        order_intent,
                        selected_bar.close,
                    ),
                },
            )
        )

        execution_result = self._execute_order_intent(
            order_intent,
            selected_bar.close,
            index,
            portfolio_allocation=portfolio_execution_context,
            dry_run=False,
            execution_order_plan=risk_decision.execution_order_plan,
        )
        if protective_exit_plan is not None and execution_result.get("filled_qty", 0.0) > 0.0:
            registered_plan = self._register_protective_exit_plan(
                protective_exit_plan,
                execution_result["filled_qty"],
            )
            if registered_plan is not None:
                execution_result = dict(execution_result)
                execution_result["protective_exit_plan"] = registered_plan
        stages.append(
            self._stage(
                "execution",
                selected_bar.timestamp,
                {
                    "order_intent": order_intent.to_payload(),
                    "protective_exit_plan": (
                        None if protective_exit_plan is None else protective_exit_plan.to_payload()
                    ),
                },
                {"execution_result": execution_result},
            )
        )

        log_payload = self._log_trade_event(
            symbol,
            run_id,
            selected_bar.timestamp,
            trace,
            decision,
            execution_result,
            feature_snapshot,
        )
        stages.append(
            self._stage(
                "logging",
                selected_bar.timestamp,
                {"decision_id": decision.decision_id, "client_order_id": order_intent.client_order_id},
                log_payload,
            )
        )

        final_output = {
            "decision": decision.to_payload(),
            "decision_result": decision_result.to_payload(),
            "risk_decision": risk_decision.to_payload(),
            "capital_budget": capital_budget.to_payload(),
            "portfolio_execution_context": portfolio_execution_context.to_payload(),
            "execution_result": execution_result,
        }
        final_output.update(final_output_extra)
        return self._finish(
            context,
            stages,
            selected_bar.timestamp,
            final_output,
        )

    def _multi_timeframe_enabled(self):
        return self._multi_timeframe_field("enabled", False) is True

    def _multi_timeframe_payload(self):
        config = self.multi_timeframe_config
        if hasattr(config, "to_payload"):
            payload = config.to_payload()
        elif isinstance(config, dict):
            payload = dict(config)
        else:
            payload = {
                "enabled": getattr(config, "enabled", False),
                "execution_timeframe": getattr(config, "execution_timeframe", None),
                "context_timeframes": getattr(config, "context_timeframes", []),
                "bar_limits": getattr(config, "bar_limits", {}),
                "default_bar_limit": getattr(config, "default_bar_limit", 100),
                "venue": getattr(config, "venue", "runtime"),
            }

        execution_timeframe = payload.get("execution_timeframe")
        context_timeframes = payload.get("context_timeframes") or []
        if not execution_timeframe:
            raise ValueError("multi_timeframe enabled requires execution_timeframe")
        if not context_timeframes:
            raise ValueError("multi_timeframe enabled requires context_timeframes")

        payload["execution_timeframe"] = str(execution_timeframe).strip()
        payload["context_timeframes"] = [str(timeframe).strip() for timeframe in context_timeframes]
        payload["bar_limits"] = dict(payload.get("bar_limits") or {})
        payload["default_bar_limit"] = int(payload.get("default_bar_limit") or 100)
        payload["venue"] = str(payload.get("venue") or "runtime").strip() or "runtime"
        return payload

    def _multi_timeframe_field(self, name, default=None):
        config = self.multi_timeframe_config
        if isinstance(config, dict):
            return config.get(name, default)
        return getattr(config, name, default)

    def _multi_timeframe_data_request(self, symbol, execution_timeframe, config):
        return MultiTimeframeDataRequest(
            symbol=symbol,
            venue=config.get("venue", "runtime"),
            execution_timeframe=execution_timeframe,
            context_timeframes=list(config["context_timeframes"]),
            limit=self._multi_timeframe_limit(execution_timeframe, config),
        )

    @staticmethod
    def _multi_timeframe_limit(timeframe, config):
        bar_limits = dict(config.get("bar_limits") or {})
        return int(bar_limits.get(timeframe, config.get("default_bar_limit", 100)))

    @staticmethod
    def _multi_timeframe_quality_report_id(quality_report):
        timeframe_parts = []
        for timeframe, timeframe_report in sorted(quality_report.timeframe_reports.items()):
            timeframe_parts.append(
                f"{timeframe}:{timeframe_report.checked_count}:"
                f"{timeframe_report.first_timestamp}:{timeframe_report.last_timestamp}"
            )
        return (
            f"quality:multi-timeframe:{quality_report.symbol}:"
            f"{quality_report.execution_timeframe}:"
            f"{quality_report.as_of_timestamp}:{'|'.join(timeframe_parts)}"
        )

    @staticmethod
    def _multi_timeframe_quality_rejection(quality_report):
        first_issue = None
        for issue in quality_report.alignment_issues:
            if issue.fatal:
                first_issue = issue
                break
        if first_issue is None and quality_report.alignment_issues:
            first_issue = quality_report.alignment_issues[0]

        if first_issue is None:
            code = "multi_timeframe_quality_failed"
            message = "Multi-timeframe quality report did not pass"
        else:
            code = first_issue.code
            message = first_issue.message

        return LayerRejection(
            layer=LayerName.DATA,
            code=code,
            message=message,
            fatal=True,
        )

    def _evaluate_runtime_kill_switch(self, symbol, timestamp):
        if not hasattr(self.risk_manager, "evaluate_kill_switch_triggers"):
            return None

        daily_loss_pct = 0.0
        initial_balance = getattr(self.account, "initial_balance", 0.0)
        equity = getattr(self.account, "equity", initial_balance)
        if initial_balance:
            daily_loss_pct = max(0.0, (initial_balance - equity) / initial_balance)

        return self.risk_manager.evaluate_kill_switch_triggers(
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "daily_loss_pct": daily_loss_pct,
                "consecutive_losses": getattr(self.account, "consecutive_losses", 0),
                "api_failure_rate": getattr(self.execution_engine, "api_failure_rate", None),
            }
        )

    def _close_positions_for_kill_switch(self, run_id, timestamp, price, index, trace):
        close_results = []
        positions = getattr(self.account, "positions", {})
        for position_symbol, position in positions.items():
            size = getattr(position, "size", 0.0)
            if size == 0.0:
                continue
            side = TradeSide.SELL if size > 0.0 else TradeSide.BUY
            quantity = abs(size)
            client_order_id = f"{run_id}:{position_symbol}:{index}:kill-switch-close"
            order_intent = OrderIntent(
                order_intent_id=f"order-intent-{client_order_id}",
                decision_id=f"{run_id}:kill-switch",
                client_order_id=client_order_id,
                symbol=position_symbol,
                side=side,
                order_type=OrderKind.MARKET,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                risk_approved=True,
                created_at=timestamp,
                trace=trace,
            )
            close_results.append(
                self._execute_order_intent(order_intent, price, index, dry_run=False)
            )
        return close_results

    def _compute_feature_series(self, klines, index):
        fast_window, slow_window = self.feature_windows
        fast = MovingAverage(fast_window)
        slow = MovingAverage(slow_window)
        fast_series = []
        slow_series = []
        for feature_index in range(len(klines)):
            fast_series.append(fast.compute(klines, feature_index))
            slow_series.append(slow.compute(klines, feature_index))
        return {
            "fast_ma": fast_series,
            "slow_ma": slow_series,
            "ma_fast": fast_series,
            "ma_slow": slow_series,
            "close": [kline.close for kline in klines],
        }

    def _build_decision(self, signal, bar, symbol, regime, trace):
        action = DecisionAction.OPEN_LONG if signal.side == TradeSide.BUY else DecisionAction.CLOSE_LONG
        return DecisionIntent(
            decision_id=f"{trace.run_id}:decision:{signal.signal_id}",
            timestamp=bar.timestamp,
            symbol=symbol,
            asset_class=AssetClass.CRYPTO,
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            regime=regime.regime,
            action=action,
            order_type=OrderKind.MARKET,
            quantity=1.0,
            time_in_force=TimeInForce.GTC,
            confidence=signal.confidence,
            reason_codes=list(signal.reason_codes),
            trace=trace,
        )

    def _build_decision_request(
        self,
        signal,
        bar,
        symbol,
        timeframe,
        regime,
        candidate_order,
        trace,
    ):
        return DecisionEngineRequest(
            request_id=f"{trace.run_id}:decision:{signal.signal_id}",
            timestamp=bar.timestamp,
            symbol=symbol,
            asset_class=AssetClass.CRYPTO,
            timeframe=timeframe,
            signal=signal,
            regime_snapshot=regime,
            portfolio_state=self._decision_portfolio_state(symbol),
            policy=self._decision_policy(),
            candidate_order=candidate_order,
            kill_switch_active=bool(getattr(self.risk_manager, "kill_switch_enabled", False)),
            trace=trace,
        )

    def _build_decision_from_result(self, decision_result, signal, bar, symbol, regime, trace):
        trade_intent = decision_result.trade_intent
        if trade_intent is None:
            return DecisionIntent(
                decision_id=f"{decision_result.result_id}:watch",
                timestamp=bar.timestamp,
                symbol=symbol,
                asset_class=AssetClass.CRYPTO,
                strategy_id=signal.strategy_id,
                strategy_version=signal.strategy_version,
                regime=regime.regime,
                action=DecisionAction.HOLD,
                order_type=OrderKind.MARKET,
                quantity=1.0,
                time_in_force=TimeInForce.GTC,
                confidence=signal.confidence,
                reason_codes=list(decision_result.reason_codes),
                trace=trace,
            )

        return DecisionIntent(
            decision_id=trade_intent.decision_id,
            timestamp=trade_intent.timestamp,
            symbol=trade_intent.symbol,
            asset_class=trade_intent.asset_class,
            market_type=trade_intent.market_type,
            strategy_id=trade_intent.strategy_id,
            strategy_version=trade_intent.strategy_version,
            regime=trade_intent.regime,
            action=trade_intent.action,
            order_type=OrderKind.MARKET,
            quantity=1.0,
            stop_loss=trade_intent.stop_loss,
            take_profit=trade_intent.take_profit,
            stop_loss_targets=list(trade_intent.stop_loss_targets),
            take_profit_targets=list(trade_intent.take_profit_targets),
            time_in_force=TimeInForce.GTC,
            confidence=trade_intent.confidence,
            reason_codes=list(decision_result.reason_codes),
            trace=trade_intent.trace or trace,
        )

    def _build_candidate_order(self, signal, price):
        if not getattr(signal, "is_orderable", True):
            return {}

        side = getattr(signal, "side", None)
        stop_loss_pct = getattr(self.risk_manager, "stop_loss_pct", 0.02)
        take_profit_pct = getattr(self.risk_manager, "take_profit_pct", None)
        if side == TradeSide.BUY:
            stop_loss = price * (1.0 - stop_loss_pct)
            take_profit = price * (1.0 + take_profit_pct) if take_profit_pct is not None else None
        else:
            stop_loss = price * (1.0 + stop_loss_pct)
            take_profit = price * (1.0 - take_profit_pct) if take_profit_pct is not None else None

        payload = {
            "entry_price": price,
            "stop_loss": stop_loss,
            "source": "orchestration_stop_target_adapter_v1",
        }
        if take_profit is not None:
            payload["take_profit"] = take_profit
        return payload

    def _decision_policy(self):
        return DecisionPolicy(
            min_confidence=0.0,
            allow_short_selling=True,
            enforce_regime_alignment=False,
        )

    def _decision_portfolio_state(self, symbol):
        position = self.account.get_position(symbol)
        size = getattr(position, "size", 0.0)
        if size > 0.0:
            side = PositionSide.LONG
        elif size < 0.0:
            side = PositionSide.SHORT
        else:
            side = PositionSide.FLAT

        return DecisionPortfolioState(
            symbol=symbol,
            position_side=side,
            open_position_quantity=abs(size),
        )

    def _build_capital_budget_request(self, trade_intent, price, timestamp, trace):
        position = self.account.get_position(trade_intent.symbol)
        current_symbol_notional = abs(position.market_value(price))
        total_notional = self._current_total_notional(price)
        max_symbol_weight = getattr(self.risk_manager, "max_position_pct", 0.10)
        return CapitalBudgetRequest(
            budget_id=f"{trace.run_id}:capital-budget:{trade_intent.trade_intent_id}",
            timestamp=timestamp,
            trade_intent=trade_intent,
            account_equity=self.account.equity,
            free_margin=max(0.0, self.account.balance),
            base_risk_budget_pct=max_symbol_weight,
            current_symbol_notional=current_symbol_notional,
            current_total_notional=total_notional,
            max_symbol_weight=max_symbol_weight,
            reason_codes=["decision_approved_trade_intent"],
            trace=trace,
        )

    def _build_risk_v2_request(self, trade_intent, capital_budget, price, timestamp, trace):
        return RiskEngineV2Request(
            request_id=f"{trace.run_id}:risk-v2:{trade_intent.trade_intent_id}",
            timestamp=timestamp,
            trade_intent=trade_intent,
            capital_budget=capital_budget,
            market_constraints=RiskMarketConstraints(
                symbol=trade_intent.symbol,
                entry_price=trade_intent.entry_price or price,
            ),
            risk_policy=RiskPolicy(
                order_type=OrderKind.MARKET,
                time_in_force=TimeInForce.GTC,
                min_risk_reward=0.0,
                allow_short_selling=True,
            ),
            trace=trace,
        )

    def _build_portfolio_execution_context(self, order_intent, capital_budget, risk_decision):
        risk_decision_id = (
            getattr(risk_decision, "risk_decision_id", None)
            or f"risk:{order_intent.decision_id}"
        )
        return PortfolioExecutionContext(
            allocation_id=capital_budget.budget_id,
            approved=capital_budget.approved,
            client_order_id=order_intent.client_order_id,
            risk_decision_id=risk_decision_id,
            symbol=capital_budget.symbol,
            side=capital_budget.side,
            allocated_quantity=order_intent.quantity,
            allocated_notional=order_intent.quantity * (
                order_intent.limit_price
                or (getattr(risk_decision.sizing, "entry_price", 0.0) if risk_decision.sizing else 0.0)
            ),
            reason_codes=list(capital_budget.reason_codes) + ["risk_v2_sized_order"],
            trace=capital_budget.trace or order_intent.trace,
        )

    def _legacy_allocation_payload(self, capital_budget, order_intent, price):
        return {
            "allocation_id": capital_budget.budget_id,
            "approved": capital_budget.approved,
            "symbol": capital_budget.symbol,
            "side": self._enum_value(capital_budget.side),
            "quantity": order_intent.quantity,
            "notional": order_intent.quantity * price,
            "price": price,
            "reason_codes": list(capital_budget.reason_codes),
            "source": "capital_budget_allocation_v1",
            "capital_budget": capital_budget.to_payload(),
        }

    def _current_total_notional(self, fallback_price):
        total = 0.0
        for position_symbol, position in getattr(self.account, "positions", {}).items():
            price = getattr(self.account, "market_prices", {}).get(position_symbol, fallback_price)
            total += abs(position.market_value(price))
        return total

    def _order_intent_with_quantity(self, order_intent, quantity):
        if quantity == order_intent.quantity:
            return order_intent
        if hasattr(order_intent, "model_copy"):
            return order_intent.model_copy(update={"quantity": quantity})
        return order_intent.copy(update={"quantity": quantity})

    def _protective_exit_plan_with_quantity(self, protective_exit_plan, quantity):
        if protective_exit_plan is None or quantity == protective_exit_plan.quantity:
            return protective_exit_plan
        if hasattr(protective_exit_plan, "model_copy"):
            return protective_exit_plan.model_copy(update={"quantity": quantity})
        return protective_exit_plan.copy(update={"quantity": quantity})

    def _register_protective_exit_plan(self, protective_exit_plan, filled_qty):
        if not hasattr(self.execution_engine, "register_protective_exit"):
            return None

        plan = self._protective_exit_plan_with_quantity(protective_exit_plan, filled_qty)
        return self.execution_engine.register_protective_exit(plan)

    def _execute_order_intent(
        self,
        order_intent,
        price,
        index,
        *,
        portfolio_allocation=None,
        dry_run=None,
        execution_order_plan=None,
    ):
        method = self.execution_engine.on_order_intent
        args = (order_intent, price, index)
        if execution_order_plan is not None and hasattr(self.execution_engine, "on_execution_order_plan"):
            method = self.execution_engine.on_execution_order_plan
            args = (execution_order_plan, price, index)
        parameters = inspect.signature(method).parameters
        kwargs = {}
        if "portfolio_allocation" in parameters:
            kwargs["portfolio_allocation"] = portfolio_allocation
        if "dry_run" in parameters:
            kwargs["dry_run"] = dry_run
        result = method(*args, **kwargs)
        result = attach_order_lifecycle_contract(
            result,
            source=self.source,
            order_intent=order_intent,
            state_machine=getattr(self.execution_engine, "state_machine", None),
            dry_run=dry_run,
        )
        return self._execution_result_with_portfolio_context(
            result,
            portfolio_allocation,
        )

    @staticmethod
    def _execution_result_with_portfolio_context(result, portfolio_allocation):
        if result is None or portfolio_allocation is None:
            return result

        context = (
            portfolio_allocation.to_payload()
            if hasattr(portfolio_allocation, "to_payload")
            else dict(portfolio_allocation)
        )
        enriched = dict(result)
        enriched["allocation_id"] = context["allocation_id"]
        enriched["portfolio_allocation_id"] = context["allocation_id"]
        enriched["portfolio_approved"] = context["approved"]
        enriched["allocated_quantity"] = context["allocated_quantity"]
        enriched["portfolio_allocated_quantity"] = context["allocated_quantity"]
        enriched["portfolio_client_order_id"] = context["client_order_id"]
        enriched["risk_decision_id"] = context["risk_decision_id"]
        return enriched

    def _log_trade_event(self, symbol, run_id, timestamp, trace, decision, execution_result, feature_snapshot=None):
        logger = self._logger_for_symbol(symbol)
        if logger is None:
            return {"records_written": 0}

        feature_snapshot_payload = None if feature_snapshot is None else feature_snapshot.to_payload()
        records = [
            DecisionLogRecord(
                event_id=f"{run_id}:decision-log:{decision.decision_id}",
                run_id=run_id,
                timestamp=timestamp,
                decision=decision,
                feature_snapshot=feature_snapshot,
                trace=trace,
                metadata=(
                    {}
                    if feature_snapshot_payload is None
                    else {"feature_snapshot": feature_snapshot_payload}
                ),
            )
        ]
        if execution_result:
            status = self._order_status(execution_result["status"])
            execution_metadata = self._execution_audit_metadata(execution_result)
            records.append(
                OrderLogRecord(
                    event_id=f"{run_id}:order-log:{execution_result['client_order_id']}",
                    run_id=run_id,
                    timestamp=timestamp,
                    order_id=str(execution_result["order_id"]),
                    client_order_id=execution_result["client_order_id"],
                    symbol=execution_result["symbol"],
                    side=execution_result["side"],
                    status=status,
                    quantity=execution_result["filled_qty"] + execution_result["remaining_qty"],
                    filled_quantity=execution_result["filled_qty"],
                    remaining_quantity=execution_result["remaining_qty"],
                    price=execution_result.get("fill_price"),
                    decision_id=decision.decision_id,
                    trace=trace,
                    metadata=execution_metadata,
                )
            )
            if execution_result.get("fill_price") and execution_result["filled_qty"] > 0.0:
                records.append(
                    FillLogRecord(
                        event_id=f"{run_id}:fill-log:{execution_result['client_order_id']}",
                        run_id=run_id,
                        timestamp=timestamp,
                        fill_id=f"{execution_result['client_order_id']}:fill",
                        order_id=str(execution_result["order_id"]),
                        client_order_id=execution_result["client_order_id"],
                        symbol=execution_result["symbol"],
                        side=execution_result["side"],
                        filled_quantity=execution_result["filled_qty"],
                        fill_price=execution_result["fill_price"],
                        decision_id=decision.decision_id,
                        trace=trace,
                        metadata=execution_metadata,
                    )
                )

        for record in records:
            logger.append(record)

        return {"records_written": len(records), "record_types": [record.record_type for record in records]}

    @staticmethod
    def _execution_audit_metadata(execution_result):
        keys = (
            "allocation_id",
            "portfolio_allocation_id",
            "portfolio_approved",
            "allocated_quantity",
            "portfolio_allocated_quantity",
            "portfolio_client_order_id",
            "risk_decision_id",
        )
        return {
            key: execution_result[key]
            for key in keys
            if key in execution_result and execution_result[key] is not None
        }

    def _symbol_request(self, request):
        if isinstance(request, PipelineSymbolRunRequest):
            return request
        if hasattr(request, "symbol") and hasattr(request, "timeframe"):
            return PipelineSymbolRunRequest(
                symbol=request.symbol,
                timeframe=request.timeframe,
                index=getattr(request, "index", None),
                metadata=self._payload(getattr(request, "metadata", {})),
            )
        return PipelineSymbolRunRequest.from_payload(request)

    def _logger_for_symbol(self, symbol):
        if self.logger_factory is not None:
            return self.logger_factory(symbol)
        return self.logger

    def _error_report(self, run_id, request, timestamp, error):
        context = PipelineRunContext(
            run_id=run_id,
            source=self.source,
            symbol=request.symbol,
            timeframe=request.timeframe,
            started_at=timestamp,
        )
        stage = PipelineStageResult(
            stage="orchestration",
            status=PipelineStageStatus.ERROR,
            started_at=timestamp,
            ended_at=timestamp,
            input_payload={"symbol": request.symbol, "timeframe": request.timeframe, "index": request.index},
            error=error,
        )
        return PipelineRunReport(
            context=context,
            stages=[stage],
            finished_at=timestamp,
            success=False,
            final_output={"symbol": request.symbol, "timeframe": request.timeframe},
            errors=[error],
            metadata={
                "runtime_health": self._build_runtime_health_snapshot(
                    context,
                    [stage],
                    timestamp,
                ).to_payload()
            },
        )

    def _finish(self, context, stages, finished_at, final_output):
        errors = [stage.error for stage in stages if stage.error]
        success = not errors and all(stage.status != PipelineStageStatus.ERROR for stage in stages)
        runtime_health = self._build_runtime_health_snapshot(context, stages, finished_at)
        return PipelineRunReport(
            context=context,
            stages=stages,
            finished_at=finished_at,
            success=success,
            final_output=final_output,
            errors=errors,
            metadata={"runtime_health": runtime_health.to_payload()},
        )

    def _build_runtime_health_snapshot(self, context, stages, observed_at):
        stage_durations = {
            stage.stage: max(0, stage.ended_at - stage.started_at) * 1000
            for stage in stages
        }
        risk_stages = [stage for stage in stages if stage.stage == "risk"]
        rejected_risk_stages = [
            stage
            for stage in risk_stages
            if stage.status == PipelineStageStatus.REJECTED
        ]
        rejected_data_quality_stages = [
            stage
            for stage in stages
            if stage.stage == "data_quality"
            and stage.status == PipelineStageStatus.REJECTED
        ]
        execution_stages = [stage for stage in stages if stage.stage == "execution"]
        failed_execution_stages = [
            stage
            for stage in execution_stages
            if stage.status == PipelineStageStatus.ERROR
            or stage.output_payload.get("execution_result", {}).get("status") == "rejected"
        ]
        error_stages = [stage for stage in stages if stage.status == PipelineStageStatus.ERROR]
        kill_switch_active = bool(getattr(self.risk_manager, "kill_switch_enabled", False))

        alerts = []
        if error_stages:
            alerts.append("pipeline_error")
        if rejected_data_quality_stages:
            alerts.append("data_quality_rejection")
        if kill_switch_active:
            alerts.append("kill_switch_active")
        if rejected_risk_stages:
            alerts.append("risk_rejection")
        if failed_execution_stages:
            alerts.append("order_failure")

        if error_stages or kill_switch_active:
            status = RuntimeHealthStatus.CRITICAL
        elif alerts:
            status = RuntimeHealthStatus.DEGRADED
        else:
            status = RuntimeHealthStatus.HEALTHY

        selected_bar_timestamp = None
        for stage in stages:
            if stage.stage == "data":
                selected_bar = stage.output_payload.get("selected_bar", {})
                selected_bar_timestamp = selected_bar.get("timestamp")
                break
        data_latency_ms = 0
        if selected_bar_timestamp is not None:
            data_latency_ms = max(0, observed_at - selected_bar_timestamp) * 1000

        return RuntimeHealthSnapshot(
            run_id=context.run_id,
            source=context.source,
            observed_at=observed_at,
            status=status,
            symbol=context.symbol,
            timeframe=context.timeframe,
            data_latency_ms=data_latency_ms,
            pipeline_stage_durations_ms=stage_durations,
            order_failure_rate=(
                len(failed_execution_stages) / len(execution_stages)
                if execution_stages
                else 0.0
            ),
            risk_rejection_rate=(
                len(rejected_risk_stages) / len(risk_stages)
                if risk_stages
                else 0.0
            ),
            kill_switch_active=kill_switch_active,
            alerts=alerts,
            metadata={
                "stage_count": len(stages),
                "error_stage_count": len(error_stages),
            },
        )

    def _stage(self, name, timestamp, input_payload, output_payload):
        return PipelineStageResult(
            stage=name,
            status=PipelineStageStatus.SUCCEEDED,
            started_at=timestamp,
            ended_at=timestamp,
            input_payload=input_payload,
            output_payload=output_payload,
        )

    def _skipped(self, name, timestamp, reason):
        return PipelineStageResult(
            stage=name,
            status=PipelineStageStatus.SKIPPED,
            started_at=timestamp,
            ended_at=timestamp,
            skip_reason=reason,
        )

    def _quality_rejection(self, quality_report):
        first_issue = quality_report.issues[0]
        return LayerRejection(
            layer=LayerName.DATA,
            code=first_issue.code,
            message=first_issue.message,
            fatal=True,
        )

    @staticmethod
    def _quality_report_id(
        quality_report,
        *,
        source_window_start=None,
        source_window_end=None,
    ):
        start = source_window_start
        if start is None:
            start = getattr(quality_report, "first_timestamp", None)
        end = source_window_end
        if end is None:
            end = getattr(quality_report, "last_timestamp", None)
        return (
            f"quality:{quality_report.symbol}:{quality_report.timeframe}:"
            f"{quality_report.checked_count}:{start}:{end}"
        )

    def _payload(self, value):
        if hasattr(value, "to_payload"):
            return value.to_payload()
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "dict"):
            return value.dict()
        return value

    @staticmethod
    def _enum_value(value):
        return getattr(value, "value", value)

    def _order_status(self, status):
        if status == "pending":
            return OrderStatus.PENDING
        if status == "partial":
            return OrderStatus.PARTIAL
        if status == "filled":
            return OrderStatus.FILLED
        if status == "rejected":
            return OrderStatus.REJECTED
        return OrderStatus.UNKNOWN
