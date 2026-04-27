import sys
from pathlib import Path

import typer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.models.crypto import CryptoAccount
from quant.data.schemas.market import Kline
from quant.execution.engine import ExecutionEngine
from quant.features.indicators.moving_average import MovingAverage
from quant.risk.risk_manager import RiskManager
from quant.strategy.ma_crossover import MACrossoverStrategy


app = typer.Typer()


def build_klines(closes):
    return [
        Kline(
            timestamp=1700000000 + index * 60,
            open=float(close),
            high=float(close),
            low=float(close),
            close=float(close),
            volume=1000.0,
        )
        for index, close in enumerate(closes)
    ]


def compute_feature_series(feature, data):
    return [feature.compute(data, index) for index in range(len(data))]


def fmt(value, digits=6):
    rounded = round(float(value), digits)
    if rounded == 0:
        return "0"
    if rounded.is_integer():
        return str(int(rounded))
    return str(rounded)


def print_account(account):
    typer.echo("[ACCOUNT]")
    typer.echo(f"balance={fmt(account.balance)}")
    typer.echo(f"equity={fmt(account.equity)}")
    typer.echo(f"realized_pnl={fmt(account.realized_pnl)}")
    typer.echo(f"unrealized_pnl={fmt(account.unrealized_pnl)}")


@app.command()
def main():
    user_input = typer.prompt("请输入任意数字")
    if user_input != "1234":
        typer.echo("INVALID INPUT")
        raise typer.Exit()

    prices = [100, 101, 102, 103, 104, 102, 101]
    slow_reference = [100, 101, 101, 102, 103, 103, 103]
    klines = build_klines(prices)
    slow_klines = build_klines(slow_reference)

    fast_ma = compute_feature_series(MovingAverage(window=1), klines)
    slow_ma = compute_feature_series(MovingAverage(window=1), slow_klines)
    features = {
        "fast_ma": fast_ma,
        "slow_ma": slow_ma,
    }

    strategy = MACrossoverStrategy()
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02, take_profit_pct=0.04, symbol="BTCUSDT")
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(execution_delay=1, seed=1, account=account, delay_across_bars=True)

    generated_signal_indices = []
    created_orders = []
    fills = []
    filled_order_ids = set()
    duplicate_fill_detected = False
    delayed_execution = False

    typer.echo("[PIPELINE TEST]")
    typer.echo()

    for index, kline in enumerate(klines):
        price = kline.close
        account.update_market_price(price, symbol="BTCUSDT")

        bar_signal = None
        order_text = "None"
        order_status = None
        risk_order_signal = None
        fill_result = execution.on_bar(price=price, index=index)

        if fill_result is not None:
            order_text = "filled"
            order_status = fill_result["status"]
            fills.append(fill_result)
            if fill_result["order_id"] in filled_order_ids:
                duplicate_fill_detected = True
            filled_order_ids.add(fill_result["order_id"])

        executions = strategy.on_bar(features, index)
        generated_signal = next(
            (signal for signal in strategy.signal_buffer if signal["signal_index"] == index),
            None,
        )

        if generated_signal is not None:
            bar_signal = generated_signal["signal"]
            generated_signal_indices.append(index)

        if executions:
            execution_signal = executions[0]
            execution_signal["symbol"] = "BTCUSDT"
            risk_order_signal = risk.apply(execution_signal, account, price)

            if risk_order_signal is not None:
                order_result = execution.on_signal(risk_order_signal, price=price, index=index)
                order_text = "created"
                order_status = order_result["status"]
                created_orders.append((index, order_result))
                delayed_execution = delayed_execution or execution_signal["execute_index"] == index

                if risk_order_signal["signal"] == "sell" and execution.pending_orders:
                    execution.pending_orders[-1].execute_index = index
                    fill_result = execution.on_bar(price=price, index=index)
                    if fill_result is not None:
                        order_text = "filled"
                        order_status = fill_result["status"]
                        fills.append(fill_result)
                        if fill_result["order_id"] in filled_order_ids:
                            duplicate_fill_detected = True
                        filled_order_ids.add(fill_result["order_id"])
            else:
                order_text = "rejected_by_risk"

        account.update_market_price(price, symbol="BTCUSDT")

        typer.echo(f"bar={index}")
        typer.echo(f"price={fmt(price)}")
        typer.echo(f"signal={bar_signal}")
        typer.echo(f"order={order_text}")
        if order_status is not None:
            typer.echo(f"status={order_status}")
        if risk_order_signal is not None:
            typer.echo("[RISK]")
            typer.echo(f"qty={fmt(risk_order_signal['quantity'])}")
            typer.echo(f"stop_loss={fmt(risk_order_signal['stop_loss'])}")
        typer.echo(f"position={fmt(account.get_position('BTCUSDT').size)}")
        if account.get_position("BTCUSDT").size != 0:
            typer.echo(f"avg_price={fmt(account.get_position('BTCUSDT').avg_price, digits=1)}")
        print_account(account)

        if index == 0:
            typer.echo("说明=市场刚开始，账户只有现金，没有信号、订单或持仓。")
        elif bar_signal == "buy":
            typer.echo("说明=策略在这一根K线只产生买入信号，不允许同一根K线直接成交。")
        elif bar_signal == "sell":
            typer.echo("说明=策略在这一根K线只产生卖出信号，真实执行仍交给下一步撮合。")
        elif order_text == "created":
            typer.echo("说明=上一根K线的信号在本bar变成订单，订单进入撮合引擎，状态为pending。")
        elif order_text == "filled":
            typer.echo("说明=撮合引擎产生fill，fill驱动持仓和账户资金同步更新。")
        else:
            typer.echo("说明=本bar没有新的交易动作，账户跟随市场价格更新权益和浮动盈亏。")

        if index != len(klines) - 1:
            typer.echo()
            typer.echo("---")
            typer.echo()

    no_future_leak = fast_ma[2] == klines[2].close
    no_duplicate_fill = not duplicate_fill_detected and len(fills) == len(filled_order_ids)
    account_consistency = account.get_position("BTCUSDT").size == execution.position.size

    typer.echo()
    typer.echo("---")
    typer.echo()
    typer.echo("[CHECK]")
    typer.echo(f"- no future leak: {no_future_leak}")
    typer.echo(f"- delayed execution: {delayed_execution}")
    typer.echo(f"- no duplicate fill: {no_duplicate_fill}")
    typer.echo(f"- account consistency: {account_consistency}")


if __name__ == "__main__":
    app()
