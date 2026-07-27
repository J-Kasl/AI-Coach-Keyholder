"""
recovery_plan/repository.py

Recovery Plan — built on infrastructure.database.Database, the same
composition pattern every other repository in this system uses. Every
lifecycle transition here is a REACTION to a Penalty Window event
(RP-6) — there is no code path by which this module initiates a
Penalty Window state change (2.5).

Canonical spec: docs/architecture/recovery_plan_technical_design.md.
See recovery_plan/README.md for exactly what this slice covers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from infrastructure.database import Database as CoreDatabase
from infrastructure.database import Transaction, apply_transition
from infrastructure.outbox import DomainEvent, write_event
from recovery_plan.models import (
    RecoveryPlan,
    RecoveryPlanStatus,
    RecoveryTask,
    RecoveryTaskCompletion,
    RecoveryTaskStatus,
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class RecoveryPlanNotFoundError(LookupError):
    def __init__(self, identifier: str) -> None:
        super().__init__(f"No RecoveryPlan found for {identifier!r}")


class RecoveryTaskNotFoundError(LookupError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"No RecoveryTask with id={task_id!r}")
        self.task_id = task_id


class RecoveryPlanManager:
    def __init__(self, db_path: str | Path, *, core: CoreDatabase | None = None) -> None:
        self.db_path = Path(db_path)
        self._core = core if core is not None else CoreDatabase(self.db_path)

    # -------------------------------------------------------------------
    # 4 — Lifecycle: reactions to Penalty Window events
    # -------------------------------------------------------------------
    # Every _*_in_transaction method here operates purely against the
    # given, already-open `tx` and reads only the event's own payload --
    # never calls PenaltyEngine's public API mid-transaction (the same
    # NestedTransactionError lesson documented in system/README.md and
    # implementation_conventions.md Section 3). Every payload these
    # handlers need (penalty_window_id, base_duration_hours,
    # new_target_active_hours) is already present on the existing
    # penalty_window.* events -- no payload extension was needed for
    # this integration.

    def _create_plan_in_transaction(
        self, tx: Transaction, penalty_window_id: str, base_duration_hours: float, now: datetime,
    ) -> RecoveryPlan:
        """Reacting to penalty_window.started (RP-7). I3:
        recovery_credit_capacity_hours = target_active_hours / 2; at
        pure creation, extensions_hours is always 0, so target ==
        base_duration_hours."""
        plan = RecoveryPlan(
            penalty_window_id=penalty_window_id,
            status=RecoveryPlanStatus.ACTIVE,
            recovery_credit_capacity_hours=base_duration_hours / 2.0,
            created_at=now,
            status_changed_at=now,
        )
        tx.execute(
            """
            INSERT INTO recovery_plans
                (id, penalty_window_id, status, current_version, recovery_credit_capacity_hours, created_at, status_changed_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            """,
            (plan.id, penalty_window_id, plan.status.value, plan.recovery_credit_capacity_hours, _iso(now), _iso(now)),
        )
        return plan

    def _mirror_status_in_transaction(
        self, tx: Transaction, penalty_window_id: str, new_status: RecoveryPlanStatus, now: datetime,
    ) -> RecoveryPlan | None:
        """Reacting to penalty_window.frozen/.resumed/.completed (RP-6) --
        status is a pure projection, never independently decided.
        Returns None (a detectable anomaly, not silently ignored) if no
        plan exists for this window -- recover_recovery_plan_state()
        (8) is what surfaces this condition at startup."""
        row = tx.fetch_one("SELECT * FROM recovery_plans WHERE penalty_window_id = ?", (penalty_window_id,))
        if row is None:
            return None
        tx.execute(
            "UPDATE recovery_plans SET status = ?, status_changed_at = ? WHERE penalty_window_id = ?",
            (new_status.value, _iso(now), penalty_window_id),
        )
        return self._row_to_plan(row, status=new_status, status_changed_at=now)

    def _regenerate_in_transaction(
        self, tx: Transaction, penalty_window_id: str, new_target_active_hours: float, now: datetime,
    ) -> RecoveryPlan | None:
        """
        Reacting to penalty_window.target_duration_changed (3.4). RP-4:
        never touches an already-COMPLETED task or its
        RecoveryTaskCompletion -- only PROPOSED/ACCEPTED tasks under the
        PREVIOUS version transition to EXPIRED. Returns None if no plan
        exists (same anomaly handling as _mirror_status_in_transaction).
        """
        row = tx.fetch_one("SELECT * FROM recovery_plans WHERE penalty_window_id = ?", (penalty_window_id,))
        if row is None:
            return None

        new_version = row["current_version"] + 1
        new_capacity = new_target_active_hours / 2.0

        tx.execute(
            """
            UPDATE recovery_plans
            SET current_version = ?, recovery_credit_capacity_hours = ?, status_changed_at = ?
            WHERE penalty_window_id = ?
            """,
            (new_version, new_capacity, _iso(now), penalty_window_id),
        )
        tx.execute(
            """
            UPDATE recovery_tasks
            SET status = ?, status_changed_at = ?
            WHERE recovery_plan_id = ? AND plan_version = ? AND status IN (?, ?)
            """,
            (
                RecoveryTaskStatus.EXPIRED.value, _iso(now),
                row["id"], row["current_version"],
                RecoveryTaskStatus.PROPOSED.value, RecoveryTaskStatus.ACCEPTED.value,
            ),
        )
        return self._row_to_plan(row, current_version=new_version, recovery_credit_capacity_hours=new_capacity, status_changed_at=now)

    # -------------------------------------------------------------------
    # Public wrappers -- open their own transaction, for direct/manual
    # calls and for tests. The event-driven path (system/startup.py)
    # calls the *_in_transaction methods above directly, against its own
    # already-open consumer transaction.
    # -------------------------------------------------------------------

    def create_plan(self, penalty_window_id: str, base_duration_hours: float, *, now: datetime) -> RecoveryPlan:
        def write(tx: Transaction, _state: object) -> RecoveryPlan:
            return self._create_plan_in_transaction(tx, penalty_window_id, base_duration_hours, now)

        def events(tx: Transaction, _state: object, result: RecoveryPlan) -> None:
            write_event(
                tx,
                DomainEvent(
                    event_type="recovery_plan.created", source_module="recovery_plan",
                    payload={"recovery_plan_id": result.id, "penalty_window_id": penalty_window_id},
                    occurred_at=now,
                ),
            )

        return apply_transition(self._core, write=write, events=events)

    def mirror_frozen(self, penalty_window_id: str, *, now: datetime) -> RecoveryPlan | None:
        return self._mirror_status_and_emit(penalty_window_id, RecoveryPlanStatus.FROZEN, "recovery_plan.frozen", now)

    def mirror_resumed(self, penalty_window_id: str, *, now: datetime) -> RecoveryPlan | None:
        return self._mirror_status_and_emit(penalty_window_id, RecoveryPlanStatus.ACTIVE, "recovery_plan.resumed", now)

    def complete_plan(self, penalty_window_id: str, *, now: datetime) -> RecoveryPlan | None:
        return self._mirror_status_and_emit(penalty_window_id, RecoveryPlanStatus.COMPLETED, "recovery_plan.completed", now)

    def _mirror_status_and_emit(
        self, penalty_window_id: str, new_status: RecoveryPlanStatus, event_type: str, now: datetime,
    ) -> RecoveryPlan | None:
        def write(tx: Transaction, _state: object) -> RecoveryPlan | None:
            return self._mirror_status_in_transaction(tx, penalty_window_id, new_status, now)

        def events(tx: Transaction, _state: object, result: RecoveryPlan | None) -> None:
            if result is not None:
                write_event(
                    tx,
                    DomainEvent(
                        event_type=event_type, source_module="recovery_plan",
                        payload={"recovery_plan_id": result.id, "penalty_window_id": penalty_window_id},
                        occurred_at=now,
                    ),
                )

        return apply_transition(self._core, write=write, events=events)

    def regenerate(self, penalty_window_id: str, new_target_active_hours: float, *, now: datetime) -> RecoveryPlan | None:
        def write(tx: Transaction, _state: object) -> RecoveryPlan | None:
            return self._regenerate_in_transaction(tx, penalty_window_id, new_target_active_hours, now)

        def events(tx: Transaction, _state: object, result: RecoveryPlan | None) -> None:
            if result is not None:
                write_event(
                    tx,
                    DomainEvent(
                        event_type="recovery_plan.regenerated", source_module="recovery_plan",
                        payload={
                            "recovery_plan_id": result.id, "penalty_window_id": penalty_window_id,
                            "new_version": result.current_version, "new_capacity_hours": result.recovery_credit_capacity_hours,
                        },
                        occurred_at=now,
                    ),
                )

        return apply_transition(self._core, write=write, events=events)

    # -------------------------------------------------------------------
    # Coach-facing task management
    # -------------------------------------------------------------------

    def propose_task(
        self, recovery_plan_id: str, title: str, description: str, credit_hours: float, *, now: datetime,
    ) -> RecoveryTask:
        def write(tx: Transaction, _state: object) -> RecoveryTask:
            plan_row = tx.fetch_one("SELECT * FROM recovery_plans WHERE id = ?", (recovery_plan_id,))
            if plan_row is None:
                raise RecoveryPlanNotFoundError(recovery_plan_id)
            task = RecoveryTask(
                recovery_plan_id=recovery_plan_id, plan_version=plan_row["current_version"],
                title=title, description=description, credit_hours=credit_hours,
                created_at=now, status_changed_at=now,
            )
            tx.execute(
                """
                INSERT INTO recovery_tasks
                    (id, recovery_plan_id, plan_version, title, description, credit_hours, status, created_at, status_changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task.id, recovery_plan_id, task.plan_version, title, description, credit_hours,
                 task.status.value, _iso(now), _iso(now)),
            )
            return task

        def events(tx: Transaction, _state: object, result: RecoveryTask) -> None:
            write_event(
                tx,
                DomainEvent(
                    event_type="recovery_plan.task_proposed", source_module="recovery_plan",
                    payload={"recovery_task_id": result.id, "recovery_plan_id": recovery_plan_id, "credit_hours": credit_hours},
                    occurred_at=now,
                ),
            )

        return apply_transition(self._core, write=write, events=events)

    def accept_task(self, task_id: str, *, now: datetime) -> None:
        self._transition_task(task_id, RecoveryTaskStatus.ACCEPTED, "recovery_plan.task_accepted", now)

    def withdraw_task(self, task_id: str, *, now: datetime) -> None:
        self._transition_task(task_id, RecoveryTaskStatus.WITHDRAWN, "recovery_plan.task_withdrawn", now)

    def _transition_task(self, task_id: str, new_status: RecoveryTaskStatus, event_type: str, now: datetime) -> None:
        def write(tx: Transaction, _state: object) -> RecoveryTask:
            row = tx.fetch_one("SELECT * FROM recovery_tasks WHERE id = ?", (task_id,))
            if row is None:
                raise RecoveryTaskNotFoundError(task_id)
            tx.execute(
                "UPDATE recovery_tasks SET status = ?, status_changed_at = ? WHERE id = ?",
                (new_status.value, _iso(now), task_id),
            )
            return self._row_to_task(row, status=new_status, status_changed_at=now)

        def events(tx: Transaction, _state: object, result: RecoveryTask) -> None:
            write_event(
                tx,
                DomainEvent(
                    event_type=event_type, source_module="recovery_plan",
                    payload={"recovery_task_id": task_id, "recovery_plan_id": result.recovery_plan_id},
                    occurred_at=now,
                ),
            )

        apply_transition(self._core, write=write, events=events)

    def complete_task(self, task_id: str, *, now: datetime, notes: str | None = None) -> RecoveryTaskCompletion:
        """
        RP-2: Recovery Plan's own interpretation that a task was
        genuinely completed. Publishes `recovery_plan.task_completed` --
        **this is the event the Penalty Engine will consume** (Section
        6, deferred to the Recovery Credit integration slice) to decide
        how many hours to credit. This module never writes to
        `recovery_credit_ledger` itself (RP-1, RP-8).
        """
        def write(tx: Transaction, _state: object) -> tuple[RecoveryTask, RecoveryTaskCompletion]:
            row = tx.fetch_one("SELECT * FROM recovery_tasks WHERE id = ?", (task_id,))
            if row is None:
                raise RecoveryTaskNotFoundError(task_id)
            tx.execute(
                "UPDATE recovery_tasks SET status = ?, status_changed_at = ? WHERE id = ?",
                (RecoveryTaskStatus.COMPLETED.value, _iso(now), task_id),
            )
            completion = RecoveryTaskCompletion(
                recovery_task_id=task_id, recovery_plan_id=row["recovery_plan_id"], created_at=now, notes=notes,
            )
            tx.execute(
                "INSERT INTO recovery_task_completions (id, recovery_task_id, recovery_plan_id, created_at, notes) VALUES (?, ?, ?, ?, ?)",
                (completion.id, task_id, row["recovery_plan_id"], _iso(now), notes),
            )
            task = self._row_to_task(row, status=RecoveryTaskStatus.COMPLETED, status_changed_at=now)
            return task, completion

        def events(tx: Transaction, _state: object, result: tuple[RecoveryTask, RecoveryTaskCompletion]) -> None:
            _task, completion = result
            write_event(
                tx,
                DomainEvent(
                    event_type="recovery_plan.task_completed", source_module="recovery_plan",
                    payload={
                        "recovery_task_completion_id": completion.id,
                        "recovery_task_id": completion.recovery_task_id,
                        "recovery_plan_id": completion.recovery_plan_id,
                    },
                    occurred_at=now,
                ),
            )

        _task, completion = apply_transition(self._core, write=write, events=events)
        return completion

    # -------------------------------------------------------------------
    # 2.3 — Narrow Public Read API
    # -------------------------------------------------------------------

    def get_recovery_task_completion(self, completion_id: str) -> RecoveryTaskCompletion | None:
        """The ONLY permitted way for the Penalty Engine to read a
        completed task judgment (Section 6)."""
        with self._core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM recovery_task_completions WHERE id = ?", (completion_id,))
        if row is None:
            return None
        return RecoveryTaskCompletion(
            id=row["id"], recovery_task_id=row["recovery_task_id"], recovery_plan_id=row["recovery_plan_id"],
            created_at=_parse_iso(row["created_at"]), notes=row["notes"],
        )

    def get_recovery_task(self, task_id: str) -> RecoveryTask | None:
        """The ONLY permitted way for the Penalty Engine to read
        `credit_hours` when deciding how much to actually credit
        (Section 6) -- a companion read function to
        get_recovery_task_completion(), for the same consumer."""
        with self._core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM recovery_tasks WHERE id = ?", (task_id,))
        return self._row_to_task(row) if row else None

    def get_recovery_plan_for_window(self, penalty_window_id: str) -> RecoveryPlan | None:
        with self._core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM recovery_plans WHERE penalty_window_id = ?", (penalty_window_id,))
        return self._row_to_plan(row) if row else None

    # -------------------------------------------------------------------
    # 8 — Crash Recovery (consistency check only)
    # -------------------------------------------------------------------

    def recover_recovery_plan_state(self, now: datetime) -> list[str]:
        """
        Consistency check only (8) -- NOT a reconciliation of pending
        timeouts (there are none; see recovery_plan/README.md). For
        every ACTIVE/FROZEN Penalty Window, confirms a RecoveryPlan
        exists in a matching status. Returns the list of
        penalty_window_ids found WITHOUT a corresponding plan -- this
        function detects and flags the anomaly; it does not silently
        create the missing plan itself (that would mask a bug in the
        normal at-least-once outbox redelivery path, which is what
        should have created it).
        """
        with self._core.transaction() as tx:
            windows = tx.fetch_all(
                "SELECT id, status FROM penalty_windows WHERE status IN ('active', 'frozen')"
            )
            missing: list[str] = []
            for w in windows:
                plan_row = tx.fetch_one("SELECT status FROM recovery_plans WHERE penalty_window_id = ?", (w["id"],))
                if plan_row is None or plan_row["status"] != w["status"]:
                    missing.append(w["id"])
        return missing

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _row_to_plan(row, **overrides) -> RecoveryPlan:
        values = {
            "id": row["id"], "penalty_window_id": row["penalty_window_id"],
            "status": RecoveryPlanStatus(row["status"]), "current_version": row["current_version"],
            "recovery_credit_capacity_hours": row["recovery_credit_capacity_hours"],
            "created_at": _parse_iso(row["created_at"]), "status_changed_at": _parse_iso(row["status_changed_at"]),
        }
        values.update(overrides)
        return RecoveryPlan(**values)

    @staticmethod
    def _row_to_task(row, **overrides) -> RecoveryTask:
        values = {
            "id": row["id"], "recovery_plan_id": row["recovery_plan_id"], "plan_version": row["plan_version"],
            "title": row["title"], "description": row["description"], "credit_hours": row["credit_hours"],
            "status": RecoveryTaskStatus(row["status"]),
            "created_at": _parse_iso(row["created_at"]), "status_changed_at": _parse_iso(row["status_changed_at"]),
        }
        values.update(overrides)
        return RecoveryTask(**values)
