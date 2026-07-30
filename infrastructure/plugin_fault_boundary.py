"""
infrastructure/plugin_fault_boundary.py

PluginFaultBoundary -- wraps every plugin-registered handler
invocation (event consumer or command) so a bug in one plugin can
never crash the caller, and so a chronically slow handler is detected
and counted toward that plugin's circuit breaker (PLUG-6/PLUG-7).

A real, concrete gap found while writing plugin_architecture_proposal.md
Section 1: `ConsumerRegistry.dispatch()` gives every consumer its own
database transaction, but has NO exception boundary at all -- an
unhandled exception from a handler propagates straight through
`dispatch()` and `process_pending_events()`, capable of crashing the
whole `on_system_startup()` call today. This module closes that gap
for plugin-registered handlers specifically (this document's scope);
the same gap for first-party domain consumers remains open, flagged as
plugin_architecture_proposal.md Section 25 Risk 1 -- not fixed here.

PLUG-7 naming, v1.2 (implementation-alignment patch -- corrected from
the initial v1.1 wording, which called this "timeout" although it
never interrupted anything): what this module actually enforces is an
**execution budget**, not a timeout. Every call is measured; a call
whose wall-clock duration exceeds `execution_budget_seconds` is logged
and counted toward the failure threshold once it returns -- but the
handler is never interrupted, and a genuinely hung (infinite-loop)
synchronous handler will hang this call, and therefore its caller,
indefinitely. This is a real, current limitation, not a detail hidden
behind reassuring vocabulary: `plugin_architecture_proposal.md`
Section 26 Open Question 4 still lists a true hard timeout as
unresolved, and this module does not resolve it.

Why not a thread-with-join-timeout (the obvious first instinct for a
hard timeout against a synchronous handler): considered and rejected.
`infrastructure/database.py` opens its sqlite3 connection without
`check_same_thread=False`, so a handler touching the shared
`core.transaction()` from a different thread than the one that opened
the connection would raise `sqlite3.ProgrammingError` -- a real bug,
not a hypothetical one, given a plugin's read capabilities (PLUG-5)
exist specifically so a handler CAN touch the database. A genuine hard
timeout needs either a fully asynchronous handler contract with
cooperative cancellation, a separate process, or some other truly
preemptible execution boundary -- explicitly deferred, not built here.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, TypeVar

logger = logging.getLogger("ai_coach_keyholder.plugin_fault_boundary")

T = TypeVar("T")

# BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
# Temporary executable value pending an explicit ownership decision.
# plugin_architecture_proposal.md Section 26, Open Question 1 --
# calibration guess, not measured against real plugin behavior. A call
# whose duration exceeds this is logged and counted toward the failure
# threshold once it returns (see the module docstring: this is an
# execution budget, not a hard timeout -- nothing is interrupted).
DEFAULT_HANDLER_EXECUTION_BUDGET_SECONDS = 5.0

# BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
# Temporary executable value pending an explicit ownership decision.
# How many failures within DEFAULT_FAILURE_WINDOW_SECONDS before a
# plugin is auto-disabled (plugin_architecture_proposal.md Decision 5).
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_FAILURE_WINDOW_SECONDS = 300.0  # 5 minutes

__all__ = ["PluginInvocationResult", "PluginFaultBoundary", "DEFAULT_HANDLER_EXECUTION_BUDGET_SECONDS",
           "DEFAULT_FAILURE_THRESHOLD", "DEFAULT_FAILURE_WINDOW_SECONDS"]


@dataclass(frozen=True, kw_only=True)
class PluginInvocationResult:
    """
    What every boundary-wrapped call returns -- never the handler's own
    return value mixed directly with failure signaling, so a caller can
    never mistake "the handler ran and returned None" for "the handler
    failed."

    `slow_execution` (renamed from `exceeded_timeout` in v1.2): true
    when the handler's measured duration exceeded its execution
    budget. Deliberately not called `timed_out` -- nothing was
    interrupted; the handler ran to completion and this field reports
    that fact after the fact, not during it.
    """
    succeeded: bool
    value: object = None
    error: str | None = None
    slow_execution: bool = False
    duration_seconds: float = 0.0


class PluginFaultBoundary:
    """
    One instance per plugin -- PLUG-6/PLUG-7 and the failure-count
    circuit breaker (Decision 5) are all per-plugin; a failure in one
    plugin's handler must never count against, or disable, a different
    plugin. `PluginRegistry` (not yet built) is expected to hold
    exactly one `PluginFaultBoundary` per loaded plugin.
    """

    def __init__(
        self, plugin_name: str, *,
        execution_budget_seconds: float = DEFAULT_HANDLER_EXECUTION_BUDGET_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        failure_window_seconds: float = DEFAULT_FAILURE_WINDOW_SECONDS,
    ) -> None:
        self.plugin_name = plugin_name
        self.execution_budget_seconds = execution_budget_seconds
        self.failure_threshold = failure_threshold
        self.failure_window_seconds = failure_window_seconds
        self._failure_timestamps: list[datetime] = []
        self.disabled_due_to_errors = False

    def call(self, handler: Callable[[], T], *, context: str, now: datetime) -> PluginInvocationResult:
        """
        PLUG-6: runs `handler` (a zero-argument callable -- the caller
        closes over whatever arguments the actual event/command handler
        needs) inside a try/except that never propagates. PLUG-7
        (v1.2 wording): measures wall-clock duration against
        `execution_budget_seconds` -- an execution budget, not a hard
        timeout; see the module docstring. A handler that never returns
        will hang this call, and its caller, indefinitely -- this
        method cannot and does not protect against that.

        Never raises for an ordinary handler failure. Always returns a
        PluginInvocationResult. If this plugin is already
        `disabled_due_to_errors`, the handler is not invoked at all.
        """
        if self.disabled_due_to_errors:
            return PluginInvocationResult(
                succeeded=False, error=f"plugin {self.plugin_name!r} is disabled_due_to_errors",
            )

        start = time.monotonic()
        try:
            value = handler()
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: PLUG-6's entire purpose
            duration = time.monotonic() - start
            logger.exception(
                "Plugin %r handler raised (context=%s, duration=%.3fs)",
                self.plugin_name, context, duration,
            )
            self._record_failure(now)
            return PluginInvocationResult(succeeded=False, error=str(exc), duration_seconds=duration)

        duration = time.monotonic() - start
        if duration > self.execution_budget_seconds:
            logger.warning(
                "Plugin %r handler exceeded its execution budget (context=%s, duration=%.3fs, budget=%.3fs) -- "
                "ran to completion (an execution budget, not a hard timeout; see module docstring), "
                "counted as a failure.",
                self.plugin_name, context, duration, self.execution_budget_seconds,
            )
            self._record_failure(now)
            return PluginInvocationResult(
                succeeded=False, value=value, slow_execution=True, duration_seconds=duration,
                error=f"exceeded {self.execution_budget_seconds}s execution budget",
            )

        return PluginInvocationResult(succeeded=True, value=value, duration_seconds=duration)

    def _record_failure(self, now: datetime) -> None:
        """
        Rolling failure count within `failure_window_seconds` (Decision
        5) -- crossing `failure_threshold` auto-transitions
        `disabled_due_to_errors`, a recorded, logged, never-silent
        transition (plugin_architecture_proposal.md Section 14),
        never a crash and never a silent no-op.
        """
        self._failure_timestamps.append(now)
        window_start = now - timedelta(seconds=self.failure_window_seconds)
        self._failure_timestamps = [t for t in self._failure_timestamps if t >= window_start]
        if len(self._failure_timestamps) >= self.failure_threshold and not self.disabled_due_to_errors:
            self.disabled_due_to_errors = True
            logger.error(
                "Plugin %r auto-disabled: %d failures within %.0fs (threshold=%d).",
                self.plugin_name, len(self._failure_timestamps), self.failure_window_seconds, self.failure_threshold,
            )
