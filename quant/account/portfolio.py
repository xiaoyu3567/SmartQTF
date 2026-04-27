class Portfolio:
    def __init__(self):
        self.positions = {}
        self.market_prices = {}

    def get_position(self, symbol):
        return self.positions.get(symbol)

    def set_position(self, symbol, position):
        self.positions[symbol] = position

    def update_market_price(self, symbol, price):
        self.market_prices[symbol] = float(price)

    def total_unrealized_pnl(self):
        return sum(
            position.unrealized_pnl(self.market_prices.get(symbol, position.avg_price))
            for symbol, position in self.positions.items()
        )

    def total_position_value(self):
        return sum(
            position.market_value(self.market_prices.get(symbol, position.avg_price))
            for symbol, position in self.positions.items()
        )
