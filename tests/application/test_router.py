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
        assert result.matched is True
        assert result.outgoing.text == "pong"

    def test_matching_is_case_insensitive_and_trims_whitespace(self) -> None:
        router = CommandRouter()
        router.register("ping", "responds pong", lambda ctx: OutgoingMessage(text="pong"))
        result = router.route("  PiNg  ", _context())
        assert result.matched is True
        assert result.outgoing.text == "pong"

    def test_unrecognized_command_is_unmatched(self) -> None:
        router = CommandRouter()
        router.register("ping", "responds pong", lambda ctx: OutgoingMessage(text="pong"))
        result = router.route("something else entirely", _context())
        assert result.matched is False
        assert result.outgoing is None

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

    def test_unrecognized_text_helper_mentions_help(self) -> None:
        router = CommandRouter()
        assert "help" in router.unrecognized_text().lower()


class TestRouteResultInvariants:
    def test_matched_true_requires_outgoing(self) -> None:
        import pytest
        from application.router import RouteResult

        with pytest.raises(ValueError, match="matched"):
            RouteResult(matched=True, outgoing=None)

    def test_matched_false_forbids_outgoing(self) -> None:
        import pytest
        from application.router import RouteResult

        with pytest.raises(ValueError, match="matched"):
            RouteResult(matched=False, outgoing=OutgoingMessage(text="x"))

    def test_matched_true_with_outgoing_is_valid(self) -> None:
        from application.router import RouteResult

        RouteResult(matched=True, outgoing=OutgoingMessage(text="x"))  # must not raise

    def test_matched_false_with_no_outgoing_is_valid(self) -> None:
        from application.router import RouteResult

        RouteResult(matched=False, outgoing=None)  # must not raise


class TestCommandFamily:
    def test_exact_match_takes_priority_over_family(self) -> None:
        router = CommandRouter()
        router.register("mode status", "status", lambda ctx: OutgoingMessage(text="exact"))
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("mode status", _context())
        assert result.outgoing.text == "exact"

    def test_family_token_catches_invalid_multiword_input(self) -> None:
        router = CommandRouter()
        router.register("mode status", "status", lambda ctx: OutgoingMessage(text="exact"))
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("mode nonsense", _context())
        assert result.matched is True
        assert result.outgoing.text == "family fallback"

    def test_family_token_catches_multiword_nonsense_too(self) -> None:
        router = CommandRouter()
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("mode request nonsense", _context())
        assert result.matched is True
        assert result.outgoing.text == "family fallback"

    def test_a_different_first_token_is_not_treated_as_the_family(self) -> None:
        router = CommandRouter()
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("model something", _context())
        assert result.matched is False  # "model" != "mode" -- no fuzzy matching

    def test_unregistered_family_leaves_text_unmatched(self) -> None:
        router = CommandRouter()
        result = router.route("whatever nonsense", _context())
        assert result.matched is False
