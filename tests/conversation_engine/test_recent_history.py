"""tests/conversation_engine/test_recent_history.py"""

from __future__ import annotations

import pytest

from conversation_engine.recent_history import ConversationRole, TransitionalRecentMessageBuffer


class TestRecentMessageBuffer:
    def test_empty_subject_returns_no_messages(self) -> None:
        buf = TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=1000)
        assert buf.get_messages(subject_key="s1") == ()

    def test_append_and_read_back_a_whole_exchange(self) -> None:
        buf = TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=1000)
        buf.append_exchange(subject_key="s1", user_text="hi", assistant_text="hello")
        messages = buf.get_messages(subject_key="s1")
        assert len(messages) == 2
        assert messages[0].role == ConversationRole.USER
        assert messages[0].content == "hi"
        assert messages[1].role == ConversationRole.ASSISTANT
        assert messages[1].content == "hello"

    def test_empty_subject_key_rejected(self) -> None:
        buf = TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=1000)
        with pytest.raises(ValueError, match="non-empty"):
            buf.append_exchange(subject_key="", user_text="hi", assistant_text="hello")
        with pytest.raises(ValueError, match="non-empty"):
            buf.get_messages(subject_key="")

    def test_trims_oldest_whole_exchange_when_over_max_exchanges(self) -> None:
        buf = TransitionalRecentMessageBuffer(max_exchanges_per_subject=2, max_characters_per_subject=10_000)
        buf.append_exchange(subject_key="s1", user_text="1", assistant_text="a")
        buf.append_exchange(subject_key="s1", user_text="2", assistant_text="b")
        buf.append_exchange(subject_key="s1", user_text="3", assistant_text="c")
        messages = buf.get_messages(subject_key="s1")
        assert len(messages) == 4  # 2 exchanges * 2 messages each
        assert messages[0].content == "2"  # oldest ("1") trimmed entirely
        assert messages[2].content == "3"

    def test_never_leaves_an_orphaned_turn(self) -> None:
        buf = TransitionalRecentMessageBuffer(max_exchanges_per_subject=1, max_characters_per_subject=10_000)
        buf.append_exchange(subject_key="s1", user_text="1", assistant_text="a")
        buf.append_exchange(subject_key="s1", user_text="2", assistant_text="b")
        messages = buf.get_messages(subject_key="s1")
        assert len(messages) % 2 == 0  # always whole pairs

    def test_trims_by_character_budget_too(self) -> None:
        buf = TransitionalRecentMessageBuffer(max_exchanges_per_subject=10, max_characters_per_subject=10)
        buf.append_exchange(subject_key="s1", user_text="aaaaa", assistant_text="bbbbb")  # 10 chars, fits exactly
        buf.append_exchange(subject_key="s1", user_text="ccccc", assistant_text="ddddd")  # would exceed 10 -> trims first
        messages = buf.get_messages(subject_key="s1")
        assert len(messages) == 2
        assert messages[0].content == "ccccc"

    def test_different_subjects_are_independent(self) -> None:
        buf = TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=1000)
        buf.append_exchange(subject_key="s1", user_text="a", assistant_text="b")
        buf.append_exchange(subject_key="s2", user_text="c", assistant_text="d")
        assert buf.get_messages(subject_key="s1")[0].content == "a"
        assert buf.get_messages(subject_key="s2")[0].content == "c"

    def test_no_persistence_a_fresh_buffer_instance_starts_empty(self) -> None:
        buf1 = TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=1000)
        buf1.append_exchange(subject_key="s1", user_text="a", assistant_text="b")
        buf2 = TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=1000)
        assert buf2.get_messages(subject_key="s1") == ()
