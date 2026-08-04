"""tests/conversation_engine/test_providers.py"""

from __future__ import annotations

from datetime import datetime, timezone

from conversation_engine.models import ConversationContextFragment
from conversation_engine.providers import ConversationContextProvider

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeProvider:
    namespace = "fake"

    def provide_context(self, *, now: datetime) -> ConversationContextFragment | None:
        return ConversationContextFragment(namespace="fake", data={"now": now.isoformat()})


class _NotAProvider:
    pass


class TestConversationContextProviderProtocol:
    def test_a_conforming_object_satisfies_the_protocol(self) -> None:
        assert isinstance(_FakeProvider(), ConversationContextProvider)

    def test_a_non_conforming_object_does_not_satisfy_the_protocol(self) -> None:
        assert not isinstance(_NotAProvider(), ConversationContextProvider)

    def test_provide_context_can_return_none(self) -> None:
        class _EmptyProvider:
            namespace = "empty"

            def provide_context(self, *, now: datetime):
                return None

        result = _EmptyProvider().provide_context(now=FIXED_TIME)
        assert result is None
