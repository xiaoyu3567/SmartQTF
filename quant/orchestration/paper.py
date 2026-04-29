from uuid import uuid4

from quant.account.account import CryptoAccount
from quant.account.capital_allocator import CapitalAllocator
from quant.data.quality import validate_klines
from quant.data.providers.mock_provider import MockProvider
from quant.features.indicators.moving_average import MovingAverage
from quant.logging.jsonl import JsonlTradeLogger
from quant.regime.rule_detector import RuleBasedRegimeDetector
from quant.risk.risk_manager import RiskManager
from quant.schemas import (
    AssetClass,
    CapitalAllocationRequest,
    DecisionAction,
    DecisionIntent,
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
    RegimeKind,
    RuntimeHealthSnapshot,
    RuntimeHealthStatus,
    TimeInForce,
    TraceContext,
    TradeSide,
)
from quant.schemas.base import LayerRejection
from quant.schemas.enums import LayerName
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
        self.capital_allocator = capital_allocator or CapitalAllocator()
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
        quality_payload = {"quality_report": quality_report.to_payload()}
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
                    self._skipped("feature", started_at, "data quality rejected klines"),
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
                {"bar_count": len(klines), "index": index},
                {"snapshot": feature_snapshot.to_payload()},
            )
        )

        regime = self.regime_detector.detect(feature_snapshot)
        stages.append(
            self._stage(
                "regime",
                selected_bar.timestamp,
                {"snapshot_id": feature_snapshot.snapshot_id},
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
                    self._skipped("risk", selected_bar.timestamp, "no decision intent"),
                    self._skipped("portfolio", selected_bar.timestamp, "no risk-approved order"),
                    self._skipped("execution", selected_bar.timestamp, "no order intent"),
                    self._skipped("logging", selected_bar.timestamp, "no trade event to log"),
                ],
                selected_bar.timestamp,
                {"signal": None, "regime": regime.to_payload()},
            )

        decision = self._build_decision(signal, selected_bar, symbol, regime, trace)
        stages.append(
            self._stage(
                "decision",
                selected_bar.timestamp,
                {"signal": signal.to_payload()},
                {"decision": decision.to_payload()},
            )
        )

        risk_input = signal.to_legacy_signal()
        risk_input.update(
            {
                "symbol": symbol,
                "timestamp": selected_bar.timestamp,
                "decision_id": decision.decision_id,
                "client_order_id": f"{run_id}:{symbol}:{index}:{decision.action}",
                "trace": trace,
            }
        )
        risk_decision = self.risk_manager.evaluate(risk_input, self.account, selected_bar.close)
        if not risk_decision.approved:
            stages.append(
                PipelineStageResult(
                    stage="risk",
                    status=PipelineStageStatus.REJECTED,
                    started_at=selected_bar.timestamp,
                    ended_at=selected_bar.timestamp,
                    input_payload={"decision_id": decision.decision_id},
                    output_payload={"risk_decision": risk_decision.to_payload()},
                    rejection=risk_decision.rejections[0],
                )
            )
            return self._finish(
                context,
                stages
                + [
                    self._skipped("portfolio", selected_bar.timestamp, "risk rejected order"),
                    self._skipped("execution", selected_bar.timestamp, "risk rejected order"),
                    self._skipped("logging", selected_bar.timestamp, "risk rejected order"),
                ],
                selected_bar.timestamp,
                {"decision": decision.to_payload(), "risk_decision": risk_decision.to_payload()},
            )

        stages.append(
            self._stage(
                "risk",
                selected_bar.timestamp,
                {"decision_id": decision.decision_id},
                {"risk_decision": risk_decision.to_payload()},
            )
        )

        order_intent = risk_decision.order_intent
        allocation_request = self._build_allocation_request(
            order_intent,
            selected_bar.close,
            selected_bar.timestamp,
            trace,
            signal,
        )
        allocation_decision = self.capital_allocator.allocate(allocation_request)
        portfolio_payload = {
            "allocation_request": allocation_request.to_payload(),
            "allocation_decision": allocation_decision.to_payload(),
            "risk_approved_quantity": order_intent.quantity,
            "client_order_id": order_intent.client_order_id,
        }
        if not allocation_decision.approved:
            stages.append(
                PipelineStageResult(
                    stage="portfolio",
                    status=PipelineStageStatus.REJECTED,
                    started_at=selected_bar.timestamp,
                    ended_at=selected_bar.timestamp,
                    input_payload={"order_intent_id": order_intent.order_intent_id},
                    output_payload=portfolio_payload,
                    rejection=LayerRejection(
                        layer=LayerName.PORTFOLIO,
                        code=allocation_decision.reason_codes[-1],
                        message="portfolio allocation rejected order intent",
                    ),
                )
            )
            return self._finish(
                context,
                stages
                + [
                    self._skipped("execution", selected_bar.timestamp, "portfolio rejected order"),
                    self._skipped("logging", selected_bar.timestamp, "portfolio rejected order"),
                ],
                selected_bar.timestamp,
                {
                    "decision": decision.to_payload(),
                    "risk_decision": risk_decision.to_payload(),
                    "allocation_decision": allocation_decision.to_payload(),
                },
            )

        order_intent = self._order_intent_with_quantity(order_intent, allocation_decision.quantity)
        protective_exit_plan = self._protective_exit_plan_with_quantity(
            risk_decision.protective_exit_plan,
            allocation_decision.quantity,
        )
        portfolio_payload["allocated_order_intent"] = order_intent.to_payload()
        if protective_exit_plan is not None:
            portfolio_payload["allocated_protective_exit_plan"] = protective_exit_plan.to_payload()
        stages.append(
            self._stage(
                "portfolio",
                selected_bar.timestamp,
                {"order_intent_id": order_intent.order_intent_id},
                portfolio_payload,
            )
        )

        execution_result = self.execution_engine.on_order_intent(order_intent, selected_bar.close, index)
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
                "risk_decision": risk_decision.to_payload(),
                "execution_result": execution_result,
            },
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
            close_results.append(self.execution_engine.on_order_intent(order_intent, price, index))
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

    def _build_allocation_request(self, order_intent, price, timestamp, trace, signal):
        requested_notional = order_intent.quantity * price
        target_weight = min(1.0, requested_notional / self.account.equity)
        position = self.account.get_position(order_intent.symbol)
        current_symbol_notional = (
            abs(position.market_value(price))
            if order_intent.side == TradeSide.BUY
            else 0.0
        )
        return CapitalAllocationRequest(
            allocation_id=f"{trace.run_id}:allocation:{order_intent.client_order_id}",
            timestamp=timestamp,
            symbol=order_intent.symbol,
            side=order_intent.side,
            price=price,
            account_equity=self.account.equity,
            available_cash=self.account.balance if order_intent.side == TradeSide.BUY else self.account.equity,
            target_weight=target_weight,
            strategy_weight=max(0.01, min(1.0, signal.confidence or 1.0)),
            current_symbol_notional=current_symbol_notional,
            max_symbol_weight=max(target_weight, getattr(self.risk_manager, "max_position_pct", target_weight)),
            reason_codes=["risk_approved_order"],
            trace=trace,
        )

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
                    )
                )

        for record in records:
            logger.append(record)

        return {"records_written": len(records), "record_types": [record.record_type for record in records]}

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

    def _payload(self, value):
        if hasattr(value, "to_payload"):
            return value.to_payload()
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "dict"):
            return value.dict()
        return value

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
