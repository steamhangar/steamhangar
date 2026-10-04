/**
 * Game display-status logic (WP 4a.3).
 *
 * Ports the mockup's `dispKind`/`statusAct`/`hasContent` trio
 * (docs/design/vault-app-mockup.html, docs/design/vault-app-mockup-NOTES.md
 * round 5/6) onto the REAL `GET /v1/games` / `GET /v1/jobs` shapes
 * (api/README.md), which are narrower than the mockup's fake data in two
 * load-bearing ways documented inline below. Pure functions only — no DOM,
 * no fetch — so every branch is unit-testable headlessly
 * (web/tests/game-status.test.js).
 *
 * **Divergence 1 — no "stale" status.** `web/js/notifications.js` already
 * documents this: `apps.status` is exactly `idle|running|done|error` (WP
 * 3.12, "apps.status gains none"); there is no manifest-oracle field folded
 * into `GET /v1/games` (api/README.md, manifest-oracle section: "folding a
 * stale flag into the library list is a Phase-4 decision to make once the
 * UI knows how it wants to render it"). This WP makes that decision: ship
 * without the "Update ready" state/chip/glyph. `diffGamesForNotifications`
 * is already future-proofed for a `stale` status arriving later; this
 * module can gain a fourth `dispKind` the same day without any other
 * change here.
 *
 * **Divergence 2 — no live progress percentage.** The mockup's `job.pct`/
 * `job.speed`/`job.target` are simulator-only; the real `JobSummary` (see
 * api/README.md's jobs endpoints and vault_api/routers/jobs.py) carries no
 * byte-level progress field at all. The capsule pill therefore shows the
 * status icon ALONE while a job is running/paused (no fabricated
 * percentage) and the cached size once `size_bytes` is real — see
 * game-card.js. This also means the round-7 "patch in place" concern is
 * narrower in the real app than in the mockup (there is no live number to
 * patch); what MUST still be avoided is rebuilding a card on every jobs
 * poll tick just because a running job's `log_excerpt` grew (the worker
 * appends to it continuously) — `shouldRebuildForJob` below is the guard:
 * only a genuine `dispKind` transition warrants a rebuild, not a
 * byte-identical-status update.
 *
 * **Cache-content invariant, ported (mockup round 5, finding 6): "cached"
 * requires visible bytes.** A `GET /v1/games` row can legitimately report
 * `status: "done"` with `size_bytes: null` — not a server bug, but the
 * documented "last cached remnant" consequence (api/README.md, "Last
 * cached remnants"): app A's only depot was shared with app B, someone
 * deleted B's copy of it as an orphaned remnant, and A's own `status` is
 * untouched by that unrelated request. `hasVisibleCacheContent` is what
 * downgrades that to the honest "Not cached" card instead of a green badge
 * over nothing.
 *
 * Naming note (read before touching the deletion-side code in
 * multiplan.js): this module's `hasVisibleCacheContent` (BYTES-based,
 * "does the grid show this as cached right now") is a DIFFERENT predicate
 * from `hasProtectedCacheContent` (STATUS-based, mirrors
 * `deletion._has_cache_content` / demo-data.js's `hasCacheContent` — "does
 * this app's mapping protect a shared depot from deletion"). The two can
 * disagree (the remnant case above is exactly status-protected-but-not-
 * visibly-cached) — that is not a bug, it is why they are two functions.
 *
 * **Installed-state badge (WP AG-2).** `GET /v1/games`/`GET /v1/games/{appid}`
 * additively carry `installed_on: [{client_id, reported_at}]` (WP AG-1,
 * api/README.md "Installed state per app") — already pre-filtered to fresh
 * reports server-side, so `[]` means "no fresh report", NOT "not installed
 * anywhere". `installedBadgeState` below must never collapse that absence
 * into a negative claim: the `NONE` state renders no badge at all, never a
 * "not installed" sentence (docs/LEARNINGS.md, "a UI sentence built from ONE
 * API field over-claims unless that field alone carries the meaning" — an
 * empty list here is exactly the null/never-ran-style ambiguity that entry
 * warns about). "Has cache content" for the badge's CACHED/NOT_CACHED split
 * reuses `hasVisibleCacheContent` verbatim — the SAME bytes-based predicate
 * the grid already uses to decide `dispKind`'s CACHED/NONE split — rather
 * than a second, independently-computed definition (the exact failure class
 * docs/LEARNINGS.md's "two call sites computing the same domain predicate
 * WILL diverge" entry documents).
 *
 * **Steam tool apps (WP API-FIX-4).** `GET /v1/games` rows carry
 * `tool_app: true` + `tool_app_name` for an app vault-api never prefills
 * (api/vault_api/tool_apps.py: 228980, "Steamworks Common
 * Redistributables" — installed on every Windows PC, its depots cached
 * together with the games that use them). The server owns the list; this
 * module only reads the flag. Such a row gets its own display kind
 * `KIND.TOOL` (shown with the neutral "notinuse" dash glyph via
 * `statusIconKind`, and the word {@link TOOL_APP_STATE_WORD}), never
 * `error` — old failed jobs left `status: "error"` on its apps row, and
 * that must not paint the card red — and never a download/retry action.
 * A live job still wins (an honest "Downloading" for a job that was queued
 * before the upgrade). `installedBadgeState` never says "not cached" for it.
 */

/** Display-status kinds this module ever returns. Intentionally NOT the
 * mockup's full set (no "stale"/"updating"/"verify" — see module header). */
export const KIND = Object.freeze({
  CACHED: "cached",
  NONE: "none",
  RUNNING: "running",
  PAUSED: "paused",
  ERROR: "error",
  // WP API-FIX-4: a Steam tool package (see module header). A DISPLAY kind
  // only, not a status-icon kind: `statusIconKind` maps it to "notinuse".
  TOOL: "tool",
});

/** The status word a tool app's card and detail header show instead of a
 * STATUS_LABEL word (WP API-FIX-4). */
export const TOOL_APP_STATE_WORD = "Steam tool package";

/** The neutral note a tool app's detail sheet shows (WP API-FIX-4). */
export const TOOL_APP_NOTE = "Steam tool package — cached together with the games that use it.";

/** True for a `GET /v1/games` row the server flags as a Steam tool app
 * (WP API-FIX-4). Strictly `true`: an older server sends no flag at all. */
export function isToolApp(game) {
  return game?.tool_app === true;
}

/** Which status-icon kind (components/status-icon.js's STATUS_LABEL keys)
 * draws a display kind: KIND.TOOL uses the neutral "notinuse" dash, every
 * other kind is its own icon kind. */
export function statusIconKind(kind) {
  return kind === KIND.TOOL ? "notinuse" : kind;
}

/**
 * The visible status word for a display kind. `labels` is
 * components/status-icon.js's STATUS_LABEL, passed in so this module stays
 * DOM-free. KIND.TOOL reads {@link TOOL_APP_STATE_WORD}.
 * @param {string} kind
 * @param {Record<string, string>} labels
 */
export function statusWordFor(kind, labels) {
  if (kind === KIND.TOOL) return TOOL_APP_STATE_WORD;
  return labels[kind] || labels.none;
}

/**
 * Display name of a games row: the vault's own name, else the tool app's
 * name from the server (WP API-FIX-4), else `App {appid}`.
 * @param {{appid: number, name?: string|null, tool_app_name?: string|null}} game
 */
export function gameDisplayName(game) {
  if (typeof game?.name === "string" && game.name.trim()) return game.name.trim();
  if (isToolApp(game) && typeof game.tool_app_name === "string" && game.tool_app_name.trim()) {
    return game.tool_app_name.trim();
  }
  return `App ${game?.appid}`;
}

/** Job statuses that occupy this app's card with a live indicator. Queued
 * jobs are deliberately excluded (mockup parity: `jobFor` only matches
 * running/paused/verify — a queued job shows in the Downloads FIFO queue,
 * WP 4a.5, not on the Library card). GC jobs are excluded too: pause/resume
 * and the download pill are prefill-only concepts (api/README.md job
 * control table: pause on a GC job is `409`), so a GC job for this appid
 * must never drive its library card into a "running" download state. */
const LIVE_JOB_STATUSES = new Set(["running", "paused"]);

/**
 * Find the job (if any) that should drive this app's library card.
 * @param {object[] | null | undefined} jobs `GET /v1/jobs` snapshot.
 * @param {number} appid
 * @returns {object | undefined}
 */
export function findLiveJob(jobs, appid) {
  if (!Array.isArray(jobs)) return undefined;
  return jobs.find(
    (j) => j.appid === appid && j.type === "prefill" && LIVE_JOB_STATUSES.has(j.status),
  );
}

/** Build an `appid -> liveJob` lookup once per tick instead of re-scanning
 * the jobs array per card (O(n) instead of O(cards*jobs)). */
export function indexLiveJobsByAppid(jobs) {
  const map = new Map();
  if (!Array.isArray(jobs)) return map;
  for (const j of jobs) {
    if (j.type === "prefill" && LIVE_JOB_STATUSES.has(j.status)) map.set(j.appid, j);
  }
  return map;
}

/** Byte-based: does the grid have real content to show as cached right now?
 * See module header for why this is distinct from hasProtectedCacheContent. */
export function hasVisibleCacheContent(game) {
  return typeof game?.size_bytes === "number" && game.size_bytes > 0;
}

/**
 * Status-based: mirrors the server's own shared-depot protection predicate
 * exactly (`deletion._has_cache_content`, ported already once in
 * `web/js/demo-data.js`'s `hasCacheContent`): an app "has cache content"
 * unless it is `idle`, has never been prefilled, AND has no active job.
 * Used by multiplan.js to decide whether an OTHER app protects a shared
 * depot from a bulk delete — never for what the grid displays.
 *
 * @param {{status: string, last_prefill_at: string|null}} game
 * @param {boolean} hasActiveJob
 */
export function hasProtectedCacheContent(game, hasActiveJob) {
  const idle = game.status === "idle";
  const neverPrefilled = game.last_prefill_at == null;
  return !(idle && neverPrefilled && !hasActiveJob);
}

/** Installed-badge kinds `installedBadgeState` ever returns. `NONE` means
 * "no fresh report" (see module header) and MUST render as no badge at all —
 * never a "not installed" sentence. */
export const INSTALLED_BADGE = Object.freeze({
  NONE: "none",
  CACHED: "cached",
  NOT_CACHED: "not_cached",
});

/**
 * Which installed-badge a game's card/detail sheet should show, derived
 * purely from `installed_on` presence and `hasVisibleCacheContent` (see
 * module header for why that specific predicate, not a new one).
 * @param {{installed_on?: {client_id: string, reported_at: string}[], size_bytes?: number|null}} game
 * @returns {string} one of INSTALLED_BADGE's values
 */
export function installedBadgeState(game) {
  const installedOn = Array.isArray(game?.installed_on) ? game.installed_on : [];
  if (installedOn.length === 0) return INSTALLED_BADGE.NONE;
  // WP API-FIX-4: a tool app is never "installed but not cached" — its
  // depots are cached with the games that use them, so the warning would
  // be false. It reads as the plain "Installed on <pc>".
  if (isToolApp(game)) return INSTALLED_BADGE.CACHED;
  return hasVisibleCacheContent(game) ? INSTALLED_BADGE.CACHED : INSTALLED_BADGE.NOT_CACHED;
}

/**
 * A short "gaming-pc" / "gaming-pc +2" summary of an `installed_on` list —
 * the first client's id plus a count of the rest, so a card badge stays one
 * line regardless of how many clients report an app installed. `null` for an
 * empty/missing list (mirrors `installedBadgeState`'s NONE — nothing honest
 * to print, same "nothing to print" posture as format.js's helpers).
 * @param {{client_id: string, reported_at: string}[] | null | undefined} installedOn
 * @returns {string | null}
 */
export function installedOnSummary(installedOn) {
  const list = Array.isArray(installedOn) ? installedOn : [];
  if (list.length === 0) return null;
  const [first, ...rest] = list;
  return rest.length ? `${first.client_id} +${rest.length}` : first.client_id;
}

/**
 * The installed badge's own display text for a given state — `null` for
 * NONE (module header: no badge, no sentence). Shared by the card badge and
 * the card's accessible-name builder so the two can never say something
 * different about the same game.
 * @param {string} state one of INSTALLED_BADGE's values
 * @param {string | null} summary from installedOnSummary
 */
export function installedBadgeText(state, summary) {
  if (state === INSTALLED_BADGE.CACHED) return `Installed on ${summary}`;
  if (state === INSTALLED_BADGE.NOT_CACHED) return `Installed but not cached · ${summary}`;
  return null;
}

/**
 * A SHORTER form of {@link installedBadgeText} for the tightest layout
 * (`.grid.cols3` — see css/app.css's cols3 badge rules, WP AG-2 review S3).
 * Review measurement: the full NOT_CACHED string (149.8px) never fit the
 * card's own cols3 width, so what rendered was "Installed but not cach…" —
 * the one piece of information a badge exists to show (WHICH client) never
 * appeared at all. This drops the redundant lead-in ("Installed"/"Installed
 * but") since the badge's own colour/position already say "this is the
 * installed indicator" — only the fact this compact form cannot state
 * without the lead-in is "not cached", which it keeps.
 * @param {string} state one of INSTALLED_BADGE's values
 * @param {string | null} summary from installedOnSummary
 */
export function installedBadgeCompactText(state, summary) {
  if (state === INSTALLED_BADGE.CACHED) return summary;
  if (state === INSTALLED_BADGE.NOT_CACHED) return `not cached · ${summary}`;
  return null;
}

/**
 * The detail sheet's round-7 structural-key input for the "Installed on"
 * section (WP AG-2 review S4 — narrower than the raw 3-state
 * `installedBadgeState`, and deliberately so). Only whether the section
 * EXISTS AT ALL is structural; CACHED vs NOT_CACHED within an already-
 * existing section is NOT, because during a live download `size_bytes` can
 * cross zero while `dispKind` stays `"running"` (the live-job override
 * ignores bytes entirely) — feeding the raw 3-state value into the
 * structural key would force a full sheet re-render (animated header icon
 * recreated, scroll reset) purely from a byte count crossing zero, for any
 * installed game, every download. `components/game-detail-sheet.js`'s
 * `patchInstalledSection` handles the CACHED/NOT_CACHED note in place on
 * every patch tick instead — see that function's header.
 * @param {object} game GameSummary/GameDetail-shaped
 * @returns {string} `"none"` or `"present"`
 */
export function installedSectionPresence(game) {
  return installedBadgeState(game) === INSTALLED_BADGE.NONE ? "none" : "present";
}

/**
 * The status a card should SHOW: a live job overrides the cache state.
 * @param {object} game GameSummary
 * @param {object|undefined} liveJob from indexLiveJobsByAppid, or undefined
 */
export function dispKind(game, liveJob) {
  if (liveJob) return liveJob.status === "paused" ? KIND.PAUSED : KIND.RUNNING;
  // WP API-FIX-4: before the error check, so old failed jobs for a tool app
  // never make its card red.
  if (isToolApp(game)) return KIND.TOOL;
  if (game.status === "error") return KIND.ERROR;
  return hasVisibleCacheContent(game) ? KIND.CACHED : KIND.NONE;
}

/**
 * What tapping the capsule pill / list-row icon does. Returns null when
 * there is no honest action (mirrors the mockup's rule: a non-actionable
 * icon renders as a plain span, never a button).
 *
 * Deliberate extension over the mockup: an `error` game IS actionable here
 * (retry) — the mockup never modeled a persistent per-app error status (its
 * "error" only ever lived on a finished JOBS row, never on a GAMES row), so
 * it never had to decide this. The real `apps.status` genuinely can sit at
 * `error` indefinitely until re-prefilled (api/README.md, "Per-game
 * deletion": "`error` is the honest state... a re-prefill... repairs the
 * cache"), so offering the same "start a prefill" action as `none` is the
 * direct, honest fix rather than forcing the user into a not-yet-built
 * detail sheet (WP 4a.4) just to retry.
 *
 * @param {object} game
 * @param {object|undefined} liveJob
 * @param {boolean} selecting `true` while multi-select is active — a tap
 *   must toggle selection instead of firing the action (mockup parity).
 *   An `owned_only` row (WP WEB-FEAT-1) never gets an action here.
 */
export function statusAction(game, liveJob, selecting) {
  if (selecting) return null;
  // WP WEB-FEAT-1 (user decision): an owned game the vault does not know
  // (`owned_only`, synthesized by lib/owned-library.js) gets its download
  // action in the detail sheet only, never as a card quick action. The
  // sheet's own "not tracked" branch offers it (game-detail-sheet.js).
  if (game && game.owned_only === true) return null;
  if (liveJob) {
    if (liveJob.status === "running") return { type: "pause", title: "Pause download" };
    if (liveJob.status === "paused") return { type: "resume", title: "Resume download" };
    return null;
  }
  // WP API-FIX-4: a tool app's dispKind is TOOL, which falls through to
  // `null` below — vault-api never prefills it (POST /v1/prefill answers
  // 422), so there is no honest download or retry to offer.
  const kind = dispKind(game, undefined);
  if (kind === KIND.NONE) return { type: "download", title: "Download to cache" };
  if (kind === KIND.ERROR) return { type: "download", title: "Retry download" };
  return null; // cached — never a silent re-download (mockup round 5 rule)
}

/**
 * Round-7 rule, ported: decide whether a job transition on this appid is a
 * genuine STATE change (rebuild warranted) or a no-op update that must NOT
 * touch the card (e.g. `log_excerpt` growing on an otherwise-unchanged
 * running job — see module header, Divergence 2). Pure so the "a growing
 * log must never cause a rebuild" guarantee is directly mutation-testable.
 *
 * @param {object|undefined} prevJob `diffByKey` `prev` half (undefined for
 *   a brand-new row).
 * @param {object|undefined} currJob `diffByKey` `curr` half.
 */
export function isJobStateTransition(prevJob, currJob) {
  if (!currJob) return true; // job disappeared (finished/cancelled/removed) — always structural
  if (!prevJob) return true; // brand-new job row — always structural
  return prevJob.status !== currJob.status;
}
