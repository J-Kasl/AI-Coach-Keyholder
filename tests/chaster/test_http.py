"""
tests/chaster/test_http.py

Unit tests for chaster/_http.py::send_with_ordered_headers() itself,
independent of any specific Chaster client. All HTTP traffic mocked
via requests.Session.send -- no real network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from chaster._http import send_with_ordered_headers


class TestHeaderOrdering:
    def test_authorization_comes_first_when_present(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers(
                "GET", "https://api.chaster.app/locks",
                headers={"Authorization": "Bearer tok", "User-Agent": "AI-Coach-Keyholder"},
                timeout=15.0,
            )
        header_keys = list(mock_send.call_args.args[0].headers.keys())
        assert header_keys[0] == "Authorization"

    def test_user_agent_comes_second_when_authorization_present(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers(
                "GET", "https://api.chaster.app/locks",
                headers={"Authorization": "Bearer tok", "User-Agent": "AI-Coach-Keyholder"},
                timeout=15.0,
            )
        header_keys = list(mock_send.call_args.args[0].headers.keys())
        assert header_keys[1] == "User-Agent"

    def test_user_agent_comes_first_when_authorization_absent(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers(
                "POST", "https://sso.chaster.app/token",
                data={"grant_type": "authorization_code"},
                headers={"User-Agent": "AI-Coach-Keyholder"},
                timeout=15.0,
            )
        header_keys = list(mock_send.call_args.args[0].headers.keys())
        assert header_keys[0] == "User-Agent"
        assert "Authorization" not in header_keys

    def test_order_is_independent_of_caller_supplied_dict_order(self) -> None:
        """The bug this helper fixes: requests always appends
        caller-supplied headers after its own defaults, regardless of
        dict order. This confirms the helper produces the correct
        order even when the caller writes User-Agent first."""
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers(
                "GET", "https://api.chaster.app/locks",
                headers={"User-Agent": "AI-Coach-Keyholder", "Authorization": "Bearer tok"},  # deliberately reversed
                timeout=15.0,
            )
        header_keys = list(mock_send.call_args.args[0].headers.keys())
        assert header_keys[0] == "Authorization"
        assert header_keys[1] == "User-Agent"


class TestPreservation:
    def test_remaining_requests_default_headers_are_preserved_not_dropped(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers(
                "GET", "https://api.chaster.app/locks",
                headers={"Authorization": "Bearer tok", "User-Agent": "AI-Coach-Keyholder"},
                timeout=15.0,
            )
        header_keys = set(mock_send.call_args.args[0].headers.keys())
        assert {"Accept", "Accept-Encoding", "Connection"}.issubset(header_keys)

    def test_header_values_are_preserved_exactly(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers(
                "GET", "https://api.chaster.app/locks",
                headers={"Authorization": "Bearer real-token-value", "User-Agent": "AI-Coach-Keyholder"},
                timeout=15.0,
            )
        prepared = mock_send.call_args.args[0]
        assert prepared.headers["Authorization"] == "Bearer real-token-value"
        assert prepared.headers["User-Agent"] == "AI-Coach-Keyholder"

    def test_query_params_are_preserved(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers(
                "GET", "https://api.chaster.app/locks", params={"status": "active"},
                headers={"Authorization": "Bearer tok"}, timeout=15.0,
            )
        assert mock_send.call_args.args[0].url == "https://api.chaster.app/locks?status=active"

    def test_post_body_is_preserved(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers(
                "POST", "https://sso.chaster.app/token", data={"grant_type": "authorization_code", "code": "abc123"},
                headers={"User-Agent": "AI-Coach-Keyholder"}, timeout=15.0,
            )
        body = mock_send.call_args.args[0].body
        assert "grant_type=authorization_code" in body
        assert "code=abc123" in body

    def test_content_type_for_form_body_is_preserved(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers(
                "POST", "https://sso.chaster.app/token", data={"grant_type": "authorization_code"},
                headers={"User-Agent": "AI-Coach-Keyholder"}, timeout=15.0,
            )
        assert mock_send.call_args.args[0].headers["Content-Type"] == "application/x-www-form-urlencoded"


class TestMethodAndTimeout:
    def test_get_method_is_used_for_get(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers("GET", "https://api.chaster.app/locks", headers={"Authorization": "Bearer tok"}, timeout=15.0)
        assert mock_send.call_args.args[0].method == "GET"

    def test_post_method_is_used_for_post(self) -> None:
        mock_response = MagicMock(status_code=204)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers("POST", "https://api.chaster.app/locks/lock1/emergency-unlock", headers={"Authorization": "Bearer tok"}, timeout=15.0)
        assert mock_send.call_args.args[0].method == "POST"

    def test_timeout_reaches_session_send_unchanged(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers("GET", "https://api.chaster.app/locks", headers={"Authorization": "Bearer tok"}, timeout=(5.0, 15.0))
        assert mock_send.call_args.kwargs["timeout"] == (5.0, 15.0)

    def test_works_with_no_headers_at_all(self) -> None:
        """Defensive: must not raise if headers=None (default)."""
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response) as mock_send:
            send_with_ordered_headers("GET", "https://api.chaster.app/locks", timeout=15.0)
        mock_send.assert_called_once()


class TestReturnValue:
    def test_returns_the_response_from_session_send(self) -> None:
        mock_response = MagicMock(status_code=200)
        with patch("requests.Session.send", return_value=mock_response):
            result = send_with_ordered_headers("GET", "https://api.chaster.app/locks", headers={"Authorization": "Bearer tok"}, timeout=15.0)
        assert result is mock_response

    def test_network_exception_propagates(self) -> None:
        import requests
        import pytest
        with patch("requests.Session.send", side_effect=requests.ConnectionError("boom")):
            with pytest.raises(requests.ConnectionError):
                send_with_ordered_headers("GET", "https://api.chaster.app/locks", headers={"Authorization": "Bearer tok"}, timeout=15.0)


class TestNoTlsWeakening:
    def test_source_never_disables_certificate_verification(self) -> None:
        import inspect
        source = inspect.getsource(send_with_ordered_headers)
        assert "verify=False" not in source
        assert "verify = False" not in source
        assert "CERT_NONE" not in source
