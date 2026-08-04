-- =============================================================================
-- Migration 015 — Task Catalog: consent audit columns on task_template_catalog_entries
-- =============================================================================
-- Fixes a real, confirmed gap found under direct review: set_current_version()/
-- activate()/deactivate() all required and validated a consent id, but none
-- of them persisted it anywhere -- task_template_catalog_entries had no
-- column for it, so the database could not answer "which consent
-- authorized this state" after the fact.
--
-- Deliberately NOT a literal copy of trust_manager's own
-- deactivated_via_consent_id pattern (migration 003) -- that column is
-- cleared to NULL on reactivation, relying on trust_manager's own
-- domain_events (trust_domain.reactivated's payload) to carry the
-- reactivation's consent instead. Task Catalog has no domain events
-- (a deliberate choice -- see task_catalog/README.md), so a
-- NULL-clearing column here would silently lose the activation
-- consent with no fallback anywhere. These two new columns are
-- therefore never cleared -- they always reflect the most recent
-- authorization, in either direction, and are the SOLE source of this
-- audit information (no event-log fallback exists for this module).
--
-- Nullable, not NOT NULL: rows created by migration 014, before this
-- migration existed, genuinely have no known answer for "who
-- authorized this" -- NULL honestly represents that, rather than a
-- fabricated default. Every newly created entry populates both columns
-- with the same creation consent; every later change populates only
-- the consent column for whichever field actually changed (activate()/
-- deactivate() only eligibility_changed_via_consent_id;
-- set_current_version() only current_version_changed_via_consent_id)
-- -- the two audit pairs are deliberately independent, not a single
-- combined one.
-- =============================================================================

ALTER TABLE task_template_catalog_entries
    ADD COLUMN eligibility_changed_via_consent_id TEXT;

ALTER TABLE task_template_catalog_entries
    ADD COLUMN current_version_changed_via_consent_id TEXT;


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (15, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Task Catalog: consent audit columns on task_template_catalog_entries (eligibility_changed_via_consent_id, current_version_changed_via_consent_id)');
