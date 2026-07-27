"""
infrastructure/clock.py

The single, injected source of the current time for this system.

Why this exists (see infrastructure/README.md for the full rationale):
`activity_authorization_technical_design.md` 16.7 establishes that no
absolute-timestamp-based deadline in this system (a confirmation
window, a freeze expiry, a grant validity window, a startup lease) may
be computed from an in-memory timer or from an uncoordinated,
directly-called system clock. Every module reads "now" from exactly
one injected `Clock`, so that:

  - restart/crash-recovery scenarios can be tested deterministically,
    by substituting a `FrozenClock` for the real one, without waiting
    on a wall clock or mocking a global function;
  - a future, database-backed `MonotonicGuardedClock`
    (`activity_authorization_technical_design.md` 16.7) can be
    introduced later, system-wide, by changing what gets injected —
    never by hunting down scattered `datetime.now()` calls.

Convention enforced by this module (`implementation_conventions.md`
Part I, Section 8 — Naming Conventions; the general principle in
Section 10 that time, like a restart-safe lock, must never depend on
an untracked in-memory/ambient source): production code calls
`clock.now()` on an injected `Clock` instance. It never calls
`datetime.now()` or `datetime.utcnow()` directly. This module is the
one, explicit exception — `SystemClock.now()` is where the real system
clock is actually read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "SystemClock", "FrozenClock"]


@runtime_checkable
class Clock(Protocol):
    """
    The dependency-injection interface every module depends on instead
    of calling `datetime.now()`/`datetime.utcnow()` itself.

    A structural Protocol, not an abstract base class: any object with
    a matching `now() -> datetime` method satisfies it, so production
    code, `SystemClock`, `FrozenClock`, and any future
    `MonotonicGuardedClock` are all interchangeable without a shared
    base class. `@runtime_checkable` makes `isinstance(x, Clock)`
    usable in tests and defensive assertions.
    """

    def now(self) -> datetime:
        """
        The current time, always timezone-aware and always in UTC.

        Implementations MUST NOT return a naive datetime, and MUST NOT
        return a datetime in any timezone other than UTC — every
        caller in this system is entitled to assume both without
        re-checking.
        """
        ...


def _require_aware_utc(dt: datetime, *, param_name: str) -> datetime:
    """
    Validates and normalizes a datetime supplied to a Clock
    implementation (never used on the *return* path of `now()` for
    SystemClock, which constructs an already-correct value directly).

    A naive datetime is rejected outright — it is inherently ambiguous
    about which timezone it represents, and this system's absolute
    timestamps (deadlines, expiries, leases) must never be ambiguous.
    An aware datetime in a non-UTC timezone is accepted and converted,
    not rejected — the caller's intent is unambiguous, so normalizing
    is more useful than forcing every call site to convert first.
    """
    if dt.tzinfo is None:
        raise ValueError(
            f"{param_name} must be a timezone-aware datetime; got a naive "
            f"datetime ({dt!r}). Use datetime(..., tzinfo=timezone.utc) or "
            f"datetime.now(timezone.utc)."
        )
    return dt.astimezone(timezone.utc)


class SystemClock:
    """
    The production `Clock` implementation. Wraps
    `datetime.now(timezone.utc)` — the ONE place in this system that
    call is allowed to appear outside this module and its tests.

    Does NOT guarantee monotonicity — two successive calls may return
    an equal or even backward-moving value if the underlying operating
    system clock is adjusted (NTP correction, manual change, VM clock
    drift). Guaranteeing "now never moves backward, even across a
    restart" is the explicit job of a future, database-backed
    `MonotonicGuardedClock`
    (`activity_authorization_technical_design.md` 16.7, not yet
    implemented — see infrastructure/README.md), layered on top of this
    class, not a property of `SystemClock` itself.

    Stateless and cheap to construct; a single shared instance is fine,
    but nothing about this class requires it to be a singleton — the
    only requirement is that production code obtain its `Clock`
    through dependency injection, not through a module-level global.
    """

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """
    The test `Clock` implementation. Time does not pass on its own —
    it only changes when the test explicitly calls `advance()` or
    `set()`. This is what makes restart/crash-recovery and
    deadline-expiry scenarios (e.g. `penalty_window_technical_design.md`
    T1, `activity_authorization_technical_design.md` RT7,
    `hygiene_privilege_technical_design.md` HRT2) deterministic to test:
    a test can construct a grant with a known `expires_at`, advance the
    clock past it by an exact amount, and assert the expected recovery
    behavior — with no reliance on real elapsed wall-clock time and no
    flakiness from test execution speed.

    Deliberately unopinionated about direction: `advance()` accepts a
    negative `timedelta`, and `set()` accepts a time earlier than the
    current one. This is intentional, not an oversight — a
    `FrozenClock` is a raw test double for exercising a *consumer's*
    behavior under a given time sequence, including a backward jump
    (e.g. `activity_authorization_technical_design.md` RT14, which
    specifically tests how the system behaves when the clock moves
    backward). Guarding against backward motion is the job of a
    monotonic-guarding Clock implementation layered on top of a real
    clock (16.7's `MonotonicGuardedClock`, not yet implemented — see
    infrastructure/README.md) — never the job of the test double used
    to test that guard.
    """

    def __init__(self, initial: datetime) -> None:
        """
        `initial` is required, not defaulted, on purpose: a test's
        starting time should always be an explicit, visible choice at
        the point the clock is constructed, not an implicit "whatever
        this module happened to default to."
        """
        self._current = _require_aware_utc(initial, param_name="initial")

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> datetime:
        """
        Moves the clock forward (or, with a negative `delta`, backward)
        by `delta` relative to its current value. Returns the new
        value for convenience (e.g. `expires_at = clock.advance(timedelta(minutes=30))`
        when a test wants "30 minutes from now, and the clock should
        now report that time too").
        """
        self._current = self._current + delta
        return self._current

    def set(self, when: datetime) -> None:
        """
        Jumps the clock directly to an absolute time, forward or
        backward, without regard to the current value — for tests that
        need to land on a specific timestamp (e.g. exactly matching a
        fixture's `expires_at`) rather than compute a relative offset.
        """
        self._current = _require_aware_utc(when, param_name="when")
