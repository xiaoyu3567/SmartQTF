import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.registry import (
    PluginAlreadyRegistered,
    PluginKind,
    PluginNotFound,
    PluginRegistry,
)


class DemoPlugin:
    def __init__(self, value="ok"):
        self.value = value


def test_register_and_create_feature_plugin():
    registry = PluginRegistry()

    descriptor = registry.register(
        PluginKind.FEATURE,
        "demo-feature",
        DemoPlugin,
        description="test feature",
    )
    instance = registry.create(PluginKind.FEATURE, "demo-feature", value="ready")

    assert descriptor.kind == PluginKind.FEATURE
    assert descriptor.name == "demo-feature"
    assert descriptor.description == "test feature"
    assert isinstance(instance, DemoPlugin)
    assert instance.value == "ready"


def test_registry_supports_required_plugin_kinds():
    registry = PluginRegistry()
    expected_kinds = [
        PluginKind.DATA,
        PluginKind.FEATURE,
        PluginKind.STRATEGY,
        PluginKind.RISK,
        PluginKind.EXECUTION,
    ]

    for kind in expected_kinds:
        registry.register(kind, kind.value, lambda kind=kind: kind.value)

    assert set(registry.names(PluginKind.DATA)) == {"data"}
    assert set(descriptor.kind for descriptor in registry.list()) == set(expected_kinds)
    assert registry.create(PluginKind.EXECUTION, "execution") == "execution"


def test_duplicate_registration_is_rejected():
    registry = PluginRegistry()
    registry.register(PluginKind.STRATEGY, "ma", DemoPlugin)

    try:
        registry.register(PluginKind.STRATEGY, "MA", DemoPlugin)
    except PluginAlreadyRegistered:
        pass
    else:
        raise AssertionError("duplicate plugin registration should be rejected")


def test_missing_plugin_is_rejected():
    registry = PluginRegistry()

    try:
        registry.get(PluginKind.RISK, "missing")
    except PluginNotFound:
        pass
    else:
        raise AssertionError("missing plugin lookup should be rejected")
