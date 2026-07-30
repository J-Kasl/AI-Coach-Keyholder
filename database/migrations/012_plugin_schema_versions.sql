-- =============================================================================
-- Migration 012 — Plugin Infrastructure: plugin_schema_versions
-- =============================================================================
-- docs/architecture/plugin_architecture_proposal.md v1.3 Section 13.
--
-- The plugin-scoped equivalent of schema_version (001) -- tracks which
-- migration version has been applied for EACH plugin independently,
-- never interleaved with core's own database/migrations/001..011
-- numbering. One row per (plugin_name, version) applied, mirroring
-- schema_version's own "one row per migration" shape exactly.
--
-- Owned by core (this table itself is infrastructure every
-- owns_tables=True plugin's own migrations rely on), never by any
-- individual plugin.
-- =============================================================================

CREATE TABLE IF NOT EXISTS plugin_schema_versions (
    plugin_name        TEXT NOT NULL,
    version              INTEGER NOT NULL,
    applied_at             TEXT NOT NULL,
    description              TEXT NOT NULL,
    PRIMARY KEY (plugin_name, version)
);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (12, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Plugin Infrastructure: plugin_schema_versions (Plugin Infra Step 3)');
