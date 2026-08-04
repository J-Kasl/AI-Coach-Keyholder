# Task Catalog

Canonical design: `docs/architecture/task_catalog_technical_design.md`
(**still `Draft for review, not approved for implementation` as a
whole document** — this README describes exactly which specific slice
of that draft has been implemented here, and nothing more; it does not
change that document's own status).

## What is implemented here

- **`task_catalog/models.py`** — `TaskInstanceRole`,
  `TaskTemplateEligibilityStatus`, `TaskTemplateVersion` (append-only,
  `frozen=True`), `TaskTemplateCatalogEntry` (mutable current-state
  pointer). Directly mirrors `goal_management`'s own `Goal`/`GoalVersion`
  split — see the design document's own Section 2 for why.

  **TC-1 (append-only) is an application-enforced invariant today, not
  a database-enforced one** — verified directly, not assumed: a raw SQL
  `UPDATE` against `task_template_versions` succeeds unconditionally,
  and a raw `DELETE` succeeds too once the row is no longer the target
  of any `TaskTemplateCatalogEntry.current_version` (the composite
  foreign key only incidentally blocks deletion of a row still pointed
  at — protecting pointer integrity, not append-only content as such).
  No application code ever issues an `UPDATE`/`DELETE` against this
  table, and `frozen=True` prevents mutating an already-loaded Python
  object, but nothing in the schema itself would stop a `DELETE`/`UPDATE`
  issued outside `TaskCatalogAdministration`.

  **Minimal validation** (`TaskTemplateVersion.__post_init__`):
  `eligible_instance_roles`/`eligible_operating_modes` must each be
  non-empty and contain no duplicates — raises a plain `ValueError`
  (a `ValueError` subclass, `InvalidTaskTemplateVersionError`, is what
  `TaskCatalogAdministration`'s own methods raise for other write
  failures — catching `ValueError` broadly catches both). Deliberately
  does **not** validate against unknown/corrupted enum values read back
  from the database — that already surfaces its own clear error at
  read time (`_row_to_version`), a separate, already-covered case.

- **`task_catalog/repository.py`** — two structurally separate public
  classes:
  - **`TaskCatalog`** — read-only (`get_template`,
    `get_active_templates`). No write method exists on this class at
    all — verified directly by
    `tests/task_catalog/test_repository.py::TestTaskCatalogHasNoWriteCapability`,
    not only documented.
  - **`TaskCatalogAdministration`** — `critical_change`-governed write
    API. **Two distinctly-named consent parameters**, matching
    `trust_manager`'s own established split:
    - `created_via_consent_id` (`create_template`, `add_version`) —
      creating a new immutable `TaskTemplateVersion`.
    - `via_consent_id` (`set_current_version`, `activate`,
      `deactivate`) — changing mutable state on an existing
      `TaskTemplateCatalogEntry`, exactly `trust_manager.deactivate_domain()`/
      `reactivate_domain()`'s own parameter name for the same class of
      operation.

    None of these methods are wired into, or intended to be called by,
    any ordinary runtime consumer.
- **`database/migrations/014_task_catalog.sql`** —
  `task_template_versions` (append-only), `task_template_catalog_entries`
  (mutable pointer), with a composite `FOREIGN KEY (template_id,
  current_version)` as a second, database-level guarantee that
  `current_version` can never point at a version that doesn't exist.
- **`database/migrations/015_task_catalog_consent_audit.sql`** — adds
  `eligibility_changed_via_consent_id`/`current_version_changed_via_consent_id`
  to `task_template_catalog_entries`. **Fixes a real, confirmed gap
  found under direct review**: `set_current_version()`/`activate()`/
  `deactivate()` previously required and validated a consent id but
  never persisted it — after the call returned, the database could not
  answer "which consent authorized this." Both columns are **never
  cleared** — unlike `trust_manager`'s own `deactivated_via_consent_id`
  (cleared to `NULL` on reactivation, relying on a domain event to
  carry that consent instead). Task Catalog has no domain events, so a
  NULL-clearing column here would silently lose the activation consent
  with no fallback anywhere — a deliberate, explained departure from
  the literal `trust_manager` pattern, not an arbitrary one. Nullable,
  not `NOT NULL`: rows from before this migration genuinely have no
  known answer, and `NULL` represents that honestly rather than a
  fabricated default.

  **Known, accepted limit** (same one `trust_manager`'s own column-based
  approach already has): only the *most recent* authorization is
  visible, not a full history across repeated activate/deactivate
  cycles. A full history requires the still-open
  `TaskTemplateEligibilityChange` append-only log (design document
  Section 10, Open Question 1) — not implemented here, deliberately,
  per explicit scope decision.
- **`database/migrations/016_task_catalog_current_version_audit.sql`** —
  adds `current_version_changed_at`. **Fixes a second, confirmed gap
  found under direct review**: `set_current_version()` accepted a `now`
  parameter but never used it — after advancing `current_version`, the
  database could say *who* authorized the change but not *when*.
  Makes the audit fully symmetric:

  | | who | when |
  |---|---|---|
  | `eligibility_status` | `eligibility_changed_via_consent_id` | `status_changed_at` |
  | `current_version` | `current_version_changed_via_consent_id` | `current_version_changed_at` |

  `create_template()` populates all four audit fields (both "who" and
  both "when") from the same creation consent/timestamp — the initial
  `ACTIVE` eligibility and the initial `current_version=1` are both
  already-authorized outcomes of that creation, not a neutral default
  with no origin (**Interpretation A**, confirmed under review — a
  prior draft of this fix left `eligibility_changed_via_consent_id`
  `NULL` at creation time, on the theory that it should track only
  *later* explicit transitions; that interpretation was rejected as
  leaving the very gap this whole fix exists to close, for the single
  most common case of all — every newly created template).

  SQLite's `ALTER TABLE ADD COLUMN` only accepts a literal/constant
  `DEFAULT`, never an expression referencing another column, so
  backfilling existing rows from `status_changed_at` could not be part
  of the `ADD COLUMN` statement itself — the migration adds the column
  nullable, then a separate `UPDATE` backfills it. **For rows that
  existed before this migration, the backfilled value is the best
  available historical approximation, not a historically accurate
  record of when the `current_version` pointer itself last changed**
  — the prior schema kept no separate timestamp for that at all.
- **58 tests** (`tests/task_catalog/`) covering both files, including
  direct verification of TC-1 (append-only, verified by re-fetching a
  version after a later `add_version()` call and asserting byte-for-byte
  equality — not merely asserted in a docstring), TC-2 (deactivation
  never touches a `TaskTemplateVersion` row), TC-4 (`TaskCatalog`'s
  structural absence of any write method), TC-7 (a deactivated
  template's own version remains readable via `get_template()`), a real
  multi-threaded concurrency test for `add_version()`, a failure-injection
  test proving `create_template()`'s atomicity, a direct test of
  `PRAGMA foreign_keys` on the actual connection shape this module's
  own repositories use, and the consent-audit/validation additions
  described above.

This is a genuinely new structural pattern for this project — every
other domain module today (`TrustManager`, `PenaltyEngine`, `GoalManager`,
`RecoveryPlanManager`) is a single class mixing read and write. Task
Catalog is the first to split them into two classes, specifically
because its own design document (TC-4) requires ordinary consumers to
have no write capability at all, not merely a documented convention
not to use one.

## What is explicitly NOT implemented — still draft, still open

Per the governing implementation decision, this slice implements
**only the catalog reference layer itself**. None of the following
exist in this codebase, and this module makes no claim about who will
eventually own them:

- **No `TaskInstance` of any kind.** `PRIMARY`/`RECOVERY`/`JOURNALING`/
  `INTEGRITY`/`OPTIONAL_CHALLENGE` exist here only as
  `TaskInstanceRole` enum values a template may declare itself
  eligible for — their presence in this enum is not evidence that any
  runtime owner exists for creating instances of that role. Today,
  **no module in this codebase creates task instances of any role at
  all** — `recovery_plan`'s own `RecoveryTask` predates, and is
  entirely unaffected by, this module.
- **No `TaskInstanceEnvelope`, no `binding_conditions_snapshot`, no
  `SourceReference` table.** These belong to whichever domain
  eventually owns a given `instance_role`'s runtime instances — not to
  Task Catalog, and not implemented anywhere yet.
- **No `TaskTemplateEligibilityChange` append-only audit log** — the
  design document's own Section 10, Open Question 1, remains open. The
  consent/timestamp audit columns above (migrations 015, 016) are a
  smaller, narrower fix for two confirmed gaps, not an implementation
  of this broader, still-undecided log — they still only capture the
  *most recent* authorization/timestamp, not a full history across
  repeated transitions.
- **No domain events.** Deliberately not added for symmetry with other
  modules — the design document doesn't define any, and no consumer
  exists yet to react to one. This is also *why* the consent-audit
  columns above don't follow `trust_manager`'s exact pattern — see
  their own description above.
- **No Task Runtime, no Advanced Mode, no Equipment Inventory, no
  Delegated Authority, no autonomous template selection, no connection
  to `recovery_plan`.** All remain exactly as open as
  `task_catalog_technical_design.md` and
  `advanced_mode_technical_design.md` themselves describe — this
  implementation slice does not resolve, or imply an answer to, any of
  their own listed open questions.

## Design decisions made during implementation (worth recording, not in the design doc itself)

- `add_version()`'s next version number is computed internally
  (`MAX(version) + 1` under a transaction), never accepted as a
  caller-supplied parameter — removes any way for a caller to create a
  version gap or collision; `UNIQUE(template_id, version)` is a
  second, database-level guarantee behind this. Verified safe under
  real concurrency, not only reasoned about — two genuinely concurrent
  `add_version()` calls (separate threads, separate DB connections)
  always produce two distinct sequential versions, never a collision,
  because `Database.transaction()`'s own `BEGIN IMMEDIATE` fully
  serializes them at the SQLite engine level.
- `activate()`/`deactivate()` on an entry already in that exact state
  raise `TaskTemplateEligibilityTransitionError` rather than silently
  no-op-ing — the same "guard against a transition to the same state"
  discipline `recovery_plan`'s own Phase 2.8 review already established.
- `get_active_templates()`'s role/operating-mode filtering happens in
  Python, not SQL, since both are JSON-encoded columns — a reasonable
  trade-off given this catalog is expected to stay small (a reference
  table of task templates, not user data).
- `task_catalog/__init__.py` is empty — matching this project's own
  existing convention (no module here uses `__all__`-based export
  restriction; only `trust_manager/__init__.py` has any content at
  all, and that is a module docstring, not an export list). Nothing at
  the package-import level stops a wrongly-written consumer from
  importing `TaskCatalogAdministration` directly — the same limitation
  every other module in this codebase already accepts as Python's own,
  not a new gap specific to this one.

