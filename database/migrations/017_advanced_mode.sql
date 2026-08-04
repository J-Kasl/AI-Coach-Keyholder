-- =============================================================================
-- Migration 017 — Advanced Mode: operating_mode_state, mode_transition_requests
-- =============================================================================
-- docs/architecture/advanced_mode_technical_design.md (draft, not
-- approved for implementation as a whole -- this migration implements
-- ONLY OperatingMode itself, its persistence, and the two-stage
-- critical_change transition process. No DelegatedAuthorityPolicy, no
-- Token Economy, no Hygiene values, no Carry Bank, no Equipment
-- Inventory, no Task assignment, no originating_mode -- see
-- advanced_mode/README.md for the exact boundary.
--
-- operating_mode_state -- a GLOBAL SINGLETON, not per-user. The
-- current domain core (Trust Manager, Penalty Engine) has no user_id
-- anywhere -- it is single-subject by design (this project's own
-- current architecture, not a statement about future multi-user
-- support). UserAccount (application/, migration 011) exists only for
-- Discord-channel-identity bookkeeping; tying OperatingMode to it
-- would be the first place in this project where a genuinely
-- normative domain concept was scoped to one particular channel
-- identity rather than the system as a whole. Mirrors
-- system_startup_lease's own singleton pattern (migration 006):
-- `id INTEGER PRIMARY KEY CHECK (id = 1)`.
--
-- mode_transition_requests -- mutable-with-status (the same shape
-- goal_change_proposals and penalty_windows already use): CANCELLED
-- and COMPLETED are terminal *values* of `status`, not rows moved to
-- a separate archive table -- both existing precedents keep resolved
-- rows in place, relying on `status` itself (not deletion or
-- migration to another table) to distinguish active from historical.
-- =============================================================================

CREATE TABLE operating_mode_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    current_mode        TEXT NOT NULL,
    mode_activated_at   TEXT NOT NULL
);

-- Bootstrap: a new installation starts in STANDARD.
-- mode_activated_at uses the same strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
-- convention already established for one-time migration bootstrap
-- values (see e.g. migration 016's own schema_version seed) -- this is
-- a one-time migration-time timestamp, not a runtime domain time; all
-- RUNTIME reads/writes of mode_activated_at (from advanced_mode's own
-- repository code) go through this project's Clock abstraction
-- (infrastructure/clock.py), exactly like every other domain module's
-- own datetime handling. The two are deliberately different code
-- paths for different purposes -- see advanced_mode/README.md.
INSERT INTO operating_mode_state (id, current_mode, mode_activated_at)
VALUES (1, 'standard', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));


CREATE TABLE mode_transition_requests (
    id                          TEXT PRIMARY KEY,
    source_mode                 TEXT NOT NULL,
    target_mode                 TEXT NOT NULL,
    status                      TEXT NOT NULL,
    requested_at                TEXT NOT NULL,
    requested_via_consent_id    TEXT NOT NULL,
    wait_started_at             TEXT,
    wait_interrupted_at         TEXT,
    confirmable_at              TEXT,
    confirmed_at                TEXT,
    confirmed_via_consent_id    TEXT,
    cancelled_at                TEXT,
    resolved_at                 TEXT,
    CHECK (target_mode != source_mode)
);

-- MODE-1: at most one non-terminal request at any time, globally, in
-- either direction. Verified functionally against this exact SQLite
-- version before being proposed (partial unique index over a constant
-- expression -- a well-known SQLite idiom for "at most one row
-- matching a condition").
CREATE UNIQUE INDEX idx_one_active_mode_transition_request
    ON mode_transition_requests((1))
    WHERE status NOT IN ('cancelled', 'completed');


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (17, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Advanced Mode: operating_mode_state (global singleton) and mode_transition_requests (two-stage critical_change transition)');
