"""
tests/chaster/test_emergency_unlock_server.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from chaster.emergency_unlock_listener import EmergencyUnlockListener
from chaster.emergency_unlock_server import EmergencyServerConfigurationError, build_listener
from core.config import Config
from infrastructure.database import Database as CoreDatabase


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


class TestFailFastConfiguration:
    def test_refuses_to_build_without_an_emergency_secret(self, tmp_path: Path) -> None:
        config = Config(
            discord_token="test-token", db_path=tmp_path / "test.db",
            chaster_token_encryption_key=Fernet.generate_key().decode(),
            chaster_emergency_unlock_secret=None,
        )
        with pytest.raises(EmergencyServerConfigurationError, match="CHASTER_EMERGENCY_UNLOCK_SECRET"):
            build_listener(config)

    def test_refuses_to_build_without_a_token_encryption_key(self, tmp_path: Path) -> None:
        config = Config(
            discord_token="test-token", db_path=tmp_path / "test.db",
            chaster_token_encryption_key=None,
            chaster_emergency_unlock_secret="a-real-secret",
        )
        with pytest.raises(EmergencyServerConfigurationError, match="CHASTER_TOKEN_ENCRYPTION_KEY"):
            build_listener(config)

    def test_refuses_to_build_with_an_invalid_encryption_key(self, tmp_path: Path) -> None:
        config = Config(
            discord_token="test-token", db_path=tmp_path / "test.db",
            chaster_token_encryption_key="not-a-valid-fernet-key",
            chaster_emergency_unlock_secret="a-real-secret",
        )
        with pytest.raises(EmergencyServerConfigurationError):
            build_listener(config)

    def test_builds_successfully_with_complete_configuration(self, tmp_path: Path) -> None:
        core = CoreDatabase(tmp_path / "test.db")
        _apply_migrations(core)
        config = Config(
            discord_token="test-token", db_path=tmp_path / "test.db",
            chaster_token_encryption_key=Fernet.generate_key().decode(),
            chaster_emergency_unlock_secret="a-real-secret",
        )
        listener = build_listener(config, core=core)
        assert isinstance(listener, EmergencyUnlockListener)

    def test_production_wiring_uses_the_real_chaster_provider_not_the_unwired_one(self, tmp_path: Path) -> None:
        """Confirms the standalone server actually wires
        RealChasterEmergencyUnlockProvider (Option A's real discovery/
        eligibility/unlock control flow) as its production default,
        not the network-inert UnwiredEmergencyUnlockProvider."""
        core = CoreDatabase(tmp_path / "test.db")
        _apply_migrations(core)
        config = Config(
            discord_token="test-token", db_path=tmp_path / "test.db",
            chaster_token_encryption_key=Fernet.generate_key().decode(),
            chaster_emergency_unlock_secret="a-real-secret",
        )
        listener = build_listener(config, core=core)
        from chaster.emergency_unlock_provider import RealChasterEmergencyUnlockProvider
        assert isinstance(listener._service._provider, RealChasterEmergencyUnlockProvider)

    def test_configuration_error_messages_never_contain_the_secret_values(self, tmp_path: Path) -> None:
        config = Config(
            discord_token="test-token", db_path=tmp_path / "test.db",
            chaster_token_encryption_key="not-a-valid-fernet-key-UNIQUE-MARKER",
            chaster_emergency_unlock_secret="a-real-secret",
        )
        try:
            build_listener(config)
            pytest.fail("expected EmergencyServerConfigurationError")
        except EmergencyServerConfigurationError as exc:
            assert "UNIQUE-MARKER" not in str(exc)
