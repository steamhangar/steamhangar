"""End-to-end: HTTP enqueue -> background worker -> job/app/mapping state.

Uses the fake SteamPrefill from tests/stub_prefill.py — no Steam login, no
network. ``TestClient`` is used as a context manager here so the FastAPI
lifespan (stale-job recovery + worker thread) actually runs.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


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
    prefill_timeout_seconds: int = 60,
    steamprefill_cache_dir: str | None = None,
    manifest_archive_dir: str | None = None,
    manifest_keep: int = 3,
) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
        steamprefill_path=steamprefill_path,
        prefill_timeout_seconds=prefill_timeout_seconds,
        # Fast polling so the tests don't sit around waiting for a tick.
        worker_poll_seconds=0.02,
        # WP 3.2: unless a test wires up a real manifest temp-cache dir, this
        # deliberately points at a directory that never exists -- manifest
        # ingestion then safely no-ops (cache_dir_unavailable), which is
        # exactly the production behavior on a host that hasn't set
        # VAULT_STEAMPREFILL_CACHE_DIR up yet, and keeps every pre-3.2 test
        # in this module unaffected by ingestion.
        steamprefill_cache_dir=steamprefill_cache_dir
        or str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=manifest_archive_dir or str(tmp_path / "manifest-archive"),
        manifest_keep=manifest_keep,
    )


def wait_for_job(client: TestClient, job_id: int, timeout: float = 30.0) -> dict:
    """Poll GET /v1/jobs/{id} until the job reaches a terminal status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} did not finish within {timeout}s: {job}")


def enqueue(client: TestClient, *appids: int) -> list[int]:
    response = client.post("/v1/prefill", json={"appids": list(appids)}, headers=AUTH)
    assert response.status_code == 202, response.text
    return [entry["job_id"] for entry in response.json()]


# -- happy path ------------------------------------------------------------


def test_successful_job_updates_job_app_status_and_mapping(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441, 442]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done", job["log_excerpt"]
        assert job["started_at"] is not None
        assert job["finished_at"] is not None
        assert "Prefill complete!" in job["log_excerpt"]
        assert "Depot mapping updated" in job["log_excerpt"]

        game = client.get("/v1/games/440", headers=AUTH).json()
        assert game["status"] == "done"
        assert game["last_prefill_at"] is not None
        assert {depot["depotid"] for depot in game["depots"]} == {441, 442}

    # Selection file proves the verified non-interactive mechanism was used.
    assert stub_prefill.read_selection(bindir) == [440]


def test_worker_replaces_stale_mapping_but_keeps_shared_depots(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """ADR-0003 decision 3, through the full stack."""
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [300, 301]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        # Pre-existing mapping: 300 shared with 730, 999 about to go stale.
        client.put("/v1/mapping/300", json={"appid": 440}, headers=AUTH)
        client.put("/v1/mapping/300", json={"appid": 730}, headers=AUTH)
        client.put("/v1/mapping/999", json={"appid": 440}, headers=AUTH)

        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"

        pairs = {
            (entry["depotid"], entry["appid"])
            for entry in client.get("/v1/mapping", headers=AUTH).json()
        }
        assert pairs == {(300, 440), (300, 730), (301, 440)}

        # 300 is still reported as shared by both apps.
        for appid in (440, 730):
            detail = client.get(f"/v1/games/{appid}", headers=AUTH).json()
            shared = {d["depotid"] for d in detail["depots"] if d["shared"]}
            assert 300 in shared


def test_nothing_new_observed_leaves_the_mapping_untouched(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """The already-fully-cached case: no writes, so no evidence, so no change."""
    executable = stub_prefill.make_stub(bindir, mode="noop", cache_root=str(cache_root))
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        client.put("/v1/mapping/441", json={"appid": 440}, headers=AUTH)

        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done"
        assert "left unchanged" in job["log_excerpt"]
        detail = client.get("/v1/games/440", headers=AUTH).json()
        assert [d["depotid"] for d in detail["depots"]] == [441]
        assert detail["status"] == "done"


def test_successful_job_invalidates_the_size_cache(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """WP 1.5: plan §3's size calculation is "cached" — a successful prefill
    must invalidate it, or GET /v1/games would keep reporting a game's
    pre-prefill (null/stale) size for up to VAULT_SIZE_CACHE_TTL seconds.

    Uses the default 60s TTL (make_settings doesn't override it) so this only
    passes if the worker actively invalidates — a passive TTL expiry could
    never make it through in the test's runtime.
    """
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        client.put("/v1/mapping/441", json={"appid": 440}, headers=AUTH)

        # Populate the size cache with a "nothing on disk yet" snapshot,
        # exactly like a real app poll would before the first prefill.
        before = client.get("/v1/games/440", headers=AUTH).json()
        assert before["size_bytes"] is None

        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"

        after = client.get("/v1/games/440", headers=AUTH).json()
        assert after["size_bytes"] is not None
        assert after["size_bytes"] > 0


# -- job outcome honesty (WP 3.3, ADR-0006 decision 1) ----------------------
#
# SteamPrefill exiting 0 does not mean it did anything for the requested app
# (WP 1.7 finding: an unowned app exits 0 with "Prefilled 0 apps"). These
# tests drive the fix through the real stub subprocess pipe -- not just
# vault_api.prefill_summary in isolation -- covering every branch worker.py's
# job-outcome wiring has to make: unparseable table (exit-code rule
# unchanged), Updated==0 AND Up To Date==0 (-> 'error', not 'done'),
# Up To Date>0 AND Updated==0 (-> 'done' + last_manifest_check), and the
# normal Updated>0 case, each also checked for the new updated/up_to_date/
# summary_parse_ok fields on GET /v1/jobs/{id} and GET /v1/jobs (schema v4).

_CLEAN_TABLE_UPDATED = (
    "  Prefilled 1 apps totaling 12 MiB in 05.0000 \n"
    "   Updated | Up To Date\n"
    "  ---------+------------\n"
    "      1    |     0\n"
)

_CLEAN_TABLE_UP_TO_DATE = (
    "  Prefilled 1 apps totaling 0 b in 02.9012 \n"
    "   Updated | Up To Date\n"
    "  ---------+------------\n"
    "      0    |     1\n"
)

# Verbatim from the real blocked run (core/tests/mvp/RESULTS-20260805-222046.md)
# -- app 480 (Spacewar), not owned by the account that ran it. Box-drawing
# glyphs corrupted into "ï¿½" runs exactly as captured for real.
_MOJIBAKE_TABLE_UNOWNED = (
    "  Prefilled 0 apps totaling 0 b in 03.2491 \n"
    "   Updated ï¿½ Up To Date                    \n"
    "  ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½                   \n"
    "      0    ï¿½     0                         \n"
)


def test_normal_prefill_is_done_and_exposes_the_summary_fields(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        summary_text=_CLEAN_TABLE_UPDATED,
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done"
        assert job["updated"] == 1
        assert job["up_to_date"] == 0
        assert job["summary_parse_ok"] is True

        listed = next(
            entry for entry in client.get("/v1/jobs", headers=AUTH).json()
            if entry["id"] == job_id
        )
        assert listed["updated"] == 1
        assert listed["up_to_date"] == 0
        assert listed["summary_parse_ok"] is True

        assert client.get("/v1/games/440", headers=AUTH).json()["status"] == "done"


def test_unparseable_summary_falls_back_to_the_exit_code_rule(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """No summary table at all (the stub's default, pre-WP-3.3 output) — the
    process still exited 0, so the job is still 'done', but the new columns
    honestly record that nothing could be parsed."""
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done"
        assert job["updated"] is None
        assert job["up_to_date"] is None
        assert job["summary_parse_ok"] is False
        assert "Could not parse SteamPrefill's summary table" in job["log_excerpt"]


def test_zero_zero_summary_ends_error_not_done(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """ADR-0006 decision 1's exact trigger, replayed from the real capture:
    Updated==0 AND Up To Date==0 means SteamPrefill never considered the app
    (the real case: unowned) -- the process exited 0, but this must NOT be
    'done' (WP 1.7's job-outcome trap: a green badge for a never-cached
    game)."""
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        # No depots_by_app entry for 440 -- nothing written to disk, matching
        # the real unowned-app run this fixture is copied from.
        summary_text=_MOJIBAKE_TABLE_UNOWNED,
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "error"
        assert job["updated"] == 0
        assert job["up_to_date"] == 0
        assert job["summary_parse_ok"] is True
        assert "is it owned by the logged-in account" in job["log_excerpt"]

        detail = client.get("/v1/games/440", headers=AUTH).json()
        assert detail["status"] == "error"
        assert detail["last_prefill_at"] is None


def test_zero_zero_summary_leaves_mapping_and_manifests_untouched_even_with_planted_evidence(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Review S2: the unowned-app branch must run BEFORE
    apply_observed_mapping/ingest_after_prefill, not after — otherwise an
    'error' job can still have mutated depot_app_map/depot_manifests from
    evidence that (by the branch's own definition, Updated==0 AND
    Up To Date==0) cannot honestly belong to this app. Deliberately plants
    BOTH a depot directory (stub writes real chunk files for app 440) AND a
    SteamPrefill manifest .bin for the same depot, to prove the ordering
    fix, not just the absence of evidence."""
    from tests.test_manifests import _bin_manifest_bytes, _chunk_id
    from vault_api.depot_manifests import get_depot_manifest

    steamprefill_cache_dir = tmp_path / "steamprefill-cache"
    manifest_archive_dir = tmp_path / "manifest-archive"
    manifest_bytes = _bin_manifest_bytes(
        depot_id=441, manifest_id=555, files=[[(_chunk_id(1), 1000)]]
    )
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        manifest_bins=[
            {
                "dir": str(steamprefill_cache_dir),
                "filename": "440_440_441_555.bin",
                "data": manifest_bytes,
            }
        ],
        summary_text=_MOJIBAKE_TABLE_UNOWNED,
    )
    settings = make_settings(
        tmp_path,
        cache_root,
        executable,
        steamprefill_cache_dir=str(steamprefill_cache_dir),
        manifest_archive_dir=str(manifest_archive_dir),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "error"
        assert "Depot mapping and manifest state were NOT touched" in job["log_excerpt"]
        assert "Manifest ingestion" not in job["log_excerpt"]

        # Planted evidence: the depot dir really is there (proves the "no
        # mutation" result isn't just "nothing to observe" happenstance).
        assert (cache_root / "depot" / "441" / "chunk").is_dir()
        assert any((cache_root / "depot" / "441" / "chunk").iterdir())

        mapping = client.get("/v1/mapping", headers=AUTH).json()
        assert not any(entry["depotid"] == 441 for entry in mapping)

    conn = get_connection(settings.db_path)
    try:
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()
    assert row is None
    assert not (manifest_archive_dir / "441_555.bin").exists()


def test_up_to_date_summary_is_done_and_touches_last_manifest_check(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Up To Date>0 AND Updated==0 -- ADR-0006's "current as of <timestamp>"
    case: a genuine successful check that changed nothing on disk still
    earns 'done' (unlike the zero/zero case above) and additionally stamps
    apps.last_manifest_check, surfaced on both GET /v1/games and
    GET /v1/games/{appid} (WP 4c) -- checked both via HTTP and directly
    against the database (the DB read pins the value the HTTP layer must
    round-trip unchanged)."""
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        summary_text=_CLEAN_TABLE_UP_TO_DATE,
    )
    settings = make_settings(tmp_path, cache_root, executable)
    app = create_app(settings)

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done"
        assert job["updated"] == 0
        assert job["up_to_date"] == 1
        assert job["summary_parse_ok"] is True

        detail = client.get("/v1/games/440", headers=AUTH).json()
        assert detail["status"] == "done"
        assert detail["last_prefill_at"] is not None
        assert detail["last_manifest_check"] is not None

        summary_row = next(
            g for g in client.get("/v1/games", headers=AUTH).json() if g["appid"] == 440
        )
        assert summary_row["last_manifest_check"] == detail["last_manifest_check"]

    conn = get_connection(settings.db_path)
    try:
        row = conn.execute(
            "SELECT last_manifest_check FROM apps WHERE appid = 440"
        ).fetchone()
    finally:
        conn.close()
    assert row["last_manifest_check"] is not None
    assert row["last_manifest_check"] == detail["last_manifest_check"]


def test_updated_case_does_not_touch_last_manifest_check(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Only the exact Up To Date>0/Updated==0 shape earns last_manifest_check
    -- a normal Updated>0 run must not set it (nothing confirmed "already
    current" here, something actually changed). Checked via HTTP (WP 4c
    surfaced the field) and directly against the database."""
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        summary_text=_CLEAN_TABLE_UPDATED,
    )
    settings = make_settings(tmp_path, cache_root, executable)
    app = create_app(settings)

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"

        detail = client.get("/v1/games/440", headers=AUTH).json()
        assert detail["last_prefill_at"] is not None
        assert detail["last_manifest_check"] is None

    conn = get_connection(settings.db_path)
    try:
        row = conn.execute(
            "SELECT last_manifest_check FROM apps WHERE appid = 440"
        ).fetchone()
    finally:
        conn.close()
    assert row["last_manifest_check"] is None


# -- needs_force (WP 3.4, ADR-0006 decision 2) ------------------------------
#
# A fresh app row defaults to needs_force=1 (schema v5 default: never filled
# before, so the first run must force). A successful job (the 'done' outcome
# -- including a parse-failure-but-exit-0 run and the up-to-date-confirmed
# case, NOT the unowned zero/zero case) clears it, so the app's NEXT run goes
# non-forced. The unowned-app error and every failure branch must leave it
# exactly as it was.


def test_first_run_is_forced_and_success_clears_needs_force_for_the_next_run(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Proven through two real subprocess runs (the recorded argv), not just
    the stored flag value: a brand-new app's first job passes --force, and
    after it succeeds the SAME app's next job omits it."""
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"
        assert stub_prefill.read_argv(bindir) == ["prefill", "--force", "--no-ansi", "--max-threads", "8"]

        (job_id_2,) = enqueue(client, 440)
        assert wait_for_job(client, job_id_2)["status"] == "done"
        assert stub_prefill.read_argv(bindir) == ["prefill", "--no-ansi", "--max-threads", "8"]


def test_needs_force_is_exposed_on_the_games_endpoints_and_cleared_by_success(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        # The apps row is created synchronously on enqueue (ensure_app_row),
        # so this is visible before the job even starts running.
        detail = client.get("/v1/games/440", headers=AUTH).json()
        assert detail["needs_force"] is True
        listed = next(
            entry for entry in client.get("/v1/games", headers=AUTH).json()
            if entry["appid"] == 440
        )
        assert listed["needs_force"] is True

        assert wait_for_job(client, job_id)["status"] == "done"

        assert client.get("/v1/games/440", headers=AUTH).json()["needs_force"] is False


def test_up_to_date_confirmation_also_clears_needs_force(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """The up-to-date-confirmed case is still a genuine success ('done') --
    ADR-0006 decision 2 clears needs_force there too, not only when something
    was actually updated."""
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), summary_text=_CLEAN_TABLE_UP_TO_DATE
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"
        assert client.get("/v1/games/440", headers=AUTH).json()["needs_force"] is False


def test_unowned_outcome_leaves_needs_force_unchanged(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Driven from needs_force=0 (not the schema default of 1), so this
    actually proves the unowned branch leaves the flag alone rather than
    merely observing the default it started at."""
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={550: [551]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 550)
        assert wait_for_job(client, job_id)["status"] == "done"
        assert client.get("/v1/games/550", headers=AUTH).json()["needs_force"] is False

        stub_prefill.set_mode(
            bindir, depots_by_app={}, summary_text=_MOJIBAKE_TABLE_UNOWNED
        )
        (job_id_2,) = enqueue(client, 550)
        job2 = wait_for_job(client, job_id_2)
        assert job2["status"] == "error"
        assert client.get("/v1/games/550", headers=AUTH).json()["needs_force"] is False


def test_failed_job_leaves_needs_force_unchanged(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Same reasoning as the unowned test above: start from needs_force=0 so
    a bug that resets it on failure would actually be caught."""
    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        assert wait_for_job(client, job_id)["status"] == "done"
        assert client.get("/v1/games/440", headers=AUTH).json()["needs_force"] is False

        stub_prefill.set_mode(bindir, mode="fail", exit_code=4)
        (job_id_2,) = enqueue(client, 440)
        job2 = wait_for_job(client, job_id_2)
        assert job2["status"] == "error"
        assert client.get("/v1/games/440", headers=AUTH).json()["needs_force"] is False


def test_a_deletion_racing_a_slow_job_does_not_get_its_needs_force_clobbered(
    tmp_path: Path, bindir: Path, cache_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer-reproduced end-to-end wedge (WP 3.4 review blocker).

    A prefill job claimed DURING a DELETE's filesystem window used to clear
    needs_force back to 0 with a last-writer-wins ``UPDATE`` at the end of
    its run, clobbering the 1 the deletion had just set. Reproduced sequence:
    app 'done' with last_prefill_at set, cache directory EMPTY, needs_force=0
    -- and no self-healing path, since SteamPrefill's own bookkeeping still
    thinks the (now-deleted) depots are present, so every future run stays
    non-forced forever.

    **Deterministic reproduction, not a real thread race gamble.**
    ``DELETE /v1/cache/{appid}``'s active-job check runs BEFORE
    ``deletion.plan_deletion`` (see ``routers/cache.py``), so wrapping
    ``plan_deletion`` to enqueue the racing job at that exact point guarantees
    it is created strictly AFTER the check already passed (no 409) and
    strictly BEFORE ``delete_app_depots``/``reset_app_after_deletion`` run.
    Polling the job's status via a direct DB read (not the HTTP client, to
    avoid nesting requests inside the DELETE's own in-flight request) confirms
    the real background worker actually claimed it -- reading
    ``needs_force=0``, the PRE-deletion value -- before letting the DELETE
    request continue. The racing job's stub sleeps long enough (well past a
    single small depot's removal) that it can only finish, and attempt its
    needs_force clear, AFTER the "fast" DELETE has already returned.
    """
    from vault_api import deletion as deletion_module
    from vault_api import jobs as jobs_queue

    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root), sleep_seconds=1.5)
    settings = make_settings(tmp_path, cache_root, executable)
    app = create_app(settings)

    # Pre-existing state: app 440 already successfully filled once
    # (needs_force=0) with real content on disk for DELETE to remove.
    depot_dir = cache_root / "depot" / "441" / "chunk"
    depot_dir.mkdir(parents=True)
    (depot_dir / "aa").write_bytes(b"1" * 10)

    with TestClient(app) as client:
        assert client.put(
            "/v1/mapping/441", json={"appid": 440}, headers=AUTH
        ).status_code == 200
        conn = get_connection(settings.db_path)
        try:
            conn.execute("UPDATE apps SET needs_force = 0 WHERE appid = 440")
            conn.commit()
        finally:
            conn.close()

        racing_job_id: list[int] = []
        real_plan_deletion = deletion_module.plan_deletion

        def plan_then_enqueue_the_racing_job(rows, appid, co_owner_states=None):  # type: ignore[no-untyped-def]
            plan = real_plan_deletion(rows, appid, co_owner_states)

            # Enqueued here: strictly AFTER the endpoint's own active-job
            # check (already passed by the time plan_deletion runs) and
            # strictly BEFORE delete_app_depots/reset_app_after_deletion.
            race_conn = get_connection(settings.db_path)
            try:
                job, _created = jobs_queue.enqueue_prefill(race_conn, appid)
            finally:
                race_conn.close()
            racing_job_id.append(int(job["id"]))

            # Give the real background worker (fast poll interval in tests)
            # a chance to actually claim it -- reading needs_force=0, the
            # PRE-deletion value -- before the deletion's own filesystem work
            # runs. Polled via a fresh direct connection, not client.get,
            # since this callback executes INSIDE the DELETE request's own
            # (threadpool) handling of this TestClient -- nesting another
            # request through the same client here is unnecessary risk for
            # no benefit.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                check_conn = get_connection(settings.db_path)
                try:
                    row = check_conn.execute(
                        "SELECT status FROM jobs WHERE id = ?", (job["id"],)
                    ).fetchone()
                finally:
                    check_conn.close()
                if row is not None and row["status"] == "running":
                    break
                time.sleep(0.01)
            else:  # pragma: no cover
                pytest.fail("racing job was never claimed by the background worker")

            return plan

        monkeypatch.setattr(
            deletion_module, "plan_deletion", plan_then_enqueue_the_racing_job
        )

        response = client.delete("/v1/cache/440", headers=AUTH)

        assert response.status_code == 200, response.text
        assert response.json()["deleted_depots"] == [
            {"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []}
        ]
        assert racing_job_id, "the racing job was never enqueued"

        # Immediately after the (fast) DELETE returns, the racing job is
        # still asleep (sleep_seconds=1.5) -- needs_force must already be 1
        # from the deletion itself.
        assert client.get("/v1/games/440", headers=AUTH).json()["needs_force"] is True

        job = wait_for_job(client, racing_job_id[0], timeout=30)
        assert job["status"] == "done"

        # THE FIX: the racing job read needs_force=0 at claim time, so its
        # end-of-run clear is a compare-and-swap against that STALE value --
        # by the time it runs, the current value is already 1 (set by the
        # deletion), so the CAS is a no-op. needs_force must still be 1, not
        # clobbered back to 0 (the reviewer-reproduced wedge).
        final = client.get("/v1/games/440", headers=AUTH).json()
        assert final["needs_force"] is True


# -- manifest ingestion (WP 3.2) --------------------------------------------


def test_successful_job_ingests_a_dropped_manifest_bin_end_to_end(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Full stack: HTTP enqueue -> worker runs the stub -> stub drops a
    synthetic SteamPrefill manifest .bin into a fake temp-cache dir -> the
    worker's post-success ingestion step parses it, records depot_manifests,
    and archives it -- exactly what a real SteamPrefill run would trigger."""
    from tests.test_manifests import _bin_manifest_bytes, _chunk_id
    from vault_api.db import get_connection
    from vault_api.depot_manifests import get_depot_manifest

    steamprefill_cache_dir = tmp_path / "steamprefill-cache"
    manifest_archive_dir = tmp_path / "manifest-archive"

    manifest_bytes = _bin_manifest_bytes(
        depot_id=441, manifest_id=555, files=[[(_chunk_id(1), 1000)]]
    )
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        manifest_bins=[
            {
                "dir": str(steamprefill_cache_dir),
                "filename": "440_440_441_555.bin",
                "data": manifest_bytes,
            }
        ],
    )
    settings = make_settings(
        tmp_path,
        cache_root,
        executable,
        steamprefill_cache_dir=str(steamprefill_cache_dir),
        manifest_archive_dir=str(manifest_archive_dir),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done"
        assert "Manifest ingestion: recorded 1 manifest" in job["log_excerpt"]

    conn = get_connection(settings.db_path)
    try:
        row = get_depot_manifest(conn, appid=440, depotid=441)
    finally:
        conn.close()

    assert row is not None
    assert row["manifestid"] == "555"
    assert row["chunk_count"] == 1
    assert (manifest_archive_dir / "441_555.bin").exists()


def test_ingestion_failure_never_flips_a_successful_job_to_error(
    tmp_path: Path, bindir: Path, cache_root: Path, monkeypatch
) -> None:
    """A crash inside manifest ingestion must not undo a successful prefill
    (worker.py's local try/except around ingest_after_prefill)."""
    import vault_api.worker as worker_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated ingestion bug")

    monkeypatch.setattr(worker_module.manifest_ingest, "ingest_after_prefill", boom)

    executable = stub_prefill.make_stub(
        bindir, cache_root=str(cache_root), depots_by_app={440: [441]}
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "done"
        assert "Manifest ingestion crashed" in job["log_excerpt"]
        assert client.get("/v1/games/440", headers=AUTH).json()["status"] == "done"


# -- one at a time ---------------------------------------------------------


def test_jobs_run_strictly_one_at_a_time_in_fifo_order(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={10: [110], 20: [120], 30: [130]},
        sleep_seconds=0.3,
    )
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        job_ids = enqueue(client, 10, 20, 30)
        for job_id in job_ids:
            assert wait_for_job(client, job_id, timeout=60)["status"] == "done"

    runs = stub_prefill.read_runs(bindir)
    assert [run["selected"] for run in runs] == [[10], [20], [30]]

    # No two runs overlap in wall-clock time -> exactly one job at a time.
    for earlier, later in zip(runs, runs[1:]):
        assert earlier["finished"] <= later["started"], runs


# -- failure paths ---------------------------------------------------------


def test_failed_job_sets_error_status_and_does_not_touch_the_mapping(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(bindir, mode="fail", exit_code=4)
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        client.put("/v1/mapping/441", json={"appid": 440}, headers=AUTH)

        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "error"
        assert "simulated depot download failure" in job["log_excerpt"]
        assert "exited with code 4" in job["log_excerpt"]
        assert "left unchanged" in job["log_excerpt"]

        detail = client.get("/v1/games/440", headers=AUTH).json()
        assert detail["status"] == "error"
        assert detail["last_prefill_at"] is None
        assert [d["depotid"] for d in detail["depots"]] == [441]


def test_not_logged_in_job_fails_with_the_interactive_login_instruction(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(bindir, mode="not_logged_in")
    app = create_app(make_settings(tmp_path, cache_root, executable))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "error"
        assert "select-apps" in job["log_excerpt"]
        assert "Steam Guard" in job["log_excerpt"]


def test_hanging_prefill_is_timed_out_and_the_queue_keeps_draining(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(bindir, mode="hang")
    app = create_app(make_settings(tmp_path, cache_root, executable, prefill_timeout_seconds=1))

    with TestClient(app) as client:
        (first, second) = enqueue(client, 440, 730)

        assert wait_for_job(client, first, timeout=30)["status"] == "error"
        job = client.get(f"/v1/jobs/{first}", headers=AUTH).json()
        assert "time budget" in job["log_excerpt"]

        # A wedged job must not stall the queue forever.
        assert wait_for_job(client, second, timeout=30)["status"] == "error"


def test_missing_steamprefill_path_fails_the_job_but_not_the_app(
    tmp_path: Path, cache_root: Path
) -> None:
    app = create_app(make_settings(tmp_path, cache_root, steamprefill_path=""))

    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        job = wait_for_job(client, job_id)

        assert job["status"] == "error"
        assert "VAULT_STEAMPREFILL_PATH is not set" in job["log_excerpt"]

        # The rest of the API is unaffected — that's the whole point.
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/games", headers=AUTH).status_code == 200
        assert client.get("/v1/games/440", headers=AUTH).json()["status"] == "error"


# -- lifecycle -------------------------------------------------------------


def test_startup_recovers_a_job_left_running_by_a_dead_process(
    tmp_path: Path, cache_root: Path
) -> None:
    settings = make_settings(tmp_path, cache_root)
    init_db(settings.db_path)

    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO jobs (id, appid, type, status, created_at, started_at)
            VALUES (77, 440, 'prefill', 'running', '2026-08-05T10:00:00Z', '2026-08-05T10:00:01Z')
            """
        )
        conn.execute("INSERT INTO apps (appid, status) VALUES (440, 'running')")
        conn.commit()
    finally:
        conn.close()

    with TestClient(create_app(settings)) as client:
        job = client.get("/v1/jobs/77", headers=AUTH).json()
        assert job["status"] == "error"
        assert job["finished_at"] is not None
        assert "died" in job["log_excerpt"]

        assert client.get("/v1/games/440", headers=AUTH).json()["status"] == "error"

        # The dead job no longer blocks a fresh request for the same app.
        (new_job_id,) = enqueue(client, 440)
        assert new_job_id != 77


def test_shutdown_aborts_a_running_job_instead_of_hanging(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    executable = stub_prefill.make_stub(bindir, mode="hang")
    settings = make_settings(tmp_path, cache_root, executable, prefill_timeout_seconds=600)
    app = create_app(settings)

    started = time.monotonic()
    with TestClient(app) as client:
        (job_id,) = enqueue(client, 440)
        # Wait until the worker has actually claimed and started it.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()["status"] == "running":
                break
            time.sleep(0.05)
        else:  # pragma: no cover
            pytest.fail("worker never claimed the job")
    elapsed = time.monotonic() - started

    # Leaving the context manager runs shutdown; it must not wait out the
    # 600s prefill timeout.
    assert elapsed < 30, elapsed

    conn = get_connection(settings.db_path)
    try:
        row = conn.execute("SELECT status, log_excerpt FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "error"
    assert "shutting down" in row["log_excerpt"]


def test_worker_only_runs_when_the_lifespan_runs(
    tmp_path: Path, bindir: Path, cache_root: Path
) -> None:
    """Guards the other test modules: no ``with`` -> no worker -> stable state."""
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root))
    client = TestClient(create_app(make_settings(tmp_path, cache_root, executable)))

    (job_id,) = enqueue(client, 440)
    time.sleep(0.3)

    assert client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()["status"] == "queued"
    assert stub_prefill.read_runs(bindir) == []
