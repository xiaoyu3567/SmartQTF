import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.models.crypto import CryptoAccount
from quant.backtest.engine import BacktestCostModel, BacktestEngine
from quant.data.schemas.market import Kline
from quant.execution.engine import ExecutionEngine
from quant.risk.risk_manager import RiskManager
from quant.strategy.ma_crossover import MACrossoverStrategy


class CloseFeature:
    def compute(self, data, index):
        return data[index].close


class CloseThresholdStrategy:
    def on_bar(self, features, index):
        if index != 1:
            return []
        if features["close"][index] > features["close"][index - 1]:
            return [{"signal": "buy", "signal_index": index, "execute_index": index}]
        return []


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
    assert result["status"] == "completed"
    assert result["quality_report"]["passed"] is True


def test_drawdown():
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(account=account)
    strategy = MACrossoverStrategy()
    engine = BacktestEngine(strategy, execution, account)

    drawdown = engine._max_drawdown([10000.0, 11000.0, 9900.0, 10500.0])

    assert drawdown == 0.1


def test_backtest_accepts_custom_feature_pipeline():
    data = build_klines([100, 101, 102])
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(execution_delay=0, seed=1, account=account)
    strategy = CloseThresholdStrategy()
    risk = RiskManager(max_position_pct=0.1, symbol="BTCUSDT")
    engine = BacktestEngine(
        strategy,
        execution,
        account,
        risk=risk,
        features={"close": CloseFeature()},
    )

    result = engine.run(data)

    assert len(result["fills"]) == 1
    assert result["fills"][0]["side"] == "buy"


def test_backtest_accepts_precomputed_feature_pipeline():
    data = build_klines([100, 101, 102])
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(execution_delay=0, seed=1, account=account)
    strategy = CloseThresholdStrategy()
    risk = RiskManager(max_position_pct=0.1, symbol="BTCUSDT")
    engine = BacktestEngine(
        strategy,
        execution,
        account,
        risk=risk,
        feature_pipeline=lambda klines: {"close": [kline.close for kline in klines]},
    )

    result = engine.run(data)

    assert len(result["fills"]) == 1
    assert result["fills"][0]["side"] == "buy"


def test_backtest_applies_fee_and_slippage_cost_model():
    data = build_klines([100, 101, 102])
    plain_account = CryptoAccount(initial_balance=10000.0)
    plain_execution = ExecutionEngine(execution_delay=0, seed=1, account=plain_account)
    strategy = CloseThresholdStrategy()
    risk = RiskManager(max_position_pct=0.1, symbol="BTCUSDT")
    plain_engine = BacktestEngine(
        strategy,
        plain_execution,
        plain_account,
        risk=risk,
        feature_pipeline=lambda klines: {"close": [kline.close for kline in klines]},
    )

    plain_result = plain_engine.run(data)

    cost_account = CryptoAccount(initial_balance=10000.0)
    cost_execution = ExecutionEngine(execution_delay=0, seed=1, account=cost_account)
    cost_engine = BacktestEngine(
        strategy,
        cost_execution,
        cost_account,
        risk=RiskManager(max_position_pct=0.1, symbol="BTCUSDT"),
        feature_pipeline=lambda klines: {"close": [kline.close for kline in klines]},
        cost_model=BacktestCostModel(fee_rate=0.001, slippage_bps=100.0),
    )

    result = cost_engine.run(data)

    assert len(result["fills"]) == 1
    assert result["fills"][0]["fill_price"] > plain_result["fills"][0]["fill_price"]
    assert result["fills"][0]["fee"] > 0.0
    assert result["costs"][0]["type"] == "fee"
    assert result["total_cost"] == result["fills"][0]["fee"]
    assert cost_account.equity < plain_account.equity


def test_backtest_applies_funding_per_bar_to_open_position():
    data = build_klines([100, 101, 102])
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(execution_delay=0, seed=1, account=account)
    strategy = CloseThresholdStrategy()
    risk = RiskManager(max_position_pct=0.1, symbol="BTCUSDT")
    engine = BacktestEngine(
        strategy,
        execution,
        account,
        risk=risk,
        feature_pipeline=lambda klines: {"close": [kline.close for kline in klines]},
        cost_model=BacktestCostModel(funding_rate_per_bar=0.001),
    )

    result = engine.run(data)

    funding_costs = [cost for cost in result["costs"] if cost["type"] == "funding"]
    assert funding_costs
    assert funding_costs[0]["amount"] > 0.0
    assert result["total_cost"] == sum(cost["amount"] for cost in result["costs"])


def test_backtest_outputs_replayable_slippage_report_with_market_impact():
    data = build_klines([100, 101, 102])
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(execution_delay=0, seed=1, account=account)
    strategy = CloseThresholdStrategy()
    risk = RiskManager(max_position_pct=0.1, symbol="BTCUSDT")
    engine = BacktestEngine(
        strategy,
        execution,
        account,
        risk=risk,
        feature_pipeline=lambda klines: {"close": [kline.close for kline in klines]},
        cost_model=BacktestCostModel(slippage_bps=10.0, market_impact_bps_per_unit=1.0),
    )

    result = engine.run(data)

    report = result["slippage_reports"][0]
    assert report["symbol"] == "BTCUSDT"
    assert report["side"] == "buy"
    assert report["signal_index"] == 1
    assert report["execute_index"] == 1
    assert report["reference_price"] == 101.0
    assert report["base_slippage"] == 101.0 * 0.001
    assert report["market_impact"] > 0.0
    assert report["actual_fill_price"] == result["fills"][0]["fill_price"]
    assert report["total_slippage"] > report["base_slippage"]


def test_backtest_latency_executes_signal_on_future_bar():
    data = build_klines([100, 101, 110])
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(execution_delay=0, seed=1, account=account)
    strategy = CloseThresholdStrategy()
    risk = RiskManager(max_position_pct=0.1, symbol="BTCUSDT")
    engine = BacktestEngine(
        strategy,
        execution,
        account,
        risk=risk,
        feature_pipeline=lambda klines: {"close": [kline.close for kline in klines]},
        cost_model=BacktestCostModel(latency_ms=1),
        timeframe="1m",
    )

    result = engine.run(data)

    assert len(result["fills"]) == 1
    assert result["fills"][0]["fill_index"] == 2
    assert result["slippage_reports"][0]["signal_index"] == 1
    assert result["slippage_reports"][0]["execute_index"] == 2
    assert result["slippage_reports"][0]["latency_ms"] == 1
    assert result["slippage_reports"][0]["reference_price"] == 110.0


def test_backtest_rejects_bad_quality_data_before_trading():
    data = [
        Kline(timestamp=1700000000, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        Kline(timestamp=1700000120, open=102.0, high=103.0, low=101.0, close=102.0, volume=1000.0),
    ]
    account = CryptoAccount(initial_balance=10000.0)
    execution = ExecutionEngine(account=account)
    engine = BacktestEngine(MACrossoverStrategy(), execution, account)

    result = engine.run(data)

    assert result["status"] == "rejected"
    assert result["rejection"] == "data_quality"
    assert result["fills"] == []
    assert result["quality_report"]["passed"] is False
