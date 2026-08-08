"""
task_runtime/repository.py

Two structurally separate public classes, the same split
task_catalog/advanced_mode/lock_state already established:

- `TaskRuntime` -- read-only (`get_active_assignment`, `get_eligible_templates`).
  No write method exists on this class.
- `TaskRuntimeAdministration` -- governed write (`assign_task`,
  `complete_task`, `cancel_task`).

CRITICAL: `assign_task()` re-derives and enforces eligibility itself --
it loads the template via TaskCatalog's own read API and calls
evaluate_task_eligibility() internally. It never trusts a caller-
supplied boolean or TaskEligibilityDecision as proof of eligibility.
`evaluate_task_eligibility()` (eligibility.py) remains public for
preview/filtering, but preview eligibility and authoritative
assignment eligibility enforcement are two different things -- the
authoritative write always repeats the check.

ERROR CLASSIFICATION (fixed after review -- see this module's own
`assign_task()` docstring): a raw `sqlite3.IntegrityError` can be
raised for more than one reason -- an invalid user_id FK, an invalid
template/version FK, or the partial unique index
(idx_one_active_assignment_per_user). Catching IntegrityError broadly
and always mapping it to TaskAssignmentConcurrencyError would
mislabel a referential-integrity failure as a concurrency race.
Following this project's own established precedent
(penalty_engine/repository.py's own `_record_recovery_credit_in_transaction`
docstring: an explicit pre-check inside the same transaction, rather
than relying on IntegrityError bubbling up uncaught), `assign_task()`
explicitly checks user_id existence INSIDE the same write transaction
before attempting the INSERT. Since this project has no user-deletion
mechanism, that check cannot itself introduce a new race: a user row,
once confirmed to exist, cannot be deleted out from under the same
transaction. Template/version existence is already guaranteed before
`write()` even runs (`TaskCatalog.get_current_version()` resolved a
real row, and task_template_versions is append-only with no delete
path -- see task_catalog/README.md's own TC-1 discussion) so no
further re-check is needed for that FK. Once both known FK causes are
excluded this way, an IntegrityError from the INSERT itself can only
be the partial unique index -- and only THEN is it mapped to
TaskAssignmentConcurrencyError. The database-level partial unique
index remains the actual, final concurrency authority; this pre-check
does not weaken or replace it, it only lets the resulting error be
classified correctly.

PRIVACY: no error message in this module includes a raw user_id,
template_id, or assignment_id -- the same discipline lock_state and
preference_profile already established (generic messages only, since
these exceptions could in principle be logged).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from infrastructure.database import Database as CoreDatabase
from infrastructure.database import Transaction, apply_transition
from infrastructure.time_format import iso as _iso
from infrastructure.time_format import parse_iso as _parse_iso
from lock_state.models import LockKnowledgeState
from task_catalog.models import TaskInstanceRole, TaskTemplateVersion
from task_catalog.repository import TaskCatalog
from task_runtime.eligibility import evaluate_task_eligibility
from task_runtime.models import TaskAssignment, TaskAssignmentStatus

__all__ = [
    "TaskRuntime",
    "TaskRuntimeAdministration",
    "TaskAssignmentError",
    "TaskNotEligibleError",
    "TaskAssignmentConcurrencyError",
    "TaskAssignmentReferentialIntegrityError",
    "TaskAssignmentTransitionError",
    "TaskAssignmentNotFoundError",
    "TaskTemplateNotFoundForAssignmentError",
]


class TaskAssignmentError(RuntimeError):
    """Base class for this module's own errors."""


class TaskNotEligibleError(TaskAssignmentError):
    """assign_task()'s own authoritative eligibility enforcement
    rejected the request -- raised regardless of what a caller may
    have separately believed about eligibility."""


class TaskAssignmentConcurrencyError(TaskAssignmentError):
    """A stable, typed error for the partial-unique-index race (at
    most one ACTIVE assignment per user_id) -- callers never need to
    interpret a raw sqlite3.IntegrityError themselves. Raised ONLY
    after known referential-integrity causes have been deterministically
    excluded -- see this module's own top docstring."""


class TaskAssignmentReferentialIntegrityError(TaskAssignmentError):
    """A referential-integrity failure that is NOT the active-assignment
    race -- e.g. a user_id with no matching user_accounts row. Kept
    distinct from TaskAssignmentConcurrencyError so a caller (and any
    future application-layer error handling) never receives a
    misleading "already has an active assignment" message for an
    unrelated integrity problem."""


class TaskAssignmentTransitionError(TaskAssignmentError):
    """An invalid lifecycle transition was attempted (e.g. resolving
    an assignment that is not currently ACTIVE)."""


class TaskAssignmentNotFoundError(TaskAssignmentError):
    """No TaskAssignment exists for the given id."""


class TaskTemplateNotFoundForAssignmentError(TaskAssignmentError):
    """assign_task()'s own template_id has no current version in
    Task Catalog."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _require_non_empty(value: str, *, name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _row_to_assignment(row) -> TaskAssignment:
    return TaskAssignment(
        id=row["id"], user_id=row["user_id"], template_id=row["template_id"],
        template_version=row["template_version"], status=TaskAssignmentStatus(row["status"]),
        assigned_at=_parse_iso(row["assigned_at"]), assigned_via_consent_id=row["assigned_via_consent_id"],
        resolved_at=_parse_iso(row["resolved_at"]) if row["resolved_at"] is not None else None,
        resolved_via_consent_id=row["resolved_via_consent_id"],
    )


class TaskRuntime:
    """Read-only. No write method exists on this class at all."""

    def __init__(self, db_path: str | Path, *, core: CoreDatabase | None = None) -> None:
        self.db_path = Path(db_path)
        self._core = core if core is not None else CoreDatabase(self.db_path)
        self._catalog = TaskCatalog(self.db_path, core=self._core)

    def get_active_assignment(self, user_id: str) -> TaskAssignment | None:
        _require_non_empty(user_id, name="user_id")
        with self._core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT * FROM task_assignments WHERE user_id = ? AND status = ?",
                (user_id, TaskAssignmentStatus.ACTIVE.value),
            )
        return _row_to_assignment(row) if row is not None else None

    def get_eligible_templates(
        self, *, role: TaskInstanceRole, operating_mode: str, lock_knowledge_state: LockKnowledgeState,
    ) -> tuple[TaskTemplateVersion, ...]:
        """
        Preview/filtering only -- never itself an assignment authority.
        Delegates candidate retrieval entirely to TaskCatalog's own
        existing get_active_templates() (no new task_catalog query
        logic duplicated here), then filters by
        evaluate_task_eligibility(). Does not select or rank among the
        eligible results -- Slice B has no selection algorithm at all.
        """
        candidates = self._catalog.get_active_templates(role=role, operating_mode=operating_mode)
        return tuple(
            t for t in candidates
            if evaluate_task_eligibility(template=t, lock_knowledge_state=lock_knowledge_state).eligible
        )


class TaskRuntimeAdministration:
    """Governed write API."""

    def __init__(self, db_path: str | Path, *, core: CoreDatabase | None = None) -> None:
        self.db_path = Path(db_path)
        self._core = core if core is not None else CoreDatabase(self.db_path)
        self._catalog = TaskCatalog(self.db_path, core=self._core)

    def assign_task(
        self, *, user_id: str, template_id: str, lock_knowledge_state: LockKnowledgeState,
        assigned_via_consent_id: str, now: datetime,
    ) -> TaskAssignment:
        """
        Loads the template's current version and re-derives eligibility
        itself -- a caller cannot bypass this by pre-computing its own
        TaskEligibilityDecision and passing it in (no such parameter
        exists on this method at all). Raises TaskNotEligibleError if
        ineligible; nothing is written in that case.

        Error classification (see this module's own top docstring for
        the full reasoning): an explicit user_id existence check runs
        INSIDE the same write transaction, before the INSERT. If that
        check fails, TaskAssignmentReferentialIntegrityError is raised
        -- never TaskAssignmentConcurrencyError. Template/version
        existence is already guaranteed by the earlier
        get_current_version() call (append-only, no delete path -- see
        task_catalog's own TC-1). Only once both are excluded does an
        IntegrityError from the INSERT itself get mapped to
        TaskAssignmentConcurrencyError -- at that point the partial
        unique index (idx_one_active_assignment_per_user) is the only
        remaining known cause.
        """
        _require_non_empty(user_id, name="user_id")
        _require_non_empty(template_id, name="template_id")
        _require_non_empty(assigned_via_consent_id, name="assigned_via_consent_id")

        template = self._catalog.get_current_version(template_id)
        if template is None:
            raise TaskTemplateNotFoundForAssignmentError("No current version found for the given template.")

        decision = evaluate_task_eligibility(template=template, lock_knowledge_state=lock_knowledge_state)
        if not decision.eligible:
            raise TaskNotEligibleError(f"Template is not eligible: {decision.reason_code.value}.")

        def write(tx: Transaction, _state: object) -> TaskAssignment:
            user_exists = tx.fetch_one("SELECT 1 FROM user_accounts WHERE id = ?", (user_id,))
            if user_exists is None:
                raise TaskAssignmentReferentialIntegrityError("No user_accounts row exists for the given user_id.")

            assignment = TaskAssignment(
                id=_new_id(), user_id=user_id, template_id=template.template_id,
                template_version=template.version, status=TaskAssignmentStatus.ACTIVE,
                assigned_at=now, assigned_via_consent_id=assigned_via_consent_id,
                resolved_at=None, resolved_via_consent_id=None,
            )
            try:
                tx.execute(
                    """
                    INSERT INTO task_assignments
                        (id, user_id, template_id, template_version, status, assigned_at, assigned_via_consent_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        assignment.id, assignment.user_id, assignment.template_id, assignment.template_version,
                        assignment.status.value, _iso(assignment.assigned_at), assignment.assigned_via_consent_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # Both known referential-integrity causes (invalid
                # user_id, invalid template/version) are already
                # excluded above/before write() runs -- the only
                # remaining known cause at this point is the partial
                # unique index.
                raise TaskAssignmentConcurrencyError(
                    "This user already has an active assignment."
                ) from exc
            return assignment

        return apply_transition(self._core, write=write)

    def complete_task(self, *, assignment_id: str, resolved_via_consent_id: str, now: datetime) -> TaskAssignment:
        return self._resolve(
            assignment_id=assignment_id, target_status=TaskAssignmentStatus.COMPLETED,
            resolved_via_consent_id=resolved_via_consent_id, now=now,
        )

    def cancel_task(self, *, assignment_id: str, resolved_via_consent_id: str, now: datetime) -> TaskAssignment:
        return self._resolve(
            assignment_id=assignment_id, target_status=TaskAssignmentStatus.CANCELLED,
            resolved_via_consent_id=resolved_via_consent_id, now=now,
        )

    def _resolve(
        self, *, assignment_id: str, target_status: TaskAssignmentStatus, resolved_via_consent_id: str, now: datetime,
    ) -> TaskAssignment:
        _require_non_empty(assignment_id, name="assignment_id")
        _require_non_empty(resolved_via_consent_id, name="resolved_via_consent_id")

        def write(tx: Transaction, _state: object) -> TaskAssignment:
            row = tx.fetch_one("SELECT * FROM task_assignments WHERE id = ?", (assignment_id,))
            if row is None:
                raise TaskAssignmentNotFoundError("No TaskAssignment exists for the given assignment_id.")
            current = _row_to_assignment(row)
            if current.status != TaskAssignmentStatus.ACTIVE:
                raise TaskAssignmentTransitionError(
                    f"Cannot resolve assignment: not ACTIVE (current status: {current.status.value})."
                )
            tx.execute(
                "UPDATE task_assignments SET status = ?, resolved_at = ?, resolved_via_consent_id = ? WHERE id = ?",
                (target_status.value, _iso(now), resolved_via_consent_id, assignment_id),
            )
            return TaskAssignment(
                id=current.id, user_id=current.user_id, template_id=current.template_id,
                template_version=current.template_version, status=target_status,
                assigned_at=current.assigned_at, assigned_via_consent_id=current.assigned_via_consent_id,
                resolved_at=now, resolved_via_consent_id=resolved_via_consent_id,
            )

        return apply_transition(self._core, write=write)
