-- =============================================================================
-- Migration 011 — Application Layer: user identity (Phase 3.1)
-- =============================================================================
-- application/README.md.
--
-- Deliberately NOT a multi-tenancy migration: every domain module built
-- so far (trust_manager, penalty_engine, recovery_plan, goal_management)
-- has single-user, unscoped schema -- no table anywhere has a user_id
-- column. This migration does not change that. It exists purely so the
-- application layer has somewhere to record "which channel identity
-- maps to which internal user" -- today there is exactly one real
-- person using this system, and this is bookkeeping for channel
-- abstraction (Discord today, something else later), not a foundation
-- for supporting multiple distinct users of the domain modules
-- themselves. See application/README.md for the explicit reasoning.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_accounts (
    id                TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_channel_identities (
    id                    TEXT PRIMARY KEY,
    user_account_id        TEXT NOT NULL REFERENCES user_accounts(id),
    channel                  TEXT NOT NULL,     -- 'discord' today; channel-agnostic by design
    external_id                TEXT NOT NULL,     -- e.g. the Discord user id, as a string
    created_at                   TEXT NOT NULL,
    UNIQUE(channel, external_id)
);

CREATE INDEX IF NOT EXISTS idx_user_channel_identities_account ON user_channel_identities(user_account_id);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (11, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Application layer: user_accounts, user_channel_identities (Phase 3.1)');
