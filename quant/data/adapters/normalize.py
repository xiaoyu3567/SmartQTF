from quant.data.schemas.market import Kline, Trade


def normalize_kline(raw: dict) -> Kline:
    return Kline(**raw)


def normalize_trade(raw: dict) -> Trade:
    return Trade(**raw)
