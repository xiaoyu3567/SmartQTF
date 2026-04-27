import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.models.crypto import CryptoAccount
from quant.backtest.engine import BacktestEngine
from quant.data.schemas.market import Kline
from quant.execution.engine import ExecutionEngine
from quant.risk.risk_manager import RiskManager
from quant.strategy.ma_crossover import MACrossoverStrategy


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


def test_backtest_pnl():
    data = build_klines([100, 99, 101, 103, 100, 98])
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(execution_delay=0, seed=1, account=account)
    strategy = MACrossoverStrategy()
    risk = RiskManager(max_position_pct=0.1, symbol="BTCUSDT")
    engine = BacktestEngine(strategy, execution, account, risk=risk, fast_window=1, slow_window=2)

    result = engine.run(data)

    assert "total_return" in result
    assert "max_drawdown" in result
    assert "win_rate" in result
    assert "sharpe_ratio" in result
    assert len(result["equity_curve"]) == len(data)
    assert account.equity != account.initial_balance


def test_drawdown():
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(account=account)
    strategy = MACrossoverStrategy()
    engine = BacktestEngine(strategy, execution, account)

    drawdown = engine._max_drawdown([10000.0, 11000.0, 9900.0, 10500.0])

    assert drawdown == 0.1
