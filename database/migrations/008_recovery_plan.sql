-- =============================================================================
-- Migration 008 — Recovery Plan (Fáze 2.6)
-- =============================================================================
-- docs/architecture/recovery_plan_technical_design.md.
-- See recovery_plan/README.md for exactly what this slice covers.
-- =============================================================================

CREATE TABLE IF NOT EXISTS recovery_plans (
    id                              TEXT PRIMARY KEY,
    penalty_window_id                TEXT NOT NULL UNIQUE REFERENCES penalty_windows(id),   -- RP-7: exactly one plan per window
    status                            TEXT NOT NULL,     -- 'active' | 'frozen' | 'completed'
    current_version                    INTEGER NOT NULL DEFAULT 1,
    recovery_credit_capacity_hours       REAL NOT NULL,   -- RP-3: a snapshot, refreshed only at creation/regeneration
    created_at                            TEXT NOT NULL,
    status_changed_at                      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recovery_plans_window ON recovery_plans(penalty_window_id);


CREATE TABLE IF NOT EXISTS recovery_tasks (
    id                     TEXT PRIMARY KEY,
    recovery_plan_id        TEXT NOT NULL REFERENCES recovery_plans(id),
    plan_version              INTEGER NOT NULL,
    title                      TEXT NOT NULL,
    description                 TEXT NOT NULL,
    credit_hours                 REAL NOT NULL,     -- the Coach's proposed value (3.3) -- not independently re-enforced here
    status                        TEXT NOT NULL,     -- 'proposed' | 'accepted' | 'completed' | 'expired' | 'withdrawn'
    created_at                     TEXT NOT NULL,
    status_changed_at               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recovery_tasks_plan ON recovery_tasks(recovery_plan_id, plan_version);


-- Append-only (RP-2: Recovery Plan's own interpretation, never
-- re-derived by the Penalty Engine). Shape not given explicitly in the
-- architecture document (unlike RecoveryPlan/RecoveryTask) -- this
-- slice's own design, see recovery_plan/README.md.
CREATE TABLE IF NOT EXISTS recovery_task_completions (
    id                    TEXT PRIMARY KEY,
    recovery_task_id       TEXT NOT NULL REFERENCES recovery_tasks(id),
    recovery_plan_id         TEXT NOT NULL REFERENCES recovery_plans(id),   -- denormalized, since a completion is always scoped to one plan
    created_at                TEXT NOT NULL,
    notes                       TEXT
);

CREATE INDEX IF NOT EXISTS idx_recovery_task_completions_task ON recovery_task_completions(recovery_task_id);


-- =============================================================================
-- Seed: zápis této migrace do schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (8, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Recovery Plan: recovery_plans, recovery_tasks, recovery_task_completions (Faze 2.6)');
