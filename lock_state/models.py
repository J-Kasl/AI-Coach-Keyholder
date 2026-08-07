"""
lock_state/models.py

docs/architecture/lock_state_technical_design.md (draft, not approved
for implementation as a whole). This module implements ONLY the
user-reported lock state -- see lock_state/README.md for the exact
boundary.

Epistemic invariant, non-negotiable: without external hardware or a
provider integration (a future, separate slice), this system cannot
know whether physical keys are actually secured. Every status this
module can represent is what the USER TOLD THE SYSTEM, never a
verified physical fact. No status here or anywhere in this module may
be named or interpreted as "VERIFIED_LOCKED", "KEYS_IN_LOCKBOX",
"PHYSICALLY_SECURED", or anything else implying technical verification
of physical reality.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = ["LockReportStatus", "LockKnowledgeState", "LockReport"]


class LockReportStatus(StrEnum):
    """The only two values ever PERSISTED (lock_reports.status,
    migration 019) -- both explicitly named '..._USER_REPORTED' so the
    epistemic boundary is visible at every call site, not just in a
    docstring. There is no UNKNOWN member here -- UNKNOWN is never a
    report someone makes; it is the read-time absence of one (see
    LockKnowledgeState)."""
    LOCKED_USER_REPORTED = "locked_user_reported"
    UNLOCKED_USER_REPORTED = "unlocked_user_reported"


class LockKnowledgeState(StrEnum):
    """
    The three-value READ-RESULT type. UNKNOWN is a property of "no
    trustworthy report exists for this user" -- it is never written to
    the database as a fake report, and it must never be silently
    treated as equivalent to UNLOCKED_USER_REPORTED (an absence of
    information is not evidence of an unlocked state, any more than it
    is evidence of a locked one).
    """
    LOCKED_USER_REPORTED = "locked_user_reported"
    UNLOCKED_USER_REPORTED = "unlocked_user_reported"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class LockReport:
    """
    Immutable -- one row, one report, never mutated or deleted after
    creation (the same append-only discipline task_catalog's own
    TaskTemplateVersion and advanced_mode's own ModeTransitionRequest
    already apply). `user_id` is this project's own existing
    UserAccount.id boundary -- never a raw Discord identifier (the
    same convention conversation_engine/memory_system/preference_profile
    already use via their own `subject_key`/`owner_key`, applied here
    under this module's own name for its own domain).
    """
    id: str
    user_id: str
    status: LockReportStatus
    sequence_number: int
    reported_at: datetime
    reported_via_consent_id: str
