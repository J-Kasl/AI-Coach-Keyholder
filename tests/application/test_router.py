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

    def test_family_token_alone_still_triggers_the_family_fallback(self) -> None:
        """Command Family Fallback Precision: the bare family word by
        itself still gets the family-specific reply -- this part of
        the contract is unchanged."""
        router = CommandRouter()
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("mode", _context())
        assert result.matched is True
        assert result.outgoing.text == "family fallback"

    def test_family_token_alone_with_surrounding_whitespace_and_case_still_matches(self) -> None:
        """Existing input normalization (strip + lowercase) is
        unchanged by this slice."""
        router = CommandRouter()
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("  MoDe  ", _context())
        assert result.matched is True
        assert result.outgoing.text == "family fallback"

    def test_family_token_no_longer_catches_a_near_miss_multiword_typo(self) -> None:
        """Command Family Fallback Precision (Option A): a near-miss
        multi-word typo like "mode nonsense" is no longer intercepted
        here -- since it is not an exact registered command either, it
        now falls through as ordinary unmatched text, reaching
        Conversation Engine exactly like any other unmatched multi-word
        input. This is the deliberate, approved trade-off of this
        slice, not a regression."""
        router = CommandRouter()
        router.register("mode status", "status", lambda ctx: OutgoingMessage(text="exact"))
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("mode nonsense", _context())
        assert result.matched is False
        assert result.outgoing is None

    def test_family_token_no_longer_catches_multiword_nonsense(self) -> None:
        router = CommandRouter()
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("mode request nonsense", _context())
        assert result.matched is False
        assert result.outgoing is None

    def test_family_token_no_longer_catches_ordinary_conversation_starting_with_the_family_word(self) -> None:
        """The exact motivating case: an ordinary conversational
        sentence that happens to start with a family word must reach
        Conversation Engine, not a deterministic family-fallback reply."""
        router = CommandRouter()
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("mode of thinking I should try", _context())
        assert result.matched is False
        assert result.outgoing is None

    def test_a_different_first_token_is_not_treated_as_the_family(self) -> None:
        router = CommandRouter()
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="family fallback"))
        result = router.route("model something", _context())
        assert result.matched is False  # "model" != "mode" -- no fuzzy matching

    def test_unregistered_family_leaves_text_unmatched(self) -> None:
        router = CommandRouter()
        result = router.route("whatever nonsense", _context())
        assert result.matched is False


class TestCommandFamilyPrecisionAcceptanceScenarios:
    """Command Family Fallback Precision -- the exact motivating
    scenarios from the approved slice, using "task"/"lock" (the real
    family names application/service.py registers) directly at the
    router level."""

    def _router_with_task_and_lock_families(self) -> CommandRouter:
        router = CommandRouter()
        router.register("task request", "request", lambda ctx: OutgoingMessage(text="task request exact"))
        router.register("task active", "active", lambda ctx: OutgoingMessage(text="task active exact"))
        router.register("task complete", "complete", lambda ctx: OutgoingMessage(text="task complete exact"))
        router.register("task cancel", "cancel", lambda ctx: OutgoingMessage(text="task cancel exact"))
        router.register("lock status", "status", lambda ctx: OutgoingMessage(text="lock status exact"))
        router.register("lock report locked", "locked", lambda ctx: OutgoingMessage(text="lock report locked exact"))
        router.register("lock report unlocked", "unlocked", lambda ctx: OutgoingMessage(text="lock report unlocked exact"))
        router.register_family("task", invalid_handler=lambda ctx: OutgoingMessage(text="task family fallback"))
        router.register_family("lock", invalid_handler=lambda ctx: OutgoingMessage(text="lock family fallback"))
        return router

    def test_bare_task_still_triggers_family_fallback(self) -> None:
        result = self._router_with_task_and_lock_families().route("task", _context())
        assert result.matched is True
        assert result.outgoing.text == "task family fallback"

    def test_bare_lock_still_triggers_family_fallback(self) -> None:
        result = self._router_with_task_and_lock_families().route("lock", _context())
        assert result.matched is True
        assert result.outgoing.text == "lock family fallback"

    def test_task_request_still_goes_the_exact_command_path(self) -> None:
        result = self._router_with_task_and_lock_families().route("task request", _context())
        assert result.matched is True
        assert result.outgoing.text == "task request exact"

    def test_task_active_still_goes_the_exact_command_path(self) -> None:
        result = self._router_with_task_and_lock_families().route("task active", _context())
        assert result.matched is True
        assert result.outgoing.text == "task active exact"

    def test_task_complete_still_goes_the_exact_command_path(self) -> None:
        result = self._router_with_task_and_lock_families().route("task complete", _context())
        assert result.matched is True
        assert result.outgoing.text == "task complete exact"

    def test_task_cancel_still_goes_the_exact_command_path(self) -> None:
        result = self._router_with_task_and_lock_families().route("task cancel", _context())
        assert result.matched is True
        assert result.outgoing.text == "task cancel exact"

    def test_lock_status_still_goes_the_exact_command_path(self) -> None:
        result = self._router_with_task_and_lock_families().route("lock status", _context())
        assert result.matched is True
        assert result.outgoing.text == "lock status exact"

    def test_lock_report_locked_still_goes_the_exact_command_path(self) -> None:
        result = self._router_with_task_and_lock_families().route("lock report locked", _context())
        assert result.matched is True
        assert result.outgoing.text == "lock report locked exact"

    def test_lock_report_unlocked_still_goes_the_exact_command_path(self) -> None:
        result = self._router_with_task_and_lock_families().route("lock report unlocked", _context())
        assert result.matched is True
        assert result.outgoing.text == "lock report unlocked exact"

    def test_task_compelte_typo_is_no_longer_family_fallback(self) -> None:
        result = self._router_with_task_and_lock_families().route("task compelte", _context())
        assert result.matched is False
        assert result.outgoing is None

    def test_task_request_nonsense_is_no_longer_family_fallback(self) -> None:
        result = self._router_with_task_and_lock_families().route("task request nonsense", _context())
        assert result.matched is False
        assert result.outgoing is None

    def test_lock_staus_typo_is_no_longer_family_fallback(self) -> None:
        result = self._router_with_task_and_lock_families().route("lock staus", _context())
        assert result.matched is False
        assert result.outgoing is None

    def test_lock_in_on_what_matters_this_week_reaches_conversation(self) -> None:
        result = self._router_with_task_and_lock_families().route("lock in on what matters this week", _context())
        assert result.matched is False
        assert result.outgoing is None

    def test_task_is_really_weighing_on_me_today_reaches_conversation(self) -> None:
        result = self._router_with_task_and_lock_families().route(
            "task is really weighing on me today, can we talk?", _context(),
        )
        assert result.matched is False
        assert result.outgoing is None

    def test_mode_of_thinking_i_should_try_reaches_conversation(self) -> None:
        router = CommandRouter()
        router.register_family("mode", invalid_handler=lambda ctx: OutgoingMessage(text="mode family fallback"))
        result = router.route("mode of thinking I should try", _context())
        assert result.matched is False
        assert result.outgoing is None
