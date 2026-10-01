"""Per-game size calculation: the disk walk, the TTL cache, and the
per-app / summary aggregation (vault_api/sizes.py, WP 1.5)."""

from __future__ import annotations

import os
from pathlib import Path

from tests.conftest import make_dir_link, make_junction
from vault_api import sizes
from vault_api.db import get_connection, init_db
from vault_api.mapping import upsert_mapping
from vault_api.sizes import (
    SizeCache,
    app_size_bytes,
    build_cache_summary,
    free_disk_bytes,
    scan_depot_dir_bytes,
    scan_depot_signatures,
    walk_file_stats,
)


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# -- the filesystem walk -----------------------------------------------------


def test_walk_file_stats_finds_nested_files(tmp_path: Path) -> None:
    _write(tmp_path / "a.bin", b"12345")
    _write(tmp_path / "sub" / "b.bin", b"12")
    _write(tmp_path / "sub" / "deeper" / "c.bin", b"1")

    sizes_found = sorted(s.st_size for s in walk_file_stats(str(tmp_path)))
    assert sizes_found == [1, 2, 5]


def test_walk_file_stats_on_missing_path_yields_nothing(tmp_path: Path) -> None:
    assert list(walk_file_stats(str(tmp_path / "does-not-exist"))) == []


def test_walk_file_stats_does_not_follow_links_inside_the_tree(tmp_path: Path) -> None:
    """Cycle protection + "reported size == deletable size" (WP 1.6).

    ``follow_symlinks=False`` alone is not enough on Windows: measured,
    ``DirEntry.is_dir(follow_symlinks=False)`` is **True** for a junction, so
    without the explicit link check the walk descends through it — counting
    foreign bytes as this depot's size, and recursing forever if the junction
    points at one of its own ancestors.
    """
    depot = tmp_path / "441"
    _write(depot / "chunk" / "aa", b"1" * 5)
    outside = tmp_path / "outside"
    _write(outside / "big.bin", b"1" * 1000)
    kind = make_dir_link(depot / "chunk" / "sneaky", outside)

    total = sum(stat.st_size for stat in walk_file_stats(str(depot)))

    assert total == 5, f"the walk followed a {kind} out of the tree"


def test_walk_file_stats_does_not_follow_a_junction_inside_the_tree(
    tmp_path: Path,
) -> None:
    """The junction-specific half of the test above — the one that actually bites.

    ``make_dir_link`` prefers a symlink where it can create one, and
    ``follow_symlinks=False`` already handled those. A junction is the case
    where it does not: ``DirEntry.is_dir(follow_symlinks=False)`` is **True**
    for a junction (measured), so before the explicit link check this walk
    descended into it — counting bytes outside the depot as the depot's size.
    """
    depot = tmp_path / "441"
    _write(depot / "chunk" / "aa", b"1" * 5)
    outside = tmp_path / "outside"
    _write(outside / "big.bin", b"1" * 1000)
    make_junction(depot / "chunk" / "sneaky", outside)

    total = sum(stat.st_size for stat in walk_file_stats(str(depot)))

    assert total == 5, "the walk followed a junction out of the tree"


def test_scan_depot_signatures_sees_a_linked_depot_directory(tmp_path: Path) -> None:
    """WP 1.6 carry-over fix #1 from the WP 1.5 review.

    A depot directory placed as a link onto another volume was **invisible** to
    the scan, because top-level entries were checked with
    ``is_dir(follow_symlinks=False)`` — ``False`` for a directory symlink
    (measured; a Windows junction was reported as ``True`` and did show up, so
    only symlinks were affected). The consequence was worse than an
    under-reported size: the depot never appeared in a prefill's before/after
    snapshot either, so ``apply_observed_mapping`` treated the app's correct
    mapping row as stale and deleted it on the next prefill.
    """
    cache_root = tmp_path / "cache"
    (cache_root / "depot").mkdir(parents=True)
    outside = tmp_path / "other-volume" / "441"
    _write(outside / "chunk" / "aa", b"1" * 20)
    kind = make_dir_link(cache_root / "depot" / "441", outside)

    result = scan_depot_signatures(str(cache_root))

    assert 441 in result, f"a {kind} depot directory must be visible to the scan"
    file_count, total_bytes, _newest_mtime = result[441]
    assert (file_count, total_bytes) == (1, 20)
    assert scan_depot_dir_bytes(str(cache_root)) == {441: 20}


def test_is_link_like_detects_both_symlinks_and_junctions(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    kind = make_dir_link(link, real)

    assert sizes.is_link_like(str(link)) is True, f"{kind} not detected"
    assert sizes.is_link_like(str(real)) is False
    # os.path.islink alone is exactly what this helper exists to fix: measured
    # False for a Windows junction.
    if kind == "junction":
        assert os.path.islink(str(link)) is False


def test_scan_depot_dir_bytes_sums_per_depot_and_ignores_empty_and_non_numeric(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1234")
    _write(cache_root / "depot" / "441" / "chunk" / "b", b"12")
    _write(cache_root / "depot" / "442" / "chunk" / "a", b"1")
    (cache_root / "depot" / "443").mkdir(parents=True)  # empty -> ignored
    _write(cache_root / "depot" / "not-a-depotid" / "a", b"999")  # non-numeric -> ignored

    result = scan_depot_dir_bytes(str(cache_root))

    assert result == {441: 6, 442: 1}


def test_scan_depot_signatures_matches_prefill_scan_depots(tmp_path: Path) -> None:
    # WP 1.5 carry-over fix #1: prefill.scan_depots is now a thin wrapper
    # around this function — pin that they agree, not just that each works.
    from vault_api import prefill

    cache_root = tmp_path / "cache"
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1234")

    assert scan_depot_signatures(str(cache_root)) == prefill.scan_depots(str(cache_root))


# -- per-app aggregation ------------------------------------------------------


def test_app_size_bytes_is_none_when_unmapped() -> None:
    assert app_size_bytes([], {441: 100}) is None


def test_app_size_bytes_is_none_when_mapped_but_uncached() -> None:
    assert app_size_bytes([441, 442], {}) is None


def test_app_size_bytes_sums_partially_cached_depots() -> None:
    # 442 has no entry (not cached yet) -> contributes 0, but the result is
    # NOT None, since at least one depot (441) IS cached.
    assert app_size_bytes([441, 442], {441: 100}) == 100


def test_app_size_bytes_counts_a_shared_depot_fully_into_each_app() -> None:
    depot_bytes = {900: 50, 441: 10, 442: 20}
    tf2 = app_size_bytes([441, 900], depot_bytes)
    cs2 = app_size_bytes([442, 900], depot_bytes)
    assert tf2 == 60
    assert cs2 == 70
    # Sum of the two apps' sizes double-counts the shared depot; the real
    # disk usage (see test_build_cache_summary_total_counts_shared_once)
    # is only 80, not 130 — this asymmetry is documented, not a bug.
    assert tf2 + cs2 == 130


# -- the TTL cache -------------------------------------------------------


def test_size_cache_recomputes_only_after_ttl_expires(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1234")

    cache = SizeCache(ttl_seconds=10.0)
    first = cache.get(str(cache_root), now=0.0)
    assert first.depot_bytes == {441: 4}

    # New file written, but well within the TTL window -> stale snapshot
    # returned, not a fresh scan.
    _write(cache_root / "depot" / "442" / "chunk" / "a", b"1")
    still_cached = cache.get(str(cache_root), now=5.0)
    assert still_cached is first
    assert still_cached.depot_bytes == {441: 4}

    # TTL expired -> recompute, now sees the new file.
    after_ttl = cache.get(str(cache_root), now=10.001)
    assert after_ttl is not first
    assert after_ttl.depot_bytes == {441: 4, 442: 1}


def test_size_cache_invalidate_forces_a_recompute_regardless_of_ttl(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1234")

    cache = SizeCache(ttl_seconds=999.0)
    first = cache.get(str(cache_root), now=0.0)
    assert first.depot_bytes == {441: 4}

    _write(cache_root / "depot" / "442" / "chunk" / "a", b"12")
    cache.invalidate()

    refreshed = cache.get(str(cache_root), now=0.5)  # still well inside the TTL
    assert refreshed is not first
    assert refreshed.depot_bytes == {441: 4, 442: 2}


def test_size_cache_total_bytes_counts_each_depot_once(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1234")
    _write(cache_root / "depot" / "442" / "chunk" / "a", b"12")

    cache = SizeCache(ttl_seconds=60.0)
    snapshot = cache.get(str(cache_root))
    assert snapshot.total_bytes == 6


# -- free disk space -----------------------------------------------------


def test_free_disk_bytes_of_an_existing_path_is_a_positive_int(tmp_path: Path) -> None:
    result = free_disk_bytes(str(tmp_path))
    assert isinstance(result, int)
    assert result > 0


def test_free_disk_bytes_walks_up_to_an_existing_ancestor(tmp_path: Path) -> None:
    missing = tmp_path / "does" / "not" / "exist" / "yet"
    result = free_disk_bytes(str(missing))
    assert isinstance(result, int)
    assert result > 0


# -- cache summary ---------------------------------------------------------


def _conn(tmp_path: Path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    return get_connection(db_path)


def test_build_cache_summary_total_counts_shared_once(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)  # TF2 only
    _write(cache_root / "depot" / "900" / "chunk" / "a", b"1" * 50)  # shared

    conn = _conn(tmp_path)
    try:
        upsert_mapping(conn, depotid=441, appid=440, name="Team Fortress 2")
        upsert_mapping(conn, depotid=900, appid=440, name="Team Fortress 2")
        upsert_mapping(conn, depotid=900, appid=730, name="Counter-Strike 2")

        summary = build_cache_summary(
            conn, str(cache_root), SizeCache(ttl_seconds=60.0).get(str(cache_root))
        )
    finally:
        conn.close()

    assert summary.total_bytes == 60  # 10 + 50, counted once each

    consumer_by_appid = {c.appid: c for c in summary.top_consumers}
    assert consumer_by_appid[440].size_bytes == 60  # 441 + shared 900
    assert consumer_by_appid[730].size_bytes == 50  # shared 900 only
    assert consumer_by_appid[440].name == "Team Fortress 2"
    # Per-app sizes sum to more than total_bytes (60 + 50 = 110 > 60) because
    # depot 900 is shared — documented in app_size_bytes.
    assert sum(c.size_bytes for c in summary.top_consumers) == 110


def test_build_cache_summary_reports_unmapped_depots(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)  # mapped
    _write(cache_root / "depot" / "555" / "chunk" / "a", b"1" * 30)  # not mapped anywhere

    conn = _conn(tmp_path)
    try:
        upsert_mapping(conn, depotid=441, appid=440, name="Team Fortress 2")
        summary = build_cache_summary(
            conn, str(cache_root), SizeCache(ttl_seconds=60.0).get(str(cache_root))
        )
    finally:
        conn.close()

    assert summary.unmapped_depots.count == 1
    assert summary.unmapped_depots.size_bytes == 30


def test_build_cache_summary_top_consumers_capped_and_sorted(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    conn = _conn(tmp_path)
    try:
        for i in range(12):
            depotid = 1000 + i
            appid = 2000 + i
            _write(
                cache_root / "depot" / str(depotid) / "chunk" / "a",
                b"1" * (i + 1),
            )
            upsert_mapping(conn, depotid=depotid, appid=appid, name=f"Game {i}")

        summary = build_cache_summary(
            conn,
            str(cache_root),
            SizeCache(ttl_seconds=60.0).get(str(cache_root)),
            top_n=10,
        )
    finally:
        conn.close()

    assert len(summary.top_consumers) == 10
    reported = [c.size_bytes for c in summary.top_consumers]
    assert reported == sorted(reported, reverse=True)
    assert reported[0] == 12  # the largest (i=11 -> 12 bytes)


def test_build_cache_summary_reports_exactly_the_snapshot_it_is_given(
    tmp_path: Path,
) -> None:
    """WP 1.6 carry-over fix #2: the summary takes a ready ``SizeSnapshot``.

    It no longer calls ``SizeCache.get()`` itself (that would walk the depot
    tree under the cache lock while the caller's SQLite connection sits open —
    the caller now does it first, see ``routers/cache.py``). So the function is
    a pure function of its inputs, and the TTL/invalidation contract is
    observable in what the *caller* passes in.
    """
    cache_root = tmp_path / "cache"
    _write(cache_root / "depot" / "441" / "chunk" / "a", b"1" * 10)

    conn = _conn(tmp_path)
    try:
        upsert_mapping(conn, depotid=441, appid=440, name="Team Fortress 2")
        cache = SizeCache(ttl_seconds=999.0)
        first = build_cache_summary(conn, str(cache_root), cache.get(str(cache_root)))
        assert first.total_bytes == 10

        # More bytes on disk, but a stale (within-TTL) snapshot -> stale total.
        _write(cache_root / "depot" / "441" / "chunk" / "b", b"1" * 90)
        still_stale = build_cache_summary(
            conn, str(cache_root), cache.get(str(cache_root))
        )
        assert still_stale.total_bytes == 10

        cache.invalidate()
        fresh = build_cache_summary(conn, str(cache_root), cache.get(str(cache_root)))
        assert fresh.total_bytes == 100
    finally:
        conn.close()


def test_scan_depot_signatures_ignores_non_ascii_digit_directory_names(tmp_path: Path) -> None:
    """WP API-FIX-2, N1: ``str.isdigit()`` is True for superscripts and
    Arabic-Indic digits; ``int("²")`` raises, which crashed the whole scan on
    one odd directory name, and ``int("٤٤١")`` silently became 441."""
    cache_root = tmp_path / "cache"
    _write(cache_root / "depot" / "441" / "chunk" / "aa", b"1" * 20)
    _write(cache_root / "depot" / "²" / "chunk" / "aa", b"1" * 5)
    _write(cache_root / "depot" / "٤٤١" / "chunk" / "aa", b"1" * 7)

    result = scan_depot_signatures(str(cache_root))

    assert set(result) == {441}
    assert result[441][:2] == (1, 20)
