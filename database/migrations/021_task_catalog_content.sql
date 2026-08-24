-- =============================================================================
-- Migration 021 — Task Catalog: human-readable task content (title, instructions)
-- =============================================================================
-- Fixes a confirmed, documented gap: TaskTemplateVersion carried
-- machine-oriented metadata (category, difficulty, duration_minutes,
-- completion_requirements) but no field telling a user -- or the
-- Conversation Engine relaying it to them -- what the task actually
-- IS or what to DO. See ActiveTaskContextProvider's own pre-existing
-- docstring, which already named this as a known dev limitation.
--
-- Nullable, NOT NOT-NULL-with-a-default: an ALTER TABLE ... ADD COLUMN
-- ... NOT NULL requires a literal DEFAULT to be syntactically valid,
-- but any literal string placed there (e.g. '' or 'TBD') would be
-- indistinguishable at the row level from real authored content --
-- exactly the failure mode this migration exists to fix, reintroduced
-- under a different name. NULL is the only honest representation of
-- "this row predates human-readable task content" (the same reasoning
-- migrations 015/016 already used for their own nullable columns).
--
-- No backfill UPDATE is included, unlike migration 016's
-- current_version_changed_at -- there, a defensible historical
-- approximation existed (status_changed_at). No such approximation
-- exists here: no prior field on this table has ever held a title or
-- instructions, so there is nothing accurate to derive them from.
-- Existing rows are read back with title=NULL/instructions=NULL and
-- are handled explicitly and honestly at the application layer
-- (task_catalog/repository.py::_row_to_version passes NULL straight
-- through as Python None; conversation_engine/prompt_builder.py
-- renders that as an explicit "(not recorded)" marker -- never
-- fabricated here, and never fabricated at read time either).
--
-- Newly created TaskTemplateVersion rows (create_template()/
-- add_version(), from this migration forward) always populate both
-- columns with real, non-empty, normalized content -- enforced at the
-- WRITE API boundary (TaskCatalogAdministration.create_template()/
-- add_version(), which require `str`, not `str | None`, for both
-- parameters and reject empty/whitespace-only/oversized values), not
-- by this migration's own schema constraints.
-- =============================================================================

ALTER TABLE task_template_versions ADD COLUMN title TEXT;
ALTER TABLE task_template_versions ADD COLUMN instructions TEXT;

-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (21, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Task Catalog: title, instructions columns on task_template_versions (nullable -- no fabricated default; existing rows read back as NULL and are handled explicitly at the application layer)');
