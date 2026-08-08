"""tests/task_runtime/test_models.py"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from task_runtime.models import (
    EligibilityReasonCode,
    TaskAssignment,
    TaskAssignmentStatus,
    TaskEligibilityDecision,
)

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _assignment(**overrides) -> TaskAssignment:
    kwargs = dict(
        id="a1", user_id="u1", template_id="t1", template_version=1,
        status=TaskAssignmentStatus.ACTIVE, assigned_at=FIXED_TIME, assigned_via_consent_id="c1",
        resolved_at=None, resolved_via_consent_id=None,
    )
    kwargs.update(overrides)
    return TaskAssignment(**kwargs)


class TestTaskAssignmentStatus:
    def test_exactly_three_members(self) -> None:
        assert set(TaskAssignmentStatus) == {
            TaskAssignmentStatus.ACTIVE, TaskAssignmentStatus.COMPLETED, TaskAssignmentStatus.CANCELLED,
        }


class TestTaskAssignment:
    def test_valid_assignment_accepted(self) -> None:
        _assignment()  # must not raise

    def test_immutable(self) -> None:
        assignment = _assignment()
        with pytest.raises(dataclasses.FrozenInstanceError):
            assignment.status = TaskAssignmentStatus.COMPLETED  # type: ignore[misc]


class TestEligibilityReasonCode:
    def test_exactly_two_members_in_this_slice(self) -> None:
        assert set(EligibilityReasonCode) == {EligibilityReasonCode.ELIGIBLE, EligibilityReasonCode.LOCK_STATE_REQUIRED}


class TestTaskEligibilityDecision:
    def test_no_raw_content_fields_exist(self) -> None:
        field_names = {f.name for f in dataclasses.fields(TaskEligibilityDecision)}
        assert field_names == {"eligible", "reason_code"}

    def test_immutable(self) -> None:
        decision = TaskEligibilityDecision(eligible=True, reason_code=EligibilityReasonCode.ELIGIBLE)
        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.eligible = False  # type: ignore[misc]
