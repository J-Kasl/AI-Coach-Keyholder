"""
tests/chaster/test_provider_observation_repository.py
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chaster.models import ChasterProviderObservation, ChasterProviderStatus
from chaster.provider_observation_repository import (
    ChasterProviderObservationRecorder,
    ChasterProviderObservations,
)
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
        for uid in ("u1", "u2"):
            conn.execute(
                "INSERT INTO user_accounts (id, created_at, last_seen_at) VALUES (?, ?, ?)",
                (uid, FIXED_TIME.isoformat(), FIXED_TIME.isoformat()),
            )
            # A real chaster_connections row is required -- connection_id
            # is a real foreign key into it (migration 023).
            conn.execute(
                """INSERT INTO chaster_connections
                    (user_id, chaster_account_id, chaster_username, encrypted_access_token,
                     encrypted_refresh_token, encryption_key_version, access_token_expires_at,
                     refresh_token_expires_at, granted_scopes_json, connection_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uid, f"chaster-acc-{uid}", f"wearer-{uid}", "enc-at", "enc-rt", "v1",
                    FIXED_TIME.isoformat(), FIXED_TIME.isoformat(), "[]", "active",
                    FIXED_TIME.isoformat(), FIXED_TIME.isoformat(),
                ),
            )
        conn.commit()
    return c


@pytest.fixture
def observations(core: CoreDatabase, tmp_path: Path) -> ChasterProviderObservations:
    return ChasterProviderObservations(tmp_path / "test.db", core=core)


@pytest.fixture
def recorder(core: CoreDatabase, tmp_path: Path) -> ChasterProviderObservationRecorder:
    return ChasterProviderObservationRecorder(tmp_path / "test.db", core=core)


def _record(recorder: ChasterProviderObservationRecorder, *, connection_id: str = "u1", **overrides) -> ChasterProviderObservation:
    kwargs = dict(
        connection_id=connection_id, provider="chaster", chaster_lock_id="lock1",
        status=ChasterProviderStatus.LOCKED, fetched_at=FIXED_TIME, now=FIXED_TIME,
    )
    kwargs.update(overrides)
    return recorder.record(**kwargs)


class TestChasterProviderStatusEnum:
    def test_exactly_three_members(self) -> None:
        assert {m.value for m in ChasterProviderStatus} == {"locked", "unlocked", "deserted"}

    def test_verified_can_never_be_constructed(self) -> None:
        with pytest.raises(ValueError):
            ChasterProviderStatus("verified")
        with pytest.raises(ValueError):
            ChasterProviderStatus("VERIFIED")

    def test_matches_the_confirmed_official_lockstatusenum_values(self) -> None:
        """Directly matches the confirmed, official LockStatusEnum
        (chaster_integration_technical_design.md Section 5/11) --
        never inferred or guessed."""
        assert ChasterProviderStatus.LOCKED.value == "locked"
        assert ChasterProviderStatus.UNLOCKED.value == "unlocked"
        assert ChasterProviderStatus.DESERTED.value == "deserted"


class TestGetLatestNoObservationYet:
    def test_returns_none_when_no_observation_exists(self, observations: ChasterProviderObservations) -> None:
        assert observations.get_latest("u1") is None

    def test_none_is_never_a_fabricated_row(self, observations: ChasterProviderObservations, recorder: ChasterProviderObservationRecorder) -> None:
        """Confirms u1 having no observations doesn't somehow affect
        u2, and vice versa -- no shared/global fallback state."""
        _record(recorder, connection_id="u2")
        assert observations.get_latest("u1") is None
        assert observations.get_latest("u2") is not None

    def test_empty_connection_id_is_rejected(self, observations: ChasterProviderObservations) -> None:
        with pytest.raises(ValueError):
            observations.get_latest("")


class TestRecordAndReadBack:
    def test_recorded_observation_round_trips_exactly(self, recorder: ChasterProviderObservationRecorder, observations: ChasterProviderObservations) -> None:
        recorded = _record(recorder, chaster_lock_id="lock-xyz", status=ChasterProviderStatus.LOCKED)
        fetched = observations.get_latest("u1")
        assert fetched == recorded

    def test_chaster_lock_id_can_be_none_zero_active_locks(self, recorder: ChasterProviderObservationRecorder, observations: ChasterProviderObservations) -> None:
        """A real, valid outcome -- 'zero active locks found' -- not
        an error and not 'not yet fetched'."""
        _record(recorder, chaster_lock_id=None, status=ChasterProviderStatus.UNLOCKED)
        fetched = observations.get_latest("u1")
        assert fetched.chaster_lock_id is None
        assert fetched.status is ChasterProviderStatus.UNLOCKED

    def test_deserted_status_round_trips(self, recorder: ChasterProviderObservationRecorder, observations: ChasterProviderObservations) -> None:
        _record(recorder, status=ChasterProviderStatus.DESERTED)
        assert observations.get_latest("u1").status is ChasterProviderStatus.DESERTED

    def test_empty_connection_id_rejected_before_any_write(self, recorder: ChasterProviderObservationRecorder) -> None:
        with pytest.raises(ValueError):
            _record(recorder, connection_id="")

    def test_empty_provider_rejected(self, recorder: ChasterProviderObservationRecorder) -> None:
        with pytest.raises(ValueError):
            _record(recorder, provider="")


class TestGetLatestOrdering:
    def test_returns_the_most_recently_fetched_observation(self, recorder: ChasterProviderObservationRecorder, observations: ChasterProviderObservations) -> None:
        first = _record(recorder, fetched_at=FIXED_TIME, status=ChasterProviderStatus.LOCKED)
        second = _record(recorder, fetched_at=FIXED_TIME + timedelta(minutes=10), status=ChasterProviderStatus.UNLOCKED)
        latest = observations.get_latest("u1")
        assert latest.id == second.id
        assert latest.status is ChasterProviderStatus.UNLOCKED

    def test_ordering_is_by_fetched_at_not_insertion_order(self, recorder: ChasterProviderObservationRecorder, observations: ChasterProviderObservations) -> None:
        """Inserts an OLDER fetched_at AFTER a newer one -- get_latest
        must still return the one with the later fetched_at, proving
        it orders by that column, not merely by insert sequence."""
        newer = _record(recorder, fetched_at=FIXED_TIME + timedelta(hours=1))
        _record(recorder, fetched_at=FIXED_TIME)  # inserted second, but chronologically earlier
        assert observations.get_latest("u1").id == newer.id

    def test_identical_fetched_at_breaks_tie_by_most_recent_insert(self, recorder: ChasterProviderObservationRecorder, observations: ChasterProviderObservations) -> None:
        _record(recorder, fetched_at=FIXED_TIME, status=ChasterProviderStatus.LOCKED)
        second = _record(recorder, fetched_at=FIXED_TIME, status=ChasterProviderStatus.UNLOCKED)
        assert observations.get_latest("u1").id == second.id

    def test_different_connections_are_fully_independent(self, recorder: ChasterProviderObservationRecorder, observations: ChasterProviderObservations) -> None:
        u1_obs = _record(recorder, connection_id="u1", status=ChasterProviderStatus.LOCKED)
        u2_obs = _record(recorder, connection_id="u2", status=ChasterProviderStatus.UNLOCKED)
        assert observations.get_latest("u1").id == u1_obs.id
        assert observations.get_latest("u2").id == u2_obs.id


class TestAppendOnly:
    def test_recording_never_deletes_or_modifies_earlier_rows(self, recorder: ChasterProviderObservationRecorder, core: CoreDatabase) -> None:
        _record(recorder, fetched_at=FIXED_TIME)
        _record(recorder, fetched_at=FIXED_TIME + timedelta(minutes=5))
        _record(recorder, fetched_at=FIXED_TIME + timedelta(minutes=10))
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as c FROM chaster_provider_observations WHERE connection_id = ?", ("u1",)).fetchone()["c"]
        assert count == 3

    def test_real_concurrent_writes_never_lose_a_row(self, recorder: ChasterProviderObservationRecorder, core: CoreDatabase) -> None:
        """Proven with real threads, the same technique this project's
        own OAuth state repository tests already established for a
        different table's concurrency guarantee."""
        errors: list[Exception] = []

        def write_many() -> None:
            try:
                for i in range(15):
                    _record(recorder, chaster_lock_id=f"lock-{i}", fetched_at=FIXED_TIME)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=write_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as c FROM chaster_provider_observations WHERE connection_id = ?", ("u1",)).fetchone()["c"]
        assert count == 60


class TestStructuralSeparationFromLockState:
    def test_no_foreign_key_between_provider_observations_and_lock_reports(self, core: CoreDatabase) -> None:
        """Confirms migration 023's own stated design: structurally
        separate from lock_state's lock_reports, never merged."""
        with core.raw_connection() as conn:
            fks = conn.execute("PRAGMA foreign_key_list(chaster_provider_observations)").fetchall()
        referenced_tables = {row["table"] for row in fks}
        assert "lock_reports" not in referenced_tables
        assert referenced_tables == {"chaster_connections"}

    def test_provider_observation_recorder_has_no_consent_id_concept(self) -> None:
        """Unlike lock_state's governed write (which requires
        reported_via_consent_id), a provider observation is a
        machine-fetched fact, not a user-supplied one -- confirmed by
        the recorder's own signature never mentioning consent."""
        import inspect
        params = inspect.signature(ChasterProviderObservationRecorder.record).parameters
        assert "reported_via_consent_id" not in params
        assert "consent" not in " ".join(params).lower()
