from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Position:
    size: float = 0.0
    avg_price: float = 0.0
    side: str | None = None
    realized_pnl: float = 0.0

    def apply_fill(self, order_side: str, price: float, qty: float) -> float:
        signed_qty = qty if order_side == "buy" else -qty
        realized = 0.0

        if self.size == 0.0:
            self.size = signed_qty
            self.avg_price = price
            self.side = self._side_from_size(self.size)
            return realized

        if self.size * signed_qty > 0:
            new_size = self.size + signed_qty
            total_cost = abs(self.size) * self.avg_price + abs(signed_qty) * price
            self.avg_price = total_cost / abs(new_size)
            self.size = new_size
            self.side = self._side_from_size(self.size)
            return realized

        closing_qty = min(abs(self.size), abs(signed_qty))
        if self.size > 0:
            realized = (price - self.avg_price) * closing_qty
        else:
            realized = (self.avg_price - price) * closing_qty

        new_size = self.size + signed_qty
        self.realized_pnl += realized

        if new_size == 0.0:
            self.size = 0.0
            self.avg_price = 0.0
            self.side = None
            return realized

        if self.size * new_size > 0:
            self.size = new_size
            self.side = self._side_from_size(self.size)
            return realized

        self.size = new_size
        self.avg_price = price
        self.side = self._side_from_size(self.size)
        return realized

    def unrealized_pnl(self, price: float) -> float:
        if self.size == 0.0:
            return 0.0
        return (price - self.avg_price) * self.size

    def market_value(self, price: float) -> float:
        return self.size * price

    def _side_from_size(self, size: float):
        if size > 0.0:
            return "long"
        if size < 0.0:
            return "short"
        return None


class BaseAccount(ABC):
    def __init__(self, initial_balance: float):
        self.initial_balance = float(initial_balance)
        self.balance = float(initial_balance)
        self.equity = float(initial_balance)
        self.positions = {}
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.market_prices = {}

    @abstractmethod
    def on_fill(self, fill):
        pass

    def update_market_price(self, price, symbol="DEFAULT"):
        self.market_prices[symbol] = float(price)
        self.unrealized_pnl = sum(
            position.unrealized_pnl(self.market_prices.get(position_symbol, position.avg_price))
            for position_symbol, position in self.positions.items()
        )
        position_value = sum(
            position.market_value(self.market_prices.get(position_symbol, position.avg_price))
            for position_symbol, position in self.positions.items()
        )
        self.equity = self.balance + position_value
        return self.unrealized_pnl

    def get_position(self, symbol="DEFAULT"):
        return self.positions.setdefault(symbol, Position())

    def _validate_fill(self, fill):
        side = fill["side"]
        qty = float(fill["filled_qty"])
        if side not in ["buy", "sell"]:
            raise ValueError("fill side must be buy or sell")
        if qty <= 0.0:
            raise ValueError("filled_qty must be greater than 0")

    def _apply_fill(self, fill, allow_short: bool):
        self._validate_fill(fill)

        symbol = fill.get("symbol", "DEFAULT")
        side = fill["side"]
        price = float(fill["fill_price"])
        qty = float(fill["filled_qty"])
        position = self.get_position(symbol)
        signed_qty = qty if side == "buy" else -qty

        if not allow_short and position.size + signed_qty < 0.0:
            raise ValueError("short position is not allowed")

        cash_flow = -price * qty if side == "buy" else price * qty
        realized = position.apply_fill(side, price, qty)

        self.balance += cash_flow
        self.realized_pnl += realized
        self.market_prices[symbol] = price
        self.update_market_price(price, symbol=symbol)
        return position
