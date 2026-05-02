from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Union

from quant.registry import PluginKind, default_registry
from quant.schemas import RegimeKind, RegimeSnapshot, StrategyRoute


StrategyFactory = Callable[[], object]
StrategyOrFactory = Union[object, StrategyFactory]
StrategyFactoryBuilder = Callable[[object], StrategyOrFactory]


class StrategyRouteNotFound(KeyError):
    pass


class SymbolRouteNotFound(KeyError):
    pass


@dataclass(frozen=True)
class RoutedStrategy:
    strategy: object
    route: StrategyRoute
    decision: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutedStrategyPool:
    strategies: tuple[RoutedStrategy, ...]
    decision: Mapping[str, Any] = field(default_factory=dict)


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

    @classmethod
    def from_config_bindings(
        cls,
        bindings: Iterable[object],
        *,
        registry=None,
        strategy_factory: Optional[StrategyFactoryBuilder] = None,
        router_id_prefix: str = "config_router",
        router_version: str = "1.0.0",
        fallback: Optional["RegimeStrategyRouter"] = None,
    ):
        symbol_routes = {}
        for binding in bindings:
            symbol = cls._normalize_symbol(getattr(binding, "symbol"))
            if symbol in symbol_routes:
                raise ValueError(f"duplicate strategy binding for symbol {symbol}")

            route_configs = (
                binding.route_configs()
                if hasattr(binding, "route_configs")
                else [binding]
            )
            symbol_routes[symbol] = RegimeStrategyRouter.from_route_configs(
                route_configs,
                registry=registry,
                strategy_factory=strategy_factory,
                router_id=f"{router_id_prefix}:{symbol}",
                router_version=router_version,
            )

        return cls(symbol_routes=symbol_routes, fallback=fallback)

    def route(self, snapshot: RegimeSnapshot) -> RoutedStrategy:
        pool = self.route_pool(snapshot)
        if not pool.strategies:
            raise StrategyRouteNotFound(f"no strategy route for regime {snapshot.regime}")
        return pool.strategies[0]

    def route_pool(self, snapshot: RegimeSnapshot) -> RoutedStrategyPool:
        symbol = self._normalize_symbol(snapshot.symbol)
        router = self.symbol_routes.get(symbol, self.fallback)
        if router is None:
            raise SymbolRouteNotFound(f"no strategy router configured for symbol {snapshot.symbol}")
        return router.route_pool(snapshot)

    @property
    def decision_log(self):
        entries: List[Mapping[str, Any]] = []
        for router in self.symbol_routes.values():
            entries.extend(router.decision_log)
        if self.fallback is not None:
            entries.extend(self.fallback.decision_log)
        return tuple(dict(entry) for entry in entries)

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
        self._decision_log: List[Mapping[str, Any]] = []

    @classmethod
    def from_route_configs(
        cls,
        route_configs: Iterable[object],
        *,
        registry=None,
        strategy_factory: Optional[StrategyFactoryBuilder] = None,
        router_id: str = "config_router",
        router_version: str = "1.0.0",
    ):
        routes: Dict[RegimeKind, StrategyOrFactory] = {}
        fallback = None
        for route_config in route_configs:
            route_name = cls._route_name(route_config)
            route_strategy = cls._strategy_from_config(
                route_config,
                registry=registry,
                strategy_factory=strategy_factory,
            )
            if route_name == "default":
                fallback = cls._append_route_strategy(fallback, route_strategy)
            else:
                regime = RegimeKind(route_name)
                routes[regime] = cls._append_route_strategy(routes.get(regime), route_strategy)

        return cls(
            routes=routes,
            fallback=fallback,
            router_id=router_id,
            router_version=router_version,
        )

    def route(self, snapshot: RegimeSnapshot) -> RoutedStrategy:
        pool = self.route_pool(snapshot)
        if not pool.strategies:
            raise StrategyRouteNotFound(f"no strategy route for regime {snapshot.regime}")
        return pool.strategies[0]

    def route_pool(self, snapshot: RegimeSnapshot) -> RoutedStrategyPool:
        regime = RegimeKind(snapshot.regime)
        route_regime = self._resolve_route_regime(regime)
        fallback_used = route_regime is None
        legacy_route_used = route_regime is not None and route_regime != regime
        strategy_or_factory = (
            self.fallback if fallback_used else self.routes.get(route_regime)
        )
        if strategy_or_factory is None:
            raise StrategyRouteNotFound(f"no strategy route for regime {snapshot.regime}")

        strategies = self._resolve_strategy_pool(strategy_or_factory)
        if not strategies:
            raise StrategyRouteNotFound(f"empty strategy pool for regime {snapshot.regime}")

        routed = []
        for position, strategy in enumerate(strategies):
            strategy_id = getattr(strategy, "strategy_id", strategy.__class__.__name__)
            strategy_version = getattr(strategy, "strategy_version", "unknown")
            reason_codes = [f"regime:{snapshot.regime}"]
            if legacy_route_used:
                reason_codes.append(f"route:legacy:{route_regime.value}")
            if fallback_used:
                reason_codes.append("route:fallback:default")
            if len(strategies) > 1:
                reason_codes.append(f"strategy_pool:index:{position}")
            route_id = f"{self.router_id}:{snapshot.regime_id}:{strategy_id}"
            if len(strategies) > 1:
                route_id = f"{route_id}:{position}"
            route = StrategyRoute(
                route_id=route_id,
                timestamp=snapshot.timestamp,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe,
                regime=snapshot.regime,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                router_id=self.router_id,
                router_version=self.router_version,
                confidence=snapshot.confidence,
                reason_codes=reason_codes,
                trace=snapshot.trace,
            )
            decision = self._route_decision(
                snapshot=snapshot,
                route=route,
                requested_regime=regime,
                resolved_regime=route_regime,
                selected_route="default" if fallback_used else route_regime.value,
                fallback_used=fallback_used,
                legacy_route_used=legacy_route_used,
            )
            self._decision_log.append(decision)
            routed.append(RoutedStrategy(strategy=strategy, route=route, decision=decision))

        pool_decision = self._pool_decision(
            snapshot=snapshot,
            requested_regime=regime,
            resolved_regime=route_regime,
            selected_route="default" if fallback_used else route_regime.value,
            fallback_used=fallback_used,
            legacy_route_used=legacy_route_used,
            routed=routed,
        )
        return RoutedStrategyPool(strategies=tuple(routed), decision=pool_decision)

    @property
    def decision_log(self):
        return tuple(dict(entry) for entry in self._decision_log)

    @staticmethod
    def _resolve_strategy(strategy_or_factory: StrategyOrFactory) -> object:
        if callable(strategy_or_factory) and not hasattr(strategy_or_factory, "generate_signal"):
            return strategy_or_factory()
        return strategy_or_factory

    @classmethod
    def _resolve_strategy_pool(cls, strategy_or_factory: StrategyOrFactory) -> tuple[object, ...]:
        if isinstance(strategy_or_factory, (list, tuple)):
            return tuple(cls._resolve_strategy(item) for item in strategy_or_factory)
        return (cls._resolve_strategy(strategy_or_factory),)

    def _resolve_route_regime(self, regime: RegimeKind) -> Optional[RegimeKind]:
        if regime in self.routes:
            return regime
        legacy_regime = regime.legacy_kind()
        if legacy_regime in self.routes:
            return legacy_regime
        return None

    @staticmethod
    def _route_name(route_config: object) -> str:
        route = getattr(route_config, "route")
        route_name = route.value if hasattr(route, "value") else str(route)
        normalized = route_name.strip().lower()
        if not normalized:
            raise ValueError("strategy route cannot be empty")
        return normalized

    @staticmethod
    def _strategy_from_config(
        route_config: object,
        *,
        registry=None,
        strategy_factory: Optional[StrategyFactoryBuilder] = None,
    ) -> StrategyOrFactory:
        if strategy_factory is not None:
            return strategy_factory(route_config)

        plugin_registry = registry or default_registry
        strategy_name = getattr(route_config, "strategy")
        version = getattr(route_config, "version", "1.0")
        parameters = dict(getattr(route_config, "parameters", {}) or {})

        def create_strategy():
            return plugin_registry.create(
                PluginKind.STRATEGY,
                strategy_name,
                version=version,
                parameters=parameters,
            )

        return create_strategy

    @staticmethod
    def _append_route_strategy(existing, route_strategy):
        if existing is None:
            return route_strategy
        if isinstance(existing, list):
            return [*existing, route_strategy]
        return [existing, route_strategy]

    def _route_decision(
        self,
        *,
        snapshot: RegimeSnapshot,
        route: StrategyRoute,
        requested_regime: RegimeKind,
        resolved_regime: Optional[RegimeKind],
        selected_route: str,
        fallback_used: bool,
        legacy_route_used: bool,
    ) -> Mapping[str, Any]:
        trace_payload = route.trace.to_payload() if route.trace is not None else None
        return {
            "schema_version": "1.0",
            "route_id": route.route_id,
            "timestamp": route.timestamp,
            "symbol": route.symbol,
            "timeframe": route.timeframe,
            "requested_regime": requested_regime.value,
            "resolved_regime": resolved_regime.value if resolved_regime is not None else None,
            "selected_route": selected_route,
            "strategy_id": route.strategy_id,
            "strategy_version": route.strategy_version,
            "router_id": self.router_id,
            "router_version": self.router_version,
            "fallback_used": fallback_used,
            "legacy_route_used": legacy_route_used,
            "regime_direction": snapshot.direction,
            "regime_volatility_state": snapshot.volatility_state,
            "regime_tradability": snapshot.tradability,
            "reason_codes": list(route.reason_codes),
            "trace": trace_payload,
        }

    def _pool_decision(
        self,
        *,
        snapshot: RegimeSnapshot,
        requested_regime: RegimeKind,
        resolved_regime: Optional[RegimeKind],
        selected_route: str,
        fallback_used: bool,
        legacy_route_used: bool,
        routed: List[RoutedStrategy],
    ) -> Mapping[str, Any]:
        return {
            "schema_version": "1.0",
            "timestamp": snapshot.timestamp,
            "symbol": snapshot.symbol,
            "timeframe": snapshot.timeframe,
            "requested_regime": requested_regime.value,
            "resolved_regime": resolved_regime.value if resolved_regime is not None else None,
            "selected_route": selected_route,
            "router_id": self.router_id,
            "router_version": self.router_version,
            "fallback_used": fallback_used,
            "legacy_route_used": legacy_route_used,
            "regime_direction": snapshot.direction,
            "regime_volatility_state": snapshot.volatility_state,
            "regime_tradability": snapshot.tradability,
            "candidate_count": len(routed),
            "strategy_ids": [item.route.strategy_id for item in routed],
            "route_ids": [item.route.route_id for item in routed],
            "reason_codes": [code for item in routed for code in item.route.reason_codes],
        }
