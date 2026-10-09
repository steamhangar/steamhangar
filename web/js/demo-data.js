/**
 * Demo-mode fixtures (WP 4a.2).
 *
 * Serves the app entirely from in-memory, synthetic data so it works with
 * no vault present (NOTES open question 5: "real value for first-run and
 * screenshots"). Shapes are MODELED on the real vault-api responses
 * documented in api/README.md's "Endpoints" table and the Pydantic models
 * in api/vault_api/routers/*.py, so api.js's callers and the polling store
 * work the same in both modes. **That is a goal, not a guarantee for every
 * route** (WP WEB-FEAT-3 correction: this header used to claim "field names
 * match exactly" while `/v1/clients` had silently missed WP AGENT-FEAT-1's
 * four fields). What IS guaranteed is only what a test pins against the
 * server source: `/v1/clients` rows and `/v1/about` (keys equal the
 * `ClientOut` / `AboutOut` / `ComponentOut` fields, web/tests/
 * demo-data-shape-guard.test.js), `installed_on` (demo-data-installed-on),
 * the settings keys and defaults (demo-data-settings, demo-data-config-
 * defaults). Any other route may lag the server; add a key pin when a
 * screen starts depending on it.
 *
 * Per docs/LEARNINGS.md ("Testing discipline": fixtures are synthetic,
 * modeled on real structure, never personal data): every title, id and
 * client name below is fictional; none of it is real Steam data.
 *
 * No network access of any kind (CSP compatibility, WP 4a.2 scope) — this
 * is local state, reset on every page load, not a mock HTTP server.
 *
 * Testability: this module imports only errors.js and two other DOM-free
 * `lib/` modules (no `window`, no `document`, no `fetch`) — it is plain
 * data plus plain functions and runs in bare Node. `resetDemoData()`
 * restores the module's mutable state to a fresh copy of the seed data;
 * tests call it between cases so scenarios (e.g. "delete this game") don't
 * leak into the next test (see web/tests/demo-data.test.js).
 *
 * WP 4a.6 extends this with `/v1/settings` (ADR-0009) and the Steam Web API
 * relay (`/v1/steam/*`, ADR-0004 addendum) so the Settings view has
 * something to render in demo mode instead of a bare 404 toast — see
 * web/tests/demo-data-settings.test.js. Both reuse the same pure validators
 * the real router/relay use client-side (`lib/steamid.js`,
 * `lib/steam-key-form.js`) so a demo rejection and a real `422` agree on
 * shape, without duplicating the grammar a third time.
 *
 * WP 4c-web extends this with `POST /v1/prefill/cached` (Phase 4c, WP
 * 4c-api's server contract) so the Library view's "Check & update all
 * cached games" trigger has a real code path in demo mode — see
 * web/tests/demo-data-cached-prefill.test.js. `enqueuePrefillForAppid` below
 * is the ONE enqueue mechanism both `POST /v1/prefill` and this route call
 * (mirrors the real API's "no second enqueue mechanism" rule), and the seed
 * data's already-`running` job (900001, Driftwood Signal) is left in place
 * on purpose — it means a fresh call to the new route exercises the
 * "already running" dedupe branch with zero extra setup.
 *
 * WP 4e.1 extends this with a large-library fixture — `generateSyntheticGames`
 * appends up to 394 deterministic synthetic games on top of the 6 curated
 * ones below, gated behind a `localStorage`-read library-size preference (or
 * an explicit `resetDemoData({ librarySize })` argument for headless tests)
 * — see that function's own header for what it can and cannot measure, and
 * `web/tests/demo-data-large-library.test.js` for its coverage. Every
 * existing test in this suite that calls `resetDemoData()` with no argument
 * is unaffected: the default library size is still exactly 6.
 */

import { ApiError, ERROR_KINDS } from "./errors.js";
import { validSteamId64 } from "./lib/steamid.js";
import { validSteamWebApiKey } from "./lib/steam-key-form.js";

function isoAgo(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

// ---------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------

function makeGame({
  appid,
  name,
  status,
  needsForce = false,
  depots = [],
  // WP 4a.4: `apps.last_manifest_check` (api/README.md "Job outcome
  // honesty") — set on only two seed games below to exercise all three
  // `lib/detail-wording.js` branches without inventing a fourth game just
  // for this field. `null` (the default) is the common case: an ordinary
  // run that actually changed depots leaves it untouched, so most demo
  // games are honestly "never confirmed" even while `status: "done"`.
  lastManifestCheck = null,
  // WP 4h.2 fix (coder's own addition, named — not one of the three defects
  // the brief called out by name, but the same drift class: WP 4h.1 added
  // `manifest_change_frequency`/`manifest_observation_days`/
  // `manifest_days_since_last_change` to the REAL GameSummary/GameDetail
  // models `api/`-only, per docs/PROJECT_PLAN.md's own note that WP 4h.1
  // landed with "no conflicts in web/" — meaning this fixture never grew
  // them at all, and the suggestions panel this WP ships has no way to
  // demo its CHANGED_RECENTLY/STABLE statement families without them).
  // Same "set on a small subset, most games stay honestly null" posture as
  // `lastManifestCheck` just above — WP 4h.1's own null-vs-"insufficient_data"
  // distinction (a game with manifest data recorded at all vs. one that
  // has never been observed) is exercised by leaving most seed games at the
  // `null` default rather than inventing a fourth game per state.
  manifestChangeFrequency = null,
  manifestObservationDays = null,
  manifestDaysSinceLastChange = null,
  // WP 4a.4 demo-only: bytes a GC dry run "discovers" as reclaimable for
  // this app, purely so the GC flow has something to show in demo mode —
  // never serialized into any response (see gcHandler below). Real
  // vault-api computes this from a manifest diff (`gc.py`); the demo model
  // tracks no manifests at all, so this is a deliberately simple stand-in,
  // not an approximation of the real algorithm.
  gcReclaimableBytes = 0,
  // WP 4a.8 demo-only addition: bytes the dry run finds but reports HELD
  // BACK (api/vault_api/gc_execute.py's `RecentlyStoredGrace` — a chunk
  // stored within the grace window). Kept separate from
  // `gcReclaimableBytes` (which is what an EXECUTE run actually frees) so
  // demo mode can exercise the real `held_back=N (M bytes)` field
  // `lib/gc-log-summary.js` parses end to end — before this WP the demo
  // log's `held_back` was hardcoded to `0 (0 bytes)` in every scenario, so
  // that branch of the parser (and the detail sheet's "N chunks held
  // back — inside the grace window" note) was never exercised by demo mode
  // at all. An execute run does NOT clear this counter: the grace window is
  // a TIME rule, not something an execute run changes (gc_execute.py: "the
  // separation is deliberate... time is a policy on top of [the plan],
  // not part of it").
  gcHeldBackBytes = 0,
  // WP AG-2: `installed_on: [{client_id, reported_at}]` (WP AG-1,
  // api/README.md "Installed state per app") — already pre-filtered to
  // fresh reports server-side, so this fixture never invents a stale/
  // filtered-out entry; every entry here is meant to be shown. Empty by
  // default (the common "no fresh report" case, most seed games below stay
  // at it) — see buildCuratedGames() for which two games exercise the
  // cached/not-cached badge states.
  installedOn = [],
}) {
  return {
    appid,
    name,
    status,
    last_prefill_at: status === "idle" ? null : isoAgo(3 * 3_600_000),
    last_manifest_check: lastManifestCheck,
    needs_force: needsForce,
    manifest_change_frequency: manifestChangeFrequency,
    manifest_observation_days: manifestObservationDays,
    manifest_days_since_last_change: manifestDaysSinceLastChange,
    installed_on: installedOn,
    // Doubles as both "mapping rows" and "cache state" for this depot in the
    // demo model (the real vault-api keeps those two facts separately —
    // api/README.md "Per-game deletion": deletion clears cache content, not
    // mapping rows). Simplification accepted for this WP: see the DELETE
    // /v1/cache/{appid} handler below for where that would matter and how
    // it is approximated.
    depots,
    _demoGcReclaimableBytes: gcReclaimableBytes,
    _demoGcHeldBackBytes: gcHeldBackBytes,
  };
}

/** The 6 curated, hand-named seed games every existing demo test asserts
 * exact appids/fields against — unchanged by WP 4e.1. `buildGames()` below
 * appends the large-library fixture on top of exactly this list. */
function buildCuratedGames() {
  return [
    makeGame({
      appid: 2010010,
      name: "Aurora Cascade",
      status: "done",
      depots: [{ depotid: 2010011, shared: false, size_bytes: 4_200_000_000 }],
      // Gives the GC dry-run flow something honest to find on the first
      // check (WP 4a.4 live-verification fixture) — see makeGame()'s header.
      gcReclaimableBytes: 120_000_000,
      // WP 4h.2 fix: exercises the suggestions panel's STABLE family —
      // enough observation days, no observed change.
      manifestChangeFrequency: "stable",
      manifestObservationDays: 120,
      // WP AG-2: installed AND cached — the plain, informational badge
      // state ("Installed on workshop-pc"), reusing buildClients()'s own
      // "workshop-pc" client_id so the two fixtures stay one consistent
      // fictional vault rather than inventing a third, unrelated name.
      installedOn: [{ client_id: "workshop-pc", reported_at: isoAgo(3_600_000) }],
    }),
    makeGame({
      appid: 2010020,
      name: "Copper Horizon",
      status: "idle",
      needsForce: true,
      depots: [],
      // WP 4a.4: last_manifest_check SURVIVING a null last_prefill_at is
      // exactly the post-deletion shape api/README.md documents — this game
      // was once prefilled and confirmed current, then had its cache
      // deleted (needsForce: true is the other half of that same story).
      // Exercises lib/detail-wording.js's CONFIRMED_BEFORE_CACHE_CLEARED
      // branch ("Confirmed current at X (before the cache was cleared)").
      lastManifestCheck: isoAgo(2 * 86_400_000),
      // WP AG-2: installed but NOT cached — the state this whole feature
      // exists for. `depots: []` above already makes `size_bytes` null, so
      // this game is exactly "an agent claims it installed, the vault has
      // nothing on disk protecting it" with zero extra bookkeeping.
      // TWO clients, deliberately (review round 1, S5): the single-client
      // curated fixtures never exercised installedOnSummary's "+N" branch
      // or the detail sheet's multi-row list in demo mode at all — and
      // multi-client is exactly where the badge's width cap bites hardest,
      // combined here with the NOT_CACHED case that already has the least
      // room to spare. Reuses "workshop-pc" (Aurora Cascade's own client,
      // buildClients() below) rather than inventing a third client_id — one
      // real agent legitimately reports several installed games.
      installedOn: [
        { client_id: "loft-laptop", reported_at: isoAgo(2 * 3_600_000) },
        { client_id: "workshop-pc", reported_at: isoAgo(5 * 3_600_000) },
      ],
    }),
    makeGame({
      appid: 2010030,
      name: "Driftwood Signal",
      status: "running",
      depots: [{ depotid: 2010031, shared: false, size_bytes: 1_100_000_000 }],
      // WP 4h.2 fix: exercises the suggestions panel's CHANGED_RECENTLY
      // family — "changed", past tense, with a real days-since-last-change.
      manifestChangeFrequency: "changed",
      manifestObservationDays: 45,
      manifestDaysSinceLastChange: 2,
    }),
    makeGame({
      appid: 2010040,
      name: "Emberreach",
      status: "done",
      depots: [
        { depotid: 2010041, shared: false, size_bytes: 800_000_000 },
        // Shared with Frostline Convoy (2010050) below — deleting Emberreach
        // alone must SKIP this depot (Frostline is still cached), and
        // deleting Frostline afterward must skip it right back (Emberreach
        // is still cached) — the "sole holder" case only fires once both
        // are gone. See web/tests/demo-data.test.js.
        { depotid: 2010060, shared: true, size_bytes: 300_000_000 },
      ],
    }),
    makeGame({
      appid: 2010050,
      name: "Frostline Convoy",
      status: "done",
      depots: [{ depotid: 2010060, shared: true, size_bytes: 300_000_000 }],
      // WP 4a.4: the ordinary CONFIRMED case — both timestamps present.
      // Exercises lib/detail-wording.js's CONFIRMED branch ("Confirmed
      // current at X").
      lastManifestCheck: isoAgo(3_600_000),
      // WP 4h.2 fix: exercises "insufficient_data" — SOME manifest data
      // exists (unlike the null default most seed games below keep), but
      // the observation window is under 4h.1's own 14-day honesty floor.
      // Deliberately distinct from `null` (WP 4h.1 pin 2: "we never looked"
      // vs. "we looked, but not long enough" are different honest answers).
      manifestChangeFrequency: "insufficient_data",
      manifestObservationDays: 5,
    }),
    makeGame({
      appid: 2010070,
      name: "Glass Meridian",
      status: "error",
      needsForce: true,
      depots: [{ depotid: 2010071, shared: false, size_bytes: 2_400_000_000 }],
      // WP 4a.8: gives the GC dry-run flow a non-zero `held_back` alongside
      // its `would_delete` — see makeGame()'s header. Aurora Cascade (above)
      // stays held_back=0 on purpose so the pre-existing
      // web/tests/demo-data-gc.test.js assertions keep pinning the plain
      // case unchanged.
      gcReclaimableBytes: 40_000_000,
      gcHeldBackBytes: 15_000_000,
    }),
  ];
}

// ---------------------------------------------------------------------
// Large-library fixture (WP 4e.1) — a gate for the whole desktop-layout
// phase, not a nicety: the round-7 mockup's grid/poll-diff machinery
// (render-plan.js's patch-vs-rebuild decisions, the games/jobs poll loops
// in store.js) has only ever been exercised against the 6 curated games
// above. Appended ON TOP of them (never replacing), so every existing demo
// test that names an exact appid/count above is completely unaffected —
// the default library size is still exactly DEFAULT_LIBRARY_SIZE (6).
// ---------------------------------------------------------------------

const DEFAULT_LIBRARY_SIZE = 6;
const MAX_LIBRARY_SIZE = 400;
const LARGE_LIBRARY_STORAGE_KEY = "steamvault.demoLibrarySize";
// Chosen well clear of every other fixture's appid range in this file (the
// curated seed games above: 2010010-2010070; the Steam-relay fixture below:
// 3300100-3300300) so a large-library run can never collide with either.
const SYNTHETIC_APPID_BASE = 5_000_000;

const SYNTHETIC_ADJECTIVES = [
  "Aurora", "Ember", "Frost", "Iron", "Glass", "Nova", "Cobalt", "Amber",
  "Cinder", "Quartz", "Umber", "Verdant", "Solace", "Rift", "Hollow",
  "Marrow", "Gale", "Onyx", "Pale", "Wraith",
];
const SYNTHETIC_NOUNS = [
  "Cascade", "Horizon", "Meridian", "Hollow", "Drift", "Protocol", "Foundry",
  "Undertow", "Signal", "Vale", "Bastion", "Convoy", "Reach", "Warden",
  "Circuit", "Ledger", "Thicket", "Anchor", "Passage", "Ember",
];

/**
 * Build `count` synthetic games — plausible name, size and depot count,
 * deterministic (no `Math.random()`) so the same `count` always produces
 * the same fixture and a headless test can assert on it directly. Exported
 * for exactly that reason (WP 4e.1 brief: "reachable both from a headless
 * test and from the running app").
 *
 * **What this fixture can, and cannot, measure — say so plainly (WP 4e.1
 * brief).** These games carry no cover art URL of their own; `game-card.js`/
 * `lib/cover-art.js` still derive a real Steam CDN cover URL from `appid` +
 * `name` unconditionally (there is no per-game opt-out), so the grid DOES
 * attempt a real image request per card and falls back to the procedural
 * tile on a 404/network error the same as any never-matched appid would.
 * That exercises DOM node count, poll-diff cost and render-plan patch/
 * rebuild decisions at scale — but NOT the "400 real images landing inside
 * the CSP allowance" half of the phase's own concern, since none of these
 * appids correspond to a real Steam depot with real artwork. Only the
 * operator's own real Steam library (fetched via the on-device/relay path,
 * never this module) exercises that half.
 * @param {number} count
 */
export function generateSyntheticGames(count) {
  const out = [];
  for (let i = 0; i < count; i++) {
    const appid = SYNTHETIC_APPID_BASE + i * 10;
    const name =
      `${SYNTHETIC_ADJECTIVES[i % SYNTHETIC_ADJECTIVES.length]} ` +
      `${SYNTHETIC_NOUNS[(i * 7 + 3) % SYNTHETIC_NOUNS.length]} ${i + 1}`;
    // Roughly 2/3 "on the cache" (done, with depot content), 1/3 not yet
    // downloaded (idle, no depots) — a plausible mixed-library shape, not a
    // degenerate all-cached or all-empty one. Deliberately no "running"/
    // "error" statuses here: those would need a matching job row to stay
    // honest (game-status.js's dispKind can be overridden by a live job),
    // and this fixture is about library-grid scale, not job-polling scale.
    const cached = i % 3 !== 0;
    const depotCount = cached ? 1 + (i % 4) : 0;
    const depots = [];
    for (let d = 0; d < depotCount; d++) {
      // Deterministic pseudo-variety (200 MB .. ~20 GB per depot) instead of
      // one repeated number, so aggregate byte totals differ per game the
      // way a real library's would.
      const sizeBytes = 200_000_000 + ((i * 97 + d * 613) % 40) * 500_000_000;
      depots.push({ depotid: appid + d + 1, shared: false, size_bytes: sizeBytes });
    }
    // needsForce: !cached (Opus review should-fix S1, WP 4e.1 fix round) —
    // makeGame()'s default (false) is wrong for the "idle" half of this
    // fixture: per api/README.md's needs_force lifecycle, a never-filled
    // app is needs_force=1 (nothing has confirmed it current yet); `0`/false
    // is only reached through a successful `done` job (which is exactly
    // what makes THIS game "cached" here). A demo fixture claiming a shape
    // the real API cannot produce (idle + needs_force=false) is a real bug,
    // not a cosmetic one — demo data is a shipped surface with a 1:1 claim.
    out.push(makeGame({ appid, name, status: cached ? "done" : "idle", depots, needsForce: !cached }));
  }
  return out;
}

/** Human-driven toggle (WP 4e.1 brief: "reachable ... from the running app
 * so a human can look at it"). In a browser with demo mode on, run:
 * `localStorage.setItem("steamvault.demoLibrarySize", "400")` then reload —
 * this module's state is built once at import time (see `let games = ...`
 * below), so the preference is read at that moment, not polled on an
 * interval. Any other/missing/non-numeric/too-small value is the ordinary
 * DEFAULT_LIBRARY_SIZE-game demo library, byte-identical to before this WP.
 * Returns DEFAULT_LIBRARY_SIZE unconditionally outside a browser (`window`
 * undefined, e.g. every headless test in web/tests/) — tests that want the
 * large fixture pass an explicit `librarySize` to `resetDemoData()` instead
 * (see its own header). */
function readLibrarySizePreference() {
  try {
    if (typeof window === "undefined" || !window.localStorage) return DEFAULT_LIBRARY_SIZE;
    const raw = window.localStorage.getItem(LARGE_LIBRARY_STORAGE_KEY);
    const n = Number(raw);
    if (Number.isInteger(n) && n > DEFAULT_LIBRARY_SIZE) return Math.min(n, MAX_LIBRARY_SIZE);
    return DEFAULT_LIBRARY_SIZE;
  } catch {
    return DEFAULT_LIBRARY_SIZE; // same fail-closed posture as library.js's readStoredLayout
  }
}

/** The curated 6-game seed list, plus `librarySize - DEFAULT_LIBRARY_SIZE`
 * synthetic games appended on top (0 more when `librarySize` is at or below
 * the default — every existing demo test, which never passes an argument
 * here, sees exactly the unmodified 6-game list it always has). */
function buildGames(librarySize = DEFAULT_LIBRARY_SIZE) {
  return buildCuratedGames().concat(
    generateSyntheticGames(Math.max(0, librarySize - DEFAULT_LIBRARY_SIZE)),
  );
}

function buildJobs() {
  return [
    {
      id: 900001,
      appid: 2010030,
      type: "prefill",
      status: "running",
      created_at: isoAgo(60_000),
      started_at: isoAgo(45_000),
      finished_at: null,
      updated: null,
      up_to_date: null,
      summary_parse_ok: null,
      gc_execute: null,
      paused_at: null,
      stop_request: null,
      log_excerpt:
        "[vault-api] worker claimed job 900001\nDownloading depot 2010031 ...",
      // Demo-only bookkeeping (never serialized into a response): how many
      // more GET /v1/jobs polls before this job "finishes". Call-count
      // driven rather than wall-clock so demo mode needs no timers of its
      // own.
      _demoTicksLeft: 3,
    },
    {
      id: 900000,
      appid: 2010070,
      type: "prefill",
      status: "error",
      created_at: isoAgo(3 * 3_600_000),
      started_at: isoAgo(3 * 3_600_000 - 5_000),
      finished_at: isoAgo(3 * 3_600_000 - 1_000),
      updated: 0,
      up_to_date: 0,
      summary_parse_ok: true,
      gc_execute: null,
      paused_at: null,
      stop_request: null,
      log_excerpt: "[vault-api] SteamPrefill exited 1 — see attached log.",
    },
    {
      id: 899999,
      appid: 2010010,
      type: "prefill",
      status: "done",
      created_at: isoAgo(6 * 3_600_000),
      started_at: isoAgo(6 * 3_600_000 - 5_000),
      finished_at: isoAgo(6 * 3_600_000 - 1_000),
      updated: 12,
      up_to_date: 0,
      summary_parse_ok: true,
      gc_execute: null,
      paused_at: null,
      stop_request: null,
      log_excerpt: "[vault-api] worker claimed job 899999\nPrefilled 12 apps.",
    },
  ];
}

// ---------------------------------------------------------------------
// Agent presence (WP WEB-FEAT-3, mirroring WP AGENT-FEAT-1's server rule)
// ---------------------------------------------------------------------

/** api/vault_api/agent_reports.py `ASSUMED_REPORT_INTERVAL_SECONDS`: the
 * interval presence assumes when a report stated none. Pinned against the
 * server constant in web/tests/demo-data-shape-guard.test.js. */
export const DEMO_ASSUMED_REPORT_INTERVAL_SECONDS = 1800;
/** api/vault_api/agent_reports.py `PRESENCE_GRACE_SECONDS` (same pin). */
export const DEMO_PRESENCE_GRACE_SECONDS = 300;

/** Server timestamp format (`to_utc_iso`): whole seconds, `Z`, no millis. */
function utcIsoSeconds(ms) {
  return new Date(Math.floor(ms / 1000) * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * `(presence, offline_after)` exactly as `agent_reports.presence` computes
 * it: offline once the last report is OLDER than 2 x interval + 5 min
 * (still online at exactly that age), interval = the stated one or 1800 s,
 * `("offline", null)` for an unreadable timestamp. (The server also
 * range-checks a stated interval, `valid_report_interval`; the demo's seed
 * values are 600 or null, both inside it.) Demo-side only — the web
 * UI itself never calls this; it reads the `presence` field the (demo)
 * server returns, like it does for a real server.
 * @param {string} lastReportedAt
 * @param {number | null} intervalSeconds
 * @param {number} nowMs
 * @returns {[("online"|"offline"), (string|null)]}
 */
export function demoPresence(lastReportedAt, intervalSeconds, nowMs) {
  const last = Date.parse(lastReportedAt);
  if (!Number.isFinite(last)) return ["offline", null];
  const interval =
    Number.isInteger(intervalSeconds) && intervalSeconds > 0 ? intervalSeconds : DEMO_ASSUMED_REPORT_INTERVAL_SECONDS;
  const deadline = last + (2 * interval + DEMO_PRESENCE_GRACE_SECONDS) * 1000;
  return [nowMs <= deadline ? "online" : "offline", utcIsoSeconds(deadline)];
}

/**
 * Seed clients. Two rows on purpose (WP WEB-FEAT-3 brief, AGENT-FEAT-1
 * review S1):
 *  - workshop-pc: a current agent (version + 600 s interval) that reported
 *    4 minutes ago -> online. `_demoReportEveryMs` (demo-only, never
 *    serialized) re-stamps its report on GET once an interval has passed,
 *    standing in for the real 10-minute schedule so a long demo session
 *    does not see it drift offline.
 *  - loft-laptop: a legacy agent (`agent_version`/`report_interval_seconds`
 *    null -> 30 min assumed) last heard from 2 h ago -> offline, and still
 *    listed (offline PCs are not hidden).
 * `presence`/`offline_after` are NOT stored: like the server, they are
 * computed per request ({@link demoPresence}), one `now` per answer.
 */
function buildClients() {
  return [
    {
      client_id: "workshop-pc",
      first_seen: isoAgo(30 * 86_400_000),
      last_reported_at: utcIsoSeconds(Date.now() - 4 * 60_000),
      app_count: 148,
      source_addrs: ["10.10.0.21"],
      cache_hits: 4213,
      cache_misses: 96,
      bytes_served: 812_345_678_912,
      last_seen_in_cache_log: isoAgo(120_000),
      bypass_suspected: false,
      agent_version: "0.1.0",
      report_interval_seconds: 600,
      _demoReportEveryMs: 600_000,
    },
    {
      client_id: "loft-laptop",
      first_seen: isoAgo(9 * 86_400_000),
      last_reported_at: utcIsoSeconds(Date.now() - 2 * 3_600_000),
      app_count: 42,
      source_addrs: ["10.10.0.44"],
      cache_hits: 3,
      cache_misses: 1,
      bytes_served: 41_943_040,
      last_seen_in_cache_log: null,
      bypass_suspected: true,
      agent_version: null,
      report_interval_seconds: null,
    },
  ];
}

/** `GET /v1/clients` (demo): the stored fields plus per-request presence.
 * Every key the server's `ClientOut` has, nothing else (pinned). */
function handleGetClients() {
  const nowMs = Date.now();
  return clients.map((c) => {
    if (typeof c._demoReportEveryMs === "number" && nowMs - Date.parse(c.last_reported_at) > c._demoReportEveryMs) {
      c.last_reported_at = utcIsoSeconds(nowMs - 30_000); // the "agent" just reported again
    }
    const [presence, offlineAfter] = demoPresence(c.last_reported_at, c.report_interval_seconds, nowMs);
    const { _demoReportEveryMs, ...wire } = c;
    return { ...wire, presence, offline_after: offlineAfter };
  });
}

// ---------------------------------------------------------------------
// GET /v1/about (WP WEB-FEAT-3, mirroring WP VER-2's AboutOut)
// ---------------------------------------------------------------------

/**
 * Six components, server order, plausible for a fresh queue-mode install
 * with the egress proxy and no vault-dns: vault-core `unknown` with the
 * "recorded at last start" detail it always has (never probed, user
 * decision "Weg A"), vault-dns `unknown` and never probed. Detail texts
 * follow api/vault_api/about.py's wording. Fictional versions/commits.
 */
function handleGetAbout() {
  const nowMs = Date.now();
  const checkedAt = utcIsoSeconds(nowMs);
  const coreRecordedAt = utcIsoSeconds(nowMs - 26 * 3_600_000);
  const commit = "3f9c2a71d4be08e5c6a1f02b9d7e4c18a5b6f3d0";
  const notProbed =
    "vault-api has no network path to vault-core (egress lock, ADR-0011), so whether it is running now is not checked.";
  return {
    components: [
      {
        name: "vault-api",
        version: "0.1.0",
        commit,
        status: "ok",
        checked_at: checkedAt,
        detail: "Also serves the web UI, so the web UI has this version.",
      },
      {
        name: "vault-core",
        version: "0.1.0",
        commit,
        status: "unknown",
        checked_at: checkedAt,
        detail: `Recorded at vault-core's last start, ${coreRecordedAt}. ${notProbed}`,
      },
      {
        name: "vault-runner",
        version: "0.1.0",
        commit,
        status: "ok",
        checked_at: checkedAt,
        detail: "Last seen 12 s ago.",
      },
      {
        name: "steamprefill",
        version: "3.7.2",
        commit: null,
        status: "ok",
        checked_at: checkedAt,
        detail: "Runs in vault-runner; version as the runner reported it.",
      },
      {
        name: "vault-proxy",
        version: null,
        commit: null,
        status: "ok",
        checked_at: checkedAt,
        detail:
          "Answers, and refuses a host that is not on the egress allowlist. Its version is not shown: reading it would need a hole in the egress filter.",
      },
      {
        name: "vault-dns",
        version: null,
        commit: null,
        status: "unknown",
        checked_at: checkedAt,
        detail:
          "Optional (compose profile dns); most setups use their own DNS rewrite instead. Not checked: vault-api has no network path to vault-dns (egress lock, ADR-0011), and a test query would itself be DNS traffic.",
      },
    ],
  };
}

let games = buildGames(readLibrarySizePreference());
let jobs = buildJobs();
let clients = buildClients();
let nextJobId = 900002;

/** Restore all demo state to a fresh copy of the seed data. Exported for
 * tests (web/tests/demo-data.test.js and friends) so scenarios don't leak
 * between cases; the real app never calls this — a page reload does the
 * same thing by re-importing the module.
 *
 * WP 4e.1: accepts an optional `librarySize` so the large-library fixture
 * is reachable from a headless test too (`resetDemoData({ librarySize: 400
 * })`), without needing a fake `window`/`localStorage` — every existing
 * call site (`resetDemoData()`, no argument) is completely unaffected:
 * `librarySize` falls back to `readLibrarySizePreference()`, which itself
 * always returns `DEFAULT_LIBRARY_SIZE` outside a browser (`window` is
 * undefined in every bare-Node test in web/tests/).
 *
 * WP 4h.2: `relayExposePlaytime`/`relayExposeLastPlayed` are demo mode's
 * "restart with the env var set" analogue for the two ADR-0010 relay-privacy
 * settings — see `ENV_ONLY_DEMO`'s own comment. Both default to `false`
 * (every existing call site, including every test written before this WP,
 * gets the DEFAULT gate-off shape unchanged — `playtime_forever`/
 * `rtime_last_played` simply absent from every owned-games entry, matching
 * the real relay's `response_model_exclude_unset` behaviour, not a fabricated
 * `0`/`null`).
 * @param {{ librarySize?: number, relayExposePlaytime?: boolean, relayExposeLastPlayed?: boolean }} [options]
 */
export function resetDemoData({ librarySize, relayExposePlaytime: rp = false, relayExposeLastPlayed: rl = false } = {}) {
  games = buildGames(librarySize ?? readLibrarySizePreference());
  jobs = buildJobs();
  clients = buildClients();
  nextJobId = 900002;
  resetDemoSettings();
  resetDemoSteamRelay();
  relayExposePlaytime = rp;
  relayExposeLastPlayed = rl;
}

// ---------------------------------------------------------------------
// /v1/settings (WP 4a.6; ADR-0009) — a small in-memory mirror of
// vault_api/settings_store.py's precedence rule (db override > env value >
// built-in default), enough to exercise the Settings view's real code path
// in demo mode. `env` below stands in for "what Settings.from_env() would
// have produced" — fixed per key rather than reading real env vars (demo
// mode has no process env of its own to read).
// ---------------------------------------------------------------------

const WEBHOOK_EVENTS_ALL = Object.freeze([
  "job.done",
  "job.error",
  "job.cancelled",
  "client.bypass_suspected",
  "client.bypass_resolved",
]);
const AUTO_GC_MODES = Object.freeze(["off", "dry-run", "execute"]);

// key -> {default, env}. `env` != `default` for vault_name/schedule_window
// on purpose, so a fresh demo session shows a realistic mix of "default"
// and "env" sourced rows, not everything defaulted.
// **The `default`/`env` values below for `auto_gc`/`sweep_include_cached`
// are NOT this file's own opinion — they must equal
// `api/vault_api/config.py`'s `DEFAULT_AUTO_GC`/`DEFAULT_SWEEP_INCLUDE_CACHED`
// constants, whatever those currently are (ADR-0014 flipped both to
// `"execute"`/`true` together, 2026-08-22 — see that module's own comments
// on `DEFAULT_AUTO_GC`/`DEFAULT_SWEEP_INCLUDE_CACHED` for the full "why",
// and docs/adr/0014-sweep-cached-and-auto-gc-default-on.md). A demo fixture
// asserting the WRONG default is worse than no fixture — it teaches the
// reader something false on exactly the surface used for screenshots
// (docs/LEARNINGS.md, "demo fixtures are a shipped surface"). This file has
// no access to Python at test time, so it cannot import those constants —
// `web/tests/demo-data-config-defaults.test.js` instead reads
// `api/vault_api/config.py` as plain TEXT and regex-extracts the same
// constants, asserting equality against the two literals below on every
// suite run; if you change either default here, that test tells you within
// the same `node --test` run whether it still agrees with config.py, and if
// you change config.py's default, THAT test (not this comment) is what
// catches a forgotten update here.
export const CONFIG_DEFAULT_AUTO_GC = "execute";
export const CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED = true;

const SETTINGS_BASE = {
  vault_name: { default: "", env: "steamhangar-demo" },
  schedule_window: { default: null, env: "22:00-06:00" },
  schedule_interval_minutes: { default: 180, env: 180 },
  schedule_client_stale_days: { default: 7, env: 7 },
  sweep_include_cached: { default: CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED, env: CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED },
  auto_gc: { default: CONFIG_DEFAULT_AUTO_GC, env: CONFIG_DEFAULT_AUTO_GC },
  webhook_url: { default: "", env: "" },
  webhook_events: { default: [...WEBHOOK_EVENTS_ALL], env: [...WEBHOOK_EVENTS_ALL] },
  // WP WEB-FEAT-1 (mirrors WP API-FEAT-1's `steam_library_steamid`): blank by
  // default, like a fresh real vault — set it in Settings (after a demo Steam
  // key) to see the owned-games merge in the Library.
  steam_library_steamid: { default: "", env: "" },
};

const SETTINGS_APPLIES = {
  vault_name: "restart-required",
  schedule_window: "next_sweep",
  schedule_interval_minutes: "next_sweep",
  schedule_client_stale_days: "next_sweep",
  sweep_include_cached: "next_sweep",
  auto_gc: "immediately",
  webhook_url: "restart-required",
  webhook_events: "restart-required",
  steam_library_steamid: "immediately",
};

// Same true/false spellings as api/vault_api/config.py's parse_strict_bool
// (config.py's _BOOL_TRUE_VALUES/_BOOL_FALSE_VALUES) — the grammar
// sweep_include_cached's real PATCH validation uses.
const BOOL_TRUE_SPELLINGS = ["1", "true", "yes", "on"];
const BOOL_FALSE_SPELLINGS = ["0", "false", "no", "off"];

// WP 4h.2 fix (carried over BY NAME from the WP 4h.0 review — this module
// diverged from the real API when 4h.0 landed api/-only, WP 4h.1 same):
// `relay_expose_playtime`/`relay_expose_last_played` (ADR-0010,
// settings_store.ENV_ONLY_INFO_KEYS's WP 4h.0 addendum) are two MORE
// env-only, informational settings rows the real `GET /v1/settings` has
// carried since WP 4h.0 that this fixture never grew. Read dynamically
// (`get value()`, not a static literal like every other row below) because,
// unlike every other env-only key here, these two have a demo-reachable
// "restart" analogue: `resetDemoData({ relayExposePlaytime,
// relayExposeLastPlayed })` is demo mode's stand-in for "set the env var and
// restart", the same one-way, boot-time-only knob ADR-0010 requires of the
// real server (there is deliberately no PATCH path for either, in demo mode
// or the real one).
let relayExposePlaytime = false;
let relayExposeLastPlayed = false;

// Mirrors settings_store.ENV_ONLY_INFO_KEYS — informational-only rows a
// settings screen shows as "this exists but only the environment controls
// it" (api/README.md "Env-only keys"). `vault_api_key` is excluded here
// too, same reasoning as the real endpoint: it never appears in ANY GET
// response, not even redacted.
const ENV_ONLY_DEMO = [
  { key: "db_path", value: "/data/vault.db" },
  { key: "cache_root", value: "/vault/cache" },
  { key: "steamprefill_path", value: "/usr/local/bin/steamprefill" },
  { key: "steamprefill_cache_dir", value: "/root/.local/share/SteamPrefill" },
  { key: "manifest_archive_dir", value: "/vault/manifest-archive" },
  { key: "web_dir", value: "/app/web" },
  { key: "settings_readonly", value: false },
  { key: "relay_expose_playtime", get value() { return relayExposePlaytime; } },
  { key: "relay_expose_last_played", get value() { return relayExposeLastPlayed; } },
];
const ENV_ONLY_KEYS = new Set(["vault_api_key", ...ENV_ONLY_DEMO.map((e) => e.key)]);

let settingsOverrides = {}; // key -> raw string, mirrors the `settings` table's TEXT column
const settingsReadonly = false; // demo mode never ships VAULT_SETTINGS_READONLY

function resetDemoSettings() {
  settingsOverrides = {};
}

/** Raw PATCH-body value -> the typed value, or throw a 422 ApiError — the
 * demo-mode mirror of each `SettingSpec.parse` in settings_store.py. Typed
 * values are only used internally (to decide "did this change") and for
 * the GET projection; PATCH itself stores the raw string, same as the real
 * `settings` table. */
function parseSettingValue(key, raw) {
  if (key === "vault_name") return raw.trim();
  if (key === "schedule_window") {
    const text = raw.trim();
    if (!text) return null;
    if (!/^\d{2}:\d{2}-\d{2}:\d{2}$/.test(text)) {
      throw validationError(`'${key}': expected 'HH:MM-HH:MM' (e.g. '22:00-06:00'), got ${JSON.stringify(raw)}.`);
    }
    return text;
  }
  if (key === "schedule_interval_minutes" || key === "schedule_client_stale_days") {
    const text = raw.trim();
    if (!/^[0-9]+$/.test(text) || Number(text) < 1) {
      throw validationError(`'${key}' must be a positive whole number, got ${JSON.stringify(raw)}.`);
    }
    return Number(text);
  }
  if (key === "auto_gc") {
    const text = raw.trim();
    if (!AUTO_GC_MODES.includes(text)) {
      throw validationError(`'${key}' must be one of ${AUTO_GC_MODES.join(", ")}, got ${JSON.stringify(raw)}.`);
    }
    return text;
  }
  if (key === "sweep_include_cached") {
    const text = raw.trim().toLowerCase();
    if (BOOL_TRUE_SPELLINGS.includes(text)) return true;
    if (BOOL_FALSE_SPELLINGS.includes(text)) return false;
    throw validationError(
      `'${key}' must be one of ${BOOL_TRUE_SPELLINGS.join(", ")} (true) or ` +
        `${BOOL_FALSE_SPELLINGS.join(", ")} (false), case-insensitive. Got ${JSON.stringify(raw)}.`,
    );
  }
  if (key === "webhook_url") {
    const text = raw.trim();
    if (text && !/^https?:\/\//i.test(text)) {
      throw validationError(`'${key}' must be a http(s) URL or blank to disable, got ${JSON.stringify(raw)}.`);
    }
    return text;
  }
  if (key === "webhook_events") {
    const text = raw.trim();
    if (!text) return [...WEBHOOK_EVENTS_ALL];
    const tokens = text.split(",").map((t) => t.trim());
    if (tokens.some((t) => !t)) {
      throw validationError(`'${key}' must be a comma-separated list with no empty entries.`);
    }
    const unknown = tokens.filter((t) => !WEBHOOK_EVENTS_ALL.includes(t));
    if (unknown.length) {
      throw validationError(`'${key}' contains unknown event name(s): ${unknown.join(", ")}.`);
    }
    return [...new Set(tokens)];
  }
  if (key === "steam_library_steamid") {
    // Mirrors config.parse_steam_library_steamid: trimmed, blank = not set,
    // otherwise the relay's own SteamID64 grammar (lib/steamid.js here).
    const text = raw.trim();
    if (!text) return "";
    if (!validSteamId64(text)) {
      throw validationError(
        `'${key}': must be a SteamID64: exactly 17 ASCII digits in the individual-account range; blank (or null over the API) clears it`,
      );
    }
    return text;
  }
  throw validationError(`unrecognised setting key ${JSON.stringify(key)}`); // unreachable: callers only pass known keys
}

/** One entry's `effective`/`source`/`fallback`, mirroring
 * settings_store.describe_settings's precedence exactly (db > env > default). */
function describeDemoSettings() {
  const infos = [];
  for (const key of Object.keys(SETTINGS_BASE)) {
    const base = SETTINGS_BASE[key];
    const raw = settingsOverrides[key];
    let effective;
    let source;
    if (raw !== undefined) {
      effective = parseSettingValue(key, raw);
      source = "db";
    } else {
      effective = base.env;
      source = JSON.stringify(base.env) === JSON.stringify(base.default) ? "default" : "env";
    }
    infos.push({
      key,
      effective,
      source,
      fallback: base.env,
      applies: SETTINGS_APPLIES[key],
      env_only: false,
    });
  }
  for (const entry of ENV_ONLY_DEMO) {
    // The two ADR-0010 relay-privacy keys report "env" whenever
    // resetDemoData() turned them on, mirroring the real server's
    // `_env_var_is_set(...)` check — every other row here has no demo
    // "restart" analogue at all, so it stays "default" the way it always
    // has (unchanged by this fix).
    const source =
      (entry.key === "relay_expose_playtime" || entry.key === "relay_expose_last_played") && entry.value === true
        ? "env"
        : "default";
    infos.push({
      key: entry.key,
      effective: entry.value,
      source,
      fallback: entry.value,
      applies: "restart-required",
      env_only: true,
    });
  }
  return infos;
}

/** One `PATCH` body value -> the raw string `parseSettingValue` expects, or
 * throw a 422 — mirrors routers/settings.py's `_coerce_patch_value`
 * (booleans rejected explicitly, `webhook_events` accepts a list). */
function coercePatchValue(key, value) {
  if (typeof value === "boolean") {
    throw validationError(`'${key}' must be a string, not a boolean.`);
  }
  if (typeof value === "string") return value;
  if (typeof value === "number" && key === "steam_library_steamid") {
    // routers/settings.py's WP API-FEAT-1 S1 rule: a JSON number for a
    // 17-digit id is already rounded by a JavaScript sender.
    throw validationError(
      `'${key}': send the SteamID64 as a JSON string (17 digits exceed JavaScript's safe integer range).`,
    );
  }
  if (typeof value === "number") return String(value);
  if (Array.isArray(value) && key === "webhook_events") {
    if (!value.every((v) => typeof v === "string")) {
      throw validationError(`'${key}': every list item must be a string event name.`);
    }
    return value.join(",");
  }
  throw validationError(`'${key}' must be a string, or null to clear the override.`);
}

// WP 4e.6 (rail foot): mirrors vault_api/__init__.py's real `__version__`
// constant — a hand-maintained string, sibling of `readonly`, never a
// `settings` row (PATCH has nothing to reject here since demo mode has no
// PATCH validation for it at all, matching the real server's "unrecognised
// key" rejection in spirit: this fixture simply never accepts writes to it).
const DEMO_SERVER_VERSION = "0.1.0";

function handleGetSettings() {
  return { readonly: settingsReadonly, server_version: DEMO_SERVER_VERSION, settings: describeDemoSettings() };
}

function handlePatchSettings(body) {
  if (settingsReadonly) {
    throw new ApiError(ERROR_KINDS.VALIDATION, "The settings API is read-only.", { status: 403 });
  }
  const toSet = [];
  const toClear = [];
  for (const [key, value] of Object.entries(body || {})) {
    if (ENV_ONLY_KEYS.has(key)) {
      // WP 4h.2 fix: byte-identical to routers/settings.py's
      // `_ENV_ONLY_DETAIL_TEMPLATE` (read at that file's own definition, not
      // guessed) — the previous, shorter demo string was already wrong for
      // the seven pre-existing env-only keys, not just the two ADR-0010
      // relay ones this fix set out to add; fixing the ONE template fixes
      // parity for all nine at once.
      throw validationError(
        `'${key}' is environment-only and cannot be changed via the API; set its environment variable and restart instead.`,
      );
    }
    if (!(key in SETTINGS_BASE)) {
      throw validationError(`'${key}' is not a recognised setting.`);
    }
    if (value === null) {
      toClear.push(key);
      continue;
    }
    const raw = coercePatchValue(key, value);
    parseSettingValue(key, raw); // validate only, same "validate everything first" order
    toSet.push([key, raw]);
  }
  // Everything validated above before anything is written (ADR-0009: a bad
  // value in a multi-key PATCH must persist nothing).
  for (const [key, raw] of toSet) settingsOverrides[key] = raw;
  for (const key of toClear) delete settingsOverrides[key];
  return handleGetSettings();
}

// ---------------------------------------------------------------------
// /v1/schedule (WP 4d-web) — mirrors api/vault_api/routers/schedule.py's
// `ScheduleOut` shape closely enough to exercise the Settings view's
// sweep-status line and cached-GC-risk warning in demo mode.
//
// `last_sweep_*`/`next_eligible_at` are a static, plausible fixture — this
// demo model has no real scheduler tick to derive them from, same posture
// as `buildJobs()`'s hand-authored history above. `sweep_include_cached`/
// `sweep_cached_gc_risk` are NOT static: both are derived from
// `describeDemoSettings()`'s live result, so toggling "Include cached
// games" or "Auto-GC" in the Settings view and re-fetching `/v1/schedule`
// reflects the change immediately — the same "no restart needed for
// `next_sweep`/`immediately` keys" property `settings_store.
// effective_settings` gives the real endpoint. `sweep_cached_gc_risk` is
// computed with the EXACT SAME formula as
// `vault_api/scheduler.py::cached_sweep_gc_risk`
// (`sweep_include_cached and auto_gc != "execute"`) — this is the one place
// in this demo model allowed to restate that formula, mirroring the real
// server's one place (`scheduler.py`); everything downstream (the Settings
// view, `lib/schedule-presentation.js`) only ever reads the already-
// computed field, never recomputes it (docs/LEARNINGS.md: "two call sites
// computing the same domain predicate WILL diverge").
// ---------------------------------------------------------------------

const DEMO_LAST_SWEEP = Object.freeze({
  at: isoAgo(42 * 60_000),
  targets: 3,
  enqueued: 1,
});

function handleGetSchedule() {
  const infos = describeDemoSettings();
  const effective = new Map(infos.map((i) => [i.key, i.effective]));
  const window = effective.get("schedule_window");
  const sweepIncludeCached = effective.get("sweep_include_cached") === true;
  const autoGc = effective.get("auto_gc");
  return {
    enabled: typeof window === "string" && window.length > 0,
    window: window ?? null,
    overnight: false,
    interval_minutes: effective.get("schedule_interval_minutes"),
    client_stale_days: effective.get("schedule_client_stale_days"),
    server_timezone: "UTC+00:00",
    last_sweep_at: DEMO_LAST_SWEEP.at,
    last_sweep_targets: DEMO_LAST_SWEEP.targets,
    last_sweep_enqueued: DEMO_LAST_SWEEP.enqueued,
    next_eligible_at: null,
    sweep_include_cached: sweepIncludeCached,
    sweep_cached_gc_risk: sweepIncludeCached && autoGc !== "execute",
  };
}

// ---------------------------------------------------------------------
// /v1/steam/* — the opt-in Steam Web API relay (WP 4a.6r; ADR-0004
// addendum). Demo mode never actually calls Valve; it validates the same
// shapes the real relay does (via the shared `lib/` validators) and answers
// from a small fixture library once a syntactically valid key is "set".
// ---------------------------------------------------------------------

let steamKeyConfigured = false;
let steamKeyLast4 = null;

function resetDemoSteamRelay() {
  steamKeyConfigured = false;
  steamKeyLast4 = null;
}

// Fictional owned-games fixture (LEARNINGS "Testing discipline": synthetic,
// never real Steam data) — deliberately a DIFFERENT list from the cache
// library's demo games above: this is what "the Steam Web API says this
// account owns", which in real life is almost always a much bigger,
// unrelated set from what happens to be cached. The two lists OVERLAP on
// purpose (WP WEB-FEAT-1): 2010010/2010040 are vault games too, the other
// three are owned-only, so the Library's merge shows both cases (dedupe by
// appid, plus "Not cached" owned-only rows).
//
// WP 4h.2 fix (named per the review that carried this defect forward): this
// is now the DEFAULT-GATE shape — `playtime_forever`/`rtime_last_played`
// keys ABSENT, matching `api/vault_api/routers/steam.py`'s
// `response_model_exclude_unset=True` behaviour when both ADR-0010 env keys
// are off (the shipped default, WP 4h.0). The previous version of this
// fixture carried `playtime_forever` on every entry unconditionally, which
// was the shape of a NON-default gate state masquerading as the baseline —
// exactly the "demo fixtures are a shipped surface, shapes 1:1 with the real
// API" rule (docs/LEARNINGS.md) applied to the gate dimension, not just the
// field list. The enabled-gate shape is `DEMO_OWNED_GAMES_PLAYTIME` below,
// an explicit second table merged in only when `resetDemoData()` was told to
// turn the corresponding key on — never the baseline.
const DEMO_OWNED_GAMES = [
  { appid: 2010010, name: "Aurora Cascade", img_icon_url: "" },
  { appid: 2010040, name: "Emberreach", img_icon_url: "" },
  { appid: 3300100, name: "Sable Undertow", img_icon_url: "" },
  { appid: 3300200, name: "Halcyon Foundry", img_icon_url: "" },
  { appid: 3300300, name: "Quietbrook", img_icon_url: "" },
];

// The enabled-gate fixture (WP 4h.2) — keyed by appid, consulted only when
// `relayExposePlaytime`/`relayExposeLastPlayed` is true. Deliberately
// includes one appid (3300100) with `playtime_forever` but no
// `rtime_last_played` at all, exercising the two ADR-0010 keys' independence
// (an operator may expose the aggregate hour count while still refusing the
// exact last-played date) even when BOTH gate settings are on — a caller
// that turned on `relayExposeLastPlayed` must not see a fabricated
// last-played value for an app this fixture never recorded one for.
const DEMO_OWNED_GAMES_PLAYTIME = {
  2010010: { playtime_forever: 4312, rtime_last_played: 1_755_000_000 },
  2010040: { playtime_forever: 118, rtime_last_played: 1_754_000_000 },
  3300100: { playtime_forever: 972 },
  3300200: { playtime_forever: 26, rtime_last_played: 1_753_000_000 },
  3300300: { playtime_forever: 0 }, // real, explicit "never played" — not absence
};

/** One `DEMO_OWNED_GAMES` entry -> the wire shape for the CURRENT gate
 * state — mirrors `routers/steam.py`'s `_owned_game_out` outermost-
 * conversion gate (ADR-0010): each field is added to the object only when
 * its OWN setting is on AND this fixture actually has a value for it; never
 * a fabricated `0`/`null` standing in for "on but no data", and never
 * present at all when the gate is off, so `JSON.stringify` behaves exactly
 * like the real `response_model_exclude_unset=True` response. */
function demoOwnedGameForCurrentGate(base) {
  const extra = {};
  const pt = DEMO_OWNED_GAMES_PLAYTIME[base.appid];
  if (relayExposePlaytime && pt && typeof pt.playtime_forever === "number") {
    extra.playtime_forever = pt.playtime_forever;
  }
  if (relayExposeLastPlayed && pt && typeof pt.rtime_last_played === "number") {
    extra.rtime_last_played = pt.rtime_last_played;
  }
  return { ...base, ...extra };
}

function demoPlayerSummary(steamid) {
  return {
    steamid,
    personaname: "vaultkeeper_demo",
    avatar: "https://avatars.steamstatic.com/demo_small.jpg",
    avatarmedium: "https://avatars.steamstatic.com/demo_medium.jpg",
    avatarfull: "https://avatars.steamstatic.com/demo_full.jpg",
    personastate: 1,
  };
}

function handleGetSteamKey() {
  return { configured: steamKeyConfigured, key_last4: steamKeyLast4 };
}

function handlePutSteamKey(body) {
  const key = body && body.key;
  if (!validSteamWebApiKey(key)) {
    throw validationError("'key' must be exactly 32 hexadecimal characters.");
  }
  steamKeyConfigured = true;
  steamKeyLast4 = key.slice(-4).toUpperCase();
  return handleGetSteamKey();
}

function handleDeleteSteamKey() {
  resetDemoSteamRelay();
  return null; // real endpoint answers 204 No Content
}

function requireSteamConfigured() {
  if (!steamKeyConfigured) {
    throw conflict("The Steam Web API relay is not configured. Set a key first (PUT /v1/steam/key).");
  }
}

function requireValidSteamId(raw) {
  const steamid = validSteamId64(typeof raw === "string" ? raw : String(raw ?? ""));
  if (!steamid) {
    throw validationError(`'${raw}' is not a valid SteamID64.`);
  }
  return steamid;
}

// ---------------------------------------------------------------------
// Projections (internal seed shape -> exact wire shape)
// ---------------------------------------------------------------------

function appSizeBytes(depots) {
  if (depots.length === 0) return null; // unmapped
  return depots.reduce((sum, d) => sum + (d.size_bytes ?? 0), 0);
}

function gameSummary(g) {
  return {
    appid: g.appid,
    name: g.name,
    status: g.status,
    last_prefill_at: g.last_prefill_at,
    // WP 4a.4: was missing from this projection entirely — the real
    // GameSummary model (api/vault_api/routers/games.py) has carried this
    // field since the WP 4c mini-WP, and the detail sheet's "confirmed
    // current" wording (lib/detail-wording.js) needs it to exercise all
    // three branches in demo mode.
    last_manifest_check: g.last_manifest_check,
    depot_count: g.depots.length,
    size_bytes: appSizeBytes(g.depots),
    needs_force: g.needs_force,
    // WP 4h.2 fix: this projection never carried WP 4h.1's three fields at
    // all (see makeGame()'s own comment on the same gap) — added here
    // straight from the seed object, same as every other pass-through field
    // on this line.
    manifest_change_frequency: g.manifest_change_frequency,
    manifest_observation_days: g.manifest_observation_days,
    manifest_days_since_last_change: g.manifest_days_since_last_change,
    // WP AG-2: additive field on GameSummary too (api/README.md "Installed
    // state per app" — both GET /v1/games and GET /v1/games/{appid} carry
    // it). Each entry copied fresh so a caller mutating the returned object
    // can never corrupt this module's own seed state.
    installed_on: g.installed_on.map((e) => ({ ...e })),
    // WP API-FIX-4: additive on both game shapes. No demo game is a Steam
    // tool app (the demo library is owned games only), so always false/null.
    tool_app: false,
    tool_app_name: null,
  };
}

function gameDetail(g) {
  return {
    appid: g.appid,
    name: g.name,
    status: g.status,
    last_prefill_at: g.last_prefill_at,
    last_manifest_check: g.last_manifest_check,
    manifest_change_frequency: g.manifest_change_frequency,
    manifest_observation_days: g.manifest_observation_days,
    manifest_days_since_last_change: g.manifest_days_since_last_change,
    depots: g.depots.map(({ depotid, shared, size_bytes }) => ({
      depotid,
      shared,
      size_bytes,
    })),
    size_bytes: appSizeBytes(g.depots),
    needs_force: g.needs_force,
    installed_on: g.installed_on.map((e) => ({ ...e })), // WP AG-2 (see gameSummary's own comment)
    tool_app: false, // WP API-FIX-4 (see gameSummary's own comment)
    tool_app_name: null,
  };
}

function jobSummary(j) {
  const {
    id,
    appid,
    type,
    status,
    created_at,
    started_at,
    finished_at,
    updated,
    up_to_date,
    summary_parse_ok,
    gc_execute,
    paused_at,
    stop_request,
  } = j;
  return {
    id,
    appid,
    type,
    status,
    created_at,
    started_at,
    finished_at,
    updated,
    up_to_date,
    summary_parse_ok,
    gc_execute,
    paused_at,
    stop_request,
  };
}

function jobDetail(j) {
  return { ...jobSummary(j), log_excerpt: j.log_excerpt };
}

// ---------------------------------------------------------------------
// Demo-only simulation
// ---------------------------------------------------------------------

function findGame(appid) {
  return games.find((g) => g.appid === appid);
}
function findJob(id) {
  return jobs.find((j) => j.id === id);
}

// ADR-0003 shared-depot vocabulary, at MODULE scope so both consumers share
// one definition (WP WEB-FIX-1, closing the WP 4f carry-over recorded in
// docs/WORKPACKAGES.md "Carry-overs" #1): the DELETE /v1/cache/{appid}
// handler's per-depot protection check, and `selectCachedAppids()`'s
// "which apps hold cache content" selection. Until this WP the two helpers
// were function-scoped inside the DELETE branch and the selection used a
// different, more generous predicate — the exact two-callers-two-answers
// drift the real backend's `deletion.appids_with_cache_content` (WP 4f)
// exists to remove.
//
// "Has cache content" mirrors the real predicate exactly
// (`deletion._has_cache_content`): it is a STATUS check
// (status/last_prefill_at/active-job), not a live disk scan — a depot's
// bytes being physically still present on a co-owner's shared mapping does
// NOT by itself count, because `DELETE /v1/cache/{appid}` unconditionally
// resets that app's own status to 'idle' and last_prefill_at to null even
// when everything it mapped was protected shared content. This is what
// actually lets a shared depot become a "last cached remnant" on a LATER
// call — a depots-array-only proxy (a co-owner's own retained mapping to
// the very depot being evaluated) can never reach that state, since it is
// trivially always "true" for the depot in question.
function hasCacheContent(g) {
  const hasActiveJob = jobs.some(
    (j) => j.appid === g.appid && ["queued", "running", "paused"].includes(j.status),
  );
  const idle = g.status === "idle";
  const neverPrefilled = g.last_prefill_at === null;
  return !(idle && neverPrefilled && !hasActiveJob);
}

/** Every game OTHER than `appid` that currently maps `depotid`. */
function otherOwners(appid, depotid) {
  return games.filter((g) => g.appid !== appid && g.depots.some((d) => d.depotid === depotid));
}

/** The real `deletion.appids_with_cache_content` rule (WP 4f: "exclusive +
 * remnant, not exclusive alone"), expressed against this demo's
 * depots-array model: `g` holds cache content iff at least one depot it
 * maps is either EXCLUSIVE to it (no other current owner) or a LAST-CACHED
 * REMNANT (every other current owner is itself uncached by
 * `hasCacheContent`). A depot that at least one OTHER owner still holds
 * cached content for — `DeletionPlan.shared` on the real side — does not
 * count, which is what keeps a just-deleted game out of the selection when
 * its only surviving mapping is a depot a co-owner protected. */
function hasSelectableCacheContent(g) {
  return g.depots.some((d) => otherOwners(g.appid, d.depotid).every((o) => !hasCacheContent(o)));
}

/** Enqueue-or-dedupe ONE appid — the single enqueue mechanism shared by both
 * `POST /v1/prefill` and `POST /v1/prefill/cached` (WP 4c-web), mirroring
 * the real `jobs.enqueue_prefill` being the one function BOTH real routes
 * call (api/README.md "Check & update all cached games": "No new enqueue
 * mechanism"). Dedupes against any job for this appid that is
 * `queued`/`running`/`paused`, same rule either route follows. */
function enqueuePrefillForAppid(appid) {
  const existing = jobs.find(
    (j) => j.appid === appid && ["queued", "running", "paused"].includes(j.status),
  );
  if (existing) {
    return { appid, job_id: existing.id, status: existing.status, deduplicated: true };
  }
  const game = findGame(appid);
  const job = {
    id: nextJobId++,
    appid,
    type: "prefill",
    status: "queued",
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
    updated: null,
    up_to_date: null,
    summary_parse_ok: null,
    gc_execute: null,
    paused_at: null,
    stop_request: null,
    log_excerpt: "[vault-api] queued.",
    _demoTicksLeft: 3,
  };
  jobs.unshift(job);
  if (!game) {
    // WP WEB-FIX-4: the real `POST /v1/prefill` inserts the `apps` row with
    // `name = NULL` (api/vault_api/jobs.py: `INSERT OR IGNORE INTO apps
    // (appid, status)`); only a depot mapping names an app later. The demo
    // used to copy the owned list's title here, which hid the "App <id>"
    // bug in Downloads. The web UI now supplies that title itself
    // (lib/owned-library.js's appTitle), so demo mode shows the same fix.
    games.push(makeGame({ appid, name: null, status: "idle", depots: [] }));
  }
  // First tick already flips it to "running" so a demo poll shortly after
  // enqueueing sees visible progress, matching the mockup's "job start"
  // transition rather than sitting at "queued" forever (this module has no
  // real worker draining a queue).
  job.status = "running";
  job.started_at = job.created_at;
  return { appid, job_id: job.id, status: job.status, deduplicated: false };
}

/** Every appid that currently "has cache content" in this demo model —
 * `POST /v1/prefill/cached`'s selection (WP 4c-web; predicate corrected in
 * WP WEB-FIX-1). The real route calls `deletion.appids_with_cache_content`
 * (WP 4f — the ONE definition the keep-current sweep and this button share;
 * api/README.md "Sweep target set" / "Check & update all cached games"):
 * an app counts iff it maps at least one depot with bytes on disk that is
 * exclusive to it or a last-cached remnant — never a depot some OTHER
 * owner still holds cached content for. `hasSelectableCacheContent` above
 * is that rule against this demo's one-list-per-game model (`makeGame()`'s
 * header: mapping and on-disk size are one `depots` array here, so
 * "maps it AND has bytes" collapses to "is in `depots`"). The pre-WP-4f
 * filter this replaced (`g.depots.length > 0`) re-selected a game the
 * operator had just deleted whenever its only surviving mapping was a depot
 * a co-owner protected — one button press away from the re-download-after-
 * delete outcome ADR-0003 exists to prevent (docs/WORKPACKAGES.md
 * "Carry-overs" #1). Sorted ascending by appid, matching the real route's
 * deterministic order. */
function selectCachedAppids() {
  return games
    .filter(hasSelectableCacheContent)
    .map((g) => g.appid)
    .sort((a, b) => a - b);
}

/** Advance one running job a step closer to "done" (see _demoTicksLeft above).
 * Branches on `job.type` (WP 4a.4 addition) — a "gc" job never touches
 * `apps.status`/`last_prefill_at` (api/README.md "Garbage collection": "What
 * a GC job does to app state: nothing to apps.status or last_prefill_at"),
 * so it needs its own completion path rather than falling through the
 * prefill one below. */
function advanceJob(job) {
  if (job.status !== "running" || typeof job._demoTicksLeft !== "number") return;
  job._demoTicksLeft -= 1;
  if (job._demoTicksLeft > 0) return;

  delete job._demoTicksLeft;
  job.finished_at = new Date().toISOString();

  if (job.type === "gc") {
    finishGcJob(job);
    return;
  }

  job.status = "done";
  job.updated = 1;
  job.up_to_date = 0;
  job.summary_parse_ok = true;
  job.log_excerpt += "\n[vault-api] worker: Prefilled 1 app.";

  const game = findGame(job.appid);
  if (game) {
    game.status = "done";
    game.last_prefill_at = job.finished_at;
    game.needs_force = false;
  }
}

/**
 * Complete a GC job (WP 4a.4; extended WP 4a.8) with a log_excerpt shaped
 * exactly like the real `GC totals (DRY RUN)`/`GC totals (EXECUTED)` lines
 * — every field name `api/vault_api/gc_execute.py`'s `GcRunReport.log_text`
 * writes, in the same order, not just the two or three
 * `lib/gc-log-summary.js` happens to parse — so demo mode exercises the
 * REAL production parse path end to end, including `held_back` (WP 4a.8:
 * before this change the demo log hardcoded `held_back=0 (0 bytes)` in
 * every scenario, so that branch of the parser — and the detail sheet's "N
 * chunks held back" note — was never exercised by demo mode at all).
 *
 * The demo model tracks no manifests at all, so "what is reclaimable" and
 * "what is held back" are two per-game counters
 * (`_demoGcReclaimableBytes`/`_demoGcHeldBackBytes`, see makeGame()'s
 * header) rather than a real orphan-chunk scan: a dry run reports both, an
 * execute run "collects" only the reclaimable half (decrementing it to 0 —
 * held_back chunks are a TIME rule an execute run does not touch, see
 * makeGame()'s header) — so a second dry run against the same game
 * honestly reports nothing left to find *except* whatever is still held
 * back, matching the real endpoint's "the plan is built when the job runs"
 * and "an executing run... invalidates the size cache" behaviour without
 * pretending to model chunk-level accounting. Every count field this
 * function cannot derive from those two counters (`already_gone`,
 * `dedupe_removed`, `problems`, `declined`) is honestly `0` — the demo
 * model has no failure/dedupe scenarios to report — and `depots_touched`/
 * `planned_depots`/`needs_force_set_for` use the game's own depot/appid
 * only when something was actually planned, mirroring the real report's
 * "empty unless something happened" properties.
 */
function finishGcJob(job) {
  job.status = "done";
  const game = findGame(job.appid);
  const reclaimable = game ? game._demoGcReclaimableBytes || 0 : 0;
  const heldBack = game ? game._demoGcHeldBackBytes || 0 : 0;
  const depotid = game && game.depots.length ? game.depots[0].depotid : null;
  const wouldDeleteCount = reclaimable > 0 ? 1 : 0;
  const heldBackCount = heldBack > 0 ? 1 : 0;

  if (job.gc_execute) {
    const chunksRemoved = wouldDeleteCount;
    const touchedDepots = chunksRemoved > 0 && depotid != null ? [depotid] : [];
    const flaggedAppids = chunksRemoved > 0 ? [job.appid] : [];
    job.log_excerpt +=
      `\n[vault-api] GC totals (EXECUTED): chunks_removed=${chunksRemoved} ` +
      `bytes_freed=${reclaimable} already_gone=0 dedupe_removed=0 ` +
      "dedupe_bytes_freed=0 " +
      `total_bytes_freed=${reclaimable} problems=0 declined=0 ` +
      `held_back=${heldBackCount} (${heldBack} bytes) ` +
      `depots_touched=[${touchedDepots.join(", ")}] ` +
      `needs_force_set_for=[${flaggedAppids.join(", ")}]`;
    if (game) game._demoGcReclaimableBytes = 0;
  } else {
    const orphans = wouldDeleteCount + heldBackCount;
    const orphanBytes = reclaimable + heldBack;
    const plannedDepots = depotid != null && orphans > 0 ? [depotid] : [];
    job.log_excerpt +=
      `\n[vault-api] GC totals (DRY RUN): orphans=${orphans} (${orphanBytes} bytes) ` +
      `held_back=${heldBackCount} (${heldBack} bytes) ` +
      `would_delete=${wouldDeleteCount} (${reclaimable} bytes) ` +
      "reclaimable_dedupe_bytes=0 " +
      `planned_depots=[${plannedDepots.join(", ")}]. ` +
      'NOTHING was deleted — re-run with {"execute": true} to reclaim it.';
  }
}

function tickAllJobs() {
  for (const job of jobs) advanceJob(job);
}

// ---------------------------------------------------------------------
// Request routing
// ---------------------------------------------------------------------

function notFound(detail) {
  return new ApiError(ERROR_KINDS.NOT_FOUND, detail, { status: 404, detail });
}
function validationError(detail) {
  return new ApiError(ERROR_KINDS.VALIDATION, detail, { status: 422, detail });
}
function conflict(detail) {
  return new ApiError(ERROR_KINDS.VALIDATION, detail, { status: 409, detail });
}

const JOB_ID_RE = /^\/v1\/jobs\/(\d+)$/;
const JOB_PAUSE_RE = /^\/v1\/jobs\/(\d+)\/pause$/;
const JOB_RESUME_RE = /^\/v1\/jobs\/(\d+)\/resume$/;
const GAME_ID_RE = /^\/v1\/games\/(\d+)$/;
const CACHE_APPID_RE = /^\/v1\/cache\/(\d+)$/;
const GC_APPID_RE = /^\/v1\/cache\/(\d+)\/gc$/;
// WP WEB-FEAT-4: one percent-encoded path segment, like the server's
// `{client_id}` (which never matches a decoded "/").
const CLIENT_ID_RE = /^\/v1\/clients\/([^/]+)$/;

/**
 * `DELETE /v1/clients/{client_id}` (demo, WP WEB-FEAT-4), mirroring WP AG-1:
 * the row is gone from `GET /v1/clients`, the answer is `204` (`null` here,
 * what api.js's `request()` returns for a 204), and an id with no rows is a
 * 404 with the server's detail wording. No agent runs in demo mode, so a
 * removed PC stays removed until a page reload (the real server lists it
 * again with the agent's next report).
 */
function handleDeleteClient(encodedId) {
  let clientId;
  try {
    clientId = decodeURIComponent(encodedId);
  } catch {
    clientId = encodedId; // malformed escape: no client can have that id
  }
  const before = clients.length;
  clients = clients.filter((c) => c.client_id !== clientId);
  if (clients.length === before) throw notFound(`Unknown client_id '${clientId}'`);
  return null;
}

function jobControlResponse(job, { status, outcome, detail }) {
  job.status = status;
  return { job_id: job.id, status, outcome, detail };
}

/**
 * Handle one request against the demo dataset, mirroring the same
 * (data | throw ApiError) contract api.js's real `request()` offers, so
 * every caller above it is indifferent to which mode is active.
 *
 * @param {string} method
 * @param {string} path
 * @param {{body?: unknown, params?: Record<string, unknown>}} [opts]
 */
export async function demoRequest(method, path, { body, params } = {}) {
  if (method === "GET" && path === "/v1/health") {
    return { status: "ok" };
  }

  if (method === "GET" && path === "/v1/games") {
    return games.map(gameSummary);
  }

  let m;
  if (method === "GET" && (m = path.match(GAME_ID_RE))) {
    const appid = Number(m[1]);
    const game = findGame(appid);
    if (!game) throw notFound(`Unknown appid ${appid}`);
    return gameDetail(game);
  }

  // WP 4a.3: the full depot->app mapping table, mirroring
  // `GET /v1/mapping` (api/README.md) exactly — one row per (depotid,
  // appid) pair. Derived from the same `depots` arrays every other demo
  // route already reads (see the makeGame() note above: this demo model
  // keeps "mapping" and "on-disk size" in one list), so a demo bulk-delete
  // confirm sees the same shared-depot ownership the rest of demo mode
  // already assumes.
  if (method === "GET" && path === "/v1/mapping") {
    const rows = [];
    for (const g of games) for (const d of g.depots) rows.push({ depotid: d.depotid, appid: g.appid });
    return rows;
  }

  if (method === "GET" && path === "/v1/jobs") {
    tickAllJobs();
    const limit = Number(params?.limit ?? 20);
    return jobs
      .slice()
      .sort((a, b) => b.id - a.id)
      .slice(0, limit)
      .map(jobSummary);
  }

  if (method === "GET" && (m = path.match(JOB_ID_RE))) {
    tickAllJobs();
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    return jobDetail(job);
  }

  if (method === "POST" && path === "/v1/prefill") {
    const appids = Array.isArray(body?.appids) ? body.appids : [];
    if (appids.length === 0) throw validationError("appids must be a non-empty list");
    // All-or-nothing, BEFORE any job is created: real body validation is a
    // Pydantic model (PrefillRequest, api/vault_api/routers/jobs.py) that
    // rejects the whole request if any appid fails `AppId`'s `>= 1`
    // constraint — a 422 must never leave a partial side effect behind
    // (WP 4a.2 review fix: one bad id among several good ones used to still
    // queue a job for the good ones before the throw).
    for (const appid of appids) {
      if (!Number.isInteger(appid) || appid < 1) {
        throw validationError(`invalid appid ${appid}`);
      }
    }
    return appids.map(enqueuePrefillForAppid);
  }

  // WP 4c-web: POST /v1/prefill/cached — the "check & update every cached
  // game" convenience route (Phase 4c, WP 4c-api; api/README.md "Check &
  // update all cached games"). Two rules mirrored exactly from that
  // contract, both load-bearing for the frontend's honesty:
  //   1. **No enqueue mechanism of its own.** Selection below hands every
  //      chosen appid to the SAME `enqueuePrefillForAppid` helper
  //      `POST /v1/prefill` uses just above — identical dedupe, identical
  //      response shape (`PrefillJobRef`) — there is exactly one place in
  //      this demo model that creates a prefill job.
  //   2. **`body` is read nowhere below.** The real route declares no body
  //      parameter and silently ignores whatever was sent, INCLUDING an
  //      `{"appids": [...]}` payload that would look like it belongs here —
  //      that would queue every cached app, not the ids in the list. Not
  //      touching `body` at all is what makes that true here too, rather
  //      than a conditional that could later "helpfully" start reading it.
  if (method === "POST" && path === "/v1/prefill/cached") {
    return selectCachedAppids().map(enqueuePrefillForAppid);
  }

  if (method === "DELETE" && (m = path.match(JOB_ID_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    if (["done", "error", "cancelled"].includes(job.status)) {
      throw conflict(`Job ${id} already finished`);
    }
    const outcome = job.status === "running" ? "requested" : "immediate";
    // Demo simplification: there is no separate background worker to defer
    // to, so a "requested" cancel of a running job settles immediately
    // rather than on some later tick. It MUST settle to 'cancelled', not
    // keep counting down toward 'done' (WP 4a.2 review fix: a cancelled
    // job used to keep ticking to completion in tickAllJobs() and fire the
    // very job_finished notification cancellation is supposed to suppress
    // — see notifications.js's "cancelled is deliberately silent").
    delete job._demoTicksLeft;
    job.finished_at = new Date().toISOString();
    return jobControlResponse(job, {
      status: "cancelled",
      outcome,
      detail:
        outcome === "requested"
          ? "Cancellation requested; poll GET /v1/jobs/{id}."
          : "Job cancelled.",
    });
  }

  if (method === "POST" && (m = path.match(JOB_PAUSE_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    if (job.type !== "prefill" || (job.status !== "running" && job.status !== "queued")) {
      throw conflict(`Job ${id} cannot be paused from status ${job.status}`);
    }
    if (job.status === "queued") {
      // WP WEB-FEAT-5: the real API parks a queued prefill at once
      // (`outcome: "immediate"`) — nothing was running, nothing is stopped.
      job.paused_at = new Date().toISOString();
      return jobControlResponse(job, {
        status: "paused",
        outcome: "immediate",
        detail: "Paused while queued: this job had not started, so nothing was running and nothing was stopped.",
      });
    }
    delete job._demoTicksLeft;
    job.paused_at = new Date().toISOString();
    return jobControlResponse(job, {
      status: "paused",
      outcome: "requested",
      detail: "Pause requested; poll GET /v1/jobs/{id}.",
    });
  }

  if (method === "POST" && (m = path.match(JOB_RESUME_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    if (job.status !== "paused") throw conflict(`Job ${id} is not paused`);
    job.status = "running";
    job._demoTicksLeft = 2;
    return jobControlResponse(job, {
      status: "queued",
      outcome: "resumed",
      detail: "Job resumed at the front of the queue.",
    });
  }

  if (method === "DELETE" && (m = path.match(CACHE_APPID_RE))) {
    const appid = Number(m[1]);
    const game = findGame(appid);
    if (!game || game.depots.length === 0) {
      throw notFound(`App ${appid} has no depot mappings, so there is nothing to delete.`);
    }
    const activeJob = jobs.find(
      (j) => j.appid === appid && ["queued", "running", "paused"].includes(j.status),
    );
    if (activeJob) {
      const label = activeJob.type === "gc" ? "GC" : "Prefill";
      throw conflict(`${label} job ${activeJob.id} for app ${appid} is ${activeJob.status}.`);
    }

    // Mirrors api/vault_api/routers/cache.py's CacheDeletionOut exactly:
    //   deleted_depots:  {depotid, size_bytes_freed, shared_with_uncached}
    //   skipped_shared:  {depotid, shared_with}
    //   failed:          {depotid, error}   (never populated here — this
    //                     demo model has no filesystem to fail against)
    //
    // ADR-0003 shared-depot protection (the part the previous version of
    // this handler skipped entirely): a depot mapped by another game that
    // currently HAS cache content is never deleted — it is reported in
    // skipped_shared instead. A depot whose every other mapping is
    // currently uncached (a "last cached remnant") IS deleted, flagged via
    // shared_with_uncached rather than merged with an ordinary exclusive
    // deletion. Re-derived per depot against the CURRENT (not
    // request-start) state, same as the real endpoint's execute-time
    // recheck (`current_co_owners` there).
    //
    // `hasCacheContent`/`otherOwners` live at module scope (see their
    // header near `findGame`) — WP WEB-FIX-1 hoisted them out of this
    // branch so `selectCachedAppids()` applies the SAME definition.

    const deletedDepots = [];
    const skippedShared = [];
    const remnantCoOwnerAppids = new Set();

    for (const depot of game.depots) {
      const others = otherOwners(appid, depot.depotid);
      const cachedOthers = others.filter(hasCacheContent);
      if (cachedOthers.length > 0) {
        skippedShared.push({ depotid: depot.depotid, shared_with: others.map((g) => g.appid) });
        continue;
      }
      deletedDepots.push({
        depotid: depot.depotid,
        size_bytes_freed: depot.size_bytes ?? 0,
        shared_with_uncached: others.map((g) => g.appid),
      });
      for (const owner of others) remnantCoOwnerAppids.add(owner.appid);
    }

    // The bytes are actually gone from disk for EVERY game that mapped a
    // deleted depot, not just the one this request targeted — this demo
    // model keeps "mapping" and "on-disk size" in one list (see the
    // makeGame() note above) rather than the real schema's two separate
    // facts, so a deleted depot is dropped from every game's depot list,
    // not only the requested app's.
    const deletedIds = new Set(deletedDepots.map((d) => d.depotid));
    for (const g of games) {
      g.depots = g.depots.filter((d) => !deletedIds.has(d.depotid));
    }
    for (const coOwnerAppid of remnantCoOwnerAppids) {
      const coOwner = findGame(coOwnerAppid);
      if (coOwner) coOwner.needs_force = true;
    }

    // Real endpoint: `new_status = 'error' if failed else 'idle'`,
    // unconditionally — even a request that only hit skipped_shared (every
    // mapped depot protected, nothing actually removed) still resets to
    // 'idle', and last_prefill_at is cleared either way. This demo never
    // produces a filesystem failure, so it is always 'idle' here.
    game.status = "idle";
    game.last_prefill_at = null;
    // Real rule (ADR-0006 decision 2): set only when something in
    // deleted_depots or failed actually changed/left uncertain what's on
    // disk for THIS app; left untouched when everything mapped was
    // protected shared content (nothing exclusive to touch).
    if (deletedDepots.length > 0) game.needs_force = true;

    const totalBytesFreed = deletedDepots.reduce((sum, d) => sum + d.size_bytes_freed, 0);

    return {
      appid,
      deleted_depots: deletedDepots,
      skipped_shared: skippedShared,
      failed: [],
      total_bytes_freed: totalBytesFreed,
    };
  }

  // WP 4a.4: POST /v1/cache/{appid}/gc — mirrors api/README.md's
  // "Garbage collection" section: 404 for an unknown app or one with no
  // depot mappings (same reasoning/wording as DELETE), a 422-shaped
  // rejection for an unrecognised body field or a non-boolean `execute`
  // (StrictBool posture — see vault_api/routers/cache.py's GcRequest kdoc),
  // and — the one deliberate difference from DELETE — NO 409 for an active
  // job (the real endpoint queues onto the single worker, which serializes
  // GC against prefills by construction; see that router's "No 409 for an
  // active job" note). Dedupe is scoped to the SAME mode (a dry run and an
  // execute run never dedupe into each other, `jobs.enqueue_gc`'s rule).
  if (method === "POST" && (m = path.match(GC_APPID_RE))) {
    const appid = Number(m[1]);
    const game = findGame(appid);
    if (!game) {
      throw notFound(`Unknown appid ${appid} — vault-api tracks no such app, so there is nothing to garbage-collect.`);
    }
    if (game.depots.length === 0) {
      throw notFound(`App ${appid} has no depot mappings, so there is nothing to garbage-collect.`);
    }
    if (body != null) {
      const unknown = Object.keys(body).filter((k) => k !== "execute");
      if (unknown.length) throw validationError(`unrecognised field(s) in GC request body: ${unknown.join(", ")}`);
      if ("execute" in body && typeof body.execute !== "boolean") {
        throw validationError("'execute' must be a literal JSON boolean.");
      }
    }
    const execute = !!(body && body.execute);

    const existing = jobs.find(
      (j) => j.appid === appid && j.type === "gc" && j.gc_execute === execute && ["queued", "running", "paused"].includes(j.status),
    );
    if (existing) {
      return {
        appid,
        job_id: existing.id,
        status: existing.status,
        type: "gc",
        mode: execute ? "execute" : "dry-run",
        execute,
        deduplicated: true,
      };
    }

    const job = {
      id: nextJobId++,
      appid,
      type: "gc",
      status: "queued",
      created_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
      updated: null,
      up_to_date: null,
      summary_parse_ok: null,
      gc_execute: execute,
      paused_at: null,
      stop_request: null,
      log_excerpt: `[vault-api] GC for app ${appid}: ${execute ? "EXECUTE" : "DRY RUN"}.`,
      _demoTicksLeft: 1,
    };
    jobs.unshift(job);
    job.status = "running";
    job.started_at = job.created_at;

    return {
      appid,
      job_id: job.id,
      status: job.status,
      type: "gc",
      mode: execute ? "execute" : "dry-run",
      execute,
      deduplicated: false,
    };
  }

  if (method === "GET" && path === "/v1/cache/summary") {
    const allDepots = new Map();
    for (const g of games) for (const d of g.depots) allDepots.set(d.depotid, d.size_bytes ?? 0);
    const totalBytes = [...allDepots.values()].reduce((a, b) => a + b, 0);
    const topConsumers = games
      .map((g) => ({ appid: g.appid, name: g.name, size_bytes: appSizeBytes(g.depots) ?? 0 }))
      .sort((a, b) => b.size_bytes - a.size_bytes)
      .slice(0, 10);
    return {
      total_bytes: totalBytes,
      top_consumers: topConsumers,
      unmapped_depots: { count: 0, size_bytes: 0 },
      free_disk_bytes: 500_000_000_000,
    };
  }

  if (method === "GET" && path === "/v1/clients") {
    return handleGetClients();
  }
  if (method === "DELETE" && (m = path.match(CLIENT_ID_RE))) {
    return handleDeleteClient(m[1]);
  }
  if (method === "GET" && path === "/v1/about") {
    return handleGetAbout();
  }

  if (method === "GET" && path === "/v1/settings") {
    return handleGetSettings();
  }
  if (method === "PATCH" && path === "/v1/settings") {
    return handlePatchSettings(body);
  }
  if (method === "GET" && path === "/v1/schedule") {
    return handleGetSchedule();
  }

  if (method === "GET" && path === "/v1/steam/key") {
    return handleGetSteamKey();
  }
  if (method === "PUT" && path === "/v1/steam/key") {
    return handlePutSteamKey(body);
  }
  if (method === "DELETE" && path === "/v1/steam/key") {
    return handleDeleteSteamKey();
  }
  if (method === "GET" && path === "/v1/steam/owned-games") {
    requireSteamConfigured();
    requireValidSteamId(params?.steamid);
    const gatedGames = DEMO_OWNED_GAMES.map(demoOwnedGameForCurrentGate);
    return { configured: true, game_count: gatedGames.length, games: gatedGames };
  }
  if (method === "GET" && path === "/v1/steam/player-summaries") {
    requireSteamConfigured();
    const steamid = requireValidSteamId(params?.steamid);
    return { configured: true, players: [demoPlayerSummary(steamid)] };
  }

  throw new ApiError(ERROR_KINDS.NOT_FOUND, `Demo mode has no route for ${method} ${path}`, {
    status: 404,
    detail: `${method} ${path}`,
  });
}
