"""tests/task_runtime/test_eligibility.py"""

from __future__ import annotations

from datetime import datetime, timezone

from lock_state.models import LockKnowledgeState
from task_catalog.models import LockRequirement, TaskInstanceRole, TaskTemplateVersion
from task_runtime.eligibility import evaluate_task_eligibility
from task_runtime.models import EligibilityReasonCode

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _template(lock_requirement: LockRequirement) -> TaskTemplateVersion:
    return TaskTemplateVersion(
        template_id="t1", version=1, category="chore", difficulty="easy", effort="low",
        duration_minutes=10, required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard",), completion_requirements={}, verification_requirements={},
        reflection_requirements=None, lock_requirement=lock_requirement,
        created_at=FIXED_TIME, created_via_consent_id="c1",
    )


class TestNoLockRequirement:
    def test_eligible_regardless_of_lock_state(self) -> None:
        template = _template(LockRequirement.NONE)
        for state in LockKnowledgeState:
            decision = evaluate_task_eligibility(template=template, lock_knowledge_state=state)
            assert decision.eligible is True
            assert decision.reason_code == EligibilityReasonCode.ELIGIBLE


class TestRequiresLocked:
    def test_locked_user_reported_is_eligible(self) -> None:
        template = _template(LockRequirement.REQUIRES_LOCKED)
        decision = evaluate_task_eligibility(template=template, lock_knowledge_state=LockKnowledgeState.LOCKED_USER_REPORTED)
        assert decision.eligible is True
        assert decision.reason_code == EligibilityReasonCode.ELIGIBLE

    def test_unknown_is_ineligible_fail_closed(self) -> None:
        template = _template(LockRequirement.REQUIRES_LOCKED)
        decision = evaluate_task_eligibility(template=template, lock_knowledge_state=LockKnowledgeState.UNKNOWN)
        assert decision.eligible is False
        assert decision.reason_code == EligibilityReasonCode.LOCK_STATE_REQUIRED

    def test_unlocked_user_reported_is_ineligible(self) -> None:
        template = _template(LockRequirement.REQUIRES_LOCKED)
        decision = evaluate_task_eligibility(template=template, lock_knowledge_state=LockKnowledgeState.UNLOCKED_USER_REPORTED)
        assert decision.eligible is False
        assert decision.reason_code == EligibilityReasonCode.LOCK_STATE_REQUIRED


class TestPureFunction:
    def test_deterministic_across_repeated_calls(self) -> None:
        template = _template(LockRequirement.REQUIRES_LOCKED)
        results = {
            evaluate_task_eligibility(template=template, lock_knowledge_state=LockKnowledgeState.UNKNOWN).eligible
            for _ in range(5)
        }
        assert results == {False}
