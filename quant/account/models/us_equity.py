from quant.account.models.base import BaseAccount


class USEquityAccount(BaseAccount):
    def on_fill(self, fill):
        return self._apply_fill(fill, allow_short=False)
