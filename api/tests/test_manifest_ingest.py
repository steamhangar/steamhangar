"""vault_api/manifest_ingest.py (WP 3.2): scan -> parse -> store -> archive.

Reuses the protobuf encoder from ``tests/test_manifests.py`` (per this work
package's instructions) rather than duplicating it — a bug in the encoder
would then have to agree with a second, independent bug in this file's own
encoder to hide, which defeats the point of an independent test.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from tests.test_manifests import _bin_manifest_bytes, _chunk_id

from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.depot_manifests import get_depot_manifest
from vault_api.manifest_ingest import (
    find_canary_mismatches,
    ingest_after_prefill,
    log_cache_dir_canary,
)

TEST_API_KEY = "test-api-key-do-not-use-in-prod"


def _settings(tmp_path: Path, *, cache_dir: Path, archive_dir: Path, keep: int = 3) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        manifest_archive_dir=str(archive_dir),
        manifest_keep=keep,
        steamprefill_cache_dir=str(cache_dir),
    )


def _write_bin_file(directory: Path, filename: str, *, depot_id: int, manifest_id: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    data = _bin_manifest_bytes(
        depot_id=depot_id,
        manifest_id=manifest_id,
        files=[[(_chunk_id(1), 1000), (_chunk_id(2), 2000)]],
    )
    path = directory / filename
    path.write_bytes(data)
    return path


def _conn(db_path: str):
    init_db(db_path)
    return get_connection(db_path)


# -- happy path --------------------------------------------------------------


def test_ingest_happy_path_records_row_additive_mapping_and_archives(tmp_path: Path) -> None:
    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir)

    # 107100_228980_229002_987.bin: real shape from the research doc -- app
    # 107100 pulling a depot whose "home" app is 228980 (a shared depot).
    _write_bin_file(
        cache_dir, "107100_228980_229002_987.bin", depot_id=229002, manifest_id=987
    )

    conn = _conn(settings.db_path)
    try:
        result = ingest_after_prefill(conn, appid=107100, settings=settings)

        assert result.parse_failures == []
        assert len(result.ingested) == 1
        ingested = result.ingested[0]
        assert ingested.depotid == 229002
        assert ingested.manifestid == "987"
        assert ingested.containing_appid == 228980
        assert ingested.chunk_count == 2
        assert ingested.total_bytes == 3000

        row = get_depot_manifest(conn, appid=107100, depotid=229002)
        assert row["manifestid"] == "987"
        assert row["containing_appid"] == 228980
        # "steamprefill_bin", not "prefill_bin": reuses the constant WP 3.1
        # already shipped (manifests.SOURCE_STEAMPREFILL_BIN) rather than
        # inventing a second spelling for the same thing.
        assert row["source"] == "steamprefill_bin"

        # Additive shared-depot mapping (WP 3.2 item 4): the depot is ALSO
        # mapped to its containing app, on top of whatever the job's own
        # replace-set mapping does (unaffected here -- this module never
        # touches appid=107100's mapping).
        mapping_rows = conn.execute(
            "SELECT appid FROM depot_app_map WHERE depotid = 229002"
        ).fetchall()
        assert {row["appid"] for row in mapping_rows} == {228980}
    finally:
        conn.close()

    archived = archive_dir / "229002_987.bin"
    assert archived.exists()
    assert archived.read_bytes() == (cache_dir / "107100_228980_229002_987.bin").read_bytes()


def test_ingest_own_depot_containing_appid_equals_appid_no_extra_mapping(
    tmp_path: Path,
) -> None:
    """The common case: a depot that belongs to the app being prefilled.
    containing_appid == appid, so no additive mapping call happens (nothing
    to add on top of the job's own mapping)."""
    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir)
    _write_bin_file(cache_dir, "440_440_441_123.bin", depot_id=441, manifest_id=123)

    conn = _conn(settings.db_path)
    try:
        result = ingest_after_prefill(conn, appid=440, settings=settings)
        assert len(result.ingested) == 1
        assert result.ingested[0].containing_appid == 440

        mapping_rows = conn.execute(
            "SELECT appid FROM depot_app_map WHERE depotid = 441"
        ).fetchall()
        assert mapping_rows == []  # this module never writes the job's own mapping
    finally:
        conn.close()


def test_ingest_only_matches_files_for_this_jobs_appid(tmp_path: Path) -> None:
    """A file for a DIFFERENT app sitting in the same shared cache dir must
    not be touched by this job's ingestion pass."""
    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir)
    _write_bin_file(cache_dir, "440_440_441_1.bin", depot_id=441, manifest_id=1)
    _write_bin_file(cache_dir, "730_730_442_1.bin", depot_id=442, manifest_id=1)

    conn = _conn(settings.db_path)
    try:
        result = ingest_after_prefill(conn, appid=440, settings=settings)
        assert [entry.depotid for entry in result.ingested] == [441]
        assert get_depot_manifest(conn, appid=730, depotid=442) is None
    finally:
        conn.close()


def test_ingest_depot_id_44_does_not_falsely_match_depot_441_files(tmp_path: Path) -> None:
    """Filename prefix matching (f'{appid}_') must not be fooled by a shorter
    appid being a numeric prefix of a longer one."""
    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir)
    _write_bin_file(cache_dir, "4400_4400_1_1.bin", depot_id=1, manifest_id=1)

    conn = _conn(settings.db_path)
    try:
        result = ingest_after_prefill(conn, appid=440, settings=settings)
        assert result.ingested == []
        assert result.parse_failures == []
    finally:
        conn.close()


# -- replace-on-newer ---------------------------------------------------------


def test_ingest_twice_replaces_the_stored_manifest(tmp_path: Path) -> None:
    """Two successive worker runs (two separate ingest_after_prefill calls,
    as the real worker makes one per successful job) -- the second call's
    manifest for the same (appid, depotid) replaces the first."""
    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir)

    conn = _conn(settings.db_path)
    try:
        _write_bin_file(cache_dir, "440_440_441_1.bin", depot_id=441, manifest_id=1)
        ingest_after_prefill(conn, appid=440, settings=settings)
        assert get_depot_manifest(conn, appid=440, depotid=441)["manifestid"] == "1"

        # Simulate SteamPrefill overwriting its own temp-cache file after a
        # game update: old file gone, new manifest id in its place.
        (cache_dir / "440_440_441_1.bin").unlink()
        _write_bin_file(cache_dir, "440_440_441_2.bin", depot_id=441, manifest_id=2)
        ingest_after_prefill(conn, appid=440, settings=settings)

        row = get_depot_manifest(conn, appid=440, depotid=441)
        assert row["manifestid"] == "2"
    finally:
        conn.close()

    # Both manifests were archived (retention keeps both at the default of 3).
    assert (archive_dir / "441_1.bin").exists()
    assert (archive_dir / "441_2.bin").exists()


def test_ingest_respects_manifest_keep_retention(tmp_path: Path) -> None:
    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir, keep=1)

    conn = _conn(settings.db_path)
    try:
        for manifest_id in (1, 2, 3):
            for old in cache_dir.glob("440_440_441_*.bin"):
                old.unlink()
            _write_bin_file(
                cache_dir, f"440_440_441_{manifest_id}.bin", depot_id=441, manifest_id=manifest_id
            )
            ingest_after_prefill(conn, appid=440, settings=settings)
    finally:
        conn.close()

    remaining = sorted(p.name for p in archive_dir.glob("441_*.bin"))
    assert remaining == ["441_3.bin"]


def test_ingest_keep_retention_holds_when_every_archive_shares_one_mtime(
    tmp_path: Path, monkeypatch
) -> None:
    """WP API-FIX-2: the retention test above used to fail intermittently
    because the three archives landed in one kernel clock tick (equal
    st_mtime_ns) and prune order fell back to scandir order. Force the tie
    outright: the just-ingested manifest must still be the one kept."""
    import vault_api.manifest_archive as archive_module

    monkeypatch.setattr(archive_module.time, "time_ns", lambda: 1_000_000_000)
    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir, keep=1)

    conn = _conn(settings.db_path)
    try:
        for manifest_id in (3, 2, 1):
            for old in cache_dir.glob("440_440_441_*.bin"):
                old.unlink()
            _write_bin_file(
                cache_dir, f"440_440_441_{manifest_id}.bin", depot_id=441, manifest_id=manifest_id
            )
            ingest_after_prefill(conn, appid=440, settings=settings)
            remaining = sorted(p.name for p in archive_dir.glob("441_*.bin"))
            assert remaining == [f"441_{manifest_id}.bin"]
    finally:
        conn.close()


# -- parse-failure tolerance ---------------------------------------------------


def test_ingest_skips_and_warns_on_a_corrupt_file_but_still_ingests_the_rest(
    tmp_path: Path, caplog
) -> None:
    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir)
    cache_dir.mkdir(parents=True)

    _write_bin_file(cache_dir, "440_440_441_1.bin", depot_id=441, manifest_id=1)
    (cache_dir / "440_440_442_2.bin").write_bytes(b"not a valid manifest payload at all")

    conn = _conn(settings.db_path)
    try:
        with caplog.at_level(logging.WARNING):
            result = ingest_after_prefill(conn, appid=440, settings=settings)

        assert [entry.depotid for entry in result.ingested] == [441]
        assert result.parse_failures == ["440_440_442_2.bin"]
        assert result.vanished_during_scan == []
        assert get_depot_manifest(conn, appid=440, depotid=442) is None
        assert any("parse failed" in message for message in caplog.messages)
    finally:
        conn.close()


def test_ingest_counts_a_file_that_vanishes_mid_scan_separately_from_parse_failures(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """WP 3.2 review nitpick: a file that disappears BETWEEN the directory
    listing and the parse attempt (SteamPrefill's own clear-temp running
    concurrently, or an operator clearing the directory by hand) is an I/O
    race, not a data-quality problem -- it must not be conflated with a
    genuine parse failure in the job log."""
    import vault_api.manifest_ingest as ingest_module

    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir)
    _write_bin_file(cache_dir, "440_440_441_1.bin", depot_id=441, manifest_id=1)

    real_parse_bin_filename = ingest_module.parse_bin_filename

    def vanish_then_parse_filename(file_path):
        result = real_parse_bin_filename(file_path)
        os.unlink(file_path)  # gone before parse_steamprefill_bin opens it
        return result

    monkeypatch.setattr(ingest_module, "parse_bin_filename", vanish_then_parse_filename)

    conn = _conn(settings.db_path)
    try:
        with caplog.at_level(logging.INFO):
            result = ingest_after_prefill(conn, appid=440, settings=settings)

        assert result.ingested == []
        assert result.parse_failures == []
        assert result.vanished_during_scan == ["440_440_441_1.bin"]
        assert get_depot_manifest(conn, appid=440, depotid=441) is None
        assert any("vanished during the scan" in message for message in caplog.messages)
        assert "vanished during the scan" in result.summary()
        assert "parse failed" not in result.summary()
    finally:
        conn.close()


def test_ingest_archived_path_is_none_when_archiving_fails_row_still_recorded(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """WP 3.2 review nitpick: archived_path is Optional[str], never an empty
    string -- an absent archive is a real "no archive exists" state."""
    import vault_api.manifest_ingest as ingest_module

    cache_dir = tmp_path / "steamprefill-cache"
    archive_dir = tmp_path / "archive"
    settings = _settings(tmp_path, cache_dir=cache_dir, archive_dir=archive_dir)
    _write_bin_file(cache_dir, "440_440_441_1.bin", depot_id=441, manifest_id=1)

    def boom(*_args, **_kwargs):
        raise OSError("simulated archive failure")

    monkeypatch.setattr(ingest_module.manifest_archive, "archive_manifest", boom)

    conn = _conn(settings.db_path)
    try:
        with caplog.at_level(logging.WARNING):
            result = ingest_after_prefill(conn, appid=440, settings=settings)

        assert len(result.ingested) == 1
        assert result.ingested[0].archived_path is None
        assert get_depot_manifest(conn, appid=440, depotid=441) is not None
        assert any("archiving failed" in message for message in caplog.messages)
    finally:
        conn.close()


# -- cache dir absent ----------------------------------------------------------


def test_ingest_missing_cache_dir_is_not_an_error(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        cache_dir=tmp_path / "does-not-exist",
        archive_dir=tmp_path / "archive",
    )
    conn = _conn(settings.db_path)
    try:
        result = ingest_after_prefill(conn, appid=440, settings=settings)
    finally:
        conn.close()

    assert result.cache_dir_unavailable is True
    assert result.ingested == []
    assert "not found" in result.summary()


# -- canary --------------------------------------------------------------------


def test_canary_finds_no_mismatches_for_well_formed_filenames(tmp_path: Path) -> None:
    cache_dir = tmp_path / "steamprefill-cache"
    _write_bin_file(cache_dir, "440_440_441_1.bin", depot_id=441, manifest_id=1)

    assert find_canary_mismatches(str(cache_dir)) == []


def test_canary_flags_a_malformed_bin_filename(tmp_path: Path) -> None:
    """Only a malformed *.bin file is a real mismatch -- see the
    sidecar-ignoring test below for why non-.bin files are never flagged."""
    cache_dir = tmp_path / "steamprefill-cache"
    cache_dir.mkdir()
    (cache_dir / "not-the-right-shape.bin").write_bytes(b"x")  # .bin, wrong shape
    (cache_dir / "440_440_441_1.bin").write_bytes(b"x")  # well-formed, ignored

    mismatches = find_canary_mismatches(str(cache_dir))

    assert mismatches == ["not-the-right-shape.bin"]


def test_canary_ignores_known_non_bin_sidecar_files(tmp_path: Path) -> None:
    """WP 3.2 review fix: SteamPrefill's real temp-cache directory also holds
    cellId.txt and lastUpdateCheck.txt (observed on a live host) -- these are
    expected sidecars, not manifests, and must never be flagged, regardless
    of their name. The bug this pins: the first version of this function
    warned on every single boot of a real deployment because of exactly
    these two files."""
    cache_dir = tmp_path / "steamprefill-cache"
    cache_dir.mkdir()
    (cache_dir / "cellId.txt").write_bytes(b"1")
    (cache_dir / "lastUpdateCheck.txt").write_bytes(b"2026-08-06T00:00:00Z")
    _write_bin_file(cache_dir, "440_440_441_1.bin", depot_id=441, manifest_id=1)

    assert find_canary_mismatches(str(cache_dir)) == []


def test_canary_on_a_missing_directory_returns_empty(tmp_path: Path) -> None:
    assert find_canary_mismatches(str(tmp_path / "nope")) == []


def test_log_cache_dir_canary_warns_once_for_mismatches(tmp_path: Path, caplog) -> None:
    cache_dir = tmp_path / "steamprefill-cache"
    cache_dir.mkdir()
    (cache_dir / "not-the-right-shape.bin").write_bytes(b"x")

    with caplog.at_level(logging.WARNING):
        log_cache_dir_canary(str(cache_dir))

    assert any("coupling canary" in message for message in caplog.messages)


def test_log_cache_dir_canary_is_silent_for_known_sidecar_files(
    tmp_path: Path, caplog
) -> None:
    """The false-positive this fix removes: booting with a real SteamPrefill
    temp-cache directory (sidecars included) must never warn."""
    cache_dir = tmp_path / "steamprefill-cache"
    cache_dir.mkdir()
    (cache_dir / "cellId.txt").write_bytes(b"1")
    (cache_dir / "lastUpdateCheck.txt").write_bytes(b"2026-08-06T00:00:00Z")
    _write_bin_file(cache_dir, "440_440_441_1.bin", depot_id=441, manifest_id=1)

    with caplog.at_level(logging.WARNING):
        log_cache_dir_canary(str(cache_dir))

    assert caplog.messages == []


def test_log_cache_dir_canary_is_silent_when_everything_matches(
    tmp_path: Path, caplog
) -> None:
    cache_dir = tmp_path / "steamprefill-cache"
    _write_bin_file(cache_dir, "440_440_441_1.bin", depot_id=441, manifest_id=1)

    with caplog.at_level(logging.WARNING):
        log_cache_dir_canary(str(cache_dir))

    assert caplog.messages == []
