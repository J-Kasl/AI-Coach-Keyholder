"""tests/application/test_service.py"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application.models import IncomingMessage
from application.service import ApplicationService
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
    return c


@pytest.fixture
def service(core: CoreDatabase) -> ApplicationService:
    return ApplicationService(core.db_path, core=core)


def _incoming(text: str, *, external_user_id: str = "42", now: datetime = FIXED_TIME) -> IncomingMessage:
    return IncomingMessage(channel="discord", external_user_id=external_user_id, text=text, received_at=now)


def _complete_onboarding(service: ApplicationService, *, external_user_id: str = "42", now: datetime = FIXED_TIME) -> None:
    """Drives a fresh user through all three onboarding steps so a
    test can then exercise ordinary command behavior -- every test in
    this module that predates onboarding assumed a fresh user reached
    the command router immediately; onboarding now intercepts first,
    so those tests need a user who has already finished it."""
    service.handle_message(_incoming("anything", external_user_id=external_user_id, now=now))  # first contact -> language prompt
    service.handle_message(_incoming("english", external_user_id=external_user_id, now=now))     # -> ai_gender prompt
    service.handle_message(_incoming("neutral", external_user_id=external_user_id, now=now))       # -> personality prompt
    service.handle_message(_incoming("alex", external_user_id=external_user_id, now=now))            # -> complete


class TestHandleMessageBasics:
    def test_help_command_returns_command_list(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("help"))
        assert "status" in result.text.lower()

    def test_unrecognized_text_gives_safe_fallback(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("blah blah blah"))
        assert "help" in result.text.lower()

    def test_status_with_no_active_window(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("status"))
        assert "no active penalty window" in result.text.lower()

    def test_creates_a_user_account_on_first_contact(self, service: ApplicationService) -> None:
        service.handle_message(_incoming("help", external_user_id="999"))
        account = service.user_service.get_or_create_user("discord", "999", now=FIXED_TIME + timedelta(days=1))
        # calling again just returns the same account -- confirms one was created, not duplicated
        with service._core.transaction() as tx:
            count = tx.fetch_one("SELECT COUNT(*) as n FROM user_accounts")["n"]
        assert count == 1


class TestHandleMessageEndToEndWithRealDomainState:
    """Proves the full pipe: IncomingMessage -> ApplicationService ->
    a real domain module's public read API -> a real answer, not a mock."""

    def test_status_reports_a_real_active_penalty_window(self, service: ApplicationService, core: CoreDatabase) -> None:
        _complete_onboarding(service)
        from trust_manager.models import (
            BreachDirectness, ConfirmationSource, EvidenceConfidenceLevel, ImpactLevel,
            IncidentConfirmation, IncidentEvidence, IntentAssessment, RepetitionEvidence,
        )

        tm = service.trust_manager
        tm.create_domain(domain_id="chastity", display_name="Chastity", description="...",
                          created_via_consent_id="c1", now=FIXED_TIME)
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="late",
            evidence=IncidentEvidence(
                actual_or_potential_impact=ImpactLevel.LOW, intentionality=IntentAssessment.UNCLEAR,
                rule_breach_directness=BreachDirectness.INDIRECT, evidence_confidence=EvidenceConfidenceLevel.HIGH,
                repetition=RepetitionEvidence(same_rule_confirmed_count=0, evaluation_window_days=30),
            ),
            now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted", now=FIXED_TIME,
        )
        service.penalty_engine.start_window_if_eligible(tm, now=FIXED_TIME)

        result = service.handle_message(_incoming("status", now=FIXED_TIME + timedelta(hours=1)))
        assert "penalty window: active" in result.text.lower()
        assert "remaining" in result.text.lower()


class TestHandleMessageNeverRaises:
    def test_handle_message_swallows_unexpected_exceptions(self, service: ApplicationService, monkeypatch) -> None:
        _complete_onboarding(service)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(service.router, "route", boom)
        result = service.handle_message(_incoming("status"))
        assert "went wrong" in result.text.lower()
        assert "RuntimeError" not in result.text  # never leaks internals
