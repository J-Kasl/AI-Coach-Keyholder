"""tests/infrastructure/test_plugin_migrations.py"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.database import Database as CoreDatabase
from infrastructure.plugin_migrations import apply_plugin_migrations


def _apply_core_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_core_migrations(c)
    return c


def _write_migration(migrations_dir: Path, filename: str, sql: str) -> None:
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / filename).write_text(sql, encoding="utf-8")


class TestApplyPluginMigrations:
    def test_no_migrations_dir_returns_empty(self, core: CoreDatabase, tmp_path: Path) -> None:
        applied = apply_plugin_migrations(core, "some_plugin", tmp_path / "does_not_exist")
        assert applied == []

    def test_applies_a_single_migration(self, core: CoreDatabase, tmp_path: Path) -> None:
        migrations_dir = tmp_path / "migrations"
        _write_migration(
            migrations_dir, "001_thing.sql",
            """
            CREATE TABLE IF NOT EXISTS plugin_thing (id TEXT PRIMARY KEY);
            INSERT INTO plugin_schema_versions (plugin_name, version, applied_at, description)
            VALUES ('demo_plugin', 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'creates plugin_thing');
            """,
        )
        applied = apply_plugin_migrations(core, "demo_plugin", migrations_dir)
        assert applied == [1]

        with core.transaction() as tx:
            row = tx.fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name='plugin_thing'")
        assert row is not None

    def test_second_call_applies_nothing_new(self, core: CoreDatabase, tmp_path: Path) -> None:
        migrations_dir = tmp_path / "migrations"
        _write_migration(
            migrations_dir, "001_thing.sql",
            """
            CREATE TABLE IF NOT EXISTS plugin_thing (id TEXT PRIMARY KEY);
            INSERT INTO plugin_schema_versions (plugin_name, version, applied_at, description)
            VALUES ('demo_plugin', 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'creates plugin_thing');
            """,
        )
        first = apply_plugin_migrations(core, "demo_plugin", migrations_dir)
        second = apply_plugin_migrations(core, "demo_plugin", migrations_dir)
        assert first == [1]
        assert second == []

    def test_two_plugins_track_independent_versions(self, core: CoreDatabase, tmp_path: Path) -> None:
        """Migration numbering (and current version) is scoped per
        plugin_name -- plugin A having applied its own '001' must never
        make plugin B's own '001' look already-applied."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_migration(
            dir_a, "001_a.sql",
            "CREATE TABLE IF NOT EXISTS a_table (id TEXT PRIMARY KEY);\n"
            "INSERT INTO plugin_schema_versions (plugin_name, version, applied_at, description) "
            "VALUES ('plugin_a', 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'a');",
        )
        _write_migration(
            dir_b, "001_b.sql",
            "CREATE TABLE IF NOT EXISTS b_table (id TEXT PRIMARY KEY);\n"
            "INSERT INTO plugin_schema_versions (plugin_name, version, applied_at, description) "
            "VALUES ('plugin_b', 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'b');",
        )
        applied_a = apply_plugin_migrations(core, "plugin_a", dir_a)
        applied_b = apply_plugin_migrations(core, "plugin_b", dir_b)
        assert applied_a == [1]
        assert applied_b == [1]

        with core.transaction() as tx:
            a_row = tx.fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name='a_table'")
            b_row = tx.fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name='b_table'")
        assert a_row is not None
        assert b_row is not None

    def test_multiple_pending_migrations_applied_in_order(self, core: CoreDatabase, tmp_path: Path) -> None:
        migrations_dir = tmp_path / "migrations"
        _write_migration(
            migrations_dir, "001_first.sql",
            "CREATE TABLE IF NOT EXISTS t1 (id TEXT PRIMARY KEY);\n"
            "INSERT INTO plugin_schema_versions (plugin_name, version, applied_at, description) "
            "VALUES ('demo_plugin', 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'first');",
        )
        _write_migration(
            migrations_dir, "002_second.sql",
            "CREATE TABLE IF NOT EXISTS t2 (id TEXT PRIMARY KEY);\n"
            "INSERT INTO plugin_schema_versions (plugin_name, version, applied_at, description) "
            "VALUES ('demo_plugin', 2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'second');",
        )
        applied = apply_plugin_migrations(core, "demo_plugin", migrations_dir)
        assert applied == [1, 2]
