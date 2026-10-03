from __future__ import annotations

import sqlite3

import pytest

from vault_api.db import SCHEMA_VERSION, get_connection, init_db

EXPECTED_TABLES = {
    "schema_version",
    "apps",
    "depot_app_map",
    "jobs",
    "agent_reports",
    "depot_manifests",
    "schedule_state",
    # v9 (WP 3.11, ADR-0008): the cache-event sweep's own storage. Added here
    # during the WP 3.9 rebase — WP 3.11 shipped them in db.py without listing
    # them, and this set is only meaningful if it is exhaustive (see
    # test_expected_tables_lists_every_table_the_ddl_creates below).
    "event_sweep_state",
    "client_cache_stats",
    "depot_miss_stats",
    "miss_trigger_state",
    # v10 (WP 3.9): the opt-in manifest oracle's own storage. Present on every
    # install, empty unless the oracle is enabled AND has been asked something.
    "oracle_app_state",
    "oracle_branch_manifests",
    # v11 (WP 3.13): the webhook feature's bypass-transition bookkeeping.
    "client_bypass_state",
    # v12 (WP 4a.6r): the opt-in Steam Web API relay's own key storage.
    "steam_relay_key",
    # v13 (settings-API work package, ADR-0009): persisted overrides for a
    # small, named set of env-backed settings.
    "settings",
    # v16 (WP VER-2): vault-runner presence for GET /v1/about.
    "runner_presence",
}


def test_init_db_creates_expected_tables(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        table_names = {row["name"] for row in rows}
    finally:
        conn.close()

    assert EXPECTED_TABLES.issubset(table_names)


def test_expected_tables_lists_every_table_the_ddl_creates() -> None:
    """``EXPECTED_TABLES`` must be EXHAUSTIVE, not merely a subset.

    The assertion above is ``issubset``, so a table added to ``_DDL`` without
    being listed here passes silently — which is exactly what happened: WP
    3.11 shipped four new tables and this set did not learn about them until a
    rebase noticed by hand. Derived from ``_DDL`` itself rather than from
    ``sqlite_master``, because the latter also holds ``sqlite_sequence`` (an
    ``AUTOINCREMENT`` artifact, not something this project declares).
    """
    import re

    from vault_api.db import _DDL

    declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", _DDL))
    assert declared == EXPECTED_TABLES


def test_init_db_is_idempotent(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")

    init_db(db_path)
    init_db(db_path)  # must not raise, must not duplicate schema_version rows

    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0]["version"] == SCHEMA_VERSION


def test_init_db_creates_parent_directory(tmp_path) -> None:
    nested_path = str(tmp_path / "nested" / "dir" / "vault.db")
    init_db(nested_path)

    conn = sqlite3.connect(nested_path)
    conn.close()


def test_depot_app_map_has_appid_index(tmp_path) -> None:
    # plan §4's main lookup direction is appid -> depots; the PK alone
    # (depotid, appid) doesn't serve that direction efficiently.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'depot_app_map'"
        ).fetchall()
        index_names = {row["name"] for row in rows}
    finally:
        conn.close()

    assert "idx_depot_app_map_appid" in index_names


def test_agent_reports_is_one_row_per_report_snapshot(tmp_path) -> None:
    # ADR-0002: the agent reports its FULL installed-app-ID list every time;
    # vault-api diffs the two most recent rows per client_id. That means one
    # row per report (client_id, reported_at, appids-as-JSON), not one row
    # per (client, app) pair.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_reports)")}
        # source_addr joined in schema v9 (WP 3.11, ADR-0008): the address the
        # report arrived FROM, which is the only key correlating a client_id
        # with the event log's addresses.
        # agent_version / report_interval_seconds joined in schema v17 (WP
        # AGENT-FEAT-1): what the agent says about itself in each report.
        assert columns == {
            "client_id",
            "reported_at",
            "appids",
            "source_addr",
            "agent_version",
            "report_interval_seconds",
        }

        conn.execute(
            "INSERT INTO agent_reports (client_id, reported_at, appids) VALUES (?, ?, ?)",
            ("pc-1", "2026-08-05T10:00:00Z", "[440, 730]"),
        )
        conn.commit()

        row = conn.execute(
            "SELECT client_id, reported_at, appids FROM agent_reports WHERE client_id = 'pc-1'"
        ).fetchone()
        assert row["appids"] == "[440, 730]"
    finally:
        conn.close()


def test_agent_reports_has_client_time_index(tmp_path) -> None:
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'agent_reports'"
        ).fetchall()
        index_names = {row["name"] for row in rows}
    finally:
        conn.close()

    assert "idx_agent_reports_client_time" in index_names


def test_connection_pragmas_are_applied(tmp_path) -> None:
    # S2: WAL mode + a busy_timeout so HTTP reads don't fail immediately
    # while a background job-queue writer (WP 1.4+) holds a write lock.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (journal_mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        (busy_timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
    finally:
        conn.close()

    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000


def test_jobs_has_the_queue_indexes(tmp_path) -> None:
    # Schema v2 (WP 1.4): the worker polls "oldest queued job" on every tick
    # and the enqueue path checks "queued/running job for this appid?" on every
    # POST /v1/prefill — both would otherwise scan the whole jobs table.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs'"
        ).fetchall()
        index_names = {row["name"] for row in rows}
    finally:
        conn.close()

    assert {"idx_jobs_status_id", "idx_jobs_appid_status"} <= index_names


def test_init_db_upgrades_a_v1_database_in_place(tmp_path) -> None:
    """v1 -> v2 is additive, so running the current DDL + bumping the marker is
    the whole migration. Simulate a v1 file by dropping the new indexes and
    resetting the recorded version."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP INDEX idx_jobs_status_id")
        conn.execute("DROP INDEX idx_jobs_appid_status")
        conn.execute("UPDATE schema_version SET version = 1")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        index_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs'"
            )
        }
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert {"idx_jobs_status_id", "idx_jobs_appid_status"} <= index_names


def test_depot_manifests_has_the_expected_columns_and_depotid_index(tmp_path) -> None:
    # Schema v3 (WP 3.2): last-known manifest state per (appid, depotid);
    # GC (ADR-0007, later) needs "every app's current manifest for a given
    # depot", which the PK (appid, depotid) alone doesn't serve efficiently.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(depot_manifests)")}
        index_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'depot_manifests'"
            )
        }
    finally:
        conn.close()

    assert columns == {
        "appid",
        "containing_appid",
        "depotid",
        "manifestid",
        "chunk_count",
        "total_bytes",
        "recorded_at",
        "source",
        # v14 (WP 4h.1): change-frequency bookkeeping, see
        # vault_api/depot_manifests.py's "Change frequency" section.
        "first_seen_at",
        "manifest_changed_at",
        "observation_count",
    }
    assert "idx_depot_manifests_depotid" in index_names


def test_depot_manifests_primary_key_is_appid_depotid(tmp_path) -> None:
    """Latest-per-(appid, depotid) with replace semantics (ADR-0006 decision
    3), not a history table -- a second INSERT for the same pair must be
    rejected by the PK unless it explicitly replaces (see
    vault_api/depot_manifests.py's ON CONFLICT upsert)."""
    import sqlite3

    import pytest

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO depot_manifests
                (appid, containing_appid, depotid, manifestid, chunk_count,
                 total_bytes, recorded_at, source,
                 first_seen_at, manifest_changed_at, observation_count)
            VALUES (440, NULL, 441, '123', 1, 100, '2026-08-06T00:00:00Z', 'prefill_bin',
                    '2026-08-06T00:00:00Z', '2026-08-06T00:00:00Z', 1)
            """
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO depot_manifests
                    (appid, containing_appid, depotid, manifestid, chunk_count,
                     total_bytes, recorded_at, source,
                     first_seen_at, manifest_changed_at, observation_count)
                VALUES (440, NULL, 441, '999', 2, 200, '2026-08-06T01:00:00Z', 'prefill_bin',
                        '2026-08-06T01:00:00Z', '2026-08-06T01:00:00Z', 1)
                """
            )
    finally:
        conn.close()


def test_init_db_upgrades_a_v2_database_to_v3_in_place(tmp_path) -> None:
    """v2 -> v3 is additive (one new table + its index), so running the
    current DDL and bumping the marker is the whole migration. Simulate a v2
    file by dropping depot_manifests and resetting the recorded version."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE depot_manifests")
        conn.execute("UPDATE schema_version SET version = 2")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        table_names = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert "depot_manifests" in table_names


def test_init_db_upgrades_a_v3_database_to_v4_in_place(tmp_path) -> None:
    """v3 -> v4 (WP 3.3) adds jobs.updated/up_to_date/summary_parse_ok. Unlike
    every earlier bump, `CREATE TABLE IF NOT EXISTS jobs` is a no-op against
    an existing v1-v3 jobs table (it already exists, just without these
    columns) -- this is the one migration that needs an explicit ALTER,
    which is what this test actually pins. Simulate a v3 file by dropping the
    three new columns and resetting the recorded version; a pre-existing job
    row proves the migration doesn't lose data."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        # Recreate `jobs` in its exact pre-v4 shape (no updated/up_to_date/
        # summary_parse_ok) rather than ALTER ... DROP COLUMN: SQLite's DROP
        # COLUMN rewrites the table's stored CREATE TABLE text and trips over
        # the multi-line `--` comment block _DDL has right before those
        # columns ("incomplete input") -- a real pre-v4 database was never
        # produced by dropping columns from the current DDL, so this isn't
        # a gap in the migration itself, just the right way to simulate one.
        conn.execute("DROP TABLE jobs")
        conn.execute(
            """
            CREATE TABLE jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                appid       INTEGER NOT NULL,
                type        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'idle',
                created_at  TEXT NOT NULL,
                started_at  TEXT,
                finished_at TEXT,
                log_excerpt TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO jobs (id, appid, type, status, created_at) "
            "VALUES (1, 440, 'prefill', 'done', '2026-08-06T00:00:00Z')"
        )
        conn.execute("UPDATE schema_version SET version = 3")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        row = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert {"updated", "up_to_date", "summary_parse_ok"} <= columns
    # The pre-existing row survived the ALTER with the new columns NULL —
    # never a silently-guessed 0 (ADR-0006 decision 1's whole point).
    assert row["appid"] == 440
    assert row["updated"] is None
    assert row["up_to_date"] is None
    assert row["summary_parse_ok"] is None


def test_init_db_upgrade_to_v4_is_idempotent_if_called_twice(tmp_path) -> None:
    """Calling init_db twice against the same pre-v4 file must not raise
    'duplicate column name' -- _add_missing_job_columns guards per-column,
    not just per-version."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("ALTER TABLE jobs DROP COLUMN updated")
        conn.execute("UPDATE schema_version SET version = 3")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # must not raise

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    finally:
        conn.close()
    assert "updated" in columns


def test_jobs_has_the_summary_columns(tmp_path) -> None:
    # Schema v4 (WP 3.3): SteamPrefill's own summary-table counters
    # (ADR-0006 decision 1), stored per job.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    finally:
        conn.close()

    assert {"updated", "up_to_date", "summary_parse_ok"} <= columns


def test_apps_has_the_needs_force_column(tmp_path) -> None:
    # Schema v5 (WP 3.4): ADR-0006 decision 2's per-app flag deciding whether
    # the next prefill must run with --force.
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
    finally:
        conn.close()

    assert "needs_force" in columns


def test_init_db_upgrades_a_pre_v6_database_by_adding_schedule_state(tmp_path) -> None:
    """v5 -> v6 (WP 3.5) adds a brand-new TABLE, so plain CREATE TABLE IF NOT
    EXISTS is the whole migration — no ALTER step, and existing data untouched.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("INSERT INTO apps (appid, status) VALUES (440, 'done')")
        conn.execute("DROP TABLE schedule_state")
        conn.execute("UPDATE schema_version SET version = 5")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        # An upgraded database reads as "never swept" — the empty table.
        (sweeps,) = conn.execute("SELECT COUNT(*) FROM schedule_state").fetchone()
        (appid,) = conn.execute("SELECT appid FROM apps").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert "schedule_state" in tables
    assert sweeps == 0
    assert appid == 440


def test_init_db_upgrades_a_v9_database_to_v10_in_place(tmp_path) -> None:
    """v9 -> v10 (WP 3.9) adds two brand-new TABLEs plus one index, so —
    like v5 -> v6 above, and unlike v4/v5/v7/v8/v9's added columns — plain
    ``CREATE ... IF NOT EXISTS`` is the whole migration: no ALTER step.

    An upgraded database must come out reading as "the oracle has never said
    anything" (two empty tables), which is exactly the state a fresh install
    with the oracle off is in — and existing rows must be untouched.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("INSERT INTO apps (appid, status) VALUES (440, 'done')")
        conn.execute("DROP TABLE oracle_branch_manifests")  # drops its index too
        conn.execute("DROP TABLE oracle_app_state")
        conn.execute("UPDATE schema_version SET version = 9")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        (states,) = conn.execute("SELECT COUNT(*) FROM oracle_app_state").fetchone()
        (branches,) = conn.execute(
            "SELECT COUNT(*) FROM oracle_branch_manifests"
        ).fetchone()
        (appid,) = conn.execute("SELECT appid FROM apps").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert {"oracle_app_state", "oracle_branch_manifests"} <= tables
    assert "idx_oracle_branch_manifests_depotid" in indexes
    assert (states, branches) == (0, 0)
    assert appid == 440


def test_init_db_upgrade_to_v10_is_idempotent_if_called_twice(tmp_path) -> None:
    """Same guarantee the other bumps carry: running the upgrade again against
    an already-upgraded file must not raise and must not duplicate anything."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE oracle_branch_manifests")
        conn.execute("DROP TABLE oracle_app_state")
        conn.execute("UPDATE schema_version SET version = 9")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # must not raise

    conn = get_connection(db_path)
    try:
        versions = conn.execute("SELECT version FROM schema_version").fetchall()
        oracle_indexes = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
                ("idx_oracle_branch_manifests_depotid",),
            )
        ]
    finally:
        conn.close()

    assert len(versions) == 1
    assert versions[0]["version"] == SCHEMA_VERSION
    assert oracle_indexes == ["idx_oracle_branch_manifests_depotid"]


def test_init_db_upgrades_a_v11_database_to_v12_in_place(tmp_path) -> None:
    """v11 -> v12 (WP 4a.6r) adds one brand-new TABLE (``steam_relay_key``),
    so — like v6/v9/v10's additions — plain ``CREATE ... IF NOT EXISTS`` is
    the whole migration: no ALTER step. An upgraded database must come out
    reading as "no relay key configured", the same state a fresh install with
    no key set is in, and existing rows must be untouched.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("INSERT INTO apps (appid, status) VALUES (440, 'done')")
        conn.execute("DROP TABLE steam_relay_key")
        conn.execute("UPDATE schema_version SET version = 11")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        (key_rows,) = conn.execute("SELECT COUNT(*) FROM steam_relay_key").fetchone()
        (appid,) = conn.execute("SELECT appid FROM apps").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert "steam_relay_key" in tables
    assert key_rows == 0
    assert appid == 440


def test_init_db_upgrade_to_v12_is_idempotent_if_called_twice(tmp_path) -> None:
    """Same guarantee the other bumps carry: running the upgrade again against
    an already-upgraded file must not raise and must not duplicate anything."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE steam_relay_key")
        conn.execute("UPDATE schema_version SET version = 11")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # must not raise

    conn = get_connection(db_path)
    try:
        versions = conn.execute("SELECT version FROM schema_version").fetchall()
    finally:
        conn.close()

    assert len(versions) == 1
    assert versions[0]["version"] == SCHEMA_VERSION


def test_init_db_upgrades_a_v13_database_to_v14_in_place(tmp_path) -> None:
    """v13 -> v14 (WP 4h.1) adds three ``depot_manifests`` columns
    (``first_seen_at``, ``manifest_changed_at``, ``observation_count``) --
    an EXISTING v3-v13 ``depot_manifests`` table lacks them, so unlike the
    brand-new-table bumps (v6/v9/v10/v11/v12) this one needs an explicit
    ``ALTER`` step (the v4/v5/v9 situation), PLUS a backfill: simulate a
    pre-v14 row by inserting one WITHOUT the three columns (dropping them
    first, matching how a real pre-v14 ``depot_manifests`` table looks), then
    assert the upgrade backfills first_seen_at/manifest_changed_at from that
    row's own recorded_at and observation_count to 1 -- the conservative
    defaults, never a guessed value invented from nothing."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("ALTER TABLE depot_manifests DROP COLUMN first_seen_at")
        conn.execute("ALTER TABLE depot_manifests DROP COLUMN manifest_changed_at")
        conn.execute("ALTER TABLE depot_manifests DROP COLUMN observation_count")
        conn.execute(
            """
            INSERT INTO depot_manifests
                (appid, containing_appid, depotid, manifestid, chunk_count,
                 total_bytes, recorded_at, source)
            VALUES (440, NULL, 441, '123', 1, 100, '2026-08-06T00:00:00Z', 'prefill_bin')
            """
        )
        conn.execute("UPDATE schema_version SET version = 13")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(depot_manifests)")}
        row = conn.execute(
            "SELECT * FROM depot_manifests WHERE appid = 440 AND depotid = 441"
        ).fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert {"first_seen_at", "manifest_changed_at", "observation_count"} <= columns
    # Backfilled from the row's own recorded_at -- never guessed as something
    # earlier, and never left NULL.
    assert row["first_seen_at"] == "2026-08-06T00:00:00Z"
    assert row["manifest_changed_at"] == "2026-08-06T00:00:00Z"
    assert row["observation_count"] == 1


def test_init_db_upgrade_to_v14_is_idempotent_if_called_twice(tmp_path) -> None:
    """Same guarantee the other bumps carry: running the upgrade again against
    an already-upgraded file must not raise (the per-column ADD COLUMN guard)
    and must not re-run the backfill UPDATE destructively (it is itself
    idempotent, gated on ``WHERE ... IS NULL``)."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("ALTER TABLE depot_manifests DROP COLUMN first_seen_at")
        conn.execute("ALTER TABLE depot_manifests DROP COLUMN manifest_changed_at")
        conn.execute("ALTER TABLE depot_manifests DROP COLUMN observation_count")
        conn.execute("UPDATE schema_version SET version = 13")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # must not raise

    conn = get_connection(db_path)
    try:
        versions = conn.execute("SELECT version FROM schema_version").fetchall()
    finally:
        conn.close()

    assert len(versions) == 1
    assert versions[0]["version"] == SCHEMA_VERSION


def test_init_db_upgrade_to_v14_skips_the_alter_when_the_table_is_entirely_absent(
    tmp_path,
) -> None:
    """A v1/v2 database (before v3 introduced ``depot_manifests`` at all) has
    no such table to ALTER -- ``PRAGMA table_info`` on a missing table
    quietly returns nothing rather than raising, so the guard must be an
    explicit existence check, not just the per-column loop. Regression pin
    for exactly the failure this migration step's first version hit: dropping
    the whole table to simulate "never existed" and upgrading from v2 must
    still succeed, with a freshly created, fully-columned table at the end."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE depot_manifests")
        conn.execute("UPDATE schema_version SET version = 2")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)  # must not raise "no such table: depot_manifests"

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(depot_manifests)")}
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert {"first_seen_at", "manifest_changed_at", "observation_count"} <= columns


def test_init_db_upgrades_a_v12_database_to_v13_in_place(tmp_path) -> None:
    """v12 -> v13 (settings-API work package, ADR-0009) adds one brand-new
    TABLE (``settings``), so — like v6/v9/v10/v11/v12's additions — plain
    ``CREATE ... IF NOT EXISTS`` is the whole migration: no ALTER step. An
    upgraded database must come out with an EMPTY settings table (no
    overrides — same state a fresh install with no PATCH ever sent is in)
    and existing rows in other tables must be untouched.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("INSERT INTO apps (appid, status) VALUES (440, 'done')")
        conn.execute("DROP TABLE settings")
        conn.execute("UPDATE schema_version SET version = 12")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        (settings_rows,) = conn.execute("SELECT COUNT(*) FROM settings").fetchone()
        (appid,) = conn.execute("SELECT appid FROM apps").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert "settings" in tables
    assert settings_rows == 0
    assert appid == 440


def test_init_db_upgrade_to_v13_is_idempotent_if_called_twice(tmp_path) -> None:
    """Same guarantee the other bumps carry: running the upgrade again against
    an already-upgraded file must not raise and must not duplicate anything."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE settings")
        conn.execute("UPDATE schema_version SET version = 12")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # must not raise

    conn = get_connection(db_path)
    try:
        versions = conn.execute("SELECT version FROM schema_version").fetchall()
    finally:
        conn.close()

    assert len(versions) == 1
    assert versions[0]["version"] == SCHEMA_VERSION


def test_settings_table_upserts_by_key(tmp_path) -> None:
    """Basic shape check: ``key`` is the primary key, so a second write to the
    same key replaces rather than duplicates (``settings_store.set_override``
    relies on this via ``ON CONFLICT(key) DO UPDATE``)."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("auto_gc", "dry-run", "2026-08-10T00:00:00Z"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("auto_gc", "execute", "2026-08-10T01:00:00Z"),
            )
    finally:
        conn.close()


def test_init_db_upgrades_a_pre_v7_database_by_adding_gc_execute(tmp_path) -> None:
    """v6 -> v7 (WP 3.8) adds ``jobs.gc_execute`` — ADR-0007's dry-run/execute
    bit. Same ALTER-guarded shape as v4, reusing the same per-column step, and
    an existing job row must come out with the column NULL: a pre-v7 job was
    never a GC job, and "not applicable" is not "dry run".
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        # Recreated in its exact pre-v7 shape rather than ALTER ... DROP
        # COLUMN, for the same reason the pre-v4 test above does it: DROP
        # COLUMN rewrites the table's stored CREATE TABLE text and trips over
        # _DDL's multi-line `--` comment block ("incomplete input").
        conn.execute("DROP TABLE jobs")
        conn.execute(
            """
            CREATE TABLE jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                appid       INTEGER NOT NULL,
                type        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'idle',
                created_at  TEXT NOT NULL,
                started_at  TEXT,
                finished_at TEXT,
                log_excerpt TEXT,
                updated           INTEGER,
                up_to_date        INTEGER,
                summary_parse_ok  INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO jobs (id, appid, type, status, created_at) "
            "VALUES (1, 440, 'prefill', 'done', '2026-08-09T00:00:00Z')"
        )
        conn.execute("UPDATE schema_version SET version = 6")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # idempotent: must not raise 'duplicate column name'

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        row = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert "gc_execute" in columns
    assert row["appid"] == 440
    assert row["gc_execute"] is None


def test_init_db_upgrades_a_pre_v8_database_by_adding_the_job_control_columns(
    tmp_path,
) -> None:
    """v7 -> v8 (WP 3.12) adds ``jobs.paused_at`` and ``jobs.stop_request``.

    Two things this pins beyond "the columns exist":

    - an existing job row comes out with both NULL — a job from before job
      control was never paused and has no pending request, and neither may be
      guessed at;
    - the columns are created as **TEXT**, not INTEGER. The per-column ALTER
      step used to hardcode ``INTEGER`` for everything, which would have given
      an upgraded database a different column type from a fresh one built by
      ``_DDL`` while both reported ``schema_version = 8``.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        # Recreated in its exact pre-v8 shape (same reason as the pre-v7 test
        # above: DROP COLUMN rewrites the stored CREATE TABLE text).
        conn.execute("DROP TABLE jobs")
        conn.execute(
            """
            CREATE TABLE jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                appid       INTEGER NOT NULL,
                type        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'idle',
                created_at  TEXT NOT NULL,
                started_at  TEXT,
                finished_at TEXT,
                log_excerpt TEXT,
                updated           INTEGER,
                up_to_date        INTEGER,
                summary_parse_ok  INTEGER,
                gc_execute        INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO jobs (id, appid, type, status, created_at) "
            "VALUES (1, 440, 'prefill', 'done', '2026-08-09T00:00:00Z')"
        )
        conn.execute("UPDATE schema_version SET version = 7")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # idempotent: must not raise 'duplicate column name'

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        types = {
            row["name"]: row["type"]
            for row in conn.execute("PRAGMA table_info(jobs)")
        }
        row = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
    finally:
        conn.close()

    # The pre-v8 file is brought all the way to the CURRENT version, whatever
    # that is — this test is about the two job-control columns arriving with
    # the right type, not about pinning the version number (v9 added the event
    # sweep on top, WP 3.11; v10 the oracle tables, WP 3.9).
    assert version == SCHEMA_VERSION
    assert types["paused_at"] == "TEXT"
    assert types["stop_request"] == "TEXT"
    assert row["paused_at"] is None
    assert row["stop_request"] is None


def test_a_fresh_database_and_an_upgraded_one_agree_on_the_jobs_columns(
    tmp_path,
) -> None:
    """The migration and ``_DDL`` must never drift apart (WP 3.12).

    A v1 database taken all the way up must end with byte-identical column
    names AND types to a database created from scratch — otherwise
    ``schema_version`` stops meaning "this is the shape of the tables".
    """
    fresh_path = str(tmp_path / "fresh.db")
    init_db(fresh_path)

    old_path = str(tmp_path / "old.db")
    init_db(old_path)
    conn = get_connection(old_path)
    try:
        conn.execute("DROP TABLE jobs")
        conn.execute(
            """
            CREATE TABLE jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                appid       INTEGER NOT NULL,
                type        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'idle',
                created_at  TEXT NOT NULL,
                started_at  TEXT,
                finished_at TEXT,
                log_excerpt TEXT
            )
            """
        )
        conn.execute("UPDATE schema_version SET version = 1")
        conn.commit()
    finally:
        conn.close()
    init_db(old_path)

    def job_columns(path: str) -> list[tuple[str, str]]:
        conn = get_connection(path)
        try:
            return [
                (row["name"], row["type"])
                for row in conn.execute("PRAGMA table_info(jobs)")
            ]
        finally:
            conn.close()

    assert job_columns(old_path) == job_columns(fresh_path)


def test_schedule_state_rejects_a_second_row(tmp_path) -> None:
    """CHECK (id = 1): "the single row" is enforced by the schema, not by
    convention — a second row would make "when did the last sweep run"
    ambiguous."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO schedule_state (id, last_sweep_at) "
            "VALUES (1, '2026-08-06T10:00:00Z')"
        )
        conn.commit()
        try:
            conn.execute(
                "INSERT INTO schedule_state (id, last_sweep_at) "
                "VALUES (2, '2026-08-06T11:00:00Z')"
            )
            raise AssertionError("a second schedule_state row was accepted")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


def test_a_fresh_app_row_defaults_needs_force_to_one(tmp_path) -> None:
    # A never-filled app has never had a chance to prove it's current, so its
    # first prefill must be forced (ADR-0006 decision 2).
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("INSERT INTO apps (appid, status) VALUES (440, 'idle')")
        conn.commit()
        row = conn.execute(
            "SELECT needs_force FROM apps WHERE appid = 440"
        ).fetchone()
    finally:
        conn.close()

    assert row["needs_force"] == 1


def test_init_db_upgrades_a_v4_database_to_v5_in_place(tmp_path) -> None:
    """v4 -> v5 (WP 3.4) adds apps.needs_force. Same situation as the v3->v4
    jobs bump: `CREATE TABLE IF NOT EXISTS apps` is a no-op against an
    existing v1-v4 apps table (it already exists, just without this column),
    so this is the one migration that needs an explicit ALTER — pinned here.
    Simulate a v4 file by dropping the column and resetting the recorded
    version; a pre-existing app row proves the migration doesn't lose data."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        # Recreate `apps` in its exact pre-v5 shape (no needs_force) rather
        # than ALTER ... DROP COLUMN, matching the v3->v4 test's own
        # reasoning for jobs (DROP COLUMN trips over _DDL's comment block).
        conn.execute("DROP TABLE apps")
        conn.execute(
            """
            CREATE TABLE apps (
                appid               INTEGER PRIMARY KEY,
                name                TEXT,
                status              TEXT NOT NULL DEFAULT 'idle',
                last_prefill_at     TEXT,
                last_manifest_check TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO apps (appid, name, status, last_prefill_at) "
            "VALUES (440, 'Team Fortress 2', 'done', '2026-08-06T00:00:00Z')"
        )
        conn.execute("UPDATE schema_version SET version = 4")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
        row = conn.execute("SELECT * FROM apps WHERE appid = 440").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    assert "needs_force" in columns
    # The pre-existing row survived the ALTER, and — because the column has a
    # constant DEFAULT 1, which SQLite applies retroactively to existing rows
    # for ALTER TABLE ADD COLUMN — it is populated, not NULL.
    assert row["name"] == "Team Fortress 2"
    assert row["needs_force"] == 1


def test_init_db_upgrade_to_v5_is_idempotent_if_called_twice(tmp_path) -> None:
    """Calling init_db twice against the same pre-v5 file must not raise
    'duplicate column name' -- _add_missing_app_columns guards per-column,
    not just per-version."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("ALTER TABLE apps DROP COLUMN needs_force")
        conn.execute("UPDATE schema_version SET version = 4")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # must not raise

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
    finally:
        conn.close()
    assert "needs_force" in columns


def test_init_db_refuses_a_database_from_a_newer_vault_api(tmp_path) -> None:
    import pytest

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="newer version"):
        init_db(db_path)


def test_client_bypass_state_table_exists(tmp_path) -> None:
    """WP 3.13, schema v10: a brand-new table, additive, no ALTER step needed."""
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(client_bypass_state)")}
    finally:
        conn.close()

    assert columns == {"client_id", "bypass_suspected", "updated_at"}


def test_connections_are_thread_confined_by_default(tmp_path) -> None:
    """WP 1.4: check_same_thread must stay ON.

    Handing a connection to another thread is what produced a native access
    violation in WP 1.3's shared-connection design (see vault_api/deps.py).
    Every connection here is opened and closed in the thread that uses it, so
    the safe default must be in force — a future accidental hand-off has to be
    a loud ProgrammingError, not a crash.
    """
    import threading

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    failures: list[str] = []

    def use_from_another_thread() -> None:
        try:
            conn.execute("SELECT 1").fetchone()
            failures.append("no ProgrammingError was raised")
        except sqlite3.ProgrammingError:
            pass

    thread = threading.Thread(target=use_from_another_thread)
    thread.start()
    thread.join(timeout=10)
    conn.close()

    assert not failures, failures


def test_init_db_upgrades_a_v14_database_to_v15_in_place(tmp_path) -> None:
    """v14 -> v15 (WP S-1, ADR-0012) adds the seven ``jobs.run_*`` columns.

    Recreated in its exact pre-v15 shape (same reason every other ALTER test
    in this file does: DROP COLUMN rewrites the stored CREATE TABLE text, so
    the fixture has to be a hand-written pre-upgrade table, not today's
    ``jobs`` with columns dropped off it).
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE jobs")
        conn.execute(
            """
            CREATE TABLE jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                appid       INTEGER NOT NULL,
                type        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'idle',
                created_at  TEXT NOT NULL,
                started_at  TEXT,
                finished_at TEXT,
                log_excerpt TEXT,
                updated           INTEGER,
                up_to_date        INTEGER,
                summary_parse_ok  INTEGER,
                gc_execute        INTEGER,
                paused_at         TEXT,
                stop_request      TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO jobs (id, appid, type, status, created_at) "
            "VALUES (1, 440, 'prefill', 'done', '2026-08-09T00:00:00Z')"
        )
        conn.execute("UPDATE schema_version SET version = 14")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    init_db(db_path)  # idempotent: must not raise 'duplicate column name'

    conn = get_connection(db_path)
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        types = {row["name"]: row["type"] for row in conn.execute("PRAGMA table_info(jobs)")}
        row = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
    finally:
        conn.close()

    assert version == SCHEMA_VERSION
    run_columns = {
        "run_use_force": "INTEGER",
        "run_before_json": "TEXT",
        "run_claimed_by": "TEXT",
        "run_claimed_at": "TEXT",
        "run_heartbeat_at": "TEXT",
        "run_completed_at": "TEXT",
        "run_result_json": "TEXT",
    }
    for name, expected_type in run_columns.items():
        assert types[name] == expected_type, (name, types[name])
        assert row[name] is None


def test_a_fresh_database_and_a_v14_upgrade_agree_on_the_jobs_columns(tmp_path) -> None:
    """Same drift guard as ``test_a_fresh_database_and_an_upgraded_one_agree_on_the_jobs_columns``
    above, specifically for the v15 (WP S-1) bump."""
    fresh_path = str(tmp_path / "fresh.db")
    init_db(fresh_path)

    old_path = str(tmp_path / "old.db")
    init_db(old_path)
    conn = get_connection(old_path)
    try:
        conn.execute("DROP TABLE jobs")
        conn.execute(
            """
            CREATE TABLE jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                appid       INTEGER NOT NULL,
                type        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'idle',
                created_at  TEXT NOT NULL,
                started_at  TEXT,
                finished_at TEXT,
                log_excerpt TEXT,
                updated           INTEGER,
                up_to_date        INTEGER,
                summary_parse_ok  INTEGER,
                gc_execute        INTEGER,
                paused_at         TEXT,
                stop_request      TEXT
            )
            """
        )
        conn.execute("UPDATE schema_version SET version = 14")
        conn.commit()
    finally:
        conn.close()
    init_db(old_path)

    def job_columns(path: str) -> list[tuple[str, str]]:
        conn = get_connection(path)
        try:
            return [(row["name"], row["type"]) for row in conn.execute("PRAGMA table_info(jobs)")]
        finally:
            conn.close()

    assert job_columns(old_path) == job_columns(fresh_path)
