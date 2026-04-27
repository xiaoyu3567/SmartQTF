import sys
from pathlib import Path

import typer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.models.crypto import CryptoAccount
from quant.backtest.engine import BacktestEngine
from quant.data.schemas.market import Kline
from quant.execution.engine import ExecutionEngine
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


@app.command()
def main():
    user_input = typer.prompt("请输入任意数字")
    if user_input != "1234":
        typer.echo("INVALID INPUT")
        raise typer.Exit()

    data = build_klines([100, 99, 101, 103, 100, 98, 102, 104])
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(execution_delay=0, seed=1, account=account)
    strategy = MACrossoverStrategy()
    risk = RiskManager(max_position_pct=0.1, symbol="BTCUSDT")
    engine = BacktestEngine(strategy, execution, account, risk=risk, fast_window=1, slow_window=2)
    result = engine.run(data)

    typer.echo("[BACKTEST RESULT]")
    typer.echo(f"total_return: {result['total_return']}")
    typer.echo(f"max_drawdown: {result['max_drawdown']}")
    typer.echo(f"win_rate: {result['win_rate']}")
    typer.echo(f"sharpe_ratio: {result['sharpe_ratio']}")


if __name__ == "__main__":
    app()
