from quant.orchestration.paper import PaperTradingOrchestrator
from quant.schemas import PipelineStageStatus, StrategyAction, StrategySignal
from quant.strategy.router import RegimeStrategyRouter


def test_paper_orchestrator_skips_decision_risk_and_execution_for_wait_signal():
    strategy = WaitForPullbackStrategy()
    risk = SpyRiskManager()
    execution = SpyExecutionEngine()
    orchestrator = PaperTradingOrchestrator(
        strategy_router=RegimeStrategyRouter(routes={}, fallback=strategy),
        risk_manager=risk,
        execution_engine=execution,
    )

    report = orchestrator.run_tick(
        symbol="BTCUSDT",
        timeframe="5m",
        index=9,
        run_id="h-strat-005-wait-contract",
    )

    stages = {stage.stage: stage for stage in report.stages}

    assert report.success is True
    assert stages["strategy"].output_payload["signal"]["action"] == "wait"
    assert stages["strategy"].output_payload["signal"]["signal_type"] == "WAIT_FOR_PULLBACK"
    assert stages["decision"].status == _value(PipelineStageStatus.SUCCEEDED)
    assert stages["decision"].output_payload["decision_result"]["decision_action"] == "WATCH"
    assert stages["decision"].output_payload["decision_result"]["forward_to_capital_allocation"] is False
    assert stages["risk"].status == _value(PipelineStageStatus.SKIPPED)
    assert stages["portfolio"].status == _value(PipelineStageStatus.SKIPPED)
    assert stages["execution"].status == _value(PipelineStageStatus.SKIPPED)
    assert report.final_output["signal"]["trade_now"] is False
    assert report.final_output["signal"]["should_send_order"] is False
    assert report.final_output["signal"]["watch_plan"]["recheck_on"] == "next_closed_bar"
    assert risk.evaluate_calls == 0
    assert risk.evaluate_v2_calls == 0
    assert execution.order_intent_calls == 0


class WaitForPullbackStrategy:
    strategy_id = "trend_pullback_long_v1"
    strategy_version = "1.0.0"

    def generate_signal(self, _features, index):
        return StrategySignal(
            signal_id=f"{self.strategy_id}:{index}:wait_for_pullback",
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            action=StrategyAction.WAIT,
            signal_type="WAIT_FOR_PULLBACK",
            signal_index=index,
            symbol="BTCUSDT",
            timeframe="5m",
            reason_codes=["UPTREND_HIGH_VOL", "price_too_far_above_ema5"],
            watch_plan={
                "plan_type": "next_bar_recheck",
                "recheck_on": "next_closed_bar",
                "expires_after_bars": 1,
            },
        )


class SpyRiskManager:
    kill_switch_enabled = False

    def __init__(self):
        self.evaluate_calls = 0
        self.evaluate_v2_calls = 0

    def evaluate(self, *_args, **_kwargs):
        self.evaluate_calls += 1
        raise AssertionError("WAIT strategy signal must not enter risk evaluation")

    def evaluate_v2(self, *_args, **_kwargs):
        self.evaluate_v2_calls += 1
        raise AssertionError("WATCH decision must not enter risk v2 evaluation")


class SpyExecutionEngine:
    api_failure_rate = None

    def __init__(self):
        self.order_intent_calls = 0

    def on_order_intent(self, *_args, **_kwargs):
        self.order_intent_calls += 1
        raise AssertionError("WAIT strategy signal must not enter execution")


def _value(value):
    return getattr(value, "value", value)
