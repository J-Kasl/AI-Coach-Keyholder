"""
tests/scripts/test_dev_chaster_production_client_smoke_test.py

Tests for scripts/dev_chaster_production_client_smoke_test.py. All
HTTP traffic mocked -- no real network calls, no real Chaster
credentials required. Since `main()` now makes TWO real calls
(list_active_locks() and fetch_raw_profile()), every test gets a
safe, default-mocked fetch_raw_profile() via the autouse fixture
below, so no test can accidentally make a real network call to
Chaster's `/auth/profile` just because it only meant to test the
locks-related behavior. Tests that specifically exercise profile
behavior override this with their own nested patch.object(...).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "dev_chaster_production_client_smoke_test.py"
_spec = importlib.util.spec_from_file_location("dev_chaster_production_client_smoke_test", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["dev_chaster_production_client_smoke_test"] = _module
_spec.loader.exec_module(_module)


@pytest.fixture(autouse=True)
def _mock_fetch_raw_profile_by_default():
    """Prevents any test in this file from making an accidental real
    network call via fetch_raw_profile() -- every test gets a safe
    default mock unless it explicitly overrides this itself with its
    own nested patch.object(...), which takes precedence for its
    duration."""
    with patch.object(_module.ChasterOAuthClient, "fetch_raw_profile", return_value={}):
        yield


class TestMissingToken:
    def test_missing_env_var_aborts_without_a_request(self, capsys, monkeypatch) -> None:
        monkeypatch.delenv("CHASTER_DEV_TOKEN", raising=False)
        with patch.object(_module.ChasterLockClient, "list_active_locks") as mock_call:
            _module.main()
        mock_call.assert_not_called()
        output = capsys.readouterr().out
        assert "not set" in output

    def test_empty_env_var_aborts_without_a_request(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "")
        with patch.object(_module.ChasterLockClient, "list_active_locks") as mock_call:
            _module.main()
        mock_call.assert_not_called()


class TestPrintSchema:
    """`print_schema()` -- the recursive, printing function that
    reveals key names and Python TYPES only, at every nesting depth.
    Added specifically to safely reveal the shape of nested fields
    (in particular `extensions`) without ever exposing a real value."""

    def test_dict_prints_each_key_and_the_type_of_its_value(self, capsys) -> None:
        _module.print_schema({"_id": "lock123", "status": "locked"})
        output = capsys.readouterr().out
        assert "_id: <class 'str'>" in output
        assert "status: <class 'str'>" in output

    def test_nested_dict_is_recursed_into_with_increasing_indent(self, capsys) -> None:
        _module.print_schema({"user": {"username": "wearer1"}})
        output = capsys.readouterr().out
        assert "user: <class 'dict'>" in output
        assert "  username: <class 'str'>" in output

    def test_list_prints_its_length(self, capsys) -> None:
        _module.print_schema({"extensions": [{"id": "a"}, {"id": "b"}, {"id": "c"}]})
        output = capsys.readouterr().out
        assert "(list, length=3)" in output

    def test_list_recurses_into_first_element_only(self, capsys) -> None:
        _module.print_schema({"extensions": [{"id": "a", "enabled": True}, {"id": "b", "enabled": False}]})
        output = capsys.readouterr().out
        assert "id: <class 'str'>" in output
        assert "enabled: <class 'bool'>" in output
        # only ONE occurrence of each -- the second element's shape is never printed
        assert output.count("id: <class 'str'>") == 1
        assert output.count("enabled: <class 'bool'>") == 1

    def test_empty_list_reported_without_recursing(self, capsys) -> None:
        _module.print_schema({"emptyList": []})
        output = capsys.readouterr().out
        assert "(list, length=0)" in output

    def test_list_of_primitives_shows_element_type(self, capsys) -> None:
        _module.print_schema({"reasonsPreventingUnlocking": ["not-time-yet"]})
        output = capsys.readouterr().out
        assert "element type: <class 'str'>" in output

    def test_deeply_nested_structure_fully_recursed(self, capsys) -> None:
        _module.print_schema({"extensions": [{"config": {"nested": {"deep": "value"}}}]})
        output = capsys.readouterr().out
        assert "deep: <class 'str'>" in output

    def test_no_actual_primitive_value_ever_printed(self, capsys) -> None:
        """The core safety requirement: only types, never values --
        checked against a realistic, complex mock extensions object
        with nested dicts/lists, simulating real Chaster extensions."""
        raw = {
            "_id": "UNIQUE-REAL-LOCK-ID-VALUE",
            "status": "UNIQUE-REAL-STATUS-VALUE",
            "user": {"username": "UNIQUE-REAL-USERNAME", "_id": "UNIQUE-REAL-USER-ID"},
            "extensions": [
                {
                    "id": "UNIQUE-REAL-EXT-ID",
                    "name": "UNIQUE-REAL-EXT-NAME",
                    "config": {"emergencyReleaseEnabled": True, "countdownSeconds": 3600, "note": "UNIQUE-REAL-NOTE-VALUE"},
                    "tags": ["UNIQUE-REAL-TAG-ONE", "UNIQUE-REAL-TAG-TWO"],
                },
            ],
        }
        _module.print_schema(raw)
        output = capsys.readouterr().out
        for secret in (
            "UNIQUE-REAL-LOCK-ID-VALUE", "UNIQUE-REAL-STATUS-VALUE", "UNIQUE-REAL-USERNAME",
            "UNIQUE-REAL-USER-ID", "UNIQUE-REAL-EXT-ID", "UNIQUE-REAL-EXT-NAME",
            "UNIQUE-REAL-NOTE-VALUE", "UNIQUE-REAL-TAG-ONE", "UNIQUE-REAL-TAG-TWO",
            "3600", "True",
        ):
            assert secret not in output
        # but the STRUCTURE (key names and types) must still be fully visible
        assert "emergencyReleaseEnabled: <class 'bool'>" in output
        assert "countdownSeconds: <class 'int'>" in output


class TestPrintExtensionConfigKeys:
    """`print_extension_config_keys()` -- iterates EVERY extension
    entry (not just the first), since each active extension type has
    a genuinely distinct `config` shape and an emergency-release/
    safety flag could live inside any one of them. Mock data below
    uses the real, confirmed active extension slugs and the real,
    confirmed `lockType` value ('chastity', not the OpenAPI-derived
    'bondage' assumption) -- but never prints their actual values."""

    def test_iterates_every_extension_not_just_the_first(self, capsys) -> None:
        extensions = [{"slug": "pillory", "config": {"enabled": True}}, {"slug": "penalty", "config": {"amount": 5}}]
        _module.print_extension_config_keys(extensions)
        output = capsys.readouterr().out
        assert "[0]" in output
        assert "[1]" in output
        assert "enabled" in output
        assert "amount" in output

    def test_finds_a_config_key_on_a_later_extension_not_the_first(self, capsys) -> None:
        """Directly validates why this function exists: a
        general first-element-only list printer would have missed
        this entirely."""
        extensions = [
            {"slug": "pillory", "config": {"enabled": True}},
            {"slug": "penalty", "config": {"penaltyType": "time"}},
            {"slug": "temporary-opening", "config": {"emergencyReleaseEnabled": True, "maxOpenings": 3}},
        ]
        _module.print_extension_config_keys(extensions)
        output = capsys.readouterr().out
        assert "emergencyReleaseEnabled" in output
        assert "maxOpenings" in output

    def test_extension_with_no_config_key_reported_explicitly(self, capsys) -> None:
        _module.print_extension_config_keys([{"slug": "link"}])
        output = capsys.readouterr().out
        assert "no 'config' key present" in output

    def test_extension_with_none_config_reported_explicitly(self, capsys) -> None:
        _module.print_extension_config_keys([{"slug": "link", "config": None}])
        output = capsys.readouterr().out
        assert "no 'config' key present" in output

    def test_non_list_extensions_reported_without_crashing(self, capsys) -> None:
        _module.print_extension_config_keys({"unexpected": "shape"})  # must not raise
        output = capsys.readouterr().out
        assert "not a list" in output

    def test_empty_extensions_list_reported_without_crashing(self, capsys) -> None:
        _module.print_extension_config_keys([])  # must not raise
        output = capsys.readouterr().out
        assert "empty" in output

    def test_non_dict_extension_entry_skipped_without_crashing(self, capsys) -> None:
        _module.print_extension_config_keys(["unexpected-string-entry"])  # must not raise
        output = capsys.readouterr().out
        assert "not a dict" in output

    def test_never_prints_an_extension_slug_value_even_though_it_would_seem_harmless(self, capsys) -> None:
        """Stricter than print_schema() on purpose -- withholds even
        an identifier-looking value, since this function must stay
        safe for any account's real, live data."""
        extensions = [{"slug": "wheel-of-fortune", "config": {"segments": 8}}]
        _module.print_extension_config_keys(extensions)
        output = capsys.readouterr().out
        assert "wheel-of-fortune" not in output

    def test_never_prints_a_config_value(self, capsys) -> None:
        extensions = [{"slug": "penalty", "config": {"amount": 42, "note": "UNIQUE-REAL-NOTE-VALUE"}}]
        _module.print_extension_config_keys(extensions)
        output = capsys.readouterr().out
        assert "42" not in output
        assert "UNIQUE-REAL-NOTE-VALUE" not in output
        assert "amount" in output
        assert "note" in output


class TestRealConfirmedLockTypeAndExtensions:
    """Regression coverage using the REAL, confirmed values from a
    live account -- lockType == 'chastity' (not the OpenAPI-derived
    'bondage' assumption used in earlier mock data throughout this
    project), and the real, confirmed set of active extension slugs.
    This does not implement or assume any eligibility logic -- it
    only confirms the diagnostic script handles this real shape
    correctly and safely."""

    def test_main_handles_a_real_shaped_chastity_lock_with_confirmed_extension_slugs(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        fake_lock = {
            "_id": "UNIQUE-REAL-LOCK-ID", "status": "locked", "lockType": "chastity",
            "extensions": [
                {"slug": "pillory", "config": {"enabled": True}},
                {"slug": "penalty", "config": {"penaltyType": "time"}},
                {"slug": "verification-picture", "config": {"intervalSeconds": 3600}},
                {"slug": "temporary-opening", "config": {"maxOpenings": 2}},
                {"slug": "unlock-condition", "config": {"conditionType": "date"}},
                {"slug": "link", "config": None},
                {"slug": "wheel-of-fortune", "config": {"segments": 8}},
                {"slug": "programmable-lock", "config": {"scriptEnabled": True}},
            ],
        }
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[fake_lock]):
            _module.main()  # must not raise
        output = capsys.readouterr().out
        assert "Active locks returned: 1" in output
        assert "UNIQUE-REAL-LOCK-ID" not in output
        assert "chastity" not in output  # the VALUE, never printed -- only lockType: <class 'str'>
        assert "lockType: <class 'str'>" in output
        # all 8 extensions must have been iterated, not just the first
        assert "[7]" in output

    def test_lock_type_value_itself_is_never_printed_only_its_type(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[{"_id": "l1", "lockType": "chastity"}]):
            _module.main()
        output = capsys.readouterr().out
        assert "'chastity'" not in output
        assert "lockType: <class 'str'>" in output


class TestNestedStructureOutput:
    def test_success_prints_recursive_structure_of_extensions(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        fake_lock = {
            "_id": "lock1", "status": "locked", "lockType": "bondage",
            "extensions": [{"emergencyReleaseEnabled": True}],
        }
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[fake_lock]):
            _module.main()
        output = capsys.readouterr().out
        assert "emergencyReleaseEnabled: <class 'bool'>" in output
        assert "True" not in output  # the VALUE must never appear, only the type name

    def test_complex_mock_extensions_processed_without_errors_and_without_leaking_values(self, capsys, monkeypatch) -> None:
        """Exercises main() end-to-end with a complex, realistic mock
        lock object containing nested dicts/lists inside extensions,
        simulating real Chaster extensions -- confirms the script
        runs cleanly and never prints a mock secret/value."""
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        fake_lock = {
            "_id": "UNIQUE-REAL-LOCK-ID",
            "status": "locked",
            "lockType": "bondage",
            "extensions": [
                {
                    "id": "UNIQUE-REAL-EXTENSION-ID",
                    "type": "emergency-release",
                    "config": {
                        "emergencyReleaseEnabled": True,
                        "countdownSeconds": 3600,
                        "history": [{"triggeredAt": "UNIQUE-REAL-TIMESTAMP", "byUser": "UNIQUE-REAL-USER-ID"}],
                    },
                },
            ],
            "user": {"_id": "UNIQUE-REAL-USER-ID", "username": "UNIQUE-REAL-USERNAME"},
            "reasonsPreventingUnlocking": [],
        }
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[fake_lock]):
            _module.main()  # must not raise
        output = capsys.readouterr().out
        for secret in ("UNIQUE-REAL-LOCK-ID", "UNIQUE-REAL-EXTENSION-ID", "UNIQUE-REAL-TIMESTAMP", "UNIQUE-REAL-USER-ID", "UNIQUE-REAL-USERNAME"):
            assert secret not in output
        assert "Active locks returned: 1" in output

    def test_non_dict_first_element_does_not_crash(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=["unexpected-string-element"]):
            _module.main()  # must not raise
        output = capsys.readouterr().out
        assert "Active locks returned: 1" in output


class TestUsesProductionClient:
    def test_calls_the_real_production_list_active_locks(self, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[]) as mock_call:
            _module.main()
        mock_call.assert_called_once_with(access_token="fake-token-for-test")

    def test_imports_the_real_production_class_not_a_duplicate(self) -> None:
        from chaster.lock_client import ChasterLockClient as ProductionClient
        assert _module.ChasterLockClient is ProductionClient


class TestProfileDiagnostic:
    """`main()`'s second real call, GET /auth/profile via the
    dev-only ChasterOAuthClient.fetch_raw_profile() -- observation
    only, never wired into production identity resolution. Both
    calls (locks, profile) must be genuinely independent: a failure
    on one must not prevent the other from being attempted."""

    def test_calls_the_real_fetch_raw_profile(self, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[]), \
             patch.object(_module.ChasterOAuthClient, "fetch_raw_profile", return_value={"_id": "u1"}) as mock_profile:
            _module.main()
        mock_profile.assert_called_once_with(access_token="fake-token-for-test")

    def test_profile_success_prints_recursive_schema(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        fake_profile = {"_id": "u1", "username": "wearer1", "roles": ["wearer"]}
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[]), \
             patch.object(_module.ChasterOAuthClient, "fetch_raw_profile", return_value=fake_profile):
            _module.main()
        output = capsys.readouterr().out
        assert "_id: <class 'str'>" in output
        assert "username: <class 'str'>" in output
        assert "roles: <class 'list'>" in output

    def test_profile_value_never_appears_in_output(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        fake_profile = {"_id": "UNIQUE-REAL-USER-ID", "email": "UNIQUE-REAL-EMAIL@example.com"}
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[]), \
             patch.object(_module.ChasterOAuthClient, "fetch_raw_profile", return_value=fake_profile):
            _module.main()
        output = capsys.readouterr().out
        assert "UNIQUE-REAL-USER-ID" not in output
        assert "UNIQUE-REAL-EMAIL" not in output

    def test_profile_failure_reported_safely_and_does_not_raise(self, capsys, monkeypatch) -> None:
        from chaster.oauth_client import ChasterTokenExchangeError
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[]), \
             patch.object(_module.ChasterOAuthClient, "fetch_raw_profile", side_effect=ChasterTokenExchangeError("Chaster's profile endpoint returned HTTP 400.")):
            _module.main()  # must not raise
        output = capsys.readouterr().out
        assert "FAILED" in output
        assert "400" in output

    def test_locks_failure_does_not_prevent_profile_from_being_attempted(self, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", side_effect=_module.ChasterLockApiError("HTTP 400")), \
             patch.object(_module.ChasterOAuthClient, "fetch_raw_profile", return_value={"_id": "u1"}) as mock_profile:
            _module.main()
        mock_profile.assert_called_once()

    def test_profile_failure_does_not_prevent_locks_from_being_reported(self, capsys, monkeypatch) -> None:
        from chaster.oauth_client import ChasterTokenExchangeError
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[{"_id": "l1"}]), \
             patch.object(_module.ChasterOAuthClient, "fetch_raw_profile", side_effect=ChasterTokenExchangeError("HTTP 400")):
            _module.main()
        output = capsys.readouterr().out
        assert "Active locks returned: 1" in output

    def test_uses_the_real_production_fetch_raw_profile_not_a_duplicate(self) -> None:
        from chaster.oauth_client import ChasterOAuthClient as ProductionOAuthClient
        assert _module.ChasterOAuthClient is ProductionOAuthClient

    def test_fetch_raw_profile_is_the_same_method_the_real_resolver_now_uses(self) -> None:
        """This invariant intentionally changed: fetch_raw_profile()
        is no longer dev-only -- chaster/callback_service.py::build_real_identity_resolver()
        (the production identity resolver, evidence-backed by a real,
        complete GET /auth/profile response) now calls it too. This
        script's own diagnostic call and production's real call use
        the exact same, single HTTP method -- confirming there is no
        duplicated transport logic between them."""
        import inspect

        import chaster.callback_service as module
        assert "fetch_raw_profile" in inspect.getsource(module)


class TestOutput:
    def test_success_prints_lock_count(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[{"_id": "l1"}, {"_id": "l2"}]):
            _module.main()
        output = capsys.readouterr().out
        assert "Active locks returned: 2" in output

    def test_success_prints_only_key_names_never_values(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[{"_id": "UNIQUE-REAL-LOCK-ID", "status": "locked"}]):
            _module.main()
        output = capsys.readouterr().out
        assert "UNIQUE-REAL-LOCK-ID" not in output
        assert "'_id'" in output or "_id" in output

    def test_token_never_appears_in_output(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "UNIQUE-SECRET-DEV-TOKEN-VALUE")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[]):
            _module.main()
        output = capsys.readouterr().out
        assert "UNIQUE-SECRET-DEV-TOKEN-VALUE" not in output

    def test_api_failure_reported_safely(self, capsys, monkeypatch) -> None:
        from chaster.lock_client import ChasterLockApiError
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", side_effect=ChasterLockApiError("Chaster's locks endpoint returned HTTP 400.")):
            _module.main()
        output = capsys.readouterr().out
        assert "FAILED" in output
        assert "400" in output

    def test_zero_locks_does_not_crash(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("CHASTER_DEV_TOKEN", "fake-token-for-test")
        with patch.object(_module.ChasterLockClient, "list_active_locks", return_value=[]):
            _module.main()  # must not raise
        output = capsys.readouterr().out
        assert "Active locks returned: 0" in output


class TestSafety:
    def test_script_never_calls_a_write_http_method_directly(self) -> None:
        import inspect
        source = inspect.getsource(_module.main)
        for forbidden in ("requests.post", "requests.put", "requests.patch", "requests.delete"):
            assert forbidden not in source

    def test_script_never_references_an_unlock_endpoint(self) -> None:
        import inspect
        source = inspect.getsource(_module.main)
        assert "/locks/" not in source

    def test_script_does_not_import_discord_or_oauth_callback(self) -> None:
        """Checks actual import statements only -- main() legitimately
        PRINTS an explanatory note that chaster/callback_service.py's
        real production flow still uses unconfirmed_identity_resolver
        (informational context, not an actual import)."""
        import inspect
        import_lines = "\n".join(ln for ln in inspect.getsource(_module).splitlines() if ln.strip().startswith(("import ", "from ")))
        assert "discord" not in import_lines.lower()
        assert "callback" not in import_lines.lower()

    def test_no_module_level_import_of_discord_or_callback_service(self) -> None:
        for name, module in sys.modules.items():
            if name == "dev_chaster_production_client_smoke_test":
                assert not hasattr(module, "discord")
                assert not hasattr(module, "callback_service")

    def test_module_is_not_imported_by_any_production_code_path(self) -> None:
        """Checks actual import statements only -- chaster/oauth_client.py's
        own docstring now legitimately MENTIONS this script's path in
        prose (explaining fetch_raw_profile() has two callers), which
        is not an actual import."""
        import importlib
        import inspect

        for module_name in (
            "bot.discord_bot", "application.service", "chaster.callback_service",
            "chaster.emergency_unlock", "chaster.emergency_unlock_server",
            "chaster.oauth_client", "chaster.lock_client",
        ):
            module = importlib.import_module(module_name)
            import_lines = "\n".join(ln for ln in inspect.getsource(module).splitlines() if ln.strip().startswith(("import ", "from ")))
            assert "dev_chaster_production_client_smoke_test" not in import_lines
