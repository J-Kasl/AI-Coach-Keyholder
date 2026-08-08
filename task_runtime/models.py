"""
task_runtime/models.py

docs/architecture/task_runtime_technical_design.md (draft, not
approved for implementation as a whole). This module implements ONLY
the Slice B foundation -- see task_runtime/README.md for the exact
boundary. Task Catalog = what CAN exist (definitions); Task Runtime =
what was assigned to a specific user and its state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = ["TaskAssignmentStatus", "TaskAssignment", "EligibilityReasonCode", "TaskEligibilityDecision"]


class TaskAssignmentStatus(StrEnum):
    """Exactly three states, exactly two transitions -- ACTIVE ->
    COMPLETED, ACTIVE -> CANCELLED. No ASSIGNED/FAILED/EXPIRED/SKIPPED/
    REFUSED in this slice -- deliberately not added "for the future"."""
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, kw_only=True)
class TaskAssignment:
    """
    One row for the assignment's entire lifetime -- updated in place on
    resolution (status/resolved_at/resolved_via_consent_id), unlike
    lock_reports' own append-only history model. An assignment is a
    single entity with a lifecycle, not a series of independent
    reports.

    `template_id`/`template_version` reference a specific, immutable
    TaskTemplateVersion (task_catalog's own append-only guarantee) --
    enforced by a database-level composite FOREIGN KEY (migration 020),
    not merely a repository-level lookup. This assignment keeps
    referring to the exact version it was created against even after
    Task Catalog's own current_version advances.
    """
    id: str
    user_id: str
    template_id: str
    template_version: int
    status: TaskAssignmentStatus
    assigned_at: datetime
    assigned_via_consent_id: str
    resolved_at: datetime | None
    resolved_via_consent_id: str | None


class EligibilityReasonCode(StrEnum):
    """No raw sensitive content -- a closed set of safe, machine-readable
    codes. Only ELIGIBLE and LOCK_STATE_REQUIRED exist in this slice;
    preference/limits/Chaster/personality dimensions are not represented."""
    ELIGIBLE = "eligible"
    LOCK_STATE_REQUIRED = "lock_state_required"


@dataclass(frozen=True, kw_only=True)
class TaskEligibilityDecision:
    eligible: bool
    reason_code: EligibilityReasonCode
