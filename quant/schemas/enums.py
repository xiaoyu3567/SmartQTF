from enum import Enum


class PayloadSource(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class LayerName(str, Enum):
    DATA = "data"
    FEATURE = "feature"
    REGIME = "regime"
    STRATEGY = "strategy"
    DECISION = "decision"
    RISK = "risk"
    PORTFOLIO = "portfolio"
    EXECUTION = "execution"
    ANALYTICS = "analytics"


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    US_EQUITY = "us_equity"
    CHINA_A = "china_a"


class MarketType(str, Enum):
    SPOT = "spot"
    MARGIN = "margin"
    PERPETUAL = "perpetual"
    FUTURES = "futures"


class DecisionAction(str, Enum):
    OPEN_LONG = "open_long"
    CLOSE_LONG = "close_long"
    OPEN_SHORT = "open_short"
    CLOSE_SHORT = "close_short"
    HOLD = "hold"


class RegimeKind(str, Enum):
    TREND = "trend"
    RANGE = "range"
    VOLATILE = "volatile"


class OrderKind(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class TimeInForce(str, Enum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    CREATED = "created"
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class ExchangeErrorCategory(str, Enum):
    RETRYABLE = "retryable"
    RECOVERABLE = "recoverable"
    FATAL = "fatal"
    CREDENTIAL_CONFIGURATION = "credential_configuration"


class TimeoutRecoveryAction(str, Enum):
    UPDATE_LOCAL_FROM_BROKER = "update_local_from_broker"
    MARK_UNKNOWN = "mark_unknown"
    RETRY_RECOVERY_LATER = "retry_recovery_later"
