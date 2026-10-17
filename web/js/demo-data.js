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
 * appends up to 390 deterministic synthetic games on top of the 10 curated
 * ones below, gated behind a `localStorage`-read library-size preference (or
 * an explicit `resetDemoData({ librarySize })` argument for headless tests)
 * — see that function's own header for what it can and cannot measure, and
 * `web/tests/demo-data-large-library.test.js` for its coverage. Every
 * existing test in this suite that calls `resetDemoData()` with no argument
 * is unaffected: the default library size is exactly 10 (6 games plus the
 * WP TOOL-APPS-2c content-package group).
 */

import { ApiError, ERROR_KINDS } from "./errors.js";
import { validSteamId64 } from "./lib/steamid.js";
import { validSteamWebApiKey } from "./lib/steam-key-form.js";
import { demoClientsOverview, demoLive, demoSeries, demoThroughput } from "./demo-traffic.js";
import { normalizeClientName } from "./lib/client-names.js";

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
  // WP WEB-FIX-UPD-VIS: the stored oracle verdict "stale" for this app (WP
  // UPD-1's `update_available`). Demo-only flag: the projection below adds
  // the real rule's second half (only while the app has bytes in the
  // cache), and a finished prefill of this app clears it (advanceJob).
  updateAvailable = false,
  // WP TOOL-APPS-2c: `parent_appid` / `app_type` (WP TOOL-APPS-2a). Null
  // (ungrouped, unknown type) for every game except the content-package
  // fixture in buildContentPackageGames().
  parentAppid = null,
  appType = null,
  // WP CACHE-COMPLETE-1c: the stored completeness figure (api/README.md
  // "Cache completeness"). Demo-only shape `{expected, present, missing}`
  // (bytes of the current build, chunk files missing); `undefined` (the
  // default) means "measured complete": every byte of the game's depots is
  // the current build. `null` = not known (e.g. an announced build nobody
  // fetched yet). The projection applies the real null rules on top.
  completeness = undefined,
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
    _demoUpdateAvailable: updateAvailable,
    // When the "oracle" last answered for this app; stamped by
    // seedDemoUpdateCheck() and by every finished demo update check for the
    // cached games, null for the rest (real: null = never asked).
    update_checked_at: null,
    parent_appid: parentAppid,
    app_type: appType,
    _demoCompleteness: completeness,
    // When the figure was measured: the seeded update check's time (3 h
    // ago); a finished demo prefill re-stamps it.
    _demoCompletenessCheckedAt: isoAgo(3 * 3_600_000).replace(/\.\d{3}Z$/, "Z"),
  };
}

/**
 * WP TOOL-APPS-2c: one game with three content packages, so demo mode shows
 * the grouped card ("+3 content packages · 1 failed"), the package list in
 * the detail sheet with its Retry button, and the grouped delete. Modeled on
 * the prod library (ADR-0022): packages are separate apps with their own
 * depots (one shares a depot with the parent), Steam names them
 * "<game> - <what>", one package failed. Fictional names and ids.
 */
function buildContentPackageGames() {
  const parent = 2010080;
  return [
    makeGame({
      appid: parent,
      name: "Starfall Legion",
      status: "done",
      appType: "game",
      // WP CACHE-COMPLETE-1c: the demo's "wrong-size chunks only" game — no
      // chunk file missing, a few with the wrong size. The group card reads
      // "Partially cached 99%"; Downloads lists it without "Complete now".
      completeness: { expected: 6_250_000_000, present: 6_236_000_000, missing: 0 },
      depots: [
        { depotid: 2010081, shared: false, size_bytes: 6_100_000_000 },
        { depotid: 2010089, shared: true, size_bytes: 150_000_000 },
      ],
    }),
    makeGame({
      appid: 2010082,
      name: "Starfall Legion - Brazilian Portuguese Voice Pack",
      status: "done",
      parentAppid: parent,
      appType: "dlc",
      depots: [
        { depotid: 2010083, shared: false, size_bytes: 300_000_000 },
        // Shared with the parent: the group size counts it once.
        { depotid: 2010089, shared: true, size_bytes: 150_000_000 },
      ],
      installedOn: [{ client_id: "loft-laptop", reported_at: isoAgo(3_600_000) }],
    }),
    makeGame({
      appid: 2010084,
      name: "Starfall Legion - Original Soundtrack",
      status: "done",
      parentAppid: parent,
      appType: "music",
      depots: [{ depotid: 2010085, shared: false, size_bytes: 450_000_000 }],
    }),
    makeGame({
      appid: 2010086,
      name: "Starfall Legion - Officer Cosmetics",
      status: "error",
      parentAppid: parent,
      appType: "dlc",
      depots: [],
    }),
  ];
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
      // WP CACHE-COMPLETE-1c: the demo's partially cached game — "Partially
      // cached 86%" on the card, listed under Downloads "Incomplete" with
      // "Complete now" (chunk files are missing, so a forced run helps).
      completeness: { expected: 4_880_000_000, present: 4_200_000_000, missing: 412 },
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
      // WP CACHE-COMPLETE-1c: mid-download, so only part of the build is on
      // disk; its live job keeps it out of Downloads "Incomplete" and the
      // finished demo job re-measures it complete.
      completeness: { expected: 2_900_000_000, present: 1_100_000_000, missing: 1_800 },
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
      // WP WEB-FIX-UPD-VIS: the one demo game with an update available, so
      // the "Update available" badge, the "Updates" chip and the update
      // check line show in demo mode (the Android demo flags its own
      // "Pale Meridian"; the web demo has no game of that name). Chosen
      // because nothing else here contradicts it: Aurora Cascade is
      // "stable" and Frostline Convoy was confirmed current an hour ago.
      updateAvailable: true,
      // WP CACHE-COMPLETE-1c: the announced build's manifest has not been
      // fetched by anyone yet, so its completeness is not known (the real
      // API's null case); the card still says "Update available" (UPD-6
      // wins), the detail sheet "Completeness unknown".
      completeness: null,
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
// the default library size is exactly DEFAULT_LIBRARY_SIZE (10).
// ---------------------------------------------------------------------

// WP TOOL-APPS-2c: the curated list is the 6 hand-named games plus the 4
// rows of the content-package group (buildContentPackageGames).
const DEFAULT_LIBRARY_SIZE = 10;
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

/** The curated seed list (6 games plus the 4-row content-package group), plus `librarySize - DEFAULT_LIBRARY_SIZE`
 * synthetic games appended on top (0 more when `librarySize` is at or below
 * the default — every existing demo test, which never passes an argument
 * here, sees exactly the unmodified 6-game list it always has). */
function buildGames(librarySize = DEFAULT_LIBRARY_SIZE) {
  return buildCuratedGames().concat(
    buildContentPackageGames(),
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
    ...buildOlderHistory(),
  ];
}

/** WP DOWNLOADS-3: older finished jobs, so the History list has more than
 * one page (first 10 rows, then "Show 20 more") in demo mode and in the
 * screenshot goldens. 24 jobs, one every 5 hours from 9 hours ago (all
 * inside 7 days), on curated demo games (Aurora Cascade, Emberreach,
 * Frostline Convoy, Starfall Legion); mostly Done, with one
 * Cancelled, one Failed and one garbage collection. */
export const DEMO_OLDER_HISTORY_COUNT = 24;
// Not Copper Horizon (2010020): the demo keeps it a never-run game.
const OLDER_HISTORY_APPS = [2010010, 2010040, 2010050, 2010080];
function buildOlderHistory() {
  const rows = [];
  for (let i = 0; i < DEMO_OLDER_HISTORY_COUNT; i += 1) {
    const id = 899998 - i;
    const finishedAgo = (9 + 5 * i) * 3_600_000;
    const status = i === 4 ? "cancelled" : i === 11 ? "error" : "done";
    const gc = i === 7;
    rows.push({
      id,
      appid: OLDER_HISTORY_APPS[i % OLDER_HISTORY_APPS.length],
      type: gc ? "gc" : "prefill",
      status,
      created_at: isoAgo(finishedAgo + 60_000),
      started_at: isoAgo(finishedAgo + 50_000),
      finished_at: isoAgo(finishedAgo),
      updated: status === "done" && !gc ? i % 3 : 0,
      up_to_date: status === "done" && !gc ? 1 : 0,
      summary_parse_ok: !gc,
      gc_execute: gc ? true : null,
      paused_at: null,
      stop_request: status === "cancelled" ? "cancel" : null,
      log_excerpt: `[vault-api] worker claimed job ${id}\n${gc ? "GC finished." : status === "done" ? "Prefill finished." : "SteamPrefill stopped."}`,
    });
  }
  return rows;
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
      // WP AGENT-ADDR-1 diagnostics: a current agent states its LAN address.
      peer_addrs: ["10.10.0.21"],
      reported_addrs: ["10.10.0.21"],
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
      // A legacy agent states nothing; it is correlated on its TCP peer.
      peer_addrs: ["10.10.0.44"],
      reported_addrs: [],
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
    return { ...wire, presence, offline_after: offlineAfter, ...demoAgentName(c) };
  });
}

// ---------------------------------------------------------------------
// Client names (WP D15-NAMES; api/vault_api/client_names.py). Demo agents
// own exactly their first source address (the overview fixture's
// `addresses` is the same slice), so the server's rule "first named owned
// address, an explicit name wins" reduces to one lookup here.
// ---------------------------------------------------------------------

/** canonical address -> name. */
let clientNameMap = new Map();

/** A demo-sized `client_names.canonical_ip`: dotted-quad IPv4 (no leading
 * zeros) or an IPv6 literal lowercased; `null` for anything else. */
function demoCanonicalIp(raw) {
  if (typeof raw !== "string" || raw !== raw.trim() || !raw || raw.includes("%")) return null;
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(raw);
  if (v4) {
    const ok = v4.slice(1).every((part) => (part === "0" || !part.startsWith("0")) && Number(part) <= 255);
    return ok ? raw : null;
  }
  if (raw.includes(":") && /^[0-9a-fA-F:.]+$/.test(raw)) return raw.toLowerCase();
  return null;
}

function resolvedName(name, addr, fallback) {
  return { name: name ?? null, name_addr: addr ?? null, display_name: name ?? fallback };
}

function demoAgentName(client) {
  const addr = Array.isArray(client.source_addrs) && client.source_addrs[0] ? demoCanonicalIp(client.source_addrs[0]) : null;
  return resolvedName(addr ? clientNameMap.get(addr) : null, addr, client.client_id);
}

function demoAddressName(addr) {
  const canonical = demoCanonicalIp(addr);
  return resolvedName(canonical ? clientNameMap.get(canonical) : null, canonical, addr);
}

const CLIENT_NAME_RE = /^\/v1\/client-names\/([^/]+)\/([^/]+)$/;

/** The path key, validated like `routers/client_names.py`'s `_key`. */
function demoNameKey(type, encodedKey) {
  if (type === "mac") throw validationError("Naming by MAC address is not supported yet: no source reports MACs.");
  if (type !== "ip") throw validationError(`Unknown key type '${type}'; use 'ip'.`);
  let key;
  try {
    key = decodeURIComponent(encodedKey);
  } catch {
    key = encodedKey;
  }
  const canonical = demoCanonicalIp(key);
  if (!canonical) throw validationError(`'${key}' is not an IP address`);
  return canonical;
}

function handleListClientNames() {
  return [...clientNameMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, name]) => ({ key_type: "ip", key, name, updated_at: utcIsoSeconds(Date.now()) }));
}

function handlePutClientName(type, encodedKey, body) {
  const key = demoNameKey(type, encodedKey);
  if (!body || typeof body !== "object" || Object.keys(body).some((k) => k !== "name")) {
    throw validationError("Body must be {\"name\": \"...\"}");
  }
  const checked = normalizeClientName(body.name);
  if (!checked.ok) throw validationError(checked.error);
  clientNameMap.set(key, checked.value);
  return { key_type: "ip", key, name: checked.value, updated_at: utcIsoSeconds(Date.now()) };
}

function handleDeleteClientName(type, encodedKey) {
  const key = demoNameKey(type, encodedKey);
  if (!clientNameMap.delete(key)) throw notFound(`No name stored for '${key}'`);
  return null;
}

// ---------------------------------------------------------------------
// Delete history (WP D15-NAMES; api/vault_api/history.py): finished jobs
// and the traffic statistics. The demo traffic is synthetic, so deleting
// it records a "since" moment the traffic fixtures zero everything before.
// ---------------------------------------------------------------------

/** Traffic before this moment (ms) was deleted; `null` = nothing deleted. */
let historySinceMs = null;

const DEMO_TERMINAL = new Set(["done", "error", "cancelled"]);

function handleDeleteHistory(scope) {
  if (scope !== "all" && scope !== "older-than-30d") {
    throw validationError("scope must be one of all, older-than-30d");
  }
  const nowMs = Date.now();
  const cutoffMs = scope === "all" ? null : nowMs - 30 * 86_400_000;
  const before = jobs.length;
  jobs = jobs.filter((j) => {
    if (!DEMO_TERMINAL.has(j.status)) return true;
    if (cutoffMs === null) return false;
    return Date.parse(j.finished_at || j.started_at || j.created_at) >= cutoffMs;
  });
  const since = cutoffMs === null ? nowMs : cutoffMs;
  historySinceMs = historySinceMs === null ? since : Math.max(historySinceMs, since);
  return {
    scope,
    cutoff: cutoffMs === null ? null : utcIsoSeconds(cutoffMs),
    jobs_deleted: before - jobs.length,
    // The demo has no stored rows to count.
    traffic_rows_deleted: { client_traffic: 0, client_depot_traffic: 0, server_traffic: 0 },
  };
}

// ---------------------------------------------------------------------
// GET /v1/dist (WP DIST-1b, mirroring WP DIST-1a's DistOut)
// ---------------------------------------------------------------------

/** The ten release files of a vault built from a release tag, all baked in
 * (api/README.md "Release downloads"), in the server's order. Version
 * equals the demo vault-api's /v1/about version; sizes are rounded rc17
 * asset sizes, the SHA-256 values are fictional. */
const DEMO_DIST_VERSION = "0.1.0";
const DEMO_DIST_FILES = [
  ["app-android", `steamhangar-app-v${DEMO_DIST_VERSION}.apk`, 9_300_000],
  ["agent-windows-amd64", `vault-agent-v${DEMO_DIST_VERSION}-windows-amd64.exe`, 9_400_000],
  ["agent-linux-amd64", `vault-agent-v${DEMO_DIST_VERSION}-linux-amd64`, 9_100_000],
  ["agent-linux-arm64", `vault-agent-v${DEMO_DIST_VERSION}-linux-arm64`, 8_900_000],
  ["install-task", "install-task.ps1", 21_000],
  ["run-vault-agent", "run-vault-agent.ps1", 6_000],
  ["uninstall-task", "uninstall-task.ps1", 4_000],
  ["systemd-service", "vault-agent-report.service", 600],
  ["systemd-timer", "vault-agent-report.timer", 400],
  ["sha256sums", "SHA256SUMS", 900],
];

function handleGetDist() {
  const releaseBase = `https://github.com/steamhangar/steamhangar/releases/download/v${DEMO_DIST_VERSION}/`;
  return {
    version: DEMO_DIST_VERSION,
    release: true,
    local_base: "/dist/",
    release_base: releaseBase,
    apk_alias_url: "/dist/steamhangar-app.apk",
    artifacts: DEMO_DIST_FILES.map(([id, name, size], i) => ({
      id,
      name,
      local: true,
      size,
      sha256: (i + 1).toString(16).padStart(2, "0").repeat(32),
      local_url: `/dist/${name}`,
      release_url: `${releaseBase}${name}`,
      url: `/dist/${name}`,
      source: "local",
    })),
  };
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
// Seeded in the /v1/updates section below (its constants must exist first).
let updateCheck = null;

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
 *
 * WP UPD-5 demo pill (user decision 2026-10-14): `demoSteamAccount` seeds a
 * demo Steam Web API key and `steam_library_steamid` (see
 * `seedDemoSteamAccount`). Like `relayExposePlaytime` it defaults to the
 * browser preference (`readRelayPlaytimePreference`: ON in a browser demo,
 * OFF outside a browser), so every bare-Node test still starts from the
 * unconfigured, gate-off baseline unless it asks otherwise.
 * @param {{ librarySize?: number, relayExposePlaytime?: boolean, relayExposeLastPlayed?: boolean, demoSteamAccount?: boolean }} [options]
 */
export function resetDemoData({
  librarySize,
  relayExposePlaytime: rp = readRelayPlaytimePreference(),
  relayExposeLastPlayed: rl = false,
  demoSteamAccount: seedAccount = readRelayPlaytimePreference(),
} = {}) {
  games = buildGames(librarySize ?? readLibrarySizePreference());
  jobs = buildJobs();
  clients = buildClients();
  clientNameMap = new Map();
  historySinceMs = null;
  nextJobId = 900002;
  updateCheck = seedDemoUpdateCheck();
  resetDemoSettings();
  resetDemoSteamRelay();
  if (seedAccount) seedDemoSteamAccount();
  relayExposePlaytime = rp;
  relayExposeLastPlayed = rl;
}

// ---------------------------------------------------------------------
// /v1/settings (WP 4a.6; ADR-0009; D16b row shape since WP D16-WEB) — an
// in-memory mirror of vault_api/settings_store.py, enough to exercise the
// Settings view's real code path in demo mode:
//   - precedence env > Settings value > default (Q1, D16b): a key with a
//     demo `env` value is "set via env" (`source: "env"`, `editable:
//     false`, PATCH 409; `shadowed` while a stored value exists);
//   - every row carries the D16b fields (`env_var`, `value`, `editable`,
//     `risky`, `expert`, `grammar`, `target`, `env_visible`, `shadowed`);
//   - PATCH: 422 for a bad value / unknown or env-only key / broken cross
//     rule, 409 for an env-pinned key, 428 `{message, risky_keys}` for a
//     risky key without `confirm_risky=true` — all before anything is
//     written.
// `env` stands in for "what Settings.from_env() would have produced",
// fixed per key (demo mode has no process env). The demo pins vault_name
// via env and keeps a stored, env-shadowed webhook timeout, so both
// states show on the normal page. Key list, env names, `applies`,
// `target`, `risky` and `expert` are pinned against settings_store.py by
// web/tests/demo-data-shape-guard.test.js.
// ---------------------------------------------------------------------

const WEBHOOK_EVENTS_ALL = Object.freeze([
  "job.done",
  "job.error",
  "job.cancelled",
  "client.bypass_suspected",
  "client.bypass_resolved",
]);
const AUTO_GC_MODES = Object.freeze(["off", "dry-run", "execute"]);
const LOG_LEVELS = Object.freeze(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]);

// **The `auto_gc`/`sweep_include_cached` defaults below are NOT this
// file's own opinion — they must equal `api/vault_api/config.py`'s
// `DEFAULT_AUTO_GC`/`DEFAULT_SWEEP_INCLUDE_CACHED` constants (ADR-0014).**
// `web/tests/demo-data-config-defaults.test.js` reads config.py as text and
// asserts equality against these two literals on every suite run (a demo
// fixture asserting the wrong default teaches the reader something false on
// the surface used for screenshots, docs/LEARNINGS.md).
export const CONFIG_DEFAULT_AUTO_GC = "execute";
export const CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED = true;

const MAX_INTERVAL_MINUTES = 527040;
const MAX_DAYS = 3650;
const INT_HINT = "whole number, ASCII digits";

function demoInt(key, raw, min, max) {
  const text = raw.trim();
  if (!text) throw validationError(`'${key}': must not be blank; use null to clear the stored value instead of an empty string`);
  if (!/^[0-9]+$/.test(text)) throw validationError(`'${key}': ${JSON.stringify(raw)} is not a whole number.`);
  const n = Number(text);
  if (n < min || (max !== undefined && n > max)) {
    throw validationError(`'${key}': must be ${max !== undefined ? `${min}..${max}` : `at least ${min}`}, got ${n}.`);
  }
  return n;
}

function demoFloat(key, raw) {
  const text = raw.trim();
  if (!/^[0-9]+(\.[0-9]+)?$/.test(text) || Number(text) <= 0) {
    throw validationError(`'${key}': must be a positive number of seconds, got ${JSON.stringify(raw)}.`);
  }
  return Number(text);
}

function demoCap(key, raw, max) {
  const text = raw.trim();
  if (!/^[1-9][0-9]*$/.test(text) || Number(text) > max) {
    throw validationError(`'${key}': ${JSON.stringify(raw)} is not a whole number 1..${max} without leading zeros.`);
  }
  return text;
}

const STEAM_HOST = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.(steamcontent\.com|steamserver\.net)$/;

/** Demo mirror of settings_store.OVERRIDABLE_SPECS, in its order. `parse`
 * returns the JSON value GET reports, or throws a 422. */
const DEMO_SETTING_SPECS = [
  {
    key: "vault_name", env_var: "VAULT_NAME", applies: "restart-required", default: "", env: "steamhangar-demo",
    grammar: "text, at most 64 printable characters",
    parse: (key, raw) => {
      const text = raw.trim();
      if (text.length > 64) throw validationError(`'${key}': at most 64 characters.`);
      return text;
    },
  },
  {
    key: "schedule_window", env_var: "VAULT_SCHEDULE_WINDOW", applies: "next_sweep", default: "03:00-07:00", target: "api+core",
    grammar: "HH:MM-HH:MM (overnight allowed, 24:00 as end), or off",
    parse: (key, raw) => {
      const text = raw.trim();
      if (text === "" || text.toLowerCase() === "off") return "off";
      if (!/^\d{2}:\d{2}-\d{2}:\d{2}$/.test(text)) {
        throw validationError(`'${key}': expected 'HH:MM-HH:MM' (e.g. '22:00-06:00') or 'off', got ${JSON.stringify(raw)}.`);
      }
      return text;
    },
  },
  {
    key: "schedule_interval_minutes", env_var: "VAULT_SCHEDULE_INTERVAL_MINUTES", applies: "next_sweep", default: 180, expert: true,
    grammar: `${INT_HINT}, 1..${MAX_INTERVAL_MINUTES}`, parse: (key, raw) => demoInt(key, raw, 1, MAX_INTERVAL_MINUTES),
  },
  {
    key: "schedule_client_stale_days", env_var: "VAULT_SCHEDULE_CLIENT_STALE_DAYS", applies: "next_sweep", default: 7, expert: true,
    grammar: `${INT_HINT}, 1..${MAX_DAYS}`, parse: (key, raw) => demoInt(key, raw, 1, MAX_DAYS),
  },
  {
    key: "sweep_include_cached", env_var: "VAULT_SWEEP_INCLUDE_CACHED", applies: "next_sweep",
    default: CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED, expert: true, grammar: "true/false (also yes/no, on/off, 1/0)",
    parse: (key, raw) => {
      const text = raw.trim().toLowerCase();
      if (BOOL_TRUE_SPELLINGS.includes(text)) return true;
      if (BOOL_FALSE_SPELLINGS.includes(text)) return false;
      throw validationError(
        `'${key}' must be one of ${BOOL_TRUE_SPELLINGS.join(", ")} (true) or ` +
          `${BOOL_FALSE_SPELLINGS.join(", ")} (false), case-insensitive. Got ${JSON.stringify(raw)}.`,
      );
    },
  },
  {
    key: "auto_gc", env_var: "VAULT_AUTO_GC", applies: "immediately", default: CONFIG_DEFAULT_AUTO_GC,
    grammar: "off, dry-run or execute",
    parse: (key, raw) => {
      const text = raw.trim();
      if (!AUTO_GC_MODES.includes(text)) {
        throw validationError(`'${key}' must be one of ${AUTO_GC_MODES.join(", ")}, got ${JSON.stringify(raw)}.`);
      }
      return text;
    },
  },
  {
    key: "gc_grace_days", env_var: "VAULT_GC_GRACE_DAYS", applies: "next_job", default: 14, expert: true,
    grammar: `${INT_HINT}, 0..${MAX_DAYS} (0 = no grace)`, parse: (key, raw) => demoInt(key, raw, 0, MAX_DAYS),
  },
  {
    key: "manifest_keep", env_var: "VAULT_MANIFEST_KEEP", applies: "next_job", default: 3, expert: true,
    grammar: `${INT_HINT}, at least 1`, parse: (key, raw) => demoInt(key, raw, 1),
  },
  {
    key: "agent_report_keep", env_var: "VAULT_AGENT_REPORT_KEEP", applies: "immediately", default: 20, expert: true,
    grammar: `${INT_HINT}, at least 2`, parse: (key, raw) => demoInt(key, raw, 2),
  },
  {
    key: "client_stats_keep", env_var: "VAULT_CLIENT_STATS_KEEP", applies: "next_sweep", default: 48, expert: true,
    grammar: `${INT_HINT}, at least 1`, parse: (key, raw) => demoInt(key, raw, 1),
  },
  {
    key: "event_sweep_interval_minutes", env_var: "VAULT_EVENT_SWEEP_INTERVAL_MINUTES", applies: "next_sweep", default: 5,
    expert: true, grammar: `${INT_HINT}, 1..${MAX_INTERVAL_MINUTES}`, parse: (key, raw) => demoInt(key, raw, 1, MAX_INTERVAL_MINUTES),
  },
  {
    key: "miss_trigger_cooldown_minutes", env_var: "VAULT_MISS_TRIGGER_COOLDOWN_MINUTES", applies: "next_sweep", default: 60,
    expert: true, grammar: `${INT_HINT}, 0..${MAX_INTERVAL_MINUTES} (0 = trigger off)`,
    parse: (key, raw) => demoInt(key, raw, 0, MAX_INTERVAL_MINUTES),
  },
  {
    key: "miss_trigger_max_per_sweep", env_var: "VAULT_MISS_TRIGGER_MAX_PER_SWEEP", applies: "next_sweep", default: 5,
    expert: true, grammar: `${INT_HINT}, at least 1`, parse: (key, raw) => demoInt(key, raw, 1),
  },
  {
    key: "bypass_window_days", env_var: "VAULT_BYPASS_WINDOW_DAYS", applies: "immediately", default: 3,
    grammar: `${INT_HINT}, 1..${MAX_DAYS}`, parse: (key, raw) => demoInt(key, raw, 1, MAX_DAYS),
  },
  {
    key: "prefill_max_threads", env_var: "VAULT_PREFILL_MAX_THREADS", applies: "next_job", default: 8, target: "api+core",
    risky: true, expert: true, grammar: `${INT_HINT}, 1..64, at most upstream_max_conns`,
    parse: (key, raw) => demoInt(key, raw, 1, 64),
  },
  {
    key: "prefill_timeout_seconds", env_var: "VAULT_PREFILL_TIMEOUT_SECONDS", applies: "next_job", default: 14400, expert: true,
    grammar: `${INT_HINT}, seconds, at least 1`, parse: (key, raw) => demoInt(key, raw, 1),
  },
  {
    key: "manifest_oracle_timeout", env_var: "VAULT_MANIFEST_ORACLE_TIMEOUT", applies: "immediately", default: 10, expert: true,
    grammar: "positive number of seconds, e.g. 10 or 2.5", parse: demoFloat,
  },
  {
    key: "webhook_events", env_var: "VAULT_WEBHOOK_EVENTS", applies: "restart-required", default: [...WEBHOOK_EVENTS_ALL],
    grammar: "comma-separated event names (or a JSON list); blank = all",
    parse: (key, raw) => {
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
      return [...new Set(tokens)].sort();
    },
  },
  {
    key: "webhook_timeout_seconds", env_var: "VAULT_WEBHOOK_TIMEOUT_SECONDS", applies: "restart-required", default: 5, env: 10,
    grammar: "positive number of seconds, e.g. 5", parse: demoFloat,
  },
  {
    key: "steam_library_steamid", env_var: "VAULT_STEAM_LIBRARY_STEAMID", applies: "immediately", default: "",
    grammar: "SteamID64 (17 digits) as a string; blank = not set",
    parse: (key, raw) => {
      // Mirrors config.parse_steam_library_steamid: trimmed, blank = not set.
      const text = raw.trim();
      if (!text) return "";
      if (!validSteamId64(text)) {
        throw validationError(
          `'${key}': must be a SteamID64: exactly 17 ASCII digits in the individual-account range; blank (or null over the API) clears it`,
        );
      }
      return text;
    },
  },
  {
    key: "upstream_rate", env_var: "VAULT_UPSTREAM_RATE", applies: "core", default: null, target: "core", expert: true,
    grammar: "bytes/s with optional k or m suffix (800k, 2m), min 1024; off = no cap",
    parse: (key, raw) => {
      const text = raw.trim();
      if (text === "off") return "off";
      const m = /^([1-9][0-9]{0,8})([kKmM]?)$/.exec(text);
      const bytes = m ? Number(m[1]) * (m[2] ? (/[kK]/.test(m[2]) ? 1024 : 1048576) : 1) : 0;
      if (!m || bytes < 1024) throw validationError(`'${key}': ${JSON.stringify(raw)} is not a rate like 800k or 2m (min 1024 bytes/s), or off.`);
      return text;
    },
  },
  {
    key: "upstream_max_conns", env_var: "VAULT_UPSTREAM_MAX_CONNS", applies: "core", default: "16", target: "core",
    risky: true, expert: true, grammar: "whole number 1..64, no leading zero, at least prefill_max_threads",
    parse: (key, raw) => demoCap(key, raw, 64),
  },
  {
    key: "tls_client_max_conns", env_var: "VAULT_TLS_CLIENT_MAX_CONNS", applies: "core", default: "64", target: "core",
    risky: true, expert: true, grammar: "whole number 1..256, no leading zero, at most tls_max_conns",
    parse: (key, raw) => demoCap(key, raw, 256),
  },
  {
    key: "tls_max_conns", env_var: "VAULT_TLS_MAX_CONNS", applies: "core", default: "256", target: "core",
    risky: true, expert: true, grammar: "whole number 1..400, no leading zero",
    parse: (key, raw) => demoCap(key, raw, 400),
  },
  {
    key: "event_log_rotate_bytes", env_var: "VAULT_EVENT_LOG_ROTATE_BYTES", applies: "core", default: "67108864", target: "core",
    grammar: "bytes, digits only, at least 1048576; 0 = rotation off",
    parse: (key, raw) => {
      const text = raw.trim();
      if (!/^[0-9]{1,15}$/.test(text) || (Number(text) !== 0 && Number(text) < 1048576)) {
        throw validationError(`'${key}': ${JSON.stringify(raw)} must be 0 (off) or at least 1048576 bytes.`);
      }
      return String(Number(text));
    },
  },
  {
    key: "upstream_edge", env_var: "VAULT_UPSTREAM_EDGE", applies: "core", default: "dist-fra1.discovery.steamserver.net",
    target: "core", risky: true, expert: true,
    grammar: "one *.steamcontent.com or *.steamserver.net host; off = legacy per-name pool",
    parse: (key, raw) => {
      const text = raw.trim().toLowerCase();
      if (text === "off" || STEAM_HOST.test(text)) return text;
      throw validationError(`'${key}': ${JSON.stringify(raw)} is not one Steam edge host (*.steamcontent.com or *.steamserver.net), or off.`);
    },
  },
  {
    key: "upstream_pool_hosts", env_var: "VAULT_UPSTREAM_POOL_HOSTS", applies: "core", default: null, target: "core",
    risky: true, expert: true,
    grammar: "up to 4 space-separated Steam edge hosts, used only while upstream_edge is off; off = none",
    parse: (key, raw) => {
      const tokens = raw.trim().toLowerCase().split(/\s+/).filter(Boolean);
      if (tokens.length === 1 && tokens[0] === "off") return "off";
      if (tokens.length === 0 || tokens.length > 4 || new Set(tokens).size !== tokens.length || !tokens.every((t) => STEAM_HOST.test(t))) {
        throw validationError(`'${key}': up to 4 different Steam edge hosts, or off.`);
      }
      return tokens.join(" ");
    },
  },
  {
    key: "resolver", env_var: "VAULT_RESOLVER", applies: "core", default: "1.1.1.1", target: "core", risky: true, expert: true,
    grammar: "IP address list, e.g. 1.1.1.1 or [2606:4700::1111]:53; never a resolver that rewrites Steam names",
    parse: (key, raw) => {
      const text = raw.trim();
      if (!text || !/^[0-9a-fA-F.:[\] ]+$/.test(text)) throw validationError(`'${key}': ${JSON.stringify(raw)} is not an IP address list.`);
      return text;
    },
  },
  {
    key: "log_level", env_var: "VAULT_LOG_LEVEL", applies: "immediately", default: "INFO", expert: true,
    grammar: "DEBUG, INFO, WARNING, ERROR or CRITICAL",
    parse: (key, raw) => {
      const text = raw.trim().toUpperCase();
      if (!LOG_LEVELS.includes(text)) throw validationError(`'${key}': must be one of ${LOG_LEVELS.join(", ")}, got ${JSON.stringify(raw)}.`);
      return text;
    },
  },
];
const DEMO_SPEC_BY_KEY = new Map(DEMO_SETTING_SPECS.map((s) => [s.key, s]));

/** Test seam: the demo's editable keys in order, with their flags. */
export function demoSettingSpecs() {
  return DEMO_SETTING_SPECS.map(({ key, env_var, applies, target = "api", risky = false, expert = false }) => ({
    key,
    env_var,
    applies,
    target,
    risky,
    expert,
  }));
}



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
// WP WEB-FEAT-SUGGEST-2 / WP UPD-5 demo pill (user decision 2026-10-14):
// the browser demo shows the card's "Never played" pill out of the box. A
// browser demo starts with the playtime gate ON, a demo Steam Web API key
// and a demo SteamID64 (`seedDemoSteamAccount`), so the Library loads the
// owned list on open and Frostline Convoy (cached, playtime 0) carries the
// pill without any manual step. This is demo mode only: a real install
// keeps `VAULT_RELAY_EXPOSE_PLAYTIME` at its shipped default, OFF (ADR-0010),
// and nothing here reaches the server. A demo-only browser preference,
// `steamvault.demoRelayPlaytime` = "0", opts the demo out (gate off, no key,
// no SteamID: the real fresh-install shape); any other value or none means
// on. Outside a browser (every bare-Node test) it is OFF, so the tests'
// baseline stays the shipped gate-off, unconfigured shape. Read at module
// load and on resetDemoData().
let relayExposePlaytime = readRelayPlaytimePreference();
let relayExposeLastPlayed = false;

function readRelayPlaytimePreference() {
  try {
    if (typeof window === "undefined" || !window.localStorage) return false;
    return window.localStorage.getItem("steamvault.demoRelayPlaytime") !== "0";
  } catch {
    return false;
  }
}


// Mirrors settings_store.ENV_ONLY_INFO_KEYS — informational-only rows a
// settings screen shows as "this exists but only the environment controls
// it" (api/README.md "Env-only keys"). `vault_api_key` is excluded here
// too, same reasoning as the real endpoint: it never appears in ANY GET
// response, not even redacted. `webhook_url` is env-only since D16b (Q2)
// and shown masked like `settings_store.mask_url` does (scheme and host,
// everything after the host replaced).
const DEMO_WEBHOOK_URL_MASKED = "https://hooks.example.net/***";
const ENV_ONLY_DEMO = [
  { key: "db_path", env_var: "VAULT_DB_PATH", value: "/data/vault.db" },
  { key: "cache_root", env_var: "VAULT_CACHE_ROOT", value: "/vault/cache" },
  { key: "steamprefill_path", env_var: "VAULT_STEAMPREFILL_PATH", value: "/usr/local/bin/steamprefill" },
  { key: "steamprefill_cache_dir", env_var: "VAULT_STEAMPREFILL_CACHE_DIR", value: "/root/.local/share/SteamPrefill" },
  { key: "manifest_archive_dir", env_var: "VAULT_MANIFEST_ARCHIVE_DIR", value: "/vault/manifest-archive" },
  { key: "web_dir", env_var: "VAULT_WEB_DIR", value: "/app/web" },
  { key: "settings_readonly", env_var: "VAULT_SETTINGS_READONLY", value: false },
  { key: "relay_expose_playtime", env_var: "VAULT_RELAY_EXPOSE_PLAYTIME", get value() { return relayExposePlaytime; } },
  { key: "relay_expose_last_played", env_var: "VAULT_RELAY_EXPOSE_LAST_PLAYED", get value() { return relayExposeLastPlayed; } },
  // WP D23: library metadata fill switch (off in the demo: no metadata).
  { key: "library_meta_enabled", env_var: "VAULT_LIBRARY_META", value: false },
  // WP D19-1a: oracle query on card open (off in the demo: no external query).
  { key: "depot_preview_enabled", env_var: "VAULT_DEPOT_PREVIEW", value: false },
  // WP D19-1c: store details on the card (off in the demo: no external query).
  { key: "store_details_enabled", env_var: "VAULT_STORE_DETAILS", value: false },
  { key: "webhook_url", env_var: "VAULT_WEBHOOK_URL", value: DEMO_WEBHOOK_URL_MASKED },
];
const ENV_ONLY_KEYS = new Set(["vault_api_key", ...ENV_ONLY_DEMO.map((e) => e.key)]);

/** A fresh demo: no stored values except one the env shadows (the webhook
 * timeout), so the "overridden by env" note shows without a manual step. */
function freshDemoOverrides() {
  return { webhook_timeout_seconds: "3" };
}
let settingsOverrides = freshDemoOverrides(); // key -> raw string, mirrors the `settings` table's TEXT column
const settingsReadonly = false; // demo mode never ships VAULT_SETTINGS_READONLY

function resetDemoSettings() {
  settingsOverrides = freshDemoOverrides();
  demoCoreGeneration = 1;
  demoCoreAppliedAt = DEMO_CORE_APPLIED_AT;
}

function demoEnvPinned(spec) {
  return spec.env !== undefined;
}

/** settings_store.describe_settings, demo edition (env > stored > default). */
function describeDemoSettings() {
  const infos = [];
  for (const spec of DEMO_SETTING_SPECS) {
    const pinned = demoEnvPinned(spec);
    const raw = settingsOverrides[spec.key];
    let stored = null;
    let storedOk = false;
    if (raw !== undefined) {
      try {
        stored = spec.parse(spec.key, raw);
        storedOk = true;
      } catch {
        storedOk = false; // the real server logs and ignores a bad row
      }
    }
    const target = spec.target || "api";
    const fallback = pinned ? spec.env : spec.default;
    let source;
    let effective;
    if (pinned) {
      source = "env";
      effective = spec.env;
    } else if (storedOk) {
      source = "db";
      effective = stored;
    } else {
      source = "default";
      effective = spec.default;
    }
    infos.push({
      key: spec.key,
      effective,
      source,
      fallback,
      applies: spec.applies,
      env_only: false,
      env_var: spec.env_var,
      value: storedOk ? stored : null,
      editable: !pinned,
      risky: spec.risky === true,
      expert: spec.expert === true,
      grammar: spec.grammar,
      target,
      env_visible: target !== "core",
      shadowed: pinned && storedOk,
      // D16b-1 review: why a row is not editable. The demo hands core keys
      // to vault-core (as with VAULT_CORE_SETTINGS_FILE set), so it never
      // reports core_file_disabled.
      reason: pinned ? "env" : null,
    });
  }
  for (const entry of ENV_ONLY_DEMO) {
    // The two ADR-0010 relay-privacy keys report "env" whenever
    // resetDemoData() turned them on, mirroring the real server's
    // `_env_var_is_set(...)`; the demo webhook URL is "set in env" too.
    const isRelay = entry.key === "relay_expose_playtime" || entry.key === "relay_expose_last_played";
    const source = (isRelay && entry.value === true) || entry.key === "webhook_url" ? "env" : "default";
    infos.push({
      key: entry.key,
      effective: entry.value,
      source,
      fallback: entry.value,
      applies: "restart-required",
      env_only: true,
      env_var: entry.env_var,
      value: null,
      editable: false,
      risky: false,
      expert: false,
      grammar: "",
      target: "api",
      env_visible: true,
      shadowed: entry.key in settingsOverrides,
      reason: "env_only",
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

// D16c `core_apply`: the demo's vault-core applies every core settings
// write at once (applied, never pending); the generation counts the writes
// like vault-api's settings file does.
let demoCoreGeneration = 1;
const DEMO_CORE_APPLIED_AT = isoAgo(3 * 3_600_000);
let demoCoreAppliedAt = DEMO_CORE_APPLIED_AT;

function demoCoreApply() {
  return {
    state: "applied",
    generation: demoCoreGeneration,
    applied_generation: demoCoreGeneration,
    reason: "",
    updated_at: demoCoreAppliedAt,
    written_generation: demoCoreGeneration,
    pending: false,
  };
}

function handleGetSettings() {
  return {
    readonly: settingsReadonly,
    server_version: DEMO_SERVER_VERSION,
    settings: describeDemoSettings(),
    core_apply: demoCoreApply(),
  };
}

const DEMO_RISKY_DETAIL =
  "This change includes debug settings that can break downloads. Repeat the request with confirm_risky=true to apply it.";

/** Audit 3.4 cross rules on the values the batch leaves behind, only when
 * the batch touches them (settings_store.check_cross_rules). */
function checkDemoCrossRules(prospective, touched) {
  const value = (key) => {
    const spec = DEMO_SPEC_BY_KEY.get(key);
    if (demoEnvPinned(spec)) return Number(spec.env);
    const raw = prospective[key];
    return Number(raw === undefined ? spec.default : spec.parse(key, raw));
  };
  if (touched.has("upstream_max_conns") || touched.has("prefill_max_threads")) {
    if (value("upstream_max_conns") < value("prefill_max_threads")) {
      throw validationError(
        `VAULT_UPSTREAM_MAX_CONNS=${value("upstream_max_conns")} is below VAULT_PREFILL_MAX_THREADS=${value("prefill_max_threads")}: the cap must be at least the prefill threads.`,
      );
    }
  }
  if (touched.has("tls_client_max_conns") || touched.has("tls_max_conns")) {
    if (value("tls_client_max_conns") > value("tls_max_conns")) {
      throw validationError(
        `VAULT_TLS_CLIENT_MAX_CONNS=${value("tls_client_max_conns")} is above VAULT_TLS_MAX_CONNS=${value("tls_max_conns")}.`,
      );
    }
  }
}

function handlePatchSettings(body, params) {
  if (settingsReadonly) {
    throw new ApiError(ERROR_KINDS.VALIDATION, "The settings API is read-only.", { status: 403 });
  }
  const toSet = [];
  const toClear = [];
  for (const [key, value] of Object.entries(body || {})) {
    if (ENV_ONLY_KEYS.has(key)) {
      // Byte-identical to routers/settings.py's `_ENV_ONLY_DETAIL_TEMPLATE`.
      throw validationError(
        `'${key}' is environment-only and cannot be changed via the API; set its environment variable and restart instead.`,
      );
    }
    const spec = DEMO_SPEC_BY_KEY.get(key);
    if (!spec) {
      throw validationError(`'${key}' is not a recognised setting.`);
    }
    if (demoEnvPinned(spec)) {
      // routers/settings.py `_ENV_PINNED_DETAIL_TEMPLATE`.
      throw new ApiError(
        ERROR_KINDS.VALIDATION,
        "PATCH /v1/settings failed (409)",
        {
          status: 409,
          detail: `'${key}' is set via env (${spec.env_var}); env always wins. Remove the environment variable and restart to change it here.`,
        },
      );
    }
    if (value === null) {
      toClear.push(key);
      continue;
    }
    const raw = coercePatchValue(key, value);
    // schedule_window: blank means off (D16b-1 review), null resets it.
    const parsed = spec.parse(key, raw); // validate only, same "validate everything first" order
    toSet.push([key, spec.target === "core" ? String(parsed) : raw]);
  }
  const risky = [...toSet.map(([k]) => k), ...toClear].filter((k) => DEMO_SPEC_BY_KEY.get(k).risky).sort();
  const confirmed = params && String(params.confirm_risky).toLowerCase() === "true";
  if (risky.length > 0 && !confirmed) {
    throw new ApiError(ERROR_KINDS.VALIDATION, "PATCH /v1/settings failed (428)", {
      status: 428,
      detail: { message: DEMO_RISKY_DETAIL, risky_keys: risky },
    });
  }
  const prospective = { ...settingsOverrides };
  for (const [key, raw] of toSet) prospective[key] = raw;
  for (const key of toClear) delete prospective[key];
  checkDemoCrossRules(prospective, new Set([...toSet.map(([k]) => k), ...toClear]));
  // Everything validated above before anything is written (ADR-0009: a bad
  // value in a multi-key PATCH must persist nothing).
  settingsOverrides = prospective;
  // vault-api rewrites the core file only when a core key changed; the demo
  // core applies that at once. An api-only write leaves the generation alone.
  const touchesCore = [...toSet.map(([k]) => k), ...toClear].some((k) => DEMO_SPEC_BY_KEY.get(k).target?.includes("core"));
  if (touchesCore) {
    demoCoreGeneration += 1;
    demoCoreAppliedAt = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }
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
    // D16b: "off" (no window) is how GET /v1/settings spells it now.
    enabled: typeof window === "string" && window.length > 0 && window !== "off",
    window: typeof window === "string" && window.length > 0 && window !== "off" ? window : null,
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

/** The browser demo's seeded Steam account (WP UPD-5 demo pill, see
 * `readRelayPlaytimePreference`). Fictional: the key is a placeholder that
 * only passes the 32-hex grammar (demo mode never calls Valve), and the
 * SteamID64 is the top of the individual-account range
 * (`STEAM_ID64_BASE + 0xffffffff`), not a person's account. */
export const DEMO_STEAM_WEB_API_KEY = "0123456789abcdef0123456789abcdef";
export const DEMO_STEAMID = "76561202255233023";

/** Seed the demo Steam account the way an operator would set it up: the
 * key through the same path as `PUT /v1/steam/key`, the SteamID as a stored
 * (`db`) `steam_library_steamid` override, like a Settings save. */
function seedDemoSteamAccount() {
  handlePutSteamKey({ key: DEMO_STEAM_WEB_API_KEY });
  settingsOverrides.steam_library_steamid = DEMO_STEAMID;
}

if (readRelayPlaytimePreference()) seedDemoSteamAccount();

// Fictional owned-games fixture (LEARNINGS "Testing discipline": synthetic,
// never real Steam data) — deliberately a DIFFERENT list from the cache
// library's demo games above: this is what "the Steam Web API says this
// account owns", which in real life is almost always a much bigger,
// unrelated set from what happens to be cached. The two lists OVERLAP on
// purpose (WP WEB-FEAT-1): 2010010/2010040/2010050 are vault games too, the
// other three are owned-only, so the Library's merge shows both cases (dedupe by
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
  // WP WEB-FEAT-SUGGEST-2: cached in the vault and never played (playtime
  // 0 below), so the card's "Never played" pill has a demo case.
  { appid: 2010050, name: "Frostline Convoy", img_icon_url: "" },
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
  2010050: { playtime_forever: 0 }, // cached and never played (WP WEB-FEAT-SUGGEST-2)
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

/** `update_available` / `update_checked_at` (WP UPD-1), mirroring
 * `games._update_fields`: a stale verdict counts only while the app has
 * bytes in the cache, and a game the oracle never answered for is
 * `false`/`null`. */
function demoUpdateFields(g) {
  return {
    update_available: g._demoUpdateAvailable === true && g.update_checked_at !== null && !!appSizeBytes(g.depots),
    update_checked_at: g.update_checked_at,
  };
}

/** `group_size_bytes` (WP TOOL-APPS-2, api/README.md "Group size"): on a
 * top-level row the union of its depots and those of every row whose
 * `parent_appid` is this app, each depot once; `null` on a grouped row and
 * under the same `null` rule as `size_bytes` (no depots at all). */
function demoGroupSizeBytes(g) {
  if (g.parent_appid != null) return null;
  const union = new Map();
  for (const m of [g, ...games.filter((c) => c.parent_appid === g.appid)]) {
    for (const d of m.depots) union.set(d.depotid, d.size_bytes ?? 0);
  }
  if (union.size === 0) return null;
  let total = 0;
  for (const v of union.values()) total += v;
  return total;
}

/** WP CACHE-COMPLETE-1c: the row's own `cache_complete_*` /
 * `cache_missing_chunks` (api/README.md "Cache completeness"): all null when
 * nothing is on disk or the figure is not known, `pct` floored. */
function demoOwnFigure(g) {
  const size = appSizeBytes(g.depots);
  const c = g._demoCompleteness;
  if (!size || c === null) return null;
  const expected = c === undefined ? size : c.expected;
  const present = c === undefined ? size : c.present;
  return {
    pct: Math.floor((100 * present) / expected),
    expected,
    present,
    missing: c === undefined ? 0 : c.missing,
    checkedAt: g._demoCompletenessCheckedAt,
  };
}

/** `group_cache_complete_pct` (D9): over the members with bytes on disk,
 * unknown when any of them is; null on a grouped row and with nothing on
 * disk. Demo approximation: a depot shared inside the group is counted per
 * member (the real API counts it once). */
function demoGroupPct(g) {
  if (g.parent_appid != null) return null;
  const members = [g, ...games.filter((c) => c.parent_appid === g.appid)].filter((m) => appSizeBytes(m.depots));
  if (members.length === 0) return null;
  let expected = 0;
  let present = 0;
  for (const m of members) {
    const f = demoOwnFigure(m);
    if (!f) return null;
    expected += f.expected;
    present += f.present;
  }
  return Math.floor((100 * present) / expected);
}

function demoCompletenessFields(g) {
  const f = demoOwnFigure(g);
  return {
    cache_complete_pct: f ? f.pct : null,
    cache_expected_bytes: f ? f.expected : null,
    cache_present_bytes: f ? f.present : null,
    cache_complete_checked_at: f ? f.checkedAt : null,
    cache_missing_chunks: f ? f.missing : null,
    group_cache_complete_pct: demoGroupPct(g),
  };
}

/** The server's `is_incomplete` (known, below 100) and `is_completable`
 * (at least one chunk file missing) against the demo figure. */
function demoIsIncomplete(g) {
  const f = demoOwnFigure(g);
  return !!f && f.pct < 100;
}
function demoIsCompletable(g) {
  const f = demoOwnFigure(g);
  return !!f && f.pct < 100 && f.missing > 0;
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
    // WP API-FIX-5: additive on both game shapes. The demo has no server
    // lookup, so every demo game uses the legacy cover path.
    cover_url: null,
    ...demoUpdateFields(g), // WP WEB-FIX-UPD-VIS (WP UPD-1 fields)
    parent_appid: g.parent_appid, // WP TOOL-APPS-2c (WP TOOL-APPS-2a fields)
    app_type: g.app_type,
    group_size_bytes: demoGroupSizeBytes(g), // WP TOOL-APPS-2
    // WP D10: additive on both game shapes; the demo stores no covers.
    cover_cached_url: null,
    ...demoCompletenessFields(g), // WP CACHE-COMPLETE-1c (WP CACHE-COMPLETE-1a/1b fields)
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
    cover_url: null, // WP API-FIX-5 (see gameSummary's own comment)
    ...demoUpdateFields(g), // WP WEB-FIX-UPD-VIS (see gameSummary)
    parent_appid: g.parent_appid, // WP TOOL-APPS-2c (see gameSummary)
    app_type: g.app_type,
    group_size_bytes: demoGroupSizeBytes(g), // WP TOOL-APPS-2
    cover_cached_url: null, // WP D10 (see gameSummary's own comment)
    ...demoCompletenessFields(g), // WP CACHE-COMPLETE-1c (see gameSummary)
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
    queue_position: demoQueuePosition(j),
  };
}

/** WP D20: the queued jobs in draw order. A job the user moved carries a
 * `_demoOrder`; one that was never moved queues behind them, oldest first. */
function demoQueuedInOrder() {
  return jobs
    .filter((j) => j.status === "queued")
    .sort((a, b) => (a._demoOrder ?? Infinity) - (b._demoOrder ?? Infinity) || a.id - b.id);
}

/** 0 = runs next; null for a job that is not queued (as the real API). */
function demoQueuePosition(j) {
  if (j.status !== "queued") return null;
  return demoQueuedInOrder().findIndex((q) => q.id === j.id);
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
    // WP WEB-FIX-UPD-VIS: the newer build is now cached (the Android demo
    // does the same, DemoState.kt).
    game._demoUpdateAvailable = false;
    // WP CACHE-COMPLETE-1c: the worker re-measures after every successful
    // prefill. Missing chunks are fetched now; chunk files with the wrong
    // size stay (a forced run serves them as HITs, api/README.md "Damaged
    // chunks"), so a damaged-only figure does not change.
    const c = game._demoCompleteness;
    if (!(c && c.missing === 0 && c.present < c.expected)) game._demoCompleteness = undefined;
    game._demoCompletenessCheckedAt = job.finished_at.replace(/\.\d{3}Z$/, "Z");
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

/** WP D15c: `GET /v1/jobs?range=` spans; `all` has none. */
const HISTORY_RANGE_MS = Object.freeze({ "24h": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000, all: null });

const JOB_ID_RE = /^\/v1\/jobs\/(\d+)$/;
const JOB_PAUSE_RE = /^\/v1\/jobs\/(\d+)\/pause$/;
const JOB_RESUME_RE = /^\/v1\/jobs\/(\d+)\/resume$/;
const JOB_MOVE_RE = /^\/v1\/jobs\/(\d+)\/move$/;
const GAME_ID_RE = /^\/v1\/games\/(\d+)$/;
const CACHE_APPID_RE = /^\/v1\/cache\/(\d+)$/;
const GC_APPID_RE = /^\/v1\/cache\/(\d+)\/gc$/;
const DELETE_PLAN_RE = /^\/v1\/cache\/(\d+)\/delete-plan$/;

// ---------------------------------------------------------------------
// Grouped delete (WP TOOL-APPS-2c; api/README.md "Grouped delete",
// WP TOOL-APPS-2b). Same contract as the real routes, against this demo's
// one-list-per-game model: the delete set is the game plus every child
// whose OWN type is a package type; children of another type are kept and
// reported; members do not protect each other's depots, every app outside
// the set protects under the same `hasCacheContent` rule the plain delete
// uses.
// ---------------------------------------------------------------------

const PACKAGE_TYPES = new Set(["dlc", "config", "music", "video", "demo"]);

function groupSelection(appid) {
  const children = games.filter((g) => g.parent_appid === appid && g.appid !== appid);
  const packages = children.filter((g) => PACKAGE_TYPES.has(g.app_type));
  const kept = children
    .filter((g) => !PACKAGE_TYPES.has(g.app_type))
    .map((g) => ({
      appid: g.appid,
      name: g.name,
      app_type: g.app_type,
      reason: g.app_type == null ? "type_unknown" : "not_a_package_type",
    }));
  return { members: [findGame(appid), ...packages], kept };
}

function groupDeletePlan(appid, withPackages) {
  const game = findGame(appid);
  if (!game) throw notFound(`Unknown appid ${appid}`);
  const { members, kept } = withPackages ? groupSelection(appid) : { members: [game], kept: [] };
  const memberIds = new Set(members.map((m) => m.appid));
  const depotOwners = new Map();
  for (const m of members) {
    for (const d of m.depots) {
      if (!depotOwners.has(d.depotid)) depotOwners.set(d.depotid, { size: d.size_bytes ?? null, owners: [] });
      depotOwners.get(d.depotid).owners.push(m.appid);
    }
  }
  if (depotOwners.size === 0) throw notFound(`App ${appid} has no depot mappings, so there is nothing to delete.`);
  const deleteDepots = [];
  const skippedShared = [];
  for (const [depotid, { size, owners }] of [...depotOwners.entries()].sort((a, b) => a[0] - b[0])) {
    const outside = games.filter((g) => !memberIds.has(g.appid) && g.depots.some((d) => d.depotid === depotid));
    if (outside.some(hasCacheContent)) {
      skippedShared.push({ depotid, shared_with: outside.map((g) => g.appid) });
    } else {
      deleteDepots.push({ depotid, size_bytes: size, owners, shared_with_uncached: outside.map((g) => g.appid) });
    }
  }
  const blockedBy = jobs
    .filter((j) => memberIds.has(j.appid) && ["queued", "running", "paused"].includes(j.status))
    .map((j) => ({ appid: j.appid, job_id: j.id, type: j.type, status: j.status }));
  return {
    appid,
    with_packages: withPackages,
    members: members.map((m) => ({
      appid: m.appid,
      role: m.appid === appid ? "parent" : "package",
      name: m.name,
      app_type: m.app_type,
      depot_ids: m.depots.map((d) => d.depotid).sort((a, b) => a - b),
    })),
    kept_children: kept,
    delete_depots: deleteDepots,
    skipped_shared: skippedShared,
    unusable: [],
    total_bytes: deleteDepots.reduce((sum, d) => sum + (d.size_bytes || 0), 0),
    blocked_by: blockedBy,
  };
}

function parseMembers(raw) {
  if (typeof raw !== "string" || !/^\d+(,\d+)*$/.test(raw)) throw validationError("members must be comma-separated app ids");
  return new Set(raw.split(",").map(Number));
}

function handleGroupDelete(appid, membersRaw) {
  const confirmed = parseMembers(membersRaw);
  const plan = groupDeletePlan(appid, true);
  const server = plan.members.map((m) => m.appid).sort((a, b) => a - b);
  const same = server.length === confirmed.size && server.every((id) => confirmed.has(id));
  if (!same) {
    const detail = {
      message:
        "The apps this delete would remove differ from the confirmed list. Nothing was deleted; fetch the delete plan again and confirm the current list.",
      server_members: server,
      confirmed_members: [...confirmed].sort((a, b) => a - b),
    };
    throw new ApiError(ERROR_KINDS.VALIDATION, detail.message, { status: 409, detail });
  }
  if (plan.blocked_by.length) {
    const job = plan.blocked_by[0];
    throw conflict(`${job.type === "gc" ? "GC" : "Prefill"} job ${job.job_id} for app ${job.appid} is ${job.status}.`);
  }
  const deletedIds = new Set(plan.delete_depots.map((d) => d.depotid));
  const lostByMember = new Map(plan.members.map((m) => [m.appid, m.depot_ids.filter((id) => deletedIds.has(id))]));
  for (const g of games) g.depots = g.depots.filter((d) => !deletedIds.has(d.depotid));
  for (const d of plan.delete_depots) {
    for (const id of d.shared_with_uncached) {
      const co = findGame(id);
      if (co) co.needs_force = true;
    }
  }
  for (const m of plan.members) {
    const g = findGame(m.appid);
    const lost = lostByMember.get(m.appid);
    if (m.role === "parent" || lost.length) {
      g.status = "idle";
      g.last_prefill_at = null;
      if (lost.length) g.needs_force = true;
    }
  }
  return {
    appid,
    deleted_depots: plan.delete_depots.map((d) => ({
      depotid: d.depotid,
      size_bytes_freed: d.size_bytes || 0,
      shared_with_uncached: d.shared_with_uncached,
    })),
    skipped_shared: plan.skipped_shared,
    failed: [],
    total_bytes_freed: plan.total_bytes,
    with_packages: true,
    members: plan.members.map((m) => ({
      appid: m.appid,
      role: m.role,
      name: m.name,
      app_type: m.app_type,
      deleted_depots: lostByMember.get(m.appid),
      failed_depots: [],
    })),
    kept_children: plan.kept_children,
  };
}
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

// ---------------------------------------------------------------------
// /v1/updates/* (WP UPD-1 contract; demo added by WP WEB-FIX-UPD-VIS) —
// "Check updates" in demo mode. Nothing is asked of any update source: a
// started check answers `running: true` to exactly ONE status read (so the
// busy button and the "Checking cached games…" line are visible for one
// poll) and has finished by the next one, counting the cached games (the
// same `selectCachedAppids()` definition "Check & update" uses, like the
// real route) and stamping their `update_checked_at`. The verdicts
// themselves never change here: the fixture's own flag (Emberreach) is the
// one "update available". Shapes follow `UpdateCheckStartOut` /
// `UpdateCheckStatusOut` (api/vault_api/routers/updates.py), keys pinned by
// web/tests/demo-data-updates.test.js.
// ---------------------------------------------------------------------

/** Number of status reads a started demo check answers `running: true`. */
export const DEMO_UPDATE_CHECK_RUNNING_READS = 1;

function finishDemoUpdateRun(state, trigger, startedAt, finishedAt) {
  const appids = selectCachedAppids();
  for (const appid of appids) {
    const g = findGame(appid);
    if (g) g.update_checked_at = finishedAt;
  }
  const stale = appids.filter((appid) => demoUpdateFields(findGame(appid)).update_available).length;
  Object.assign(state, {
    running: false,
    last_trigger: trigger,
    last_started_at: startedAt,
    last_finished_at: finishedAt,
    last_outcome: "completed",
    last_apps: appids.length,
    last_ok: appids.length,
    last_failed: 0,
    last_stale: stale,
  });
}

/** Demo game whose update check failed in the seeded run, so its last
 * successful check is old enough for the "update check due" notification
 * (WP WEB-FEAT-SUGGEST-2, 14 days). A manual demo check succeeds for it. */
export const DEMO_UPDATE_CHECK_OVERDUE_APPID = 2010070; // Glass Meridian
const DEMO_UPDATE_CHECK_OVERDUE_DAYS = 20;

/** The demo's starting point: a scheduled check finished three hours ago
 * (the demo ships a schedule window, so `scheduled` is true). The oracle
 * did not answer for one game (Glass Meridian), whose last successful
 * check is therefore 20 days old. */
function seedDemoUpdateCheck() {
  const state = { running: false, _readsLeft: 0 };
  const startedAt = isoAgo(3 * 3_600_000).replace(/\.\d{3}Z$/, "Z");
  const finishedAt = isoAgo(3 * 3_600_000 - 40_000).replace(/\.\d{3}Z$/, "Z");
  finishDemoUpdateRun(state, "scheduled", startedAt, finishedAt);
  const overdue = findGame(DEMO_UPDATE_CHECK_OVERDUE_APPID);
  if (overdue && overdue.update_checked_at !== null) {
    overdue.update_checked_at = isoAgo(DEMO_UPDATE_CHECK_OVERDUE_DAYS * 86_400_000).replace(/\.\d{3}Z$/, "Z");
    state.last_ok -= 1;
    state.last_failed += 1;
  }
  return state;
}

updateCheck = seedDemoUpdateCheck();

function demoUpdateStatusOut() {
  const s = updateCheck;
  return {
    enabled: true,
    // WP UPD-5: `scheduled` means "the oracle is on" (it equals `enabled`),
    // whether or not a schedule window is configured.
    scheduled: true,
    running: s.running,
    last_trigger: s.last_trigger,
    last_started_at: s.last_started_at,
    last_finished_at: s.running ? null : s.last_finished_at,
    last_outcome: s.running ? null : s.last_outcome,
    last_apps: s.running ? null : s.last_apps,
    last_ok: s.running ? null : s.last_ok,
    last_failed: s.running ? null : s.last_failed,
    last_stale: s.running ? null : s.last_stale,
  };
}

function handleGetUpdatesStatus() {
  const s = updateCheck;
  if (s.running) {
    if (s._readsLeft > 0) {
      s._readsLeft -= 1;
    } else {
      finishDemoUpdateRun(s, "manual", s.last_started_at, new Date().toISOString().replace(/\.\d{3}Z$/, "Z"));
    }
  }
  return demoUpdateStatusOut();
}

function handlePostUpdatesCheck() {
  if (updateCheck.running) return { status: "already_running", enabled: true };
  updateCheck.running = true;
  updateCheck._readsLeft = DEMO_UPDATE_CHECK_RUNNING_READS;
  updateCheck.last_trigger = "manual";
  updateCheck.last_started_at = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  return { status: "started", enabled: true };
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
    // WP CACHE-COMPLETE-1c: `?incomplete=true` (WP CACHE-COMPLETE-1b).
    if (params?.incomplete === true || params?.incomplete === "true") {
      return games.filter(demoIsIncomplete).map(gameSummary);
    }
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

  // WP D19-1b: the depot preview is off by default (own env switch).
  if (method === "GET" && /^\/v1\/oracle\/\d+\/preview$/.test(path)) {
    return { state: "off" };
  }

  // WP D19-1c: store details are off by default too (own env switch).
  if (method === "GET" && /^\/v1\/store\/\d+\/details$/.test(path)) {
    return { state: "off" };
  }

  if (method === "GET" && path === "/v1/jobs") {
    // WP D15c: the history filter (`range`, WP D15b). Its fetch does not
    // advance the job simulation; only the store's plain poll does, so the
    // demo job runs at the same pace with the Downloads view open.
    const range = params?.range;
    if (range !== undefined && !Object.hasOwn(HISTORY_RANGE_MS, range)) {
      throw validationError("range must be one of 24h, 7d, 30d, all");
    }
    if (range === undefined) tickAllJobs();
    const limit = Number(params?.limit ?? 20);
    const span = range === undefined ? null : HISTORY_RANGE_MS[range];
    const cutoff = span ? Date.now() - span : null;
    return jobs
      .slice()
      .filter((j) => {
        if (cutoff === null || ["queued", "running", "paused"].includes(j.status)) return true;
        return Date.parse(j.finished_at || j.started_at || j.created_at) >= cutoff;
      })
      .sort((a, b) => b.id - a.id)
      .slice(0, limit)
      .map(jobSummary);
  }

  // WP GRAPH-LIVE: the stitched live series (no range parameter).
  if (method === "GET" && path === "/v1/throughput/live") return demoLive(Date.now(), historySinceMs);

  // WP D15c: the traffic endpoints (WP D15b), from demo-traffic.js.
  if (method === "GET" && (path === "/v1/throughput" || path === "/v1/throughput/series" || path === "/v1/clients/overview")) {
    const fallback = path === "/v1/clients/overview" ? "7d" : "24h";
    const range = params?.range ?? fallback;
    // WP GRAPH-ZOOM: the throughput endpoints also take 1h and 6h (the
    // 1-minute tier); the clients overview keeps the four.
    const shortOk = path !== "/v1/clients/overview" && (range === "1h" || range === "6h");
    if (!shortOk && !Object.hasOwn(HISTORY_RANGE_MS, range)) {
      throw validationError(
        path === "/v1/clients/overview" ? "range must be one of 24h, 7d, 30d, all" : "range must be one of 1h, 6h, 24h, 7d, 30d, all",
      );
    }
    if (path === "/v1/throughput/series") return demoSeries(range, Date.now(), historySinceMs);
    const running = jobs.filter((j) => j.status === "running" && j.type === "prefill");
    if (path === "/v1/throughput") {
      return demoThroughput(
        range,
        running.map((j) => ({ id: j.id, appid: j.appid, started_at: j.started_at, name: findGame(j.appid)?.name ?? null })),
        Date.now(),
        historySinceMs,
      );
    }
    const installed = new Map();
    for (const g of games) {
      for (const e of g.installed_on) {
        if (!installed.has(e.client_id)) installed.set(e.client_id, []);
        installed.get(e.client_id).push(g.appid);
      }
    }
    const runningGame = running[0] ? { appid: running[0].appid, name: findGame(running[0].appid)?.name ?? null } : null;
    return demoClientsOverview(range, {
      agents: handleGetClients(),
      runningGame,
      games: games.map((g) => ({ appid: g.appid, name: g.name })),
      installed,
      addressName: demoAddressName,
      historySinceMs,
    });
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

  // WP CACHE-COMPLETE-1c: "Verify" / "Complete now" (POST
  // /v1/prefill/verify) and "Complete all" (POST /v1/prefill/incomplete),
  // api/README.md "Verify and complete": flag `needs_force` first, then the
  // one enqueue (same dedupe as POST /v1/prefill). The demo job runs
  // forced and re-measures on success (advanceJob).
  if (method === "POST" && path === "/v1/prefill/verify") {
    const appids = Array.isArray(body?.appids) ? body.appids : [];
    if (appids.length === 0) throw validationError("appids must be a non-empty list");
    for (const appid of appids) {
      if (!Number.isInteger(appid) || appid < 1) throw validationError(`invalid appid ${appid}`);
    }
    for (const appid of appids) {
      const game = findGame(appid);
      if (game) game.needs_force = true;
    }
    return appids.map(enqueuePrefillForAppid);
  }
  if (method === "POST" && path === "/v1/prefill/incomplete") {
    // No body read, like /v1/prefill/cached: the server selects.
    const selected = games
      .filter(demoIsCompletable)
      .map((g) => g.appid)
      .sort((a, b) => a - b);
    for (const appid of selected) findGame(appid).needs_force = true;
    return selected.map(enqueuePrefillForAppid);
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

  if (method === "POST" && (m = path.match(JOB_MOVE_RE))) {
    const id = Number(m[1]);
    const job = findJob(id);
    if (!job) throw notFound(`Unknown job id ${id}`);
    const position = body?.position;
    if (!Number.isInteger(position) || position < 0) throw validationError("position must be an integer >= 0");
    if (job.status !== "queued") throw conflict(`Job ${id} is ${job.status}, only a queued job can be moved`);
    const order = demoQueuedInOrder().filter((q) => q.id !== id);
    order.splice(Math.min(position, order.length), 0, job);
    order.forEach((q, i) => {
      q._demoOrder = i;
    });
    return { ...jobControlResponse(job, { status: "queued", outcome: "immediate", detail: "Moved." }), queue_position: demoQueuePosition(job) };
  }

  if (method === "GET" && (m = path.match(DELETE_PLAN_RE))) {
    return groupDeletePlan(Number(m[1]), params?.with_packages === "true");
  }

  if (method === "DELETE" && (m = path.match(CACHE_APPID_RE)) && params?.with_packages === "true") {
    return handleGroupDelete(Number(m[1]), params.members);
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
  if (method === "GET" && path === "/v1/client-names") {
    return handleListClientNames();
  }
  if (method === "PUT" && (m = path.match(CLIENT_NAME_RE))) {
    return handlePutClientName(m[1], m[2], body);
  }
  if (method === "DELETE" && (m = path.match(CLIENT_NAME_RE))) {
    return handleDeleteClientName(m[1], m[2]);
  }
  if (method === "DELETE" && path === "/v1/history") {
    return handleDeleteHistory(params?.scope);
  }
  if (method === "GET" && path === "/v1/about") {
    return handleGetAbout();
  }
  if (method === "GET" && path === "/v1/dist") {
    return handleGetDist();
  }

  if (method === "POST" && path === "/v1/updates/check") {
    return handlePostUpdatesCheck();
  }
  if (method === "GET" && path === "/v1/updates/status") {
    return handleGetUpdatesStatus();
  }
  if (method === "GET" && path === "/v1/settings") {
    return handleGetSettings();
  }
  if (method === "PATCH" && path === "/v1/settings") {
    return handlePatchSettings(body, params);
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
