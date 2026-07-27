"""
tests/infrastructure/test_startup_lease.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from infrastructure.database import Database
from infrastructure.startup_lease import acquire_system_startup_lease, release_system_startup_lease

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _apply_migration(db: Database) -> None:
    with db.raw_connection() as conn:
        conn.executescript(
            "CREATE TABLE schema_version (version INTEGER, applied_at TEXT, description TEXT);"
        )
        migration = Path(__file__).parent.parent.parent / "database" / "migrations" / "006_startup_lease.sql"
        conn.executescript(migration.read_text(encoding="utf-8"))


def _db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    _apply_migration(d)
    return d


class TestAcquireLease:
    def test_first_acquisition_succeeds(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        lease = acquire_system_startup_lease(db, "process-a", FIXED_TIME, timedelta(minutes=5))
        assert lease is not None
        assert lease.held_by == "process-a"

    def test_second_process_cannot_acquire_a_live_lease(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        acquire_system_startup_lease(db, "process-a", FIXED_TIME, timedelta(minutes=5))
        second = acquire_system_startup_lease(db, "process-b", FIXED_TIME + timedelta(seconds=1), timedelta(minutes=5))
        assert second is None

    def test_can_acquire_after_previous_lease_expires(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        acquire_system_startup_lease(db, "process-a", FIXED_TIME, timedelta(minutes=5))
        later = FIXED_TIME + timedelta(minutes=10)
        second = acquire_system_startup_lease(db, "process-b", later, timedelta(minutes=5))
        assert second is not None
        assert second.held_by == "process-b"

    def test_can_reacquire_after_release(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        lease = acquire_system_startup_lease(db, "process-a", FIXED_TIME, timedelta(minutes=5))
        release_system_startup_lease(db, lease)
        second = acquire_system_startup_lease(db, "process-b", FIXED_TIME + timedelta(seconds=1), timedelta(minutes=5))
        assert second is not None


class TestReleaseLease:
    def test_release_does_not_affect_a_different_holders_lease(self, tmp_path: Path) -> None:
        """A process holding an expired lease reference must never clear
        a DIFFERENT, now-current instance's live lease."""
        db = _db(tmp_path)
        stale_lease = acquire_system_startup_lease(db, "process-a", FIXED_TIME, timedelta(minutes=5))
        # process-a's lease expires; process-b takes over
        current_lease = acquire_system_startup_lease(db, "process-b", FIXED_TIME + timedelta(minutes=10), timedelta(minutes=5))
        assert current_lease is not None

        # process-a, unaware its lease expired, tries to release its own stale reference
        release_system_startup_lease(db, stale_lease)

        # process-b's lease must still be live
        third = acquire_system_startup_lease(db, "process-c", FIXED_TIME + timedelta(minutes=11), timedelta(minutes=5))
        assert third is None
