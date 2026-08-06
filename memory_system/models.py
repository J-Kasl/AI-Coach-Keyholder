"""
memory_system/models.py

docs/architecture/memory_system_technical_design.md (draft, not
approved for implementation as a whole). This module implements ONLY
the non-persistent Working Memory foundation slice -- see
memory_system/README.md for the exact boundary between what is
implemented here and what remains draft/undecided (the four
persistent layers -- Episodic, Semantic, Relationship, Decision --
remain fully blocked on a privacy/consent design that does not yet
exist).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "WorkingMemoryRole",
    "WorkingMemoryTurn",
    "WorkingMemorySnapshot",
    "WorkingMemoryError",
    "WorkingMemoryCapacityError",
]


class WorkingMemoryRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, kw_only=True)
class WorkingMemoryTurn:
    """
    Immutable, and immutable is enforced at RUNTIME, not merely by the
    type hint -- `role: WorkingMemoryRole` alone would not stop
    `WorkingMemoryTurn(role="system", content="...")` from
    constructing successfully; a plain dataclass performs no such
    check on its own. `__post_init__` closes that gap. `content` is
    only ever validated, never normalized -- the exact characters
    passed in are the exact characters stored.
    """
    role: WorkingMemoryRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, WorkingMemoryRole):
            raise ValueError("role must be a WorkingMemoryRole.")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class WorkingMemorySnapshot:
    """A point-in-time, immutable view -- never the internal mutable
    list InMemoryWorkingMemory itself holds. Oldest turn first."""
    turns: tuple[WorkingMemoryTurn, ...]


class WorkingMemoryError(RuntimeError):
    """Base class for this module's own errors. Never carries raw
    user/assistant content in its own message -- see
    WorkingMemoryCapacityError for the one concrete case this slice
    defines."""


class WorkingMemoryCapacityError(WorkingMemoryError):
    """A single exchange (user_content + assistant_content) exceeds
    max_characters_per_subject on its own -- raised BEFORE any
    mutation, nothing is stored. A commit that raises this must never
    have partially succeeded."""
