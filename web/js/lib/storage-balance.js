/**
 * Storage balance presentation (WP STORAGE-BALANCE-1).
 *
 * Pure: no DOM, no fetch. `views/settings.js` renders the result as the
 * "Storage balance" card of the Expert settings panel, from the store's
 * "cache" snapshot (the raw `GET /v1/cache/summary` body).
 *
 * The figures come from `storage_balance` in that body (api/README.md,
 * "Storage balance"). An older server does not send it: `storageBalanceView`
 * then returns `null` and the card stays hidden — never zeros.
 */

import { formatBytesGBOrZero } from "./format.js";

/** Card title. */
export const STORAGE_BALANCE_TITLE = "Storage balance";

/** The line under the tiles. */
export const STORAGE_BALANCE_NOTE =
  "From the last size count and the last update check. Older builds is an estimate: garbage collection reclaims them but also keeps beta branches.";

/** The line under the tiles while the residual is 0 (no "Unexplained" tile
 * then: five tiles fit one row on a wide screen). */
export const STORAGE_BALANCE_ACCOUNTED = "Everything on disk is accounted for (unexplained: 0).";

/** Warning text when the server flags the balance without a reason. */
export const STORAGE_BALANCE_WARNING_FALLBACK =
  "A large part of the cache is not used by the current builds.";

const NUMBER_FIELDS = [
  "on_disk_bytes",
  "planned_bytes",
  "present_bytes",
  "measured_depots",
  "old_builds_bytes",
  "unmeasured_bytes",
  "unmeasured_depots",
  "unmapped_bytes",
  "unmapped_depots",
  "unexplained_bytes",
];

function depots(n) {
  return n === 1 ? "1 depot" : `${n} depots`;
}

/** Signed size: the formatter refuses negatives (never a byte count), the
 * balance's residual can be one. */
function signedSize(bytes) {
  if (bytes < 0) return `−${formatBytesGBOrZero(-bytes)}`;
  return formatBytesGBOrZero(bytes);
}

/**
 * The card's content, or `null` when there is nothing honest to show (no
 * summary yet, an older server without `storage_balance`, or a malformed
 * object).
 *
 * @param {object | null | undefined} summary the raw summary body
 * The "Unexplained" tile is only there when the residual is not 0;
 * `accounted` is true otherwise (the view prints STORAGE_BALANCE_ACCOUNTED).
 *
 * @returns {{tiles: Array<{key: string, label: string, value: string, sub: string | null, note: string, hi: boolean}>, warning: string | null, accounted: boolean} | null}
 */
export function storageBalanceView(summary) {
  const b = summary && typeof summary === "object" ? summary.storage_balance : null;
  if (!b || typeof b !== "object") return null;
  for (const f of NUMBER_FIELDS) {
    if (typeof b[f] !== "number" || !Number.isFinite(b[f])) return null;
  }
  for (const f of NUMBER_FIELDS) {
    if (f !== "unexplained_bytes" && b[f] < 0) return null;
  }
  const warning = b.warning === true
    ? (typeof b.warning_reason === "string" && b.warning_reason.trim() ? b.warning_reason.trim() : STORAGE_BALANCE_WARNING_FALLBACK)
    : null;
  const size = (v) => formatBytesGBOrZero(v);
  const tiles = [
    {
      key: "on_disk",
      label: "On disk",
      value: size(b.on_disk_bytes),
      sub: null,
      note: "Everything in the cache folder.",
      hi: false,
    },
    {
      key: "current",
      label: "Current builds",
      value: size(b.present_bytes),
      sub: null,
      note: `On disk, of the ${size(b.planned_bytes)} the current builds of the measured games need (${depots(b.measured_depots)}).`,
      hi: false,
    },
    {
      key: "old_builds",
      label: "Older builds",
      value: size(b.old_builds_bytes),
      sub: null,
      note: "In measured depots but not part of the current build as last checked: older builds, damaged chunks, and new downloads since the last check.",
      hi: warning !== null,
    },
    {
      key: "unmeasured",
      label: "Not measured",
      value: size(b.unmeasured_bytes),
      sub: depots(b.unmeasured_depots),
      note: "Games, DLCs and tool packages without a completeness check.",
      hi: false,
    },
    {
      key: "unmapped",
      label: "No game",
      value: size(b.unmapped_bytes),
      sub: depots(b.unmapped_depots),
      note: "Depot folders no game is mapped to.",
      hi: false,
    },
  ];
  const accounted = b.unexplained_bytes === 0;
  if (!accounted) {
    tiles.push({
      key: "unexplained",
      label: "Unexplained",
      value: signedSize(b.unexplained_bytes),
      sub: null,
      note: "Should be 0. The size count and the last check disagree (files changed in between, or a depot measured against two builds).",
      hi: warning !== null,
    });
  }
  return { tiles, warning, accounted };
}
