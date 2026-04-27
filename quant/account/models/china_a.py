from quant.account.models.base import BaseAccount


class ChinaAAccount(BaseAccount):
    def __init__(self, initial_balance: float):
        super().__init__(initial_balance)
        self.today_buys = {}
        self.current_day = 0

    def set_trading_day(self, day: int):
        if day != self.current_day:
            self.current_day = day
            self.today_buys = {}

    def on_fill(self, fill):
        symbol = fill.get("symbol", "DEFAULT")
        side = fill["side"]
        qty = float(fill["filled_qty"])

        if side == "sell":
            position = self.get_position(symbol)
            locked_qty = self.today_buys.get(symbol, 0.0)
            sellable_qty = position.size - locked_qty
            if qty > sellable_qty:
                raise ValueError("China A-shares T+1 rule: same-day buys cannot be sold")

        position = self._apply_fill(fill, allow_short=False)

        if side == "buy":
            self.today_buys[symbol] = self.today_buys.get(symbol, 0.0) + qty

        return position
