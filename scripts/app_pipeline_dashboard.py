import json
import sys
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.test_full_pipeline_cli import SYMBOL, run_pipeline


def fmt(value, digits=4):
    if value is None:
        return "None"
    if isinstance(value, str):
        return value
    rounded = round(float(value), digits)
    if rounded == 0:
        return "0"
    if rounded.is_integer():
        return str(int(rounded))
    return str(rounded)


def payload_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def build_summary_table(rows):
    return [
        {
            "bar": row["bar"],
            "price": row["price"],
            "event": row["event"],
            "position": row["account_json"]["position_size"],
            "avg_price": row["account_json"]["avg_price"],
            "equity": row["account_json"]["equity"],
            "realized_pnl": row["account_json"]["realized_pnl"],
            "teacher_note": row["teacher_note"],
        }
        for row in rows
    ]


def marker_points(rows, event_name):
    points = []
    for row in rows:
        event = row["event"]
        if event_name == "buy" and event == "buy":
            points.append(row)
        elif event_name == "sell" and event == "sell":
            points.append(row)
        elif event_name == "stop_loss" and "stop_loss" in event:
            points.append(row)
        elif event_name == "take_profit" and "take_profit" in event:
            points.append(row)
    return points


def feature_value(row, name):
    features = row["feature_json"].get("features", {})
    return features.get(name)


def add_event_markers(fig, rows, event_name, symbol, color):
    points = marker_points(rows, event_name)
    fig.add_trace(
        go.Scatter(
            x=[row["bar"] for row in points],
            y=[row["price"] for row in points],
            mode="markers",
            name=event_name,
            marker={
                "symbol": symbol,
                "size": 14,
                "color": color,
                "line": {"width": 1, "color": "white"},
            },
        )
    )


def price_figure(rows, current_bar):
    bars = [row["bar"] for row in rows]
    fast_ma = [feature_value(row, "fast_ma") for row in rows]
    slow_ma = [feature_value(row, "slow_ma") for row in rows]
    prices = [row["price"] for row in rows]
    current = rows[current_bar]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=bars,
            y=prices,
            mode="lines+markers",
            name="price",
            line={"color": "#2563eb", "width": 3},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bars,
            y=fast_ma,
            mode="lines",
            name="fast MA",
            line={"color": "#16a34a", "width": 2},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bars,
            y=slow_ma,
            mode="lines",
            name="slow MA",
            line={"color": "#f97316", "width": 2},
        )
    )
    add_event_markers(fig, rows, "buy", "triangle-up", "#15803d")
    add_event_markers(fig, rows, "sell", "triangle-down", "#b91c1c")
    add_event_markers(fig, rows, "stop_loss", "x", "#7f1d1d")
    add_event_markers(fig, rows, "take_profit", "star", "#ca8a04")
    fig.add_trace(
        go.Scatter(
            x=[current["bar"]],
            y=[current["price"]],
            mode="markers",
            name="current bar",
            marker={"symbol": "circle-open", "size": 22, "color": "#111827", "line": {"width": 3}},
        )
    )
    fig.update_layout(
        title=f"{SYMBOL} 教学行情回放：当前第 {current_bar} 根K线",
        xaxis_title="bar",
        yaxis_title="price",
        hovermode="x unified",
        legend_title_text="",
        margin={"l": 24, "r": 24, "t": 52, "b": 24},
    )
    return fig


def account_figure(rows, current_bar):
    shown = rows[: current_bar + 1]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[row["bar"] for row in shown],
            y=[row["account_json"]["equity"] for row in shown],
            mode="lines+markers",
            name="equity",
            line={"color": "#0f766e", "width": 3},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[row["bar"] for row in shown],
            y=[row["account_json"]["balance"] for row in shown],
            mode="lines+markers",
            name="balance",
            line={"color": "#64748b", "width": 2},
        )
    )
    fig.add_trace(
        go.Bar(
            x=[row["bar"] for row in shown],
            y=[row["account_json"]["position_size"] for row in shown],
            name="position",
            marker_color="#7c3aed",
            yaxis="y2",
            opacity=0.35,
        )
    )
    fig.update_layout(
        title="账户与仓位如何跟随订单变化",
        xaxis_title="bar",
        yaxis_title="equity / balance",
        yaxis2={"title": "position", "overlaying": "y", "side": "right"},
        hovermode="x unified",
        legend_title_text="",
        margin={"l": 24, "r": 24, "t": 52, "b": 24},
    )
    return fig


def stage_status(row):
    if row["execution_result_json"] is not None:
        return "已执行订单"
    if row["risk_decision_json"] is not None:
        decision = row["risk_decision_json"]
        return "风控通过" if decision.get("approved") else "风控拒绝"
    if row["generated_signal_json"] is not None:
        return "策略已生成信号，等待下一根K线执行"
    return "观察中"


def render_stage_cards(row):
    cards = [
        ("市场", row["market_note"]),
        ("用户", row["user_note"]),
        ("软件内部", row["software_note"]),
        ("量化老师", row["teacher_note"]),
    ]
    columns = st.columns(4)
    for column, (title, body) in zip(columns, cards):
        with column:
            st.markdown(f"**{title}**")
            st.write(body)


def render_json_tabs(row):
    tabs = st.tabs([
        "Pipeline Report",
        "Feature JSON",
        "Signal JSON",
        "Risk JSON",
        "Order JSON",
        "Execution JSON",
        "Account JSON",
    ])
    with tabs[0]:
        st.code(payload_text(row["pipeline_report_json"]), language="json")
    with tabs[1]:
        st.code(payload_text(row["feature_json"]), language="json")
    with tabs[2]:
        st.code(payload_text({
            "generated_signal": row["generated_signal_json"],
            "decision": row["decision_json"],
            "regime": row["regime_json"],
            "route": row["route_json"],
        }), language="json")
    with tabs[3]:
        st.code(payload_text(row["risk_decision_json"]), language="json")
    with tabs[4]:
        st.code(payload_text({
            "order_intent": row["order_intent_json"],
            "portfolio": row["portfolio_json"],
        }), language="json")
    with tabs[5]:
        st.code(payload_text(row["execution_result_json"]), language="json")
    with tabs[6]:
        st.code(payload_text(row["account_json"]), language="json")


def advance(delta, total):
    st.session_state.current_bar = max(0, min(total - 1, st.session_state.current_bar + delta))


def main():
    st.set_page_config(page_title="SmartQTF Pipeline Dashboard", layout="wide")
    st.title("SmartQTF Pipeline Dashboard")
    st.caption("点一下就向前走一根K线：所有阶段都读取同一份 Orchestrator PipelineRunReport。")

    rows, summary = run_pipeline(verbose=False)
    if "current_bar" not in st.session_state:
        st.session_state.current_bar = 0

    total = len(rows)
    current_bar = st.session_state.current_bar
    row = rows[current_bar]

    controls = st.columns([1, 1, 1, 2, 2])
    with controls[0]:
        if st.button("上一步", width="stretch"):
            advance(-1, total)
            st.rerun()
    with controls[1]:
        if st.button("下一步", width="stretch"):
            advance(1, total)
            st.rerun()
    with controls[2]:
        if st.button("重置", width="stretch"):
            st.session_state.current_bar = 0
            st.rerun()
    with controls[3]:
        selected = st.slider("当前K线", 0, total - 1, current_bar)
        if selected != current_bar:
            st.session_state.current_bar = selected
            st.rerun()
    with controls[4]:
        st.metric("当前阶段", stage_status(row), f"bar {row['bar']} / {total - 1}")

    current_bar = st.session_state.current_bar
    row = rows[current_bar]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("价格", fmt(row["price"], 2))
    k2.metric("快线 / 慢线", f"{fmt(feature_value(row, 'fast_ma'), 2)} / {fmt(feature_value(row, 'slow_ma'), 2)}")
    k3.metric("事件", row["event"])
    k4.metric("账户权益", fmt(row["account_json"]["equity"], 2), fmt(row["account_json"]["realized_pnl"], 2))

    st.plotly_chart(price_figure(rows, current_bar), width="stretch")

    render_stage_cards(row)

    left, right = st.columns([1.15, 0.85])
    with left:
        st.plotly_chart(account_figure(rows, current_bar), width="stretch")
        st.subheader("到当前为止的流水")
        st.dataframe(build_summary_table(rows[: current_bar + 1]), width="stretch", hide_index=True)
    with right:
        st.subheader("这一阶段还差哪些")
        for item in row["missing"]:
            st.write(f"- {item}")
        st.subheader("关键 JSON 内容")
        render_json_tabs(row)

    st.subheader("本次教学测试总结")
    st.code(payload_text(summary), language="json")
    st.info(
        "这张看板用于解释 pipeline 是否可理解，不用于证明策略有效。"
        "下一阶段应该接真实只读行情、纸交易循环、运行时监控和更完整的复盘报告。"
    )


if __name__ == "__main__":
    main()
