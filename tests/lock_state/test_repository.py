"""tests/lock_state/test_repository.py"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infrastructure.database import Database as CoreDatabase
from lock_state.models import LockKnowledgeState, LockReportStatus
from lock_state.repository import LockState, LockStateAdministration

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


def _create_user(core: CoreDatabase) -> str:
    user_id = str(uuid.uuid4())
    with core.raw_connection() as conn:
        conn.execute(
            "INSERT INTO user_accounts (id, created_at, last_seen_at) VALUES (?, ?, ?)",
            (user_id, FIXED_TIME.isoformat(), FIXED_TIME.isoformat()),
        )
        conn.commit()
    return user_id


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(c)
    return c


@pytest.fixture
def reader(core: CoreDatabase) -> LockState:
    return LockState(core.db_path, core=core)


@pytest.fixture
def admin(core: CoreDatabase) -> LockStateAdministration:
    return LockStateAdministration(core.db_path, core=core)


@pytest.fixture
def user_id(core: CoreDatabase) -> str:
    return _create_user(core)


class TestUnknownIsTheAbsenceState:
    def test_unknown_before_any_report(self, reader: LockState, user_id: str) -> None:
        assert reader.get_current_knowledge_state(user_id) == LockKnowledgeState.UNKNOWN

    def test_get_current_report_returns_none_before_any_report(self, reader: LockState, user_id: str) -> None:
        assert reader.get_current_report(user_id) is None

    def test_unknown_is_never_stored_as_a_row(self, admin: LockStateAdministration, core: CoreDatabase, user_id: str) -> None:
        """The DB contains only real user reports -- there is no code
        path that could ever insert an 'unknown' row."""
        admin.report_status(
            user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED,
            reported_via_consent_id="discord_message:1", now=FIXED_TIME,
        )
        with core.raw_connection() as conn:
            rows = conn.execute("SELECT status FROM lock_reports").fetchall()
        assert all(r["status"] in ("locked_user_reported", "unlocked_user_reported") for r in rows)


class TestReportAndRead:
    def test_report_then_read_round_trips(self, admin: LockStateAdministration, reader: LockState, user_id: str) -> None:
        admin.report_status(
            user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED,
            reported_via_consent_id="discord_message:1", now=FIXED_TIME,
        )
        assert reader.get_current_knowledge_state(user_id) == LockKnowledgeState.LOCKED_USER_REPORTED

    def test_second_report_becomes_current(self, admin: LockStateAdministration, reader: LockState, user_id: str) -> None:
        admin.report_status(
            user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED,
            reported_via_consent_id="discord_message:1", now=FIXED_TIME,
        )
        admin.report_status(
            user_id=user_id, status=LockReportStatus.UNLOCKED_USER_REPORTED,
            reported_via_consent_id="discord_message:2", now=FIXED_TIME,
        )
        assert reader.get_current_knowledge_state(user_id) == LockKnowledgeState.UNLOCKED_USER_REPORTED

    def test_older_report_is_not_lost_it_is_superseded(self, admin: LockStateAdministration, core: CoreDatabase, user_id: str) -> None:
        """Append-only -- the earlier row still exists, just isn't 'current'."""
        admin.report_status(
            user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED,
            reported_via_consent_id="discord_message:1", now=FIXED_TIME,
        )
        admin.report_status(
            user_id=user_id, status=LockReportStatus.UNLOCKED_USER_REPORTED,
            reported_via_consent_id="discord_message:2", now=FIXED_TIME,
        )
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as n FROM lock_reports WHERE user_id = ?", (user_id,)).fetchone()["n"]
        assert count == 2


class TestDeterministicOrdering:
    def test_current_report_is_the_highest_sequence_number_not_random_sqlite_order(
        self, admin: LockStateAdministration, reader: LockState, user_id: str,
    ) -> None:
        """Both reports share the exact same timestamp -- if ordering
        relied on timestamp precision alone, this would be ambiguous.
        sequence_number is the real, explicit tiebreaker."""
        admin.report_status(
            user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED,
            reported_via_consent_id="discord_message:1", now=FIXED_TIME,
        )
        second = admin.report_status(
            user_id=user_id, status=LockReportStatus.UNLOCKED_USER_REPORTED,
            reported_via_consent_id="discord_message:2", now=FIXED_TIME,  # identical timestamp
        )
        current = reader.get_current_report(user_id)
        assert current is not None
        assert current.id == second.id
        assert current.sequence_number == 2


class TestUserIsolation:
    def test_one_users_report_is_not_visible_to_another(self, admin: LockStateAdministration, reader: LockState, core: CoreDatabase) -> None:
        user_a = _create_user(core)
        user_b = _create_user(core)
        admin.report_status(
            user_id=user_a, status=LockReportStatus.LOCKED_USER_REPORTED,
            reported_via_consent_id="discord_message:1", now=FIXED_TIME,
        )
        assert reader.get_current_knowledge_state(user_a) == LockKnowledgeState.LOCKED_USER_REPORTED
        assert reader.get_current_knowledge_state(user_b) == LockKnowledgeState.UNKNOWN


class TestInvalidInputRejected:
    def test_empty_user_id_rejected_on_report(self, admin: LockStateAdministration) -> None:
        with pytest.raises(ValueError, match="user_id"):
            admin.report_status(
                user_id="", status=LockReportStatus.LOCKED_USER_REPORTED,
                reported_via_consent_id="discord_message:1", now=FIXED_TIME,
            )

    def test_empty_user_id_rejected_on_read(self, reader: LockState) -> None:
        with pytest.raises(ValueError, match="user_id"):
            reader.get_current_report("")

    def test_empty_consent_id_rejected(self, admin: LockStateAdministration, user_id: str) -> None:
        with pytest.raises(ValueError, match="reported_via_consent_id"):
            admin.report_status(
                user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED,
                reported_via_consent_id="", now=FIXED_TIME,
            )

    def test_whitespace_only_consent_id_rejected(self, admin: LockStateAdministration, user_id: str) -> None:
        with pytest.raises(ValueError, match="reported_via_consent_id"):
            admin.report_status(
                user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED,
                reported_via_consent_id="   ", now=FIXED_TIME,
            )


class TestFailedWriteLeavesNoPartialState:
    def test_rejected_consent_id_writes_nothing(self, admin: LockStateAdministration, core: CoreDatabase, user_id: str) -> None:
        with pytest.raises(ValueError):
            admin.report_status(
                user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED,
                reported_via_consent_id="", now=FIXED_TIME,
            )
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as n FROM lock_reports").fetchone()["n"]
        assert count == 0

    def test_foreign_key_violation_writes_nothing(self, admin: LockStateAdministration, core: CoreDatabase) -> None:
        """A user_id with no matching user_accounts row -- the FK
        constraint itself should reject the insert, and the transaction
        rolls back cleanly."""
        with pytest.raises(Exception):
            admin.report_status(
                user_id="nonexistent-user-id", status=LockReportStatus.LOCKED_USER_REPORTED,
                reported_via_consent_id="discord_message:1", now=FIXED_TIME,
            )
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as n FROM lock_reports").fetchone()["n"]
        assert count == 0


class TestReadDoesNotWrite:
    def test_reading_current_state_repeatedly_does_not_create_rows(self, reader: LockState, core: CoreDatabase, user_id: str) -> None:
        reader.get_current_report(user_id)
        reader.get_current_knowledge_state(user_id)
        reader.get_current_report(user_id)
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as n FROM lock_reports").fetchone()["n"]
        assert count == 0
