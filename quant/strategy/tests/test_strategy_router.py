import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from quant.config import StrategyBinding, StrategyRouteConfig
from quant.registry import PluginKind, PluginRegistry
from quant.schemas import RegimeKind, RegimeSnapshot, StrategyRoute, TraceContext
from quant.strategy.router import (
    RegimeStrategyRouter,
    StrategyRouteNotFound,
    SymbolRegimeStrategyRouter,
    SymbolRouteNotFound,
)


class DummyStrategy:
    def __init__(self, strategy_id="dummy", strategy_version="1.0"):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version

    def generate_signal(self, features, index):
        return None


def _snapshot(
    regime=RegimeKind.TREND,
    *,
    direction=None,
    volatility_state=None,
    tradability=None,
):
    return RegimeSnapshot(
        regime_id="regime-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000060,
        detector_id="rule_based_regime",
        detector_version="1.0.0",
        regime=regime,
        confidence=0.72,
        reason_codes=["trend_threshold_exceeded"],
        direction=direction,
        volatility_state=volatility_state,
        tradability=tradability,
        trace=TraceContext(
            run_id="bt-001",
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=1710000060,
            bar_index=42,
        ),
    )


def _snapshot_with_symbol(symbol, regime=RegimeKind.TREND):
    payload = _snapshot(regime).to_payload()
    payload["symbol"] = symbol
    payload["trace"]["symbol"] = symbol
    return RegimeSnapshot.from_payload(payload)


def test_router_selects_strategy_from_regime_snapshot():
    trend_strategy = DummyStrategy("trend_follow", "1.2")
    router = RegimeStrategyRouter({RegimeKind.TREND: trend_strategy})

    routed = router.route(_snapshot(RegimeKind.TREND))

    assert routed.strategy is trend_strategy
    assert routed.route.to_payload() == {
        "schema_version": "1.0",
        "route_id": "regime_strategy_router:regime-001:trend_follow",
        "timestamp": 1710000060,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "regime": "trend",
        "strategy_id": "trend_follow",
        "strategy_version": "1.2",
        "router_id": "regime_strategy_router",
        "router_version": "1.0.0",
        "confidence": 0.72,
        "reason_codes": ["regime:trend"],
        "trace": {
            "schema_version": "1.0",
            "run_id": "bt-001",
            "source": "backtest",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": 1710000060,
            "bar_index": 42,
        },
    }


def test_router_can_create_strategy_from_factory():
    router = RegimeStrategyRouter({
        RegimeKind.RANGE: lambda: DummyStrategy("mean_reversion", "0.3")
    })

    routed = router.route(_snapshot(RegimeKind.RANGE))

    assert isinstance(routed.strategy, DummyStrategy)
    assert routed.route.strategy_id == "mean_reversion"
    assert routed.route.strategy_version == "0.3"


def test_router_uses_fallback_for_unmapped_regime():
    fallback = DummyStrategy("capital_protection", "1.0")
    router = RegimeStrategyRouter({RegimeKind.TREND: DummyStrategy()}, fallback=fallback)

    routed = router.route(_snapshot(RegimeKind.VOLATILE))

    assert routed.strategy is fallback
    assert routed.route.regime == "volatile"
    assert routed.route.strategy_id == "capital_protection"


def test_router_accepts_exact_fine_grained_regime_route():
    strategy = DummyStrategy("trend_pullback_long_v1", "1.0")
    router = RegimeStrategyRouter({RegimeKind.UPTREND_HIGH_VOL: strategy})

    routed = router.route(_snapshot(RegimeKind.UPTREND_HIGH_VOL))

    assert routed.strategy is strategy
    assert routed.route.regime == "uptrend_high_vol"
    assert routed.route.reason_codes == ["regime:uptrend_high_vol"]
    assert routed.decision["requested_regime"] == "uptrend_high_vol"
    assert routed.decision["resolved_regime"] == "uptrend_high_vol"
    assert routed.decision["selected_route"] == "uptrend_high_vol"
    assert routed.decision["legacy_route_used"] is False
    assert routed.decision["fallback_used"] is False


def test_router_maps_fine_grained_regime_to_legacy_route_when_exact_route_is_missing():
    trend_strategy = DummyStrategy("legacy_trend_follow", "1.0")
    range_strategy = DummyStrategy("legacy_range_mean_reversion", "1.0")
    volatile_strategy = DummyStrategy("legacy_volatile_defense", "1.0")
    router = RegimeStrategyRouter(
        {
            RegimeKind.TREND: trend_strategy,
            RegimeKind.RANGE: range_strategy,
            RegimeKind.VOLATILE: volatile_strategy,
        }
    )

    uptrend = router.route(_snapshot(RegimeKind.UPTREND_HIGH_VOL))
    ranged = router.route(_snapshot(RegimeKind.RANGE_LOW_VOL))
    chaos = router.route(_snapshot(RegimeKind.CHAOS))

    assert uptrend.strategy is trend_strategy
    assert uptrend.route.regime == "uptrend_high_vol"
    assert uptrend.route.reason_codes == ["regime:uptrend_high_vol", "route:legacy:trend"]
    assert uptrend.decision["requested_regime"] == "uptrend_high_vol"
    assert uptrend.decision["resolved_regime"] == "trend"
    assert uptrend.decision["selected_route"] == "trend"
    assert uptrend.decision["legacy_route_used"] is True
    assert uptrend.decision["fallback_used"] is False

    assert ranged.strategy is range_strategy
    assert ranged.decision["resolved_regime"] == "range"
    assert chaos.strategy is volatile_strategy
    assert chaos.decision["resolved_regime"] == "volatile"


def test_router_carries_regime_contract_fields_as_read_only_decision_metadata():
    strategy = DummyStrategy("trend_follow", "1.0")
    router = RegimeStrategyRouter({RegimeKind.TREND: strategy})
    snapshot = _snapshot(
        RegimeKind.TREND,
        direction="bearish",
        volatility_state="high",
        tradability="observe_only",
    )

    routed = router.route(snapshot)
    pool = router.route_pool(snapshot)

    assert routed.strategy is strategy
    assert routed.decision["regime_direction"] == "bearish"
    assert routed.decision["regime_volatility_state"] == "high"
    assert routed.decision["regime_tradability"] == "observe_only"
    assert pool.decision["regime_direction"] == "bearish"
    assert pool.decision["regime_volatility_state"] == "high"
    assert pool.decision["regime_tradability"] == "observe_only"


def test_symbol_router_keeps_symbol_strategy_bindings_independent():
    btc_router = RegimeStrategyRouter({
        RegimeKind.TREND: DummyStrategy("btc_trend_follow", "1.0")
    })
    eth_router = RegimeStrategyRouter({
        RegimeKind.TREND: DummyStrategy("eth_trend_follow", "2.0")
    })
    router = SymbolRegimeStrategyRouter({
        "BTCUSDT": btc_router,
        "ETHUSDT": eth_router,
    })

    btc = router.route(_snapshot(RegimeKind.TREND))
    eth = router.route(_snapshot_with_symbol("ETHUSDT", RegimeKind.TREND))

    assert btc.route.symbol == "BTCUSDT"
    assert btc.route.strategy_id == "btc_trend_follow"
    assert eth.route.symbol == "ETHUSDT"
    assert eth.route.strategy_id == "eth_trend_follow"


def test_configured_router_routes_same_symbol_to_different_regime_strategies():
    created = []
    registry = PluginRegistry()

    def factory(strategy_id):
        def create(**kwargs):
            created.append((strategy_id, kwargs))
            return DummyStrategy(strategy_id, kwargs["version"])

        return create

    registry.register(PluginKind.STRATEGY, "trend_follow", factory("trend_follow"))
    registry.register(PluginKind.STRATEGY, "mean_reversion", factory("mean_reversion"))
    registry.register(PluginKind.STRATEGY, "capital_protection", factory("capital_protection"))
    binding = StrategyBinding(
        symbol="BTCUSDT",
        strategy="capital_protection",
        routes=[
            StrategyRouteConfig(
                route="trend",
                strategy="trend_follow",
                version="2.0",
                parameters={"fast_window": 5},
            ),
            StrategyRouteConfig(
                route="range",
                strategy="mean_reversion",
                version="1.4",
                parameters={"lookback": 30},
            ),
            StrategyRouteConfig(route="default", strategy="capital_protection"),
        ],
    )

    router = SymbolRegimeStrategyRouter.from_config_bindings([binding], registry=registry)

    trend = router.route(_snapshot(RegimeKind.TREND))
    range_route = router.route(_snapshot(RegimeKind.RANGE))
    volatile = router.route(_snapshot(RegimeKind.VOLATILE))

    assert trend.route.strategy_id == "trend_follow"
    assert range_route.route.strategy_id == "mean_reversion"
    assert volatile.route.strategy_id == "capital_protection"
    assert volatile.route.reason_codes == ["regime:volatile", "route:fallback:default"]
    assert volatile.decision["fallback_used"] is True
    assert volatile.decision["selected_route"] == "default"
    assert [entry["strategy_id"] for entry in router.decision_log] == [
        "trend_follow",
        "mean_reversion",
        "capital_protection",
    ]
    assert created[0] == (
        "trend_follow",
        {"version": "2.0", "parameters": {"fast_window": 5}},
    )
    assert "symbol" not in created[0][1]


def test_configured_symbol_router_keeps_symbols_on_independent_routers():
    registry = PluginRegistry()
    registry.register(
        PluginKind.STRATEGY,
        "btc_trend_follow",
        lambda **_: DummyStrategy("btc_trend_follow", "1.0"),
    )
    registry.register(
        PluginKind.STRATEGY,
        "eth_trend_follow",
        lambda **_: DummyStrategy("eth_trend_follow", "2.0"),
    )
    router = SymbolRegimeStrategyRouter.from_config_bindings(
        [
            StrategyBinding(
                symbol="BTCUSDT",
                strategy="btc_trend_follow",
                route="trend",
            ),
            StrategyBinding(
                symbol="ETHUSDT",
                strategy="eth_trend_follow",
                route="trend",
            ),
        ],
        registry=registry,
    )

    btc = router.route(_snapshot_with_symbol("BTCUSDT", RegimeKind.TREND))
    eth = router.route(_snapshot_with_symbol("ETHUSDT", RegimeKind.TREND))

    assert btc.route.router_id == "config_router:BTCUSDT"
    assert btc.route.strategy_id == "btc_trend_follow"
    assert eth.route.router_id == "config_router:ETHUSDT"
    assert eth.route.strategy_id == "eth_trend_follow"


def test_configured_router_rejects_duplicate_normalized_symbols():
    registry = PluginRegistry()
    registry.register(PluginKind.STRATEGY, "trend_follow", lambda **_: DummyStrategy())

    try:
        SymbolRegimeStrategyRouter.from_config_bindings(
            [
                StrategyBinding(symbol="BTCUSDT", strategy="trend_follow"),
                StrategyBinding(symbol="btcusdt", strategy="trend_follow"),
            ],
            registry=registry,
        )
    except ValueError:
        return

    raise AssertionError("normalized duplicate symbol routers should be rejected")


def test_symbol_router_rejects_unconfigured_symbol_without_fallback():
    router = SymbolRegimeStrategyRouter({
        "BTCUSDT": RegimeStrategyRouter({RegimeKind.TREND: DummyStrategy()})
    })

    try:
        router.route(_snapshot_with_symbol("SOLUSDT", RegimeKind.TREND))
    except SymbolRouteNotFound:
        return

    raise AssertionError("unconfigured symbol should require an explicit router fallback")


def test_router_rejects_unmapped_regime_without_fallback():
    router = RegimeStrategyRouter({RegimeKind.TREND: DummyStrategy()})

    try:
        router.route(_snapshot(RegimeKind.RANGE))
    except StrategyRouteNotFound:
        return

    raise AssertionError("unmapped regime should require an explicit fallback")


def test_strategy_route_schema_round_trip_and_validation():
    route = StrategyRoute(
        route_id="route-001",
        timestamp=1710000060,
        symbol="BTCUSDT",
        timeframe="1m",
        regime=RegimeKind.TREND,
        strategy_id="trend_follow",
        strategy_version="1.0",
        router_id="router",
        router_version="1.0",
        confidence=0.5,
    )

    assert StrategyRoute.from_payload(route.to_payload()) == route

    try:
        StrategyRoute(
            route_id="route-001",
            timestamp=1710000060,
            symbol="BTCUSDT",
            timeframe="1m",
            regime=RegimeKind.TREND,
            strategy_id="trend_follow",
            strategy_version="1.0",
            router_id="router",
            router_version="1.0",
            confidence=1.5,
        )
    except ValidationError:
        return

    raise AssertionError("StrategyRoute confidence must be a probability")
