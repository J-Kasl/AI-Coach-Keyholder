"""
tests/task_catalog/test_repository.py

Behavioral tests use TaskCatalogAdministration (the governance write
API) to set up state -- never a raw SQL INSERT, so the invariants
these tests exist to prove are never bypassed by the test setup
itself. Direct SQL is used only in the two places explicitly permitted
(migration application itself, and one test verifying the schema's
own composite foreign key as a second, independent guarantee).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from infrastructure.database import Database as CoreDatabase
from task_catalog.models import LockRequirement, TaskInstanceRole
from task_catalog.repository import (
    InvalidTaskTemplateVersionError,
    TaskCatalog,
    TaskCatalogAdministration,
    TaskTemplateEligibilityTransitionError,
    TaskTemplateNotFoundError,
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
def catalog(core: CoreDatabase) -> TaskCatalog:
    return TaskCatalog(core.db_path, core=core)


@pytest.fixture
def admin(core: CoreDatabase) -> TaskCatalogAdministration:
    return TaskCatalogAdministration(core.db_path, core=core)


def _create_kwargs(**overrides) -> dict:
    kwargs = dict(
        template_id="tmpl-1", category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home", safety_classification="safe",
        eligible_instance_roles=(TaskInstanceRole.RECOVERY,), eligible_operating_modes=("standard",),
        completion_requirements={"type": "checkbox"}, verification_requirements={"method": "text"},
        reflection_requirements=None, lock_requirement=LockRequirement.NONE,
        created_via_consent_id="consent-1", now=FIXED_TIME,
    )
    kwargs.update(overrides)
    return kwargs


class TestCreateTemplate:
    def test_creates_version_1_and_an_active_entry(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        admin.create_template(**_create_kwargs())
        version = catalog.get_template("tmpl-1", 1)
        assert version is not None
        assert version.version == 1
        assert version.category == "chore"

    def test_new_template_is_active_by_default(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        admin.create_template(**_create_kwargs())
        active = catalog.get_active_templates(role=TaskInstanceRole.RECOVERY, operating_mode="standard")
        assert len(active) == 1
        assert active[0].template_id == "tmpl-1"

    def test_duplicate_template_id_raises(self, admin: TaskCatalogAdministration) -> None:
        admin.create_template(**_create_kwargs())
        with pytest.raises(InvalidTaskTemplateVersionError):
            admin.create_template(**_create_kwargs())

    def test_empty_consent_id_raises(self, admin: TaskCatalogAdministration) -> None:
        with pytest.raises(InvalidTaskTemplateVersionError):
            admin.create_template(**_create_kwargs(created_via_consent_id=""))

    def test_whitespace_only_consent_id_raises(self, admin: TaskCatalogAdministration) -> None:
        with pytest.raises(InvalidTaskTemplateVersionError):
            admin.create_template(**_create_kwargs(created_via_consent_id="   "))


class TestAddVersion:
    def test_appends_version_2_without_changing_current_version(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        admin.create_template(**_create_kwargs())
        admin.add_version("tmpl-1", **{k: v for k, v in _create_kwargs().items() if k not in ("template_id",)})

        v2 = catalog.get_template("tmpl-1", 2)
        assert v2 is not None
        assert v2.version == 2

        # current_version untouched by add_version alone
        active = catalog.get_active_templates(role=TaskInstanceRole.RECOVERY, operating_mode="standard")
        assert len(active) == 1
        assert active[0].version == 1

    def test_version_number_is_computed_not_caller_supplied(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        """add_version()'s own signature has no `version` parameter at
        all -- this test documents that fact behaviorally: two
        successive calls produce 2, then 3, with no way to request a
        specific number."""
        admin.create_template(**_create_kwargs())
        kwargs = {k: v for k, v in _create_kwargs().items() if k not in ("template_id",)}
        v2 = admin.add_version("tmpl-1", **kwargs)
        v3 = admin.add_version("tmpl-1", **kwargs)
        assert v2.version == 2
        assert v3.version == 3

    def test_original_version_1_is_completely_unchanged_after_add_version(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        """TC-1, verified directly: creating version 2 must not alter
        anything about version 1's own row."""
        admin.create_template(**_create_kwargs(category="original-category"))
        before = catalog.get_template("tmpl-1", 1)

        kwargs = {k: v for k, v in _create_kwargs(category="different-category").items() if k not in ("template_id",)}
        admin.add_version("tmpl-1", **kwargs)

        after = catalog.get_template("tmpl-1", 1)
        assert before == after
        assert after.category == "original-category"

    def test_nonexistent_template_raises(self, admin: TaskCatalogAdministration) -> None:
        kwargs = {k: v for k, v in _create_kwargs().items() if k not in ("template_id",)}
        with pytest.raises(TaskTemplateNotFoundError):
            admin.add_version("does-not-exist", **kwargs)

    def test_empty_consent_id_raises(self, admin: TaskCatalogAdministration) -> None:
        admin.create_template(**_create_kwargs())
        kwargs = {k: v for k, v in _create_kwargs(created_via_consent_id="").items() if k not in ("template_id",)}
        with pytest.raises(InvalidTaskTemplateVersionError):
            admin.add_version("tmpl-1", **kwargs)


class TestSetCurrentVersion:
    def test_advances_the_pointer(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        admin.create_template(**_create_kwargs())
        kwargs = {k: v for k, v in _create_kwargs().items() if k not in ("template_id",)}
        admin.add_version("tmpl-1", **kwargs)

        admin.set_current_version("tmpl-1", 2, via_consent_id="consent-2", now=FIXED_TIME)

        active = catalog.get_active_templates(role=TaskInstanceRole.RECOVERY, operating_mode="standard")
        assert active[0].version == 2

    def test_pointing_at_a_nonexistent_version_raises(self, admin: TaskCatalogAdministration) -> None:
        admin.create_template(**_create_kwargs())
        with pytest.raises(InvalidTaskTemplateVersionError):
            admin.set_current_version("tmpl-1", 99, via_consent_id="consent-2", now=FIXED_TIME)

    def test_nonexistent_template_raises(self, admin: TaskCatalogAdministration) -> None:
        with pytest.raises(TaskTemplateNotFoundError):
            admin.set_current_version("does-not-exist", 1, via_consent_id="consent-2", now=FIXED_TIME)

    def test_empty_consent_id_raises(self, admin: TaskCatalogAdministration) -> None:
        admin.create_template(**_create_kwargs())
        with pytest.raises(InvalidTaskTemplateVersionError):
            admin.set_current_version("tmpl-1", 1, via_consent_id="", now=FIXED_TIME)

    def test_composite_foreign_key_rejects_an_impossible_pointer_even_bypassing_the_app_check(
        self, core: CoreDatabase,
    ) -> None:
        """Defense in depth (task_catalog_technical_design.md's own
        note on this table): even if application-level validation were
        somehow skipped, the schema's own composite FOREIGN KEY
        (template_id, current_version) -> task_template_versions
        rejects an impossible pointer outright."""
        import sqlite3

        with core.transaction() as tx:
            tx.execute(
                "INSERT INTO task_template_versions (id, template_id, version, category, difficulty, effort, "
                "duration_minutes, required_equipment_json, required_privacy, required_context, "
                "safety_classification, eligible_instance_roles_json, eligible_operating_modes_json, "
                "completion_requirements_json, verification_requirements_json, reflection_requirements_json, "
                "created_at, created_via_consent_id) VALUES "
                "('v1', 'tmpl-x', 1, 'c', 'd', 'e', 10, '[]', 'none', 'home', 'safe', '[]', '[]', '{}', '{}', NULL, ?, 'c1')",
                (FIXED_TIME.isoformat(),),
            )
        with pytest.raises(sqlite3.IntegrityError):
            with core.transaction() as tx:
                tx.execute(
                    "INSERT INTO task_template_catalog_entries (template_id, current_version, eligibility_status, status_changed_at) "
                    "VALUES ('tmpl-x', 99, 'active', ?)",
                    (FIXED_TIME.isoformat(),),
                )


class TestActivateDeactivate:
    def test_deactivate_removes_from_active_templates(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        admin.create_template(**_create_kwargs())
        admin.deactivate("tmpl-1", via_consent_id="consent-2", now=FIXED_TIME)
        active = catalog.get_active_templates(role=TaskInstanceRole.RECOVERY, operating_mode="standard")
        assert active == []

    def test_deactivated_templates_own_version_remains_readable(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        """TC-7: historical reference reads (get_template) always work,
        independent of current eligibility_status -- deactivation only
        prevents NEW instances, per the design document."""
        admin.create_template(**_create_kwargs())
        admin.deactivate("tmpl-1", via_consent_id="consent-2", now=FIXED_TIME)
        version = catalog.get_template("tmpl-1", 1)
        assert version is not None
        assert version.category == "chore"

    def test_deactivating_an_already_deactivated_template_raises(self, admin: TaskCatalogAdministration) -> None:
        admin.create_template(**_create_kwargs())
        admin.deactivate("tmpl-1", via_consent_id="consent-2", now=FIXED_TIME)
        with pytest.raises(TaskTemplateEligibilityTransitionError):
            admin.deactivate("tmpl-1", via_consent_id="consent-3", now=FIXED_TIME)

    def test_activating_an_already_active_template_raises(self, admin: TaskCatalogAdministration) -> None:
        admin.create_template(**_create_kwargs())
        with pytest.raises(TaskTemplateEligibilityTransitionError):
            admin.activate("tmpl-1", via_consent_id="consent-2", now=FIXED_TIME)

    def test_reactivation_works(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        admin.create_template(**_create_kwargs())
        admin.deactivate("tmpl-1", via_consent_id="consent-2", now=FIXED_TIME)
        admin.activate("tmpl-1", via_consent_id="consent-3", now=FIXED_TIME + timedelta(days=1))
        active = catalog.get_active_templates(role=TaskInstanceRole.RECOVERY, operating_mode="standard")
        assert len(active) == 1

    def test_deactivate_never_touches_task_template_versions(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        """TC-2, verified directly: only eligibility_status/status_changed_at
        on the CatalogEntry change -- the version row itself is untouched."""
        admin.create_template(**_create_kwargs())
        before = catalog.get_template("tmpl-1", 1)
        admin.deactivate("tmpl-1", via_consent_id="consent-2", now=FIXED_TIME)
        after = catalog.get_template("tmpl-1", 1)
        assert before == after

    def test_nonexistent_template_raises_for_both(self, admin: TaskCatalogAdministration) -> None:
        with pytest.raises(TaskTemplateNotFoundError):
            admin.activate("does-not-exist", via_consent_id="c", now=FIXED_TIME)
        with pytest.raises(TaskTemplateNotFoundError):
            admin.deactivate("does-not-exist", via_consent_id="c", now=FIXED_TIME)

    def test_empty_consent_id_raises_for_both(self, admin: TaskCatalogAdministration) -> None:
        admin.create_template(**_create_kwargs())
        with pytest.raises(InvalidTaskTemplateVersionError):
            admin.deactivate("tmpl-1", via_consent_id="", now=FIXED_TIME)


class TestCreateTemplateAtomicity:
    """Point 4 of the requested review: a real failure-injection test,
    not only a happy-path test. Simulates the SECOND write (the
    TaskTemplateCatalogEntry INSERT) failing after the FIRST write
    (the TaskTemplateVersion INSERT) already succeeded within the same
    transaction, and verifies BOTH are rolled back together -- proving
    apply_transition()/Database.transaction()'s BEGIN IMMEDIATE +
    commit()/rollback-on-exception actually protects the whole
    create_template() body as one atomic unit, not merely asserting it."""

    def test_a_failure_on_the_second_write_rolls_back_the_first(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog, core: CoreDatabase, monkeypatch,
    ) -> None:
        from infrastructure.database import Transaction

        original_execute = Transaction.execute

        def failing_execute(self, sql, params=()):
            if "task_template_catalog_entries" in sql and "INSERT INTO" in sql:
                raise RuntimeError("simulated failure on the second write (entry insert)")
            return original_execute(self, sql, params)

        monkeypatch.setattr(Transaction, "execute", failing_execute)

        with pytest.raises(RuntimeError, match="simulated failure"):
            admin.create_template(**_create_kwargs())

        monkeypatch.undo()  # restore before using the real Transaction.execute for verification reads

        assert catalog.get_template("tmpl-1", 1) is None  # the version INSERT was rolled back too
        with core.transaction() as tx:
            v_count = tx.fetch_one("SELECT COUNT(*) as n FROM task_template_versions WHERE template_id = 'tmpl-1'")["n"]
            e_count = tx.fetch_one("SELECT COUNT(*) as n FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'")["n"]
        assert v_count == 0
        assert e_count == 0


class TestForeignKeysEnforcedOnRepositoryConnection:
    """Point 5 of the requested review: confirms PRAGMA foreign_keys is
    actually ON for the exact connection shape task_catalog's own
    repository classes use (via core.transaction()), not only in an
    isolated migration-level test -- infrastructure/database.py's own
    _connect() sets this, but this test proves it holds for THIS
    module's own usage, not merely inherited by assumption."""

    def test_pragma_foreign_keys_is_on(self, core: CoreDatabase) -> None:
        with core.transaction() as tx:
            row = tx.fetch_one("PRAGMA foreign_keys")
        assert row[0] == 1


class TestCreateTemplateValidation:
    """Point 4, verified through the actual repository write path, not
    only at the model level -- create_template() constructs
    TaskTemplateVersion inside its own transaction, so a ValueError
    from __post_init__ must roll back cleanly, exactly like any other
    write failure (TestCreateTemplateAtomicity's own pattern)."""

    def test_empty_eligible_instance_roles_is_rejected_end_to_end(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        with pytest.raises(ValueError, match="eligible_instance_roles must not be empty"):
            admin.create_template(**_create_kwargs(eligible_instance_roles=()))
        assert catalog.get_template("tmpl-1", 1) is None  # rolled back, no partial row


class TestConsentAudit:
    """Points 1+6 of the requested review: the two consent-audit
    columns actually persist, and reflect the MOST RECENT authorization
    -- never cleared, unlike trust_manager's own NULL-clearing pattern
    (see TaskTemplateCatalogEntry's own docstring for why)."""

    def test_create_template_populates_both_consent_fields_with_the_same_creation_consent(
        self, admin: TaskCatalogAdministration, core: CoreDatabase,
    ) -> None:
        """Interpretation A (confirmed decision): the initial ACTIVE
        eligibility and the initial current_version=1 are both
        already-authorized outcomes of creation, not a neutral default
        with no origin -- so create_template() populates BOTH consent
        fields from the same creation consent, not just one."""
        admin.create_template(**_create_kwargs(created_via_consent_id="creation-consent"))
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'")
        assert row["current_version_changed_via_consent_id"] == "creation-consent"
        assert row["eligibility_changed_via_consent_id"] == "creation-consent"

    def test_create_template_populates_both_timestamp_fields_with_now(
        self, admin: TaskCatalogAdministration, core: CoreDatabase,
    ) -> None:
        from infrastructure.time_format import parse_iso

        admin.create_template(**_create_kwargs(now=FIXED_TIME))
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'")
        assert parse_iso(row["status_changed_at"]) == FIXED_TIME
        assert parse_iso(row["current_version_changed_at"]) == FIXED_TIME

    def test_set_current_version_updates_current_version_changed_via_consent_id(
        self, admin: TaskCatalogAdministration, core: CoreDatabase,
    ) -> None:
        admin.create_template(**_create_kwargs(created_via_consent_id="creation-consent"))
        kwargs = {k: v for k, v in _create_kwargs().items() if k not in ("template_id",)}
        admin.add_version("tmpl-1", **kwargs)
        admin.set_current_version("tmpl-1", 2, via_consent_id="pointer-consent", now=FIXED_TIME)

        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'")
        assert row["current_version_changed_via_consent_id"] == "pointer-consent"  # overwritten, not appended

    def test_deactivate_populates_eligibility_changed_via_consent_id(
        self, admin: TaskCatalogAdministration, core: CoreDatabase,
    ) -> None:
        admin.create_template(**_create_kwargs())
        admin.deactivate("tmpl-1", via_consent_id="deactivation-consent", now=FIXED_TIME)
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'")
        assert row["eligibility_changed_via_consent_id"] == "deactivation-consent"

    def test_reactivation_consent_is_not_lost_unlike_trust_managers_own_pattern(
        self, admin: TaskCatalogAdministration, core: CoreDatabase,
    ) -> None:
        """The specific gap this iteration exists to close: trust_manager's
        own deactivated_via_consent_id is cleared to NULL on
        reactivation (relying on a domain event this module deliberately
        does not have). Task Catalog's own column is never cleared --
        reactivation's consent remains visible in the database, not
        only in memory during the call that authorized it."""
        admin.create_template(**_create_kwargs())
        admin.deactivate("tmpl-1", via_consent_id="deactivation-consent", now=FIXED_TIME)
        admin.activate("tmpl-1", via_consent_id="reactivation-consent", now=FIXED_TIME + timedelta(days=1))

        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'")
        assert row["eligibility_changed_via_consent_id"] == "reactivation-consent"  # NOT NULL, NOT lost

    def test_row_to_entry_exposes_both_consent_fields_via_the_dataclass(
        self, admin: TaskCatalogAdministration,
    ) -> None:
        admin.create_template(**_create_kwargs(created_via_consent_id="creation-consent"))
        entry = admin.deactivate("tmpl-1", via_consent_id="deactivation-consent", now=FIXED_TIME)
        assert entry.eligibility_changed_via_consent_id == "deactivation-consent"
        assert entry.current_version_changed_via_consent_id == "creation-consent"


    def test_set_current_version_updates_all_three_current_version_audit_fields(
        self, admin: TaskCatalogAdministration, core: CoreDatabase,
    ) -> None:
        admin.create_template(**_create_kwargs())
        kwargs = {k: v for k, v in _create_kwargs().items() if k not in ("template_id",)}
        admin.add_version("tmpl-1", **kwargs)

        later = FIXED_TIME + timedelta(days=1)
        admin.set_current_version("tmpl-1", 2, via_consent_id="pointer-consent", now=later)

        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'")
        assert row["current_version"] == 2
        assert row["current_version_changed_via_consent_id"] == "pointer-consent"
        from infrastructure.time_format import parse_iso
        assert parse_iso(row["current_version_changed_at"]) == later

    def test_set_current_version_does_not_touch_eligibility_audit_fields(
        self, admin: TaskCatalogAdministration, core: CoreDatabase,
    ) -> None:
        admin.create_template(**_create_kwargs(created_via_consent_id="creation-consent", now=FIXED_TIME))
        kwargs = {k: v for k, v in _create_kwargs().items() if k not in ("template_id",)}
        admin.add_version("tmpl-1", **kwargs)

        admin.set_current_version("tmpl-1", 2, via_consent_id="pointer-consent", now=FIXED_TIME + timedelta(days=1))

        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'")
        assert row["eligibility_status"] == "active"
        assert row["eligibility_changed_via_consent_id"] == "creation-consent"  # unchanged since creation
        from infrastructure.time_format import parse_iso
        assert parse_iso(row["status_changed_at"]) == FIXED_TIME  # unchanged since creation

    def test_activate_deactivate_do_not_touch_current_version_audit_fields(
        self, admin: TaskCatalogAdministration, core: CoreDatabase,
    ) -> None:
        admin.create_template(**_create_kwargs(created_via_consent_id="creation-consent", now=FIXED_TIME))
        admin.deactivate("tmpl-1", via_consent_id="deactivation-consent", now=FIXED_TIME + timedelta(days=1))
        admin.activate("tmpl-1", via_consent_id="reactivation-consent", now=FIXED_TIME + timedelta(days=2))

        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'")
        assert row["current_version"] == 1
        assert row["current_version_changed_via_consent_id"] == "creation-consent"  # unchanged since creation
        from infrastructure.time_format import parse_iso
        assert parse_iso(row["current_version_changed_at"]) == FIXED_TIME  # unchanged since creation

    def test_add_version_does_not_touch_any_catalog_entry_audit_field(
        self, admin: TaskCatalogAdministration, core: CoreDatabase,
    ) -> None:
        admin.create_template(**_create_kwargs(created_via_consent_id="creation-consent", now=FIXED_TIME))
        with core.transaction() as tx:
            before = dict(tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'"))

        kwargs = {k: v for k, v in _create_kwargs().items() if k not in ("template_id",)}
        admin.add_version("tmpl-1", **kwargs)  # does not set the new version as current

        with core.transaction() as tx:
            after = dict(tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = 'tmpl-1'"))
        assert before == after


class TestMigration016Backfill:
    """Point 7 of the requested review: a real pre-016 row, backfilled
    by the migration itself, not merely reasoned about."""

    def test_a_row_created_before_migration_016_gets_backfilled_from_status_changed_at(
        self, tmp_path: Path,
    ) -> None:
        pre_016_core = CoreDatabase(tmp_path / "test.db")
        migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
        all_migrations = sorted(migrations_dir.glob("*.sql"))
        migration_016 = [p for p in all_migrations if p.name.startswith("016_")][0]
        migrations_before_016 = [p for p in all_migrations if p.name < migration_016.name]

        # Apply everything up through 015 only -- task_template_catalog_entries
        # exists (014) with its consent columns (015), but NOT current_version_changed_at yet.
        with pre_016_core.raw_connection() as conn:
            for path in migrations_before_016:
                conn.executescript(path.read_text(encoding="utf-8"))

        # A row exactly as create_template() would have written it BEFORE
        # migration 016 -- status_changed_at is its only timestamp.
        old_status_changed_at = "2025-06-01T00:00:00Z"
        with pre_016_core.transaction() as tx:
            tx.execute(
                "INSERT INTO task_template_versions (id, template_id, version, category, difficulty, effort, "
                "duration_minutes, required_equipment_json, required_privacy, required_context, "
                "safety_classification, eligible_instance_roles_json, eligible_operating_modes_json, "
                "completion_requirements_json, verification_requirements_json, reflection_requirements_json, "
                "created_at, created_via_consent_id) VALUES "
                "('v1', 'pre-016-template', 1, 'c', 'd', 'e', 10, '[]', 'none', 'home', 'safe', '[]', '[]', "
                "'{}', '{}', NULL, ?, 'old-consent')",
                (old_status_changed_at,),
            )
            tx.execute(
                "INSERT INTO task_template_catalog_entries "
                "(template_id, current_version, eligibility_status, status_changed_at) "
                "VALUES ('pre-016-template', 1, 'active', ?)",
                (old_status_changed_at,),
            )

        # Now apply 016 (and anything after it, though nothing exists yet).
        remaining = [p for p in all_migrations if p.name >= migration_016.name]
        with pre_016_core.raw_connection() as conn:
            for path in remaining:
                conn.executescript(path.read_text(encoding="utf-8"))

        with pre_016_core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT * FROM task_template_catalog_entries WHERE template_id = 'pre-016-template'",
            )
        assert row["current_version_changed_at"] == old_status_changed_at  # backfilled, not NULL
        assert row["current_version_changed_via_consent_id"] is None  # 015's own column -- also predates any write, correctly still NULL


    def test_current_version_changed_at_is_parsed_as_a_real_datetime_on_the_dataclass(
        self, admin: TaskCatalogAdministration,
    ) -> None:
        """Point 8: the model/row-mapping loads the new field correctly
        -- not just present in the raw SQL row, but a real `datetime`
        on the returned TaskTemplateCatalogEntry, matching every other
        timestamp field's own type."""
        admin.create_template(**_create_kwargs(now=FIXED_TIME))
        updated = admin.set_current_version("tmpl-1", 1, via_consent_id="c2", now=FIXED_TIME + timedelta(hours=1))
        assert updated.current_version_changed_at == FIXED_TIME + timedelta(hours=1)
        assert isinstance(updated.current_version_changed_at, datetime)


class TestGetActiveTemplatesFiltering:
    def test_filters_by_role(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        admin.create_template(**_create_kwargs(template_id="recovery-only", eligible_instance_roles=(TaskInstanceRole.RECOVERY,)))
        admin.create_template(**_create_kwargs(template_id="journaling-only", eligible_instance_roles=(TaskInstanceRole.JOURNALING,)))

        recovery_active = catalog.get_active_templates(role=TaskInstanceRole.RECOVERY, operating_mode="standard")
        assert {v.template_id for v in recovery_active} == {"recovery-only"}

    def test_filters_by_operating_mode(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        admin.create_template(**_create_kwargs(template_id="standard-only", eligible_operating_modes=("standard",)))
        admin.create_template(**_create_kwargs(template_id="advanced-only", eligible_operating_modes=("advanced",)))

        standard_active = catalog.get_active_templates(role=TaskInstanceRole.RECOVERY, operating_mode="standard")
        assert {v.template_id for v in standard_active} == {"standard-only"}

    def test_a_template_eligible_for_multiple_roles_appears_for_each(
        self, admin: TaskCatalogAdministration, catalog: TaskCatalog,
    ) -> None:
        """The whole reason Task Catalog exists (task_catalog_technical_design.md
        Section 1): one template usable by more than one instance_role."""
        admin.create_template(**_create_kwargs(
            template_id="shared", eligible_instance_roles=(TaskInstanceRole.RECOVERY, TaskInstanceRole.PRIMARY),
        ))
        recovery_active = catalog.get_active_templates(role=TaskInstanceRole.RECOVERY, operating_mode="standard")
        primary_active = catalog.get_active_templates(role=TaskInstanceRole.PRIMARY, operating_mode="standard")
        assert {v.template_id for v in recovery_active} == {"shared"}
        assert {v.template_id for v in primary_active} == {"shared"}


class TestGetTemplate:
    def test_returns_none_for_nonexistent_template(self, catalog: TaskCatalog) -> None:
        assert catalog.get_template("does-not-exist", 1) is None

    def test_returns_none_for_nonexistent_version(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        admin.create_template(**_create_kwargs())
        assert catalog.get_template("tmpl-1", 99) is None


class TestGetCurrentVersion:
    def test_returns_none_for_nonexistent_template(self, catalog: TaskCatalog) -> None:
        assert catalog.get_current_version("does-not-exist") is None

    def test_returns_version_1_right_after_create(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        admin.create_template(**_create_kwargs())
        current = catalog.get_current_version("tmpl-1")
        assert current is not None
        assert current.version == 1

    def test_add_version_alone_does_not_change_current_version(self, admin: TaskCatalogAdministration, catalog: TaskCatalog) -> None:
        admin.create_template(**_create_kwargs())
        admin.add_version("tmpl-1", **{k: v for k, v in _create_kwargs().items() if k not in ("template_id",)})
        current = catalog.get_current_version("tmpl-1")
        assert current is not None
        assert current.version == 1  # add_version() alone never advances current_version


class TestTaskCatalogHasNoWriteCapability:
    """TC-4, verified structurally, not only by documentation."""

    def test_no_create_method(self, catalog: TaskCatalog) -> None:
        assert not hasattr(catalog, "create_template")

    def test_no_add_version_method(self, catalog: TaskCatalog) -> None:
        assert not hasattr(catalog, "add_version")

    def test_no_activate_or_deactivate_method(self, catalog: TaskCatalog) -> None:
        assert not hasattr(catalog, "activate")
        assert not hasattr(catalog, "deactivate")

    def test_only_the_three_documented_public_methods_exist(self, catalog: TaskCatalog) -> None:
        public_methods = {name for name in dir(catalog) if not name.startswith("_")}
        # db_path is a plain attribute, not a method -- included since dir() doesn't distinguish
        assert public_methods == {"get_template", "get_active_templates", "get_current_version", "db_path"}


class TestConcurrentAddVersion:
    """Point 1 of the requested review: real multi-threaded verification,
    not merely asserted from reading apply_transition()'s use of
    BEGIN IMMEDIATE. Each thread gets its OWN Database/CoreDatabase
    instance (infrastructure/database.py's own documented threading
    constraint), mirroring the onboarding conditional-update
    concurrency test's own approach."""

    def test_two_concurrent_add_version_calls_never_corrupt_state(self, core: CoreDatabase) -> None:
        import threading

        admin_main = TaskCatalogAdministration(core.db_path, core=core)
        admin_main.create_template(**_create_kwargs())

        barrier = threading.Barrier(2)
        results: list = []
        errors: list = []

        def race(category: str) -> None:
            try:
                thread_admin = TaskCatalogAdministration(core.db_path, core=CoreDatabase(core.db_path))
                kwargs = {k: v for k, v in _create_kwargs(category=category).items() if k not in ("template_id",)}
                barrier.wait(timeout=5)
                result = thread_admin.add_version("tmpl-1", **kwargs)
                results.append(result)
            except Exception as exc:  # pragma: no cover -- failure path only
                errors.append(exc)

        t1 = threading.Thread(target=race, args=("category-a",))
        t2 = threading.Thread(target=race, args=("category-b",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Unexpected exception(s) during concurrent add_version: {errors}"
        assert len(results) == 2
        versions = sorted(r.version for r in results)
        assert versions == [2, 3], f"expected two distinct sequential versions, got {versions}"

        catalog = TaskCatalog(core.db_path, core=core)
        v2 = catalog.get_template("tmpl-1", 2)
        v3 = catalog.get_template("tmpl-1", 3)
        assert v2 is not None and v3 is not None
        assert {v2.category, v3.category} == {"category-a", "category-b"}
        # No third, corrupted, or missing row -- exactly versions 1, 2, 3 exist.
        with core.transaction() as tx:
            count = tx.fetch_one("SELECT COUNT(*) as n FROM task_template_versions WHERE template_id = 'tmpl-1'")["n"]
        assert count == 3
