"""
task_catalog/repository.py

docs/architecture/task_catalog_technical_design.md (draft, not approved
for implementation as a whole -- see task_catalog/README.md for the
exact boundary this module implements).

Two structurally separate public classes, per explicit review
decision -- not a convention that exists elsewhere in this project
(every other domain module today is a single class mixing read and
write; this is the first module to split them), adopted here because
Task Catalog's own design document (TC-4) requires ordinary consumers
to have no write capability at all, not merely a documented
convention not to use one:

- `TaskCatalog` -- read-only, for any future consumer (recovery_plan
  today reads nothing from here yet; this module has no consumers
  wired in as of this slice). No write method exists on this class,
  full stop.
- `TaskCatalogAdministration` -- critical_change-governed catalog
  management. Every method requires a non-empty consent reference --
  `created_via_consent_id` for the two methods that create a new
  immutable entity (`create_template`, `add_version`), `via_consent_id`
  for the three that change mutable state on an existing entry
  (`set_current_version`, `activate`, `deactivate`), matching
  `trust_manager`'s own established split between the two kinds of
  operation. Never imported by an ordinary runtime consumer -- a
  caller holding only a `TaskCatalog` reference has no path to write
  capability at all.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from infrastructure.database import Database as CoreDatabase
from infrastructure.database import Transaction, apply_transition
from infrastructure.time_format import iso as _iso
from infrastructure.time_format import parse_iso as _parse_iso
from task_catalog.models import (
    LockRequirement,
    TaskInstanceRole,
    TaskTemplateCatalogEntry,
    TaskTemplateEligibilityStatus,
    TaskTemplateVersion,
)

__all__ = [
    "TaskCatalog",
    "TaskCatalogAdministration",
    "TaskTemplateNotFoundError",
    "InvalidTaskTemplateVersionError",
    "TaskTemplateEligibilityTransitionError",
]


class TaskTemplateNotFoundError(LookupError):
    """Raised by TaskCatalogAdministration methods that require an
    existing template_id (add_version, set_current_version, activate,
    deactivate) -- TaskCatalog's own read methods return None instead,
    matching the read-API convention every other module in this
    project already uses for "not found"."""


class InvalidTaskTemplateVersionError(ValueError):
    """A malformed or invalid TaskTemplateVersion write was attempted
    -- e.g. an empty created_via_consent_id, or set_current_version
    pointed at a version that doesn't exist for that template_id."""


class TaskTemplateEligibilityTransitionError(ValueError):
    """activate()/deactivate() called on an entry already in that
    exact state -- the same "guard against a transition to the same
    state" discipline recovery_plan's own Phase 2.8 review already
    established for RecoveryTask."""


def _row_to_version(row) -> TaskTemplateVersion:
    return TaskTemplateVersion(
        id=row["id"], template_id=row["template_id"], version=row["version"],
        category=row["category"], difficulty=row["difficulty"], effort=row["effort"],
        duration_minutes=row["duration_minutes"],
        required_equipment=tuple(json.loads(row["required_equipment_json"])),
        required_privacy=row["required_privacy"], required_context=row["required_context"],
        safety_classification=row["safety_classification"],
        eligible_instance_roles=tuple(TaskInstanceRole(r) for r in json.loads(row["eligible_instance_roles_json"])),
        eligible_operating_modes=tuple(json.loads(row["eligible_operating_modes_json"])),
        completion_requirements=json.loads(row["completion_requirements_json"]),
        verification_requirements=json.loads(row["verification_requirements_json"]),
        reflection_requirements=(
            json.loads(row["reflection_requirements_json"]) if row["reflection_requirements_json"] is not None else None
        ),
        lock_requirement=LockRequirement(row["lock_requirement"]),
        created_at=_parse_iso(row["created_at"]), created_via_consent_id=row["created_via_consent_id"],
    )


def _row_to_entry(row) -> TaskTemplateCatalogEntry:
    return TaskTemplateCatalogEntry(
        template_id=row["template_id"], current_version=row["current_version"],
        eligibility_status=TaskTemplateEligibilityStatus(row["eligibility_status"]),
        status_changed_at=_parse_iso(row["status_changed_at"]),
        eligibility_changed_via_consent_id=row["eligibility_changed_via_consent_id"],
        current_version_changed_via_consent_id=row["current_version_changed_via_consent_id"],
        current_version_changed_at=(
            _parse_iso(row["current_version_changed_at"]) if row["current_version_changed_at"] is not None else None
        ),
    )


def _insert_version(tx: Transaction, version: TaskTemplateVersion) -> None:
    """Shared by TaskCatalogAdministration.create_template()/add_version()
    -- the only place an INSERT into task_template_versions happens.
    Never an UPDATE, never a DELETE, against this table, anywhere in
    this module (TC-1)."""
    tx.execute(
        """
        INSERT INTO task_template_versions
            (id, template_id, version, category, difficulty, effort, duration_minutes,
             required_equipment_json, required_privacy, required_context, safety_classification,
             eligible_instance_roles_json, eligible_operating_modes_json,
             completion_requirements_json, verification_requirements_json, reflection_requirements_json,
             lock_requirement, created_at, created_via_consent_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version.id, version.template_id, version.version, version.category, version.difficulty,
            version.effort, version.duration_minutes,
            json.dumps(list(version.required_equipment)), version.required_privacy, version.required_context,
            version.safety_classification,
            json.dumps([r.value for r in version.eligible_instance_roles]),
            json.dumps(list(version.eligible_operating_modes)),
            json.dumps(version.completion_requirements), json.dumps(version.verification_requirements),
            json.dumps(version.reflection_requirements) if version.reflection_requirements is not None else None,
            version.lock_requirement.value,
            _iso(version.created_at), version.created_via_consent_id,
        ),
    )


def _require_consent_id(consent_id: str) -> None:
    if not consent_id or not consent_id.strip():
        raise InvalidTaskTemplateVersionError(
            "A non-empty consent reference is required -- every Task Catalog "
            "write is critical_change-governed (task_catalog_technical_design.md TC-4)."
        )


class TaskCatalog:
    """
    Read-only (TC-4). No write method exists on this class at all --
    not merely discouraged, structurally absent, so a caller holding
    only a `TaskCatalog` reference has no way to reach write capability.
    """

    def __init__(self, db_path: str | Path, *, core: CoreDatabase | None = None) -> None:
        self.db_path = Path(db_path)
        self._core = core if core is not None else CoreDatabase(self.db_path)

    def get_template(self, template_id: str, version: int) -> TaskTemplateVersion | None:
        with self._core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT * FROM task_template_versions WHERE template_id = ? AND version = ?",
                (template_id, version),
            )
        return _row_to_version(row) if row is not None else None

    def get_current_version(self, template_id: str) -> TaskTemplateVersion | None:
        """The template's current version, per its own
        TaskTemplateCatalogEntry.current_version pointer -- `None` if
        no entry exists for this template_id at all. Added for
        task_runtime's own benefit (resolving which version to assign)
        -- a small, read-only addition, no change to TC-1/TC-2's own
        governance model."""
        with self._core.transaction() as tx:
            row = tx.fetch_one(
                """
                SELECT v.* FROM task_template_versions v
                JOIN task_template_catalog_entries e
                    ON e.template_id = v.template_id AND e.current_version = v.version
                WHERE v.template_id = ?
                """,
                (template_id,),
            )
        return _row_to_version(row) if row is not None else None

    def get_active_templates(
        self, *, role: TaskInstanceRole, operating_mode: str,
    ) -> list[TaskTemplateVersion]:
        """
        Only the CURRENT version of ACTIVE entries, filtered further to
        those whose eligible_instance_roles/eligible_operating_modes
        include the given values. Role/mode membership is checked in
        Python, not SQL -- both are JSON-encoded columns, and this
        catalog is expected to stay small (a reference table of task
        templates, not user data), so a full scan of active current
        versions is a reasonable, honest trade-off over a fragile
        JSON-in-SQL membership query.
        """
        with self._core.transaction() as tx:
            rows = tx.fetch_all(
                """
                SELECT v.* FROM task_template_versions v
                JOIN task_template_catalog_entries e
                    ON e.template_id = v.template_id AND e.current_version = v.version
                WHERE e.eligibility_status = ?
                """,
                (TaskTemplateEligibilityStatus.ACTIVE.value,),
            )
        versions = [_row_to_version(row) for row in rows]
        return [
            v for v in versions
            if role in v.eligible_instance_roles and operating_mode in v.eligible_operating_modes
        ]


class TaskCatalogAdministration:
    """
    critical_change-governed catalog management (TC-4's "outside this
    document's own runtime API surface"). Never imported by an
    ordinary runtime consumer.

    Two distinctly-named consent parameters, matching `trust_manager`'s
    own established split between the two kinds of write operation:
    - `created_via_consent_id` (`create_template`, `add_version`) --
      creating a new immutable `TaskTemplateVersion`.
    - `via_consent_id` (`set_current_version`, `activate`, `deactivate`)
      -- changing mutable state on an already-existing
      `TaskTemplateCatalogEntry`, exactly `trust_manager.deactivate_domain()`/
      `reactivate_domain()`'s own parameter name for the same kind of
      operation. Persisted differently than `trust_manager`, though --
      see `TaskTemplateCatalogEntry`'s own docstring for why (no domain
      events here to fall back on).
    """

    def __init__(self, db_path: str | Path, *, core: CoreDatabase | None = None) -> None:
        self.db_path = Path(db_path)
        self._core = core if core is not None else CoreDatabase(self.db_path)

    def create_template(
        self, *, template_id: str, category: str, difficulty: str, effort: str, duration_minutes: int,
        required_equipment: tuple[str, ...], required_privacy: str, required_context: str,
        safety_classification: str, eligible_instance_roles: tuple[TaskInstanceRole, ...],
        eligible_operating_modes: tuple[str, ...], completion_requirements: dict,
        verification_requirements: dict, reflection_requirements: dict | None,
        lock_requirement: LockRequirement,
        created_via_consent_id: str, now: datetime,
    ) -> TaskTemplateVersion:
        """
        Creates the FIRST version (1) and its TaskTemplateCatalogEntry
        atomically. Raises InvalidTaskTemplateVersionError if
        template_id already has an entry (use add_version() for a
        second version).

        Populates all four audit fields on the new CatalogEntry from
        this same creation consent/timestamp -- the initial `ACTIVE`
        eligibility and the initial `current_version=1` are both
        already-authorized outcomes of this creation, not a neutral
        default with no origin (see TaskTemplateCatalogEntry's own
        docstring).
        """
        _require_consent_id(created_via_consent_id)

        def write(tx: Transaction, _state: object) -> TaskTemplateVersion:
            existing = tx.fetch_one(
                "SELECT 1 FROM task_template_catalog_entries WHERE template_id = ?", (template_id,),
            )
            if existing is not None:
                raise InvalidTaskTemplateVersionError(
                    f"Template {template_id!r} already exists -- use add_version() to add a new version."
                )
            version = TaskTemplateVersion(
                template_id=template_id, version=1, category=category, difficulty=difficulty,
                effort=effort, duration_minutes=duration_minutes, required_equipment=required_equipment,
                required_privacy=required_privacy, required_context=required_context,
                safety_classification=safety_classification, eligible_instance_roles=eligible_instance_roles,
                eligible_operating_modes=eligible_operating_modes, completion_requirements=completion_requirements,
                verification_requirements=verification_requirements, reflection_requirements=reflection_requirements,
                lock_requirement=lock_requirement,
                created_at=now, created_via_consent_id=created_via_consent_id,
            )
            _insert_version(tx, version)
            tx.execute(
                """
                INSERT INTO task_template_catalog_entries
                    (template_id, current_version, eligibility_status, status_changed_at,
                     eligibility_changed_via_consent_id, current_version_changed_via_consent_id,
                     current_version_changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id, 1, TaskTemplateEligibilityStatus.ACTIVE.value, _iso(now),
                    created_via_consent_id, created_via_consent_id, _iso(now),
                ),
            )
            return version

        return apply_transition(self._core, write=write)

    def add_version(
        self, template_id: str, *, category: str, difficulty: str, effort: str, duration_minutes: int,
        required_equipment: tuple[str, ...], required_privacy: str, required_context: str,
        safety_classification: str, eligible_instance_roles: tuple[TaskInstanceRole, ...],
        eligible_operating_modes: tuple[str, ...], completion_requirements: dict,
        verification_requirements: dict, reflection_requirements: dict | None,
        lock_requirement: LockRequirement,
        created_via_consent_id: str, now: datetime,
    ) -> TaskTemplateVersion:
        """
        Appends a new, append-only TaskTemplateVersion under an
        existing template_id. The next version number is computed
        internally (max existing + 1) -- never accepted as a caller-
        supplied parameter, so there is no way to create a gap or a
        caller-induced collision; UNIQUE(template_id, version) is a
        second, database-level guarantee behind this.

        Does NOT change current_version -- use set_current_version()
        separately once the new version should become current.
        """
        _require_consent_id(created_via_consent_id)

        def write(tx: Transaction, _state: object) -> TaskTemplateVersion:
            entry_row = tx.fetch_one(
                "SELECT * FROM task_template_catalog_entries WHERE template_id = ?", (template_id,),
            )
            if entry_row is None:
                raise TaskTemplateNotFoundError(f"No template {template_id!r} -- use create_template() first.")
            max_row = tx.fetch_one(
                "SELECT MAX(version) as v FROM task_template_versions WHERE template_id = ?", (template_id,),
            )
            next_version = (max_row["v"] or 0) + 1
            version = TaskTemplateVersion(
                template_id=template_id, version=next_version, category=category, difficulty=difficulty,
                effort=effort, duration_minutes=duration_minutes, required_equipment=required_equipment,
                required_privacy=required_privacy, required_context=required_context,
                safety_classification=safety_classification, eligible_instance_roles=eligible_instance_roles,
                eligible_operating_modes=eligible_operating_modes, completion_requirements=completion_requirements,
                verification_requirements=verification_requirements, reflection_requirements=reflection_requirements,
                lock_requirement=lock_requirement,
                created_at=now, created_via_consent_id=created_via_consent_id,
            )
            _insert_version(tx, version)
            return version

        return apply_transition(self._core, write=write)

    def set_current_version(
        self, template_id: str, version: int, *, via_consent_id: str, now: datetime,
    ) -> TaskTemplateCatalogEntry:
        """Advances the CatalogEntry's current_version pointer. `version`
        must already exist for this template_id (enforced both here,
        with a clear error, and by the schema's own composite foreign
        key as a second, independent guarantee).

        `via_consent_id`, not `created_via_consent_id` -- this changes
        mutable state on an existing entry, it does not create a new
        immutable entity (that distinction is `create_template()`/
        `add_version()`'s own `created_via_consent_id`, matching
        `trust_manager`'s own established naming split between the two
        kinds of operation). Persisted in
        `current_version_changed_via_consent_id`/`current_version_changed_at`
        -- never cleared, always reflect the most recent authorization
        (see `TaskTemplateCatalogEntry`'s own docstring for why this
        differs from `trust_manager`'s NULL-clearing pattern). Never
        touches `eligibility_status`/`status_changed_at`/
        `eligibility_changed_via_consent_id` -- eligibility and
        current_version are two independent audit pairs on the same
        row."""
        _require_consent_id(via_consent_id)

        def write(tx: Transaction, _state: object) -> TaskTemplateCatalogEntry:
            entry_row = tx.fetch_one(
                "SELECT * FROM task_template_catalog_entries WHERE template_id = ?", (template_id,),
            )
            if entry_row is None:
                raise TaskTemplateNotFoundError(f"No template {template_id!r}.")
            version_row = tx.fetch_one(
                "SELECT 1 FROM task_template_versions WHERE template_id = ? AND version = ?",
                (template_id, version),
            )
            if version_row is None:
                raise InvalidTaskTemplateVersionError(
                    f"Template {template_id!r} has no version {version} to point current_version at."
                )
            tx.execute(
                "UPDATE task_template_catalog_entries SET current_version = ?, "
                "current_version_changed_via_consent_id = ?, current_version_changed_at = ? "
                "WHERE template_id = ?",
                (version, via_consent_id, _iso(now), template_id),
            )
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = ?", (template_id,))
            return _row_to_entry(row)

        return apply_transition(self._core, write=write)

    def activate(self, template_id: str, *, via_consent_id: str, now: datetime) -> TaskTemplateCatalogEntry:
        return self._set_eligibility(
            template_id, TaskTemplateEligibilityStatus.ACTIVE, via_consent_id=via_consent_id, now=now,
        )

    def deactivate(self, template_id: str, *, via_consent_id: str, now: datetime) -> TaskTemplateCatalogEntry:
        return self._set_eligibility(
            template_id, TaskTemplateEligibilityStatus.DEACTIVATED, via_consent_id=via_consent_id, now=now,
        )

    def _set_eligibility(
        self, template_id: str, target: TaskTemplateEligibilityStatus, *, via_consent_id: str, now: datetime,
    ) -> TaskTemplateCatalogEntry:
        _require_consent_id(via_consent_id)

        def write(tx: Transaction, _state: object) -> TaskTemplateCatalogEntry:
            entry_row = tx.fetch_one(
                "SELECT * FROM task_template_catalog_entries WHERE template_id = ?", (template_id,),
            )
            if entry_row is None:
                raise TaskTemplateNotFoundError(f"No template {template_id!r}.")
            current = TaskTemplateEligibilityStatus(entry_row["eligibility_status"])
            if current == target:
                raise TaskTemplateEligibilityTransitionError(
                    f"Template {template_id!r} is already {target.value!r} -- no transition to make."
                )
            tx.execute(
                "UPDATE task_template_catalog_entries SET eligibility_status = ?, status_changed_at = ?, "
                "eligibility_changed_via_consent_id = ? WHERE template_id = ?",
                (target.value, _iso(now), via_consent_id, template_id),
            )
            row = tx.fetch_one("SELECT * FROM task_template_catalog_entries WHERE template_id = ?", (template_id,))
            return _row_to_entry(row)

        return apply_transition(self._core, write=write)
