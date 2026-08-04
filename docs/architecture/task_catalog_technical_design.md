# Task Catalog — Technical Design (v1.0)

> **Status: Draft for review, not approved for implementation.**
>
> Owns the versioned reference layer for task templates used across
> Recovery Plan today and, potentially, Goal Management, a future
> Journaling owner, a future Integrity Task owner, Optional Challenge,
> and Advanced Mode's autonomous task selection. Does **not** own any
> running task instance, any lifecycle, any consequence, or any
> runtime decision. Four of its five currently-named `instance_role`
> values have no runtime owner yet — see Section 8; this does not make
> the catalog incomplete (Section 3).
>
> Depends on nothing this project hasn't already approved. Reuses,
> rather than reinvents, the `Goal`/`GoalVersion` split
> (`goal_technical_design.md`) for its own versioning — see Section 2.

## 1. The Question This Document Answers

Multiple parts of this system need to hand someone a *kind* of task —
a Recovery Task today; potentially an Primary Task, a Journaling
Task, an Integrity Task, an Optional Challenge, or an
Advanced-Mode-assigned task tomorrow. Without a shared reference layer,
each owner would independently invent its own notion of "what this
task requires" (equipment, privacy, verification method, safety
classification) — duplicated, and free to silently drift apart even
when describing what should be the same real-world activity. This
document answers: **where does the shared, versioned definition of a
task live, and exactly how little authority does it have over
anything that happens once an instance exists.**

## 2. Versioning: `TaskTemplateVersion` / `TaskTemplateCatalogEntry`

Directly reuses the `Goal`/`GoalVersion` split already approved and
implemented in `goal_management` — the same append-only-content,
separate-mutable-pointer shape, not a new pattern:

```
TaskTemplateVersion (append-only, immutable — never edited, never
                      deleted, exactly GoalVersion's own discipline):
    id: str
    template_id: str
    version: int
    category: str
    difficulty: str
    effort: str
    duration_minutes: int
    required_equipment: tuple[str, ...]
    required_privacy: str
    required_context: str
    safety_classification: str
    eligible_instance_roles: tuple[TaskInstanceRole, ...]
    eligible_operating_modes: tuple[str, ...]        # 'standard' | 'advanced'
    completion_requirements: dict
    verification_requirements: dict
    reflection_requirements: dict | None
    created_at: datetime
    created_via_consent_id: str                        # critical_change (philosophy.md 2.5)

TaskTemplateCatalogEntry (mutable current-state pointer — exactly
                           Goal's own relationship to GoalVersion):
    template_id: str
    current_version: int
    eligibility_status: TaskTemplateEligibilityStatus   # 'active' | 'deactivated'
    status_changed_at: datetime
    eligibility_changed_via_consent_id: str | None      # added during implementation --
    current_version_changed_via_consent_id: str | None  # a confirmed audit gap found
    current_version_changed_at: datetime | None         # under direct review; see
                                                          # task_catalog/README.md for the
                                                          # full reasoning (this is a fix
                                                          # within the already-approved
                                                          # implementation slice, not a
                                                          # newly-approved part of the
                                                          # broader draft)
```

**TC-1:** No field on any `TaskTemplateVersion` row is ever mutated or
deleted after creation, without exception — a corrected version is a
*new* `TaskTemplateVersion` row under the same `template_id`, never an
edit to an existing one.

**TC-2:** `eligibility_status` lives exclusively on
`TaskTemplateCatalogEntry`, never on `TaskTemplateVersion` — activating
or deactivating a template mutates only the pointer, exactly as
`Goal.status` mutates while every `GoalVersion` under it stays
untouched. A deactivated template's historical versions remain
permanently readable through the catalog's own read API; deactivation
only prevents *new* instances from being created against it (Section
5).

## 3. `eligible_instance_roles` — Closed Enum, Open Ownership

```python
class TaskInstanceRole(StrEnum):
    RECOVERY = "recovery"
    PRIMARY = "primary"
    JOURNALING = "journaling"
    INTEGRITY = "integrity"
    OPTIONAL_CHALLENGE = "optional_challenge"
```

**TC-3:** A role's presence in this enum, and its use in a template's
`eligible_instance_roles`, is entirely independent of whether that role
currently has a runtime owner. Only `RECOVERY` has one today (Recovery
Plan). `PRIMARY`/`JOURNALING`/`INTEGRITY`/`OPTIONAL_CHALLENGE` are
fully defined, valid catalog values with no assigned owner — this is a
deliberate, named open state (Section 8), not an incompleteness in
this document. A future `CONSEQUENCE_TASK` role, if `Consequence`
(a domain effect, never automatically a task — see the companion
`advanced_mode_technical_design.md` Section 9) is ever realized as a
task, would be added the same way.

## 4. Public API — The Only Way In

```python
class TaskCatalog:
    def get_template(self, template_id: str, version: int) -> TaskTemplateVersion | None:
        """Returns the EXACT historical content of a specific version --
        immutable, therefore safe to call at any time, for any purpose
        (Section 7), without any staleness risk."""

    def get_active_templates(
        self, *, role: TaskInstanceRole, operating_mode: str,
    ) -> list[TaskTemplateVersion]:
        """Only templates whose CatalogEntry.eligibility_status is
        'active' and whose current_version's eligible_instance_roles/
        eligible_operating_modes include the given values -- the only
        entry point for "what can I create an instance of right now."""
```

**TC-4:** No public write method exists on this interface at all — a
template's own creation/versioning is `critical_change`-governed
(`created_via_consent_id`), outside this document's own runtime API
surface, the same discipline `activity_authorization_technical_design.md`
already applies to `ActivityPolicy`.

## 5. What Task Catalog Never Does

**TC-5:** Task Catalog never: assigns a task to a user, decides *why*
an instance is created, tracks any instance's lifecycle, credits
Recovery Credit, changes Goal state, creates a Penalty Window or any
other consequence, or validates any domain's own `supplementary_metadata`
(Section 6 — that stays each source domain's own responsibility, per
the Interpretation Handoff Pattern `implementation_conventions.md`
already establishes).

## 6. `SourceReference` — Typed, Not a Free-Form Dict

Not owned by this document (an instance-owning domain constructs and
stores this on its own instance, not in the catalog) — specified here
because every instance-owning domain needs the same shape:

```python
@dataclass(frozen=True, kw_only=True)
class SourceReference:
    source_domain: str          # e.g. 'recovery_plan', 'goal_management', 'advanced_mode'
    source_entity_type: str      # e.g. 'RecoveryPlan', 'Goal', 'DelegatedAuthorityDecision'
    source_entity_id: str
    schema_version: int
    supplementary_metadata: dict  # validated by source_domain itself, never by Task Catalog
                                    # or by the instance-owning domain if different from source_domain
```

**TC-6:** `supplementary_metadata`'s schema, for a given
`(source_domain, source_entity_type, schema_version)`, is validated
exclusively by `source_domain` itself — never by Task Catalog, never
by whichever domain happens to own the resulting task instance if that
differs from `source_domain`. Prevents Task Catalog from becoming an
unintended central schema registry for every other domain's own
reference data.

## 7. Runtime Source of Truth vs. Historical Reference Source

**TC-7 (the corrected invariant — see this document's own revision
note):** The current state of Task Catalog must never be treated as
authoritative for the runtime behavior of an already-created instance.
Runtime decisions — completion, verification, evaluation, consequences,
anything that determines what actually happens — use exclusively the
instance's own `binding_conditions_snapshot` (Section 8), copied once,
at creation time, and never re-read from the catalog afterward.

This is deliberately **not** "Task Catalog must never be read again
for an existing instance" — that earlier formulation was too broad. A
`TaskTemplateVersion` is immutable (TC-1); reading `get_template(id,
version)` for an existing instance at *any* later time returns exactly
what was true when the instance was created, with no staleness risk.
Audit, history display, UI ("this task's original template was
called..."), and user-facing explanation may read Task Catalog freely,
at any time, for any existing instance. What may never happen is a
runtime decision (does this count as complete? was this verified
correctly? what consequence follows?) being computed from anything
Task Catalog currently says, rather than from the instance's own frozen
snapshot.

## 8. `binding_conditions_snapshot` — Not a Shared Model, a Convention

**Deliberately not a shared table, shared dataclass, or required
runtime interface** (per explicit review guidance) — a small set of
fields every instance-owning domain models independently, in its own
schema, in its own way:

```
Cross-domain audit convention (not a class, not a table):
    template_id: str
    template_version: int
    instance_role: TaskInstanceRole
    originating_mode: str                    # 'standard' | 'advanced'
    created_at / assigned_at: datetime
    source_ref: SourceReference               # Section 6
    binding_conditions_snapshot: dict          # the runtime-authoritative copy (TC-7) --
                                                 # only the fields that are actually binding
                                                 # (completion/verification/reflection
                                                 # requirements, difficulty, effort — NOT
                                                 # the whole TaskTemplateVersion row)
```

**TC-8:** Domain-specific lifecycle fields (`status` enum values,
presence or absence of a `deadline`, verification state machine,
attempt history, expiry/withdrawal semantics) are never prescribed
here — each owning domain's own meaning stays its own, exactly as
`RecoveryTask`'s `EXPIRED`/`WITHDRAWN` today are meaningful only in
terms of `RecoveryPlan`'s own regeneration/versioning, not a generic
"task expired" concept this document imposes.

## 9. Ownership Table

| Concern | Owner |
|---|---|
| `TaskTemplateVersion`, `TaskTemplateCatalogEntry` | This document |
| `eligible_instance_roles` enum | This document |
| Any `RECOVERY`-role instance | `recovery_plan` (existing, unchanged) |
| Any `PRIMARY`/`JOURNALING`/`INTEGRITY`/`OPTIONAL_CHALLENGE`-role instance | **Open** — see Section 8's own note and `advanced_mode_technical_design.md` Section 9 |
| `SourceReference.supplementary_metadata` schema | The `source_domain` named in that specific reference |
| Whether a Task Runtime module should ever exist | `advanced_mode_technical_design.md` Section 10 |

## 10. Open Questions

1. Whether `TaskTemplateEligibilityChange` (an append-only log of
   activation/deactivation events, mirroring `ConfirmationRecord`'s own
   pattern) should exist from the start, or wait until a real
   deactivation actually happens — not decided.
2. Exact governance for who may propose a new `TaskTemplateVersion` —
   assumed `critical_change` throughout this document, but whether a
   Coach-proposed-user-approved path (mirroring `GoalVersion.created_via`)
   should exist is not decided.
3. Whether `eligible_instance_roles` needs to be `critical_change`-governed
   as its own field, or is fully covered by the template version's own
   governance — not decided.
4. This is the first purely-referential table in this project with its
   own mutable status field with no direct user-facing effect of its
   own (Section 2's note) — flagged as worth watching, not a known
   problem.
