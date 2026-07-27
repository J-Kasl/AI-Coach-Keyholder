-- =============================================================================
-- Migration 005 — Penalty Engine, Slice 1 (Phase 2.3)
-- =============================================================================
-- docs/architecture/penalty_window_technical_design.md Sections 2.1-2.6,
-- 3.1-3.3 (minus 3.4, Recovery Credit integration — deferred, see
-- penalty_engine/README.md).
--
-- domain_events/domain_event_consumers already exist (migration 002).
-- trust_domains/trust_domain_state already exist, owned by the Trust
-- Manager (migration 003) — this migration only adds tables genuinely
-- new to the Penalty Engine.
-- =============================================================================

CREATE TABLE IF NOT EXISTS penalty_windows (
    id                          TEXT PRIMARY KEY,
    created_at                   TEXT NOT NULL,
    status                       TEXT NOT NULL,     -- 'active' | 'frozen' | 'completed'
    closed_at                    TEXT,
    resolution_method            TEXT,               -- 'countdown_complete' -- 'manual_termination' deferred (terminate() not implemented, 2.1)

    base_duration_hours          REAL NOT NULL,
    extensions_hours             REAL NOT NULL DEFAULT 0,   -- I1: written only by penalty_engine; always 0 in this slice (extend()/should_extend() deferred)

    accumulated_active_hours     REAL NOT NULL DEFAULT 0,
    active_period_started_at     TEXT                -- NULL when FROZEN/COMPLETED
);


-- =============================================================================
-- Freeze as a set of concurrently active reasons (2.3) -- a window is
-- FROZEN exactly when >=1 row here has ended_at IS NULL, regardless of
-- reason (I22/PW-FREEZE-SET).
-- =============================================================================

CREATE TABLE IF NOT EXISTS freeze_periods (
    id                          TEXT PRIMARY KEY,
    penalty_window_id           TEXT NOT NULL REFERENCES penalty_windows(id),
    started_at                   TEXT NOT NULL,
    ended_at                      TEXT,               -- NULL = this reason is still active
    reason                        TEXT NOT NULL,      -- 'temporary_wear_exemption' | 'emergency_override' | 'partnered_intimacy_authorization'

    exemption_id                  TEXT,                -- populated ONLY for reason='temporary_wear_exemption'
    authorization_decision_id     TEXT,                -- populated ONLY for reason='partnered_intimacy_authorization'
    expires_at                     TEXT,                -- populated ONLY when the reason carries a policy-driven cap

    end_reason                     TEXT,                -- 'resumed_normally' | 'expired' | NULL while still open

    CHECK (
        (reason = 'temporary_wear_exemption'        AND exemption_id IS NOT NULL AND authorization_decision_id IS NULL) OR
        (reason = 'partnered_intimacy_authorization' AND authorization_decision_id IS NOT NULL AND exemption_id IS NULL) OR
        (reason = 'emergency_override'               AND exemption_id IS NULL AND authorization_decision_id IS NULL)
    )
);

-- I21 (AA-FREEZE-1): at most one OPEN partnered_intimacy_authorization
-- freeze per window -- other reasons are unaffected (a general set, no
-- count limit, per PW-FREEZE-SET).
CREATE UNIQUE INDEX IF NOT EXISTS idx_freeze_periods_one_open_intimacy_auth
    ON freeze_periods (penalty_window_id)
    WHERE reason = 'partnered_intimacy_authorization' AND ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_freeze_periods_window ON freeze_periods(penalty_window_id);
CREATE INDEX IF NOT EXISTS idx_freeze_periods_open ON freeze_periods(penalty_window_id, ended_at);
CREATE INDEX IF NOT EXISTS idx_freeze_periods_auth_decision ON freeze_periods(authorization_decision_id);


-- =============================================================================
-- incident_consumption (I11/I12) -- the Penalty Engine's OWN record of
-- which already-CONFIRMED Incidents (owned entirely by the Trust
-- Manager) it has consumed. Never duplicates Incident's own shape
-- (confirmation, assessment, description) -- those stay in
-- trust_manager's incidents table, read only via get_incident_assessment()/
-- get_confirmed_incidents_since().
-- =============================================================================

CREATE TABLE IF NOT EXISTS incident_consumption (
    incident_id          TEXT PRIMARY KEY,   -- write-once via PRIMARY KEY (I11)
    penalty_window_id     TEXT NOT NULL REFERENCES penalty_windows(id),
    trust_domain           TEXT NOT NULL,      -- denormalized snapshot from get_confirmed_incidents_since(), never re-derived
    consumed_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incident_consumption_window ON incident_consumption(penalty_window_id);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (5, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Penalty Engine Slice 1: state machine, freeze-as-set-of-reasons, incident_consumption (Faze 2.3)');
