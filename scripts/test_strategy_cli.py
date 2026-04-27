import sys
from pathlib import Path

import typer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.strategy.ma_crossover import MACrossoverStrategy


app = typer.Typer()


@app.command()
def main():
    user_input = typer.prompt("请输入任意数字")
    if user_input != "1234":
        typer.echo("INVALID INPUT")
        raise typer.Exit()

    features = {
        "fast_ma": [None, 1.0, 3.0, 4.0, 5.0],
        "slow_ma": [None, 2.0, 2.0, 2.0, 2.0],
    }
    strategy = MACrossoverStrategy()

    typer.echo("[STRATEGY TEST]")

    for index in range(len(features["fast_ma"])):
        executions = strategy.on_bar(features, index)
        generated_signal = next(
            (signal for signal in strategy.signal_buffer if signal["signal_index"] == index),
            None,
        )

        if executions:
            typer.echo(f"index={index} -> executed: {executions[0]['signal']}")
        elif generated_signal is not None:
            typer.echo(f"index={index} -> signal generated: {generated_signal['signal']}")
        elif index == len(features["fast_ma"]) - 1:
            typer.echo(f"index={index} -> executed: none")
        else:
            typer.echo(f"index={index} -> no signal")


if __name__ == "__main__":
    app()
