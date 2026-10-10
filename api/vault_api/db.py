"""SQLite schema (v1) and connection helpers — plain sqlite3, no ORM.

Schema is created idempotently: ``init_db`` uses ``CREATE TABLE IF NOT
EXISTS`` and only seeds ``schema_version`` once, so calling it repeatedly
(e.g. on every app startup) is safe and cheap.

See api/README.md for a description of each table's purpose.
"""

from __future__ import annotations

import os
import sqlite3

#: v1 (WP 1.2): initial schema.
#: v2 (WP 1.4): added the two ``jobs`` indexes below. Purely additive
#: (``CREATE INDEX IF NOT EXISTS``), so upgrading a v1 file is just running
#: the current DDL and recording the new version — see ``init_db``.
#: v3 (WP 3.2): added ``depot_manifests`` (ADR-0006 decision 3: last-known
#: manifest state per (appid, depotid), recorded from SteamPrefill's
#: temp-cache filenames) plus its ``depotid`` index. Also purely additive.
#: v4 (WP 3.3): added ``jobs.updated``, ``jobs.up_to_date``,
#: ``jobs.summary_parse_ok`` — SteamPrefill's own summary-table counters
#: (ADR-0006 decision 1), stored per job so the API can expose them and job
#: outcome honesty (worker.py) can be audited after the fact. Additive, but
#: NOT expressible as ``CREATE TABLE IF NOT EXISTS`` alone: that statement is
#: a no-op against an *existing* ``jobs`` table from v1-v3, which lacks these
#: columns, so ``init_db`` runs an explicit ``ALTER TABLE ... ADD COLUMN``
#: step for pre-v4 databases (see ``_add_missing_job_columns``) — a fresh
#: install gets them straight from ``_DDL``'s ``CREATE TABLE``.
#: v5 (WP 3.4): added ``apps.needs_force`` — ADR-0006 decision 2's per-app
#: flag deciding whether the next prefill for this app must run with
#: ``--force``. ``DEFAULT 1`` on the column itself encodes "a fresh app row
#: has never been filled, so its first run needs --force" without any
#: application code having to special-case a never-seen app. Same
#: not-expressible-as-``CREATE TABLE IF NOT EXISTS`` situation as v4 (an
#: existing pre-v5 ``apps`` table lacks the column), so this needs its own
#: explicit ``ALTER TABLE ... ADD COLUMN`` step too (see
#: ``_add_missing_app_columns``).
#: v6 (WP 3.5): added ``schedule_state`` — the scheduler's single-row sweep
#: bookkeeping (when the last sweep ran, and what it did). A brand-new TABLE,
#: so unlike v4/v5 this one IS fully expressible as ``CREATE TABLE IF NOT
#: EXISTS`` and needs no ALTER step: an older database simply gains the empty
#: table, which reads as "never swept".
#: v7 (WP 3.8): added ``jobs.gc_execute`` — the one bit that separates a GC
#: **dry run** from a GC that actually deletes (ADR-0007: "dry-run by
#: default"). It lives in the job row rather than in memory because the
#: request thread that accepts ``POST /v1/cache/{appid}/gc`` and the worker
#: thread that runs it are different threads, possibly different *processes*
#: across a restart, and "did the operator ask for deletions?" must survive
#: that gap unambiguously — and stay auditable afterwards, which an in-memory
#: flag would not be. ``NULL`` for every non-GC job, 0 = dry run, 1 = execute.
#: Same not-expressible-as-``CREATE TABLE IF NOT EXISTS`` situation as v4/v5,
#: so it reuses v4's explicit ``ALTER TABLE ... ADD COLUMN`` step (see
#: ``_add_missing_job_columns``, which is per-column guarded and therefore
#: correct for a database at any version from v1 to v6).
#: v8 (WP 3.12): added ``jobs.paused_at`` and ``jobs.stop_request`` — job
#: control (cancel/pause/resume). ``stop_request`` is the operator's pending
#: request against a RUNNING job (``NULL`` | ``'cancel'`` | ``'pause'``); it
#: lives in the database rather than in process memory because the HTTP thread
#: that accepts the request and the worker thread that must honor it are
#: different threads polling a subprocess, and because an operator has to be
#: able to see it. ``paused_at`` records when a job was last suspended
#: (``finished_at`` stays NULL — a paused job is not finished). Both are TEXT,
#: which is why ``_add_missing_job_columns`` had to learn per-column types
#: instead of adding everything as INTEGER.
#: v9 (WP 3.11, ADR-0008): the cache-event sweep. Four brand-new tables
#: (``event_sweep_state``, ``client_cache_stats``, ``depot_miss_stats``,
#: ``miss_trigger_state``) — fully expressible as ``CREATE TABLE IF NOT
#: EXISTS``, like v6 — plus ONE new column, ``agent_reports.source_addr``,
#: which is not: an existing ``agent_reports`` table from v1-v8 lacks it and
#: ``CREATE TABLE IF NOT EXISTS`` is a no-op against it. So this version reuses
#: the v4/v5/v8 pattern with a third per-column ALTER step
#: (``_add_missing_agent_report_columns``). ``source_addr`` is the address the
#: report arrived FROM: ADR-0008's client-identity correlation ("vault-agent
#: reports already arrive FROM those addresses, so vault-api records the report
#: source address and correlates") is what turns event-log lines, which carry
#: only addresses, into named clients. NULL for every row written before this
#: version — and that NULL is load-bearing, not cosmetic: a client whose
#: address is unknown can never be ``bypass_suspected`` (see
#: ``routers/clients.py``).
#: v10 (WP 3.9): added ``oracle_app_state`` and ``oracle_branch_manifests`` —
#: the opt-in third-party manifest oracle's own storage (ADR-0006 decision 4).
#: **Two new tables, deliberately separate from ``depot_manifests``**: that
#: table is vault-api's first-hand record of manifests it parsed itself, and a
#: third-party claim must never be indistinguishable from it (see
#: ``vault_api/oracle.py``). Like v6, these are brand-new TABLEs and therefore
#: fully expressible as ``CREATE TABLE IF NOT EXISTS`` — no ALTER step; an
#: older database simply gains two empty tables, which read as "the oracle has
#: never said anything", the same as a fresh install with the oracle off.
#: (This work package was written against v8 and has been renumbered twice by
#: rebases — onto WP 3.12, which claimed v8, and onto WP 3.11, which claimed
#: v9. That is harmless precisely because it adds no column to anything those
#: versions touched: the three ALTER steps below are each guarded per column
#: via ``PRAGMA table_info``, so which file gets which column is decided by
#: what the file actually has, not by where this table bump landed in the
#: sequence. ``init_db``'s ``< 8`` / ``< 9`` conditions are unchanged by it.)
#: v11 (WP 3.13): added ``client_bypass_state`` -- the generic-webhook
#: feature's own bookkeeping, one row per ``client_id``, recording the LAST
#: computed ``bypass_suspected`` verdict for that client. It exists so the
#: cache-event sweep (``event_sweep.py``) can tell a NEW accusation (the
#: transition worth a webhook) apart from the steady state (a client that has
#: been flagged for days and would otherwise fire on every sweep tick) --
#: without it there would be nowhere to remember "was this client already
#: flagged the last time we checked" across restarts. A brand-new table, so
#: like v6/v9/v10's new tables this is fully expressible as ``CREATE TABLE IF
#: NOT EXISTS`` and needs no ``ALTER`` step. (Renumbered from v10 in the merge
#: with WP 3.9, which claimed v10 first on the public branch — harmless for
#: the same reason as v10's own renumbering note above.)
#: v12 (WP 4a.6r): added ``steam_relay_key`` -- the opt-in Steam Web API
#: relay's own storage (ADR-0004 addendum, user decision A+C). Single row
#: (same ``CHECK(id = 1)`` device as ``schedule_state``/``event_sweep_state``):
#: one vault, one relay, one revocable read-scoped Web API key. Stored
#: PLAINTEXT, the same trust posture ``VAULT_API_KEY`` and
#: ``VAULT_WEBHOOK_URL``'s embedded Basic-Auth credentials already have in
#: this database (api/README.md "Auth"): a single-operator homelab service
#: where the operator who can reach this endpoint can already read
#: ``vault.db`` directly. A brand-new table, so — like v6/v9/v10/v11's new
#: tables — this needs no ``ALTER`` step.
#: v13 (settings-API work package, ADR-0009): added ``settings`` -- persisted
#: overrides for a small, named set of env-backed settings (vault name,
#: schedule window/cadence, webhook URL/event filter, auto-GC mode). One row
#: per OVERRIDDEN key; a key with no row simply falls through to the env
#: value or the built-in default (``vault_api/settings_store.py`` resolves
#: the precedence). ``value`` is stored in the SAME string grammar the
#: corresponding ``VAULT_*`` env var uses, validated with the identical
#: parser ``config.py`` applies at startup, so one validation path covers
#: both sources and a bad value can never reach this table. A brand-new
#: table, so -- like v6/v9/v10/v11/v12's additions -- this needs no
#: ``ALTER`` step.
#: v14 (WP 4h.1): added ``depot_manifests.first_seen_at``,
#: ``depot_manifests.manifest_changed_at`` and
#: ``depot_manifests.observation_count`` -- the minimal, honest signal the
#: Phase-4h "how often does this game change" panel field needs
#: (``vault_api/depot_manifests.py``'s "Change frequency" section has the
#: full reasoning). ``first_seen_at`` is written ONCE, at INSERT, and never
#: touched by the upsert's ``DO UPDATE`` again -- it is the observation
#: window's anchor. ``manifest_changed_at`` starts equal to ``first_seen_at``
#: and only advances to the new ``recorded_at`` when an ingest's
#: ``manifestid`` actually differs from what was stored -- a plain re-ingest
#: of an unchanged manifest (a confirmed-current, non-forced run) must not
#: look like a change. ``observation_count`` increments on every ingest,
#: changed or not, and is what lets ``depot_manifests.py`` tell "one
#: observation" (frequency is undefined) apart from "many observations, no
#: change" (genuinely stable). Same not-expressible-as-``CREATE TABLE IF NOT
#: EXISTS`` situation as v4/v5/v9 -- an existing pre-v14 ``depot_manifests``
#: table lacks all three columns -- so this needs its own explicit ``ALTER
#: TABLE ... ADD COLUMN`` step (see ``_add_missing_depot_manifest_columns``).
#: ``first_seen_at``/``manifest_changed_at`` are added NULLable (SQLite
#: cannot default a NOT NULL ``ADD COLUMN`` to another column's value) and
#: then backfilled from the row's own ``recorded_at`` -- the conservative
#: choice ``vault_api/depot_manifests.py`` documents: a pre-existing row's
#: TRUE first-observed moment is unknown, and dating it to its latest known
#: touch (rather than guessing something older) is the direction that can
#: never overstate how long it has actually been watched.
#: ``observation_count`` gets a constant ``DEFAULT 1``, which is likewise
#: conservative -- it forces every pre-existing row back to the
#: "insufficient data" boundary until it is genuinely re-observed at least
#: once post-upgrade, rather than inventing a history this code never saw.
#: v15 (WP S-1, ADR-0012): added the SEVEN ``jobs.run_*`` columns -- the
#: hand-off/lease bookkeeping between vault-api's worker (job lifecycle owner)
#: and the separate ``prefill_runner`` process (subprocess owner). All seven
#: are NULL for every job that never goes through queue mode (every GC job,
#: every prefill job run in the default ``VAULT_PREFILL_MODE=subprocess``, and
#: every job that predates this version) -- see ``vault_api/jobs.py``'s
#: "queue-mode job handoff" section for what each column means and
#: ``vault_api/prefill_queue.py`` for who writes/reads them.
#: Same not-expressible-as-``CREATE TABLE IF NOT EXISTS`` situation as
#: v4/v5/v8/v9/v14 -- an existing pre-v15 ``jobs`` table lacks all seven -- so
#: this reuses the ``_add_missing_job_columns`` per-column-guarded ALTER step.
#: v16 (WP VER-2): added ``runner_presence`` -- one row per vault-runner
#: process, refreshed by ``prefill_runner`` about every
#: ``prefill_runner.PRESENCE_INTERVAL_SECONDS`` while it runs (idle or busy),
#: so ``GET /v1/about`` can say which runner build and which SteamPrefill
#: version are live and when the runner was last seen. Before this the runner
#: left a trace only while it owned a job (``jobs.run_heartbeat_at``). A
#: brand-new table, so -- like v6/v9/v13 -- it needs no ``ALTER`` step.
#: v17 (WP AGENT-FEAT-1, ADR-0016 addendum): added
#: ``agent_reports.agent_version`` and ``agent_reports.report_interval_seconds``
#: -- what the agent says about itself in each report: its build version
#: (VER-1 grammar) and how often it reports (60..86400 s). ``GET /v1/clients``
#: reads them from the client's latest snapshot and derives online/offline
#: from ``last_reported_at`` plus the interval. Both NULLable with no default:
#: NULL is "the agent did not say" (an agent from before this version, or a
#: one-shot run that was not told its interval), and the presence rule then
#: assumes 30 minutes. Same not-expressible-as-``CREATE TABLE IF NOT EXISTS``
#: situation as v9, so it reuses ``_add_missing_agent_report_columns``.
#: v18 (WP API-FIX-5, ADR-0016 addendum): added ``app_cover_art`` -- one row
#: per app the cover-art lookup (``vault_api/cover_art.py``) has asked Steam
#: about: the validated cover URL (or NULL), the outcome, consecutive failed
#: attempts and when to ask again. A brand-new table, so -- like v6/v9/v13/v16
#: -- it needs no ``ALTER`` step; ``apps`` itself is untouched.
SCHEMA_VERSION = 18

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS apps (
    appid               INTEGER PRIMARY KEY,
    name                TEXT,
    status              TEXT NOT NULL DEFAULT 'idle',
    last_prefill_at     TEXT,
    last_manifest_check TEXT,
    -- v5 (WP 3.4): ADR-0006 decision 2. 1 = the next prefill for this app
    -- must run with --force; 0 = a non-forced run is enough (SteamPrefill's
    -- own up-to-date bookkeeping is trusted). DEFAULT 1 because a brand-new
    -- app row has never been filled -- see worker.py/deletion.py for who
    -- flips it and README.md's "needs_force lifecycle" table for the full
    -- state machine.
    needs_force         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS depot_app_map (
    depotid INTEGER NOT NULL,
    appid   INTEGER NOT NULL,
    PRIMARY KEY (depotid, appid)
);

-- plan §4's main lookup direction is appid -> depots (delete/size a game by
-- its depot list); the PK above only indexes depotid-first.
CREATE INDEX IF NOT EXISTS idx_depot_app_map_appid ON depot_app_map (appid);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    appid       INTEGER NOT NULL,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'idle',
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    log_excerpt TEXT,
    -- v4 (WP 3.3): SteamPrefill's own summary-table counters (ADR-0006
    -- decision 1). NULL for every job type/outcome that never reaches a
    -- parsed summary (GC jobs, a job that never finished, a finished job
    -- whose summary could not be parsed). summary_parse_ok is 0/1, not a
    -- real SQLite boolean (there isn't one) -- NULL means "not applicable",
    -- 0 means "parse failed", 1 means "parsed".
    updated           INTEGER,
    up_to_date        INTEGER,
    summary_parse_ok  INTEGER,
    -- v7 (WP 3.8): GC jobs only (ADR-0007's "dry-run by default"). NULL for
    -- every other job type, 0 = plan and report but delete NOTHING, 1 = the
    -- operator explicitly opted in to deletion via {"execute": true}. The
    -- worker reads THIS column, not a request-scoped variable, so a queued
    -- job that outlives a restart still carries the mode it was created with
    -- and an old job row still says which mode it ran in.
    gc_execute        INTEGER,
    -- v8 (WP 3.12): job control. paused_at is when this job was most recently
    -- suspended (NULL = never); it is NOT cleared on resume, because "when was
    -- this last paused" stays true afterwards and `status` is the authority on
    -- whether it is paused right now. stop_request is the pending operator
    -- request against a RUNNING job: NULL (nothing), 'cancel' or 'pause'. It is
    -- cleared by every terminal transition (jobs.finish_job), by park_paused
    -- once the pause has been honored, and by resume_job.
    paused_at         TEXT,
    stop_request      TEXT,
    -- v15 (WP S-1, ADR-0012): queue-mode hand-off between vault-api's worker
    -- (still the ONLY writer of every column above -- job lifecycle stays
    -- here) and the separate prefill_runner process (the only thing that ever
    -- writes run_result_json/run_completed_at, and the only writer of
    -- run_heartbeat_at once it owns a job -- see that column's note for the
    -- one write vault-api makes to it). NULL for every job that never goes
    -- through queue mode. See vault_api/jobs.py's "queue-mode job handoff"
    -- section for the full state machine and vault_api/prefill_queue.py for
    -- the encode/decode helpers.
    --
    -- run_use_force: the --force bit vault-api already decided (from
    -- apps.needs_force) at hand-off time. Written ONCE, by handoff_run, and
    -- never again -- a restarted vault-api process must honor exactly what it
    -- already told the runner, not re-derive a possibly-different value from
    -- apps.needs_force as it stands NOW.
    -- run_before_json: a JSON-encoded {depotid: [count, bytes, mtime]} depot
    -- signature snapshot (vault_api.sizes.DepotSignature), taken by vault-api
    -- immediately before hand-off. Persisted (not kept in worker memory only)
    -- for the same reattach reason as run_use_force: apply_observed_mapping's
    -- before/after diff must use the snapshot from BEFORE this run started,
    -- which a process that restarted mid-wait can no longer recompute
    -- honestly (a live re-scan at reattach time would already include
    -- whatever the runner wrote while vault-api was down).
    -- run_claimed_by / run_claimed_at: which runner instance is executing
    -- this job, and since when. NULL = unclaimed. Claiming is a
    -- compare-and-swap (jobs.claim_run) so two runner instances (a second
    -- replica, or a restarted one racing its own dangling row) can never both
    -- claim the same job.
    -- run_heartbeat_at: the runner lease clock. Stamped by vault-api ONCE,
    -- at hand-off (jobs.handoff_run, WP API-FIX-1 B2) so that "no runner
    -- claimed this within the lease" is measured from the moment a runner
    -- could have claimed it -- not from started_at, which predates
    -- vault-api's own pre-hand-off cache walk -- and then updated by the
    -- runner while SteamPrefill is in flight (piggybacked on
    -- prefill.run_prefill's existing 0.2s poll tick).
    -- Its staleness (jobs.run_is_stale) is the ONLY signal that a claimed job
    -- has a dead runner behind it -- there is deliberately no lease-stealing
    -- reclaim: once staleness is detected the JOB is failed (status leaves
    -- 'running'), which is what makes claim_run's `run_claimed_by IS NULL`
    -- check sufficient on its own (a terminal job can never be claimed again;
    -- see ADR-0012 §4 for why silent reclaim-and-retry was rejected).
    -- run_completed_at / run_result_json: non-NULL once the runner has a
    -- result. run_result_json is `{"success", "failure_reason", "exit_code",
    -- "output"}` -- the exact fields of prefill.PrefillResult -- so vault-api
    -- can reconstruct one and run the SAME post-run branch logic
    -- (worker.py's _finalize_prefill_result) regardless of which process
    -- actually ran SteamPrefill.
    run_use_force     INTEGER,
    run_before_json   TEXT,
    run_claimed_by    TEXT,
    run_claimed_at    TEXT,
    run_heartbeat_at  TEXT,
    run_completed_at  TEXT,
    run_result_json   TEXT
);

-- The job worker polls "give me the oldest queued job" on every tick and the
-- enqueue path asks "does this app already have a queued/running job?" on
-- every POST /v1/prefill. Both would otherwise scan the whole (append-only,
-- ever-growing) jobs table. Schema v2, WP 1.4.
CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs (status, id);
CREATE INDEX IF NOT EXISTS idx_jobs_appid_status ON jobs (appid, status);

-- One row per report (full-list snapshot), matching ADR-0002 literally:
-- the agent reports its complete installed-app-ID list every time; vault-api
-- derives additions/removals by diffing the two most recent rows per
-- client_id. appids is a JSON array of ints, e.g. "[440, 730]".
CREATE TABLE IF NOT EXISTS agent_reports (
    client_id   TEXT NOT NULL,
    reported_at TEXT NOT NULL,
    appids      TEXT NOT NULL,
    -- v9 (WP 3.11, ADR-0008): the network address this report arrived FROM,
    -- as seen at the TCP level. The join key between "which machine says it
    -- has these games installed" (this table) and "which address actually
    -- pulled bytes through the cache" (client_cache_stats) -- the event log
    -- knows only addresses, agent reports know only client_ids, and this
    -- column is the only thing connecting them.
    -- NULL when unknown: rows written before v9, and any request whose peer
    -- address the ASGI server did not report. Never guessed, and never
    -- derived from X-Forwarded-For (vault-api is not behind a proxy in this
    -- design, and a trusted-header story is a security decision nobody has
    -- made here).
    source_addr TEXT,
    -- v17 (WP AGENT-FEAT-1): what the agent said about itself in THIS
    -- report: its build version (VER-1 grammar) and its report interval in
    -- seconds (60..86400). NULL when it did not say (agents from before
    -- v17, a one-shot run without --interval); GET /v1/clients then shows
    -- "version unknown" and assumes a 30-minute interval for presence.
    agent_version           TEXT,
    report_interval_seconds INTEGER
);

CREATE INDEX IF NOT EXISTS idx_agent_reports_client_time
    ON agent_reports (client_id, reported_at DESC);

-- WP 3.2 / ADR-0006 decision 3: LATEST manifest state per (appid, depotid),
-- not a history table -- GC (ADR-0007) and the future staleness comparison
-- only ever need "what does vault-api currently believe this app's depot
-- manifest is", so a newer ingest for the same pair REPLACES the row
-- (vault_api/depot_manifests.py's upsert) rather than adding a new one.
--
-- appid: the app this row is attributed to for THIS mapping (usually the
-- job's own appid, i.e. the .bin filename's originalAppId).
-- containing_appid: the .bin filename's containingAppId -- the shared-depot
-- signal (research doc: e.g. 107100_228980_229002_...bin = app 107100
-- pulling a depot that "belongs" to app 228980). NULL for a cache-stored
-- manifest, which carries no such distinction.
-- manifestid is TEXT, not INTEGER, on purpose: SQLite's INTEGER storage is a
-- signed 64-bit value, and Steam manifest ids are u64 -- a real id CAN
-- exceed 2^63-1 even though every id observed during this project's research
-- (e.g. 3040704736299968944, ~3e18) stayed under it. TEXT never overflows
-- and this column is never used for arithmetic, only equality/lookup.
-- v14 (WP 4h.1) columns, see the SCHEMA_VERSION docstring block above for
-- the full reasoning: first_seen_at is written once at INSERT and never
-- updated again (the observation-window anchor); manifest_changed_at starts
-- equal to first_seen_at and only advances when a re-ingest's manifestid
-- actually differs; observation_count increments on every ingest.
CREATE TABLE IF NOT EXISTS depot_manifests (
    appid               INTEGER NOT NULL,
    containing_appid    INTEGER,
    depotid             INTEGER NOT NULL,
    manifestid          TEXT NOT NULL,
    chunk_count         INTEGER NOT NULL,
    total_bytes         INTEGER NOT NULL,
    recorded_at         TEXT NOT NULL,
    source              TEXT NOT NULL,
    first_seen_at       TEXT NOT NULL,
    manifest_changed_at TEXT NOT NULL,
    observation_count   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (appid, depotid)
);

-- Future GC (ADR-0007) needs "every app's current manifest for this depot"
-- (the shared-depot keep-set is a UNION across apps) -- the PK above only
-- indexes appid-first.
CREATE INDEX IF NOT EXISTS idx_depot_manifests_depotid ON depot_manifests (depotid);

-- WP 3.5: the scheduler's sweep bookkeeping. Exactly ONE row, forced by the
-- CHECK on the primary key -- this is process-wide state ("when did the last
-- sweep run"), not a per-something record, and a table that can only hold one
-- row cannot silently grow a second one that some future query then picks at
-- random. Writers use INSERT ... ON CONFLICT(id) DO UPDATE.
--
-- It lives in the DATABASE rather than in memory for one specific reason
-- (WP 3.5's crash-recovery rule): a restart in the middle of the window must
-- not sweep again immediately. In-memory state would reset to "never swept"
-- on every container restart, turning a crash loop -- or just an operator
-- editing .env a few times -- into a burst of Steam logins.
--
-- last_sweep_at is a UTC 'YYYY-MM-DDTHH:MM:SSZ' string, the same format every
-- other timestamp column in this database uses (jobs.utcnow_iso), so plain
-- string comparison sorts chronologically. The two counters are NULLABLE and
-- are deliberately set back to NULL when a sweep is claimed, then filled in
-- when it finishes: NULL therefore means "that sweep is still running, or the
-- process died part-way through it", which is honest, where a stale count
-- left over from the previous sweep would not be.
CREATE TABLE IF NOT EXISTS schedule_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    last_sweep_at       TEXT,
    last_sweep_targets  INTEGER,
    last_sweep_enqueued INTEGER
);

-- WP 3.11 / ADR-0008: the cache-event sweep's bookkeeping. Single-row, same
-- CHECK(id = 1) device and the same reasoning as schedule_state above.
--
-- cursor_offset is THE durable contract of this feature: the byte offset in
-- vault-core's event log up to which every line has been read AND its effects
-- committed. It advances in the same transaction that writes those effects, so
-- a process that dies mid-sweep re-reads the batch from the last committed
-- offset instead of losing it (ADR-0008: "each line is read once, a sweep
-- failure re-reads instead of losing data"). It lives in the database for the
-- same reason schedule_state does: in memory it would reset to 0 on every
-- restart and re-ingest the whole file.
--
-- first_sweep_at is not decoration. Bypass detection has to distinguish "this
-- client never appears in the cache log" from "the log has not been running
-- long enough to know", and the age of the feed is the only honest source for
-- that (see routers/clients.py's fail-toward-not-suspecting rule).
--
-- The counters are diagnostics: the *_total ones accumulate across sweeps,
-- the last_* ones describe the most recent sweep only (NULL before the first
-- one). GET /v1/stats reads exactly this row.
--
-- truncate_denied_count / last_truncate_denied_at record when the sweeper
-- CANNOT rotate the file it is contractually responsible for. In the shipped
-- containers it can: since WP SEC-FIX-5 event.log is 101:101 inside a
-- root-only /vault/logs, and vault-api (uid 101) ftruncates it through a
-- verified fd (event_sweep.maybe_truncate). A denial means a deployment that
-- differs (a differently-owned or 0644 log from another writer). That is a
-- fail-soft condition, not an error -- sweeping stays correct because
-- correctness is cursor-based -- but it means the file grows without bound,
-- which an operator has to be able to SEE rather than infer from a full disk.
-- Hence a persisted counter next to the log warnings.
CREATE TABLE IF NOT EXISTS event_sweep_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    cursor_offset           INTEGER NOT NULL DEFAULT 0,
    first_sweep_at          TEXT,
    last_sweep_at           TEXT,
    last_rotated_at         TEXT,
    lines_read_total        INTEGER NOT NULL DEFAULT 0,
    lines_skipped_total     INTEGER NOT NULL DEFAULT 0,
    last_lines              INTEGER,
    last_skipped            INTEGER,
    last_enqueued           INTEGER,
    last_dropped_by_cap     INTEGER,
    truncate_denied_count   INTEGER NOT NULL DEFAULT 0,
    last_truncate_denied_at TEXT,
    -- How often the sweeper had to step over a region whose "line" was longer
    -- than a whole read batch (4 MiB). Unreachable in normal operation --
    -- nginx bounds the URI field to 300 characters -- so a non-zero value here
    -- means something other than the event log is writing to that file, or it
    -- is corrupt. It is persisted rather than only logged because the
    -- alternative (the pre-review behaviour) was a SILENT PERMANENT STALL:
    -- the sweep re-read the same bytes forever, consumed nothing, and the only
    -- signal was an INFO line that read like progress.
    oversized_skips_total   INTEGER NOT NULL DEFAULT 0,
    last_oversized_at       TEXT
);

-- WP 3.11: per-client-address request statistics, one row per address per
-- sweep window (plan §5/§6 "per-client hit stats"). window_at is the sweep's
-- own UTC timestamp, so the row means "what this address did in the lines this
-- sweep consumed" -- NOT a fixed wall-clock bucket.
--
-- Writers use INSERT ... ON CONFLICT DO UPDATE with `col = col + excluded.col`.
-- That is what makes two sweeps landing in the same second merge instead of
-- raising, and it keeps the counters additive, which is the property the
-- crash-recovery story needs: the whole aggregate is applied in the same
-- transaction as cursor_offset, so a re-read batch is never double-counted.
--
-- requests = hits + misses + bypasses + errors, by construction. hits/misses/
-- bypasses count only lines whose HTTP status was 2xx (field 9 of the event
-- log exists precisely so a 403/404/502 is not counted as served traffic);
-- everything else lands in `errors`. bytes_served likewise sums bytes_sent on
-- 2xx lines only, so an error body's bytes never inflate a client's total.
CREATE TABLE IF NOT EXISTS client_cache_stats (
    client_addr  TEXT NOT NULL,
    window_at    TEXT NOT NULL,
    requests     INTEGER NOT NULL DEFAULT 0,
    hits         INTEGER NOT NULL DEFAULT 0,
    misses       INTEGER NOT NULL DEFAULT 0,
    bypasses     INTEGER NOT NULL DEFAULT 0,
    errors       INTEGER NOT NULL DEFAULT 0,
    bytes_served INTEGER NOT NULL DEFAULT 0,
    last_seen    TEXT NOT NULL,
    PRIMARY KEY (client_addr, window_at)
);

-- WP 3.11: which depots are being MISSed, and whether vault-api had a mapping
-- for them at the time. ADR-0008: "misses on unmapped depots are counted but
-- trigger nothing" -- this table is where that counting lands, and it is the
-- operator's answer to "what is my LAN downloading that I have no mapping
-- for". One row per depot (not per sighting), bounded by
-- event_sweep.MAX_DEPOT_MISS_ROWS on a least-recently-seen basis.
CREATE TABLE IF NOT EXISTS depot_miss_stats (
    depotid    INTEGER PRIMARY KEY,
    miss_count INTEGER NOT NULL DEFAULT 0,
    mapped     INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);

-- WP 3.11 / ADR-0008: the miss trigger's per-app cooldown, persisted for the
-- same reason every other piece of sweep state is -- an in-memory cooldown
-- resets on restart, and "restart the container" would become "enqueue a
-- prefill for every app that misses in the next batch".
--
-- Written in its own committed transaction immediately AFTER the enqueue it
-- describes (see event_sweep.record_trigger): that ordering is deliberate, so
-- a crash between the two leaves a queued job with no cooldown -- which the
-- queue's own per-app dedupe then absorbs -- rather than a cooldown with no
-- job, which would suppress the trigger for an app nothing is filling.
CREATE TABLE IF NOT EXISTS miss_trigger_state (
    appid             INTEGER PRIMARY KEY,
    last_triggered_at TEXT NOT NULL,
    trigger_count     INTEGER NOT NULL DEFAULT 0
);

-- GET /v1/clients aggregates a client's statistics across every address it has
-- reported from; retention prunes per address. Both are address-keyed scans of
-- a table that grows one row per active address per sweep.
CREATE INDEX IF NOT EXISTS idx_client_cache_stats_addr
    ON client_cache_stats (client_addr, window_at DESC);

-- Retention drops the least-recently-seen depots; GET /v1/stats lists the most
-- recently seen ones. Both sort by last_seen.
CREATE INDEX IF NOT EXISTS idx_depot_miss_stats_last_seen
    ON depot_miss_stats (last_seen);

-- WP 3.13: generic webhook notifications. One row per client_id, holding the
-- LAST bypass_suspected verdict the cache-event sweep computed for it. This
-- is not user-facing state -- GET /v1/clients recomputes bypass_suspected
-- fresh on every request, same as before this table existed -- it exists
-- purely so a sweep can detect a client NEWLY flipping to bypass_suspected
-- (the only moment worth a webhook) instead of a client that has been
-- flagged for days firing again on every sweep tick. Populated only when
-- VAULT_WEBHOOK_URL is set and 'client.bypass_suspected' is one of the
-- configured VAULT_WEBHOOK_EVENTS -- an installation that never asked for
-- this webhook never gets a row here.
CREATE TABLE IF NOT EXISTS client_bypass_state (
    client_id        TEXT PRIMARY KEY,
    bypass_suspected INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL
);

-- WP 3.9 / ADR-0006 decision 4: the opt-in third-party manifest oracle's own
-- storage. THIRD-PARTY CLAIMS LIVE HERE AND NOWHERE ELSE -- nothing in
-- vault_api/oracle.py writes to depot_manifests, which stays vault-api's
-- first-hand record of manifests it parsed itself. Every row additionally
-- carries a `source` provenance tag naming the oracle that produced it, so a
-- future second oracle cannot be confused with this one.
--
-- One row per app: when it was last asked, and the public branch's build id
-- (operator information -- nothing branches on the build id).
CREATE TABLE IF NOT EXISTS oracle_app_state (
    appid        INTEGER PRIMARY KEY,
    buildid      TEXT,
    checked_at   TEXT NOT NULL,
    source       TEXT NOT NULL,
    depot_count  INTEGER NOT NULL,
    branch_count INTEGER NOT NULL
);

-- One row per (app, depot, OPEN branch). Password-protected branches are
-- never inserted at all (vault_api/oracle.py drops them at validation time):
-- their manifests are encrypted and uncoverable, so a gid that is absent from
-- this table cannot leak into a GC keep set through a forgotten WHERE clause.
-- `manifestid` is TEXT for the same reason depot_manifests.manifestid is:
-- Steam gids are u64 and SQLite's INTEGER storage is signed 64-bit.
--
-- Snapshot semantics: a refresh DELETEs the app's rows and re-inserts them in
-- one transaction, so a branch that disappeared upstream stops protecting
-- chunks instead of lingering forever.
CREATE TABLE IF NOT EXISTS oracle_branch_manifests (
    appid       INTEGER NOT NULL,
    depotid     INTEGER NOT NULL,
    branch      TEXT NOT NULL,
    manifestid  TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source      TEXT NOT NULL,
    PRIMARY KEY (appid, depotid, branch)
);

-- GC asks "every open-branch manifest for THIS DEPOT, whichever app recorded
-- it" (a shared depot's beta chunks belong to the depot, not to one app) --
-- the appid-first primary key above does not serve that direction.
CREATE INDEX IF NOT EXISTS idx_oracle_branch_manifests_depotid
    ON oracle_branch_manifests (depotid);

-- WP 4a.6r / ADR-0004 addendum: opt-in Steam Web API relay (GetOwnedGames /
-- GetPlayerSummaries) for the web UI, which cannot call the Steam Web API
-- itself (no CORS headers on Valve's side). Exactly one row, forced by the
-- CHECK on the primary key -- there is one relay key for the whole vault, not
-- one per web-UI user. NEVER a password: this is a revocable, READ-SCOPED Web
-- API key the operator generates on Valve's site and enters once in the web
-- UI's settings (vault_api/steam_relay.py has the validator and the
-- boundaries). Cleared by DELETE /v1/steam/key; GET /v1/steam/key never
-- returns the value, only whether one is configured and its last 4 chars.
CREATE TABLE IF NOT EXISTS steam_relay_key (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    api_key    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Settings-API work package (ADR-0009): persisted overrides for a small,
-- named set of env-backed settings. One row per OVERRIDDEN key -- there is
-- no row for a key that has never been PATCHed, and DELETE (a PATCH with
-- `null`) removes the row entirely rather than storing a tombstone, which is
-- what makes "revert to the env value or built-in default" a plain absence
-- check instead of a NULL check. `value` is TEXT in the SAME string grammar
-- the corresponding VAULT_* env var would use (e.g. "180", "09:00-17:00",
-- "job.done,job.error") -- vault_api/settings_store.py validates a write
-- with the identical parser vault_api/config.py applies to the env var at
-- startup, so a value can never reach this table without already being
-- something the rest of the app can consume. `updated_at` is an ordinary UTC
-- 'YYYY-MM-DDTHH:MM:SSZ' timestamp, the same format every other timestamp
-- column in this database uses -- an audit breadcrumb ("when was this last
-- changed"), not something any read path branches on.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- WP VER-2: vault-runner presence. One row per runner PROCESS (runner_id is
-- prefill_runner.make_runner_id(): hostname:pid:random), upserted by that
-- process about every 30 s whether or not it owns a job; rows not seen for a
-- day are pruned by the next write. Read only by GET /v1/about, which shows
-- the freshest row. build_version/build_commit/steamprefill_version hold what
-- the runner's own image says (vault_api.reported_identity /
-- steamprefill_version: a valid value, 'invalid', or NULL for "not baked"),
-- and vault-api validates them again on read. Timestamps use the one UTC
-- format of this database (jobs.TIMESTAMP_FORMAT).
-- WP API-FIX-5 (schema v18): real cover art per app, from Steam's store item
-- lookup (IStoreBrowseService/GetItems on api.steampowered.com, no key).
-- Written only by vault_api/cover_art.py's background refresher; read by
-- GET /v1/games[/{appid}] as `cover_url`. `cover_url` is NULL unless it passed
-- cover_art.valid_cover_url (https on shared.akamai.steamstatic.com or
-- cdn.akamai.steamstatic.com), and it is validated again on read.
-- `outcome` is 'found', 'none' (Steam has no cover for it) or 'failed' (the
-- lookup itself failed; a URL found earlier is kept). `attempts` counts
-- consecutive failures and drives the backoff; `next_check_at` (UTC, the one
-- timestamp format of this database) is when the refresher asks again.
-- No foreign key: an app row is never deleted, and a stray row is harmless.
CREATE TABLE IF NOT EXISTS app_cover_art (
    appid         INTEGER PRIMARY KEY,
    cover_url     TEXT,
    outcome       TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    checked_at    TEXT NOT NULL,
    next_check_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_cover_art_next_check
    ON app_cover_art (next_check_at);

CREATE TABLE IF NOT EXISTS runner_presence (
    runner_id            TEXT PRIMARY KEY,
    build_version        TEXT,
    build_commit         TEXT,
    steamprefill_version TEXT,
    started_at           TEXT NOT NULL,
    last_seen            TEXT NOT NULL
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a new connection with sane defaults (row access by column name).

    WAL journal mode + a busy_timeout let HTTP request handlers read the
    database while the background job worker holds a write transaction,
    instead of failing immediately with "database is locked".

    ``check_same_thread`` is left at its safe default of ``True`` (WP 1.4;
    WP 1.3 had set it to ``False``). Every connection in this codebase is now
    thread-confined by construction — request handlers open and close theirs
    inside the endpoint body (``deps.db_opener``) and the job worker owns one
    connection created inside its own thread — so the default costs nothing
    and turns a future accidental hand-off into a loud ``ProgrammingError``
    instead of a native crash. See ``deps.py`` for the measured access
    violation that made the old shared-connection approach untenable.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: str) -> None:
    """Create/upgrade the schema. Idempotent — safe to call on every startup.

    Migration story (deliberately minimal, plan §9 "keep it simple"): every
    statement in ``_DDL`` is ``CREATE ... IF NOT EXISTS``, so running the
    current DDL against an older file brings it to the current shape as long
    as all changes so far have been *additive*. That holds for v1 -> v2 (two
    new ``jobs`` indexes), so the "migration" is just recording the new
    version number afterwards.

    The day a change is NOT additive (dropping/retyping a column), this
    function needs a real per-version step list — the ``stored > SCHEMA_VERSION``
    guard below exists so a downgrade is caught loudly instead of silently
    operating on a schema this code doesn't understand.

    **Ordering (WP 1.5 carry-over fix from the WP 1.4 review):** the stored
    version is read and checked BEFORE the rest of ``_DDL`` runs, not after.
    The previous ordering ran the full DDL unconditionally and only checked
    the version afterwards — harmless so far because every statement so far
    has been additive, but it read as a downgrade *gate* without being one:
    a future non-additive statement would already have executed against a
    newer-than-understood schema by the time the guard fired. Only the
    ``schema_version`` table itself (a single, always-safe ``CREATE TABLE IF
    NOT EXISTS``) runs before the check.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = get_connection(db_path)
    try:
        # Only the version marker table — cheap and always additive — before
        # the guard runs. The rest of _DDL (which also (re)creates this same
        # table, harmlessly, via IF NOT EXISTS) follows only once we know this
        # code understands the stored version.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is not None and row["version"] > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database {db_path} has schema version {row['version']}, but this "
                f"vault-api only understands up to {SCHEMA_VERSION}. It was written "
                "by a newer version — upgrade vault-api instead of downgrading."
            )

        # v4 (WP 3.3): an existing pre-v4 database already has a `jobs` table
        # without the new columns, so `CREATE TABLE IF NOT EXISTS` below is a
        # no-op for it — unlike the v2/v3 bumps, this one needs an explicit
        # ALTER. A brand-new database (row is None) skips this: _DDL's CREATE
        # TABLE already includes the columns directly. Runs before
        # executescript so the two migration mechanisms (ALTER here, CREATE
        # ... IF NOT EXISTS below) never depend on ordering relative to each
        # other.
        # v7 (WP 3.8) and v8 (WP 3.12) reuse the same step:
        # `_add_missing_job_columns` is guarded per column, so one call brings a
        # `jobs` table at ANY version from v1 to v7 up to the current column set
        # (a v7 database is missing only the two v8 columns; a v1 database is
        # missing all six).
        # v15 (WP S-1, ADR-0012) reuses the same step once more -- same
        # per-column guard, so one call also brings a v8..v14 database up to
        # date by adding just the seven queue-mode ``run_*`` columns it lacks.
        if row is not None and row["version"] < 15:
            _add_missing_job_columns(conn)

        # v5 (WP 3.4): same situation as the v4 step above -- an existing
        # pre-v5 `apps` table already exists, so `CREATE TABLE IF NOT EXISTS`
        # below is a no-op for it and needs_force needs an explicit ALTER.
        if row is not None and row["version"] < 5:
            _add_missing_app_columns(conn)

        # v9 (WP 3.11): same situation once more, for `agent_reports`. An
        # existing table from v1-v8 has no `source_addr` column and
        # `CREATE TABLE IF NOT EXISTS agent_reports` below is a no-op against
        # it. The four NEW tables v9 also adds need no step at all -- they are
        # plain `CREATE TABLE IF NOT EXISTS` (the v6 situation).
        # v17 (WP AGENT-FEAT-1) reuses the same per-column-guarded step for
        # agent_version / report_interval_seconds.
        if row is not None and row["version"] < 17:
            _add_missing_agent_report_columns(conn)

        # v14 (WP 4h.1): same situation once more, for `depot_manifests`. An
        # existing table from v3-v13 has none of the three change-frequency
        # columns and `CREATE TABLE IF NOT EXISTS depot_manifests` below is a
        # no-op against it.
        if row is not None and row["version"] < 14:
            _add_missing_depot_manifest_columns(conn)

        conn.executescript(_DDL)

        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        elif row["version"] < SCHEMA_VERSION:
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

        conn.commit()
    finally:
        conn.close()


#: The ``jobs`` columns added after v1, with the type each must be created
#: with. Types are spelled out per column rather than assumed (WP 3.12): v4/v7
#: added only nullable INTEGERs and the old loop hardcoded ``INTEGER``, which
#: would have silently given v8's two TEXT columns the wrong affinity on an
#: upgraded database while a fresh one got TEXT from ``_DDL`` — two databases
#: with the same ``schema_version`` and different column types.
_POST_V1_JOB_COLUMNS = (
    ("updated", "INTEGER"),
    ("up_to_date", "INTEGER"),
    ("summary_parse_ok", "INTEGER"),
    ("gc_execute", "INTEGER"),
    ("paused_at", "TEXT"),
    ("stop_request", "TEXT"),
    # v15 (WP S-1, ADR-0012): queue-mode job hand-off, see the SCHEMA_VERSION
    # docstring block and _DDL's own comment on the jobs table for what each
    # one means.
    ("run_use_force", "INTEGER"),
    ("run_before_json", "TEXT"),
    ("run_claimed_by", "TEXT"),
    ("run_claimed_at", "TEXT"),
    ("run_heartbeat_at", "TEXT"),
    ("run_completed_at", "TEXT"),
    ("run_result_json", "TEXT"),
)


def _add_missing_job_columns(conn: sqlite3.Connection) -> None:
    """v1->v15 migration step: add every ``jobs`` column added after v1.

    ``updated``/``up_to_date``/``summary_parse_ok`` came with v4 (WP 3.3),
    ``gc_execute`` with v7 (WP 3.8), ``paused_at``/``stop_request`` with v8
    (WP 3.12), the seven ``run_*`` columns with v15 (WP S-1). All are nullable
    with no default, so one guarded loop covers them — see
    ``_POST_V1_JOB_COLUMNS`` for why the loop carries types.

    Guarded per-column via ``PRAGMA table_info`` (not just per-version) so
    calling ``init_db`` twice against the same older file — or against a file
    some *other* migration already partially touched — never raises
    ``duplicate column name`` instead of silently doing nothing on the second
    call, matching the idempotency the rest of ``init_db`` already promises.
    That per-column guard is also what lets ONE step serve every version from
    v1 to v14: a v14 database simply gains the seven v15 columns and nothing
    else, while a v1 database gains all thirteen post-v1 columns.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    for column, column_type in _POST_V1_JOB_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {column_type}")


#: The ``agent_reports`` columns added after v1, same shape as
#: ``_POST_V1_JOB_COLUMNS``.
_POST_V1_AGENT_REPORT_COLUMNS = (
    ("source_addr", "TEXT"),
    # v17 (WP AGENT-FEAT-1)
    ("agent_version", "TEXT"),
    ("report_interval_seconds", "INTEGER"),
)


def _add_missing_agent_report_columns(conn: sqlite3.Connection) -> None:
    """v1->v17 migration step: add ``agent_reports.source_addr`` (v9, WP 3.11)
    and ``agent_version`` / ``report_interval_seconds`` (v17, WP AGENT-FEAT-1).

    Guarded per column via ``PRAGMA table_info`` for exactly the reasons
    ``_add_missing_job_columns`` spells out, and carrying an explicit type for
    the reason WP 3.12 discovered the hard way (a hardcoded type gives an
    upgraded database a different column affinity from a fresh one at the same
    ``schema_version``).

    Nullable with no default, deliberately: a report stored before v9 has no
    recorded source address and there is no honest value to invent for it.
    ``NULL`` means "unknown", and every consumer treats unknown as "cannot
    correlate, therefore cannot accuse" — see ``routers/clients.py``. The
    same holds for the v17 columns: a report stored before v17 did not say
    which agent version sent it or how often it reports.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(agent_reports)")}
    for column, column_type in _POST_V1_AGENT_REPORT_COLUMNS:
        if column not in existing:
            conn.execute(
                f"ALTER TABLE agent_reports ADD COLUMN {column} {column_type}"
            )


#: The ``depot_manifests`` columns added after v3 (WP 4h.1).
#: ``first_seen_at``/``manifest_changed_at`` are added NULLable — SQLite's
#: ``ADD COLUMN`` cannot default a ``NOT NULL`` column to another column's
#: value — and backfilled below via ``UPDATE``. ``observation_count`` DOES
#: take a constant default because 1 is genuinely the right value for a
#: migrated row (see the ``SCHEMA_VERSION`` docstring block).
_POST_V3_DEPOT_MANIFEST_COLUMNS = (
    ("first_seen_at", "TEXT"),
    ("manifest_changed_at", "TEXT"),
    ("observation_count", "INTEGER NOT NULL DEFAULT 1"),
)


def _add_missing_depot_manifest_columns(conn: sqlite3.Connection) -> None:
    """v3->v14 migration step: add ``depot_manifests``' change-frequency
    columns (WP 4h.1; ``vault_api/depot_manifests.py`` "Change frequency").

    Guarded per column via ``PRAGMA table_info`` for exactly the reasons
    ``_add_missing_job_columns`` spells out. ``first_seen_at`` and
    ``manifest_changed_at`` are backfilled from the row's own ``recorded_at``
    once the columns exist — a pre-existing row's TRUE first-observed moment
    is unknown, and dating it to its latest known touch (rather than
    guessing something earlier) is the conservative direction: it can only
    ever UNDERSTATE how long a depot has been watched, never overstate it,
    which is the same "never wrongly claim stability" posture the two
    honesty pins in ``depot_manifests.py`` are built around. The backfill
    ``UPDATE`` is itself idempotent (``WHERE ... IS NULL``), matching the
    per-column ``ADD COLUMN`` guard above — calling this twice against the
    same file is safe.

    **Also guarded against the table not existing at all yet** (a v1/v2
    database, before v3 introduced ``depot_manifests`` in the first place —
    ``PRAGMA table_info`` on a missing table quietly returns zero rows rather
    than raising, so the per-column loop alone would try to ``ALTER`` a table
    that isn't there). In that case there is nothing to migrate; the
    unconditional ``CREATE TABLE IF NOT EXISTS`` in ``_DDL`` that runs right
    after this function creates the table fresh, with every v14 column
    already ``NOT NULL`` and no pre-existing rows to backfill.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'depot_manifests'"
    ).fetchone()
    if table_exists is None:
        return

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(depot_manifests)")}
    for column, column_type in _POST_V3_DEPOT_MANIFEST_COLUMNS:
        if column not in existing:
            conn.execute(
                f"ALTER TABLE depot_manifests ADD COLUMN {column} {column_type}"
            )
    conn.execute(
        "UPDATE depot_manifests SET first_seen_at = recorded_at WHERE first_seen_at IS NULL"
    )
    conn.execute(
        "UPDATE depot_manifests SET manifest_changed_at = recorded_at "
        "WHERE manifest_changed_at IS NULL"
    )


def _add_missing_app_columns(conn: sqlite3.Connection) -> None:
    """v1->v5 migration step: add ``apps.needs_force`` (ADR-0006 decision 2).

    Same reasoning as ``_add_missing_job_columns``: guarded via ``PRAGMA
    table_info`` rather than only the version check, so calling ``init_db``
    twice against the same pre-v5 file never raises ``duplicate column
    name``. ``DEFAULT 1`` on the ``ALTER TABLE`` is deliberate and matches
    ``_DDL``'s own column default exactly: every pre-existing app row already
    represents "has been prefilled before" from vault-api's point of view,
    but this migration has no way to know whether that prior prefill ran with
    or without ``--force`` (schema versions before this one always passed
    ``--force`` unconditionally — see ``vault_api/prefill.py``). Starting
    every pre-existing app at ``needs_force=1`` costs at most one redundant
    forced run per app after the upgrade (cheap: disk-speed re-requests, not
    bandwidth, per the same ADR-0001 measurement `--force` always relied on),
    which is a safe default to err on compared to silently trusting
    SteamPrefill's own stale bookkeeping for an app this code has no history
    for.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
    if "needs_force" not in existing:
        conn.execute(
            "ALTER TABLE apps ADD COLUMN needs_force INTEGER NOT NULL DEFAULT 1"
        )
