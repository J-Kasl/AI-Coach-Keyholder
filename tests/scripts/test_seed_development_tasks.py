"""tests/scripts/test_seed_development_tasks.py"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from infrastructure.database import Database as CoreDatabase
from scripts.seed_development_tasks import DEV_SEED_CONSENT_ID, seed_development_templates
from task_catalog.models import LockRequirement
from task_catalog.repository import TaskCatalog, TaskCatalogAdministration

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
def admin(core: CoreDatabase) -> TaskCatalogAdministration:
    return TaskCatalogAdministration(core.db_path, core=core)


@pytest.fixture
def catalog(core: CoreDatabase) -> TaskCatalog:
    return TaskCatalog(core.db_path, core=core)


class TestFirstRun:
    def test_creates_all_twelve_templates(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        created = seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        assert len(created) == 12
        assert set(created) == {
            "dev-seed-basic-chore", "dev-seed-locked-chore",
            "tidy-one-surface", "clear-one-inbox", "five-minute-declutter", "plan-tomorrow",
            "evening-reset", "focused-work-block", "finish-one-postponed-task", "short-walk",
            "evening-journal-entry", "locked-reflection-entry",
        }

    def test_basic_chore_has_no_lock_requirement(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        template = catalog.get_current_version("dev-seed-basic-chore")
        assert template is not None
        assert template.lock_requirement == LockRequirement.NONE

    def test_locked_chore_requires_locked(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        template = catalog.get_current_version("dev-seed-locked-chore")
        assert template is not None
        assert template.lock_requirement == LockRequirement.REQUIRES_LOCKED

    def test_consent_id_is_always_the_system_marker(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        template = catalog.get_current_version("dev-seed-basic-chore")
        assert template is not None
        assert template.created_via_consent_id == "system:dev_seed"
        assert template.created_via_consent_id.strip() != ""


class TestIdempotence:
    def test_second_run_creates_nothing(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        created_second_time = seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        assert created_second_time == []

    def test_second_run_does_not_create_a_second_version(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        template = catalog.get_current_version("dev-seed-basic-chore")
        assert template is not None
        assert template.version == 1


class TestFirstDevelopmentCatalogContent:
    """Task Catalog Content Proposal, Option A -- the ten newly
    approved templates alongside the two original dev-seed-* fixtures."""

    NEW_NONE_TEMPLATE_IDS = (
        "tidy-one-surface", "clear-one-inbox", "five-minute-declutter", "plan-tomorrow",
        "evening-reset", "focused-work-block", "finish-one-postponed-task", "short-walk",
        "evening-journal-entry",
    )

    def test_original_two_dev_seed_templates_are_preserved_unchanged(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        basic = catalog.get_current_version("dev-seed-basic-chore")
        locked = catalog.get_current_version("dev-seed-locked-chore")
        assert basic is not None and basic.title == "Tidy one surface" and basic.lock_requirement == LockRequirement.NONE
        assert locked is not None and locked.title == "Tidy one surface (locked-only)" and locked.lock_requirement == LockRequirement.REQUIRES_LOCKED

    def test_all_nine_new_none_templates_have_no_lock_requirement(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        for template_id in self.NEW_NONE_TEMPLATE_IDS:
            template = catalog.get_current_version(template_id)
            assert template is not None, f"{template_id} was not created"
            assert template.lock_requirement == LockRequirement.NONE, f"{template_id} should be NONE"

    def test_locked_reflection_entry_is_the_only_new_requires_locked_template(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        template = catalog.get_current_version("locked-reflection-entry")
        assert template is not None
        assert template.lock_requirement == LockRequirement.REQUIRES_LOCKED
        assert "not on independent physical verification" in template.instructions

    def test_short_walk_is_present_with_approved_content(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        template = catalog.get_current_version("short-walk")
        assert template is not None
        assert template.title == "Take a short walk"
        assert template.category == "movement"
        assert template.duration_minutes == 10
        assert template.lock_requirement == LockRequirement.NONE
        assert template.completion_requirements == {"description": "A short, ordinary walk was completed."}

    def test_exact_title_instructions_and_completion_requirements_survive_persistence(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        """Spot-checks the full approved content, not just presence,
        for a representative NONE task and the one REQUIRES_LOCKED
        task -- proving the exact strings survived validation and
        round-tripped through the real write/read path unchanged."""
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)

        plan_tomorrow = catalog.get_current_version("plan-tomorrow")
        assert plan_tomorrow is not None
        assert plan_tomorrow.title == "Plan tomorrow in three lines"
        assert plan_tomorrow.instructions == (
            "Write down the three most important things you want to get done "
            "tomorrow. Keep it short -- one line each. Put the list somewhere you "
            "will actually see it in the morning."
        )
        assert plan_tomorrow.category == "planning"
        assert plan_tomorrow.difficulty == "easy"
        assert plan_tomorrow.duration_minutes == 10
        assert plan_tomorrow.completion_requirements == {"description": "A three-item plan for tomorrow has been written down."}

        locked_reflection = catalog.get_current_version("locked-reflection-entry")
        assert locked_reflection is not None
        assert locked_reflection.title == "Reflection journal entry (locked-only)"
        assert locked_reflection.instructions == (
            "Spend ten minutes writing about how you're feeling about your "
            "current commitment today -- what's easy, what's hard, and one thing "
            "you're proud of. This task is only offered while you have reported "
            "yourself as locked; that eligibility is based on your user-reported "
            "lock state, not on independent physical verification."
        )
        assert locked_reflection.category == "reflection"
        assert locked_reflection.duration_minutes == 10
        assert locked_reflection.completion_requirements == {"description": "One reflection journal entry was written."}

    def test_second_run_creates_none_of_the_new_templates_either(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        created_second_time = seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        assert created_second_time == []
