# system — Composition Layer

The runtime/bootstrap layer `system_state_machine.md` Section 7 and
Finding 4 describe: owns startup sequencing and cross-module event
wiring, no domain state of its own, no business decisions
(`philosophy.md` 2.11 applied to orchestration itself).

## What this delivers (Fáze 2.4-2.6)

- **`infrastructure/consumer_registry.py`** — the Consumer Framework:
  `ConsumerRegistry` (event_type → handler(s) mapping) and
  `process_pending_events()` (claim → dispatch → mark published, tying
  together `infrastructure/outbox.py`'s existing primitives with the one
  piece that was missing).
- **`infrastructure/startup_lease.py`** — `acquire_system_startup_lease()`/
  `release_system_startup_lease()`, the restart-safe DB lease
  (LEASE-1) guaranteeing at most one process instance performs startup
  reconciliation at a time.
- **`system/startup.py`** — `on_system_startup()`, calling, in order:
  1. `TrustManager.recover_trust_manager_state()`
  2. `PenaltyEngine.recover_penalty_window_state()`
  3. `RecoveryPlanManager.recover_recovery_plan_state()` (Fáze 2.6 —
     a consistency check, depends only on step 2)
  4. `process_pending_events()` (the outbox publisher, last, so events
     from steps 1-3 are delivered immediately)

  Steps 3-5 of `system_state_machine.md`'s full sequence (Activity
  Authorization, Hygiene Privilege, Goal Management) are not called —
  those modules do not exist yet. No placeholder calls were added for
  them.
- **The first real, working cross-module event subscription** —
  `build_consumer_registry()` wires Penalty Engine to react to Trust
  Manager's `incident.confirmation_changed` (filtered to
  `new_confirmation=CONFIRMED`), replacing what was previously only
  directly-callable via `PenaltyEngine.start_window_if_eligible()`.
- **A second, independent consumer wiring (Fáze 2.6)** — Recovery Plan
  reacts to all five of Penalty Engine's own `penalty_window.*`
  lifecycle events, the same consumer-handler discipline applied a
  second time by a different pair of modules. See
  `recovery_plan/README.md` for a real cascading-events finding this
  integration surfaced and how it was fixed
  (`infrastructure/consumer_registry.py`'s `process_pending_events()`
  now drains a full event cascade within one call, not just the first
  event).

## A real architectural finding, not merely a plan followed

Wiring the first genuine cross-module consumer surfaced something the
design documents had not fully worked through: **`TrustManager` and
`PenaltyEngine`, once they share the same underlying
`infrastructure.database.Database` core, also share that core's
single-open-transaction guard.** A consumer handler already running
inside `consume_event()`'s open transaction cannot call
`TrustManager.get_confirmed_incidents_since()` or any other method that
opens its own transaction — doing so raises `NestedTransactionError`,
correctly, since two independent connections against the same file
mid-transaction is exactly the hazard that guard exists to prevent
(`infrastructure/database.py`).

**Resolution:** a consumer handler must read everything it needs
directly from the triggering event's own payload, never by calling back
into the publishing module's live API mid-transaction. Concretely:

- `incident.confirmation_changed`'s payload gained a `trust_domain`
  field (`trust_manager/repository.py`) — previously only
  `incident_id`/`previous_confirmation`/`new_confirmation` — so a
  consumer never needs to ask Trust Manager anything further about the
  Incident it already has an id for. (Fáze 2.5 later extended this
  further with `rule_group_id`/`intrinsic_severity`/`cooperation_*` for
  Extension's own needs — see `penalty_engine/README.md`.)
- `PenaltyEngine` gained
  `_consume_confirmed_incident_in_transaction(tx, incident_id, trust_domain, rule_group_id, intrinsic_severity, cooperation, now)`
  — a narrower sibling to `start_window_if_eligible()`/`consume_confirmed_incident()`
  that operates entirely against a given, already-open `Transaction` and
  never calls another module's public API. This is what
  `build_consumer_registry()`'s handler actually calls. (Named
  `_start_window_from_confirmed_incident_in_transaction` when first
  introduced in this phase; renamed and extended in Fáze 2.5 once
  Extension unified window-starting and window-extending into one
  consumption path.)

**Why this is the right fix, not a workaround:** it is a direct
application of `implementation_conventions.md`'s own Interpretation
Handoff Pattern (Section 3) — "Domain B reads the judgment through
Domain A's narrow API, never the raw facts" already implied that the
judgment itself should be self-contained. What this integration made
concrete is that "self-contained" must mean *transactionally*
self-contained too, not only semantically — a consumer cannot assume it
can freely re-query the publisher mid-reaction. Verified directly by
`tests/system/test_startup.py`, which confirms a `PenaltyWindow` is
created purely through `on_system_startup()`'s real event wiring — not
by calling `start_window_if_eligible()` directly — with no
`NestedTransactionError` anywhere in that path.

**Consequence for every future consumer handler in this system:** the
same discipline applies — an event's payload must carry everything its
known/anticipated consumers need, and a handler must never call another
module's transaction-opening public method from inside its own. This
precedent was already exercised twice more within this same project,
not merely anticipated: Fáze 2.5 (Extension) extended
`incident.confirmation_changed`'s payload further (`rule_group_id`,
`intrinsic_severity`, `cooperation_*`) for `should_extend()`'s own
needs, and Fáze 2.6 (Recovery Plan) wired a *second, independent*
consumer relationship (Penalty Engine → Recovery Plan) needing no
payload extension at all, since the triggering `penalty_window.*`
events were already shaped with enough information — see
`recovery_plan/README.md`'s own design notes for why that is itself a
small piece of evidence the discipline converges. The same will apply
to every future Activity Authorization/Hygiene Privilege/Goal
Management consumer.

**A second, related finding surfaced only once a second consumer
existed** (Fáze 2.6): a single `process_pending_events()` call
originally processed only the batch of events that existed at the
moment it was invoked — a cascade (one handler's side effect publishing
a new event a *different* handler needs to react to) would not
propagate within the same call. Fixed by looping until a claim round
returns nothing new; see `recovery_plan/README.md` for the full account
and the test that verifies it.

## What is deferred

- **Steps 3-5 of the full startup sequence** (Activity Authorization,
  Hygiene Privilege, Goal Management) — added when their respective
  modules exist.
- ~~**A registry entry for `should_extend()`/Extension**~~ — **delivered
  in Fáze 2.5**: `should_extend()` is wired into the SAME
  `"incident.confirmation_changed"` → `"penalty_engine"` registration
  as window-starting — one unified consumption handler, not a second
  registry entry, since Extension is Penalty Engine's own logic, not a
  separate module. See `penalty_engine/README.md` for how the payload
  was further extended (`rule_group_id`, `intrinsic_severity`,
  `cooperation_*`) to keep this handler transactionally self-contained.
- **Consumer/publisher running as a genuinely separate, continuously
  polling process** — `process_pending_events()` is called once, at
  startup, in this slice. A scheduled/background publisher loop
  (running periodically during normal operation, not only at startup)
  is a natural next addition once the Discord bot's own event loop
  exists to host it.
