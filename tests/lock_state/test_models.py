"""tests/lock_state/test_models.py"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from lock_state.models import LockKnowledgeState, LockReport, LockReportStatus


class TestLockReportStatusCannotRepresentVerifiedPhysicalFact:
    def test_exactly_two_members(self) -> None:
        """No third member exists that could imply verification --
        UNKNOWN deliberately lives only on LockKnowledgeState."""
        assert set(LockReportStatus) == {
            LockReportStatus.LOCKED_USER_REPORTED,
            LockReportStatus.UNLOCKED_USER_REPORTED,
        }

    def test_no_member_name_implies_physical_verification(self) -> None:
        forbidden_substrings = ("VERIFIED", "PHYSICAL", "LOCKBOX", "SECURED", "CONFIRMED_HARDWARE")
        for member in LockReportStatus:
            for forbidden in forbidden_substrings:
                assert forbidden not in member.name


class TestLockKnowledgeState:
    def test_exactly_three_members(self) -> None:
        assert set(LockKnowledgeState) == {
            LockKnowledgeState.LOCKED_USER_REPORTED,
            LockKnowledgeState.UNLOCKED_USER_REPORTED,
            LockKnowledgeState.UNKNOWN,
        }

    def test_unknown_is_distinct_from_unlocked(self) -> None:
        """The specific confusion this module must never allow --
        absence of a report is not evidence of an unlocked state."""
        assert LockKnowledgeState.UNKNOWN != LockKnowledgeState.UNLOCKED_USER_REPORTED
        assert LockKnowledgeState.UNKNOWN.value != LockKnowledgeState.UNLOCKED_USER_REPORTED.value


def _report(**overrides) -> LockReport:
    kwargs = dict(
        id="report-1", user_id="user-1", status=LockReportStatus.LOCKED_USER_REPORTED,
        sequence_number=1, reported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        reported_via_consent_id="discord_message:1",
    )
    kwargs.update(overrides)
    return LockReport(**kwargs)


class TestLockReport:
    def test_valid_report_accepted(self) -> None:
        _report()  # must not raise

    def test_immutable(self) -> None:
        report = _report()
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.status = LockReportStatus.UNLOCKED_USER_REPORTED  # type: ignore[misc]

    def test_field_set_matches_the_approved_shape(self) -> None:
        field_names = {f.name for f in dataclasses.fields(LockReport)}
        assert field_names == {"id", "user_id", "status", "sequence_number", "reported_at", "reported_via_consent_id"}
