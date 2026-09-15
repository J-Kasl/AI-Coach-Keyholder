"""
tests/chaster/test_emergency_unlock.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from chaster.emergency_unlock import EmergencyUnlockRequestStatus, EmergencyUnlockService
from chaster.emergency_unlock_provider import (
    EmergencyUnlockProvider,
    UnwiredEmergencyUnlockProvider,
    ProviderUnlockOutcome,
    ProviderUnlockResult,
)
from chaster.repository import ChasterConnectionRepository
from chaster.token_encryptor import TokenEncryptor
from infrastructure.database import Database as CoreDatabase

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
REAL_SECRET = "the-real-emergency-secret"


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
def connections(core: CoreDatabase, tmp_path: Path) -> ChasterConnectionRepository:
    return ChasterConnectionRepository(tmp_path / "test.db", core=core, encryptor=TokenEncryptor(Fernet.generate_key()))


class _FakeProvider:
    """A fully controllable fake -- never a real network call, models
    the intended semantics honestly (an explicit outcome is always
    returned, never silently assumed)."""

    def __init__(self, outcome: ProviderUnlockOutcome, detail: str = "fake result") -> None:
        self._outcome = outcome
        self._detail = detail
        self.calls: list[str] = []

    def attempt_unlock(self, *, access_token: str) -> ProviderUnlockResult:
        self.calls.append(access_token)
        return ProviderUnlockResult(outcome=self._outcome, detail=self._detail)


def _service(core, tmp_path, connections, *, provider=None, secret=REAL_SECRET) -> EmergencyUnlockService:
    return EmergencyUnlockService(
        tmp_path / "test.db", core=core, connections=connections,
        provider=provider if provider is not None else UnwiredEmergencyUnlockProvider(),
        expected_secret=secret,
    )


def _create_connection(connections: ChasterConnectionRepository, *, user_id: str = "u1", access_token: str = "real-access-token") -> None:
    connections.create_or_replace(
        user_id=user_id, chaster_account_id="chaster-acc-1", chaster_username="wearer1",
        access_token=access_token, refresh_token="real-refresh-token",
        access_token_expires_at=FIXED_TIME + timedelta(minutes=5),
        refresh_token_expires_at=FIXED_TIME + timedelta(minutes=30),
        granted_scopes=("locks",), now=FIXED_TIME,
    )


def _audit_rows(core: CoreDatabase) -> list[tuple[str, str]]:
    with core.raw_connection() as conn:
        return [(r[0], r[1]) for r in conn.execute("SELECT event_type, payload_json FROM domain_events ORDER BY rowid").fetchall()]


class TestAuthentication:
    def test_missing_secret_configuration_rejects_every_request(self, core, tmp_path, connections) -> None:
        service = _service(core, tmp_path, connections, secret=None)
        result = service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        assert result.status is EmergencyUnlockRequestStatus.AUTH_FAILED

    def test_wrong_secret_is_rejected(self, core, tmp_path, connections) -> None:
        service = _service(core, tmp_path, connections)
        result = service.request_unlock(presented_secret="wrong-secret", user_id="u1", now=FIXED_TIME)
        assert result.status is EmergencyUnlockRequestStatus.AUTH_FAILED

    def test_empty_presented_secret_is_rejected(self, core, tmp_path, connections) -> None:
        service = _service(core, tmp_path, connections)
        result = service.request_unlock(presented_secret="", user_id="u1", now=FIXED_TIME)
        assert result.status is EmergencyUnlockRequestStatus.AUTH_FAILED

    def test_correct_secret_is_accepted(self, core, tmp_path, connections) -> None:
        _create_connection(connections)
        service = _service(core, tmp_path, connections)
        result = service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        assert result.status is not EmergencyUnlockRequestStatus.AUTH_FAILED

    def test_repeated_auth_failures_are_each_individually_audited(self, core, tmp_path, connections) -> None:
        service = _service(core, tmp_path, connections)
        service.request_unlock(presented_secret="wrong1", user_id="u1", now=FIXED_TIME)
        service.request_unlock(presented_secret="wrong2", user_id="u1", now=FIXED_TIME)
        rows = _audit_rows(core)
        auth_failures = [r for r in rows if r[0] == "chaster_emergency.auth_failed"]
        assert len(auth_failures) == 2

    def test_auth_failure_audit_never_contains_the_presented_secret(self, core, tmp_path, connections) -> None:
        service = _service(core, tmp_path, connections)
        service.request_unlock(presented_secret="UNIQUE-WRONG-SECRET-VALUE", user_id="u1", now=FIXED_TIME)
        rows = _audit_rows(core)
        for _event_type, payload_json in rows:
            assert "UNIQUE-WRONG-SECRET-VALUE" not in payload_json
            assert REAL_SECRET not in payload_json


class TestAuthorizationScope:
    def test_only_the_unlock_operation_is_exposed(self) -> None:
        """EmergencyUnlockService's own public API surface -- confirms
        no other public METHOD exists that could be misused as a
        broader admin interface (public data attributes like db_path
        are not operations and are excluded)."""
        import inspect
        public_methods = [
            name for name, member in inspect.getmembers(EmergencyUnlockService, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]
        assert public_methods == ["request_unlock"]

    def test_provider_interface_accepts_only_an_access_token_no_arbitrary_parameters(self) -> None:
        import inspect
        sig = inspect.signature(EmergencyUnlockProvider.attempt_unlock)
        assert list(sig.parameters) == ["self", "access_token"]


class TestNoConnection:
    def test_authenticated_request_with_no_connection_fails_safely(self, core, tmp_path, connections) -> None:
        service = _service(core, tmp_path, connections)
        result = service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        assert result.status is EmergencyUnlockRequestStatus.NO_CONNECTION


class TestProviderOutcomes:
    def test_provider_success_reports_provider_succeeded(self, core, tmp_path, connections) -> None:
        _create_connection(connections)
        provider = _FakeProvider(ProviderUnlockOutcome.SUCCEEDED, "unlocked ok")
        service = _service(core, tmp_path, connections, provider=provider)
        result = service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        assert result.status is EmergencyUnlockRequestStatus.PROVIDER_SUCCEEDED
        assert result.message == "unlocked ok"

    def test_provider_rejection_reports_provider_failed(self, core, tmp_path, connections) -> None:
        _create_connection(connections)
        provider = _FakeProvider(ProviderUnlockOutcome.FAILED, "provider rejected the request")
        service = _service(core, tmp_path, connections, provider=provider)
        result = service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        assert result.status is EmergencyUnlockRequestStatus.PROVIDER_FAILED

    def test_provider_timeout_modeled_as_failed_not_silently_ignored(self, core, tmp_path, connections) -> None:
        provider = _FakeProvider(ProviderUnlockOutcome.FAILED, "provider request timed out")
        _create_connection(connections)
        service = _service(core, tmp_path, connections, provider=provider)
        result = service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        assert result.status is EmergencyUnlockRequestStatus.PROVIDER_FAILED
        assert "timed out" in result.message

    def test_unwired_provider_never_reports_success(self, core, tmp_path, connections) -> None:
        _create_connection(connections)
        service = _service(core, tmp_path, connections)  # default: UnwiredEmergencyUnlockProvider
        result = service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        assert result.status is EmergencyUnlockRequestStatus.PROVIDER_FAILED
        assert "never contacts Chaster" in result.message

    def test_provider_receives_the_real_decrypted_access_token(self, core, tmp_path, connections) -> None:
        _create_connection(connections, access_token="the-real-plaintext-access-token")
        provider = _FakeProvider(ProviderUnlockOutcome.SUCCEEDED)
        service = _service(core, tmp_path, connections, provider=provider)
        service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        assert provider.calls == ["the-real-plaintext-access-token"]


class TestAuditTrail:
    def test_every_request_creates_at_least_one_audit_event(self, core, tmp_path, connections) -> None:
        service = _service(core, tmp_path, connections)
        service.request_unlock(presented_secret="wrong", user_id="u1", now=FIXED_TIME)
        assert len(_audit_rows(core)) >= 1

    def test_full_successful_flow_audits_every_stage(self, core, tmp_path, connections) -> None:
        _create_connection(connections)
        provider = _FakeProvider(ProviderUnlockOutcome.SUCCEEDED)
        service = _service(core, tmp_path, connections, provider=provider)
        service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        event_types = [r[0] for r in _audit_rows(core)]
        assert event_types == [
            "chaster_emergency.request_accepted",
            "chaster_emergency.provider_attempted",
            "chaster_emergency.provider_succeeded",
        ]

    def test_full_failed_flow_audits_every_stage(self, core, tmp_path, connections) -> None:
        _create_connection(connections)
        provider = _FakeProvider(ProviderUnlockOutcome.FAILED)
        service = _service(core, tmp_path, connections, provider=provider)
        service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        event_types = [r[0] for r in _audit_rows(core)]
        assert event_types == [
            "chaster_emergency.request_accepted",
            "chaster_emergency.provider_attempted",
            "chaster_emergency.provider_failed",
        ]

    def test_no_connection_flow_audits_correctly(self, core, tmp_path, connections) -> None:
        service = _service(core, tmp_path, connections)
        service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        event_types = [r[0] for r in _audit_rows(core)]
        assert event_types == ["chaster_emergency.request_accepted", "chaster_emergency.no_connection"]

    def test_secrets_never_appear_in_any_audit_payload(self, core, tmp_path, connections) -> None:
        _create_connection(connections, access_token="UNIQUE-SECRET-ACCESS-TOKEN")
        provider = _FakeProvider(ProviderUnlockOutcome.SUCCEEDED)
        service = _service(core, tmp_path, connections, provider=provider)
        service.request_unlock(presented_secret=REAL_SECRET, user_id="u1", now=FIXED_TIME)
        rows = _audit_rows(core)
        for _event_type, payload_json in rows:
            assert "UNIQUE-SECRET-ACCESS-TOKEN" not in payload_json
            assert REAL_SECRET not in payload_json

    def test_audit_events_are_written_to_the_shared_existing_outbox_no_new_table(self, core, tmp_path, connections) -> None:
        """Confirms this reuses infrastructure.outbox's existing
        domain_events table -- no new migration, no new schema."""
        service = _service(core, tmp_path, connections)
        service.request_unlock(presented_secret="wrong", user_id="u1", now=FIXED_TIME)
        with core.raw_connection() as conn:
            row = conn.execute(
                "SELECT source_module FROM domain_events WHERE event_type = 'chaster_emergency.auth_failed'"
            ).fetchone()
        assert row[0] == "chaster_emergency"


class TestNoAIIndependence:
    """Checks actual `import`/`from ... import` statements only --
    never a raw substring search across the whole module source, which
    would false-positive on this module's own explanatory docstrings
    (e.g. this file's own header names `bot/discord_bot.py` as
    something it deliberately does NOT import)."""

    @staticmethod
    def _imported_module_names(module) -> set[str]:
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(module))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_emergency_unlock_module_imports_nothing_from_the_normal_control_plane(self) -> None:
        import chaster.emergency_unlock as module
        imported = self._imported_module_names(module)
        forbidden = ("discord", "conversation_engine", "application", "ollama")
        assert not any(name.startswith(f) for name in imported for f in forbidden)

    def test_emergency_unlock_listener_module_imports_nothing_from_the_normal_control_plane(self) -> None:
        import chaster.emergency_unlock_listener as module
        imported = self._imported_module_names(module)
        forbidden = ("discord", "conversation_engine", "application", "ollama")
        assert not any(name.startswith(f) for name in imported for f in forbidden)

    def test_emergency_unlock_server_module_imports_nothing_from_the_normal_control_plane(self) -> None:
        import chaster.emergency_unlock_server as module
        imported = self._imported_module_names(module)
        forbidden = ("discord", "conversation_engine", "application", "ollama")
        assert not any(name.startswith(f) for name in imported for f in forbidden)

    def test_lock_client_module_imports_nothing_from_the_normal_control_plane(self) -> None:
        import chaster.lock_client as module
        imported = self._imported_module_names(module)
        forbidden = ("discord", "conversation_engine", "application", "ollama")
        assert not any(name.startswith(f) for name in imported for f in forbidden)

    def test_emergency_unlock_provider_module_imports_nothing_from_the_normal_control_plane(self) -> None:
        import chaster.emergency_unlock_provider as module
        imported = self._imported_module_names(module)
        forbidden = ("discord", "conversation_engine", "application", "ollama")
        assert not any(name.startswith(f) for name in imported for f in forbidden)

    def test_bot_discord_bot_module_never_references_emergency_unlock(self) -> None:
        import bot.discord_bot as module
        imported = self._imported_module_names(module)
        assert not any("emergency" in name for name in imported)
