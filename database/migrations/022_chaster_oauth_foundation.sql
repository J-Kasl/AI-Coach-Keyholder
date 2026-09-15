-- =============================================================================
-- Migration 022 — CHASTER-01A: OAuth Connection Foundation
-- =============================================================================
-- docs/architecture/chaster_integration_technical_design.md (draft,
-- not approved for implementation as a whole). This migration
-- implements ONLY the two tables CHASTER-01A itself needs -- see
-- chaster/README.md for the exact boundary. `chaster_provider_
-- observations` (CHASTER-01B: reading and storing actual Chaster
-- lock status) is explicitly NOT part of this migration -- it does
-- not yet exist anywhere in this schema.
--
-- Both tables reference user_accounts(id) -- the same canonical,
-- channel-agnostic identity every other domain table already uses
-- (migration 011) -- never a raw Discord snowflake directly.
--
-- Both tables are genuinely one-row-per-user, never accumulated, so
-- both use user_preferences' own PK idiom (migration 013:
-- `user_id TEXT PRIMARY KEY REFERENCES user_accounts(id)`), not
-- task_assignments' separate-id-plus-partial-index idiom (migration
-- 020) -- that idiom exists specifically for tables that legitimately
-- keep multiple historical rows per user, which neither table here
-- does: a chaster_oauth_states row is replaced or deleted, a
-- chaster_connections row is replaced (on reconnect) or deleted (on
-- disconnect), never kept alongside an older row for the same user.
--
-- chaster_oauth_states: a pending, single-use OAuth `state` value.
-- UNIQUE(user_id) is what enforces "at most one pending state per
-- user" at the database level, not merely in application logic --
-- the same "database guarantee, not a repository-level check that
-- could race" discipline migration 020's own comment already
-- describes for task_assignments' partial unique index. No
-- encryption -- see chaster/README.md: a high-entropy random value
-- with no other sensitive payload attached needs unguessability, not
-- database confidentiality.
--
-- chaster_connections: a durable, encrypted-at-rest OAuth connection.
-- encrypted_access_token/encrypted_refresh_token are TEXT (a Fernet
-- token is already URL-safe base64 ASCII, never BLOB), NOT NULL (a
-- row is only ever created atomically with both real tokens -- there
-- is no valid intermediate state where a row exists without them).
-- No separate nonce/IV or authentication-tag column -- both are
-- embedded inside the Fernet token itself. encryption_key_version is
-- nullable and unused in CHASTER-01A, reserved for a future rotation
-- slice so that slice needs no migration of its own just to add this
-- column. chaster_account_id is UNIQUE: the intended invariant is
-- one Chaster account <-> one internal application user (no known
-- repository/API evidence found during this implementation
-- suggesting the same Chaster account would ever legitimately need
-- multiple internal-user connections).
-- =============================================================================

CREATE TABLE IF NOT EXISTS chaster_oauth_states (
    state           TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL UNIQUE REFERENCES user_accounts(id),
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chaster_connections (
    user_id                     TEXT PRIMARY KEY REFERENCES user_accounts(id),
    chaster_account_id          TEXT NOT NULL UNIQUE,
    chaster_username            TEXT,
    encrypted_access_token      TEXT NOT NULL,
    encrypted_refresh_token     TEXT NOT NULL,
    encryption_key_version      TEXT,
    access_token_expires_at     TEXT NOT NULL,
    refresh_token_expires_at    TEXT NOT NULL,
    granted_scopes_json         TEXT NOT NULL,
    connection_status           TEXT NOT NULL,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chaster_connections_chaster_account
    ON chaster_connections(chaster_account_id);

-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (22, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'CHASTER-01A: chaster_oauth_states, chaster_connections (OAuth connection foundation -- no lock fetching, no provider observations)');
