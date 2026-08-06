"""tests/memory_system/test_working_memory.py"""

from __future__ import annotations

import pytest

from memory_system.models import WorkingMemoryCapacityError, WorkingMemoryRole
from memory_system.working_memory import InMemoryWorkingMemory


def _wm(*, max_exchanges: int = 5, max_characters: int = 5000) -> InMemoryWorkingMemory:
    return InMemoryWorkingMemory(max_exchanges_per_subject=max_exchanges, max_characters_per_subject=max_characters)


class TestConstructorLimits:
    @pytest.mark.parametrize("bad_value", [0, -1, True, False, 1.0, "10"])
    def test_max_exchanges_rejects_invalid_values(self, bad_value) -> None:
        with pytest.raises(ValueError, match="max_exchanges_per_subject"):
            InMemoryWorkingMemory(max_exchanges_per_subject=bad_value, max_characters_per_subject=100)

    @pytest.mark.parametrize("bad_value", [0, -1, True, False, 1.0, "10"])
    def test_max_characters_rejects_invalid_values(self, bad_value) -> None:
        with pytest.raises(ValueError, match="max_characters_per_subject"):
            InMemoryWorkingMemory(max_exchanges_per_subject=5, max_characters_per_subject=bad_value)

    def test_valid_positive_integers_accepted(self) -> None:
        InMemoryWorkingMemory(max_exchanges_per_subject=1, max_characters_per_subject=1)  # must not raise


class TestInputValidation:
    @pytest.mark.parametrize("bad_value", [None, 123, "", "   "])
    def test_read_rejects_invalid_subject_key(self, bad_value) -> None:
        with pytest.raises(ValueError, match="subject_key"):
            _wm().read(subject_key=bad_value)

    @pytest.mark.parametrize("bad_value", [None, 123, "", "   "])
    def test_commit_rejects_invalid_subject_key(self, bad_value) -> None:
        with pytest.raises(ValueError, match="subject_key"):
            _wm().commit_exchange(subject_key=bad_value, user_content="hi", assistant_content="hello")

    @pytest.mark.parametrize("bad_value", [None, 123, "", "   "])
    def test_commit_rejects_invalid_user_content(self, bad_value) -> None:
        with pytest.raises(ValueError, match="user_content"):
            _wm().commit_exchange(subject_key="s1", user_content=bad_value, assistant_content="hello")

    @pytest.mark.parametrize("bad_value", [None, 123, "", "   "])
    def test_commit_rejects_invalid_assistant_content(self, bad_value) -> None:
        with pytest.raises(ValueError, match="assistant_content"):
            _wm().commit_exchange(subject_key="s1", user_content="hi", assistant_content=bad_value)


class TestSuccessfulCommitAndRead:
    def test_commit_then_read_round_trips(self) -> None:
        wm = _wm()
        wm.commit_exchange(subject_key="s1", user_content="hi", assistant_content="hello")
        snapshot = wm.read(subject_key="s1")
        assert len(snapshot.turns) == 2
        assert snapshot.turns[0].role == WorkingMemoryRole.USER
        assert snapshot.turns[0].content == "hi"
        assert snapshot.turns[1].role == WorkingMemoryRole.ASSISTANT
        assert snapshot.turns[1].content == "hello"

    def test_content_is_stored_unnormalized(self) -> None:
        wm = _wm()
        wm.commit_exchange(subject_key="s1", user_content="  hi  ", assistant_content="hello\n")
        snapshot = wm.read(subject_key="s1")
        assert snapshot.turns[0].content == "  hi  "
        assert snapshot.turns[1].content == "hello\n"


class TestCapacityBoundary:
    def test_exactly_at_character_limit_is_accepted(self) -> None:
        wm = _wm(max_characters=10)
        wm.commit_exchange(subject_key="s1", user_content="aaaaa", assistant_content="bbbbb")  # exactly 10
        assert len(wm.read(subject_key="s1").turns) == 2

    def test_one_character_over_limit_is_rejected(self) -> None:
        wm = _wm(max_characters=10)
        with pytest.raises(WorkingMemoryCapacityError):
            wm.commit_exchange(subject_key="s1", user_content="aaaaaa", assistant_content="bbbbb")  # 11

    def test_capacity_violation_stores_nothing(self) -> None:
        wm = _wm(max_characters=10)
        with pytest.raises(WorkingMemoryCapacityError):
            wm.commit_exchange(subject_key="s1", user_content="aaaaaa", assistant_content="bbbbb")
        assert wm.read(subject_key="s1").turns == ()

    def test_capacity_check_happens_before_any_mutation_prior_exchanges_survive(self) -> None:
        wm = _wm(max_exchanges=5, max_characters=100)
        wm.commit_exchange(subject_key="s1", user_content="first", assistant_content="reply")
        with pytest.raises(WorkingMemoryCapacityError):
            wm.commit_exchange(subject_key="s1", user_content="x" * 60, assistant_content="y" * 60)
        snapshot = wm.read(subject_key="s1")
        assert len(snapshot.turns) == 2  # the first exchange is untouched
        assert snapshot.turns[0].content == "first"


class TestTrimming:
    def test_count_trimming_removes_oldest_whole_exchange(self) -> None:
        wm = _wm(max_exchanges=2, max_characters=10_000)
        wm.commit_exchange(subject_key="s1", user_content="1", assistant_content="a")
        wm.commit_exchange(subject_key="s1", user_content="2", assistant_content="b")
        wm.commit_exchange(subject_key="s1", user_content="3", assistant_content="c")
        snapshot = wm.read(subject_key="s1")
        assert len(snapshot.turns) == 4
        assert snapshot.turns[0].content == "2"
        assert snapshot.turns[2].content == "3"

    def test_character_trimming_removes_oldest_whole_exchange(self) -> None:
        wm = _wm(max_exchanges=10, max_characters=10)
        wm.commit_exchange(subject_key="s1", user_content="aaaaa", assistant_content="bbbbb")  # 10, fits
        wm.commit_exchange(subject_key="s1", user_content="ccccc", assistant_content="ddddd")  # would total 20 -> trims first
        snapshot = wm.read(subject_key="s1")
        assert len(snapshot.turns) == 2
        assert snapshot.turns[0].content == "ccccc"

    def test_never_leaves_an_orphaned_turn(self) -> None:
        wm = _wm(max_exchanges=1, max_characters=10_000)
        wm.commit_exchange(subject_key="s1", user_content="1", assistant_content="a")
        wm.commit_exchange(subject_key="s1", user_content="2", assistant_content="b")
        assert len(wm.read(subject_key="s1").turns) % 2 == 0


class TestOrderingAndIsolation:
    def test_deterministic_oldest_to_newest_ordering(self) -> None:
        wm = _wm()
        wm.commit_exchange(subject_key="s1", user_content="1", assistant_content="a")
        wm.commit_exchange(subject_key="s1", user_content="2", assistant_content="b")
        wm.commit_exchange(subject_key="s1", user_content="3", assistant_content="c")
        contents = [t.content for t in wm.read(subject_key="s1").turns]
        assert contents == ["1", "a", "2", "b", "3", "c"]

    def test_snapshot_is_isolated_from_later_commits(self) -> None:
        wm = _wm()
        wm.commit_exchange(subject_key="s1", user_content="1", assistant_content="a")
        snapshot_before = wm.read(subject_key="s1")
        wm.commit_exchange(subject_key="s1", user_content="2", assistant_content="b")
        assert len(snapshot_before.turns) == 2  # unaffected by the later commit

    def test_snapshot_cannot_be_used_to_mutate_internal_state(self) -> None:
        wm = _wm()
        wm.commit_exchange(subject_key="s1", user_content="1", assistant_content="a")
        snapshot = wm.read(subject_key="s1")
        with pytest.raises((AttributeError, TypeError)):
            snapshot.turns.append(snapshot.turns[0])  # type: ignore[attr-defined]
        # Confirm the underlying store is unaffected regardless
        assert len(wm.read(subject_key="s1").turns) == 2


class TestUnknownSubjectAndFreshInstance:
    def test_unknown_subject_returns_empty_snapshot(self) -> None:
        wm = _wm()
        wm.commit_exchange(subject_key="s1", user_content="1", assistant_content="a")
        assert wm.read(subject_key="never-seen").turns == ()

    def test_a_fresh_instance_is_always_empty(self) -> None:
        wm1 = _wm()
        wm1.commit_exchange(subject_key="s1", user_content="1", assistant_content="a")
        wm2 = _wm()
        assert wm2.read(subject_key="s1").turns == ()


class TestSubjectIsolation:
    def test_different_subjects_do_not_share_history(self) -> None:
        wm = _wm()
        wm.commit_exchange(subject_key="s1", user_content="1", assistant_content="a")
        wm.commit_exchange(subject_key="s2", user_content="2", assistant_content="b")
        assert wm.read(subject_key="s1").turns[0].content == "1"
        assert wm.read(subject_key="s2").turns[0].content == "2"
