/**
 * Depot-sharing WORDING decision for the detail sheet (WP 4a.4 brief:
 * "Depots with computed sharing (holders/sole-holder/orphan wording)") —
 * presentation logic layered on top of `lib/multiplan.js`'s
 * `buildMultiPlan` arithmetic, which this sheet reuses AS-IS for a
 * single-id batch (`buildMultiPlan([appid], ...)`, mirroring the Android
 * sibling's `DetailController` kdoc) rather than re-deriving the
 * co-owner/holder computation a second time — the ADR-0003 guarantee ("a
 * shared depot with an outside cached holder is never counted as freed",
 * docs/design/vault-app-mockup-NOTES.md round 5 "Shared is a mapping fact;
 * kept is a cache-state fact") therefore applies here for free, already
 * proven in multiplan.test.js and pinned again for the single-id shape in
 * web/tests/depot-presentation.test.js.
 *
 * A `MultiPlanDepotRow`'s (`multiplan.js`'s row shape) `free` field answers
 * "if THIS app is deleted, does this depot lose its last cached holder" —
 * that is exactly "sole holder" WHEN this app itself currently holds the
 * depot (mockup round 5, "Shared · sole holder": cache-state truth leads
 * over mapping truth), but a DIFFERENT, rarer case when it does not:
 * mockup-notes.md's sample-data note seeds Meridian Rally/Tundra Protocol
 * scenarios so "a shared depot with no cached co-owner" can exist even
 * though the VIEWED game is not a holder either. `DEPOT_TAG` keeps those two
 * apart (`SOLE_HOLDER` vs `ORPHANED`) by taking `thisAppIsHolder` as an
 * explicit input, computed ONCE per detail (by the same
 * `hasProtectedCacheContent` predicate used for every OTHER co-owner in
 * `row.holderAppids` itself) rather than re-deriving it per depot row.
 *
 * **Recorded divergence (WP 4b.6, adopted here per this WP's brief):** the
 * depot-sharing presentation has a FOURTH state beyond the mockup's three —
 * `ORPHANED`, for a shared depot whose co-owning apps all have no cache
 * content (ADR-0003 last-remnant case). Collapsing it into "sole holder"
 * would state a falsehood (the viewed game holds nothing either).
 *
 * Ported from the Android sibling's `ui/detail/logic/DepotPresentation.kt`
 * (WP 4b.6) — same four states, same inputs, same reasoning.
 *
 * Pure — no DOM, no fetch. Covered in web/tests/depot-presentation.test.js.
 */

import { UNKNOWN_APP_NAME } from "./app-names.js";

export const DEPOT_TAG = Object.freeze({
  EXCLUSIVE: "exclusive",
  SOLE_HOLDER: "sole_holder",
  PROTECTED: "protected",
  ORPHANED: "orphaned",
});

/**
 * @param {object} row one `MultiPlanDepotRow` from
 *   `buildMultiPlan([thisAppid], ...)` — `{depotid, sizeBytes, shared,
 *   others, holderAppids, free}`.
 * @param {Map<number, object>} gamesByAppid for resolving a co-owner's
 *   display name — falls back to "Unknown app" for one never seen on a
 *   `GET /v1/games` poll, same literal convention `views/library.js` and
 *   `views/downloads.js` already use.
 * @param {boolean} thisAppIsHolder whether the game THIS sheet is showing
 *   currently protects its own shared depots (see module header) —
 *   irrelevant for an EXCLUSIVE row, decisive for the SOLE_HOLDER/ORPHANED
 *   split.
 * @returns {{depotid: number, sizeBytes: number|null, tag: string,
 *   coOwners: {appid: number, name: string, cached: boolean}[]}}
 */
export function buildDepotPresentation(row, gamesByAppid, thisAppIsHolder) {
  let tag;
  if (!row.shared) {
    tag = DEPOT_TAG.EXCLUSIVE;
  } else if (row.holderAppids.length > 0) {
    tag = DEPOT_TAG.PROTECTED;
  } else if (thisAppIsHolder) {
    tag = DEPOT_TAG.SOLE_HOLDER;
  } else {
    tag = DEPOT_TAG.ORPHANED;
  }

  const coOwners = row.others.map((appid) => ({
    appid,
    name: gamesByAppid.get(appid)?.name || UNKNOWN_APP_NAME,
    cached: row.holderAppids.includes(appid),
  }));

  return { depotid: row.depotid, sizeBytes: row.sizeBytes, tag, coOwners };
}
