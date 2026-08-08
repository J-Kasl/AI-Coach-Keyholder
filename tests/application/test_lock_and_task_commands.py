"""
tests/application/test_lock_and_task_commands.py

Tests for `lock status`/`lock report locked`/`lock report unlocked`
and `task request`/`task active`/`task complete`/`task cancel` wired
into ApplicationService (First Testable Keyholder Milestone, Slice C).
Uses ApplicationService.handle_message() end-to-end (through the real
CommandRouter) -- domain-level behavior is already covered by
tests/lock_state/ and tests/task_runtime/.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from application.models import IncomingMessage
from application.service import ApplicationService
from infrastructure.database import Database as CoreDatabase
from task_catalog.models import LockRequirement, TaskInstanceRole

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
    service.handle_message(_incoming("anything", external_user_id=external_user_id, now=now, external_message_id="ob0"))
    service.handle_message(_incoming("english", external_user_id=external_user_id, now=now, external_message_id="ob1"))
    service.handle_message(_incoming("neutral", external_user_id=external_user_id, now=now, external_message_id="ob2"))
    service.handle_message(_incoming("alex", external_user_id=external_user_id, now=now, external_message_id="ob3"))


def _create_template(service: ApplicationService, *, template_id: str, lock_requirement: LockRequirement) -> None:
    from task_catalog.repository import TaskCatalogAdministration
    admin = TaskCatalogAdministration(service.db_path, core=service._core)
    admin.create_template(
        template_id=template_id, category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home", safety_classification="safe",
        eligible_instance_roles=(TaskInstanceRole.PRIMARY,), eligible_operating_modes=("standard", "advanced"),
        completion_requirements={}, verification_requirements={}, reflection_requirements=None,
        lock_requirement=lock_requirement, created_via_consent_id="test-consent", now=FIXED_TIME,
    )


class TestLockStatus:
    def test_unknown_before_any_report(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("lock status", external_message_id="m1"))
        assert "no lock report yet" in result.text.lower()

    def test_reports_locked(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("lock report locked", external_message_id="m1"))
        result = service.handle_message(_incoming("lock status", external_message_id="m2"))
        assert "locked (as you reported)" in result.text.lower()

    def test_reports_unlocked(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("lock report unlocked", external_message_id="m1"))
        result = service.handle_message(_incoming("lock status", external_message_id="m2"))
        assert "unlocked (as you reported)" in result.text.lower()

    def test_second_report_supersedes_first(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("lock report locked", external_message_id="m1"))
        service.handle_message(_incoming("lock report unlocked", external_message_id="m2"))
        result = service.handle_message(_incoming("lock status", external_message_id="m3"))
        assert "unlocked" in result.text.lower()

    def test_invalid_lock_command_gets_family_reply(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("lock frobnicate", external_message_id="m1"))
        assert "not a recognized `lock` command" in result.text.lower()

    def test_persists_across_a_reopened_application_service(self, core: CoreDatabase) -> None:
        service1 = ApplicationService(core.db_path, core=core)
        _complete_onboarding(service1)
        service1.handle_message(_incoming("lock report locked", external_message_id="m1"))

        service2 = ApplicationService(core.db_path, core=CoreDatabase(core.db_path))
        result = service2.handle_message(_incoming("lock status", external_user_id="42", external_message_id="m2"))
        assert "locked" in result.text.lower()

    def test_user_isolation(self, service: ApplicationService) -> None:
        _complete_onboarding(service, external_user_id="user-a")
        _complete_onboarding(service, external_user_id="user-b")
        service.handle_message(_incoming("lock report locked", external_user_id="user-a", external_message_id="ma1"))
        result_a = service.handle_message(_incoming("lock status", external_user_id="user-a", external_message_id="ma2"))
        result_b = service.handle_message(_incoming("lock status", external_user_id="user-b", external_message_id="mb1"))
        assert "locked" in result_a.text.lower()
        assert "no lock report yet" in result_b.text.lower()


class TestTaskActiveWithoutAssignment:
    def test_no_active_task_message(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("task active", external_message_id="m1"))
        assert "no active task" in result.text.lower()


class TestTaskRequest:
    def test_request_with_no_eligible_template_at_all(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("task request", external_message_id="m1"))
        assert "no eligible task available" in result.text.lower()

    def test_request_assigns_a_no_lock_requirement_task(self, service: ApplicationService) -> None:
        _create_template(service, template_id="basic-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service)
        result = service.handle_message(_incoming("task request", external_message_id="m1"))
        assert "assigned: basic-chore" in result.text.lower()

    def test_request_skips_locked_requirement_task_when_lock_state_unknown(self, service: ApplicationService) -> None:
        _create_template(service, template_id="locked-chore", lock_requirement=LockRequirement.REQUIRES_LOCKED)
        _complete_onboarding(service)
        result = service.handle_message(_incoming("task request", external_message_id="m1"))
        assert "no eligible task available" in result.text.lower()

    def test_request_assigns_locked_requirement_task_once_locked(self, service: ApplicationService) -> None:
        _create_template(service, template_id="locked-chore", lock_requirement=LockRequirement.REQUIRES_LOCKED)
        _complete_onboarding(service)
        service.handle_message(_incoming("lock report locked", external_message_id="m1"))
        result = service.handle_message(_incoming("task request", external_message_id="m2"))
        assert "assigned: locked-chore" in result.text.lower()

    def test_request_selection_is_deterministic_lowest_template_id(self, service: ApplicationService) -> None:
        _create_template(service, template_id="zzz-chore", lock_requirement=LockRequirement.NONE)
        _create_template(service, template_id="aaa-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service)
        result = service.handle_message(_incoming("task request", external_message_id="m1"))
        assert "assigned: aaa-chore" in result.text.lower()

    def test_request_with_existing_active_task_refuses(self, service: ApplicationService) -> None:
        _create_template(service, template_id="basic-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service)
        service.handle_message(_incoming("task request", external_message_id="m1"))
        result = service.handle_message(_incoming("task request", external_message_id="m2"))
        assert "already have an active task" in result.text.lower()

    def test_invalid_task_command_gets_family_reply(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("task frobnicate", external_message_id="m1"))
        assert "not a recognized `task` command" in result.text.lower()


class TestTaskActiveWithAssignment:
    def test_shows_the_active_task(self, service: ApplicationService) -> None:
        _create_template(service, template_id="basic-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service)
        service.handle_message(_incoming("task request", external_message_id="m1"))
        result = service.handle_message(_incoming("task active", external_message_id="m2"))
        assert "basic-chore" in result.text.lower()


class TestTaskComplete:
    def test_completes_the_active_task(self, service: ApplicationService) -> None:
        _create_template(service, template_id="basic-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service)
        service.handle_message(_incoming("task request", external_message_id="m1"))
        result = service.handle_message(_incoming("task complete", external_message_id="m2"))
        assert result.text == "Completed."
        follow_up = service.handle_message(_incoming("task active", external_message_id="m3"))
        assert "no active task" in follow_up.text.lower()

    def test_complete_with_no_active_task(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("task complete", external_message_id="m1"))
        assert "no active task to resolve" in result.text.lower()

    def test_completing_twice_gives_a_safe_reply(self, service: ApplicationService) -> None:
        """The second 'task complete' finds no active task (the first
        one already resolved it) -- not a transition error, since
        get_active_assignment() correctly returns None by then."""
        _create_template(service, template_id="basic-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service)
        service.handle_message(_incoming("task request", external_message_id="m1"))
        service.handle_message(_incoming("task complete", external_message_id="m2"))
        result = service.handle_message(_incoming("task complete", external_message_id="m3"))
        assert "no active task to resolve" in result.text.lower()


class TestTaskCancel:
    def test_cancels_the_active_task(self, service: ApplicationService) -> None:
        _create_template(service, template_id="basic-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service)
        service.handle_message(_incoming("task request", external_message_id="m1"))
        result = service.handle_message(_incoming("task cancel", external_message_id="m2"))
        assert result.text == "Cancelled."

    def test_cancel_with_no_active_task(self, service: ApplicationService) -> None:
        _complete_onboarding(service)
        result = service.handle_message(_incoming("task cancel", external_message_id="m1"))
        assert "no active task to resolve" in result.text.lower()

    def test_after_cancel_a_new_request_can_be_made(self, service: ApplicationService) -> None:
        _create_template(service, template_id="basic-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service)
        service.handle_message(_incoming("task request", external_message_id="m1"))
        service.handle_message(_incoming("task cancel", external_message_id="m2"))
        result = service.handle_message(_incoming("task request", external_message_id="m3"))
        assert "assigned: basic-chore" in result.text.lower()


class TestPersistenceAndIsolation:
    def test_task_assignment_persists_across_a_reopened_application_service(self, core: CoreDatabase) -> None:
        service1 = ApplicationService(core.db_path, core=core)
        _create_template(service1, template_id="basic-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service1)
        service1.handle_message(_incoming("task request", external_message_id="m1"))

        service2 = ApplicationService(core.db_path, core=CoreDatabase(core.db_path))
        result = service2.handle_message(_incoming("task active", external_user_id="42", external_message_id="m2"))
        assert "basic-chore" in result.text.lower()

    def test_task_user_isolation(self, service: ApplicationService) -> None:
        _create_template(service, template_id="basic-chore", lock_requirement=LockRequirement.NONE)
        _complete_onboarding(service, external_user_id="user-a")
        _complete_onboarding(service, external_user_id="user-b")
        service.handle_message(_incoming("task request", external_user_id="user-a", external_message_id="ma1"))
        result_a = service.handle_message(_incoming("task active", external_user_id="user-a", external_message_id="ma2"))
        result_b = service.handle_message(_incoming("task active", external_user_id="user-b", external_message_id="mb1"))
        assert "basic-chore" in result_a.text.lower()
        assert "no active task" in result_b.text.lower()
