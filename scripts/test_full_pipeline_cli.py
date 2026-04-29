import json
import sys
from pathlib import Path

import typer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.data.schemas.market import Kline
from quant.orchestration import PaperTradingOrchestrator


app = typer.Typer()

SYMBOL = "BTCUSDT"
TIMEFRAME = "1m"
PRICES = [10.0, 9.0, 8.0, 7.0, 12.0, 13.0]
FEATURE_WINDOWS = (2, 3)


class DemoProvider:
    def get_klines(self, symbol, timeframe):
        return [
            Kline(
                timestamp=1700000000 + index * 60,
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1000.0 + index,
            )
            for index, close in enumerate(PRICES)
        ]

    def get_trades(self, symbol):
        return []


def as_payload(value):
    if value is None:
        return None
    if hasattr(value, "to_payload"):
        return value.to_payload()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return dict(value)
    return value


def pretty_json(value):
    return json.dumps(as_payload(value), ensure_ascii=False, indent=2, sort_keys=True)


def fmt(value, digits=6):
    if value is None:
        return "None"
    rounded = round(float(value), digits)
    if rounded == 0:
        return "0"
    if rounded.is_integer():
        return str(int(rounded))
    return str(rounded)


def stage_map(report):
    return {stage.stage: stage for stage in report.stages}


def stage_output(stages, name, key=None):
    stage = stages.get(name)
    if stage is None:
        return None
    output = stage.output_payload
    if key is None:
        return output
    return output.get(key)


def stage_input(stages, name, key=None):
    stage = stages.get(name)
    if stage is None:
        return None
    payload = stage.input_payload
    if key is None:
        return payload
    return payload.get(key)


def status_value(status):
    return status.value if hasattr(status, "value") else status


def report_to_row(report, index):
    stages = stage_map(report)
    selected_bar = stage_input(stages, "data", "selected_bar") or {}
    feature_snapshot = stage_output(stages, "feature", "snapshot") or {}
    strategy_output = stage_output(stages, "strategy") or {}
    signal = strategy_output.get("signal")
    route = strategy_output.get("route")
    regime = stage_output(stages, "regime", "regime")
    decision = stage_output(stages, "decision", "decision")
    risk_decision = stage_output(stages, "risk", "risk_decision")
    order_intent = None if risk_decision is None else risk_decision.get("order_intent")
    portfolio = stage_output(stages, "portfolio")
    execution_result = stage_output(stages, "execution", "execution_result")
    logging_output = stage_output(stages, "logging")

    if execution_result is not None:
        event = execution_result.get("side", "order")
    elif risk_decision is not None and not risk_decision.get("approved"):
        event = "risk_rejected"
    elif signal is not None:
        event = signal.get("side", "signal")
    else:
        event = "hold"

    return {
        "bar": index,
        "timestamp": report.context.started_at,
        "price": selected_bar.get("close"),
        "event": event,
        "stage_statuses": {stage.stage: status_value(stage.status) for stage in report.stages},
        "pipeline_report_json": report.to_payload(),
        "market_note": build_market_note(index, selected_bar, feature_snapshot),
        "user_note": "用户看到的是 Orchestrator 运行报告，而不是 CLI 或 Dashboard 各自手写流程。",
        "software_note": build_software_note(report),
        "teacher_note": build_teacher_note(report),
        "missing": build_gap_note(report),
        "feature_json": feature_snapshot,
        "regime_json": regime,
        "route_json": route,
        "generated_signal_json": signal,
        "decision_json": decision,
        "risk_decision_json": risk_decision,
        "order_intent_json": order_intent,
        "portfolio_json": portfolio,
        "execution_result_json": execution_result,
        "logging_json": logging_output,
        "account_json": {
            "equity": None if portfolio is None else portfolio.get("account_equity"),
            "balance": None if portfolio is None else portfolio.get("available_cash"),
            "position_size": None,
            "avg_price": None,
            "realized_pnl": None,
            "unrealized_pnl": None,
        },
    }


def build_market_note(index, selected_bar, feature_snapshot):
    close = selected_bar.get("close")
    features = feature_snapshot.get("features", {})
    fast_ma = features.get("fast_ma")
    slow_ma = features.get("slow_ma")
    if close is None:
        return f"第 {index} 根K线没有可展示价格。"
    if fast_ma is None or slow_ma is None:
        return f"第 {index} 根K线 close={fmt(close)}，均线窗口尚未完整。"
    if fast_ma > slow_ma:
        relation = "快线在慢线上方，趋势输入偏强。"
    elif fast_ma < slow_ma:
        relation = "快线在慢线下方，趋势输入偏弱。"
    else:
        relation = "快线和慢线相等，策略继续观察。"
    return f"第 {index} 根K线 close={fmt(close)}，fast_ma={fmt(fast_ma)}，slow_ma={fmt(slow_ma)}。{relation}"


def build_software_note(report):
    stages = []
    for stage in report.stages:
        status = status_value(stage.status)
        if status == "skipped":
            stages.append(f"{stage.stage}=skipped({stage.skip_reason})")
        else:
            stages.append(f"{stage.stage}={status}")
    return "Orchestrator 已生成 PipelineRunReport：" + " -> ".join(stages)


def build_teacher_note(report):
    stages = stage_map(report)
    execution_result = stage_output(stages, "execution", "execution_result")
    risk_decision = stage_output(stages, "risk", "risk_decision")
    signal = (stage_output(stages, "strategy") or {}).get("signal")
    if execution_result is not None:
        return "本根K线从策略信号一路通过风控、组合和执行，完整链路都来自同一份运行报告。"
    if risk_decision is not None and not risk_decision.get("approved"):
        return "策略提出交易，但风控拒绝，后续组合和执行阶段被跳过。"
    if signal is not None:
        return "策略生成了信号；是否下单继续由后续决策、风控和执行阶段决定。"
    return "策略没有生成信号，Orchestrator 明确把决策、风控、组合、执行和日志阶段标记为 skipped。"


def build_gap_note(report):
    gaps = [
        "当前仍是教学数据，不是真实 Binance/OKX 行情。",
        "Dashboard 和 CLI 已读取同一份 PipelineRunReport，但还没有运行配置层。",
        "执行层仍是模拟成交，真实 Broker Adapter、限流和交易所回报尚未接入。",
    ]
    if not report.success:
        gaps.append("本次运行报告包含错误，需要先处理错误阶段。")
    return gaps


def build_reports():
    orchestrator = PaperTradingOrchestrator(provider=DemoProvider(), feature_windows=FEATURE_WINDOWS)
    return [
        orchestrator.run_tick(symbol=SYMBOL, timeframe=TIMEFRAME, index=index, run_id=f"demo-{index}")
        for index in range(len(PRICES))
    ]


def run_pipeline(verbose=True):
    reports = build_reports()
    rows = [report_to_row(report, index) for index, report in enumerate(reports)]
    executed = [row for row in rows if row["execution_result_json"] is not None]
    rejected = [row for row in rows if row["risk_decision_json"] is not None and not row["risk_decision_json"].get("approved")]
    skipped_execution = [
        row
        for row in rows
        if row["stage_statuses"].get("execution") == "skipped"
    ]
    summary = {
        "bars": len(rows),
        "reports": len(reports),
        "successful_reports": sum(1 for report in reports if report.success),
        "executed_orders": len(executed),
        "risk_rejections": len(rejected),
        "execution_skipped": len(skipped_execution),
        "report_source": "PaperTradingOrchestrator",
        "shared_report_contract": "PipelineRunReport",
    }

    if verbose:
        typer.echo("[SmartQTF Orchestrator Pipeline Demo]")
        typer.echo("CLI 现在读取 PaperTradingOrchestrator 生成的 PipelineRunReport。")
        typer.echo()
        for row in rows:
            typer.echo(f"[BAR {row['bar']}] price={fmt(row['price'])} event={row['event']}")
            typer.echo("软件内部发生什么:")
            typer.echo(row["software_note"])
            typer.echo("关键 JSON:")
            typer.echo("pipeline_report=" + pretty_json(row["pipeline_report_json"]))
            typer.echo()
            typer.echo("---")
            typer.echo()
        typer.echo("[SUMMARY]")
        typer.echo(pretty_json(summary))

    return rows, summary


@app.command()
def main():
    user_input = typer.prompt("请输入任意数字")
    if user_input != "1234":
        typer.echo("INVALID INPUT")
        raise typer.Exit()
    run_pipeline(verbose=True)


if __name__ == "__main__":
    app()
