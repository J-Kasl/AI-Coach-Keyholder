"""
chaster/emergency_unlock_provider.py

The narrow seam the real, confirmed Chaster unlock operation plugs
into. See docs/architecture/chaster_integration_technical_design.md
Section 25c/26 for the full history: the operation itself
(`POST /locks/{lockId}/emergency-unlock`) is confirmed via the
official OpenAPI spec; Option A (dynamic active-lock discovery at
request time, fail closed on zero/multiple candidates) is the
approved target-lock semantics.

`EmergencyUnlockProvider.attempt_unlock(access_token)`'s signature is
DELIBERATELY UNCHANGED -- it still accepts only a plaintext access
token, never a lock ID -- target selection happens entirely INSIDE
`RealChasterEmergencyUnlockProvider`, using Chaster's own live
response, so no caller (including the local HTTP listener) can ever
supply or influence which lock is targeted.

**One genuine, confirmed research gap remains, isolated here rather
than guessed**: this project has not confirmed `LockForWearer`'s
exact field-level JSON schema (its id/type/bondage-config field
names) from any authoritative first-party Chaster source -- two
raw-OpenAPI-JSON fetches, the Swagger UI (a client-side-rendered
shell, unreachable by this project's tools), and a targeted search
(surfacing only third-party SDKs, explicitly excluded as evidence)
all failed to reach it. `unconfirmed_lock_field_extractor` is the
ONLY place this gap is isolated -- it always raises
`LockFieldsUnconfirmedError`, exactly mirroring
`chaster/callback_service.py`'s own `unconfirmed_identity_resolver`
precedent for the same category of gap. Everything else in
`RealChasterEmergencyUnlockProvider` -- calling the confirmed
`GET /locks` endpoint, enforcing "exactly one active lock, fail
closed otherwise," and calling the confirmed
`POST /locks/{lockId}/emergency-unlock` endpoint -- is real,
implemented, and tested.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from chaster.lock_client import ChasterLockApiError, ChasterLockClient, EmergencyUnlockHttpOutcome

__all__ = [
    "EmergencyUnlockProvider",
    "ProviderUnlockOutcome",
    "ProviderUnlockResult",
    "UnwiredEmergencyUnlockProvider",
    "RealChasterEmergencyUnlockProvider",
    "LockEligibility",
    "LockFieldsUnconfirmedError",
    "unconfirmed_lock_field_extractor",
]


class ProviderUnlockOutcome(StrEnum):
    """Exactly two terminal outcomes for one attempted provider call --
    deliberately not more; a provider that couldn't be reached at all
    (network/timeout), found zero/multiple active locks, or found an
    ineligible lock are all FAILED, distinguished by `detail`, not
    separate enum values the caller would need to handle differently."""
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, kw_only=True)
class ProviderUnlockResult:
    """`detail` is always safe to log/audit -- never a token, never raw
    provider response content, a short human-readable reason only."""
    outcome: ProviderUnlockOutcome
    detail: str


class EmergencyUnlockProvider(Protocol):
    """Receives ONLY a plaintext access token (decrypted by the caller
    immediately before this call, per `chaster/repository.py`'s own
    load-only-at-point-of-use principle) -- no lock ID, no arbitrary
    API path, no other caller-supplied parameter, by design (Option A:
    target selection is the provider's own internal responsibility,
    never the caller's)."""

    def attempt_unlock(self, *, access_token: str) -> ProviderUnlockResult: ...


class UnwiredEmergencyUnlockProvider:
    """A provider that never touches the network at all -- used only
    in tests/fixtures that need a provider guaranteed not to make any
    HTTP call. Production now uses
    `RealChasterEmergencyUnlockProvider` (see
    chaster/emergency_unlock_server.py)."""

    def attempt_unlock(self, *, access_token: str) -> ProviderUnlockResult:
        return ProviderUnlockResult(
            outcome=ProviderUnlockOutcome.FAILED,
            detail="This provider never contacts Chaster. No request was sent.",
        )


class LockFieldsUnconfirmedError(RuntimeError):
    """Raised by `unconfirmed_lock_field_extractor` -- see this
    module's own header docstring. Never includes the raw lock dict
    (which could, in principle, carry lock-session content) in its
    message."""


@dataclass(frozen=True, kw_only=True)
class LockEligibility:
    """The result of examining ONE active lock's raw dict for
    emergency-unlock eligibility (bondage type + an enabled safety
    feature, per the confirmed endpoint's own documented
    prerequisites)."""
    lock_id: str
    eligible: bool
    reason: str


def unconfirmed_lock_field_extractor(raw_lock: dict) -> LockEligibility:
    """The production placeholder for the one remaining, genuinely
    unconfirmed piece -- always raises. See this module's own header
    docstring for the full reasoning. Deliberately NOT a guessed
    `raw_lock["_id"]`/`raw_lock["type"]`/etc. access -- that would be
    exactly the kind of schema guess this project's research
    discipline forbids for safety-critical code."""
    raise LockFieldsUnconfirmedError(
        "Chaster's LockForWearer response schema has not been confirmed from an "
        "authoritative first-party source -- this application cannot safely determine "
        "a lock's id, type, or bondage-safety configuration from the raw API response."
    )


class RealChasterEmergencyUnlockProvider:
    """Implements Option A (docs/architecture/chaster_integration_technical_design.md
    Section 26): discover the account's active lock(s) live, at
    request time, via the confirmed `GET /locks` endpoint; proceed
    ONLY if exactly one is found; check its documented emergency-unlock
    eligibility via the injected (currently always-failing, honestly)
    `lock_field_extractor`; call the confirmed
    `POST /locks/{lockId}/emergency-unlock` endpoint only for that one,
    freshly-discovered lock. Never accepts a lock ID from any caller."""

    def __init__(
        self, *, lock_client: ChasterLockClient | None = None,
        lock_field_extractor: Callable[[dict], LockEligibility] = unconfirmed_lock_field_extractor,
    ) -> None:
        self._lock_client = lock_client if lock_client is not None else ChasterLockClient()
        self._lock_field_extractor = lock_field_extractor

    def attempt_unlock(self, *, access_token: str) -> ProviderUnlockResult:
        try:
            active_locks = self._lock_client.list_active_locks(access_token=access_token)
        except ChasterLockApiError as exc:
            return ProviderUnlockResult(outcome=ProviderUnlockOutcome.FAILED, detail=f"Could not retrieve active locks: {exc}")

        if len(active_locks) == 0:
            return ProviderUnlockResult(outcome=ProviderUnlockOutcome.FAILED, detail="No active Chaster lock found.")
        if len(active_locks) > 1:
            return ProviderUnlockResult(
                outcome=ProviderUnlockOutcome.FAILED,
                detail=f"Found {len(active_locks)} active Chaster locks -- cannot determine an unambiguous target. No lock was unlocked.",
            )

        raw_lock = active_locks[0]
        try:
            eligibility = self._lock_field_extractor(raw_lock)
        except LockFieldsUnconfirmedError as exc:
            return ProviderUnlockResult(
                outcome=ProviderUnlockOutcome.FAILED,
                detail=f"Could not determine emergency-unlock eligibility for the active lock: {exc}",
            )

        if not eligibility.eligible:
            return ProviderUnlockResult(
                outcome=ProviderUnlockOutcome.FAILED,
                detail=f"The active lock does not meet Chaster's documented emergency-unlock prerequisites: {eligibility.reason}",
            )

        try:
            http_result = self._lock_client.emergency_unlock(access_token=access_token, lock_id=eligibility.lock_id)
        except ChasterLockApiError as exc:
            return ProviderUnlockResult(outcome=ProviderUnlockOutcome.FAILED, detail=f"Emergency-unlock request failed: {exc}")

        if http_result.outcome is EmergencyUnlockHttpOutcome.SUCCEEDED:
            return ProviderUnlockResult(outcome=ProviderUnlockOutcome.SUCCEEDED, detail="Chaster confirmed the emergency unlock (HTTP 204).")

        detail_by_outcome = {
            EmergencyUnlockHttpOutcome.NOT_ELIGIBLE: "Chaster rejected the emergency unlock -- the lock is not a bondage lock, has no safety feature enabled, or is not locked.",
            EmergencyUnlockHttpOutcome.UNAUTHORIZED: "Chaster rejected the emergency unlock -- not authorized.",
            EmergencyUnlockHttpOutcome.FORBIDDEN: "Chaster rejected the emergency unlock -- only the wearer can emergency-unlock.",
            EmergencyUnlockHttpOutcome.NOT_FOUND: "Chaster rejected the emergency unlock -- the target lock could not be found (it may have changed since discovery).",
            EmergencyUnlockHttpOutcome.UNEXPECTED: f"Chaster returned an unexpected response (HTTP {http_result.http_status}).",
        }
        return ProviderUnlockResult(outcome=ProviderUnlockOutcome.FAILED, detail=detail_by_outcome[http_result.outcome])
