-- =============================================================================
-- Migration 023 — CHASTER-01B: Provider Observation Log (Increment 1)
-- =============================================================================
-- docs/architecture/chaster_integration_technical_design.md, Section
-- 25a Milestone D / Section 26. This migration implements ONLY the
-- storage for CHASTER-01B's own read-only provider observations --
-- see chaster/README.md and this project's own CHASTER-01B review
-- turn for the exact boundary.
--
-- `chaster_provider_observations` is deliberately structurally
-- separate from `lock_state`'s own `lock_reports` (migration 019) --
-- no foreign key between them, no merged/combined status anywhere.
-- The two are stored, read, and displayed independently, exactly as
-- Section 15 ("Conflict with user-reported state") specifies: this
-- table represents what Chaster's own API reported, at the moment it
-- was fetched -- PROVIDER-OBSERVED, never "verified" (no status value
-- stored here may ever be named or treated as VERIFIED -- see
-- chaster/emergency_unlock_provider.py's own, already-established
-- precedent for this same naming discipline applied to a different
-- Chaster status concept).
--
-- Append-only, matching `lock_reports`/`task_template_versions`' own
-- established discipline (migrations 019/021) -- "current provider
-- state" is simply the most recent row for a connection, never an
-- UPDATE or DELETE after insert.
--
-- `connection_id` references `chaster_connections(user_id)`
-- (migration 022) -- the CHASTER-01A connection this observation was
-- fetched through. `chaster_lock_id` is nullable: an observation can
-- legitimately represent "zero active locks found" (see
-- chaster/emergency_unlock_provider.py's own confirmed, real handling
-- of that exact case), which has no lock id to record.
--
-- `provider` is a free-text column (not an enum/CHECK constraint) --
-- CHASTER-01 has exactly one provider (Chaster) today, but this keeps
-- the column meaningfully named rather than implicitly Chaster-only,
-- matching the same forward-looking, unconstrained-string choice
-- already made for `chaster_connections.connection_status`.
--
-- Index: read pattern is always "the most recent observation for a
-- given connection" (`ORDER BY fetched_at DESC LIMIT 1`, mirroring
-- lock_state/repository.py::LockState.get_current_report()'s own
-- `ORDER BY ... DESC LIMIT 1` pattern) -- this index serves exactly
-- that query.

CREATE TABLE IF NOT EXISTS chaster_provider_observations (
    id              TEXT PRIMARY KEY,
    connection_id   TEXT NOT NULL REFERENCES chaster_connections(user_id),
    provider        TEXT NOT NULL,
    chaster_lock_id TEXT,
    status          TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chaster_provider_observations_connection_fetched
    ON chaster_provider_observations(connection_id, fetched_at DESC);

-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (23, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'CHASTER-01B Increment 1: chaster_provider_observations (read-only provider observation log)');
