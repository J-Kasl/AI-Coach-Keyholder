"""
conversation_engine/subject_queue.py

SubjectConversationQueue -- ticket-based FIFO, NOT a plain
threading.Lock. A bare Lock guarantees mutual exclusion but not order;
under near-simultaneous asyncio.to_thread() calls, the second message
to arrive could acquire the lock before the first. This queue
guarantees:

    For one subject, conversational operations execute FIFO in the
    order worker threads enter the Conversation Engine queue.

This is NOT the same as Discord gateway event order -- see this
module's own note below for exactly what is, and is not, guaranteed.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Iterator

__all__ = ["SubjectConversationQueue"]


@dataclass
class _SubjectQueueEntry:
    waiting: deque = field(default_factory=deque)
    active_count: int = 0


class SubjectConversationQueue:
    """
    `_registry_lock` protects only the entries dict itself (ticket
    enqueue/dequeue, entry creation/removal) -- never the duration of
    the caller's own work inside `turn()`. Different subjects proceed
    fully in parallel; only calls sharing the SAME subject_key are
    serialized, and in FIFO order.

    What "arrival order" means here, precisely: the order in which
    worker threads reach `turn()`'s own ticket-enqueue step -- i.e.
    AFTER onboarding/routing has already run on that thread and
    determined the message is an unmatched conversational request.
    This is NOT a guarantee about the order Discord's own gateway
    delivered the underlying events in. In practice the two usually
    coincide (discord.py dispatches on_message sequentially on the
    event loop before handing off to asyncio.to_thread()), but if
    onboarding/routing work for an earlier message takes longer than
    for a later one on a different worker thread, the later message
    can reach the queue first. This is a real, disclosed limit, not a
    stronger promise.
    """

    def __init__(self) -> None:
        self._registry_lock = Lock()
        self._entries: dict[str, _SubjectQueueEntry] = {}

    @contextmanager
    def turn(self, subject_key: str) -> Iterator[None]:
        if not subject_key.strip():
            raise ValueError("subject_key must be non-empty.")

        my_ticket = Event()
        with self._registry_lock:
            entry = self._entries.setdefault(subject_key, _SubjectQueueEntry())
            entry.active_count += 1
            is_first_in_line = len(entry.waiting) == 0
            entry.waiting.append(my_ticket)
            if is_first_in_line:
                my_ticket.set()

        my_ticket.wait()  # blocks until this ticket is the head of the FIFO queue
        try:
            yield
        finally:
            with self._registry_lock:
                entry.waiting.popleft()
                entry.active_count -= 1
                if entry.waiting:
                    entry.waiting[0].set()  # wake exactly the next ticket in line
                if entry.active_count == 0:
                    del self._entries[subject_key]
