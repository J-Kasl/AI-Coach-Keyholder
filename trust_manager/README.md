# trust_manager — Slice 1 + Slice 2

The first real domain module built against the architecture baseline
(`docs/architecture/trust_manager_technical_design.md`). Built in two
tested slices — the same incremental discipline used for
`infrastructure/` (Clock → Database → Outbox, each its own phase).

## What is covered (Slice 1 + Slice 2)

Canonical sections implemented: **2.1, 2.2, 2.4 (enum only), 2.6, 2.8,
2.10, 3.1-3.6, 5.1, 5.2, 5.3, 5.4, 7 (the subset below), 8 (the subset
below), 13, 14**.

- **Domain Registry + Domain State** (`create_domain`,
  `deactivate_domain`, `reactivate_domain`, `get_domain_state`) — TI1's
  consent-id requirement, the 3.4 default values.
- **Incident + Confirmation lifecycle** (`register_incident_report`,
  `confirm_incident`) — the atomic CONFIRMED-path fix (TI23/14.2), now
  extended: reaching `CONFIRMED` also triggers the 'incident'
  recalculation (3.2), in the SAME transaction (see "Slice 2 design
  notes" below for why this is safe).
- **The severity/cooperation rubric** (`trust_manager/severity.py`).
- **The score recalculation pipeline** (`trust_manager/recalculation.py`
  — pure functions; `TrustManager.recalculate_domain_trust()`/
  `_recalculate_domain_trust_in_transaction()` — database access):
  `effective_weight()` (3.3/TI9, capped), `apply_recalculation()`
  (3.5/TI19, the per-recalculation delta cap and score clamp),
  `compute_confidence()` (3.6, a diminishing-returns function of
  evidence volume within the rolling window). `TrustDomainState.score`/
  `confidence` **now actually update** — the Slice 1 gap this slice
  exists to close.
- **The public read API** (13) — `get_incident_assessment()`,
  `get_confirmed_incidents_since()`.
- **Crash recovery** (14.3) — `recover_trust_manager_state()`.
- **Events** — Slice 1's events, plus `trust_domain.recalculated`
  (every recalculation, even a purely confidence-driven one with
  `delta_score=0` — 8's "even when `delta=0`").

## What is deferred to a later slice

- **`TrustEvidenceDispute`** (2.5) and the three restricted manual-review
  operations (2.9).
- **`OverallTrustReport`** (2.7, Section 4).
- **The `window_completion` and `scheduled_review` recalculation
  triggers** (3.2) — both require modules that do not exist yet
  (Penalty Engine; a check-in/scheduling mechanism for assembling
  `ExposureRecord`, 3.7). `recalculate_domain_trust()` is already
  callable with any `triggered_by` string, so wiring either trigger
  later is a caller-side addition, not a change to this module.
  `ExposureRecord` and `maybe_create_sustained_period_evidence()` (3.7)
  are similarly not yet implemented — no code path produces
  `SUSTAINED_PERIOD` evidence in this slice.
- **Goal Accountability Assessment integration** (Section 15).
- **`should_extend()`/`ExtensionContext`** (Section 6).
- **`TrustDomainState.trend`** — stays at its initial value
  (`'stable'`, set at domain creation); no code in either slice computes
  or updates it. The architecture document does not specify how trend
  is derived, so nothing was invented here rather than guess.

## Slice 2 design notes

- **Two constants without a specified value in the architecture
  document**, flagged rather than silently invented (see
  `trust_manager/recalculation.py`'s own docstring for the full
  reasoning): `MAX_ABS_EFFECTIVE_WEIGHT = 0.5` (3.3/TI9 says "capped
  below a threshold," no number given) and `CONFIDENCE_K = 0.3` (3.6
  says "the exact constant k is a parameter to be tuned," no number
  given). Both are ordinary module-level constants, easy to revisit —
  flagged specifically so a future reviewer knows these two, unlike
  every other constant in this module, were not transcribed from the
  architecture document but chosen by this implementation.
- **Why the 'incident' trigger recalculates inside `confirm_incident()`'s
  own transaction, rather than via a separate, later call**: the
  evidence being consumed and its consumption are produced by the exact
  same code path, with no cross-module event delivery involved — there
  is no crash-recovery gap to protect against here the way TI23 protects
  the assessment/evidence write, because nothing can complete between
  "evidence written" and "evidence consumed" when both happen in the
  same transaction. `window_completion`/`scheduled_review` will
  genuinely need `recalculate_domain_trust()`'s separate-transaction
  form, since they react to a `domain_events` delivery from a different
  module's transaction.
- **Confidence is computed from evidence recorded as "applied" (i.e.
  already referenced by a `trust_recalculation_evidence` row), not from
  every existing `TrustEvidence` row** — this matches 3.6's own
  language ("confidence naturally decreases when old evidence 'drops
  out' of the window without being replaced") and means confidence never
  counts evidence that has not yet actually informed the score.
- **TI4's `UNIQUE(evidence_id)`** on `trust_recalculation_evidence`
  (not a composite key with `recalculation_id`) is what makes "consumed
  at most once, ever" a database-enforced guarantee, not merely an
  application-level convention — verified directly
  (`test_evidence_is_consumed_exactly_once`).

## Design notes carried over from Slice 1

- **`Incident` is mutable-with-status, not append-only**
  (`implementation_conventions.md` Section 7) — `confirmation` and
  `assessment` are "what is true now"; history lives in the append-only
  `ConfirmationRecord` trail, not in old values of this row.
- **`IncidentNotFoundError`** — raised by `confirm_incident()` when
  given an unknown `incident_id`, on purpose (a genuine precondition
  violation, not a domain outcome).
- **Row ↔ dataclass mapping** is intentionally verbose rather than
  clever — every field maps to one named column, no dynamic field
  iteration.
