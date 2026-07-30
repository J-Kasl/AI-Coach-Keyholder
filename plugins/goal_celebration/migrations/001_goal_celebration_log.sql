-- plugins/goal_celebration/migrations/001_goal_celebration_log.sql
--
-- Exists purely for idempotency (has this specific Goal already been
-- celebrated) -- the same reasoning every domain module in this
-- project already applies to its own dedup concerns
-- (plugin_architecture_proposal.md Section 20). No FK into
-- goal_management's own tables (Section 13) -- goal_group_id is a
-- stored reference, never a hard FK.

CREATE TABLE IF NOT EXISTS goal_celebration_log (
    goal_group_id    TEXT PRIMARY KEY,
    celebrated_at      TEXT NOT NULL
);

INSERT INTO plugin_schema_versions (plugin_name, version, applied_at, description)
VALUES ('goal_celebration', 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'goal_celebration_log table for idempotent celebration');
