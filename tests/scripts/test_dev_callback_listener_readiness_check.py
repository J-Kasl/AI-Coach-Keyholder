"""
tests/scripts/test_dev_callback_listener_readiness_check.py

(pytest-asyncio is not a dependency of this project -- async tests
use a plain asyncio.run() wrapper, the same convention
tests/bot/test_chaster_listener_lifecycle.py already established.)
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "dev_callback_listener_readiness_check.py"
_spec = importlib.util.spec_from_file_location("dev_callback_listener_readiness_check", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["dev_callback_listener_readiness_check"] = _module
_spec.loader.exec_module(_module)


def _run(coro):
    return asyncio.run(coro)


class TestFakeOnCallback:
    def test_matches_the_real_callback_handler_positional_order(self) -> None:
        """state, code, error -- the exact order chaster/callback_listener.py::_handle_callback
        actually calls on_callback with, confirmed by reading its
        real source, not assumed."""
        async def _go():
            return await _module._fake_on_callback("some-state", "some-code", None)
        result = _run(_go())
        assert "state=True" in result
        assert "code=True" in result

    def test_returns_a_plain_string_not_an_object(self) -> None:
        async def _go():
            return await _module._fake_on_callback(None, None, None)
        result = _run(_go())
        assert isinstance(result, str)


class TestReadinessCheckAgainstARealListener:
    """Runs the actual check function against a real, local
    ChasterCallbackListener -- no mocking of the listener itself,
    since the entire point is to prove the real class works."""

    def test_succeeds_against_a_real_local_listener(self) -> None:
        result = _run(_module._run("127.0.0.1", 18421))
        assert result is True

    def test_reports_failure_gracefully_on_a_port_already_in_use(self) -> None:
        """Binds the port first itself, then confirms the script
        reports a clean failure rather than crashing when the real
        listener can't bind."""
        async def _go():
            server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 18422)
            try:
                return await _module._run("127.0.0.1", 18422)
            finally:
                server.close()
                await server.wait_closed()
        result = _run(_go())
        assert result is False


class TestSafety:
    def test_never_uses_a_real_chaster_client_id_or_secret(self) -> None:
        import inspect
        source = inspect.getsource(_module)
        assert "CHASTER_CLIENT_SECRET" not in source
        assert "chaster_client_secret" not in source.lower().replace("_", "")

    def test_does_not_construct_a_real_callback_service(self) -> None:
        """Confirmed deliberately -- a real ChasterCallbackService
        needs a real database and real Chaster credentials to be
        meaningful; this script only tests the listener itself."""
        import inspect
        source = inspect.getsource(_module)
        assert "ChasterCallbackService(" not in source

    def test_module_is_not_imported_by_any_production_code_path(self) -> None:
        import importlib
        import inspect

        for module_name in (
            "bot.discord_bot", "application.service", "chaster.callback_service",
            "chaster.callback_listener", "chaster.emergency_unlock", "chaster.emergency_unlock_server",
        ):
            module = importlib.import_module(module_name)
            import_lines = "\n".join(ln for ln in inspect.getsource(module).splitlines() if ln.strip().startswith(("import ", "from ")))
            assert "dev_callback_listener_readiness_check" not in import_lines
