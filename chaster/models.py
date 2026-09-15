"""
chaster/models.py

docs/architecture/chaster_integration_technical_design.md (draft, not
approved for implementation as a whole). This module implements ONLY
CHASTER-01A's own data shapes -- OAuth connection foundation. No lock
fetching, no provider observations (CHASTER-01B, not built here).

Two entities, both genuinely one-row-per-user (never accumulated --
see task_catalog/README.md's own "id vs. user_id as PK" reasoning
this module mirrors for the same structural reason
`user_preferences` already uses it):

- ChasterOAuthState -- a pending, single-use OAuth `state` value.
  Replaced (never accumulated) by a new `chaster connect`; consumed
  exactly once by a real callback.
- ChasterConnection -- a durable, encrypted-at-rest OAuth connection.
  `connection_status` has exactly two values -- see
  ConnectionStatus's own docstring for why the design deliberately
  does NOT add more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ConnectionStatus(StrEnum):
    """Exactly two values -- deliberately not more, mirroring this
    project's own repeatedly-demonstrated minimal-state-model
    discipline (task_runtime's own "exactly three states... not added
    'for the future'"). Every other state you might expect ("not
    connected", "disconnected", "authorization in progress", "token
    refresh required") is represented structurally instead of as a
    stored value here:
    - "not connected"/"disconnected": no ChasterConnection row exists
      at all -- absence of a row IS the state, not a status value.
    - "authorization in progress": lives exclusively in
      ChasterOAuthState, never here -- the two entities are never
      both "about" the same in-progress attempt at once.
    - "token refresh required": transient runtime behavior (a refresh
      is attempted transparently at the moment a token is needed) --
      never persisted. Only if the refresh itself fails does the
      status change, to NEEDS_REAUTHORIZATION.
    - "unusable/corrupt credentials" (e.g. TokenDecryptionError):
      maps to NEEDS_REAUTHORIZATION too, reusing this same value
      rather than inventing a new one -- from the user's own
      perspective, both cases mean the same thing: reconnect."""
    ACTIVE = "active"
    NEEDS_REAUTHORIZATION = "needs_reauthorization"


@dataclass(frozen=True, kw_only=True)
class ChasterOAuthState:
    """A pending, single-use OAuth `state` value bound to exactly one
    internal user (user_accounts.id, never a raw Discord snowflake).
    Frozen/append-only in spirit, but the underlying table is NOT
    append-only in the LockReport sense -- a second `chaster connect`
    for the same user REPLACES this row entirely (see
    ChasterOAuthStateRepository.create_or_replace()), and a
    successful or failed callback DELETES it -- there is never more
    than one row per user, ever."""
    state: str
    user_id: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, kw_only=True)
class ChasterConnection:
    """A durable Chaster OAuth connection for exactly one internal
    user. `encrypted_access_token`/`encrypted_refresh_token` are
    Fernet ciphertext (TEXT, never BLOB -- a Fernet token is already
    URL-safe base64 ASCII) -- this dataclass itself never holds
    plaintext; decryption happens only at the repository boundary,
    immediately before a value is actually needed for an outbound
    call (chaster_integration_technical_design.md Section 10's own
    "load-only-at-point-of-use" principle).

    `__repr__` is intentionally NOT the dataclass default: the
    default would include both encrypted-token fields verbatim,
    which must never appear in a stray print()/traceback/test-failure
    message even as ciphertext -- see this module's own tests."""
    user_id: str
    chaster_account_id: str
    chaster_username: str | None
    encrypted_access_token: str
    encrypted_refresh_token: str
    encryption_key_version: str | None
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime
    granted_scopes: tuple[str, ...]
    connection_status: ConnectionStatus
    created_at: datetime
    updated_at: datetime

    def __repr__(self) -> str:
        return (
            f"ChasterConnection(user_id={self.user_id!r}, "
            f"chaster_account_id={self.chaster_account_id!r}, "
            f"chaster_username={self.chaster_username!r}, "
            f"encrypted_access_token=<redacted>, encrypted_refresh_token=<redacted>, "
            f"connection_status={self.connection_status!r})"
        )
