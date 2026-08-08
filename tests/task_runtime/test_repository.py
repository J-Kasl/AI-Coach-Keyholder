"""tests/task_runtime/test_repository.py"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infrastructure.database import Database as CoreDatabase
from lock_state.models import LockKnowledgeState
from task_catalog.models import LockRequirement, TaskInstanceRole
from task_catalog.repository import TaskCatalogAdministration
from task_runtime.models import TaskAssignmentStatus
from task_runtime.repository import (
    TaskAssignmentConcurrencyError,
    TaskAssignmentNotFoundError,
    TaskAssignmentReferentialIntegrityError,
    TaskAssignmentTransitionError,
    TaskNotEligibleError,
    TaskRuntime,
    TaskRuntimeAdministration,
    TaskTemplateNotFoundForAssignmentError,
)

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
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
def reader(core: CoreDatabase) -> TaskRuntime:
    return TaskRuntime(core.db_path, core=core)


@pytest.fixture
def admin(core: CoreDatabase) -> TaskRuntimeAdministration:
    return TaskRuntimeAdministration(core.db_path, core=core)


@pytest.fixture
def user_id(core: CoreDatabase) -> str:
    return _create_user(core)


def _create_template(catalog_admin: TaskCatalogAdministration, *, template_id: str, lock_requirement: LockRequirement) -> None:
    catalog_admin.create_template(
        template_id=template_id, category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home", safety_classification="safe",
        eligible_instance_roles=(TaskInstanceRole.PRIMARY,), eligible_operating_modes=("standard",),
        completion_requirements={}, verification_requirements={}, reflection_requirements=None,
        lock_requirement=lock_requirement, created_via_consent_id="consent-1", now=FIXED_TIME,
    )


class TestAssignEligibleTask:
    def test_assign_task_with_no_lock_requirement_succeeds(self, catalog_admin, admin: TaskRuntimeAdministration, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        assignment = admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        assert assignment.status == TaskAssignmentStatus.ACTIVE
        assert assignment.template_version == 1

    def test_assign_requires_locked_with_locked_user_reported_succeeds(self, catalog_admin, admin: TaskRuntimeAdministration, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.REQUIRES_LOCKED)
        assignment = admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.LOCKED_USER_REPORTED,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        assert assignment.status == TaskAssignmentStatus.ACTIVE


class TestRejectIneligibleAssignment:
    def test_requires_locked_with_unknown_rejected(self, catalog_admin, admin: TaskRuntimeAdministration, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.REQUIRES_LOCKED)
        with pytest.raises(TaskNotEligibleError):
            admin.assign_task(
                user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
                assigned_via_consent_id="c1", now=FIXED_TIME,
            )

    def test_requires_locked_with_unlocked_reported_rejected(self, catalog_admin, admin: TaskRuntimeAdministration, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.REQUIRES_LOCKED)
        with pytest.raises(TaskNotEligibleError):
            admin.assign_task(
                user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNLOCKED_USER_REPORTED,
                assigned_via_consent_id="c1", now=FIXED_TIME,
            )

    def test_rejected_eligibility_writes_nothing(self, catalog_admin, admin: TaskRuntimeAdministration, core: CoreDatabase, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.REQUIRES_LOCKED)
        with pytest.raises(TaskNotEligibleError):
            admin.assign_task(
                user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
                assigned_via_consent_id="c1", now=FIXED_TIME,
            )
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as n FROM task_assignments").fetchone()["n"]
        assert count == 0


class TestReferentialIntegrity:
    def test_invalid_user_id_rejected_with_referential_integrity_error(
        self, catalog_admin, admin: TaskRuntimeAdministration, core: CoreDatabase,
    ) -> None:
        """Must be TaskAssignmentReferentialIntegrityError -- specifically
        NOT TaskAssignmentConcurrencyError, which would mislabel an
        invalid user_id as an active-assignment race."""
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        with pytest.raises(TaskAssignmentReferentialIntegrityError):
            admin.assign_task(
                user_id="nonexistent-user", template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
                assigned_via_consent_id="c1", now=FIXED_TIME,
            )
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as n FROM task_assignments").fetchone()["n"]
        assert count == 0

    def test_invalid_template_id_rejected(self, admin: TaskRuntimeAdministration, user_id: str) -> None:
        with pytest.raises(TaskTemplateNotFoundForAssignmentError):
            admin.assign_task(
                user_id=user_id, template_id="does-not-exist", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
                assigned_via_consent_id="c1", now=FIXED_TIME,
            )

    def test_assignment_references_the_exact_immutable_version_it_was_created_against(
        self, catalog_admin, admin: TaskRuntimeAdministration, user_id: str,
    ) -> None:
        """assign_task() always uses the CURRENT version at assignment
        time -- and that reference stays correct even after Task
        Catalog's own current_version later advances (a separate,
        later add_version()/set_current_version() call does not
        retroactively change this assignment's own template_version)."""
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        assignment = admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        assert assignment.template_version == 1

        catalog_admin.add_version(
            "t1", category="chore", difficulty="hard", effort="high", duration_minutes=20,
            required_equipment=(), required_privacy="none", required_context="home", safety_classification="safe",
            eligible_instance_roles=(TaskInstanceRole.PRIMARY,), eligible_operating_modes=("standard",),
            completion_requirements={}, verification_requirements={}, reflection_requirements=None,
            lock_requirement=LockRequirement.REQUIRES_LOCKED, created_via_consent_id="c2", now=FIXED_TIME,
        )
        catalog_admin.set_current_version("t1", version=2, via_consent_id="c3", now=FIXED_TIME)

        assert assignment.template_version == 1  # the returned object itself never changes -- frozen


class TestDirectDatabaseIntegrityIsIndependentOfConcurrencyClassification:
    """assign_task() can never reach an invalid template_version through
    its own public API (get_current_version() always resolves a real,
    existing version first) -- but the schema-level composite FK must
    still independently reject one, and that rejection must never be
    confused with the partial-unique-index concurrency error. Verified
    directly against the schema, bypassing the repository layer
    entirely."""

    def test_invalid_template_version_is_rejected_by_the_composite_fk_directly(
        self, catalog_admin, core: CoreDatabase, user_id: str,
    ) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        with pytest.raises(Exception) as excinfo:
            with core.transaction() as tx:
                tx.execute(
                    """
                    INSERT INTO task_assignments
                        (id, user_id, template_id, template_version, status, assigned_at, assigned_via_consent_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("direct-insert-1", user_id, "t1", 999, "active", FIXED_TIME.isoformat(), "c1"),
                )
        # A genuine sqlite3 FK violation -- not this module's own
        # TaskAssignmentConcurrencyError, proving the two are never
        # conflated even at the lowest level.
        assert "TaskAssignmentConcurrencyError" not in type(excinfo.value).__name__
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as n FROM task_assignments").fetchone()["n"]
        assert count == 0


class TestPersistenceAndRead:
    def test_persists_across_a_reopened_database_connection(self, catalog_admin, core: CoreDatabase, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        admin1 = TaskRuntimeAdministration(core.db_path, core=core)
        assignment = admin1.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        reader2 = TaskRuntime(core.db_path, core=CoreDatabase(core.db_path))
        found = reader2.get_active_assignment(user_id)
        assert found is not None
        assert found.id == assignment.id

    def test_get_active_assignment_returns_none_when_none_exists(self, reader: TaskRuntime, user_id: str) -> None:
        assert reader.get_active_assignment(user_id) is None


class TestLifecycle:
    def test_complete_active_assignment(self, catalog_admin, admin: TaskRuntimeAdministration, reader: TaskRuntime, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        assignment = admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        completed = admin.complete_task(assignment_id=assignment.id, resolved_via_consent_id="c2", now=FIXED_TIME)
        assert completed.status == TaskAssignmentStatus.COMPLETED
        assert reader.get_active_assignment(user_id) is None

    def test_cancel_active_assignment(self, catalog_admin, admin: TaskRuntimeAdministration, reader: TaskRuntime, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        assignment = admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        cancelled = admin.cancel_task(assignment_id=assignment.id, resolved_via_consent_id="c2", now=FIXED_TIME)
        assert cancelled.status == TaskAssignmentStatus.CANCELLED

    def test_invalid_transition_completing_already_completed_assignment(
        self, catalog_admin, admin: TaskRuntimeAdministration, user_id: str,
    ) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        assignment = admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        admin.complete_task(assignment_id=assignment.id, resolved_via_consent_id="c2", now=FIXED_TIME)
        with pytest.raises(TaskAssignmentTransitionError):
            admin.complete_task(assignment_id=assignment.id, resolved_via_consent_id="c3", now=FIXED_TIME)

    def test_resolve_nonexistent_assignment_raises_not_found(self, admin: TaskRuntimeAdministration) -> None:
        with pytest.raises(TaskAssignmentNotFoundError):
            admin.complete_task(assignment_id="does-not-exist", resolved_via_consent_id="c1", now=FIXED_TIME)


class TestCardinality:
    def test_second_active_assignment_for_same_user_rejected(self, catalog_admin, admin: TaskRuntimeAdministration, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        _create_template(catalog_admin, template_id="t2", lock_requirement=LockRequirement.NONE)
        admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        with pytest.raises(TaskAssignmentConcurrencyError):
            admin.assign_task(
                user_id=user_id, template_id="t2", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
                assigned_via_consent_id="c2", now=FIXED_TIME,
            )

    def test_concurrent_assign_race_only_one_succeeds(self, catalog_admin, core: CoreDatabase, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        admin = TaskRuntimeAdministration(core.db_path, core=core)
        results: list[str] = []

        def attempt(consent_id: str) -> None:
            try:
                admin.assign_task(
                    user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
                    assigned_via_consent_id=consent_id, now=FIXED_TIME,
                )
                results.append("success")
            except TaskAssignmentConcurrencyError:
                results.append("concurrency_error")

        threads = [threading.Thread(target=attempt, args=(f"c{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert results.count("success") == 1
        assert results.count("concurrency_error") == 9


class TestUserIsolation:
    def test_one_users_assignment_is_not_visible_to_another(self, catalog_admin, admin: TaskRuntimeAdministration, reader: TaskRuntime, core: CoreDatabase) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        user_a = _create_user(core)
        user_b = _create_user(core)
        admin.assign_task(
            user_id=user_a, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        assert reader.get_active_assignment(user_a) is not None
        assert reader.get_active_assignment(user_b) is None


class TestGovernedWriteRequirement:
    def test_empty_consent_id_rejected_on_assign(self, catalog_admin, admin: TaskRuntimeAdministration, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        with pytest.raises(ValueError, match="assigned_via_consent_id"):
            admin.assign_task(
                user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
                assigned_via_consent_id="", now=FIXED_TIME,
            )

    def test_empty_consent_id_rejected_on_complete(self, catalog_admin, admin: TaskRuntimeAdministration, user_id: str) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        assignment = admin.assign_task(
            user_id=user_id, template_id="t1", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
            assigned_via_consent_id="c1", now=FIXED_TIME,
        )
        with pytest.raises(ValueError, match="resolved_via_consent_id"):
            admin.complete_task(assignment_id=assignment.id, resolved_via_consent_id="", now=FIXED_TIME)


class TestReadMethodsNeverWrite:
    def test_reading_repeatedly_creates_no_rows(self, reader: TaskRuntime, core: CoreDatabase, user_id: str) -> None:
        reader.get_active_assignment(user_id)
        reader.get_active_assignment(user_id)
        with core.raw_connection() as conn:
            count = conn.execute("SELECT COUNT(*) as n FROM task_assignments").fetchone()["n"]
        assert count == 0


class TestPreviewEligibilityFiltering:
    def test_get_eligible_templates_excludes_ineligible(self, catalog_admin, reader: TaskRuntime) -> None:
        _create_template(catalog_admin, template_id="t1", lock_requirement=LockRequirement.NONE)
        _create_template(catalog_admin, template_id="t2", lock_requirement=LockRequirement.REQUIRES_LOCKED)
        eligible = reader.get_eligible_templates(
            role=TaskInstanceRole.PRIMARY, operating_mode="standard", lock_knowledge_state=LockKnowledgeState.UNKNOWN,
        )
        template_ids = {t.template_id for t in eligible}
        assert template_ids == {"t1"}
