"""
tests/scripts/test_dev_http_framing_capture.py

Tests for scripts/dev_http_framing_capture.py -- all against real,
local, loopback-only sockets (never the real network), using fake
placeholder credentials only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "dev_http_framing_capture.py"
_spec = importlib.util.spec_from_file_location("dev_http_framing_capture", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["dev_http_framing_capture"] = _module
_spec.loader.exec_module(_module)


class TestCaptureRequestsDefault:
    def test_captures_the_actual_wire_bytes(self) -> None:
        captured = _module._capture_one_request(_module._capture_requests_default, port=28443)
        assert b"GET /locks?status=active HTTP/1.1" in captured
        assert b"Authorization: Bearer FAKE-TOKEN-FOR-LOCAL-CAPTURE-ONLY" in captured

    def test_default_requests_connection_header_is_keep_alive(self) -> None:
        """Documents the actual, real default -- never assumed."""
        captured = _module._capture_one_request(_module._capture_requests_default, port=28444)
        assert b"Connection: keep-alive" in captured

    def test_default_requests_includes_user_agent_and_accept_encoding(self) -> None:
        captured = _module._capture_one_request(_module._capture_requests_default, port=28445)
        assert b"User-Agent: python-requests" in captured
        assert b"Accept-Encoding: gzip, deflate" in captured

    def test_no_placeholder_looks_like_a_real_secret(self) -> None:
        captured = _module._capture_one_request(_module._capture_requests_default, port=28446)
        assert b"FAKE-TOKEN-FOR-LOCAL-CAPTURE-ONLY" in captured  # the fake value IS expected to appear -- that's the point
        assert _module.FAKE_TOKEN.startswith("FAKE-")


class TestCaptureRequestsConnectionClose:
    def test_explicit_connection_close_actually_changes_the_wire_bytes(self) -> None:
        captured = _module._capture_one_request(_module._capture_requests_connection_close, port=28447)
        assert b"Connection: close" in captured
        assert b"Connection: keep-alive" not in captured


class TestCaptureBareHttpClient:
    def test_captures_minimal_header_set(self) -> None:
        captured = _module._capture_one_request(_module._capture_bare_httpclient, port=28448)
        assert b"Authorization: Bearer FAKE-TOKEN-FOR-LOCAL-CAPTURE-ONLY" in captured
        assert b"User-Agent" not in captured  # http.client sends no automatic User-Agent

    def test_http_client_default_accept_encoding_differs_from_requests(self) -> None:
        captured = _module._capture_one_request(_module._capture_bare_httpclient, port=28449)
        assert b"Accept-Encoding: identity" in captured


class TestNoRealNetworkOrCredentials:
    def test_listener_is_bound_to_loopback_only(self) -> None:
        import inspect
        source = inspect.getsource(_module._capture_one_request)
        assert '"127.0.0.1"' in source
        assert "0.0.0.0" not in source

    def test_known_good_request_uses_a_placeholder_token_not_a_real_one(self) -> None:
        assert b"<token>" in _module.KNOWN_GOOD_MANUAL_REQUEST

    def test_script_never_references_a_real_chaster_hostname_in_a_network_call(self) -> None:
        """The literal string api.chaster.app IS expected once, inside
        the known-good comparison constant (documentation only, never
        connected to) -- confirms it never appears as an actual
        connection target."""
        import inspect
        source = inspect.getsource(_module)
        for fn_name in ("_capture_requests_default", "_capture_requests_connection_close", "_capture_bare_httpclient"):
            fn_source = inspect.getsource(getattr(_module, fn_name))
            assert "api.chaster.app" not in fn_source
            assert "127.0.0.1" in fn_source

    def test_script_never_calls_a_write_http_method(self) -> None:
        import inspect
        source = inspect.getsource(_module)
        for forbidden in (".post(", ".put(", ".patch(", ".delete("):
            assert forbidden not in source


class TestDevOnlyIsolation:
    def test_module_is_not_imported_by_any_production_code_path(self) -> None:
        import importlib
        import inspect

        for module_name in (
            "bot.discord_bot", "application.service", "chaster.callback_service",
            "chaster.emergency_unlock", "chaster.emergency_unlock_server",
            "chaster.oauth_client", "chaster.lock_client",
        ):
            module = importlib.import_module(module_name)
            source = inspect.getsource(module)
            assert "dev_http_framing_capture" not in source
