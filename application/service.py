"""
application/service.py

ApplicationService.handle_message() is THE boundary between any
adapter (bot/discord_bot.py today) and the rest of this system.
Channel-agnostic in, channel-agnostic out (IncomingMessage ->
OutgoingMessage) -- an adapter never needs to know which domain modules
exist, and this layer never needs to know which channel it's being
called from.

Only calls domain modules through their PUBLIC read APIs
(get_active_or_frozen_penalty_window(), etc.) -- never a
`_*_in_transaction` method, the same boundary every consumer handler in
`system/startup.py` respects for a different reason (transactional
self-containment there; here, simply because those methods are not
this layer's to call).
"""

from __future__ import annotations

import logging
from pathlib import Path

from application.models import IncomingMessage, OutgoingMessage
from application.router import CommandRouter, RequestContext
from application.user_service import UserService
from goal_management.repository import GoalManager
from infrastructure.database import Database as CoreDatabase
from penalty_engine.repository import PenaltyEngine
from penalty_engine.window import remaining_active_hours, target_active_hours
from recovery_plan.repository import RecoveryPlanManager
from trust_manager.repository import TrustManager

logger = logging.getLogger("ai_coach_keyholder.application")


class ApplicationService:
    def __init__(self, db_path: str | Path, *, core: CoreDatabase | None = None) -> None:
        self.db_path = Path(db_path)
        self._core = core if core is not None else CoreDatabase(self.db_path)

        self.user_service = UserService(self.db_path, core=self._core)
        self.trust_manager = TrustManager(self.db_path, core=self._core)
        self.penalty_engine = PenaltyEngine(self.db_path, core=self._core)
        self.recovery_plan = RecoveryPlanManager(self.db_path, core=self._core)
        self.goal_management = GoalManager(self.db_path, core=self._core)

        self.router = CommandRouter()
        self._register_commands()

    def handle_message(self, incoming: IncomingMessage) -> OutgoingMessage:
        """
        The single entry point every adapter calls. Never raises --
        any exception from user resolution or routing is caught,
        logged, and turned into a safe, generic reply. An adapter still
        wraps its OWN call to this method in its own try/except too
        (defense in depth, the same layering this project already uses
        elsewhere -- e.g. `consume_event()`'s dedup plus
        `UNIQUE(completion_id)` as a second, independent guard) in case
        this method's own plumbing (e.g. opening the DB) fails before
        any of this function's own code runs.
        """
        try:
            user = self.user_service.get_or_create_user(
                incoming.channel, incoming.external_user_id, now=incoming.received_at,
            )
            context = RequestContext(user=user, now=incoming.received_at)
            return self.router.route(incoming.text, context)
        except Exception:
            logger.exception("ApplicationService.handle_message failed for channel=%r", incoming.channel)
            return OutgoingMessage(text="Something went wrong handling that. It's been logged.")

    # -------------------------------------------------------------------
    # Explicit, limited command set (per this phase's own scope --
    # not a natural-language router, not full Coach/Keyholder reasoning)
    # -------------------------------------------------------------------

    def _register_commands(self) -> None:
        self.router.register("help", "Show this list of commands", self._handle_help)
        self.router.register("status", "Show the current penalty window, if any", self._handle_status)

    def _handle_help(self, ctx: RequestContext) -> OutgoingMessage:
        return OutgoingMessage(text=self.router.help_text())

    def _handle_status(self, ctx: RequestContext) -> OutgoingMessage:
        """
        Deliberately scoped to Penalty Engine only in this slice --
        TrustManager/GoalManager have no "list everything relevant"
        read API yet (both are designed around looking up a specific
        domain_id/goal_group_id, which this generic status command
        does not have). Adding one is a real future need, not invented
        here just to make this command feel more complete.
        """
        window = self.penalty_engine.get_active_or_frozen_penalty_window()
        if window is None:
            return OutgoingMessage(text="No active penalty window right now.")
        remaining = remaining_active_hours(window, ctx.now)
        target = target_active_hours(window)
        return OutgoingMessage(
            text=(
                f"Penalty window: {window.status.value}\n"
                f"~{remaining:.1f}h remaining of {target:.1f}h target."
            ),
        )
