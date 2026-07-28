"""tests/application/test_user_service.py"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application.user_service import UserService
from infrastructure.database import Database as CoreDatabase

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


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
def users(core: CoreDatabase) -> UserService:
    return UserService(core.db_path, core=core)


class TestGetOrCreateUser:
    def test_first_contact_creates_a_new_account(self, users: UserService) -> None:
        account = users.get_or_create_user("discord", "12345", now=FIXED_TIME)
        assert account.id is not None
        assert account.created_at == FIXED_TIME
        assert account.last_seen_at == FIXED_TIME

    def test_second_contact_returns_the_same_account(self, users: UserService) -> None:
        first = users.get_or_create_user("discord", "12345", now=FIXED_TIME)
        second = users.get_or_create_user("discord", "12345", now=FIXED_TIME + timedelta(days=1))
        assert second.id == first.id

    def test_second_contact_updates_last_seen_at(self, users: UserService) -> None:
        users.get_or_create_user("discord", "12345", now=FIXED_TIME)
        second = users.get_or_create_user("discord", "12345", now=FIXED_TIME + timedelta(days=1))
        assert second.last_seen_at == FIXED_TIME + timedelta(days=1)
        assert second.created_at == FIXED_TIME  # unchanged

    def test_different_external_ids_get_different_accounts(self, users: UserService) -> None:
        a = users.get_or_create_user("discord", "111", now=FIXED_TIME)
        b = users.get_or_create_user("discord", "222", now=FIXED_TIME)
        assert a.id != b.id

    def test_same_external_id_on_different_channels_gets_different_accounts(self, users: UserService) -> None:
        """The identity mapping is keyed on (channel, external_id) together --
        the same raw id string on two different channels is not assumed to be the same person."""
        a = users.get_or_create_user("discord", "111", now=FIXED_TIME)
        b = users.get_or_create_user("some_other_channel", "111", now=FIXED_TIME)
        assert a.id != b.id

    def test_get_user_by_id(self, users: UserService) -> None:
        created = users.get_or_create_user("discord", "12345", now=FIXED_TIME)
        fetched = users.get_user(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_user_missing_returns_none(self, users: UserService) -> None:
        assert users.get_user("does-not-exist") is None
