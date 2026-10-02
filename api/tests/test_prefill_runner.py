"""The SteamPrefill runner and depot attribution, without the worker/HTTP layers."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests import stub_prefill
from vault_api import prefill
from vault_api.db import get_connection, init_db
from vault_api.mapping import upsert_mapping


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    yield connection
    connection.close()


# -- configuration failures are per-job, never fatal -----------------------


def test_missing_path_is_a_clear_job_failure_not_an_exception() -> None:
    result = prefill.run_prefill(440, "", timeout_seconds=5)

    assert result.success is False
    assert result.failure_reason == "setup"
    assert "VAULT_STEAMPREFILL_PATH is not set" in result.output


def test_nonexistent_path_is_a_clear_job_failure(tmp_path: Path) -> None:
    bogus = str(tmp_path / "nope" / "SteamPrefill.exe")
    result = prefill.run_prefill(440, bogus, timeout_seconds=5)

    assert result.success is False
    assert result.failure_reason == "setup"
    assert "not an existing file" in result.output


# -- the verified CLI contract --------------------------------------------


def test_runner_selects_the_appid_via_the_state_file_and_uses_the_verified_argv(
    tmp_path: Path,
) -> None:
    """Pins the empirically verified invocation (see vault_api/prefill.py).

    v3.7.1 has no --app-ids option and no positional app id, so the only
    non-interactive selection mechanism is Config/selectedAppsToPrefill.json.
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )

    result = prefill.run_prefill(440, executable, timeout_seconds=30)

    assert result.success is True, result.output
    assert result.exit_code == 0
    assert stub_prefill.read_selection(bindir) == [440]
    # use_force defaults to True (the pre-WP-3.4 behavior every direct caller
    # relied on) when the argument isn't passed at all.
    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi", "--max-threads", "8"]
    # The stub echoes what it read back, proving the selection reached it.
    assert "selected=[440]" in result.output


# -- use_force (WP 3.4, ADR-0006 decision 2) --------------------------------


def test_use_force_true_includes_the_force_flag(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"))

    result = prefill.run_prefill(440, executable, timeout_seconds=30, use_force=True)

    assert result.success is True, result.output
    assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi", "--max-threads", "8"]


def test_use_force_false_omits_the_force_flag(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"))

    result = prefill.run_prefill(440, executable, timeout_seconds=30, use_force=False)

    assert result.success is True, result.output
    assert stub_prefill.read_argv(bindir) == ["prefill", "--no-ansi", "--max-threads", "8"]


def test_subprocess_gets_a_closed_stdin(tmp_path: Path) -> None:
    # Without this, SteamPrefill's interactive credential prompt would block
    # the worker forever instead of failing fast.
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"))

    result = prefill.run_prefill(440, executable, timeout_seconds=30)

    assert "stdin=eof" in result.output


def test_selection_file_is_rewritten_for_every_job(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"))

    prefill.run_prefill(440, executable, timeout_seconds=30)
    assert stub_prefill.read_selection(bindir) == [440]

    prefill.run_prefill(730, executable, timeout_seconds=30)
    assert stub_prefill.read_selection(bindir) == [730]


def test_write_selected_apps_is_atomic_and_leaves_no_tmp_file_behind(
    tmp_path: Path,
) -> None:
    """WP 1.5 carry-over fix: written via tempfile + os.replace, not a
    truncating open() — a reader must never observe a half-written file, and
    the tempfile must not linger afterwards."""
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"))

    error = prefill.write_selected_apps(executable, 12345)

    assert error is None
    config_dir = Path(executable).parent / "Config"
    assert [p.name for p in config_dir.iterdir()] == ["selectedAppsToPrefill.json"]
    assert stub_prefill.read_selection(bindir) == [12345]


def test_write_selected_apps_overwrites_atomically_on_repeated_calls(
    tmp_path: Path,
) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, cache_root=str(tmp_path / "cache"))

    prefill.write_selected_apps(executable, 111)
    prefill.write_selected_apps(executable, 222)

    config_dir = Path(executable).parent / "Config"
    assert [p.name for p in config_dir.iterdir()] == ["selectedAppsToPrefill.json"]
    assert stub_prefill.read_selection(bindir) == [222]


# -- failure modes ---------------------------------------------------------


def test_nonzero_exit_is_reported_with_the_captured_output(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="fail", exit_code=5)

    result = prefill.run_prefill(440, executable, timeout_seconds=30)

    assert result.success is False
    assert result.failure_reason == "exit_code"
    assert result.exit_code == 5
    # stderr is merged into the captured stream, so it shows up in the excerpt.
    assert "simulated depot download failure" in result.output
    assert "exited with code 5" in result.output


def test_not_logged_in_output_is_detected_and_gets_an_actionable_hint(
    tmp_path: Path,
) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="not_logged_in")

    result = prefill.run_prefill(440, executable, timeout_seconds=30)

    assert result.success is False
    assert result.failure_reason == "not_logged_in"
    assert "select-apps" in result.output
    assert "never sees or stores Steam credentials" in result.output


def test_a_hanging_subprocess_hits_the_timeout_and_is_killed(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="hang")

    started = time.monotonic()
    result = prefill.run_prefill(440, executable, timeout_seconds=1)
    elapsed = time.monotonic() - started

    assert result.success is False
    assert result.failure_reason == "timeout"
    assert "exceeded the 1s time budget" in result.output
    # It must actually return promptly, not wait out the stub's own bound.
    assert elapsed < 9, elapsed


def test_should_abort_stops_the_run(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="hang")

    result = prefill.run_prefill(
        440, executable, timeout_seconds=60, should_abort=lambda: True
    )

    assert result.success is False
    assert result.failure_reason == "aborted"
    assert "shutting down" in result.output


def test_output_is_ansi_stripped() -> None:
    assert prefill.strip_ansi("\x1b[38;5;9mred\x1b[0m text") == "red text"


# -- decode fix (WP 3.3) -----------------------------------------------------
#
# The bug: SteamPrefill's Spectre.Console summary table writes box-drawing
# glyphs in the OS's OEM codepage when its console output is redirected to a
# file, NOT UTF-8. This project's own dev machine already established which
# codepage that is (850, on a German Windows install — see
# api/README.md's agent-report note and api/tests/conftest.py's mklink
# capture comment); a differently-localized host has a different one (437 on
# US Windows), so the fix asks the OS (GetOEMCP), rather than hardcoding one.
# The previous code always decoded as UTF-8 with errors="replace", which
# fails on nearly every OEM-codepage byte and silently replaced each with
# U+FFFD — exactly the corruption captured for real in
# core/tests/mvp/RESULTS-20260805-222046.md / RESULTS-20260805-223328.md.


def test_windows_oem_encoding_matches_the_actual_os_codepage() -> None:
    """Not hardcoded to this project's own dev machine's codepage (850) —
    checked against the OS's own answer, so the test stays meaningful on a
    differently-localized Windows host too."""
    if os.name != "nt":
        pytest.skip("OEM codepage detection is a Windows-only concept")
    import ctypes

    expected = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    assert prefill._windows_oem_encoding() == expected


def test_windows_oem_encoding_is_utf8_off_windows() -> None:
    if os.name == "nt":
        pytest.skip("this branch only runs off Windows")
    assert prefill._windows_oem_encoding() == "utf-8"


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows OEM-codepage fallback")
def test_read_text_recovers_box_drawing_glyphs_from_oem_codepage_bytes(
    tmp_path: Path,
) -> None:
    """The actual regression test: bytes that are NOT valid UTF-8 (real
    OEM-codepage box-drawing glyphs) must decode to the real glyphs, not
    U+FFFD."""
    original = (
        "   Updated │ Up To Date\n"
        "  ─────────┼─────────────────────\n"
        "      3    │     4\n"
    )
    encoding = prefill._windows_oem_encoding()
    raw_bytes = original.encode(encoding)
    # Prerequisite check: prove these bytes really are not valid UTF-8. If
    # they were, this test would pass without ever exercising the fallback,
    # and silently stop meaning anything.
    with pytest.raises(UnicodeDecodeError):
        raw_bytes.decode("utf-8")

    log_path = tmp_path / "captured.log"
    log_path.write_bytes(raw_bytes)

    recovered = prefill._read_text(str(log_path))

    assert recovered == original
    assert "�" not in recovered


def test_read_text_strict_utf8_is_tried_first(tmp_path: Path) -> None:
    """Valid UTF-8 bytes (plain ASCII login lines, or a Linux-container
    SteamPrefill run that really does write UTF-8) decode as-is, no fallback
    involved."""
    original = "plain ASCII login lines\nand proper UTF-8 box glyphs: ─│┼\n"
    log_path = tmp_path / "captured.log"
    log_path.write_bytes(original.encode("utf-8"))

    recovered = prefill._read_text(str(log_path))

    assert recovered == original


def test_read_text_reports_an_oserror_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.log"
    result = prefill._read_text(str(missing))
    assert "Could not read captured output" in result


def test_read_text_never_raises_on_invalid_utf8_when_oem_encoding_is_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review blocker: on POSIX, `_windows_oem_encoding()` returns "utf-8",
    so the fallback used to be the exact same strict decode attempted again
    -- raising the exact same UnicodeDecodeError a second time, uncaught.
    Real-world trigger: run_prefill's timeout/abort path terminates the
    subprocess mid-write, which can truncate a multi-byte UTF-8 sequence at
    the end of the captured bytes -- an otherwise-successful run must not be
    reported as a crashed job just because _read_text couldn't decode its
    own tail byte-for-byte. Patches _windows_oem_encoding to "utf-8"
    directly so this reproduces on Windows too, not only on Linux CI.
    """
    monkeypatch.setattr(prefill, "_windows_oem_encoding", lambda: "utf-8")

    # A valid line followed by a truncated 3-byte UTF-8 sequence (only the
    # first 2 of 3 bytes present) -- exactly what terminate()/kill() mid-write
    # can leave behind.
    raw_bytes = "Prefill complete!\n".encode("utf-8") + "─".encode("utf-8")[:2]

    log_path = tmp_path / "captured.log"
    log_path.write_bytes(raw_bytes)

    recovered = prefill._read_text(str(log_path))  # must not raise

    assert "Prefill complete!" in recovered


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows OEM-codepage fallback")
def test_run_prefill_recovers_oem_codepage_summary_table_end_to_end(
    tmp_path: Path,
) -> None:
    """Full pipeline through a real subprocess: the stub writes raw
    OEM-codepage bytes for the summary table (as the real SteamPrefill.exe
    does), and run_prefill's captured output must recover it well enough for
    prefill_summary.parse_summary to read the counters back out."""
    from vault_api.prefill_summary import parse_summary

    bindir = tmp_path / "bin"
    table = (
        "   Updated │ Up To Date\n"
        "  ─────────┼─────────────────────\n"
        "      2    │     5\n"
    )
    encoding = prefill._windows_oem_encoding()
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(tmp_path / "cache"), summary_bytes=table.encode(encoding),
    )

    result = prefill.run_prefill(440, executable, timeout_seconds=30)

    assert result.success is True, result.output
    assert "�" not in result.output

    summary = parse_summary(result.output)
    assert summary.parse_ok is True
    assert summary.updated == 2
    assert summary.up_to_date == 5


def test_large_output_is_captured_without_deadlocking(tmp_path: Path) -> None:
    # Output goes to a temp file rather than a pipe precisely so a chatty run
    # can't fill an OS pipe buffer while the runner is polling for a timeout.
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="chatty", cache_root=str(tmp_path / "c"))

    result = prefill.run_prefill(440, executable, timeout_seconds=60)

    assert result.success is True
    assert len(result.output) > 16000
    assert "Prefill complete!" in result.output


# -- depot scanning / diffing ---------------------------------------------


def _write_chunk(cache_root: Path, depotid: int, name: str, payload: bytes = b"x" * 64) -> None:
    chunk_dir = cache_root / "depot" / str(depotid) / "chunk"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / name).write_bytes(payload)


def test_scan_depots_on_a_missing_cache_root_is_empty(tmp_path: Path) -> None:
    assert prefill.scan_depots(str(tmp_path / "does-not-exist")) == {}


def test_scan_depots_ignores_non_numeric_and_empty_depot_dirs(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write_chunk(cache_root, 441, "a")
    (cache_root / "depot" / "manifest").mkdir(parents=True, exist_ok=True)
    (cache_root / "depot" / "999").mkdir(parents=True, exist_ok=True)  # empty

    signatures = prefill.scan_depots(str(cache_root))

    assert set(signatures) == {441}


def test_diff_depots_reports_new_and_changed_only(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write_chunk(cache_root, 441, "a")
    _write_chunk(cache_root, 500, "a")
    before = prefill.scan_depots(str(cache_root))

    _write_chunk(cache_root, 441, "b")   # changed (new file)
    _write_chunk(cache_root, 442, "a")   # new depot

    after = prefill.scan_depots(str(cache_root))

    assert prefill.diff_depots(before, after) == {441, 442}


def test_diff_depots_ignores_removed_depots(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _write_chunk(cache_root, 441, "a")
    before = prefill.scan_depots(str(cache_root))
    os.remove(cache_root / "depot" / "441" / "chunk" / "a")
    after = prefill.scan_depots(str(cache_root))

    assert prefill.diff_depots(before, after) == set()


# -- mapping replace-semantics (ADR-0003 decision 3) ----------------------


def test_apply_observed_mapping_replaces_within_the_app(conn) -> None:
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")
    upsert_mapping(conn, depotid=999, appid=440, name=None)  # will go stale

    change = prefill.apply_observed_mapping(conn, 440, {441, 442})

    depots = {
        row["depotid"]
        for row in conn.execute("SELECT depotid FROM depot_app_map WHERE appid = 440")
    }
    assert depots == {441, 442}
    assert change.removed == {999}
    assert change.added == {442}
    assert change.kept == {441}
    assert change.skipped_empty is False


def test_apply_observed_mapping_keeps_shared_depots_mapped_to_other_apps(conn) -> None:
    # Depot 300 is a shared/redistributable depot used by 440 and 730.
    upsert_mapping(conn, depotid=300, appid=440, name=None)
    upsert_mapping(conn, depotid=300, appid=730, name=None)
    upsert_mapping(conn, depotid=999, appid=440, name=None)

    prefill.apply_observed_mapping(conn, 440, {300, 301})

    pairs = {
        (row["depotid"], row["appid"])
        for row in conn.execute("SELECT depotid, appid FROM depot_app_map")
    }
    # Replace within 440 (999 gone, 301 added), additive across apps (730 kept).
    assert pairs == {(300, 440), (300, 730), (301, 440)}


def test_apply_observed_mapping_leaves_everything_alone_when_nothing_observed(conn) -> None:
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")

    change = prefill.apply_observed_mapping(conn, 440, set())

    assert change.skipped_empty is True
    assert "left unchanged" in change.summary()
    depots = {
        row["depotid"]
        for row in conn.execute("SELECT depotid FROM depot_app_map WHERE appid = 440")
    }
    assert depots == {441}


def test_apply_observed_mapping_creates_the_app_row_for_a_new_title(conn) -> None:
    prefill.apply_observed_mapping(conn, 12345, {1, 2})

    row = conn.execute("SELECT appid FROM apps WHERE appid = 12345").fetchone()
    assert row is not None


# -- WP API-FIX-1 (P2/P4): wait-loop exceptions and the abort wording --------


def _spy_stop_process(monkeypatch) -> list:
    """Wrap the real ``_stop_process`` so a test can see WHICH child it was
    asked to stop, then check that child's ``poll()`` afterwards."""
    stopped: list = []
    real = prefill._stop_process

    def spy(process):
        stopped.append(process)
        return real(process)

    monkeypatch.setattr(prefill, "_stop_process", spy)
    return stopped


def test_a_raising_stop_request_still_terminates_the_child(tmp_path: Path, monkeypatch) -> None:
    """WP API-FIX-1 (P2): the queue-mode ``stop_request`` callback talks to
    SQLite; before the fix an exception out of it unwound the wait loop
    WITHOUT ``_stop_process``, leaving SteamPrefill running unreaped. The
    exception must still propagate (it is not a prefill outcome), but the
    hanging child must be dead by the time it does."""
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="hang")
    stopped = _spy_stop_process(monkeypatch)

    def broken_stop_request() -> str | None:
        raise RuntimeError("database is locked (simulated)")

    with pytest.raises(RuntimeError, match="simulated"):
        prefill.run_prefill(
            440, executable, timeout_seconds=60, stop_request=broken_stop_request
        )

    assert len(stopped) == 1, "the child was not stopped on the way out"
    assert stopped[0].poll() is not None, "the child is still running"


def test_a_raising_should_abort_still_terminates_the_child(tmp_path: Path, monkeypatch) -> None:
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="hang")
    stopped = _spy_stop_process(monkeypatch)

    def broken_should_abort() -> bool:
        raise RuntimeError("stop event exploded (simulated)")

    with pytest.raises(RuntimeError, match="simulated"):
        prefill.run_prefill(
            440, executable, timeout_seconds=60, should_abort=broken_should_abort
        )

    assert len(stopped) == 1
    assert stopped[0].poll() is not None


def test_abort_reason_defaults_to_vault_api_wording(tmp_path: Path) -> None:
    """Subprocess mode is byte-identical to before WP API-FIX-1: the caller
    that passes nothing still gets the vault-api sentence."""
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="hang")

    result = prefill.run_prefill(
        440, executable, timeout_seconds=60, should_abort=lambda: True
    )

    assert result.failure_reason == "aborted"
    assert "[vault-api] Aborted: vault-api is shutting down." in result.output


def test_abort_reason_is_recorded_verbatim(tmp_path: Path) -> None:
    """WP API-FIX-1 (P4): the sentence names whose shutdown it was."""
    bindir = tmp_path / "bin"
    executable = stub_prefill.make_stub(bindir, mode="hang")

    result = prefill.run_prefill(
        440, executable, timeout_seconds=60, should_abort=lambda: True,
        abort_reason="the moon is shutting down",
    )

    assert result.failure_reason == "aborted"
    assert "[vault-api] Aborted: the moon is shutting down." in result.output
    assert "vault-api is shutting down" not in result.output
