from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional, Union

from quant.schemas import RegimeKind, RegimeSnapshot, StrategyRoute


StrategyFactory = Callable[[], object]
StrategyOrFactory = Union[object, StrategyFactory]


class StrategyRouteNotFound(KeyError):
    pass


class SymbolRouteNotFound(KeyError):
    pass


@dataclass(frozen=True)
class RoutedStrategy:
    strategy: object
    route: StrategyRoute


class SymbolRegimeStrategyRouter:
    def __init__(
        self,
        symbol_routes: Mapping[str, "RegimeStrategyRouter"],
        *,
        fallback: Optional["RegimeStrategyRouter"] = None,
    ):
        if not symbol_routes and fallback is None:
            raise ValueError("symbol router requires at least one symbol route or fallback")

        self.symbol_routes = {
            self._normalize_symbol(symbol): router
            for symbol, router in symbol_routes.items()
        }
        self.fallback = fallback

    def route(self, snapshot: RegimeSnapshot) -> RoutedStrategy:
        symbol = self._normalize_symbol(snapshot.symbol)
        router = self.symbol_routes.get(symbol, self.fallback)
        if router is None:
            raise SymbolRouteNotFound(f"no strategy router configured for symbol {snapshot.symbol}")
        return router.route(snapshot)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("symbol cannot be empty")
        return normalized


class RegimeStrategyRouter:
    def __init__(
        self,
        routes: Mapping[RegimeKind, StrategyOrFactory],
        *,
        fallback: Optional[StrategyOrFactory] = None,
        router_id: str = "regime_strategy_router",
        router_version: str = "1.0.0",
    ):
        if not routes and fallback is None:
            raise ValueError("router requires at least one route or fallback")

        self.routes: Dict[RegimeKind, StrategyOrFactory] = {
            RegimeKind(regime): strategy
            for regime, strategy in routes.items()
        }
        self.fallback = fallback
        self.router_id = router_id
        self.router_version = router_version

    def route(self, snapshot: RegimeSnapshot) -> RoutedStrategy:
        strategy_or_factory = self.routes.get(RegimeKind(snapshot.regime), self.fallback)
        if strategy_or_factory is None:
            raise StrategyRouteNotFound(f"no strategy route for regime {snapshot.regime}")

        strategy = self._resolve_strategy(strategy_or_factory)
        strategy_id = getattr(strategy, "strategy_id", strategy.__class__.__name__)
        strategy_version = getattr(strategy, "strategy_version", "unknown")
        route = StrategyRoute(
            route_id=f"{self.router_id}:{snapshot.regime_id}:{strategy_id}",
            timestamp=snapshot.timestamp,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            regime=snapshot.regime,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            router_id=self.router_id,
            router_version=self.router_version,
            confidence=snapshot.confidence,
            reason_codes=[f"regime:{snapshot.regime}"],
            trace=snapshot.trace,
        )
        return RoutedStrategy(strategy=strategy, route=route)

    @staticmethod
    def _resolve_strategy(strategy_or_factory: StrategyOrFactory) -> object:
        if callable(strategy_or_factory) and not hasattr(strategy_or_factory, "generate_signal"):
            return strategy_or_factory()
        return strategy_or_factory
