-- =============================================================================
-- Migration 014 — Task Catalog: task_template_versions, task_template_catalog_entries
-- =============================================================================
-- docs/architecture/task_catalog_technical_design.md (draft, not approved
-- for implementation as a whole -- this migration implements ONLY the
-- catalog layer itself: TC-1 through TC-4, TC-8's data shapes. No task
-- instance, no snapshot, no Task Runtime, no SourceReference table.
--
-- task_template_versions -- APPEND-ONLY (TC-1). No UPDATE, no DELETE is
-- ever issued against this table by application code. A correction is
-- a new row under the same template_id, next version number.
--
-- task_template_catalog_entries -- the mutable current-state pointer
-- (TC-2), exactly Goal's own relationship to GoalVersion
-- (goal_management, migration 010). eligibility_status lives here,
-- never on task_template_versions.
-- =============================================================================

CREATE TABLE IF NOT EXISTS task_template_versions (
    id                            TEXT PRIMARY KEY,
    template_id                     TEXT NOT NULL,
    version                           INTEGER NOT NULL,
    category                            TEXT NOT NULL,
    difficulty                            TEXT NOT NULL,
    effort                                  TEXT NOT NULL,
    duration_minutes                          INTEGER NOT NULL,
    required_equipment_json                     TEXT NOT NULL,   -- JSON array of str
    required_privacy                              TEXT NOT NULL,
    required_context                                TEXT NOT NULL,
    safety_classification                             TEXT NOT NULL,
    eligible_instance_roles_json                        TEXT NOT NULL,   -- JSON array of TaskInstanceRole values
    eligible_operating_modes_json                         TEXT NOT NULL,   -- JSON array of str
    completion_requirements_json                            TEXT NOT NULL,   -- JSON object
    verification_requirements_json                            TEXT NOT NULL,   -- JSON object
    reflection_requirements_json                                TEXT,            -- JSON object, or NULL
    created_at                                                    TEXT NOT NULL,
    created_via_consent_id                                          TEXT NOT NULL,   -- TC governance: never created without one
    UNIQUE (template_id, version)
);

CREATE INDEX IF NOT EXISTS idx_task_template_versions_template
    ON task_template_versions(template_id);


CREATE TABLE IF NOT EXISTS task_template_catalog_entries (
    template_id           TEXT PRIMARY KEY,
    current_version         INTEGER NOT NULL,
    eligibility_status         TEXT NOT NULL,
    status_changed_at            TEXT NOT NULL,
    FOREIGN KEY (template_id, current_version)
        REFERENCES task_template_versions(template_id, version)
);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (14, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Task Catalog: task_template_versions, task_template_catalog_entries (catalog layer only, no task instances)');
