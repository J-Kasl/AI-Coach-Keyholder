# Recovery Plan — Technical Design (v1.1)

> **v1.1:** the Recovery Credit integration proposed in Section 6 is
> now applied to `penalty_window_technical_design.md` (Section 3.4) —
> `record_recovery_credit_from_task_completion()`,
> `recovery_credit_decisions` (always written, mirroring
> `ExtensionDecision`), and the `I26` dedup constraint. Open question 4
> (the exact call site) is resolved and removed from Section 10.
>
> Draft for review, **not implemented**. Resolves `system_state_machine.md`
> Finding 2 — Recovery Plan was referenced by event name and by
> constitutional role (`philosophy.md` 3.2, 3.4, 3.9) but never given
> its own state machine, data model, or owning module. This document
> follows the six-point template that emerged, independently, across
> every prior module in this system: the question a module answers,
> the state it owns, the interpretation it owns, the narrow API it
> exposes, the events it publishes, and what it must never do — derived
> in that order, not started from a data model.
>
> Status: **Architecture baseline — approved for implementation.**
> Reached this status once the six-point module template was applied in
> full and the Recovery Credit integration (v1.1) was applied on both
> sides — this document is now the baseline for Recovery Plan's
> implementation, not a proposal still awaiting changes.

---

## 1. The Question This Module Answers

Every other module in this system reduces to one guiding question:

| Module | Question |
|---|---|
| Trust Manager | How safe is it to grant autonomy? |
| Penalty Engine | How long should this restriction last? |
| Activity Authorization | May this specific activity happen right now? |
| Hygiene Privilege | What hygiene policy applies right now? |
| Goal Management | What intervention best supports long-term development? |
| **Recovery Plan** | **What concrete steps, right now, would most effectively help the user earn back time within this Penalty Window?** |

Everything below follows from this question, not the other way around.
Two things follow immediately from how the question is phrased:

- **"Right now"** — the plan is not a fixed document created once and
  left alone; it reflects the user's actual, current situation, and is
  explicitly regenerated when the Penalty Window's capacity changes
  (`philosophy.md` 3.4: "Whenever Penalty Window is extended, Recovery
  Plan must be regenerated").
- **"Concrete steps"** — the answer is not a philosophy or a mood, it
  is a specific, achievable set of `Recovery Task`s
  (`domain_glossary.md`: "a task assigned to support behavioral
  recovery"). `philosophy.md` 3.9: "Coach should always design it to be
  realistically achievable."

This question is asked exclusively by the Coach (`philosophy.md` 3.2:
"Recovery is designed by Coach... Coach never decides whether Recovery
exists"). The Keyholder decides the restriction (Penalty Engine's
question); the Coach decides the path back (this module's question).
This is `philosophy.md` 2.9 (Accountability Versus Development) applied
to the one place the constitution names explicitly as its concrete
embodiment: "Every Penalty Window ALWAYS consists of two inseparable
parts" — Restriction (Keyholder) and Recovery (Coach).

---

## 2. Positioning: What This Module Owns, Reads, Publishes, and Never Does

### 2.1 State It Owns

- `RecoveryPlan` — one per Penalty Window, mirroring (not duplicating)
  the window's own lifecycle.
- `RecoveryTask` — the individual, concrete steps that make up a plan.
- `RecoveryTaskCompletion` — Recovery Plan's own judgment that a task
  was genuinely completed.

### 2.2 Interpretation It Owns

Whether a given `RecoveryTask` has actually been completed, and how
much of the plan's total capacity that completion should represent, is
Recovery Plan's own interpretation — the Coach's domain, exactly as
`assess_severity()` is the Trust Manager's own interpretation and
`GoalAccountabilityAssessment` is the Keyholder's. No other module
second-guesses this judgment; it only consumes the result (2.11,
Domain Interpretation).

### 2.3 Narrow Public API This Module Exposes

```python
def get_recovery_task_completion(db: Database, completion_id: str) -> RecoveryTaskCompletion | None:
    """
    The ONLY permitted way for the Penalty Engine to read a completed
    task judgment when deciding how many Recovery Credit hours to
    record (Section 6). Exposes nothing about the plan's other tasks,
    the Coach's reasoning for the plan's design, or anything beyond
    this one completion record.
    """
```

Recovery Plan reads, but never writes: `penalty_windows` existence/
status (the same established precedent as Activity Authorization and
Hygiene Privilege — Section 2 of `system_state_machine.md`), via
direct read of status, never `freeze_periods` — freeze/resume
timing is consumed entirely through the events in Section 5, never
polled.

### 2.4 Events It Publishes

See Section 5 in full. In summary: `recovery_plan.created`,
`recovery_plan.regenerated`, `recovery_plan.task_completed`,
`recovery_plan.frozen`, `recovery_plan.resumed`,
`recovery_plan.completed`.

### 2.5 What This Module Must Never Do

- **Never decides whether a Penalty Window exists, how long it lasts,
  or when it extends** — that is the Keyholder's question, answered
  entirely within the Penalty Engine (`philosophy.md` 3.2). Recovery
  Plan reacts to Penalty Window events; it never causes them.
- **Never writes to `penalty_windows`, `freeze_periods`,
  `incident_consumption`, or the Penalty Engine's
  `recovery_credit_ledger`** — Recovery Credits are Penalty Engine's own
  ledger (`penalty_window_technical_design.md` 3.3); this module
  publishes a completed task judgment (Section 6), and the Penalty
  Engine alone decides whether and how much credit to record from it,
  the same pattern used for Goal Management's relationship to the Trust
  Manager (`system_state_machine.md` Finding 1's resolution).
- **Never treats a Recovery Task as mandatory in the sense Rules are
  mandatory** — failing to complete a proposed task is never itself an
  Incident, never itself extends the Penalty Window, and never itself
  lowers Trust. It simply means less credit is earned — the absence of
  a benefit, never a Rule-violation-shaped penalty
  (`philosophy.md` 2.2, 2.10, applied here by the same reasoning already
  established for Goals).
- **Never exists outside a Penalty Window.** Unlike Goals, which are
  ongoing, a `RecoveryPlan` has no meaning without an active or frozen
  window to be recovering from (`philosophy.md` 3.9: "Recovery Plan
  exists ONLY during Penalty Window. It is not a general coaching
  tool").

---

## 3. Data Model

### 3.1 `RecoveryPlan`

```python
class RecoveryPlanStatus(StrEnum):
    ACTIVE = "active"       # mirrors the Penalty Window's own ACTIVE state
    FROZEN = "frozen"        # mirrors the Penalty Window's own FROZEN state
    COMPLETED = "completed"  # the Penalty Window completed; this plan's life ends with it


@dataclass
class RecoveryPlan:
    """
    MUTABLE, one per Penalty Window (1:1 -- philosophy.md 3.2: Recovery
    Plan is automatically created whenever a Penalty Window begins). Its
    status is a PROJECTION of the Penalty Window's own status (mirrored
    via events, Section 5), never independently decided -- this module
    does not own the freeze/resume/complete decision, only reacts to it
    (2.5 above).
    """
    id: str
    penalty_window_id: str            # 1:1, unique
    status: RecoveryPlanStatus
    current_version: int              # incremented on regeneration (3.4)
    recovery_credit_capacity_hours: float   # a READ, copied from the Penalty Window at creation/regeneration time -- never independently computed (see 3.4)
    created_at: datetime
    status_changed_at: datetime
```

`recovery_credit_capacity_hours` is a **snapshot**, not a live value —
the authoritative figure
(`target_active_hours / 2`, `penalty_window_technical_design.md` I3) is
always owned and computed by the Penalty Engine; Recovery Plan copies
it at creation and at each regeneration (3.4), the same denormalization
discipline used throughout this system (e.g.,
`EffectiveHygienePolicyResult`'s audit snapshots).

### 3.2 `RecoveryTask`

```python
class RecoveryTaskStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    EXPIRED = "expired"       # the plan was regenerated (3.4) before this task was completed
    WITHDRAWN = "withdrawn"    # the Coach removed it as no longer relevant, before completion


@dataclass
class RecoveryTask:
    """
    MUTABLE, belongs to exactly one RecoveryPlan version. A task
    proposed in version N does not automatically carry over to version
    N+1 (3.4) -- regeneration is a genuine re-design, not an in-place
    edit.
    """
    id: str
    recovery_plan_id: str
    plan_version: int
    title: str
    description: str
    credit_hours: float          # the Coach's proposed value for this task -- see 3.3 for the constraint this is subject to
    status: RecoveryTaskStatus
    created_at: datetime
    status_changed_at: datetime
```

### 3.3 The 50% Constraint Is Enforced Where Capacity Already Lives

`philosophy.md` 3.4: "Recovery Plan should always allow exactly half of
the initially assigned Penalty Window, plus exactly half of every later
extension." This is **not** re-derived or re-enforced by this module —
it is already a Penalty Engine invariant
(`penalty_window_technical_design.md` I3:
`recovery_credit_capacity_hours = target_active_hours / 2`, computed as
a property, not a stored value that could drift). Recovery Plan's own
constraint, at the level of *this* module, is only:

```
sum(task.credit_hours for task in plan.tasks) SHOULD approximate
plan.recovery_credit_capacity_hours (a design guideline for the Coach
when proposing tasks -- not independently re-enforced here)
```

The actual, binding cap is applied where Recovery Credits are recorded
— the Penalty Engine, at the moment a completion is converted to
ledger hours (Section 6) — using the exact same "cap is a separate,
later concern from the module's own interpretation" pattern already
established for Extension's `apply_capacity_cap()`
(`extension_technical_design.md` 3.4). Recovery Plan proposing a
slightly generous set of tasks is not itself an error; the Penalty
Engine's ledger simply never credits past the cap, the same way an
eligible Incident's Extension can be `capacity_limited` without the
Incident having been ineligible.

### 3.4 Regeneration

```python
def regenerate_recovery_plan(db: Database, recovery_plan_id: str, new_capacity_hours: float, now: datetime) -> None:
    """
    Triggered by penalty_window.target_duration_changed
    (penalty_window_technical_design.md 4.2) -- i.e., whenever should_extend()
    (extension_technical_design.md) assigns additional hours.
    Increments RecoveryPlan.current_version, snapshots the new
    recovery_credit_capacity_hours, and transitions every task still
    PROPOSED/ACCEPTED under the previous version to EXPIRED -- the Coach
    then proposes a fresh set of tasks under the new version, reflecting
    the user's actual current situation rather than an in-place patch
    to a stale plan.
    """
```

Regeneration never touches already-`COMPLETED` tasks or their
`RecoveryTaskCompletion` records — history is preserved exactly as
earned; only the forward-looking, not-yet-completed portion of the
plan resets.

---

## 4. Recovery Plan Lifecycle

```
penalty_window.started -> RecoveryPlan created, status=ACTIVE
penalty_window.frozen   -> RecoveryPlan.status -> FROZEN
penalty_window.resumed  -> RecoveryPlan.status -> ACTIVE
penalty_window.target_duration_changed -> regenerate_recovery_plan() (3.4), status unchanged
penalty_window.completed -> RecoveryPlan.status -> COMPLETED (terminal)
```

Every transition here is a **reaction** to a Penalty Window event
(Section 2.5) — there is no path from Recovery Plan back into the
Penalty Window's own state machine. This mirrors exactly the
relationship `penalty_window_technical_design.md` 4.2 already
described conceptually, before this document existed (originally as a
placeholder "recovery_plan_generator" consumer of `penalty_window.*`
events, since corrected there to reference this document directly) —
this document is simply the place that consumer's own internal state
is actually specified.

---

## 5. Domain Events

| event_type | source_module | When It Occurs |
|---|---|---|
| `recovery_plan.created` | recovery_plan | reacting to `penalty_window.started` |
| `recovery_plan.frozen` | recovery_plan | reacting to `penalty_window.frozen` |
| `recovery_plan.resumed` | recovery_plan | reacting to `penalty_window.resumed` |
| `recovery_plan.regenerated` | recovery_plan | reacting to `penalty_window.target_duration_changed` (3.4) |
| `recovery_plan.task_proposed` | recovery_plan | a new `RecoveryTask(status=PROPOSED)` |
| `recovery_plan.task_accepted` | recovery_plan | `PROPOSED -> ACCEPTED` (the user acknowledges the task — a lightweight confirmation, not a `critical_change`, the same weight as a Goal's `GoalChangeProposal`) |
| `recovery_plan.task_completed` | recovery_plan | a new `RecoveryTaskCompletion` — **this is the event the Penalty Engine consumes (Section 6)** |
| `recovery_plan.task_withdrawn` | recovery_plan | the Coach withdraws a task before completion |
| `recovery_plan.completed` | recovery_plan | reacting to `penalty_window.completed` |

All events use the transactional outbox already defined in
`penalty_window_technical_design.md` — no new mechanism here.

---

## 6. Recovery Credit Integration (Publish-Then-Penalty-Engine-Writes) — Applied

Directly mirroring the resolution of `system_state_machine.md` Finding
1 (Goal Management → Trust Manager): Recovery Plan **never writes** to
the Penalty Engine's `recovery_credit_ledger`. It publishes a completed,
interpreted judgment; the Penalty Engine reads it and decides. **Now
applied on both sides** (`penalty_window_technical_design.md` Section
3.4):

```
Recovery Plan
    -- interprets: was this RecoveryTask genuinely completed?
    -- publishes recovery_plan.task_completed
Penalty Engine
    -- consumes the event
    -- reads the completion via get_recovery_task_completion() (2.3)
    -- decides how many hours to credit (capped by its own capacity logic, 3.3)
    -- writes recovery_credit_ledger itself, plus an always-written
       RecoveryCreditDecision (auditable even at credited_hours=0)
```

```python
# Applied in penalty_window_technical_design.md Section 3.4 -- summarized here for reference.

def record_recovery_credit_from_task_completion(db: Database, completion_id: str, event: DomainEvent) -> RecoveryCreditDecision:
    """
    Reads the referenced RecoveryTaskCompletion (read-only, via Recovery
    Plan's get_recovery_task_completion(), 2.3) and the corresponding
    RecoveryTask.credit_hours, then ALWAYS writes an append-only
    RecoveryCreditDecision (mirroring ExtensionDecision's shape --
    extension_technical_design.md 2.2 -- so a zero-hour outcome remains
    fully auditable), and writes to recovery_credit_ledger only when
    credited_hours > 0 -- capped so that recovery_credits_earned_hours
    never exceeds recovery_credit_capacity_hours (I3), the same
    capacity-is-separate-from-eligibility discipline used for Extension
    (extension_technical_design.md 3.4). The Penalty Engine, not
    Recovery Plan, is the sole writer of both tables, consistent with
    its existing ownership (penalty_window_technical_design.md 3.3).
    """
```

**Deduplication (I26, `penalty_window_technical_design.md`):** a given
`RecoveryTaskCompletion` is credited at most once, enforced in two
layers — `recovery_credit_decisions.completion_id` (`UNIQUE`, the
primary, always-applicable guarantee, since a `RecoveryCreditDecision`
is written regardless of outcome) and
`recovery_credit_ledger.source_completion_id` (a partial `UNIQUE`,
applying only to entries that actually recorded genuine credit). Both
exist independently of the standard consumer-side event dedup (I19),
the same defense-in-depth reasoning applied to the Goal integration's
`TrustEvidence` uniqueness constraint (TI25).

---

## 7. Invariants

| # | Source | Invariant |
|---|---|---|
| RP-1 | 2.5 | Recovery Plan never writes to `penalty_windows`, `freeze_periods`, `incident_consumption`, or `recovery_credit_ledger`. It only reads Penalty Window existence/status directly and reacts to its events. |
| RP-2 | 2.2, 6 | Whether a `RecoveryTask` was genuinely completed is Recovery Plan's own interpretation, recorded as `RecoveryTaskCompletion` and published — never decided by, or re-derived from, the Penalty Engine. |
| RP-3 | 3.1 | `RecoveryPlan.recovery_credit_capacity_hours` is a snapshot, refreshed only at creation and regeneration (3.4) — never independently recomputed by this module from a raw Penalty Window read. |
| RP-4 | 3.4 | `regenerate_recovery_plan()` never modifies or deletes an already-`COMPLETED` `RecoveryTask` or its `RecoveryTaskCompletion` — only `PROPOSED`/`ACCEPTED` tasks under the previous version are affected (transitioned to `EXPIRED`). |
| RP-5 | 2.5, `philosophy.md` 2.2/2.10 | An incomplete or `EXPIRED` `RecoveryTask` never creates an Incident, never extends the Penalty Window, and never lowers Trust — the only consequence of non-completion is the absence of credit. |
| RP-6 | 4 | Every `RecoveryPlan` lifecycle transition is a direct reaction to a specific Penalty Window event (Section 4) — there is no code path by which Recovery Plan initiates a Penalty Window state change. |
| RP-7 | 2.5, `philosophy.md` 3.9 | A `RecoveryPlan` exists only for the lifetime of its associated Penalty Window — created on `penalty_window.started`, terminated on `penalty_window.completed`. No `RecoveryPlan` exists without a corresponding Penalty Window. |
| RP-8 | 6 | The actual Recovery Credit hour amount recorded in `recovery_credit_ledger` is decided exclusively by the Penalty Engine's `record_recovery_credit_from_task_completion()` — Recovery Plan's `RecoveryTask.credit_hours` is a proposal, not a binding instruction. |

---

## 8. Persistence and Crash Recovery

Every write in this module (plan creation/status transition,
regeneration, task proposal/acceptance/completion/withdrawal) is a
single, immediate, atomic operation — the same `_apply_transition`
discipline used throughout this system. There is no multi-step,
non-terminal lifecycle analogous to Activity Authorization's
`PENDING_FREEZE` or Hygiene Privilege's `GRANTED` — a `RecoveryTask`
does not have an externally-imposed deadline of its own (unlike a
Hygiene grant's `grant_expires_at`); it simply remains `PROPOSED`/
`ACCEPTED` until the user completes it, the Coach withdraws it, or a
regeneration expires it. No dedicated timeout-driven recovery function
is required: `recover_recovery_plan_state()` at startup
(`system_state_machine.md` Section 7, added as a new step alongside the
existing five) needs only to confirm that every `ACTIVE`/`FROZEN`
Penalty Window has an exactly-one corresponding `RecoveryPlan` in the
matching status — a consistency check, not a reconciliation of pending
timeouts.

```python
def recover_recovery_plan_state(db: Database, now: datetime) -> None:
    """
    Consistency check only. For every ACTIVE/FROZEN Penalty Window,
    confirms a RecoveryPlan exists with a matching status; if a crash
    occurred between penalty_window.started being emitted and this
    module consuming it, the standard at-least-once outbox redelivery
    (penalty_window_technical_design.md I23) -- not this function --
    is what actually creates the missing plan. This function exists to
    detect and flag (not silently fix) any case where that redelivery
    itself did not eventually succeed, which would indicate a bug
    elsewhere, not a normal condition for this function to repair
    directly.
    """
```

---

## 9. Test Matrix

| # | Scenario | Given | When | Then |
|---|---|---|---|---|
| RPT1 | A plan is created exactly when a window starts | a new Penalty Window | `penalty_window.started` is consumed | a `RecoveryPlan(status=ACTIVE)` is created, 1:1 with the window (RP-7) |
| RPT2 | A plan mirrors freeze/resume | `RecoveryPlan(status=ACTIVE)` | `penalty_window.frozen` then `penalty_window.resumed` are consumed | `status -> FROZEN -> ACTIVE`, never independently decided (RP-6) |
| RPT3 | Regeneration expires stale tasks, preserves completed ones | a plan with one `COMPLETED` task and one `PROPOSED` task | `penalty_window.target_duration_changed` is consumed | the `COMPLETED` task and its `RecoveryTaskCompletion` are untouched; the `PROPOSED` task becomes `EXPIRED` (RP-4) |
| RPT4 | Non-completion has no Rule-shaped consequence | a `RecoveryTask` becomes `EXPIRED` via regeneration | inspect Trust/Incident/Penalty Window effects | none — no Incident, no Extension, no Trust change (RP-5) |
| RPT5 | Recovery Plan never writes the credit ledger | a `RecoveryTaskCompletion` is recorded | inspect all writes | no write to `recovery_credit_ledger` from this module — only `recovery_plan.task_completed` is published (RP-1, RP-8) |
| RPT6 | The Penalty Engine caps credit independent of the proposed value | `RecoveryTask.credit_hours` exceeds remaining `recovery_credit_capacity_hours` | `record_recovery_credit_from_task_completion()` runs | the ledger entry is capped at the remaining capacity, not the full proposed value; the `RecoveryCreditDecision` records both the proposed and capped values regardless (RP-8, mirrors Extension's capacity cap) |
| RPT7 | A plan terminates exactly when its window completes | `RecoveryPlan(status=ACTIVE or FROZEN)` | `penalty_window.completed` is consumed | `status -> COMPLETED` (terminal); no further tasks may be proposed (RP-7) |
| RPT8 | No plan exists without a window | — | query for any `RecoveryPlan` with no corresponding `penalty_windows` row | none exist, by construction (RP-7) |

---

## 10. Open Questions Before Implementation

1. **Who authors `RecoveryTask` content** — presumably the Coach (LLM),
   proposing tasks in conversation, but the exact mechanism (a
   structured generation step, a conversational extraction, a review
   gate before a task becomes `PROPOSED`) is a separate, follow-up
   design, the same way "who populates `IncidentEvidence`" and "what
   generates `GoalEvidence`" were left open in their respective
   documents.
2. **Task acceptance weight** — this document treats
   `PROPOSED -> ACCEPTED` as a lightweight user acknowledgment, not a
   `GoalChangeProposal`-style confirmation with an expiry. Whether it
   needs one is your call; nothing else in this design depends on the
   answer.
3. **`credit_hours` guidance for the Coach** — Section 3.3 states the
   Coach *should* keep the sum close to capacity, but this document
   does not enforce it structurally (the Penalty Engine's cap is the
   real backstop). Whether to add a soft warning when a proposed task's
   `credit_hours` would clearly overshoot remaining capacity is a
   UX question, not an architectural one.
