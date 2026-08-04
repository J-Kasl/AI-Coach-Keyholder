"""
conversation_engine/providers.py

The provider contract (conversation_engine_technical_design.md Section
6). A structural Protocol, matching this project's own one existing
precedent (infrastructure/clock.py's `Clock(Protocol)`) rather than an
abstract base class -- any object with a matching `namespace`
property and `provide_context()` method satisfies it.

Slice 1 uses a small, explicit, hand-written list of providers --
deliberately no dynamic discovery, no registry (Slice 5's own future
territory, per the design document's roadmap).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from conversation_engine.models import ConversationContextFragment

__all__ = ["ConversationContextProvider"]


@runtime_checkable
class ConversationContextProvider(Protocol):
    """
    CE-5: reads only through its owning module's own public read API,
    never writes anything, anywhere. `namespace` is this provider's own
    stable, declared key -- `provide_context()`'s returned fragment
    must carry exactly this same namespace (enforced by
    context.assemble_context(), not by this Protocol itself, since a
    Protocol cannot express a postcondition).

    Returns `None` (not an empty fragment) when this provider has
    nothing to contribute right now -- context assembly's own fault
    boundary (CE-6) treats that identically to a raised exception: the
    namespace is simply absent unless it was required.
    """

    @property
    def namespace(self) -> str: ...

    def provide_context(self, *, now: datetime) -> ConversationContextFragment | None: ...
