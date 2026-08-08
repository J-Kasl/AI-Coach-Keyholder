-- =============================================================================
-- Migration 020 — Task Runtime: task_assignments, and task_catalog's own
-- new lock_requirement column
-- =============================================================================
-- docs/architecture/task_runtime_technical_design.md (draft, not
-- approved for implementation as a whole). See task_runtime/README.md
-- for the exact boundary this migration implements.
--
-- Two distinct changes, both required for this slice:
--
-- 1. task_template_versions gets a new `lock_requirement` column.
--    Owned by task_catalog, NOT task_runtime -- lock requirement is a
--    property of the task DEFINITION (the same kind of field as
--    required_equipment/safety_classification already are), never of
--    a specific user's assignment. This keeps the dependency direction
--    one-way: task_runtime -> task_catalog, never the reverse.
--
--    ALTER TABLE ... ADD COLUMN requires a DEFAULT for the NOT NULL
--    constraint to be syntactically valid, even though no existing
--    rows exist yet in this project's own runtime today -- the
--    default itself is never relied upon by application code, which
--    always supplies lock_requirement explicitly (task_catalog/models.py's
--    own TaskTemplateVersion has no default for this field at the
--    Python level).
--
-- 2. task_assignments -- what was assigned to a specific user, and its
--    lifecycle. APPEND-adjacent, not append-only in the strict sense
--    task_template_versions is: a row IS updated in place (status,
--    resolved_at, resolved_via_consent_id) when an assignment resolves
--    -- there is exactly one row per assignment for its entire
--    lifetime, never a new row per transition. This differs
--    deliberately from lock_reports' own append-only history model --
--    an assignment is a single entity with a lifecycle, not a series
--    of independent reports.
--
--    FOREIGN KEY (template_id, template_version) REFERENCES
--    task_template_versions(template_id, version) -- a composite FK
--    against task_template_versions' own existing UNIQUE(template_id,
--    version) constraint (migration 014). This is the actual database-
--    level guarantee that an assignment can never reference a
--    template_id/version pair that doesn't exist -- not merely a
--    repository-level lookup that could race. A valid template_id with
--    an invalid/nonexistent version number fails this FK exactly the
--    same way a wholly nonexistent template_id does.
--
--    The partial unique index below enforces "at most one ACTIVE
--    assignment per user_id" at the database level -- the same idiom
--    advanced_mode's own migration 017 already established
--    (idx_one_active_mode_transition_request), applied here per-user
--    instead of as a global singleton.
-- =============================================================================

ALTER TABLE task_template_versions ADD COLUMN lock_requirement TEXT NOT NULL DEFAULT 'none';


CREATE TABLE IF NOT EXISTS task_assignments (
    id                          TEXT PRIMARY KEY,
    user_id                     TEXT NOT NULL REFERENCES user_accounts(id),
    template_id                 TEXT NOT NULL,
    template_version             INTEGER NOT NULL,
    status                        TEXT NOT NULL,
    assigned_at                    TEXT NOT NULL,
    assigned_via_consent_id          TEXT NOT NULL,
    resolved_at                        TEXT,
    resolved_via_consent_id              TEXT,
    FOREIGN KEY (template_id, template_version)
        REFERENCES task_template_versions(template_id, version)
);

CREATE INDEX IF NOT EXISTS idx_task_assignments_user
    ON task_assignments(user_id);

CREATE UNIQUE INDEX idx_one_active_assignment_per_user
    ON task_assignments(user_id)
    WHERE status = 'active';


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (20, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Task Runtime: task_assignments with composite FK to task_template_versions, at-most-one-active-per-user partial unique index; task_catalog own new lock_requirement column');
