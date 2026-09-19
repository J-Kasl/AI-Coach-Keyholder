"""
tests/scripts/test_dev_chaster_schema_probe.py

Tests the pure `redact()` function and the diagnostic-fallback
`_print_diagnostic_response()` helper (added to diagnose the real
HTTP 400 responses from the first developer-token test). `main()`
itself requires interactive input and real network calls and is not
exercised here, consistent with this script's own explicit
development-only, manual-use purpose. All HTTP traffic in these
tests is mocked -- no real network calls, no real Chaster
credentials required.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "dev_chaster_schema_probe.py"
_spec = importlib.util.spec_from_file_location("dev_chaster_schema_probe", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["dev_chaster_schema_probe"] = _module
_spec.loader.exec_module(_module)
redact = _module.redact
_print_diagnostic_response = _module._print_diagnostic_response


class TestRedact:
    def test_top_level_sensitive_key_is_redacted(self) -> None:
        assert redact({"access_token": "UNIQUE-SECRET-VALUE"}) == {"access_token": "<redacted>"}

    def test_email_key_is_redacted(self) -> None:
        assert redact({"email": "someone@example.com"}) == {"email": "<redacted>"}

    def test_nested_sensitive_key_is_redacted(self) -> None:
        result = redact({"user": {"profile": {"authToken": "UNIQUE-SECRET-VALUE"}}})
        assert result == {"user": {"profile": {"authToken": "<redacted>"}}}

    def test_sensitive_key_inside_a_list_of_dicts_is_redacted(self) -> None:
        result = redact([{"clientSecret": "UNIQUE-SECRET-VALUE"}, {"id": "lock1"}])
        assert result == [{"clientSecret": "<redacted>"}, {"id": "lock1"}]

    def test_non_sensitive_keys_are_preserved_unchanged(self) -> None:
        result = redact({"_id": "lock123", "username": "wearer1", "type": "bondage", "status": "locked"})
        assert result == {"_id": "lock123", "username": "wearer1", "type": "bondage", "status": "locked"}

    def test_case_insensitive_matching(self) -> None:
        assert redact({"AccessToken": "UNIQUE-SECRET-VALUE"}) == {"AccessToken": "<redacted>"}
        assert redact({"REFRESH_TOKEN": "UNIQUE-SECRET-VALUE"}) == {"REFRESH_TOKEN": "<redacted>"}

    def test_partial_substring_match_redacts(self) -> None:
        """Confirms the filter matches ANY key containing the
        substring, not just an exact key name -- e.g. Chaster's own
        real field names are unknown in advance, so this must be
        broad rather than an exact-name allowlist."""
        assert redact({"authenticationMethod": "some-value"}) == {"authenticationMethod": "<redacted>"}

    def test_non_dict_non_list_values_pass_through_unchanged(self) -> None:
        assert redact("plain string") == "plain string"
        assert redact(42) == 42
        assert redact(None) is None
        assert redact(True) is True

    def test_deeply_nested_mixed_structure(self) -> None:
        raw = {
            "_id": "lock1",
            "type": "bondage",
            "bondageConfig": {"emergencyReleaseEnabled": True, "someSecretKey": "UNIQUE-SECRET-VALUE"},
            "history": [{"event": "locked", "authorizationCode": "UNIQUE-SECRET-VALUE"}],
        }
        result = redact(raw)
        assert result["_id"] == "lock1"
        assert result["type"] == "bondage"
        assert result["bondageConfig"]["emergencyReleaseEnabled"] is True
        assert result["bondageConfig"]["someSecretKey"] == "<redacted>"
        assert result["history"][0]["event"] == "locked"
        assert result["history"][0]["authorizationCode"] == "<redacted>"

    def test_module_is_not_imported_by_any_production_code_path(self) -> None:
        import inspect

        for module_name in ("bot.discord_bot", "application.service", "chaster.callback_service", "chaster.emergency_unlock", "chaster.emergency_unlock_server"):
            module = importlib.import_module(module_name)
            source = inspect.getsource(module)
            assert "dev_chaster_schema_probe" not in source


class TestBuildHeaders:
    """`_build_headers()` -- the pure function deciding what header
    set each diagnostic variant sends."""

    def test_default_style_sends_only_authorization_explicitly(self) -> None:
        headers = _module._build_headers("real-token", style="default")
        assert headers == {"Authorization": "Bearer real-token"}

    def test_content_type_style_adds_content_type_header(self) -> None:
        headers = _module._build_headers("at", style="content_type")
        assert headers["Content-type"] == "application/json"
        assert headers["Authorization"] == "Bearer at"

    def test_curl_equivalent_style_matches_the_known_working_curl_request_exactly(self) -> None:
        """The confirmed real curl.exe request that succeeded (HTTP
        200) sent exactly: User-Agent: curl/8.14.1, Accept: */*,
        Authorization -- nothing else. This variant must reproduce
        that exact shape, with Accept-Encoding/Connection explicitly
        removed (via requests' own None-value removal convention, not
        just left unset -- unset would still let requests' own
        defaults apply)."""
        headers = _module._build_headers("real-token", style="curl_equivalent")
        assert headers["User-Agent"] == "curl/8.14.1"
        assert headers["Accept"] == "*/*"
        assert headers["Authorization"] == "Bearer real-token"
        assert headers["Accept-Encoding"] is None
        assert headers["Connection"] is None

    def test_curl_equivalent_style_produces_the_exact_real_prepared_headers(self) -> None:
        """End-to-end proof, using requests' own real
        PreparedRequest construction (not mocked) -- confirms the
        None-value removal actually works as this diagnostic depends
        on, matching curl's own shown header set exactly (Host is
        automatic and not compared here, since requests' `Session.
        prepare_request` doesn't set it explicitly either -- both
        rely on the underlying HTTP layer to set it from the URL)."""
        import requests as real_requests
        session = real_requests.Session()
        headers = _module._build_headers("REDACTED", style="curl_equivalent")
        req = real_requests.Request("GET", "https://api.chaster.app/locks", params={"status": "active"}, headers=headers)
        prepared = session.prepare_request(req)
        assert dict(prepared.headers) == {
            "User-Agent": "curl/8.14.1",
            "Accept": "*/*",
            "Authorization": "Bearer REDACTED",
        }
        assert "Accept-Encoding" not in prepared.headers
        assert "Connection" not in prepared.headers

    def test_accept_json_style_sends_accept_application_json(self) -> None:
        headers = _module._build_headers("real-token", style="accept_json")
        assert headers["Accept"] == "application/json"

    def test_accept_json_style_isolates_exactly_one_variable_vs_curl_equivalent(self) -> None:
        """Per the Chaster developer's own suggestion -- must change
        ONLY Accept, nothing else (User-Agent, Accept-Encoding,
        Connection, Authorization mechanism all identical to
        curl_equivalent)."""
        curl_eq = _module._build_headers("real-token", style="curl_equivalent")
        accept_json = _module._build_headers("real-token", style="accept_json")
        differing_keys = [k for k in curl_eq if curl_eq[k] != accept_json.get(k)]
        assert differing_keys == ["Accept"]
        assert set(curl_eq) == set(accept_json)  # same key set, nothing added/removed

    def test_accept_json_style_produces_the_exact_real_prepared_headers(self) -> None:
        """Same end-to-end real-PreparedRequest proof as curl_equivalent,
        confirming the only actual wire difference is the Accept
        header value."""
        import requests as real_requests
        session = real_requests.Session()
        headers = _module._build_headers("REDACTED", style="accept_json")
        req = real_requests.Request("GET", "https://api.chaster.app/locks", params={"status": "active"}, headers=headers)
        prepared = session.prepare_request(req)
        assert dict(prepared.headers) == {
            "User-Agent": "curl/8.14.1",
            "Accept": "application/json",
            "Authorization": "Bearer REDACTED",
        }
        assert "Accept-Encoding" not in prepared.headers
        assert "Connection" not in prepared.headers

    def test_client_id_style_adds_x_chaster_client_id_header(self) -> None:
        """Per the Chaster developer's own confirmed working Go
        example -- `X-Chaster-Client-Id` alongside `Authorization`."""
        headers = _module._build_headers("real-token", style="client_id", client_id="my-client-id-123")
        assert headers["X-Chaster-Client-Id"] == "my-client-id-123"
        assert headers["Authorization"] == "Bearer real-token"

    def test_client_id_style_isolates_exactly_one_variable_vs_curl_equivalent(self) -> None:
        curl_eq = _module._build_headers("real-token", style="curl_equivalent")
        client_id_variant = _module._build_headers("real-token", style="client_id", client_id="my-client-id-123")
        added_keys = set(client_id_variant) - set(curl_eq)
        assert added_keys == {"X-Chaster-Client-Id"}
        for k in curl_eq:
            assert curl_eq[k] == client_id_variant[k]  # every existing header value unchanged

    def test_client_id_style_without_a_configured_client_id_raises(self) -> None:
        """A silent no-op here would be misleading -- this must fail
        loudly rather than quietly send the same request as
        curl_equivalent while claiming to test the Client ID
        hypothesis."""
        with pytest.raises(ValueError, match="requires a client_id"):
            _module._build_headers("real-token", style="client_id", client_id=None)

    def test_client_id_style_produces_the_exact_real_prepared_headers(self) -> None:
        import requests as real_requests
        session = real_requests.Session()
        headers = _module._build_headers("REDACTED", style="client_id", client_id="my-client-id-123")
        req = real_requests.Request("GET", "https://api.chaster.app/locks", params={"status": "active"}, headers=headers)
        prepared = session.prepare_request(req)
        assert dict(prepared.headers) == {
            "User-Agent": "curl/8.14.1",
            "Accept": "*/*",
            "Authorization": "Bearer REDACTED",
            "X-Chaster-Client-Id": "my-client-id-123",
        }
        assert "Accept-Encoding" not in prepared.headers
        assert "Connection" not in prepared.headers

    def test_client_id_accept_json_style_combines_both(self) -> None:
        headers = _module._build_headers("real-token", style="client_id_accept_json", client_id="my-client-id-123")
        assert headers["X-Chaster-Client-Id"] == "my-client-id-123"
        assert headers["Accept"] == "application/json"
        assert headers["Authorization"] == "Bearer real-token"

    def test_default_style_does_not_strip_requests_own_defaults(self) -> None:
        """Confirms the 'default' style deliberately reproduces
        current production behavior exactly -- requests' own
        automatic headers (User-Agent: python-requests/..., etc.)
        are left untouched, not stripped."""
        import requests as real_requests
        session = real_requests.Session()
        headers = _module._build_headers("REDACTED", style="default")
        req = real_requests.Request("GET", "https://api.chaster.app/locks", params={"status": "active"}, headers=headers)
        prepared = session.prepare_request(req)
        assert prepared.headers["User-Agent"].startswith("python-requests/")
        assert "Accept-Encoding" in prepared.headers


class TestPrintDiagnosticResponse:
    """`_print_diagnostic_response()` -- the read-only fallback that
    captures and safely prints a full response when a primary
    production-helper call already failed. Never makes a write
    request; only ever GET, confirmed by inspecting the call itself."""

    def test_only_ever_calls_requests_get_never_a_write_method(self, capsys) -> None:
        mock_response = MagicMock(status_code=200, headers={"Content-Type": "application/json"})
        mock_response.json.return_value = {"ok": True}
        with patch.object(_module.requests, "get", return_value=mock_response) as mock_get, \
             patch.object(_module.requests, "post") as mock_post:
            _print_diagnostic_response("label", "https://api.chaster.app/locks", params=None, access_token="at", header_style="default")
        mock_get.assert_called_once()
        mock_post.assert_not_called()

    def test_sends_bearer_authorization_header(self, capsys) -> None:
        mock_response = MagicMock(status_code=200, headers={"Content-Type": "application/json"})
        mock_response.json.return_value = {}
        with patch.object(_module.requests, "get", return_value=mock_response) as mock_get:
            _print_diagnostic_response("label", "https://api.chaster.app/locks", params=None, access_token="real-token-value", header_style="default")
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer real-token-value"

    def test_curl_equivalent_header_style_is_actually_used(self, capsys) -> None:
        mock_response = MagicMock(status_code=200, headers={"Content-Type": "application/json"})
        mock_response.json.return_value = []
        with patch.object(_module.requests, "get", return_value=mock_response) as mock_get:
            _print_diagnostic_response("label", "https://api.chaster.app/locks", params={"status": "active"}, access_token="at", header_style="curl_equivalent")
        assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "curl/8.14.1"

    def test_json_body_is_redacted_before_printing(self, capsys) -> None:
        mock_response = MagicMock(status_code=400, headers={"Content-Type": "application/json"})
        mock_response.json.return_value = {"message": "Bad Request", "someAuthField": "UNIQUE-SECRET-VALUE"}
        with patch.object(_module.requests, "get", return_value=mock_response):
            _print_diagnostic_response("label", "https://api.chaster.app/locks", params=None, access_token="at", header_style="default")
        output = capsys.readouterr().out
        assert "Bad Request" in output
        assert "UNIQUE-SECRET-VALUE" not in output
        assert "<redacted>" in output

    def test_response_headers_are_redacted_before_printing(self, capsys) -> None:
        mock_response = MagicMock(status_code=200, headers={"Content-Type": "application/json", "X-Auth-Debug": "UNIQUE-SECRET-HEADER-VALUE"})
        mock_response.json.return_value = {}
        with patch.object(_module.requests, "get", return_value=mock_response):
            _print_diagnostic_response("label", "https://api.chaster.app/locks", params=None, access_token="at", header_style="default")
        output = capsys.readouterr().out
        assert "UNIQUE-SECRET-HEADER-VALUE" not in output

    def test_non_json_body_is_never_printed_raw(self, capsys) -> None:
        mock_response = MagicMock(status_code=400, headers={"Content-Type": "text/plain"}, content=b"some raw text body")
        mock_response.json.side_effect = ValueError("not json")
        with patch.object(_module.requests, "get", return_value=mock_response):
            _print_diagnostic_response("label", "https://api.chaster.app/locks", params=None, access_token="at", header_style="default")
        output = capsys.readouterr().out
        assert "some raw text body" not in output
        assert "not valid JSON" in output

    def test_access_token_never_appears_in_printed_output(self, capsys) -> None:
        mock_response = MagicMock(status_code=400, headers={"Content-Type": "application/json"})
        mock_response.json.return_value = {"message": "Bad Request"}
        with patch.object(_module.requests, "get", return_value=mock_response):
            _print_diagnostic_response("label", "https://api.chaster.app/locks", params=None, access_token="UNIQUE-REAL-ACCESS-TOKEN", header_style="default")
        output = capsys.readouterr().out
        assert "UNIQUE-REAL-ACCESS-TOKEN" not in output

    def test_access_token_never_appears_in_printed_output_curl_equivalent_style(self, capsys) -> None:
        mock_response = MagicMock(status_code=200, headers={"Content-Type": "application/json"})
        mock_response.json.return_value = []
        with patch.object(_module.requests, "get", return_value=mock_response):
            _print_diagnostic_response("label", "https://api.chaster.app/locks", params=None, access_token="UNIQUE-REAL-ACCESS-TOKEN", header_style="curl_equivalent")
        output = capsys.readouterr().out
        assert "UNIQUE-REAL-ACCESS-TOKEN" not in output

    def test_network_error_is_handled_safely_without_a_traceback(self, capsys) -> None:
        import requests
        with patch.object(_module.requests, "get", side_effect=requests.ConnectionError("boom")):
            _print_diagnostic_response("label", "https://api.chaster.app/locks", params=None, access_token="at", header_style="default")
        output = capsys.readouterr().out
        assert "network error" in output


class TestBareHttpClientTransport:
    """`_test_bare_httpclient_transport()` -- the isolation experiment
    using stdlib `http.client` directly, bypassing `requests`/
    `urllib3` entirely. Confirms it is genuinely GET-only, never
    leaks the token, and correctly redacts headers/body."""

    def test_uses_http_client_httpsconnection_directly(self, capsys) -> None:
        mock_conn = MagicMock()
        mock_response = MagicMock(status=200)
        mock_response.read.return_value = b"[]"
        mock_response.getheaders.return_value = [("Content-Type", "application/json")]
        mock_conn.getresponse.return_value = mock_response
        with patch.object(_module.http.client, "HTTPSConnection", return_value=mock_conn) as mock_https:
            _module._test_bare_httpclient_transport("label", host="api.chaster.app", path="/locks?status=active", access_token="at")
        mock_https.assert_called_once_with("api.chaster.app", timeout=15.0)

    def test_sends_only_a_get_request_never_a_write_method(self) -> None:
        mock_conn = MagicMock()
        mock_response = MagicMock(status=200)
        mock_response.read.return_value = b"[]"
        mock_response.getheaders.return_value = []
        mock_conn.getresponse.return_value = mock_response
        with patch.object(_module.http.client, "HTTPSConnection", return_value=mock_conn):
            _module._test_bare_httpclient_transport("label", host="api.chaster.app", path="/locks?status=active", access_token="at")
        mock_conn.request.assert_called_once_with("GET", "/locks?status=active", headers={"Authorization": "Bearer at"})

    def test_connection_is_always_closed(self) -> None:
        mock_conn = MagicMock()
        mock_response = MagicMock(status=200)
        mock_response.read.return_value = b"[]"
        mock_response.getheaders.return_value = []
        mock_conn.getresponse.return_value = mock_response
        with patch.object(_module.http.client, "HTTPSConnection", return_value=mock_conn):
            _module._test_bare_httpclient_transport("label", host="api.chaster.app", path="/locks?status=active", access_token="at")
        mock_conn.close.assert_called_once()

    def test_connection_is_closed_even_on_network_error(self) -> None:
        mock_conn = MagicMock()
        mock_conn.request.side_effect = OSError("connection refused")
        with patch.object(_module.http.client, "HTTPSConnection", return_value=mock_conn):
            _module._test_bare_httpclient_transport("label", host="api.chaster.app", path="/locks?status=active", access_token="at")
        mock_conn.close.assert_called_once()

    def test_access_token_never_appears_in_printed_output(self, capsys) -> None:
        mock_conn = MagicMock()
        mock_response = MagicMock(status=400)
        mock_response.read.return_value = b'{"message": "Bad Request"}'
        mock_response.getheaders.return_value = []
        mock_conn.getresponse.return_value = mock_response
        with patch.object(_module.http.client, "HTTPSConnection", return_value=mock_conn):
            _module._test_bare_httpclient_transport("label", host="api.chaster.app", path="/locks?status=active", access_token="UNIQUE-REAL-ACCESS-TOKEN")
        output = capsys.readouterr().out
        assert "UNIQUE-REAL-ACCESS-TOKEN" not in output

    def test_json_body_is_redacted_before_printing(self, capsys) -> None:
        mock_conn = MagicMock()
        mock_response = MagicMock(status=400)
        mock_response.read.return_value = b'{"message": "Bad Request", "someAuthField": "UNIQUE-SECRET-VALUE"}'
        mock_response.getheaders.return_value = []
        mock_conn.getresponse.return_value = mock_response
        with patch.object(_module.http.client, "HTTPSConnection", return_value=mock_conn):
            _module._test_bare_httpclient_transport("label", host="api.chaster.app", path="/locks?status=active", access_token="at")
        output = capsys.readouterr().out
        assert "Bad Request" in output
        assert "UNIQUE-SECRET-VALUE" not in output
        assert "<redacted>" in output

    def test_response_headers_are_redacted_before_printing(self, capsys) -> None:
        mock_conn = MagicMock()
        mock_response = MagicMock(status=200)
        mock_response.read.return_value = b"[]"
        mock_response.getheaders.return_value = [("X-Auth-Debug", "UNIQUE-SECRET-HEADER-VALUE")]
        mock_conn.getresponse.return_value = mock_response
        with patch.object(_module.http.client, "HTTPSConnection", return_value=mock_conn):
            _module._test_bare_httpclient_transport("label", host="api.chaster.app", path="/locks?status=active", access_token="at")
        output = capsys.readouterr().out
        assert "UNIQUE-SECRET-HEADER-VALUE" not in output

    def test_non_json_body_is_never_printed_raw(self, capsys) -> None:
        mock_conn = MagicMock()
        mock_response = MagicMock(status=400)
        mock_response.read.return_value = b"some raw text body"
        mock_response.getheaders.return_value = []
        mock_conn.getresponse.return_value = mock_response
        with patch.object(_module.http.client, "HTTPSConnection", return_value=mock_conn):
            _module._test_bare_httpclient_transport("label", host="api.chaster.app", path="/locks?status=active", access_token="at")
        output = capsys.readouterr().out
        assert "some raw text body" not in output
        assert "not valid JSON" in output

    def test_network_error_is_handled_safely_without_a_traceback(self, capsys) -> None:
        mock_conn = MagicMock()
        mock_conn.request.side_effect = OSError("connection refused")
        with patch.object(_module.http.client, "HTTPSConnection", return_value=mock_conn):
            _module._test_bare_httpclient_transport("label", host="api.chaster.app", path="/locks?status=active", access_token="at")
        output = capsys.readouterr().out
        assert "network error" in output


class TestTlsHandshakeMetadata:
    """`_print_tls_handshake_metadata()` -- TLS-handshake-only, sends
    NO HTTP request at all, needs NO token. All network calls mocked
    -- no real connection is ever made in this test suite."""

    def _mock_tls_sock(self, *, version="TLSv1.3", cipher=("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256), alpn="http/1.1", cert="_unset_"):
        mock_tls_sock = MagicMock()
        mock_tls_sock.version.return_value = version
        mock_tls_sock.cipher.return_value = cipher
        mock_tls_sock.selected_alpn_protocol.return_value = alpn
        mock_tls_sock.getpeercert.return_value = cert if cert != "_unset_" else {
            "subject": (("commonName", "example.com"),), "issuer": (("commonName", "Example CA"),), "notAfter": "Jan  1 00:00:00 2027 GMT",
        }
        mock_tls_sock.__enter__ = MagicMock(return_value=mock_tls_sock)
        mock_tls_sock.__exit__ = MagicMock(return_value=False)
        return mock_tls_sock

    def _mock_raw_sock(self):
        mock_raw_sock = MagicMock()
        mock_raw_sock.__enter__ = MagicMock(return_value=mock_raw_sock)
        mock_raw_sock.__exit__ = MagicMock(return_value=False)
        return mock_raw_sock

    def test_sends_no_http_request_only_a_tls_handshake(self, capsys) -> None:
        mock_tls_sock = self._mock_tls_sock()
        mock_context = MagicMock()
        mock_context.wrap_socket.return_value = mock_tls_sock
        with patch.object(_module.socket, "create_connection", return_value=self._mock_raw_sock()), \
             patch.object(_module.ssl, "create_default_context", return_value=mock_context):
            _module._print_tls_handshake_metadata("api.chaster.app")
        mock_tls_sock.sendall.assert_not_called()
        mock_tls_sock.send.assert_not_called()

    def test_uses_sni_matching_the_host(self) -> None:
        mock_tls_sock = self._mock_tls_sock()
        mock_context = MagicMock()
        mock_context.wrap_socket.return_value = mock_tls_sock
        with patch.object(_module.socket, "create_connection", return_value=self._mock_raw_sock()), \
             patch.object(_module.ssl, "create_default_context", return_value=mock_context):
            _module._print_tls_handshake_metadata("api.chaster.app")
        assert mock_context.wrap_socket.call_args.kwargs["server_hostname"] == "api.chaster.app"

    def test_sets_alpn_to_http_1_1_matching_http_client(self) -> None:
        mock_context = MagicMock()
        mock_context.wrap_socket.return_value = self._mock_tls_sock()
        with patch.object(_module.socket, "create_connection", return_value=self._mock_raw_sock()), \
             patch.object(_module.ssl, "create_default_context", return_value=mock_context):
            _module._print_tls_handshake_metadata("api.chaster.app")
        mock_context.set_alpn_protocols.assert_called_once_with(["http/1.1"])

    def test_prints_negotiated_version_cipher_alpn(self, capsys) -> None:
        mock_context = MagicMock()
        mock_context.wrap_socket.return_value = self._mock_tls_sock(version="TLSv1.2", alpn="http/1.1")
        with patch.object(_module.socket, "create_connection", return_value=self._mock_raw_sock()), \
             patch.object(_module.ssl, "create_default_context", return_value=mock_context):
            _module._print_tls_handshake_metadata("api.chaster.app")
        output = capsys.readouterr().out
        assert "TLSv1.2" in output
        assert "http/1.1" in output

    def test_never_prints_a_raw_certificate_blob_or_private_key(self, capsys) -> None:
        mock_context = MagicMock()
        mock_context.wrap_socket.return_value = self._mock_tls_sock()
        with patch.object(_module.socket, "create_connection", return_value=self._mock_raw_sock()), \
             patch.object(_module.ssl, "create_default_context", return_value=mock_context):
            _module._print_tls_handshake_metadata("api.chaster.app")
        output = capsys.readouterr().out
        assert "BEGIN CERTIFICATE" not in output
        assert "PRIVATE KEY" not in output

    def test_handles_missing_certificate_gracefully(self, capsys) -> None:
        mock_context = MagicMock()
        mock_context.wrap_socket.return_value = self._mock_tls_sock(cert=None)
        with patch.object(_module.socket, "create_connection", return_value=self._mock_raw_sock()), \
             patch.object(_module.ssl, "create_default_context", return_value=mock_context):
            _module._print_tls_handshake_metadata("api.chaster.app")  # must not raise
        output = capsys.readouterr().out
        assert "not available" in output

    def test_network_error_is_handled_safely_without_a_traceback(self, capsys) -> None:
        with patch.object(_module.socket, "create_connection", side_effect=OSError("connection refused")):
            _module._print_tls_handshake_metadata("api.chaster.app")
        output = capsys.readouterr().out
        assert "TLS handshake failed" in output

    def test_requires_no_token_parameter_at_all(self) -> None:
        import inspect
        sig = inspect.signature(_module._print_tls_handshake_metadata)
        assert "token" not in sig.parameters
        assert "access_token" not in sig.parameters


class TestDiagnosticIsReadOnly:
    def test_script_source_never_calls_a_write_http_method(self) -> None:
        import inspect

        source = inspect.getsource(_module)
        for forbidden in ("requests.post", "requests.put", "requests.patch", "requests.delete"):
            assert forbidden not in source

    def test_script_source_never_references_an_unlock_endpoint(self) -> None:
        import inspect

        source = inspect.getsource(_module)
        # The only occurrences must be inside the module's own
        # explanatory docstring/comments, never a callable path --
        # confirmed structurally by the absence of "requests.post"
        # above (every real HTTP call in this file is requests.get).
        assert "emergency-unlock" in source  # mentioned (in the docstring), but never called
        assert "/locks/{lockId}/unlock\"" not in source.replace("`", "")


class TestSelfIdentity:
    """`_print_self_identity()` -- exists specifically so a future
    discrepancy between a real run's output and the expected source
    can be checked directly from the printed output, without needing
    another round of artifact-level forensics."""

    def test_prints_a_valid_sha256_hex_digest(self, capsys) -> None:
        _module._print_self_identity()
        output = capsys.readouterr().out
        assert "sha256=" in output
        digest = output.split("sha256=")[1].split()[0]
        assert len(digest) == 64
        int(digest, 16)  # raises ValueError if not valid hex

    def test_reported_hash_matches_a_fresh_hash_of_the_actual_file_on_disk(self, capsys) -> None:
        import hashlib as _hashlib
        _module._print_self_identity()
        output = capsys.readouterr().out
        digest = output.split("sha256=")[1].split()[0]
        expected = _hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest()
        assert digest == expected

    def test_main_prints_self_identity_before_prompting_for_a_token(self, capsys) -> None:
        call_order = []
        with patch.object(_module, "_print_self_identity", side_effect=lambda: call_order.append("identity")) as mock_identity, \
             patch.object(_module, "getpass") as mock_getpass_module, \
             patch.object(_module, "_print_tls_handshake_metadata"):
            mock_getpass_module.getpass.side_effect = lambda *a, **k: call_order.append("getpass") or ""
            _module.main()
        mock_identity.assert_called_once()
        assert call_order == ["identity", "getpass"]


class TestMainControlFlow:
    """Exercises `main()` itself end-to-end (getpass and the two
    production helpers mocked, `_print_diagnostic_response` spied on)
    -- pins down the EXACT observable sequence a real run produces,
    rather than only testing the individual building blocks in
    isolation. This is the specific gap a real Windows run surfaced:
    the building-block tests alone could not have caught a stale
    local copy of this file running old code, but a control-flow test
    like this one is still valuable to pin the current, correct
    behavior down explicitly and catch any future regression in how
    `main()` itself wires things together."""

    def _run_main_with(self, *, token: str, profile_raises, locks_raises, client_id: str | None = "test-client-id"):
        with patch.object(_module, "getpass") as mock_getpass_module, \
             patch.object(_module, "ChasterOAuthClient") as mock_oauth_cls, \
             patch.object(_module, "ChasterLockClient") as mock_lock_cls, \
             patch.object(_module, "_print_diagnostic_response") as mock_diag, \
             patch.object(_module, "_test_bare_httpclient_transport") as mock_httpclient_diag, \
             patch.object(_module, "_print_tls_handshake_metadata"), \
             patch.object(_module, "Config") as mock_config_cls:
            mock_getpass_module.getpass.return_value = token
            mock_config_cls.load.return_value.chaster_client_id = client_id
            mock_oauth_instance = mock_oauth_cls.return_value
            mock_lock_instance = mock_lock_cls.return_value
            if profile_raises is not None:
                mock_oauth_instance.fetch_raw_profile.side_effect = profile_raises
            else:
                mock_oauth_instance.fetch_raw_profile.return_value = {"_id": "u1"}
            if locks_raises is not None:
                mock_lock_instance.list_active_locks.side_effect = locks_raises
            else:
                mock_lock_instance.list_active_locks.return_value = []
            _module.main()
        return mock_diag, mock_httpclient_diag

    def test_locks_failure_calls_print_diagnostic_response_in_the_expected_order_with_client_id_configured(self) -> None:
        mock_diag, _ = self._run_main_with(
            token="real-token",
            profile_raises=None,
            locks_raises=_module.ChasterLockApiError("HTTP 400"),
            client_id="test-client-id",
        )
        calls = [c for c in mock_diag.call_args_list if c.args and "locks" in c.args[1]]
        assert len(calls) == 5
        assert calls[0].kwargs["header_style"] == "default"
        assert calls[1].kwargs["header_style"] == "curl_equivalent"
        assert calls[2].kwargs["header_style"] == "client_id"
        assert calls[2].kwargs["client_id"] == "test-client-id"
        assert calls[3].kwargs["header_style"] == "accept_json"
        assert calls[4].kwargs["header_style"] == "client_id_accept_json"
        assert calls[4].kwargs["client_id"] == "test-client-id"

    def test_locks_failure_skips_client_id_variants_when_not_configured(self, capsys) -> None:
        mock_diag, _ = self._run_main_with(
            token="real-token",
            profile_raises=None,
            locks_raises=_module.ChasterLockApiError("HTTP 400"),
            client_id=None,
        )
        calls = [c for c in mock_diag.call_args_list if c.args and "locks" in c.args[1]]
        styles = [c.kwargs["header_style"] for c in calls]
        assert "client_id" not in styles
        assert "client_id_accept_json" not in styles
        assert styles == ["default", "curl_equivalent", "accept_json"]
        output = capsys.readouterr().out
        assert "SKIPPED -- CHASTER_CLIENT_ID is not configured" in output

    def test_locks_failure_also_runs_the_bare_httpclient_isolation_test(self) -> None:
        _, mock_httpclient_diag = self._run_main_with(
            token="real-token",
            profile_raises=None,
            locks_raises=_module.ChasterLockApiError("HTTP 400"),
        )
        mock_httpclient_diag.assert_called_once_with(
            "bare stdlib http.client (bypasses requests/urllib3 entirely)",
            host="api.chaster.app", path="/locks?status=active", access_token="real-token",
        )

    def test_locks_failure_diagnostic_calls_use_status_active_param(self) -> None:
        mock_diag, _ = self._run_main_with(
            token="real-token",
            profile_raises=None,
            locks_raises=_module.ChasterLockApiError("HTTP 400"),
        )
        for call in mock_diag.call_args_list:
            assert call.kwargs["params"] == {"status": "active"}

    def test_profile_failure_does_not_call_print_diagnostic_response_at_all(self) -> None:
        """Confirms the current, deliberate scoping: /auth/profile
        diagnostics were intentionally left unwired this pass --
        /locks is the current priority per instruction."""
        mock_diag, mock_httpclient_diag = self._run_main_with(
            token="real-token",
            profile_raises=_module.ChasterTokenExchangeError("HTTP 400"),
            locks_raises=None,
        )
        mock_diag.assert_not_called()
        mock_httpclient_diag.assert_not_called()

    def test_no_failures_never_calls_print_diagnostic_response(self) -> None:
        mock_diag, mock_httpclient_diag = self._run_main_with(token="real-token", profile_raises=None, locks_raises=None)
        mock_diag.assert_not_called()
        mock_httpclient_diag.assert_not_called()

    def test_empty_token_aborts_before_constructing_any_client(self) -> None:
        with patch.object(_module, "getpass") as mock_getpass_module, \
             patch.object(_module, "ChasterOAuthClient") as mock_oauth_cls, \
             patch.object(_module, "ChasterLockClient") as mock_lock_cls, \
             patch.object(_module, "_print_tls_handshake_metadata"):
            mock_getpass_module.getpass.return_value = ""
            _module.main()
        mock_oauth_cls.assert_not_called()
        mock_lock_cls.assert_not_called()
