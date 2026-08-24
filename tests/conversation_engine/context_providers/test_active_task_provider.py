"""tests/conversation_engine/context_providers/test_active_task_provider.py"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conversation_engine.context_providers.active_task_provider import ActiveTaskContextProvider
from infrastructure.database import Database as CoreDatabase
from lock_state.models import LockKnowledgeState
from task_catalog.models import LockRequirement, TaskInstanceRole
from task_catalog.repository import TaskCatalog, TaskCatalogAdministration
from task_runtime.repository import TaskRuntime, TaskRuntimeAdministration

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
def catalog_admin(core: CoreDatabase) -> TaskCatalogAdministration:
    return TaskCatalogAdministration(core.db_path, core=core)


@pytest.fixture
def runtime_admin(core: CoreDatabase) -> TaskRuntimeAdministration:
    return TaskRuntimeAdministration(core.db_path, core=core)


@pytest.fixture
def provider(core: CoreDatabase) -> ActiveTaskContextProvider:
    return ActiveTaskContextProvider(
        task_runtime=TaskRuntime(core.db_path, core=core), task_catalog=TaskCatalog(core.db_path, core=core),
    )


@pytest.fixture
def user_id(core: CoreDatabase) -> str:
    return _create_user(core)


def _create_template(
    catalog_admin: TaskCatalogAdministration, *, template_id: str = "t1",
    title: str = "Tidy one surface", instructions: str = "Pick a surface and clear it off.",
) -> None:
    catalog_admin.create_template(
        template_id=template_id, title=title, instructions=instructions,
        category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home", safety_classification="safe",
        eligible_instance_roles=(TaskInstanceRole.PRIMARY,), eligible_operating_modes=("standard",),
        completion_requirements={"steps": ["do it"]}, verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE, created_via_consent_id="c1", now=FIXED_TIME,
    )


class TestNamespace:
    def test_namespace_is_active_task(self, provider: ActiveTaskContextProvider) -> None:
        assert provider.namespace == "active_task"


class TestNoActiveAssignment:
    def test_no_assignment_yields_a_real_fragment_not_none(self, provider: ActiveTaskContextProvider, user_id: str) -> None:
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert fragment is not None
        assert fragment.data["has_active_task"] is False


class TestActiveAssignment:
    def test_active_assignment_included(self, catalog_admin, runtime_admin, provider: ActiveTaskContextProvider, user_id: str) -> None:
        _create_template(catalog_admin)
        runtime_admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c2", now=FIXED_TIME,
        )
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert fragment.data["has_active_task"] is True
        assert fragment.data["template_id"] == "t1"
        assert fragment.data["template_version"] == 1
        assert fragment.data["title"] == "Tidy one surface"
        assert fragment.data["instructions"] == "Pick a surface and clear it off."
        assert fragment.data["category"] == "chore"
        assert fragment.data["difficulty"] == "easy"
        assert fragment.data["duration_minutes"] == 10

    def test_uses_exact_historical_version_not_current_latest(self, catalog_admin, runtime_admin, provider: ActiveTaskContextProvider, user_id: str) -> None:
        _create_template(catalog_admin)
        assignment = runtime_admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c2", now=FIXED_TIME,
        )
        assert assignment.template_version == 1

        catalog_admin.add_version(
            "t1", title="Tidy one surface (v2 wording)", instructions="Completely different v2 instructions.",
            category="chore-v2", difficulty="hard", effort="high", duration_minutes=99,
            required_equipment=(), required_privacy="none", required_context="home", safety_classification="safe",
            eligible_instance_roles=(TaskInstanceRole.PRIMARY,), eligible_operating_modes=("standard",),
            completion_requirements={}, verification_requirements={}, reflection_requirements=None,
            lock_requirement=LockRequirement.NONE, created_via_consent_id="c3", now=FIXED_TIME,
        )
        catalog_admin.set_current_version("t1", version=2, via_consent_id="c4", now=FIXED_TIME)

        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert fragment.data["template_version"] == 1
        assert fragment.data["category"] == "chore"  # NOT "chore-v2" -- the version pinned at assignment time
        # Candidate B / instruction #10's own mandatory historical-version
        # acceptance test: title/instructions must extend this SAME
        # already-existing invariant, not a parallel mechanism.
        assert fragment.data["title"] == "Tidy one surface"
        assert fragment.data["instructions"] == "Pick a surface and clear it off."
        assert fragment.data["title"] != "Tidy one surface (v2 wording)"
        assert fragment.data["instructions"] != "Completely different v2 instructions."

    def test_resolved_assignment_is_no_longer_active_context(self, catalog_admin, runtime_admin, provider: ActiveTaskContextProvider, user_id: str) -> None:
        _create_template(catalog_admin)
        assignment = runtime_admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c2", now=FIXED_TIME,
        )
        runtime_admin.complete_task(assignment_id=assignment.id, resolved_via_consent_id="c3", now=FIXED_TIME)
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert fragment.data["has_active_task"] is False


class TestNoInternalIdLeakage:
    def test_fragment_data_never_contains_assignment_id_user_id_or_consent_id(
        self, catalog_admin, runtime_admin, provider: ActiveTaskContextProvider, user_id: str,
    ) -> None:
        _create_template(catalog_admin)
        assignment = runtime_admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="very-secret-consent-id", now=FIXED_TIME,
        )
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        rendered = str(fragment.data)
        assert assignment.id not in rendered
        assert user_id not in rendered
        assert "very-secret-consent-id" not in rendered


class TestUserIsolation:
    def test_one_users_assignment_does_not_leak_to_another(self, catalog_admin, runtime_admin, provider: ActiveTaskContextProvider, core: CoreDatabase) -> None:
        _create_template(catalog_admin)
        user_a = _create_user(core)
        user_b = _create_user(core)
        runtime_admin.assign_task(
            user_id=user_a, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c2", now=FIXED_TIME,
        )
        fragment_a = provider.provide_context(subject_key=user_a, now=FIXED_TIME)
        fragment_b = provider.provide_context(subject_key=user_b, now=FIXED_TIME)
        assert fragment_a.data["has_active_task"] is True
        assert fragment_b.data["has_active_task"] is False


class TestConcurrentSubjectIsolation:
    def test_same_provider_instance_concurrent_calls_never_cross_contaminate(
        self, catalog_admin, runtime_admin, provider: ActiveTaskContextProvider, core: CoreDatabase,
    ) -> None:
        _create_template(catalog_admin)
        user_a = _create_user(core)
        user_b = _create_user(core)
        runtime_admin.assign_task(
            user_id=user_a, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c2", now=FIXED_TIME,
        )

        results: dict[str, bool] = {}
        errors: list[str] = []

        def call(label: str, subject: str) -> None:
            for _ in range(20):
                fragment = provider.provide_context(subject_key=subject, now=FIXED_TIME)
                if results.get(label) not in (None, fragment.data["has_active_task"]):
                    errors.append(f"{label} saw inconsistent state")
                results[label] = fragment.data["has_active_task"]

        threads = [threading.Thread(target=call, args=("a", user_a)), threading.Thread(target=call, args=("b", user_b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        assert results["a"] is True
        assert results["b"] is False


class TestNoAdministrationDependency:
    def test_provider_has_no_administration_attributes(self, provider: ActiveTaskContextProvider) -> None:
        assert not hasattr(provider, "_task_runtime_admin")
        assert not hasattr(provider, "_task_catalog_admin")


class TestLegacyRowWithNoRecordedContent:
    """Candidate B, migration 021: a TaskTemplateVersion row that
    predates human-readable content reads back with title=None/
    instructions=None -- never fabricated here. This provider's job
    is only to pass that None through; rendering an explicit "not
    recorded" marker is conversation_engine/prompt_builder.py's job
    (see tests/conversation_engine/test_prompt_domain_context.py)."""

    def test_legacy_row_with_null_title_and_instructions_exposes_none(
        self, catalog_admin, runtime_admin, provider: ActiveTaskContextProvider, core: CoreDatabase, user_id: str,
    ) -> None:
        # Simulate a pre-migration-021 row directly via raw SQL --
        # there is no way to construct one through the governed write
        # API, which now always requires real title/instructions.
        catalog_admin.create_template(
            template_id="legacy-t1", title="placeholder", instructions="placeholder",
            category="chore", difficulty="easy", effort="low", duration_minutes=10,
            required_equipment=(), required_privacy="none", required_context="home", safety_classification="safe",
            eligible_instance_roles=(TaskInstanceRole.PRIMARY,), eligible_operating_modes=("standard",),
            completion_requirements={}, verification_requirements={}, reflection_requirements=None,
            lock_requirement=LockRequirement.NONE, created_via_consent_id="c1", now=FIXED_TIME,
        )
        with core.raw_connection() as conn:
            conn.execute(
                "UPDATE task_template_versions SET title = NULL, instructions = NULL "
                "WHERE template_id = ? AND version = 1",
                ("legacy-t1",),
            )
            conn.commit()

        runtime_admin.assign_task(
            user_id=user_id, template_id="legacy-t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c2", now=FIXED_TIME,
        )
        fragment = provider.provide_context(subject_key=user_id, now=FIXED_TIME)
        assert fragment.data["has_active_task"] is True
        assert fragment.data["title"] is None
        assert fragment.data["instructions"] is None
