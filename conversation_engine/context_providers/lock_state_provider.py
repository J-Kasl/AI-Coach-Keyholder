"""
conversation_engine/context_providers/lock_state_provider.py

LockStateContextProvider -- a ConversationContextProvider reading only
through lock_state's own read-only LockState, never
LockStateAdministration. Always returns a real fragment on a
successful read, including for UNKNOWN -- None/an exception is
reserved exclusively for a genuine read failure, never used to
represent a successfully-determined "nothing to report" state (see
this module's own test suite for the distinction this preserves in
assemble_context()'s own fault-boundary semantics).
"""

from __future__ import annotations

from datetime import datetime

from conversation_engine.models import ConversationContextFragment
from lock_state.models import LockKnowledgeState
from lock_state.repository import LockState

__all__ = ["LockStateContextProvider"]

_NOTES = {
    LockKnowledgeState.UNKNOWN: (
        "No current user-reported lock state is recorded. Do not infer that the user is unlocked."
    ),
    LockKnowledgeState.LOCKED_USER_REPORTED: (
        "The user has reported that they are locked. This is user-reported and has not been "
        "independently physically verified."
    ),
    LockKnowledgeState.UNLOCKED_USER_REPORTED: (
        "The user has reported that they are unlocked. This is user-reported and has not been "
        "independently physically verified."
    ),
}


class LockStateContextProvider:
    """Stateless, static -- `subject_key` is a per-call argument, never
    stored on this instance, so the same instance is safe to share
    across every subject the engine ever serves, including
    concurrently."""

    namespace = "lock_state"

    def __init__(self, *, lock_state: LockState) -> None:
        self._lock_state = lock_state

    def provide_context(self, *, subject_key: str, now: datetime) -> ConversationContextFragment | None:
        state = self._lock_state.get_current_knowledge_state(subject_key)
        return ConversationContextFragment(
            namespace=self.namespace, data={"status": state.value, "note": _NOTES[state]},
        )
