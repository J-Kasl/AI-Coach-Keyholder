"""
tests/infrastructure/test_plugin_registry.py

Exercises PluginRegistry entirely against synthetic, tmp_path-
constructed plugin directories -- no real plugin exists yet
(plugin_architecture_proposal.md Section 20's `goal_celebration` is a
later, separate step, per Step 2's own explicit scope).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application.router import CommandRouter
from infrastructure.consumer_registry import ConsumerRegistry
from infrastructure.database import Database as CoreDatabase
from infrastructure.outbox import DomainEvent, claim_pending_events, write_event
from infrastructure.plugin_registry import PluginRegistry

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(c)
    return c


@pytest.fixture
def plugins_dir(tmp_path):
    """
    A real, importable `plugins` package rooted at a temp directory --
    `sys.path` is manipulated for the duration of the test only, and
    every `plugins.*`/`plugins` module is evicted from `sys.modules`
    on teardown so tests never leak state into each other (Python
    caches imports by module name, and every test in this file reuses
    the name `plugins`).
    """
    root = tmp_path / "plugin_root"
    root.mkdir()
    (root / "plugins").mkdir()
    (root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(root))
    try:
        yield root / "plugins"
    finally:
        sys.path.remove(str(root))
        for name in list(sys.modules):
            if name == "plugins" or name.startswith("plugins."):
                del sys.modules[name]


def _write_plugin(plugins_dir: Path, name: str, *, manifest_src: str, handlers_src: str | None = None) -> None:
    plugin_dir = plugins_dir / name
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "manifest.py").write_text(manifest_src, encoding="utf-8")
    if handlers_src is not None:
        (plugin_dir / "handlers.py").write_text(handlers_src, encoding="utf-8")


_VALID_MANIFEST_TEMPLATE = """
from infrastructure.plugin_models import PluginManifest

MANIFEST = PluginManifest(
    name={name!r},
    version="0.1.0",
    plugin_api_version="1.0",
    min_core_version="1.0",
    consumes_event_types=({consumes!r},) if {consumes!r} else (),
    registers_commands=({commands!r},) if {commands!r} else (),
)
"""


def _registry(core: CoreDatabase, plugins_dir: Path, **kwargs) -> PluginRegistry:
    return PluginRegistry(
        plugins_dir=plugins_dir, core=core,
        consumer_registry=kwargs.pop("consumer_registry", ConsumerRegistry()),
        command_router=kwargs.pop("command_router", CommandRouter()),
        **kwargs,
    )


class TestDiscovery:
    def test_no_plugins_dir_returns_empty(self, core: CoreDatabase, tmp_path: Path) -> None:
        registry = _registry(core, tmp_path / "does_not_exist")
        assert registry.discover() == []

    def test_discovers_directories_with_a_manifest(self, core: CoreDatabase, plugins_dir: Path) -> None:
        _write_plugin(plugins_dir, "zeta", manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="zeta", consumes="", commands=""))
        _write_plugin(plugins_dir, "alpha", manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="alpha", consumes="", commands=""))
        registry = _registry(core, plugins_dir)
        found = registry.discover()
        assert [p.name for p in found] == ["alpha", "zeta"]  # PLUG-9: deterministic, alphabetical

    def test_ignores_directories_without_a_manifest(self, core: CoreDatabase, plugins_dir: Path) -> None:
        (plugins_dir / "not_a_plugin").mkdir()
        (plugins_dir / "not_a_plugin" / "__init__.py").write_text("", encoding="utf-8")
        registry = _registry(core, plugins_dir)
        assert registry.discover() == []


class TestManifestLoading:
    def test_loads_a_valid_manifest(self, core: CoreDatabase, plugins_dir: Path) -> None:
        _write_plugin(plugins_dir, "greeter", manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="greeter", consumes="", commands=""))
        registry = _registry(core, plugins_dir)
        manifest = registry.load_manifest(plugins_dir / "greeter")
        assert manifest.name == "greeter"

    def test_missing_manifest_attribute_raises(self, core: CoreDatabase, plugins_dir: Path) -> None:
        _write_plugin(plugins_dir, "broken", manifest_src="# no MANIFEST defined here\n")
        registry = _registry(core, plugins_dir)
        from infrastructure.plugin_registry import PluginConventionError
        with pytest.raises(PluginConventionError):
            registry.load_manifest(plugins_dir / "broken")

    def test_name_mismatch_with_directory_raises(self, core: CoreDatabase, plugins_dir: Path) -> None:
        _write_plugin(plugins_dir, "correct_dir_name", manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="wrong_name", consumes="", commands=""))
        registry = _registry(core, plugins_dir)
        from infrastructure.plugin_registry import PluginConventionError
        with pytest.raises(PluginConventionError):
            registry.load_manifest(plugins_dir / "correct_dir_name")


class TestCompatibilityValidation:
    def test_first_party_within_version_range_is_compatible(self, core: CoreDatabase, plugins_dir: Path) -> None:
        _write_plugin(plugins_dir, "ok_plugin", manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="ok_plugin", consumes="", commands=""))
        registry = _registry(core, plugins_dir, core_version="1.0")
        manifest = registry.load_manifest(plugins_dir / "ok_plugin")
        assert registry.validate_compatibility(manifest) is True

    def test_min_core_version_above_running_core_is_incompatible(self, core: CoreDatabase, plugins_dir: Path) -> None:
        src = """
from infrastructure.plugin_models import PluginManifest
MANIFEST = PluginManifest(name="future_plugin", version="0.1.0", plugin_api_version="1.0", min_core_version="2.0")
"""
        _write_plugin(plugins_dir, "future_plugin", manifest_src=src)
        registry = _registry(core, plugins_dir, core_version="1.0")
        manifest = registry.load_manifest(plugins_dir / "future_plugin")
        assert registry.validate_compatibility(manifest) is False

    def test_max_core_version_below_running_core_is_incompatible(self, core: CoreDatabase, plugins_dir: Path) -> None:
        src = """
from infrastructure.plugin_models import PluginManifest
MANIFEST = PluginManifest(name="old_plugin", version="0.1.0", plugin_api_version="1.0", min_core_version="1.0", max_core_version="1.0")
"""
        _write_plugin(plugins_dir, "old_plugin", manifest_src=src)
        registry = _registry(core, plugins_dir, core_version="2.0")
        manifest = registry.load_manifest(plugins_dir / "old_plugin")
        assert registry.validate_compatibility(manifest) is False


class TestLoadAllEndToEnd:
    def test_a_command_only_plugin_loads_and_registers(self, core: CoreDatabase, plugins_dir: Path) -> None:
        _write_plugin(
            plugins_dir, "pinger",
            manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="pinger", consumes="", commands="ping"),
            handlers_src='''
from application.models import OutgoingMessage

def build_commands(sdk, repo):
    def handle_ping(ctx):
        return OutgoingMessage(text="pong")
    return {"ping": ("responds pong", handle_ping)}
''',
        )
        command_router = CommandRouter()
        registry = _registry(core, plugins_dir, command_router=command_router)
        loaded, failures = registry.load_all()

        assert failures == []
        assert len(loaded) == 1
        assert loaded[0].manifest.name == "pinger"

        from application.router import RequestContext
        from application.models import UserAccount
        ctx = RequestContext(user=UserAccount(created_at=FIXED_TIME, last_seen_at=FIXED_TIME), now=FIXED_TIME)
        result = command_router.route("ping", ctx)
        assert result.matched is True
        assert result.outgoing.text == "pong"

    def test_an_event_consumer_plugin_loads_and_reacts_to_a_real_event(self, core: CoreDatabase, plugins_dir: Path) -> None:
        _write_plugin(
            plugins_dir, "listener",
            manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="listener", consumes="goal.completed", commands=""),
            handlers_src='''
CALLS = []

def build_event_consumers(sdk, repo):
    def on_goal_completed(tx, event):
        CALLS.append(event.event_type)
    return {"goal.completed": on_goal_completed}
''',
        )
        consumer_registry = ConsumerRegistry()
        registry = _registry(core, plugins_dir, consumer_registry=consumer_registry)
        loaded, failures = registry.load_all()
        assert failures == []

        with core.transaction() as tx:
            write_event(tx, DomainEvent(event_type="goal.completed", source_module="goal_management", payload={}, occurred_at=FIXED_TIME))
        claimed = claim_pending_events(core, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))
        ran = consumer_registry.dispatch(core, claimed[0], now=FIXED_TIME)
        assert ran == 1

        import plugins.listener.handlers as listener_handlers
        assert listener_handlers.CALLS == ["goal.completed"]

    def test_a_failing_event_consumer_does_not_crash_load_all_or_other_plugins(self, core: CoreDatabase, plugins_dir: Path) -> None:
        _write_plugin(
            plugins_dir, "buggy",
            manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="buggy", consumes="goal.completed", commands=""),
            handlers_src='''
def build_event_consumers(sdk, repo):
    def on_goal_completed(tx, event):
        raise RuntimeError("buggy plugin bug")
    return {"goal.completed": on_goal_completed}
''',
        )
        _write_plugin(
            plugins_dir, "healthy",
            manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="healthy", consumes="goal.completed", commands=""),
            handlers_src='''
CALLS = []
def build_event_consumers(sdk, repo):
    def on_goal_completed(tx, event):
        CALLS.append(1)
    return {"goal.completed": on_goal_completed}
''',
        )
        consumer_registry = ConsumerRegistry()
        registry = _registry(core, plugins_dir, consumer_registry=consumer_registry)
        loaded, failures = registry.load_all()
        assert failures == []
        assert len(loaded) == 2

        with core.transaction() as tx:
            write_event(tx, DomainEvent(event_type="goal.completed", source_module="goal_management", payload={}, occurred_at=FIXED_TIME))
        claimed = claim_pending_events(core, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))
        ran = consumer_registry.dispatch(core, claimed[0], now=FIXED_TIME)  # must not raise
        assert ran == 1  # only "healthy" counted as having run

        import plugins.healthy.handlers as healthy_handlers
        assert healthy_handlers.CALLS == [1]

    def test_incompatible_plugin_is_recorded_as_a_failure_not_loaded(self, core: CoreDatabase, plugins_dir: Path) -> None:
        src = """
from infrastructure.plugin_models import PluginManifest
MANIFEST = PluginManifest(name="too_new", version="0.1.0", plugin_api_version="1.0", min_core_version="99.0")
"""
        _write_plugin(plugins_dir, "too_new", manifest_src=src)
        registry = _registry(core, plugins_dir, core_version="1.0")
        loaded, failures = registry.load_all()
        assert loaded == []
        assert len(failures) == 1
        assert failures[0].plugin_name == "too_new"

    def test_a_plugin_declaring_a_capability_not_delivered_by_handlers_fails_cleanly(self, core: CoreDatabase, plugins_dir: Path) -> None:
        _write_plugin(
            plugins_dir, "incomplete",
            manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="incomplete", consumes="goal.completed", commands=""),
            handlers_src="def build_event_consumers(sdk, repo):\n    return {}\n",  # declares consuming, delivers nothing
        )
        registry = _registry(core, plugins_dir)
        loaded, failures = registry.load_all()
        assert loaded == []
        assert len(failures) == 1

    def test_sdk_capability_gating_still_holds_through_the_registry(self, core: CoreDatabase, plugins_dir: Path) -> None:
        """End-to-end proof that PLUG-5 survives being wired through
        PluginRegistry, not only when build_plugin_sdk() is called
        directly (already covered by test_plugin_sdk.py)."""
        _write_plugin(
            plugins_dir, "checker",
            manifest_src=_VALID_MANIFEST_TEMPLATE.format(name="checker", consumes="", commands="check"),
            handlers_src='''
from application.models import OutgoingMessage

RESULT = {}

def build_commands(sdk, repo):
    def handle_check(ctx):
        RESULT["has_get_goal"] = hasattr(sdk, "get_goal")
        return OutgoingMessage(text="checked")
    return {"check": ("checks capability gating", handle_check)}
''',
        )
        command_router = CommandRouter()
        registry = _registry(core, plugins_dir, command_router=command_router)
        loaded, failures = registry.load_all()
        assert failures == []

        from application.router import RequestContext
        from application.models import UserAccount
        ctx = RequestContext(user=UserAccount(created_at=FIXED_TIME, last_seen_at=FIXED_TIME), now=FIXED_TIME)
        command_router.route("check", ctx)

        import plugins.checker.handlers as checker_handlers
        assert checker_handlers.RESULT["has_get_goal"] is False  # never declared, never granted
