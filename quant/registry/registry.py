from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Optional, Tuple


class PluginKind(str, Enum):
    DATA = "data"
    FEATURE = "feature"
    STRATEGY = "strategy"
    RISK = "risk"
    EXECUTION = "execution"


class PluginAlreadyRegistered(ValueError):
    pass


class PluginNotFound(KeyError):
    pass


@dataclass(frozen=True)
class PluginDescriptor:
    kind: PluginKind
    name: str
    factory: Callable[..., Any]
    version: str = "1.0"
    description: str = ""


class PluginRegistry:
    def __init__(self):
        self._plugins: Dict[Tuple[PluginKind, str], PluginDescriptor] = {}

    def register(
        self,
        kind: PluginKind,
        name: str,
        factory: Callable[..., Any],
        version: str = "1.0",
        description: str = "",
    ) -> PluginDescriptor:
        if not callable(factory):
            raise TypeError("plugin factory must be callable")

        plugin_kind = PluginKind(kind)
        plugin_name = self._normalize_name(name)
        key = (plugin_kind, plugin_name)
        if key in self._plugins:
            raise PluginAlreadyRegistered(f"{plugin_kind.value}:{plugin_name} is already registered")

        descriptor = PluginDescriptor(
            kind=plugin_kind,
            name=plugin_name,
            factory=factory,
            version=version,
            description=description,
        )
        self._plugins[key] = descriptor
        return descriptor

    def get(self, kind: PluginKind, name: str) -> PluginDescriptor:
        key = (PluginKind(kind), self._normalize_name(name))
        try:
            return self._plugins[key]
        except KeyError as exc:
            raise PluginNotFound(f"{key[0].value}:{key[1]} is not registered") from exc

    def create(self, kind: PluginKind, name: str, *args: Any, **kwargs: Any) -> Any:
        descriptor = self.get(kind, name)
        return descriptor.factory(*args, **kwargs)

    def list(self, kind: Optional[PluginKind] = None) -> Iterable[PluginDescriptor]:
        if kind is None:
            return tuple(self._plugins.values())

        plugin_kind = PluginKind(kind)
        return tuple(
            descriptor
            for descriptor in self._plugins.values()
            if descriptor.kind == plugin_kind
        )

    def names(self, kind: PluginKind) -> Tuple[str, ...]:
        return tuple(descriptor.name for descriptor in self.list(kind))

    @staticmethod
    def _normalize_name(name: str) -> str:
        plugin_name = name.strip().lower()
        if not plugin_name:
            raise ValueError("plugin name cannot be empty")
        return plugin_name


default_registry = PluginRegistry()
