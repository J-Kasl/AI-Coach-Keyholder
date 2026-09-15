"""
chaster/callback_service.py

ChasterCallbackService -- orchestrates a completed OAuth callback:
consume the state, exchange the code (or handle a provider error/
malformed callback), resolve which Chaster account authorized this
connection, persist it, and report a safe outcome. Framework-agnostic
-- plain str|None in, a plain result object out; fully testable
without aiohttp or a real Chaster server.

IMPORTANT, HONEST LIMITATION (see the final implementation report):
confirming WHICH Chaster account just completed authorization
requires an authenticated identity call this project's own research
did not confirm the existence/shape of -- the user-approved scope for
this slice explicitly listed only the Authorization and Token
endpoints and explicitly forbade guessing additional ones. Rather
than guess an endpoint that could misbehave against the real API,
identity resolution is an explicitly INJECTED dependency
(`resolve_identity`). Tests supply a fake. Production wiring
(bot/discord_bot.py) supplies `unconfirmed_identity_resolver`, which
always raises `ChasterIdentityResolutionUnavailable` -- meaning a
real callback fails safely at this one specific step, with a clear,
generic message, until a future slice confirms and implements the
real mechanism. Everything before this point (state consumption, code
exchange) is fully real and confirmed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from chaster.oauth_client import ChasterOAuthClient, ChasterTokenExchangeError
from chaster.repository import ChasterConnectionRepository, ChasterOAuthStateRepository

__all__ = [
    "ChasterCallbackService",
    "ChasterCallbackOutcome",
    "ChasterIdentity",
    "ChasterIdentityResolutionUnavailable",
    "unconfirmed_identity_resolver",
]


class ChasterIdentityResolutionUnavailable(RuntimeError):
    """Raised by `unconfirmed_identity_resolver` -- see this module's
    own docstring. Never includes the access token in its message."""


@dataclass(frozen=True, kw_only=True)
class ChasterIdentity:
    chaster_account_id: str
    chaster_username: str | None


def unconfirmed_identity_resolver(access_token: str) -> ChasterIdentity:
    """The production placeholder -- always raises. Deliberately NOT
    a guessed HTTP call to an unconfirmed endpoint. See this module's
    own docstring for the full reasoning."""
    raise ChasterIdentityResolutionUnavailable(
        "Chaster account identity resolution is not yet implemented -- the "
        "authenticated endpoint to confirm which Chaster account authorized "
        "this connection has not been confirmed against official Chaster "
        "documentation. This must be resolved before CHASTER-01A can complete "
        "a real connection."
    )


@dataclass(frozen=True, kw_only=True)
class ChasterCallbackOutcome:
    """A safe, generic outcome -- `message` is written assuming it may
    be shown directly to the end user (in the browser response) and is
    therefore never allowed to contain a token, code, or any other
    secret value."""
    success: bool
    user_id: str | None
    message: str


class ChasterCallbackService:
    def __init__(
        self, *, states: ChasterOAuthStateRepository, connections: ChasterConnectionRepository,
        oauth_client: ChasterOAuthClient, resolve_identity: Callable[[str], ChasterIdentity],
    ) -> None:
        self._states = states
        self._connections = connections
        self._oauth_client = oauth_client
        self._resolve_identity = resolve_identity

    def handle_callback(
        self, *, state: str | None, code: str | None, error: str | None, now: datetime,
    ) -> ChasterCallbackOutcome:
        if not state:
            return ChasterCallbackOutcome(
                success=False, user_id=None,
                message="Missing or malformed callback -- no state parameter.",
            )

        result = self._states.consume(state=state, now=now)
        if not result.matched:
            return ChasterCallbackOutcome(
                success=False, user_id=None,
                message=(
                    "This connection link is no longer valid. It may have already "
                    "been used, been replaced by a newer `chaster connect`, or the "
                    "link itself was invalid."
                ),
            )
        if result.expired:
            return ChasterCallbackOutcome(
                success=False, user_id=None,
                message="This connection link has expired. Please send `chaster connect` again.",
            )

        user_id = result.user_id
        assert user_id is not None  # matched and not expired always carries a user_id

        if error:
            # Chaster's own error/error_description query parameters
            # are NEVER echoed back verbatim -- they are provider-
            # controlled external content and could, in principle,
            # contain arbitrary text; only this fixed, generic message
            # is ever shown.
            return ChasterCallbackOutcome(
                success=False, user_id=user_id,
                message="The Chaster authorization was not completed.",
            )
        if not code:
            return ChasterCallbackOutcome(
                success=False, user_id=user_id,
                message="Missing or malformed callback -- no authorization code.",
            )

        try:
            tokens = self._oauth_client.exchange_code_for_tokens(code=code)
        except ChasterTokenExchangeError:
            return ChasterCallbackOutcome(
                success=False, user_id=user_id,
                message="Could not complete the connection to Chaster. Please try `chaster connect` again.",
            )

        try:
            identity = self._resolve_identity(tokens.access_token)
        except ChasterIdentityResolutionUnavailable:
            return ChasterCallbackOutcome(
                success=False, user_id=user_id,
                message="Could not complete the connection to Chaster. Please try again later.",
            )

        from datetime import timedelta
        self._connections.create_or_replace(
            user_id=user_id, chaster_account_id=identity.chaster_account_id,
            chaster_username=identity.chaster_username,
            access_token=tokens.access_token, refresh_token=tokens.refresh_token,
            access_token_expires_at=now + timedelta(seconds=tokens.expires_in),
            refresh_token_expires_at=now + timedelta(seconds=tokens.refresh_expires_in),
            granted_scopes=tokens.granted_scopes, now=now,
        )
        return ChasterCallbackOutcome(
            success=True, user_id=user_id,
            message="Chaster connected successfully. You can close this tab and return to Discord.",
        )
