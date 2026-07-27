"""
tests/penalty_engine/test_repository.py

Integration tests for Penalty Engine Slice 1
(docs/architecture/penalty_window_technical_design.md Sections 2.1-2.6,
3.1-3.3, 4.4, 4.5). Uses a real Trust Manager to produce CONFIRMED
Incidents -- the actual cross-module boundary (I12), not a mock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from infrastructure.database import Database as CoreDatabase
from penalty_engine.models import (
    AuthorizationFreezeState,
    FreezeReason,
    PenaltyWindowNotFound,
    PenaltyWindowStatus,
)
from penalty_engine.repository import PenaltyEngine
from penalty_engine.window import DEFAULT_BASE_DURATION_HOURS
from trust_manager.models import (
    BreachDirectness,
    ConfirmationSource,
    CooperationAssessment,
    EvidenceConfidenceLevel,
    ImpactLevel,
    IncidentConfirmation,
    IncidentEvidence,
    IntentAssessment,
    RepetitionEvidence,
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
    manager = TrustManager("unused", core=core)
    manager.create_domain(
        domain_id="chastity", display_name="Chastity", description="...",
        created_via_consent_id="setup-consent", now=FIXED_TIME,
    )
    return manager


@pytest.fixture
def pe(core: CoreDatabase) -> PenaltyEngine:
    return PenaltyEngine("unused", core=core)


def _confirm_incident(tm: TrustManager, now: datetime = FIXED_TIME, *, cooperation: CooperationAssessment | None = None):
    """
    Default cooperation is HIGH (self_disclosed + active_cooperation_in_resolution)
    so an isolated MINOR incident is INELIGIBLE for Extension
    (extension_technical_design.md ET3) -- keeping these state-machine
    focused tests' completion timing exactly base_duration_hours, not
    incidentally extended. Tests specifically about Extension pass an
    explicit LOW-cooperation CooperationAssessment instead (see
    TestExtensionIntegration below).
    """
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
        cooperation=cooperation or CooperationAssessment(self_disclosed=True, active_cooperation_in_resolution=True),
    )
    return incident


class TestStartWindow:
    def test_starts_when_confirmed_incident_exists(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        assert window is not None
        assert window.status == PenaltyWindowStatus.ACTIVE
        assert window.base_duration_hours == DEFAULT_BASE_DURATION_HOURS

    def test_does_not_start_with_no_confirmed_incidents(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        assert pe.start_window_if_eligible(tm, now=FIXED_TIME) is None

    def test_a_second_confirmed_incident_extends_the_existing_window_rather_than_starting_a_new_one(
        self, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        """
        Consumption is unconditional (philosophy.md 3.8) -- a second
        CONFIRMED Incident while a window is still ACTIVE/FROZEN is
        consumed into the SAME window via should_extend(), never
        silently dropped and never starting a second, concurrent window
        (extension_technical_design.md Section 4).
        """
        _confirm_incident(tm)
        first = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        _confirm_incident(tm, now=FIXED_TIME + timedelta(hours=1))
        second = pe.start_window_if_eligible(tm, now=FIXED_TIME + timedelta(hours=1))
        assert first is not None
        assert second is not None
        assert second.id == first.id  # same window, not a second one

    def test_consumes_the_incident(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        incident = _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        domains = pe.get_penalty_window_relevant_domains(window.id)
        assert domains == frozenset({"chastity"})

    def test_does_not_reconsume_the_same_incident_for_a_later_window(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        """I11: incident_consumption.incident_id is written exactly once."""
        _confirm_incident(tm)
        first = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe._complete_window(first.id, FIXED_TIME + timedelta(hours=100))
        # No NEW confirmed incident since -- must not start a second window
        # from the same, already-consumed one.
        second = pe.start_window_if_eligible(tm, now=FIXED_TIME + timedelta(hours=101))
        assert second is None

    def test_emits_started_event(self, pe: PenaltyEngine, tm: TrustManager, core: CoreDatabase) -> None:
        _confirm_incident(tm)
        pe.start_window_if_eligible(tm, now=FIXED_TIME)
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'penalty_window.started'")
        assert row is not None


class TestFreezeAsSetOfReasons:
    def test_freeze_transitions_to_frozen(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(window.id, FreezeReason.TEMPORARY_WEAR_EXEMPTION, exemption_id="ex-1", now=FIXED_TIME + timedelta(hours=1))
        current = pe.get_active_or_frozen_penalty_window()
        assert current.status == PenaltyWindowStatus.FROZEN

    def test_second_concurrent_reason_does_not_change_status_or_time(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(window.id, FreezeReason.TEMPORARY_WEAR_EXEMPTION, exemption_id="ex-1", now=FIXED_TIME + timedelta(hours=1))
        state_after_first = pe.get_active_or_frozen_penalty_window()
        pe.emergency_freeze(window.id, now=FIXED_TIME + timedelta(hours=2))
        state_after_second = pe.get_active_or_frozen_penalty_window()
        assert state_after_second.status == PenaltyWindowStatus.FROZEN
        assert state_after_second.accumulated_active_hours == state_after_first.accumulated_active_hours

    def test_resume_only_reactivates_once_last_reason_closes(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        """I22/PW-FREEZE-SET."""
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(window.id, FreezeReason.TEMPORARY_WEAR_EXEMPTION, exemption_id="ex-1", now=FIXED_TIME + timedelta(hours=1))
        pe.emergency_freeze(window.id, now=FIXED_TIME + timedelta(hours=2))

        pe.resume(window.id, FreezeReason.TEMPORARY_WEAR_EXEMPTION, now=FIXED_TIME + timedelta(hours=3))
        still_frozen = pe.get_active_or_frozen_penalty_window()
        assert still_frozen.status == PenaltyWindowStatus.FROZEN

        pe.resume(window.id, FreezeReason.EMERGENCY_OVERRIDE, now=FIXED_TIME + timedelta(hours=4))
        now_active = pe.get_active_or_frozen_penalty_window()
        assert now_active.status == PenaltyWindowStatus.ACTIVE

    def test_frozen_time_does_not_advance(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(window.id, FreezeReason.EMERGENCY_OVERRIDE, now=FIXED_TIME + timedelta(hours=2))
        frozen_state = pe.get_active_or_frozen_penalty_window()
        assert frozen_state.accumulated_active_hours == 2.0

        # advance real time by a lot while still frozen
        pe.ensure_current_state(FIXED_TIME + timedelta(days=30))
        still_frozen = pe.get_active_or_frozen_penalty_window()
        assert still_frozen.status == PenaltyWindowStatus.FROZEN
        assert still_frozen.accumulated_active_hours == 2.0

    def test_emergency_freeze_emits_both_events(self, pe: PenaltyEngine, tm: TrustManager, core: CoreDatabase) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.emergency_freeze(window.id, now=FIXED_TIME + timedelta(hours=1))
        with core.transaction() as tx:
            events = {r["event_type"] for r in tx.fetch_all("SELECT event_type FROM domain_events")}
        assert "freeze_periods.opened" in events
        assert "emergency_override.triggered" in events

    def test_freeze_missing_window_raises(self, pe: PenaltyEngine) -> None:
        with pytest.raises(PenaltyWindowNotFound):
            pe.freeze("does-not-exist", FreezeReason.EMERGENCY_OVERRIDE, now=FIXED_TIME)


class TestExpiringFreeze:
    def test_freeze_with_expires_at_closes_automatically(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(
            window.id, FreezeReason.PARTNERED_INTIMACY_AUTHORIZATION,
            authorization_decision_id="auth-1", expires_at=FIXED_TIME + timedelta(hours=2),
            now=FIXED_TIME + timedelta(hours=1),
        )
        # past expiry, but no one has explicitly resumed
        pe.ensure_current_state(FIXED_TIME + timedelta(hours=5))
        state = pe.get_authorization_freeze_state("auth-1")
        assert state == AuthorizationFreezeState.EXPIRED

    def test_expired_freeze_reactivates_window_if_it_was_the_last_reason(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(
            window.id, FreezeReason.PARTNERED_INTIMACY_AUTHORIZATION,
            authorization_decision_id="auth-1", expires_at=FIXED_TIME + timedelta(hours=2),
            now=FIXED_TIME + timedelta(hours=1),
        )
        result = pe.ensure_current_state(FIXED_TIME + timedelta(hours=5))
        assert result is not None
        assert result.status == PenaltyWindowStatus.ACTIVE

    def test_startup_reconciliation_counts_wall_clock_time_for_expiry(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        """4.5: expiry is counted by real time, regardless of whether the
        process was 'running' -- ensure_current_state() simulates this by
        simply being called much later, exactly as it would be at startup
        after an outage."""
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(
            window.id, FreezeReason.PARTNERED_INTIMACY_AUTHORIZATION,
            authorization_decision_id="auth-1", expires_at=FIXED_TIME + timedelta(minutes=30),
            now=FIXED_TIME,
        )
        # simulate a long outage, then the very first call after restart
        pe.ensure_current_state(FIXED_TIME + timedelta(days=1))
        assert pe.get_authorization_freeze_state("auth-1") == AuthorizationFreezeState.EXPIRED


class TestExtensionIntegration:
    """extension_technical_design.md Section 4 wired into the real
    incident-consumption flow, not just tested as pure functions."""

    def test_isolated_minor_with_low_cooperation_extends_the_window_it_starts(
        self, pe: PenaltyEngine, tm: TrustManager,
    ) -> None:
        """ET4 wired end-to-end: even the FIRST Incident that starts a
        window goes through should_extend() -- consumption and
        Extension are the same unified path (Section 4)."""
        _confirm_incident(tm, cooperation=CooperationAssessment(self_disclosed=False, active_cooperation_in_resolution=False))
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        assert window.extensions_hours > 0.0

    def test_isolated_minor_high_cooperation_does_not_extend(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        """ET3 wired end-to-end."""
        _confirm_incident(tm)  # default high cooperation
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        assert window.extensions_hours == 0.0

    def test_second_low_severity_incident_extends_the_active_window(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)  # first: high cooperation, no extension
        first = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        assert first.extensions_hours == 0.0

        _confirm_incident(
            tm, now=FIXED_TIME + timedelta(hours=1),
            cooperation=CooperationAssessment(self_disclosed=False, active_cooperation_in_resolution=False),
        )
        second = pe.start_window_if_eligible(tm, now=FIXED_TIME + timedelta(hours=1))
        assert second.id == first.id
        assert second.extensions_hours > 0.0

    def test_extension_decision_recorded_event_is_emitted_for_every_consumption(
        self, pe: PenaltyEngine, tm: TrustManager, core: CoreDatabase,
    ) -> None:
        _confirm_incident(tm)  # ineligible (high cooperation, isolated MINOR)
        pe.start_window_if_eligible(tm, now=FIXED_TIME)
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'extension.decision_recorded'")
        assert row is not None  # emitted even for an ineligible decision

    def test_penalty_window_extended_event_only_when_assigned_hours_positive(
        self, pe: PenaltyEngine, tm: TrustManager, core: CoreDatabase,
    ) -> None:
        _confirm_incident(tm)  # ineligible -- no extension
        pe.start_window_if_eligible(tm, now=FIXED_TIME)
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'penalty_window.extended'")
        assert row is None

    def test_capacity_cap_prevents_exceeding_the_absolute_maximum(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        """Defense in depth (Section 9): even a CRITICAL, repeated
        Incident cannot push target_active_hours past 336."""
        from penalty_engine.window import MAX_TARGET_ACTIVE_HOURS, target_active_hours

        _confirm_incident(
            tm, cooperation=CooperationAssessment(self_disclosed=False, active_cooperation_in_resolution=False),
        )
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        # Manually push the window close to the ceiling to exercise the cap deterministically.
        with pe._core.transaction() as tx:
            tx.execute("UPDATE penalty_windows SET extensions_hours = ? WHERE id = ?", (330.0, window.id))

        _confirm_incident(
            tm, now=FIXED_TIME + timedelta(hours=1),
            cooperation=CooperationAssessment(self_disclosed=False, active_cooperation_in_resolution=False),
        )
        # register a second, CRITICAL incident to force a large uncapped magnitude
        critical_incident = tm.register_incident_report(
            rule_group_id="rg2", trust_domain="chastity", description="severe",
            evidence=IncidentEvidence(
                actual_or_potential_impact=ImpactLevel.HIGH, intentionality=IntentAssessment.DELIBERATE,
                rule_breach_directness=BreachDirectness.DIRECT, evidence_confidence=EvidenceConfidenceLevel.HIGH,
                repetition=RepetitionEvidence(same_rule_confirmed_count=0, evaluation_window_days=30),
            ),
            now=FIXED_TIME + timedelta(hours=2),
        )
        tm.confirm_incident(
            critical_incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME + timedelta(hours=2),
        )

        result = pe.start_window_if_eligible(tm, now=FIXED_TIME + timedelta(hours=2))
        assert target_active_hours(result) <= MAX_TARGET_ACTIVE_HOURS


class TestCompletion:
    def test_ensure_current_state_completes_when_target_reached(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        result = pe.ensure_current_state(FIXED_TIME + timedelta(hours=DEFAULT_BASE_DURATION_HOURS + 1))
        assert result is None
        assert pe.get_active_or_frozen_penalty_window() is None

    def test_ensure_current_state_leaves_incomplete_window_untouched(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        result = pe.ensure_current_state(FIXED_TIME + timedelta(hours=1))
        assert result is not None
        assert result.status == PenaltyWindowStatus.ACTIVE

    def test_completion_emits_event_with_resolution_method(self, pe: PenaltyEngine, tm: TrustManager, core: CoreDatabase) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.ensure_current_state(FIXED_TIME + timedelta(hours=DEFAULT_BASE_DURATION_HOURS + 1))
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'penalty_window.completed'")
        assert row is not None

    def test_after_completion_a_new_incident_can_start_a_new_window(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.ensure_current_state(FIXED_TIME + timedelta(hours=DEFAULT_BASE_DURATION_HOURS + 1))

        _confirm_incident(tm, now=FIXED_TIME + timedelta(hours=DEFAULT_BASE_DURATION_HOURS + 2))
        new_window = pe.start_window_if_eligible(tm, now=FIXED_TIME + timedelta(hours=DEFAULT_BASE_DURATION_HOURS + 2))
        assert new_window is not None
        assert new_window.id != window.id


class TestPublicReadAPI:
    def test_get_penalty_window_relevant_domains_missing_window_raises(self, pe: PenaltyEngine) -> None:
        with pytest.raises(PenaltyWindowNotFound):
            pe.get_penalty_window_relevant_domains("does-not-exist")

    def test_get_authorization_freeze_state_not_found(self, pe: PenaltyEngine) -> None:
        assert pe.get_authorization_freeze_state("never-existed") == AuthorizationFreezeState.NOT_FOUND

    def test_get_authorization_freeze_state_open(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(
            window.id, FreezeReason.PARTNERED_INTIMACY_AUTHORIZATION,
            authorization_decision_id="auth-1", now=FIXED_TIME + timedelta(hours=1),
        )
        assert pe.get_authorization_freeze_state("auth-1") == AuthorizationFreezeState.OPEN

    def test_get_authorization_freeze_state_closed_normally(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(
            window.id, FreezeReason.PARTNERED_INTIMACY_AUTHORIZATION,
            authorization_decision_id="auth-1", now=FIXED_TIME + timedelta(hours=1),
        )
        pe.resume(window.id, FreezeReason.PARTNERED_INTIMACY_AUTHORIZATION, now=FIXED_TIME + timedelta(hours=2))
        assert pe.get_authorization_freeze_state("auth-1") == AuthorizationFreezeState.CLOSED

    def test_at_most_one_open_intimacy_authorization_freeze(self, pe: PenaltyEngine, tm: TrustManager) -> None:
        """I21/AA-FREEZE-1: enforced by a partial unique index."""
        import sqlite3
        _confirm_incident(tm)
        window = pe.start_window_if_eligible(tm, now=FIXED_TIME)
        pe.freeze(
            window.id, FreezeReason.PARTNERED_INTIMACY_AUTHORIZATION,
            authorization_decision_id="auth-1", now=FIXED_TIME + timedelta(hours=1),
        )
        with pytest.raises(sqlite3.IntegrityError):
            pe.freeze(
                window.id, FreezeReason.PARTNERED_INTIMACY_AUTHORIZATION,
                authorization_decision_id="auth-2", now=FIXED_TIME + timedelta(hours=1, minutes=30),
            )
