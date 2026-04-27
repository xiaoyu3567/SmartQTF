from math import sqrt

from quant.features.indicators.moving_average import MovingAverage
from quant.risk.risk_manager import RiskManager


class BacktestEngine:
    def __init__(
        self,
        strategy,
        execution,
        account,
        risk=None,
        symbol="BTCUSDT",
        fast_window=1,
        slow_window=2,
    ):
        self.strategy = strategy
        self.execution = execution
        self.account = account
        self.risk = risk or RiskManager(symbol=symbol)
        self.symbol = symbol
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.execution.account = account

    def run(self, data):
        equity_curve = []
        realized_trade_pnls = []
        fills = []
        features = self._build_features(data)

        for index, kline in enumerate(data):
            price = kline.close
            self.account.update_market_price(price, symbol=self.symbol)

            realized_before = self.account.realized_pnl
            fill_result = self.execution.on_bar(price=price, index=index)
            if fill_result is not None and fill_result["status"] in ["filled", "partial"]:
                fills.append(fill_result)
                realized_delta = self.account.realized_pnl - realized_before
                if realized_delta != 0.0:
                    realized_trade_pnls.append(realized_delta)

            executions = self.strategy.on_bar(features, index)
            for signal in executions:
                signal["symbol"] = self.symbol
                order_signal = self.risk.apply(signal, self.account, price)
                if order_signal is None:
                    continue

                realized_before = self.account.realized_pnl
                result = self.execution.on_signal(order_signal, price=price, index=index)
                if result["status"] in ["filled", "partial"]:
                    fills.append(result)
                    realized_delta = self.account.realized_pnl - realized_before
                    if realized_delta != 0.0:
                        realized_trade_pnls.append(realized_delta)

            self.account.update_market_price(price, symbol=self.symbol)
            equity_curve.append(self.account.equity)

        if data:
            self.account.update_market_price(data[-1].close, symbol=self.symbol)

        return {
            "total_return": self._total_return(),
            "max_drawdown": self._max_drawdown(equity_curve),
            "win_rate": self._win_rate(realized_trade_pnls),
            "sharpe_ratio": self._sharpe_ratio(equity_curve),
            "equity_curve": equity_curve,
            "fills": fills,
        }

    def _build_features(self, data):
        fast_ma = MovingAverage(window=self.fast_window)
        slow_ma = MovingAverage(window=self.slow_window)
        return {
            "fast_ma": [fast_ma.compute(data, index) for index in range(len(data))],
            "slow_ma": [slow_ma.compute(data, index) for index in range(len(data))],
        }

    def _total_return(self):
        return (self.account.equity - self.account.initial_balance) / self.account.initial_balance

    def _max_drawdown(self, equity_curve):
        if not equity_curve:
            return 0.0

        peak = equity_curve[0]
        max_drawdown = 0.0

        for equity in equity_curve:
            peak = max(peak, equity)
            if peak > 0.0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)

        return max_drawdown

    def _win_rate(self, realized_trade_pnls):
        if not realized_trade_pnls:
            return 0.0

        wins = [pnl for pnl in realized_trade_pnls if pnl > 0.0]
        return len(wins) / len(realized_trade_pnls)

    def _sharpe_ratio(self, equity_curve):
        if len(equity_curve) < 2:
            return 0.0

        returns = [
            (equity_curve[index] - equity_curve[index - 1]) / equity_curve[index - 1]
            for index in range(1, len(equity_curve))
            if equity_curve[index - 1] != 0.0
        ]
        if len(returns) < 2:
            return 0.0

        mean_return = sum(returns) / len(returns)
        variance = sum((value - mean_return) ** 2 for value in returns) / len(returns)
        std_return = variance ** 0.5

        if std_return == 0.0:
            return 0.0

        return (mean_return / std_return) * sqrt(len(returns))
