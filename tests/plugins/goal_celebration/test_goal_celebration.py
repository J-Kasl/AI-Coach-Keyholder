"""
tests/plugins/goal_celebration/test_goal_celebration.py

End-to-end tests for the first real plugin
(plugin_architecture_proposal.md Section 20) -- loaded through the
real `PluginRegistry` against the real `plugins/` directory, reacting
to a real `goal.completed` event published by the real
`goal_management` module. No synthetic plugin directories here (see
tests/infrastructure/test_plugin_registry.py for those) -- this is the
first genuine proof the whole design holds together outside synthetic
tests (Section 27, Step 3).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application.router import CommandRouter
from goal_management.repository import GoalManager
from infrastructure.consumer_registry import ConsumerRegistry
from infrastructure.database import Database as CoreDatabase
from infrastructure.plugin_registry import PluginRegistry

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

_PLUGINS_DIR = Path(__file__).parent.parent.parent.parent / "plugins"


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(c)
    return c


@pytest.fixture
def goal_management(core: CoreDatabase) -> GoalManager:
    return GoalManager(core.db_path, core=core)


def _load_goal_celebration(core: CoreDatabase, goal_management: GoalManager):
    consumer_registry = ConsumerRegistry()
    registry = PluginRegistry(
        plugins_dir=_PLUGINS_DIR, core=core,
        consumer_registry=consumer_registry, command_router=CommandRouter(),
        goal_management=goal_management,
    )
    loaded, failures = registry.load_all()
    return loaded, failures, consumer_registry


def _complete_a_goal(goal_management: GoalManager, *, now: datetime = FIXED_TIME) -> str:
    goal = goal_management.create_goal(
        title="Exercise several times per week", target_description="3 workouts per week",
        trust_domain="fitness", created_via="user_proposed", now=now,
    )
    goal_management.complete_goal(goal.goal_group_id, "durably achieved", now=now + timedelta(hours=1))
    return goal.goal_group_id


class TestGoalCelebrationLoadsCleanly:
    def test_loads_with_no_failures(self, core: CoreDatabase, goal_management: GoalManager) -> None:
        loaded, failures, _ = _load_goal_celebration(core, goal_management)
        assert failures == []
        names = [p.manifest.name for p in loaded]
        assert "goal_celebration" in names

    def test_its_own_migration_created_the_log_table(self, core: CoreDatabase, goal_management: GoalManager) -> None:
        _load_goal_celebration(core, goal_management)
        with core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='goal_celebration_log'"
            )
        assert row is not None

    def test_a_second_load_applies_no_duplicate_migration(self, core: CoreDatabase, goal_management: GoalManager) -> None:
        _load_goal_celebration(core, goal_management)  # first process start
        loaded, failures, _ = _load_goal_celebration(core, goal_management)  # simulates a second start
        assert failures == []
        assert len(loaded) == 1  # still loads cleanly; migration was a no-op the second time


class TestGoalCelebrationReactsToARealGoalCompletion:
    def test_celebrates_a_completed_goal(self, core: CoreDatabase, goal_management: GoalManager) -> None:
        from infrastructure.outbox import claim_pending_events

        goal_group_id = _complete_a_goal(goal_management)
        _loaded, failures, consumer_registry = _load_goal_celebration(core, goal_management)
        assert failures == []

        claimed = claim_pending_events(core, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))
        goal_completed = next(e for e in claimed if e.event_type == "goal.completed")
        ran = consumer_registry.dispatch(core, goal_completed, now=FIXED_TIME)
        assert ran == 1

        with core.transaction() as tx:
            log_row = tx.fetch_one("SELECT * FROM goal_celebration_log WHERE goal_group_id = ?", (goal_group_id,))
        assert log_row is not None

    def test_publishes_its_own_namespaced_event(self, core: CoreDatabase, goal_management: GoalManager) -> None:
        from infrastructure.outbox import claim_pending_events

        goal_group_id = _complete_a_goal(goal_management)
        _loaded, _failures, consumer_registry = _load_goal_celebration(core, goal_management)

        claimed = claim_pending_events(core, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))
        goal_completed = next(e for e in claimed if e.event_type == "goal.completed")
        consumer_registry.dispatch(core, goal_completed, now=FIXED_TIME)

        with core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT * FROM domain_events WHERE event_type = 'plugin_goal_celebration.sent'"
            )
        assert row is not None
        assert row["source_module"] == "plugin_goal_celebration"

    def test_never_writes_to_goal_managements_own_tables(self, core: CoreDatabase, goal_management: GoalManager) -> None:
        """PLUG-1, verified concretely: celebrating a Goal must never
        change anything about goal_management's own record of it."""
        from infrastructure.outbox import claim_pending_events

        goal_group_id = _complete_a_goal(goal_management)
        before = goal_management.get_goal(goal_group_id)

        _loaded, _failures, consumer_registry = _load_goal_celebration(core, goal_management)
        claimed = claim_pending_events(core, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))
        goal_completed = next(e for e in claimed if e.event_type == "goal.completed")
        consumer_registry.dispatch(core, goal_completed, now=FIXED_TIME)

        after = goal_management.get_goal(goal_group_id)
        assert before == after

    def test_does_not_celebrate_the_same_goal_twice(self, core: CoreDatabase, goal_management: GoalManager) -> None:
        from infrastructure.outbox import claim_pending_events

        goal_group_id = _complete_a_goal(goal_management)
        _loaded, _failures, consumer_registry = _load_goal_celebration(core, goal_management)

        claimed = claim_pending_events(core, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))
        goal_completed = next(e for e in claimed if e.event_type == "goal.completed")
        consumer_registry.dispatch(core, goal_completed, now=FIXED_TIME)  # first delivery
        consumer_registry.dispatch(core, goal_completed, now=FIXED_TIME)  # a redelivery of the same claimed event

        with core.transaction() as tx:
            rows = tx.fetch_all("SELECT * FROM goal_celebration_log WHERE goal_group_id = ?", (goal_group_id,))
        assert len(rows) == 1
