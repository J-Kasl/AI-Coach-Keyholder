"""
task_runtime/eligibility.py

Deterministic, pure -- no DB access, no LLM, no side effects.
`evaluate_task_eligibility()` is the PREVIEW/filtering function --
`TaskRuntimeAdministration.assign_task()` (repository.py) re-derives
and enforces this same decision itself before writing, never trusting
a caller-supplied boolean or TaskEligibilityDecision as proof
(repository.py's own docstring explains why).

Slice B: only the lock-state dimension exists. `eligibility first,
ranking second` remains true structurally -- this function returns a
decision per template; selecting/ranking among eligible templates is
explicitly not this module's job (Slice B has no selection algorithm
at all -- see task_runtime/README.md).
"""

from __future__ import annotations

from lock_state.models import LockKnowledgeState
from task_catalog.models import LockRequirement, TaskTemplateVersion
from task_runtime.models import EligibilityReasonCode, TaskEligibilityDecision

__all__ = ["evaluate_task_eligibility"]


def evaluate_task_eligibility(
    *, template: TaskTemplateVersion, lock_knowledge_state: LockKnowledgeState,
) -> TaskEligibilityDecision:
    """
    Fail-closed: UNKNOWN and UNLOCKED_USER_REPORTED both fail a
    REQUIRES_LOCKED template -- only LOCKED_USER_REPORTED passes.
    No physical-verification semantics are implied or assumed; this
    reads the existing user-reported epistemic state as-is.
    """
    if template.lock_requirement == LockRequirement.REQUIRES_LOCKED:
        if lock_knowledge_state != LockKnowledgeState.LOCKED_USER_REPORTED:
            return TaskEligibilityDecision(eligible=False, reason_code=EligibilityReasonCode.LOCK_STATE_REQUIRED)

    return TaskEligibilityDecision(eligible=True, reason_code=EligibilityReasonCode.ELIGIBLE)
