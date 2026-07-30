"""
infrastructure/plugin_migrations.py

apply_plugin_migrations() -- the plugin-scoped equivalent of
`database.database.Database.migrate()`, tracked in `plugin_schema_versions`
(migration 012) instead of core's own `schema_version`. Mirrors that
function's own logic deliberately closely, not a reimplementation with
different behavior: same "only additive, never destructive" rule
(database/migrations/README.md, unchanged for plugins -- see
plugin_architecture_proposal.md Section 13), same
`executescript()`-per-file approach, same reliance on each migration
file's own seed INSERT (via SQLite's own `strftime('now')`) rather than
a `now` passed in from Python.

Canonical: docs/architecture/plugin_architecture_proposal.md v1.3
Section 13.
"""

from __future__ import annotations

from pathlib import Path

from infrastructure.database import Database as CoreDatabase

__all__ = ["apply_plugin_migrations"]


def apply_plugin_migrations(core: CoreDatabase, plugin_name: str, migrations_dir: Path) -> list[int]:
    """
    Applies every `<migrations_dir>/*.sql` not yet applied for
    `plugin_name` specifically, in filename order (`001_`, `002_`, ...).
    Returns the version numbers newly applied. A plugin's own migration
    directory is never interleaved with another plugin's, or with
    core's own `database/migrations/`.

    No backup step here (unlike `Database.migrate()`'s own
    pre_migration backup) -- a deliberate simplification: core's own
    daily/pre-migration backup already covers the whole database file,
    plugin tables included, and this function does not duplicate that
    concern.
    """
    if not migrations_dir.is_dir():
        return []

    migration_files = sorted(migrations_dir.glob("*.sql"))

    with core.raw_connection() as conn:
        current_version = 0
        row = conn.execute(
            "SELECT MAX(version) as v FROM plugin_schema_versions WHERE plugin_name = ?", (plugin_name,)
        ).fetchone()
        if row and row["v"] is not None:
            current_version = row["v"]

        pending = [
            path for path in migration_files
            if int(path.name.split("_")[0]) > current_version
        ]
        if not pending:
            return []

        applied: list[int] = []
        for path in pending:
            version = int(path.name.split("_")[0])
            conn.executescript(path.read_text(encoding="utf-8"))
            applied.append(version)

    return applied
