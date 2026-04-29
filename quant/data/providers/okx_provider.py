from typing import Optional

from adapters.exchange.okx import OKXAdapter
from quant.data.providers.mock_provider import DataProvider
from quant.data.schemas.market import KlineBatch
from quant.data.universe import build_universe_snapshot
from quant.schemas import (
    FundingRateSnapshot,
    NetflowSnapshot,
    OpenInterestSnapshot,
    OrderBookSnapshot,
    UniverseFilterConfig,
    UniverseSnapshot,
)


class OKXDataProvider(DataProvider):
    """OKX public market data provider returning typed SmartQTF payloads."""

    def __init__(self, adapter: Optional[OKXAdapter] = None):
        self.adapter = adapter or OKXAdapter(require_credentials=False)

    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: int = 100,
    ):
        return self.get_kline_batch(
            symbol=symbol,
            timeframe=timeframe,
            start_ts=start_ts,
            end_ts=end_ts,
            limit=limit,
        ).klines

    def get_kline_batch(
        self,
        symbol: str,
        timeframe: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: int = 100,
    ) -> KlineBatch:
        return self.adapter.get_klines(
            symbol=symbol,
            timeframe=timeframe,
            start_ts=start_ts,
            end_ts=end_ts,
            limit=limit,
        )

    def get_open_interest(self, symbol: str) -> OpenInterestSnapshot:
        return self.adapter.get_open_interest(symbol)

    def get_funding_rate(self, symbol: str) -> FundingRateSnapshot:
        return self.adapter.get_funding_rate(symbol)

    def get_trades(self, symbol: str, limit: int = 100):
        return self.adapter.get_trades(symbol=symbol, limit=limit)

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        return self.adapter.get_orderbook(symbol=symbol, depth=depth)

    def get_netflow(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> NetflowSnapshot:
        return self.adapter.get_netflow(symbol=symbol, timeframe=timeframe, limit=limit)

    def discover_universe(
        self,
        filter_config: Optional[UniverseFilterConfig] = None,
        *,
        as_of_timestamp: Optional[int] = None,
    ) -> UniverseSnapshot:
        config = filter_config or UniverseFilterConfig()
        instruments = self.adapter.get_universe_instruments(instrument_type=config.instrument_type)
        return build_universe_snapshot(
            instruments,
            config,
            as_of_timestamp=as_of_timestamp,
            source="okx_public_instruments_and_tickers",
        )
