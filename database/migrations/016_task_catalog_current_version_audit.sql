-- =============================================================================
-- Migration 016 — Task Catalog: current_version_changed_at
-- =============================================================================
-- Fixes a second, confirmed gap found under direct review:
-- set_current_version() accepted a `now` parameter but never used it --
-- after advancing current_version, the database could say WHO
-- authorized the change (current_version_changed_via_consent_id,
-- migration 015) but not WHEN. This makes the current_version audit
-- symmetric with the eligibility audit, which already had both
-- eligibility_changed_via_consent_id AND status_changed_at.
--
-- SQLite's ALTER TABLE ADD COLUMN only accepts a literal/constant
-- DEFAULT, never an expression referencing another column -- so
-- backfilling existing rows from status_changed_at cannot be done as
-- part of the ADD COLUMN statement itself. Smallest safe variant:
-- add the column nullable (no DEFAULT at all), then a separate UPDATE
-- backfills it. Left nullable rather than rebuilding the table to add
-- a NOT NULL constraint afterward (SQLite has no ALTER COLUMN for
-- this; doing so would mean CREATE a new table, copy every row, DROP
-- the old one, RENAME -- a much larger operation than this fix
-- warrants), matching the same nullable pattern migration 015's own
-- two columns already use for the identical reason.
--
-- IMPORTANT: for rows that existed before this migration, the
-- backfilled value is the best available historical approximation,
-- NOT a historically accurate record of when the current_version
-- pointer itself last changed. The prior schema kept no separate
-- timestamp for that at all -- status_changed_at tracks a DIFFERENT
-- field (eligibility_status) that merely happened to also change at
-- entry-creation time. Any row written by TaskCatalogAdministration
-- from this migration forward gets a real, accurate
-- current_version_changed_at -- only pre-existing rows carry this
-- approximation.
-- =============================================================================

ALTER TABLE task_template_catalog_entries
    ADD COLUMN current_version_changed_at TEXT;

UPDATE task_template_catalog_entries
    SET current_version_changed_at = status_changed_at
    WHERE current_version_changed_at IS NULL;


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (16, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Task Catalog: current_version_changed_at on task_template_catalog_entries (backfilled from status_changed_at for pre-existing rows -- an approximation, not a historically accurate record)');
