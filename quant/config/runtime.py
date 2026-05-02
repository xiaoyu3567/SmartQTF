import json
from enum import Enum
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


class RuntimeEnvironmentTier(str, Enum):
    UNSPECIFIED = "unspecified"
    MOCK = "mock"
    PAPER = "paper"
    EXCHANGE_SANDBOX = "exchange_sandbox"
    LIVE_READ_ONLY = "live_read_only"
    LIVE_DRY_RUN = "live_dry_run"
    LIVE_TRADING = "live_trading"


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


class EnvironmentConfigBase(SmartQTFModel):
    tier: RuntimeEnvironmentTier = RuntimeEnvironmentTier.UNSPECIFIED
    external_exchange_access: bool = False
    private_api_read: bool = False
    live_order_submission: bool = False
    dry_run: bool = True
    requires_proxy: bool = False
    requires_credentials: bool = False
    requires_manual_preflight: bool = False
    requires_human_approval: bool = False
    tests_default_skipped: bool = False
    credential_mode: str = "none"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def validate_environment_values(cls, values):
        tier = cls._enum_value(values.get("tier") or RuntimeEnvironmentTier.UNSPECIFIED)
        live_order_submission = values.get("live_order_submission") is True
        dry_run = values.get("dry_run") is not False
        external_exchange_access = values.get("external_exchange_access") is True
        requires_proxy = values.get("requires_proxy") is True
        requires_credentials = values.get("requires_credentials") is True
        requires_manual_preflight = values.get("requires_manual_preflight") is True
        requires_human_approval = values.get("requires_human_approval") is True
        tests_default_skipped = values.get("tests_default_skipped") is True
        credential_mode = str(values.get("credential_mode") or "none").strip().lower()
        values["credential_mode"] = credential_mode

        live_tiers = {
            RuntimeEnvironmentTier.EXCHANGE_SANDBOX.value,
            RuntimeEnvironmentTier.LIVE_READ_ONLY.value,
            RuntimeEnvironmentTier.LIVE_DRY_RUN.value,
            RuntimeEnvironmentTier.LIVE_TRADING.value,
        }

        if live_order_submission and tier != RuntimeEnvironmentTier.LIVE_TRADING.value:
            raise ValueError("live_order_submission is only valid for live_trading tier")
        if live_order_submission and dry_run:
            raise ValueError("live_order_submission requires dry_run=false")

        if tier in live_tiers:
            if not external_exchange_access:
                raise ValueError("exchange/live tiers must mark external_exchange_access=true")
            if not requires_proxy:
                raise ValueError("exchange/live tiers must require proxy")
            if not requires_credentials:
                raise ValueError("exchange/live tiers must require credentials")
            if not requires_human_approval:
                raise ValueError("exchange/live tiers must require human approval")
            if not tests_default_skipped:
                raise ValueError("exchange/live tiers must keep external tests skipped by default")
            if credential_mode in {"", "none", "fixture"}:
                raise ValueError("exchange/live tiers require a non-fixture credential_mode")

        if tier in {
            RuntimeEnvironmentTier.LIVE_DRY_RUN.value,
            RuntimeEnvironmentTier.LIVE_TRADING.value,
        } and not requires_manual_preflight:
            raise ValueError("live dry-run and live trading tiers require manual preflight")

        if tier == RuntimeEnvironmentTier.LIVE_READ_ONLY.value and live_order_submission:
            raise ValueError("live_read_only tier cannot submit live orders")
        if tier == RuntimeEnvironmentTier.LIVE_DRY_RUN.value and live_order_submission:
            raise ValueError("live_dry_run tier cannot submit live orders")
        if tier == RuntimeEnvironmentTier.LIVE_TRADING.value:
            if not live_order_submission:
                raise ValueError("live_trading tier must explicitly set live_order_submission=true")
            if dry_run:
                raise ValueError("live_trading tier requires dry_run=false")

        if tier in {
            RuntimeEnvironmentTier.MOCK.value,
            RuntimeEnvironmentTier.PAPER.value,
        }:
            if external_exchange_access:
                raise ValueError("mock and paper tiers must not require external exchange access")
            if requires_credentials:
                raise ValueError("mock and paper tiers must not require credentials")
            if live_order_submission:
                raise ValueError("mock and paper tiers cannot submit live orders")

        return values

    @staticmethod
    def _enum_value(value):
        return value.value if hasattr(value, "value") else str(value)


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


class MultiTimeframeConfigBase(SmartQTFModel):
    enabled: bool = False
    execution_timeframe: Optional[str] = None
    context_timeframes: List[str] = Field(default_factory=list)
    bar_limits: Dict[str, int] = Field(default_factory=dict)
    default_bar_limit: int = 100
    venue: str = "runtime"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def validate_multi_timeframe_values(cls, values):
        enabled = values.get("enabled") is True
        execution_timeframe = values.get("execution_timeframe")
        context_timeframes = values.get("context_timeframes") or []
        bar_limits = values.get("bar_limits") or {}
        default_bar_limit = values.get("default_bar_limit")
        venue = values.get("venue")

        if execution_timeframe is not None:
            execution_timeframe = str(execution_timeframe).strip()
            if not execution_timeframe:
                raise ValueError("multi_timeframe execution_timeframe must not be empty")
            values["execution_timeframe"] = execution_timeframe

        normalized_contexts = []
        seen_contexts = set()
        for timeframe in context_timeframes:
            clean_timeframe = str(timeframe).strip()
            if not clean_timeframe:
                raise ValueError("multi_timeframe context_timeframes must not contain empty values")
            if clean_timeframe in seen_contexts:
                raise ValueError("multi_timeframe context_timeframes must be unique")
            normalized_contexts.append(clean_timeframe)
            seen_contexts.add(clean_timeframe)
        values["context_timeframes"] = normalized_contexts

        if execution_timeframe is not None and execution_timeframe in seen_contexts:
            raise ValueError("multi_timeframe context_timeframes must not include execution_timeframe")

        if default_bar_limit is not None and int(default_bar_limit) <= 0:
            raise ValueError("multi_timeframe default_bar_limit must be positive")
        if default_bar_limit is not None:
            values["default_bar_limit"] = int(default_bar_limit)

        normalized_limits = {}
        for timeframe, limit in bar_limits.items():
            clean_timeframe = str(timeframe).strip()
            if not clean_timeframe:
                raise ValueError("multi_timeframe bar_limits keys must not be empty")
            numeric_limit = int(limit)
            if numeric_limit <= 0:
                raise ValueError("multi_timeframe bar_limits values must be positive")
            normalized_limits[clean_timeframe] = numeric_limit
        values["bar_limits"] = normalized_limits

        if venue is not None:
            values["venue"] = str(venue).strip() or "runtime"

        if enabled:
            if execution_timeframe is None:
                raise ValueError("multi_timeframe enabled requires execution_timeframe")
            if not normalized_contexts:
                raise ValueError("multi_timeframe enabled requires context_timeframes")

        return values

    def limit_for_timeframe(self, timeframe):
        return int(self.bar_limits.get(timeframe, self.default_bar_limit))


class RuntimeConfigBase(SmartQTFModel):
    name: str = "default"
    source: PayloadSource = PayloadSource.PAPER
    markets: List[MarketConfigBase]
    strategies: List[StrategyBindingBase]
    risk: RiskConfigBase = Field(default_factory=RiskConfigBase)
    broker: BrokerConfigBase = Field(default_factory=BrokerConfigBase)
    environment: EnvironmentConfigBase = Field(default_factory=EnvironmentConfigBase)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    scan: ScanConfigBase = Field(default_factory=ScanConfigBase)
    multi_timeframe: MultiTimeframeConfigBase = Field(default_factory=MultiTimeframeConfigBase)
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
        cls.validate_multi_timeframe_runtime_values(values)
        cls.validate_environment_runtime_values(values)
        return values

    @classmethod
    def validate_multi_timeframe_runtime_values(cls, values):
        multi_timeframe = values.get("multi_timeframe")
        if multi_timeframe is None:
            return values
        enabled = cls._field_value(multi_timeframe, "enabled", False) is True
        if not enabled:
            return values

        execution_timeframe = cls._field_value(multi_timeframe, "execution_timeframe", None)
        markets = values.get("markets") or []
        enabled_markets = [market for market in markets if cls._field_value(market, "enabled", True)]
        if execution_timeframe is None:
            raise ValueError("multi_timeframe enabled requires execution_timeframe")
        if not any(cls._field_value(market, "timeframe", None) == execution_timeframe for market in enabled_markets):
            raise ValueError("multi_timeframe execution_timeframe must match an enabled market timeframe")
        return values

    @classmethod
    def apply_runtime_config_normalized_values(cls, target, values):
        multi_timeframe = values.get("multi_timeframe")
        if multi_timeframe is not None and getattr(target, "multi_timeframe", None) is not multi_timeframe:
            target.multi_timeframe = multi_timeframe
        environment = values.get("environment")
        if environment is not None and getattr(target, "environment", None) is not environment:
            target.environment = environment
        return target

    @classmethod
    def validate_environment_runtime_values(cls, values):
        environment = values.get("environment")
        if environment is None:
            return values

        tier = cls._field_value(environment, "tier", RuntimeEnvironmentTier.UNSPECIFIED)
        tier = tier.value if hasattr(tier, "value") else str(tier)
        if tier == RuntimeEnvironmentTier.UNSPECIFIED.value:
            return values

        source = cls._field_value(values.get("source"), "value", values.get("source"))
        source = source.value if hasattr(source, "value") else str(source)
        broker = values.get("broker")
        broker_settings = cls._field_value(broker, "settings", {}) or {}
        broker_mode = cls._field_value(broker, "mode", None)
        broker_mode = broker_mode.value if hasattr(broker_mode, "value") else str(broker_mode)

        if values.get("metadata", {}).get("contains_real_credentials") is True:
            raise ValueError("runtime config must not embed real credentials")

        if tier == RuntimeEnvironmentTier.MOCK.value and source not in {
            PayloadSource.BACKTEST.value,
            PayloadSource.PAPER.value,
        }:
            raise ValueError("mock environment tier only supports backtest or paper source")
        if tier == RuntimeEnvironmentTier.PAPER.value and source != PayloadSource.PAPER.value:
            raise ValueError("paper environment tier requires paper source")

        exchange_live_tiers = {
            RuntimeEnvironmentTier.EXCHANGE_SANDBOX.value,
            RuntimeEnvironmentTier.LIVE_READ_ONLY.value,
            RuntimeEnvironmentTier.LIVE_DRY_RUN.value,
            RuntimeEnvironmentTier.LIVE_TRADING.value,
        }
        if tier in exchange_live_tiers and source != PayloadSource.LIVE.value:
            raise ValueError("exchange/live environment tiers require live source")
        if tier in exchange_live_tiers and broker_mode != PayloadSource.LIVE.value:
            raise ValueError("exchange/live environment tiers require live broker mode")

        allow_live_orders = broker_settings.get("allow_live_orders")
        dry_run_setting = broker_settings.get("dry_run")
        require_manual_preflight = broker_settings.get("require_manual_preflight")
        credential_mode = broker_settings.get("credential_mode")
        if credential_mode is None:
            credential_mode = cls._field_value(environment, "credential_mode", None)
        credential_mode = str(credential_mode or "").strip().lower()

        if tier in {
            RuntimeEnvironmentTier.EXCHANGE_SANDBOX.value,
            RuntimeEnvironmentTier.LIVE_READ_ONLY.value,
            RuntimeEnvironmentTier.LIVE_DRY_RUN.value,
        } and allow_live_orders is not False:
            raise ValueError(f"{tier} environment tier requires broker.settings.allow_live_orders=false")

        if tier == RuntimeEnvironmentTier.LIVE_DRY_RUN.value and dry_run_setting is False:
            raise ValueError("live_dry_run environment tier must not set broker.settings.dry_run=false")

        if tier == RuntimeEnvironmentTier.LIVE_TRADING.value:
            if allow_live_orders is not True:
                raise ValueError("live_trading environment tier requires broker.settings.allow_live_orders=true")
            if dry_run_setting is not False:
                raise ValueError("live_trading environment tier requires broker.settings.dry_run=false")
            if require_manual_preflight is not True:
                raise ValueError("live_trading environment tier requires broker.settings.require_manual_preflight=true")
            if credential_mode in {"", "none", "fixture"}:
                raise ValueError("live_trading environment tier requires non-fixture credential_mode")

        return values

    @staticmethod
    def _field_value(target, name, default=None):
        if target is None:
            return default
        if isinstance(target, dict):
            return target.get(name, default)
        return getattr(target, name, default)

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

    class EnvironmentConfig(EnvironmentConfigBase):
        @model_validator(mode="after")
        def validate_values(self):
            values = self.__dict__.copy()
            self.validate_environment_values(values)
            self.credential_mode = values["credential_mode"]
            return self

    class MultiTimeframeConfig(MultiTimeframeConfigBase):
        @model_validator(mode="after")
        def validate_values(self):
            values = self.__dict__.copy()
            self.validate_multi_timeframe_values(values)
            self.execution_timeframe = values["execution_timeframe"]
            self.context_timeframes = values["context_timeframes"]
            self.bar_limits = values["bar_limits"]
            self.default_bar_limit = values["default_bar_limit"]
            self.venue = values["venue"]
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
        environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
        scan: ScanConfig = Field(default_factory=ScanConfig)
        multi_timeframe: MultiTimeframeConfig = Field(default_factory=MultiTimeframeConfig)

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
            self.apply_runtime_config_normalized_values(self, values)
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

    class EnvironmentConfig(EnvironmentConfigBase):
        @root_validator
        def validate_values(cls, values):
            return cls.validate_environment_values(values)

    class MultiTimeframeConfig(MultiTimeframeConfigBase):
        @root_validator
        def validate_values(cls, values):
            return cls.validate_multi_timeframe_values(values)

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
        environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
        scan: ScanConfig = Field(default_factory=ScanConfig)
        multi_timeframe: MultiTimeframeConfig = Field(default_factory=MultiTimeframeConfig)

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
