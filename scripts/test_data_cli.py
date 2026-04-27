import sys
from pathlib import Path

import typer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.data.providers.mock_provider import MockProvider


app = typer.Typer()


@app.command()
def main():
    user_input = typer.prompt("请输入任意数字")

    if user_input == "1234":
        provider = MockProvider()
        klines = provider.get_klines(symbol="BTCUSDT", timeframe="1m")
        trades = provider.get_trades(symbol="BTCUSDT")

        typer.echo(f"Kline 数量: {len(klines)}")
        typer.echo(f"Trade 数量: {len(trades)}")
        for i in range(5):
            typer.echo(f"第一条 Kline: {klines[i]}")
    else:
        typer.echo("INVALID INPUT")


if __name__ == "__main__":
    app()
