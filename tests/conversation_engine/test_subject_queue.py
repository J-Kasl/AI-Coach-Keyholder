"""tests/conversation_engine/test_subject_queue.py"""

from __future__ import annotations

import threading
import time

import pytest

from conversation_engine.subject_queue import SubjectConversationQueue


class TestFIFOOrder:
    def test_tickets_a_b_c_preserve_order(self) -> None:
        queue = SubjectConversationQueue()
        order: list[str] = []
        start_barrier = threading.Barrier(3)

        def worker(label: str, index: int) -> None:
            start_barrier.wait(timeout=5)
            time.sleep(index * 0.05)  # ensures enqueue order A, B, C
            with queue.turn("subject-1"):
                order.append(label)
                time.sleep(0.02)  # hold the turn briefly

        threads = [
            threading.Thread(target=worker, args=("A", 0)),
            threading.Thread(target=worker, args=("B", 1)),
            threading.Thread(target=worker, args=("C", 2)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert order == ["A", "B", "C"]

    def test_b_does_not_start_before_a_completes(self) -> None:
        queue = SubjectConversationQueue()
        timeline: list[tuple[str, str]] = []
        release_a = threading.Event()

        def a() -> None:
            with queue.turn("subject-1"):
                timeline.append(("A", "start"))
                release_a.wait(timeout=5)
                timeline.append(("A", "end"))

        def b() -> None:
            with queue.turn("subject-1"):
                timeline.append(("B", "start"))
                timeline.append(("B", "end"))

        ta = threading.Thread(target=a)
        ta.start()
        time.sleep(0.05)
        tb = threading.Thread(target=b)
        tb.start()
        time.sleep(0.05)

        assert ("B", "start") not in timeline
        release_a.set()
        ta.join(timeout=5)
        tb.join(timeout=5)

        a_end_index = timeline.index(("A", "end"))
        b_start_index = timeline.index(("B", "start"))
        assert a_end_index < b_start_index


class TestExceptionWakesNext:
    def test_exception_in_a_still_wakes_b(self) -> None:
        queue = SubjectConversationQueue()
        results: list[str] = []

        def a() -> None:
            with pytest.raises(RuntimeError):
                with queue.turn("subject-1"):
                    raise RuntimeError("boom")

        def b() -> None:
            with queue.turn("subject-1"):
                results.append("b ran")

        ta = threading.Thread(target=a)
        ta.start()
        ta.join(timeout=5)

        tb = threading.Thread(target=b)
        tb.start()
        tb.join(timeout=5)

        assert results == ["b ran"]


class TestDifferentSubjectsConcurrent:
    def test_two_subjects_run_in_parallel_not_sequentially(self) -> None:
        queue = SubjectConversationQueue()
        barrier = threading.Barrier(2)
        started = []

        def work(subject: str) -> None:
            with queue.turn(subject):
                started.append(subject)
                barrier.wait(timeout=5)  # both must be inside their own turn() simultaneously to pass

        t1 = threading.Thread(target=work, args=("subject-a",))
        t2 = threading.Thread(target=work, args=("subject-b",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert set(started) == {"subject-a", "subject-b"}


class TestEntryCleanup:
    def test_entry_is_removed_after_queue_empties(self) -> None:
        queue = SubjectConversationQueue()
        with queue.turn("subject-1"):
            pass
        assert "subject-1" not in queue._entries

    def test_waiting_worker_stays_counted_until_its_own_completion(self) -> None:
        queue = SubjectConversationQueue()
        release = threading.Event()
        entered_a = threading.Event()

        def a() -> None:
            with queue.turn("subject-1"):
                entered_a.set()
                release.wait(timeout=5)

        def b() -> None:
            with queue.turn("subject-1"):
                pass

        ta = threading.Thread(target=a)
        ta.start()
        entered_a.wait(timeout=5)

        tb = threading.Thread(target=b)
        tb.start()
        time.sleep(0.05)

        with queue._registry_lock:
            assert queue._entries["subject-1"].active_count == 2  # A running, B waiting -- both counted

        release.set()
        ta.join(timeout=5)
        tb.join(timeout=5)
        assert "subject-1" not in queue._entries

    def test_no_ticket_is_lost_or_woken_twice(self) -> None:
        queue = SubjectConversationQueue()
        lock = threading.Lock()
        n = 10
        results: list[int] = []

        def work(i: int) -> None:
            with queue.turn("subject-1"):
                with lock:
                    results.append(i)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert sorted(results) == list(range(n))
        assert len(results) == n


class TestSubjectKeyValidation:
    def test_empty_subject_key_rejected(self) -> None:
        queue = SubjectConversationQueue()
        with pytest.raises(ValueError, match="non-empty"):
            with queue.turn(""):
                pass
