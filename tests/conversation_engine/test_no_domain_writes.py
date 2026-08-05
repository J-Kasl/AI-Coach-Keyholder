"""tests/conversation_engine/test_no_domain_writes.py"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from application.models import IncomingMessage
from application.service import ApplicationService
from conversation_engine.engine import ConversationEngine
from conversation_engine.recent_history import TransitionalRecentMessageBuffer
from conversation_engine.subject_queue import SubjectConversationQueue
from infrastructure.database import Database as CoreDatabase

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeModel:
    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, *, request) -> str:
        return self._response


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


def _engine_with_response(text: str) -> ConversationEngine:
    return ConversationEngine(
        model=_FakeModel(text), buffer=TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=5000),
        queue=SubjectConversationQueue(),
    )


def _incoming(text: str, *, external_message_id: str | None = None, now: datetime = FIXED_TIME) -> IncomingMessage:
    return IncomingMessage(channel="discord", external_user_id="1", text=text, received_at=now, external_message_id=external_message_id)


def _complete_onboarding(service: ApplicationService) -> None:
    for i, text in enumerate(("anything", "english", "neutral", "alex")):
        service.handle_message(_incoming(text, external_message_id=f"onboard-{i}"))


def _snapshot_relevant_tables(core: CoreDatabase) -> dict:
    tables = [
        "operating_mode_state", "mode_transition_requests", "penalty_windows",
        "trust_domains", "goals", "recovery_plans", "domain_events",
    ]
    snapshot = {}
    with core.transaction() as tx:
        for table in tables:
            try:
                rows = tx.fetch_all(f"SELECT * FROM {table}")
                snapshot[table] = [dict(r) for r in rows]
            except Exception:
                snapshot[table] = None  # table may not exist / may need args -- skip gracefully
    return snapshot


class TestScenario1NewUserBootstrapIsNotAViolation:
    def test_first_message_causes_bootstrap_writes_this_is_expected(self, tmp_path: Path) -> None:
        core = CoreDatabase(tmp_path / "test.db")
        _apply_migrations(core)
        service = ApplicationService(core.db_path, core=core, conversation_engine=_engine_with_response("hi"))

        result = service.handle_message(_incoming("hello", external_message_id="m0"))
        assert result.text  # onboarding prompt -- some bootstrap write happened, and that's fine

        with core.transaction() as tx:
            user_count = tx.fetch_one("SELECT COUNT(*) as n FROM user_accounts")["n"]
        assert user_count == 1  # get_or_create_user() bootstrap -- not a violation of this invariant


class TestScenario2OrdinaryUnmatchedTextDoesNotWriteDomainState:
    def test_relevant_tables_unchanged_after_unmatched_message(self, tmp_path: Path) -> None:
        core = CoreDatabase(tmp_path / "test.db")
        _apply_migrations(core)
        service = ApplicationService(core.db_path, core=core, conversation_engine=_engine_with_response("just chatting back"))
        _complete_onboarding(service)

        before = _snapshot_relevant_tables(core)
        result = service.handle_message(_incoming("how's it going", external_message_id="m10"))
        after = _snapshot_relevant_tables(core)

        assert result.text == "just chatting back"
        assert before == after

    def test_no_new_domain_event_created(self, tmp_path: Path) -> None:
        core = CoreDatabase(tmp_path / "test.db")
        _apply_migrations(core)
        service = ApplicationService(core.db_path, core=core, conversation_engine=_engine_with_response("reply"))
        _complete_onboarding(service)

        with core.transaction() as tx:
            before_count = tx.fetch_one("SELECT COUNT(*) as n FROM domain_events")["n"]
        service.handle_message(_incoming("just talking", external_message_id="m11"))
        with core.transaction() as tx:
            after_count = tx.fetch_one("SELECT COUNT(*) as n FROM domain_events")["n"]
        assert before_count == after_count


class TestScenario3ModelClaimingAnOperationDoesNotPerformIt:
    def test_claimed_mode_switch_does_not_change_operating_mode(self, tmp_path: Path) -> None:
        core = CoreDatabase(tmp_path / "test.db")
        _apply_migrations(core)
        service = ApplicationService(
            core.db_path, core=core, conversation_engine=_engine_with_response("I switched you to Advanced Mode."),
        )
        _complete_onboarding(service)

        with core.transaction() as tx:
            before_mode = tx.fetch_one("SELECT current_mode FROM operating_mode_state WHERE id = 1")["current_mode"]

        result = service.handle_message(_incoming("switch me to advanced mode please", external_message_id="m12"))
        assert result.text == "I switched you to Advanced Mode."  # shown as text

        with core.transaction() as tx:
            after_mode = tx.fetch_one("SELECT current_mode FROM operating_mode_state WHERE id = 1")["current_mode"]
            request_count = tx.fetch_one("SELECT COUNT(*) as n FROM mode_transition_requests")["n"]

        assert before_mode == after_mode == "standard"
        assert request_count == 0  # no mode_transition_request was ever created
