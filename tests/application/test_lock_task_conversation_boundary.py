"""
tests/application/test_lock_task_conversation_boundary.py

Verifies the boundary between deterministic lock/task commands
(First Testable Keyholder Milestone, Slice C) and Conversation Engine:
known commands never reach the model; ordinary conversational text
mentioning lock/task-like intent never causes a write; a deterministic
command response never enters Working Memory.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from application.models import IncomingMessage
from application.service import ApplicationService
from conversation_engine.engine import ConversationEngine
from conversation_engine.model_types import ModelGenerationRequest
from conversation_engine.subject_queue import SubjectConversationQueue
from infrastructure.database import Database as CoreDatabase
from memory_system.working_memory import InMemoryWorkingMemory
from task_catalog.models import LockRequirement, TaskInstanceRole
from task_catalog.repository import TaskCatalogAdministration

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _RecordingModel:
    """Records every request it receives -- used to prove (or disprove)
    that Conversation Engine was invoked at all."""

    def __init__(self, response: str = "a plain conversational reply") -> None:
        self._response = response
        self.calls: list[ModelGenerationRequest] = []

    def generate(self, *, request: ModelGenerationRequest) -> str:
        self.calls.append(request)
        return self._response


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


def _incoming(text: str, *, external_user_id: str = "42", external_message_id: str | None = None) -> IncomingMessage:
    return IncomingMessage(
        channel="discord", external_user_id=external_user_id, text=text, received_at=FIXED_TIME,
        external_message_id=external_message_id,
    )


def _complete_onboarding(service: ApplicationService, *, external_user_id: str = "42") -> None:
    for i, text in enumerate(("anything", "english", "neutral", "alex")):
        service.handle_message(_incoming(text, external_user_id=external_user_id, external_message_id=f"ob{i}"))


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(c)
    return c


@pytest.fixture
def model() -> _RecordingModel:
    return _RecordingModel()


@pytest.fixture
def service(core: CoreDatabase, model: _RecordingModel) -> ApplicationService:
    buffer = InMemoryWorkingMemory(max_exchanges_per_subject=10, max_characters_per_subject=8000)
    engine = ConversationEngine(
        model=model, working_memory_reader=buffer, working_memory_writer=buffer, queue=SubjectConversationQueue(),
    )
    return ApplicationService(core.db_path, core=core, conversation_engine=engine)


def _create_template(service: ApplicationService, *, template_id: str = "basic-chore") -> None:
    admin = TaskCatalogAdministration(service.db_path, core=service._core)
    admin.create_template(
        template_id=template_id, category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home", safety_classification="safe",
        eligible_instance_roles=(TaskInstanceRole.PRIMARY,), eligible_operating_modes=("standard", "advanced"),
        completion_requirements={}, verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE, created_via_consent_id="test-consent", now=FIXED_TIME,
    )


class TestKnownCommandsNeverReachTheModel:
    def test_lock_status_never_calls_the_model(self, service: ApplicationService, model: _RecordingModel) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("lock status", external_message_id="m1"))
        assert model.calls == []

    def test_lock_report_locked_never_calls_the_model(self, service: ApplicationService, model: _RecordingModel) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("lock report locked", external_message_id="m1"))
        assert model.calls == []

    def test_task_request_never_calls_the_model(self, service: ApplicationService, model: _RecordingModel) -> None:
        _create_template(service)
        _complete_onboarding(service)
        service.handle_message(_incoming("task request", external_message_id="m1"))
        assert model.calls == []

    def test_task_complete_never_calls_the_model(self, service: ApplicationService, model: _RecordingModel) -> None:
        _create_template(service)
        _complete_onboarding(service)
        service.handle_message(_incoming("task request", external_message_id="m1"))
        model.calls.clear()
        service.handle_message(_incoming("task complete", external_message_id="m2"))
        assert model.calls == []

    def test_invalid_lock_family_command_never_calls_the_model(self, service: ApplicationService, model: _RecordingModel) -> None:
        """CE-25's own guarantee, extended to the new command families --
        even an INVALID 'lock ...'/'task ...' input is caught by the
        family invalid_handler, never falling through to Conversation Engine."""
        _complete_onboarding(service)
        service.handle_message(_incoming("lock frobnicate", external_message_id="m1"))
        assert model.calls == []


class TestOrdinaryConversationalTextNeverWritesDomainState:
    def test_saying_i_am_locked_in_plain_conversation_does_not_create_a_lock_report(
        self, service: ApplicationService, model: _RecordingModel,
    ) -> None:
        model._response = "Got it, thanks for letting me know!"
        _complete_onboarding(service)
        result = service.handle_message(_incoming("hey, I'm locked right now", external_message_id="m1"))
        assert result.text == "Got it, thanks for letting me know!"
        assert len(model.calls) == 1  # this DID go to the model -- it's unmatched text
        with core_raw(service) as conn:
            count = conn.execute("SELECT COUNT(*) as n FROM lock_reports").fetchone()["n"]
        assert count == 0

    def test_saying_i_completed_it_in_plain_conversation_does_not_resolve_a_task(
        self, service: ApplicationService, model: _RecordingModel,
    ) -> None:
        _create_template(service)
        _complete_onboarding(service)
        service.handle_message(_incoming("task request", external_message_id="m1"))

        model._response = "Nice work!"
        service.handle_message(_incoming("I finished it just now", external_message_id="m2"))

        active = service.task_runtime.get_active_assignment(
            service.user_service.get_or_create_user("discord", "42", now=FIXED_TIME).id
        )
        assert active is not None  # still active -- free text never resolved it


def core_raw(service: ApplicationService):
    return service._core.raw_connection()


class TestDeterministicResponsesNeverEnterWorkingMemory:
    def test_lock_status_response_is_not_recorded_in_working_memory(
        self, service: ApplicationService, model: _RecordingModel,
    ) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("lock status", external_message_id="m1"))
        # Trigger an actual conversational turn to inspect Working Memory's own state.
        model._response = "just chatting"
        service.handle_message(_incoming("hello there", external_message_id="m2"))
        # Working Memory should contain exactly the ONE conversational
        # exchange -- never the deterministic 'lock status' reply.
        user = service.user_service.get_or_create_user("discord", "42", now=FIXED_TIME)
        snapshot = service._conversation_engine._working_memory_reader.read(subject_key=user.id)
        contents = [t.content for t in snapshot.turns]
        assert "hello there" in contents
        assert not any("no lock report yet" in c.lower() for c in contents)

    def test_task_active_response_is_not_recorded_in_working_memory(
        self, service: ApplicationService, model: _RecordingModel,
    ) -> None:
        _complete_onboarding(service)
        service.handle_message(_incoming("task active", external_message_id="m1"))
        model._response = "just chatting"
        service.handle_message(_incoming("hello there", external_message_id="m2"))
        user = service.user_service.get_or_create_user("discord", "42", now=FIXED_TIME)
        snapshot = service._conversation_engine._working_memory_reader.read(subject_key=user.id)
        contents = [t.content for t in snapshot.turns]
        assert not any("no active task" in c.lower() for c in contents)
