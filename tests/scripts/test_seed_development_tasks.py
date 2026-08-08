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
    def test_creates_exactly_two_templates(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        created = seed_development_templates(admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=FIXED_TIME)
        assert len(created) == 2
        assert set(created) == {"dev-seed-basic-chore", "dev-seed-locked-chore"}

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
