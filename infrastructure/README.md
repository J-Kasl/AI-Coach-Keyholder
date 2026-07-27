# infrastructure

Shared, cross-cutting components used by every domain module. Never a
domain module itself — this package owns no business state and makes
no business decisions (`implementation_conventions.md` Section 2: every
stateful table has exactly one *domain* owner; this package is where
the plumbing those domains share actually lives).

## Why `Clock` exists

Across the architecture baseline, several documents independently
arrived at the same requirement: no timeout, deadline, or expiry in
this system may be tracked by an in-memory timer or read from an
uncoordinated, directly-called system clock.

- `penalty_window_technical_design.md` 4.5/2.8 (I24/RESTART-1): "No
  important timeout may exist solely as an in-memory timer... all are
  stored as absolute UTC timestamps."
- `activity_authorization_technical_design.md` 16.7 establishes the
  `Clock` protocol itself, plus a future `MonotonicGuardedClock` that
  will guard against the system clock jumping backward after a
  restart.
- `implementation_conventions.md` Section 8 (Naming Conventions) and
  Section 9 (Crash/Restart Recovery Conventions) both assume every
  `recover_<module>_state(db, now)` function receives its `now` from
  an injected `Clock`, never from calling `datetime.now()` itself.

The reason this is infrastructure, not a detail left to each module: if
every module called `datetime.now(timezone.utc)` on its own, testing
any restart/expiry scenario deterministically would require either
real elapsed wall-clock time in tests (slow, flaky) or mocking a global
function per test (easy to do inconsistently across modules). A single
injected `Clock`, used everywhere, means every module's tests — and,
later, a system-wide `MonotonicGuardedClock` — are written once, the
same way, here.

**Important clarification of scope** (per architectural review): `Clock`
is not a mechanism for computing "time until unlock" — this system does
not decide unlocking by counting down a Chaster-side timer. `Clock` is
the uniform source of time for this system's own internal events:
Incident creation, Penalty Window start/end, cooldowns, authorization
validity windows, history/audit timestamps, the moment a request or
decision is made, and event ordering. The decision to unlock is made
over domain state; Chaster (or any future physical-lock integration) is
a downstream technical integration layer, not the source of that
decision.

### `Clock`, `SystemClock`, `FrozenClock`

- `Clock` — the `Protocol` every module depends on.
- `SystemClock` — the production implementation (wraps
  `datetime.now(timezone.utc)`). Does NOT guarantee monotonicity — see
  its docstring; that guarantee is the explicit job of a future
  `MonotonicGuardedClock`, not this class.
- `FrozenClock` — the test implementation: starts at an explicit time,
  and only changes when a test calls `advance()` (relative) or `set()`
  (absolute jump, in either direction — deliberate, see its docstring).

A repository-wide guard test
(`test_no_direct_datetime_now_calls_outside_clock_module`,
`tests/infrastructure/test_clock.py`) enforces "production code never
calls `datetime.now()`/`datetime.utcnow()` directly" mechanically, not
just as a written rule. As of Phase 1.2, this guard has **zero**
production exceptions — `KNOWN_PRE_CLOCK_VIOLATIONS` is empty (see
"What changed in Phase 0" below).

## Why `infrastructure.database` exists

`Database`/`Transaction`/`apply_transition()` (`infrastructure/database.py`)
are the single, shared way every module opens an atomic unit of work
against SQLite — `implementation_conventions.md` Section 4 (`_apply_transition`)
and Section 2 (single-writer ownership) made concrete in actual code,
not just as a documented pattern each module was expected to reinvent.

**Who owns the transactional boundary:** `infrastructure.database.Database`
alone. It owns connection creation, pragma configuration
(`foreign_keys`, WAL journal mode, `busy_timeout` — set deliberately,
not left to SQLite's defaults), and the single `transaction()` context
manager that commits on success and rolls back on any exception. No
other class in this codebase opens a `sqlite3.connect()` call directly.

**Why repository methods never call `commit()`/`rollback()` themselves:**
`Transaction` (the object yielded by `transaction()`) exposes only
`execute()`/`executemany()`/`fetch_one()`/`fetch_all()` — deliberately
narrow and technical, per architectural review: it must never grow
domain-shaped methods like `insert_incident()` or `record_trust_history()`,
which belong in a repository (`database/database.py`), not in the
transactional primitive itself. Committing is a decision `Database.transaction()`
makes exactly once, when its `with` block exits without an exception —
letting an individual repository method commit independently would
make it impossible to compose two repository operations into one
atomic unit, which is the entire reason this class exists.

**How the next phase (transactional outbox) will slot in without
changing existing call sites:** `apply_transition()`'s `events=`
parameter is already present and already unused by every current call
site. When `domain_events` is introduced, a call site that needs to
also write an event passes `events=some_callable`; every call site that
doesn't need one is completely unaffected. No existing signature or
call site changes when that phase begins.

## Why `infrastructure.outbox` exists (Phase 1.4)

`DomainEvent`/`write_event`/`claim_pending_events`/`mark_published`/
`has_been_processed`/`mark_processed`/`consume_event`
(`infrastructure/outbox.py`) are the shared transactional outbox
`implementation_conventions.md` Section 5 describes, and the first
real consumer of the `events=` slot `apply_transition()` has carried
since Phase 1.2.

**Schema versus behavior, the same split as `infrastructure/database.py`:**
the `domain_events`/`domain_event_consumers` tables are defined in
`database/migrations/002_domain_events.sql` — a project migration, not
infrastructure code — while every behavior that reads or writes them
lives here, domain-agnostic. `infrastructure/outbox.py` never imports
from `database/`; test-only setup convenience (a fully migrated schema)
is the only place the two meet, in `tests/infrastructure/test_outbox.py`.

**Write side:** `write_event(tx, event)` is called from inside an
already-open `Transaction`, exactly the shape `events=` expects — the
event and the state change that caused it commit or roll back
together, never independently. A fresh event id is generated on every
call; there is no write-side idempotency concern, since a rolled-back
transaction attempt writes nothing to roll back.

**Publish side:** `claim_pending_events()`/`mark_published()` are the
claim/publish half of the outbox — a publisher claims a batch of
unclaimed-or-lease-expired rows (one atomic `BEGIN IMMEDIATE`
transaction, so two concurrent publisher processes can never claim the
same row), delivers them, then marks them published. At-least-once
delivery is the explicit contract, not an oversight — see
`consume_event` below for what makes that safe.

**Consume side:** `has_been_processed()`/`mark_processed()` are the
consumer-side dedup guard (`domain_event_consumers`,
`UNIQUE(event_id, consumer_name)` via its primary key). `consume_event()`
is the consumption counterpart to `apply_transition()` — built directly
on top of it (dedup-check via `load`, the consumer's own reaction plus
`mark_processed()` via `write`), not a parallel reimplementation of
transaction handling. Returns `True` if the handler actually ran,
`False` if the delivery was a harmless, silently-absorbed redelivery.

**No real domain module exists yet to wire this into** (Trust Manager,
Penalty Engine, etc. — `docs/architecture/domain_events_catalog.md` —
are still architecture, not code). The one real, honestly-labeled
demonstration is `database/database.py`'s
`record_rule_change_with_consent()`, which now also emits a
`consent_log.rule_change_recorded` event via the `events=` slot — this
is Phase 0 demo wiring, not a catalog event, and is documented as such
at its call site. The outbox itself is fully implemented and tested
(`tests/infrastructure/test_outbox.py`) independent of that one
example, ready for the first real domain module's events when it
arrives.

**Finding 6 (`docs/architecture/domain_events_catalog.md` Section 9),
resolved:** every event type, without exception, goes through this one
outbox — no per-event-type carve-out for "this one doesn't need
delivery machinery." Simplicity (one mechanism, no special cases) won
over minimalism (skip the outbox for today's zero-consumer events).

**Why migrations use `raw_connection()`, not `transaction()`:**
`sqlite3.Connection.executescript()` (used to apply a `.sql` migration
file) implicitly commits any pending transaction before running and
does not participate in manual `BEGIN`/`COMMIT`/`ROLLBACK` control the
same way `execute()` does. Wrapping it in `transaction()`'s `BEGIN
IMMEDIATE` would misrepresent the atomicity actually available — this
is a genuine SQLite/Python constraint, not a limitation introduced by
this wrapper. `raw_connection()` is a deliberately separate, clearly
documented escape hatch for exactly this case; domain writes must never
use it. Confirmed by a real test
(`tests/infrastructure/test_database.py::TestRawConnection::test_raw_connection_contends_with_an_open_transaction_on_the_same_file`):
using it concurrently with an open `transaction()` on the same database
file raises `sqlite3.OperationalError` once `busy_timeout` elapses,
rather than silently succeeding.

**Nested transactions are explicitly forbidden**, not given SAVEPOINT-based
semantics: calling `transaction()` while one is already open on the same
`Database` instance raises `NestedTransactionError`. A domain operation
needing several steps to be atomic together must be one
`transaction()`/`apply_transition()` call, not two nested ones — nesting
would silently open a second, independent SQLite connection while the
first is still open and uncommitted.

**`:memory:` is explicitly rejected** in `Database.__init__` — this
wrapper opens a new connection per `transaction()` call, and SQLite's
`:memory:` database is unique per connection unless shared-cache mode
is enabled (which this wrapper does not do). Tests use a real temporary
file (pytest's `tmp_path` fixture), never `:memory:`.

**Thread-safety is explicitly out of scope for now**: one `Database`
instance is not safe for concurrent `transaction()` calls from multiple
threads (the open-transaction guard is a plain instance attribute, not
a lock). This system's current scope (a single-process Discord bot)
does not need cross-thread sharing of one instance. Cross-*process*
concurrency is a separate, already-handled concern: `BEGIN IMMEDIATE`
correctly serializes concurrent writers across OS processes at the
SQLite engine level regardless of this class's thread-safety scope.

## What changed in Phase 0 (Phase 1.2)

Per architectural review, `field(default_factory=utc_now)` was not
merely "calling a global function" — the deeper problem was that a
*model* was deciding the time of its own creation. Fixed by removing
`utc_now()` entirely (`database/models.py`) and making `created_at` a
required, keyword-only constructor parameter on every affected
dataclass (`ContextSnapshot`, `CoachAssessment`, `KeyholderAssessment`,
`DecisionResult`, `ObservationRecord`, `Rule`, `ConsentRecord`,
`ConversationMessage`). There is now exactly one path by which any of
these objects can receive a timestamp: the caller passes
`created_at=clock.now()` explicitly, having obtained `clock` via
dependency injection. Neither the model, the repository
(`database/database.py`), nor the transactional core
(`infrastructure/database.py`) generates a timestamp on its own.

`database/backup.py`'s two `datetime.now()` call sites were migrated
the same way — every time-dependent function (`create_backup()`,
`has_backup_today()`, `ensure_daily_backup()`) now takes `now: datetime`
explicitly. `database/database.py`'s `record_trust_history()` and
`mark_observation_reviewed()` (which called `utc_now()` indirectly, not
`datetime.now()` directly — the AST guard did not previously catch
these, since it was watching for the wrong call, not the wrong
*pattern*) now take `now: datetime` explicitly as well.

`bot/discord_bot.py` now constructs and owns a `SystemClock`, passed to
`build_bot()`/`CoachKeyholderBot`, and supplies `created_at=self.clock.now()`
at both `ConversationMessage(...)` construction sites.

`KNOWN_PRE_CLOCK_VIOLATIONS` (`tests/infrastructure/test_clock.py`) is
now empty — kept as a named, empty placeholder (not deleted) so the
pattern stays visible for any future, deliberately-documented exception.

## What this phase deliberately does NOT deliver

- ~~**The transactional outbox (`domain_events`)**~~ — **delivered in
  Phase 1.4** (`infrastructure/outbox.py`).
- ~~**A standalone consumer framework/registry**~~ — **delivered in
  Phase 2.4** (`infrastructure/consumer_registry.py`:
  `ConsumerRegistry`/`process_pending_events()`).
- ~~**The startup orchestrator**~~ — **delivered in Phase 2.4**
  (`system/startup.py`'s `on_system_startup()`; the restart-safe lease
  itself is `infrastructure/startup_lease.py`). See `system/README.md`
  for a real architectural finding this integration surfaced
  (cross-module `NestedTransactionError` and its resolution).
- **`MonotonicGuardedClock`** (`activity_authorization_technical_design.md`
  16.7) — now that the database wrapper exists, this is unblocked, but
  still not implemented here. It will implement the same `Clock`
  protocol, so introducing it means changing what gets injected at the
  composition root — no call site anywhere else needs to change.
- **Verification/Chaster integration policy** (`VerificationRequirement`/
  `VerificationContext`/`VerificationGateway`) — a separate, later
  architectural concern for the `integrations/` layer. Nothing in this
  phase's `Clock`/`Database`/`Transaction`/`Outbox` design assumes
  anything about it, so no conflict exists either way.

## Usage

```python
from infrastructure.clock import Clock, SystemClock, FrozenClock
from infrastructure.database import Database, apply_transition

# Production composition root:
clock: Clock = SystemClock()
core_db = Database("data/coach_keyholder.db")

# A repository method:
def save_something(core_db: Database, thing: Something) -> str:
    def write(tx, _state):
        tx.execute("INSERT INTO things (id, created_at) VALUES (?, ?)", (thing.id, iso(thing.created_at)))
    apply_transition(core_db, write=write)
    return thing.id

# Tests:
from datetime import datetime, timedelta, timezone

def test_something_expires_after_thirty_minutes() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    grant_expires_at = clock.advance(timedelta(minutes=30))
    # ... construct the thing under test using clock.now() / grant_expires_at ...
    clock.advance(timedelta(minutes=1))  # now past expiry
    # ... assert the expected expiry behavior, deterministically, with no real waiting ...
```

Any function or class that needs the current time takes a `Clock` (or
an already-computed `datetime` obtained from one) as a parameter — never
imports `datetime` and calls `datetime.now()`/`datetime.utcnow()`
itself. Any function that changes state takes a `Database`/`Transaction`
and goes through `transaction()`/`apply_transition()` — never opens its
own `sqlite3.connect()`.
