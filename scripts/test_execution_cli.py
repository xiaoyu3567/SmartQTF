import sys
from pathlib import Path

import typer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.execution.engine import ExecutionEngine


app = typer.Typer()


def format_position(engine):
    return f"size={engine.position.size} avg_price={engine.position.avg_price}"


@app.command()
def main():
    user_input = typer.prompt("请输入任意数字")
    if user_input != "1234":
        typer.echo("INVALID INPUT")
        raise typer.Exit()

    signal = {"signal": "buy", "signal_index": 2, "quantity": 1.0}
    price = 100.0

    filled_engine = ExecutionEngine(seed=1)
    filled_result = filled_engine.on_signal(signal, price=price, index=3)

    partial_engine = ExecutionEngine(seed=0)
    partial_result = partial_engine.on_signal(signal, price=price, index=3)

    rejected_engine = ExecutionEngine(seed=2)
    rejected_result = rejected_engine.on_signal(signal, price=price, index=3)

    delay_engine = ExecutionEngine(execution_delay=1, seed=1)
    delayed_order_result = delay_engine.on_signal(signal, price=price, index=3)
    delayed_fill_result = delay_engine.on_bar(price=price, index=3)

    typer.echo("[EXECUTION TEST]")
    typer.echo()

    typer.echo("case 1:")
    typer.echo(f"signal: {signal['signal']}")
    typer.echo(f"price: {int(price)}")
    typer.echo(f"status: {filled_result['status']}")
    typer.echo(f"filled_price: {filled_result['fill_price']}")
    typer.echo(f"position: {format_position(filled_engine)}")
    typer.echo()

    typer.echo("case 2:")
    typer.echo(f"signal: {signal['signal']}")
    typer.echo(f"price: {int(price)}")
    typer.echo(f"status: {partial_result['status']}")
    typer.echo(f"filled_qty: {partial_result['filled_qty']}")
    typer.echo(f"remaining: {partial_result['remaining_qty']}")
    typer.echo(f"position update after partial: {format_position(partial_engine)}")
    typer.echo()

    typer.echo("case 3:")
    typer.echo(f"signal: {signal['signal']}")
    typer.echo(f"price: {int(price)}")
    typer.echo(f"status: {rejected_result['status']}")
    typer.echo(f"position unchanged: {format_position(rejected_engine)}")
    typer.echo()

    typer.echo("case 4:")
    typer.echo("t=2 signal")
    typer.echo(f"t=3 order created: {delayed_order_result['status']}")
    typer.echo(f"t=3 filled: {delayed_fill_result['status']}")


if __name__ == "__main__":
    app()
