"""
tests/chaster/test_oauth_client.py

All Chaster HTTP traffic is mocked -- no real network calls, no real
Chaster credentials required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaster.oauth_client import AUTHORIZATION_URL, TOKEN_URL, ChasterOAuthClient, ChasterTokenExchangeError


def _client() -> ChasterOAuthClient:
    return ChasterOAuthClient(client_id="test-client-id", client_secret="test-client-secret", redirect_uri="https://example.com/oauth/chaster/callback")


class TestConstruction:
    def test_empty_client_id_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ChasterOAuthClient(client_id="", client_secret="s", redirect_uri="https://example.com/cb")

    def test_empty_client_secret_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ChasterOAuthClient(client_id="c", client_secret="", redirect_uri="https://example.com/cb")

    def test_empty_redirect_uri_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ChasterOAuthClient(client_id="c", client_secret="s", redirect_uri="")


class TestAuthorizationUrl:
    def test_url_targets_the_confirmed_authorization_endpoint(self) -> None:
        url = _client().build_authorization_url(state="abc123")
        assert url.startswith(AUTHORIZATION_URL + "?")

    def test_url_includes_client_id_redirect_uri_and_state(self) -> None:
        url = _client().build_authorization_url(state="abc123")
        assert "client_id=test-client-id" in url
        assert "state=abc123" in url
        assert "response_type=code" in url

    def test_default_scope_is_locks_only(self) -> None:
        url = _client().build_authorization_url(state="abc123")
        assert "scope=locks" in url

    def test_empty_state_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            _client().build_authorization_url(state="")


class TestCodeExchange:
    def test_successful_exchange_returns_parsed_token_response(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "access_token": "real-access-token", "refresh_token": "real-refresh-token",
            "expires_in": 300, "refresh_expires_in": 1800, "scope": "locks",
        }
        with patch("chaster.oauth_client.requests.post", return_value=mock_response) as mock_post:
            tokens = _client().exchange_code_for_tokens(code="real-auth-code")
        assert tokens.access_token == "real-access-token"
        assert tokens.refresh_token == "real-refresh-token"
        assert tokens.expires_in == 300
        assert tokens.refresh_expires_in == 1800
        assert tokens.granted_scopes == ("locks",)
        # Confirms it actually hit the confirmed Token URL, not a guessed one.
        assert mock_post.call_args.args[0] == TOKEN_URL

    def test_exchange_sends_client_secret_and_code_in_the_request_body_not_the_url(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "access_token": "at", "refresh_token": "rt", "expires_in": 300, "refresh_expires_in": 1800, "scope": "locks",
        }
        with patch("chaster.oauth_client.requests.post", return_value=mock_response) as mock_post:
            _client().exchange_code_for_tokens(code="real-auth-code")
        sent_data = mock_post.call_args.kwargs["data"]
        assert sent_data["client_secret"] == "test-client-secret"
        assert sent_data["code"] == "real-auth-code"
        assert sent_data["grant_type"] == "authorization_code"

    def test_non_200_response_raises_token_exchange_error(self) -> None:
        mock_response = MagicMock(status_code=400)
        with patch("chaster.oauth_client.requests.post", return_value=mock_response):
            with pytest.raises(ChasterTokenExchangeError):
                _client().exchange_code_for_tokens(code="bad-code")

    def test_malformed_json_response_raises_token_exchange_error(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.side_effect = ValueError("not json")
        with patch("chaster.oauth_client.requests.post", return_value=mock_response):
            with pytest.raises(ChasterTokenExchangeError):
                _client().exchange_code_for_tokens(code="real-code")

    def test_missing_expected_field_raises_token_exchange_error(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"access_token": "at"}  # missing refresh_token etc.
        with patch("chaster.oauth_client.requests.post", return_value=mock_response):
            with pytest.raises(ChasterTokenExchangeError):
                _client().exchange_code_for_tokens(code="real-code")

    def test_network_error_raises_token_exchange_error(self) -> None:
        import requests
        with patch("chaster.oauth_client.requests.post", side_effect=requests.ConnectionError("boom")):
            with pytest.raises(ChasterTokenExchangeError):
                _client().exchange_code_for_tokens(code="real-code")

    def test_empty_code_is_rejected_before_any_network_call(self) -> None:
        with patch("chaster.oauth_client.requests.post") as mock_post:
            with pytest.raises(ValueError):
                _client().exchange_code_for_tokens(code="")
            mock_post.assert_not_called()

    def test_exchange_error_message_never_contains_the_code_or_secrets(self) -> None:
        mock_response = MagicMock(status_code=400)
        try:
            with patch("chaster.oauth_client.requests.post", return_value=mock_response):
                _client().exchange_code_for_tokens(code="UNIQUE-SECRET-CODE-VALUE")
            pytest.fail("expected ChasterTokenExchangeError")
        except ChasterTokenExchangeError as exc:
            assert "UNIQUE-SECRET-CODE-VALUE" not in str(exc)
            assert "test-client-secret" not in str(exc)


class TestRefresh:
    def test_successful_refresh_returns_new_tokens(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "access_token": "new-access", "refresh_token": "new-refresh",
            "expires_in": 300, "refresh_expires_in": 1800, "scope": "locks",
        }
        with patch("chaster.oauth_client.requests.post", return_value=mock_response):
            tokens = _client().refresh_tokens(refresh_token="old-refresh-token")
        assert tokens.access_token == "new-access"

    def test_failed_refresh_raises_token_exchange_error_without_leaking_the_refresh_token(self) -> None:
        mock_response = MagicMock(status_code=400)
        try:
            with patch("chaster.oauth_client.requests.post", return_value=mock_response):
                _client().refresh_tokens(refresh_token="UNIQUE-REFRESH-TOKEN-VALUE")
            pytest.fail("expected ChasterTokenExchangeError")
        except ChasterTokenExchangeError as exc:
            assert "UNIQUE-REFRESH-TOKEN-VALUE" not in str(exc)


class TestTokenResponseReprRedaction:
    def test_repr_never_includes_raw_token_values(self) -> None:
        from chaster.oauth_client import ChasterTokenResponse
        tokens = ChasterTokenResponse(
            access_token="UNIQUE-SECRET-ACCESS", refresh_token="UNIQUE-SECRET-REFRESH",
            expires_in=300, refresh_expires_in=1800, granted_scopes=("locks",),
        )
        text = repr(tokens)
        assert "UNIQUE-SECRET-ACCESS" not in text
        assert "UNIQUE-SECRET-REFRESH" not in text
        assert "<redacted>" in text


class TestFetchRawProfileDevelopmentDiagnostic:
    """`fetch_raw_profile` is a development/manual-test-only diagnostic
    -- never called from any production code path. See its own
    docstring."""

    def test_calls_the_confirmed_profile_endpoint(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"_id": "u1"}
        with patch("chaster.oauth_client.requests.get", return_value=mock_response) as mock_get:
            _client().fetch_raw_profile(access_token="at")
        assert mock_get.call_args.args[0] == "https://api.chaster.app/auth/profile"

    def test_sends_the_access_token_as_a_bearer_header(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"_id": "u1"}
        with patch("chaster.oauth_client.requests.get", return_value=mock_response) as mock_get:
            _client().fetch_raw_profile(access_token="real-access-token")
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer real-access-token"

    def test_returns_the_raw_dict_unparsed(self) -> None:
        raw_body = {"_id": "u1", "username": "wearer1", "email": "someone@example.com"}
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = raw_body
        with patch("chaster.oauth_client.requests.get", return_value=mock_response):
            result = _client().fetch_raw_profile(access_token="at")
        assert result == raw_body

    def test_non_200_response_raises(self) -> None:
        mock_response = MagicMock(status_code=401)
        with patch("chaster.oauth_client.requests.get", return_value=mock_response):
            with pytest.raises(ChasterTokenExchangeError):
                _client().fetch_raw_profile(access_token="at")

    def test_non_dict_response_body_raises(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = ["unexpected", "list"]
        with patch("chaster.oauth_client.requests.get", return_value=mock_response):
            with pytest.raises(ChasterTokenExchangeError):
                _client().fetch_raw_profile(access_token="at")

    def test_empty_access_token_is_rejected_before_any_network_call(self) -> None:
        with patch("chaster.oauth_client.requests.get") as mock_get:
            with pytest.raises(ValueError):
                _client().fetch_raw_profile(access_token="")
            mock_get.assert_not_called()

    def test_error_message_never_contains_the_access_token(self) -> None:
        mock_response = MagicMock(status_code=401)
        try:
            with patch("chaster.oauth_client.requests.get", return_value=mock_response):
                _client().fetch_raw_profile(access_token="UNIQUE-SECRET-ACCESS-TOKEN")
            pytest.fail("expected ChasterTokenExchangeError")
        except ChasterTokenExchangeError as exc:
            assert "UNIQUE-SECRET-ACCESS-TOKEN" not in str(exc)

    def test_never_wired_into_the_real_callback_service(self) -> None:
        """Confirms this diagnostic is never reachable from the real
        production callback flow -- chaster/callback_service.py must
        never reference fetch_raw_profile."""
        import inspect

        import chaster.callback_service as module
        assert "fetch_raw_profile" not in inspect.getsource(module)
