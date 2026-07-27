"""
penalty_engine/repository.py

Penalty Engine Slice 1 — built on infrastructure.database.Database, the
same composition pattern database/database.py and trust_manager/repository.py
already established: no sqlite3 connections of its own, every write via
self._core.transaction()/apply_transition().

Canonical spec: docs/architecture/penalty_window_technical_design.md.
See penalty_engine/README.md for exactly what this slice covers and
what is deferred.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from infrastructure.database import Database as CoreDatabase
from infrastructure.database import Transaction, apply_transition
from infrastructure.outbox import DomainEvent, write_event
from penalty_engine.extension import ExtensionContext, ExtensionDecision, should_extend
from penalty_engine.models import (
    AuthorizationFreezeState,
    FreezeEndReason,
    FreezePeriod,
    FreezeReason,
    PenaltyWindow,
    PenaltyWindowNotFound,
    PenaltyWindowStatus,
    ResolutionMethod,
)
from penalty_engine.window import DEFAULT_BASE_DURATION_HOURS, MAX_TARGET_ACTIVE_HOURS, is_complete
from trust_manager.models import CooperationAssessment, SeverityTier
from trust_manager.repository import TrustManager


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def active_hours_elapsed_from_row(row, now: datetime) -> float:
    """Same formula as penalty_engine.window.active_hours_elapsed(), but
    operating directly on a freshly-read SQL row inside an open
    transaction -- used only at the moment of freezing, where we already
    have the row and constructing a full PenaltyWindow first would be
    redundant. Kept in sync with window.py's own docstring/reasoning by
    being the only other place this formula appears."""
    if row["status"] != PenaltyWindowStatus.ACTIVE.value or row["active_period_started_at"] is None:
        return row["accumulated_active_hours"]
    started = _parse_iso(row["active_period_started_at"])
    elapsed_hours = (now - started).total_seconds() / 3600.0
    return row["accumulated_active_hours"] + elapsed_hours


class PenaltyEngine:
    def __init__(self, db_path: str | Path, *, core: CoreDatabase | None = None) -> None:
        self.db_path = Path(db_path)
        self._core = core if core is not None else CoreDatabase(self.db_path)

    # -------------------------------------------------------------------
    # 2.2 — (none) -> ACTIVE
    # -------------------------------------------------------------------

    def start_window_if_eligible(
        self,
        trust_manager: TrustManager,
        *,
        base_duration_hours: float = DEFAULT_BASE_DURATION_HOURS,
        now: datetime,
    ) -> PenaltyWindow | None:
        """
        Guard (2.2): >=1 unconsumed Incident exists with
        created_at > last_closed_at. Consumes EVERY unconsumed candidate
        found, in order -- Incident consumption is unconditional
        (`philosophy.md` 3.8); each one, including the very first, goes
        through `should_extend()` (extension_technical_design.md Section
        4: "Incident consumption ... happens unconditionally ... Only
        the Extension itself is conditional"). If no window is currently
        ACTIVE/FROZEN, the first unconsumed Incident starts one; any
        further unconsumed Incidents in the same call extend it. This is
        one unified path, not "start" and "extend" as two different
        code paths.

        I12: reads confirmed Incidents via the Trust Manager's own
        get_confirmed_incidents_since() -- never a direct read of
        Incident data, which this module does not own and does not
        store (I25).
        """
        last_closed_at = self._last_window_closed_at()
        candidates = trust_manager.get_confirmed_incidents_since(last_closed_at)
        if not candidates:
            return None

        with self._core.transaction() as tx:
            placeholders = ",".join("?" for _ in candidates)
            rows = tx.fetch_all(
                f"SELECT incident_id FROM incident_consumption WHERE incident_id IN ({placeholders})",
                tuple(c.id for c in candidates),
            )
            already_consumed_ids = {row["incident_id"] for row in rows}
        unconsumed = [c for c in candidates if c.id not in already_consumed_ids]
        if not unconsumed:
            return None

        result_window: PenaltyWindow | None = None
        for candidate in unconsumed:
            assessment = trust_manager.get_incident_assessment(candidate.id)
            if assessment is None:
                # Should not happen for a genuinely CONFIRMED Incident
                # (TI15/TI23 guarantee assessment exists once CONFIRMED)
                # -- skipped defensively rather than raised, since this
                # loop may be mid-way through consuming several
                # candidates and one anomalous row should not abort the
                # rest.
                continue
            result_window = self.consume_confirmed_incident(
                candidate.id, candidate.trust_domain, candidate.rule_group_id,
                assessment.intrinsic_severity, assessment.cooperation,
                now=now, base_duration_hours=base_duration_hours,
            )
        return result_window

    def consume_confirmed_incident(
        self,
        incident_id: str,
        trust_domain: str,
        rule_group_id: str,
        intrinsic_severity: SeverityTier,
        cooperation: CooperationAssessment,
        *,
        now: datetime,
        base_duration_hours: float = DEFAULT_BASE_DURATION_HOURS,
    ) -> PenaltyWindow | None:
        """
        Opens its own transaction -- for the event-driven case (a
        consumer handler already inside consume_event()'s transaction),
        call `_consume_confirmed_incident_in_transaction` directly
        against the handler's own already-open `tx` instead (see
        system/README.md for why: NestedTransactionError).

        Takes `intrinsic_severity`/`cooperation` directly rather than a
        full `IncidentAssessment` -- these are the only two fields
        `ExtensionContext` actually needs (EXT-1/EXT-8), and the
        event-driven caller has exactly these two available from the
        `incident.confirmation_changed` payload, never a full
        `IncidentAssessment` object (which would require re-fetching via
        Trust Manager's API, the nested-transaction problem this
        signature exists to avoid).
        """
        def write(tx: Transaction, _state: object):
            return self._consume_confirmed_incident_in_transaction(
                tx, incident_id, trust_domain, rule_group_id, intrinsic_severity, cooperation, now,
                base_duration_hours=base_duration_hours,
            )

        def events(tx: Transaction, _state: object, result) -> None:
            if result is None:
                return
            self._emit_consumption_events(tx, result, now)

        result = apply_transition(self._core, write=write, events=events)
        if result is None:
            return None
        window, _decision, _started = result
        return window

    def _consume_confirmed_incident_in_transaction(
        self,
        tx: Transaction,
        incident_id: str,
        trust_domain: str,
        rule_group_id: str,
        intrinsic_severity: SeverityTier,
        cooperation: CooperationAssessment,
        now: datetime,
        *,
        base_duration_hours: float = DEFAULT_BASE_DURATION_HOURS,
    ) -> tuple[PenaltyWindow, ExtensionDecision, bool] | None:
        """
        Runs entirely against the given, already-open `tx` -- never
        opens its own transaction or calls any other module's public
        API. This is what makes it safely callable from a consumer
        handler already inside consume_event()'s transaction
        (system/startup.py's real Trust Manager -> Penalty Engine
        wiring).

        Returns None (a normal outcome, not an error) if this exact
        incident_id was already consumed (I11's write-once guarantee,
        checked here defensively even though the caller -- consume_event
        -- already deduplicates on event_id; a different event could in
        principle reference the same incident_id, so this check is not
        redundant). Otherwise always returns a
        (PenaltyWindow, ExtensionDecision, started) tuple -- consumption
        is unconditional (philosophy.md 3.8); only the Extension itself
        (assigned_hours) is conditional on should_extend()'s outcome.
        """
        already_consumed = tx.fetch_one("SELECT 1 FROM incident_consumption WHERE incident_id = ?", (incident_id,))
        if already_consumed is not None:
            return None

        window_row = tx.fetch_one(
            "SELECT * FROM penalty_windows WHERE status IN (?, ?) ORDER BY created_at DESC LIMIT 1",
            (PenaltyWindowStatus.ACTIVE.value, PenaltyWindowStatus.FROZEN.value),
        )
        started = window_row is None
        if started:
            window = PenaltyWindow(
                created_at=now, status=PenaltyWindowStatus.ACTIVE,
                base_duration_hours=base_duration_hours, active_period_started_at=now,
            )
            tx.execute(
                """
                INSERT INTO penalty_windows
                    (id, created_at, status, base_duration_hours, extensions_hours,
                     accumulated_active_hours, active_period_started_at)
                VALUES (?, ?, ?, ?, 0, 0, ?)
                """,
                (window.id, _iso(now), window.status.value, base_duration_hours, _iso(now)),
            )
        else:
            window = self._row_to_window(window_row)

        tx.execute(
            "INSERT INTO incident_consumption (incident_id, penalty_window_id, trust_domain, rule_group_id, consumed_at) VALUES (?, ?, ?, ?, ?)",
            (incident_id, window.id, trust_domain, rule_group_id, _iso(now)),
        )

        # EXT-2: scoped to the CURRENT, still-open window only -- counts
        # the row just inserted above, so an isolated occurrence yields 1,
        # not 0.
        same_rule_count = tx.fetch_one(
            "SELECT COUNT(*) as n FROM incident_consumption WHERE penalty_window_id = ? AND rule_group_id = ?",
            (window.id, rule_group_id),
        )["n"]

        # 3.4/EXT-1: an already-interpreted number, computed from this
        # module's OWN state (I5's formula), never a raw window handed
        # to should_extend() to reach into itself.
        remaining_capacity = max(0.0, MAX_TARGET_ACTIVE_HOURS - (window.base_duration_hours + window.extensions_hours))

        context = ExtensionContext(
            intrinsic_severity=intrinsic_severity,
            cooperation=cooperation,
            same_rule_confirmed_incident_count_in_current_window=same_rule_count,
            remaining_active_hour_capacity=remaining_capacity,
            occurred_during_recovery_task=False,  # Recovery Plan does not exist yet (EXT-10, penalty_engine/README.md)
        )
        decision = should_extend(context, incident_id, window.id, now=now)

        tx.execute(
            """
            INSERT INTO extension_decisions
                (id, created_at, incident_id, penalty_window_id, eligible, eligibility_reason,
                 base_hours, mitigation_hours, uncapped_hours, assigned_hours, capacity_limited, explanation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.id, _iso(now), incident_id, window.id, int(decision.eligible), decision.eligibility_reason.value,
                decision.base_hours, decision.mitigation_hours, decision.uncapped_hours,
                decision.assigned_hours, int(decision.capacity_limited), decision.explanation,
            ),
        )

        if decision.assigned_hours > 0:
            tx.execute(
                "UPDATE penalty_windows SET extensions_hours = extensions_hours + ? WHERE id = ?",
                (decision.assigned_hours, window.id),
            )
            window.extensions_hours += decision.assigned_hours  # keep the in-memory copy the caller sees consistent

        return window, decision, started

    def _emit_consumption_events(
        self, tx: Transaction, result: tuple[PenaltyWindow, ExtensionDecision, bool], now: datetime,
    ) -> None:
        """Shared by both consume_confirmed_incident() and the
        consumer-handler path (system/startup.py) -- one place defining
        exactly which events a consumption produces, so the two callers
        can never drift apart on this."""
        window, decision, started = result

        if started:
            write_event(
                tx,
                DomainEvent(
                    event_type="penalty_window.started",
                    source_module="penalty_engine",
                    payload={
                        "penalty_window_id": window.id,
                        "base_duration_hours": window.base_duration_hours,
                        "incident_ids": [decision.incident_id],
                    },
                    occurred_at=now,
                ),
            )

        write_event(
            tx,
            DomainEvent(
                event_type="extension.decision_recorded",
                source_module="penalty_engine",
                payload={
                    "extension_decision_id": decision.id,
                    "incident_id": decision.incident_id,
                    "penalty_window_id": decision.penalty_window_id,
                    "eligible": decision.eligible,
                    "eligibility_reason": decision.eligibility_reason.value,
                    "assigned_hours": decision.assigned_hours,
                    "capacity_limited": decision.capacity_limited,
                },
                occurred_at=now,
            ),
        )

        if decision.assigned_hours > 0:
            write_event(
                tx,
                DomainEvent(
                    event_type="penalty_window.extended",
                    source_module="penalty_engine",
                    payload={"penalty_window_id": window.id, "assigned_hours": decision.assigned_hours},
                    occurred_at=now,
                ),
            )
            write_event(
                tx,
                DomainEvent(
                    event_type="penalty_window.target_duration_changed",
                    source_module="penalty_engine",
                    payload={"penalty_window_id": window.id, "new_target_active_hours": window.base_duration_hours + window.extensions_hours},
                    occurred_at=now,
                ),
            )

    def recover_penalty_window_state(self, now: datetime) -> None:
        """
        Naming matches system_state_machine.md Section 7's
        `recover_penalty_window_state(db, now)` exactly, for the startup
        orchestrator to call uniformly across modules. A thin wrapper --
        Penalty Engine's actual reconciliation logic is
        ensure_current_state() (4.4/4.5), already correct for both
        interactive calls and startup; no separate recovery algorithm
        was needed.
        """
        self.ensure_current_state(now)


    def freeze(
        self,
        penalty_window_id: str,
        reason: FreezeReason,
        *,
        now: datetime,
        exemption_id: str | None = None,
        authorization_decision_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> FreezePeriod:
        """
        Always just an INSERT of a new FreezePeriod. accumulated_active_hours
        is updated ONLY on the first freeze (ACTIVE -> FROZEN transition)
        -- a second concurrent reason on an already-FROZEN window has no
        time effect, since the countdown is already stopped (2.3).
        """
        return self._freeze_in_transaction(
            penalty_window_id, reason, now=now,
            exemption_id=exemption_id, authorization_decision_id=authorization_decision_id,
            expires_at=expires_at,
        )

    def emergency_freeze(self, penalty_window_id: str, *, now: datetime) -> FreezePeriod:
        """
        2.4/I16: a separate, minimal function -- no dependency on
        should_evaluate_exemption() or any legitimacy logic, no import of
        ollama_client/coach_engine/keyholder_engine/decision_engine (this
        module never imports any of those regardless of which function is
        called -- I16 is satisfied structurally, not merely by this
        function's own brevity). Produces the identical data effect as
        freeze(reason=EMERGENCY_OVERRIDE), plus the additional
        emergency_override.triggered event (domain_events_catalog.md
        Finding 5).
        """
        return self._freeze_in_transaction(
            penalty_window_id, FreezeReason.EMERGENCY_OVERRIDE, now=now, emit_emergency_event=True,
        )

    def _freeze_in_transaction(
        self,
        penalty_window_id: str,
        reason: FreezeReason,
        *,
        now: datetime,
        exemption_id: str | None = None,
        authorization_decision_id: str | None = None,
        expires_at: datetime | None = None,
        emit_emergency_event: bool = False,
    ) -> FreezePeriod:
        freeze_period = FreezePeriod(
            penalty_window_id=penalty_window_id, started_at=now, reason=reason,
            exemption_id=exemption_id, authorization_decision_id=authorization_decision_id,
            expires_at=expires_at,
        )

        def write(tx: Transaction, _state: object) -> tuple[FreezePeriod, bool]:
            window_row = tx.fetch_one("SELECT * FROM penalty_windows WHERE id = ?", (penalty_window_id,))
            if window_row is None:
                raise PenaltyWindowNotFound(penalty_window_id)

            was_first_freeze = window_row["status"] == PenaltyWindowStatus.ACTIVE.value

            tx.execute(
                """
                INSERT INTO freeze_periods
                    (id, penalty_window_id, started_at, reason, exemption_id, authorization_decision_id, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    freeze_period.id, penalty_window_id, _iso(now), reason.value,
                    exemption_id, authorization_decision_id, _iso(expires_at) if expires_at else None,
                ),
            )

            if was_first_freeze:
                accumulated = active_hours_elapsed_from_row(window_row, now)
                tx.execute(
                    "UPDATE penalty_windows SET status = ?, accumulated_active_hours = ?, active_period_started_at = NULL WHERE id = ?",
                    (PenaltyWindowStatus.FROZEN.value, accumulated, penalty_window_id),
                )

            return freeze_period, was_first_freeze

        def events(tx: Transaction, _state: object, result: tuple[FreezePeriod, bool]) -> None:
            fp, was_first = result
            write_event(
                tx,
                DomainEvent(
                    event_type="freeze_periods.opened",
                    source_module="penalty_engine",
                    payload={"freeze_period_id": fp.id, "penalty_window_id": penalty_window_id, "reason": reason.value},
                    occurred_at=now,
                ),
            )
            if was_first:
                write_event(
                    tx,
                    DomainEvent(
                        event_type="penalty_window.frozen",
                        source_module="penalty_engine",
                        payload={"penalty_window_id": penalty_window_id, "reason": reason.value},
                        occurred_at=now,
                    ),
                )
            if emit_emergency_event:
                write_event(
                    tx,
                    DomainEvent(
                        event_type="emergency_override.triggered",
                        source_module="penalty_engine",
                        payload={"penalty_window_id": penalty_window_id, "freeze_period_id": fp.id},
                        occurred_at=now,
                    ),
                )

        result_fp, _ = apply_transition(self._core, write=write, events=events)
        return result_fp

    def resume(self, penalty_window_id: str, reason: FreezeReason, *, now: datetime) -> None:
        """
        Closes the open FreezePeriod matching `reason` (the most recently
        opened one, if more than one somehow exists -- see
        penalty_engine/README.md for why this default was chosen). The
        window returns to ACTIVE only once count_open_freeze_periods == 0
        (I22/PW-FREEZE-SET) -- closing one of several concurrently open
        reasons, with another still open, changes neither status nor
        emits penalty_window.resumed.
        """
        self._close_freeze_period_in_transaction(
            penalty_window_id, reason, now=now, end_reason=FreezeEndReason.RESUMED_NORMALLY,
        )

    def _close_freeze_period_in_transaction(
        self, penalty_window_id: str, reason: FreezeReason, *, now: datetime, end_reason: FreezeEndReason,
        freeze_period_id: str | None = None,
    ) -> None:
        def write(tx: Transaction, _state: object) -> bool:
            if freeze_period_id is not None:
                row = tx.fetch_one(
                    "SELECT * FROM freeze_periods WHERE id = ? AND ended_at IS NULL", (freeze_period_id,),
                )
            else:
                row = tx.fetch_one(
                    """
                    SELECT * FROM freeze_periods
                    WHERE penalty_window_id = ? AND reason = ? AND ended_at IS NULL
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (penalty_window_id, reason.value),
                )
            if row is None:
                return False  # nothing open to close -- a harmless no-op, not an error

            tx.execute(
                "UPDATE freeze_periods SET ended_at = ?, end_reason = ? WHERE id = ?",
                (_iso(now), end_reason.value, row["id"]),
            )

            remaining_open = tx.fetch_one(
                "SELECT COUNT(*) as n FROM freeze_periods WHERE penalty_window_id = ? AND ended_at IS NULL",
                (penalty_window_id,),
            )["n"]

            became_active_again = remaining_open == 0
            if became_active_again:
                tx.execute(
                    "UPDATE penalty_windows SET status = ?, active_period_started_at = ? WHERE id = ?",
                    (PenaltyWindowStatus.ACTIVE.value, _iso(now), penalty_window_id),
                )
            return became_active_again

        def events(tx: Transaction, _state: object, became_active_again: bool) -> None:
            write_event(
                tx,
                DomainEvent(
                    event_type="freeze_periods.closed",
                    source_module="penalty_engine",
                    payload={"penalty_window_id": penalty_window_id, "reason": reason.value, "end_reason": end_reason.value},
                    occurred_at=now,
                ),
            )
            if end_reason == FreezeEndReason.EXPIRED:
                write_event(
                    tx,
                    DomainEvent(
                        event_type="penalty_engine.freeze_expired",
                        source_module="penalty_engine",
                        payload={"penalty_window_id": penalty_window_id, "reason": reason.value},
                        occurred_at=now,
                    ),
                )
            if became_active_again:
                write_event(
                    tx,
                    DomainEvent(
                        event_type="penalty_window.resumed",
                        source_module="penalty_engine",
                        payload={"penalty_window_id": penalty_window_id},
                        occurred_at=now,
                    ),
                )

        apply_transition(self._core, write=write, events=events)

    # -------------------------------------------------------------------
    # 4.4, 4.5 — Completion Detection and Startup Reconciliation
    # -------------------------------------------------------------------

    def ensure_current_state(self, now: datetime) -> PenaltyWindow | None:
        """
        The mandatory precondition (4.4): called at the start of every
        operation that depends on the window's state, AND at process
        startup (4.5), before accepting any new request. First closes any
        expired freeze (4.5's expires_at rule), then checks completion --
        both against the SAME `now`, so a window that both un-freezes and
        immediately completes in the same call is handled correctly, not
        left one step behind.
        """
        window = self.get_active_or_frozen_penalty_window()
        if window is None:
            return None

        self._close_expired_freezes(window.id, now)
        window = self.get_active_or_frozen_penalty_window()
        if window is None:
            return None

        if window.status == PenaltyWindowStatus.ACTIVE and is_complete(window, now):
            self._complete_window(window.id, now)
            return None

        return window

    def _close_expired_freezes(self, penalty_window_id: str, now: datetime) -> None:
        with self._core.transaction() as tx:
            expired = tx.fetch_all(
                "SELECT * FROM freeze_periods WHERE penalty_window_id = ? AND ended_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?",
                (penalty_window_id, _iso(now)),
            )
        for row in expired:
            self._close_freeze_period_in_transaction(
                penalty_window_id, FreezeReason(row["reason"]), now=now,
                end_reason=FreezeEndReason.EXPIRED, freeze_period_id=row["id"],
            )

    def _complete_window(self, penalty_window_id: str, now: datetime) -> None:
        def write(tx: Transaction, _state: object) -> None:
            tx.execute(
                "UPDATE penalty_windows SET status = ?, closed_at = ?, resolution_method = ? WHERE id = ?",
                (PenaltyWindowStatus.COMPLETED.value, _iso(now), ResolutionMethod.COUNTDOWN_COMPLETE.value, penalty_window_id),
            )

        def events(tx: Transaction, _state: object, _result: None) -> None:
            write_event(
                tx,
                DomainEvent(
                    event_type="penalty_window.completed",
                    source_module="penalty_engine",
                    payload={"penalty_window_id": penalty_window_id, "resolution_method": ResolutionMethod.COUNTDOWN_COMPLETE.value},
                    occurred_at=now,
                ),
            )

        apply_transition(self._core, write=write, events=events)

    # -------------------------------------------------------------------
    # Reads
    # -------------------------------------------------------------------

    def get_active_or_frozen_penalty_window(self) -> PenaltyWindow | None:
        with self._core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT * FROM penalty_windows WHERE status IN (?, ?) ORDER BY created_at DESC LIMIT 1",
                (PenaltyWindowStatus.ACTIVE.value, PenaltyWindowStatus.FROZEN.value),
            )
        return self._row_to_window(row) if row else None

    def _last_window_closed_at(self) -> datetime:
        with self._core.transaction() as tx:
            row = tx.fetch_one("SELECT MAX(closed_at) as m FROM penalty_windows WHERE status = ?", (PenaltyWindowStatus.COMPLETED.value,))
        if row is None or row["m"] is None:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
        return _parse_iso(row["m"])

    def get_authorization_freeze_state(self, authorization_decision_id: str) -> AuthorizationFreezeState:
        """The ONLY permitted way for Activity Authorization (or any
        future module) to query freeze state tied to its own decision (2.5)."""
        with self._core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT * FROM freeze_periods WHERE authorization_decision_id = ? ORDER BY started_at DESC LIMIT 1",
                (authorization_decision_id,),
            )
        if row is None:
            return AuthorizationFreezeState.NOT_FOUND
        if row["ended_at"] is None:
            return AuthorizationFreezeState.OPEN
        if row["end_reason"] == FreezeEndReason.EXPIRED.value:
            return AuthorizationFreezeState.EXPIRED
        return AuthorizationFreezeState.CLOSED

    def get_penalty_window_relevant_domains(self, penalty_window_id: str) -> frozenset[str]:
        """The ONLY permitted way for another module to learn which Trust
        domain(s) relate to a window (2.6). Raises PenaltyWindowNotFound
        if the window itself does not exist."""
        with self._core.transaction() as tx:
            window_row = tx.fetch_one("SELECT id FROM penalty_windows WHERE id = ?", (penalty_window_id,))
            if window_row is None:
                raise PenaltyWindowNotFound(penalty_window_id)
            rows = tx.fetch_all(
                "SELECT DISTINCT trust_domain FROM incident_consumption WHERE penalty_window_id = ?",
                (penalty_window_id,),
            )
        return frozenset(r["trust_domain"] for r in rows)

    @staticmethod
    def _row_to_window(row) -> PenaltyWindow:
        return PenaltyWindow(
            id=row["id"],
            created_at=_parse_iso(row["created_at"]),
            status=PenaltyWindowStatus(row["status"]),
            closed_at=_parse_iso(row["closed_at"]) if row["closed_at"] else None,
            resolution_method=ResolutionMethod(row["resolution_method"]) if row["resolution_method"] else None,
            base_duration_hours=row["base_duration_hours"],
            extensions_hours=row["extensions_hours"],
            accumulated_active_hours=row["accumulated_active_hours"],
            active_period_started_at=_parse_iso(row["active_period_started_at"]) if row["active_period_started_at"] else None,
        )
