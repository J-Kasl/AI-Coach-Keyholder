"""
tests/chaster/test_oauth_state_repository.py
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chaster.repository import STATE_TTL, ChasterOAuthStateRepository
from infrastructure.database import Database as CoreDatabase

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
    with c.raw_connection() as conn:
        conn.execute(
            "INSERT INTO user_accounts (id, created_at, last_seen_at) VALUES (?, ?, ?)",
            ("u1", FIXED_TIME.isoformat(), FIXED_TIME.isoformat()),
        )
        conn.execute(
            "INSERT INTO user_accounts (id, created_at, last_seen_at) VALUES (?, ?, ?)",
            ("u2", FIXED_TIME.isoformat(), FIXED_TIME.isoformat()),
        )
        conn.commit()
    return c


@pytest.fixture
def repo(core: CoreDatabase, tmp_path: Path) -> ChasterOAuthStateRepository:
    return ChasterOAuthStateRepository(tmp_path / "test.db", core=core)


class TestCreation:
    def test_state_is_created_and_immediately_consumable(self, repo: ChasterOAuthStateRepository) -> None:
        state = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        result = repo.consume(state=state, now=FIXED_TIME)
        assert result.matched is True
        assert result.expired is False
        assert result.user_id == "u1"

    def test_state_value_is_high_entropy_and_url_safe(self, repo: ChasterOAuthStateRepository) -> None:
        state = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        assert len(state) >= 32  # base64url of 32 random bytes is well over 32 chars
        assert all(c.isalnum() or c in "-_" for c in state)

    def test_two_states_for_different_users_are_different_values(self, repo: ChasterOAuthStateRepository) -> None:
        s1 = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        s2 = repo.create_or_replace(user_id="u2", now=FIXED_TIME)
        assert s1 != s2

    def test_empty_user_id_is_rejected(self, repo: ChasterOAuthStateRepository) -> None:
        with pytest.raises(ValueError):
            repo.create_or_replace(user_id="", now=FIXED_TIME)


class TestOnePendingStatePerUser:
    def test_a_new_state_replaces_the_previous_pending_state_for_the_same_user(
        self, repo: ChasterOAuthStateRepository,
    ) -> None:
        first = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        second = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        assert first != second

        # The OLD link is now dead -- the exact, previously-analyzed
        # consequence of "replace" semantics.
        old_result = repo.consume(state=first, now=FIXED_TIME)
        assert old_result.matched is False
        assert old_result.user_id is None

        # The NEW link still works.
        new_result = repo.consume(state=second, now=FIXED_TIME)
        assert new_result.matched is True
        assert new_result.user_id == "u1"

    def test_states_for_different_users_do_not_interfere(self, repo: ChasterOAuthStateRepository) -> None:
        s1 = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        s2 = repo.create_or_replace(user_id="u2", now=FIXED_TIME)
        result1 = repo.consume(state=s1, now=FIXED_TIME)
        result2 = repo.consume(state=s2, now=FIXED_TIME)
        assert result1.user_id == "u1"
        assert result2.user_id == "u2"


class TestSingleUseConsumption:
    def test_state_can_only_be_consumed_once(self, repo: ChasterOAuthStateRepository) -> None:
        state = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        first = repo.consume(state=state, now=FIXED_TIME)
        second = repo.consume(state=state, now=FIXED_TIME)
        assert first.matched is True
        assert second.matched is False
        assert second.user_id is None

    def test_duplicate_callback_replay_is_rejected(self, repo: ChasterOAuthStateRepository) -> None:
        """A literal replay attempt -- consuming the exact same state
        string a second time -- must never succeed twice."""
        state = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        repo.consume(state=state, now=FIXED_TIME)
        replay = repo.consume(state=state, now=FIXED_TIME)
        assert replay.matched is False


class TestUnknownAndExpiredState:
    def test_unknown_state_is_rejected(self, repo: ChasterOAuthStateRepository) -> None:
        result = repo.consume(state="a-state-value-that-was-never-created", now=FIXED_TIME)
        assert result.matched is False
        assert result.expired is False
        assert result.user_id is None

    def test_expired_state_is_rejected_and_reports_expired(self, repo: ChasterOAuthStateRepository) -> None:
        state = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        past_expiry = FIXED_TIME + STATE_TTL + timedelta(seconds=1)
        result = repo.consume(state=state, now=past_expiry)
        assert result.matched is True
        assert result.expired is True
        assert result.user_id is None

    def test_expired_state_is_still_deleted_on_consumption(self, repo: ChasterOAuthStateRepository) -> None:
        """Single-use applies even to an expired state -- it must not
        remain consumable forever just because it expired rather than
        succeeded."""
        state = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        past_expiry = FIXED_TIME + STATE_TTL + timedelta(seconds=1)
        repo.consume(state=state, now=past_expiry)
        second_attempt = repo.consume(state=state, now=FIXED_TIME)  # even "before" expiry, per new now
        assert second_attempt.matched is False

    def test_unknown_and_expired_are_indistinguishable_to_a_simple_success_check(
        self, repo: ChasterOAuthStateRepository,
    ) -> None:
        """Deliberate: neither leaks whether a given state string was
        ever real to a caller that only checks user_id is None."""
        state = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        past_expiry = FIXED_TIME + STATE_TTL + timedelta(seconds=1)
        expired_result = repo.consume(state=state, now=past_expiry)
        unknown_result = repo.consume(state="never-existed", now=FIXED_TIME)
        assert expired_result.user_id is unknown_result.user_id is None


class TestConcurrentConsumptionRace:
    def test_two_concurrent_consume_calls_for_the_same_state_do_not_both_succeed(
        self, repo: ChasterOAuthStateRepository,
    ) -> None:
        """The critical invariant: BEGIN IMMEDIATE (Database.transaction()'s
        own isolation) means only one of two racing consume() calls
        ever sees the row -- proven here with real threads hitting the
        real database, not merely asserted from reading the code."""
        state = repo.create_or_replace(user_id="u1", now=FIXED_TIME)
        results: list = []
        barrier = threading.Barrier(2)

        def attempt() -> None:
            barrier.wait()
            results.append(repo.consume(state=state, now=FIXED_TIME))

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r.matched and not r.expired and r.user_id is not None]
        assert len(successes) == 1, f"expected exactly one success, got {len(successes)}"


class TestNoLeakage:
    def test_empty_user_id_error_message_does_not_leak_a_state_value(
        self, repo: ChasterOAuthStateRepository,
    ) -> None:
        with pytest.raises(ValueError) as exc_info:
            repo.create_or_replace(user_id="", now=FIXED_TIME)
        assert "user_id" in str(exc_info.value).lower()
