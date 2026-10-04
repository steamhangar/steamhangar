"""Job control: cancel / pause / resume, the status model, auto-GC (WP 3.12).

Three layers, on purpose:

1. **The status model** (``jobs.py``) — every consumer of ``jobs.status`` that
   the WP-3.12 audit named gets its own pin here, in both directions
   (``paused`` counts as in-flight, ``cancelled`` does not). These are the
   tests that die when ``ACTIVE_STATUSES`` or ``recover_stale_jobs`` is
   widened/narrowed by accident.
2. **The endpoints** (``routers/jobs.py``) — 200/404/409 and what the body
   says.
3. **End to end through the real worker** with the fake SteamPrefill from
   ``tests/stub_prefill.py``: a running prefill really is terminated, a paused
   job really does survive a restart, and a cancelled run really does leave the
   mapping, the manifest state and ``needs_force`` alone.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api import deletion, jobs, prefill
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}

#: A parseable summary that reports one UPDATED app — the only shape auto-GC
#: reacts to (an update is what leaves orphans behind).
TABLE_UPDATED = (
    "  Prefilled 1 apps totaling 12 MiB in 05.0000 \n"
    "   Updated | Up To Date\n"
    "  ---------+------------\n"
    "      1    |     0\n"
)

#: Nothing changed: a routine ADR-0006 staleness confirmation.
TABLE_UP_TO_DATE = (
    "  Prefilled 1 apps totaling 0 b in 02.9012 \n"
    "   Updated | Up To Date\n"
    "  ---------+------------\n"
    "      0    |     1\n"
)

#: Updated==0 AND Up To Date==0 — the WP 1.7 unowned-app trap.
TABLE_UNOWNED = (
    "  Prefilled 0 apps totaling 0 b in 03.2491 \n"
    "   Updated | Up To Date\n"
    "  ---------+------------\n"
    "      0    |     0\n"
)


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "vault.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path: str) -> sqlite3.Connection:
    connection = get_connection(db_path)
    yield connection
    connection.close()


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    return tmp_path / "bin"


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def make_settings(
    tmp_path: Path,
    cache_root: Path,
    steamprefill_path: str = "",
    *,
    auto_gc: str = "off",
    steamprefill_cache_dir: str | None = None,
) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
        steamprefill_path=steamprefill_path,
        prefill_timeout_seconds=60,
        worker_poll_seconds=0.02,
        steamprefill_cache_dir=steamprefill_cache_dir
        or str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=str(tmp_path / "manifest-archive"),
        auto_gc=auto_gc,
    )


def park_running_job(conn: sqlite3.Connection, appid: int = 440) -> dict:
    """Enqueue, claim and then pause one prefill job, DB-level."""
    job, _ = jobs.enqueue_prefill(conn, appid)
    jobs.claim_next_job(conn)
    parked = jobs.park_paused(conn, int(job["id"]), "[stub] partial output")
    assert parked is not None and parked["status"] == jobs.STATUS_PAUSED
    return parked


def cancel_now(conn: sqlite3.Connection, job_id: int) -> jobs.ControlResult:
    result = jobs.cancel_job(conn, job_id)
    assert result.outcome == jobs.CONTROL_IMMEDIATE
    return result


def get_job(client: TestClient, job_id: int) -> dict:
    response = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def wait_for_status(
    client: TestClient, job_id: int, wanted: tuple[str, ...], timeout: float = 30.0
) -> dict:
    deadline = time.monotonic() + timeout
    job: dict = {}
    while time.monotonic() < deadline:
        job = get_job(client, job_id)
        if job["status"] in wanted:
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} never reached {wanted}: {job}")


def enqueue(client: TestClient, appid: int) -> int:
    response = client.post("/v1/prefill", json={"appids": [appid]}, headers=AUTH)
    assert response.status_code == 202, response.text
    return int(response.json()[0]["job_id"])


def app_status(client: TestClient, appid: int) -> str:
    return client.get(f"/v1/games/{appid}", headers=AUTH).json()["status"]


# ==========================================================================
# 1. The status model — the audit, pinned consumer by consumer
# ==========================================================================


def test_paused_is_active_and_cancelled_is_terminal() -> None:
    """The two memberships every other test in this section depends on."""
    assert jobs.STATUS_PAUSED in jobs.ACTIVE_STATUSES
    assert jobs.STATUS_CANCELLED not in jobs.ACTIVE_STATUSES
    assert jobs.STATUS_CANCELLED in jobs.TERMINAL_STATUSES
    assert jobs.STATUS_PAUSED not in jobs.TERMINAL_STATUSES
    # 'cancelled' must be its OWN status, not an alias for a failure.
    assert jobs.STATUS_CANCELLED != jobs.STATUS_ERROR


def test_a_paused_prefill_deduplicates_a_new_prefill_request(conn) -> None:
    """Audit item 2: a rival job would download what the paused one is holding."""
    parked = park_running_job(conn, 440)

    again, created = jobs.enqueue_prefill(conn, 440)

    assert created is False
    assert again["id"] == parked["id"]
    assert again["status"] == jobs.STATUS_PAUSED


def test_a_cancelled_prefill_does_not_block_a_new_one(conn) -> None:
    """The other direction: a terminal status must never wedge the dedupe."""
    job, _ = jobs.enqueue_prefill(conn, 440)
    cancel_now(conn, int(job["id"]))

    fresh, created = jobs.enqueue_prefill(conn, 440)

    assert created is True
    assert fresh["id"] != job["id"]


def test_a_paused_job_also_deduplicates_gc_in_the_same_mode(conn) -> None:
    job, _ = jobs.enqueue_gc(conn, 440, execute=False)
    jobs.claim_next_job(conn)
    jobs.park_paused(conn, int(job["id"]), "partial")

    again, created = jobs.enqueue_gc(conn, 440, execute=False)
    assert created is False and again["id"] == job["id"]

    # ...and still not across modes: that rule is untouched by WP 3.12.
    other, created_other = jobs.enqueue_gc(conn, 440, execute=True)
    assert created_other is True and other["id"] != job["id"]


def test_active_job_for_app_sees_paused_but_not_cancelled(conn) -> None:
    """Audit item 3 — the read DELETE /v1/cache/{appid} refuses on."""
    parked = park_running_job(conn, 440)
    active = jobs.active_job_for_app(conn, 440)
    assert active is not None and active["id"] == parked["id"]

    cancel_now(conn, int(parked["id"]))
    assert jobs.active_job_for_app(conn, 440) is None


def test_claim_next_job_never_claims_a_paused_job(conn) -> None:
    """Audit item 4: a paused job re-enters the queue only through resume."""
    park_running_job(conn, 440)
    assert jobs.claim_next_job(conn) is None


def test_recover_stale_jobs_leaves_paused_jobs_alone(conn) -> None:
    """Audit item 5, and the single most load-bearing line of the model.

    Mutation-pinned: widening ``recover_stale_jobs``'s WHERE clause to include
    ``paused`` eats every paused job on every container restart.
    """
    parked = park_running_job(conn, 440)
    # A genuinely orphaned running job alongside it, so this test proves the
    # function still does its actual work rather than doing nothing at all.
    other, _ = jobs.enqueue_prefill(conn, 730)
    jobs.claim_next_job(conn)

    assert jobs.recover_stale_jobs(conn) == 1

    assert jobs.get_job(conn, int(parked["id"]))["status"] == jobs.STATUS_PAUSED
    assert jobs.get_job(conn, int(other["id"]))["status"] == jobs.STATUS_ERROR


def test_a_paused_job_makes_its_app_count_as_having_cache_content(conn) -> None:
    """Audit item 1, the decisive one (ADR-0003 addendum, fail-closed).

    A paused prefill has written chunks. If its app read as "no content", a
    co-owner's deletion could take a last-remnant shared depot out from under
    the very download the pause exists to preserve.
    """
    conn.execute("INSERT INTO apps (appid, status) VALUES (730, 'idle')")
    conn.commit()
    # idle + never prefilled + no job = the only combination that reads as
    # "uncached" — so this is the state the job status alone has to flip.
    assert deletion.load_co_owner_states(conn, [730]) == {730: False}

    park_running_job(conn, 730)
    assert deletion.load_co_owner_states(conn, [730]) == {730: True}


def test_a_cancelled_job_does_not_keep_an_app_looking_cached(conn) -> None:
    """The other direction of the same rule: terminal means terminal."""
    conn.execute("INSERT INTO apps (appid, status) VALUES (730, 'idle')")
    conn.commit()
    parked = park_running_job(conn, 730)
    assert deletion.load_co_owner_states(conn, [730]) == {730: True}

    cancel_now(conn, int(parked["id"]))

    assert deletion.load_co_owner_states(conn, [730]) == {730: False}


# ==========================================================================
# 2. Cancel / pause / resume at the DB level
# ==========================================================================


def test_cancelling_a_queued_job_finalizes_it_and_it_never_runs(conn) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)

    result = jobs.cancel_job(conn, int(job["id"]))

    assert result.outcome == jobs.CONTROL_IMMEDIATE
    assert result.job["status"] == jobs.STATUS_CANCELLED
    assert result.job["finished_at"] is not None
    assert result.job["started_at"] is None
    # The worker would have claimed it on its next tick — now there is nothing
    # to claim, which is what "never runs" means at this layer.
    assert jobs.claim_next_job(conn) is None


def test_cancelling_a_queued_job_does_not_touch_the_apps_row(conn) -> None:
    """A queued job never set apps.status, so cancelling it must not either —
    otherwise cancelling a queued re-check would grey out a filled game."""
    jobs.set_app_status(conn, 440, jobs.STATUS_DONE, last_prefill_at="2026-08-09T10:00:00Z")
    job, _ = jobs.enqueue_prefill(conn, 440)

    cancel_now(conn, int(job["id"]))

    row = conn.execute("SELECT status, last_prefill_at FROM apps WHERE appid = 440").fetchone()
    assert row["status"] == jobs.STATUS_DONE
    assert row["last_prefill_at"] == "2026-08-09T10:00:00Z"


def test_cancelling_a_paused_job_is_immediate(conn) -> None:
    parked = park_running_job(conn, 440)

    result = jobs.cancel_job(conn, int(parked["id"]))

    assert result.outcome == jobs.CONTROL_IMMEDIATE
    assert result.job["status"] == jobs.STATUS_CANCELLED
    assert result.job["finished_at"] is not None
    assert "the pause had either terminated SteamPrefill" in result.job["log_excerpt"]


def test_cancelling_a_running_job_records_the_request_for_the_worker(conn) -> None:
    jobs.enqueue_prefill(conn, 440)
    claimed = jobs.claim_next_job(conn)

    result = jobs.cancel_job(conn, int(claimed["id"]))

    assert result.outcome == jobs.CONTROL_REQUESTED
    # Still running: only the WORKER may finalize a job it owns a subprocess for.
    assert result.job["status"] == jobs.STATUS_RUNNING
    assert result.job["stop_request"] == jobs.STOP_REQUEST_CANCEL
    assert jobs.read_stop_request(conn, int(claimed["id"])) == "cancel"


@pytest.mark.parametrize(
    "final_status", [jobs.STATUS_DONE, jobs.STATUS_ERROR, jobs.STATUS_CANCELLED]
)
def test_cancelling_a_finished_job_is_a_conflict(conn, final_status: str) -> None:
    """"Do not rewrite history": an outcome is what actually happened."""
    jobs.enqueue_prefill(conn, 440)
    claimed = jobs.claim_next_job(conn)
    jobs.finish_job(conn, int(claimed["id"]), final_status, "done and dusted")

    result = jobs.cancel_job(conn, int(claimed["id"]))

    assert result.outcome == jobs.CONTROL_CONFLICT
    assert final_status in result.detail
    assert jobs.get_job(conn, int(claimed["id"]))["status"] == final_status


def test_cancelling_an_unknown_job_is_unknown(conn) -> None:
    assert jobs.cancel_job(conn, 12345).outcome == jobs.CONTROL_UNKNOWN


def test_pause_is_refused_for_a_gc_job(conn) -> None:
    job, _ = jobs.enqueue_gc(conn, 440, execute=False)
    jobs.claim_next_job(conn)

    result = jobs.request_pause(conn, int(job["id"]))

    assert result.outcome == jobs.CONTROL_CONFLICT
    assert "cannot be paused" in result.detail
    assert jobs.read_stop_request(conn, int(job["id"])) is None


@pytest.mark.parametrize("state", ["paused", "finished", "cancelled"])
def test_pause_is_refused_for_a_paused_or_finished_job(conn, state: str) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)
    job_id = int(job["id"])
    if state == "paused":
        jobs.claim_next_job(conn)
        jobs.park_paused(conn, job_id, "partial")
    elif state == "finished":
        jobs.claim_next_job(conn)
        jobs.finish_job(conn, job_id, jobs.STATUS_DONE, "ok")
    else:
        cancel_now(conn, job_id)
    before = jobs.get_job(conn, job_id)

    result = jobs.request_pause(conn, job_id)

    assert result.outcome == jobs.CONTROL_CONFLICT
    assert "not 'queued' or 'running'" in result.detail
    assert jobs.get_job(conn, job_id) == before, "a refused pause writes nothing"


# --- WP WEB-FEAT-5: pausing a QUEUED prefill job (ADR-0016 addendum) -------


def test_pausing_a_queued_job_parks_it_immediately(conn) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)
    job_id = int(job["id"])

    result = jobs.request_pause(conn, job_id)

    assert result.outcome == jobs.CONTROL_IMMEDIATE
    assert result.detail == jobs.PAUSED_QUEUED_MESSAGE
    row = jobs.get_job(conn, job_id)
    assert row["status"] == jobs.STATUS_PAUSED
    assert row["paused_at"] is not None
    assert row["stop_request"] is None
    # It never started and is not finished.
    assert row["started_at"] is None
    assert row["finished_at"] is None


def test_pausing_a_queued_job_does_not_touch_the_apps_row(conn) -> None:
    """A queued job never set apps.status, so parking it must not either —
    pausing a re-check cannot grey out a game that is already 'done'."""
    jobs.set_app_status(conn, 440, jobs.STATUS_DONE)
    job, _ = jobs.enqueue_prefill(conn, 440)

    jobs.request_pause(conn, int(job["id"]))

    status = conn.execute("SELECT status FROM apps WHERE appid = 440").fetchone()[0]
    assert status == jobs.STATUS_DONE


def test_the_worker_never_claims_a_job_paused_while_queued(conn) -> None:
    first, _ = jobs.enqueue_prefill(conn, 440)
    second, _ = jobs.enqueue_prefill(conn, 730)
    jobs.request_pause(conn, int(first["id"]))

    claimed = jobs.claim_next_job(conn)

    assert claimed is not None and claimed["id"] == second["id"]
    assert jobs.claim_next_job(conn) is None, "the paused job stays parked"
    assert jobs.get_job(conn, int(first["id"]))["status"] == jobs.STATUS_PAUSED


def test_pausing_every_queued_job_then_the_running_one_leaves_nothing_to_claim(
    conn,
) -> None:
    """The "Pause all" order the frontends use: queued first, running last."""
    ids = [int(jobs.enqueue_prefill(conn, appid)[0]["id"]) for appid in (440, 730, 570)]
    running = jobs.claim_next_job(conn)
    assert running["id"] == ids[0]

    for job_id in ids[1:]:
        assert jobs.request_pause(conn, job_id).outcome == jobs.CONTROL_IMMEDIATE
    assert jobs.request_pause(conn, ids[0]).outcome == jobs.CONTROL_REQUESTED
    jobs.park_paused(conn, ids[0], "[stub] partial")

    assert jobs.claim_next_job(conn) is None
    assert [jobs.get_job(conn, i)["status"] for i in ids] == ["paused"] * 3


def test_a_job_paused_while_queued_resumes_with_its_original_id_in_fifo_order(
    conn,
) -> None:
    first, _ = jobs.enqueue_prefill(conn, 440)
    jobs.request_pause(conn, int(first["id"]))
    later, _ = jobs.enqueue_prefill(conn, 730)

    result = jobs.resume_job(conn, int(first["id"]))

    assert result.outcome == jobs.CONTROL_RESUMED
    assert result.job["id"] == first["id"]
    assert jobs.claim_next_job(conn)["id"] == first["id"]
    assert int(later["id"]) > int(first["id"])


def test_a_job_paused_while_queued_deduplicates_a_new_prefill(conn) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)
    jobs.request_pause(conn, int(job["id"]))

    again, created = jobs.enqueue_prefill(conn, 440)

    assert created is False
    assert again["id"] == job["id"]


def test_cancelling_a_job_paused_while_queued_is_immediate_and_honest(conn) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)
    jobs.request_pause(conn, int(job["id"]))

    result = cancel_now(conn, int(job["id"]))

    assert result.job["status"] == jobs.STATUS_CANCELLED
    # The message covers a pause that never terminated anything.
    assert "caught the job before it started" in result.detail


def test_pausing_a_queued_gc_job_is_still_refused(conn) -> None:
    job, _ = jobs.enqueue_gc(conn, 440, execute=False)

    result = jobs.request_pause(conn, int(job["id"]))

    assert result.outcome == jobs.CONTROL_CONFLICT
    assert "cannot be paused" in result.detail
    assert jobs.get_job(conn, int(job["id"]))["status"] == jobs.STATUS_QUEUED


def test_pause_racing_the_workers_claim_resolves_cleanly(db_path: str) -> None:
    """Pause (queued branch) and claim_next_job race on separate connections.

    Both take BEGIN IMMEDIATE, so exactly one shape may come out per round:
    pause wins -> 'paused', never claimed; claim wins -> 'running' with a
    pause stop_request the worker honours. No round may lose the job (left
    'queued' unclaimed with an 'immediate' answer, or claimed while 'paused').
    """
    import threading

    setup = get_connection(db_path)
    try:
        outcomes: set[str] = set()
        for round_no in range(40):
            job, _ = jobs.enqueue_prefill(setup, 1000 + round_no)
            job_id = int(job["id"])
            barrier = threading.Barrier(2)
            seen: dict[str, object] = {}

            def do_claim() -> None:
                c = get_connection(db_path)
                try:
                    barrier.wait()
                    seen["claim"] = jobs.claim_next_job(c)
                finally:
                    c.close()

            def do_pause() -> None:
                c = get_connection(db_path)
                try:
                    barrier.wait()
                    seen["pause"] = jobs.request_pause(c, job_id)
                finally:
                    c.close()

            threads = [threading.Thread(target=do_claim), threading.Thread(target=do_pause)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            assert not any(t.is_alive() for t in threads)

            pause = seen["pause"]
            claim = seen["claim"]
            row = jobs.get_job(setup, job_id)
            if pause.outcome == jobs.CONTROL_IMMEDIATE:
                assert claim is None, "a job paused while queued was claimed anyway"
                assert row["status"] == jobs.STATUS_PAUSED
            else:
                assert pause.outcome == jobs.CONTROL_REQUESTED, pause
                assert claim is not None and claim["id"] == job_id
                assert row["status"] == jobs.STATUS_RUNNING
                assert row["stop_request"] == jobs.STOP_REQUEST_PAUSE
                # Let the next round start from an empty queue.
                jobs.park_paused(setup, job_id, "[stub] partial")
            outcomes.add(pause.outcome)
        assert outcomes <= {jobs.CONTROL_IMMEDIATE, jobs.CONTROL_REQUESTED}
    finally:
        setup.close()


def test_resume_is_refused_unless_the_job_is_paused(conn) -> None:
    job, _ = jobs.enqueue_prefill(conn, 440)

    result = jobs.resume_job(conn, int(job["id"]))

    assert result.outcome == jobs.CONTROL_CONFLICT
    assert "not 'paused'" in result.detail
    assert jobs.get_job(conn, int(job["id"]))["status"] == jobs.STATUS_QUEUED


def test_resume_puts_the_job_at_the_FRONT_of_the_queue(conn) -> None:
    """The slot decision, pinned.

    Pause releases the worker slot, so other jobs run while this one is parked.
    Resume must then mean "carry on with this", not "get in line behind
    everything that overtook you" — which falls out of keeping the original job
    id, because the queue is FIFO by id. An implementation that created a NEW
    job row on resume would fail here.
    """
    parked = park_running_job(conn, 440)
    later, _ = jobs.enqueue_prefill(conn, 730)
    assert int(later["id"]) > int(parked["id"])

    result = jobs.resume_job(conn, int(parked["id"]))

    assert result.outcome == jobs.CONTROL_RESUMED
    assert result.job["status"] == jobs.STATUS_QUEUED
    assert result.job["id"] == parked["id"]
    assert result.job["stop_request"] is None

    claimed = jobs.claim_next_job(conn)
    assert claimed["id"] == parked["id"], "the resumed job must run before job 2"


def test_resume_keeps_paused_at_as_the_last_pause_timestamp(conn) -> None:
    parked = park_running_job(conn, 440)
    assert parked["paused_at"] is not None

    resumed = jobs.resume_job(conn, int(parked["id"])).job

    assert resumed["paused_at"] == parked["paused_at"]


def test_finish_job_always_clears_a_pending_stop_request(conn) -> None:
    """A terminal job showing "cancelling…" forever is a UI bug waiting to
    happen, so the clear lives in the one function every terminal transition
    goes through."""
    jobs.enqueue_prefill(conn, 440)
    claimed = jobs.claim_next_job(conn)
    jobs.cancel_job(conn, int(claimed["id"]))
    assert jobs.read_stop_request(conn, int(claimed["id"])) == "cancel"

    jobs.finish_job(conn, int(claimed["id"]), jobs.STATUS_DONE, "finished anyway")

    assert jobs.read_stop_request(conn, int(claimed["id"])) is None


# ==========================================================================
# 3. The endpoints
# ==========================================================================


def test_control_endpoints_404_on_an_unknown_job(client: TestClient) -> None:
    assert client.delete("/v1/jobs/999", headers=AUTH).status_code == 404
    assert client.post("/v1/jobs/999/pause", headers=AUTH).status_code == 404
    assert client.post("/v1/jobs/999/resume", headers=AUTH).status_code == 404


def test_control_endpoints_require_the_api_key(client: TestClient) -> None:
    """Router-level auth: a new route cannot forget it, but pin it anyway."""
    assert client.delete("/v1/jobs/1").status_code == 401
    assert client.post("/v1/jobs/1/pause").status_code == 401
    assert client.post("/v1/jobs/1/resume").status_code == 401


def test_delete_a_queued_job_over_http(client: TestClient) -> None:
    job_id = enqueue(client, 440)

    response = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "job_id": job_id,
        "status": "cancelled",
        "outcome": "immediate",
        "detail": jobs.CANCELLED_QUEUED_MESSAGE,
    }
    assert get_job(client, job_id)["status"] == "cancelled"


def test_deleting_a_finished_job_is_409_over_http(client: TestClient) -> None:
    job_id = enqueue(client, 440)
    client.delete(f"/v1/jobs/{job_id}", headers=AUTH)

    second = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)

    assert second.status_code == 409
    assert "already finished" in second.json()["detail"]


def test_pausing_a_queued_job_over_http_is_immediate(client: TestClient) -> None:
    """WP WEB-FEAT-5: was a 409 before the ADR-0016 addendum."""
    job_id = enqueue(client, 440)

    response = client.post(f"/v1/jobs/{job_id}/pause", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "job_id": job_id,
        "status": "paused",
        "outcome": "immediate",
        "detail": jobs.PAUSED_QUEUED_MESSAGE,
    }
    assert get_job(client, job_id)["status"] == "paused"

    second = client.post(f"/v1/jobs/{job_id}/pause", headers=AUTH)
    assert second.status_code == 409
    assert "already paused" in second.json()["detail"]


def test_resuming_a_job_that_is_not_paused_is_409_over_http(client: TestClient) -> None:
    job_id = enqueue(client, 440)

    response = client.post(f"/v1/jobs/{job_id}/resume", headers=AUTH)

    assert response.status_code == 409
    assert "not 'paused'" in response.json()["detail"]


def test_the_job_views_expose_paused_at_and_stop_request(
    client: TestClient, settings: Settings
) -> None:
    job_id = enqueue(client, 440)
    detail = get_job(client, job_id)
    assert detail["paused_at"] is None
    assert detail["stop_request"] is None

    conn = get_connection(settings.db_path)
    try:
        jobs.claim_next_job(conn)
        jobs.park_paused(conn, job_id, "partial")
    finally:
        conn.close()

    assert get_job(client, job_id)["paused_at"] is not None
    listed = next(
        entry
        for entry in client.get("/v1/jobs", headers=AUTH).json()
        if entry["id"] == job_id
    )
    assert listed["status"] == "paused"
    assert listed["paused_at"] is not None
    assert listed["stop_request"] is None


def test_delete_cache_is_409_while_a_prefill_is_paused(
    client: TestClient, settings: Settings
) -> None:
    """Audit item 3 through the real endpoint, with its own explanation."""
    client.put("/v1/mapping/441", json={"appid": 440}, headers=AUTH)
    job_id = enqueue(client, 440)
    conn = get_connection(settings.db_path)
    try:
        jobs.claim_next_job(conn)
        jobs.park_paused(conn, job_id, "partial")
    finally:
        conn.close()

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "paused" in detail
    assert "throw that progress away" in detail


# ==========================================================================
# 4. End to end through the worker
# ==========================================================================


def start_hanging_prefill(
    client: TestClient, appid: int = 440
) -> int:
    """Enqueue a job against a stub that hangs, and wait until it is running."""
    job_id = enqueue(client, appid)
    wait_for_status(client, job_id, ("running",))
    return job_id


def test_cancelling_a_running_prefill_terminates_it_and_finalizes_honestly(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """The headline case, and the needs_force / ingestion audit in one test.

    A valid SteamPrefill ``.bin`` file is planted in the temp-cache directory
    BEFORE the run, so "manifest ingestion did not happen" is a statement about
    a file that really was there to ingest — not about an empty directory.
    """
    from tests.test_manifests import _bin_manifest_bytes, _chunk_id
    from vault_api.depot_manifests import get_depot_manifest

    steamprefill_cache_dir = tmp_path / "steamprefill-cache"
    steamprefill_cache_dir.mkdir()
    (steamprefill_cache_dir / "440_440_441_555.bin").write_bytes(
        _bin_manifest_bytes(depot_id=441, manifest_id=555, files=[[(_chunk_id(1), 1000)]])
    )

    executable = stub_prefill.make_stub(bindir, mode="hang", cache_root=str(cache_root))
    settings = make_settings(
        tmp_path,
        cache_root,
        executable,
        steamprefill_cache_dir=str(steamprefill_cache_dir),
    )

    with TestClient(create_app(settings)) as client:
        job_id = start_hanging_prefill(client)
        assert app_status(client, 440) == "running"

        response = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "requested"
        assert response.json()["status"] == "running"

        job = wait_for_status(client, job_id, ("cancelled", "done", "error"))

        assert job["status"] == "cancelled", job["log_excerpt"]
        assert job["finished_at"] is not None
        assert job["stop_request"] is None
        # No misleading counters from a run that was killed part-way: the
        # summary is never parsed off a terminated run.
        assert job["updated"] is None
        assert job["up_to_date"] is None
        assert job["summary_parse_ok"] is None
        assert "stopped on request (cancelled)" in job["log_excerpt"]

        # apps.status: not 'running' (nothing is), not 'error' (nothing failed).
        assert app_status(client, 440) == "idle"

        # A partial run is no evidence: the mapping was not rewritten.
        assert client.get("/v1/mapping", headers=AUTH).json() == []

    conn = get_connection(settings.db_path)
    try:
        # needs_force is left exactly as it was — the run never completed, so
        # whatever made it forced still holds.
        assert jobs.get_app_needs_force(conn, 440) is True
        # ...and manifest ingestion never ran, despite a valid .bin being there.
        assert get_depot_manifest(conn, appid=440, depotid=441) is None
    finally:
        conn.close()
    assert (steamprefill_cache_dir / "440_440_441_555.bin").exists()


def test_pause_then_resume_reruns_steamprefill_and_completes(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """pause = terminate, resume = re-run — the whole feature, end to end."""
    executable = stub_prefill.make_stub(bindir, mode="hang", cache_root=str(cache_root))
    settings = make_settings(tmp_path, cache_root, executable)

    with TestClient(create_app(settings)) as client:
        job_id = start_hanging_prefill(client)

        response = client.post(f"/v1/jobs/{job_id}/pause", headers=AUTH)
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "requested"

        paused = wait_for_status(client, job_id, ("paused", "done", "error"))
        assert paused["status"] == "paused", paused["log_excerpt"]
        assert paused["finished_at"] is None, "a paused job is NOT finished"
        assert paused["paused_at"] is not None
        assert paused["stop_request"] is None
        assert app_status(client, 440) == "idle"

        # The worker slot was released: the next job runs while this one waits.
        assert client.post(
            "/v1/prefill", json={"appids": [440]}, headers=AUTH
        ).json()[0]["deduplicated"] is True

        # Now let the re-run succeed.
        stub_prefill.set_mode(
            bindir,
            mode="success",
            depots_by_app={440: [441]},
            summary_text=TABLE_UPDATED,
        )

        resumed = client.post(f"/v1/jobs/{job_id}/resume", headers=AUTH)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["outcome"] == "resumed"
        assert resumed.json()["status"] == "queued"

        done = wait_for_status(client, job_id, ("done", "error", "cancelled"))
        assert done["status"] == "done", done["log_excerpt"]
        assert done["updated"] == 1
        # paused_at survives: "this job was paused once" stays true.
        assert done["paused_at"] is not None
        assert app_status(client, 440) == "done"

    # SteamPrefill really was invoked a second time (the re-run recorded itself;
    # the hanging first attempt never got that far).
    assert len(stub_prefill.read_runs(bindir)) == 1
    assert stub_prefill.read_selection(bindir) == [440]


def test_pause_all_order_against_the_real_worker_leaves_it_idle(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """WP WEB-FEAT-5 end to end: pause the queued jobs, then the running one.
    The worker must not start either parked job afterwards."""
    executable = stub_prefill.make_stub(bindir, mode="hang", cache_root=str(cache_root))
    settings = make_settings(tmp_path, cache_root, executable)

    with TestClient(create_app(settings)) as client:
        running_id = start_hanging_prefill(client, 440)
        queued_ids = [enqueue(client, 730), enqueue(client, 570)]

        for job_id in queued_ids:
            response = client.post(f"/v1/jobs/{job_id}/pause", headers=AUTH)
            assert response.status_code == 200, response.text
            assert response.json()["outcome"] == "immediate"
        response = client.post(f"/v1/jobs/{running_id}/pause", headers=AUTH)
        assert response.json()["outcome"] == "requested"

        assert wait_for_status(client, running_id, ("paused",))["status"] == "paused"
        time.sleep(0.5)  # ~25 worker polls at 0.02 s
        for job_id in queued_ids:
            job = get_job(client, job_id)
            assert job["status"] == "paused"
            assert job["started_at"] is None, "a job paused while queued was started"


def test_a_paused_job_survives_a_restart_and_is_still_resumable(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Cross-restart: recover_stale_jobs must not eat it (audit item 5)."""
    executable = stub_prefill.make_stub(bindir, mode="hang", cache_root=str(cache_root))
    settings = make_settings(tmp_path, cache_root, executable)

    with TestClient(create_app(settings)) as client:
        job_id = start_hanging_prefill(client)
        client.post(f"/v1/jobs/{job_id}/pause", headers=AUTH)
        assert wait_for_status(client, job_id, ("paused",))["status"] == "paused"

    # ... vault-api restarts (a whole new app + lifespan against the same DB) ...
    stub_prefill.set_mode(
        bindir, mode="success", depots_by_app={440: [441]}, summary_text=TABLE_UPDATED
    )
    with TestClient(create_app(settings)) as client:
        after_restart = get_job(client, job_id)
        assert after_restart["status"] == "paused"
        assert after_restart["finished_at"] is None

        assert client.post(f"/v1/jobs/{job_id}/resume", headers=AUTH).status_code == 200
        assert wait_for_status(client, job_id, ("done", "error"))["status"] == "done"


def test_a_cancel_that_arrives_after_the_run_finished_does_not_rewrite_it(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """The finish-line race, decided in favour of what really happened."""
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        summary_text=TABLE_UPDATED,
    )
    settings = make_settings(tmp_path, cache_root, executable)

    with TestClient(create_app(settings)) as client:
        job_id = enqueue(client, 440)
        job = wait_for_status(client, job_id, ("done", "error"))
        assert job["status"] == "done"

        response = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)

        assert response.status_code == 409
        assert get_job(client, job_id)["status"] == "done"


def test_terminating_the_child_really_reaps_it(tmp_path: Path) -> None:
    """"A paused job never leaves an orphan SteamPrefill behind" — measured.

    Uses a real long-lived child started directly (no ``.cmd`` shim, whose
    known Windows artifact is that terminating it does not kill the Python
    grandchild — see tests/stub_prefill.py). ``_wait_for_process`` returns only
    after ``_stop_process`` has terminated AND waited, so the exit code being
    non-None and ``poll()`` answering immediately is the proof.
    """
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        outcome, exit_code = prefill._wait_for_process(
            process,
            timeout_seconds=60,
            should_abort=None,
            stop_request=lambda: "pause",
        )
    finally:
        if process.poll() is None:  # pragma: no cover - only if the stop failed
            process.kill()
            process.wait(timeout=10)

    assert outcome == "paused"
    assert exit_code is not None, "the child was terminated but never reaped"
    assert process.poll() is not None


def test_shutdown_wins_over_a_pending_pause(tmp_path: Path) -> None:
    """Both callbacks firing at once: the container is going down, so the run
    ends 'aborted' (-> job 'error', recoverable) rather than being parked at
    'paused' with nobody left to resume it."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        outcome, _ = prefill._wait_for_process(
            process,
            timeout_seconds=60,
            should_abort=lambda: True,
            stop_request=lambda: "pause",
        )
    finally:
        if process.poll() is None:  # pragma: no cover
            process.kill()
            process.wait(timeout=10)

    assert outcome == "aborted"


def test_a_process_that_already_exited_wins_the_race_against_a_stop_request(
    tmp_path: Path,
) -> None:
    """``process.poll()`` is checked FIRST on every tick, and that ordering is
    the whole answer to "the cancel raced the finish line".

    A run that completed genuinely completed — reporting it as ``cancelled``
    would throw away the mapping and manifest work it earned. Mutation-pinned:
    letting the pending request win here makes this test fail.
    """
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process.wait(timeout=30)  # it is already finished before we even look

    outcome, exit_code = prefill._wait_for_process(
        process,
        timeout_seconds=60,
        should_abort=None,
        stop_request=lambda: "cancel",
    )

    assert outcome == "exited"
    assert exit_code == 0


def test_an_unrecognized_stop_request_never_kills_a_download(tmp_path: Path) -> None:
    """Fail-safe direction: only the two known words stop a run."""
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    outcome, exit_code = prefill._wait_for_process(
        process,
        timeout_seconds=60,
        should_abort=None,
        stop_request=lambda: "halt-and-catch-fire",
    )

    assert outcome == "exited"
    assert exit_code == 0


# ==========================================================================
# 5. GC cancellation (cooperative, between depots)
# ==========================================================================


def build_two_depot_world(tmp_path: Path) -> Settings:
    """App 440 -> depots 441 and 442, each with orphans to collect."""
    from tests.test_gc import _cid, write_archive_bin, write_chunks
    from tests.test_gc_execute import (
        make_settings as gc_make_settings,
        open_db,
        seed_app,
        seed_mapping,
        seed_recorded_manifest,
    )

    settings = gc_make_settings(tmp_path, grace_days=0)
    root = Path(settings.cache_root)
    (root / "depot").mkdir(parents=True)

    for depotid in (441, 442):
        write_chunks(root, depotid, {_cid(0xA1): 100, _cid(0xB1): 300})
        write_archive_bin(
            Path(settings.manifest_archive_dir),
            depotid=depotid,
            manifestid="900",
            chunks={_cid(0xA1): 100},
        )

    conn = open_db(settings)
    try:
        seed_app(conn, 440)
        for depotid in (441, 442):
            seed_mapping(conn, depotid, 440)
            seed_recorded_manifest(
                conn, appid=440, depotid=depotid, manifestid="900"
            )
    finally:
        conn.close()
    return settings


def orphan_paths(settings: Settings) -> dict[int, Path]:
    from tests.test_gc import _cid

    return {
        depotid: Path(settings.cache_root)
        / "depot"
        / str(depotid)
        / "chunk"
        / _cid(0xB1)
        for depotid in (441, 442)
    }


def test_gc_cancellation_stops_between_depots_and_reports_honestly(
    tmp_path: Path,
) -> None:
    """Cooperative cancellation: the depot in progress finishes, the rest are
    never started, and the report says exactly that.

    Mutation-pinned: dropping the ``break`` (ignoring the flag) collects both
    depots and this test fails on the surviving orphan.
    """
    from vault_api import gc_execute

    settings = build_two_depot_world(tmp_path)
    orphans = orphan_paths(settings)
    assert all(path.exists() for path in orphans.values())

    calls: list[int] = []

    def should_cancel() -> bool:
        # False before the first depot, True before the second: exactly one
        # depot is processed.
        calls.append(1)
        return len(calls) > 1

    conn = get_connection(settings.db_path)
    try:
        report = gc_execute.run_gc(
            conn,
            440,
            cache_root=settings.cache_root,
            archive_dir=settings.manifest_archive_dir,
            execute=True,
            should_cancel=should_cancel,
        )
    finally:
        conn.close()

    assert report.cancelled is True
    assert len(report.depots) == 1
    assert report.skipped_depots == [442]
    assert report.removed_count == 1
    assert "CANCELLED on request after 1 depot(s)" in report.log_text()

    assert not orphans[441].exists(), "the depot in progress must be finished"
    assert orphans[442].exists(), "a skipped depot must lose nothing at all"


def test_a_cancelled_gc_job_ends_cancelled_not_done(tmp_path: Path) -> None:
    """Through the job entry point: the status names why the run ended."""
    from vault_api import gc_execute

    settings = build_two_depot_world(tmp_path)
    conn = get_connection(settings.db_path)
    try:
        job, _ = jobs.enqueue_gc(conn, 440, execute=True)
        claimed = jobs.claim_next_job(conn)
        # The operator cancels while the job is running.
        jobs.cancel_job(conn, int(claimed["id"]))

        gc_execute.run_gc_job(conn, claimed, settings=settings)

        finished = jobs.get_job(conn, int(job["id"]))
    finally:
        conn.close()

    assert finished["status"] == jobs.STATUS_CANCELLED
    assert finished["stop_request"] is None
    assert "CANCELLED on request" in finished["log_excerpt"]
    # Cancelled before the first depot -> nothing was touched at all.
    assert all(path.exists() for path in orphan_paths(settings).values())


def test_a_gc_dry_run_is_cancellable_too(tmp_path: Path) -> None:
    from vault_api import gc_execute

    settings = build_two_depot_world(tmp_path)
    conn = get_connection(settings.db_path)
    try:
        report = gc_execute.run_gc(
            conn,
            440,
            cache_root=settings.cache_root,
            archive_dir=settings.manifest_archive_dir,
            execute=False,
            should_cancel=lambda: True,
        )
    finally:
        conn.close()

    assert report.cancelled is True
    assert report.depots == []
    assert report.skipped_depots == [441, 442]


def test_an_uncancelled_gc_run_is_unaffected(tmp_path: Path) -> None:
    """The flag can only ever make a run do less — never change a normal one."""
    from vault_api import gc_execute

    settings = build_two_depot_world(tmp_path)
    conn = get_connection(settings.db_path)
    try:
        report = gc_execute.run_gc(
            conn,
            440,
            cache_root=settings.cache_root,
            archive_dir=settings.manifest_archive_dir,
            execute=True,
            should_cancel=lambda: False,
        )
    finally:
        conn.close()

    assert report.cancelled is False
    assert report.skipped_depots == []
    assert report.removed_count == 2
    assert "CANCELLED" not in report.log_text()


# ==========================================================================
# 6. Auto-GC (VAULT_AUTO_GC)
# ==========================================================================


def gc_jobs_for(conn: sqlite3.Connection, appid: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, type, status, gc_execute FROM jobs WHERE appid = ? AND type = ?",
        (appid, jobs.JOB_TYPE_GC),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def run_one_prefill(
    tmp_path: Path,
    bindir: Path,
    cache_root: Path,
    *,
    auto_gc: str,
    mode: str = "success",
    summary_text: str | None = TABLE_UPDATED,
) -> tuple[Settings, dict]:
    executable = stub_prefill.make_stub(
        bindir,
        mode=mode,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        summary_text=summary_text,
    )
    settings = make_settings(tmp_path, cache_root, executable, auto_gc=auto_gc)
    with TestClient(create_app(settings)) as client:
        job_id = enqueue(client, 440)
        job = wait_for_status(client, job_id, ("done", "error", "cancelled"))
    return settings, job


@pytest.mark.parametrize(
    ("auto_gc", "expected_execute"), [("dry-run", 0), ("execute", 1)]
)
def test_auto_gc_queues_a_gc_job_after_an_updating_prefill(
    tmp_path: Path, bindir: Path, cache_root: Path, auto_gc: str, expected_execute: int
) -> None:
    settings, job = run_one_prefill(tmp_path, bindir, cache_root, auto_gc=auto_gc)

    assert job["status"] == "done", job["log_excerpt"]
    assert f"Auto-GC (VAULT_AUTO_GC={auto_gc})" in job["log_excerpt"]

    conn = get_connection(settings.db_path)
    try:
        queued = gc_jobs_for(conn, 440)
    finally:
        conn.close()
    assert len(queued) == 1
    assert queued[0]["gc_execute"] == expected_execute


def test_auto_gc_off_queues_nothing_when_explicitly_configured(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Renamed (WP SWEEP-1, ADR-0014): this test's own former name claimed
    ``off`` "is the default", which stopped being true when
    ``DEFAULT_AUTO_GC`` flipped to ``AUTO_GC_EXECUTE`` (see
    ``test_config.py::test_auto_gc_defaults_to_execute`` for that pin). The
    behavioural claim this test actually exercises is unchanged and still
    correct either way: ``run_one_prefill`` below passes ``auto_gc="off"``
    EXPLICITLY, so nothing about this assertion ever depended on what the
    default happens to be.
    """
    settings, job = run_one_prefill(tmp_path, bindir, cache_root, auto_gc="off")

    assert job["status"] == "done"
    assert "Auto-GC" not in job["log_excerpt"]
    conn = get_connection(settings.db_path)
    try:
        assert gc_jobs_for(conn, 440) == []
    finally:
        conn.close()
    # A SEPARATE pin from the one above: `Settings(...)` here deliberately
    # omits `auto_gc=`, so this exercises the dataclass FIELD's own literal
    # default (`auto_gc: str = DEFAULT_AUTO_GC`) -- a different code path
    # from `Settings.from_env()`'s env-fallback pathway that
    # `test_config.py::test_auto_gc_defaults_to_execute` pins. Two call
    # sites deriving the same default are exactly the kind of pair that can
    # quietly diverge (docs/LEARNINGS.md "Containers") -- keeping both pinned
    # is deliberate, not redundant.
    assert Settings(
        vault_api_key="k", db_path="x", cache_root="y", log_level="INFO"
    ).auto_gc == "execute"


def test_auto_gc_does_not_fire_on_a_failed_run(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Mutation-pinned: moving the call out of the success branch fails here.

    A failed run tells us nothing about what is now orphaned — collecting off
    the back of one would act on a cache state nobody vouched for.
    """
    settings, job = run_one_prefill(
        tmp_path, bindir, cache_root, auto_gc="execute", mode="fail"
    )

    assert job["status"] == "error"
    conn = get_connection(settings.db_path)
    try:
        assert gc_jobs_for(conn, 440) == []
    finally:
        conn.close()


def test_auto_gc_does_not_fire_on_an_unowned_app(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """The exit-0-but-nothing-happened trap (ADR-0006 decision 1) is a failure
    outcome here too, so auto-GC must stay out of it."""
    settings, job = run_one_prefill(
        tmp_path, bindir, cache_root, auto_gc="execute", summary_text=TABLE_UNOWNED
    )

    assert job["status"] == "error"
    conn = get_connection(settings.db_path)
    try:
        assert gc_jobs_for(conn, 440) == []
    finally:
        conn.close()


def test_auto_gc_does_not_fire_when_nothing_was_updated(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Orphans come from game UPDATES. A routine "still current" confirmation
    changed nothing, so scanning every depot after it would be pure cost."""
    settings, job = run_one_prefill(
        tmp_path, bindir, cache_root, auto_gc="execute", summary_text=TABLE_UP_TO_DATE
    )

    assert job["status"] == "done"
    assert "Auto-GC" not in job["log_excerpt"]
    conn = get_connection(settings.db_path)
    try:
        assert gc_jobs_for(conn, 440) == []
    finally:
        conn.close()


def test_auto_gc_does_not_fire_when_the_summary_could_not_be_parsed(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """An unparseable table is not evidence of an update (WP 3.3's rule)."""
    settings, job = run_one_prefill(
        tmp_path, bindir, cache_root, auto_gc="execute", summary_text=None
    )

    assert job["status"] == "done"
    conn = get_connection(settings.db_path)
    try:
        assert gc_jobs_for(conn, 440) == []
    finally:
        conn.close()


def test_auto_gc_deduplicates_against_an_operators_pending_gc_job(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """No new mechanism: it calls the same enqueue_gc the endpoint does, so the
    per-(app, mode) dedupe rule applies unchanged."""
    from vault_api.worker import PrefillWorker
    from vault_api.prefill_summary import PrefillSummary

    settings = make_settings(tmp_path, cache_root, auto_gc="dry-run")
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        existing, created = jobs.enqueue_gc(conn, 440, execute=False)
        assert created is True

        log_parts: list[str] = []
        PrefillWorker(settings)._maybe_queue_auto_gc(
            conn,
            job_id=1,
            appid=440,
            summary=PrefillSummary(
                updated=2, up_to_date=0, total_bytes_text=None, parse_ok=True
            ),
            log_parts=log_parts,
        )

        assert gc_jobs_for(conn, 440) == [
            {
                "id": existing["id"],
                "type": jobs.JOB_TYPE_GC,
                "status": jobs.STATUS_QUEUED,
                "gc_execute": 0,
            }
        ]
    finally:
        conn.close()
    assert "already queued" in "\n".join(log_parts)


def test_auto_gc_failure_never_flips_a_successful_prefill(
    tmp_path: Path, cache_root: Path
) -> None:
    """A follow-up job that cannot be queued is a log line, not an outcome."""
    from vault_api.worker import PrefillWorker
    from vault_api.prefill_summary import PrefillSummary

    settings = make_settings(tmp_path, cache_root, auto_gc="execute")
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    conn.close()  # a closed connection makes enqueue_gc raise

    log_parts: list[str] = []
    PrefillWorker(settings)._maybe_queue_auto_gc(
        conn,
        job_id=1,
        appid=440,
        summary=PrefillSummary(
            updated=1, up_to_date=0, total_bytes_text=None, parse_ok=True
        ),
        log_parts=log_parts,
    )

    assert "could not be queued" in "\n".join(log_parts)
