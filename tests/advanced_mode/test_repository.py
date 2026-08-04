"""
tests/advanced_mode/test_repository.py

Behavioral tests use AdvancedModeAdministration (the governance write
API) to set up state -- never a raw SQL INSERT, except where explicitly
testing a database constraint or migration itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from advanced_mode.models import (
    ActiveModeTransitionExistsError,
    MinimumTimeInAdvancedNotMetError,
    ModeTransitionInterruptedByPenaltyWindowError,
    ModeTransitionNotConfirmableError,
    ModeTransitionSourceModeMismatchError,
    ModeTransitionStatus,
    NoActiveModeTransitionError,
    OperatingMode,
)
from advanced_mode.repository import AdvancedMode, AdvancedModeAdministration
from infrastructure.database import Database as CoreDatabase
from penalty_engine.repository import PenaltyEngine
from trust_manager.models import (
    BreachDirectness, ConfirmationSource, CooperationAssessment, EvidenceConfidenceLevel, ImpactLevel,
    IncidentConfirmation, IncidentEvidence, IntentAssessment, RepetitionEvidence,
)
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


@pytest.fixture
def tm(core: CoreDatabase) -> TrustManager:
    manager = TrustManager(core.db_path, core=core)
    manager.create_domain(
        domain_id="chastity", display_name="Chastity", description="...",
        created_via_consent_id="c1", now=FIXED_TIME,
    )
    return manager


@pytest.fixture
def pe(core: CoreDatabase) -> PenaltyEngine:
    return PenaltyEngine(core.db_path, core=core)


@pytest.fixture
def mode(core: CoreDatabase) -> AdvancedMode:
    return AdvancedMode(core.db_path, core=core)


@pytest.fixture
def admin(core: CoreDatabase) -> AdvancedModeAdministration:
    return AdvancedModeAdministration(core.db_path, core=core)


def _start_active_penalty_window(tm: TrustManager, pe: PenaltyEngine, *, now: datetime = FIXED_TIME):
    """Confirms a MINOR incident (HIGH cooperation -> Extension-ineligible,
    keeping timing focused on this module's own concerns) and starts
    the resulting Penalty Window -- the same helper shape
    tests/penalty_engine/test_repository.py's own _confirm_incident uses."""
    incident = tm.register_incident_report(
        rule_group_id="rg1", trust_domain="chastity", description="late",
        evidence=IncidentEvidence(
            actual_or_potential_impact=ImpactLevel.LOW, intentionality=IntentAssessment.UNCLEAR,
            rule_breach_directness=BreachDirectness.INDIRECT, evidence_confidence=EvidenceConfidenceLevel.HIGH,
            repetition=RepetitionEvidence(same_rule_confirmed_count=0, evaluation_window_days=30),
        ),
        now=now,
    )
    tm.confirm_incident(
        incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
        source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted", now=now,
        cooperation=CooperationAssessment(self_disclosed=True, active_cooperation_in_resolution=True),
    )
    return pe.start_window_if_eligible(tm, now=now)


class TestBootstrap:
    def test_new_installation_starts_in_standard(self, mode: AdvancedMode) -> None:
        state = mode.get_current_mode()
        assert state.current_mode == OperatingMode.STANDARD

    def test_no_active_request_on_a_fresh_installation(self, mode: AdvancedMode) -> None:
        assert mode.get_active_request() is None


class TestRequestTransition:
    def test_creates_a_waiting_request_when_no_penalty_window(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine,
    ) -> None:
        request = admin.request_transition(
            pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME,
        )
        assert request.status == ModeTransitionStatus.WAITING
        assert request.source_mode == OperatingMode.STANDARD
        assert request.target_mode == OperatingMode.ADVANCED
        assert request.wait_started_at == FIXED_TIME
        assert request.confirmable_at == FIXED_TIME + timedelta(hours=24)

    def test_creates_a_blocked_request_when_penalty_window_active(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        _start_active_penalty_window(tm, pe)
        request = admin.request_transition(
            pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME,
        )
        assert request.status == ModeTransitionStatus.BLOCKED_BY_PENALTY_WINDOW
        assert request.wait_started_at is None
        assert request.confirmable_at is None

    def test_second_request_while_one_is_active_raises(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine,
    ) -> None:
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        with pytest.raises(ActiveModeTransitionExistsError):
            admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-2", now=FIXED_TIME)

    def test_target_mode_same_as_current_is_rejected(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine,
    ) -> None:
        with pytest.raises(ValueError, match="must differ from the current mode"):
            admin.request_transition(pe, target_mode=OperatingMode.STANDARD, requested_via_consent_id="req-1", now=FIXED_TIME)

    def test_empty_consent_id_raises(self, admin: AdvancedModeAdministration, pe: PenaltyEngine) -> None:
        with pytest.raises(ValueError, match="non-empty consent"):
            admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="", now=FIXED_TIME)


class TestMinimumTimeInAdvanced:
    def _move_to_advanced(self, admin: AdvancedModeAdministration, pe: PenaltyEngine, *, now: datetime) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=now)
        confirmable_time = now + timedelta(hours=24)
        admin.advance_transition_state(pe, now=confirmable_time)
        admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=confirmable_time)

    def test_advanced_to_standard_before_30_days_is_rejected(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine,
    ) -> None:
        self._move_to_advanced(admin, pe, now=FIXED_TIME)
        just_before = FIXED_TIME + timedelta(hours=24) + timedelta(days=29, hours=23, minutes=59)
        with pytest.raises(MinimumTimeInAdvancedNotMetError):
            admin.request_transition(pe, target_mode=OperatingMode.STANDARD, requested_via_consent_id="req-2", now=just_before)

    def test_advanced_to_standard_at_exactly_30_days_succeeds(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine,
    ) -> None:
        self._move_to_advanced(admin, pe, now=FIXED_TIME)
        activated_at = FIXED_TIME + timedelta(hours=24)
        exactly_30_days = activated_at + timedelta(days=30)
        request = admin.request_transition(pe, target_mode=OperatingMode.STANDARD, requested_via_consent_id="req-2", now=exactly_30_days)
        assert request.status == ModeTransitionStatus.WAITING

    def test_standard_to_advanced_has_no_minimum(self, admin: AdvancedModeAdministration, pe: PenaltyEngine) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        assert request.status == ModeTransitionStatus.WAITING


class TestCancelRequest:
    def test_cancels_a_waiting_request(self, admin: AdvancedModeAdministration, pe: PenaltyEngine) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        cancelled = admin.cancel_request(request.id, now=FIXED_TIME + timedelta(hours=1))
        assert cancelled.status == ModeTransitionStatus.CANCELLED
        assert cancelled.cancelled_at == FIXED_TIME + timedelta(hours=1)
        assert cancelled.resolved_at == FIXED_TIME + timedelta(hours=1)

    def test_cancels_a_blocked_request(self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager) -> None:
        _start_active_penalty_window(tm, pe)
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        cancelled = admin.cancel_request(request.id, now=FIXED_TIME + timedelta(hours=1))
        assert cancelled.status == ModeTransitionStatus.CANCELLED

    def test_cancelling_an_already_cancelled_request_raises(self, admin: AdvancedModeAdministration, pe: PenaltyEngine) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.cancel_request(request.id, now=FIXED_TIME + timedelta(hours=1))
        with pytest.raises(NoActiveModeTransitionError):
            admin.cancel_request(request.id, now=FIXED_TIME + timedelta(hours=2))

    def test_after_cancel_a_new_request_can_be_made(self, admin: AdvancedModeAdministration, pe: PenaltyEngine) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.cancel_request(request.id, now=FIXED_TIME + timedelta(hours=1))
        new_request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-2", now=FIXED_TIME + timedelta(hours=2))
        assert new_request.status == ModeTransitionStatus.WAITING


class TestAdvanceTransitionState:
    def test_blocked_becomes_waiting_once_penalty_window_ends(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        _start_active_penalty_window(tm, pe)  # created at FIXED_TIME, 24h default duration
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        assert request.status == ModeTransitionStatus.BLOCKED_BY_PENALTY_WINDOW

        pe.ensure_current_state(FIXED_TIME + timedelta(hours=25))  # completes the window
        advanced = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=26))
        assert advanced.status == ModeTransitionStatus.WAITING
        assert advanced.wait_started_at == FIXED_TIME + timedelta(hours=26)
        assert advanced.confirmable_at == FIXED_TIME + timedelta(hours=26) + timedelta(hours=24)

    def test_waiting_becomes_paused_when_new_penalty_window_starts(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        _start_active_penalty_window(tm, pe, now=FIXED_TIME + timedelta(hours=5))

        paused = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=6))
        assert paused.status == ModeTransitionStatus.PAUSED_BY_PENALTY_WINDOW
        assert paused.wait_interrupted_at == FIXED_TIME + timedelta(hours=6)
        assert paused.confirmable_at is None

    def test_paused_becomes_waiting_with_a_fresh_full_24h_after_pw_ends(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        _start_active_penalty_window(tm, pe, now=FIXED_TIME + timedelta(hours=5))
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=6))  # -> PAUSED

        pe.ensure_current_state(FIXED_TIME + timedelta(hours=30))  # window created at +5h, 24h default duration -> completes by +29h
        restarted = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=31))
        assert restarted.status == ModeTransitionStatus.WAITING
        assert restarted.wait_started_at == FIXED_TIME + timedelta(hours=31)  # brand new, not the original
        assert restarted.confirmable_at == FIXED_TIME + timedelta(hours=31) + timedelta(hours=24)

    def test_waiting_does_not_skip_straight_to_awaiting_confirmation_when_restarted_in_the_same_call(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        """The explicit requirement: PAUSED -> WAITING must not, in the
        SAME call, also become AWAITING_CONFIRMATION -- the freshly
        restarted 24h wait has obviously not elapsed."""
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        _start_active_penalty_window(tm, pe, now=FIXED_TIME + timedelta(hours=5))
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=6))

        pe.ensure_current_state(FIXED_TIME + timedelta(hours=30))
        # Even calling this at a time that is 24h+ past the ORIGINAL wait_started_at
        result = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(days=5))
        assert result.status == ModeTransitionStatus.WAITING  # not AWAITING_CONFIRMATION

    def test_waiting_becomes_awaiting_confirmation_once_24h_elapsed_with_no_pw(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine,
    ) -> None:
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        result = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        assert result.status == ModeTransitionStatus.AWAITING_CONFIRMATION

    def test_waiting_not_yet_advanced_before_24h(self, admin: AdvancedModeAdministration, pe: PenaltyEngine) -> None:
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        result = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=23))
        assert result.status == ModeTransitionStatus.WAITING

    def test_awaiting_confirmation_becomes_paused_when_new_pw_starts(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        """The decision that closed the original gap: a new PW during
        AWAITING_CONFIRMATION invalidates the prior wait exactly like
        during WAITING."""
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))  # -> AWAITING_CONFIRMATION

        _start_active_penalty_window(tm, pe, now=FIXED_TIME + timedelta(hours=25))
        result = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=26))
        assert result.status == ModeTransitionStatus.PAUSED_BY_PENALTY_WINDOW
        assert result.confirmable_at is None

    def test_returns_none_when_no_active_request(self, admin: AdvancedModeAdministration, pe: PenaltyEngine) -> None:
        assert admin.advance_transition_state(pe, now=FIXED_TIME) is None

    def test_idempotent_on_a_stable_waiting_state_no_update_issued(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, core: CoreDatabase,
    ) -> None:
        """Explicit requirement: repeated calls in a stable state issue
        no UPDATE and change no timestamp."""
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        first = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=1))
        second = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=2))
        assert first == second  # wait_started_at/confirmable_at unchanged despite different `now`
        assert first.wait_started_at == FIXED_TIME
        assert first.confirmable_at == FIXED_TIME + timedelta(hours=24)

    def test_idempotent_on_a_stable_awaiting_confirmation_state(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine,
    ) -> None:
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        first = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        second = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=48))
        assert first == second
        assert first.status == ModeTransitionStatus.AWAITING_CONFIRMATION


class TestConfirmTransition:
    def test_successful_confirmation_changes_operating_mode(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, mode: AdvancedMode,
    ) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        confirmed_at = FIXED_TIME + timedelta(hours=25)
        result = admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=confirmed_at)

        assert result.status == ModeTransitionStatus.COMPLETED
        assert result.confirmed_at == confirmed_at
        assert result.confirmed_via_consent_id == "conf-1"
        assert result.resolved_at == confirmed_at

        state = mode.get_current_mode()
        assert state.current_mode == OperatingMode.ADVANCED
        assert state.mode_activated_at == confirmed_at

    def test_confirming_when_not_awaiting_confirmation_raises(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine,
    ) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        with pytest.raises(ModeTransitionNotConfirmableError):
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=1))

    def test_empty_confirmation_consent_id_raises(self, admin: AdvancedModeAdministration, pe: PenaltyEngine) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        with pytest.raises(ValueError, match="non-empty consent"):
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="", now=FIXED_TIME + timedelta(hours=25))

    def test_interrupted_by_penalty_window_raises_and_never_leaves_confirmed_fields_set(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager, mode: AdvancedMode,
    ) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))  # -> AWAITING_CONFIRMATION
        _start_active_penalty_window(tm, pe, now=FIXED_TIME + timedelta(hours=25))

        with pytest.raises(ModeTransitionInterruptedByPenaltyWindowError) as excinfo:
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=26))

        interrupted_request = excinfo.value.request
        assert interrupted_request.status == ModeTransitionStatus.PAUSED_BY_PENALTY_WINDOW
        assert interrupted_request.confirmed_at is None
        assert interrupted_request.confirmed_via_consent_id is None
        assert interrupted_request.confirmable_at is None

        # OperatingMode must not have changed.
        assert mode.get_current_mode().current_mode == OperatingMode.STANDARD

    def test_interrupted_state_is_verified_via_a_fresh_database_connection_not_the_exceptions_own_object(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager, core: CoreDatabase,
    ) -> None:
        """Explicit requirement: checking the exception's attached
        object is not enough -- must reopen a fresh connection and
        confirm the write was truly committed to disk, not merely held
        in the Python object that happened to be attached to the
        exception."""
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        _start_active_penalty_window(tm, pe, now=FIXED_TIME + timedelta(hours=25))

        with pytest.raises(ModeTransitionInterruptedByPenaltyWindowError):
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=26))

        fresh_core = CoreDatabase(core.db_path)  # a genuinely new connection
        with fresh_core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM mode_transition_requests WHERE id = ?", (request.id,))
            state_row = tx.fetch_one("SELECT * FROM operating_mode_state WHERE id = 1")
        assert row["status"] == "paused_by_penalty_window"
        assert row["confirmed_at"] is None
        assert row["confirmed_via_consent_id"] is None
        assert state_row["current_mode"] == "standard"  # never changed

    def test_confirming_a_nonexistent_request_raises(self, admin: AdvancedModeAdministration, pe: PenaltyEngine) -> None:
        with pytest.raises(NoActiveModeTransitionError):
            admin.confirm_transition("does-not-exist", pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME)

    def test_after_interruption_a_fresh_full_24h_wait_and_new_consent_are_required(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        _start_active_penalty_window(tm, pe, now=FIXED_TIME + timedelta(hours=25))  # created at +25h, 24h default duration
        with pytest.raises(ModeTransitionInterruptedByPenaltyWindowError):
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=26))

        pe.ensure_current_state(FIXED_TIME + timedelta(hours=50))  # completes the window (+25h created, +24h duration -> done by +49h)
        restarted = admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=51))
        assert restarted.status == ModeTransitionStatus.WAITING
        assert restarted.confirmable_at == FIXED_TIME + timedelta(hours=51) + timedelta(hours=24)

        # The full, fresh 24h must elapse again -- confirming immediately still fails.
        with pytest.raises(ModeTransitionNotConfirmableError):
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-2", now=FIXED_TIME + timedelta(hours=52))


class TestMode1DatabaseConstraint:
    def test_partial_unique_index_rejects_a_second_non_terminal_row_directly(self, core: CoreDatabase) -> None:
        """Defense in depth, verified directly against the schema
        itself, independent of AdvancedModeAdministration's own
        application-level check."""
        import sqlite3

        with core.transaction() as tx:
            tx.execute(
                "INSERT INTO mode_transition_requests (id, source_mode, target_mode, status, requested_at, requested_via_consent_id) "
                "VALUES ('r1', 'standard', 'advanced', 'waiting', ?, 'c1')",
                (FIXED_TIME.isoformat(),),
            )
        with pytest.raises(sqlite3.IntegrityError):
            with core.transaction() as tx:
                tx.execute(
                    "INSERT INTO mode_transition_requests (id, source_mode, target_mode, status, requested_at, requested_via_consent_id) "
                    "VALUES ('r2', 'standard', 'advanced', 'blocked_by_penalty_window', ?, 'c2')",
                    (FIXED_TIME.isoformat(),),
                )

    def test_two_terminal_rows_are_allowed(self, core: CoreDatabase) -> None:
        with core.transaction() as tx:
            tx.execute(
                "INSERT INTO mode_transition_requests (id, source_mode, target_mode, status, requested_at, requested_via_consent_id, cancelled_at, resolved_at) "
                "VALUES ('r1', 'standard', 'advanced', 'cancelled', ?, 'c1', ?, ?)",
                (FIXED_TIME.isoformat(), FIXED_TIME.isoformat(), FIXED_TIME.isoformat()),
            )
            tx.execute(
                "INSERT INTO mode_transition_requests (id, source_mode, target_mode, status, requested_at, requested_via_consent_id, cancelled_at, resolved_at) "
                "VALUES ('r2', 'standard', 'advanced', 'cancelled', ?, 'c2', ?, ?)",
                (FIXED_TIME.isoformat(), FIXED_TIME.isoformat(), FIXED_TIME.isoformat()),
            )  # must not raise


class TestConcurrentRequestTransition:
    """A real multi-threaded verification, mirroring task_catalog's own
    concurrency test and the onboarding conditional-update test."""

    def test_two_concurrent_requests_never_both_succeed(self, core: CoreDatabase) -> None:
        import threading

        results: list = []
        errors: list = []
        barrier = threading.Barrier(2)

        def race() -> None:
            try:
                thread_core = CoreDatabase(core.db_path)
                thread_admin = AdvancedModeAdministration(core.db_path, core=thread_core)
                thread_pe = PenaltyEngine(core.db_path, core=thread_core)
                barrier.wait(timeout=5)
                result = thread_admin.request_transition(
                    thread_pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="race-consent", now=FIXED_TIME,
                )
                results.append(result)
            except ActiveModeTransitionExistsError as exc:
                errors.append(exc)
            except Exception as exc:  # pragma: no cover -- unexpected failure path only
                errors.append(exc)

        t1 = threading.Thread(target=race)
        t2 = threading.Thread(target=race)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert len(results) == 1, f"expected exactly one success, got {len(results)} (errors: {errors})"
        assert len(errors) == 1
        assert isinstance(errors[0], ActiveModeTransitionExistsError)

        with core.transaction() as tx:
            count = tx.fetch_one(
                "SELECT COUNT(*) as n FROM mode_transition_requests WHERE status NOT IN ('cancelled', 'completed')",
            )["n"]
        assert count == 1


class TestConfirmTransitionAtomicity:
    def test_a_failure_writing_operating_mode_state_rolls_back_the_request_update_too(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, core: CoreDatabase, monkeypatch,
    ) -> None:
        from infrastructure.database import Transaction

        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))

        original_execute = Transaction.execute

        def failing_execute(self, sql, params=()):
            if "operating_mode_state" in sql and "UPDATE" in sql:
                raise RuntimeError("simulated failure writing operating_mode_state")
            return original_execute(self, sql, params)

        monkeypatch.setattr(Transaction, "execute", failing_execute)
        with pytest.raises(RuntimeError, match="simulated failure"):
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=25))
        monkeypatch.undo()

        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM mode_transition_requests WHERE id = ?", (request.id,))
            state_row = tx.fetch_one("SELECT * FROM operating_mode_state WHERE id = 1")
        assert row["status"] == "awaiting_confirmation"  # rolled back, NOT completed
        assert row["confirmed_at"] is None
        assert state_row["current_mode"] == "standard"  # rolled back, never changed


class TestAdvancedModeHasNoWriteCapability:
    def test_no_request_transition_method(self, mode: AdvancedMode) -> None:
        assert not hasattr(mode, "request_transition")

    def test_no_confirm_or_cancel_method(self, mode: AdvancedMode) -> None:
        assert not hasattr(mode, "confirm_transition")
        assert not hasattr(mode, "cancel_request")

    def test_no_advance_transition_state_method(self, mode: AdvancedMode) -> None:
        """The core point of this whole review round: reading must
        never have this hidden as a side effect."""
        assert not hasattr(mode, "advance_transition_state")

    def test_only_the_two_documented_public_methods_exist(self, mode: AdvancedMode) -> None:
        public_methods = {name for name in dir(mode) if not name.startswith("_")}
        assert public_methods == {"get_current_mode", "get_active_request", "db_path"}


class TestSourceModeMismatchInvalidatesRequest:
    """
    Regression tests for the fixed behavior -- this class previously
    held a REPRODUCTION test proving the gap existed
    (TestReproduction_SourceModeNotRecheckedAtConfirmation); now that
    the check is implemented, these tests prove the required behavior
    instead.
    """

    def test_mismatch_invalidates_the_request_and_does_not_change_operating_mode(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, core: CoreDatabase, mode: AdvancedMode,
    ) -> None:
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        assert request.source_mode == OperatingMode.STANDARD

        with core.transaction() as tx:
            tx.execute(
                "UPDATE operating_mode_state SET current_mode = 'advanced', mode_activated_at = ? WHERE id = 1",
                (FIXED_TIME.isoformat(),),
            )

        with pytest.raises(ModeTransitionSourceModeMismatchError) as excinfo:
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=25))

        invalidated = excinfo.value.request
        assert invalidated.status == ModeTransitionStatus.INVALIDATED
        assert excinfo.value.expected_source_mode == OperatingMode.STANDARD
        assert excinfo.value.actual_current_mode == OperatingMode.ADVANCED
        # OperatingMode was already 'advanced' (from the simulated external
        # write) and must remain exactly that -- confirm_transition() must
        # not have touched it further (e.g. re-writing mode_activated_at).
        assert mode.get_current_mode().current_mode == OperatingMode.ADVANCED

    def test_invalidated_state_is_verified_via_a_fresh_database_connection(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, core: CoreDatabase,
    ) -> None:
        """Point 2 of the required tests: after catching the exception,
        a genuinely new connection confirms INVALIDATED was truly
        committed, not merely held on the exception's own attached object."""
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        with core.transaction() as tx:
            tx.execute(
                "UPDATE operating_mode_state SET current_mode = 'advanced', mode_activated_at = ? WHERE id = 1",
                (FIXED_TIME.isoformat(),),
            )

        with pytest.raises(ModeTransitionSourceModeMismatchError):
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=25))

        fresh_core = CoreDatabase(core.db_path)
        with fresh_core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM mode_transition_requests WHERE id = ?", (request.id,))
        assert row["status"] == "invalidated"

    def test_confirmation_consent_remains_null(self, admin: AdvancedModeAdministration, pe: PenaltyEngine, core: CoreDatabase) -> None:
        """Point 3 of the required tests."""
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        with core.transaction() as tx:
            tx.execute(
                "UPDATE operating_mode_state SET current_mode = 'advanced', mode_activated_at = ? WHERE id = 1",
                (FIXED_TIME.isoformat(),),
            )
        with pytest.raises(ModeTransitionSourceModeMismatchError) as excinfo:
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=25))
        assert excinfo.value.request.confirmed_at is None
        assert excinfo.value.request.confirmed_via_consent_id is None
        assert excinfo.value.request.cancelled_at is None

    def test_resolved_at_and_invalidated_at_are_populated(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, core: CoreDatabase,
    ) -> None:
        """Point 4 of the required tests."""
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        with core.transaction() as tx:
            tx.execute(
                "UPDATE operating_mode_state SET current_mode = 'advanced', mode_activated_at = ? WHERE id = 1",
                (FIXED_TIME.isoformat(),),
            )
        confirm_time = FIXED_TIME + timedelta(hours=25)
        with pytest.raises(ModeTransitionSourceModeMismatchError) as excinfo:
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=confirm_time)
        assert excinfo.value.request.invalidated_at == confirm_time
        assert excinfo.value.request.resolved_at == confirm_time

    def test_a_new_request_can_be_made_after_invalidation(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, core: CoreDatabase,
    ) -> None:
        """Point 5 of the required tests -- INVALIDATED must be treated
        as terminal by the partial unique index too (migration 018)."""
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        with core.transaction() as tx:
            # mode_activated_at set 31 days in the past so the 30-day
            # minimum (unrelated to this test's own concern) doesn't
            # block the follow-up request below.
            tx.execute(
                "UPDATE operating_mode_state SET current_mode = 'advanced', mode_activated_at = ? WHERE id = 1",
                ((FIXED_TIME - timedelta(days=31)).isoformat(),),
            )
        with pytest.raises(ModeTransitionSourceModeMismatchError):
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=25))

        # OperatingMode is now 'advanced' -- request the (only valid) opposite direction.
        new_request = admin.request_transition(
            pe, target_mode=OperatingMode.STANDARD, requested_via_consent_id="req-2", now=FIXED_TIME + timedelta(hours=26),
        )
        assert new_request.status in (ModeTransitionStatus.WAITING, ModeTransitionStatus.BLOCKED_BY_PENALTY_WINDOW)

    def test_a_failure_writing_the_invalidated_row_leaves_no_partial_state(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, core: CoreDatabase, monkeypatch,
    ) -> None:
        """Point 6 of the required tests: rollback mid-invalidation
        leaves no partial state -- mirrors
        TestConfirmTransitionAtomicity's own failure-injection pattern."""
        from infrastructure.database import Transaction

        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        with core.transaction() as tx:
            tx.execute(
                "UPDATE operating_mode_state SET current_mode = 'advanced', mode_activated_at = ? WHERE id = 1",
                (FIXED_TIME.isoformat(),),
            )

        original_execute = Transaction.execute

        def failing_execute(self, sql, params=()):
            if "mode_transition_requests" in sql and "UPDATE" in sql:
                raise RuntimeError("simulated failure writing the INVALIDATED row")
            return original_execute(self, sql, params)

        monkeypatch.setattr(Transaction, "execute", failing_execute)
        with pytest.raises(RuntimeError, match="simulated failure"):
            admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=25))
        monkeypatch.undo()

        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM mode_transition_requests WHERE id = ?", (request.id,))
        assert row["status"] == "awaiting_confirmation"  # rolled back, NOT invalidated
        assert row["invalidated_at"] is None

    def test_matching_source_mode_still_confirms_normally(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, mode: AdvancedMode,
    ) -> None:
        """Point 7 of the required tests -- the ordinary, matching-source_mode
        path (already covered by TestConfirmTransition::test_successful_confirmation_changes_operating_mode)
        still works after this change; re-asserted here for this
        specific regression class's own completeness."""
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        admin.advance_transition_state(pe, now=FIXED_TIME + timedelta(hours=24))
        result = admin.confirm_transition(request.id, pe, confirmed_via_consent_id="conf-1", now=FIXED_TIME + timedelta(hours=25))
        assert result.status == ModeTransitionStatus.COMPLETED
        assert mode.get_current_mode().current_mode == OperatingMode.ADVANCED


class TestConservativePenaltyWindowContract:
    """
    Documents the explicit, deliberately conservative contract chosen
    for this iteration (Variant C) -- not a bug, and not something this
    module resolves. `get_active_or_frozen_penalty_window_in_transaction(tx)`
    reads persisted `status` only; it never calls
    `PenaltyEngine.ensure_current_state(now)`, which opens its own
    separate transactions and publishes its own domain events on
    completion, and so cannot safely run nested inside Advanced Mode's
    own transaction (see this module's own README for the full
    reasoning and the project-wide open question this points to).

    Consequence, accepted deliberately: a Penalty Window whose target
    duration has elapsed by wall-clock time, but which nothing has yet
    called `ensure_current_state(now)` for, still reads as
    `active`/`frozen` here -- `advance_transition_state()`/
    `confirm_transition()` may therefore block a mode transition longer
    than the theoretical countdown completion time. This can only ever
    make Advanced Mode MORE conservative (never allows a transition
    during a genuinely active/frozen PW), never less.
    """

    def test_advance_transition_state_conservatively_stays_blocked_until_something_settles_the_window(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        _start_active_penalty_window(tm, pe, now=FIXED_TIME)  # 24h target duration
        request = admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)
        assert request.status == ModeTransitionStatus.BLOCKED_BY_PENALTY_WINDOW

        far_future = FIXED_TIME + timedelta(hours=100)  # 76+ hours past the 24h target
        # Deliberately NOT calling pe.ensure_current_state(far_future) here --
        # this test exists specifically to demonstrate the conservative
        # contract holds without it.
        result = admin.advance_transition_state(pe, now=far_future)

        # The persisted status is what governs -- still BLOCKED, exactly
        # as the documented conservative contract states.
        assert result.status == ModeTransitionStatus.BLOCKED_BY_PENALTY_WINDOW
        stale_pw = pe.get_active_or_frozen_penalty_window()
        assert stale_pw is not None
        assert stale_pw.status.value == "active"

    def test_the_same_call_correctly_advances_once_ensure_current_state_is_called_first(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        """Confirms Advanced Mode itself never settles the window --
        the identical advance_transition_state() call proceeds
        correctly once PenaltyEngine's own owner-side settlement has
        run, via its own separate call path."""
        _start_active_penalty_window(tm, pe, now=FIXED_TIME)
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)

        far_future = FIXED_TIME + timedelta(hours=100)
        pe.ensure_current_state(far_future)  # the hidden obligation, made explicit here
        result = admin.advance_transition_state(pe, now=far_future)
        assert result.status == ModeTransitionStatus.WAITING


class TestGetActiveRequestDoesNotMutate:
    def test_repeated_reads_never_change_the_stored_status(
        self, admin: AdvancedModeAdministration, pe: PenaltyEngine, mode: AdvancedMode, core: CoreDatabase,
    ) -> None:
        """Explicit requirement: a read-only method must never apply
        advance_transition_state()'s own lazy transitions as a hidden
        side effect, even when 24h has clearly elapsed."""
        admin.request_transition(pe, target_mode=OperatingMode.ADVANCED, requested_via_consent_id="req-1", now=FIXED_TIME)

        first_read = mode.get_active_request()
        second_read = mode.get_active_request()  # called well past confirmable_at, but never told `now`

        assert first_read.status == ModeTransitionStatus.WAITING  # still WAITING -- unread time never advanced it
        assert second_read.status == ModeTransitionStatus.WAITING
        assert first_read == second_read
