import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

try:
    from pydantic import field_validator, model_validator
except ImportError:
    field_validator = None
    model_validator = None
    from pydantic import root_validator, validator

from quant.registry import PluginKind
from quant.schemas import AssetClass, PayloadSource, UniverseFilterConfig
from quant.schemas.base import SmartQTFModel


class MarketConfigBase(SmartQTFModel):
    symbol: str
    timeframe: str
    asset_class: AssetClass = AssetClass.CRYPTO
    enabled: bool = True
    provider: str = "mock"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value):
        if not value or not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()


class StrategyRouteConfigBase(SmartQTFModel):
    route: str = "default"
    strategy: str
    version: str = "1.0"
    parameters: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def non_empty_string(cls, value):
        if not value or not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()


class StrategyBindingBase(SmartQTFModel):
    symbol: str
    strategy: str
    route: str = "default"
    version: str = "1.0"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    routes: List["StrategyRouteConfig"] = Field(default_factory=list)

    @classmethod
    def non_empty_string(cls, value):
        if not value or not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()

    @classmethod
    def validate_route_values(cls, values):
        routes = values.get("routes") or []
        route_names = set()
        for route in routes:
            route_name = route.route
            if route_name in route_names:
                raise ValueError("strategy routes must not contain duplicate route entries")
            route_names.add(route_name)
        return values

    def route_configs(self):
        if self.routes:
            return list(self.routes)
        return [
            StrategyRouteConfig(
                route=self.route,
                strategy=self.strategy,
                version=self.version,
                parameters=dict(self.parameters),
            )
        ]

    def strategy_for_route(self, route):
        wanted_route = route.value if hasattr(route, "value") else str(route)
        fallback = None
        for route_config in self.route_configs():
            if route_config.route == wanted_route:
                return route_config
            if route_config.route == "default":
                fallback = route_config
        if fallback is not None:
            return fallback
        raise KeyError(f"no strategy route configured for {self.symbol}/{wanted_route}")


class RiskConfigBase(SmartQTFModel):
    risk_plugin: str = "default"
    kill_switch_enabled: bool = False
    daily_loss_limit_pct: Optional[float] = None
    consecutive_loss_limit: Optional[int] = None
    api_failure_rate_limit: Optional[float] = None
    max_position_size: float = 1.0
    max_drawdown: float = 0.2
    per_symbol_limits: Dict[str, float] = Field(default_factory=dict)

    @classmethod
    def validate_risk_values(cls, values):
        max_position_size = values.get("max_position_size")
        max_drawdown = values.get("max_drawdown")
        daily_loss_limit_pct = values.get("daily_loss_limit_pct")
        consecutive_loss_limit = values.get("consecutive_loss_limit")
        api_failure_rate_limit = values.get("api_failure_rate_limit")
        per_symbol_limits = values.get("per_symbol_limits") or {}

        if max_position_size is not None and max_position_size <= 0:
            raise ValueError("max_position_size must be positive")
        if max_drawdown is not None and not 0 < max_drawdown <= 1:
            raise ValueError("max_drawdown must be in (0, 1]")
        if daily_loss_limit_pct is not None and not 0 < daily_loss_limit_pct <= 1:
            raise ValueError("daily_loss_limit_pct must be in (0, 1]")
        if consecutive_loss_limit is not None and consecutive_loss_limit <= 0:
            raise ValueError("consecutive_loss_limit must be positive")
        if api_failure_rate_limit is not None and not 0 < api_failure_rate_limit <= 1:
            raise ValueError("api_failure_rate_limit must be in (0, 1]")
        if any(limit <= 0 for limit in per_symbol_limits.values()):
            raise ValueError("per_symbol_limits must be positive")
        return values


class BrokerConfigBase(SmartQTFModel):
    mode: PayloadSource = PayloadSource.PAPER
    broker_plugin: str = "simulated"
    account_id: Optional[str] = None
    order_log_path: str = "logs/trades.jsonl"
    settings: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def live_mode_requires_account(cls, values):
        mode = values.get("mode")
        account_id = values.get("account_id")
        if mode == PayloadSource.LIVE and not account_id:
            raise ValueError("live broker mode requires account_id")
        return values


class LoggingConfig(SmartQTFModel):
    decision_log_path: str = "logs/decisions.jsonl"
    order_log_path: str = "logs/orders.jsonl"
    fill_log_path: str = "logs/fills.jsonl"
    pipeline_report_dir: str = "logs/pipeline-runs"


class ScanConfigBase(SmartQTFModel):
    enabled: bool = True
    interval_seconds: int = 600
    candidate_symbols: List[str] = Field(default_factory=list)
    holding_symbols: List[str] = Field(default_factory=list)
    default_timeframe: Optional[str] = None
    universe_enabled: bool = False
    universe_filter: UniverseFilterConfig = Field(default_factory=UniverseFilterConfig)
    universe_max_symbols: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def normalize_symbol_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            raise ValueError("scan symbols must be a list")

        normalized = []
        seen = set()
        for symbol in value:
            if not symbol or not str(symbol).strip():
                raise ValueError("scan symbols must not contain empty values")
            clean_symbol = str(symbol).strip()
            if clean_symbol not in seen:
                normalized.append(clean_symbol)
                seen.add(clean_symbol)
        return normalized

    @classmethod
    def validate_scan_values(cls, values):
        interval_seconds = values.get("interval_seconds")
        default_timeframe = values.get("default_timeframe")
        universe_max_symbols = values.get("universe_max_symbols")
        if interval_seconds is not None and interval_seconds <= 0:
            raise ValueError("scan interval_seconds must be positive")
        if default_timeframe is not None and not str(default_timeframe).strip():
            raise ValueError("scan default_timeframe must not be empty")
        if default_timeframe is not None:
            values["default_timeframe"] = str(default_timeframe).strip()
        if universe_max_symbols is not None and universe_max_symbols <= 0:
            raise ValueError("scan universe_max_symbols must be positive")
        return values


class RuntimeConfigBase(SmartQTFModel):
    name: str = "default"
    source: PayloadSource = PayloadSource.PAPER
    markets: List[MarketConfigBase]
    strategies: List[StrategyBindingBase]
    risk: RiskConfigBase = Field(default_factory=RiskConfigBase)
    broker: BrokerConfigBase = Field(default_factory=BrokerConfigBase)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    scan: ScanConfigBase = Field(default_factory=ScanConfigBase)
    registry_plugins: Dict[PluginKind, str] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def validate_runtime_config(cls, values):
        markets = values.get("markets") or []
        strategies = values.get("strategies") or []
        source = values.get("source")
        broker = values.get("broker")

        market_keys = set()
        enabled_symbols = set()
        for market in markets:
            key = (market.symbol, market.timeframe)
            if key in market_keys:
                raise ValueError("markets must not contain duplicate symbol/timeframe entries")
            market_keys.add(key)
            if market.enabled:
                enabled_symbols.add(market.symbol)

        strategy_symbols = set()
        for binding in strategies:
            if binding.symbol in strategy_symbols:
                raise ValueError("strategies must not contain duplicate symbol bindings")
            strategy_symbols.add(binding.symbol)
            if binding.symbol not in enabled_symbols:
                raise ValueError("strategy bindings must reference enabled market symbols")

        if broker is not None and source is not None and broker.mode != source:
            raise ValueError("broker mode must match runtime source")
        return values

    def enabled_markets(self):
        return [market for market in self.markets if market.enabled]

    def strategy_for_symbol(self, symbol):
        for binding in self.strategies:
            if binding.symbol == symbol:
                return binding
        raise KeyError(f"no strategy binding configured for {symbol}")


if hasattr(BaseModel, "model_validate"):

    class StrategyRouteConfig(StrategyRouteConfigBase):
        @field_validator("route", "strategy", "version")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class MarketConfig(MarketConfigBase):
        @field_validator("symbol", "timeframe", "provider")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class StrategyBinding(StrategyBindingBase):
        routes: List[StrategyRouteConfig] = Field(default_factory=list)

        @field_validator("symbol", "strategy", "route", "version")
        @classmethod
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @model_validator(mode="after")
        def validate_values(self):
            values = self.__dict__.copy()
            self.validate_route_values(values)
            return self

    class RiskConfig(RiskConfigBase):
        @model_validator(mode="after")
        def validate_values(self):
            values = self.__dict__.copy()
            self.validate_risk_values(values)
            return self

    class BrokerConfig(BrokerConfigBase):
        @model_validator(mode="after")
        def validate_values(self):
            values = self.__dict__.copy()
            self.live_mode_requires_account(values)
            return self

    class ScanConfig(ScanConfigBase):
        @field_validator("candidate_symbols", "holding_symbols")
        @classmethod
        def validate_scan_symbols(cls, value):
            return cls.normalize_symbol_list(value)

        @model_validator(mode="after")
        def validate_values(self):
            values = self.__dict__.copy()
            self.validate_scan_values(values)
            if values["default_timeframe"] != self.default_timeframe:
                self.default_timeframe = values["default_timeframe"]
            return self

    class RuntimeConfig(RuntimeConfigBase):
        markets: List[MarketConfig]
        strategies: List[StrategyBinding]
        risk: RiskConfig = Field(default_factory=RiskConfig)
        broker: BrokerConfig = Field(default_factory=BrokerConfig)
        scan: ScanConfig = Field(default_factory=ScanConfig)

        @field_validator("markets", "strategies")
        @classmethod
        def validate_non_empty_list(cls, value):
            if not value:
                raise ValueError("runtime config requires at least one item")
            return value

        @model_validator(mode="after")
        def validate_values(self):
            values = self.__dict__.copy()
            self.validate_runtime_config(values)
            return self

else:

    class StrategyRouteConfig(StrategyRouteConfigBase):
        @validator("route", "strategy", "version")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class MarketConfig(MarketConfigBase):
        @validator("symbol", "timeframe", "provider")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

    class StrategyBinding(StrategyBindingBase):
        routes: List[StrategyRouteConfig] = Field(default_factory=list)

        @validator("symbol", "strategy", "route", "version")
        def validate_non_empty_string(cls, value):
            return cls.non_empty_string(value)

        @root_validator
        def validate_values(cls, values):
            return cls.validate_route_values(values)

    class RiskConfig(RiskConfigBase):
        @root_validator
        def validate_values(cls, values):
            return cls.validate_risk_values(values)

    class BrokerConfig(BrokerConfigBase):
        @root_validator
        def validate_values(cls, values):
            return cls.live_mode_requires_account(values)

    class ScanConfig(ScanConfigBase):
        @validator("candidate_symbols", "holding_symbols", pre=True)
        def validate_scan_symbols(cls, value):
            return cls.normalize_symbol_list(value)

        @root_validator
        def validate_values(cls, values):
            return cls.validate_scan_values(values)

    class RuntimeConfig(RuntimeConfigBase):
        markets: List[MarketConfig]
        strategies: List[StrategyBinding]
        risk: RiskConfig = Field(default_factory=RiskConfig)
        broker: BrokerConfig = Field(default_factory=BrokerConfig)
        scan: ScanConfig = Field(default_factory=ScanConfig)

        @validator("markets", "strategies")
        def validate_non_empty_list(cls, value):
            if not value:
                raise ValueError("runtime config requires at least one item")
            return value

        @root_validator
        def validate_values(cls, values):
            return cls.validate_runtime_config(values)


def load_runtime_config(path):
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return RuntimeConfig.from_payload(payload)
