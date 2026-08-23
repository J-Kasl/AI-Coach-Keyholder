"""tests/conversation_engine/context_providers/test_lock_state_provider.py"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conversation_engine.context_providers.lock_state_provider import LockStateContextProvider
from infrastructure.database import Database as CoreDatabase
from lock_state.models import LockReportStatus
from lock_state.repository import LockState, LockStateAdministration

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent.parent / "database" / "migrations"
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
def admin(core: CoreDatabase) -> LockStateAdministration:
    return LockStateAdministration(core.db_path, core=core)


@pytest.fixture
def provider(core: CoreDatabase) -> LockStateContextProvider:
    return LockStateContextProvider(lock_state=LockState(core.db_path, core=core))


@pytest.fixture
def user_id(core: CoreDatabase) -> str:
    return _create_user(core)


class TestNamespace:
    def test_namespace_is_lock_state(self, provider: LockStateContextProvider) -> None:
        assert provider.namespace == "lock_state"


class TestUnknown:
    def test_no_report_yields_a_real_fragment_not_none(self, provider: LockStateContextProvider, user_id: str) -> None:
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert fragment is not None
        assert fragment.data["status"] == "unknown"

    def test_unknown_note_does_not_imply_unlocked(self, provider: LockStateContextProvider, user_id: str) -> None:
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert "do not infer" in fragment.data["note"].lower()
        assert "unlocked" not in fragment.data["note"].lower().replace("do not infer that the user is unlocked", "")


class TestLockedUserReported:
    def test_reports_locked_status(self, admin: LockStateAdministration, provider: LockStateContextProvider, user_id: str) -> None:
        admin.report_status(user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED, reported_via_consent_id="c1", now=FIXED_TIME)
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert fragment.data["status"] == "locked_user_reported"

    def test_note_never_claims_physical_verification(self, admin: LockStateAdministration, provider: LockStateContextProvider, user_id: str) -> None:
        admin.report_status(user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED, reported_via_consent_id="c1", now=FIXED_TIME)
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        note = fragment.data["note"].lower()
        assert "not been independently physically verified" in note
        assert "verified locked" not in note


class TestUnlockedUserReported:
    def test_reports_unlocked_status(self, admin: LockStateAdministration, provider: LockStateContextProvider, user_id: str) -> None:
        admin.report_status(user_id=user_id, status=LockReportStatus.UNLOCKED_USER_REPORTED, reported_via_consent_id="c1", now=FIXED_TIME)
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert fragment.data["status"] == "unlocked_user_reported"

    def test_note_never_claims_physical_verification(self, admin: LockStateAdministration, provider: LockStateContextProvider, user_id: str) -> None:
        admin.report_status(user_id=user_id, status=LockReportStatus.UNLOCKED_USER_REPORTED, reported_via_consent_id="c1", now=FIXED_TIME)
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert "not been independently physically verified" in fragment.data["note"].lower()


class TestNoProvenanceLeakage:
    def test_fragment_data_never_contains_consent_id(self, admin: LockStateAdministration, provider: LockStateContextProvider, user_id: str) -> None:
        admin.report_status(user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED, reported_via_consent_id="very-secret-consent-id", now=FIXED_TIME)
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert "very-secret-consent-id" not in str(fragment.data)

    def test_fragment_data_never_contains_the_subject_key_itself(self, admin: LockStateAdministration, provider: LockStateContextProvider, user_id: str) -> None:
        admin.report_status(user_id=user_id, status=LockReportStatus.LOCKED_USER_REPORTED, reported_via_consent_id="c1", now=FIXED_TIME)
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert user_id not in str(fragment.data)


class TestUserIsolation:
    def test_one_users_report_does_not_leak_to_another(self, admin: LockStateAdministration, provider: LockStateContextProvider, core: CoreDatabase) -> None:
        user_a = _create_user(core)
        user_b = _create_user(core)
        admin.report_status(user_id=user_a, status=LockReportStatus.LOCKED_USER_REPORTED, reported_via_consent_id="c1", now=FIXED_TIME)
        fragment_a = provider.provide_context(subject_key=user_a, now=FIXED_TIME)
        fragment_b = provider.provide_context(subject_key=user_b, now=FIXED_TIME)
        assert fragment_a.data["status"] == "locked_user_reported"
        assert fragment_b.data["status"] == "unknown"


class TestConcurrentSubjectIsolation:
    def test_same_provider_instance_concurrent_calls_never_cross_contaminate(
        self, admin: LockStateAdministration, provider: LockStateContextProvider, core: CoreDatabase,
    ) -> None:
        """The whole point of subject_key being a plain per-call argument,
        not instance state -- verified under genuine thread concurrency,
        not just sequential calls."""
        user_a = _create_user(core)
        user_b = _create_user(core)
        admin.report_status(user_id=user_a, status=LockReportStatus.LOCKED_USER_REPORTED, reported_via_consent_id="c1", now=FIXED_TIME)
        admin.report_status(user_id=user_b, status=LockReportStatus.UNLOCKED_USER_REPORTED, reported_via_consent_id="c2", now=FIXED_TIME)

        results: dict[str, str] = {}
        errors: list[str] = []

        def call(label: str, subject: str) -> None:
            for _ in range(20):
                fragment = provider.provide_context(subject_key=subject, now=FIXED_TIME)
                if results.get(label) not in (None, fragment.data["status"]):
                    errors.append(f"{label} saw inconsistent status")
                results[label] = fragment.data["status"]

        threads = [
            threading.Thread(target=call, args=("a", user_a)),
            threading.Thread(target=call, args=("b", user_b)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        assert results["a"] == "locked_user_reported"
        assert results["b"] == "unlocked_user_reported"


class TestNoAdministrationDependency:
    def test_provider_has_no_administration_attribute(self, provider: LockStateContextProvider) -> None:
        assert not hasattr(provider, "_lock_state_admin")
        assert not hasattr(provider, "lock_state_admin")
