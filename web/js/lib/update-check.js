/**
 * "Check updates" — the web side of the always-on update check (WP UPD-2,
 * docs/handover/post-0.1.0-roadmap.md D14; server side WP UPD-1, api/README.md
 * "POST /v1/updates/check" / "GET /v1/updates/status").
 *
 * **Not "Check & update".** `POST /v1/updates/check` only refreshes the
 * manifest oracle for every cached game; it never starts a download. That is
 * what makes a bare "Check" label honest here, unlike the Phase 4c "Check &
 * update all cached games" button (`cached-prefill-outcome.js`), which stays
 * untouched and keeps its "& update" wording because it CAN download. The
 * 2026-08-18 "never bare Check" rule is superseded for this one control by
 * the D14 user decision (ADR addendum in WP UPD-4).
 *
 * Flow: POST (answers at once: `started` / `already_running` / `disabled`;
 * a 503 while vault-api shuts down is a transient error, nothing started),
 * then poll `GET /v1/updates/status` every `UPDATE_POLL_INTERVAL_MS` until
 * `running` is false, then the caller re-reads `GET /v1/games`. Polling is
 * bounded (`UPDATE_POLL_MAX_TICKS`) and stops the moment the Library view
 * is left (`detach()`); re-entering the view (`attach()`) re-reads the
 * status once and resumes polling if a check is still running — including a
 * scheduled one this browser did not start.
 *
 * **Older vault-api (no UPD-1).** Both routes 404 there. A 404 on either
 * marks the feature `unsupported`; the view hides the button and the status
 * line. The "Updates" chip is gated separately and data-driven
 * (`hasUpdateField`): it shows only when `GET /v1/games` rows actually carry
 * `update_available`.
 *
 * Pure decision logic and a timer-injected runner, no DOM — unit-tested in
 * web/tests/update-check.test.js; `views/library.js` is the DOM glue.
 */

import { ERROR_KINDS } from "../errors.js";
import { formatAgo } from "./format.js";

export const CHECK_UPDATES_LABEL = "Check updates";
export const CHECK_UPDATES_BUSY_LABEL = "Checking for updates…";

/** 2 s between status reads: a check is one oracle request per cached
 * game, so it takes seconds to minutes; faster polling buys nothing. */
export const UPDATE_POLL_INTERVAL_MS = 2000;
/** 150 x 2 s = 5 minutes, then the view stops asking (the check itself
 * keeps running on the vault; the next view open picks the result up). */
export const UPDATE_POLL_MAX_TICKS = 150;

export const UPDATE_PHASE = Object.freeze({
  UNKNOWN: "unknown", // status not read yet (button hidden until known)
  UNSUPPORTED: "unsupported", // vault-api without the routes (404)
  DISABLED: "disabled", // oracle off (enabled: false)
  IDLE: "idle",
  RUNNING: "running",
});

/** Does this `GET /v1/games` list come from a vault-api that reports
 * update state at all? Gates the "Updates" chip (older vault-api: hidden). */
export function hasUpdateField(games) {
  return Array.isArray(games) && games.some((g) => g && typeof g.update_available === "boolean");
}

export function countUpdates(games) {
  return Array.isArray(games) ? games.filter((g) => g && g.update_available === true).length : 0;
}

/** True when a `GET /v1/games` tick flipped any row's `update_available`.
 * The flag is not part of the card's structural key (`dispKind`), so the
 * patch path would neither move the card in/out of the "Updates" filter nor
 * refresh the chip counts — the caller falls back to a full render. */
export function updateFlagsChanged(diff) {
  if (!diff || !Array.isArray(diff.updated)) return false;
  return diff.updated.some(
    ({ prev, curr }) => (prev?.update_available === true) !== (curr?.update_available === true),
  );
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/**
 * Toast text for a finished check, from `GET /v1/updates/status`.
 * @param {object | null | undefined} status
 * @returns {{message: string, warn: boolean}}
 */
export function describeUpdateOutcome(status) {
  const outcome = status && status.last_outcome;
  if (outcome === "completed") {
    const apps = Number.isInteger(status.last_apps) ? status.last_apps : null;
    const stale = Number.isInteger(status.last_stale) ? status.last_stale : 0;
    const failed = Number.isInteger(status.last_failed) ? status.last_failed : 0;
    if (apps === 0) return { message: "No cached games to check.", warn: false };
    const parts = [];
    if (apps !== null) parts.push(`Checked ${plural(apps, "game")}`);
    if (stale > 0) {
      parts.push(`${plural(stale, "update")} available`);
    } else {
      // "All up to date" only when every game answered — a failed one is
      // unknown, not current.
      parts.push(failed > 0 ? "no updates found" : "all up to date");
    }
    if (failed > 0) parts.push(`${failed} could not be checked`);
    const message = parts.join(" · ");
    return { message: message.charAt(0).toUpperCase() + message.slice(1), warn: failed > 0 };
  }
  if (outcome === "bailed_out") {
    return {
      message: "Update check stopped early: the manifest oracle did not answer. Try again later.",
      warn: true,
    };
  }
  if (outcome === "error") {
    return { message: "Update check failed on the vault — see the vault-api log.", warn: true };
  }
  if (outcome === "aborted") {
    return { message: "Update check stopped: the vault is shutting down.", warn: true };
  }
  if (outcome === "interrupted") {
    return { message: "Update check was interrupted: vault-api restarted during it.", warn: true };
  }
  return { message: "Update check finished.", warn: false };
}

export const UPDATE_DISABLED_TEXT =
  "Update check is off: the manifest oracle is disabled on this vault (VAULT_MANIFEST_ORACLE).";
export const UPDATE_GAVE_UP_TEXT =
  "The update check is still running on the vault. Its result shows here once it finishes.";

/**
 * The small status line under the check buttons. Empty string = hide it.
 * @param {string} phase one of UPDATE_PHASE
 * @param {object | null} status last `GET /v1/updates/status` body
 * @param {number} [nowMs]
 */
export function updateStatusLine(phase, status, nowMs = Date.now()) {
  if (phase === UPDATE_PHASE.UNKNOWN || phase === UPDATE_PHASE.UNSUPPORTED) return "";
  if (phase === UPDATE_PHASE.DISABLED) return UPDATE_DISABLED_TEXT;
  if (phase === UPDATE_PHASE.RUNNING) return "Checking cached games for updates…";
  if (!status) return "";
  // `scheduled` (UPD-1 final contract): oracle on AND a schedule window set,
  // i.e. the vault also checks by itself. Shown only when the field exists.
  const mode =
    status.scheduled === true ? " · automatic checks on" : status.scheduled === false ? " · manual checks only" : "";
  const finished = formatAgo(status.last_finished_at, nowMs);
  if (finished) return `Updates last checked ${finished}${mode}`;
  const started = formatAgo(status.last_started_at, nowMs);
  if (started) return `Last update check started ${started} and did not finish${mode}`;
  return `Updates not checked yet${mode}`;
}

function isNotFound(err) {
  return !!err && err.kind === ERROR_KINDS.NOT_FOUND;
}

function errorMessage(err) {
  // 503 while vault-api shuts down (UPD-1 final contract), or any other
  // 5xx: transient, nothing was started.
  if (err && err.kind === ERROR_KINDS.SERVER) {
    return "The vault could not start the update check right now (it may be restarting). Try again shortly.";
  }
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Could not start the update check.";
}

/**
 * The runner: one check at a time, bounded polling, detach-safe.
 *
 * @param {{
 *   startCheck: () => Promise<object>,   // POST /v1/updates/check
 *   fetchStatus: () => Promise<object>,  // GET /v1/updates/status
 *   onChange?: (snap: {phase: string, status: object|null}) => void,
 *   onDone?: (result: {kind: "finished", status: object, announce: boolean} | {kind: "gave_up", announce: boolean}) => void,
 *   setTimer?: (fn: () => void, ms: number) => unknown,
 *   clearTimer?: (id: unknown) => void,
 *   intervalMs?: number,
 *   maxTicks?: number,
 * }} deps
 */
export function createUpdateCheckRunner({
  startCheck,
  fetchStatus,
  onChange = () => {},
  onDone = () => {},
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (id) => clearTimeout(id),
  intervalMs = UPDATE_POLL_INTERVAL_MS,
  maxTicks = UPDATE_POLL_MAX_TICKS,
}) {
  let phase = UPDATE_PHASE.UNKNOWN;
  let status = null;
  let active = false; // the Library view is mounted
  let starting = false; // a POST is in flight
  let polling = false;
  let timer = null;
  let ticks = 0;
  let gen = 0; // generation token: a stale tick after detach() does nothing
  // Set when THIS browser pressed the button: only then is the finished
  // result toasted. A scheduled run seen on view open just updates the line.
  let announce = false;

  const snapshot = () => ({ phase, status });
  const emit = () => onChange(snapshot());

  function phaseFromStatus(s) {
    if (!s || s.enabled === false) return UPDATE_PHASE.DISABLED;
    return s.running ? UPDATE_PHASE.RUNNING : UPDATE_PHASE.IDLE;
  }

  function stopPolling() {
    gen += 1;
    polling = false;
    if (timer !== null) clearTimer(timer);
    timer = null;
  }

  function startPolling() {
    if (polling || !active) return;
    polling = true;
    ticks = 0;
    const my = ++gen;
    schedule(my);
  }

  function schedule(my) {
    timer = setTimer(() => tick(my), intervalMs);
  }

  async function tick(my) {
    if (my !== gen) return;
    timer = null;
    ticks += 1;
    let s = null;
    let err = null;
    try {
      s = await fetchStatus();
    } catch (e) {
      err = e;
    }
    if (my !== gen) return; // detached (or restarted) while the read was out
    if (isNotFound(err)) {
      stopPolling();
      phase = UPDATE_PHASE.UNSUPPORTED;
      announce = false;
      emit();
      return;
    }
    if (s && !s.running) {
      stopPolling();
      status = s;
      phase = phaseFromStatus(s);
      const wasAnnounced = announce;
      announce = false;
      emit();
      onDone({ kind: "finished", status: s, announce: wasAnnounced });
      return;
    }
    if (s) status = s; // still running; a transient read error keeps the last one
    if (ticks >= maxTicks) {
      stopPolling();
      // The check may still be running on the vault; this view just stops
      // asking. IDLE re-enables the button: a second press answers
      // `already_running` and polls again, it cannot start a second run.
      phase = UPDATE_PHASE.IDLE;
      const wasAnnounced = announce;
      announce = false;
      emit();
      onDone({ kind: "gave_up", announce: wasAnnounced });
      return;
    }
    schedule(my);
  }

  return {
    snapshot,
    isBusy: () => starting || phase === UPDATE_PHASE.RUNNING,

    /** Library view mounted: read the status once (support probe + "last
     * checked"), resume polling if a check is running. */
    async attach() {
      active = true;
      const my = gen;
      let s;
      try {
        s = await fetchStatus();
      } catch (err) {
        if (!active || my !== gen) return;
        if (isNotFound(err)) {
          phase = UPDATE_PHASE.UNSUPPORTED;
          announce = false;
        } else if (phase === UPDATE_PHASE.UNKNOWN) {
          // Unreachable for now (network, 5xx): offer the button anyway, a
          // press reports the real reason.
          phase = UPDATE_PHASE.IDLE;
        } else if (phase === UPDATE_PHASE.RUNNING) {
          // Left and came back mid-run, and this read failed: keep polling
          // (bounded), or the button would stay busy forever.
          emit();
          startPolling();
          return;
        }
        emit();
        return;
      }
      if (!active || my !== gen) return;
      status = s;
      phase = phaseFromStatus(s);
      // The run finished while the view was closed: nobody is waiting for
      // its toast any more, and a LATER run (e.g. scheduled) must not
      // inherit this press's announce flag.
      if (phase !== UPDATE_PHASE.RUNNING) announce = false;
      emit();
      if (phase === UPDATE_PHASE.RUNNING) startPolling();
    },

    /** Library view left: stop polling now. State is kept for the next
     * attach(). */
    detach() {
      active = false;
      stopPolling();
    },

    /**
     * The button press.
     * @returns {Promise<{skipped: true} | {result: "started"|"already_running"|"disabled"|"unsupported"} | {result: "error", message: string}>}
     */
    async run() {
      if (starting || phase === UPDATE_PHASE.RUNNING) return { skipped: true };
      starting = true;
      emit();
      let body;
      try {
        body = await startCheck();
      } catch (err) {
        starting = false;
        if (isNotFound(err)) {
          phase = UPDATE_PHASE.UNSUPPORTED;
          emit();
          return { result: "unsupported" };
        }
        emit();
        return { result: "error", message: errorMessage(err) };
      }
      starting = false;
      if (!body || body.enabled === false || body.status === "disabled") {
        phase = UPDATE_PHASE.DISABLED;
        if (status) status = { ...status, enabled: false };
        emit();
        return { result: "disabled" };
      }
      const result = body.status === "already_running" ? "already_running" : "started";
      phase = UPDATE_PHASE.RUNNING;
      announce = true;
      emit();
      startPolling();
      return { result };
    },
  };
}
