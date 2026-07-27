"""
tests/penalty_engine/test_window.py

Pure-function tests for penalty_engine/window.py (I5, I6, 2.2). No
database involved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from penalty_engine.models import PenaltyWindow, PenaltyWindowStatus
from penalty_engine.window import (
    MAX_TARGET_ACTIVE_HOURS,
    active_hours_elapsed,
    is_complete,
    remaining_active_hours,
    target_active_hours,
)

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _window(
    status: PenaltyWindowStatus = PenaltyWindowStatus.ACTIVE,
    base_duration_hours: float = 24.0,
    extensions_hours: float = 0.0,
    accumulated_active_hours: float = 0.0,
    active_period_started_at: datetime | None = FIXED_TIME,
) -> PenaltyWindow:
    return PenaltyWindow(
        created_at=FIXED_TIME, status=status, base_duration_hours=base_duration_hours,
        extensions_hours=extensions_hours, accumulated_active_hours=accumulated_active_hours,
        active_period_started_at=active_period_started_at,
    )


class TestTargetActiveHours:
    def test_sums_base_and_extensions(self) -> None:
        w = _window(base_duration_hours=24.0, extensions_hours=6.0)
        assert target_active_hours(w) == 30.0

    def test_caps_at_max(self) -> None:
        """I5: min(base + extensions, 336)."""
        w = _window(base_duration_hours=300.0, extensions_hours=100.0)
        assert target_active_hours(w) == MAX_TARGET_ACTIVE_HOURS


class TestActiveHoursElapsed:
    def test_active_window_accrues_time(self) -> None:
        w = _window(status=PenaltyWindowStatus.ACTIVE, active_period_started_at=FIXED_TIME, accumulated_active_hours=0.0)
        later = FIXED_TIME + timedelta(hours=5)
        assert active_hours_elapsed(w, later) == 5.0

    def test_active_window_adds_to_previously_accumulated(self) -> None:
        w = _window(status=PenaltyWindowStatus.ACTIVE, active_period_started_at=FIXED_TIME, accumulated_active_hours=10.0)
        later = FIXED_TIME + timedelta(hours=3)
        assert active_hours_elapsed(w, later) == 13.0

    def test_frozen_window_does_not_accrue_time(self) -> None:
        """I6: frozen intervals are never included."""
        w = _window(status=PenaltyWindowStatus.FROZEN, active_period_started_at=None, accumulated_active_hours=8.0)
        much_later = FIXED_TIME + timedelta(days=30)
        assert active_hours_elapsed(w, much_later) == 8.0

    def test_downtime_counts_toward_active_window(self) -> None:
        """4.5: downtime IS counted for an ACTIVE window -- this is
        simply a consequence of comparing two absolute timestamps, with
        no special-casing for whether anything was running in between."""
        w = _window(status=PenaltyWindowStatus.ACTIVE, active_period_started_at=FIXED_TIME)
        after_a_week_of_downtime = FIXED_TIME + timedelta(days=7)
        assert active_hours_elapsed(w, after_a_week_of_downtime) == 7 * 24.0


class TestIsComplete:
    def test_not_complete_before_target(self) -> None:
        w = _window(base_duration_hours=24.0, active_period_started_at=FIXED_TIME)
        assert is_complete(w, FIXED_TIME + timedelta(hours=1)) is False

    def test_complete_at_exactly_target(self) -> None:
        w = _window(base_duration_hours=24.0, active_period_started_at=FIXED_TIME)
        assert is_complete(w, FIXED_TIME + timedelta(hours=24)) is True

    def test_complete_beyond_target(self) -> None:
        w = _window(base_duration_hours=24.0, active_period_started_at=FIXED_TIME)
        assert is_complete(w, FIXED_TIME + timedelta(hours=48)) is True

    def test_frozen_window_never_completes_regardless_of_wall_clock_time(self) -> None:
        w = _window(status=PenaltyWindowStatus.FROZEN, active_period_started_at=None,
                     accumulated_active_hours=10.0, base_duration_hours=24.0)
        assert is_complete(w, FIXED_TIME + timedelta(days=365)) is False


class TestRemainingActiveHours:
    def test_full_remaining_at_start(self) -> None:
        w = _window(base_duration_hours=24.0, active_period_started_at=FIXED_TIME)
        assert remaining_active_hours(w, FIXED_TIME) == 24.0

    def test_partial_remaining(self) -> None:
        w = _window(base_duration_hours=24.0, active_period_started_at=FIXED_TIME)
        assert remaining_active_hours(w, FIXED_TIME + timedelta(hours=10)) == 14.0

    def test_never_negative(self) -> None:
        w = _window(base_duration_hours=24.0, active_period_started_at=FIXED_TIME)
        assert remaining_active_hours(w, FIXED_TIME + timedelta(hours=1000)) == 0.0
