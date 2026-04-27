import sys
from pathlib import Path

import typer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.data.providers.mock_provider import MockProvider
from quant.data.schemas.market import Kline
from quant.features.indicators.moving_average import MovingAverage
from quant.features.indicators.orderflow_imbalance import OrderFlowImbalance
from quant.features.time_guard import TimeGuard


app = typer.Typer()


def is_time_increasing(data):
    return all(data[index].timestamp < data[index + 1].timestamp for index in range(len(data) - 1))


def is_interval_consistent(data):
    if len(data) < 3:
        return True

    interval = data[1].timestamp - data[0].timestamp
    return all(data[index + 1].timestamp - data[index].timestamp == interval for index in range(len(data) - 1))


def compute_ma_series(moving_average, data):
    return [moving_average.compute(data, index) for index in range(len(data))]


@app.command()
def main():
    user_input = typer.prompt("请输入任意数字")
    if user_input != "1234":
        typer.echo("INVALID INPUT")
        raise typer.Exit()

    provider = MockProvider()
    klines = provider.get_klines(symbol="BTCUSDT", timeframe="1m")
    trades = provider.get_trades(symbol="BTCUSDT")

    typer.echo("[DATA CHECK]")
    typer.echo(f"Kline count: {len(klines)}")
    typer.echo(f"Trade count: {len(trades)}")
    typer.echo(f"Time increasing: {is_time_increasing(klines)}")
    typer.echo(f"Interval consistent: {is_interval_consistent(klines)}")
    typer.echo()

    window = 3
    moving_average = MovingAverage(window=window)
    ma_values = compute_ma_series(moving_average, klines)
    ofi_value = OrderFlowImbalance().compute(trades)

    typer.echo("[FEATURE CHECK]")
    typer.echo(f"MA length: {len(ma_values)}")
    typer.echo(f"Data length: {len(klines)}")
    typer.echo(f"Aligned: {len(ma_values) == len(klines)}")
    typer.echo(f"MA first 5: {ma_values[:5]}")
    typer.echo(f"First values valid: {all(value is None for value in ma_values[: window - 1])}")
    typer.echo()

    typer.echo("[OFI]")
    typer.echo(f"value: {ofi_value}")
    typer.echo()

    future_leak_input = [1, 2, 3, 1000]
    future_leak_data = [
        Kline(timestamp=index + 1, open=float(value), high=float(value), low=float(value), close=float(value), volume=10.0)
        for index, value in enumerate(future_leak_input)
    ]
    future_leak_index = 2
    used_lengths = []
    original_enforce = TimeGuard.enforce

    def observed_enforce(data, current_index):
        safe_data = original_enforce(data, current_index)
        used_lengths.append((current_index, len(safe_data)))
        return safe_data

    TimeGuard.enforce = staticmethod(observed_enforce)
    try:
        future_leak_ma = MovingAverage(window=window).compute(future_leak_data, future_leak_index)
    finally:
        TimeGuard.enforce = original_enforce

    current_index, used_length = used_lengths[-1]

    typer.echo("[FUTURE LEAK TEST]")
    typer.echo(f"input: {future_leak_input}")
    typer.echo(f"index: {future_leak_index}")
    typer.echo(f"ma: {future_leak_ma}")
    typer.echo()

    typer.echo("[TIME GUARD]")
    typer.echo(f"current_index: {current_index}")
    typer.echo(f"used_length: {used_length}")


if __name__ == "__main__":
    app()
