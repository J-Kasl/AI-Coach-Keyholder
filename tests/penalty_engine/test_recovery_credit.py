"""
tests/penalty_engine/test_recovery_credit.py

Tests for Recovery Credit integration
(docs/architecture/penalty_window_technical_design.md Section 3.4,
applying recovery_plan_technical_design.md Section 6).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from infrastructure.database import Database as CoreDatabase
from penalty_engine.models import PenaltyWindowNotFound
from penalty_engine.repository import PenaltyEngine
from recovery_plan.repository import RecoveryPlanManager

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
def pe(core: CoreDatabase) -> PenaltyEngine:
    return PenaltyEngine(core.db_path, core=core)


@pytest.fixture
def rp(core: CoreDatabase) -> RecoveryPlanManager:
    return RecoveryPlanManager(core.db_path, core=core)


def _create_window_and_plan(core: CoreDatabase, rp: RecoveryPlanManager, base_duration_hours: float = 24.0) -> str:
    """base_duration_hours=24 -> capacity = 12h (I3)."""
    with core.transaction() as tx:
        tx.execute(
            """
            INSERT INTO penalty_windows
                (id, created_at, status, base_duration_hours, extensions_hours, accumulated_active_hours, active_period_started_at)
            VALUES ('win-1', ?, 'active', ?, 0, 0, ?)
            """,
            (_iso(FIXED_TIME), base_duration_hours, _iso(FIXED_TIME)),
        )
    rp.create_plan("win-1", base_duration_hours=base_duration_hours, now=FIXED_TIME)
    return "win-1"


class TestRecordRecoveryCreditDirect:
    def test_credits_task_in_full_when_within_capacity(self, pe: PenaltyEngine, rp: RecoveryPlanManager) -> None:
        _create_window_and_plan(core=pe._core, rp=rp)  # capacity = 12h
        plan = rp.get_recovery_plan_for_window("win-1")
        task = rp.propose_task(plan.id, "Journal", "desc", credit_hours=4.0, now=FIXED_TIME)
        completion = rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))

        decision = pe.record_recovery_credit_from_task_completion(rp, completion.id, now=FIXED_TIME + timedelta(hours=1))
        assert decision.credited_hours == 4.0
        assert decision.capacity_limited is False

    def test_caps_at_remaining_capacity(self, pe: PenaltyEngine, rp: RecoveryPlanManager) -> None:
        _create_window_and_plan(core=pe._core, rp=rp)  # capacity = 12h
        plan = rp.get_recovery_plan_for_window("win-1")
        task = rp.propose_task(plan.id, "Big task", "desc", credit_hours=50.0, now=FIXED_TIME)
        completion = rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))

        decision = pe.record_recovery_credit_from_task_completion(rp, completion.id, now=FIXED_TIME + timedelta(hours=1))
        assert decision.credited_hours == 12.0
        assert decision.capacity_limited is True

    def test_updates_window_earned_hours(self, pe: PenaltyEngine, rp: RecoveryPlanManager) -> None:
        _create_window_and_plan(core=pe._core, rp=rp)
        plan = rp.get_recovery_plan_for_window("win-1")
        task = rp.propose_task(plan.id, "Journal", "desc", credit_hours=4.0, now=FIXED_TIME)
        completion = rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))
        pe.record_recovery_credit_from_task_completion(rp, completion.id, now=FIXED_TIME + timedelta(hours=1))

        with pe._core.transaction() as tx:
            row = tx.fetch_one("SELECT recovery_credits_earned_hours FROM penalty_windows WHERE id = 'win-1'")
        assert row["recovery_credits_earned_hours"] == 4.0

    def test_second_completion_respects_remaining_capacity_not_full_capacity_again(
        self, pe: PenaltyEngine, rp: RecoveryPlanManager,
    ) -> None:
        _create_window_and_plan(core=pe._core, rp=rp)  # capacity = 12h
        plan = rp.get_recovery_plan_for_window("win-1")

        task1 = rp.propose_task(plan.id, "Task A", "desc", credit_hours=8.0, now=FIXED_TIME)
        completion1 = rp.complete_task(task1.id, now=FIXED_TIME + timedelta(hours=1))
        decision1 = pe.record_recovery_credit_from_task_completion(rp, completion1.id, now=FIXED_TIME + timedelta(hours=1))
        assert decision1.credited_hours == 8.0

        task2 = rp.propose_task(plan.id, "Task B", "desc", credit_hours=8.0, now=FIXED_TIME + timedelta(hours=2))
        completion2 = rp.complete_task(task2.id, now=FIXED_TIME + timedelta(hours=3))
        decision2 = pe.record_recovery_credit_from_task_completion(rp, completion2.id, now=FIXED_TIME + timedelta(hours=3))
        # only 4h of capacity remained (12 - 8)
        assert decision2.credited_hours == 4.0
        assert decision2.capacity_limited is True

    def test_zero_credit_hours_task_still_writes_a_decision(self, pe: PenaltyEngine, rp: RecoveryPlanManager) -> None:
        _create_window_and_plan(core=pe._core, rp=rp)
        plan = rp.get_recovery_plan_for_window("win-1")
        task = rp.propose_task(plan.id, "Trivial", "desc", credit_hours=0.0, now=FIXED_TIME)
        completion = rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))
        decision = pe.record_recovery_credit_from_task_completion(rp, completion.id, now=FIXED_TIME + timedelta(hours=1))
        assert decision.credited_hours == 0.0
        assert decision.explanation.strip() != ""

    def test_no_ledger_entry_when_zero_credited(self, pe: PenaltyEngine, rp: RecoveryPlanManager) -> None:
        _create_window_and_plan(core=pe._core, rp=rp)
        plan = rp.get_recovery_plan_for_window("win-1")

        # exhaust capacity first
        task1 = rp.propose_task(plan.id, "Task A", "desc", credit_hours=12.0, now=FIXED_TIME)
        completion1 = rp.complete_task(task1.id, now=FIXED_TIME + timedelta(hours=1))
        pe.record_recovery_credit_from_task_completion(rp, completion1.id, now=FIXED_TIME + timedelta(hours=1))

        task2 = rp.propose_task(plan.id, "Task B", "desc", credit_hours=5.0, now=FIXED_TIME + timedelta(hours=2))
        completion2 = rp.complete_task(task2.id, now=FIXED_TIME + timedelta(hours=3))
        decision2 = pe.record_recovery_credit_from_task_completion(rp, completion2.id, now=FIXED_TIME + timedelta(hours=3))
        assert decision2.credited_hours == 0.0

        with pe._core.transaction() as tx:
            ledger_rows = tx.fetch_all("SELECT * FROM recovery_credit_ledger WHERE source_completion_id = ?", (completion2.id,))
        assert ledger_rows == []

    def test_decision_recorded_event_emitted(self, pe: PenaltyEngine, rp: RecoveryPlanManager) -> None:
        _create_window_and_plan(core=pe._core, rp=rp)
        plan = rp.get_recovery_plan_for_window("win-1")
        task = rp.propose_task(plan.id, "Journal", "desc", credit_hours=4.0, now=FIXED_TIME)
        completion = rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))
        pe.record_recovery_credit_from_task_completion(rp, completion.id, now=FIXED_TIME + timedelta(hours=1))

        with pe._core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'recovery_credit_decision.recorded'")
        assert row is not None

    def test_missing_completion_raises(self, pe: PenaltyEngine, rp: RecoveryPlanManager) -> None:
        with pytest.raises(ValueError):
            pe.record_recovery_credit_from_task_completion(rp, "does-not-exist", now=FIXED_TIME)

    def test_missing_window_raises(self, pe: PenaltyEngine) -> None:
        """Tests the tx-only core function directly with a fabricated,
        never-created penalty_window_id -- simpler than trying to force
        an inconsistent RecoveryPlan/Window pairing through the real
        FK-constrained schema."""
        with pytest.raises(PenaltyWindowNotFound):
            with pe._core.transaction() as tx:
                pe._record_recovery_credit_in_transaction(tx, "completion-x", "never-existed", 4.0, FIXED_TIME)


class TestI26Dedup:
    def test_duplicate_completion_id_raises_integrity_error(self, pe: PenaltyEngine, rp: RecoveryPlanManager) -> None:
        """I26 primary guarantee: UNIQUE(completion_id) on recovery_credit_decisions."""
        import sqlite3

        _create_window_and_plan(core=pe._core, rp=rp)
        plan = rp.get_recovery_plan_for_window("win-1")
        task = rp.propose_task(plan.id, "Journal", "desc", credit_hours=4.0, now=FIXED_TIME)
        completion = rp.complete_task(task.id, now=FIXED_TIME + timedelta(hours=1))
        pe.record_recovery_credit_from_task_completion(rp, completion.id, now=FIXED_TIME + timedelta(hours=1))

        with pytest.raises(sqlite3.IntegrityError):
            pe.record_recovery_credit_from_task_completion(rp, completion.id, now=FIXED_TIME + timedelta(hours=2))
