# AI Coach & Keyholder

A personal, local AI system combining a long-term coach with a
consistency/accountability system. See [`philosophy.md`](philosophy.md)
for the principles every decision and every future change must satisfy,
and [`docs/architecture/`](docs/architecture/) for the full
architecture baseline (nine technical designs + an integration audit +
implementation conventions -- all with status *Architecture baseline --
approved for implementation*).

## Architecture at a glance

The system is event-driven: domain modules never call each other's
internals directly. Each module owns its own state, publishes domain
events when that state changes, and reacts to other modules' events
through narrow, named consumer handlers -- never by reaching into
another module's tables or opening a second database transaction
mid-reaction. See `implementation_conventions.md` Section 3 and
`system/README.md` for the precise rules this follows, including a real
constraint (`NestedTransactionError`) discovered while wiring the first
cross-module consumer, and how it shaped every consumer written since.

The current, fully implemented lifecycle:

```
Incident (Trust Manager)
    │  confirmed
    ▼
Trust Score (recalculation pipeline)
    │  incident.confirmation_changed
    ▼
Penalty Window (Penalty Engine)
    │  should_extend() -- Extension
    ▼
Recovery Plan
    │  task completed
    ▼
Recovery Credit (back into Penalty Engine)
```

Three domain modules (Trust Manager, Penalty Engine, Recovery Plan) and
one composition layer (`system/`) exist today, fully wired together
through real, tested events -- not mocks. Each module's own README
documents exactly what it covers, what's deliberately deferred, and any
real architectural findings surfaced while building it:

- [`trust_manager/README.md`](trust_manager/README.md) -- Incident
  lifecycle, confirmation, severity assessment, the trust score
  recalculation pipeline.
- [`penalty_engine/README.md`](penalty_engine/README.md) -- the
  PenaltyWindow state machine, freeze-as-a-set-of-reasons, Extension
  (`should_extend()`), and Recovery Credit.
- [`recovery_plan/README.md`](recovery_plan/README.md) -- the Recovery
  Plan lifecycle as a pure reaction to Penalty Window events, plus
  Coach-facing task management.
- [`system/README.md`](system/README.md) -- the startup orchestrator,
  the consumer/dispatch framework, and the composition-layer wiring
  between all of the above.
- [`infrastructure/README.md`](infrastructure/README.md) -- the shared
  `Clock`, `Database`/`Transaction`, and transactional outbox that
  every module above is built on.

## Project status

**288 passing tests** across the whole repository (`pytest`), including
a repository-wide guard test that mechanically confirms no production
code outside `infrastructure/clock.py` calls `datetime.now()`/
`datetime.utcnow()` directly.

Nine sequential database migrations are applied so far
(`database/migrations/001` through `009`), covering the initial Phase 0
schema, the transactional outbox, Trust Manager, the trust
recalculation pipeline, Penalty Engine, the startup lease, Extension,
Recovery Plan, and Recovery Credit. See
[`database/migrations/README.md`](database/migrations/README.md) for
the hard rule migrations must follow (never destructive to user data).

### Implementation order so far

1. ~~`infrastructure/clock.py` -- `Clock`, `SystemClock`, `FrozenClock`~~ **done**
2. ~~Database wrapper (the transactional `apply_transition` helper)~~ **done**
3. ~~`domain_events` schema + transactional outbox (claim/publish)~~ **done (Phase 1.4)**
4. ~~Consumer framework (dispatch by `event_type`)~~ **done (Phase 2.4)**
5. ~~Startup orchestrator (`on_system_startup()`, `system_startup_lease`)~~ **done (Phase 2.4)**
6. ~~Trust Manager Slice 1 (Domain Registry, Incident, Confirmation, Severity)~~ **done (Phase 2.1)**
7. ~~Trust Manager Slice 2 (score recalculation pipeline)~~ **done (Phase 2.2)**
8. ~~Penalty Engine Slice 1 (state machine, freeze-as-set-of-reasons, natural completion)~~ **done (Phase 2.3)**
9. ~~Extension (`should_extend()`, the unified consumption path)~~ **done (Phase 2.5)**
10. ~~Recovery Plan (lifecycle as a reaction to Penalty Window events)~~ **done (Phase 2.6)**
11. ~~Recovery Credit integration (Penalty Engine consumes `recovery_plan.task_completed`)~~ **done (Phase 2.7)**

**Next up:** Goal Manager, the first module independent of the Trust
Manager -> Penalty Engine -> Recovery Plan -> Recovery Credit branch
above, which is now a complete, closed lifecycle.

### Phase 0 foundation (unchanged since the original design)

- Directory structure (`core/`, `ai/`, `database/`, `bot/`,
  `integrations/`, `observations/`).
- `database/models.py` -- the original dataclass contracts
  (ContextSnapshot, CoachAssessment, KeyholderAssessment,
  DecisionResult, ObservationRecord, Rule, ConsentRecord, ...).
- `database/migrations/001_initial_schema.sql` -- the SQLite schema
  (hybrid: normalized fields + JSON, rule versioning, consent_log,
  observations).
- `database/database.py` -- the access layer (migrations + save/get for
  every entity), later rebuilt in Phase 1.2 to compose on top of
  `infrastructure.database.Database` rather than managing its own
  connections.
- `core/config.py` -- loading configuration and secrets from `.env`.
- `database/backup.py` -- backups via SQLite's online backup API, at
  most 1 automatic daily backup, always a backup before applying a
  migration (if the DB already had some schema version), simple
  rotation (default: the 14 most recent backups,
  `BACKUP_RETENTION_COUNT` in `.env`).
- `bot/discord_bot.py` -- a basic Discord bot: connects, logs messages
  to short-term memory, **no AI logic yet** (arrives in Phase 1+).

All of the above was tested end-to-end (round-trip save/load for every
entity, bot import and initialization, backup scenarios including the
daily limit, pre-migration backups, and rotation).

**Data and backup policy (verified):**
- User data (`data/coach_keyholder.db`, `data/backups/`) is outside git
  (`.gitignore`) -- updating the application (`git pull`) never touches
  it.
- Migrations are exclusively additive (see
  `database/migrations/README.md`).
- The runtime (bot, future core engines) never reads from
  `observations/` or from audit exports -- that's a write-only layer
  from the runtime's perspective.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

For development and running tests:

```bash
pip install -r requirements-dev.txt
pytest
```

Requires Python 3.13 (the code uses `enum.StrEnum` and modern typing --
it would work on 3.11+, but 3.13 is the agreed target).

## Configuration

```bash
copy .env.example .env
```

Fill in at least `DISCORD_TOKEN` in `.env` (Discord Developer Portal ->
your application -> Bot -> Token). The bot needs to be invited to a
server with permission to read and send messages, and **Message
Content Intent** needs to be enabled in the Developer Portal (without
it the bot won't see message content -- `discord_bot.py` relies on it).

## Running

```bash
python -m bot.discord_bot
```

On first run, `data/coach_keyholder.db` is created automatically and
migrations are applied. For now the bot only logs messages and replies
with an acknowledgement -- this verifies the communication layer, not
the AI logic.

## Structure

```
core/            # coach_engine, keyholder_engine, decision_engine, config (business logic -- Phase 1+)
ai/              # ollama_client, personality, analysis (Phase 1+)
database/        # models.py, database.py, migrations/
infrastructure/  # shared cross-cutting layer (Clock, Database, Outbox,
                 # Consumer Registry, Startup Lease)
trust_manager/   # first domain module (Slice 1+2 -- see trust_manager/README.md)
penalty_engine/  # second domain module (Slice 1 + Extension + Recovery Credit -- see penalty_engine/README.md)
recovery_plan/   # third domain module (see recovery_plan/README.md)
system/          # composition layer: startup orchestrator, cross-module wiring (see system/README.md)
docs/architecture/  # architecture baseline: system_state_machine.md,
                     # seven domain technical designs, implementation_conventions.md,
                     # domain_events_catalog.md
bot/             # discord_bot.py, approval_flow.py (Phase 6)
integrations/    # chaster.py, apple_health.py (Phase 7)
observations/    # audit export (write-only from the runtime's perspective -- Phase 3+)
tests/           # pytest, structure mirrors the packages (tests/infrastructure/, tests/trust_manager/, ...)
philosophy.md    # the project's reference principles -- read this first
```

## Development history

Each phase below is summarized briefly; the module-level READMEs
linked above are the authoritative, detailed record of what each one
covers, what's deferred, and any real findings.

- **Phase 0** -- foundational schema, config, backups, a Discord bot
  skeleton with no AI logic yet.
- **Phase 1.1** -- `infrastructure/clock.py` (`Clock`/`SystemClock`/`FrozenClock`)
  and the repository-wide guard against direct `datetime.now()` calls.
- **Phase 1.2** -- the shared `Database`/`Transaction`/`apply_transition()`
  transactional core, with `database/database.py` rebuilt on top of it.
- **Phase 1.3** -- the architecture baseline (`philosophy.md` v1.12.1
  and the nine architecture documents) moved physically into the
  repository under `docs/architecture/`, plus a consolidated
  `domain_events_catalog.md` with five real cross-document
  inconsistencies found and documented (not yet resolved at the time).
- **Phase 1.4** -- the transactional outbox
  (`infrastructure/outbox.py`, `domain_events` schema): write, claim,
  publish, and consumer-side dedup, all built on `apply_transition()`.
- **Phase 2.1-2.2** -- Trust Manager: Incident registration and
  confirmation, deterministic severity assessment, and the trust score
  recalculation pipeline (evidence -> score, with a bounded
  per-recalculation delta and diminishing-returns confidence).
- **Phase 2.3** -- Penalty Engine: the PenaltyWindow state machine
  (start/freeze/resume/complete), freeze modeled as a set of
  concurrently active reasons rather than a single flag.
- **Phase 2.4** -- the Consumer Framework and Startup Orchestrator
  (`infrastructure/consumer_registry.py`, `system/startup.py`), and the
  first real, working cross-module event subscription (Trust Manager ->
  Penalty Engine). This is where a genuine architectural constraint
  (`NestedTransactionError`, arising when two modules share one
  database core's single-open-transaction guard) was discovered and
  resolved by making event payloads carry everything a consumer needs,
  rather than letting a handler call back into another module's public
  API mid-transaction. See `system/README.md` for the full account --
  this became a standing rule applied to every event added since.
- **Phase 2.5** -- Extension (`should_extend()`), unifying
  window-starting and window-extending into one consumption path, with
  every TBD parameter from the architecture document flagged explicitly
  as this implementation's own default rather than an architectural
  decision.
- **Phase 2.6** -- Recovery Plan, reacting purely to Penalty Engine's
  own events. Wiring it as a second downstream consumer surfaced a
  second real finding: `process_pending_events()` originally processed
  only one batch per call, so an event cascade (one handler's side
  effect triggering another) wouldn't fully propagate within a single
  `on_system_startup()` call. Fixed by draining the cascade in a loop,
  bounded by `max_cascade_rounds` as a safety limit.
- **Phase 2.7** -- Recovery Credit integration: Penalty Engine consumes
  `recovery_plan.task_completed` and decides, independently, how many
  hours a completed task actually earns against the window's capacity.
  This closes the full Incident -> Assessment -> Penalty -> Recovery ->
  Credit -> Penalty Adjustment lifecycle described in
  `penalty_window_technical_design.md` Section 3.4.
