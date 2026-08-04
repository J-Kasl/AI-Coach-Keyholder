"""
task_catalog/models.py

docs/architecture/task_catalog_technical_design.md (draft, not approved
for implementation as a whole). This module implements ONLY the
catalog-layer data shapes (TC-1, TC-2, TC-3) approved for this
specific implementation slice -- see task_catalog/README.md for the
exact boundary between what is implemented here and what remains
draft/undecided.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


def new_id() -> str:
    return str(uuid.uuid4())


class TaskInstanceRole(StrEnum):
    """
    task_catalog_technical_design.md Section 3. A role's presence here
    is entirely independent of whether it has a runtime owner (TC-3).
    Only RECOVERY has one today (recovery_plan, unchanged by this
    module). PRIMARY/JOURNALING/INTEGRITY/OPTIONAL_CHALLENGE are valid
    catalog values with NO assigned runtime owner -- their presence in
    this enum must never be read as evidence they are implemented
    anywhere else in this codebase.
    """
    RECOVERY = "recovery"
    PRIMARY = "primary"
    JOURNALING = "journaling"
    INTEGRITY = "integrity"
    OPTIONAL_CHALLENGE = "optional_challenge"


class TaskTemplateEligibilityStatus(StrEnum):
    """Lives exclusively on TaskTemplateCatalogEntry (TC-2) -- never on
    TaskTemplateVersion."""
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


@dataclass(frozen=True, kw_only=True)
class TaskTemplateVersion:
    """
    Append-only (TC-1) -- the same discipline `goal_management.GoalVersion`
    already applies. Never edited, never deleted after creation; a
    correction is a NEW version under the same template_id, never an
    edit to an existing row. `frozen=True` enforces this at the Python
    level too, not only in the database schema (no application code
    ever mutates an instance's fields after construction).
    """
    id: str = field(default_factory=new_id)
    template_id: str
    version: int
    category: str
    difficulty: str
    effort: str
    duration_minutes: int
    required_equipment: tuple[str, ...]
    required_privacy: str
    required_context: str
    safety_classification: str
    eligible_instance_roles: tuple[TaskInstanceRole, ...]
    eligible_operating_modes: tuple[str, ...]
    completion_requirements: dict
    verification_requirements: dict
    reflection_requirements: dict | None
    created_at: datetime
    created_via_consent_id: str   # governance (TC-4): never created without one

    def __post_init__(self) -> None:
        """
        Minimal, deliberately small validation -- confirmed missing
        under direct review (empty/duplicate values previously wrote
        successfully with no error at all). Raises plain `ValueError`
        (not a Task-Catalog-specific error class -- keeping this
        validation in `models.py` would otherwise need to import an
        error class from `repository.py`, which already imports from
        `models.py`; `InvalidTaskTemplateVersionError` is itself a
        `ValueError` subclass, so callers catching that broadly still
        catch this). Applies uniformly wherever a `TaskTemplateVersion`
        is constructed -- both `create_template()` and `add_version()`
        -- with no repository-level duplication needed.

        Deliberately does NOT validate individual `TaskInstanceRole`
        values against the enum (the type system already guarantees
        that for any caller going through normal Python construction)
        or reject unknown values read back from a corrupted database
        row -- that surfaces its own clear error at read time
        (`task_catalog/repository.py::_row_to_version`), and is a
        separate, already-covered case, not a gap this validation is
        meant to close.
        """
        if not self.eligible_instance_roles:
            raise ValueError("eligible_instance_roles must not be empty.")
        if len(set(self.eligible_instance_roles)) != len(self.eligible_instance_roles):
            raise ValueError("eligible_instance_roles must not contain duplicates.")
        if not self.eligible_operating_modes:
            raise ValueError("eligible_operating_modes must not be empty.")
        if len(set(self.eligible_operating_modes)) != len(self.eligible_operating_modes):
            raise ValueError("eligible_operating_modes must not contain duplicates.")


@dataclass(kw_only=True)
class TaskTemplateCatalogEntry:
    """
    Mutable current-state pointer (TC-2) -- exactly `Goal`'s own
    relationship to `GoalVersion`. `eligibility_status`/`current_version`
    are the only fields that ever change after creation; no
    `TaskTemplateVersion` row is ever touched by activating or
    deactivating a template, or by advancing `current_version`.

    Two symmetric audit pairs, each (who, when) for its own field --
    `create_template()` populates all four from the same creation
    consent/timestamp, since the initial `ACTIVE` state and the
    initial `current_version=1` are both already-authorized outcomes
    of that same creation, not a neutral default with no origin:

    - `eligibility_status` <-> `eligibility_changed_via_consent_id` +
      `status_changed_at`
    - `current_version` <-> `current_version_changed_via_consent_id` +
      `current_version_changed_at`

    Both consent fields always reflect the MOST RECENT authorization --
    never cleared, unlike `trust_manager`'s own `deactivated_via_consent_id`
    (which is cleared on reactivation, relying on a domain event to
    carry that consent instead). Task Catalog has no domain events, so
    these columns are this module's SOLE audit record -- see
    task_catalog/README.md for the full reasoning. `None` for rows that
    predate the relevant migration (015 for the consent fields, 016 for
    `current_version_changed_at`) or, in principle, if a row was
    somehow written outside `TaskCatalogAdministration`.
    """
    template_id: str
    current_version: int
    eligibility_status: TaskTemplateEligibilityStatus
    status_changed_at: datetime
    eligibility_changed_via_consent_id: str | None = None
    current_version_changed_via_consent_id: str | None = None
    current_version_changed_at: datetime | None = None
