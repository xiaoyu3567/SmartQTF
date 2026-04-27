import random
from dataclasses import dataclass


@dataclass
class Order:
    id: int
    symbol: str
    side: str
    status: str
    qty: float
    filled_qty: float
    price: float
    created_index: int
    execute_index: int


@dataclass
class Position:
    size: float = 0.0
    entry_price: float = 0.0
    pnl: float = 0.0

    @property
    def avg_price(self):
        return self.entry_price

    def update(self, side, fill_price, filled_qty):
        signed_qty = filled_qty if side == "buy" else -filled_qty

        if self.size == 0.0 or self.size * signed_qty > 0:
            new_size = self.size + signed_qty
            total_cost = abs(self.size) * self.entry_price + abs(signed_qty) * fill_price
            self.entry_price = total_cost / abs(new_size)
            self.size = new_size
            return

        if abs(signed_qty) < abs(self.size):
            self.size += signed_qty
            return

        if abs(signed_qty) == abs(self.size):
            self.size = 0.0
            self.entry_price = 0.0
            return

        remaining_qty = signed_qty + self.size
        self.size = remaining_qty
        self.entry_price = fill_price


class ExecutionEngine:
    IDLE = "IDLE"
    ORDER_PENDING = "ORDER_PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    POSITION_OPEN = "POSITION_OPEN"
    EXIT = "EXIT"

    def __init__(self, execution_delay=0, seed=1, account=None, delay_across_bars=False):
        if execution_delay < 0 or execution_delay > 1:
            raise ValueError("execution_delay must be 0 or 1")

        self.execution_delay = execution_delay
        self.delay_across_bars = delay_across_bars
        self.random = random.Random(seed)
        self.state = self.IDLE
        self.orders = []
        self.pending_orders = []
        self.position = Position()
        self.account = account
        self.last_order_status = None
        self.next_order_id = 1

    def on_signal(self, signal, price, index):
        order = self._create_order(signal, price, index)

        if order is None:
            self.state = self.REJECTED
            self.last_order_status = self.REJECTED
            return {
                "status": "rejected",
            }

        self.orders.append(order)
        self.state = self.ORDER_PENDING

        if self.execution_delay == 1:
            self.pending_orders.append(order)
            return self._order_result(order)

        return self._match_order(order, price, index)

    def on_bar(self, price, index):
        due_orders = [order for order in self.pending_orders if order.execute_index == index]
        self.pending_orders = [order for order in self.pending_orders if order.execute_index > index]

        if not due_orders:
            return None

        return self._match_order(due_orders[0], price, index)

    def _create_order(self, signal, price, index):
        side = signal.get("signal")
        if side not in ["buy", "sell"]:
            return None

        order = Order(
            id=self.next_order_id,
            symbol=signal.get("symbol", "DEFAULT"),
            side=side,
            status="pending",
            qty=signal.get("quantity", 1.0),
            filled_qty=0.0,
            price=price,
            created_index=index,
            execute_index=index + 1 if self.delay_across_bars and self.execution_delay == 1 else index,
        )
        self.next_order_id += 1
        return order

    def _match_order(self, order, price, index):
        outcome = self._draw_outcome()

        if outcome == "rejected":
            order.status = "rejected"
            self.state = self.REJECTED
            self.last_order_status = self.REJECTED
            return self._order_result(order)

        slippage = self._calculate_slippage(price, order.side)
        fill_price = price + slippage

        if outcome == "partial":
            fill_ratio = self.random.uniform(0.1, 0.9)
            filled_qty = order.qty * fill_ratio
            order.status = "partial"
            order.filled_qty = filled_qty
            self.state = self.PARTIAL
            self.last_order_status = self.PARTIAL
        else:
            filled_qty = order.qty
            order.status = "filled"
            order.filled_qty = filled_qty
            self.state = self.FILLED
            self.last_order_status = self.FILLED

        self.position.update(order.side, fill_price, filled_qty)
        if self.position.size != 0.0:
            self.state = self.POSITION_OPEN

        result = self._order_result(order, fill_price=fill_price, slippage=slippage, fill_index=index)
        if self.account is not None:
            self.account.on_fill(result)
        return result

    def _order_result(self, order, fill_price=None, slippage=0.0, fill_index=None):
        result = {
            "order_id": order.id,
            "symbol": order.symbol,
            "side": order.side,
            "status": order.status,
            "filled_qty": order.filled_qty,
            "remaining_qty": order.qty - order.filled_qty,
        }

        if fill_price is not None:
            result["fill_price"] = fill_price
            result["slippage"] = slippage
            result["fill_index"] = fill_index

        return result

    def _draw_outcome(self):
        value = self.random.random()

        if value < 0.8:
            return "filled"
        if value < 0.95:
            return "partial"
        return "rejected"

    def _calculate_slippage(self, price, side):
        slippage = price * self.random.uniform(0.0005, 0.002)
        if side == "buy":
            return slippage
        return -slippage

    def exit_position(self):
        self.position = Position()
        self.state = self.EXIT
