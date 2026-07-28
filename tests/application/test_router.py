"""tests/application/test_router.py"""

from __future__ import annotations

from datetime import datetime, timezone

from application.models import OutgoingMessage, UserAccount
from application.router import CommandRouter, RequestContext

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _context() -> RequestContext:
    return RequestContext(user=UserAccount(created_at=FIXED_TIME, last_seen_at=FIXED_TIME), now=FIXED_TIME)


class TestCommandRouter:
    def test_routes_to_the_matching_registered_handler(self) -> None:
        router = CommandRouter()
        router.register("ping", "responds pong", lambda ctx: OutgoingMessage(text="pong"))
        result = router.route("ping", _context())
        assert result.text == "pong"

    def test_matching_is_case_insensitive_and_trims_whitespace(self) -> None:
        router = CommandRouter()
        router.register("ping", "responds pong", lambda ctx: OutgoingMessage(text="pong"))
        result = router.route("  PiNg  ", _context())
        assert result.text == "pong"

    def test_unrecognized_command_gives_a_safe_fallback(self) -> None:
        router = CommandRouter()
        router.register("ping", "responds pong", lambda ctx: OutgoingMessage(text="pong"))
        result = router.route("something else entirely", _context())
        assert "help" in result.text.lower()

    def test_handler_receives_the_request_context(self) -> None:
        router = CommandRouter()
        captured = {}

        def handler(ctx: RequestContext) -> OutgoingMessage:
            captured["now"] = ctx.now
            captured["user_id"] = ctx.user.id
            return OutgoingMessage(text="ok")

        router.register("whoami", "test", handler)
        ctx = _context()
        router.route("whoami", ctx)
        assert captured["now"] == FIXED_TIME
        assert captured["user_id"] == ctx.user.id

    def test_help_text_lists_all_registered_commands(self) -> None:
        router = CommandRouter()
        router.register("ping", "responds pong", lambda ctx: OutgoingMessage(text="pong"))
        router.register("status", "shows status", lambda ctx: OutgoingMessage(text="status"))
        help_text = router.help_text()
        assert "ping" in help_text
        assert "status" in help_text
