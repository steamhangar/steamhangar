"""WP API-FIX-3: a failed SteamPrefill run is not "app not considered".

SteamPrefill exits 0 even when it fails an app; its summary table then grows
a Failed column. Before this package vault-api read only the first two
integers of that table, so a failed run (0 | 0 | 1) took the 0/0 branch and
the job log claimed the app was probably not owned. These tests pin:

- the parser reads the Failed column, tolerates its absence (success), and
  treats an unreadable Failed column as a failure (``reports_failure``);
- the job ends 'error' with the distinct reason ``prefill_failed`` in
  vault-api's LAST log line (the shape web/js/lib/job-failure.js reads), with
  the narrow cause hints and without the false "is it owned" claim;
- the depot mapping, manifest state, ``needs_force`` and ``last_prefill_at``
  stay untouched for a failed run, even with planted on-disk evidence;
- the genuine "not considered" case keeps its wording;
- queue mode reaches the same outcome, and the runner's log line says it
  reports the process outcome, not the job's final state.

Fixtures A and B are the production job outputs from 2026-10-02 as shown in
the web UI (vault-api's own ``[vault-api]`` lines removed, since those are
what this package changes). Case B's border row was not in the report and is
filled in from case A's; the rest is verbatim. The success, not-considered
and malformed tables are synthetic, modeled on the same structure.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.test_worker import AUTH, enqueue, make_settings, wait_for_job
from vault_api import jobs, prefill
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.prefill_runner import PrefillRunner
from vault_api.prefill_summary import parse_summary, reports_failure
from vault_api.worker import PrefillWorker

# -- fixtures ------------------------------------------------------------------

CASE_A_CACHE_UNREACHABLE = (
    "[10:11:30 AM] Download failed! 22213 requests failed unexpectedly, see app.log for more details.\n"
    "[10:11:30 AM] Prefill complete!\n"
    "  Prefilled 1 apps totaling 937.52 MiB in 02:14.64\n"
    "   Updated │ Up To Date │ Failed\n"
    "  ━━━━━━━━━┿━━━━━━━━━━━━┿━━━━━━━━\n"
    "      0    │     0      │   1\n"
)

CASE_B_MANIFESTS = (
    "[8:07:55 PM] Unexpected download error : Unable to download manifests!  Skipping app...\n"
    "  Prefilled 1 apps totaling 0 b in 07.6439\n"
    "   Updated │ Up To Date │ Failed\n"
    "  ━━━━━━━━━┿━━━━━━━━━━━━┿━━━━━━━━\n"
    "      0    │     0      │   1\n"
)

SUCCESS_NO_FAILED_COLUMN = (
    "[10:33:48 PM] Finished downloading 75.97 MiB in 13.4216 - 47.48 Mbit/s\n"
    "[10:33:48 PM] Prefill complete!\n"
    "  Prefilled 1 apps totaling 75.97 MiB in 16.5553\n"
    "   Updated │ Up To Date\n"
    "  ━━━━━━━━━┿━━━━━━━━━━━━\n"
    "      1    │     0\n"
)

NOT_CONSIDERED = (
    "[10:20:54 PM] Prefill complete!\n"
    "  Prefilled 0 apps totaling 0 b in 03.2491\n"
    "   Updated │ Up To Date\n"
    "  ━━━━━━━━━┿━━━━━━━━━━━━\n"
    "      0    │     0\n"
)

#: A Failed column that says 0 is not a failure: with 0/0 it is the
#: ordinary "not considered" shape.
NOT_CONSIDERED_EXPLICIT_FAILED_ZERO = (
    "  Prefilled 0 apps totaling 0 b in 03.2491\n"
    "   Updated │ Up To Date │ Failed\n"
    "  ━━━━━━━━━┿━━━━━━━━━━━━┿━━━━━━━━\n"
    "      0    │     0      │   0\n"
)

#: The real SteamPrefill v3.7.1 not-considered shape (PrefillSummaryResult.cs,
#: lancache-prefill-common 93d102a, read in review): an unowned app adds an
#: Unowned column, and with no Failed column it stays "not considered".
NOT_CONSIDERED_UNOWNED_COLUMN = (
    "  Prefilled 0 apps totaling 0 b in 03.2491\n"
    "   Updated │ Up To Date │ Unowned\n"
    "  ━━━━━━━━━┿━━━━━━━━━━━━┿━━━━━━━━━\n"
    "      0    │     0      │    1\n"
)

#: Failed and Unowned together; Failed is always rendered before Unowned.
FAILED_AND_UNOWNED = (
    "  Prefilled 2 apps totaling 0 b in 07.6439\n"
    "   Updated │ Up To Date │ Failed │ Unowned\n"
    "  ━━━━━━━━━┿━━━━━━━━━━━━┿━━━━━━━━┿━━━━━━━━━\n"
    "      0    │     0      │   1    │    2\n"
)

#: An unstripped SGR remnant glued to the header word.
FAILED_HEADER_WITH_SGR = (
    "  Prefilled 1 apps totaling 0 b in 07.6439\n"
    "   Updated │ Up To Date │ \x1b[31mFailed\x1b[0m\n"
    "  ━━━━━━━━━┿━━━━━━━━━━━━┿━━━━━━━━\n"
    "      0    │     0      │   1\n"
)

#: The output was cut off inside the data row: the Failed count is missing.
MALFORMED_TRUNCATED_ROW = (
    "[10:11:30 AM] Prefill complete!\n"
    "  Prefilled 1 apps totaling 937.52 MiB in 02:14.64\n"
    "   Updated │ Up To Date │ Failed\n"
    "  ━━━━━━━━━┿━━━━━━━━━━━━┿━━━━━━━━\n"
    "      0    │     0      │\n"
)

#: Header with a Failed column and nothing after it.
MALFORMED_HEADER_ONLY = (
    "  Prefilled 1 apps totaling 0 b in 07.6439\n"
    "   Updated │ Up To Date │ Failed\n"
)

#: The web's reason regex (web/js/lib/job-failure.js REASON_LINE), restated
#: as a literal so a change on either side shows up here.
WEB_REASON_LINE = re.compile(r"^\[vault-api\] Prefill failed \(reason=([a-z_]+)\)")

EXPECTED_LAST_LINE_A = (
    "[vault-api] Prefill failed (reason=prefill_failed): SteamPrefill reported "
    "1 app(s) as failed: 22213 download requests failed unexpectedly, so the "
    "cache or the upstream it fetches from was unreachable. The depot mapping "
    "and manifest state for this app were left unchanged."
)

EXPECTED_LAST_LINE_B = (
    "[vault-api] Prefill failed (reason=prefill_failed): SteamPrefill reported "
    "1 app(s) as failed: it could not download the depot manifests; a possible "
    "cause is HTTPS to *.steamcontent.com being rewritten to a cache that does "
    "not pass port 443 through. The depot mapping and manifest state for this "
    "app were left unchanged."
)

EXPECTED_LAST_LINE_MALFORMED = (
    "[vault-api] Prefill failed (reason=prefill_failed): SteamPrefill's summary "
    "table has a Failed column, but its count could not be read: see "
    "SteamPrefill's output above for the cause. The depot mapping and manifest "
    "state for this app were left unchanged."
)

OWNED_CLAIM = "is it owned by the logged-in account"


def _last_line(excerpt: str) -> str:
    return [line for line in excerpt.splitlines() if line.strip()][-1]


# -- parser --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "total"),
    [(CASE_A_CACHE_UNREACHABLE, "937.52 MiB"), (CASE_B_MANIFESTS, "0 b")],
)
def test_failed_table_parses_all_three_columns(text: str, total: str) -> None:
    summary = parse_summary(text)
    assert summary.parse_ok is True
    assert (summary.updated, summary.up_to_date, summary.failed) == (0, 0, 1)
    assert summary.failed_column is True
    assert summary.total_bytes_text == total
    assert reports_failure(summary) is True


def test_success_without_failed_column_is_not_a_failure() -> None:
    summary = parse_summary(SUCCESS_NO_FAILED_COLUMN)
    assert summary.parse_ok is True
    assert (summary.updated, summary.up_to_date, summary.failed) == (1, 0, 0)
    assert summary.failed_column is False
    assert reports_failure(summary) is False


@pytest.mark.parametrize(
    "text", [NOT_CONSIDERED, NOT_CONSIDERED_EXPLICIT_FAILED_ZERO, NOT_CONSIDERED_UNOWNED_COLUMN]
)
def test_not_considered_shapes_are_not_failures(text: str) -> None:
    summary = parse_summary(text)
    assert summary.parse_ok is True
    assert (summary.updated, summary.up_to_date, summary.failed) == (0, 0, 0)
    assert reports_failure(summary) is False


def test_failed_then_unowned_columns_read_failed_as_the_third_integer() -> None:
    """At least three integers, Failed is the third (v3.7.1 renders Failed
    before Unowned): a clean count, still a failure."""
    summary = parse_summary(FAILED_AND_UNOWNED)
    assert summary.parse_ok is True
    assert (summary.updated, summary.up_to_date, summary.failed) == (0, 0, 1)
    assert reports_failure(summary) is True


def test_sgr_remnant_glued_to_failed_header_still_counts() -> None:
    """No leading word boundary: "\\x1b[31mFailed" must not fall back to
    the 0/0 "is it owned" branch."""
    summary = parse_summary(FAILED_HEADER_WITH_SGR)
    assert summary.failed_column is True
    assert (summary.parse_ok, summary.failed) == (True, 1)
    assert reports_failure(summary) is True


def test_a_longer_header_word_is_not_the_failed_column() -> None:
    """Trailing word boundary: "FailedApps" is not "Failed"."""
    text = "   Updated │ Up To Date │ FailedApps\n  ━━━━━━━━━┿━━━━━━━━━━━━┿━━━━━━━━\n      0    │     0      │   1\n"
    summary = parse_summary(text)
    assert summary.failed_column is False
    assert (summary.updated, summary.up_to_date, summary.failed) == (0, 0, 0)
    assert reports_failure(summary) is False


@pytest.mark.parametrize("text", [MALFORMED_TRUNCATED_ROW, MALFORMED_HEADER_ONLY])
def test_unreadable_failed_column_fails_closed(text: str) -> None:
    """Never a guess: counts stay None (parse_ok False), but a Failed column
    is only rendered when something failed, so the run counts as failed."""
    summary = parse_summary(text)
    assert summary.parse_ok is False
    assert (summary.updated, summary.up_to_date, summary.failed) == (None, None, None)
    assert summary.failed_column is True
    assert reports_failure(summary) is True


def test_a_three_int_row_without_failed_header_keeps_the_two_column_rule() -> None:
    """No Failed header -> the third integer is not read as a count."""
    text = "   Updated | Up To Date\n  ---------+------------\n      0    |     0    | 7\n"
    summary = parse_summary(text)
    assert summary.failed_column is False
    assert summary.failed == 0
    assert reports_failure(summary) is False


def test_mojibake_failed_table_still_parses() -> None:
    """The OEM-codepage corruption the parser already tolerates (WP 3.3)."""
    text = (
        "  Prefilled 1 apps totaling 0 b in 07.6439 \n"
        "   Updated ï¿½ Up To Date ï¿½ Failed \n"
        "  ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ \n"
        "      0    ï¿½     0      ï¿½   2 \n"
    )
    summary = parse_summary(text)
    assert (summary.parse_ok, summary.failed) == (True, 2)
    assert reports_failure(summary) is True


# -- detail text ---------------------------------------------------------------


def test_detail_without_a_known_phrase_points_at_the_output() -> None:
    assert prefill.prefill_failed_detail(1, "something else went wrong") == (
        "SteamPrefill reported 1 app(s) as failed: see SteamPrefill's output "
        "above for the cause"
    )


def test_detail_hints_are_exact_phrase_matches() -> None:
    """Narrow and cosmetic: wording that differs only in case gives no cause
    hint (both lines would match a case-insensitive search)."""
    for near_miss in ("12 Requests Failed Unexpectedly", "unable to download manifests!"):
        assert prefill.prefill_failed_detail(1, near_miss) == (
            "SteamPrefill reported 1 app(s) as failed: see SteamPrefill's "
            "output above for the cause"
        ), near_miss


def test_detail_names_both_causes_when_both_phrases_appear() -> None:
    detail = prefill.prefill_failed_detail(2, CASE_A_CACHE_UNREACHABLE + CASE_B_MANIFESTS)
    assert detail.startswith("SteamPrefill reported 2 app(s) as failed: 22213 download requests")
    assert "; and it could not download the depot manifests" in detail


# -- end to end (subprocess mode, real stub pipe) ----------------------------


def _run_job(
    tmp_path: Path, summary_text: str, *, plant_manifest: bool = False
) -> tuple[dict, dict, list[int], object, int]:
    """Run one prefill of app 440 whose stub writes depot 441 to disk (and
    optionally a manifest .bin), printing ``summary_text``. Returns the job,
    the game detail, the mapped depots for 440, the depot_manifests row and
    apps.needs_force."""
    from tests.test_manifests import _bin_manifest_bytes, _chunk_id
    from vault_api.depot_manifests import get_depot_manifest

    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    steamprefill_cache_dir = tmp_path / "steamprefill-cache"
    manifest_bins = None
    if plant_manifest:
        manifest_bins = [
            {
                "dir": str(steamprefill_cache_dir),
                "filename": "440_440_441_555.bin",
                "data": _bin_manifest_bytes(
                    depot_id=441, manifest_id=555, files=[[(_chunk_id(1), 1000)]]
                ),
            }
        ]
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        manifest_bins=manifest_bins,
        summary_text=summary_text,
    )
    settings = make_settings(
        tmp_path, cache_root, executable,
        steamprefill_cache_dir=str(steamprefill_cache_dir),
    )
    with TestClient(create_app(settings)) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)
        game = client.get("/v1/games/440", headers=AUTH).json()
        mapped = [
            int(e["depotid"]) for e in client.get("/v1/mapping", headers=AUTH).json()
            if int(e["appid"]) == 440
        ]
    assert (cache_root / "depot" / "441" / "chunk").is_dir(), "planted evidence missing"
    conn = get_connection(settings.db_path)
    try:
        manifest_row = get_depot_manifest(conn, appid=440, depotid=441)
        needs_force = int(
            conn.execute("SELECT needs_force FROM apps WHERE appid = 440").fetchone()[0]
        )
    finally:
        conn.close()
    return job, game, mapped, manifest_row, needs_force


def _assert_prefill_failed(job: dict, game: dict, mapped: list[int], needs_force: int) -> None:
    excerpt = job["log_excerpt"]
    assert job["status"] == "error", excerpt
    match = WEB_REASON_LINE.match(_last_line(excerpt))
    assert match is not None and match.group(1) == "prefill_failed", _last_line(excerpt)
    assert OWNED_CLAIM not in excerpt
    assert "Depot mapping updated" not in excerpt
    assert "Manifest ingestion" not in excerpt
    assert mapped == []
    assert game["status"] == "error"
    assert game["last_prefill_at"] is None
    assert game["last_manifest_check"] is None
    assert needs_force == 1  # the success path would have cleared it


def test_case_a_cache_unreachable_ends_prefill_failed(tmp_path: Path) -> None:
    job, game, mapped, manifest_row, needs_force = _run_job(
        tmp_path, CASE_A_CACHE_UNREACHABLE, plant_manifest=True
    )
    _assert_prefill_failed(job, game, mapped, needs_force)
    assert _last_line(job["log_excerpt"]) == EXPECTED_LAST_LINE_A
    assert (
        "[vault-api] Prefill summary: updated=0 up_to_date=0 failed=1 "
        "(totaling 937.52 MiB)"
    ) in job["log_excerpt"]
    # Same counters on the job row as before this reason existed.
    assert (job["updated"], job["up_to_date"], job["summary_parse_ok"]) == (0, 0, True)
    assert manifest_row is None


def test_case_b_manifests_ends_prefill_failed(tmp_path: Path) -> None:
    job, game, mapped, _manifest_row, needs_force = _run_job(tmp_path, CASE_B_MANIFESTS)
    _assert_prefill_failed(job, game, mapped, needs_force)
    assert _last_line(job["log_excerpt"]) == EXPECTED_LAST_LINE_B


def test_malformed_failed_table_ends_prefill_failed(tmp_path: Path) -> None:
    job, game, mapped, _manifest_row, needs_force = _run_job(tmp_path, MALFORMED_TRUNCATED_ROW)
    _assert_prefill_failed(job, game, mapped, needs_force)
    assert _last_line(job["log_excerpt"]) == EXPECTED_LAST_LINE_MALFORMED
    assert (job["updated"], job["up_to_date"], job["summary_parse_ok"]) == (None, None, False)
    # Not the exit-code-rule fallback: that note would contradict the outcome.
    assert "exit-code rule" not in job["log_excerpt"]


def test_success_without_failed_column_is_done_and_maps(tmp_path: Path) -> None:
    job, game, mapped, _manifest_row, needs_force = _run_job(tmp_path, SUCCESS_NO_FAILED_COLUMN)
    excerpt = job["log_excerpt"]
    assert job["status"] == "done", excerpt
    assert "Prefill failed" not in excerpt
    assert "[vault-api] Prefill summary: updated=1 up_to_date=0 (totaling 75.97 MiB)" in excerpt
    assert mapped == [441]
    assert game["status"] == "done"
    assert needs_force == 0


@pytest.mark.parametrize(
    "text", [NOT_CONSIDERED, NOT_CONSIDERED_EXPLICIT_FAILED_ZERO, NOT_CONSIDERED_UNOWNED_COLUMN]
)
def test_genuine_not_considered_keeps_its_wording(tmp_path: Path, text: str) -> None:
    job, game, mapped, _manifest_row, needs_force = _run_job(tmp_path, text)
    excerpt = job["log_excerpt"]
    assert job["status"] == "error"
    assert _last_line(excerpt) == (
        "[vault-api] SteamPrefill did not consider this app - is it owned by the "
        "logged-in account? Depot mapping and manifest state were NOT touched."
    )
    assert "reason=prefill_failed" not in excerpt
    assert mapped == []
    assert game["status"] == "error"
    assert needs_force == 1


# -- queue mode: same outcome, honest runner line ----------------------------


def test_queue_mode_case_b_ends_prefill_failed_and_runner_line_says_process_outcome(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from tests.test_prefill_runner_process import _queue_settings, _wait_for_job

    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]},
        summary_text=CASE_B_MANIFESTS,
    )
    settings = _queue_settings(tmp_path, steamprefill_path=executable, cache_root=str(cache_root))
    init_db(settings.db_path)

    worker = PrefillWorker(settings)
    runner = PrefillRunner(settings, runner_id="test-runner-fix3")
    caplog.set_level("INFO", logger="vault_api.prefill_runner")
    worker.start()
    runner_thread = threading.Thread(target=runner.run_forever, daemon=True)
    runner_thread.start()
    try:
        conn = get_connection(settings.db_path)
        try:
            job, _created = jobs.enqueue_prefill(conn, 440)
            job_id = int(job["id"])
        finally:
            conn.close()
        finished = _wait_for_job(settings.db_path, job_id)
    finally:
        worker.stop(timeout=5)
        runner.stop()
        runner_thread.join(timeout=5)

    assert finished["status"] == jobs.STATUS_ERROR
    assert _last_line(str(finished["log_excerpt"])) == EXPECTED_LAST_LINE_B
    conn = get_connection(settings.db_path)
    try:
        mapped = conn.execute("SELECT depotid FROM depot_app_map WHERE appid = 440").fetchall()
    finally:
        conn.close()
    assert mapped == []

    runner_lines = [
        r.getMessage() for r in caplog.records
        if r.name == "vault_api.prefill_runner" and f"job {job_id} " in r.getMessage()
    ]
    assert any(
        "SteamPrefill run finished, run_success=True failure_reason=None exit_code=0" in line
        and "vault-api, which sets the job's final state" in line
        for line in runner_lines
    ), runner_lines
    assert not any("finished, success=" in line for line in runner_lines)
