"""
application/models.py

Channel-agnostic data structures for the application layer. Nothing
here imports `discord` or any other channel-specific package -- an
adapter (bot/discord_bot.py today, something else later) is responsible
for translating its own message type into IncomingMessage and
OutgoingMessage.text back into whatever it sends.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True, kw_only=True)
class IncomingMessage:
    """What an adapter hands to ApplicationService.handle_message() --
    everything the application layer needs, and nothing specific to
    Discord (or any other channel)."""
    channel: str            # 'discord' today; a plain string, not a channel-specific enum, deliberately extensible
    external_user_id: str    # the channel's own identifier for the sender (e.g. Discord user id, as a string)
    text: str
    received_at: datetime


@dataclass(frozen=True, kw_only=True)
class OutgoingMessage:
    """What ApplicationService.handle_message() returns -- an adapter
    sends `.text` however its channel sends text."""
    text: str


@dataclass(kw_only=True)
class UserAccount:
    """
    The application layer's own identity record -- NOT a domain-module
    concept. No table in trust_manager/penalty_engine/recovery_plan/
    goal_management references this id; those modules remain
    single-user and unscoped, exactly as they were before this layer
    existed. See application/README.md for why.
    """
    id: str = field(default_factory=new_id)
    created_at: datetime
    last_seen_at: datetime
