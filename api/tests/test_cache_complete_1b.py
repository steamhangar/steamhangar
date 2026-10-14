"""WP CACHE-COMPLETE-1b: verify and complete incomplete games.

Guarantees pinned here, by name:

1. **Verify is a forced prefill.** ``POST /v1/prefill/verify`` sets
   ``needs_force = 1`` before it queues, so the worker runs the job with
   ``--force`` (recorded argv of the stub), through the one
   ``enqueue_prefill`` (dedupe, tool-app 422, auth).
2. **A running job is not duplicated** and its end does not clear the flag:
   the app's next run is forced.
3. **Complete all acts on exactly the listed games.** ``POST
   /v1/prefill/incomplete`` and ``GET /v1/games?incomplete=true`` share
   ``cache_completeness.is_incomplete`` (structural pin).
4. **Automatic completion only in the window, only for sweep targets, only
   with missing chunks.** No window, outside the window, not a target, or a
   game short only of wrong-size chunk files: nothing is forced.
5. **Re-measured after the job** (1a's worker hook; end-to-end here through
   a verify).
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_cache_complete_1a import chunks, record, seed_two_games, settings_for
from tests.test_gc import write_archive_bin, write_chunks
from tests.test_scheduler import local
from vault_api import cache_completeness, jobs, scheduler, update_check
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.schedule_window import parse_window

AUTH = {"X-Api-Key": TEST_API_KEY}


@pytest.fixture
def conn(tmp_path: Path):
    path = str(tmp_path / "vault.db")
    init_db(path)
    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def needs_force(conn: sqlite3.Connection, appid: int) -> int | None:
    row = conn.execute("SELECT needs_force FROM apps WHERE appid = ?", (appid,)).fetchone()
    return None if row is None else int(row[0])


def seed_measured(conn: sqlite3.Connection, settings: Settings) -> None:
    """440 complete, 730 at 25 % (3 chunks missing); both flagged non-forced,
    as after a successful earlier prefill."""
    seed_two_games(conn, settings)
    for appid in (440, 730):
        jobs.ensure_app_row(conn, appid)
    conn.execute("UPDATE apps SET needs_force = 0")
    conn.commit()
    cache_completeness.evaluate_apps(conn, settings, [440, 730])


def client_for(settings: Settings) -> TestClient:
    # No `with`: no lifespan, so no worker claims the queued jobs.
    return TestClient(create_app(settings))


# --------------------------------------------------------------------------
# 1. verify
# --------------------------------------------------------------------------


def test_verify_flags_the_app_and_queues_one_job(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_measured(conn, settings)
    client = client_for(settings)

    first = client.post("/v1/prefill/verify", json={"appids": [440]}, headers=AUTH)
    assert first.status_code == 202, first.text
    (ref,) = first.json()
    assert ref["appid"] == 440 and ref["status"] == "queued" and not ref["deduplicated"]
    assert needs_force(conn, 440) == 1
    assert needs_force(conn, 730) == 0

    again = client.post("/v1/prefill/verify", json={"appids": [440, 440]}, headers=AUTH)
    assert [(r["job_id"], r["deduplicated"]) for r in again.json()] == [
        (ref["job_id"], True),
        (ref["job_id"], True),
    ]
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_verify_refuses_tool_apps_and_needs_the_key(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    client = client_for(settings)
    refused = client.post("/v1/prefill/verify", json={"appids": [440, 228980]}, headers=AUTH)
    assert refused.status_code == 422
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert needs_force(conn, 440) is None  # nothing written, not even the flag

    assert client.post("/v1/prefill/verify", json={"appids": [440]}).status_code == 401
    assert client.post("/v1/prefill/incomplete").status_code == 401
    assert client.post("/v1/prefill/verify", json={"appids": []}, headers=AUTH).status_code == 422


def test_a_running_job_is_not_duplicated_and_the_next_run_is_forced(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_measured(conn, settings)
    job, _ = jobs.enqueue_prefill(conn, 730)
    claimed = jobs.claim_next_job(conn)
    assert claimed is not None and claimed["id"] == job["id"]
    seen_at_claim = jobs.get_app_needs_force(conn, 730)  # what the worker read
    assert seen_at_claim is False

    response = client_for(settings).post(
        "/v1/prefill/verify", json={"appids": [730]}, headers=AUTH
    )
    (ref,) = response.json()
    assert (ref["job_id"], ref["status"], ref["deduplicated"]) == (job["id"], "running", True)

    # The running job ends successfully: its compare-and-swap must not clear
    # the flag the verify set.
    assert jobs.clear_needs_force_if_unchanged(conn, 730, seen_at_claim) is False
    assert needs_force(conn, 730) == 1


# --------------------------------------------------------------------------
# 3. complete all
# --------------------------------------------------------------------------


def test_complete_all_queues_exactly_the_incomplete_games(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_measured(conn, settings)
    client = client_for(settings)

    listed = [g["appid"] for g in client.get("/v1/games?incomplete=true", headers=AUTH).json()]
    assert listed == [730]

    response = client.post("/v1/prefill/incomplete", headers=AUTH)
    assert response.status_code == 202, response.text
    assert [(r["appid"], r["deduplicated"]) for r in response.json()] == [(730, False)]
    assert (needs_force(conn, 730), needs_force(conn, 440)) == (1, 0)

    again = client.post("/v1/prefill/incomplete", headers=AUTH).json()
    assert [(r["appid"], r["deduplicated"]) for r in again] == [(730, True)]
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_complete_all_with_nothing_incomplete_is_an_empty_202(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = settings_for(tmp_path)
    seed_measured(conn, settings)
    write_chunks(Path(settings.cache_root), 731, chunks(10, 4))
    cache_completeness.evaluate_apps(conn, settings, [730])

    response = client_for(settings).post("/v1/prefill/incomplete", headers=AUTH)
    assert response.status_code == 202
    assert response.json() == []
    assert needs_force(conn, 730) == 0


def test_list_complete_all_and_scheduler_share_one_predicate(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural pin (LEARNINGS WP 4f): swap the predicate for one that
    selects the COMPLETE game; every caller must follow it exactly."""
    settings = settings_for(tmp_path)
    seed_measured(conn, settings)
    monkeypatch.setattr(
        cache_completeness, "is_incomplete", lambda figure: figure.pct == 100
    )
    client = client_for(settings)
    listed = [g["appid"] for g in client.get("/v1/games?incomplete=true", headers=AUTH).json()]
    queued = [r["appid"] for r in client.post("/v1/prefill/incomplete", headers=AUTH).json()]
    assert listed == queued == [440]

    sentinel = [cache_completeness.IncompleteApp(
        appid=4242, figure=cache_completeness.Completeness(pct=1, missing_chunks=1)
    )]
    monkeypatch.setattr(
        cache_completeness, "incomplete_apps", lambda *a, **k: sentinel
    )
    assert scheduler.completion_appids(conn, settings, [4242, 440, 730]) == (4242,)


# --------------------------------------------------------------------------
# 4. automatic completion in the sweep
# --------------------------------------------------------------------------


def window_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "schedule_window": parse_window("09:00-17:00"),
        "sweep_include_cached": True,
        **overrides,
    }
    return dataclasses.replace(settings_for(tmp_path), **values)


def test_inside_the_window_incomplete_targets_run_forced(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    settings = window_settings(tmp_path)
    seed_measured(conn, settings)
    result = scheduler.maybe_sweep(conn, settings, local(10))
    assert result.swept
    assert set(result.targets) == {440, 730}
    assert result.completing == (730,)
    assert (needs_force(conn, 730), needs_force(conn, 440)) == (1, 0)
    queued = {int(r[0]) for r in conn.execute("SELECT appid FROM jobs")}
    assert queued == {440, 730}


@pytest.mark.parametrize(
    "case",
    ["no-window", "outside-window", "not-a-target"],
)
def test_no_automatic_completion_outside_the_window_or_scope(
    tmp_path: Path, conn: sqlite3.Connection, case: str
) -> None:
    if case == "no-window":
        settings = dataclasses.replace(window_settings(tmp_path), schedule_window=None)
        moment = local(10)
    elif case == "outside-window":
        settings = window_settings(tmp_path)
        moment = local(20)
    else:  # cached games are not sweep targets, no client reports anything
        settings = window_settings(tmp_path, sweep_include_cached=False)
        moment = local(10)
    seed_measured(conn, settings)
    result = scheduler.maybe_sweep(conn, settings, moment)
    assert result.completing == ()
    assert needs_force(conn, 730) == 0
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_a_game_short_only_of_wrong_size_chunks_is_not_auto_completed(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """A forced run serves a wrong-size chunk file from disk as a HIT and
    cannot replace it; re-forcing every interval would replay the game for
    nothing. The game still lists as incomplete (manual action stays)."""
    settings = window_settings(tmp_path)
    seed_measured(conn, settings)
    cache = Path(settings.cache_root)
    write_chunks(cache, 731, chunks(10, 3))
    write_chunks(cache, 731, chunks(13, 1, size=99))  # damaged
    # A second game with a missing chunk, so the sweep's cheap "any row
    # missing?" pre-check passes and the per-app rule itself is what decides.
    record(conn, 570, 571, "3001", "2026-10-01T00:00:00Z")
    write_archive_bin(
        Path(settings.manifest_archive_dir), depotid=571, manifestid="3001",
        chunks=chunks(30, 2),
    )
    write_chunks(cache, 571, chunks(30, 1))
    jobs.ensure_app_row(conn, 570)
    conn.execute("UPDATE apps SET needs_force = 0")
    conn.commit()
    cache_completeness.evaluate_apps(conn, settings, [730, 570])
    found = {
        a.appid: a.figure.missing_chunks
        for a in cache_completeness.incomplete_apps(conn, settings, {731: 1, 441: 1, 571: 1})
    }
    assert found == {570: 1, 730: 0}

    result = scheduler.maybe_sweep(conn, settings, local(10))
    assert result.swept and 730 in result.targets
    assert result.completing == (570,)
    assert (needs_force(conn, 730), needs_force(conn, 570)) == (0, 1)


def test_a_failing_completion_step_does_not_stop_the_sweep(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = window_settings(tmp_path)
    seed_measured(conn, settings)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(cache_completeness, "incomplete_apps", boom)
    result = scheduler.maybe_sweep(conn, settings, local(10))
    assert result.swept and result.completing == ()
    assert set(result.enqueued) == {440, 730}
    assert needs_force(conn, 730) == 0


# --------------------------------------------------------------------------
# 1 + 5 end to end: the worker runs the verify forced and re-measures
# --------------------------------------------------------------------------


def test_verify_runs_forced_through_the_worker_and_is_remeasured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests import stub_prefill
    from tests.test_manifests import _bin_manifest_bytes
    from tests.test_worker import enqueue, make_settings, wait_for_job
    from vault_api import oracle
    from vault_api.config import MANIFEST_ORACLE_STEAMCMD_API

    monkeypatch.setattr(
        update_check.UpdateCheckRunner, "start",
        lambda self, *a, **k: update_check.DISABLED,
    )

    def no_fetch(url: str, *args: Any, **kwargs: Any) -> bytes:
        raise AssertionError(f"network fetch attempted: {url}")

    monkeypatch.setattr(oracle, "http_fetch", no_fetch)

    cache_root = tmp_path / "cache"
    temp_cache = tmp_path / "steamprefill-cache"
    bindir = tmp_path / "bin"
    stub_chunk = "%040x" % (441 * 1000)  # what the stub writes first, 160 bytes
    manifest = _bin_manifest_bytes(
        depot_id=441, manifest_id=555, files=[[(stub_chunk, 160)]],
    )
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        manifest_bins=[{
            "dir": str(temp_cache), "filename": "440_440_441_555.bin", "data": manifest,
        }],
    )
    settings = dataclasses.replace(
        make_settings(tmp_path, cache_root, executable, steamprefill_cache_dir=str(temp_cache)),
        manifest_oracle=MANIFEST_ORACLE_STEAMCMD_API,
    )

    with TestClient(create_app(settings)) as client:
        (first,) = enqueue(client, 440)  # first fill (forced by default)
        assert wait_for_job(client, first)["status"] == "done"
        (plain,) = enqueue(client, 440)
        assert wait_for_job(client, plain)["status"] == "done"
        assert "--force" not in stub_prefill.read_argv(bindir)

        db = get_connection(settings.db_path)
        try:
            db.execute("DELETE FROM cache_completeness")
            db.commit()
        finally:
            db.close()

        (ref,) = client.post("/v1/prefill/verify", json={"appids": [440]}, headers=AUTH).json()
        assert wait_for_job(client, ref["job_id"])["status"] == "done"
        assert "--force" in stub_prefill.read_argv(bindir)
        game = client.get("/v1/games/440", headers=AUTH).json()

    assert game["cache_complete_pct"] == 100  # re-measured after the job
    assert game["needs_force"] is False  # cleared by the forced run's success
