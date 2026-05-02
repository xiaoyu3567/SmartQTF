from typing import List, Optional

from pydantic import BaseModel

from quant.utils.time_format import add_display_times


class Kline(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_complete: Optional[bool] = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.timestamp < 0:
            raise ValueError("kline timestamp must be non-negative")

        prices = [self.open, self.high, self.low, self.close]
        if any(price <= 0 for price in prices):
            raise ValueError("kline OHLC prices must be positive")
        if self.high < self.low:
            raise ValueError("kline high must be greater than or equal to low")
        if not self.low <= self.open <= self.high:
            raise ValueError("kline open must be within low/high range")
        if not self.low <= self.close <= self.high:
            raise ValueError("kline close must be within low/high range")
        if self.volume < 0:
            raise ValueError("kline volume must be non-negative")

    def to_display_payload(self):
        if hasattr(self, "model_dump"):
            payload = self.model_dump(mode="json")
        else:
            payload = self.dict()
        return add_display_times(payload)


class KlineBatch(BaseModel):
    symbol: str
    timeframe: str
    venue: str
    klines: List[Kline]

    def __init__(self, **data):
        super().__init__(**data)
        timestamps = [kline.timestamp for kline in self.klines]
        if timestamps != sorted(timestamps):
            raise ValueError("klines must be sorted by timestamp")
        if len(timestamps) != len(set(timestamps)):
            raise ValueError("klines must not contain duplicate timestamps")

    def to_display_payload(self):
        if hasattr(self, "model_dump"):
            payload = self.model_dump(mode="json")
        else:
            payload = self.dict()
        return add_display_times(payload)


class Trade(BaseModel):
    timestamp: int
    price: float
    size: float
    side: str
    trade_id: Optional[str] = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.timestamp < 0:
            raise ValueError("trade timestamp must be non-negative")
        if self.price <= 0:
            raise ValueError("trade price must be positive")
        if self.size <= 0:
            raise ValueError("trade size must be positive")

        side = self.side.strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("trade side must be buy or sell")
        object.__setattr__(self, "side", side)
        if self.trade_id is not None:
            object.__setattr__(self, "trade_id", self.trade_id.strip() or None)

    def to_display_payload(self):
        if hasattr(self, "model_dump"):
            payload = self.model_dump(mode="json")
        else:
            payload = self.dict()
        return add_display_times(payload)
