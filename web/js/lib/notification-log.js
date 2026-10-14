/**
 * Notification log — read/unread lifecycle + navigate-target mapping
 * (WP 4a.7).
 *
 * This module deliberately does NOT diff anything itself. It consumes the
 * event objects `web/js/notifications.js` already produces on the polling
 * store's `"notifications"` channel (store.js, WP 4a.2/4a.3) — "consume,
 * don't reimplement" per this WP's brief. Its only job is turning that flat
 * event stream into the bell panel's list: newest-first, read/unread, and
 * "where does tapping this row go" (docs/design/vault-app-mockup-NOTES.md
 * round 6, "Notifications lead somewhere").
 *
 * **Persistence: session-only, in-memory (round-6 finding, decided here).**
 * The NOTES are silent on localStorage vs. session-only for the real app —
 * round 5's "no server-held notification store" only rules out a SERVER
 * side store, it says nothing about the browser tab surviving a reload.
 * This WP chooses session-only: a log surviving a reload would need its own
 * staleness/eviction story (is a three-day-old "download finished" toast
 * still useful context, or noise?) that no round of the mockup design work
 * ever worked through, and shipping persistence silently would be exactly
 * the kind of undocumented assumption docs/LEARNINGS.md's testing-discipline
 * section warns about. The log (and its `nextId` counter) lives in
 * `web/js/components/notifications.js`'s module-level state and is empty
 * again on every page load — revisit with an explicit decision if a
 * cross-reload log is ever wanted.
 *
 * **Why the log never seeds from current state, unlike the mockup's
 * `seedNotes()`.** The mockup fabricates an initial notification list from
 * whatever the demo data already looks like when it boots. This module
 * takes an intentionally narrower, more honest position: `notifications.js`
 * (the differ) fires nothing for state that was already true before the
 * app's first poll ("first-poll-silent", pinned in
 * web/tests/notifications.test.js) — reproducing the mockup's seeding here
 * would mean re-deriving "is this actually news" for already-true
 * conditions a second time, in a different module, with different rules.
 * The bell panel therefore legitimately starts empty and fills in as real
 * transitions happen after the app is open — contrast this with
 * `web/js/lib/bypass-banner.js`'s `bypassBannerVisible`, which is a live
 * snapshot predicate for exactly this reason (a persistent banner DOES need
 * to reflect "already true" state; a change LOG does not).
 *
 * Pure only — no DOM, no fetch, no clock reads (the caller supplies `at`,
 * an ISO-8601 string compatible with `lib/format.js`'s `formatTimestamp`,
 * and `startId`, the next free identifier). Covered in
 * web/tests/notification-log.test.js.
 */

/**
 * Presentation metadata per event type (mirrors the mockup's round-5
 * `NOTE_KIND` table), mapped onto THIS app's real status-icon vocabulary
 * (`components/status-icon.js`'s `kind` values) and its `tx-*` colour
 * classes (css/app.css). Literal, not derived from status-icon.js's own
 * tables — docs/LEARNINGS.md's constants-vs-literals rule: a cross-module
 * wire/label contract must be pinned by string literals in its test, not a
 * derived round-trip that would stay green even if status-icon.js's
 * vocabulary drifted.
 */
export const NOTIFICATION_META = Object.freeze({
  job_finished: Object.freeze({ icon: "cached", tx: "tx-cached", word: "Cached" }),
  job_failed: Object.freeze({ icon: "error", tx: "tx-error", word: "Failed" }),
  update_ready: Object.freeze({ icon: "stale", tx: "tx-stale", word: "Update ready" }),
  bypass_suspected: Object.freeze({ icon: "warn", tx: "tx-warn", word: "Warning" }),
  bypass_resolved: Object.freeze({ icon: "cached", tx: "tx-cached", word: "Resolved" }),
  // WP WEB-FEAT-SUGGEST-2: the standing "update check due" entry (see
  // syncUpdateCheckDue below).
  update_check_due: Object.freeze({ icon: "stale", tx: "tx-stale", word: "Check due" }),
});

/** Fallback for an event type this table doesn't know — fails toward a
 * visibly generic, never-blank presentation rather than throwing or
 * silently dropping the row (same "fail toward the safer/honest reading"
 * posture as notifications.js's own guards). */
const FALLBACK_META = Object.freeze({ icon: "none", tx: "tx-none", word: "Notice" });

/** @param {string} type @returns {{icon: string, tx: string, word: string}} */
export function metaFor(type) {
  return NOTIFICATION_META[type] || FALLBACK_META;
}

/** Hard cap on how many entries the in-memory log keeps (oldest dropped
 * first). A tab left polling for days must not grow this array without
 * bound — well above anything a bell panel is usable at anyway. */
export const MAX_LOG_ENTRIES = 200;

/**
 * Append one poll tick's worth of differ events to a log. Pure/immutable:
 * never mutates `log`, and the caller threads `nextId` through explicitly
 * (rather than this module holding a mutable counter itself) so the same
 * inputs always produce the same outputs — no hidden module-level state to
 * make this hard to test deterministically.
 *
 * @param {object[]} log current log, newest-first
 * @param {object[] | null | undefined} events this tick's batch from
 *   notifications.js (already filtered — never a first-poll storm)
 * @param {{at: string, startId: number}} stamp `at`: ISO-8601 timestamp for
 *   every entry in this batch; `startId`: the first id to assign.
 * @returns {{log: object[], nextId: number}}
 */
export function appendNotifications(log, events, { at, startId }) {
  const baseline = Array.isArray(log) ? log : [];
  if (!Array.isArray(events) || !events.length) {
    return { log: baseline, nextId: startId };
  }
  let id = startId;
  const fresh = events.map((event) => ({ ...event, id: id++, read: false, at }));
  // Newest-first: this batch's events go on top (in their own given order),
  // ahead of everything already in the log.
  const merged = [...fresh, ...baseline].slice(0, MAX_LOG_ENTRIES);
  return { log: merged, nextId: id };
}

/** @param {object[]} log @returns {number} */
export function unreadCount(log) {
  return Array.isArray(log) ? log.filter((entry) => !entry.read).length : 0;
}

/**
 * Mark every entry read (mockup round 5: "an unread badge that clears when
 * the panel is opened"). Immutable and a no-op-preserving identity when
 * nothing was unread, so a caller re-rendering on reference-equality
 * doesn't do pointless work every time the panel is reopened with nothing
 * new since the last open.
 * @param {object[]} log
 * @returns {object[]}
 */
export function markAllRead(log) {
  const list = Array.isArray(log) ? log : [];
  if (list.every((entry) => entry.read)) return list;
  return list.map((entry) => (entry.read ? entry : { ...entry, read: true }));
}

/**
 * Navigate-target mapping (round 6, "Notifications lead somewhere"),
 * literal-pinned per event type — see web/tests/notification-log.test.js.
 *
 * Adapted from the mockup's own targets (game detail sheet / clients sheet
 * / Downloads history row) onto what the REAL app can actually do without
 * reaching into web/js/views/library.js (the WP 4a.7 constraint this table
 * was first written under): a finished/failed job's own history row lives
 * in Downloads regardless of whether it succeeded or failed, so both land
 * there; a client bypass event opens the clients sheet.
 *
 * **`update_ready` target upgrade (WP 4a.4, the recorded WP 4a.7 TODO):**
 * now that the detail sheet exists (`components/game-detail-sheet.js`), a
 * game turning stale has a real per-game destination to deep-link into —
 * `update_ready`'s diffed event already carries `appid`/`name`
 * (`notifications.js`'s `diffGamesForNotifications`), so no new data is
 * needed to open it there instead of the whole Library view.
 */
const NAVIGATION_KIND = Object.freeze({
  job_finished: "downloads",
  job_failed: "downloads",
  update_ready: "detail",
  bypass_suspected: "clients",
  bypass_resolved: "clients",
  update_check_due: "library", // where the "Check updates" button is
});

/**
 * @param {object} entry a log entry (event fields + id/read/at)
 * @returns {{kind: "downloads", jobId: number} | {kind: "detail", appid: number, name?: string} | {kind: "clients"} | {kind: "library"}}
 */
export function navigationTargetFor(entry) {
  // Fails toward the Library, not a crash, for anything this table doesn't
  // recognize (same posture "detail" itself couldn't have without an
  // appid) — unchanged from before the update_ready upgrade.
  const kind = (entry && NAVIGATION_KIND[entry.type]) || "library";
  if (kind === "downloads") return { kind, jobId: entry.jobId };
  if (kind === "detail") return { kind, appid: entry.appid, name: entry.name };
  return { kind };
}

/** The one log key the standing "update check due" entry uses. */
export const UPDATE_CHECK_DUE_KEY = "update_check_due";

/**
 * Keep the log's single "update check due" entry in step with the current
 * list of cached games whose last update check is 14+ days old (WP
 * WEB-FEAT-SUGGEST-2; the list comes from `lib/decision-support.js`'s
 * `staleUpdateChecks`, run on every `GET /v1/games` tick).
 *
 * Unlike the differ's events this is a CONDITION, not a transition, so it
 * is the one deliberate exception to "the log never seeds from current
 * state" (module header): the user asked to be told. To keep it from
 * spamming, there is only ever ONE such entry, and it is unread only while
 * it names a game the user has not acknowledged yet:
 *   - empty list -> the entry is removed (the games were checked);
 *   - no entry yet, or a new appid joins -> one fresh entry on top, unread
 *     unless EVERY appid is in `seen` (acknowledged earlier, possibly
 *     before a reload: the bell persists `seen`, see
 *     `ackUpdateCheckDue`/`pruneUpdateCheckDueSeen`);
 *   - otherwise (same games, fewer games, day counts moving) -> the entry
 *     is updated where it is, read state and position kept.
 * So a reload does not re-raise it for the same games; only a NEW game
 * makes it unread again. Pure/immutable like `appendNotifications`.
 *
 * @param {object[]} log newest-first
 * @param {Array<{appid: number, days: number}>} items
 * @param {{at: string, startId: number, seen?: Iterable<number>}} stamp
 *   `at`/`startId` are used only for a fresh entry
 * @returns {{log: object[], nextId: number}}
 */
export function syncUpdateCheckDue(log, items, { at, startId, seen = [] }) {
  const baseline = Array.isArray(log) ? log : [];
  const list = Array.isArray(items) ? items : [];
  const idx = baseline.findIndex((e) => e && e.key === UPDATE_CHECK_DUE_KEY);
  const existing = idx === -1 ? null : baseline[idx];
  if (list.length === 0) {
    return { log: existing ? baseline.filter((_, i) => i !== idx) : baseline, nextId: startId };
  }
  const known = new Set(existing ? existing.appids : []);
  const grew = !existing || list.some((i) => !known.has(i.appid));
  const fields = { appids: list.map((i) => i.appid), items: list.map((i) => ({ ...i })) };
  if (!grew) {
    const sameItems =
      existing.items.length === list.length &&
      existing.items.every((e, i) => e.appid === list[i].appid && e.days === list[i].days);
    if (sameItems) return { log: baseline, nextId: startId };
    const next = baseline.slice();
    next[idx] = { ...existing, ...fields };
    return { log: next, nextId: startId };
  }
  const acknowledged = new Set(seen);
  const read = list.every((i) => acknowledged.has(i.appid));
  const fresh = { type: UPDATE_CHECK_DUE_KEY, key: UPDATE_CHECK_DUE_KEY, ...fields, id: startId, read, at };
  const rest = existing ? baseline.filter((_, i) => i !== idx) : baseline;
  return { log: [fresh, ...rest].slice(0, MAX_LOG_ENTRIES), nextId: startId + 1 };
}

/**
 * The acknowledged appids after the bell was opened: `seen` plus every
 * game the "update check due" entry currently names. Sorted, deduplicated.
 * @param {Iterable<number>} seen
 * @param {object[]} log
 * @returns {number[]}
 */
export function ackUpdateCheckDue(seen, log) {
  const out = new Set(seen);
  const entry = (Array.isArray(log) ? log : []).find((e) => e && e.key === UPDATE_CHECK_DUE_KEY);
  if (entry) for (const appid of entry.appids) out.add(appid);
  return [...out].sort((a, b) => a - b);
}

/**
 * `seen` reduced to the games that are still overdue: a game that was
 * checked (and so left the list) is forgotten, and becomes news again if
 * it is ever 14 days overdue again.
 * @param {Iterable<number>} seen
 * @param {Array<{appid: number}>} items
 * @returns {number[]}
 */
export function pruneUpdateCheckDueSeen(seen, items) {
  const current = new Set((Array.isArray(items) ? items : []).map((i) => i.appid));
  return [...new Set(seen)].filter((a) => current.has(a)).sort((a, b) => a - b);
}
