import json

from quant.account.capital_allocator import CapitalBudgetAllocator
from quant.decision import DecisionEngine
from quant.execution.broker import BrokerAdapter
from quant.orchestration.paper import PaperTradingOrchestrator
from quant.orchestration.runtime import BrokerExecutionHandler, LiveOrderGate
from quant.risk.risk_manager import RiskManager
from quant.schemas import (
    AssetClass,
    BrokerOrderResult,
    BrokerProtectiveOrderResult,
    CapitalBudgetRequest,
    DecisionEngineRequest,
    DecisionPolicy,
    DecisionPortfolioState,
    MarketType,
    OrderKind,
    OrderStatus,
    PayloadSource,
    PipelineStageStatus,
    PositionSide,
    PortfolioExecutionContext,
    RegimeKind,
    RegimeSnapshot,
    RiskEngineV2Request,
    RiskMarketConstraints,
    RiskPolicy,
    StrategyAction,
    StrategySignal,
    TimeInForce,
    TraceContext,
    TradeSide,
)
from quant.data.schemas.market import Kline


def test_decision_capital_risk_v2_happy_path_preserves_layer_boundaries():
    result = _evaluate_decision_to_risk()

    assert result["decision"].decision_action == "APPROVE_TRADE_INTENT"
    assert result["decision"].forward_to_capital_allocation is True
    assert result["decision"].trade_intent is not None
    assert "quantity" not in result["decision"].trade_intent.to_payload()
    assert result["decision"].safety == {
        "network_used": False,
        "ai_provider_called": False,
        "broker_called": False,
        "live_orders_sent": False,
        "risk_bypassed": False,
    }

    assert result["capital_budget"].approved is True
    assert result["capital_budget"].adjusted_risk_budget_usdt == 80.0
    assert result["capital_budget"].input_refs == {
        "decision_id": result["decision"].trade_intent.decision_id,
        "trade_intent_id": result["decision"].trade_intent.trade_intent_id,
        "source_signal_id": "signal-contract-001",
        "correlation_group": "crypto-major",
    }
    assert result["capital_budget"].safety["order_intent_created"] is False
    assert "order_intent" not in result["capital_budget"].to_payload()
    assert "client_order_id" not in result["capital_budget"].to_payload()

    assert result["risk"].approved is True
    assert result["risk"].sizing.raw_quantity == 0.08
    assert result["risk"].order_intent.quantity == 0.08
    assert result["risk"].execution_order_plan is not None
    assert result["risk"].execution_order_plan.risk_decision_id == result["risk"].risk_decision_id
    assert result["risk"].execution_order_plan.allocation_id == result["capital_budget"].budget_id
    assert result["risk"].execution_order_plan.entry_order.client_order_id == (
        result["risk"].order_intent.client_order_id
    )
    assert result["risk"].sizing.safety == {
        "network_used": False,
        "ai_provider_called": False,
        "broker_called": False,
        "live_orders_sent": False,
        "legacy_signal_used": False,
    }


def test_no_trade_and_watch_stop_before_capital_risk_and_execution():
    for action in (StrategyAction.NO_TRADE, StrategyAction.WAIT):
        decision = DecisionEngine().evaluate(
            _decision_request(
                signal=_signal(
                    action=action,
                    side=None,
                    trade_now=False,
                    should_send_order=False,
                )
            )
        )

        assert decision.decision_action == "WATCH"
        assert decision.forward_to_capital_allocation is False
        assert decision.trade_intent is None
        assert f"strategy_action_{action.value}" in decision.reason_codes

    pipeline = PaperTradingOrchestrator(
        provider=_CrossingProvider(),
        strategy_router=_StaticRouter(_StaticStrategy(_signal(
            action=StrategyAction.WAIT,
            side=None,
            trade_now=False,
            should_send_order=False,
        ))),
        risk_manager=_SpyRiskManager(),
        capital_budget_allocator=_SpyCapitalBudgetAllocator(),
        execution_engine=_SpyExecutionEngine(),
        feature_windows=(2, 3),
    ).run_tick(symbol="BTCUSDT", timeframe="1m", index=4, run_id="qa-025-watch")

    assert _stage(pipeline, "decision").status == PipelineStageStatus.SUCCEEDED
    assert _stage(pipeline, "portfolio").status == PipelineStageStatus.SKIPPED
    assert _stage(pipeline, "risk").status == PipelineStageStatus.SKIPPED
    assert _stage(pipeline, "execution").status == PipelineStageStatus.SKIPPED
    assert pipeline.final_output["decision_result"]["forward_to_capital_allocation"] is False


def test_decision_policy_rejections_do_not_forward_to_capital_or_risk():
    cases = [
        (
            "confidence",
            _decision_request(signal=_signal(confidence=0.25), policy=DecisionPolicy(min_confidence=0.7)),
            "confidence_below_minimum",
        ),
        (
            "regime",
            _decision_request(
                regime_snapshot=_regime(direction="bearish"),
                policy=DecisionPolicy(enforce_regime_alignment=True),
            ),
            "regime_alignment_failed",
        ),
        (
            "position",
            _decision_request(
                portfolio_state=DecisionPortfolioState(
                    symbol="BTCUSDT",
                    position_side=PositionSide.LONG,
                    open_position_quantity=1.0,
                )
            ),
            "position_conflict_long_already_open",
        ),
        (
            "cooldown",
            _decision_request(
                timestamp=1_710_000_005_000,
                portfolio_state=DecisionPortfolioState(
                    symbol="BTCUSDT",
                    position_side=PositionSide.FLAT,
                    last_trade_timestamp=1_710_000_004_500,
                ),
                policy=DecisionPolicy(cooldown_ms=10_000),
            ),
            "cooldown_active",
        ),
        (
            "daily-limit",
            _decision_request(
                portfolio_state=DecisionPortfolioState(
                    symbol="BTCUSDT",
                    position_side=PositionSide.FLAT,
                    trades_today=3,
                ),
                policy=DecisionPolicy(daily_trade_limit=3),
            ),
            "daily_trade_limit_reached",
        ),
    ]

    for _name, request, expected_reason in cases:
        decision = DecisionEngine().evaluate(request)

        assert decision.decision_action == "REJECT"
        assert decision.forward_to_capital_allocation is False
        assert decision.trade_intent is None
        assert expected_reason in decision.reason_codes
        assert decision.safety["broker_called"] is False
        assert decision.safety["live_orders_sent"] is False


def test_capital_budget_scales_by_confidence_volatility_and_correlation():
    trade_intent = _approved_trade_intent(confidence=0.50)
    capital_budget = CapitalBudgetAllocator().allocate(
        _capital_budget_request(
            trade_intent=trade_intent,
            volatility=0.40,
            target_volatility=0.10,
            current_correlation_group_notional=2500.0,
        )
    )

    assert capital_budget.approved is True
    assert capital_budget.base_risk_budget_usdt == 200.0
    assert capital_budget.confidence_multiplier == 0.50
    assert capital_budget.volatility_multiplier == 0.25
    assert capital_budget.correlation_multiplier == 0.50
    assert capital_budget.scaled_risk_budget_usdt == 12.5
    assert capital_budget.adjusted_risk_budget_usdt == 12.5
    assert set(capital_budget.reason_codes) >= {
        "confidence_scaled",
        "volatility_scaled",
        "correlation_exposure_scaled",
        "capital_budget_approved",
    }
    assert capital_budget.safety["broker_called"] is False
    assert capital_budget.safety["order_intent_created"] is False


def test_risk_v2_sizing_caps_and_rejections_are_before_execution_order_creation():
    capped = _evaluate_decision_to_risk(
        current_symbol_notional=2400.0,
        free_margin=100000.0,
        max_symbol_weight=0.25,
    )
    assert capped["risk"].approved is True
    assert capped["risk"].sizing.raw_quantity == 0.08
    assert capped["risk"].sizing.adjusted_quantity == 0.001
    assert capped["risk"].sizing.notional == 65.0
    assert "quantity_capped" in capped["risk"].sizing.reason_codes

    rr_rejected = _evaluate_decision_to_risk(take_profit=65500.0, risk_policy=RiskPolicy(min_risk_reward=1.0))
    assert rr_rejected["risk"].approved is False
    assert rr_rejected["risk"].reason_codes == ["risk_reward_below_minimum"]
    assert rr_rejected["risk"].order_intent is None
    assert rr_rejected["risk"].execution_order_plan is None

    slippage_rejected = _evaluate_decision_to_risk(risk_policy=RiskPolicy(max_slippage_pct=0.02))
    assert slippage_rejected["risk"].approved is False
    assert slippage_rejected["risk"].reason_codes == ["slippage_exceeds_risk_budget"]
    assert slippage_rejected["risk"].order_intent is None
    assert slippage_rejected["risk"].execution_order_plan is None

    margin_rejected = _evaluate_decision_to_risk(free_margin=0.01, min_risk_budget_usdt=0.0)
    assert margin_rejected["risk"].approved is False
    assert margin_rejected["risk"].reason_codes == ["zero_quantity_after_constraints"]
    assert margin_rejected["risk"].order_intent is None
    assert margin_rejected["risk"].execution_order_plan is None

    exchange_rule_rejected = _evaluate_decision_to_risk(min_notional=6000.0)
    assert exchange_rule_rejected["risk"].approved is False
    assert exchange_rule_rejected["risk"].reason_codes == ["notional_below_minimum"]
    assert exchange_rule_rejected["risk"].order_intent is None
    assert exchange_rule_rejected["risk"].execution_order_plan is None


def test_pipeline_report_orders_decision_capital_risk_before_execution():
    report = PaperTradingOrchestrator(
        provider=_CrossingProvider(),
        feature_windows=(2, 3),
    ).run_tick(symbol="BTCUSDT", timeframe="1m", index=4, run_id="qa-025-order")

    assert [stage.stage for stage in report.stages] == [
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
    assert _stage(report, "decision").output_payload["decision_result"]["forward_to_capital_allocation"] is True
    assert _stage(report, "portfolio").output_payload["capital_budget"]["approved"] is True
    assert _stage(report, "risk").output_payload["risk_decision"]["execution_order_plan"] is not None
    assert _stage(report, "risk").output_payload["portfolio_execution_context"]["allocation_id"] == (
        _stage(report, "portfolio").output_payload["capital_budget"]["budget_id"]
    )
    assert _stage(report, "execution").input_payload["order_intent"]["client_order_id"] == (
        _stage(report, "risk").output_payload["risk_decision"]["order_intent"]["client_order_id"]
    )


def test_live_gate_rejection_prevents_bracket_broker_calls(tmp_path):
    evaluated = _evaluate_decision_to_risk()
    order_intent = evaluated["risk"].order_intent
    execution_plan = evaluated["risk"].execution_order_plan
    portfolio_context = PortfolioExecutionContext(
        allocation_id=execution_plan.allocation_id,
        approved=True,
        client_order_id=order_intent.client_order_id,
        risk_decision_id=execution_plan.risk_decision_id,
        symbol=order_intent.symbol,
        side=order_intent.side,
        allocated_quantity=order_intent.quantity,
        allocated_notional=order_intent.quantity * 65000.0,
        reason_codes=["capital_budget_approved", "risk_v2_sized_order"],
        trace=order_intent.trace,
    )
    broker = _RecordingBroker()
    gate = LiveOrderGate(
        {
            "allow_live_orders": True,
            "live_mode": True,
            "dry_run": False,
            "credential_mode": "env",
            "require_manual_preflight": True,
            "preflight_artifact_path": str(_write_preflight_artifact(tmp_path, success=False)),
            "preflight_max_age_seconds": 60,
        },
        risk_manager=_HealthyRiskManager(),
        clock=lambda: 1_700_000_010,
    )
    handler = BrokerExecutionHandler(broker, live_order_gate=gate)

    result = handler.on_execution_order_plan(
        execution_plan,
        price=65000.0,
        index=10,
        portfolio_allocation=portfolio_context,
        dry_run=False,
    )

    assert result["status"] == "rejected"
    assert result["bracket_execution_status"] == "REJECTED"
    assert result["broker_called"] is False
    assert result["live_orders_sent"] is False
    assert "preflight_artifact_not_successful" in result["reason_codes"]
    assert "preflight_checks_failed" in result["reason_codes"]
    assert broker.place_order_calls == []
    assert broker.protective_calls == []


def _evaluate_decision_to_risk(**overrides):
    take_profit = overrides.pop("take_profit", 68000.0)
    signal = _signal(confidence=overrides.pop("confidence", 0.80))
    candidate_order = {
        "entry_price": 65000.0,
        "stop_loss": 64000.0,
        "take_profit": take_profit,
    }
    decision = DecisionEngine().evaluate(
        _decision_request(signal=signal, candidate_order=candidate_order)
    )
    assert decision.trade_intent is not None
    trade_intent = decision.trade_intent
    capital_budget = CapitalBudgetAllocator().allocate(
        _capital_budget_request(
            trade_intent=trade_intent,
            free_margin=overrides.pop("free_margin", 10000.0),
            current_symbol_notional=overrides.pop("current_symbol_notional", 0.0),
            current_total_notional=overrides.pop("current_total_notional", 0.0),
            current_correlation_group_notional=overrides.pop("current_correlation_group_notional", 2500.0),
            max_symbol_weight=overrides.pop("max_symbol_weight", 1.0),
            max_total_weight=overrides.pop("max_total_weight", 1.0),
            max_correlation_group_weight=overrides.pop("max_correlation_group_weight", 1.0),
            min_risk_budget_usdt=overrides.pop("min_risk_budget_usdt", 10.0),
        )
    )
    risk_request = RiskEngineV2Request(
        request_id="risk-v2-contract-request-001",
        timestamp=1_710_000_000_000,
        trade_intent=trade_intent,
        capital_budget=capital_budget,
        market_constraints=RiskMarketConstraints(
            symbol=trade_intent.symbol,
            entry_price=65000.0,
            min_notional=overrides.pop("min_notional", 10.0),
            min_quantity=overrides.pop("min_quantity", 0.0001),
            quantity_step=overrides.pop("quantity_step", 0.001),
            price_tick=overrides.pop("price_tick", None),
            max_leverage=overrides.pop("max_leverage", 3.0),
        ),
        risk_policy=overrides.pop(
            "risk_policy",
            RiskPolicy(
                desired_leverage=overrides.pop("desired_leverage", 1.0),
                max_slippage_pct=overrides.pop("max_slippage_pct", 0.001),
                min_risk_reward=overrides.pop("min_risk_reward", 1.0),
                liquidation_buffer_pct=overrides.pop("liquidation_buffer_pct", 0.01),
            ),
        ),
        trace=trade_intent.trace,
    )
    assert not overrides
    risk = RiskManager().evaluate_v2(risk_request)
    return {
        "decision": decision,
        "capital_budget": capital_budget,
        "risk": risk,
    }


def _decision_request(
    *,
    timestamp=1_710_000_000_000,
    signal=None,
    regime_snapshot=None,
    portfolio_state=None,
    policy=None,
    candidate_order=None,
):
    return DecisionEngineRequest(
        request_id="decision-contract-request-001",
        timestamp=timestamp,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.PERPETUAL,
        timeframe="5m",
        signal=signal or _signal(),
        regime_snapshot=regime_snapshot or _regime(),
        portfolio_state=portfolio_state
        or DecisionPortfolioState(symbol="BTCUSDT", position_side=PositionSide.FLAT),
        policy=policy
        or DecisionPolicy(
            min_confidence=0.60,
            allowed_regimes=["trend"],
            enforce_regime_alignment=True,
        ),
        candidate_order=candidate_order
        or {
            "entry_price": 65000.0,
            "stop_loss": 64000.0,
            "take_profit": 68000.0,
        },
        kill_switch_active=False,
        trace=_trace(),
    )


def _signal(
    *,
    action=StrategyAction.BUY,
    side=TradeSide.BUY,
    confidence=0.80,
    trade_now=True,
    should_send_order=True,
):
    return StrategySignal(
        signal_id="signal-contract-001",
        strategy_id="trend_pullback_long_v1",
        strategy_version="1.0.0",
        side=side,
        action=action,
        signal_type="BREAKOUT_CONFIRMED",
        signal_index=10,
        execute_index=11,
        symbol="BTCUSDT",
        timeframe="5m",
        confidence=confidence,
        reason_codes=["breakout_confirmed"],
        trade_now=trade_now,
        should_send_order=should_send_order,
        trace=_trace(),
    )


def _regime(*, direction="bullish", tradability="tradable", regime=RegimeKind.TREND):
    return RegimeSnapshot(
        regime_id="regime-contract-001",
        timestamp=1_710_000_000_000,
        symbol="BTCUSDT",
        timeframe="5m",
        as_of_timestamp=1_710_000_000_000,
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        regime=regime,
        confidence=0.75,
        reason_codes=["trend_confirmed"],
        direction=direction,
        tradability=tradability,
        trace=_trace(),
    )


def _capital_budget_request(trade_intent, **overrides):
    payload = {
        "budget_id": "capital-budget-contract-001",
        "timestamp": 1_710_000_000_000,
        "trade_intent": trade_intent,
        "account_equity": 10000.0,
        "free_margin": 10000.0,
        "base_risk_budget_pct": 0.02,
        "min_risk_budget_usdt": 10.0,
        "current_symbol_notional": 0.0,
        "current_total_notional": 0.0,
        "current_correlation_group_notional": 2500.0,
        "max_symbol_weight": 1.0,
        "max_total_weight": 1.0,
        "max_correlation_group_weight": 1.0,
        "correlation_group": "crypto-major",
        "reason_codes": ["decision_approved"],
        "trace": trade_intent.trace,
    }
    payload.update(overrides)
    return CapitalBudgetRequest(**payload)


def _approved_trade_intent(**overrides):
    decision = DecisionEngine().evaluate(
        _decision_request(
            signal=_signal(confidence=overrides.pop("confidence", 0.80)),
            policy=DecisionPolicy(
                min_confidence=overrides.pop("min_decision_confidence", 0.0),
                allowed_regimes=["trend"],
                enforce_regime_alignment=True,
            ),
            candidate_order={
                "entry_price": 65000.0,
                "stop_loss": 64000.0,
                "take_profit": overrides.pop("take_profit", 68000.0),
            },
        )
    )
    assert decision.trade_intent is not None
    payload = decision.trade_intent.to_payload()
    payload.update(overrides)
    return decision.trade_intent.__class__.from_payload(payload)


def _trace():
    return TraceContext(
        run_id="qa-025-contract",
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="5m",
        timestamp=1_710_000_000_000,
        bar_index=10,
    )


def _stage(report, name):
    return next(stage for stage in report.stages if stage.stage == name)


class _CrossingProvider:
    def get_klines(self, symbol, timeframe):
        closes = [10.0, 9.0, 8.0, 7.0, 12.0]
        return [
            Kline(
                timestamp=1_700_000_000 + index * 60,
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


class _StaticStrategy:
    strategy_id = "static_wait_strategy"
    strategy_version = "1.0.0"

    def __init__(self, signal):
        self.signal = signal

    def generate_signal(self, features, index):
        return self.signal


class _StaticRouter:
    def __init__(self, strategy):
        self.strategy = strategy

    def route(self, regime):
        return type(
            "Routed",
            (),
            {
                "strategy": self.strategy,
                "route": type(
                    "Route",
                    (),
                    {"to_payload": lambda _self: {"strategy_id": self.strategy.strategy_id}},
                )(),
            },
        )()


class _SpyRiskManager(RiskManager):
    def __init__(self):
        super().__init__()
        self.v2_requests = []

    def evaluate_v2(self, request):
        self.v2_requests.append(request)
        return super().evaluate_v2(request)


class _SpyCapitalBudgetAllocator(CapitalBudgetAllocator):
    def __init__(self):
        self.requests = []

    def allocate(self, request):
        self.requests.append(request)
        return super().allocate(request)


class _SpyExecutionEngine:
    def __init__(self):
        self.order_intents = []
        self.execution_plans = []

    def on_order_intent(self, order_intent, price, index, **kwargs):
        self.order_intents.append(order_intent)
        return {"status": "unexpected"}

    def on_execution_order_plan(self, execution_order_plan, price, index, **kwargs):
        self.execution_plans.append(execution_order_plan)
        return {"status": "unexpected"}


class _HealthyRiskManager:
    kill_switch_enabled = False


class _RecordingBroker(BrokerAdapter):
    name = "recording_broker"

    def __init__(self):
        self.place_order_calls = []
        self.protective_calls = []

    def place_order(self, request):
        self.place_order_calls.append(request)
        return BrokerOrderResult(
            client_order_id=request.client_order_id,
            broker_order_id="broker-entry-1",
            symbol=request.symbol,
            side=request.side,
            status=OrderStatus.FILLED,
            requested_qty=request.quantity,
            filled_qty=request.quantity,
            avg_fill_price=65000.0,
            trace=request.trace,
        )

    def place_native_protective_order(self, request):
        self.protective_calls.append(request)
        return BrokerProtectiveOrderResult(
            protective_client_order_id=request.protective_client_order_id,
            parent_client_order_id=request.parent_client_order_id,
            broker_order_id="broker-protective-1",
            symbol=request.symbol,
            exit_side=request.exit_side(),
            native_order_type=request.metadata.get("native_order_type", "oco"),
            status=OrderStatus.ACCEPTED,
            requested_qty=request.quantity,
            stop_loss_price=request.stop_loss_price,
            take_profit_price=request.take_profit_price,
            stop_loss_client_order_id=request.stop_loss_client_order_id,
            take_profit_client_order_id=request.take_profit_client_order_id,
            live_order_gate=request.live_order_gate,
            trace=request.trace,
            metadata=request.metadata,
        )

    def cancel_order(self, client_order_id):
        raise AssertionError("cancel_order should not be called")

    def replace_order(self, request):
        raise AssertionError("replace_order should not be called")

    def get_order(self, client_order_id):
        raise AssertionError("get_order should not be called")

    def list_open_orders(self, symbol=None):
        return []


def _write_preflight_artifact(tmp_path, *, success):
    artifact_path = tmp_path / "latest-preflight.json"
    artifact_path.write_text(
        json.dumps(
            {
                "report_id": "qa-025-preflight",
                "generated_at": 1_700_000_000,
                "success": success,
                "preflight_summary": {"failed_count": 0 if success else 1},
                "metadata": {
                    "live_orders_sent": False,
                    "contains_real_credentials": False,
                },
                "checks": [],
            }
        ),
        encoding="utf-8",
    )
    return artifact_path
