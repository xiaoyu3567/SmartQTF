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


class StrategyAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"
    HOLD = "hold"
    WAIT = "wait"
    CANCEL = "cancel"
    INVALID = "invalid"
    NO_TRADE = "no_trade"


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
    UPTREND_HIGH_VOL = "uptrend_high_vol"
    UPTREND_NORMAL_VOL = "uptrend_normal_vol"
    UPTREND_LOW_VOL = "uptrend_low_vol"
    DOWNTREND_HIGH_VOL = "downtrend_high_vol"
    DOWNTREND_NORMAL_VOL = "downtrend_normal_vol"
    DOWNTREND_LOW_VOL = "downtrend_low_vol"
    RANGE_HIGH_VOL = "range_high_vol"
    RANGE_NORMAL_VOL = "range_normal_vol"
    RANGE_LOW_VOL = "range_low_vol"
    CHAOS = "chaos"
    UNKNOWN = "unknown"

    def legacy_kind(self) -> "RegimeKind":
        if self in {
            RegimeKind.UPTREND_HIGH_VOL,
            RegimeKind.UPTREND_NORMAL_VOL,
            RegimeKind.UPTREND_LOW_VOL,
            RegimeKind.DOWNTREND_HIGH_VOL,
            RegimeKind.DOWNTREND_NORMAL_VOL,
            RegimeKind.DOWNTREND_LOW_VOL,
        }:
            return RegimeKind.TREND
        if self in {
            RegimeKind.RANGE_HIGH_VOL,
            RegimeKind.RANGE_NORMAL_VOL,
            RegimeKind.RANGE_LOW_VOL,
        }:
            return RegimeKind.RANGE
        if self == RegimeKind.CHAOS:
            return RegimeKind.VOLATILE
        return self


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


class TimeoutFailureKind(str, Enum):
    API_TIMEOUT = "api_timeout"
    NETWORK_ERROR = "network_error"
    EXCHANGE_RESPONSE_DELAYED = "exchange_response_delayed"
    BROKER_ORDER_MISSING = "broker_order_missing"
    RECOVERY_QUERY_FAILED = "recovery_query_failed"
    UNKNOWN = "unknown"
