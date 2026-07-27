# Penalty Window — Technical Design (v2)

> **v2 — applied two integrations following the
> `system_state_machine.md` audit:** (1) `incidents` replaced by
> `incident_consumption`, owned by the Penalty Engine, with `Incident`
> itself owned entirely by the Trust Manager (Finding 1 — see Section
> 2.5/2.6 and `trust_manager_technical_design.md` Section 13); (2) the
> Recovery Plan integration proposed in
> `recovery_plan_technical_design.md` Section 6 is now applied (Section
> 3.4): `record_recovery_credit_from_task_completion()`,
> `recovery_credit_decisions` (append-only, mirroring
> `ExtensionDecision`'s shape), and `recovery_credit_ledger.source_completion_id`
> (I26) — a `RecoveryTaskCompletion` is credited at most once, and a
> zero-hour outcome remains fully auditable.
>
> This document is technical documentation derived from `philosophy.md`
> v1.5, Section 3 (and now also Section 4.4, see the `freeze_periods`
> modification below). It does not contain production code — it is a
> design proposal intended for review before implementation. Wherever the
> text refers to a philosophical principle, it always references a
> specific subsection (3.x/4.x or 2.x) of `philosophy.md`, rather than
> making an independent claim.
>
> **Addendum (Activity Authorization):** the `freeze_periods` schema has
> been extended with `reason='partnered_intimacy_authorization'` and the
> foreign key `authorization_decision_id` — see Section 3.3 and
> invariants I21/I22. The full design of the module that issues this
> freeze reason is documented separately in
> `activity_authorization_technical_design.md`.
>
> Status: **Architecture baseline — approved for implementation.**
> Reached this status once the Recovery Plan integration (Section 3.4)
> and the `Incident` ownership fix (Finding 1, `incident_consumption`)
> were applied — this document is now the baseline for the Penalty
> Engine's implementation, not a proposal still awaiting changes.

---

## 1. Technical Invariants Derived from the Philosophy

The table below maps every philosophical invariant (see `philosophy.md`
Section 3.x) to a concrete, testable technical condition. This is the
bridge between "what must hold" and "how we know it holds."

| # | Philosophical Source | Technical Invariant |
|---|---|---|
| I1 | 3.2 | No operation in `coach_engine`/`recovery_plan` may write to `penalty_windows.status`, `base_duration_hours`, or `extensions_hours`. Writing to these fields is reserved exclusively for `penalty_engine`. |
| I2 | 3.3 | Earning tokens (`token_ledger` credit operations) must never check `penalty_window.status` anywhere. Spending tokens (debit operations) must always verify that no `ACTIVE`/`FROZEN` window exists. |
| I3 | 3.4 | `recovery_credit_capacity_hours` is always a derived value (`target_active_hours / 2`), never a stored column that could go out of sync after `extend()`. |
| I4 | 3.4 | No operation may decrease `recovery_credits_earned_hours` or delete a record from `recovery_credit_ledger`. The table is append-only (no `UPDATE`/`DELETE` path in the access layer). |
| I5 | 3.5 | `target_active_hours = min(base_duration_hours + extensions_hours, 336)` — a calculation, not a stored value; the cap is enforced at read time, not only on write. |
| I6 | 3.5 | The sum of all `FreezePeriod` intervals for a given window is never included in `active_hours_elapsed()`. |
| I7 | 3.6 | A decision about a Temporary Wear Exemption (approval/denial) must never read `penalty_window.status` as an input. The only input is the legitimacy of the request itself. |
| I8 | 3.7 | `OverallTrust.score` is never computed as an aggregate query over `TrustDomain` on read. It is a separately written value, changed only by an explicit, purpose-built operation. |
| I9 | 3.7 | A change to `TrustDomain` in domain A must never write to `HygieneLimits`, `LockPrivileges`, or similar data belonging to domain B, in any code path. |
| I10 | 3.7 | The list of valid domains (`trust_domain_registry`) may only be changed by an operation that creates a `ConsentRecord` with `target_type='trust_domain'` before the write. |
| I11 | 3.8 | `incident_consumption.incident_id` is written exactly once per Incident (`PRIMARY KEY` enforces write-once — a second insert attempt is a constraint violation, not a silent overwrite). This table, not any field on `Incident` itself, is the Penalty Engine's own record of consumption (System State Machine Finding 1 — `Incident` is owned entirely by the Trust Manager, see `trust_manager_technical_design.md` Section 13). |
| I12 | 3.8 | Selecting available Incidents for a new window: the Penalty Engine calls `get_confirmed_incidents_since(last_closed_at)` (Trust Manager, Section 13) and filters out any whose `id` already appears in `incident_consumption`. |
| I13 | 3.8, 3.9, 3.10 | A legitimate `FreezePeriod` (of any `reason`) never by itself creates a row in `incidents`. |
| I14 | 3.9 | A `HygieneAccessDecision` for a `MANDATORY` request never reads `penalty_window` or `TrustDomain('hygiene')` as a condition for `allowed` — it may read them at most for logging purposes. |
| I15 | 3.9 | The frequency of `MANDATORY` requests must never be an input to any function that writes to `trust_history`, `incidents`, or `penalty_windows`. It may only be an input to a function that generates a prompt for the Coach (an observation). |
| I16 | 3.10 | The `trigger_emergency_override()` path must have no dependency (import, call) on `ollama_client`, `coach_engine`, `keyholder_engine`, or `decision_engine`. It depends only on the `database` layer. |
| I17 | 3.10 | After `EMERGENCY_OVERRIDE`, `penalty_window.status` must never transition back to `ACTIVE` via any automatic path — only an explicit `resume()` called after confirmation may do so. |
| I18 | 2.8 | Every operation that changes `penalty_window.status` or inserts a row into `recovery_credit_ledger`/`freeze_periods` writes the corresponding `domain_event` within the same DB transaction. No code path may write one without the other. |
| I19 | 2.8 | Processing of a `domain_event` by a consumer (Coach) is idempotent with respect to `event.id` — redelivery of the same `event.id` must not cause a second effect. |
| I20 | 3.11 | Writes to `observations` originating from the Penalty Window subsystem never include a callback that modifies `penalty_windows`, `trust_domains`, or `rules`. |
| I21 (AA-FREEZE-1) | philosophy.md 4.4, Activity Authorization review | For a single `penalty_window_id`, at most one open (`ended_at IS NULL`) `freeze_periods` row with `reason='partnered_intimacy_authorization'` may exist — enforced by a partial unique index, not merely by application logic. Other `reason` values are unaffected. |
| I22 (PW-FREEZE-SET) | 2.3 | A Penalty Window is effectively `FROZEN` exactly when at least one open `freeze_periods` row exists for it, regardless of `reason`. `penalty_window.resumed` is emitted only on the transition of the count of open reasons from 1 to 0 — closing one of several concurrent reasons while another remains open causes neither a status change nor an event. |
| I23 (OUTBOX-1) | 2.8, Activity Authorization review ("transactional outbox") | `domain_events` is the transactional outbox for the entire system, not just a log. Writing the row (in the same transaction as the corresponding state change, I18) and publishing it (writing `published_at`) are separate steps. The publisher first claims a row (`claimed_at`/`claim_expires_at`) — safe even with multiple concurrent publisher processes. Delivery is **at-least-once**; `published_at` means only "the publisher handed it to the transport layer," not "the consumer processed it" — consumer-side deduplication is always the responsibility of `domain_event_consumers`, not of the outbox. |
| I24 (RESTART-1) | 2.8, "crash/restart recovery" review | No important timeout (confirmation, freeze deadline, session expiration) may exist solely as an in-memory timer. All are stored as absolute UTC timestamps in the database. On process startup, BEFORE accepting any new request, deterministic reconciliation of all in-progress states against the current time must run — `ensure_current_state()` (4.4) is therefore called at startup as well as during interactions. |
| I25 (API-BOUNDARY-2) | 2.6, Hygiene Privilege module | No module other than the Penalty Engine reads `incident_consumption` directly. The only permitted path to learn which Trust domain(s) a Penalty Window relates to is `get_penalty_window_relevant_domains()` (2.6). `Incident` itself (confirmation, assessment, description) is read by no module except via the Trust Manager's own `get_incident_assessment()`/`get_confirmed_incidents_since()` (`trust_manager_technical_design.md` Section 13). |
| I26 | 3.4, `recovery_plan_technical_design.md` Section 6 | A `RecoveryTaskCompletion` is credited at most once. `recovery_credit_decisions.completion_id` is the primary, always-written guarantee (`UNIQUE`); `recovery_credit_ledger.source_completion_id` (partial `UNIQUE`, populated only for completion-sourced entries) is a secondary reinforcement at the point genuine credit is actually recorded. |

---

## 2. State Machine

### 2.1 States

```
                    ┌─────────────┐
                    │   ACTIVE    │◄──────────────┐
                    └──────┬──────┘                │
                 freeze()  │     ▲ resume()         │ extend()
                    ┌──────▼──────┐                 │
                    │   FROZEN    │                 │
                    └──────┬──────┘                 │
                 resume()  │                         │
                           └─────────────────────────┘
                    ┌─────────────┐
        ACTIVE ────►│  COMPLETED  │
        (is_complete)└─────────────┘
```

For the first implementation there is **only one path to `COMPLETED`**:
natural completion of the countdown from `ACTIVE` (see 4.4).
`FROZEN → COMPLETED` via administrative termination (`terminate()`) is
deferred — the philosophy (`philosophy.md` 3.x) anticipates
Keyholder-driven administrative termination as a future possibility, but
we are not implementing it technically in the first version (see Section
6, open question resolved: deferred). If a need arises (database
recovery, migration, another administrative operation), it will be added
as a separate, later-designed operation — the data model
(`resolution_method` column) is already prepared for it; only the
function that would actively use it is missing.

### 2.2 Transitions and Their Guards

| Transition | Guard (Condition) | Side Effects |
|---|---|---|
| `(none) → ACTIVE` | ≥1 unconsumed Incident exists with `created_at > last_closed_at` | creates a `PenaltyWindow`, consumes the Incident(s), emits `STARTED` |
| `ACTIVE → FROZEN` | approved `TemporaryWearExemption` OR `EmergencyOverride` | `accumulated_active_hours = active_hours_elapsed(now)`; `active_period_started_at = None`; creates a `FreezePeriod`; emits `FROZEN` (+ `EMERGENCY_OVERRIDE_TRIGGERED` where relevant) |
| `FROZEN → ACTIVE` | closing a `FreezePeriod` (exemption expiry OR explicit confirmation of a safe state after an override) | `active_period_started_at = now`; closes `FreezePeriod.ended_at`; emits `RESUMED` |
| `ACTIVE → ACTIVE` (extend) | new Incident while `ACTIVE`, `should_extend() == True` (full decision logic in `extension_technical_design.md`) | `extensions_hours += Δ` (implicitly capped via `target_active_hours`); emits `EXTENDED`, `INCIDENT_CONSUMED` |
| `ACTIVE → COMPLETED` | `is_complete(now) == True` on any check operation (see 4.4) | `closed_at = now`; `resolution_method='countdown_complete'`; emits `COMPLETED` |

*(Administrative `terminate()` — `FROZEN`/`ACTIVE → COMPLETED` outside the
countdown — is deferred beyond the first implementation; see 2.1.)*

The guard on `EmergencyOverride` is deliberately **empty** (no
condition) — see `philosophy.md` 3.10: it must not require any
evaluation before execution. This is the only transition in the entire
state machine without a guard condition.

### 2.3 Freeze as a Set of Active Reasons

A Freeze is not modeled as a single reason with its own state machine for
combinations (as previously proposed). It is a **set of concurrently
active `FreezePeriod` records** for a given window:

```python
def is_frozen(db: Database, penalty_window_id: str) -> bool:
    """The window is FROZEN exactly when at least one FreezePeriod
    with ended_at IS NULL exists. No other state is tracked separately."""
    return db.count_open_freeze_periods(penalty_window_id) > 0
```

Transitions:

- **`freeze(reason)`** — always just an `INSERT` of a new `FreezePeriod`
  with `ended_at = NULL`. If `status == FROZEN` already, for a different
  reason, `status` does not change (it stays `FROZEN`); only a second
  open reason is added. `accumulated_active_hours` is updated only on
  the **first** freeze (the transition from `ACTIVE` to `FROZEN`) — a
  second or later freeze on an already-frozen window has no time effect
  (the countdown is already stopped).
- **`resume(reason)`** — closes (`ended_at = now`) the specific
  `FreezePeriod` matching the reason. The window returns to `ACTIVE`
  (`active_period_started_at = now`) only once
  `count_open_freeze_periods == 0` — i.e., once the **last** active
  reason disappears (I22/PW-FREEZE-SET).

**Boundary of responsibility with Activity Authorization** (see
`activity_authorization_technical_design.md`): `freeze()`/`resume()` with
`reason='partnered_intimacy_authorization'` is called exclusively by the
Penalty Engine, never directly by the Activity Authorization module.
Activity Authorization only issues an `AuthorizationDecision` with the
flag `freeze_penalty_window=True` (and the reference
`authorization_decision_id`); the Penalty Engine performs its own,
audited state transition based on it. Activity Authorization therefore
never writes to `penalty_windows` or `freeze_periods` directly — this is
the same one-way dependency as between the Trust Manager and
`should_extend()`.

This replaces the earlier proposal of "a second concurrent FreezePeriod
with priority on resume" — instead of a special priority rule, it
resolves concurrency naturally: as long as any reason (including
emergency) is open, the window stays FROZEN. This removes the need to
handle combinations of states in code, as requested — `is_frozen()` is
the single, trivial condition over an existing table.

### 2.4 Emergency Override as an Independent Technical Path

Even though the Emergency Override now, at the data level, creates an
ordinary `FreezePeriod` record (see 2.3), technically it **does not call
the same function** as a regular `freeze()` from a Temporary Wear
Exemption. It calls a separate, minimal function, `emergency_freeze()`,
which:

1. does not depend on `should_evaluate_exemption()` or any legitimacy
   logic,
2. produces the identical data effect as `freeze(reason=EMERGENCY_OVERRIDE)`
   (inserting a `FreezePeriod`, updating `accumulated_active_hours` only
   if this is the first open reason),
3. runs on the shortest, least-branching code path possible (see I16).

Reason for a separate function instead of a shared `reason` parameter in
one function: a shared function would need branching for the "normal"
freeze case (legitimacy check), and even if the override parameter
bypassed that branch, the regression risk (someone adds a check "above"
and accidentally hits the override branch too) is unnecessary. A
separate, trivial function is the safer default — the data model is
shared; the code path that writes it is not.

### 2.5 Public Read API for Cross-Module Queries (Added per Activity Authorization Review)

`freeze_periods` is an internal Penalty Engine table — no other module
(Activity Authorization, future modules) may read it directly, either
during normal operation or during crash recovery. The reason is the same
as why Activity Authorization must not write to `freeze_periods` (see
`activity_authorization_technical_design.md`, Sections 1, 7): schema
ownership is one-sided; otherwise, the boundary between modules would
blur at the first convenient opportunity — typically in recovery code,
which tends to be written "quickly" and easily ends up crossing
boundaries.

```python
class AuthorizationFreezeState(StrEnum):
    NOT_FOUND = "not_found"    # no freeze_periods row with this authorization_decision_id
    OPEN = "open"                # exists, ended_at IS NULL
    CLOSED = "closed"            # exists, ended_at IS NOT NULL, closed normally
    EXPIRED = "expired"          # exists, ended_at IS NOT NULL, closed due to expires_at

def get_authorization_freeze_state(db: Database, authorization_decision_id: str) -> AuthorizationFreezeState:
    """
    The ONLY permitted way for Activity Authorization (or any future
    module) to query the freeze state tied to its own decision — whether
    during normal operation or during crash recovery (see
    activity_authorization_technical_design.md, Section 16.3). Returns
    nothing about the penalty_window itself or about other freeze
    reasons — only what pertains to THIS authorization_decision_id.
    """
```

This is the only function the Penalty Engine exposes externally for this
purpose — no generic "give me the whole `freeze_periods` row." If a
future module needs a different view of the freeze state, another
narrow, purpose-named function is added, rather than opening access to
the table.

### 2.6 Public Read API: Which Trust Domains a Penalty Window Relates To (Added for the Hygiene Privilege Module)

Some consumers (the Hygiene Privilege module, see
`hygiene_privilege_technical_design.md`) need to know which Trust
domain(s) triggered or extended a given Penalty Window — for example, to
distinguish a hygiene-specific Penalty Window from one unrelated to
hygiene (`philosophy.md` 3.9). This information lives in the
`incident_consumption` table (`trust_domain`, `penalty_window_id`),
which is internal to the Penalty Engine for the same reason
`freeze_periods` is: opening direct read access to it would let module
boundaries blur at the next convenient opportunity.

```python
class PenaltyWindowNotFound(Exception):
    """Raised when the given penalty_window_id does not exist at all —
    distinct from a genuinely existing window with no recorded relevant
    domains, so that a caller can tell 'no such window' apart from 'this
    window's data looks anomalous' (see the note below)."""


def get_penalty_window_relevant_domains(db: Database, penalty_window_id: str) -> frozenset[str]:
    """
    The ONLY permitted way for another module to learn which Trust
    domain(s) are associated with a given Penalty Window — i.e., the
    denormalized `trust_domain` snapshots of the `incident_consumption`
    rows whose `penalty_window_id` matches.

    Raises PenaltyWindowNotFound if no penalty_windows row with this ID
    exists. Returns frozenset() only for a window that DOES exist but
    has (anomalously) consumed no Incident with a recorded domain — see
    the note below on why this should not normally happen.

    Does not expose anything else about the underlying Incidents
    (severity, description, confirmation, etc. — none of which the
    Penalty Engine even stores; see `trust_manager_technical_design.md`
    Section 13) — only the set of domain identifiers, which is the
    minimum a consumer legitimately needs to decide "is this window
    related to domain X."
    """
```

**A Penalty Window with zero relevant domains is an anomalous data
state, not a normal case to silently tolerate.** Every window is created
because it consumed at least one Incident (2.2, the `(none) → ACTIVE`
transition guard requires "≥1 unconsumed Incident"), and every Incident
carries a `trust_domain`. A caller receiving `frozenset()` for an
existing window should treat it as a signal worth flagging for audit
(e.g., an `ObservationRecord`), not as an ordinary "unrelated" result —
callers must not conflate "no relevant domains recorded" with "confirmed
unrelated to domain X," since the former indicates a data invariant
violation upstream, while the latter is the normal, expected outcome for
most windows relative to most domains.

Reading `penalty_windows` itself (existence, `status`, `closed_at`) to
determine whether an active or frozen window exists at all remains
permitted directly, consistent with the existing pattern used by
Activity Authorization (Section 1 of that document) and invariant I2 —
only `freeze_periods` and `incidents` are treated as internal tables
requiring a narrow public API rather than direct reads.

---

## 3. Persistence and Transaction Boundaries

### 3.1 Principle

Derived from I18/I19 (Principle 2.8): **state and event are created in a
single transaction.** SQLite supports transactions natively
(`BEGIN`/`COMMIT`), so at this stage of the project no transactional
outbox or event sourcing is needed to satisfy this — a simpler, but
equally safe, approach works:

```python
def _apply_transition(db: Database, penalty_window_id: str, mutation: Callable, event: DomainEvent) -> None:
    """
    The single entry point for ANY Penalty Window state change. The data
    mutation and the event write happen within the same sqlite3
    transaction — either both, or neither. This enforces I18 at the code
    level, not merely as a convention.
    """
    with db._connect() as conn:          # one transaction, see database.py
        mutation(conn)                    # UPDATE penalty_windows / INSERT freeze_periods / ...
        _write_event(conn, event)         # INSERT INTO domain_events
    # conn.commit() happens at the end of the `with` block (see database._connect)
    # -> a crash between mutation() and _write_event() causes a ROLLBACK of both,
    #    never a partial write.
```

This directly satisfies the philosophy's requirement that *"no audit
record can be created for a change that did not occur"* and that *"no
change can occur without a corresponding audit trail"* — both sentences
describe the same guarantee (atomicity), just from opposite directions,
and a single transaction gives it to us for free, without needing a
distributed pattern.

### 3.2 Why Not Event Sourcing (Yet)

A full event-sourcing model (state = a projection of events, not its own
table) was considered. I do not recommend it at this stage of the
project:

- SQLite plus single-user operation does not have the problem event
  sourcing typically solves (concurrent writes, distributed components).
- It would double the complexity (state plus an event log) without a
  corresponding benefit.
- Nothing in `philosophy.md` 2.8 specifically requires event sourcing —
  it requires properties (atomicity, recoverability, idempotency) that
  can be satisfied by a simpler route as well.

If concurrency is added in the future (multiple clients writing at once,
distributed operation), this is the first thing to reconsider — but the
`domain_events` table (below) is designed so that it could serve as the
basis for a later move to event sourcing without discarding history.

### 3.3 Schema (Design Proposal, Not a Final Migration)

```sql
CREATE TABLE penalty_windows (
    id                          TEXT PRIMARY KEY,
    created_at                  TEXT NOT NULL,
    status                      TEXT NOT NULL,   -- 'active' | 'frozen' | 'completed'
    closed_at                   TEXT,
    resolution_method           TEXT,             -- 'countdown_complete' | 'manual_termination'

    base_duration_hours         REAL NOT NULL,
    extensions_hours            REAL NOT NULL DEFAULT 0,

    accumulated_active_hours    REAL NOT NULL DEFAULT 0,
    active_period_started_at    TEXT,             -- NULL when FROZEN/COMPLETED

    recovery_credits_earned_hours REAL NOT NULL DEFAULT 0,  -- denormalized sum from the ledger
    high_security_status        INTEGER NOT NULL DEFAULT 1
    -- NOTE: an earlier draft of this schema included a
    -- `hygiene_level_override INTEGER` column here. It has been removed:
    -- a Penalty Window never directly changes a Hygiene Trust Level (or
    -- any Level at all). The hygiene-specific override policy is a
    -- separate, auditable determination owned by the Hygiene Privilege
    -- module (see hygiene_privilege_technical_design.md,
    -- HygienePenaltyOverrideDetermination) and keyed to a
    -- penalty_window_id, not stored on this table.
);

CREATE TABLE recovery_credit_ledger (
    id                   TEXT PRIMARY KEY,
    penalty_window_id    TEXT NOT NULL REFERENCES penalty_windows(id),
    earned_at            TEXT NOT NULL,
    hours                REAL NOT NULL,
    source_activity      TEXT NOT NULL,

    -- Applying recovery_plan_technical_design.md Section 6 (I26):
    -- populated when this entry originates from a RecoveryTaskCompletion
    -- (owned by the Recovery Plan module). NULL for any future
    -- source_activity that is not completion-based, which is why the
    -- uniqueness constraint below is partial, not a plain column
    -- constraint.
    source_completion_id TEXT
    -- Deliberately WITHOUT a 'revoked_at' or similar field (see I4).
    -- The access layer exposes no UPDATE/DELETE method for this table.
);

-- I26: a given RecoveryTaskCompletion may be credited at most once.
-- Partial (not a plain UNIQUE column) so that future, non-completion-
-- sourced ledger entries (source_completion_id IS NULL) are unaffected.
CREATE UNIQUE INDEX idx_recovery_credit_ledger_one_per_completion
    ON recovery_credit_ledger (source_completion_id)
    WHERE source_completion_id IS NOT NULL;

-- Applying recovery_plan_technical_design.md Section 6: append-only,
-- mirrors ExtensionDecision's shape (extension_technical_design.md 2.2)
-- exactly, so that a zero-hour outcome (e.g., capacity exhausted) is
-- always distinguishable from "this completion was never processed."
CREATE TABLE recovery_credit_decisions (
    id                   TEXT PRIMARY KEY,
    created_at           TEXT NOT NULL,
    completion_id         TEXT NOT NULL,   -- FK to Recovery Plan's RecoveryTaskCompletion; UNIQUE enforces "processed at most once" as the primary guarantee (I26)
    penalty_window_id    TEXT NOT NULL REFERENCES penalty_windows(id),

    proposed_hours        REAL NOT NULL,    -- RecoveryTask.credit_hours, as proposed by Recovery Plan
    credited_hours        REAL NOT NULL,    -- actually applied; MAY be 0
    capacity_limited      INTEGER NOT NULL, -- boolean: True iff credited_hours < proposed_hours due to remaining capacity
    explanation           TEXT NOT NULL     -- REQUIRED, non-empty regardless of credited_hours
);

CREATE UNIQUE INDEX idx_recovery_credit_decisions_one_per_completion
    ON recovery_credit_decisions (completion_id);

CREATE TABLE freeze_periods (
    id                       TEXT PRIMARY KEY,
    penalty_window_id        TEXT NOT NULL REFERENCES penalty_windows(id),
    started_at                TEXT NOT NULL,
    ended_at                   TEXT,             -- NULL = this particular reason is still active
    reason                     TEXT NOT NULL,    -- 'temporary_wear_exemption' | 'emergency_override' | 'partnered_intimacy_authorization'

    exemption_id               TEXT,              -- populated ONLY for reason='temporary_wear_exemption'
    authorization_decision_id  TEXT,              -- populated ONLY for reason='partnered_intimacy_authorization'
                                                    -- FK into the Activity Authorization module (activity_authorization_technical_design.md)

    -- The single source of truth for the maximum duration (Activity
    -- Authorization review, "session and maximum expiration need a
    -- single source of truth"). Populated ONLY when the reason carries a
    -- policy-driven cap (today only partnered_intimacy_authorization).
    -- Computed EXACTLY ONCE, at the moment this row is created
    -- (started_at + ActivityPolicy.maximum_unlock_duration), and is
    -- never independently recomputed in another module — Activity
    -- Authorization receives the same value via the domain_event payload
    -- (see activity_authorization_technical_design.md 8.4), not from its
    -- own calculation.
    expires_at                  TEXT,

    -- Distinguishes HOW the freeze was closed — needed by
    -- get_authorization_freeze_state() (2.5) to distinguish CLOSED
    -- (normal resume) from EXPIRED (automatic closure at expires_at).
    -- NULL as long as ended_at IS NULL (still open).
    end_reason                  TEXT,   -- 'resumed_normally' | 'expired' | NULL

    -- Consistency of the freeze reason's source (per review) — every
    -- reason must have exactly its corresponding FK populated and no
    -- other. emergency_override has no FK at all (see philosophy.md
    -- 3.10 — unconditional, with no link to any request under review).
    CHECK (
        (reason = 'temporary_wear_exemption'         AND exemption_id IS NOT NULL AND authorization_decision_id IS NULL) OR
        (reason = 'partnered_intimacy_authorization'  AND authorization_decision_id IS NOT NULL AND exemption_id IS NULL) OR
        (reason = 'emergency_override'                AND exemption_id IS NULL AND authorization_decision_id IS NULL)
    )

    -- NOTE: for one penalty_window_id, MULTIPLE rows with ended_at IS NULL
    -- may exist at the same time (concurrent freeze reasons, see Sections
    -- 2.3/2.4 and AA-FREEZE-1/PW-FREEZE-SET below). The window's status is
    -- FROZEN as long as at least one such row exists.
);

-- Partial unique index enforcing AA-FREEZE-1: for one penalty_window_id,
-- at most ONE open (ended_at IS NULL) freeze_period with
-- reason='partnered_intimacy_authorization' may exist. Does not affect
-- other reasons — those are governed only by the general PW-FREEZE-SET
-- (a set of reasons, with no count limit).
CREATE UNIQUE INDEX idx_freeze_periods_one_open_intimacy_auth
    ON freeze_periods (penalty_window_id)
    WHERE reason = 'partnered_intimacy_authorization' AND ended_at IS NULL;

-- Replaces the earlier `incidents` table entirely (System State Machine
-- Finding 1: that table duplicated, incompatibly, what
-- trust_manager_technical_design.md already owns in full -- Incident's
-- existence, confirmation, assessment, and description). The Penalty
-- Engine never stores those fields. It stores only the fact that a
-- specific, already-CONFIRMED Incident (owned entirely by the Trust
-- Manager) was consumed by a specific window, plus a denormalized
-- trust_domain snapshot needed for get_penalty_window_relevant_domains()
-- (2.6) without a cross-module join on every read -- the same
-- denormalization discipline already used elsewhere in this system
-- (e.g., recovery_credits_earned_hours on penalty_windows).
CREATE TABLE incident_consumption (
    incident_id         TEXT PRIMARY KEY,   -- FK to the Trust Manager's Incident.id; PRIMARY KEY enforces write-once (I11)
    penalty_window_id   TEXT NOT NULL REFERENCES penalty_windows(id),
    trust_domain        TEXT NOT NULL,       -- denormalized snapshot, read via get_incident_assessment()/get_confirmed_incidents_since() at consumption time -- never re-derived independently
    consumed_at          TEXT NOT NULL
);

CREATE TABLE trust_domains (
    domain_id       TEXT PRIMARY KEY,     -- 'routine' | 'hygiene' | 'lock' | ...
    display_name    TEXT NOT NULL,
    description     TEXT NOT NULL,        -- human-readable purpose of the domain
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    created_via_consent_id TEXT NOT NULL REFERENCES consent_log(id)  -- I10
);

CREATE TABLE trust_domain_scores (
    domain_id       TEXT NOT NULL REFERENCES trust_domains(domain_id),
    score           REAL NOT NULL,
    recent_trend    TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (domain_id)
);

CREATE TABLE overall_trust (
    id              INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row table, one value in the system
    score           REAL NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Domain events: append-only, see Section 4. Functions as the
-- TRANSACTIONAL OUTBOX for all cross-module communication across the
-- whole system (Penalty Window, Trust Manager, Activity Authorization,
-- future modules) — not just a "log," but the source of truth for what
-- is still awaiting delivery. A shared table, defined here once and used
-- across documents.
CREATE TABLE domain_events (
    id                   TEXT PRIMARY KEY,   -- UUID, also used for consumer idempotency
    created_at           TEXT NOT NULL,
    event_type           TEXT NOT NULL,
    source_module        TEXT NOT NULL,      -- 'penalty_engine' | 'trust_manager' | 'activity_authorization' | ...
    related_entity_type  TEXT NOT NULL,      -- 'penalty_window' | 'recovery_plan' | 'authorization_decision' | ...
    related_entity_id    TEXT NOT NULL,
    payload_json         TEXT NOT NULL DEFAULT '{}',

    -- Outbox pattern: a publisher process (separate from the transaction
    -- that wrote the row) finds rows with published_at IS NULL, delivers
    -- them to consumers (AT LEAST ONCE — not exactly-once, see below),
    -- and only AFTER successful delivery writes published_at.
    -- delivery_attempts is incremented on every attempt, for diagnostics
    -- and possible backoff. The database commit of the row (inside
    -- _apply_transition, I18) is a separate step from publication — so
    -- the commit never fails because of a broker/consumer outage.
    --
    -- IMPORTANT (per review): `published_at` means ONLY "the publisher
    -- successfully handed the event to the transport layer" — NEVER
    -- "the effect at the consumer definitely happened." A consumer may
    -- receive delivery more than once (retry, publisher restart between
    -- handing off to the broker and writing published_at) — so it MUST
    -- have its own persisted deduplication (domain_event_consumers,
    -- below), rather than relying on `published_at` implying "exactly
    -- once."
    published_at         TEXT,
    delivery_attempts    INTEGER NOT NULL DEFAULT 0,

    -- Claim mechanism for safe publishing even with multiple concurrent
    -- publisher processes (per review: "startup must be single-writer"
    -- only covers the reconciliation step at startup — a publisher, as
    -- an ongoing process, could in theory run more than once even
    -- outside startup, so claiming is an independent, additional
    -- safeguard). A database-agnostic equivalent of `SELECT ... FOR
    -- UPDATE SKIP LOCKED` — the publisher first "claims" a row
    -- (UPDATE ... WHERE claimed_at IS NULL OR claim_expires_at < now),
    -- and only then processes it. If the publisher crashes while holding
    -- a claim, the claim expires (claim_expires_at) and another (or the
    -- restarted) publisher can take it over.
    claimed_at            TEXT,
    claimed_by             TEXT,               -- identifier of the process/instance
    claim_expires_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_domain_events_unpublished ON domain_events (published_at) WHERE published_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_domain_events_claimable ON domain_events (claim_expires_at) WHERE published_at IS NULL;

CREATE TABLE domain_event_consumers (
    -- The consumer-side deduplication layer (a "consumer inbox") —
    -- independent of published_at (which describes only PUBLISHER
    -- success, not the consumer's; see the comment on domain_events
    -- above). Tracks which events a given consumer has ACTUALLY
    -- processed — enforces idempotency (I19) even under at-least-once
    -- delivery (a repeated publish of the same event never causes a
    -- second effect at the consumer).
    event_id        TEXT NOT NULL REFERENCES domain_events(id),
    consumer_name   TEXT NOT NULL,       -- 'recovery_plan' | 'activity_authorization' | ...
    processed_at    TEXT NOT NULL,
    PRIMARY KEY (event_id, consumer_name)
);
```

`recovery_credits_earned_hours` on `penalty_windows` is a deliberate
denormalization (the same pattern as `trust_history` in the existing
schema) — the source of truth is `recovery_credit_ledger`, but for
`remaining_active_hours()`, read on every interaction (see 4.4), summing
the ledger every time would be wasteful. It is updated within the same
transaction as the insert into the ledger.

### 3.4 Recovery Credit Integration (Applying `recovery_plan_technical_design.md` Section 6)

Previously proposed, not applied. Now applied, per your approval — the
same publish-then-owning-module-writes pattern used for the Goal
integration (`trust_manager_technical_design.md` Section 15): Recovery
Plan interprets whether a task was genuinely completed; the Penalty
Engine (this document), as the sole owner of `recovery_credit_ledger`,
decides independently how many hours that completion actually earns.

```python
def record_recovery_credit_from_task_completion(db: Database, completion_id: str, event: DomainEvent) -> RecoveryCreditDecision:
    """
    Consumes recovery_plan.task_completed (recovery_plan_technical_design.md
    Section 5). Reads the referenced RecoveryTaskCompletion via Recovery
    Plan's own narrow API, get_recovery_task_completion()
    (recovery_plan_technical_design.md 2.3) -- never a raw table read.

    Always writes a RecoveryCreditDecision (append-only) -- eligible or
    not, capped or not -- so a zero-hour outcome is never silently
    indistinguishable from "this completion was never processed" (the
    same discipline as ExtensionDecision, extension_technical_design.md
    2.2). Writes to recovery_credit_ledger ONLY when credited_hours > 0
    -- the ledger reflects genuine credit; the decision record is the
    complete audit trail regardless.
    """
    with db._connect() as conn:
        completion = recovery_plan.get_recovery_task_completion(conn, completion_id)   # recovery_plan_technical_design.md 2.3
        task = recovery_plan.get_recovery_task(conn, completion.recovery_task_id)       # for proposed_hours -- read-only, same boundary
        window = _load_penalty_window(conn, completion.penalty_window_id)

        remaining_capacity = window.recovery_credit_capacity_hours - window.recovery_credits_earned_hours   # I3-derived, read here, never recomputed independently
        credited_hours = min(task.credit_hours, max(0.0, remaining_capacity))
        capacity_limited = credited_hours < task.credit_hours

        decision = RecoveryCreditDecision(
            id=new_id(), created_at=utc_now(), completion_id=completion_id,
            penalty_window_id=window.id,
            proposed_hours=task.credit_hours, credited_hours=credited_hours,
            capacity_limited=capacity_limited,
            explanation=(
                f"Task completion processed; proposed {task.credit_hours}h"
                + (f", capped to {credited_hours}h by remaining Recovery Credit capacity." if capacity_limited else f", credited in full.")
            ),
        )
        _insert_recovery_credit_decision(conn, decision)   # UNIQUE(completion_id) enforced here -- I26, primary guarantee

        if credited_hours > 0:
            _insert_recovery_credit_ledger_entry(conn, window.id, credited_hours, source_completion_id=completion_id)   # partial UNIQUE(source_completion_id) -- I26, secondary reinforcement
            # recovery_credits_earned_hours denormalization updated in the same transaction, per the existing pattern above

        _write_event(conn, _recovery_credit_decision_event(decision))
        return decision
```

`RecoveryCreditDecision` (the Python-facing shape of the SQL table in
3.3):

```python
@dataclass(frozen=True)
class RecoveryCreditDecision:
    id: str
    created_at: datetime
    completion_id: str          # UNIQUE -- I26
    penalty_window_id: str

    proposed_hours: float
    credited_hours: float        # may be 0
    capacity_limited: bool
    explanation: str             # REQUIRED, non-empty regardless of credited_hours
```

---

## 4. Domain Events and Idempotency

### 4.1 Proposal: Generic Events Instead of a Module-Specific Enum

Events do not need to belong to a single enum. Proposal:

```python
@dataclass
class DomainEvent:
    id: str                          # UUID, the key used for idempotency
    created_at: datetime
    event_type: str                  # see the list below — a string, not a closed enum
    source_module: str                # 'penalty_engine', 'trust_manager', 'hygiene_access', ...
    related_entity_type: str          # 'penalty_window', 'recovery_plan', 'incident', ...
    related_entity_id: str
    payload: dict                     # free-form JSON, type specific to event_type
```

`event_type` is a string, not a `StrEnum` scoped to `penalty_engine` —
new modules (Recovery Plan, Trust Manager) can add their own event types
without touching `penalty_engine`. Validating which specific types are
allowed for a given `source_module` can live as a mapping on the
consumer side, rather than as a database constraint — this preserves
extensibility without losing control over what the runtime is allowed to
generate (see I10 for the analogous pattern used for trust domains).

### 4.2 Domain Events

> Canonical for the events listed as "owned here" below. For events
> owned by another document, this table only cross-references — see
> `docs/architecture/domain_events_catalog.md` for the full picture and
> the rule this follows ("one canonical definition per event, every
> other document references it, none redefine it").

**Owned here:**

| event_type | source_module | When It Occurs |
|---|---|---|
| `penalty_window.started` | penalty_engine | a new window is activated |
| `penalty_window.frozen` | penalty_engine | freeze (exemption or override) |
| `penalty_window.resumed` | penalty_engine | end of freeze |
| `penalty_window.extended` | penalty_engine | a new Incident extended the window (consumed alongside `extension.decision_recorded`, canonically defined in `extension_technical_design.md`) |
| `penalty_window.target_duration_changed` | penalty_engine | a derived event on `extended` — explicitly carries the new `target_active_hours` so the consumer (Recovery Plan) does not have to compute the capacity itself |
| `penalty_window.completed` | penalty_engine | the countdown reached zero, or manual termination |
| `freeze_periods.opened` | penalty_engine | any new open `freeze_periods` row, for ANY reason (`temporary_wear_exemption`, `emergency_override`, `partnered_intimacy_authorization`) — the single canonical low-level event; Activity Authorization's `activity_authorization.freeze_confirmed` (canonical: `activity_authorization_technical_design.md`) is a separate, downstream event it publishes itself after consuming this one, filtered to its own reason (resolved via `domain_events_catalog.md` Finding 2 — one publisher per event, no exceptions) |
| `freeze_periods.closed` | penalty_engine | any `freeze_periods` row closing, for any reason — same role for `.resume_confirmed`/`.freeze_expired`-style consumers as `.opened` plays above |
| `penalty_engine.freeze_expired` | penalty_engine | automatic closure of a freeze whose `expires_at` has passed (4.5) — a `freeze_periods.closed` occurrence specifically caused by expiry, not a user/AI-initiated resume; consumed by Activity Authorization |
| `emergency_override.triggered` | penalty_engine | emitted in the SAME transaction as `freeze_periods.opened(reason=emergency_override)`, by `emergency_freeze()` — a second, additional event for this one reason, so a consumer never has to inspect `freeze_periods.opened`'s payload just to notice an emergency happened. Single publisher (penalty_engine, since it owns `freeze_periods` regardless of which UI surface called `emergency_freeze()` — Discord command, a future physical button, etc.) — resolved via `domain_events_catalog.md` Finding 5; I16's zero-precondition guarantee is unaffected, since both events are written in the one transaction the write already required. |
| `recovery_credit_decision.recorded` | penalty_engine | any new `RecoveryCreditDecision` (3.4), eligible/capped/zero-hour alike — renamed from `recovery_engine.credit_decision_recorded` (there is no module named "recovery_engine" in this system; resolved via `domain_events_catalog.md` Finding 4) |

**Referenced, not redefined here** (canonical elsewhere):

| event_type | Canonical Definition | Relationship to This Document |
|---|---|---|
| `extension.decision_recorded` | `extension_technical_design.md` Section 5 | Written in the same transaction as `penalty_window.extended`/`.target_duration_changed` when applicable (Section 4 there) |
| `recovery_plan.created`/`.frozen`/`.resumed`/`.regenerated`/`.completed` | `recovery_plan_technical_design.md` Section 5 | Reactions to this document's `penalty_window.*` events — module name is `recovery_plan` (an earlier version of this table said `recovery_plan_generator` and omitted `.regenerated`; both fixed by removing the redefinition entirely — resolved via `domain_events_catalog.md` Finding 3) |
| `recovery_plan.task_completed` | `recovery_plan_technical_design.md` Section 5 | Consumed here (3.4) to run `record_recovery_credit_from_task_completion()` — not emitted by this module |
| `incident.confirmation_changed` | `trust_manager_technical_design.md` Section 8 | Consumed here, filtered to `new_confirmation=CONFIRMED`, to drive Incident consumption (I11/I12) and the Extension decision — resolved via `domain_events_catalog.md` Finding 1 |

### 4.3 Consumer Idempotency

```python
def handle_event(db: Database, consumer_name: str, event: DomainEvent, handler: Callable) -> None:
    with db._connect() as conn:
        already = conn.execute(
            "SELECT 1 FROM domain_event_consumers WHERE event_id = ? AND consumer_name = ?",
            (event.id, consumer_name),
        ).fetchone()
        if already:
            return  # I19 - this event was already processed, no second effect
        handler(conn, event)
        conn.execute(
            "INSERT INTO domain_event_consumers (event_id, consumer_name, processed_at) VALUES (?, ?, ?)",
            (event.id, consumer_name, iso(utc_now())),
        )
    # handler() and the INSERT into domain_event_consumers happen within the same transaction
    # -> if handler() fails, the "processed" record is rolled back too (the I18 pattern applied to consumers)
```

### 4.4 Completion Detection Without a Scheduler

Exactly as requested — `is_complete()` is not checked only on messages,
but as a **mandatory precondition** for operations that depend on the
window's state. Proposal for a single entry point:

```python
def ensure_current_state(db: Database, now: datetime) -> PenaltyWindow | None:
    """
    Called at the START of every operation that depends on the Penalty
    Window's state: spending a token, a hygiene request, a check-in, the
    !status command, a Recovery Plan change, evaluating a new Incident.

    If an active window is, in fact, complete, it atomically transitions
    it to COMPLETED (mutation + event, one transaction — see 3.1), and
    only then returns the current (possibly now None) state to the
    caller.
    """
    pw = db.get_active_or_frozen_penalty_window()
    if pw is None:
        return None
    if pw.status == PenaltyWindowStatus.ACTIVE and pw.is_complete(now):
        _apply_transition(db, pw.id, mutation=lambda conn: _mark_completed(conn, pw, now), event=_completed_event(pw))
        return None
    return pw
```

Calling code (`bot/discord_bot.py`, the future `token_ledger.py`,
`hygiene_access.py`, etc.) must call `ensure_current_state()` as its
first step, rather than checking the `status` column directly — this
prevents a situation where two different code paths hold an inconsistent
view of whether the window is still running.

### 4.5 Startup Reconciliation and Time Behavior Across an Outage (I24/RESTART-1)

**`ensure_current_state()` is also called explicitly at process
startup**, before accepting any new request — not only reactively on the
first interaction. This is necessary because the system runs on an
ordinary computer that is routinely turned off and on, and waiting for
"the first message after startup" would delay detecting window
completion by an indeterminate amount.

**Explicit rule for what counts toward downtime:**

```
ACTIVE Penalty Window:
  Downtime IS counted toward its countdown — active_hours_elapsed() is
  always derived from `now - active_period_started_at`, which includes
  any period during which the process was not running. The countdown is
  independent of whether anything was running — it is purely a function
  of two timestamps.

FROZEN Penalty Window:
  Downtime is NOT counted, because the countdown stops in exactly the
  same way it would stop during a freeze while the process is running —
  active_period_started_at is NULL, so there is no "elapsed time" to
  subtract.

A freeze with expires_at (partnered_intimacy_authorization, etc.):
  Expiration IS counted according to real (wall-clock) time, not the
  process's running time — if `expires_at <= now` at startup, the freeze
  must be closed immediately as part of reconciliation, not left waiting
  for the next regular check.
```

This follows from the model having been built from the start on absolute
timestamps rather than elapsed process runtime (3.1) — no special logic
for "downtime" is needed; `ensure_current_state()` called at startup
produces the same result as if it were called at any other time.

**Automatic closure of a freeze with an expired `expires_at`** (relevant
to `partnered_intimacy_authorization`; see the `freeze_periods.expires_at`
column above) is part of `ensure_current_state()`: if an open freeze with
`expires_at <= now` exists, the Penalty Engine closes it itself
(`ended_at = now`) and emits both `freeze_periods.closed` (the canonical,
generic closure event, 4.2) and `penalty_engine.freeze_expired` (payload:
`authorization_decision_id`, `freeze_period_id`) in the same transaction
— Activity Authorization consumes the latter to close the corresponding
session/decision (see `activity_authorization_technical_design.md`, the
crash recovery section). This applies regardless of whether the closure
was triggered during normal operation or during startup reconciliation
after an outage — the same code path, the same events.

### 4.6 Safe Outbox Claiming With Multiple Publishers (I23)

```python
CLAIM_DURATION = timedelta(seconds=30)   # parameter

def claim_unpublished_events(db: Database, publisher_id: str, now: datetime, limit: int = 50) -> list[DomainEvent]:
    """
    A database-agnostic equivalent of 'SELECT ... FOR UPDATE SKIP
    LOCKED'. Atomically claims up to `limit` rows that were either never
    claimed, or whose claim has already expired (another publisher
    crashed while holding an open claim):

        UPDATE domain_events
        SET claimed_at = :now, claimed_by = :publisher_id,
            claim_expires_at = :now + CLAIM_DURATION
        WHERE published_at IS NULL
          AND (claimed_at IS NULL OR claim_expires_at < :now)
        LIMIT :limit
        RETURNING *

    The publisher then delivers the claimed rows and, on success, writes
    published_at (a separate step, see the comment on domain_events).
    If the publisher crashes between claiming and writing published_at,
    the row remains claimed only until claim_expires_at — after that,
    anyone else may take it over.
    """
```

This mechanism is independent of `RESTART-LEASE-1` (the single-writer
lease for startup reconciliation, see
`activity_authorization_technical_design.md` Section 16) — the lease
protects against concurrent reconciliation at startup, while claiming
protects the publisher, as an ongoing process, against concurrently
processing the same row, even outside of startup. Both safeguards are
restart-safe (database-backed); neither relies on in-memory state.

---

## 5. Test Matrix

Legend: **Given** (initial state) → **When** (action) → **Then** (expected
result plus invariants to verify).

| # | Scenario | Given | When | Then |
|---|---|---|---|---|
| T1 | Completion of a window during inactivity | `ACTIVE`, `remaining_active_hours` = 0.5h, no interaction for hours | user sends any message after the countdown has elapsed | `ensure_current_state()` detects completion, atomically transitions to `COMPLETED`, emits `completed`, only then processes the message (I18, I19) |
| T2 | Extension just below the limit | `target_active_hours` = 330h | an Incident proposes a 5h extension | `extensions_hours` grows, `target_active_hours` = 335h, no capping (I5) |
| T3 | Extension above the limit | `target_active_hours` = 334h | an Incident proposes a 10h extension | `target_active_hours` caps at 336h (not 344h); the Incident is still `consumed` (I5, see 3.8 literally) |
| T4 | The final Recovery Credit completes the window | `remaining_active_hours(now)` = exactly 1h | user completes an activity = +1h credit | the ledger write causes `remaining_active_hours` = 0 → on the next `ensure_current_state()` the window transitions to `COMPLETED` (I4, I18) |
| T5 | Freeze and Resume from ACTIVE | `ACTIVE` | approved exemption → `freeze()` → later `resume()` | `accumulated_active_hours` correctly captures the active time before the freeze; after resume, the countdown continues from there, not from zero (I6) |
| T6 | Freeze and Resume while FROZEN (a second freeze reason on an already-frozen window) | `FROZEN` (open `FreezePeriod` from an exemption) | `freeze(reason=X)` with a different/same reason | a second `INSERT` into `freeze_periods` with `ended_at=NULL` is added; `status` remains `FROZEN`; `accumulated_active_hours` is NOT changed a second time (the countdown was already stopped) — see 2.3 |
| T7 | Emergency Override during ACTIVE | `ACTIVE` | `emergency_freeze()` | immediate transition to `FROZEN` (first open `FreezePeriod`), `reason='emergency_override'`, an `emergency_override.triggered` event, NO new Incident, NO Trust impact (I13, I17) |
| T8 | Emergency Override while already FROZEN (e.g., already frozen due to an exemption) | `FROZEN` (open `FreezePeriod` from an exemption) | `emergency_freeze()` | a second open `FreezePeriod` with `reason='emergency_override'` is added; the window stays `FROZEN`; `resume()` to `ACTIVE` occurs only once BOTH have closed (the last one to disappear) — a direct consequence of the "set of active reasons" model (2.3), no special priority rule needed |
| T9 | Repeated delivery of the same event | a `recovery_plan.created` event already processed by the consumer | the same `event.id` delivered again (retry, restart) | the `domain_event_consumers` record exists → the handler is not called a second time, no duplicate Recovery Plan (I19) |
| T10 | A crash between the state change and the audit write | the `_apply_transition` transaction is running | a simulated crash/exception between `mutation()` and `_write_event()` | the SQLite transaction rolls back in full — `penalty_windows.status` remains in its ORIGINAL state, no orphaned event (I18 — verified by a test that raises an exception mid-way and checks the rollback) |
| T11 | An Incident during ACTIVE | `ACTIVE` | a new, `CONFIRMED` Incident arrives (via the Trust Manager's `incident.confirmation_changed` event, filtered to `new_confirmation=CONFIRMED` — see `domain_events_catalog.md` Finding 1) | a row is inserted into `incident_consumption` for the current window; if `should_extend()`, an extension too (I11, I12) |
| T12 | An Incident during FROZEN | `FROZEN` | a new Incident is registered (a genuine violation independent of the freeze reason) | same as T11 — FROZEN blocks only the countdown, not Incident consumption (I12, 3.8) |
| T13 | An attempt to reuse a consumed Incident | an Incident with a row already present in `incident_consumption` | an attempt to include it in a new window | rejected at the `get_confirmed_incidents_since()`-filtered-by-`incident_consumption` query level (I12); bonus test: a direct attempt at a second `INSERT` for the same `incident_id` fails on the `PRIMARY KEY` constraint (I11, write-once) |
| T14 | An attempt to start a new window using an old Incident after the maximum has been reached | window A closed at 336h with old Incident I already present in `incident_consumption` | an attempt to create window B with Incident I | rejected — `incident_consumption` already has a row for `I.id` (I12) |
| T15 | Mandatory Hygiene Access with the Discretionary limit exhausted | `weekly_usage.count >= limit` for Discretionary | a `MANDATORY`-type request | `allowed=True` unconditionally, `counts_against_weekly_limit=False`, does not touch the Discretionary counter (I14) |
| T16 | Frequent Mandatory requests without an automatic Trust decrease | 10 `MANDATORY` requests already today | the 11th request | `allowed=True`; no write to `trust_domain_scores`, `incidents`, or `penalty_windows`; at most an observation with `flagged_for_review` for the Coach (I15) |
| T17 | A Temporary Wear Exemption during a Penalty Window | `ACTIVE` | an exemption request that would also be approved outside a Penalty Window | approved identically to outside a Penalty Window (I7); followed by `freeze()` |
| T18 | Spending tokens during a Penalty Window | `ACTIVE` or `FROZEN`, a token available in the account | an attempt to spend a token | rejected (I2) |
| T19 | Earning tokens during a Penalty Window | `ACTIVE` | user completes an activity that grants a token | the token is credited normally, no `penalty_window.status` check on the credit path (I2) |
| T20 | Completion of the window and resumed token spending | window just transitioned to `COMPLETED` (via the T1 flow) | an attempt to spend a token | allowed — `get_active_or_frozen_penalty_window()` now returns nothing (I2) |
| T21 | Overall Trust is not affected by an isolated domain event | `TrustDomain('hygiene')` drops after an Incident | check `TrustDomain('routine')` and `overall_trust` | both unchanged within the same operation (I8, I9) |
| T22 | An attempt by the runtime to create a new Trust domain | — | a hypothetical call that would write to `trust_domains` without `created_via_consent_id` | must fail both at the schema level (`NOT NULL REFERENCES consent_log`) and at the access-layer level (I10) |
| T23 | Recovery credit is capped independent of the proposed value | `RecoveryTask.credit_hours` exceeds the window's remaining `recovery_credit_capacity_hours` | `record_recovery_credit_from_task_completion()` runs | `credited_hours` is capped at the remaining capacity, `capacity_limited=True`, `proposed_hours` still reflects the original, uncapped value (I26, mirrors Extension's capacity cap) |
| T24 | A zero-hour outcome is still auditable | remaining capacity is already 0 | `record_recovery_credit_from_task_completion()` runs | a `RecoveryCreditDecision` is still written (`credited_hours=0`, `capacity_limited=True`, non-empty `explanation`); no `recovery_credit_ledger` row is created, since `credited_hours` is not `> 0` |
| T25 | A completion is credited at most once | a `RecoveryTaskCompletion` already processed into a `RecoveryCreditDecision` | `record_recovery_credit_from_task_completion()` is called again for the same `completion_id` (e.g., a redelivered event with a different `event.id`) | rejected by `UNIQUE(completion_id)` on `recovery_credit_decisions` (I26) — this holds even if the outbox consumer-side dedup (I19) is somehow bypassed, since it is a second, independent layer of protection |

The "set of active freeze reasons" model (2.3) replaced the earlier
proposal from the previous review round ("a second concurrent
FreezePeriod with priority on resume") — the result is the same (both
must be closed before the window returns to ACTIVE), but without needing
a special priority rule.

---

## 6. Status of Open Questions

All three issues from the previous review round have been resolved:

1. **Concurrent Freeze** — resolved using the "set of active reasons"
   model (2.3). No dedicated state machine for combinations is needed.
2. **`terminate()`** — deferred beyond the first implementation (2.1).
   The data model (`resolution_method` column) is ready; the function
   itself does not yet exist.
3. **`should_extend()`/Extension decision logic** — resolved
   architecturally and specified in full in
   `extension_technical_design.md`. The Penalty Engine (this document)
   owns application of an approved Extension —
   `extensions_hours += assigned_hours`, within the same transaction as
   incident consumption (I11/I12) and the existing `EXTENDED`/
   `INCIDENT_CONSUMED` events. Eligibility, magnitude, mitigation,
   capacity limiting, the resulting explanation, and their invariants
   (EXT-1 through EXT-9) are defined exclusively in that document, not
   duplicated here.

Sections 1–4 of this document (invariants, state machine, persistence,
events) are, after these revisions, considered complete and ready for
approval. The test matrix (Section 5) has been updated to reflect the new
freeze model (T6, T8) and is otherwise unchanged.
