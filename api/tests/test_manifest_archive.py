"""vault_api/manifest_archive.py (WP 3.2): archive + retention."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vault_api import manifest_archive
from vault_api.manifest_archive import archive_filename, archive_manifest, prune_archive


def test_archive_manifest_copies_bytes_verbatim(tmp_path: Path) -> None:
    src = tmp_path / "source.bin"
    src.write_bytes(b"fake-manifest-payload")
    archive_dir = tmp_path / "archive"

    dest = archive_manifest(str(archive_dir), depotid=441, manifestid="123", src_path=str(src))

    assert os.path.basename(dest) == "441_123.bin"
    assert Path(dest).read_bytes() == b"fake-manifest-payload"


def test_archive_manifest_creates_the_archive_dir_if_missing(tmp_path: Path) -> None:
    src = tmp_path / "source.bin"
    src.write_bytes(b"x")
    archive_dir = tmp_path / "does" / "not" / "exist" / "yet"

    dest = archive_manifest(str(archive_dir), depotid=1, manifestid="1", src_path=str(src))

    assert Path(dest).exists()


def test_archive_manifest_leaves_no_tempfile_behind_on_success(tmp_path: Path) -> None:
    src = tmp_path / "source.bin"
    src.write_bytes(b"payload")
    archive_dir = tmp_path / "archive"

    archive_manifest(str(archive_dir), depotid=441, manifestid="123", src_path=str(src))

    names = os.listdir(archive_dir)
    assert names == ["441_123.bin"]  # no leftover .tmp file


def test_archive_manifest_reingesting_the_same_pair_overwrites_cleanly(tmp_path: Path) -> None:
    """A repeated ingest of an already-current app's manifest re-archives the
    same (depotid, manifestid) -- os.replace must overwrite, not fail or
    duplicate."""
    src1 = tmp_path / "a.bin"
    src1.write_bytes(b"first-version")
    src2 = tmp_path / "b.bin"
    src2.write_bytes(b"second-version-longer-payload")
    archive_dir = tmp_path / "archive"

    archive_manifest(str(archive_dir), depotid=441, manifestid="123", src_path=str(src1))
    dest = archive_manifest(str(archive_dir), depotid=441, manifestid="123", src_path=str(src2))

    assert Path(dest).read_bytes() == b"second-version-longer-payload"
    assert os.listdir(archive_dir) == ["441_123.bin"]


def test_archive_manifest_raises_oserror_for_a_missing_source(tmp_path: Path) -> None:
    import pytest

    archive_dir = tmp_path / "archive"
    with pytest.raises(OSError):
        archive_manifest(
            str(archive_dir), depotid=441, manifestid="1",
            src_path=str(tmp_path / "does-not-exist.bin"),
        )
    # And no tempfile leftover from the failed attempt.
    assert not os.path.isdir(archive_dir) or os.listdir(archive_dir) == []


def test_archive_filename_matches_the_documented_template() -> None:
    assert archive_filename(depotid=441, manifestid="123") == "441_123.bin"


# -- retention / pruning ----------------------------------------------------


def _archive_n(archive_dir: Path, depotid: int, manifestids: list[str]) -> None:
    """Archive several manifests for one depot, in order, with distinct
    mtimes (the pruning signal) -- a small sleep would be flaky under load,
    so mtimes are set explicitly instead."""
    import time

    for index, manifestid in enumerate(manifestids):
        src = archive_dir.parent / f"src-{depotid}-{manifestid}.bin"
        src.write_bytes(f"payload-{manifestid}".encode())
        dest = archive_manifest(
            str(archive_dir), depotid=depotid, manifestid=manifestid, src_path=str(src)
        )
        # Force a strictly increasing mtime per archived file, oldest first,
        # regardless of how fast the filesystem clock ticks in a test run.
        stamp = time.time() + index
        os.utime(dest, (stamp, stamp))


def test_prune_archive_keeps_only_the_newest_n(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    _archive_n(archive_dir, depotid=441, manifestids=["1", "2", "3", "4", "5"])

    removed = prune_archive(str(archive_dir), depotid=441, keep=3)

    remaining = sorted(os.listdir(archive_dir))
    assert remaining == ["441_3.bin", "441_4.bin", "441_5.bin"]
    assert set(removed) == {"441_1.bin", "441_2.bin"}


def test_prune_archive_never_removes_more_depots_than_asked(tmp_path: Path) -> None:
    """A shorter depot id must not prefix-match a longer one's files
    (depot 44 vs depot 441)."""
    archive_dir = tmp_path / "archive"
    _archive_n(archive_dir, depotid=44, manifestids=["1", "2"])
    _archive_n(archive_dir, depotid=441, manifestids=["1", "2"])

    prune_archive(str(archive_dir), depotid=44, keep=1)

    remaining = set(os.listdir(archive_dir))
    # Only depot 44's older file is pruned; depot 441's two files survive.
    assert remaining == {"44_2.bin", "441_1.bin", "441_2.bin"}


def test_prune_archive_keep_at_or_above_count_removes_nothing(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    _archive_n(archive_dir, depotid=441, manifestids=["1", "2"])

    removed = prune_archive(str(archive_dir), depotid=441, keep=5)

    assert removed == []
    assert sorted(os.listdir(archive_dir)) == ["441_1.bin", "441_2.bin"]


def test_prune_archive_on_a_missing_directory_returns_empty_and_does_not_raise(
    tmp_path: Path,
) -> None:
    removed = prune_archive(str(tmp_path / "never-created"), depotid=441, keep=3)
    assert removed == []


def test_prune_archive_survives_an_entry_that_cannot_be_stated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """WP API-FIX-2, N2: the docstring promises "never raises", but the sort
    key called ``entry.stat()`` outside the try -- an entry vanishing between
    scandir and the sort raised ``OSError`` out of an ingestion pass."""
    archive_dir = tmp_path / "archive"
    _archive_n(archive_dir, depotid=441, manifestids=["1", "2", "3"])

    class _Vanished:
        name = "441_9.bin"
        path = str(archive_dir / "441_9.bin")

        def is_file(self) -> bool:
            return True

        def stat(self):
            raise FileNotFoundError(self.path)

    real_scandir = os.scandir

    def fake_scandir(path):
        return list(real_scandir(path)) + [_Vanished()]

    monkeypatch.setattr(manifest_archive.os, "scandir", fake_scandir)
    with caplog.at_level("WARNING", logger="vault_api.manifest_archive"):
        removed = prune_archive(str(archive_dir), depotid=441, keep=2)

    assert removed == ["441_1.bin"]
    assert sorted(os.listdir(archive_dir)) == ["441_2.bin", "441_3.bin"]
    assert any("could not stat" in r.getMessage() for r in caplog.records)


# -- mtime ties (WP API-FIX-2) ------------------------------------------------
# Kernel file timestamps come from a coarse clock tick, so archives written
# in quick succession can share one st_mtime_ns; ranking then must not fall
# back to scandir order (which is hash order, not insertion order).


def _archive_with_equal_mtimes(archive_dir: Path, depotid: int, manifestids: list[str]) -> None:
    for manifestid in manifestids:
        src = archive_dir.parent / f"src-{depotid}-{manifestid}.bin"
        src.write_bytes(f"payload-{manifestid}".encode())
        dest = archive_manifest(
            str(archive_dir), depotid=depotid, manifestid=manifestid, src_path=str(src)
        )
        os.utime(dest, ns=(1_000_000_000, 1_000_000_000))


def test_prune_archive_always_keeps_current_on_an_mtime_tie(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    # "1" sorts lowest by name, so only the explicit current pin can save it.
    _archive_with_equal_mtimes(archive_dir, depotid=441, manifestids=["3", "2", "1"])

    removed = prune_archive(str(archive_dir), depotid=441, keep=1, current="441_1.bin")

    assert sorted(os.listdir(archive_dir)) == ["441_1.bin"]
    assert set(removed) == {"441_2.bin", "441_3.bin"}


def test_prune_archive_keeps_current_even_when_its_mtime_is_older(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    _archive_n(archive_dir, depotid=441, manifestids=["1", "2", "3"])

    prune_archive(str(archive_dir), depotid=441, keep=1, current="441_1.bin")

    assert sorted(os.listdir(archive_dir)) == ["441_1.bin"]


def test_prune_archive_breaks_remaining_ties_deterministically(tmp_path: Path) -> None:
    for run in range(5):
        archive_dir = tmp_path / f"archive-{run}"
        _archive_with_equal_mtimes(archive_dir, depotid=441, manifestids=["a", "b", "c", "d"])

        prune_archive(str(archive_dir), depotid=441, keep=2)

        assert sorted(os.listdir(archive_dir)) == ["441_c.bin", "441_d.bin"]


def test_archive_manifest_stamps_strictly_increasing_mtimes(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")

    mtimes = [
        os.stat(
            archive_manifest(str(archive_dir), depotid=441, manifestid=str(i), src_path=str(src))
        ).st_mtime_ns
        for i in range(5)
    ]

    assert mtimes == sorted(mtimes)
    assert len(set(mtimes)) == len(mtimes)


def test_archive_manifest_survives_a_utime_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    src = tmp_path / "source.bin"
    src.write_bytes(b"payload")
    archive_dir = tmp_path / "archive"

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("utime not permitted")

    monkeypatch.setattr(manifest_archive.os, "utime", _boom)

    with caplog.at_level("WARNING", logger="vault_api.manifest_archive"):
        dest = archive_manifest(str(archive_dir), depotid=441, manifestid="123", src_path=str(src))

    assert Path(dest).read_bytes() == b"payload"
    assert sorted(os.listdir(archive_dir)) == ["441_123.bin"]
    assert "could not stamp mtime" in caplog.text
