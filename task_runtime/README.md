# Task Runtime

Canonical design: `docs/architecture/task_runtime_technical_design.md`
(**`Draft for review, not approved for implementation`** — this README
describes exactly which specific slice of that draft has been
implemented here).

**Task Catalog = what CAN exist (definitions). Task Runtime = what
was assigned to a specific user and its state.** Two separate
modules, one-way dependency: `task_runtime -> task_catalog`,
`task_runtime -> lock_state`, never the reverse (verified by AST scan,
`tests/task_runtime/test_system_independence.py`).

## What is implemented here — Slice B

**`models.py`**: `TaskAssignmentStatus` (`ACTIVE`/`COMPLETED`/`CANCELLED`
— exactly two transitions, `ACTIVE -> COMPLETED` and
`ACTIVE -> CANCELLED`, nothing else), `TaskAssignment` (immutable, one
row for the assignment's entire lifetime), `EligibilityReasonCode`
(`ELIGIBLE`/`LOCK_STATE_REQUIRED` — a closed set of safe codes, no raw
sensitive content), `TaskEligibilityDecision`.

**`eligibility.py`**: `evaluate_task_eligibility()` — pure, deterministic,
no DB access, no LLM. Fail-closed: `LockKnowledgeState.UNKNOWN` and
`UNLOCKED_USER_REPORTED` both fail a `REQUIRES_LOCKED` template; only
`LOCKED_USER_REPORTED` passes. This is a **preview/filtering**
function — it never itself authorizes a write.

**`repository.py`**:
- `TaskRuntime` — read-only. `get_active_assignment(user_id)`,
  `get_eligible_templates(*, role, operating_mode, lock_knowledge_state)`
  (delegates candidate retrieval entirely to `TaskCatalog`'s own
  `get_active_templates()`, filters by `evaluate_task_eligibility()` —
  no task_catalog query logic duplicated here).
- `TaskRuntimeAdministration` — governed write.
  `assign_task()`/`complete_task()`/`cancel_task()`.

## The authoritative eligibility invariant

**`assign_task()` re-derives and enforces eligibility itself** — it
loads the template's current version via `TaskCatalog.get_current_version()`
and calls `evaluate_task_eligibility()` internally. There is no
parameter on `assign_task()` for a caller to supply its own
pre-computed decision — a caller cannot bypass the check by calling
`evaluate_task_eligibility()` separately and passing the result in.
Preview eligibility (`TaskRuntime.get_eligible_templates()`) and
authoritative assignment eligibility enforcement are two different
code paths; only the latter can ever cause a write, and it always
repeats the check itself.

## Referential integrity — a real, database-level guarantee

`task_assignments.template_id`/`template_version` are constrained by a
**composite `FOREIGN KEY (template_id, template_version) REFERENCES
task_template_versions(template_id, version)`** (migration 020),
against `task_template_versions`' own existing `UNIQUE(template_id,
version)` constraint (migration 014). A valid `template_id` with an
invalid/nonexistent version fails this exactly the same way a wholly
nonexistent `template_id` does — verified directly, not merely assumed
from a repository-level lookup that could race.

Because `task_template_versions` is append-only (TC-1), an assignment
keeps referring to the **exact version** it was created against even
after Task Catalog's own `current_version` pointer later advances —
verified directly (`add_version()` + `set_current_version()` after an
assignment exists does not change that assignment's own
`template_version`).

## Cardinality and concurrency — database-level, not application-level

**At most one `ACTIVE` assignment per `user_id`**, enforced by a
partial unique index (`idx_one_active_assignment_per_user`) — the same
idiom `advanced_mode`'s own migration 017 established
(`idx_one_active_mode_transition_request`), applied here per-user
instead of as a global singleton. Under a genuine race (verified with
10 concurrent threads), exactly one `assign_task()` call succeeds; the
rest raise the stable, typed `TaskAssignmentConcurrencyError` — never
a raw `sqlite3.IntegrityError` a caller would have to interpret
itself.

## Lifecycle and governance

`ACTIVE -> COMPLETED` and `ACTIVE -> CANCELLED` only — no
`ASSIGNED`/`FAILED`/`EXPIRED`/`SKIPPED`/`REFUSED` in this slice. An
invalid transition (e.g. completing an already-resolved assignment)
raises `TaskAssignmentTransitionError`. All three write operations
(`assign`/`complete`/`cancel`) are governed writes requiring a
non-empty consent ID, going through the same shared
`infrastructure.database.apply_transition()` every other governed
write in this project already uses. **Conversation Engine/LLM has no
reference to `TaskRuntimeAdministration` at all** — structurally
impossible for a model's own text to mutate assignment state.

## What is explicitly NOT implemented — still draft, still open

- **No Discord commands, no `ApplicationService` integration.**
- **No `ConversationContextProvider`, no Conversation Engine wiring.**
- **No selection algorithm.** `assign_task()` takes an explicit
  `template_id` — random/weighted/personality-driven selection among
  eligible templates is a future slice's own work.
- **No preference/limits eligibility dimension.** Only
  `LockRequirement` exists in this slice.
- **No Chaster or any other external provider integration.**
- **No Scarlett or any personality change.**
