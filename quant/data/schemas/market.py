from pydantic import BaseModel


class Kline(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class Trade(BaseModel):
    timestamp: int
    price: float
    size: float
    side: str
