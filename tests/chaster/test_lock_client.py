"""
tests/chaster/test_lock_client.py

All Chaster HTTP traffic mocked -- no real network calls, no real
Chaster credentials required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaster.lock_client import (
    LOCKS_URL,
    ChasterLockApiError,
    ChasterLockClient,
    EmergencyUnlockHttpOutcome,
)


def _client() -> ChasterLockClient:
    return ChasterLockClient()


class TestListActiveLocks:
    def test_calls_the_confirmed_locks_endpoint_with_status_active(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = []
        with patch("chaster.lock_client.requests.get", return_value=mock_response) as mock_get:
            _client().list_active_locks(access_token="real-access-token")
        assert mock_get.call_args.args[0] == LOCKS_URL
        assert mock_get.call_args.kwargs["params"] == {"status": "active"}

    def test_sends_the_access_token_as_a_bearer_header(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = []
        with patch("chaster.lock_client.requests.get", return_value=mock_response) as mock_get:
            _client().list_active_locks(access_token="real-access-token")
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer real-access-token"

    def test_returns_the_raw_list_unparsed(self) -> None:
        raw_locks = [{"_id": "lock1"}, {"_id": "lock2"}]
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = raw_locks
        with patch("chaster.lock_client.requests.get", return_value=mock_response):
            result = _client().list_active_locks(access_token="at")
        assert result == raw_locks

    def test_empty_result_list_is_returned_as_is(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = []
        with patch("chaster.lock_client.requests.get", return_value=mock_response):
            result = _client().list_active_locks(access_token="at")
        assert result == []

    def test_non_200_response_raises(self) -> None:
        mock_response = MagicMock(status_code=401)
        with patch("chaster.lock_client.requests.get", return_value=mock_response):
            with pytest.raises(ChasterLockApiError):
                _client().list_active_locks(access_token="at")

    def test_non_list_response_body_raises(self) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"unexpected": "shape"}
        with patch("chaster.lock_client.requests.get", return_value=mock_response):
            with pytest.raises(ChasterLockApiError):
                _client().list_active_locks(access_token="at")

    def test_network_error_raises(self) -> None:
        import requests
        with patch("chaster.lock_client.requests.get", side_effect=requests.ConnectionError("boom")):
            with pytest.raises(ChasterLockApiError):
                _client().list_active_locks(access_token="at")

    def test_empty_access_token_is_rejected_before_any_network_call(self) -> None:
        with patch("chaster.lock_client.requests.get") as mock_get:
            with pytest.raises(ValueError):
                _client().list_active_locks(access_token="")
            mock_get.assert_not_called()

    def test_error_message_never_contains_the_access_token(self) -> None:
        mock_response = MagicMock(status_code=401)
        try:
            with patch("chaster.lock_client.requests.get", return_value=mock_response):
                _client().list_active_locks(access_token="UNIQUE-SECRET-ACCESS-TOKEN")
            pytest.fail("expected ChasterLockApiError")
        except ChasterLockApiError as exc:
            assert "UNIQUE-SECRET-ACCESS-TOKEN" not in str(exc)


class TestEmergencyUnlock:
    def test_calls_the_confirmed_emergency_unlock_endpoint(self) -> None:
        mock_response = MagicMock(status_code=204)
        with patch("chaster.lock_client.requests.post", return_value=mock_response) as mock_post:
            _client().emergency_unlock(access_token="at", lock_id="real-lock-id")
        assert mock_post.call_args.args[0] == f"{LOCKS_URL}/real-lock-id/emergency-unlock"

    def test_sends_the_access_token_as_a_bearer_header(self) -> None:
        mock_response = MagicMock(status_code=204)
        with patch("chaster.lock_client.requests.post", return_value=mock_response) as mock_post:
            _client().emergency_unlock(access_token="real-access-token", lock_id="lock1")
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer real-access-token"

    def test_204_maps_to_succeeded(self) -> None:
        mock_response = MagicMock(status_code=204)
        with patch("chaster.lock_client.requests.post", return_value=mock_response):
            result = _client().emergency_unlock(access_token="at", lock_id="lock1")
        assert result.outcome is EmergencyUnlockHttpOutcome.SUCCEEDED

    def test_400_maps_to_not_eligible(self) -> None:
        mock_response = MagicMock(status_code=400)
        with patch("chaster.lock_client.requests.post", return_value=mock_response):
            result = _client().emergency_unlock(access_token="at", lock_id="lock1")
        assert result.outcome is EmergencyUnlockHttpOutcome.NOT_ELIGIBLE

    def test_401_maps_to_unauthorized(self) -> None:
        mock_response = MagicMock(status_code=401)
        with patch("chaster.lock_client.requests.post", return_value=mock_response):
            result = _client().emergency_unlock(access_token="at", lock_id="lock1")
        assert result.outcome is EmergencyUnlockHttpOutcome.UNAUTHORIZED

    def test_403_maps_to_forbidden(self) -> None:
        mock_response = MagicMock(status_code=403)
        with patch("chaster.lock_client.requests.post", return_value=mock_response):
            result = _client().emergency_unlock(access_token="at", lock_id="lock1")
        assert result.outcome is EmergencyUnlockHttpOutcome.FORBIDDEN

    def test_404_maps_to_not_found(self) -> None:
        mock_response = MagicMock(status_code=404)
        with patch("chaster.lock_client.requests.post", return_value=mock_response):
            result = _client().emergency_unlock(access_token="at", lock_id="lock1")
        assert result.outcome is EmergencyUnlockHttpOutcome.NOT_FOUND

    def test_unexpected_status_maps_to_unexpected_never_succeeded(self) -> None:
        mock_response = MagicMock(status_code=500)
        with patch("chaster.lock_client.requests.post", return_value=mock_response):
            result = _client().emergency_unlock(access_token="at", lock_id="lock1")
        assert result.outcome is EmergencyUnlockHttpOutcome.UNEXPECTED
        assert result.outcome is not EmergencyUnlockHttpOutcome.SUCCEEDED

    def test_network_error_raises_never_silently_reports_success(self) -> None:
        import requests
        with patch("chaster.lock_client.requests.post", side_effect=requests.Timeout("timed out")):
            with pytest.raises(ChasterLockApiError):
                _client().emergency_unlock(access_token="at", lock_id="lock1")

    def test_empty_lock_id_is_rejected_before_any_network_call(self) -> None:
        with patch("chaster.lock_client.requests.post") as mock_post:
            with pytest.raises(ValueError):
                _client().emergency_unlock(access_token="at", lock_id="")
            mock_post.assert_not_called()

    def test_error_message_never_contains_the_access_token(self) -> None:
        import requests
        try:
            with patch("chaster.lock_client.requests.post", side_effect=requests.ConnectionError("boom")):
                _client().emergency_unlock(access_token="UNIQUE-SECRET-ACCESS-TOKEN", lock_id="lock1")
            pytest.fail("expected ChasterLockApiError")
        except ChasterLockApiError as exc:
            assert "UNIQUE-SECRET-ACCESS-TOKEN" not in str(exc)
