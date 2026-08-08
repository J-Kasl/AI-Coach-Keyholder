# Task Runtime — Architectural Proposal (v1.0)

> **Status: Draft for review, not approved for implementation.** This
> document describes the runtime layer sitting on top of Task Catalog
> -- Task Catalog answers "what can exist" (template definitions);
> Task Runtime answers "what was assigned to a specific user, and in
> what state." Nothing beyond what `task_runtime/README.md` describes
> as implemented is built until it, or the next slice, is separately
> approved.

## 1. The Question This Document Answers

Given a task definition that CAN exist, what does it mean for a
specific user to actually be doing it right now, and how is that
tracked without creating a second, competing definition of what a task
is.

## 2. Ownership Boundary

Task Catalog owns the definition, including which eligibility
properties belong to a specific template version (`LockRequirement`
lives in `task_catalog.models`, deliberately -- see
`preference_limits_profile_technical_design.md`'s own precedent for
"eligibility metadata belongs to the versioned definition, not a
separate mapping table"). Task Runtime owns only what happened for a
specific user against a specific, immutable template version. The
dependency is one-way: `task_runtime -> task_catalog`,
`task_runtime -> lock_state`, never the reverse -- `task_catalog` must
never import `task_runtime` (verified by AST scan).

## 3. Slice B — Implemented

> See `task_runtime/README.md` for the exact, currently-true boundary.
> Summarized here for document completeness.

`TaskAssignmentStatus`, `TaskAssignment`, `EligibilityReasonCode`,
`TaskEligibilityDecision`; `evaluate_task_eligibility()` (pure,
fail-closed on the lock-state dimension only); `TaskRuntime` (read-only),
`TaskRuntimeAdministration` (governed write: assign/complete/cancel).
Authoritative eligibility enforcement inside `assign_task()` itself --
never trusting a caller-supplied decision. Database-level composite
foreign key to `task_template_versions`, database-level partial unique
index for at-most-one-active-assignment-per-user.

No Discord commands, no `ApplicationService` integration, no
Conversation Engine wiring, no selection algorithm, no preference/
limits eligibility dimension, no external provider integration.

## 4. Future Design — Not Implemented, Sketched for Continuity Only

### 4.1 Selection

A future slice would add ranking/selection among templates that
already passed `evaluate_task_eligibility()` -- `eligibility first,
ranking second` remains the governing structural rule; personality
(e.g. Scarlett) may influence which of several *eligible* templates is
preferred, but can never see or override an ineligible one.

### 4.2 Preference/Limits Eligibility

A future dimension alongside `LockRequirement`, once
`preference_profile` has a real runtime repository (Slice 2) to read
from. `evaluate_task_eligibility()`'s own shape is designed to grow
additional checks without restructuring -- each dimension either
passes or produces a distinct `EligibilityReasonCode`, never a raw
sensitive value.

### 4.3 Conversation Context Integration

A future `ConversationContextProvider` would expose the active
assignment (and, later, eligible-template previews) to Conversation
Engine -- read-only, optional, never a write path. The model's own
text must never be able to complete/cancel/assign a task; any such
intent must be routed through a deterministic command into
`TaskRuntimeAdministration`, never inferred from free text as
authoritative.

## 5. Explicitly Blocked

Any Discord/application/Conversation Engine integration -- until a
separately approved slice builds it.

## 6. Implementation Roadmap

1. **Slice B** (this document's own Section 3) -- done.
2. **Selection** among eligible templates.
3. **Preference/limits eligibility dimension** -- after
   `preference_profile` Slice 2.
4. **Conversation Context integration**.
5. **External provider integration** (e.g. Chaster) -- its own,
   entirely separate roadmap.
