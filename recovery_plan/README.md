# recovery_plan — Slice 1

The third real domain module built against the architecture baseline
(`docs/architecture/recovery_plan_technical_design.md`). Consumes
Penalty Engine's own `penalty_window.*` events (never Penalty Engine's
public API mid-transaction) — the second independent instance of the
same consumer-handler discipline `system/README.md` documents for Trust
Manager → Penalty Engine.

## What this slice covers

All of `recovery_plan_technical_design.md`'s core sections (1-9).

- **`RecoveryPlan`/`RecoveryTask`/`RecoveryTaskCompletion`**
  (`recovery_plan/models.py`) — mutable-with-status for the first two,
  append-only for the third.
- **Lifecycle as pure reaction** (`recovery_plan/repository.py`,
  wired in `system/startup.py`) — `penalty_window.started` creates a
  plan, `.frozen`/`.resumed`/`.completed` mirror status,
  `.target_duration_changed` regenerates (expiring stale
  `PROPOSED`/`ACCEPTED` tasks, preserving `COMPLETED` ones and their
  completions untouched — RP-4). No code path in this module can cause
  a Penalty Window state change (RP-6).
- **Coach-facing task management** — `propose_task()`, `accept_task()`,
  `complete_task()` (publishes `recovery_plan.task_completed` — **the
  event the Penalty Engine will consume**, Recovery Credit integration,
  deferred), `withdraw_task()`.
- **The narrow public read API** (2.3) — `get_recovery_task_completion()`,
  plus `get_recovery_task()` (a companion read function for the same
  future consumer, needed to read `credit_hours`).
- **Crash recovery as a consistency check, not a reconciliation** (8) —
  `recover_recovery_plan_state()` returns the list of ACTIVE/FROZEN
  Penalty Windows missing a matching-status plan; it does not silently
  create the missing plan (the standard at-least-once redelivery is
  what should do that).
- **A real, tested end-to-end chain** — `tests/system/test_startup.py`
  confirms a `RecoveryPlan` is created, frozen, and completed purely
  through `on_system_startup()`'s real event wiring (a confirmed
  Incident → Penalty Window → Recovery Plan), never by calling
  `RecoveryPlanManager` methods directly.

## What is deferred

- **Recovery Credit integration** (Section 6) — the Penalty Engine side
  (`record_recovery_credit_from_task_completion()`,
  `recovery_credit_ledger`, `recovery_credit_decisions`) does not exist
  yet. `recovery_plan.task_completed` is already published correctly;
  nothing yet consumes it.
- **Who authors `RecoveryTask` content, task-acceptance weight, `credit_hours`
  UX guidance** (10, the document's own open questions) — not resolved
  here; this slice only builds the mechanism, not the Coach-side
  authoring logic.

## A cascading-events finding, discovered while wiring this module

Wiring Recovery Plan as a **second** downstream consumer (after Penalty
Engine) surfaced a real gap in `infrastructure/consumer_registry.py`'s
`process_pending_events()`: it claimed and dispatched exactly one batch
per call. A handler that itself publishes a new event as a side effect
(e.g. Penalty Engine emitting `penalty_window.started` while reacting to
`incident.confirmation_changed`) produces a fresh `domain_events` row
that was never part of the batch already claimed — so a single
`on_system_startup()` call would create the window but leave Recovery
Plan's reaction to it unprocessed until a *second* call. With no
continuously-running publisher loop yet (still deferred, per
`system/README.md`), that could mean waiting for the next full process
restart.

**Fixed**: `process_pending_events()` now loops, claiming and
dispatching a fresh batch each round, until a round claims nothing new
— draining a full cascade (Trust Manager → Penalty Engine → Recovery
Plan, three levels today) within one call, bounded by
`max_cascade_rounds` (default 10) as a safety limit against a
hypothetical infinite mutual-triggering bug. Verified directly:
`tests/system/test_startup.py::TestRecoveryPlanEndToEnd::test_recovery_plan_created_purely_through_the_full_event_chain`
confirms a `RecoveryPlan` exists, at the correct post-Extension
capacity, after exactly one `on_system_startup()` call — not two.

## Design notes

- **`RecoveryTaskCompletion`'s exact fields are this slice's own
  design** — the architecture document establishes that it exists and
  is read via `get_recovery_task_completion()`, but (unlike
  `RecoveryPlan`/`RecoveryTask`) never gives it an explicit `@dataclass`
  block. `recovery_plan_id` is denormalized onto it for convenient
  querying — always derivable from `recovery_task_id`, not a second
  source of truth.
- **`penalty_window.target_duration_changed`'s payload already carried
  everything needed** (`new_target_active_hours`) — no payload
  extension was required for this integration, unlike Trust Manager's
  event (which needed `trust_domain`, then `rule_group_id`/
  `intrinsic_severity`/`cooperation_*` added across two prior
  integrations). This is itself a small piece of evidence that the
  "extend the payload with whatever a real consumer needs" discipline
  (`implementation_conventions.md` Section 3) converges — Penalty
  Engine's own events were already shaped with enough information for
  a plausible downstream reaction, without anyone having to guess in
  advance which fields a future consumer would want.
- **`recover_recovery_plan_state()` returns a list, not a bool/count
  alone** — the actual `penalty_window_id`s found without a matching
  plan, so a future observability layer (or a human reading logs) can
  act on which windows are affected, not just that some are.
