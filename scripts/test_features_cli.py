import sys
from pathlib import Path

import typer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.data.providers.mock_provider import MockProvider
from quant.features.indicators.moving_average import MovingAverage
from quant.features.indicators.orderflow_imbalance import OrderFlowImbalance


app = typer.Typer()


@app.command()
def main():
    user_input = typer.prompt("请输入任意数字")

    if user_input == "1234":
        provider = MockProvider()
        klines = provider.get_klines(symbol="BTCUSDT", timeframe="1m")
        trades = provider.get_trades(symbol="BTCUSDT")

        moving_average = MovingAverage(window=3)
        ma_values = [moving_average.compute(klines, index) for index in range(len(klines))]
        ofi_value = OrderFlowImbalance().compute(trades)
        guard_index = 2

        typer.echo(f"MA 前5个值: {ma_values[:5]}")
        typer.echo(f"OFI 值: {ofi_value}")
        typer.echo(f"index={guard_index} 的 MA: {moving_average.compute(klines, guard_index)}")
    else:
        typer.echo("INVALID INPUT")


if __name__ == "__main__":
    app()
