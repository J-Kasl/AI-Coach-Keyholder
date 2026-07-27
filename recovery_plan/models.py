"""
recovery_plan/models.py

Data structures for the Recovery Plan module. Canonical:
docs/architecture/recovery_plan_technical_design.md Sections 3.1, 3.2.
See recovery_plan/README.md for what this slice covers.

`RecoveryTaskCompletion`'s exact shape is NOT given explicitly in the
architecture document (unlike `RecoveryPlan`/`RecoveryTask`, which have
full `@dataclass` definitions) -- this slice's own design, flagged
below at its definition.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


def new_id() -> str:
    return str(uuid.uuid4())


class RecoveryPlanStatus(StrEnum):
    ACTIVE = "active"        # mirrors the Penalty Window's own ACTIVE state
    FROZEN = "frozen"        # mirrors the Penalty Window's own FROZEN state
    COMPLETED = "completed"  # the Penalty Window completed; this plan's life ends with it


@dataclass(kw_only=True)
class RecoveryPlan:
    """
    Mutable-with-status (implementation_conventions.md Section 7), one
    per Penalty Window (1:1, RP-7). `status` is a PROJECTION of the
    Penalty Window's own status, mirrored via events, never
    independently decided (2.5) -- this module does not own the
    freeze/resume/complete decision, only reacts to it.
    """
    id: str = field(default_factory=new_id)
    penalty_window_id: str          # 1:1, unique
    status: RecoveryPlanStatus
    current_version: int = 1        # incremented on regeneration (3.4)
    recovery_credit_capacity_hours: float   # a snapshot, copied from the Penalty Window -- never independently computed (RP-3)
    created_at: datetime
    status_changed_at: datetime


class RecoveryTaskStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    EXPIRED = "expired"      # the plan was regenerated (3.4) before this task was completed
    WITHDRAWN = "withdrawn"  # the Coach removed it as no longer relevant, before completion


@dataclass(kw_only=True)
class RecoveryTask:
    """Mutable, belongs to exactly one RecoveryPlan version. A task
    proposed in version N does not automatically carry over to version
    N+1 (3.4) -- regeneration is a genuine re-design, not an in-place
    edit."""
    id: str = field(default_factory=new_id)
    recovery_plan_id: str
    plan_version: int
    title: str
    description: str
    credit_hours: float          # the Coach's proposed value -- see 3.3 for the (Penalty-Engine-owned) constraint this is subject to
    status: RecoveryTaskStatus = RecoveryTaskStatus.PROPOSED
    created_at: datetime
    status_changed_at: datetime


@dataclass(frozen=True, kw_only=True)
class RecoveryTaskCompletion:
    """
    Append-only (RP-2: Recovery Plan's own interpretation, never
    re-derived by the Penalty Engine). This slice's own design for the
    record's exact fields -- the architecture document establishes that
    it exists and is read via `get_recovery_task_completion()`, but does
    not give an explicit dataclass the way `RecoveryPlan`/`RecoveryTask`
    have one. `recovery_plan_id` is denormalized (a completion is always
    scoped to one plan) purely for convenient querying, not a second
    source of truth -- it is always derivable from `recovery_task_id`.
    """
    id: str = field(default_factory=new_id)
    recovery_task_id: str
    recovery_plan_id: str
    created_at: datetime
    notes: str | None = None
