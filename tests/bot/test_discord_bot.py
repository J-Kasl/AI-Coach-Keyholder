"""
tests/bot/test_discord_bot.py

Tests the adapter's own logic (DM filtering, message conversion, error
handling) WITHOUT a live Discord connection -- discord.Client.on_message()
is a plain coroutine method; calling it directly with a fake message
object (via asyncio.run(), stdlib only -- pytest-asyncio is not a
dependency of this project) exercises the exact same code path a real
gateway event would, without needing bot.run()/a network connection at
all.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from application.service import ApplicationService
from bot.discord_bot import build_bot
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


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(c)
    return c


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(discord_token="test-token", db_path=tmp_path / "test.db")


@pytest.fixture
def bot(config: Config, core: CoreDatabase):
    db = Database(config.db_path, core=core)
    clock = FrozenClock(FIXED_TIME)
    application_service = ApplicationService(config.db_path, core=core)
    return build_bot(config, db, clock, application_service)


def _fake_dm_message(content: str, author_id: int = 12345, message_id: int = 1) -> Mock:
    message = Mock()
    message.author = Mock()
    message.author.id = author_id
    message.content = content
    message.id = message_id
    message.channel = Mock(spec=discord.DMChannel)
    message.channel.id = 999
    message.channel.send = AsyncMock(return_value=Mock(id=message_id + 1))
    return message


def _fake_guild_message(content: str) -> Mock:
    message = Mock()
    message.author = Mock()
    message.author.id = 12345
    message.content = content
    message.channel = Mock(spec=discord.TextChannel)  # NOT a DMChannel
    message.channel.send = AsyncMock()
    return message


class TestDMFiltering:
    def test_dm_messages_get_a_reply(self, bot) -> None:
        message = _fake_dm_message("help")
        _run(bot.on_message(message))
        message.channel.send.assert_called_once()

    def test_guild_channel_messages_are_ignored(self, bot) -> None:
        """Per this phase's explicit scope: Discord communication only via DM."""
        message = _fake_guild_message("help")
        _run(bot.on_message(message))
        message.channel.send.assert_not_called()

    def test_bots_own_messages_are_ignored(self, bot, monkeypatch) -> None:
        message = _fake_dm_message("help")
        # discord.Client.user is a read-only property (backed by internal
        # connection state) -- overridden at the class level for this
        # test only, since there is no public setter to simulate "the
        # bot has logged in as this account."
        monkeypatch.setattr(type(bot), "user", property(lambda self: message.author))
        _run(bot.on_message(message))
        message.channel.send.assert_not_called()


class TestMessageFlow:
    def test_help_command_reaches_the_application_service_and_replies(self, bot) -> None:
        message = _fake_dm_message("help")
        _run(bot.on_message(message))
        reply_text = message.channel.send.call_args[0][0]
        assert "status" in reply_text.lower()

    def test_status_command_reaches_a_real_domain_module(self, bot) -> None:
        message = _fake_dm_message("status")
        _run(bot.on_message(message))
        reply_text = message.channel.send.call_args[0][0]
        assert "no active penalty window" in reply_text.lower()

    def test_both_sides_of_the_exchange_are_logged(self, bot, core: CoreDatabase) -> None:
        message = _fake_dm_message("help")
        _run(bot.on_message(message))
        with core.transaction() as tx:
            rows = tx.fetch_all("SELECT * FROM conversation_messages ORDER BY created_at")
        roles = [r["role"] for r in rows]
        assert "user" in roles
        assert "assistant" in roles


class TestAdapterLevelErrorHandling:
    def test_a_failure_in_audit_logging_does_not_prevent_a_real_reply(self, bot) -> None:
        """Audit logging is best-effort -- a failure there must never
        replace the real reply with the generic fallback (found while
        writing this test: the first implementation coupled the two,
        so a logging failure produced the generic error instead of the
        real 'help' response)."""
        message = _fake_dm_message("help")
        bot.db.save_conversation_message = Mock(side_effect=RuntimeError("simulated DB failure"))

        _run(bot.on_message(message))

        reply_text = message.channel.send.call_args[0][0]
        assert "available commands" in reply_text.lower()  # the REAL reply, not the fallback

    def test_a_failure_reaching_the_application_service_yields_a_safe_reply(self, bot) -> None:
        """Simulates a failure in the adapter's own call into the
        application layer -- the adapter's try/except around that call
        is a second, independent safety net on top of
        ApplicationService.handle_message()'s own internal one."""
        message = _fake_dm_message("help")
        bot.application_service.handle_message = Mock(side_effect=RuntimeError("simulated failure"))

        _run(bot.on_message(message))

        reply_text = message.channel.send.call_args[0][0]
        assert "went wrong" in reply_text.lower()
        assert "RuntimeError" not in reply_text  # never leaks internals to the user
