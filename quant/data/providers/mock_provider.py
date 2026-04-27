from abc import ABC, abstractmethod

from quant.data.schemas.market import Kline, Trade


class DataProvider(ABC):
    @abstractmethod
    def get_klines(self, symbol: str, timeframe: str):
        pass

    @abstractmethod
    def get_trades(self, symbol: str):
        pass


class MockProvider(DataProvider):
    def get_klines(self, symbol: str, timeframe: str):
        return [
            Kline(timestamp=1700000000, open=100.0, high=101.5, low=99.5, close=101.0, volume=1200.0),
            Kline(timestamp=1700000060, open=101.0, high=102.2, low=100.6, close=101.8, volume=1180.0),
            Kline(timestamp=1700000120, open=101.8, high=103.0, low=101.2, close=102.6, volume=1325.0),
            Kline(timestamp=1700000180, open=102.6, high=103.4, low=101.9, close=102.1, volume=1410.0),
            Kline(timestamp=1700000240, open=102.1, high=104.0, low=101.8, close=103.7, volume=1530.0),
            Kline(timestamp=1700000300, open=103.7, high=104.4, low=103.1, close=103.5, volume=1495.0),
            Kline(timestamp=1700000360, open=103.5, high=105.1, low=103.2, close=104.8, volume=1620.0),
            Kline(timestamp=1700000420, open=104.8, high=105.6, low=104.0, close=105.2, volume=1585.0),
            Kline(timestamp=1700000480, open=105.2, high=106.0, low=104.7, close=105.6, volume=1660.0),
            Kline(timestamp=1700000540, open=105.6, high=106.4, low=105.0, close=106.1, volume=1715.0),
        ]

    def get_trades(self, symbol: str):
        return [
            Trade(timestamp=1700000001, price=100.1, size=0.50, side="buy"),
            Trade(timestamp=1700000005, price=100.3, size=0.80, side="buy"),
            Trade(timestamp=1700000010, price=100.2, size=0.40, side="sell"),
            Trade(timestamp=1700000015, price=100.6, size=1.20, side="buy"),
            Trade(timestamp=1700000020, price=100.4, size=0.30, side="sell"),
            Trade(timestamp=1700000025, price=100.8, size=0.75, side="buy"),
            Trade(timestamp=1700000030, price=101.0, size=0.60, side="buy"),
            Trade(timestamp=1700000035, price=100.9, size=0.45, side="sell"),
            Trade(timestamp=1700000040, price=101.1, size=1.00, side="buy"),
            Trade(timestamp=1700000045, price=101.3, size=0.55, side="buy"),
            Trade(timestamp=1700000050, price=101.2, size=0.35, side="sell"),
            Trade(timestamp=1700000055, price=101.5, size=0.90, side="buy"),
            Trade(timestamp=1700000060, price=101.7, size=0.70, side="buy"),
            Trade(timestamp=1700000065, price=101.6, size=0.25, side="sell"),
            Trade(timestamp=1700000070, price=101.9, size=1.10, side="buy"),
            Trade(timestamp=1700000075, price=102.0, size=0.65, side="buy"),
            Trade(timestamp=1700000080, price=101.8, size=0.50, side="sell"),
            Trade(timestamp=1700000085, price=102.2, size=0.85, side="buy"),
            Trade(timestamp=1700000090, price=102.4, size=0.95, side="buy"),
            Trade(timestamp=1700000095, price=102.1, size=0.40, side="sell"),
        ]
