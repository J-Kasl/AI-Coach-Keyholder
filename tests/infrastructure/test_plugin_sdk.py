"""tests/infrastructure/test_plugin_sdk.py"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from goal_management.repository import GoalManager
from infrastructure.database import Database as CoreDatabase
from infrastructure.plugin_models import PluginManifest
from infrastructure.plugin_sdk import PluginCapabilityNotGrantedError, build_plugin_sdk
from penalty_engine.repository import PenaltyEngine
from recovery_plan.repository import RecoveryPlanManager
from trust_manager.repository import TrustManager

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


def _manifest(**overrides) -> PluginManifest:
    kwargs = dict(
        name="goal_celebration", version="0.1.0", plugin_api_version="1.0", min_core_version="1.0",
    )
    kwargs.update(overrides)
    return PluginManifest(**kwargs)


class TestCapabilityGatingIsStructural:
    """PLUG-5: an undeclared capability's method must not exist on the
    instance at all -- not merely raise when called."""

    def test_no_capabilities_declared_means_no_read_methods_at_all(self, core: CoreDatabase) -> None:
        sdk = build_plugin_sdk(_manifest(), core=core, config_values={})
        assert not hasattr(sdk, "get_goal")
        assert not hasattr(sdk, "get_domain_state")
        assert not hasattr(sdk, "get_active_or_frozen_penalty_window")
        assert not hasattr(sdk, "get_recovery_plan")

    def test_declaring_goal_management_read_exposes_only_that(self, core: CoreDatabase) -> None:
        gm = GoalManager(core.db_path, core=core)
        sdk = build_plugin_sdk(
            _manifest(requested_read_capabilities=("goal_management.read",)),
            core=core, config_values={}, goal_management=gm,
        )
        assert hasattr(sdk, "get_goal")
        assert hasattr(sdk, "get_goal_version")
        assert hasattr(sdk, "get_change_proposal")
        assert hasattr(sdk, "get_change_proposal_content")
        # still nothing from the other three domain modules
        assert not hasattr(sdk, "get_domain_state")
        assert not hasattr(sdk, "get_active_or_frozen_penalty_window")
        assert not hasattr(sdk, "get_recovery_plan")

    def test_declared_read_method_actually_delegates_to_the_real_repository(self, core: CoreDatabase) -> None:
        gm = GoalManager(core.db_path, core=core)
        goal = gm.create_goal(
            title="Exercise", target_description="3x/week", trust_domain="fitness",
            created_via="user_proposed", now=FIXED_TIME,
        )
        sdk = build_plugin_sdk(
            _manifest(requested_read_capabilities=("goal_management.read",)),
            core=core, config_values={}, goal_management=gm,
        )
        fetched = sdk.get_goal(goal.goal_group_id)
        assert fetched is not None
        assert fetched.goal_group_id == goal.goal_group_id

    def test_declaring_capability_without_supplying_repository_raises_immediately(self, core: CoreDatabase) -> None:
        with pytest.raises(ValueError):
            build_plugin_sdk(
                _manifest(requested_read_capabilities=("goal_management.read",)),
                core=core, config_values={},  # goal_management=None, the bug this guards against
            )

    def test_all_four_domain_modules_can_be_granted_together(self, core: CoreDatabase) -> None:
        sdk = build_plugin_sdk(
            _manifest(requested_read_capabilities=(
                "trust_manager.read", "penalty_engine.read", "recovery_plan.read", "goal_management.read",
            )),
            core=core, config_values={},
            trust_manager=TrustManager(core.db_path, core=core),
            penalty_engine=PenaltyEngine(core.db_path, core=core),
            recovery_plan=RecoveryPlanManager(core.db_path, core=core),
            goal_management=GoalManager(core.db_path, core=core),
        )
        assert hasattr(sdk, "get_domain_state")
        assert hasattr(sdk, "get_active_or_frozen_penalty_window")
        assert hasattr(sdk, "get_recovery_plan")
        assert hasattr(sdk, "get_goal")


class TestNoRawDatabaseAccess:
    """
    v1.2 structural fix: an earlier draft stored `self._core = core` on
    PluginSDK -- trivially reachable as `sdk._core`, silently defeating
    PLUG-1/PLUG-5 regardless of what a plugin's manifest declared,
    since Python's underscore convention is not real privacy. Verifies
    the fix directly, not just by its absence from the source.
    """

    def test_sdk_has_no_core_attribute_under_any_name(self, core: CoreDatabase) -> None:
        sdk = build_plugin_sdk(
            _manifest(requested_read_capabilities=("goal_management.read",)),
            core=core, config_values={}, goal_management=GoalManager(core.db_path, core=core),
        )
        for attr_name in vars(sdk):
            assert "core" not in attr_name.lower(), f"Unexpected core-like attribute found: {attr_name!r}"
        assert not hasattr(sdk, "_core")
        assert not hasattr(sdk, "core")
        assert not hasattr(sdk, "database")
        assert not hasattr(sdk, "db")

    def test_sdk_dict_only_contains_manifest_and_config(self, core: CoreDatabase) -> None:
        """A plugin inspecting `sdk.__dict__`/`vars(sdk)` (the ordinary,
        no-special-effort way to look for a hidden attribute) finds
        only what's meant to be there."""
        sdk = build_plugin_sdk(_manifest(), core=core, config_values={})
        # publish_event/publish_event_in_transaction are bound as
        # instance attributes (closures, not raw Database references)
        # -- expected and safe; see PluginSDK's own class docstring for
        # why this differs from storing `core` itself.
        assert set(vars(sdk).keys()) <= {"manifest", "config", "publish_event", "publish_event_in_transaction"}


class TestPublishEventAllowlist:
    """`publishes_event_types` is a binding allowlist (fixed after a
    real review question caught that the original implementation only
    checked the namespace prefix, not membership in the declared list
    -- the same "declare it or you can't reach it" discipline PLUG-5
    already applies to read capabilities)."""

    def test_publishing_a_declared_event_type_succeeds(self, core: CoreDatabase) -> None:
        sdk = build_plugin_sdk(
            _manifest(publishes_event_types=("plugin_goal_celebration.sent",)), core=core, config_values={},
        )
        sdk.publish_event("plugin_goal_celebration.sent", {"goal_group_id": "abc"}, now=FIXED_TIME)
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'plugin_goal_celebration.sent'")
        assert row is not None
        assert row["source_module"] == "plugin_goal_celebration"

    def test_publishing_an_undeclared_event_type_is_rejected_even_with_a_correct_namespace(self, core: CoreDatabase) -> None:
        """The specific gap a review question caught: correctly
        namespaced (`plugin_goal_celebration.*`) is necessary but not
        sufficient -- the exact event_type must also have been
        declared in publishes_event_types."""
        sdk = build_plugin_sdk(
            _manifest(publishes_event_types=("plugin_goal_celebration.sent",)), core=core, config_values={},
        )
        with pytest.raises(PluginCapabilityNotGrantedError):
            sdk.publish_event("plugin_goal_celebration.some_other_event_never_declared", {}, now=FIXED_TIME)

    def test_publishing_with_no_declared_event_types_at_all_is_always_rejected(self, core: CoreDatabase) -> None:
        sdk = build_plugin_sdk(_manifest(), core=core, config_values={})  # publishes_event_types=() by default
        with pytest.raises(PluginCapabilityNotGrantedError):
            sdk.publish_event("plugin_goal_celebration.sent", {}, now=FIXED_TIME)

    def test_impersonating_a_domain_module_event_at_call_time_is_rejected(self, core: CoreDatabase) -> None:
        """PLUG-2 re-checked at call time, not only at manifest
        construction -- a plugin's code could build this string
        dynamically at runtime."""
        sdk = build_plugin_sdk(_manifest(), core=core, config_values={})
        with pytest.raises(PluginCapabilityNotGrantedError):
            sdk.publish_event("goal.completed", {}, now=FIXED_TIME)

    def test_impersonating_another_plugin_at_call_time_is_rejected(self, core: CoreDatabase) -> None:
        sdk = build_plugin_sdk(_manifest(), core=core, config_values={})
        with pytest.raises(PluginCapabilityNotGrantedError):
            sdk.publish_event("plugin_someone_else.sent", {}, now=FIXED_TIME)


class TestPluginConfig:
    def test_declared_key_resolves(self, core: CoreDatabase) -> None:
        sdk = build_plugin_sdk(
            _manifest(config_keys=("GREETING",)), core=core, config_values={"GREETING": "hello"},
        )
        assert sdk.config.get("GREETING") == "hello"

    def test_undeclared_key_raises(self, core: CoreDatabase) -> None:
        sdk = build_plugin_sdk(_manifest(config_keys=("GREETING",)), core=core, config_values={"GREETING": "hello", "SECRET": "nope"})
        with pytest.raises(PluginCapabilityNotGrantedError):
            sdk.config.get("SECRET")

    def test_declared_but_missing_key_returns_default(self, core: CoreDatabase) -> None:
        sdk = build_plugin_sdk(_manifest(config_keys=("OPTIONAL_KEY",)), core=core, config_values={})
        assert sdk.config.get("OPTIONAL_KEY", "fallback") == "fallback"
