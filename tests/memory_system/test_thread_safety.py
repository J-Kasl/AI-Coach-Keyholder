"""tests/memory_system/test_thread_safety.py"""

from __future__ import annotations

import threading

from memory_system.working_memory import InMemoryWorkingMemory


def _wm(*, max_exchanges: int = 1000, max_characters: int = 1_000_000) -> InMemoryWorkingMemory:
    return InMemoryWorkingMemory(max_exchanges_per_subject=max_exchanges, max_characters_per_subject=max_characters)


class TestConcurrentCommitsSameSubject:
    def test_structure_is_not_corrupted_under_concurrent_commits(self) -> None:
        wm = _wm()
        n = 50
        errors: list[Exception] = []

        def commit(i: int) -> None:
            try:
                wm.commit_exchange(subject_key="s1", user_content=f"u{i}", assistant_content=f"a{i}")
            except Exception as exc:  # pragma: no cover -- failure path only
                errors.append(exc)

        threads = [threading.Thread(target=commit, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        snapshot = wm.read(subject_key="s1")
        assert len(snapshot.turns) == n * 2  # every exchange landed, nothing lost or duplicated
        assert len(snapshot.turns) % 2 == 0  # always whole exchanges

    def test_every_snapshot_observed_during_concurrent_writes_has_whole_exchanges_only(self) -> None:
        wm = _wm()
        stop = threading.Event()
        violations: list[int] = []

        def writer() -> None:
            for i in range(100):
                wm.commit_exchange(subject_key="s1", user_content=f"u{i}", assistant_content=f"a{i}")
            stop.set()

        def reader() -> None:
            while not stop.is_set():
                length = len(wm.read(subject_key="s1").turns)
                if length % 2 != 0:
                    violations.append(length)

        writer_thread = threading.Thread(target=writer)
        reader_thread = threading.Thread(target=reader)
        writer_thread.start()
        reader_thread.start()
        writer_thread.join(timeout=10)
        reader_thread.join(timeout=10)

        assert violations == []


class TestConcurrentCommitsDifferentSubjects:
    def test_no_data_lost_across_different_subjects(self) -> None:
        wm = _wm()
        n_subjects = 20

        def commit(i: int) -> None:
            wm.commit_exchange(subject_key=f"subject-{i}", user_content=f"u{i}", assistant_content=f"a{i}")

        threads = [threading.Thread(target=commit, args=(i,)) for i in range(n_subjects)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        for i in range(n_subjects):
            snapshot = wm.read(subject_key=f"subject-{i}")
            assert len(snapshot.turns) == 2
            assert snapshot.turns[0].content == f"u{i}"


class TestSnapshotIsolationUnderConcurrency:
    def test_snapshot_returned_during_concurrent_writes_is_never_mutated_after_the_fact(self) -> None:
        wm = _wm()
        wm.commit_exchange(subject_key="s1", user_content="first", assistant_content="reply")
        snapshot = wm.read(subject_key="s1")
        original_len = len(snapshot.turns)

        def hammer() -> None:
            for i in range(50):
                wm.commit_exchange(subject_key="s1", user_content=f"u{i}", assistant_content=f"a{i}")

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(snapshot.turns) == original_len  # the earlier snapshot itself never changes
