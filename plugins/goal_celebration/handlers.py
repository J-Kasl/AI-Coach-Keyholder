"""
plugins/goal_celebration/handlers.py

Reacts to `goal.completed` (goal_management) -- explicitly *not* a
`Decision` (relationship_decision_engine_technical_design.md); this
plugin never claims to be a system decision, just a plugin-authored,
clearly-scoped celebration (plugin_architecture_proposal.md Section
20, Decision 4's "no forged authority" rule).

**Deliberately does not call any `PluginSDK` read method (e.g. what
would be `sdk.get_goal()`) from inside this handler.** A real,
structural limitation was found while first writing this handler: a
`PluginSDK` read method delegates directly to a domain module's own
public getter (`infrastructure/plugin_sdk.py`'s own docstring), and
every such getter opens its own transaction
(`with self._core.transaction() as tx: ...`) -- safe when called from
an ordinary, no-transaction-open context (e.g. a command handler), but
this handler already runs *inside* `consume_event()`'s own
transaction, so calling one raises `NestedTransactionError` the exact
same way the write-side bug (fixed in `repository.py`, see its
`has_been_celebrated_in_transaction()` docstring) did. This affects
*every* SDK read method, not only `get_goal()`. Not resolved by this
plugin -- flagged in `plugin_architecture_proposal.md` Section 26,
Open Question 6, where the fix's shape has since been **decided**
(v1.5): explicit `_in_transaction`-suffixed read variants (e.g. a
future `sdk.get_goal_in_transaction(tx, goal_id)`), mirroring
`publish_event`/`publish_event_in_transaction` exactly -- implementation
is its own separate infrastructure step, not yet built. This handler
avoids the problem entirely by not needing the read at all --
`event.payload["goal_group_id"]` already carries everything a minimal
celebration needs.
"""

from __future__ import annotations

from infrastructure.database import Transaction
from infrastructure.outbox import ClaimedDomainEvent
from plugins.goal_celebration.repository import GoalCelebrationRepository

__all__ = ["build_event_consumers", "build_commands"]


def build_event_consumers(sdk, repo: GoalCelebrationRepository):
    def on_goal_completed(tx: Transaction, event: ClaimedDomainEvent) -> None:
        goal_group_id = event.payload["goal_group_id"]

        # Idempotency check. Redelivery of the SAME event is already
        # independently prevented by consume_event()'s own dedup
        # (domain_event_consumers) -- this check exists as a second,
        # narrower guard specifically against celebrating the same
        # Goal twice for any other reason (e.g. two different domain
        # events both eventually implying the same Goal is complete).
        if repo.has_been_celebrated_in_transaction(tx, goal_group_id):
            return

        # A real message would go through the future Communication
        # Layer once one exists (ai_identity_technical_design.md) --
        # for this slice, recording that a celebration happened (via
        # the log row + the published event below) is enough to prove
        # the pipe works end-to-end; no send path exists yet to plug
        # this into, and no domain read is needed to do it.

        repo.mark_celebrated_in_transaction(tx, goal_group_id, now=event.occurred_at)

        # publish_event_in_transaction, NOT publish_event -- this
        # handler is already running inside consume_event()'s own
        # transaction (`tx`); calling publish_event() here would open
        # a SECOND transaction and raise NestedTransactionError (see
        # infrastructure/plugin_sdk.py's own docstring for the full
        # explanation -- a real bug found and fixed while writing this
        # plugin, not a hypothetical one).
        sdk.publish_event_in_transaction(
            tx, "plugin_goal_celebration.sent", {"goal_group_id": goal_group_id}, now=event.occurred_at,
        )

    return {"goal.completed": on_goal_completed}


def build_commands(sdk, repo: GoalCelebrationRepository):
    return {}
