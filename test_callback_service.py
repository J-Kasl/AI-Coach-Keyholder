"""
tests/chaster/test_callback_service.py

All Chaster HTTP traffic mocked via a fake ChasterOAuthClient (or by
patching requests) -- no real network calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from chaster.callback_service import (
    ChasterCallbackService,
    ChasterIdentity,
    ChasterIdentityResolutionUnavailable,
    build_real_identity_resolver,
    unconfirmed_identity_resolver,
)
from chaster.models import ConnectionStatus
from chaster.oauth_client import ChasterOAuthClient, ChasterTokenExchangeError, ChasterTokenResponse
from chaster.repository import ChasterConnectionRepository, ChasterOAuthStateRepository
from chaster.token_encryptor import TokenEncryptor
from infrastructure.database import Database as CoreDatabase

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(c)
    with c.raw_connection() as conn:
        conn.execute(
            "INSERT INTO user_accounts (id, created_at, last_seen_at) VALUES (?, ?, ?)",
            ("u1", FIXED_TIME.isoformat(), FIXED_TIME.isoformat()),
        )
        conn.commit()
    return c


@pytest.fixture
def states(core: CoreDatabase, tmp_path: Path) -> ChasterOAuthStateRepository:
    return ChasterOAuthStateRepository(tmp_path / "test.db", core=core)


@pytest.fixture
def connections(core: CoreDatabase, tmp_path: Path) -> ChasterConnectionRepository:
    return ChasterConnectionRepository(tmp_path / "test.db", core=core, encryptor=TokenEncryptor(Fernet.generate_key()))


@pytest.fixture
def oauth_client() -> MagicMock:
    return MagicMock(spec=ChasterOAuthClient)


def _fake_identity(access_token: str) -> ChasterIdentity:
    return ChasterIdentity(chaster_account_id="chaster-acc-1", chaster_username="wearer1")


def _service(states, connections, oauth_client, resolve_identity=_fake_identity) -> ChasterCallbackService:
    return ChasterCallbackService(states=states, connections=connections, oauth_client=oauth_client, resolve_identity=resolve_identity)


class TestSuccessfulCallback:
    def test_successful_callback_persists_an_active_connection(self, states, connections, oauth_client) -> None:
        oauth_client.exchange_code_for_tokens.return_value = ChasterTokenResponse(
            access_token="real-at", refresh_token="real-rt", expires_in=300, refresh_expires_in=1800,
            granted_scopes=("locks",),
        )
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        outcome = _service(states, connections, oauth_client).handle_callback(
            state=state, code="real-code", error=None, now=FIXED_TIME,
        )
        assert outcome.success is True
        assert outcome.user_id == "u1"
        connection = connections.get("u1")
        assert connection is not None
        assert connection.connection_status == ConnectionStatus.ACTIVE
        assert connection.chaster_account_id == "chaster-acc-1"

    def test_success_message_never_contains_a_token_value(self, states, connections, oauth_client) -> None:
        oauth_client.exchange_code_for_tokens.return_value = ChasterTokenResponse(
            access_token="UNIQUE-ACCESS-TOKEN", refresh_token="UNIQUE-REFRESH-TOKEN",
            expires_in=300, refresh_expires_in=1800, granted_scopes=("locks",),
        )
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        outcome = _service(states, connections, oauth_client).handle_callback(
            state=state, code="real-code", error=None, now=FIXED_TIME,
        )
        assert "UNIQUE-ACCESS-TOKEN" not in outcome.message
        assert "UNIQUE-REFRESH-TOKEN" not in outcome.message


class TestMissingOrMalformedCallback:
    def test_missing_state_fails_safely(self, states, connections, oauth_client) -> None:
        outcome = _service(states, connections, oauth_client).handle_callback(
            state=None, code="c", error=None, now=FIXED_TIME,
        )
        assert outcome.success is False
        assert outcome.user_id is None

    def test_unknown_state_fails_safely(self, states, connections, oauth_client) -> None:
        outcome = _service(states, connections, oauth_client).handle_callback(
            state="never-existed", code="c", error=None, now=FIXED_TIME,
        )
        assert outcome.success is False

    def test_missing_code_with_valid_state_fails_safely(self, states, connections, oauth_client) -> None:
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        outcome = _service(states, connections, oauth_client).handle_callback(
            state=state, code=None, error=None, now=FIXED_TIME,
        )
        assert outcome.success is False
        assert outcome.user_id == "u1"
        oauth_client.exchange_code_for_tokens.assert_not_called()


class TestProviderDenial:
    def test_provider_error_param_fails_safely_without_echoing_it(self, states, connections, oauth_client) -> None:
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        outcome = _service(states, connections, oauth_client).handle_callback(
            state=state, code=None, error="access_denied: some arbitrary provider text", now=FIXED_TIME,
        )
        assert outcome.success is False
        assert "access_denied" not in outcome.message
        assert "arbitrary provider text" not in outcome.message
        oauth_client.exchange_code_for_tokens.assert_not_called()

    def test_state_is_still_consumed_on_provider_denial(self, states, connections, oauth_client) -> None:
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        _service(states, connections, oauth_client).handle_callback(
            state=state, code=None, error="access_denied", now=FIXED_TIME,
        )
        replay = states.consume(state=state, now=FIXED_TIME)
        assert replay.matched is False


class TestExchangeFailure:
    def test_token_exchange_failure_fails_safely_and_persists_nothing(self, states, connections, oauth_client) -> None:
        oauth_client.exchange_code_for_tokens.side_effect = ChasterTokenExchangeError("HTTP 400")
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        outcome = _service(states, connections, oauth_client).handle_callback(
            state=state, code="bad-code", error=None, now=FIXED_TIME,
        )
        assert outcome.success is False
        assert connections.get("u1") is None


class TestUnknownStateAndExpiredState:
    def test_expired_state_fails_safely(self, states, connections, oauth_client) -> None:
        from chaster.repository import STATE_TTL
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        past_expiry = FIXED_TIME + STATE_TTL + timedelta(seconds=1)
        outcome = _service(states, connections, oauth_client).handle_callback(
            state=state, code="c", error=None, now=past_expiry,
        )
        assert outcome.success is False


class TestUnconfirmedIdentityResolver:
    def test_unconfirmed_resolver_always_raises(self) -> None:
        with pytest.raises(ChasterIdentityResolutionUnavailable):
            unconfirmed_identity_resolver("any-access-token")

    def test_service_with_unconfirmed_resolver_fails_safely_after_a_successful_exchange(
        self, states, connections, oauth_client,
    ) -> None:
        """Documents the current, honest limitation: a real callback,
        wired with the production resolver, fails safely at the
        identity-resolution step -- it does not crash, and it does not
        persist a connection with a fabricated identity."""
        oauth_client.exchange_code_for_tokens.return_value = ChasterTokenResponse(
            access_token="at", refresh_token="rt", expires_in=300, refresh_expires_in=1800, granted_scopes=("locks",),
        )
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        service = _service(states, connections, oauth_client, resolve_identity=unconfirmed_identity_resolver)
        outcome = service.handle_callback(state=state, code="real-code", error=None, now=FIXED_TIME)
        assert outcome.success is False
        assert connections.get("u1") is None


class TestNoDomainWrites:
    def test_a_failed_callback_never_touches_lock_state_or_task_runtime_tables(
        self, states, connections, oauth_client, core: CoreDatabase,
    ) -> None:
        oauth_client.exchange_code_for_tokens.side_effect = ChasterTokenExchangeError("boom")
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        _service(states, connections, oauth_client).handle_callback(state=state, code="c", error=None, now=FIXED_TIME)
        with core.raw_connection() as conn:
            lock_count = conn.execute("SELECT COUNT(*) FROM lock_reports").fetchone()[0]
            task_count = conn.execute("SELECT COUNT(*) FROM task_assignments").fetchone()[0]
        assert lock_count == 0
        assert task_count == 0

    def test_a_successful_callback_never_touches_lock_state_or_task_runtime_tables(
        self, states, connections, oauth_client, core: CoreDatabase,
    ) -> None:
        oauth_client.exchange_code_for_tokens.return_value = ChasterTokenResponse(
            access_token="at", refresh_token="rt", expires_in=300, refresh_expires_in=1800, granted_scopes=("locks",),
        )
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        _service(states, connections, oauth_client).handle_callback(state=state, code="c", error=None, now=FIXED_TIME)
        with core.raw_connection() as conn:
            lock_count = conn.execute("SELECT COUNT(*) FROM lock_reports").fetchone()[0]
            task_count = conn.execute("SELECT COUNT(*) FROM task_assignments").fetchone()[0]
        assert lock_count == 0
        assert task_count == 0


# A representative subset of the real, confirmed GET /auth/profile
# schema (verbatim field NAMES from a real response; VALUES here are
# synthetic test fixture data, never real captured personal data).
# Deliberately includes many fields ChasterIdentity does NOT need, to
# prove the resolver correctly ignores everything except _id/username.
REALISTIC_PROFILE_RESPONSE = {
    "_id": "real-account-id-abc123",
    "keycloakId": "kc-id-xyz",
    "username": "wearer_example",
    "email": "wearer@example.com",
    "emailVerified": True,
    "subscriptionEnd": "2027-01-01T00:00:00.000Z",
    "customSubscriptionEnd": None,
    "hasPastDueSubscription": False,
    "description": "some bio text",
    "location": "Somewhere",
    "gender": "unspecified",
    "sexualOrientation": "unspecified",
    "pronouns": "they/them",
    "birthDate": "1990-01-01",
    "role": "member",
    "avatarUrl": "https://example.com/avatar.png",
    "isPremium": False,
    "isDeveloper": False,
    "subscriptionCancelAfterEnd": False,
    "discordId": "1234567890",
    "discordUsername": "discorduser",
    "isAdmin": False,
    "isModerator": False,
    "isTeamMember": False,
    "isFindom": False,
    "settings": {"showLocksOnProfile": True, "showOnlineStatus": False},
    "metadata": {"locktober2025Points": 0},
    "country": {"countryName": "Nowhere", "countryShortCode": "XX"},
    "region": None,
    "privateMetadata": {"userSearchForm": None},
    "hasAcceptedCommunityRules": True,
    "needsDiscordMigration": False,
    "timezone": "UTC",
    "phoneNumber": "",
    "phoneNumberVerified": False,
    "isSuspended": False,
    "hasActiveModerationNotification": False,
}


class TestBuildRealIdentityResolver:
    """Evidence-backed identity resolution -- built against a real,
    complete, verbatim GET /auth/profile response (see
    docs/architecture/chaster_integration_technical_design.md).
    `_id` and `username` were directly confirmed as non-null string
    fields; only these two are ever read."""

    def test_extracts_account_id_and_username_from_a_realistic_full_profile(self) -> None:
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.return_value = REALISTIC_PROFILE_RESPONSE
        resolver = build_real_identity_resolver(mock_oauth)
        identity = resolver("real-access-token")
        assert identity == ChasterIdentity(chaster_account_id="real-account-id-abc123", chaster_username="wearer_example")

    def test_ignores_every_other_confirmed_field(self) -> None:
        """The real response has ~90 fields total -- confirms none of
        the other ~88 are read, stored, or cause any error."""
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.return_value = REALISTIC_PROFILE_RESPONSE
        resolver = build_real_identity_resolver(mock_oauth)
        identity = resolver("real-access-token")
        # ChasterIdentity itself structurally cannot hold anything
        # beyond these two fields -- this is enforced by its own
        # dataclass shape, not just by this test's assertions.
        assert set(identity.__dataclass_fields__) == {"chaster_account_id", "chaster_username"}

    def test_passes_the_access_token_through_to_fetch_raw_profile(self) -> None:
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.return_value = REALISTIC_PROFILE_RESPONSE
        resolver = build_real_identity_resolver(mock_oauth)
        resolver("real-access-token-value")
        mock_oauth.fetch_raw_profile.assert_called_once_with(access_token="real-access-token-value")

    def test_reuses_the_given_oauth_client_instance(self) -> None:
        """Confirms clean dependency injection -- the resolver is
        bound to whatever ChasterOAuthClient instance the composition
        root already constructed, not a new one."""
        mock_oauth_a = MagicMock()
        mock_oauth_a.fetch_raw_profile.return_value = {"_id": "a", "username": "a"}
        mock_oauth_b = MagicMock()
        mock_oauth_b.fetch_raw_profile.return_value = {"_id": "b", "username": "b"}
        resolver_a = build_real_identity_resolver(mock_oauth_a)
        resolver_a("token")
        mock_oauth_a.fetch_raw_profile.assert_called_once()
        mock_oauth_b.fetch_raw_profile.assert_not_called()

    def test_missing_id_field_raises(self) -> None:
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.return_value = {"username": "wearer1"}
        resolver = build_real_identity_resolver(mock_oauth)
        with pytest.raises(ChasterIdentityResolutionUnavailable):
            resolver("at")

    def test_id_wrong_type_raises(self) -> None:
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.return_value = {"_id": 12345, "username": "wearer1"}
        resolver = build_real_identity_resolver(mock_oauth)
        with pytest.raises(ChasterIdentityResolutionUnavailable):
            resolver("at")

    def test_empty_id_string_raises(self) -> None:
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.return_value = {"_id": "", "username": "wearer1"}
        resolver = build_real_identity_resolver(mock_oauth)
        with pytest.raises(ChasterIdentityResolutionUnavailable):
            resolver("at")

    def test_missing_username_resolves_with_none_not_an_error(self) -> None:
        """ChasterIdentity.chaster_username is str | None -- a
        missing username is not itself a failure."""
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.return_value = {"_id": "real-account-id"}
        resolver = build_real_identity_resolver(mock_oauth)
        identity = resolver("at")
        assert identity.chaster_account_id == "real-account-id"
        assert identity.chaster_username is None

    def test_username_wrong_type_raises(self) -> None:
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.return_value = {"_id": "real-account-id", "username": 12345}
        resolver = build_real_identity_resolver(mock_oauth)
        with pytest.raises(ChasterIdentityResolutionUnavailable):
            resolver("at")

    def test_underlying_fetch_failure_propagates_as_identity_resolution_unavailable(self) -> None:
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.side_effect = ChasterTokenExchangeError("Chaster's profile endpoint returned HTTP 401.")
        resolver = build_real_identity_resolver(mock_oauth)
        with pytest.raises(ChasterIdentityResolutionUnavailable):
            resolver("at")

    def test_error_messages_never_contain_the_access_token(self) -> None:
        mock_oauth = MagicMock()
        mock_oauth.fetch_raw_profile.side_effect = ChasterTokenExchangeError("HTTP 401.")
        resolver = build_real_identity_resolver(mock_oauth)
        try:
            resolver("UNIQUE-SECRET-ACCESS-TOKEN-VALUE")
            pytest.fail("expected ChasterIdentityResolutionUnavailable")
        except ChasterIdentityResolutionUnavailable as exc:
            assert "UNIQUE-SECRET-ACCESS-TOKEN-VALUE" not in str(exc)

    def test_full_callback_flow_succeeds_end_to_end_with_the_real_resolver(
        self, states, connections, oauth_client, core: CoreDatabase,
    ) -> None:
        """Integration: build_real_identity_resolver wired through the
        actual ChasterCallbackService, exactly as bot/discord_bot.py
        now wires it in production."""
        oauth_client.exchange_code_for_tokens.return_value = ChasterTokenResponse(
            access_token="at", refresh_token="rt", expires_in=300, refresh_expires_in=1800, granted_scopes=("locks",),
        )
        oauth_client.fetch_raw_profile = MagicMock(return_value=REALISTIC_PROFILE_RESPONSE)
        state = states.create_or_replace(user_id="u1", now=FIXED_TIME)
        service = ChasterCallbackService(
            states=states, connections=connections, oauth_client=oauth_client,
            resolve_identity=build_real_identity_resolver(oauth_client),
        )
        outcome = service.handle_callback(state=state, code="c", error=None, now=FIXED_TIME)
        assert outcome.success is True
        stored = connections.get("u1")
        assert stored is not None
        assert stored.chaster_account_id == "real-account-id-abc123"
        assert stored.chaster_username == "wearer_example"
