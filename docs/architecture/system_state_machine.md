# System State Machine — Integration Design

> Draft for review, **not implemented**. This document does not
> introduce new domain behavior — it audits and coordinates the state
> machines already specified across `penalty_window_technical_design.md`,
> `trust_manager_technical_design.md`,
> `activity_authorization_technical_design.md`,
> `hygiene_privilege_technical_design.md`, `goal_technical_design.md`,
> and `extension_technical_design.md`. Its purpose is to catch the class
> of bug that no single document can catch on its own: two modules
> quietly representing the same state differently, an unowned
> transition, an invalid combination of otherwise-valid domain states,
> or a startup reconciliation order that briefly produces a state no
> individual document permits.
>
> Where this audit finds a genuine gap, it is recorded as a **Finding**
> (Section 12) rather than silently patched — each Finding proposes a
> fix, but applying it means editing the specific document(s)
> responsible, not this one.
>
> Status: **Architecture baseline — approved for implementation.**
> Reached this status once all five integration Findings were resolved
> and applied across their respective documents — this document is now
> the baseline record of how every module fits together, not a proposal
> still awaiting changes.

---

## 1. Why a System-Level State Model Is Needed

Every module built so far has its own state machine, invariants, events,
and recovery logic, reviewed individually and in depth. That was the
right first step — but it is not sufficient. The risk that remains
after every module is individually correct is specifically a
**between-module** risk:

1. Does every cross-domain transition have exactly one owner?
2. Is any state represented twice, in two different shapes, in two
   different modules?
3. After a restart, is there one unambiguous order in which modules
   reconcile, or could two valid orders produce different outcomes?
4. Which combinations of independently-valid domain states are actually
   meaningful together, and which should never occur?
5. Do any events form a write-back cycle (`philosophy.md` 2.11,
   Domain Interpretation, applied at the level of module wiring rather
   than a single decision)?
6. Is there a clear line between genuine system-wide state and a mere
   projection of one domain's own state?

This document does not re-derive any domain's internal rules — it
treats each existing document as authoritative for its own domain and
checks only the seams between them.

---

## 2. State Ownership and Non-Ownership

| State / Entity | Owning Module | Who May Write | Who May Read |
|---|---|---|---|
| `penalty_windows` (status, durations) | Penalty Engine | Penalty Engine only (I1) | Activity Authorization, Hygiene Privilege (existence/status only, an established precedent) |
| `freeze_periods` | Penalty Engine | Penalty Engine only | No other module directly — only via `get_authorization_freeze_state()` (2.5) and `get_penalty_window_relevant_domains()` (2.6) |
| `incidents` (existence, `consumed_by_penalty_window_id`) | Penalty Engine | Penalty Engine only | See **Finding 1** (Section 12) — the confirmation/assessment fields conceptually needed here are owned elsewhere |
| `Incident.confirmation`, `Incident.assessment` | Trust Manager | Trust Manager only | Extension (Penalty Engine) reads `assessment` fields to build `ExtensionContext` — see **Finding 1** |
| `TrustDomainState`, `TrustEvidence`, `TrustRecalculation` | Trust Manager | Trust Manager only | Hygiene Privilege (`TrustDomainState` for `hygiene`, read-only); Extension (via `ExtensionContext`, never directly) |
| `ActivityAuthorizationDecision`, `ActivityAuthorizationSession` | Activity Authorization | Activity Authorization only | — |
| `Goal`, `GoalVersion`, `GoalEvidence`, `GoalEvaluation`, `GoalAccountabilityAssessment`, `GoalNegotiation` | Goal Management | Goal Management only | Trust Manager reads a completed `GoalAccountabilityAssessment` (read-only) |
| `DiscretionaryHygieneBreakGrant`/`Session`, `HygienePenaltyOverrideDetermination`, `EffectiveHygienePolicyResult` | Hygiene Privilege | Hygiene Privilege only | — |
| `ExtensionDecision` | Penalty Engine (Extension is internal to it) | Penalty Engine only | — |
| `RecoveryPlan`, `RecoveryTask`, `RecoveryTaskCompletion` | Recovery Plan | Recovery Plan only | Penalty Engine reads a completed `RecoveryTaskCompletion` (read-only, `recovery_plan_technical_design.md` 2.3) |
| `domain_events` (transactional outbox) | shared infrastructure, not any one domain | any module, for its own events | any module, for consuming others' events |
| `system_startup_lease` | shared infrastructure (runtime/bootstrap layer) | the startup orchestrator (`on_system_startup()`, Section 7) | — |

The general rule this table enforces (`philosophy.md` 2.11, Domain
Interpretation): every row has exactly one writer. Where a second
module needs the information, it reads through a named, narrow
function (`get_authorization_freeze_state()`,
`get_penalty_window_relevant_domains()`, `get_trust_domain_state()`,
the `GoalAccountabilityAssessment` read in the Trust Manager) — never a
second writer, and never a raw table.

---

## 3. Orthogonal Domain State Machines

Per your framing: this is deliberately **not** one global enum. It is a
fixed set of independently-evolving machines, each already specified in
its own document, restated here only as a map.

### 3.1 Penalty Window Lifecycle (owner: Penalty Engine)

```
(none) -> ACTIVE -> FROZEN -> ACTIVE -> ... -> COMPLETED
```
`penalty_window_technical_design.md` 2.1/2.2. `terminate()` (an
additional `FROZEN/ACTIVE -> COMPLETED` path) is deferred — backlog
item, Section 11.

### 3.2 Freeze Reason Set (owner: Penalty Engine, orthogonal to 3.1)

Not an enum — a **set** of independently timed reasons
(`temporary_wear_exemption`, `emergency_override`,
`partnered_intimacy_authorization`), each with its own
`started_at`/`ended_at`/`expires_at`/`end_reason`. The Penalty Window is
`FROZEN` exactly when this set is non-empty (I22/PW-FREEZE-SET). Its
cardinality constraint (at most one open
`partnered_intimacy_authorization` reason) is I21.

### 3.3 Incident Confirmation Lifecycle (owner: Trust Manager)

```
UNCONFIRMED -> PROVISIONAL -> CONFIRMED
```
`trust_manager_technical_design.md` 2.8/5.1. Only `CONFIRMED` incidents
are visible to Penalty Engine's consumption flow (I12) or to Extension
(EXT-4's structural precondition). See **Finding 1** for the schema
concern this raises.

### 3.4 Activity Authorization Decision Lifecycle (owner: Activity Authorization)

```
(preliminary) -> DENIED
              -> PENDING_CONFIRMATION -> DECLINED / EXPIRED
                                      -> PENDING_COMMIT -> CLOSED
                                                         -> PENDING_FREEZE -> ACTIVE -> PENDING_RESUME -> CLOSED
                                                                           -> FAILED
```
`activity_authorization_technical_design.md` 8.1. `PENDING_FREEZE ->
ACTIVE` and `PENDING_RESUME -> CLOSED` each depend on a transition in
3.2 (a `partnered_intimacy_authorization` freeze reason opening/closing)
— the only place two of these orthogonal machines are directly coupled,
and the coupling is one Activity Authorization decision to one Freeze
Reason Set entry, never many-to-many.

### 3.5 Discretionary Hygiene Break Grant Lifecycle (owner: Hygiene Privilege)

```
(request) -> DENIED
          -> GRANTED -> STARTED -> ENDED
                     -> EXPIRED_UNUSED
                     -> FAILED_AT_START
```
`hygiene_privilege_technical_design.md` 4.1. Independent of 3.1/3.2 in
mechanism — it only *reads* Penalty Window existence/status and
relevant domains to select a policy; it never participates in Penalty
Window's own transitions.

### 3.6 Effective Hygiene Policy (owner: Hygiene Privilege, derived — not a lifecycle)

Not a stored state — recomputed on demand from three independent
inputs: `HygieneTrustLevel` (from Trust Manager's `TrustDomainState`,
continuous), the Penalty Window Override context (from 3.1/3.2, via the
read paths in Section 2), and any `HygienePenaltyOverrideDetermination`
for the current window. Never cached across a recomputation
(`philosophy.md` 3.9).

### 3.7 Goal Lifecycle (owner: Goal Management)

```
(none) -> ACTIVE <-> PAUSED
ACTIVE/PAUSED -> COMPLETED / ABANDONED / REPLACED
```
Plus `archived_at`, independent of status. Fully independent of every
other machine in this document — a Goal Failure never touches Penalty
Window, Activity Authorization, or Hygiene state (GOAL-1).

### 3.8 Goal Change Proposal Lifecycle (owner: Goal Management)

```
PENDING -> ACCEPTED / DECLINED / EXPIRED
```
Gates 3.7's `ACTIVE -> COMPLETED/ABANDONED/REPLACED` transitions and
`GoalVersion` adaptation.

### 3.9 Goal Negotiation Lifecycle (owner: Goal Management)

```
OPEN -> RESOLVED / ESCALATED_TO_USER
```
Triggered only by a `GoalAccountabilityAssessment` with
`review_outcome=NEGOTIATE` — itself not a lifecycle, an append-only
judgment, at most one per `GoalEvaluation` (GOAL-13).

### 3.10 Trust Domain State (owner: Trust Manager, continuous — not discrete)

`TrustDomainState.score`/`confidence` change only via a
`TrustRecalculation` (TI2), but the value itself is continuous, not a
named state machine — it is read as context by Hygiene Privilege (3.6)
and, indirectly and narrowly, by Extension (`ExtensionContext`), never
written by either.

### 3.11 Recovery Plan Lifecycle (owner: Recovery Plan module — Finding 2 resolved)

```
penalty_window.started -> ACTIVE
penalty_window.frozen -> FROZEN
penalty_window.resumed -> ACTIVE
penalty_window.completed -> COMPLETED
```
`recovery_plan_technical_design.md` 3.1/4. A 1:1 projection of the
Penalty Window's own lifecycle — every transition is a reaction to a
Penalty Window event (RP-6), never independently decided. Regeneration
(`penalty_window.target_duration_changed` → `regenerate_recovery_plan()`)
does not change `RecoveryPlan.status`, only its task set and
`current_version` (3.4 there).

---

## 4. Valid Cross-Domain State Combinations

Because the machines in Section 3 are orthogonal, most combinations are
simply independent and unremarkable (e.g., Goal `PAUSED` while Activity
Authorization is `ACTIVE` — nothing links them). This section lists
only the combinations worth naming explicitly, either because they are
expected and meaningful, or because they must never be allowed to
persist.

**Expected, meaningful combinations:**

| Combination | Meaning |
|---|---|
| Penalty Window `FROZEN` (reason=`partnered_intimacy_authorization`) + Activity Authorization decision `ACTIVE` | The normal, designed state during an authorized unlock (`philosophy.md` 4.4) |
| Penalty Window `FROZEN` (reason=`temporary_wear_exemption`) + Hygiene Effective Policy = unrelated-override | An approved exemption in effect; hygiene policy still reflects the unrelated-window override because the window exists regardless of freeze status |
| Penalty Window `FROZEN` with **two** open reasons (e.g., exemption + emergency override) simultaneously | Legitimate per I22 — the window stays `FROZEN` until the last reason closes; Activity Authorization is uninvolved if neither reason is `partnered_intimacy_authorization` |
| Goal `ACTIVE` with an `OPEN` `GoalNegotiation` | Normal — the Goal itself is untouched while Coach and Keyholder negotiate its handling (a stable resting state) |

**Combinations that must never persist past a single transaction (transient-only):**

| Combination | Why It Must Not Persist |
|---|---|
| Penalty Window `COMPLETED` + Activity Authorization decision `PENDING_FREEZE`/`ACTIVE` referencing it | The window's completion (which requires no open freeze reasons) and an in-progress or open `partnered_intimacy_authorization` freeze are mutually exclusive — `PENDING_FREEZE`/`ACTIVE` implies an open freeze reason exists, which implies `FROZEN`, not `COMPLETED`. If ever observed, it indicates a reconciliation ordering bug (see Section 7) |
| Penalty Window `COMPLETED` + a `HygienePenaltyOverrideDetermination` still being read as current for that window | Harmless by construction (the determination is scoped to a `penalty_window_id`; a completed window's determinations simply stop being consulted, since `determine_penalty_window_override()` only looks at the *active/frozen* window) — listed here to confirm it is **not** a hazard, not because it is one |
| A `GoalNegotiation` referencing a `GoalEvaluation` whose Goal has since transitioned to `ABANDONED`/`REPLACED` via an unrelated path | **Resolved** (Finding 3, Section 12) — the Goal's own lifecycle transition now closes any `OPEN` negotiation on it as `MOOT` in the same transaction (`goal_technical_design.md` GOAL-14). Listed here to confirm it is no longer a hazard, not because it still is one. |

---

## 5. Event-Driven Transition Map

Only cross-domain events — intra-module events are already fully
specified in each module's own document.

| Event | Emitted By | Consumed By | Effect |
|---|---|---|---|
| `freeze_periods.opened` | Penalty Engine | Activity Authorization (filtered to `partnered_intimacy_authorization`) | The canonical, generic event for any new open `freeze_periods` row, any reason (`docs/architecture/domain_events_catalog.md` Finding 2) |
| `freeze_periods.closed` | Penalty Engine | Activity Authorization (filtered), consumers of `penalty_engine.freeze_expired` | The canonical, generic closure event, any reason |
| `penalty_engine.freeze_expired` | Penalty Engine | Activity Authorization | Emitted alongside `freeze_periods.closed` specifically for expiry — closes the corresponding session as `EXPIRED`, decision `-> CLOSED` |
| `activity_authorization.committed` | Activity Authorization | Penalty Engine | Creates the `freeze_periods` row (`partnered_intimacy_authorization`) |
| `activity_authorization.freeze_confirmed` | Activity Authorization | — | `PENDING_FREEZE -> ACTIVE`, triggered by consuming `freeze_periods.opened` — single publisher, resolved (Finding 2; previously disputed with Penalty Engine as emitter) |
| `activity_authorization.resume_requested` | Activity Authorization | Penalty Engine | Closes the corresponding `freeze_periods` row |
| `activity_authorization.resume_confirmed` | Activity Authorization | — | `PENDING_RESUME -> CLOSED`, triggered by consuming `freeze_periods.closed` — same resolution as `.freeze_confirmed` |
| `hygiene_privilege.*` events | Hygiene Privilege | *(no consumers yet — audit only; a future Coach check-in reaction is anticipated but not designed)* | — |
| `goal_accountability_assessment.recorded` | Goal Management | Trust Manager | Writes `GOAL_PROGRESS`/`GOAL_SETBACK` `TrustEvidence` when `relevant_to_trust=True` and `direction != NEUTRAL` — applied (`trust_manager_technical_design.md` Section 15, `goal_technical_design.md` Section 11) |
| `incident.confirmation_changed` (filtered to `new_confirmation=CONFIRMED`) | Trust Manager | Penalty Engine (if an active/frozen window exists) | Incident consumption + Extension decision — the one canonical confirmation event, filtered by payload rather than a separate `incident.confirmed` event type (Finding 1) |
| `penalty_window.started`/`frozen`/`resumed`/`completed`/`target_duration_changed` | Penalty Engine | Recovery Plan | Creates/mirrors/regenerates/terminates the corresponding `RecoveryPlan` (`recovery_plan_technical_design.md` Section 4) |
| `recovery_plan.task_completed` | Recovery Plan | Penalty Engine | Writes to `recovery_credit_ledger` (capped) plus an always-written `RecoveryCreditDecision` — applied (`penalty_window_technical_design.md` Section 3.4, `recovery_plan_technical_design.md` Section 6) |

See `docs/architecture/domain_events_catalog.md` for the full,
consolidated, cross-checked event registry (publisher, consumers,
aggregate owner, persistence, external relevance, and canonical
definition per event) — this table remains only as the cross-module
subset relevant to this document's own state machine discussion.

No cycle exists in this table: every row has a single emitter and every
consumer's reaction stays within its own module's writes (Section 2).
This is the concrete check for point 5 of your list — verified, not
merely asserted.

---

## 6. Priority and Hard-Boundary Rules

Restating `philosophy.md` 2.11 (Domain Interpretation) as concrete
wiring rules, now that every module exists to check it against:

1. **A module never derives a consequence from another module's raw
   state.** Every cross-domain read in Section 2 goes through a named
   function, never a raw table — with the one flagged exception
   (Finding 1).
2. **Mandatory Hygiene/Health Access outranks everything.** It is
   evaluated first, outside the privilege system entirely, before any
   other machine in this document is even consulted.
3. **A hygiene-specific Penalty Window override outranks the Hygiene
   Trust Level**, which outranks an unrelated-window override only in
   the sense that the unrelated override is checked first structurally
   — but a hygiene-specific override, when present, wins over both
   (the priority order from `philosophy.md` 3.9).
4. **The Keyholder's authority over a confirmed Rule violation is never
   negotiated** — Goal negotiation (3.9) exists in an entirely separate
   space from Penalty Window decisions (`philosophy.md` 2.9/2.10).
5. **No module blocks Emergency Override.** It has no dependency on any
   other module's availability — the only rule in this entire system
   with zero cross-module preconditions.

---

## 7. Startup Reconciliation Order — The Definitive Sequence

Each module's own document specifies its own recovery function and
notes it runs "inside the system startup lease," but the **complete,
ordered sequence across all modules together has never been written
down in one place** — it was scattered as partial references, primarily
inside `activity_authorization_technical_design.md` 16.2. That
document's `on_process_startup()` was the closest thing to an
orchestrator that existed at the time, but startup orchestration was
never really Activity Authorization's responsibility to begin with —
it is a responsibility of the **runtime/bootstrap layer**, which this
document formalizes as the authoritative home for it (Finding 4,
Section 12). `activity_authorization_technical_design.md` 16.2 has been
reduced accordingly to describe only its own
`recover_activity_authorization_state()`, cross-referencing here for
the full sequence — the same pattern already used between
`penalty_window_technical_design.md` and `extension_technical_design.md`
for `should_extend()`.

**This section is the authoritative definition of both the lease
mechanism and the full reconciliation order:**

```python
STARTUP_LEASE_DURATION = timedelta(minutes=5)   # parameter, with margin above the expected recovery duration

def on_system_startup(db: Database, process_id: str, clock: Clock) -> None:
    """
    THE definitive startup entry point, owned by the runtime/bootstrap
    layer -- not by any single domain module (Finding 4). Called BEFORE
    the Discord bot starts / before the first request is accepted.
    """
    now = clock.now()
    lease = acquire_system_startup_lease(db, process_id, now, STARTUP_LEASE_DURATION)
    if lease is None:
        # Another instance already holds the lease and is performing/has
        # performed recovery. This process does not participate in
        # startup as a reconciler — it either waits, or (if this is an
        # accidentally launched second instance) terminates.
        raise StartupLeaseNotAcquired("Another instance is already performing startup reconciliation.")

    try:
        # 1. Trust Manager -- recover_trust_manager_state() (Finding 5,
        #    now resolved: trust_manager_technical_design.md 14.3). Runs
        #    first because Extension's consumption flow (step 2) reads
        #    Incident.assessment via get_incident_assessment(), and must
        #    never see an incomplete result left over from a crash
        #    between confirmation and assessment.
        recover_trust_manager_state(db, now)

        # 2. Penalty Engine -- foundational; Activity Authorization and
        #    Hygiene Privilege both read its state.
        recover_penalty_window_state(db, now)

        # 3. Activity Authorization -- depends on (2).
        recover_activity_authorization_state(db, now)

        # 4. Hygiene Privilege -- depends on (2), not on (3).
        recover_hygiene_privilege_state(db, now)

        # 5. Goal Management -- independent of (2)/(3)/(4).
        recover_goal_management_state(db, now)

        # 6. Recovery Plan -- a consistency check dependent on (2)
        #    (confirms one RecoveryPlan per ACTIVE/FROZEN window),
        #    otherwise independent of (3)/(4)/(5).
        recover_recovery_plan_state(db, now)

        # 7. Outbox publisher -- LAST, so that events generated by
        #    steps 1-6 above are delivered immediately rather than
        #    waiting for the next cycle.
        publish_pending_outbox_events(db, process_id, now)
    finally:
        release_system_startup_lease(db, lease)


def acquire_system_startup_lease(db: Database, process_id: str, now: datetime, duration: timedelta) -> Lease | None:
    """
    An atomic UPDATE/INSERT over the single-row system_startup_lease
    table:
        UPDATE system_startup_lease
        SET held_by = :process_id, acquired_at = :now, expires_at = :now + :duration
        WHERE expires_at IS NULL OR expires_at < :now
        RETURNING *
    Returns None if another process holds the lease and it has not yet
    expired -- this enforces 'at most one instance performs startup
    reconciliation' at the DB level, not by convention.
    """
```

**Architectural note:** the startup orchestrator owns only sequencing
and lifecycle coordination. It owns no domain state and makes no
business decisions — every recovery step remains owned by its
respective domain module, called here only in the order Section 7
establishes. This is `philosophy.md` 2.11 (Domain Interpretation)
applied to orchestration itself: the orchestrator does not interpret
what any module's data means, and does not decide anything on a
module's behalf — it only guarantees that each module gets to interpret
its *own* facts in an order where the facts it depends on are already
settled.

**Why this exact order and not another:** steps 3 and 4 both have a
one-directional read dependency on step 2's outcome (a window that
*should* be `COMPLETED` must actually be `COMPLETED` before Activity
Authorization or Hygiene Privilege reason about it — otherwise the
transient-invalid combination flagged in Section 4 could briefly
appear, not as a real system state, but as what a stale read would
report). Step 2 in turn has a one-directional dependency on step 1
(Extension, part of the Penalty Engine's own consumption flow, reads
Incident assessments that step 1 guarantees are complete). Steps 4 and
5 have no dependency on step 3 or on each other, so their relative
order does not matter — they are listed 4-then-5 only for readability,
not because 5-then-4 would be wrong. Step 6 (Recovery Plan) depends
only on step 2 (it checks Penalty Window status directly), not on
steps 3, 4, or 5 — it could equally run any time after step 2, and is
placed last among the recovery steps only because it is the most
recently added.

---

## 8. Failure and Partial-Delivery Behavior

Already fully specified per-module; this section only confirms the
cross-module picture is consistent:

- **Outbox delivery is at-least-once system-wide**, and every consumer
  across every module deduplicates via `domain_event_consumers` —
  verified consistent across Activity Authorization, Hygiene Privilege,
  and Goal Management's event tables; none of them introduce a second
  deduplication mechanism.
- **The claim mechanism and the startup lease (Section 7) are
  independent safeguards**, confirmed non-overlapping: the lease
  protects the reconciliation *steps themselves* from running twice
  concurrently; the claim mechanism protects the *ongoing publisher*
  from delivering the same event twice. A system could (in principle)
  have a correct lease and a buggy claim mechanism, or vice versa,
  without either masking the other's failure.
- **`Clock` is shared, not per-module** — confirmed as the single time
  source every module's recovery function should use; no module
  defines its own clock abstraction.

---

## 9. Global Invariants

| # | Invariant |
|---|---|
| SSM-1 | Every stateful entity listed in Section 2 has exactly one writing module. No exceptions remain — the one formerly under dispute (Finding 1, `Incident`'s split ownership) was resolved by assigning `Incident` fully to the Trust Manager. |
| SSM-2 | Every cross-module read goes through a named function (Section 2's "Who May Read" column) — never a raw table, and never a second writer. |
| SSM-3 | The combinations listed as "must never persist" in Section 4 are transient-only: any observation of one outside a single in-progress transaction indicates a reconciliation-order bug, not a valid state. |
| SSM-4 | The startup order in Section 7 is the only correct order. A module's recovery function must never be called before a module it structurally depends on has completed its own. |
| SSM-5 | No event in Section 5 forms part of a cycle — every consumer's reaction is confined to writes owned by its own module (Section 2). |
| SSM-6 | Mandatory Hygiene/Health Access and Emergency Override (Section 6, points 2 and 5) are never gated behind any other module's availability or recovery state — they must remain reachable even if every other module's recovery step in Section 7 were somehow stalled. |

---

## 10. Scenario / Test Matrix

| # | Scenario | Given | When | Then |
|---|---|---|---|---|
| SST1 | Authorized unlock ends exactly as Penalty Window completes | Activity Authorization `ACTIVE` (freezing the window), Penalty Window would otherwise reach `COMPLETED` at the same moment | the countdown check and `end_session()` occur close together | the window cannot actually reach `COMPLETED` while the freeze is open (I22 keeps it `FROZEN`) — `ensure_current_state()` correctly reports `FROZEN`, not `COMPLETED`, until the freeze closes |
| SST2 | Startup recovery in the correct order prevents a transient-invalid read | A crash left Penalty Window `ACTIVE`-but-actually-`COMPLETED` (unreconciled) and Activity Authorization `PENDING_FREEZE` for an unrelated, still-legitimate freeze | `on_system_startup()` (Section 7) runs | Step 2 reconciles Penalty Window first; step 3's Activity Authorization recovery then sees the *already-correct* window state, never the stale one |
| SST3 | Reversing steps 2 and 3 would be observably wrong | Same setup as SST2 | Activity Authorization recovery hypothetically ran *before* Penalty Window recovery | it could read a stale, not-yet-`COMPLETED` window and make a decision against data that is about to change underneath it — this is exactly why Section 7's order is not arbitrary |
| SST4 | Hygiene policy correctly reflects a hygiene-specific override through an unrelated freeze/unfreeze cycle | Penalty Window hygiene-specific, `HygienePenaltyOverrideDetermination(EXCEPTIONAL)` recorded; window also gets a *second*, unrelated freeze reason (e.g., emergency override) that later closes | `evaluate_effective_hygiene_policy()` is called at each point | the exceptional override applies throughout, unaffected by the unrelated freeze reason opening/closing (3.2 and 3.6 are independent; only the hygiene-specific override's own presence matters) |
| SST5 | A Goal Failure never reaches Penalty Window even during an active window | Penalty Window `ACTIVE` (for an unrelated Rule violation), a Goal simultaneously accumulates `GoalEvidence(outcome=MISSED)` | a `GoalEvaluation`/`GoalAccountabilityAssessment` cycle runs to completion, `direction=SETBACK` | no `Incident`, no effect on `extensions_hours`, no interaction with the open Penalty Window whatsoever (GOAL-1, confirmed as a genuine cross-domain test) |
| SST6 | A negotiation becomes moot after unrelated Goal abandonment | An `OPEN` `GoalNegotiation` on Goal X; Goal X is separately `ABANDONED` via an accepted `GoalChangeProposal` before the negotiation resolves | the Goal lifecycle transition applies | the same transaction closes the negotiation as `MOOT` (`goal_technical_design.md` GOAL-14) — resolved; Finding 3 no longer applies |
| SST7 | Recovery Plan's own recovery never blocks Penalty Window's | A Penalty Window completes during a restart | `recover_penalty_window_state()` runs (step 2), before `recover_recovery_plan_state()` (step 6) | Penalty Window's own state machine (3.1) has no code dependency on Recovery Plan at all — Penalty Window recovery completes correctly regardless of when (or whether) Recovery Plan's own consistency check runs, confirming the one-directional relationship in Section 4 of `recovery_plan_technical_design.md` (RP-6: Recovery Plan reacts to Penalty Window, never the reverse) |

---

## 11. Deferred Workflows

Explicitly backlogged, per your instruction — not designed further
here:

- **`terminate()`** — the administrative `FROZEN`/`ACTIVE -> COMPLETED`
  path. Data model (`resolution_method`) is ready; no function exists
  yet.
- **The producer of `IncidentEvidence`** — the upstream mechanism that
  observes and structures facts into a form `assess_severity()` can
  consume. The state model in this document begins at
  `IncidentEvidence`/`CONFIRMED Incident`; whatever produces that input
  is a separate, later mechanism.
- **`UrgeSupportProtocol`** — a supportive workflow reacting to
  `urge_disclosure.recorded`. Once designed, it plugs into the event
  map (Section 5) as a new consumer; it does not change any existing
  state machine's shape.

---

## 12. Findings Requiring Follow-Up

These are the concrete results of this integration audit — each is a
gap in an *existing* document, not a new design decision. Fixing any of
them means editing the document named, not this one.

### Finding 1 — `Incident` Has Two Incompatible Schemas (Resolved)

`penalty_window_technical_design.md`'s original SQL schema (3.3) defined
`incidents` with a direct `severity REAL` column and no confirmation
state at all. `trust_manager_technical_design.md`'s `Incident` dataclass
(2.10) instead has `confirmation: IncidentConfirmation` and
`assessment: IncidentAssessment | None`, with severity living inside
`assessment.intrinsic_severity`, not as a direct column. These described
the same conceptual entity with **incompatible shapes**. This surfaced
concretely in `extension_technical_design.md` Section 4:
`_build_extension_context()` needed `intrinsic_severity` and
`cooperation` — fields that exist only on the Trust Manager's model —
while the Penalty Engine owned `consumed_by_penalty_window_id` on its
own, differently-shaped table.

**Resolution applied:** `Incident` is now owned by the Trust Manager in
full (confirmation, assessment, and all descriptive fields —
`trust_manager_technical_design.md` v3). The Penalty Engine holds only
its own `incident_consumption` table (a narrow reference:
`incident_id`, `penalty_window_id`, a denormalized `trust_domain`
snapshot), reading everything else via
`get_incident_assessment()`/`get_confirmed_incidents_since()`
(`trust_manager_technical_design.md` Section 13), consumed by Extension
instead of an implied direct read. This mirrors the resolution already
chosen for `GoalAccountabilityAssessment`: ownership follows "what is
this an assessment *of*," and severity/confirmation are assessments
belonging to the Trust Manager's domain, not the Penalty Engine's.

### Finding 2 — Recovery Plan Had No Owning Document (Resolved)

Referenced by event name and by philosophical role (`philosophy.md`
3.2, 3.4, 3.9) but previously given no data model, lifecycle, module
boundary, or invariants of its own. Section 3.11 and SST7 confirmed
this was a **documentation gap, not a functional blocker** — nothing
elsewhere in the system had a broken code dependency on Recovery Plan's
internals, only on the event names it was expected to emit/consume.
**Resolution applied:** `recovery_plan_technical_design.md` now defines
its full state machine, data model, narrow public API, and invariants
(RP-1 through RP-8), following the six-point module template that
emerged across every other module in this system (its own Section 1).
Recovery Credits continue to be recorded exclusively by the Penalty
Engine, per the same publish-then-owning-module-writes pattern
resolved for Finding 1 — a `record_recovery_credit_from_task_completion()`
proposal is pending approval for `penalty_window_technical_design.md`,
mirroring the still-pending Goal integration proposal for the Trust
Manager.

### Finding 3 — Orphaned Goal Negotiation After Unrelated Abandonment (Resolved)

(SST6.) No invariant previously prevented a `GoalNegotiation` from
resolving against a Goal that was independently abandoned/replaced
while the negotiation was still `OPEN`. **Resolution applied:**
`goal_technical_design.md` v3 adds a `MOOT` terminal
`GoalNegotiationStatus`. Whenever a Goal's lifecycle transitions to
`COMPLETED`/`ABANDONED`/`REPLACED`, the same transaction now
automatically closes any `OPEN` negotiation referencing that Goal as
`MOOT` (GOAL-14) — never left orphaned, and never silently treated as
if a decision had been reached.

### Finding 4 — Startup Orchestration Is a Runtime/Bootstrap Responsibility

`on_process_startup()` was previously documented inside
`activity_authorization_technical_design.md` (16.2), but per Section 7
above, it must call into Goal Management and Hygiene Privilege as well
— modules Activity Authorization has no other relationship with. This
is not a defect in Activity Authorization's design; it reflects a
responsibility the project had not yet named: **startup orchestration
belongs to the runtime/bootstrap layer**, coordinating domain modules
without itself being one. **Resolution applied:** Section 7 of this
document is now the authoritative definition of both the startup lease
mechanism and the full reconciliation order;
`activity_authorization_technical_design.md` 16.2 has been reduced to
describing only `recover_activity_authorization_state()` itself, with a
cross-reference here — mirroring how `extension_technical_design.md`
now holds the authoritative Extension algorithm while
`penalty_window_technical_design.md` holds only the cross-reference.

**A broader observation, not a design decision:** this Finding is not
merely "a function lives in the wrong file." `on_system_startup()`
coordinates *which domain module's recovery runs when*, without itself
belonging to any domain — it is neither a business rule (Coach/
Keyholder/Trust/Goal) nor raw infrastructure (the database, the outbox,
the `Clock`, the lease/claim mechanisms). This suggests the project's
implicit two-layer picture (`Domain modules` above `Infrastructure`) is
becoming a three-layer one:

```
Philosophy
    |
Domain modules        (Penalty Engine, Trust Manager, Activity
    |                   Authorization, Hygiene Privilege, Goal Management)
System composition     (startup orchestration, reconciliation ordering,
    |                   cross-module event wiring, integration audits
    |                   like this document itself)
Infrastructure         (database, transactional outbox, Clock, leases,
                         claims)
```

This document — and Section 7 in particular — is arguably the first
concrete inhabitant of that middle layer, not merely a symptom needing
a home elsewhere. Formalizing "System Composition" as its own
documented layer is not undertaken here; it is left as an architectural
observation for whenever a second inhabitant of that layer appears.

### Finding 5 — Trust Manager Lacked Its Own Crash Recovery Section (Resolved)

Noted in review much earlier in this project and, at the time, never
applied: `confirm_incident()` (writing a `ConfirmationRecord` and
updating `Incident.confirmation`) and the subsequent `assess_severity()`
+ `TrustEvidence` write were described as sequential steps, not
necessarily one transaction. A crash between them would have left a
`CONFIRMED` Incident with `assessment=None` indefinitely, with no
documented reconciliation step to detect or fix it — unlike every other
module in this system, which has a Section 16/8/9-style recovery
section.

**Resolution applied:** `confirm_incident()` now merges the
`ConfirmationRecord` write, the assessment, and the `TrustEvidence`
write into one transaction when `new_confirmation == CONFIRMED`
(`trust_manager_technical_design.md` Section 14.2), with a
`recover_trust_manager_state()` reconciliation step (14.3) added as
Step 1 of the system startup sequence (Section 7 above).
