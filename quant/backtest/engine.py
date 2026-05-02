from dataclasses import dataclass
from math import ceil
from math import sqrt

from quant.data.quality import validate_klines
from quant.execution.state_machine import ExecutionEvent, ExecutionState, ExecutionStateMachine
from quant.execution.lifecycle_contract import attach_order_lifecycle_contract
from quant.features.indicators.moving_average import MovingAverage
from quant.risk.risk_manager import RiskManager
from quant.schemas import PayloadSource


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


@dataclass(frozen=True)
class BacktestExecutionModel:
    partial_fill_ratio: float = 1.0
    timeout_recovery_bars: int = 0

    def __post_init__(self):
        if self.partial_fill_ratio <= 0.0 or self.partial_fill_ratio > 1.0:
            raise ValueError("partial_fill_ratio must be in (0.0, 1.0]")
        if self.timeout_recovery_bars < 0:
            raise ValueError("timeout_recovery_bars must be non-negative")


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
        execution_model=None,
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
        self.execution_model = execution_model or BacktestExecutionModel()
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
                "order_lifecycle_reports": [],
                "execution_simulation_reports": [],
            }

        equity_curve = []
        realized_trade_pnls = []
        fills = []
        costs = []
        slippage_reports = []
        execution_simulation_reports = []
        pending_backtest_orders = []
        features = self._build_features(data)

        for index, kline in enumerate(data):
            price = kline.close
            self.account.update_market_price(price, symbol=self.symbol)

            realized_before = self.account.realized_pnl
            fill_result = self.execution.on_bar(price=price, index=index)
            if fill_result is not None and fill_result["status"] in ["filled", "partial"]:
                fill_result = self._attach_order_lifecycle_contract(fill_result)
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
                execution_simulation_reports,
            )
            slippage_reports.extend(executed_reports)

            executions = self.strategy.on_bar(features, index)
            for signal in executions:
                if not getattr(signal, "is_orderable", True):
                    continue
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
                    execution_simulation_reports=execution_simulation_reports,
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
            "order_lifecycle_reports": [
                fill["order_lifecycle"] for fill in fills if "order_lifecycle" in fill
            ],
            "execution_simulation_reports": execution_simulation_reports,
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
        execution_simulation_reports,
    ):
        remaining_orders = []
        reports = []
        for pending_order in pending_backtest_orders:
            if pending_order["execute_index"] > index:
                remaining_orders.append(pending_order)
                continue

            if pending_order.get("event") == "timeout":
                recovery_order = self._record_timeout_and_schedule_recovery(
                    pending_order=pending_order,
                    data=data,
                    execution_simulation_reports=execution_simulation_reports,
                )
                if recovery_order is not None:
                    remaining_orders.append(recovery_order)
                continue

            report = self._execute_order_signal(
                order_signal=pending_order["order_signal"],
                signal_index=pending_order["signal_index"],
                execute_index=index,
                data=data,
                fills=fills,
                costs=costs,
                realized_trade_pnls=realized_trade_pnls,
                execution_simulation_reports=execution_simulation_reports,
                recovered_from_timeout=pending_order.get("event") == "recovery",
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
        execution_simulation_reports,
    ):
        order_signal = self._ensure_client_order_id(order_signal, signal_index)
        execute_index = signal_index + self._latency_bars()
        if execute_index >= len(data):
            return self._build_slippage_report(
                order_signal=order_signal,
                signal_index=signal_index,
                execute_index=execute_index,
                reference_price=data[signal_index].close,
            )

        if self.execution_model.timeout_recovery_bars > 0:
            timeout_order = {
                "event": "timeout",
                "order_signal": order_signal,
                "signal_index": signal_index,
                "execute_index": execute_index,
            }
            if execute_index <= signal_index:
                recovery_order = self._record_timeout_and_schedule_recovery(
                    pending_order=timeout_order,
                    data=data,
                    execution_simulation_reports=execution_simulation_reports,
                )
                if recovery_order is not None:
                    pending_backtest_orders.append(recovery_order)
                return None

            pending_backtest_orders.append(timeout_order)
            return None

        if execute_index > signal_index:
            pending_backtest_orders.append(
                {
                    "event": "execute",
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
            execution_simulation_reports=execution_simulation_reports,
        )

    def _record_timeout_and_schedule_recovery(
        self,
        pending_order,
        data,
        execution_simulation_reports,
    ):
        order_signal = pending_order["order_signal"]
        timeout_index = pending_order["execute_index"]
        timeout_machine = self._build_lifecycle_machine(
            order_signal,
            final_status="unknown",
            timeout=True,
            filled_qty=0.0,
            remaining_qty=self._signal_quantity(order_signal),
        )
        timeout_result = self._attach_order_lifecycle_contract(
            self._timeout_result(order_signal),
            state_machine=timeout_machine,
            metadata={
                "engine": "BacktestEngine",
                "simulation_event": "timeout",
                "timeout_index": timeout_index,
                "duplicate_order_guard_active": True,
            },
        )
        execution_simulation_reports.append(
            {
                "event": "timeout",
                "status": "unknown",
                "client_order_id": timeout_result["client_order_id"],
                "symbol": timeout_result["symbol"],
                "signal_index": pending_order["signal_index"],
                "timeout_index": timeout_index,
                "recovery_index": timeout_index + self.execution_model.timeout_recovery_bars,
                "recovery_attempt": 1,
                "max_recovery_attempts": 1,
                "broker_place_called": False,
                "duplicate_order_guard_active": True,
                "live_orders_sent": False,
                "order_lifecycle": timeout_result["order_lifecycle"],
            }
        )

        recovery_index = timeout_index + self.execution_model.timeout_recovery_bars
        if recovery_index >= len(data):
            return None

        return {
            "event": "recovery",
            "order_signal": order_signal,
            "signal_index": pending_order["signal_index"],
            "execute_index": recovery_index,
            "timeout_index": timeout_index,
        }

    def _execute_order_signal(
        self,
        order_signal,
        signal_index,
        execute_index,
        data,
        fills,
        costs,
        realized_trade_pnls,
        execution_simulation_reports,
        recovered_from_timeout=False,
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
        submitted_signal = self._execution_order_signal(order_signal)
        result = self.execution.on_signal(
            submitted_signal,
            price=execution_price,
            index=execute_index,
        )
        if result["status"] in ["filled", "partial"]:
            result = self._normalize_execution_result(
                result,
                order_signal=order_signal,
                recovered_from_timeout=recovered_from_timeout,
            )
            state_machine = self._build_lifecycle_machine(
                order_signal,
                final_status=result["status"],
                timeout=recovered_from_timeout,
                filled_qty=result.get("filled_qty", 0.0),
                remaining_qty=result.get("remaining_qty", 0.0),
            )
            result = self._attach_order_lifecycle_contract(
                result,
                state_machine=state_machine,
                metadata={
                    "engine": "BacktestEngine",
                    "partial_fill_ratio": self.execution_model.partial_fill_ratio,
                    "recovered_from_timeout": recovered_from_timeout,
                },
            )
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
            self._append_execution_simulation_report(
                execution_simulation_reports,
                result=result,
                signal_index=signal_index,
                execute_index=execute_index,
                recovered_from_timeout=recovered_from_timeout,
            )

        return report

    def _execution_order_signal(self, order_signal):
        if self.execution_model.partial_fill_ratio == 1.0:
            return order_signal

        execution_signal = dict(order_signal)
        execution_signal["quantity"] = (
            self._signal_quantity(order_signal) * self.execution_model.partial_fill_ratio
        )
        return execution_signal

    def _normalize_execution_result(self, result, *, order_signal, recovered_from_timeout):
        normalized = dict(result)
        requested_qty = self._signal_quantity(order_signal)
        remaining_qty = max(0.0, requested_qty - float(normalized.get("filled_qty", 0.0)))
        normalized["requested_qty"] = requested_qty
        normalized["remaining_qty"] = remaining_qty
        normalized["broker_called"] = False
        normalized["live_orders_sent"] = False
        normalized["recovered_from_timeout"] = recovered_from_timeout

        if remaining_qty > 0.0:
            normalized["status"] = "partial"
            self._normalize_fill_events(normalized, remaining_qty)
        return normalized

    def _normalize_fill_events(self, result, remaining_qty):
        for key in ("fill_event",):
            if key in result:
                result[key] = dict(result[key])
                result[key]["status"] = result["status"]
                result[key]["remaining_qty"] = remaining_qty
                result[key]["cumulative_filled_qty"] = result["filled_qty"]

        if "fill_events" in result:
            result["fill_events"] = [dict(event) for event in result["fill_events"]]
            if result["fill_events"]:
                result["fill_events"][-1]["status"] = result["status"]
                result["fill_events"][-1]["remaining_qty"] = remaining_qty
                result["fill_events"][-1]["cumulative_filled_qty"] = result["filled_qty"]

    def _append_execution_simulation_report(
        self,
        execution_simulation_reports,
        *,
        result,
        signal_index,
        execute_index,
        recovered_from_timeout,
    ):
        event = "recovery_fill" if recovered_from_timeout else result["status"]
        execution_simulation_reports.append(
            {
                "event": event,
                "status": result["status"],
                "client_order_id": result["client_order_id"],
                "symbol": result.get("symbol", self.symbol),
                "signal_index": signal_index,
                "execute_index": execute_index,
                "filled_qty": result.get("filled_qty", 0.0),
                "remaining_qty": result.get("remaining_qty", 0.0),
                "broker_place_called": False,
                "duplicate_order_guard_active": True,
                "live_orders_sent": False,
                "order_lifecycle": result.get("order_lifecycle"),
            }
        )

    def _timeout_result(self, order_signal):
        return {
            "client_order_id": self._client_order_id(order_signal),
            "symbol": order_signal.get("symbol", self.symbol),
            "side": order_signal.get("signal"),
            "status": "unknown",
            "requested_qty": self._signal_quantity(order_signal),
            "filled_qty": 0.0,
            "remaining_qty": self._signal_quantity(order_signal),
            "broker_called": False,
            "live_orders_sent": False,
        }

    def _build_lifecycle_machine(
        self,
        order_signal,
        *,
        final_status,
        timeout,
        filled_qty,
        remaining_qty,
    ):
        client_order_id = self._client_order_id(order_signal)
        machine = ExecutionStateMachine()
        machine.transition(
            ExecutionEvent.ORDER_CREATED,
            ExecutionState.CREATED,
            client_order_id=client_order_id,
        )
        machine.transition(
            ExecutionEvent.ORDER_VALIDATED,
            ExecutionState.VALIDATED,
            client_order_id=client_order_id,
        )
        machine.transition(
            ExecutionEvent.ORDER_SUBMITTING,
            ExecutionState.SUBMITTING,
            client_order_id=client_order_id,
            metadata={"duplicate_order_guard_active": True},
        )
        if timeout:
            machine.transition(
                ExecutionEvent.ORDER_TIMEOUT,
                ExecutionState.TIMEOUT,
                client_order_id=client_order_id,
                reason="backtest_timeout_simulation",
            )
            machine.transition(
                ExecutionEvent.RECOVERY_STARTED,
                ExecutionState.RECOVERY,
                client_order_id=client_order_id,
                reason="backtest_timeout_recovery",
                metadata={"broker_place_called": False},
            )
        else:
            machine.transition(
                ExecutionEvent.ORDER_SUBMITTED,
                ExecutionState.SUBMITTED,
                client_order_id=client_order_id,
            )

        if final_status == "partial":
            machine.transition(
                ExecutionEvent.ORDER_PARTIALLY_FILLED,
                ExecutionState.PARTIALLY_FILLED,
                client_order_id=client_order_id,
                metadata={"filled_qty": filled_qty, "remaining_qty": remaining_qty},
            )
        elif final_status == "filled":
            machine.transition(
                ExecutionEvent.ORDER_FILLED,
                ExecutionState.FILLED,
                client_order_id=client_order_id,
                metadata={"filled_qty": filled_qty, "remaining_qty": remaining_qty},
            )
        return machine

    def _signal_quantity(self, order_signal):
        return float(order_signal.get("quantity", 0.0))

    def _client_order_id(self, order_signal):
        return order_signal.get("client_order_id") or f"backtest-{order_signal.get('signal_index', 'unknown')}"

    def _ensure_client_order_id(self, order_signal, signal_index):
        if order_signal.get("client_order_id"):
            return order_signal
        with_client_id = dict(order_signal)
        side = with_client_id.get("signal", "order")
        with_client_id["client_order_id"] = f"backtest-{self.symbol}-{signal_index}-{side}"
        return with_client_id

    def _attach_order_lifecycle_contract(self, execution_result, state_machine=None, metadata=None):
        if execution_result is None or "client_order_id" not in execution_result:
            return execution_result
        return attach_order_lifecycle_contract(
            execution_result,
            source=PayloadSource.BACKTEST,
            state_machine=state_machine or getattr(self.execution, "state_machine", None),
            dry_run=False,
            metadata=metadata or {"engine": "BacktestEngine"},
        )

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
