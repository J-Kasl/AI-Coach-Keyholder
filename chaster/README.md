# Chaster

Canonical design: `docs/architecture/chaster_integration_technical_design.md`
(**`Draft for review`** — HTTP callback architecture and the
encryption/key-management architecture are approved at the
architecture level; this README describes exactly which slice,
**CHASTER-01A: OAuth Connection Foundation**, has actually been
implemented here).

## Epistemic invariant — read this first

> **Chaster-reported state, once CHASTER-01B exists, will be
> `PROVIDER_REPORTED` state — never `VERIFIED`.**

Chaster's API can confirm what its own system was told, never whether
a lock is *physically* secured. This slice (CHASTER-01A) doesn't even
reach that point yet — it only establishes a per-user OAuth
connection. No lock status is fetched, stored, or exposed anywhere in
this module.

## What is implemented here — CHASTER-01A (OAuth Connection Foundation)

- **`models.py`** — `ChasterOAuthState`, `ChasterConnection`,
  `ConnectionStatus` (exactly two values: `active` /
  `needs_reauthorization` — every other conceptual state is
  represented structurally: row absence = not connected/disconnected,
  a `chaster_oauth_states` row with no matching `chaster_connections`
  row = authorization in progress, a transparent refresh attempt =
  not a stored state at all). `ChasterConnection.__repr__` is
  overridden to redact both encrypted token fields.
- **`token_encryptor.py`** — `TokenEncryptor`, the ONLY place in this
  codebase that imports `cryptography.fernet`. Fails fast (at
  construction) on a missing/invalid master key — never generates a
  replacement. Deliberately never passes `ttl=` to `Fernet.decrypt()`
  — see the module's own docstring for why.
- **`repository.py`** — `ChasterOAuthStateRepository` (single-use
  `state` values, atomic consume via `Database.transaction()`'s own
  `BEGIN IMMEDIATE`, proven with real concurrent threads in this
  module's own tests) and `ChasterConnectionRepository` (owns the
  encryption boundary — callers only ever see plaintext at this
  layer's own edges, never ciphertext).
- **`oauth_client.py`** — `ChasterOAuthClient`, built only from the
  two officially confirmed endpoints (Authorization, Token). Never
  logs or echoes a code/token/secret in any exception message.
- **`callback_service.py`** — `ChasterCallbackService`, the
  framework-agnostic orchestration: consume state → exchange code →
  resolve identity → persist. **Known, honestly-flagged limitation**:
  identity resolution (confirming *which* Chaster account authorized
  a connection) requires an authenticated call this project's
  research never confirmed the existence/shape of. Rather than guess
  an endpoint, this is an injected dependency
  (`resolve_identity`) — tests supply a fake; production wiring uses
  `unconfirmed_identity_resolver`, which always raises
  `ChasterIdentityResolutionUnavailable`. **A real callback against
  the real Chaster API will fail safely at exactly this one step
  until a future slice resolves it.** Everything before this point
  (state consumption, code exchange) is fully real.
- **`callback_listener.py`** — `ChasterCallbackListener`, the ONLY
  `aiohttp.web` server in this project. One route
  (`/oauth/chaster/callback`), localhost-only bind. Started/stopped
  by `bot/discord_bot.py::CoachKeyholderBot.setup_hook()`/`close()` —
  a startup failure here is caught and logged, never allowed to
  prevent Discord itself from starting (`setup_hook()` runs *before*
  the websocket connects, so an uncaught exception there would take
  down the whole bot — verified directly in this project's own tests
  by simulating a bind failure).

## What is NOT implemented (explicitly out of scope for CHASTER-01A)

- Fetching or storing any Chaster lock status (`chaster_provider_observations`
  does not exist — CHASTER-01B).
- Any change to `lock_state`'s user-reported model or `task_runtime`'s
  eligibility logic — both completely untouched.
- `chaster disconnect` / `chaster status` commands.
- Real Chaster account identity resolution (see `callback_service.py`
  above).
- Background/scheduled token refresh (a refresh is only ever attempted
  transparently at the moment a token is actually needed — no
  scheduler exists anywhere in this project).
- Key rotation tooling (`encryption_key_version` is reserved,
  unused, always `NULL`).
- **`LockForWearer`'s exact field-level JSON schema** — the real
  Chaster unlock operation IS implemented (`POST
  /locks/{lockId}/emergency-unlock`, Option A active-lock discovery
  via `GET /locks?status=active` — both confirmed, both real, both
  tested), but this project has never confirmed the exact JSON field
  names of a returned lock object from any authoritative first-party
  source. `emergency_unlock_provider.py::unconfirmed_lock_field_extractor`
  isolates this one remaining gap — a real request today discovers
  the active lock for real, enforces "exactly one" for real, and
  then fails safely at the eligibility-check step.

## PC-local emergency-unlock safety plane

A **separate, standalone process** — `chaster/emergency_unlock_server.py`
(`python3 -m chaster.emergency_unlock_server`), never imported by or
importing `bot/discord_bot.py`, `application/`, or
`conversation_engine/` — confirmed by this project's own tests via
actual AST-parsed import statements, not a docstring claim. Exists
so the safety mechanism stays usable even if the Discord bot process
itself is hung, crashed, or otherwise malfunctioning.

- **`lock_client.py`** — `ChasterLockClient`: the two confirmed
  Chaster endpoints this safety plane needs (`GET /locks`,
  `POST /locks/{lockId}/emergency-unlock`). Returns each lock as a
  raw, unparsed `dict` — deliberately does not guess field names.
- **`emergency_unlock_provider.py`** — `RealChasterEmergencyUnlockProvider`:
  the real, tested Option A control flow (discover → enforce
  exactly-one → check eligibility → call the confirmed endpoint for
  that one lock only). `EmergencyUnlockProvider.attempt_unlock(access_token)`'s
  signature is unchanged — no caller can ever supply a lock ID.
  `UnwiredEmergencyUnlockProvider` remains as a test-only,
  network-inert fixture.
- **`emergency_unlock.py`** — `EmergencyUnlockService`: authenticates
  against a dedicated secret (`CHASTER_EMERGENCY_UNLOCK_SECRET`,
  never reused from any other Chaster secret), writes an audit event
  at every stage by reusing the existing `infrastructure.outbox`
  domain-events table (no new migration), decrypts the access token
  only immediately before a provider call.
- **`emergency_unlock_listener.py`** — its own separate
  `aiohttp.web.Application`, localhost-only, one route.
- **`emergency_unlock_server.py`** — the standalone entry point;
  refuses to start without both the emergency secret and the token
  encryption key configured.

**Deployment gate (unchanged): real personal Chaster deployment
remains blocked until a real, confirmed unlock operation exists and
has itself been thoroughly tested — this safety plane being
implemented and locally tested does not itself clear that gate.**

## Configuration

All new `.env` values are optional — the bot starts normally, and
`chaster connect` replies that Chaster is "not configured," if any
piece is missing:

- `CHASTER_CLIENT_ID`, `CHASTER_REDIRECT_URI` (non-secret)
- `CHASTER_CALLBACK_BIND_HOST` (default `127.0.0.1`), `CHASTER_CALLBACK_BIND_PORT` (default `8420`) (non-secret)
- `CHASTER_CLIENT_SECRET`, `CHASTER_TOKEN_ENCRYPTION_KEY` (secrets — generate the encryption key with
  `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`,
  then back it up **separately from the database** — losing one
  without the other makes every stored connection permanently
  undecryptable)

## Testability

- **Milestone A** (pure local, mocked Chaster HTTP): `tests/chaster/`
  — fully exercised, no domain/Cloudflare/real Chaster application
  needed.
- **Milestone B** (real local listener, no Cloudflare):
  `tests/chaster/test_callback_listener.py`,
  `tests/bot/test_chaster_listener_lifecycle.py` — a real `aiohttp`
  server, real HTTP requests, still entirely local.
- **Milestone C** (real OAuth through a real tunnel + a real,
  approved Chaster application): **not achievable yet** — blocked on
  the identity-resolution gap above, independent of any
  domain/Cloudflare/approval status.

## Current real-provider readiness (checkpoint status)

- **Option A is selected** for emergency-unlock target-lock semantics
  and is **implemented**: active-lock discovery happens live, at the
  moment of each emergency request, via the confirmed `GET
  /locks?status=active` — never from a cached or stale value.
- **Exactly one active lock is required.** Zero active locks fails
  safely (`"No active Chaster lock found"`); more than one fails
  safely with the exact candidate count, never picking one
  arbitrarily.
- **No caller can ever supply a `lockId`.** `EmergencyUnlockProvider.attempt_unlock(access_token)`'s
  signature has no lock parameter at all — selection happens entirely
  inside `RealChasterEmergencyUnlockProvider`.
- **The confirmed `POST /locks/{lockId}/emergency-unlock` endpoint is
  used**, with the documented bondage-lock + enabled-safety-feature
  eligibility requirement enforced before any unlock is attempted.
- **`LockForWearer`'s exact field schema was NOT authoritatively
  confirmed** from any first-party Chaster source, despite a genuine,
  dedicated attempt. The implementation does not pretend otherwise:
  `unconfirmed_lock_field_extractor` isolates this exact gap and
  always raises, so a real request today fails safely at the
  eligibility-check step rather than guessing field names.
- **`CurrentUser`'s exact field schema also remains unresolved**
  (`callback_service.py`'s own identity-resolution gap, above) —
  unrelated to but structurally identical to the lock-schema gap.
- **The real provider has not been proven against a real Chaster
  account.** All 67 of this slice's own tests mock every Chaster HTTP
  call — they prove this project's own control flow (discovery,
  exactly-one enforcement, eligibility gating, status-code mapping)
  behaves correctly; **they do not, and cannot, constitute proof that
  Chaster's real API behaves as documented or that a real unlock
  would succeed.**
- **Cloudflare Tunnel is still not implemented** — Milestone C
  (real OAuth end-to-end) remains unreachable for that reason alone,
  separate from the schema gaps above.
- **The local emergency safety-plane deployment gate remains open.**
  No real personal Chaster deployment should occur yet.
