# Migration policy

This directory contains sequential `.sql` migrations, applied in order
by the number in the filename (`001_`, `002_`, ...).
`database.database.Database.migrate()` tracks the current version in
the `schema_version` table and applies only migrations with a higher
number.

## Hard rule: a migration must never cause data loss

This rule exists because updating the application must never require,
or risk, deleting user data (see `philosophy.md`, principle 2.5 —
Consent & Control also covers the user never losing history simply
because they updated the application).

Concretely, this means:

**Allowed:**
- `CREATE TABLE IF NOT EXISTS ...`
- `ALTER TABLE ... ADD COLUMN ...` (a new column, ideally with a `DEFAULT`)
- `CREATE INDEX IF NOT EXISTS ...`
- A new migration that only **adds** data, or **copies/transforms** it
  into new structures while preserving the original data (and ideally
  the original tables too, until it's clear the migration went
  smoothly).

A column added without a `DEFAULT` (nullable) is also allowed, and is
the *correct* choice specifically when no literal default value could
be truthful for pre-existing rows — a fabricated default (e.g. an
empty string standing in for real content) is worse than `NULL`,
because it is indistinguishable from genuine data at read time. See
`database/migrations/021_task_catalog_content.sql` for a concrete
case: `title`/`instructions` are added nullable, with `NULL` reserved
to mean exactly "this row predates this migration," and no backfill
`UPDATE` is issued because no historically accurate value exists to
backfill with (contrast migrations 015/016, task_catalog's own
consent/timestamp audit columns, where a real backfill was possible
and was performed).

**Forbidden without an explicit exception and an extra backup:**
- `DROP TABLE`
- `DROP COLUMN`
- `DELETE FROM ...` outside targeted data-consistency fixes
- Any operation that, if it failed partway through, could leave data
  in an inconsistent or lost state

If it ever becomes necessary to remove or fundamentally restructure a
table, the process is:
1. A new migration creates the new structure alongside the old one (not
   in place of it).
2. Runtime code switches over to the new structure.
3. The old structure is kept for at least one "release" as a backup
   inside the DB.
4. Only then does a separate, explicitly labeled migration remove the
   old structure — and only after confirming the new structure works
   and the backup (below) exists.

## Backups

`Database.migrate()` automatically creates a backup
(`backup.create_backup`, `reason=pre_migration`) immediately before
applying any new migrations, if the database file already exists.
`Database.ensure_daily_backup()`, called at application startup, also
guarantees at most one automatic backup per day outside of migrations.
Backups are rotated (`backup.rotate_backups`) according to
`BACKUP_RETENTION_COUNT` from configuration (default 14).

Backups live in `data/backups/`, outside the source tree — updating the
application (e.g. `git pull`) never touches them.
