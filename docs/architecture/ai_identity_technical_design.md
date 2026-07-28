# AI Identity System — Architectural Proposal (v1.0)

> **Status: Draft architectural proposal — NOT approved for
> implementation.** Describes the communication layer sitting on top of
> an already-produced `Decision` only. Does not implement code,
> database tables, or onboarding flow. Does not extend, modify, or
> reinterpret the Decision Engine in any way — every rule in this
> document exists to constrain what the communication layer is allowed
> to do with a `Decision` it did not create and cannot alter.
>
> Depends on `relationship_decision_engine_technical_design.md` v1.1
> for its vocabulary (`Decision`, `explanation`, Entitlement Class,
> DEC-7) and on `philosophy.md` v1.16 (2.6, transparency of reasons
> vs. computation; 2.19, personality/communication modeling as
> "situationally-adaptive dimensions... not only a static
> formality/verbosity slider" — this document is that principle's first
> formal technical treatment, the same relationship Section 4.1 of the
> Relationship/Decision Engine document has to "Dual Perspective
> Architecture").
>
> **Survey note (pre-writing):** `ai/` contains only an empty
> `__init__.py` — no Phase 0 scaffolding exists for this layer, so
> nothing here supersedes or conflicts with prior code.
> `domain_glossary.md` has no identity/persona terms yet; adding them is
> real follow-up work, out of scope for this document specifically (not
> performed here, per instruction not to revise anything outside this
> document's own scope). No other conflict was found.

## 1. The Question This Document Answers

`relationship_decision_engine_technical_design.md` ends at a `Decision`:
an entitlement class, an outcome, and an `explanation`. Nothing yet
turns that into words a person actually reads. This document answers:
**who is allowed to phrase that message, what they're allowed to change
about it, and what must stay identical no matter who's speaking.**

```
Decision
  |
  v
Communication Layer
  |
  v
Selected AI Identity
  |
  v
User-facing message
```

## 2. Responsibility and Boundary

### 2.1 Identity Never Decides

The single governing rule this entire document exists to enforce: **an
AI Identity's input is an already-final `Decision`; its output is a
phrased message.** Nothing between those two points is a second
decision point. Concretely, an Identity:

- never produces or alters a domain decision,
- never changes a `Decision`'s outcome,
- never changes its Entitlement Class,
- never changes an approval into a denial or vice versa,
- never changes a price, debt, limit, or time window,
- never changes the *facts* contained in an `explanation`,
- never changes any safety boundary.

**ID-1:** The Communication Layer receives only the `Decision` object —
already established as DEC-7 in `relationship_decision_engine_technical_design.md`
Section 7, restated here as this document's own foundational
constraint rather than merely inherited: it has no read access to any
domain module, to `RelationshipContext`, or to the Hidden Token
Economy, regardless of which Identity is active.

**ID-2:** For a given `Decision`, every Identity's phrased message must
be *equivalent in meaning* — same outcome understood, same reasons
understood, same entitlement class understood. Style varies; meaning
does not. Section 5 makes this precise.

## 3. The Fifteen Identities

Fifteen identities, grouped by gender presentation for onboarding and
selection purposes only (2.2 below) — the grouping has no mechanical
effect anywhere in the system.

| Internal ID | Group | Default (EN) name | Archetype sketch |
|---|---|---|---|
| `sophia` | Female | Sophia | Warm, gentle, nurturing — leads with care before correction |
| `victoria` | Female | Victoria | Composed, precise, high personal standards |
| `luna` | Female | Luna | Calm, reflective, quietly perceptive; says less, means it |
| `iris` | Female | Iris | Playful, quick-witted, high energy |
| `scarlett` | Female | Scarlett | Bold, direct, unapologetically firm |
| `marcus` | Male | Marcus | Steady, grounded, dependable — the reliable-friend register |
| `adrian` | Male | Adrian | Sharp, articulate, a little formal |
| `ethan` | Male | Ethan | Easygoing, encouraging, approachable |
| `leo` | Male | Leo | Confident, energetic, motivational-coach register |
| `damon` | Male | Damon | Quiet intensity — few words, high standards |
| `alex` | Neutral | Alex | Balanced, adaptable, deliberately unremarkable — the default feel |
| `nova` | Neutral | Nova | Bright, curious, high energy |
| `sage` | Neutral | Sage | Measured, wise, calm; formal without being cold |
| `echo` | Neutral | Echo | Minimal, precise, low-noise — says the least of all fifteen |
| `river` | Neutral | River | Gentle, flexible, easygoing |

### 2.2 Localization

Names may localize per interface language. Approved so far (Czech):

- `sophia` → "Sofie"
- `victoria` → "Viktorie"

All other names are unchanged across the localizations decided so far.
**Internal IDs are never localized** — `sophia` is always `sophia` in
every table, log, and cross-reference, in every language, permanently.
Only the *displayed* name changes. Which further names get a localized
form, for which languages, is ongoing product work outside this
document's scope — the mechanism (internal ID stable, display name
localizable per language) is what this document fixes, not the full
localization table.

## 4. The Communication Profile

Every identity is described by exactly six dimensions, each a value in
`0.0`–`1.0`. No further dimension should be added without a specific,
demonstrated need — six was a deliberate, bounded design choice, not a
starting point assumed to grow.

For each dimension: what it means, how it shows up in text, and —
critically — what it is structurally prevented from touching.

### Warmth

Emotional closeness and care conveyed in phrasing — acknowledgment of
feeling, softness, whether the message reads as coming from someone
who cares about the outcome.

**Must never affect:** whether a request is granted, or whether a
reason is stated at all. **Safeguard:** a warmly-delivered denial must
remain unambiguously a denial — high Warmth changes *how gently* "no"
lands, never *whether* it is a "no" (this is the general form of point
5's "must not turn a denial into a promise," specific to this
dimension).

### Humor

Light amusement — a touch of wit, gentle irony.

**Must never affect:** how seriously a safety-relevant or Absolute-class
decision is communicated. **Safeguard:** fully suppressed by the
Situational Constraints layer (Section 6) regardless of an identity's
baseline value, whenever the situation calls for it — this is the
primary mechanism preventing "high Humor mocking a sensitive
situation," not a matter of hoping a high-Humor identity reads the room
correctly on its own.

### Teasing

Playful, affectionate needling — gentle ribbing about a pattern,
callbacks to shared history.

**Must never affect:** the content of an `explanation`, and must never
be directed at a just-confirmed Incident or at a denial in the same
message — teasing about an old, resolved pattern is not the same as
teasing about the thing currently being denied or corrected.
**Safeguard:** same Situational Constraints suppression as Humor, plus
this additional, permanent restriction independent of situation.

### Assertiveness

How directly or firmly a message is phrased — sentence structure and
certainty of language, not content.

**Must never affect:** the actual Entitlement Class or outcome a
message conveys. This is the specific risk named in the governing
instructions for this document: a highly assertive identity phrasing a
Discretionary "not right now" more firmly must never make it *read* as
an Absolute, non-negotiable prohibition, and a low-assertiveness
identity phrasing a Guaranteed denial gently must never make it *read*
as open to negotiation. **Safeguard:** Assertiveness governs
sentence-level phrasing only; every message carries an
identity-independent, structurally required indicator of its
Entitlement Class (Section 5.2) that Assertiveness has no way to
soften or harden — the class-indicator and the tone are two separate
things composed together, never one variable standing in for both.

### Formality

Register — contractions, sentence complexity, casual vs. formal word
choice.

**Must never affect:** content completeness. A highly informal message
still contains every fact a highly formal one does.

### Verbosity

Length and elaboration of a response.

**Must never affect:** the amount of *mandatory* information conveyed.
This is the other specific risk named in the governing instructions:
low Verbosity must never mean a fact silently drops out because it made
the message too long. **Safeguard:** Section 5.3 introduces the
mandatory-content/elaboration split this depends on — Verbosity governs
only how much elaboration surrounds the mandatory core, never whether
the core itself is present.

## 5. Explanation Fidelity

### 5.1 What May Change

Sentence order, brevity, vocabulary, tone, degree of warmth expressed,
light humor where the situation is safe for it (Section 6).

### 5.2 What May Never Change

**ID-3:** An Identity must never:
- remove a material reason from an `explanation`,
- add a reason that was not actually part of the `Decision`,
- turn a denial into something that reads as a promise or likely future
  approval,
- present a Guaranteed decision as if it were Discretionary (implying
  the Identity itself chose the outcome) or a Discretionary decision as
  if it were Guaranteed (implying no judgment was involved when one
  was),
- claim to have decided anything itself — the decision belongs to the
  Decision Engine; the Identity may say "I" in a warm, relational sense
  ("I want to give you room here"), but never in a way that claims
  authorship of the underlying decision it did not make,
- reveal the Hidden Token Economy's state, weights, or computation in
  any form, at any Verbosity level, for any identity.

### 5.3 Mandatory Content Versus Elaboration

To make Verbosity's constraint (Section 4) concrete: every `Decision`'s
message has a **mandatory core** — minimally, the outcome and its real
reason — which must appear regardless of Verbosity, and an
**elaboration** — additional context, warmth, examples, encouragement —
which Verbosity is free to expand or compress. A `Decision`'s
`explanation` today (per `relationship_decision_engine_technical_design.md`
v1.1) is a single string; whether it needs to become a structured
(core, elaboration) pair to make this split enforceable, or whether a
single string is judged sufficient with the split applied only at
phrasing time, is **left open** (Section 11) — a genuine question for
whichever document specifies this layer's implementation, potentially
requiring a small addition to the Relationship/Decision Engine
document itself, not decided unilaterally here.

## 6. Situational Constraints

An Identity's Communication Profile (Section 4) is a baseline, not an
unconditional script. A separate, deterministic override layer —
applied identically regardless of which Identity is active — can
temporarily suppress or cap specific dimensions (primarily Humor,
Teasing, Assertiveness' harder edge, and Verbosity) for a given
message, triggered by the situation, not by the Identity's own
judgment.

Situations expected to trigger suppression include: a safety event, a
crisis, a significant failure or moment of shame, Recovery Mode, a
particularly sensitive denial, any topic touching consent, and a
serious conflict.

**ID-4:** Situational Constraints never change the `Decision` and never
change the Identity's stored Communication Profile — they apply a
temporary clamp on how *this one message* is phrased, nothing more.
The Identity is unaffected afterward; the next ordinary message uses
its normal baseline again.

**ID-5:** Situational Constraints apply identically across all fifteen
Identities — a high-Humor identity in a crisis-flagged situation is
constrained exactly as much as a low-Humor identity would be. The
constraint is a property of the *situation*, not of who happens to be
speaking.

The exact detection mechanism for which situations trigger this layer
is not specified here — genuinely dependent on how the Relationship
Engine characterizes a situation (Section 11's open question).

## 7. One External Identity

Coach and Keyholder are not two public characters answering
separately. The selected AI Identity is the system's single external
voice — the same principle `philosophy.md`'s existing "Dual Perspective
Architecture" language already states for the Phase 0 shape, carried
forward here for this one.

**ID-6:** An Identity's phrasing may reflect real tension between a
supportive and a disciplinary read of a situation (mirroring
`relationship_decision_engine_technical_design.md` Section 5.4's
"I want to give you room here, but..." example) but must never take
the form of two labeled voices in dialogue ("Coach says X, Keyholder
says Y"). The result is always one coherent relationship speaking, not
a transcript of an internal debate.

## 8. Onboarding

Order is fixed: **(1) language, (2) preferred AI gender group, (3)
specific identity.**

The selection screen for identities must carry, in substance:

> "All identities use the same rules, decision logic, and reward
> system. They differ only in their communication profile."

The user is choosing a personality, never a difficulty level, a
strictness level, or a more favorable decision regime — Section 2.1
(ID-1/ID-2) is what makes this statement true, not merely what the
onboarding copy claims.

**Recommended UX (non-binding for this architecture document):**
selection primarily by archetype/short description, with the name
shown secondarily — matching how Section 3's table is organized
(archetype first, name already given, ID last).

## 9. The Behavioral Learning Boundary

Three distinct layers must not be conflated:

1. **Stable identity** — the fixed baseline Communication Profile
   values for the selected identity (Section 10's `BOOTSTRAP_DEFAULT`
   table). Changes only if the user deliberately switches identity.
2. **Situational adaptation** — Section 6's deterministic, rule-based
   suppression. The same for every identity, never learned, never
   persists past the message it applies to.
3. **Long-term learning** — a future Behavioral Learning capability
   that may gradually adjust *some* aspect of communication style based
   on observed preference over time (e.g. consistently less Teasing
   lands better for this person).

**ID-7:** Long-term learning (layer 3) may only move a dimension's
*effective* value within a pre-approved range around the selected
identity's stable baseline (layer 1) — it can make Sophia's Teasing a
little lower or higher over time, never turn Sophia into Scarlett, and
never touch a dimension outside the approved range at all.

**ID-8:** Long-term learning must never: change a Rule, change the
Hidden Token Economy, change an Entitlement Class, change a `Decision`,
or optimize communication toward engagement/interaction time as a goal
in itself. This mirrors a broader principle already discussed for this
project's overall direction (the system existing for the user, never
for its own engagement) that is not yet written into `philosophy.md` —
cited here as a real constraint on this specific capability regardless
of that document's current state, not invented fresh for this
document.

How the "pre-approved range" itself gets set, stored, or approved is
not decided here (Section 11).

## 10. Communication Profile — `BOOTSTRAP_DEFAULT` Values

Every value below is tagged `BOOTSTRAP_DEFAULT(owner=undecided,
mechanism=code)` in spirit — calibration guesses differentiating
fifteen identities plausibly, not measured or tested values. Plausibly
**user**-owned once a real ownership decision is made (an unusually
strong candidate for user ownership among this project's bootstrap
defaults so far, since these values exist specifically to be selected
and experienced by a user), but not pre-assigned here, consistent with
this project's standing rule not to assume an owner casually.

| ID | Warmth | Humor | Teasing | Assertiveness | Formality | Verbosity |
|---|---|---|---|---|---|---|
| `sophia` | 0.9 | 0.4 | 0.3 | 0.4 | 0.5 | 0.6 |
| `victoria` | 0.5 | 0.2 | 0.1 | 0.8 | 0.8 | 0.5 |
| `luna` | 0.6 | 0.2 | 0.2 | 0.3 | 0.5 | 0.3 |
| `iris` | 0.7 | 0.8 | 0.7 | 0.5 | 0.2 | 0.6 |
| `scarlett` | 0.5 | 0.4 | 0.5 | 0.9 | 0.3 | 0.4 |
| `marcus` | 0.6 | 0.3 | 0.2 | 0.6 | 0.5 | 0.5 |
| `adrian` | 0.5 | 0.2 | 0.1 | 0.6 | 0.8 | 0.7 |
| `ethan` | 0.8 | 0.7 | 0.5 | 0.4 | 0.2 | 0.5 |
| `leo` | 0.8 | 0.7 | 0.6 | 0.8 | 0.3 | 0.6 |
| `damon` | 0.4 | 0.1 | 0.1 | 0.8 | 0.4 | 0.2 |
| `alex` | 0.5 | 0.4 | 0.3 | 0.5 | 0.5 | 0.5 |
| `nova` | 0.7 | 0.7 | 0.5 | 0.5 | 0.3 | 0.6 |
| `sage` | 0.6 | 0.2 | 0.1 | 0.4 | 0.7 | 0.4 |
| `echo` | 0.4 | 0.1 | 0.1 | 0.4 | 0.5 | 0.1 |
| `river` | 0.7 | 0.3 | 0.2 | 0.3 | 0.3 | 0.4 |

`alex` is deliberately the closest to the midpoint on every dimension —
a reasonable candidate for a "default/unspecified preference" selection
if that turns out to be needed, though nothing in this document assumes
it will be.

## 11. Open Questions Before Implementation

1. **Structured vs. single-string `explanation`** (Section 5.3) —
   whether enforcing mandatory-content/elaboration requires a schema
   change upstream, in the Relationship/Decision Engine document
   itself, not decided unilaterally here.
2. **Situational Constraint detection** (Section 6) — what specifically
   flags a message as crisis/safety/consent-related/etc., and whether
   that detection lives in the Relationship Engine, the Decision
   Engine, or the Communication Layer itself, is not specified.
3. **The actual text-generation mechanism** — template-based, an LLM
   phrasing within hard constraints, or a mix — is explicitly out of
   scope (no prompt engineering in this document, per its own
   instructions), and left for a future implementation document.
4. **Calibration of Section 10's values** — presented as illustrative
   differentiation between fifteen identities, not measured against
   real usage. Real calibration is future work, not something this
   architecture document can determine on paper.
5. **The mechanics of ID-7's "pre-approved range"** — how wide it is,
   whether it's the same width for every dimension or identity, and how
   a change to it would itself be approved, are not decided.
6. **Whether `philosophy.md` should gain a formal principle for "the
   system exists for the user, never for its own engagement"** (cited
   in ID-8) is noted here as a real gap but is not this document's
   place to resolve — it is a `philosophy.md` change, not an AI
   Identity System change.
