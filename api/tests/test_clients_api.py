"""WP 2.4: GET /v1/clients — minimal v1 (agent-report side only).

WP AG-1 adds ``DELETE /v1/clients/{client_id}`` — the ghost-row cleanup path
for a client that stopped reporting (e.g. after being renamed, WP AG-0). See
``vault_api.agent_reports.delete_client`` for the full table-by-table and
race-outcome write-up these tests pin.
"""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY
from tests.test_agent_reports import make_client, report
from vault_api.db import get_connection, init_db
from vault_api import agent_reports

AUTH = {"X-Api-Key": TEST_API_KEY}


def test_clients_requires_api_key(client: TestClient) -> None:
    assert client.get("/v1/clients").status_code == 401


def test_no_reports_yet_is_an_empty_list(client: TestClient) -> None:
    assert client.get("/v1/clients", headers=AUTH).json() == []


def test_lists_one_row_per_client_with_the_latest_snapshot_size(
    client: TestClient,
) -> None:
    report(client, "gaming-pc", [440, 730, 570])
    report(client, "steam-deck", [440])
    # A second report for one client must not add a second row — and the
    # app_count must follow the LATEST snapshot, not the first one.
    report(client, "gaming-pc", [440, 730])

    body = client.get("/v1/clients", headers=AUTH).json()

    assert [row["client_id"] for row in body] == ["gaming-pc", "steam-deck"]
    gaming_pc, steam_deck = body
    assert gaming_pc["app_count"] == 2
    assert steam_deck["app_count"] == 1
    # first_seen is the oldest retained report, last_reported_at the newest;
    # both are the second-precision ISO strings the rest of the DB uses.
    for row in body:
        assert row["first_seen"].endswith("Z")
        assert row["last_reported_at"] >= row["first_seen"]
    # WP 3.11 (ADR-0008) added the cache-side fields plan §6 always promised.
    # The WP 2.4 fields are all still here, unchanged and in place — that is
    # the forward-compatibility claim that row shape was chosen for.
    assert set(gaming_pc) == {
        "client_id",
        "first_seen",
        "last_reported_at",
        "app_count",
        "source_addrs",
        "cache_hits",
        "cache_misses",
        "bytes_served",
        "last_seen_in_cache_log",
        "bypass_suspected",
        # WP AGENT-FEAT-1 (schema v17): appended, nothing above moved.
        "agent_version",
        "report_interval_seconds",
        "presence",
        "offline_after",
    }
    # With no event feed configured there is nothing to correlate, and nobody
    # is accused of anything.
    for row in body:
        assert row["cache_hits"] == 0
        assert row["last_seen_in_cache_log"] is None
        assert row["bypass_suspected"] is False


def test_a_client_deleted_between_the_group_by_and_the_lookup_is_silently_skipped(
    client: TestClient, monkeypatch
) -> None:
    """WP AG-1 review round 1, S2: ``DELETE /v1/clients/{client_id}`` made
    ``agent_reports.list_clients``'s ``latest is None`` guard reachable for
    the first time too (a DELETE committing between its GROUP BY query and
    the per-client ``latest_snapshot`` lookup) -- same class as
    ``scheduler.fresh_client_snapshots``'s identical guard. Exercised
    directly rather than relying on a real race."""
    report(client, "gaming-pc", [440])
    report(client, "steam-deck", [730])

    real_latest_snapshot = agent_reports.latest_snapshot

    def vanishing_for_gaming_pc(conn, client_id):
        if client_id == "gaming-pc":
            return None  # simulates DELETE committing right here
        return real_latest_snapshot(conn, client_id)

    monkeypatch.setattr(agent_reports, "latest_snapshot", vanishing_for_gaming_pc)

    body = client.get("/v1/clients", headers=AUTH).json()

    assert [row["client_id"] for row in body] == ["steam-deck"]


def test_an_empty_library_reports_app_count_zero(client: TestClient) -> None:
    report(client, "fresh-deck", [])
    row = client.get("/v1/clients", headers=AUTH).json()[0]
    assert row["app_count"] == 0


def test_first_seen_is_the_oldest_RETAINED_report(tmp_path) -> None:
    """Documented consequence of retention, pinned so it can't drift silently."""
    client, settings = make_client(tmp_path, keep=2)

    conn = get_connection(settings.db_path)
    try:
        # Three reports with hand-set timestamps so the pruning is visible.
        for index, stamp in enumerate(
            ("2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z", "2026-08-03T00:00:00Z")
        ):
            conn.execute(
                "INSERT INTO agent_reports (client_id, reported_at, appids) "
                "VALUES ('gaming-pc', ?, ?)",
                (stamp, f"[{440 + index}]"),
            )
        conn.commit()
        agent_reports.prune_reports(conn, "gaming-pc", keep=2)
        conn.commit()
    finally:
        conn.close()

    row = client.get("/v1/clients", headers=AUTH).json()[0]
    assert row["first_seen"] == "2026-08-02T00:00:00Z", "the 08-01 row was pruned"
    assert row["last_reported_at"] == "2026-08-03T00:00:00Z"
    assert row["app_count"] == 1


def test_app_count_is_null_when_the_latest_snapshot_is_unreadable(tmp_path) -> None:
    client, settings = make_client(tmp_path)
    db_path = settings.db_path
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) "
            "VALUES ('gaming-pc', '2026-08-05T10:00:00Z', 'not json{')"
        )
        conn.commit()
    finally:
        conn.close()

    row = client.get("/v1/clients", headers=AUTH).json()[0]
    assert row["app_count"] is None
    assert row["last_reported_at"] == "2026-08-05T10:00:00Z"


# ---------------------------------------------------------------------------
# DELETE /v1/clients/{client_id} (WP AG-1)
# ---------------------------------------------------------------------------


def test_delete_client_requires_api_key(client: TestClient) -> None:
    response = client.delete("/v1/clients/gaming-pc")
    assert response.status_code == 401


def test_delete_unknown_client_is_404(client: TestClient) -> None:
    response = client.delete("/v1/clients/never-reported", headers=AUTH)
    assert response.status_code == 404


def test_delete_client_removes_it_from_the_list(client: TestClient) -> None:
    report(client, "gaming-pc", [440, 730])
    report(client, "steam-deck", [440])

    response = client.delete("/v1/clients/gaming-pc", headers=AUTH)
    assert response.status_code == 204
    assert response.content == b""

    body = client.get("/v1/clients", headers=AUTH).json()
    assert [row["client_id"] for row in body] == ["steam-deck"], (
        "deleting one client must not touch another client's rows"
    )


def test_deleting_twice_is_404_the_second_time(client: TestClient) -> None:
    """Not idempotent DELETE semantics on purpose — the second call has
    nothing to act on, same shape as the mapping DELETE's repeat-call 404."""
    report(client, "gaming-pc", [440])
    assert client.delete("/v1/clients/gaming-pc", headers=AUTH).status_code == 204
    assert client.delete("/v1/clients/gaming-pc", headers=AUTH).status_code == 404


def test_deleted_client_reappears_cleanly_on_the_next_report(client: TestClient) -> None:
    """Documented "not a ban" property: after DELETE, the SAME client_id
    reporting again is treated as a genuine first report -- a fresh diff
    chain, not an error and not silently merged with the deleted history."""
    report(client, "gaming-pc", [440, 730])
    assert client.delete("/v1/clients/gaming-pc", headers=AUTH).status_code == 204

    # Gone in the meantime.
    assert client.get("/v1/clients", headers=AUTH).json() == []

    response = report(client, "gaming-pc", [570])
    body = response.json()
    assert body["first_report"] is True
    assert body["added"] == [570]
    assert body["removed"] == [], "nothing to diff against -- clean slate"

    row = client.get("/v1/clients", headers=AUTH).json()[0]
    assert row["client_id"] == "gaming-pc"
    assert row["app_count"] == 1


def test_delete_removes_the_bypass_transition_baseline_too(tmp_path) -> None:
    """``client_bypass_state`` (WP 3.13) is the other table keyed on
    client_id -- verify by reading the schema, not by assuming, that DELETE
    actually clears it and does not leave a stale verdict a reappearing
    client would be compared against."""
    client, settings = make_client(tmp_path)
    report(client, "gaming-pc", [440])

    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO client_bypass_state (client_id, bypass_suspected, updated_at) "
            "VALUES ('gaming-pc', 1, '2026-08-01T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()

    assert client.delete("/v1/clients/gaming-pc", headers=AUTH).status_code == 204

    conn = get_connection(settings.db_path)
    try:
        remaining = conn.execute(
            "SELECT * FROM client_bypass_state WHERE client_id = 'gaming-pc'"
        ).fetchall()
    finally:
        conn.close()
    assert remaining == []


def test_delete_does_not_touch_client_cache_stats_which_are_address_keyed(
    tmp_path,
) -> None:
    """``client_cache_stats`` is keyed on ``client_addr``, not ``client_id``
    (schema v9) -- deleting a client must leave address-keyed cache-traffic
    history alone. Pinned so a future "just delete everything client-shaped"
    edit cannot quietly widen the delete's blast radius."""
    client, settings = make_client(tmp_path)
    report(client, "gaming-pc", [440])

    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO client_cache_stats "
            "(client_addr, window_at, requests, hits, bytes_served, last_seen) "
            "VALUES ('10.0.0.5', '2026-08-01T00:00:00Z', 3, 3, 100, '2026-08-01T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()

    assert client.delete("/v1/clients/gaming-pc", headers=AUTH).status_code == 204

    conn = get_connection(settings.db_path)
    try:
        remaining = conn.execute(
            "SELECT * FROM client_cache_stats WHERE client_addr = '10.0.0.5'"
        ).fetchall()
    finally:
        conn.close()
    assert len(remaining) == 1, "address-keyed traffic history must survive the delete"


def test_delete_races_a_concurrent_report_for_the_same_client_id(
    tmp_path_factory,
) -> None:
    """Two writers, same client_id: DELETE and a concurrent
    POST /v1/agent/installed. ``agent_reports.delete_client``'s docstring
    documents exactly two acceptable interleavings -- this drives the real
    HTTP layer with genuine thread concurrency (real ``threading.Thread``s,
    not ``TestClient`` sequential calls) and checks the result is ALWAYS one
    of those two, never a third, partial state (double row, wrong appids,
    orphaned bypass_state). Run several times with a fresh database each
    time (the project's flake-hunt pattern, LEARNINGS.md "Testing
    discipline") since the actual winner of the race is nondeterministic.
    """
    import json

    from vault_api.config import Settings
    from vault_api.main import create_app

    for iteration in range(15):
        tmp_path = tmp_path_factory.mktemp(f"ag1-race-{iteration}")
        settings = Settings(
            vault_api_key=TEST_API_KEY,
            db_path=str(tmp_path / "vault.db"),
            cache_root=str(tmp_path / "cache"),
            log_level="INFO",
        )
        app = create_app(settings)
        seed_client = TestClient(app)
        report(seed_client, "gaming-pc", [440])  # exists before the race starts

        results: dict[str, int] = {}

        # Plain construction, no `with` -- same pattern conftest.py's `client`
        # fixture uses everywhere else in this suite (lifespan startup is not
        # needed for a synchronous DB-backed endpoint call, and starting it
        # twice from two threads would spin up a second scheduler/worker
        # thread pair for no reason relevant to this race).
        delete_client_http = TestClient(app)
        report_client_http = TestClient(app)

        def do_delete() -> None:
            results["delete"] = delete_client_http.delete(
                "/v1/clients/gaming-pc", headers=AUTH
            ).status_code

        def do_report() -> None:
            results["report"] = report_client_http.post(
                "/v1/agent/installed",
                json={"client_id": "gaming-pc", "appids": [730]},
                headers=AUTH,
            ).status_code

        t1 = threading.Thread(target=do_delete)
        t2 = threading.Thread(target=do_report)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not t1.is_alive() and not t2.is_alive(), "a writer deadlocked"
        # The seeded row always exists at race start, so DELETE never 404s
        # regardless of ordering; the report always 200s.
        assert results["delete"] == 204, results
        assert results["report"] == 200, results

        conn = get_connection(settings.db_path)
        try:
            rows = conn.execute(
                "SELECT appids FROM agent_reports WHERE client_id = 'gaming-pc' "
                "ORDER BY rowid"
            ).fetchall()
        finally:
            conn.close()

        # Exactly the two outcomes delete_client's docstring documents --
        # never a third shape.
        if not rows:
            continue  # DELETE committed after the report -- everything gone.
        assert len(rows) == 1, (
            "DELETE-then-report must leave exactly the fresh first report, "
            f"never a merged/duplicated history: {rows}"
        )
        assert json.loads(rows[0]["appids"]) == [730]
