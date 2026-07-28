"""
tests/recovery_plan/test_repository.py

Tests for recovery_plan/repository.py
(docs/architecture/recovery_plan_technical_design.md, RPT1-RPT8-style
coverage). Uses a real Penalty Window row (via direct SQL setup, not
the full PenaltyEngine, to keep these tests focused on Recovery Plan's
own reactions) as the thing being reacted to.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from infrastructure.database import Database as CoreDatabase
from recovery_plan.models import RecoveryPlanStatus, RecoveryTaskStatus
from recovery_plan.repository import (
    InvalidTaskTransitionError,
    RecoveryPlanManager,
    RecoveryPlanNotFoundError,
    RecoveryTaskNotFoundError,
)

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(c)
    return c


@pytest.fixture
def rp(core: CoreDatabase) -> RecoveryPlanManager:
    return RecoveryPlanManager(core.db_path, core=core)


def _create_penalty_window(core: CoreDatabase, window_id: str = "win-1", base_duration_hours: float = 24.0) -> None:
    """Minimal, direct SQL setup of a penalty_windows row -- Recovery
    Plan only ever reads penalty_windows for the consistency check (8);
    it never depends on the full PenaltyEngine to react to its events."""
    with core.transaction() as tx:
        tx.execute(
            """
            INSERT INTO penalty_windows
                (id, created_at, status, base_duration_hours, extensions_hours, accumulated_active_hours, active_period_started_at)
            VALUES (?, ?, 'active', ?, 0, 0, ?)
            """,
            (window_id, _iso(FIXED_TIME), base_duration_hours, _iso(FIXED_TIME)),
        )


class TestPlanCreation:
    def test_rpt1_plan_created_on_window_start(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        assert plan.status == RecoveryPlanStatus.ACTIVE
        assert plan.penalty_window_id == "win-1"

    def test_capacity_is_half_of_base_duration(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        """RP-3/I3: recovery_credit_capacity_hours = target_active_hours / 2."""
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        assert plan.recovery_credit_capacity_hours == 12.0

    def test_created_event_emitted(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'recovery_plan.created'")
        assert row is not None


class TestStatusMirroring:
    def test_rpt2_mirrors_frozen_then_resumed(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)

        rp.mirror_frozen("win-1", now=FIXED_TIME + timedelta(hours=1))
        frozen = rp.get_recovery_plan_for_window("win-1")
        assert frozen.status == RecoveryPlanStatus.FROZEN

        rp.mirror_resumed("win-1", now=FIXED_TIME + timedelta(hours=2))
        active = rp.get_recovery_plan_for_window("win-1")
        assert active.status == RecoveryPlanStatus.ACTIVE

    def test_rpt7_completed_on_window_completion(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        rp.complete_plan("win-1", now=FIXED_TIME + timedelta(hours=25))
        plan = rp.get_recovery_plan_for_window("win-1")
        assert plan.status == RecoveryPlanStatus.COMPLETED

    def test_mirroring_a_nonexistent_plan_returns_none(self, rp: RecoveryPlanManager) -> None:
        """A detectable anomaly (recover_recovery_plan_state), not an
        exception -- the redelivery path is expected to eventually
        create the plan."""
        assert rp.mirror_frozen("does-not-exist", now=FIXED_TIME) is None

    def test_rpt8_no_plan_exists_without_a_window(self, rp: RecoveryPlanManager) -> None:
        assert rp.get_recovery_plan_for_window("never-existed") is None


class TestRegeneration:
    def test_rpt3_regeneration_expires_stale_tasks_preserves_completed(
        self, rp: RecoveryPlanManager, core: CoreDatabase,
    ) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)

        completed_task = rp.propose_task(plan.id, "Task A", "desc", credit_hours=4.0, now=FIXED_TIME)
        rp.complete_task(completed_task.id, now=FIXED_TIME + timedelta(minutes=30))

        proposed_task = rp.propose_task(plan.id, "Task B", "desc", credit_hours=4.0, now=FIXED_TIME + timedelta(hours=1))

        rp.regenerate("win-1", new_target_active_hours=30.0, now=FIXED_TIME + timedelta(hours=2))

        refreshed_completed = rp.get_recovery_task(completed_task.id)
        refreshed_proposed = rp.get_recovery_task(proposed_task.id)
        assert refreshed_completed.status == RecoveryTaskStatus.COMPLETED  # untouched
        assert refreshed_proposed.status == RecoveryTaskStatus.EXPIRED     # expired

    def test_rpt4_completion_record_untouched_by_regeneration(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Task A", "desc", credit_hours=4.0, now=FIXED_TIME)
        completion = rp.complete_task(task.id, now=FIXED_TIME + timedelta(minutes=30))

        rp.regenerate("win-1", new_target_active_hours=30.0, now=FIXED_TIME + timedelta(hours=2))

        still_there = rp.get_recovery_task_completion(completion.id)
        assert still_there is not None
        assert still_there.id == completion.id

    def test_regeneration_increments_version_and_updates_capacity(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        result = rp.regenerate("win-1", new_target_active_hours=30.0, now=FIXED_TIME + timedelta(hours=1))
        assert result.current_version == 2
        assert result.recovery_credit_capacity_hours == 15.0

    def test_regenerated_event_emitted(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        rp.regenerate("win-1", new_target_active_hours=30.0, now=FIXED_TIME + timedelta(hours=1))
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'recovery_plan.regenerated'")
        assert row is not None


class TestTaskLifecycle:
    def test_propose_accept_complete(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Journal", "Write a reflection", credit_hours=3.0, now=FIXED_TIME)
        assert task.status == RecoveryTaskStatus.PROPOSED

        rp.accept_task(task.id, now=FIXED_TIME + timedelta(minutes=10))
        accepted = rp.get_recovery_task(task.id)
        assert accepted.status == RecoveryTaskStatus.ACCEPTED

        completion = rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1), notes="done well")
        completed = rp.get_recovery_task(task.id)
        assert completed.status == RecoveryTaskStatus.COMPLETED
        assert completion.notes == "done well"

    def test_rpt5_task_completed_event_is_published_no_ledger_write(
        self, rp: RecoveryPlanManager, core: CoreDatabase,
    ) -> None:
        """RP-1/RP-8: this module never writes recovery_credit_ledger
        itself -- only publishes the event the Penalty Engine's
        Recovery Credit integration (Phase 2.7) consumes. Checked here
        by confirming zero rows exist immediately after
        complete_task(), before any consumer has had a chance to run --
        this module's own write scope, not the table's mere existence
        (which migration 009 now creates regardless)."""
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Journal", "desc", credit_hours=3.0, now=FIXED_TIME)
        rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))

        with core.transaction() as tx:
            event_row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'recovery_plan.task_completed'")
            ledger_rows = tx.fetch_all("SELECT * FROM recovery_credit_ledger")
        assert event_row is not None
        assert ledger_rows == []

    def test_withdraw_task(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Journal", "desc", credit_hours=3.0, now=FIXED_TIME)
        rp.withdraw_task(task.id, now=FIXED_TIME + timedelta(minutes=10))
        withdrawn = rp.get_recovery_task(task.id)
        assert withdrawn.status == RecoveryTaskStatus.WITHDRAWN

    def test_propose_task_for_missing_plan_raises(self, rp: RecoveryPlanManager) -> None:
        with pytest.raises(RecoveryPlanNotFoundError):
            rp.propose_task("does-not-exist", "Title", "desc", credit_hours=1.0, now=FIXED_TIME)

    def test_complete_missing_task_raises(self, rp: RecoveryPlanManager) -> None:
        with pytest.raises(RecoveryTaskNotFoundError):
            rp.complete_task("does-not-exist", now=FIXED_TIME)


class TestInvalidTaskTransitions:
    """
    Finding #1 from the focused post-Phase-2.7 architectural review:
    none of accept_task()/complete_task()/withdraw_task() checked the
    task's current status before applying the new one. An EXPIRED or
    WITHDRAWN task could still be "completed", producing a real
    RecoveryTaskCompletion (and downstream Recovery Credit) for a task
    the system itself already considered dead.
    """

    def test_cannot_complete_an_expired_task(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Task A", "desc", credit_hours=4.0, now=FIXED_TIME)
        rp.regenerate("win-1", new_target_active_hours=30.0, now=FIXED_TIME + timedelta(hours=1))
        expired = rp.get_recovery_task(task.id)
        assert expired.status == RecoveryTaskStatus.EXPIRED  # sanity check on the setup

        with pytest.raises(InvalidTaskTransitionError):
            rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=2))

    def test_cannot_complete_a_withdrawn_task(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Task A", "desc", credit_hours=4.0, now=FIXED_TIME)
        rp.withdraw_task(task.id, now=FIXED_TIME + timedelta(minutes=10))

        with pytest.raises(InvalidTaskTransitionError):
            rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))

    def test_cannot_complete_an_already_completed_task_twice(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Task A", "desc", credit_hours=4.0, now=FIXED_TIME)
        rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))

        with pytest.raises(InvalidTaskTransitionError):
            rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=2))

    def test_cannot_accept_an_already_accepted_task(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Task A", "desc", credit_hours=4.0, now=FIXED_TIME)
        rp.accept_task(task.id, now=FIXED_TIME + timedelta(minutes=10))

        with pytest.raises(InvalidTaskTransitionError):
            rp.accept_task(task.id, now=FIXED_TIME + timedelta(minutes=20))

    def test_cannot_withdraw_a_completed_task(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Task A", "desc", credit_hours=4.0, now=FIXED_TIME)
        rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))

        with pytest.raises(InvalidTaskTransitionError):
            rp.withdraw_task(task.id, now=FIXED_TIME + timedelta(hours=2))

    def test_completing_directly_from_proposed_still_works(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        """Completion without an explicit accept step remains legitimate
        -- this guard narrows what's INVALID, not what's ALLOWED."""
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Task A", "desc", credit_hours=4.0, now=FIXED_TIME)
        completion = rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))
        assert completion is not None

    def test_error_message_names_the_actual_and_allowed_states(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        task = rp.propose_task(plan.id, "Task A", "desc", credit_hours=4.0, now=FIXED_TIME)
        rp.withdraw_task(task.id, now=FIXED_TIME + timedelta(minutes=10))
        try:
            rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))
        except InvalidTaskTransitionError as e:
            assert e.current_status == "withdrawn"
            assert e.requested_status == "completed"
        else:
            pytest.fail("expected InvalidTaskTransitionError")

    """RPT6's actual capping happens in the (deferred) Penalty Engine
    Recovery Credit integration -- this module only stores the Coach's
    proposed credit_hours without re-enforcing the cap itself (3.3)."""

    def test_credit_hours_exceeding_capacity_is_still_stored_uncapped(
        self, rp: RecoveryPlanManager, core: CoreDatabase,
    ) -> None:
        _create_penalty_window(core)
        plan = rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)  # capacity = 12.0
        task = rp.propose_task(plan.id, "Big task", "desc", credit_hours=50.0, now=FIXED_TIME)
        assert task.credit_hours == 50.0  # not capped here -- Penalty Engine's job, later


class TestCrashRecoveryConsistencyCheck:
    def test_reports_no_anomaly_when_healthy(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        assert rp.recover_recovery_plan_state(FIXED_TIME) == []

    def test_reports_a_window_with_no_plan_at_all(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)  # no rp.create_plan() call -- simulates a crash before redelivery
        anomalies = rp.recover_recovery_plan_state(FIXED_TIME)
        assert anomalies == ["win-1"]

    def test_reports_a_status_mismatch(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        with core.transaction() as tx:
            tx.execute("UPDATE penalty_windows SET status = 'frozen' WHERE id = 'win-1'")
        # plan is still ACTIVE, window is now FROZEN -- a mismatch
        anomalies = rp.recover_recovery_plan_state(FIXED_TIME)
        assert anomalies == ["win-1"]

    def test_does_not_check_completed_windows(self, rp: RecoveryPlanManager, core: CoreDatabase) -> None:
        _create_penalty_window(core)
        rp.create_plan("win-1", base_duration_hours=24.0, now=FIXED_TIME)
        with core.transaction() as tx:
            tx.execute("UPDATE penalty_windows SET status = 'completed' WHERE id = 'win-1'")
        # plan is still ACTIVE (never told to complete) -- but this
        # function only checks ACTIVE/FROZEN windows, so a completed
        # window with a stale plan is not this function's concern.
        assert rp.recover_recovery_plan_state(FIXED_TIME) == []
