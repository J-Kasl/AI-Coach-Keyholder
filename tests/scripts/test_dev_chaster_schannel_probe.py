"""
tests/scripts/test_dev_chaster_schannel_probe.py

Tests for scripts/dev_chaster_schannel_probe.py. Since
`requests-schannel` is a Windows-only package and cannot be installed
in this (Linux) sandbox, actual transport execution cannot be tested
here -- these tests are deliberately environment-independent: they
exercise the pure `_redact()` helper, confirm the script's graceful
behavior when `requests-schannel` is absent (the real situation in
this sandbox, and the situation on any machine before the user
installs it), and confirm the DEV-only/no-write-endpoint/no-token-
leak invariants this project requires of every diagnostic script.
Actual proof-of-concept execution against the real Chaster API must
happen on the user's own Windows machine with `requests-schannel`
installed -- this test file cannot and does not attempt to prove
that the transport itself works.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "dev_chaster_schannel_probe.py"
_spec = importlib.util.spec_from_file_location("dev_chaster_schannel_probe", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["dev_chaster_schannel_probe"] = _module
_spec.loader.exec_module(_module)


class TestRedact:
    def test_top_level_sensitive_key_is_redacted(self) -> None:
        assert _module._redact({"access_token": "UNIQUE-SECRET-VALUE"}) == {"access_token": "<redacted>"}

    def test_client_id_header_is_redacted_if_it_ever_appeared_in_a_response(self) -> None:
        """X-Chaster-Client-Id itself would not match any sensitive
        substring (it isn't a secret) -- but if Chaster ever echoed
        back something like an X-Auth-* header, it must still be
        caught."""
        assert _module._redact({"X-Auth-Debug": "UNIQUE-SECRET-VALUE"}) == {"X-Auth-Debug": "<redacted>"}

    def test_non_sensitive_keys_pass_through_unchanged(self) -> None:
        assert _module._redact({"Content-Type": "application/json", "_id": "lock1"}) == {"Content-Type": "application/json", "_id": "lock1"}

    def test_nested_structures_are_redacted_recursively(self) -> None:
        result = _module._redact({"outer": {"inner_token": "UNIQUE-SECRET-VALUE"}})
        assert result == {"outer": {"inner_token": "<redacted>"}}

    def test_lists_are_redacted_element_wise(self) -> None:
        result = _module._redact([{"secret": "UNIQUE-SECRET-VALUE"}, {"ok": "fine"}])
        assert result == [{"secret": "<redacted>"}, {"ok": "fine"}]


class TestGracefulMissingDependency:
    """`requests-schannel` cannot be installed on this sandbox
    (Windows-only) -- confirms the script handles that honestly,
    since this is also the exact situation on the user's own machine
    before they install it."""

    def test_main_reports_missing_package_without_crashing(self, capsys) -> None:
        _module.main()  # requests-schannel is genuinely not installed here
        output = capsys.readouterr().out
        assert "not installed" in output
        assert "pip install requests-schannel" in output

    def test_main_never_reaches_the_token_prompt_when_package_missing(self, capsys) -> None:
        with patch.object(_module, "getpass") as mock_getpass_module:
            _module.main()
        mock_getpass_module.getpass.assert_not_called()


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
            assert "dev_chaster_schannel_probe" not in source

    def test_requirements_txt_does_not_declare_requests_schannel(self) -> None:
        """This is a throwaway spike, not a production dependency --
        must never leak into requirements.txt."""
        requirements_path = Path(__file__).parent.parent.parent / "requirements.txt"
        content = requirements_path.read_text(encoding="utf-8")
        assert "requests-schannel" not in content
        assert "requests_schannel" not in content

    def test_script_source_never_calls_a_write_http_method(self) -> None:
        import inspect
        source = inspect.getsource(_module)
        for forbidden in (".post(", ".put(", ".patch(", ".delete("):
            assert forbidden not in source

    def test_script_source_never_references_an_unlock_endpoint_as_a_call(self) -> None:
        import inspect
        source = inspect.getsource(_module.main)  # code only, not the module's own explanatory docstring
        assert "/locks/" not in source  # no lockId-scoped path of any kind is ever built

    def test_script_source_never_uses_verify_false(self) -> None:
        import inspect
        code_lines = [ln for ln in inspect.getsource(_module.main).splitlines() if not ln.strip().startswith("#")]
        source = "\n".join(code_lines)
        assert "verify=False" not in source
        assert "verify = False" not in source

    def test_script_source_never_disables_hostname_verification(self) -> None:
        import inspect
        code_lines = [ln for ln in inspect.getsource(_module.main).splitlines() if not ln.strip().startswith("#")]
        source = "\n".join(code_lines)
        assert "check_hostname" not in source
        assert "CERT_NONE" not in source

    def test_script_source_only_targets_the_locks_endpoint(self) -> None:
        import inspect
        source = inspect.getsource(_module)
        assert "api.chaster.app/locks" in source
        assert "auth/profile" not in source


class TestTokenAndClientIdNeverPrinted:
    def test_client_id_value_never_appears_in_any_print_statement_source(self) -> None:
        """Static check: the source must never interpolate the
        client_id variable's own value into a print() call -- only
        ever its presence/absence."""
        import inspect
        source = inspect.getsource(_module.main)
        # The only print referencing client_id must be the
        # presence/absence messages, never f"{client_id}" itself.
        assert 'f"X-Chaster-Client-Id: {client_id}' not in source
        assert "print(client_id)" not in source

    def test_headers_dict_construction_uses_bearer_scheme(self) -> None:
        import inspect
        source = inspect.getsource(_module.main)
        assert '"Authorization": f"Bearer {token}"' in source
