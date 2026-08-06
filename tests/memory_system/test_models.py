"""tests/memory_system/test_models.py"""

from __future__ import annotations

import dataclasses

import pytest

from memory_system.models import WorkingMemoryRole, WorkingMemorySnapshot, WorkingMemoryTurn


class TestWorkingMemoryRole:
    def test_user_value(self) -> None:
        assert WorkingMemoryRole.USER.value == "user"

    def test_assistant_value(self) -> None:
        assert WorkingMemoryRole.ASSISTANT.value == "assistant"

    def test_exactly_two_members(self) -> None:
        assert set(WorkingMemoryRole) == {WorkingMemoryRole.USER, WorkingMemoryRole.ASSISTANT}


class TestWorkingMemoryTurnRoleInvariant:
    def test_valid_user_role(self) -> None:
        WorkingMemoryTurn(role=WorkingMemoryRole.USER, content="hi")  # must not raise

    def test_valid_assistant_role(self) -> None:
        WorkingMemoryTurn(role=WorkingMemoryRole.ASSISTANT, content="hi")  # must not raise

    def test_string_user_rejected(self) -> None:
        with pytest.raises(ValueError, match="role"):
            WorkingMemoryTurn(role="user", content="hi")  # type: ignore[arg-type]

    def test_string_system_rejected(self) -> None:
        with pytest.raises(ValueError, match="role"):
            WorkingMemoryTurn(role="system", content="hi")  # type: ignore[arg-type]

    def test_none_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="role"):
            WorkingMemoryTurn(role=None, content="hi")  # type: ignore[arg-type]

    def test_arbitrary_object_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="role"):
            WorkingMemoryTurn(role=object(), content="hi")  # type: ignore[arg-type]

    def test_integer_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="role"):
            WorkingMemoryTurn(role=1, content="hi")  # type: ignore[arg-type]


class TestWorkingMemoryTurnContentInvariant:
    def test_blank_content_rejected(self) -> None:
        with pytest.raises(ValueError, match="content"):
            WorkingMemoryTurn(role=WorkingMemoryRole.USER, content="   ")

    def test_empty_content_rejected(self) -> None:
        with pytest.raises(ValueError, match="content"):
            WorkingMemoryTurn(role=WorkingMemoryRole.USER, content="")

    def test_none_content_rejected(self) -> None:
        with pytest.raises(ValueError, match="content"):
            WorkingMemoryTurn(role=WorkingMemoryRole.USER, content=None)  # type: ignore[arg-type]

    def test_non_string_content_rejected(self) -> None:
        with pytest.raises(ValueError, match="content"):
            WorkingMemoryTurn(role=WorkingMemoryRole.USER, content=123)  # type: ignore[arg-type]

    def test_content_is_stored_exactly_unchanged(self) -> None:
        """Only validated via strip(), never normalized -- internal
        whitespace/newlines/leading-trailing spacing survive exactly."""
        raw = "  hello\nworld  "
        turn = WorkingMemoryTurn(role=WorkingMemoryRole.USER, content=raw)
        assert turn.content == raw


class TestWorkingMemoryTurnImmutability:
    def test_is_frozen(self) -> None:
        turn = WorkingMemoryTurn(role=WorkingMemoryRole.USER, content="hi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            turn.content = "changed"  # type: ignore[misc]


class TestWorkingMemorySnapshotImmutability:
    def test_is_frozen(self) -> None:
        snapshot = WorkingMemorySnapshot(turns=())
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.turns = ()  # type: ignore[misc]

    def test_turns_is_a_tuple(self) -> None:
        turn = WorkingMemoryTurn(role=WorkingMemoryRole.USER, content="hi")
        snapshot = WorkingMemorySnapshot(turns=(turn,))
        assert isinstance(snapshot.turns, tuple)
