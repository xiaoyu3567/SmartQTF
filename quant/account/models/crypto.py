from quant.account.models.base import BaseAccount


class CryptoAccount(BaseAccount):
    def __init__(self, initial_balance: float, leverage: float = 1.0):
        super().__init__(initial_balance)
        self.leverage = float(leverage)

    def on_fill(self, fill):
        return self._apply_fill(fill, allow_short=True)
