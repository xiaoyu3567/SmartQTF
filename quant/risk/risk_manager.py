class RiskManager:
    def __init__(
        self,
        max_position_pct=0.1,
        stop_loss_pct=0.02,
        take_profit_pct=0.04,
        max_drawdown_pct=0.1,
        symbol="BTCUSDT",
    ):
        if max_position_pct <= 0.0 or max_position_pct > 1.0:
            raise ValueError("max_position_pct must be between 0.0 and 1.0")
        if stop_loss_pct <= 0.0:
            raise ValueError("stop_loss_pct must be greater than 0.0")
        if take_profit_pct is not None and take_profit_pct <= 0.0:
            raise ValueError("take_profit_pct must be greater than 0.0")
        if max_drawdown_pct <= 0.0 or max_drawdown_pct > 1.0:
            raise ValueError("max_drawdown_pct must be between 0.0 and 1.0")

        self.max_position_pct = max_position_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.symbol = symbol

    def apply(self, signal, account, price):
        if signal is None or self.is_drawdown_exceeded(account):
            return None

        side = signal["signal"]
        qty = self._calculate_quantity(side, account, price)
        if qty <= 0.0:
            return None

        order_signal = dict(signal)
        order_signal["symbol"] = signal.get("symbol", self.symbol)
        order_signal["quantity"] = qty
        order_signal["stop_loss"] = self._calculate_stop_loss(side, price)

        take_profit = self._calculate_take_profit(side, price)
        if take_profit is not None:
            order_signal["take_profit"] = take_profit

        return order_signal

    def should_stop_loss(self, order_signal, price):
        side = order_signal["signal"]
        stop_loss = order_signal["stop_loss"]

        if side == "buy":
            return price <= stop_loss
        if side == "sell":
            return price >= stop_loss

        raise ValueError("signal must be buy or sell")

    def is_drawdown_exceeded(self, account):
        drawdown = (account.initial_balance - account.equity) / account.initial_balance
        return drawdown > self.max_drawdown_pct

    def _calculate_quantity(self, side, account, price):
        position = account.get_position(self.symbol)

        if side == "sell" and position.size > 0.0:
            return abs(position.size)

        return (account.balance * self.max_position_pct) / price

    def _calculate_stop_loss(self, side, price):
        if side == "buy":
            return price * (1.0 - self.stop_loss_pct)
        if side == "sell":
            return price * (1.0 + self.stop_loss_pct)

        raise ValueError("signal must be buy or sell")

    def _calculate_take_profit(self, side, price):
        if self.take_profit_pct is None:
            return None

        if side == "buy":
            return price * (1.0 + self.take_profit_pct)
        if side == "sell":
            return price * (1.0 - self.take_profit_pct)

        raise ValueError("signal must be buy or sell")
