import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from quant.schemas import (
    AssetClass,
    BrokerProtectiveOrderRequest,
    BrokerProtectiveOrderResult,
    DecisionAction,
    DecisionIntent,
    DecisionStopLossTarget,
    DecisionTakeProfitTarget,
    ExecutionFillEvent,
    FeatureSnapshot,
    LayerName,
    MarketType,
    LayerRejection,
    NetflowSnapshot,
    OpenInterestSnapshot,
    OrderIntent,
    OrderKind,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderStatus,
    PayloadSource,
    ProtectiveExitPlan,
    ProtectiveExitTriggerEvent,
    PipelineBatchRunReport,
    PipelineRunContext,
    PipelineRunReport,
    PipelineStageResult,
    PipelineStageStatus,
    PipelineSymbolRunRequest,
    RiskDecision,
    RuntimeHealthSnapshot,
    RuntimeHealthStatus,
    TimeInForce,
    TraceContext,
    TradeSide,
    LiveOrderGateDecision,
)


def test_trace_context_round_trip():
    context = TraceContext(
        run_id="bt-001",
        source=PayloadSource.BACKTEST,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1700000000,
        bar_index=12,
    )

    payload = context.to_payload()
    restored = TraceContext.from_payload(payload)

    assert payload == {
        "schema_version": "1.0",
        "run_id": "bt-001",
        "source": "backtest",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "timestamp": 1700000000,
        "bar_index": 12,
    }
    assert restored == context


def test_layer_rejection_round_trip():
    rejection = LayerRejection(
        layer=LayerName.RISK,
        code="max_position",
        message="position limit exceeded",
        fatal=False,
    )

    restored = LayerRejection.from_payload(rejection.to_payload())

    assert restored.layer == LayerName.RISK
    assert restored.code == "max_position"
    assert restored.fatal is False


def test_pipeline_run_report_round_trip():
    context = PipelineRunContext(
        run_id="paper-001",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        started_at=1710000000,
    )
    data_stage = PipelineStageResult(
        stage="data",
        status=PipelineStageStatus.SUCCEEDED,
        started_at=1710000000,
        ended_at=1710000001,
        output_payload={"bars": 120},
    )
    risk_stage = PipelineStageResult(
        stage="risk",
        status=PipelineStageStatus.REJECTED,
        started_at=1710000002,
        ended_at=1710000003,
        rejection=LayerRejection(
            layer=LayerName.RISK,
            code="kill_switch",
            message="new orders disabled",
            fatal=True,
        ),
    )
    report = PipelineRunReport(
        context=context,
        stages=[data_stage, risk_stage],
        finished_at=1710000003,
        success=False,
        final_output={"order_created": False},
    )

    payload = report.to_payload()
    restored = PipelineRunReport.from_payload(payload)

    assert payload["context"]["source"] == "paper"
    assert payload["stages"][0]["status"] == "succeeded"
    assert payload["stages"][1]["rejection"]["code"] == "kill_switch"
    assert restored.to_payload() == payload


def test_pipeline_stage_rejected_requires_rejection():
    try:
        PipelineStageResult(
            stage="risk",
            status=PipelineStageStatus.REJECTED,
            started_at=1710000000,
            ended_at=1710000001,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("rejected pipeline stages must include rejection detail")


def test_pipeline_batch_run_report_round_trip():
    request = PipelineSymbolRunRequest(symbol="BTCUSDT", timeframe="1m", index=3)
    report = PipelineRunReport(
        context=PipelineRunContext(
            run_id="batch-001:0:BTCUSDT:1m",
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            started_at=1710000000,
        ),
        stages=[
            PipelineStageResult(
                stage="data",
                status=PipelineStageStatus.SUCCEEDED,
                started_at=1710000000,
                ended_at=1710000000,
            )
        ],
        finished_at=1710000000,
        success=True,
    )
    batch = PipelineBatchRunReport(
        batch_id="batch-001",
        source=PayloadSource.PAPER,
        requested_at=1710000000,
        requests=[request],
        reports=[report],
        success=True,
    )

    payload = batch.to_payload()
    restored = PipelineBatchRunReport.from_payload(payload)

    assert payload["requests"][0]["symbol"] == "BTCUSDT"
    assert payload["reports"][0]["context"]["run_id"] == "batch-001:0:BTCUSDT:1m"
    assert restored.to_payload() == payload


def test_pipeline_batch_rejects_duplicate_symbol_timeframe_requests():
    report = PipelineRunReport(
        context=PipelineRunContext(
            run_id="batch-001:0:BTCUSDT:1m",
            source=PayloadSource.PAPER,
            symbol="BTCUSDT",
            timeframe="1m",
            started_at=1710000000,
        ),
        stages=[
            PipelineStageResult(
                stage="data",
                status=PipelineStageStatus.SUCCEEDED,
                started_at=1710000000,
                ended_at=1710000000,
            )
        ],
        finished_at=1710000000,
        success=True,
    )
    try:
        PipelineBatchRunReport(
            batch_id="batch-001",
            source=PayloadSource.PAPER,
            requested_at=1710000000,
            requests=[
                PipelineSymbolRunRequest(symbol="BTCUSDT", timeframe="1m"),
                PipelineSymbolRunRequest(symbol="BTCUSDT", timeframe="1m"),
            ],
            reports=[report, report],
            success=True,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("batch requests must not contain duplicate symbol/timeframe entries")


def test_successful_pipeline_report_rejects_error_stage():
    try:
        PipelineRunReport(
            context=PipelineRunContext(
                run_id="paper-001",
                source=PayloadSource.PAPER,
                symbol="BTCUSDT",
                timeframe="1m",
                started_at=1710000000,
            ),
            stages=[
                PipelineStageResult(
                    stage="execution",
                    status=PipelineStageStatus.ERROR,
                    started_at=1710000001,
                    ended_at=1710000002,
                    error="broker timeout",
                )
            ],
            finished_at=1710000002,
            success=True,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("successful pipeline reports must not include error stages")


def test_runtime_health_snapshot_round_trip():
    snapshot = RuntimeHealthSnapshot(
        run_id="paper-001",
        source=PayloadSource.PAPER,
        observed_at=1710000000,
        status=RuntimeHealthStatus.DEGRADED,
        symbol="BTCUSDT",
        timeframe="1m",
        data_latency_ms=1200,
        pipeline_stage_durations_ms={"data": 20, "risk": 5, "execution": 11},
        order_failure_rate=0.1,
        risk_rejection_rate=0.25,
        broker_reconciliation_anomalies=1,
        kill_switch_active=False,
        alerts=["broker reconciliation anomaly detected"],
    )

    payload = snapshot.to_payload()
    restored = RuntimeHealthSnapshot.from_payload(payload)

    assert payload["status"] == "degraded"
    assert payload["source"] == "paper"
    assert payload["pipeline_stage_durations_ms"]["risk"] == 5
    assert restored.to_payload() == payload


def test_runtime_health_snapshot_rejects_invalid_rates_and_durations():
    try:
        RuntimeHealthSnapshot(
            run_id="paper-001",
            observed_at=1710000000,
            status=RuntimeHealthStatus.DEGRADED,
            data_latency_ms=-1,
            pipeline_stage_durations_ms={"risk": -5},
            order_failure_rate=1.2,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("runtime health metrics must reject invalid numeric bounds")


def test_healthy_runtime_health_snapshot_rejects_active_kill_switch():
    try:
        RuntimeHealthSnapshot(
            run_id="paper-001",
            observed_at=1710000000,
            status=RuntimeHealthStatus.HEALTHY,
            kill_switch_active=True,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("healthy snapshots cannot have kill switch active")


def test_critical_runtime_health_snapshot_requires_alert():
    try:
        RuntimeHealthSnapshot(
            run_id="paper-001",
            observed_at=1710000000,
            status=RuntimeHealthStatus.CRITICAL,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("critical snapshots require at least one alert")


def test_shared_enum_rejects_unknown_values():
    try:
        TradeSide("hold")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown trade side should be rejected")


def test_trace_context_requires_symbol():
    try:
        TraceContext(run_id="bt-001")
    except ValidationError:
        pass
    else:
        raise AssertionError("symbol is required for replayable trace context")


def test_decision_intent_round_trip():
    decision = DecisionIntent(
        decision_id="decision-001",
        timestamp=1710000000,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        regime="trend_up",
        action=DecisionAction.OPEN_LONG,
        order_type=OrderKind.LIMIT,
        quantity=0.01,
        limit_price=65000.0,
        stop_loss=64000.0,
        take_profit=67500.0,
        time_in_force=TimeInForce.GTC,
        confidence=0.62,
        reason_codes=["ma_cross_up", "trend_filter_passed"],
        trace=TraceContext(
            run_id="bt-001",
            source=PayloadSource.BACKTEST,
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=1710000000,
            bar_index=42,
        ),
    )

    payload = decision.to_payload()
    restored = DecisionIntent.from_payload(payload)

    assert payload["asset_class"] == "crypto"
    assert payload["market_type"] == "spot"
    assert payload["action"] == "open_long"
    assert payload["order_type"] == "limit"
    assert payload["time_in_force"] == "gtc"
    assert payload["trace"]["run_id"] == "bt-001"
    assert restored == decision


def test_decision_intent_supports_bracket_targets_and_order_intent_mapping():
    decision = DecisionIntent(
        decision_id="decision-002",
        timestamp=1710000060,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.PERPETUAL,
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        action=DecisionAction.CLOSE_SHORT,
        order_type=OrderKind.LIMIT,
        quantity=0.02,
        limit_price=64500.0,
        stop_loss_targets=[
            DecisionStopLossTarget(price=66000.0, quantity_pct=0.5, reason_code="risk_half"),
            DecisionStopLossTarget(price=67000.0, quantity_pct=0.5, reason_code="risk_full"),
        ],
        take_profit_targets=[
            DecisionTakeProfitTarget(price=63000.0, quantity_pct=0.4, reason_code="tp_1"),
            DecisionTakeProfitTarget(price=62000.0, quantity_pct=0.6, reason_code="tp_2"),
        ],
        time_in_force=TimeInForce.GTC,
        reduce_only=True,
    )

    payload = decision.to_payload()
    restored = DecisionIntent.from_payload(payload)
    order_intent = decision.to_order_intent(client_order_id="client-close-short-001")

    assert payload["market_type"] == "perpetual"
    assert payload["stop_loss_targets"][0]["quantity_pct"] == 0.5
    assert restored == decision
    assert order_intent.client_order_id == "client-close-short-001"
    assert order_intent.side == TradeSide.BUY
    assert order_intent.reduce_only is True
    assert order_intent.limit_price == 64500.0


def test_decision_intent_rejects_target_quantity_pct_over_allocation():
    try:
        DecisionIntent(
            decision_id="decision-003",
            timestamp=1710000060,
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            strategy_id="ma_crossover",
            strategy_version="1.0.0",
            action=DecisionAction.OPEN_LONG,
            order_type=OrderKind.MARKET,
            quantity=0.02,
            take_profit_targets=[
                DecisionTakeProfitTarget(price=66000.0, quantity_pct=0.75),
                DecisionTakeProfitTarget(price=67000.0, quantity_pct=0.50),
            ],
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("decision target quantity_pct allocation must not exceed 1.0")


def test_decision_intent_rejects_invalid_quantity():
    try:
        DecisionIntent(
            decision_id="decision-001",
            timestamp=1710000000,
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            strategy_id="ma_crossover",
            strategy_version="1.0.0",
            action=DecisionAction.OPEN_LONG,
            order_type=OrderKind.MARKET,
            quantity=0.0,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("decision quantity must be positive")


def test_limit_decision_intent_requires_limit_price():
    try:
        DecisionIntent(
            decision_id="decision-001",
            timestamp=1710000000,
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            strategy_id="ma_crossover",
            strategy_version="1.0.0",
            action=DecisionAction.OPEN_LONG,
            order_type=OrderKind.LIMIT,
            quantity=0.01,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("limit decision must include limit_price")


def test_order_intent_round_trip():
    intent = OrderIntent(
        order_intent_id="order-intent-001",
        decision_id="decision-001",
        client_order_id="smartqtf-BTCUSDT-1710000000-0001",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.LIMIT,
        quantity=0.01,
        limit_price=65000.0,
        time_in_force=TimeInForce.GTC,
        reduce_only=False,
        post_only=False,
        risk_approved=True,
        created_at=1710000000,
        trace=TraceContext(
            run_id="bt-001",
            source=PayloadSource.BACKTEST,
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=1710000000,
            bar_index=42,
        ),
    )

    payload = intent.to_payload()
    restored = OrderIntent.from_payload(payload)

    assert payload["side"] == "buy"
    assert payload["order_type"] == "limit"
    assert payload["time_in_force"] == "gtc"
    assert payload["risk_approved"] is True
    assert restored == intent


def test_order_intent_rejects_unapproved_risk():
    try:
        OrderIntent(
            order_intent_id="order-intent-001",
            decision_id="decision-001",
            client_order_id="smartqtf-BTCUSDT-1710000000-0001",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.MARKET,
            quantity=0.01,
            risk_approved=False,
            created_at=1710000000,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("order intent must require risk approval")


def test_limit_order_intent_requires_limit_price():
    try:
        OrderIntent(
            order_intent_id="order-intent-001",
            decision_id="decision-001",
            client_order_id="smartqtf-BTCUSDT-1710000000-0001",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            order_type=OrderKind.LIMIT,
            quantity=0.01,
            risk_approved=True,
            created_at=1710000000,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("limit order intent must include limit_price")


def test_execution_fill_event_round_trip():
    event = ExecutionFillEvent(
        fill_event_id="fill-001",
        client_order_id="client-001",
        broker_order_id="broker-001",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        status=OrderStatus.PARTIAL,
        fill_qty=0.25,
        fill_price=65000.0,
        cumulative_filled_qty=0.25,
        remaining_qty=0.75,
        fill_index=42,
    )

    payload = event.to_payload()
    restored = ExecutionFillEvent.from_payload(payload)

    assert payload["side"] == "buy"
    assert payload["status"] == "partial"
    assert restored == event


def test_execution_fill_event_rejects_invalid_quantities():
    try:
        ExecutionFillEvent(
            fill_event_id="fill-001",
            client_order_id="client-001",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            status=OrderStatus.PARTIAL,
            fill_qty=0.5,
            fill_price=65000.0,
            cumulative_filled_qty=0.25,
            remaining_qty=0.75,
            fill_index=42,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("fill_qty cannot exceed cumulative_filled_qty")


def test_risk_decision_can_use_typed_order_intent():
    intent = OrderIntent(
        order_intent_id="order-intent-001",
        decision_id="decision-001",
        client_order_id="client-001",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type=OrderKind.MARKET,
        quantity=0.01,
        risk_approved=True,
        created_at=1710000000,
    )

    decision = RiskDecision.approve(
        order_payload=None,
        order_intent=intent,
        reason_codes=["valid_signal"],
    )

    assert decision.approved is True
    assert decision.order_payload is None
    assert decision.order_intent == intent


def test_feature_snapshot_round_trip():
    snapshot = FeatureSnapshot(
        snapshot_id="features-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000060,
        feature_set_id="technical_v1",
        feature_set_version="1.0.0",
        values={
            "ma_fast": 65010.0,
            "ma_slow": 64980.0,
            "cross_up": True,
        },
        source_window_start=1710000000,
        source_window_end=1710000060,
        trace=TraceContext(
            run_id="bt-001",
            source=PayloadSource.BACKTEST,
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=1710000060,
            bar_index=43,
        ),
    )

    payload = snapshot.to_payload()
    restored = FeatureSnapshot.from_payload(payload)

    assert payload["values"]["ma_fast"] == 65010.0
    assert payload["values"]["cross_up"] is True
    assert snapshot.feature_names == ["cross_up", "ma_fast", "ma_slow"]
    assert restored == snapshot


def test_feature_snapshot_rejects_future_as_of_timestamp():
    try:
        FeatureSnapshot(
            snapshot_id="features-001",
            timestamp=1710000060,
            symbol="BTCUSDT",
            timeframe="1m",
            as_of_timestamp=1710000120,
            feature_set_id="technical_v1",
            feature_set_version="1.0.0",
            values={"ma_fast": 65010.0},
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("feature snapshot must not read future data")


def test_feature_snapshot_requires_named_values():
    try:
        FeatureSnapshot(
            snapshot_id="features-001",
            timestamp=1710000060,
            symbol="BTCUSDT",
            timeframe="1m",
            as_of_timestamp=1710000060,
            feature_set_id="technical_v1",
            feature_set_version="1.0.0",
            values={"": 65010.0},
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("feature snapshot must reject empty feature names")


def test_feature_snapshot_aligns_feature_series_at_index():
    snapshot = FeatureSnapshot.from_feature_series(
        {
            "fast_ma": [None, 100.0, 102.0],
            "slow_ma": [None, 101.0, 101.5],
        },
        2,
        snapshot_id="features-002",
        timestamp=1710000120,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000120,
        feature_set_id="technical_v1",
        feature_set_version="1.0.0",
    )

    assert snapshot.values == {
        "fast_ma": 102.0,
        "slow_ma": 101.5,
    }
    assert snapshot.feature_names == ["fast_ma", "slow_ma"]


def test_feature_snapshot_rejects_out_of_range_series_index():
    try:
        FeatureSnapshot.from_feature_series(
            {"fast_ma": [None, 100.0]},
            2,
            snapshot_id="features-002",
            timestamp=1710000120,
            symbol="BTCUSDT",
            timeframe="1m",
            as_of_timestamp=1710000120,
            feature_set_id="technical_v1",
            feature_set_version="1.0.0",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("feature snapshot must reject out of range feature indexes")


def test_open_interest_snapshot_round_trip():
    snapshot = OpenInterestSnapshot(
        snapshot_id="oi-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        venue="binance",
        as_of_timestamp=1710000060,
        open_interest=12500.5,
        open_interest_value=812532500.0,
        funding_rate=0.0001,
        next_funding_timestamp=1710028800,
        trace=TraceContext(
            run_id="bt-001",
            source=PayloadSource.BACKTEST,
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=1710000060,
            bar_index=43,
        ),
    )

    payload = snapshot.to_payload()
    restored = OpenInterestSnapshot.from_payload(payload)

    assert payload["venue"] == "binance"
    assert payload["open_interest"] == 12500.5
    assert restored == snapshot


def test_open_interest_snapshot_rejects_negative_interest():
    try:
        OpenInterestSnapshot(
            snapshot_id="oi-001",
            timestamp=1710000060,
            symbol="BTCUSDT",
            venue="binance",
            as_of_timestamp=1710000060,
            open_interest=-1.0,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("open interest snapshot must reject negative interest")


def test_netflow_snapshot_round_trip_and_computes_netflow():
    snapshot = NetflowSnapshot(
        snapshot_id="flow-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        venue="binance",
        timeframe="1h",
        as_of_timestamp=1710000060,
        inflow=120.0,
        outflow=75.0,
        netflow=45.0,
        exchange_reserve=25000.0,
        large_transfer_count=3,
    )

    restored = NetflowSnapshot.from_payload(snapshot.to_payload())

    assert snapshot.computed_netflow == 45.0
    assert restored == snapshot


def test_netflow_snapshot_rejects_mismatched_netflow():
    try:
        NetflowSnapshot(
            snapshot_id="flow-001",
            timestamp=1710000060,
            symbol="BTCUSDT",
            venue="binance",
            timeframe="1h",
            as_of_timestamp=1710000060,
            inflow=120.0,
            outflow=75.0,
            netflow=10.0,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("netflow snapshot must match inflow - outflow")


def test_order_book_snapshot_round_trip():
    snapshot = OrderBookSnapshot(
        snapshot_id="book-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        venue="binance",
        as_of_timestamp=1710000060,
        bids=[
            OrderBookLevel(price=64999.0, quantity=1.2),
            OrderBookLevel(price=64998.5, quantity=0.8),
        ],
        asks=[
            OrderBookLevel(price=65000.5, quantity=1.0),
            OrderBookLevel(price=65001.0, quantity=0.9),
        ],
        depth=2,
    )

    payload = snapshot.to_payload()
    restored = OrderBookSnapshot.from_payload(payload)

    assert snapshot.best_bid == 64999.0
    assert snapshot.best_ask == 65000.5
    assert payload["bids"][0]["price"] == 64999.0
    assert restored == snapshot


def test_order_book_snapshot_rejects_crossed_book():
    try:
        OrderBookSnapshot(
            snapshot_id="book-001",
            timestamp=1710000060,
            symbol="BTCUSDT",
            venue="binance",
            as_of_timestamp=1710000060,
            bids=[OrderBookLevel(price=65001.0, quantity=1.2)],
            asks=[OrderBookLevel(price=65000.5, quantity=1.0)],
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("order book snapshot must reject crossed books")


def test_order_book_snapshot_rejects_unsorted_levels():
    try:
        OrderBookSnapshot(
            snapshot_id="book-001",
            timestamp=1710000060,
            symbol="BTCUSDT",
            venue="binance",
            as_of_timestamp=1710000060,
            bids=[
                OrderBookLevel(price=64998.5, quantity=0.8),
                OrderBookLevel(price=64999.0, quantity=1.2),
            ],
            asks=[OrderBookLevel(price=65000.5, quantity=1.0)],
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("order book snapshot must reject unsorted levels")


def test_risk_decision_round_trip():
    decision = RiskDecision.approve(
        order_payload={
            "signal": "buy",
            "symbol": "BTCUSDT",
            "quantity": 10.0,
            "stop_loss": 98.0,
        },
        reason_codes=["valid_signal", "position_sizing"],
    )

    payload = decision.to_payload()
    restored = RiskDecision.from_payload(payload)

    assert payload["approved"] is True
    assert payload["order_payload"]["quantity"] == 10.0
    assert restored == decision


def test_protective_exit_plan_round_trip():
    plan = ProtectiveExitPlan(
        exit_plan_id="protective-exit-001",
        parent_client_order_id="entry-001",
        symbol="BTCUSDT",
        entry_side=TradeSide.BUY,
        quantity=1.0,
        stop_loss_price=98.0,
        take_profit_price=104.0,
        created_at=1710000000,
    )

    payload = plan.to_payload()
    restored = ProtectiveExitPlan.from_payload(payload)

    assert payload["entry_side"] == "buy"
    assert payload["stop_loss_price"] == 98.0
    assert restored == plan


def test_protective_exit_plan_rejects_reversed_long_targets():
    try:
        ProtectiveExitPlan(
            exit_plan_id="protective-exit-001",
            parent_client_order_id="entry-001",
            symbol="BTCUSDT",
            entry_side=TradeSide.BUY,
            quantity=1.0,
            stop_loss_price=104.0,
            take_profit_price=98.0,
            created_at=1710000000,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("long protective exit plan must reject reversed prices")


def test_broker_protective_order_request_round_trips_live_gate_context():
    gate = LiveOrderGateDecision(
        approved=True,
        reason_codes=["live_order_gate_approved"],
        message="live order gate approved",
        checked_at=1710000000,
        allow_live_orders=True,
    )
    request = BrokerProtectiveOrderRequest(
        protective_client_order_id="protective-001",
        parent_client_order_id="entry-001",
        symbol="BTCUSDT",
        entry_side=TradeSide.BUY,
        quantity=1.0,
        stop_loss_price=98.0,
        take_profit_price=104.0,
        stop_loss_client_order_id="protective-001-sl",
        take_profit_client_order_id="protective-001-tp",
        live_order_gate=gate,
    )

    payload = request.to_payload()
    restored = BrokerProtectiveOrderRequest.from_payload(payload)

    assert payload["live_order_gate"]["approved"] is True
    assert request.exit_side() == TradeSide.SELL
    assert restored == request


def test_broker_protective_order_request_rejects_reversed_short_targets():
    gate = LiveOrderGateDecision(
        approved=True,
        reason_codes=["live_order_gate_approved"],
        message="live order gate approved",
        checked_at=1710000000,
        allow_live_orders=True,
    )

    try:
        BrokerProtectiveOrderRequest(
            protective_client_order_id="protective-001",
            parent_client_order_id="entry-001",
            symbol="BTCUSDT",
            entry_side=TradeSide.SELL,
            quantity=1.0,
            stop_loss_price=98.0,
            take_profit_price=104.0,
            live_order_gate=gate,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("short native protective order must reject reversed prices")


def test_broker_protective_order_result_round_trips_native_oco_status():
    gate = LiveOrderGateDecision(
        approved=True,
        reason_codes=["live_order_gate_approved"],
        message="live order gate approved",
        checked_at=1710000000,
        allow_live_orders=True,
    )
    result = BrokerProtectiveOrderResult(
        protective_client_order_id="protective-001",
        parent_client_order_id="entry-001",
        broker_order_id="oco-001",
        symbol="BTCUSDT",
        exit_side=TradeSide.SELL,
        native_order_type="oco",
        status=OrderStatus.ACCEPTED,
        requested_qty=1.0,
        stop_loss_price=98.0,
        take_profit_price=104.0,
        live_order_gate=gate,
    )

    payload = result.to_payload()
    restored = BrokerProtectiveOrderResult.from_payload(payload)

    assert payload["native_order_type"] == "oco"
    assert payload["exit_side"] == "sell"
    assert restored == result


def test_protective_exit_trigger_event_round_trip():
    order_intent = OrderIntent(
        order_intent_id="exit-intent-001",
        decision_id="protective-exit:001",
        client_order_id="exit-001",
        symbol="BTCUSDT",
        side=TradeSide.SELL,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        time_in_force=TimeInForce.GTC,
        reduce_only=True,
        risk_approved=True,
        created_at=1710000060,
    )
    event = ProtectiveExitTriggerEvent(
        trigger_event_id="trigger-001",
        exit_plan_id="protective-exit-001",
        parent_client_order_id="entry-001",
        symbol="BTCUSDT",
        trigger_type="stop_loss",
        trigger_price=98.0,
        market_price=97.5,
        quantity=1.0,
        exit_side=TradeSide.SELL,
        triggered_at=1710000060,
        order_intent=order_intent,
    )

    payload = event.to_payload()
    restored = ProtectiveExitTriggerEvent.from_payload(payload)

    assert payload["order_intent"]["reduce_only"] is True
    assert restored == event


def test_risk_decision_rejects_approved_without_order_payload():
    try:
        RiskDecision(approved=True, reason_codes=["position_sizing"])
    except ValidationError:
        pass
    else:
        raise AssertionError("approved risk decision must include order_payload")
