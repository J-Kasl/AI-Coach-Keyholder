# Conversation Engine

Canonical design: `docs/architecture/conversation_engine_technical_design.md`
(**still `Draft for review, not approved for implementation` as a
whole document** — this README describes exactly which specific slice
of that draft has been implemented here, and nothing more).

## What is implemented here — Slice 1 only

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

- **No LLM client, no prompt text, no ordinary conversation.** Slice 2's
  own territory.
- **No integration with `ApplicationService`.** No existing command
  handler was touched; the fallback branch in `handle_message()` that
  would eventually call this engine does not exist yet.
- **No recent-message buffer, no Memory System integration.** Slices 2
  and 3.
- **No provider registry or plugin discovery.** Slice 5, and only once
  at least three real providers exist.
- **No tool calling, no tool dispatcher.** Slice 6, separately
  designed and approved.
- **No new database table, no migration.** This slice is pure runtime
  types and library functions.
- **No semantic Explanation Fidelity validator, no LLM-based
  retry/repair.** Design document Section 19's own still-open
  questions.
