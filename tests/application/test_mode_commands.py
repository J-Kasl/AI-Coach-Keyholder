"""
tests/application/test_mode_commands.py

Tests for the `mode`/`mode status`/`mode request advanced`/
`mode request standard`/`mode cancel`/`mode confirm` commands wired
into ApplicationService. Uses ApplicationService.handle_message()
end-to-end (through the real CommandRouter), not AdvancedMode/
AdvancedModeAdministration directly -- those are already covered by
tests/advanced_mode/.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application.models import IncomingMessage
from application.service import ApplicationService
from infrastructure.database import Database as CoreDatabase
from trust_manager.models import (
    BreachDirectness, ConfirmationSource, CooperationAssessment, EvidenceConfidenceLevel, ImpactLevel,
    IncidentConfirmation, IncidentEvidence, IntentAssessment, RepetitionEvidence,
)

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
def service(core: CoreDatabase) -> ApplicationService:
    return ApplicationService(core.db_path, core=core)


def _incoming(
    text: str, *, external_user_id: str = "42", now: datetime = FIXED_TIME, external_message_id: str | None = None,
) -> IncomingMessage:
    return IncomingMessage(
        channel="discord", external_user_id=external_user_id, text=text, received_at=now,
        external_message_id=external_message_id,
    )


def _complete_onboarding(service: ApplicationService, *, external_user_id: str = "42", now: datetime = FIXED_TIME) -> None:
    service.handle_message(_incoming("anything", external_user_id=external_user_id, now=now, external_message_id="m0"))
    service.handle_message(_incoming("english", external_user_id=external_user_id, now=now, external_message_id="m1"))
    service.handle_message(_incoming("neutral", external_user_id=external_user_id, now=now, external_message_id="m2"))
    service.handle_message(_incoming("alex", external_user_id=external_user_id, now=now, external_message_id="m3"))


def _start_active_penalty_window(service: ApplicationService, *, now: datetime = FIXED_TIME):
    """Mirrors tests/advanced_mode/test_repository.py's own helper --
    cooperation=HIGH to avoid an unplanned Extension, keeping the
    target duration at exactly the 24h default."""
    tm = service.trust_manager
    tm.create_domain(domain_id="chastity", display_name="Chastity", description="...", created_via_consent_id="c1", now=now)
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
        incident.id, new_confirmation=IncidentConfirmation.CONFIRMED, source=ConfirmationSource.USER_ACKNOWLEDGED,
        evidence_description="admitted", now=now,
        cooperation=CooperationAssessment(self_disclosed=True, active_cooperation_in_resolution=True),
    )
    return service.penalty_engine.start_window_if_eligible(tm, now=now)


class TestModeStatus:
    def test_status_in_standard_with_no_request(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("mode status", external_message_id="m4"))
        assert "current mode: standard" in result.text.lower()
        assert "no active mode transition request" in result.text.lower()

    def test_mode_and_mode_status_are_equivalent(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        a = service.handle_message(_incoming("mode", external_message_id="m4"))
        b = service.handle_message(_incoming("mode status", external_message_id="m5"))
        assert a.text == b.text

    def test_unknown_mode_command_falls_through_to_the_generic_unrecognized_reply(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("mode frobnicate", external_message_id="m4"))
        assert "don't recognize" in result.text.lower()


class TestModeRequestAdvanced:
    def test_request_standard_to_advanced(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        assert "standard -> advanced" in result.text.lower() or "requested: standard" in result.text.lower()
        assert "24-hour" in result.text
        assert "not wired in yet" in result.text.lower()

        status = service.handle_message(_incoming("mode status", external_message_id="m5"))
        assert "waiting" in status.text.lower()

    def test_request_blocked_by_active_penalty_window(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        _start_active_penalty_window(service)
        result = service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        assert "penalty window" in result.text.lower()
        assert "24-hour wait will only start" in result.text.lower()

    def test_second_request_while_one_pending_is_rejected(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        result = service.handle_message(_incoming("mode request advanced", external_message_id="m5"))
        assert "already have a pending" in result.text.lower()


class TestModeStatusShowsRemainingWait:
    def test_status_shows_confirmable_at_and_cannot_confirm_yet(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        status = service.handle_message(_incoming("mode status", now=FIXED_TIME + timedelta(hours=1), external_message_id="m5"))
        assert "confirmable at" in status.text.lower()
        assert "can confirm now: no" in status.text.lower()


class TestModeTransitionToAwaitingConfirmation:
    def test_status_reflects_awaiting_confirmation_after_24h_via_settle_orchestration(self, service: ApplicationService) -> None:
        """Exercises the fake-clock-driven transition through the real
        _settle_mode_state() orchestration -- no direct
        AdvancedModeAdministration call, purely through handle_message()."""
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        status = service.handle_message(_incoming("mode status", now=FIXED_TIME + timedelta(hours=24), external_message_id="m5"))
        assert "awaiting_confirmation" in status.text.lower()
        assert "can confirm now: yes" in status.text.lower()


class TestModeConfirm:
    def test_successful_confirmation(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        result = service.handle_message(
            _incoming("mode confirm", now=FIXED_TIME + timedelta(hours=24), external_message_id="m5"),
        )
        assert "confirmed" in result.text.lower()
        assert "advanced" in result.text.lower()

        status = service.handle_message(_incoming("mode status", now=FIXED_TIME + timedelta(hours=25), external_message_id="m6"))
        assert "current mode: advanced" in status.text.lower()

    def test_request_and_confirm_use_different_consent_references(self, service: ApplicationService, core: CoreDatabase) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="request-msg-id"))
        service.handle_message(_incoming("mode confirm", now=FIXED_TIME + timedelta(hours=24), external_message_id="confirm-msg-id"))

        with core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT * FROM mode_transition_requests WHERE status = 'completed' ORDER BY requested_at DESC LIMIT 1",
            )
        assert row["requested_via_consent_id"] == "discord_message:request-msg-id"
        assert row["confirmed_via_consent_id"] == "discord_message:confirm-msg-id"
        assert row["requested_via_consent_id"] != row["confirmed_via_consent_id"]

    def test_confirm_too_early_is_rejected_with_a_clear_message(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        result = service.handle_message(_incoming("mode confirm", now=FIXED_TIME + timedelta(hours=1), external_message_id="m5"))
        assert "not yet" in result.text.lower()

    def test_confirm_with_no_pending_request(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("mode confirm", external_message_id="m4"))
        assert "don't have a pending" in result.text.lower()

    def test_confirm_interrupted_by_new_penalty_window(self, service: ApplicationService) -> None:
        """A new PW appearing before `mode confirm` is sent gets caught
        by `_settle_mode_state()`'s own orchestration (point 4) BEFORE
        the handler's status check even runs -- so in ordinary,
        sequential use (not a genuine TOCTOU race), the user sees the
        "not yet" wording, not `ModeTransitionInterruptedByPenaltyWindowError`'s
        own message. Both communicate the same thing (wait invalidated,
        restarts after the PW ends); the exception path specifically
        requires a race between settle and confirm_transition()'s own
        internal re-check, which this orchestration is precisely
        designed to make rare."""
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        service.handle_message(_incoming("mode status", now=FIXED_TIME + timedelta(hours=24), external_message_id="m5"))
        _start_active_penalty_window(service, now=FIXED_TIME + timedelta(hours=25))

        result = service.handle_message(_incoming("mode confirm", now=FIXED_TIME + timedelta(hours=26), external_message_id="m6"))
        assert "not yet" in result.text.lower()
        assert "restart" in result.text.lower()

        status = service.handle_message(_incoming("mode status", now=FIXED_TIME + timedelta(hours=27), external_message_id="m7"))
        assert "paused_by_penalty_window" in status.text.lower()

    def test_confirm_interrupted_by_a_genuine_race_after_settle(
        self, service: ApplicationService, core: CoreDatabase, monkeypatch,
    ) -> None:
        """Exercises the OTHER branch -- ModeTransitionInterruptedByPenaltyWindowError's
        own message -- by simulating a PW appearing exactly between
        _settle_mode_state() and confirm_transition()'s own internal
        re-check, the genuine TOCTOU race the exception path exists for."""
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        service.handle_message(_incoming("mode status", now=FIXED_TIME + timedelta(hours=24), external_message_id="m5"))

        original = service.advanced_mode_admin.confirm_transition

        def confirm_after_injecting_a_pw(*args, **kwargs):
            _start_active_penalty_window(service, now=FIXED_TIME + timedelta(hours=25, minutes=30))
            return original(*args, **kwargs)

        monkeypatch.setattr(service.advanced_mode_admin, "confirm_transition", confirm_after_injecting_a_pw)
        result = service.handle_message(_incoming("mode confirm", now=FIXED_TIME + timedelta(hours=26), external_message_id="m6"))
        assert "no longer valid" in result.text.lower()
        assert "restart" in result.text.lower()

    def test_confirm_invalidated_by_source_mode_mismatch(self, service: ApplicationService, core: CoreDatabase) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        service.handle_message(_incoming("mode status", now=FIXED_TIME + timedelta(hours=24), external_message_id="m5"))

        # Simulates OperatingMode changing via some other path before confirmation.
        with core.transaction() as tx:
            tx.execute("UPDATE operating_mode_state SET current_mode = 'advanced', mode_activated_at = ? WHERE id = 1", (FIXED_TIME.isoformat(),))

        result = service.handle_message(_incoming("mode confirm", now=FIXED_TIME + timedelta(hours=25), external_message_id="m6"))
        assert "invalidated" in result.text.lower()
        assert "new" in result.text.lower()


class TestModeCancel:
    def test_cancel_a_pending_request(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("mode request advanced", external_message_id="m4"))
        result = service.handle_message(_incoming("mode cancel", external_message_id="m5"))
        assert "cancelled" in result.text.lower()

        status = service.handle_message(_incoming("mode status", external_message_id="m6"))
        assert "no active mode transition request" in status.text.lower()

    def test_cancel_with_nothing_pending(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("mode cancel", external_message_id="m4"))
        assert "don't have a pending" in result.text.lower()


class TestModeRequestStandard:
    def _move_to_advanced(self, service: ApplicationService, *, now: datetime = FIXED_TIME) -> None:
        service.handle_message(_incoming("mode request advanced", now=now, external_message_id="req"))
        service.handle_message(_incoming("mode confirm", now=now + timedelta(hours=24), external_message_id="conf"))

    def test_thirty_day_minimum_enforced(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        self._move_to_advanced(service)
        too_soon = FIXED_TIME + timedelta(hours=24) + timedelta(days=10)
        result = service.handle_message(_incoming("mode request standard", now=too_soon, external_message_id="m10"))
        assert "30 days" in result.text
        assert "eligible from" in result.text.lower()

    def test_request_standard_after_thirty_days_succeeds(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        self._move_to_advanced(service)
        eligible = FIXED_TIME + timedelta(hours=24) + timedelta(days=30)
        result = service.handle_message(_incoming("mode request standard", now=eligible, external_message_id="m10"))
        assert "advanced -> standard" in result.text.lower()
