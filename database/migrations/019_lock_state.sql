-- =============================================================================
-- Migration 019 — Lock State: lock_reports
-- =============================================================================
-- docs/architecture/lock_state_technical_design.md (draft, not approved
-- for implementation as a whole -- this migration implements ONLY the
-- user-reported lock state itself. No Chaster fields, no preference/task
-- data, no external/hardware verification of any kind -- see
-- lock_state/README.md for the exact boundary.
--
-- lock_reports -- APPEND-ONLY. No UPDATE, no DELETE is ever issued
-- against this table by application code. Every report the user makes
-- is a new row; "the current state" is read as the most recent row for
-- that user, never mutated in place -- the same discipline
-- task_template_versions (migration 014) and mode_transition_requests
-- (migration 017) already apply to their own append-only data.
--
-- `status` only ever stores LOCKED_USER_REPORTED or
-- UNLOCKED_USER_REPORTED -- never a persisted "unknown" row. Absence of
-- any row for a user IS the unknown state, read at the application
-- layer (lock_state/repository.py), not encoded as data here.
--
-- `sequence_number` is the deterministic ordering tiebreaker -- assigned
-- monotonically per user_id within the same write transaction, the same
-- precedent goal_management's own append-only `version` column already
-- established (never relying on timestamp precision or SQLite's own
-- implicit row order).
-- =============================================================================

CREATE TABLE IF NOT EXISTS lock_reports (
    id                          TEXT PRIMARY KEY,
    user_id                     TEXT NOT NULL REFERENCES user_accounts(id),
    status                      TEXT NOT NULL,
    sequence_number             INTEGER NOT NULL,
    reported_at                 TEXT NOT NULL,
    reported_via_consent_id     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lock_reports_user_sequence
    ON lock_reports(user_id, sequence_number DESC);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (19, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Lock State: lock_reports (append-only, user-reported lock status, never a verified physical fact)');
