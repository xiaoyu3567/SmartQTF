import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

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


def _snapshot(regime=RegimeKind.TREND):
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
