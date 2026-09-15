"""
chaster/emergency_unlock.py

EmergencyUnlockService -- the PC-local safety-control plane. NOT part
of the normal control plane (Discord, the model, Conversation Engine,
Task Runtime, normal `chaster connect`/callback flow) -- this module
imports none of them, by design (see this project's own AI-independence
tests). Deliberately usable even if the Discord bot process itself is
hung, crashed, or otherwise malfunctioning: see
chaster/emergency_unlock_server.py, the SEPARATE standalone process
this service runs inside of.

Safety model (docs/architecture/chaster_integration_technical_design.md
Section 25c):
1. Authenticate the local operator against a dedicated, separate
   secret (`CHASTER_EMERGENCY_UNLOCK_SECRET`) -- never the Chaster
   client secret, never the token-encryption key, compared with
   `secrets.compare_digest` (constant-time, resists timing attacks).
2. Write an immutable audit event for every request, whatever the
   outcome -- reusing `infrastructure.outbox`, this project's own
   existing, tested, domain-agnostic append-only event log. No new
   migration, no new schema.
3. If authenticated and a Chaster connection exists for the
   configured account, attempt exactly one operation -- unlock --
   through the narrow `EmergencyUnlockProvider` seam. Production
   wiring uses `UnwiredEmergencyUnlockProvider`, which never
   contacts Chaster (see that module's own docstring for why).
4. Never claims success unless the provider itself reported success.
   Never touches `lock_state` -- a provider-observed result is never
   fabricated into `LockKnowledgeState.LOCKED_USER_REPORTED`/
   `UNLOCKED_USER_REPORTED`; if a real provider is ever confirmed,
   how (or whether) a successful emergency unlock should be reflected
   in the existing Lock State model is an explicit, separate,
   future decision -- not silently decided here.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from chaster.emergency_unlock_provider import EmergencyUnlockProvider, ProviderUnlockOutcome
from chaster.repository import ChasterConnectionRepository
from infrastructure.database import Database as CoreDatabase
from infrastructure.outbox import DomainEvent, write_event

__all__ = [
    "EmergencyUnlockService",
    "EmergencyUnlockRequestResult",
    "EmergencyUnlockRequestStatus",
]

SOURCE_MODULE = "chaster_emergency"

# Event types written to the shared outbox (infrastructure/outbox.py) --
# this module's own, narrow slice of this project's existing event
# vocabulary, following the same "module.thing_happened" naming
# convention already used elsewhere (e.g. "rules.consent_recorded").
EVENT_REQUEST_ACCEPTED = "chaster_emergency.request_accepted"
EVENT_AUTH_FAILED = "chaster_emergency.auth_failed"
EVENT_NO_CONNECTION = "chaster_emergency.no_connection"
EVENT_PROVIDER_ATTEMPTED = "chaster_emergency.provider_attempted"
EVENT_PROVIDER_SUCCEEDED = "chaster_emergency.provider_succeeded"
EVENT_PROVIDER_FAILED = "chaster_emergency.provider_failed"


class EmergencyUnlockRequestStatus(StrEnum):
    """The terminal status of one emergency-unlock request. Each value
    corresponds to exactly one of the audit event types above having
    been the LAST event written for this request -- the status here
    and the audit trail can never disagree, since both are derived
    from the same code path, not tracked independently."""
    AUTH_FAILED = "auth_failed"
    NO_CONNECTION = "no_connection"
    PROVIDER_SUCCEEDED = "provider_succeeded"
    PROVIDER_FAILED = "provider_failed"


@dataclass(frozen=True, kw_only=True)
class EmergencyUnlockRequestResult:
    """Safe to show directly to the local operator -- `message` never
    contains a secret, a token, or raw provider response content."""
    status: EmergencyUnlockRequestStatus
    message: str


class EmergencyUnlockService:
    def __init__(
        self, db_path: str | Path, *, core: CoreDatabase | None = None,
        connections: ChasterConnectionRepository, provider: EmergencyUnlockProvider,
        expected_secret: str | None,
    ) -> None:
        self.db_path = Path(db_path)
        self._core = core if core is not None else CoreDatabase(self.db_path)
        self._connections = connections
        self._provider = provider
        self._expected_secret = expected_secret

    def request_unlock(self, *, presented_secret: str, user_id: str, now: datetime) -> EmergencyUnlockRequestResult:
        """The one operation this entire service exposes. `presented_secret`
        is never logged, never included in any audit payload, never
        echoed back in the result -- only whether it matched."""
        if not self._authenticate(presented_secret):
            self._audit(EVENT_AUTH_FAILED, user_id=user_id, now=now, detail={})
            return EmergencyUnlockRequestResult(
                status=EmergencyUnlockRequestStatus.AUTH_FAILED,
                message="Authentication failed.",
            )

        self._audit(EVENT_REQUEST_ACCEPTED, user_id=user_id, now=now, detail={})

        connection = self._connections.get(user_id)
        if connection is None:
            self._audit(EVENT_NO_CONNECTION, user_id=user_id, now=now, detail={})
            return EmergencyUnlockRequestResult(
                status=EmergencyUnlockRequestStatus.NO_CONNECTION,
                message="No Chaster connection exists for this account -- nothing to unlock.",
            )

        # Decrypted only now, immediately before the one call that
        # actually needs it (chaster/repository.py's own
        # load-only-at-point-of-use principle) -- never held any
        # longer, never returned from this method.
        access_token = self._connections.get_decrypted_access_token(user_id, now=now)
        self._audit(
            EVENT_PROVIDER_ATTEMPTED, user_id=user_id, now=now,
            detail={"chaster_account_id": connection.chaster_account_id},
        )
        result = self._provider.attempt_unlock(access_token=access_token)

        if result.outcome is ProviderUnlockOutcome.SUCCEEDED:
            self._audit(
                EVENT_PROVIDER_SUCCEEDED, user_id=user_id, now=now,
                detail={"chaster_account_id": connection.chaster_account_id, "detail": result.detail},
            )
            return EmergencyUnlockRequestResult(
                status=EmergencyUnlockRequestStatus.PROVIDER_SUCCEEDED, message=result.detail,
            )

        self._audit(
            EVENT_PROVIDER_FAILED, user_id=user_id, now=now,
            detail={"chaster_account_id": connection.chaster_account_id, "detail": result.detail},
        )
        return EmergencyUnlockRequestResult(
            status=EmergencyUnlockRequestStatus.PROVIDER_FAILED, message=result.detail,
        )

    def _authenticate(self, presented_secret: str) -> bool:
        if not self._expected_secret:
            # Fails closed -- an unconfigured secret must never be
            # treated as "anything is accepted" or "nothing is
            # accepted (skip the check)"; it is always a hard reject.
            return False
        if not presented_secret:
            return False
        return secrets.compare_digest(presented_secret, self._expected_secret)

    def _audit(self, event_type: str, *, user_id: str, now: datetime, detail: dict) -> None:
        # detail is deliberately never allowed to carry a secret,
        # token, or raw provider response -- every call site above
        # passes only user_id/chaster_account_id/a short detail
        # string, never anything decrypted.
        payload = {"user_id": user_id, **detail}
        with self._core.transaction() as tx:
            write_event(
                tx,
                DomainEvent(event_type=event_type, source_module=SOURCE_MODULE, payload=payload, occurred_at=now),
            )
