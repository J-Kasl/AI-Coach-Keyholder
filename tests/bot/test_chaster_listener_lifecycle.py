"""
tests/bot/test_chaster_listener_lifecycle.py

CoachKeyholderBot.setup_hook()/close() -- the Chaster callback
listener's own start/stop lifecycle, and critically, that a listener
startup failure can never prevent the bot itself from being usable.
Same asyncio.run()-direct pattern as tests/bot/test_discord_bot.py
(pytest-asyncio is not a dependency of this project).
"""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from application.service import ApplicationService
from bot.discord_bot import build_bot
from chaster.oauth_client import ChasterOAuthClient
from chaster.repository import ChasterConnectionRepository
from chaster.token_encryptor import TokenEncryptor
from core.config import Config
from database.database import Database
from infrastructure.clock import FrozenClock
from infrastructure.database import Database as CoreDatabase

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(c)
    return c


@pytest.fixture
def configured_config(tmp_path: Path) -> Config:
    return Config(
        discord_token="test-token", db_path=tmp_path / "test.db",
        chaster_client_id="cid", chaster_client_secret="csecret",
        chaster_redirect_uri="https://example.com/oauth/chaster/callback",
        chaster_callback_bind_host="127.0.0.1", chaster_callback_bind_port=_free_port(),
        chaster_token_encryption_key=Fernet.generate_key().decode(),
    )


@pytest.fixture
def unconfigured_config(tmp_path: Path) -> Config:
    return Config(discord_token="test-token", db_path=tmp_path / "test.db")


def _configured_service(core: CoreDatabase, tmp_path: Path) -> ApplicationService:
    connections = ChasterConnectionRepository(tmp_path / "test.db", core=core, encryptor=TokenEncryptor(Fernet.generate_key()))
    oauth_client = ChasterOAuthClient(client_id="cid", client_secret="csecret", redirect_uri="https://example.com/oauth/chaster/callback")
    return ApplicationService(core.db_path, core=core, chaster_connections=connections, chaster_oauth_client=oauth_client)


class TestListenerStartsWhenConfigured:
    def test_setup_hook_starts_the_listener_when_chaster_is_fully_configured(
        self, configured_config: Config, core: CoreDatabase, tmp_path: Path,
    ) -> None:
        db = Database(configured_config.db_path, core=core)
        clock = FrozenClock(FIXED_TIME)
        service = _configured_service(core, tmp_path)
        bot = build_bot(configured_config, db, clock, service)

        async def _run_lifecycle() -> None:
            await bot.setup_hook()
            assert bot._chaster_listener is not None
            await bot.close()
            assert bot._chaster_listener is None

        _run(_run_lifecycle())


class TestListenerSkippedWhenNotConfigured:
    def test_setup_hook_does_not_start_a_listener_when_chaster_is_not_configured(
        self, unconfigured_config: Config, core: CoreDatabase,
    ) -> None:
        db = Database(unconfigured_config.db_path, core=core)
        clock = FrozenClock(FIXED_TIME)
        service = ApplicationService(core.db_path, core=core)  # no chaster_connections/oauth_client
        bot = build_bot(unconfigured_config, db, clock, service)

        async def _run_lifecycle() -> None:
            await bot.setup_hook()  # must not raise
            assert bot._chaster_listener is None
            await bot.close()  # must not raise even with nothing to stop

        _run(_run_lifecycle())


class TestListenerStartupFailureNeverCrashesTheBot:
    def test_a_listener_bind_failure_is_caught_and_does_not_propagate(
        self, configured_config: Config, core: CoreDatabase, tmp_path: Path,
    ) -> None:
        """The critical invariant: an exception during listener
        startup inside setup_hook() must never propagate, since
        setup_hook() runs before Discord's own websocket connects --
        an uncaught exception there would prevent the bot from
        starting at all."""
        db = Database(configured_config.db_path, core=core)
        clock = FrozenClock(FIXED_TIME)
        service = _configured_service(core, tmp_path)
        bot = build_bot(configured_config, db, clock, service)

        async def _run_lifecycle() -> None:
            with patch("bot.discord_bot.ChasterCallbackListener.start", side_effect=OSError("address already in use")):
                await bot.setup_hook()  # must NOT raise despite the simulated bind failure
            assert bot._chaster_listener is None
            await bot.close()  # must still be safe

        _run(_run_lifecycle())  # the test itself failing (an exception escaping) is the failure condition


class TestCloseIsResilientToListenerStopFailure:
    def test_a_listener_stop_failure_does_not_prevent_normal_shutdown_from_completing(
        self, configured_config: Config, core: CoreDatabase, tmp_path: Path,
    ) -> None:
        db = Database(configured_config.db_path, core=core)
        clock = FrozenClock(FIXED_TIME)
        service = _configured_service(core, tmp_path)
        bot = build_bot(configured_config, db, clock, service)

        async def _run_lifecycle() -> None:
            await bot.setup_hook()
            with patch("bot.discord_bot.ChasterCallbackListener.stop", side_effect=RuntimeError("simulated stop failure")):
                await bot.close()  # must not raise -- close() must still complete
            assert bot._chaster_listener is None

        _run(_run_lifecycle())
