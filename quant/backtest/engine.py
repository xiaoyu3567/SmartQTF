from dataclasses import dataclass
from math import ceil
from math import sqrt

from quant.data.quality import validate_klines
from quant.features.indicators.moving_average import MovingAverage
from quant.risk.risk_manager import RiskManager


@dataclass(frozen=True)
class SlippageReport:
    symbol: str
    side: str
    quantity: float
    signal_index: int
    execute_index: int
    latency_ms: int
    reference_price: float
    execution_price: float
    base_slippage: float
    market_impact: float
    actual_fill_price: float | None = None
    execution_engine_slippage: float = 0.0

    def to_payload(self):
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "signal_index": self.signal_index,
            "execute_index": self.execute_index,
            "latency_ms": self.latency_ms,
            "reference_price": self.reference_price,
            "execution_price": self.execution_price,
            "base_slippage": self.base_slippage,
            "market_impact": self.market_impact,
            "actual_fill_price": self.actual_fill_price,
            "execution_engine_slippage": self.execution_engine_slippage,
            "total_slippage": self.total_slippage,
        }

    @property
    def total_slippage(self):
        if self.actual_fill_price is not None:
            return self.actual_fill_price - self.reference_price
        return self.execution_price - self.reference_price


@dataclass(frozen=True)
class BacktestCostModel:
    fee_rate: float = 0.0
    slippage_bps: float = 0.0
    funding_rate_per_bar: float = 0.0
    latency_ms: int = 0
    market_impact_bps_per_unit: float = 0.0

    def __post_init__(self):
        if self.fee_rate < 0.0:
            raise ValueError("fee_rate must be non-negative")
        if self.slippage_bps < 0.0:
            raise ValueError("slippage_bps must be non-negative")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if self.market_impact_bps_per_unit < 0.0:
            raise ValueError("market_impact_bps_per_unit must be non-negative")

    def execution_price(self, price, side, quantity=0.0):
        adjustment = self.base_slippage_amount(price, side)
        impact = self.market_impact(price, side, quantity)
        return float(price) + adjustment + impact

    def base_slippage(self, price, side):
        return float(price) + self.base_slippage_amount(price, side)

    def base_slippage_amount(self, price, side):
        adjustment = float(price) * (self.slippage_bps / 10000.0)
        if side == "buy":
            return adjustment
        if side == "sell":
            return -adjustment
        return 0.0

    def market_impact(self, price, side, quantity):
        impact = float(price) * (self.market_impact_bps_per_unit / 10000.0) * abs(float(quantity))
        if side == "buy":
            return impact
        if side == "sell":
            return -impact
        return 0.0

    def fee(self, fill):
        return abs(float(fill["fill_price"]) * float(fill["filled_qty"])) * self.fee_rate

    def funding(self, position_size, price):
        return float(position_size) * float(price) * self.funding_rate_per_bar


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
        features=None,
        feature_pipeline=None,
        cost_model=None,
        timeframe="1m",
        enforce_data_quality=True,
    ):
        self.strategy = strategy
        self.execution = execution
        self.account = account
        self.risk = risk or RiskManager(symbol=symbol)
        self.symbol = symbol
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.features = features
        self.feature_pipeline = feature_pipeline
        self.cost_model = cost_model or BacktestCostModel()
        self.timeframe = timeframe
        self.enforce_data_quality = enforce_data_quality
        self.execution.account = account

    def run(self, data):
        quality_report = validate_klines(
            klines=data,
            symbol=self.symbol,
            timeframe=self.timeframe,
        )
        if self.enforce_data_quality and not quality_report.passed:
            return {
                "status": "rejected",
                "rejection": "data_quality",
                "quality_report": quality_report.to_payload(),
                "total_return": self._total_return(),
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "sharpe_ratio": 0.0,
                "equity_curve": [],
                "fills": [],
                "costs": [],
                "total_cost": 0.0,
                "slippage_reports": [],
            }

        equity_curve = []
        realized_trade_pnls = []
        fills = []
        costs = []
        slippage_reports = []
        pending_backtest_orders = []
        features = self._build_features(data)

        for index, kline in enumerate(data):
            price = kline.close
            self.account.update_market_price(price, symbol=self.symbol)

            realized_before = self.account.realized_pnl
            fill_result = self.execution.on_bar(price=price, index=index)
            if fill_result is not None and fill_result["status"] in ["filled", "partial"]:
                fill_costs = self._apply_fill_costs(fill_result, index)
                fills.append(fill_result)
                costs.extend(fill_costs)
                realized_delta = self.account.realized_pnl - realized_before
                realized_delta -= self._sum_costs(fill_costs)
                if realized_delta != 0.0:
                    realized_trade_pnls.append(realized_delta)

            pending_backtest_orders, executed_reports = self._process_pending_backtest_orders(
                pending_backtest_orders,
                data,
                index,
                fills,
                costs,
                realized_trade_pnls,
            )
            slippage_reports.extend(executed_reports)

            executions = self.strategy.on_bar(features, index)
            for signal in executions:
                signal = self._signal_to_risk_payload(signal)
                signal["symbol"] = self.symbol
                order_signal = self.risk.apply(signal, self.account, price)
                if order_signal is None:
                    continue

                report = self._submit_order_signal(
                    order_signal=order_signal,
                    signal_index=index,
                    data=data,
                    pending_backtest_orders=pending_backtest_orders,
                    fills=fills,
                    costs=costs,
                    realized_trade_pnls=realized_trade_pnls,
                )
                if report is not None:
                    slippage_reports.append(report)

            funding_cost = self._apply_funding_cost(price, index)
            if funding_cost is not None:
                costs.append(funding_cost)

            self.account.update_market_price(price, symbol=self.symbol)
            equity_curve.append(self.account.equity)

        if data:
            self.account.update_market_price(data[-1].close, symbol=self.symbol)

        return {
            "status": "completed",
            "quality_report": quality_report.to_payload(),
            "total_return": self._total_return(),
            "max_drawdown": self._max_drawdown(equity_curve),
            "win_rate": self._win_rate(realized_trade_pnls),
            "sharpe_ratio": self._sharpe_ratio(equity_curve),
            "equity_curve": equity_curve,
            "fills": fills,
            "costs": costs,
            "total_cost": self._sum_costs(costs),
            "slippage_reports": [report.to_payload() for report in slippage_reports],
        }

    def _build_features(self, data):
        if self.feature_pipeline is not None:
            return self.feature_pipeline(data)

        feature_map = self.features
        if feature_map is None:
            feature_map = {
                "fast_ma": MovingAverage(window=self.fast_window),
                "slow_ma": MovingAverage(window=self.slow_window),
            }

        return {
            name: [feature.compute(data, index) for index in range(len(data))]
            for name, feature in feature_map.items()
        }

    def _signal_to_risk_payload(self, signal):
        if hasattr(signal, "to_legacy_signal"):
            return signal.to_legacy_signal()
        return dict(signal)

    def _process_pending_backtest_orders(
        self,
        pending_backtest_orders,
        data,
        index,
        fills,
        costs,
        realized_trade_pnls,
    ):
        remaining_orders = []
        reports = []
        for pending_order in pending_backtest_orders:
            if pending_order["execute_index"] != index:
                remaining_orders.append(pending_order)
                continue

            report = self._execute_order_signal(
                order_signal=pending_order["order_signal"],
                signal_index=pending_order["signal_index"],
                execute_index=index,
                data=data,
                fills=fills,
                costs=costs,
                realized_trade_pnls=realized_trade_pnls,
            )
            reports.append(report)

        return remaining_orders, reports

    def _submit_order_signal(
        self,
        order_signal,
        signal_index,
        data,
        pending_backtest_orders,
        fills,
        costs,
        realized_trade_pnls,
    ):
        execute_index = signal_index + self._latency_bars()
        if execute_index >= len(data):
            return self._build_slippage_report(
                order_signal=order_signal,
                signal_index=signal_index,
                execute_index=execute_index,
                reference_price=data[signal_index].close,
            )

        if execute_index > signal_index:
            pending_backtest_orders.append(
                {
                    "order_signal": order_signal,
                    "signal_index": signal_index,
                    "execute_index": execute_index,
                }
            )
            return None

        return self._execute_order_signal(
            order_signal=order_signal,
            signal_index=signal_index,
            execute_index=execute_index,
            data=data,
            fills=fills,
            costs=costs,
            realized_trade_pnls=realized_trade_pnls,
        )

    def _execute_order_signal(
        self,
        order_signal,
        signal_index,
        execute_index,
        data,
        fills,
        costs,
        realized_trade_pnls,
    ):
        reference_price = data[execute_index].close
        execution_price = self.cost_model.execution_price(
            reference_price,
            order_signal.get("signal"),
            order_signal.get("quantity", 0.0),
        )
        report = self._build_slippage_report(
            order_signal=order_signal,
            signal_index=signal_index,
            execute_index=execute_index,
            reference_price=reference_price,
            execution_price=execution_price,
        )

        realized_before = self.account.realized_pnl
        result = self.execution.on_signal(order_signal, price=execution_price, index=execute_index)
        if result["status"] in ["filled", "partial"]:
            report = self._build_slippage_report(
                order_signal=order_signal,
                signal_index=signal_index,
                execute_index=execute_index,
                reference_price=reference_price,
                execution_price=execution_price,
                result=result,
            )
            fill_costs = self._apply_fill_costs(result, execute_index)
            fills.append(result)
            costs.extend(fill_costs)
            realized_delta = self.account.realized_pnl - realized_before
            realized_delta -= self._sum_costs(fill_costs)
            if realized_delta != 0.0:
                realized_trade_pnls.append(realized_delta)

        return report

    def _build_slippage_report(
        self,
        order_signal,
        signal_index,
        execute_index,
        reference_price,
        execution_price=None,
        result=None,
    ):
        side = order_signal.get("signal")
        quantity = float(order_signal.get("quantity", 0.0))
        modeled_execution_price = (
            self.cost_model.execution_price(reference_price, side, quantity)
            if execution_price is None
            else execution_price
        )
        return SlippageReport(
            symbol=order_signal.get("symbol", self.symbol),
            side=side,
            quantity=quantity,
            signal_index=signal_index,
            execute_index=execute_index,
            latency_ms=self.cost_model.latency_ms,
            reference_price=float(reference_price),
            execution_price=modeled_execution_price,
            base_slippage=self.cost_model.base_slippage_amount(reference_price, side),
            market_impact=self.cost_model.market_impact(reference_price, side, quantity),
            actual_fill_price=result.get("fill_price") if result is not None else None,
            execution_engine_slippage=result.get("slippage", 0.0) if result is not None else 0.0,
        )

    def _latency_bars(self):
        if self.cost_model.latency_ms == 0:
            return 0

        return max(1, ceil(self.cost_model.latency_ms / (self._timeframe_seconds() * 1000)))

    def _timeframe_seconds(self):
        unit = self.timeframe[-1]
        amount = int(self.timeframe[:-1])
        if unit == "s":
            return amount
        if unit == "m":
            return amount * 60
        if unit == "h":
            return amount * 60 * 60
        if unit == "d":
            return amount * 60 * 60 * 24
        raise ValueError(f"unsupported timeframe: {self.timeframe}")

    def _apply_fill_costs(self, fill, index):
        fee = self.cost_model.fee(fill)
        if fee == 0.0:
            return []

        self.account.balance -= fee
        self.account.update_market_price(fill["fill_price"], symbol=fill.get("symbol", self.symbol))
        cost = {
            "type": "fee",
            "index": index,
            "symbol": fill.get("symbol", self.symbol),
            "order_id": fill.get("order_id"),
            "client_order_id": fill.get("client_order_id"),
            "amount": fee,
        }
        fill["fee"] = fill.get("fee", 0.0) + fee
        return [cost]

    def _apply_funding_cost(self, price, index):
        if self.cost_model.funding_rate_per_bar == 0.0:
            return None

        position = self.account.positions.get(self.symbol)
        if position is None or position.size == 0.0:
            return None

        amount = self.cost_model.funding(position.size, price)
        if amount == 0.0:
            return None

        self.account.balance -= amount
        self.account.update_market_price(price, symbol=self.symbol)
        return {
            "type": "funding",
            "index": index,
            "symbol": self.symbol,
            "amount": amount,
            "position_size": position.size,
            "mark_price": price,
        }

    def _sum_costs(self, costs):
        return sum(cost["amount"] for cost in costs)

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
