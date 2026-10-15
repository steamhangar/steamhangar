"""``POST /v1/prefill/cached`` (Phase 4c, WP 4c-api): the server-side
"check & update every cached game" convenience route.

No worker runs in these tests (the ``client`` fixture never enters the
lifespan as a context manager — same rule ``test_jobs_api.py`` documents), so
every job created here stays ``queued`` unless a test explicitly claims it
with ``jobs.claim_next_job`` to simulate the worker picking it up.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from vault_api import event_sweep, jobs
from vault_api.db import get_connection
from vault_api.routers.jobs import PrefillJobRef, _select_appids_with_cache_content

AUTH = {"X-Api-Key": TEST_API_KEY}


# --------------------------------------------------------------------------
# Seeding helpers
# --------------------------------------------------------------------------


def _write_bytes(path: Path, content: bytes = b"x" * 32) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _seed_mapping(client: TestClient, depotid: int, appid: int) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": None}, headers=AUTH
    )
    assert response.status_code == 200, response.text


def _seed_cache_bytes(client: TestClient, depotid: int, content: bytes = b"x" * 32) -> None:
    cache_root = Path(client.app.state.settings.cache_root)
    _write_bytes(cache_root / "depot" / str(depotid) / "chunk" / "a", content)


def _seed_cached_app(client: TestClient, appid: int, depotid: int) -> None:
    """A fully ordinary cached app: mapped, and with real bytes on disk."""
    _seed_mapping(client, depotid, appid)
    _seed_cache_bytes(client, depotid)


def _open_conn(client: TestClient):
    return get_connection(client.app.state.settings.db_path)


def _claim_oldest_job(client: TestClient) -> None:
    """Simulate the worker picking up the head of the queue (no real worker
    runs in this module)."""
    conn = _open_conn(client)
    try:
        claimed = jobs.claim_next_job(conn)
        assert claimed is not None
    finally:
        conn.close()


def _sql(client: TestClient, statement: str, params: tuple = ()) -> None:
    conn = _open_conn(client)
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


def test_cached_prefill_requires_api_key(client: TestClient) -> None:
    assert client.post("/v1/prefill/cached").status_code == 401


# --------------------------------------------------------------------------
# Empty / degenerate cases
# --------------------------------------------------------------------------


def test_empty_cache_returns_an_empty_successful_result(client: TestClient) -> None:
    response = client.post("/v1/prefill/cached", headers=AUTH)
    assert response.status_code == 202, response.text
    assert response.json() == []
    assert client.get("/v1/jobs", headers=AUTH).json() == []


def test_cached_depot_with_no_mapping_contributes_no_job(client: TestClient) -> None:
    """A depot with real bytes on disk but no depot_app_map row (an unmapped
    depot, same concept GET /v1/cache/summary reports) names no app — there is
    nothing to enqueue a prefill FOR. This must not error; it must simply
    select nothing."""
    _seed_cache_bytes(client, depotid=555)

    response = client.post("/v1/prefill/cached", headers=AUTH)
    assert response.status_code == 202
    assert response.json() == []
    assert client.get("/v1/jobs", headers=AUTH).json() == []


def test_app_with_residual_content_after_a_partial_deletion_is_still_selected(
    client: TestClient,
) -> None:
    """Simulates the honest 'mid-deletion' case: a depot still has bytes on
    disk (a DELETE that failed partway through, WP 1.6's documented
    shutil.rmtree behaviour), the app is left at status='error' with
    needs_force=1, but the mapping and the on-disk bytes are still there.
    Selection is disk-and-mapping truth, not apps.status -- this app must
    still be picked up, because "check & update" is exactly the repair this
    app needs."""
    _seed_cached_app(client, appid=441, depotid=4410)
    _sql(
        client,
        "UPDATE apps SET status = 'error', needs_force = 1 WHERE appid = ?",
        (441,),
    )

    response = client.post("/v1/prefill/cached", headers=AUTH)
    assert response.status_code == 202
    body = response.json()
    assert [entry["appid"] for entry in body] == [441]
    assert body[0]["deduplicated"] is False


# --------------------------------------------------------------------------
# Selection + enqueue (the ordinary case)
# --------------------------------------------------------------------------


def test_selects_and_enqueues_every_cached_app_via_the_shared_queue(
    client: TestClient,
) -> None:
    _seed_cached_app(client, appid=440, depotid=441)
    _seed_cached_app(client, appid=730, depotid=731)
    # An app that is mapped but has never been prefilled (no bytes on disk
    # yet) must NOT be selected -- it has no cache content to check.
    _seed_mapping(client, depotid=999, appid=999)

    response = client.post("/v1/prefill/cached", headers=AUTH)
    assert response.status_code == 202, response.text
    body = response.json()

    assert sorted(entry["appid"] for entry in body) == [440, 730]
    assert all(entry["status"] == jobs.STATUS_QUEUED for entry in body)
    assert all(entry["deduplicated"] is False for entry in body)

    queued = client.get("/v1/jobs", headers=AUTH).json()
    assert {job["appid"] for job in queued} == {440, 730}
    assert all(job["type"] == jobs.JOB_TYPE_PREFILL for job in queued)


def test_multi_depot_apps_and_an_adr_0003_shared_depot_yield_exactly_one_entry_per_app(
    client: TestClient,
) -> None:
    """The primary guarantee of this route, pinned directly: "exactly one
    entry per app", never one entry per cached DEPOT. Two shapes that would
    break a naive per-depot walk (docs/LEARNINGS.md: the same ADR-0003
    shared-depot invariant blocked WP 4a.2):

    - app 10 maps TWO of its own cached depots (a normal multi-depot game);
    - depot 200 is mapped to BOTH app 20 and app 21 (a shared/redistributable
      depot, plan §4 / ADR-0003) -- each app must still get exactly one job,
      not one job for owning the depot and another for sharing it.

    A selection that returns one row per depot instead of per app would
    duplicate app 10's entry and could duplicate app 20/21's -- exactly the
    "duplicate rows in the Jobs view" bug two frontend packages would
    otherwise render.
    """
    # app 10: two of its own depots, both cached.
    _seed_mapping(client, depotid=101, appid=10)
    _seed_mapping(client, depotid=102, appid=10)
    _seed_cache_bytes(client, depotid=101)
    _seed_cache_bytes(client, depotid=102)

    # depot 200 shared between app 20 and app 21.
    _seed_mapping(client, depotid=200, appid=20)
    _seed_mapping(client, depotid=200, appid=21)
    _seed_cache_bytes(client, depotid=200)

    # app 30: an ordinary single-depot control case.
    _seed_cached_app(client, appid=30, depotid=300)

    response = client.post("/v1/prefill/cached", headers=AUTH)
    assert response.status_code == 202, response.text
    body = response.json()

    ids = [entry["appid"] for entry in body]
    assert sorted(ids) == [10, 20, 21, 30]
    # The guarantee, stated as its own assertion so it cannot be satisfied
    # by accident: no appid appears twice.
    assert len(ids) == len(set(ids)), f"duplicate appid entries in response: {ids}"

    queued = client.get("/v1/jobs", headers=AUTH).json()
    assert len(queued) == 4
    assert {job["appid"] for job in queued} == {10, 20, 21, 30}


def test_response_shape_matches_the_real_prefill_job_ref_model(
    client: TestClient,
) -> None:
    """Asserted against the actual Pydantic model the route declares as its
    response_model -- not a hand-written dict a future field rename could
    silently drift away from."""
    _seed_cached_app(client, appid=440, depotid=441)

    body = client.post("/v1/prefill/cached", headers=AUTH).json()
    assert body

    expected_fields = set(PrefillJobRef.model_fields)
    for entry in body:
        assert set(entry.keys()) == expected_fields
        # Round-trips cleanly through the real model with no coercion surprises.
        ref = PrefillJobRef.model_validate(entry)
        assert ref.appid == entry["appid"]
        assert ref.job_id == entry["job_id"]
        assert ref.status == entry["status"]
        assert ref.deduplicated == entry["deduplicated"]


# --------------------------------------------------------------------------
# WP 4f, S1 (reviewer should-fix, 2026-08-18 review round): the user-visible
# behaviour the whole package exists for, pinned at the HTTP layer where the
# original bug actually lived. The structural pin
# (tests/test_wp4f_shared_cache_content_definition.py) and the unit tests on
# deletion.appids_with_cache_content (tests/test_cache_delete.py) both catch
# a regression in the SHARED function, but neither exercises a real DELETE
# against a real filesystem through this route -- and reverting this route
# to its pre-WP-4f generous predicate left the rest of THIS file fully green
# (its own ADR-0003 fixture is a remnant case, which passes under both the
# old and the new rule). This is the test that would have caught the
# original divergence.
# --------------------------------------------------------------------------


def test_a_deleted_app_whose_only_surviving_content_is_a_shared_protected_depot_is_not_reselected(
    client: TestClient,
) -> None:
    """Real ``DELETE /v1/cache/440`` against a real filesystem: app 440 has
    its own exclusive depot 441 plus a depot 300 shared with app 730; app 730
    has its own exclusive depot 731 and currently has content, so depot 300
    stays SHARED/protected (never a deletable remnant) throughout. After the
    delete, 440's only surviving mapped depot is that protected depot 300 —
    ``POST /v1/prefill/cached`` must NOT re-select 440 for it."""
    _seed_mapping(client, depotid=441, appid=440)
    _seed_cache_bytes(client, depotid=441)
    _seed_mapping(client, depotid=300, appid=440)
    _seed_mapping(client, depotid=300, appid=730)
    _seed_cache_bytes(client, depotid=300)
    _seed_mapping(client, depotid=731, appid=730)
    _seed_cache_bytes(client, depotid=731)

    # 730 currently has content -> depot 300 stays SHARED/protected, not a
    # deletable remnant, through the delete below (ADR-0003).
    _sql(
        client,
        "UPDATE apps SET status = 'done', last_prefill_at = ? WHERE appid = 730",
        ("2026-08-05T10:00:00Z",),
    )

    delete_response = client.delete("/v1/cache/440", headers=AUTH)
    assert delete_response.status_code == 200, delete_response.text
    body = delete_response.json()
    assert body["deleted_depots"] == [
        {"depotid": 441, "size_bytes_freed": 32, "shared_with_uncached": []}
    ]
    assert body["skipped_shared"] == [{"depotid": 300, "shared_with": [730]}]

    response = client.post("/v1/prefill/cached", headers=AUTH)
    assert response.status_code == 202, response.text
    appids = [entry["appid"] for entry in response.json()]

    assert 440 not in appids
    assert appids == [730]


# --------------------------------------------------------------------------
# Dedupe -- reuses jobs.enqueue_prefill, so this is the SAME rule
# POST /v1/prefill has, not a second implementation.
# --------------------------------------------------------------------------


def test_a_second_call_dedupes_against_the_still_queued_job(client: TestClient) -> None:
    _seed_cached_app(client, appid=440, depotid=441)

    first = client.post("/v1/prefill/cached", headers=AUTH).json()
    second = client.post("/v1/prefill/cached", headers=AUTH).json()

    assert first[0]["deduplicated"] is False
    assert second[0]["job_id"] == first[0]["job_id"]
    assert second[0]["deduplicated"] is True
    # No second job was stacked on top of the first.
    assert len(client.get("/v1/jobs", headers=AUTH).json()) == 1


def test_a_second_call_dedupes_against_a_running_job(client: TestClient) -> None:
    _seed_cached_app(client, appid=440, depotid=441)

    first = client.post("/v1/prefill/cached", headers=AUTH).json()
    _claim_oldest_job(client)  # -> running, no real worker involved

    second = client.post("/v1/prefill/cached", headers=AUTH).json()

    assert second[0]["job_id"] == first[0]["job_id"]
    assert second[0]["deduplicated"] is True
    assert second[0]["status"] == jobs.STATUS_RUNNING
    assert len(client.get("/v1/jobs", headers=AUTH).json()) == 1


def test_a_second_call_dedupes_against_a_paused_job(client: TestClient) -> None:
    """ACTIVE_STATUSES includes 'paused' (WP 3.12) -- enqueue_prefill dedupes
    against it exactly like 'queued'/'running', and this route inherits that
    for free by reusing enqueue_prefill. Pinned at the route level (in
    addition to jobs.py's own tests) so the README's "same dedupe" claim is
    self-verifying here too, not only one layer down."""
    _seed_cached_app(client, appid=440, depotid=441)

    first = client.post("/v1/prefill/cached", headers=AUTH).json()
    _claim_oldest_job(client)  # -> running
    conn = _open_conn(client)
    try:
        parked = jobs.park_paused(conn, int(first[0]["job_id"]), "test-paused")
        assert parked is not None and parked["status"] == jobs.STATUS_PAUSED
    finally:
        conn.close()

    second = client.post("/v1/prefill/cached", headers=AUTH).json()

    assert second[0]["job_id"] == first[0]["job_id"]
    assert second[0]["deduplicated"] is True
    assert second[0]["status"] == jobs.STATUS_PAUSED
    assert len(client.get("/v1/jobs", headers=AUTH).json()) == 1


def test_an_impatient_double_tap_across_two_cached_apps_converges_on_one_job_each(
    client: TestClient,
) -> None:
    _seed_cached_app(client, appid=440, depotid=441)
    _seed_cached_app(client, appid=730, depotid=731)

    client.post("/v1/prefill/cached", headers=AUTH)
    second = client.post("/v1/prefill/cached", headers=AUTH).json()

    assert all(entry["deduplicated"] is True for entry in second)
    assert len(client.get("/v1/jobs", headers=AUTH).json()) == 2


# --------------------------------------------------------------------------
# Guardrail: the WP 3.11 miss-trigger cooldown is bypassed, structurally
# --------------------------------------------------------------------------


def test_cached_prefill_bypasses_the_miss_trigger_cooldown(client: TestClient) -> None:
    """WP 3.11's per-app cooldown (VAULT_MISS_TRIGGER_COOLDOWN_MINUTES,
    default 60) exists to stop an unattended, flapping cache MISS signal from
    re-triggering the same app over and over. A human pressing "check & update
    now" is a deliberate one-off ask, not a flapping signal, and this route
    must queue a real job regardless of that cooldown.

    The bypass is structural: this route calls jobs.enqueue_prefill directly
    and never imports event_sweep or consults miss_trigger_state at all (see
    event_sweep.in_cooldown, the ONLY reader of that table). This test seeds
    the app as freshly cooldown-triggered -- if a future change added a
    cooldown check to the route (the "silent no-op" regression the work
    package explicitly warns about), the assertions below would fail: no
    fresh job would appear, and deduplicated would never even get a first
    'False' to report.
    """
    _seed_cached_app(client, appid=440, depotid=441)

    conn = _open_conn(client)
    try:
        # Records last_triggered_at = now, i.e. squarely inside ANY positive
        # cooldown window (the default is 60 minutes) -- if this route
        # consulted event_sweep.in_cooldown the way run_miss_trigger does,
        # appid 440 would be skipped.
        event_sweep.record_trigger(conn, 440, jobs.utcnow_iso())
    finally:
        conn.close()

    response = client.post("/v1/prefill/cached", headers=AUTH)
    assert response.status_code == 202
    body = response.json()

    assert [entry["appid"] for entry in body] == [440]
    assert body[0]["deduplicated"] is False
    assert body[0]["status"] == jobs.STATUS_QUEUED
    assert len(client.get("/v1/jobs", headers=AUTH).json()) == 1


# --------------------------------------------------------------------------
# Scale: the selection query must not be a per-app loop
# --------------------------------------------------------------------------


def test_selecting_cached_apps_is_not_a_per_app_query(client: TestClient) -> None:
    """A homelab library-sized selection (hundreds of apps) must cost ONE
    SQL statement, not one per app or one per depot -- otherwise "select
    every cached app" quietly becomes an O(N) query storm as a library grows.

    Exercises `_select_appids_with_cache_content` directly (rather than
    through the HTTP route) so the statement count measured is exactly the
    selection step, not the enqueue loop after it -- enqueueing N jobs is
    legitimately N writes, and mixing that into the same count would hide a
    regression in the SELECTION half specifically.
    """
    from vault_api.mapping import upsert_mapping

    conn = _open_conn(client)
    try:
        app_count = 500
        depot_bytes: dict[int, int] = {}
        for i in range(app_count):
            appid = 100_000 + i
            depotid = 900_000 + i
            upsert_mapping(conn, depotid=depotid, appid=appid, name=None)
            depot_bytes[depotid] = 4096

        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            selected = _select_appids_with_cache_content(conn, depot_bytes)
        finally:
            conn.set_trace_callback(None)

        assert selected == list(range(100_000, 100_000 + app_count))
        # The whole point: a constant number of statements, regardless of
        # app_count: the selection itself plus the not-owned lookup (WP
        # NOT-OWNED-1), never one per app.
        assert len(statements) == 2, statements
    finally:
        conn.close()


def test_cached_prefill_route_handles_a_large_library_end_to_end(
    client: TestClient,
) -> None:
    """Sanity check at the HTTP layer (not a statement-count pin -- that is
    the unit test above): a large cached library still gets one job per app
    and a well-formed response, all in one request."""
    app_count = 150
    for i in range(app_count):
        _seed_cached_app(client, appid=200_000 + i, depotid=800_000 + i)

    response = client.post("/v1/prefill/cached", headers=AUTH)
    assert response.status_code == 202
    body = response.json()

    assert len(body) == app_count
    assert {entry["appid"] for entry in body} == {
        200_000 + i for i in range(app_count)
    }
    assert all(entry["deduplicated"] is False for entry in body)
    # GET /v1/jobs defaults to limit=20 -- ask for enough to see every job
    # this request just queued (le=200, comfortably above app_count here).
    listed = client.get("/v1/jobs?limit=200", headers=AUTH).json()
    assert len(listed) == app_count
