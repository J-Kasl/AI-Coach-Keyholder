"""
application/router.py

A minimal, explicit command router. Knows nothing about Discord (or
any channel) and nothing about how any handler is implemented
internally -- it only matches trimmed, lowercased message text against
a fixed, explicitly registered set of commands and calls the matching
handler.

Deliberately NOT a natural-language intent parser -- that is real
scope, later. This slice matches exact command strings, nothing more,
consistent with the request for "explicit and limited" supported
actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from application.models import OutgoingMessage, UserAccount

__all__ = ["RequestContext", "CommandRouter"]


@dataclass(frozen=True, kw_only=True)
class RequestContext:
    """What a handler receives -- the resolved UserAccount and the
    request's own `now`, nothing channel-specific."""
    user: UserAccount
    now: datetime


Handler = Callable[[RequestContext], OutgoingMessage]


class CommandRouter:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, command: str, description: str, handler: Handler) -> None:
        key = command.strip().lower()
        self._handlers[key] = handler
        self._descriptions[key] = description

    def route(self, text: str, context: RequestContext) -> OutgoingMessage:
        command = text.strip().lower()
        handler = self._handlers.get(command)
        if handler is None:
            return OutgoingMessage(text=self._unrecognized_text())
        return handler(context)

    def help_text(self) -> str:
        lines = ["Available commands:"]
        for command in sorted(self._descriptions):
            lines.append(f"  {command} — {self._descriptions[command]}")
        return "\n".join(lines)

    def _unrecognized_text(self) -> str:
        return "I don't recognize that yet. Send `help` to see what I can do."
