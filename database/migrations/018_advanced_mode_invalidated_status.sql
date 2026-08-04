-- =============================================================================
-- Migration 018 — Advanced Mode: INVALIDATED status support
-- =============================================================================
-- Fixes a real, confirmed gap found under direct review:
-- confirm_transition() never re-checked that OperatingMode's current
-- value still matched the request's own source_mode before writing
-- target_mode as the new current mode -- an explicitly required
-- integrity check (the governing decision's own requirement: if the
-- current mode unexpectedly differs from the request's own starting
-- mode, the transition must not complete).
--
-- A mismatch invalidates the request -- distinct from CANCELLED (not
-- an explicit user cancellation) and distinct from COMPLETED/PAUSED
-- (the request's own premise, not merely its timing, is no longer
-- valid). invalidated_at is nullable for the same reason every other
-- audit timestamp in this table is: only rows reaching INVALIDATED
-- ever populate it.
--
-- The partial unique index (migration 017) must be recreated to
-- include 'invalidated' in the terminal set -- SQLite has no ALTER
-- INDEX, so this is DROP + CREATE, not an edit to migration 017
-- itself (which remains untouched, per this project's own standing
-- migration discipline).
-- =============================================================================

ALTER TABLE mode_transition_requests
    ADD COLUMN invalidated_at TEXT;

DROP INDEX idx_one_active_mode_transition_request;

CREATE UNIQUE INDEX idx_one_active_mode_transition_request
    ON mode_transition_requests((1))
    WHERE status NOT IN ('cancelled', 'completed', 'invalidated');


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (18, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Advanced Mode: INVALIDATED status (source_mode mismatch at confirmation), invalidated_at, and the partial unique index recreated to include it as a terminal status');
