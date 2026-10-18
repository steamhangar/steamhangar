"""UI-COLD-CACHED-1: ``SizeCache`` serves stale-while-revalidate.

All tests drive a fake walk with ``threading.Event``s instead of real sleeps:
the walk blocks until the test releases it, so "a walk is running" is a state
the test controls, not a timing guess.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from vault_api import sizes
from vault_api.sizes import DepotWalk, SizeCache, SizeWalkError, walk_depot_tree

ROOT = "/cache"
TIMEOUT = 5.0


class FakeWalk:
    """A walk that blocks until released and returns queued results."""

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self.started = threading.Semaphore(0)
        self._gates: list[threading.Event] = []
        self._outcomes: list[dict[int, int] | Exception] = []

    def queue(self, outcome: dict[int, int] | Exception) -> threading.Event:
        """Queue the next walk's outcome; returns the event that releases it."""
        gate = threading.Event()
        with self._lock:
            self._gates.append(gate)
            self._outcomes.append(outcome)
        return gate

    def __call__(self, cache_root: str) -> DepotWalk:
        with self._lock:
            index = self.calls
            self.calls += 1
            gate = self._gates[index]
            outcome = self._outcomes[index]
        self.started.release()
        assert gate.wait(TIMEOUT), "test never released the walk"
        if isinstance(outcome, Exception):
            raise outcome
        return DepotWalk(depot_bytes=outcome, file_count=len(outcome), dir_count=len(outcome))


def _get_in_thread(cache: SizeCache, now: float) -> tuple[threading.Thread, list[object]]:
    out: list[object] = []

    def run() -> None:
        try:
            out.append(cache.get(ROOT, now=now))
        except Exception as exc:  # noqa: BLE001 - handed to the test
            out.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


def _warm(cache: SizeCache, walk: FakeWalk, depot_bytes: dict[int, int]) -> None:
    walk.queue(depot_bytes).set()
    cache.get(ROOT, now=0.0)
    # Consume this walk's start token, so later acquires mean later walks.
    assert walk.started.acquire(timeout=TIMEOUT)


def test_first_call_blocks_until_the_walk_is_done_and_is_shared() -> None:
    walk = FakeWalk()
    gate = walk.queue({441: 10})
    cache = SizeCache(ttl_seconds=60.0, walk=walk)

    first, first_out = _get_in_thread(cache, 0.0)
    assert walk.started.acquire(timeout=TIMEOUT)
    second, second_out = _get_in_thread(cache, 0.0)

    first.join(0.05)
    assert first.is_alive() and not first_out, "first call must wait for the walk"

    gate.set()
    first.join(TIMEOUT)
    second.join(TIMEOUT)
    assert first_out[0] is second_out[0]
    assert first_out[0].depot_bytes == {441: 10}  # type: ignore[union-attr]
    assert walk.calls == 1, "concurrent first callers share one walk"


def test_expired_snapshot_is_served_while_one_recompute_runs() -> None:
    walk = FakeWalk()
    cache = SizeCache(ttl_seconds=60.0, walk=walk)
    _warm(cache, walk, {441: 10})
    old = cache.get(ROOT, now=1.0)

    gate = walk.queue({441: 99})
    # Many readers after the TTL: each gets the old snapshot at once ...
    for i in range(20):
        assert cache.get(ROOT, now=61.0 + i) is old
    assert walk.started.acquire(timeout=TIMEOUT)
    # ... while exactly one recompute runs.
    assert walk.calls == 2

    gate.set()
    assert cache.wait_idle(TIMEOUT)
    fresh = cache.get(ROOT, now=62.0)
    assert fresh.depot_bytes == {441: 99}
    assert cache.wait_idle(TIMEOUT)
    assert walk.calls == 2


def test_concurrent_expired_readers_start_a_single_recompute() -> None:
    walk = FakeWalk()
    cache = SizeCache(ttl_seconds=60.0, walk=walk)
    _warm(cache, walk, {441: 10})
    gate = walk.queue({441: 20})

    barrier = threading.Barrier(16)
    results: list[object] = []

    def reader() -> None:
        barrier.wait(TIMEOUT)
        results.append(cache.get(ROOT, now=100.0).depot_bytes)

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT)
    assert results == [{441: 10}] * 16
    gate.set()
    assert cache.wait_idle(TIMEOUT)
    assert walk.calls == 2


def test_invalidate_blocks_the_next_read_until_a_post_change_walk() -> None:
    walk = FakeWalk()
    cache = SizeCache(ttl_seconds=999.0, walk=walk)
    _warm(cache, walk, {441: 10})

    gate = walk.queue({})  # the deletion removed depot 441
    cache.invalidate()
    # invalidate() starts no walk itself; the next reader does.
    assert cache.wait_idle(TIMEOUT)
    assert walk.calls == 1

    reader, out = _get_in_thread(cache, 1.0)
    assert walk.started.acquire(timeout=TIMEOUT)
    reader.join(0.05)
    assert reader.is_alive() and not out, "pre-change snapshot must not be served"

    gate.set()
    reader.join(TIMEOUT)
    assert out[0].depot_bytes == {}  # type: ignore[union-attr]
    assert walk.calls == 2


def test_a_walk_running_during_invalidate_is_not_served_as_fresh() -> None:
    walk = FakeWalk()
    cache = SizeCache(ttl_seconds=60.0, walk=walk)
    _warm(cache, walk, {441: 10})

    pre_change = walk.queue({441: 10})
    assert cache.get(ROOT, now=100.0).depot_bytes == {441: 10}  # starts the refresh
    assert walk.started.acquire(timeout=TIMEOUT)

    post_change = walk.queue({})
    cache.invalidate()  # the disk changed while that walk was running
    reader, out = _get_in_thread(cache, 101.0)

    pre_change.set()
    # The pre-change walk ends; a new one must start, and the reader waits for it.
    assert walk.started.acquire(timeout=TIMEOUT)
    reader.join(0.05)
    assert reader.is_alive() and not out

    post_change.set()
    reader.join(TIMEOUT)
    assert out[0].depot_bytes == {}  # type: ignore[union-attr]
    assert walk.calls == 3


def test_invalidate_before_any_read_does_not_walk() -> None:
    walk = FakeWalk()
    cache = SizeCache(ttl_seconds=60.0, walk=walk)
    cache.invalidate()  # no cache_root known yet: nothing to walk
    assert walk.calls == 0
    walk.queue({1: 1}).set()
    assert cache.get(ROOT, now=0.0).depot_bytes == {1: 1}


def test_a_failing_background_walk_keeps_the_old_snapshot_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    walk = FakeWalk()
    cache = SizeCache(ttl_seconds=60.0, walk=walk)
    _warm(cache, walk, {441: 10})
    old = cache.get(ROOT, now=1.0)

    walk.queue(OSError("disk went away")).set()
    with caplog.at_level(logging.ERROR, logger="vault_api.sizes"):
        assert cache.get(ROOT, now=100.0) is old
        assert cache.wait_idle(TIMEOUT)
    assert any(
        "failed" in record.getMessage() and record.exc_info is not None
        for record in caplog.records
    )

    # The old snapshot survives the failure; it is still expired, so this
    # read retries in the background.
    walk.queue({441: 11}).set()
    assert cache.get(ROOT, now=100.5) is old
    assert cache.wait_idle(TIMEOUT)
    assert cache.get(ROOT, now=101.0).depot_bytes == {441: 11}
    assert walk.calls == 3


def test_a_failing_first_walk_raises_to_the_waiter_and_the_next_call_retries() -> None:
    walk = FakeWalk()
    cache = SizeCache(ttl_seconds=60.0, walk=walk)
    walk.queue(OSError("boom")).set()
    with pytest.raises(SizeWalkError):
        cache.get(ROOT, now=0.0)

    walk.queue({7: 7}).set()
    assert cache.get(ROOT, now=0.0).depot_bytes == {7: 7}
    assert walk.calls == 2


def test_each_walk_logs_duration_and_counts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache_root = tmp_path / "cache"
    for depot, files in ((441, 3), (442, 2)):
        chunk = cache_root / "depot" / str(depot) / "chunk"
        chunk.mkdir(parents=True)
        for i in range(files):
            (chunk / f"f{i}").write_bytes(b"x" * (i + 1))

    cache = SizeCache(ttl_seconds=60.0)
    with caplog.at_level(logging.INFO, logger="vault_api.sizes"):
        snapshot = cache.get(str(cache_root), now=0.0)
    assert snapshot.depot_bytes == {441: 6, 442: 3}
    lines = [r.getMessage() for r in caplog.records if "Depot size walk" in r.getMessage()]
    assert len(lines) == 1
    # 2 depot dirs + 2 chunk dirs listed below them; 5 files.
    assert "(first)" in lines[0]
    assert "2 depots, 4 dirs, 5 files" in lines[0]


def test_walk_depot_tree_counts_files_and_dirs(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    nested = cache_root / "depot" / "441" / "chunk" / "ab"
    nested.mkdir(parents=True)
    (nested / "a").write_bytes(b"1234")
    (cache_root / "depot" / "441" / "chunk" / "b").write_bytes(b"1")
    result = walk_depot_tree(str(cache_root))
    assert result.depot_bytes == {441: 5}
    assert result.file_count == 2
    assert result.dir_count == 3  # 441, chunk, ab


class _UnstartableThread(threading.Thread):
    def start(self) -> None:
        raise RuntimeError("can't start new thread")


def test_a_walk_thread_that_cannot_start_errors_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walk = FakeWalk()
    cache = SizeCache(ttl_seconds=60.0, walk=walk)
    monkeypatch.setattr(sizes.threading, "Thread", _UnstartableThread)

    # A blocked first reader gets the error, it does not wait forever.
    with pytest.raises(RuntimeError, match="can't start"):
        cache.get(ROOT, now=0.0)
    assert cache.wait_idle(timeout=0.5), "the walking flag must be cleared"

    monkeypatch.undo()
    _warm(cache, walk, {441: 10})
    old = cache.get(ROOT, now=1.0)

    # An expired read whose refresh cannot start still gets the old snapshot.
    monkeypatch.setattr(sizes.threading, "Thread", _UnstartableThread)
    assert cache.get(ROOT, now=100.0) is old
    assert cache.wait_idle(timeout=0.5)
    assert walk.calls == 1
