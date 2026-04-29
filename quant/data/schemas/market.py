from typing import List

from pydantic import BaseModel


class Kline(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


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


class Trade(BaseModel):
    timestamp: int
    price: float
    size: float
    side: str
