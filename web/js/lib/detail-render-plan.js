/**
 * Round-7 patch-in-place for the game detail sheet (WP 4a.4 brief: "the
 * sheet must not rebuild animated nodes on poll ticks — patch volatile
 * values, reuse the render-plan pattern"). Same rule as `lib/render-plan.js`
 * (Library grid) and `lib/downloads-render-plan.js` (Downloads job cards):
 * a background `GET /v1/games` / `GET /v1/jobs` tick that changes nothing
 * SHOWN as a shape (the header status icon, the job-control button set, a
 * depot's sharing tag) must never recreate that shape's DOM — only a tick
 * whose STRUCTURAL signature actually changed may rebuild it.
 *
 * The sheet's one animated node is the header `createStatusIcon` glyph
 * (`components/game-detail-sheet.js`'s `DetailHeader`-equivalent) — driven
 * by `dispKind` exactly like a Library card. Everything else volatile
 * (sizes, the last-download/confirmed-current lines, a co-owner's cached/
 * not-cached text) is plain text with nothing to animate, so patching it
 * unconditionally alongside the icon-preserving check is both correct and
 * simpler than tracking a separate signature per field.
 *
 * `buildDetailStructuralKey` folds the things that change the SHAPE
 * of the sheet body into one comparable string:
 *   - `dispKind` — the header icon + word, and which of
 *     download/retry/nothing shows;
 *   - the tracked job's status (or `null`) — which of the
 *     pause/resume/cancel buttons show (`lib/detail-job.js`);
 *   - each depot's sharing tag (`lib/depot-presentation.js`) — a co-owner's
 *     cache state changing on a LIVE poll tick (a different game finishing
 *     its own download) can flip PROTECTED -> ORPHANED for a depot this
 *     sheet is showing, which changes the tag/note text shown, not just a
 *     number, so it counts as structural too;
 *   - `installedBadge` (WP AG-2) — `lib/game-status.js`'s
 *     `installedSectionPresence`: `"none"`/`"present"`, deliberately NOT the
 *     raw 3-state `installedBadgeState` (none/cached/not_cached). A
 *     none<->present transition adds or removes the whole "Installed on"
 *     section, the same kind of shape change as the download/retry button
 *     appearing above. CACHED vs NOT_CACHED within an already-present
 *     section is NOT structural (review round 1, S4 — see
 *     `installedSectionPresence`'s own header for the measured reason: a
 *     live download's `size_bytes` can cross zero, flipping that sub-state,
 *     while `dispKind` stays `"running"` the whole time, so making it
 *     structural forced an unwanted full re-render — animated header icon
 *     recreated, scroll reset — once per download for every installed
 *     game). The client SET, a `reported_at` value, or that sub-state
 *     moving WITHOUT a presence transition is deliberately NOT part of this
 *     key (see `components/game-detail-sheet.js`'s `patchInstalledSection`,
 *     which patches all three cases in place).
 *
 * Three named mutation targets, same shape as the sibling render-plan
 * modules' test files: dropping `dispKind` from the key would let a
 * download-finishing tick leave the OLD status icon painted (rebuild
 * skipped) with a status word restarted only next full render; dropping
 * `depotTags` would let a co-owner's protection state change silently show
 * a stale sharing tag/note until something else forces a rebuild; dropping
 * `installedBadge` would let an app going from "no fresh report" to
 * "installed" (or back) never actually paint/remove that section until some
 * UNRELATED structural change happened to also fire.
 *
 * Pure — no DOM. Covered in web/tests/detail-render-plan.test.js.
 */

/**
 * @param {{dispKind: string, trackedJobStatus: string|null, depotTags: string[], installedBadge?: string}} input
 * @returns {string} an opaque, comparable signature — equal in and equal
 *   out is the only contract; never parse this string.
 */
export function buildDetailStructuralKey({ dispKind, trackedJobStatus, depotTags, installedBadge, packages, updates, completeness }) {
  const key = {
    k: dispKind,
    j: trackedJobStatus ?? null,
    d: Array.isArray(depotTags) ? depotTags : [],
    i: installedBadge ?? "none",
  };
  // WP TOOL-APPS-2c: the content-package rows (package-list.js
  // `packagesStructuralKey`), only for a game that has packages, so every
  // other game's key is unchanged.
  if (typeof packages === "string") key.p = packages;
  // WP UPD-6 review fix: the "Update available" header state and the app
  // ids the sheet's "Update now" would send, only when either is present,
  // so every other game's key is unchanged.
  if (typeof updates === "string" && updates) key.u = updates;
  // WP CACHE-COMPLETE-1c: the header's completeness overlay and the
  // completeness block's shape (Verify targets, damaged note, group line),
  // only when the block shows, so every other game's key is unchanged.
  if (typeof completeness === "string" && completeness) key.c = completeness;
  return JSON.stringify(key);
}
