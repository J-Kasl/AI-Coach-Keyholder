"""tests/advanced_mode/test_models.py"""

from __future__ import annotations

from datetime import datetime, timezone

from advanced_mode.models import (
    ModeTransitionInterruptedByPenaltyWindowError,
    ModeTransitionRequest,
    ModeTransitionStatus,
    OperatingMode,
)

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _request(**overrides) -> ModeTransitionRequest:
    kwargs = dict(
        source_mode=OperatingMode.STANDARD, target_mode=OperatingMode.ADVANCED,
        status=ModeTransitionStatus.WAITING, requested_at=FIXED_TIME, requested_via_consent_id="c1",
    )
    kwargs.update(overrides)
    return ModeTransitionRequest(**kwargs)


class TestModeTransitionRequestIsTerminal:
    def test_waiting_is_not_terminal(self) -> None:
        assert _request(status=ModeTransitionStatus.WAITING).is_terminal() is False

    def test_cancelled_is_terminal(self) -> None:
        assert _request(status=ModeTransitionStatus.CANCELLED).is_terminal() is True

    def test_completed_is_terminal(self) -> None:
        assert _request(status=ModeTransitionStatus.COMPLETED).is_terminal() is True


class TestModeTransitionInterruptedByPenaltyWindowError:
    def test_carries_the_request_it_was_constructed_with(self) -> None:
        request = _request(status=ModeTransitionStatus.PAUSED_BY_PENALTY_WINDOW)
        error = ModeTransitionInterruptedByPenaltyWindowError(request)
        assert error.request is request
        assert request.id in str(error)
