# Conversation Engine

Canonical design: `docs/architecture/conversation_engine_technical_design.md`
(**still `Draft for review, not approved for implementation` as a
whole document** — this README describes exactly which specific slice
of that draft has been implemented here, and nothing more).

## What is implemented here — Slice 2 (ordinary unmatched conversation via Ollama)

**The first real AI-generated conversation in this project.** Only for
messages `CommandRouter` doesn't match — a known command, or a `mode`
family invalid input, is fully handled by its own deterministic
handler and never reaches this path at all (CE-25).

- **`model_types.py`** — `ModelMessageRole`, `ModelMessage`,
  `ModelGenerationRequest`, `ConversationModel` (the Protocol boundary
  — implementations know nothing about `ResponseContextSnapshot`/
  `ResponsePlan`/the recent-history buffer/`ConversationResponse`),
  `LLMGenerationError` (the only exception type a `ConversationModel`
  may let escape `generate()`).
- **`ollama_adapter.py`** — `OllamaConversationModel`, the **only file
  in this project that imports `requests` for LLM communication**.
  Talks to Ollama's own `/api/chat`. A bounded HTTP reader
  (`_read_bounded_response_body`) that never uses `response.content`/
  `.text`/`.json()` — `Content-Length` is checked but never trusted,
  the actual byte count is checked on every chunk regardless. Named
  bootstrap defaults, not scattered literals:
  `DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0`,
  `DEFAULT_READ_TIMEOUT_SECONDS = 30.0`,
  `DEFAULT_MAX_RESPONSE_BODY_BYTES = 1_000_000`,
  `DEFAULT_HTTP_CHUNK_SIZE_BYTES = 8192`. No new `.env`/`Config`
  fields for timeouts in this slice — the existing `OLLAMA_HOST`/
  `OLLAMA_MODEL` are the only Ollama config this slice uses.
- **`prompt_builder.py`** — `build_generation_request()`. The system
  message is produced **entirely** by deterministic code; the user's
  own text is **never** concatenated into it — a distinct, always-last
  `role="user"` message is a structural guarantee, not a textual
  convention. *Role separation guarantees that user content cannot
  physically alter the deterministically assembled system message. It
  does not guarantee that the model can never be influenced by
  malicious user content. Slice 2 does not implement a semantic
  prompt-injection detector.*
- **`planning.py`** — `build_response_plan()`. Every actively-processed
  unmatched message plans `ResponseCategory.COACHING_DIALOGUE` via
  `GenerationPath.MODEL_GENERATION` — the **intended** path, never
  retroactively rewritten to `DETERMINISTIC_FALLBACK` if the model
  later fails. A model failure produces a fallback *response*, not a
  changed *plan*.
- **`recent_history.py`** — `TransitionalRecentMessageBuffer`. In-memory,
  per-subject (`UserAccount.id`), bounded by both exchange count and
  character budget, trims oldest **whole** exchanges only. No
  persistence, no migration, wiped on every process restart. Only a
  fully successful, validated exchange is ever stored — never on a
  fallback path.
- **`subject_queue.py`** — `SubjectConversationQueue`, a **ticket-based
  FIFO**, not a plain `threading.Lock` (a bare lock guarantees mutual
  exclusion but not order). Guarantees: *"For one subject, conversational
  operations execute FIFO in the order worker threads enter the
  Conversation Engine queue."* **Does not** guarantee Discord gateway
  event order — see the module's own docstring for the precise,
  disclosed limit.
- **`engine.py`** — `ConversationEngine.generate_response()`, the
  orchestration entry point and the **only** place in this package
  that logs an expected model/validation failure (sanitized: error
  code, adapter class name, response category — never user text,
  prompt, history, subject key, identity profile, or HTTP response
  body).
- **56 new tests** across 9 files, including a real multi-threaded FIFO
  ordering proof, 38 bounded-HTTP-reader tests, a structural
  prompt-injection test (the system message is proven identical across
  five adversarial user messages, including one attempting a direct
  override), and three DB-write-invariant scenarios run through a real
  `ApplicationService`.

### `infrastructure/database.py` — the thread-safety fix this slice required

`asyncio.to_thread()` (`bot/discord_bot.py`'s own integration, below)
runs `ApplicationService.handle_message()` on a worker thread, never
the Discord event loop. This exposed a real, **empirically confirmed**
bug: `Database`'s nested-transaction guard was a plain instance
attribute, racing across threads sharing one `Database` instance — a
20-thread concurrent-onboarding experiment produced dozens of spurious
`NestedTransactionError`s, silently swallowed by `ApplicationService`'s
own top-level exception handler (a worse failure mode than a crash —
invisible, widespread functional failure). Fixed by making the guard
`threading.local()` — public API and commit/rollback behavior
unchanged; the same 20-thread experiment now produces zero errors
(verified directly, `tests/infrastructure/test_database_thread_safety.py`).

### `CommandRouter` — `RouteResult` and command families

`route()` now returns `RouteResult(matched: bool, outgoing: OutgoingMessage | None)`
instead of always producing a fixed `OutgoingMessage` — the caller
decides what an unmatched result means, rather than the router baking
in one fixed fallback string. `register_family("mode", invalid_handler=...)`
catches `mode nonsense`/`mode request nonsense` with a deterministic,
`mode`-specific reply, **never** falling through to Conversation
Engine — exact first-token matching only, no fuzzy matching (`model
something` is correctly NOT treated as the `mode` family).

### `application/service.py` — the one approved integration point

```python
result = self.router.route(incoming.text, context)
if result.matched:
    return result.outgoing
if self._conversation_engine is None:
    return OutgoingMessage(text=self.router.unrecognized_text())
if preferences.identity_id is None:
    return OutgoingMessage(text=render_fallback(FallbackReason.MISSING_REQUIRED_CONTEXT).text)
# ... generate_response(), UnknownIdentityError -> same fallback ...
```

`ApplicationService` never constructs `ConversationEngine`/
`OllamaConversationModel`/the buffer/the queue itself — dependency
injection only, `conversation_engine: ConversationEngine | None = None`.
`identity_id is None`, an unknown `identity_id` (`UnknownIdentityError`),
or no engine configured at all — all three fall back deterministically,
**never** calling the model.

### `bot/discord_bot.py` — the async boundary and composition root

```python
outgoing = await asyncio.to_thread(self.application_service.handle_message, incoming)
```

The composition root (`main()`) is the **only** place `OllamaConversationModel`/
`TransitionalRecentMessageBuffer`/`SubjectConversationQueue`/
`ConversationEngine` are constructed, using the existing `config.ollama_host`/
`config.ollama_model`.

### The DB-write invariant

> Conversation Engine, the `ConversationModel` adapter, the prompt
> builder, response planner, transitional recent-history buffer, and
> model output must not directly or indirectly cause a new domain,
> governance, or conversational database write. Existing identity and
> onboarding bootstrap behavior in `ApplicationService` remains
> unchanged.

A model response claiming an action (*"I switched you to Advanced
Mode."*) is phrased as text and returned as-is — `operating_mode_state`
never changes, no `mode_transition_requests` row is ever created, and
no other domain write API is ever called. Verified directly through a
real `ApplicationService`, not asserted.

## What is implemented here — Slice 1 (unchanged by Slice 2)

Runtime types and a deterministic safety shell. **No LLM client, no
prompt text, no ordinary conversation, nothing wired into
`ApplicationService`, and today's Discord behavior is completely
unchanged.**

- **`models.py`** — `ResponseCategory`, `GenerationPath` (one member,
  `DETERMINISTIC_FALLBACK` — no LLM value exists, since no LLM path is
  implemented), `ConversationContextFragment` (recursively immutable —
  see below), `SituationalConstraints` (validated `0.0–1.0` or `None`),
  `ResponseContextSnapshot`, `ToolCallRequest`/`ToolResult` (unused
  design sketches), `ResponsePlan`, `ConversationResponse`, and five
  exceptions.
- **`providers.py`** — `ConversationContextProvider`, a
  `@runtime_checkable` `Protocol` (matching this project's one existing
  Protocol precedent, `infrastructure/clock.py`'s `Clock`). No concrete
  provider ships in this slice; Slice 1 only defines the contract.
- **`identity_adapter.py`** — `build_identity_profile(identity_id)`,
  a direct passthrough of `ai/identity_catalog.py`'s own
  `CommunicationProfile` (the exact same object, not a copy). Fails
  deterministically (`UnknownIdentityError`) for any unrecognized ID.
- **`context.py`** — `assemble_context()` (the low-level primitive),
  `build_response_context()` (the *one* orchestration point that turns
  a required-provider failure into a deterministic fallback —
  see below), and `apply_situational_constraints()` (a pure clamp
  function).
- **`validation.py`** — `validate_response()`, purely structural
  checks (Section "Validation" below).
- **`fallback.py`** — `render_fallback()`, the deterministic renderer
  that works with zero dependencies on the rest of this package.
- **64 tests** in `tests/conversation_engine/`, including a recursive-
  immutability proof (not just top-level), a static AST scan proving
  no existing package imports this one, and direct exercise of
  existing command paths through a real `ApplicationService`.

## Recursive immutability — why `MappingProxyType` alone wasn't enough

A bare `MappingProxyType(dict(data))` only protects the *top-level*
mapping — a nested list or dict inside `data` would stay mutable. This
was caught under direct review before implementation. `models._freeze()`
recursively normalizes fragment data:

| Input type | Becomes |
|---|---|
| Mapping | Immutable mapping (`MappingProxyType`), values recursively frozen |
| `list`/`tuple` | `tuple`, elements recursively frozen |
| `set`/`frozenset` | `frozenset`, elements recursively frozen |
| `str`/`int`/`float`/`bool`/`bytes`/`None` | Unchanged |
| Anything else | Rejected — `UnsupportedFragmentDataError` |

Deliberately **not** a `deepcopy` of arbitrary objects, and it never
tries to "freeze" a foreign domain instance — a provider that wants to
expose one must serialize it to a supported shape itself. Verified
directly: `tests/conversation_engine/test_models.py`'s own nested-list/
nested-dict/deeply-nested tests construct a fragment, then attempt to
mutate the *returned* structure and assert it raises — not merely that
construction succeeded.

## The one orchestration point for a required-provider failure

`assemble_context()` (low-level) raises `RequiredProviderFailedError`
when a namespace listed as required has no successful fragment — it
never decides what happens next. `build_response_context()` is the
**single** place in this package that catches that exception and
returns `render_fallback(MISSING_REQUIRED_CONTEXT)` instead — no other
function independently makes this decision, avoiding exactly the
ambiguity a prior draft of this API would have left open (per explicit
review instruction). The result type, `ContextAssemblyOutcome`, sets
**exactly one** of `snapshot`/`fallback_response` — enforced by its own
`__post_init__`, not merely a naming convention.

## Provider namespace contract

- A provider declares its own `namespace` (a property, not a
  fragment field it controls independently).
- If the fragment it returns carries a *different* namespace →
  `ProviderNamespaceMismatchError`, assembly fails immediately.
- If two providers' fragments claim the *same* namespace →
  `ProviderNamespaceCollisionError`, assembly fails immediately.
- If a provider returns `None` or raises, and that namespace is
  **optional** → recorded in the returned `ProviderCallOutcome`
  sequence (a runtime diagnostic, never persisted — no new audit
  table), the response can still proceed.
- If a provider returns `None` or raises, and that namespace is
  **required** → `RequiredProviderFailedError` (see orchestration
  above) — the response never proceeds with a fabricated or partial
  stand-in for an authoritative fact.

No dynamic discovery, no plugin registry — Slice 1 uses a small,
explicit, hand-written provider list. A real registry is deliberately
deferred to Slice 5 (the design document's own roadmap), once at least
three real providers exist and static wiring has become genuinely
painful.

## `apply_situational_constraints()` — what it does and does not do

Pure function, no detection logic of its own — the clamp is already
decided by whichever caller supplies `SituationalConstraints`
(`ai_identity_technical_design.md` Section 6's own ID-4/ID-5: this
engine never decides *whether* to activate a constraint, only applies
one already handed to it). Clamps exactly the four dimensions
`SituationalConstraints` names — Humor, Teasing, Assertiveness,
Verbosity — via `min()` only, never raising a value. Warmth and
Formality are always passed through unchanged. Returns a **new**
`CommunicationProfile`; the catalog's own stored value is never
mutated — verified directly (`test_context.py`'s own test re-reads the
catalog after calling this function and asserts bit-for-bit equality
with the value before the call, and that it's still the *same object*).

## Validation — structural only, not semantic

`validate_response()` checks: non-empty `text` (after `strip()`),
response category match between the response and its plan, a real
`GenerationPath` member, empty `tool_calls` (**CE-21** — no tool
calling in this slice, enforced here, not merely by convention),
non-overlapping required/optional namespace sets, every required
namespace present in the snapshot, non-empty `language`/
`current_user_message`, and fragment-key/`namespace` agreement.
**Deliberately does not** attempt semantic Explanation Fidelity
comparison (ID-3) — that remains explicitly open (design document
Section 19, Open Questions 4/5), not pretended to be solved here.

## Tool calling — sketches only, never constructed by production code

`ToolCallRequest`/`ToolResult` exist as types so the pipeline doesn't
need a structural rewrite the day tool calling is separately approved
(Slice 6). In this slice: no production code path ever constructs a
`ToolCallRequest`; `validate_response()` rejects a non-empty
`tool_calls` outright; no tool dispatcher or registry exists anywhere
in this package.

## System independence (CE-20) — verified, not merely asserted

`tests/conversation_engine/test_system_independence.py` statically
parses the AST of every domain module, `application/`, `bot/`, and
`infrastructure/` source file and asserts none of them import
`conversation_engine` — then constructs a real `ApplicationService`
and directly exercises `help`/`status`/`mode status`/an unrecognized
command, confirming their behavior is byte-for-byte what it was before
this package existed. This deliberately does **not** invoke the
existing pytest suite from inside a test (per explicit review
instruction) — the full regression suite is run as its own, separate
step (see the project's own root `README.md`).

## What is explicitly NOT implemented — still draft, still open

- **No Memory System integration.** `TransitionalRecentMessageBuffer`
  is explicitly temporary — retired, not extended, once Slice 3 lands.
- **No provider registry or plugin discovery.** Slice 5, and only once
  at least three real providers exist. Slice 2 uses zero concrete
  domain/memory providers — the prompt's own "domain context"/
  "conversation context" sections are simply omitted, not emitted
  empty.
- **No tool calling, no tool dispatcher.** `ToolCallRequest`/`ToolResult`
  remain unused type sketches; Slice 6, separately designed and
  approved.
- **No new database table, no migration.** Slice 2 is pure runtime
  types, library functions, and one small infrastructure fix
  (`Database`'s own thread-local guard).
- **No semantic Explanation Fidelity validator, no LLM-based
  retry/repair.** Design document Section 19's own still-open
  questions — `validate_response()` remains purely structural.
- **No `GOVERNANCE_EXPLANATION`, no `Decision` phrasing.** Slice 4,
  once a real Decision Engine exists to produce something to phrase.
- **No semantic prompt-injection detector.** Role separation is a
  structural guarantee against the system message being *physically*
  altered by user content — it is not a guarantee the model can never
  be *influenced* by adversarial user content.
- **No proactive/scheduled messages.** Conversation Engine only ever
  responds to an incoming message; nothing calls it on a timer.
