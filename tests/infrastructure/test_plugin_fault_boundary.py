"""tests/infrastructure/test_plugin_fault_boundary.py"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from infrastructure.plugin_fault_boundary import PluginFaultBoundary

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestPlug6ExceptionBoundary:
    def test_a_successful_handler_returns_its_value(self) -> None:
        boundary = PluginFaultBoundary("goal_celebration")
        result = boundary.call(lambda: 42, context="test", now=FIXED_TIME)
        assert result.succeeded is True
        assert result.value == 42

    def test_a_raising_handler_never_propagates(self) -> None:
        boundary = PluginFaultBoundary("goal_celebration")

        def boom():
            raise RuntimeError("simulated plugin bug")

        result = boundary.call(boom, context="test", now=FIXED_TIME)
        assert result.succeeded is False
        assert "simulated plugin bug" in result.error

    def test_a_raising_handler_does_not_crash_the_caller(self) -> None:
        """The whole point of PLUG-6 -- this test would itself crash if
        .call() propagated instead of catching."""
        boundary = PluginFaultBoundary("goal_celebration")
        boundary.call(lambda: 1 / 0, context="test", now=FIXED_TIME)
        # reaching this line at all is the assertion


class TestPlug7ExecutionBudget:
    def test_a_fast_handler_is_not_flagged(self) -> None:
        boundary = PluginFaultBoundary("goal_celebration", execution_budget_seconds=1.0)
        result = boundary.call(lambda: "ok", context="test", now=FIXED_TIME)
        assert result.slow_execution is False
        assert result.succeeded is True

    def test_a_slow_handler_is_flagged_but_its_value_is_still_returned(self) -> None:
        """An execution budget, not a hard timeout (module docstring)
        -- the handler still runs to completion (a *finite* sleep
        here, deliberately -- see
        test_an_actually_hanging_handler_is_a_known_unresolved_limitation
        below for why a genuinely infinite handler cannot be safely
        exercised in this test suite at all); the value is preserved
        even though the call is counted as a failure."""
        boundary = PluginFaultBoundary("goal_celebration", execution_budget_seconds=0.01)
        result = boundary.call(lambda: (time.sleep(0.05), "done")[1], context="test", now=FIXED_TIME)
        assert result.slow_execution is True
        assert result.succeeded is False
        assert result.value == "done"

    def test_an_actually_hanging_handler_is_a_known_unresolved_limitation(self) -> None:
        """Deliberately NOT a test of interruption -- PLUG-7 (v1.2) is
        an execution budget, not a hard timeout, and there is no safe
        way to write a test proving a hard timeout fires when none
        exists. A genuinely infinite handler (e.g. `while True: pass`)
        would hang this test itself, the same way it would hang the
        real caller -- that is precisely the documented, open
        limitation (plugin_architecture_proposal.md Section 26, Open
        Question 4), not something this test suite can responsibly
        pretend to verify is handled. This test exists only to make that
        absence explicit, rather than silently missing."""
        assert True  # no interruption mechanism exists to test (see docstring)


class TestDecision5CircuitBreaker:
    def test_repeated_failures_within_window_disable_the_plugin(self) -> None:
        boundary = PluginFaultBoundary("goal_celebration", failure_threshold=3, failure_window_seconds=300.0)

        def boom():
            raise RuntimeError("boom")

        for i in range(3):
            boundary.call(boom, context="test", now=FIXED_TIME + timedelta(seconds=i))

        assert boundary.disabled_due_to_errors is True

    def test_disabled_plugin_handler_is_not_invoked_at_all(self) -> None:
        boundary = PluginFaultBoundary("goal_celebration", failure_threshold=1, failure_window_seconds=300.0)
        boundary.call(lambda: 1 / 0, context="test", now=FIXED_TIME)
        assert boundary.disabled_due_to_errors is True

        calls = []
        result = boundary.call(lambda: calls.append(1), context="test", now=FIXED_TIME + timedelta(seconds=1))
        assert calls == []  # never invoked
        assert result.succeeded is False
        assert "disabled_due_to_errors" in result.error

    def test_failures_outside_the_window_do_not_count(self) -> None:
        boundary = PluginFaultBoundary("goal_celebration", failure_threshold=2, failure_window_seconds=60.0)

        def boom():
            raise RuntimeError("boom")

        boundary.call(boom, context="test", now=FIXED_TIME)
        # second failure is 5 minutes later -- outside the 60s window, so
        # only 1 failure is "in window" at that point, below threshold=2
        boundary.call(boom, context="test", now=FIXED_TIME + timedelta(minutes=5))
        assert boundary.disabled_due_to_errors is False

    def test_a_failure_in_one_plugin_never_affects_another(self) -> None:
        """Each PluginFaultBoundary instance is independent -- a
        different plugin's failures never count against this one."""
        plugin_a = PluginFaultBoundary("plugin_a", failure_threshold=1)
        plugin_b = PluginFaultBoundary("plugin_b", failure_threshold=1)

        plugin_a.call(lambda: 1 / 0, context="test", now=FIXED_TIME)

        assert plugin_a.disabled_due_to_errors is True
        assert plugin_b.disabled_due_to_errors is False


class TestPluginInvocationResultShape:
    def test_result_never_conflates_a_none_return_value_with_failure(self) -> None:
        boundary = PluginFaultBoundary("goal_celebration")
        result = boundary.call(lambda: None, context="test", now=FIXED_TIME)
        assert result.succeeded is True
        assert result.value is None
        assert result.error is None
