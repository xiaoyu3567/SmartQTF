from quant.data.schemas.market import Trade
from quant.features.base.feature import Feature


class OrderFlowImbalance(Feature):
    def compute(self, data: list[Trade]) -> float:
        buy_size = sum(trade.size for trade in data if trade.side == "buy")
        sell_size = sum(trade.size for trade in data if trade.side == "sell")
        return buy_size - sell_size
