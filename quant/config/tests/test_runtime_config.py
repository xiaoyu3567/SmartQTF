import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from quant.config import (
    BrokerConfig,
    MarketConfig,
    RiskConfig,
    RuntimeConfig,
    ScanConfig,
    StrategyBinding,
    StrategyRouteConfig,
    load_runtime_config,
)
from quant.orchestration import TradingRuntimeOrchestrator
from quant.registry import PluginKind, PluginRegistry
from quant.schemas import AssetClass, PayloadSource


def make_runtime_config(**overrides):
    payload = {
        "name": "paper-demo",
        "source": PayloadSource.PAPER,
        "markets": [
            MarketConfig(symbol="BTCUSDT", timeframe="1m", asset_class=AssetClass.CRYPTO),
        ],
        "strategies": [
            StrategyBinding(
                symbol="BTCUSDT",
                strategy="ma_crossover",
                route="trend",
                parameters={"fast_window": 3, "slow_window": 5},
            ),
        ],
        "broker": BrokerConfig(mode=PayloadSource.PAPER, broker_plugin="simulated"),
    }
    payload.update(overrides)
    return RuntimeConfig(**payload)


def test_runtime_config_round_trip_and_symbol_lookup():
    config = make_runtime_config()

    payload = config.to_payload()
    restored = RuntimeConfig.from_payload(payload)

    assert payload["source"] == "paper"
    assert payload["markets"][0]["symbol"] == "BTCUSDT"
    assert payload["scan"]["interval_seconds"] == 600
    assert restored.strategy_for_symbol("BTCUSDT").strategy == "ma_crossover"
    assert [market.symbol for market in restored.enabled_markets()] == ["BTCUSDT"]


def test_scan_config_accepts_candidate_and_holding_symbols():
    scan = ScanConfig(
        interval_seconds=300,
        candidate_symbols=[" BTCUSDT ", "ETHUSDT", "BTCUSDT"],
        holding_symbols=["ETHUSDT", "SOLUSDT"],
        default_timeframe=" 5m ",
        universe_enabled=True,
        universe_filter={"venue": "okx", "quote_currencies": ["USDT", "USDC"]},
        universe_max_symbols=25,
    )

    assert scan.interval_seconds == 300
    assert scan.candidate_symbols == ["BTCUSDT", "ETHUSDT"]
    assert scan.holding_symbols == ["ETHUSDT", "SOLUSDT"]
    assert scan.default_timeframe == "5m"
    assert scan.universe_enabled is True
    assert scan.universe_filter.venue == "okx"
    assert scan.universe_filter.quote_currencies == ["USDT", "USDC"]
    assert scan.universe_max_symbols == 25


def test_scan_config_rejects_invalid_interval():
    try:
        ScanConfig(interval_seconds=0)
    except ValidationError:
        pass
    else:
        raise AssertionError("scan interval must be positive")


def test_scan_config_rejects_invalid_universe_limit():
    try:
        ScanConfig(universe_max_symbols=0)
    except ValidationError:
        pass
    else:
        raise AssertionError("scan universe_max_symbols must be positive")


def test_strategy_binding_supports_symbol_specific_route_configs():
    binding = StrategyBinding(
        symbol="ETHUSDT",
        strategy="capital_protection",
        route="default",
        routes=[
            StrategyRouteConfig(
                route="trend",
                strategy="eth_trend_follow",
                version="2.0",
                parameters={"fast_window": 5, "slow_window": 21},
            ),
            StrategyRouteConfig(
                route="range",
                strategy="eth_mean_reversion",
                version="1.4",
                parameters={"lookback": 30},
            ),
            StrategyRouteConfig(route="default", strategy="capital_protection"),
        ],
    )

    assert binding.strategy_for_route("trend").strategy == "eth_trend_follow"
    assert binding.strategy_for_route("range").parameters == {"lookback": 30}
    assert binding.strategy_for_route("volatile").strategy == "capital_protection"


def test_strategy_binding_keeps_legacy_single_route_compatibility():
    binding = StrategyBinding(
        symbol="BTCUSDT",
        strategy="ma_crossover",
        route="trend",
        parameters={"fast_window": 3},
    )

    routes = binding.route_configs()

    assert len(routes) == 1
    assert routes[0].route == "trend"
    assert routes[0].strategy == "ma_crossover"
    assert routes[0].parameters == {"fast_window": 3}


def test_runtime_config_loads_from_json_file(tmp_path):
    config = make_runtime_config()
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps(config.to_payload()), encoding="utf-8")

    loaded = load_runtime_config(config_path)

    assert loaded.name == "paper-demo"
    assert loaded.broker.broker_plugin == "simulated"


def test_runtime_config_rejects_strategy_without_enabled_market():
    try:
        make_runtime_config(
            markets=[MarketConfig(symbol="ETHUSDT", timeframe="1m", enabled=False)],
            strategies=[StrategyBinding(symbol="ETHUSDT", strategy="ma_crossover")],
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("strategy bindings should require an enabled market")


def test_runtime_config_rejects_duplicate_market_timeframe():
    try:
        make_runtime_config(
            markets=[
                MarketConfig(symbol="BTCUSDT", timeframe="1m"),
                MarketConfig(symbol="BTCUSDT", timeframe="1m"),
            ],
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("duplicate market/timeframe entries should be rejected")


def test_strategy_binding_rejects_duplicate_route_configs():
    try:
        StrategyBinding(
            symbol="BTCUSDT",
            strategy="fallback",
            routes=[
                StrategyRouteConfig(route="trend", strategy="trend_a"),
                StrategyRouteConfig(route="trend", strategy="trend_b"),
            ],
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("strategy route configs should not duplicate route names")


def test_runtime_config_rejects_source_broker_mode_mismatch():
    try:
        make_runtime_config(
            source=PayloadSource.BACKTEST,
            broker=BrokerConfig(mode=PayloadSource.PAPER),
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("broker mode must match runtime source")


def test_live_broker_config_requires_account_id():
    try:
        BrokerConfig(mode=PayloadSource.LIVE, broker_plugin="real-broker")
    except ValidationError:
        pass
    else:
        raise AssertionError("live broker config should require account_id")


def test_risk_config_accepts_kill_switch_thresholds():
    risk = RiskConfig(
        daily_loss_limit_pct=0.05,
        consecutive_loss_limit=3,
        api_failure_rate_limit=0.2,
    )

    assert risk.daily_loss_limit_pct == 0.05
    assert risk.consecutive_loss_limit == 3
    assert risk.api_failure_rate_limit == 0.2


class _LiveReadinessProvider:
    pass


class _LiveReadinessExecution:
    def on_order_intent(self, order_intent, price, index):
        raise AssertionError("example config construction must not place live orders")


def _example_payload(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _flatten_strings(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _flatten_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _flatten_strings(nested)
    elif isinstance(value, str):
        yield value


def test_production_example_configs_load_without_real_credentials():
    example_dir = PROJECT_ROOT / "config" / "examples"
    paper_config = load_runtime_config(example_dir / "paper-runtime.example.json")
    live_config = load_runtime_config(example_dir / "live-runtime.example.json")
    live_payload = _example_payload(example_dir / "live-runtime.example.json")

    assert paper_config.source == PayloadSource.PAPER
    assert paper_config.broker.mode == PayloadSource.PAPER
    assert paper_config.markets[0].provider == "mock"
    assert paper_config.logging.pipeline_report_dir == "logs/paper/pipeline-runs"
    assert paper_config.scan.interval_seconds == 600
    assert paper_config.scan.candidate_symbols == ["BTCUSDT"]
    assert live_config.source == PayloadSource.LIVE
    assert live_config.broker.mode == PayloadSource.LIVE
    assert live_config.markets[0].provider == "okx_public"
    assert live_config.broker.broker_plugin == "okx_broker"
    assert live_config.scan.holding_symbols == ["BTC-USDT"]
    assert live_config.risk.kill_switch_enabled is True
    assert live_config.broker.settings["allow_live_orders"] is False
    assert live_config.metadata["contains_real_credentials"] is False
    assert not any(
        token.startswith(("sk-", "AKIA", "-----BEGIN", "OKX-REAL-"))
        for token in _flatten_strings(live_payload)
    )


def test_production_example_configs_can_construct_runtime_handlers():
    example_dir = PROJECT_ROOT / "config" / "examples"

    paper_runtime = TradingRuntimeOrchestrator.from_config_file(
        example_dir / "paper-runtime.example.json",
        registry=PluginRegistry(),
    )

    live_registry = PluginRegistry()
    live_registry.register(PluginKind.DATA, "okx_public", lambda: _LiveReadinessProvider())
    live_registry.register(PluginKind.EXECUTION, "okx_broker", lambda **_: _LiveReadinessExecution())
    live_runtime = TradingRuntimeOrchestrator.from_config_file(
        example_dir / "live-runtime.example.json",
        registry=live_registry,
    )

    assert PayloadSource.PAPER in paper_runtime.handlers
    assert PayloadSource.LIVE in live_runtime.handlers
