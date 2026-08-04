# Conversation Engine — Technical Design (v1.0)

> **Status: Draft for review, not approved for implementation.**
>
> Answers *how* the system phrases things for the user — never *what*
> is decided, *whether* something is allowed, or *what changes in the
> database*. No AI logic, no prompts, no LLM calls, no memory storage,
> and no new database tables are implemented by this document. It is a
> pure architecture proposal, at the same stage
> `advanced_mode_technical_design.md` and `task_catalog_technical_design.md`
> were before their own first implementation slices — survey, ownership,
> boundaries, open questions, nothing built yet.

## 1. The Question This Document Answers

Every domain module in this project (Trust Manager, Penalty Engine,
Goal Management, Advanced Mode, Task Catalog) decides things and
changes state. Nothing in this project today decides **how those
things get said**. `help`/`status`/`preferences`/`mode ...` all return
hand-written, fixed English strings today, directly from
`application/service.py` — there is no layer between "here is what
happened" and "here is the text the user reads." This document answers:
**where does that layer live, what does it own, and what must it never
be allowed to do.**

## 2. Survey — What Already Touches Communication Today

Read directly, not from memory, before writing anything below.

| Location | What it does today |
|---|---|
| `application/onboarding_service.py` | Hand-written, English-only prompt strings per onboarding step. Localizes exactly one thing: an identity's *display name* (`ai/identity_catalog.py`'s `display_name(language)`) — never prompt text itself (its own Section 5 documents this limit explicitly). |
| `application/service.py` | Hand-written, English-only response strings for every command (`_handle_help`, `_handle_status`, `_handle_preferences`, all six `_handle_mode_*`). No personality, no tone variation, no context beyond the literal data being reported. |
| `application/router.py` (`CommandRouter`) | Exact-string matching only — deliberately not a natural-language layer (its own docstring says so). Not something this document changes. |
| `bot/discord_bot.py` | Pure text passthrough — sends `OutgoingMessage.text` verbatim. Never touches phrasing. |
| `ai/identity_catalog.py` | The 15-identity **reference catalog** (names, archetypes, the six Communication Profile values) — approved, static data. Not a runtime communication mechanism; nothing reads the six values yet. |
| `philosophy.md` 3.2 | "Dual Perspective Architecture... both roles remain internal perspectives beneath a single external voice of the system" — the foundational principle behind One External Identity. |
| `ai_identity_technical_design.md` (draft) | Owns: the 15-identity catalog (approved data only), the six-dimension Communication Profile, Explanation Fidelity (ID-3, what phrasing may/may not change), Situational Constraints (ID-4/ID-5), the Behavioral Learning Boundary (ID-7/ID-8), a narrower concept referring to `conversation_engine`'s own `GOVERNANCE_EXPLANATION` category. |
| `relationship_decision_engine_technical_design.md` (draft) | Section 7/8 describe `conversation_engine`'s `GOVERNANCE_EXPLANATION` category as the consumer of `Decision` objects — "the primary home for LLM usage: phrasing a `Decision`'s already-fixed `explanation`... never inventing or altering the reason itself" (DEC-7: no read access to any domain module, `RelationshipContext`, or the Hidden Token Economy). |
| `memory_system_technical_design.md` (draft) | Owns all five memory layers (Section 4) **and already owns retrieval-for-prompt-construction and the context budget** (MEM-7, Section 3.10) **and prompt-injection protection for retrieved memory** (Section 3.11). This is critical — see Section 9 below. |
| `implementation_conventions.md` | The Interpretation Handoff Pattern (each layer interprets its own facts, never reaches past the layer that owns them) — the same discipline this document's own boundary (Section 3) applies. |

**No existing "Open Question" anywhere in the project names this exact
layer** — `ai_identity_technical_design.md` Section 5.3 and Section 6
each independently flag that phrasing/situational-suppression
mechanics are "genuinely dependent on... whichever document specifies
this layer's implementation," without naming that document. This
document is that missing piece — but see Section 4 for why its actual
scope turns out to be broader than what those two documents anticipated.

## 3. The Naming/Scope Decision — `conversation_engine` Is the General Layer

> **Resolved.** Previously Open Question 1 — closed by explicit decision,
> not left as a judgment call.

`relationship_decision_engine_technical_design.md`/`ai_identity_technical_design.md`
use the term "Communication Layer" — narrowly, for phrasing a
`Decision`'s `explanation` only. **`conversation_engine` is the
system's one general communication layer.** What those two documents
call "Communication Layer" names a **specific, narrower use case
inside it** — the `GOVERNANCE_EXPLANATION` category (Section 5) —
never a second, parallel module. Both documents have had this
terminology updated accordingly (see the accompanying message for the
exact diff) — this revision closes that ambiguity rather than merely
proposing to.

`conversation_engine` therefore covers: ordinary conversation, coaching
dialogue, motivational communication, reflection, crisis communication,
phrasing informational responses, and phrasing finished governance
decisions. It still never owns the *domain logic* behind any of these
— Section 4's ownership boundary is unchanged by this decision; only
the naming ambiguity is resolved.

## 4. Ownership

**`conversation_engine` owns:**
- Response structure and tone selection for a given message.
- Reading already-decided facts and turning them into text.
- Applying the selected Identity's Communication Profile (baseline) and
  Situational Constraints (temporary clamp) to phrasing.
- `ResponseContextSnapshot` assembly for a single response (Section 6)
  — what's relevant *right now*, not long-term memory storage.
- Long-term communication *consistency* — not deciding facts, but not
  contradicting a fact stated five messages ago either.

**`conversation_engine` never owns, and never touches:**
- Trust Manager, Penalty Engine, Goal Management, Recovery Plan, Task
  Catalog, Advanced Mode, any future Task Runtime, or any other domain
  module's own state.
- Any database write of domain state. (**CE-1**)
- Governance/consent (`critical_change`, two-stage confirmation flows)
  — it may *describe* that a confirmation is required; it never
  performs, tracks, or validates one. (**CE-2**)
- The five memory layers themselves (`memory_system_technical_design.md`
  remains sole owner) — `conversation_engine` is a *consumer* of that
  system's future read API, never a second store of the same
  information. (**CE-3**)
- The Decision Engine, Relationship Engine, or Hidden Token Economy
  (`relationship_decision_engine_technical_design.md` remains sole
  owner) — `conversation_engine` receives a `Decision` object already
  finished; it cannot see how it was produced (mirrors DEC-7 exactly).
- Deciding whether a command/action is permitted at all — the command
  router, `AdvancedModeAdministration`, `PenaltyEngine`, etc. decide
  that; `conversation_engine` only phrases the result.

### 4.1 Boundary Table

| Concern | Owner |
|---|---|
| Whether a Penalty Window exists, its duration | `penalty_engine` |
| Whether a mode transition is allowed right now | `advanced_mode` |
| What a `Decision`'s real reason is | Decision Engine (draft, unbuilt) |
| Which of 15 identities, their six Communication Profile values | `ai_identity_technical_design.md` (data), `ai/identity_catalog.py` (code) |
| Long-term facts, episodic events, relationship boundaries | `memory_system_technical_design.md` (draft, unbuilt) |
| **How any of the above gets phrased into text** | **`conversation_engine`** |
| Whether *this specific phrasing* violates Explanation Fidelity (ID-3) | `conversation_engine`, enforcing a rule `ai_identity_technical_design.md` already defines — not this document's own invention |

## 5. Response Categories

Not the exact list suggested in the original request — reasoned
independently against what the project actually has and is likely to
need:

| Category | Example today | Governed by |
|---|---|---|
| `INFORMATIONAL_STATUS` | `status`, `mode status`, `preferences` | Facts only, minimal tone variation — these must stay predictable/scannable even under a high-Warmth identity |
| `OPERATION_CONFIRMATION` | `mode cancel` succeeded, onboarding step accepted | Confirms exactly what happened, nothing more |
| `GOVERNANCE_EXPLANATION` | Phrasing a `Decision.explanation`; explaining *why* a mode transition is blocked | Explanation Fidelity (ID-3) applies at full strength — this is the narrow use case Section 3 resolves |
| `ONBOARDING` | Today's onboarding prompts | Currently hand-written/English-only (Section 2) — a real future candidate for this engine, not touched by this document until its own migration is separately approved (CE-20) |
| `COACHING_DIALOGUE` | Not built anywhere yet | The least-specified category — genuinely open how it differs structurally from `GOVERNANCE_EXPLANATION` beyond tone (Open Question 2) |
| `MOTIVATIONAL` | Not built | Recovery Plan encouragement, streak acknowledgment — reads Recovery/Goal state, decides nothing |
| `REFLECTIVE` | Not built | Journaling-adjacent (Task Catalog's own `JournalingTask`, still ownerless) — prompts a user to reflect, does not itself store the reflection (Memory System's job) |
| `CRISIS` | Not built | The one category where Situational Constraints (ID-4/ID-5) are *guaranteed* active, not merely possible — see Section 8 |
| `ERROR_FALLBACK` | Today's generic "Something went wrong... it's been logged." | Deliberately the LEAST personality-driven category — a failure state should never depend on identity/LLM availability to be legible |

**CE-4:** `ERROR_FALLBACK` must remain reachable through a path that
does **not** depend on `conversation_engine` itself being available —
if the engine fails, the fallback cannot be "ask the engine to phrase
the failure." (Mirrors `bot/discord_bot.py`'s own existing "one bad
message never crashes the bot" posture, applied one layer up; see also
the broader system-independence guarantee, CE-20.)

## 6. Provider Architecture

**Minimal form, sized for Slice 1** — not a dynamic registry, not
plugin discovery. A typed interface and a small, explicit, statically
assembled list of providers; a full registry is deliberately deferred
(Slice 5, Section 16) until there are enough real providers for static
wiring to actually hurt.

```
class ConversationContextProvider(Protocol):
    """Reads exactly one namespaced slice of context. Never writes
    anything, anywhere."""
    def provide_context(self, *, now: datetime) -> ConversationContextFragment | None: ...

class ConversationContextFragment:
    """Immutable. `namespace` is this fragment's stable key in
    ResponseContextSnapshot.context_fragments (Section 7) --
    'advanced_mode', 'memory.working', 'memory.retrieved', etc."""
    namespace: str
    data: Mapping[str, Any]
```

**CE-5:** A provider reads only through the public read API of the
module it wraps — never a domain module's internal tables, never a
second, parallel read path. A provider never writes to the database,
never performs governance, and never changes domain state; it is
`conversation_engine`'s own read-only lens onto one owner's already-true
facts, nothing more.

**CE-6 — Fault boundary, and it is not one rule but two:**
- **Optional provider fails** → its fragment is simply absent from
  `context_fragments`; the response proceeds without that slice of
  context.
- **Required provider (for the current `ResponseCategory`) fails** →
  the response must **not** proceed with a fabricated or partial
  stand-in for that fact. It falls through to the deterministic
  fallback (CE-4) instead. A provider must never return a vague
  "best effort" value where the response category calls for an
  authoritative one — `ResponsePlan` (Section 11) carries the set of
  required provider namespaces for its own category, so this check can
  happen before generation, not be discovered after the fact.

For Slice 1, the provider list itself is a plain, explicit, hand-written
sequence in code — no dynamic discovery, no plugin loading. Only two
real providers exist conceptually today (an identity-profile source and
an operating-mode source); a static list costs nothing extra at this
scale and avoids building registry machinery before there is a second
or third real consumer to justify it (Slice 5's own condition).

## 7. `ResponseContextSnapshot`

> **Resolved.** Previously Open Question 7 (Working Memory vs. this
> engine's own context) — closed by explicit decision, further refined
> this revision into the hybrid form below.

**Working Memory remains entirely owned by `memory_system_technical_design.md`**
(Section 4.1) — mid-flow dialogue state, a question awaiting an answer,
the current topic, all persisted there, never duplicated here.

**`conversation_engine` owns exactly one runtime object of its own,
now a hybrid of fixed core fields and provider-sourced fragments:**

```
ResponseContextSnapshot (runtime-only -- see Section 13 for its
                          mutability class):
    # Core fields -- always present, never provider-sourced, because
    # every single response needs them regardless of which domain
    # modules happen to be involved.
    response_category: ResponseCategory
    current_user_message: str
    language: str
    identity_profile: CommunicationProfile           # ai/identity_catalog.py, read-only
    situational_constraints: SituationalConstraints    # this call's own clamp, if any

    # Provider-sourced -- everything domain- or memory-specific,
    # namespaced, assembled by Context Assembly (Section 10) calling
    # whichever providers this response category actually needs.
    context_fragments: Mapping[str, ConversationContextFragment]
```

**CE-7:** `ResponseContextSnapshot` is: assembled fresh from read-only
inputs for one response, immutable for the duration of that one
generation, **never persisted by `conversation_engine`**, and discarded
once the response is produced. It is not a cache, not a session
object, and not a second Working Memory.

**CE-8:** A fragment's `namespace` key is stable and owned by its
provider — two providers may never silently write the same key; a
collision is a Context Assembly error, not a last-write-wins overwrite.

**CE-9:** `context_fragments` and every fragment within it are
read-only from `conversation_engine`'s own perspective once assembled
— the engine may read a fragment's `data`, but may never reinterpret,
augment, or change what a fragment means. A memory fragment in
particular arrives **already retrieved and already budget-ranked** by
`memory_system_technical_design.md`'s own MEM-7 rule — this engine
assembles it into the snapshot, it never re-ranks or re-filters it.

| Priority | Field/Namespace | Owner | Notes |
|---|---|---|---|
| 1 | A domain-fact fragment (e.g. `decision`, `advanced_mode`) | Whichever domain module produced it, via its own provider | Never reinterpreted, only phrased (CE-11) |
| 2 | `situational_constraints` (core field) | `ai_identity_technical_design.md` Section 6 | Deterministic override, checked before identity baseline |
| 3 | `identity_profile` (core field) | `ai/identity_catalog.py` | The baseline tone, before any suppression |
| 4 | `memory.working` fragment | `memory_system_technical_design.md` Section 4.1 (future) | Read into the snapshot; the persisted object stays theirs |
| 5 | `memory.retrieved` fragment | `memory_system_technical_design.md` Sections 4.2-4.4 (future) | Already MEM-7-ranked before this engine ever sees it |
| 6 | `advanced_mode` fragment | `advanced_mode` | Affects *what* may be said (CE-16), not tone |
| 7 | `language` (core field) | `application/onboarding_service.py`/`user_preferences` | Already exists |

**CE-10:** Higher-priority fields/fragments may never be silently
overridden by a lower one — `situational_constraints` (priority 2)
always win over `identity_profile` (priority 3) exactly as ID-4/ID-5
already require.

## 8. Conversation Engine MUST NOT

Not limited to this list — every entry here is either already an
explicit invariant somewhere in this project, or a direct logical
consequence of one, not invented fresh:

- **CE-11:** Must never change what a `Decision` says, add a reason
  that wasn't real, or remove one that was (ID-3, restated at this
  layer's own boundary, not a new rule).
- **CE-12:** Must never perform, track, or bypass consent/governance —
  it may say "this needs your confirmation," it may never *be* the
  confirmation.
- **CE-13:** Must never claim a fact the system does not actually know
  — including inferring one from silence (e.g., must never say "since
  you didn't mention X, you must be fine" as if that were a system
  fact).
- **CE-14:** Must never disclose the Hidden Token Economy's state,
  weights, or computation, at any tone/Verbosity level, for any
  identity (ID-3's own final bullet, restated).
- **CE-15:** Must never let a Situational Constraint be skipped because
  an identity's own baseline "would normally" express something
  differently (ID-5) — the constraint is a property of the situation,
  never negotiable by personality.
- **CE-16:** Must never say something that implies an Advanced-Mode-only
  capability is available while `advanced_mode`'s own state says
  otherwise (a project-specific instance of CE-11's general rule, since
  Advanced Mode is the first place "what the system can currently do"
  varies by persisted state the engine does not own).
- **CE-17:** Must never write to, or invent, a memory record — reading
  from `memory_system_technical_design.md`'s future layers is
  permitted; writing to them is not this engine's job (mirrors the
  read/write separation `task_catalog`/`advanced_mode` already
  established, applied here to memory instead).
- **CE-18:** Must never let identity/tone selection affect which
  `Decision` or Entitlement Class applies (DEC-7's own guarantee,
  restated at the boundary that receives its output).
- **CE-19:** Must never treat engagement/interaction time as a goal in
  itself (ID-8, restated — cited there as "not yet written into
  `philosophy.md`" but binding regardless of that document's current
  state).

## 9. System Independence From Conversation Engine

**A new, explicit, cross-cutting guarantee — not previously stated as
its own invariant, only implied by others.**

**CE-20:** The system's core deterministic functionality must remain
fully usable when `conversation_engine` is disabled, unconfigured, or
failing. `conversation_engine` must never become the *sole* place a
domain fact or an authoritative explanation exists. Concretely, all of
the following remain fully available with the engine absent or down:

- Every known deterministic command (`help`, `status`, `preferences`,
  `mode ...`, and any future one) — guaranteed structurally by CE-25
  (Section 12: `CommandRouter` has absolute priority, this engine never
  sees a matched command's text at all).
- Every domain write operation (Penalty Engine, Advanced Mode, Goal
  Management, Task Catalog, etc.) — none of them import or depend on
  `conversation_engine` today, and this document proposes no change
  that would make any of them start doing so.
- Every governance flow (`critical_change`, two-stage confirmations) —
  unaffected, since CE-2/CE-12 already forbid this engine from
  participating in governance at all.
- Status reporting — `status`/`mode status` stay deterministic
  (Slice 1/2, Section 16) until their own migration into this engine is
  a separately approved decision, not assumed by this document.
- Onboarding — likewise stays exactly as it is today (Section 2) until
  separately approved; this document does not migrate it.
- The error fallback path (CE-4) — reachable independent of the engine
  by construction.

**Consequences, stated explicitly rather than left implicit:**
- No domain module's own test suite may ever require
  `conversation_engine` (mock or real) to pass — already true today
  (no `advanced_mode`/`task_catalog` test imports anything AI-related),
  and this document commits to it remaining true.
- Disabling or misconfiguring the LLM must never block any
  deterministic part of the system — only Slice 2's own unmatched-text
  fallback (CE-25) is affected, and even that degrades to CE-4's
  deterministic fallback, never a hard failure.
- A known command must never fail *because of* the AI layer — a
  matched command is fully handled by its own handler and structurally
  never reaches this engine (CE-25).

## 10. Response Pipeline

**Revised — the earlier three-stage model (Context → Generation →
Post-processing) collapsed two genuinely different concerns
("was this wrong" and "what do we do about it") into one
"post-processing" step, leaving the resulting question unanswered
(Open Question 5 still names it). Seven stages, one responsibility
each:**

1. **Context Assembly** — calls the provider list (Section 6), builds
   `ResponseContextSnapshot` (Section 7). Must never perform a domain
   write (CE-1); a required provider's failure routes straight to
   Repair/Fallback (stage 5), not onward.
2. **Response Planning** — produces a `ResponsePlan` (Section 11):
   confirms/refines the response category, selects the generation path,
   and — in every slice this document actually scopes — leaves
   `tool_calls` empty (CE-23). Never itself initiates a workflow,
   command, or domain action.
3. **Generation** — deterministic today, an LLM later; produces
   candidate text from the snapshot and plan. Section 10.1 describes
   this stage's own internal prompt layering. Never decides domain
   permission — it phrases what Planning and the snapshot already
   established.
4. **Validation** — checks Explanation Fidelity (ID-3/CE-11), for
   missing mandatory content, for a forbidden claim (CE-13/CE-14), and
   for basic structural contract (e.g. the output is non-empty text).
   Produces a pass/fail plus a reason, nothing else.
5. **Repair/Fallback** — only reachable after a Validation failure or a
   required-provider failure. May correct *phrasing only* — it must
   never alter the underlying domain fact (CE-11, restated at this
   stage's own boundary). If no safe repair exists, falls through to
   the deterministic fallback (CE-4).
6. **Formatting** — adapts the validated/repaired text to the
   channel-agnostic textual contract this project already uses
   (`OutgoingMessage.text`). Discord-specific concerns (length limits,
   markdown escaping, actual send) stay in `bot/discord_bot.py`,
   unchanged — this stage never crosses into adapter territory.
7. **Delivery Handoff** — returns the finished `ConversationResponse`
   to the application layer. `conversation_engine` never sends
   anything itself; it hands a value back, exactly like every other
   domain module's own write methods already do.

### 10.1 Generation's Own Prompt Layering

The layering previously described as if it were the whole pipeline is,
more precisely, the internal structure of stage 3 alone:

```
1. System prompt                (fixed, not identity-specific -- the model's own operating rules)
2. Situational Constraints       (checked BEFORE identity, not after --
                                   ID-5 requires this precedence; a later
                                   position would let identity tone leak
                                   through before suppression applies)
3. Conversation policy           (this document's own MUST NOT list, Section 8 --
                                   the invariants, not a "style guide")
4. Identity Communication Profile (the baseline -- ai/identity_catalog.py)
5. Context fragments              (from ResponseContextSnapshot.context_fragments,
                                   Section 7 -- read-only, CE-9)
6. Current user message
```

**Why Situational Constraints sit at position 2, ahead of Conversation
policy and Identity:** ID-5's own text is explicit that suppression
"applies identically... triggered by the situation, not by the
Identity's own judgment" — placing it after the Identity layer would
let the prompt structure itself imply identity has a chance to shape
the situation's handling, even if the eventual instruction says
otherwise. Layer order is not neutral; it is itself a place ID-3/ID-5
could quietly leak through.

## 11. `ResponsePlan` and the Future Tool Interface

**Design types only — tool calling is not approved for implementation
by this document, in any slice it scopes.** These types exist so the
pipeline (Section 10) does not need a structural rewrite the day tool
calling is eventually, separately approved (Slice 6, Section 16).

```
ResponsePlan (Response Planning's own output, Section 10 stage 2):
    response_category: ResponseCategory
    required_provider_namespaces: frozenset[str]   # Section 6's CE-6, checked before Generation
    generation_path: str                             # e.g. "deterministic_template" | "llm"
    tool_calls: tuple[ToolCallRequest, ...] = ()        # ALWAYS empty through Slice 1-5

ToolCallRequest (design sketch, not implemented, not approved for use):
    tool_name: str
    parameters: Mapping[str, Any]

ToolResult (design sketch, not implemented, not approved for use):
    tool_name: str
    outcome: Any
    error: str | None
```

**CE-21:** Response Planning must never itself initiate a workflow,
command, or tool call — in every slice this document scopes,
`ResponsePlan.tool_calls` is always empty, enforced as a fact about
these slices, not merely a convention.

**CE-22:** Repair/Fallback (Section 10 stage 5) may correct phrasing
only; it must never alter, invent, or discard the domain fact a
response is built around.

**CE-23:** Neither `conversation_engine` nor any future tool planner
may write to a domain table directly, ever, at any point. A future
write-capable tool must delegate to the existing public write API of
whichever module owns that data, and that delegation must preserve, in
full, that module's own: validation, governance, consent requirements,
transactional rules, audit trail, and domain-specific exceptions — the
same discipline `task_catalog`/`advanced_mode` already apply to their
own governed write APIs, extended here to any future tool rather than
invented fresh.

**CE-24:** A `ToolCallRequest` is never itself treated as consent — if
the delegated write API requires a consent reference (as
`AdvancedModeAdministration`'s own methods do today), that consent must
come from the same place it comes from today, never manufactured by
the tool layer itself.

## 12. Slice 2's Own Constraint: No AI-Initiated Action

**CE-25:** `CommandRouter` has absolute priority. `conversation_engine`
never sees a message at all unless `CommandRouter.route()` already
found no matching handler for it — no change to `CommandRouter` itself
is required; `ApplicationService.handle_message()` already has exactly
one fallback branch for unmatched text, and that branch is Slice 2's
own integration point. A message matching `mode confirm` (or any other
registered command) is fully handled by its own handler and never
reaches this engine, at any slice.

**A second, narrower guarantee specific to Slice 2 itself:** a response
to unmatched text must never itself trigger a command, a workflow, a
governance action, a task assignment, or any database change. For
example, given the message *"I want to switch to Advanced Mode,"* the
engine may explain what that means and point to the deterministic
command `mode request advanced` — it must never call
`AdvancedModeAdministration.request_transition()` itself. This holds
for every current and future write operation until a governed Tool
Interface (Slice 6) is separately designed and approved — Slice 2's
own scope explicitly excludes it (CE-21/CE-23 already forbid the
mechanism; this restates the guarantee at the point in the roadmap
where it first becomes relevant).

## 13. Memory (Consumer, Not Owner)

**CE-3, restated precisely:** `conversation_engine` does not define
Working/Episodic/Semantic/Relationship/Decision Memory — all five
belong to `memory_system_technical_design.md` (draft, unbuilt). This
document's only job regarding memory is describing **how a future
consumer would use it**, via the provider architecture (Section 6):

- **Working memory** — read via a `memory.working` provider for
  "what's the user mid-way through right now," per-response, never
  persisted by this engine itself.
- **Episodic/Semantic/Relationship memory** — read via a
  `memory.retrieved` provider, already ranked/budgeted entirely by
  that system's own MEM-7 rule, inserted into Generation's own prompt
  (Section 10.1) as clearly delimited, labeled data blocks (Section
  3.11's own protection — this document adopts it, does not redesign
  it).
- **Decision memory** — read-only reference to past decisions/lessons,
  same posture, same provider pattern.

**No new persisted state is proposed by this document at all** — see
Section 17 for the one place this document speculates a small new
table *might* eventually be needed, without proposing one.

## 14. Integration (Future, Not Implemented)

| Consumer | How it would eventually call `conversation_engine` |
|---|---|
| `ApplicationService` | Today's hand-written `_handle_*` methods would eventually delegate their *phrasing* (not their logic) to this engine — the domain call (`self.penalty_engine.get_active_or_frozen_penalty_window()`, etc.) stays exactly where it is; only the string construction moves. Not before Slice 1/2's own integration point (Section 12). |
| `bot/discord_bot.py` | Unchanged — it already only ever sees `OutgoingMessage.text`/`ConversationResponse`'s formatted output, regardless of which layer produced it |
| Future Task Runtime | Would be a new *domain fact producer* with its own provider (Section 6), same as Penalty Engine or Advanced Mode today — no special-casing |

## 15. Data Flows, Layers, Mutability

```
Domain module(s) --(read-only, via providers)--> Context Assembly
                                                       |
                                          ResponseContextSnapshot
                                                       |
                                            Response Planning --> ResponsePlan
                                                       |
                                                  Generation
                                                       |
                                                  Validation
                                                       |
                                            Repair/Fallback (only if needed)
                                                       |
                                                  Formatting
                                                       |
                                            Delivery Handoff --> ConversationResponse
```

- **Immutable:** the Identity catalog's six values (already true today,
  `ai/identity_catalog.py`), any domain fact fragment (CE-9), any
  memory fragment (CE-9), `ResponseContextSnapshot` itself once
  assembled (CE-7).
- **Runtime-only, never persisted by this engine:** `ResponseContextSnapshot`,
  `ResponsePlan`, the assembled prompt itself.
- **Pure orchestration, no state of its own at all:** provider
  selection (Section 6), pipeline stage ordering (Section 10), category
  selection (Section 5) — these are *decisions about how to call other
  owners*, not facts this engine stores.

## 16. Review Table

| Area | Verified against | Finding |
|---|---|---|
| Does a "Communication Layer" already exist in name? | `relationship_decision_engine_technical_design.md` Sections 7-8, `ai_identity_technical_design.md` Sections 5.3/6 | Yes, narrower scope — resolved by decision in Section 3, and both documents' own terminology updated this revision |
| Does Memory System already own retrieval/context budget? | `memory_system_technical_design.md` Section 3.10, MEM-7 | Yes — this document explicitly does not re-own it (Section 13) |
| Does anything today localize response text (not just names)? | `application/onboarding_service.py` Section 5, `application/service.py` | No — confirmed English-only throughout; this document doesn't change that |
| Does Explanation Fidelity already have enforcement rules? | `ai_identity_technical_design.md` ID-3 | Yes — this document restates them at its own boundary (CE-11, CE-14), does not redefine them |
| Is there existing precedent for "read-only vs. governed-write" split at this layer? | `task_catalog`, `advanced_mode` (`TaskCatalog`/`AdvancedMode` vs. their Administration classes) | Applied here too, in a new form — providers (Section 6) are the read side; this engine has no write side of its own at all (CE-1 through CE-19 are restrictions, not a second API surface) |
| Does the project have precedent for a fault-tolerant, many-source aggregation layer? | `plugin_architecture_proposal.md` (implemented, Steps 1-3) | Yes — the closest existing precedent for Section 6's own fault-boundary discipline (CE-6), though this document deliberately does not adopt its full registry/discovery mechanism until Slice 5 |

## 17. Ownership Table (Summary)

| Concern | Owner |
|---|---|
| Response structure, tone selection, phrasing, pipeline | `conversation_engine` (this document) |
| Explanation Fidelity, Situational Constraints, Identity catalog | `ai_identity_technical_design.md` (draft) — this document enforces, does not redefine |
| `Decision`, Relationship Engine, Entitlement Classes | `relationship_decision_engine_technical_design.md` (draft) |
| All five memory layers, retrieval ranking, prompt-injection protection for memory | `memory_system_technical_design.md` (draft) |
| Domain state (Trust, Penalty, Goal, Recovery, Task Catalog, Advanced Mode) | Each respective existing module — reached only via a read-only provider (Section 6), never directly |
| Command dispatch, adapter wiring | `application/`, `bot/` (existing, implemented) |
| Any future domain write triggered by a tool | The owning module's own existing governed write API (CE-23) — never `conversation_engine` itself |

## 18. Implementation Roadmap (Not Started)

**Revised this iteration** — the previous four-slice roadmap is
replaced by six slices, each with explicit prerequisites and explicit
out-of-scope items, per the request driving this revision.

### Slice 1 — Runtime types and deterministic safety shell

> **Implemented.** See `conversation_engine/README.md` for the exact
> boundary. This document's own global status remains unchanged
> (`Draft for review, not approved for implementation`) — only this
> specific slice has been built; Slices 2 through 6 remain entirely
> unimplemented and unapproved.

**Prerequisites:** none beyond this document's own approval for this
slice specifically. **No LLM calls anywhere in this slice.**

- `ResponseCategory` (Section 5)
- `ConversationContextProvider` / `ConversationContextFragment` (Section 6)
- The hybrid `ResponseContextSnapshot` (Section 7)
- `ResponsePlan` (Section 11) — `tool_calls` always empty (CE-21)
- `ConversationResponse` (Section 10 stage 7's own output shape — not
  yet specified in field-level detail anywhere in this document; needs
  its own definition when this slice is actually scoped)
- `ToolCallRequest`/`ToolResult` as **type sketches only, unused** (Section 11)
- An identity-profile adapter/provider (reads `ai/identity_catalog.py`
  — read-only, no new data)
- A situational-constraint input type (the *type*, not detection logic
  — Open Question 3 remains genuinely unresolved)
- Provider fault-boundary implementation (CE-6)
- Validation contracts for CE-1 through CE-25
- A deterministic fallback renderer (`ERROR_FALLBACK`, CE-4) — the one
  thing that must work even if every later slice is broken or absent

**Out of scope:** no existing command handler is touched (`help`/
`status`/`preferences`/`mode ...` keep their own hand-written strings
exactly as today); no LLM integration of any kind.

### Slice 2 — Unmatched ordinary conversation through LLM

**Prerequisites:** Slice 1 complete.

- `CommandRouter` has absolute priority (CE-25) — this engine only ever
  sees the fallback branch for text matching no registered command.
- No tool calling (`ResponsePlan.tool_calls` stays empty — CE-21).
- No database writes of any kind.
- No workflow, governance, or task-assignment initiation (CE-25's own
  second guarantee).
- A bounded, in-memory `TransitionalRecentMessageBuffer` (per-user, no
  persistence, wiped on restart, explicitly temporary — retired, not
  extended, in Slice 3).
- Inputs limited to: current message, the transitional buffer's bounded
  history, language, and selected identity.
- Deterministic fallback (Slice 1's renderer) on any LLM failure.
- `help`/`status`/`preferences`/`mode ...` remain fully deterministic
  and untouched.

**Out of scope:** anything requiring Memory System or a Decision Engine.

### Slice 3 — Memory System read integration

**Prerequisites:** `memory_system_technical_design.md` has an actual,
implemented read API — not before.

- The `TransitionalRecentMessageBuffer` is removed, not extended.
- A `memory.working`/`memory.retrieved` provider (Section 6) replaces
  it, returning fragments already ranked/budgeted by that system's own
  MEM-7 rule.

**Out of scope:** any change to Memory System's own retrieval/ranking
logic — this slice only ever consumes it.

### Slice 4 — Structured domain facts and governance explanations

**Prerequisites:** a real Decision Engine (or another structured fact
producer) exists to produce something to phrase.

- The `GOVERNANCE_EXPLANATION` category (Section 3/5) becomes real —
  the specific use case `ai_identity_technical_design.md`/
  `relationship_decision_engine_technical_design.md` already describe.

**Out of scope:** any Entitlement Classification or Decision-Engine
logic itself — that document's own territory, not this one's.

### Slice 5 — Provider registration maturity

**Prerequisites:** at least three real providers exist and static,
hand-written wiring (Slice 1's own approach) has become genuinely
painful to maintain — not a fixed slice number or calendar trigger.

- Only here does a plugin-style registry (mirroring
  `plugin_architecture_proposal.md`'s own already-implemented pattern)
  become worth its own complexity.

**Out of scope:** building this preemptively, before the pain it
solves actually exists.

### Slice 6 — Governed Tool Interface

**Prerequisites:** its own, separate technical design and approval —
not assumed, not scoped, by this document.

- No write capability is assumed here; `ToolCallRequest`/`ToolResult`
  (Section 11) remain unused type sketches until this slice has its
  own explicit governance and consent boundary defined, matching
  `task_catalog`/`advanced_mode`'s own precedent (CE-23/CE-24).

## 19. Open Questions

**Closed this revision:** the naming/scope question (Section 3), the
Working Memory vs. this engine's own context question (Section 7,
`ResponseContextSnapshot`), and — as a direct consequence of the
Provider Architecture and Response Pipeline decisions (Sections 6, 10)
— the previous roadmap's own ambiguity about what Slice 1 actually
contains.

**Not resolved, and this revision does not pretend otherwise:**

1. **Does `Decision.explanation` need to become a structured
   (core, elaboration) pair**, per `ai_identity_technical_design.md`
   Section 5.3's own still-open question — this document doesn't
   resolve it either, since it depends on the Decision Engine's own
   eventual shape.
2. **How does `COACHING_DIALOGUE` actually differ from
   `GOVERNANCE_EXPLANATION` structurally**, not just in tone? Section
   5 names both but cannot fully separate them without a real Decision
   Engine to observe in practice.
3. **Where does the "situation is crisis-flagged" signal actually come
   from?** `ai_identity_technical_design.md` Section 6 already defers
   this to "how the Relationship Engine characterizes a situation" —
   this document inherits that same unresolved dependency, doesn't add
   a new answer.
4. **Does Validation catching a fidelity violation (Section 10, stage
   4) need its own persisted audit trail** — i.e., should a
   rejected/repaired generation be recorded anywhere, or is a
   same-request retry sufficient? Not decided; the one place this
   document speculates a small new table *might* eventually be needed,
   without proposing one.
5. **What is Repair's own actual strategy (Section 10, stage 5)** —
   retry generation, fall back to a templated version of the same
   `explanation`, or something else? CE-22 constrains *what Repair may
   touch*, not *how it decides what to do* — that mechanism is not
   decided here.
6. **How exactly does `ResponsePlan.required_provider_namespaces`
   (Section 11) get populated per `ResponseCategory`** — a static
   mapping maintained alongside the category enum, or something more
   dynamic? Not decided; Section 6's CE-6 depends on this existing in
   some form, but its own shape is left to whichever slice actually
   implements it.
