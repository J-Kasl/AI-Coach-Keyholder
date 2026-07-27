# penalty_engine — Slice 1 + Extension

The second real domain module built against the architecture baseline
(`docs/architecture/penalty_window_technical_design.md`,
`docs/architecture/extension_technical_design.md`), consuming Trust
Manager's public read API. Same incremental discipline as
`trust_manager/` and `infrastructure/`.

## What is covered

Canonical sections implemented: **1 (the subset below), 2.1-2.6, 3.1,
3.3 (minus 3.4), 4.1, 4.2 (the subset below), 4.4, 4.5**, plus
**Extension** (`extension_technical_design.md`, all sections).

- **The state machine** — `(none) → ACTIVE`, `ACTIVE ↔ FROZEN`,
  `ACTIVE → COMPLETED` (natural countdown), exactly as Slice 1
  delivered.
- **`ACTIVE → ACTIVE` (Extension)** — `penalty_engine/extension.py`'s
  `should_extend()` (Eligibility → Base Magnitude → Mitigation →
  Capacity Cap), wired into ONE unified incident-consumption path
  (`consume_confirmed_incident()`/`_consume_confirmed_incident_in_transaction()`):
  the first unconsumed Incident starts a window if none is
  ACTIVE/FROZEN; every subsequent unconsumed Incident (including that
  very first one) is consumed through `should_extend()`. Consumption is
  unconditional (`philosophy.md` 3.8); only `extensions_hours` is
  conditional on the decision's `assigned_hours`.
- **Freeze as a set of reasons, startup reconciliation, the public read
  APIs, events** — unchanged from Slice 1.
- **New events**: `extension.decision_recorded` (every consumption,
  eligible or not), `penalty_window.extended`/`.target_duration_changed`
  (only when `assigned_hours > 0`).

## What is deferred to a later slice

- **Recovery Credit integration** (3.4) — depends on Recovery Plan,
  which does not exist yet.
- **`terminate()`** — explicitly deferred by the architecture document
  itself (2.1).
- **Any actual caller of `freeze(reason=partnered_intimacy_authorization)`
  or `freeze(reason=temporary_wear_exemption)`** — mechanism exists and
  is tested generically; Activity Authorization and the exemption
  approval logic (Coach engine) do not exist yet.
- **`occurred_during_recovery_task`** — always `False` in this slice
  (EXT-10; Recovery Plan does not exist to signal it).

## Design notes

- **Four TBD parameter groups from `extension_technical_design.md`
  Section 10**, this slice's own defaults, flagged (not silently
  presented as architecture-decided):
  - `BASE_HOURS_BY_SEVERITY` — MINOR=4.0, MODERATE=12.0, MAJOR=24.0,
    CRITICAL=48.0h.
  - `REPETITION_INCREMENT_HOURS` — 6.0h per additional same-rule
    repetition within the current window.
  - `MINIMUM_RETAINED_FRACTION` for MAJOR/CRITICAL — 0.5/0.7, adopting
    the architecture document's own illustrative comment values (they
    were offered as reasonable examples, not arbitrary placeholders) as
    this slice's actual defaults.
  - `_is_high_cooperation()`/`_mitigation_fraction()` — "high
    cooperation" requires BOTH `self_disclosed` AND
    `active_cooperation_in_resolution`; mitigation is graduated
    (0.3 + 0.3 + 0.2 for recovery-task context, capped at 1.0 before the
    MAJOR/CRITICAL floor is applied).
- **`ConfirmedIncidentSummary` (Trust Manager) gained `rule_group_id`**
  — EXT-2's current-window-scoped repetition count needs it locally,
  without a cross-module read mid-transaction (see below).
- **`incident_consumption` gained a `rule_group_id` column** (migration
  007, additive) — the same reasoning: EXT-2's count is computed from
  this module's own table, never by asking Trust Manager.
- **`incident.confirmation_changed`'s payload gained `intrinsic_severity`,
  `cooperation_self_disclosed`, `cooperation_active_cooperation_in_resolution`**
  (only populated when the transition reaches CONFIRMED) — the
  event-driven consumer handler (`system/startup.py`) needs
  `ExtensionContext`'s two Trust-Manager-owned fields without calling
  back into Trust Manager's API mid-transaction. This is the same
  NestedTransactionError lesson `system/README.md` documents for
  `trust_domain`, now extended to Extension's needs.
- **`remaining_active_hour_capacity` is computed as
  `MAX_TARGET_ACTIVE_HOURS - (base_duration_hours + extensions_hours)`**
  — the room left before the absolute 336-hour ceiling, NOT related to
  `active_hours_elapsed()`/the countdown at all. Extension's capacity
  cap (Section 3.4) is a structural constraint on how large
  `target_active_hours` may grow, independent of how much time remains
  on the current countdown.
- **A real finding from writing the integration tests**: with default
  (unspecified) cooperation, `confirm_incident()`'s
  `CooperationAssessment()` default is LOW (`self_disclosed=False,
  active_cooperation_in_resolution=False`) — meaning an isolated MINOR
  Incident is, by default, *eligible* for Extension
  (`ELIGIBLE_BY_LOW_COOPERATION`). Tests that want to exercise "clean"
  state-machine behavior without incidental extension now pass an
  explicit HIGH-cooperation `CooperationAssessment` — this is a real
  behavior, not a test artifact, and is exactly what `philosophy.md`
  2.1/3.8 would predict: cooperation must be actively demonstrated to
  earn leniency, not assumed by default.

