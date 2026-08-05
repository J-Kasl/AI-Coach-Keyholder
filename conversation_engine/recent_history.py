"""
conversation_engine/recent_history.py

TransitionalRecentMessageBuffer -- in-memory, per-subject, bounded.
Explicitly transitional: exists only to give Slice 2 something to put
in the prompt before memory_system_technical_design.md's own Working
Memory (Section 4.1) has a real implementation to read from instead.
No persistence, no database table, no migration -- wiped on every
process restart by construction (a plain dict, never touching disk).
Retired, not extended, once Slice 3 (Memory System read integration)
lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

__all__ = [
    "ConversationRole",
    "RecentConversationMessage",
    "RecentConversationExchange",
    "TransitionalRecentMessageBuffer",
]


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, kw_only=True)
class RecentConversationMessage:
    role: ConversationRole
    content: str


@dataclass(frozen=True, kw_only=True)
class RecentConversationExchange:
    """Always a complete user+assistant pair -- never a lone turn.
    Nothing else: no message/consent/audit ID, no prompt, no snapshot,
    no fragments, no identity, no exception data. Command messages are
    never stored here (CommandRouter handles them before Conversation
    Engine is ever reached -- CE-25)."""
    user: RecentConversationMessage
    assistant: RecentConversationMessage


class TransitionalRecentMessageBuffer:
    """
    A short internal `Lock` protects only the dict/list mutation itself
    (microseconds) -- NOT the duration of a generation call. Per-subject
    serialization of the whole conversational flow is
    SubjectConversationQueue's own job (subject_queue.py), not this
    buffer's.
    """

    def __init__(self, *, max_exchanges_per_subject: int, max_characters_per_subject: int) -> None:
        self._max_exchanges = max_exchanges_per_subject
        self._max_characters = max_characters_per_subject
        self._lock = Lock()
        self._exchanges: dict[str, list[RecentConversationExchange]] = {}

    def get_messages(self, *, subject_key: str) -> tuple[RecentConversationMessage, ...]:
        if not subject_key.strip():
            raise ValueError("subject_key must be non-empty.")
        with self._lock:
            exchanges = list(self._exchanges.get(subject_key, ()))
        messages: list[RecentConversationMessage] = []
        for exchange in exchanges:
            messages.append(exchange.user)
            messages.append(exchange.assistant)
        return tuple(messages)

    def append_exchange(self, *, subject_key: str, user_text: str, assistant_text: str) -> None:
        """Called only after a successful, validated model response
        (ConversationEngine's own contract, engine.py) -- never on a
        fallback path. Atomic: the whole exchange is added, or nothing
        is."""
        if not subject_key.strip():
            raise ValueError("subject_key must be non-empty.")
        exchange = RecentConversationExchange(
            user=RecentConversationMessage(role=ConversationRole.USER, content=user_text),
            assistant=RecentConversationMessage(role=ConversationRole.ASSISTANT, content=assistant_text),
        )
        with self._lock:
            existing = self._exchanges.setdefault(subject_key, [])
            existing.append(exchange)
            self._trim(existing)

    def _trim(self, exchanges: list[RecentConversationExchange]) -> None:
        """Trims OLDEST WHOLE exchanges only -- never a lone turn."""
        while len(exchanges) > self._max_exchanges:
            exchanges.pop(0)
        while exchanges and self._total_characters(exchanges) > self._max_characters:
            exchanges.pop(0)

    @staticmethod
    def _total_characters(exchanges: list[RecentConversationExchange]) -> int:
        return sum(len(e.user.content) + len(e.assistant.content) for e in exchanges)
