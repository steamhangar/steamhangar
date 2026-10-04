/**
 * The receiving side of a browser pairing link `<origin>/#pair=<key>`
 * (WP PAIR-1; the link is built by lib/pair-link.js).
 *
 * Two steps, split on purpose:
 *
 *  1. {@link takePairFromLocation} runs synchronously at app start, before
 *     anything renders: it reads the fragment and removes it with
 *     `history.replaceState`, so the key never stays in the address bar,
 *     the history entry or a bookmark — whether the link is good or not.
 *  2. {@link runPairIntake} decides what to do with the key and does it:
 *     same key as stored → say so; a DIFFERENT key stored → ask first
 *     (it may be another hangar), declining changes nothing; otherwise (or
 *     after a yes) check the key against the server with the same check
 *     onboarding step 1 uses (`checkVaultApiKey`: `/v1/health`, then one
 *     authenticated `GET /v1/settings`) and only a key that passes is
 *     stored, exactly the way onboarding stores it (`setStoredApiKey` +
 *     `setDemoMode(false)`), then the page reloads so every module starts
 *     against it (onboarding's own exit does the same). A key that fails the
 *     check is never stored — fail closed, like onboarding's step-1 gate.
 *
 * The success toast must survive that reload, so it is handed over through
 * a sessionStorage flag ({@link PAIRED_NOTICE_KEY}) that app start reads once
 * ({@link takePairedNotice}). The flag holds "1", never the key.
 *
 * Every collaborator is injected (same DI posture as
 * components/auth-recovery.js), so web/tests/pair-intake.test.js drives the
 * whole flow with fakes: no DOM, no network.
 */

import { readPairFragment, urlWithoutFragment, pairIntakeAction } from "./pair-link.js";

export const PAIRED_NOTICE_KEY = "steamvault.pairedNotice";

export const PAIR_TEXT = Object.freeze({
  checking: "Checking the pairing link…",
  same: "This browser is already connected with this key.",
  kept: "Kept this browser's current key. Nothing was changed.",
  paired: "Paired. This browser is now connected to the hangar.",
  rejected: "The pairing link's key was rejected by this hangar. Nothing was changed.",
  unreachable: "Could not reach the hangar to check the pairing link. Nothing was changed; open the link again when the server is reachable.",
});

/**
 * Read and strip the pairing fragment. Returns what {@link readPairFragment}
 * returned; the fragment is removed in every non-null case.
 *
 * @param {{location: {hash: string, pathname: string, search?: string}, history: {state?: unknown, replaceState: Function}}} win
 * @returns {null | {key: string} | {error: string}}
 */
export function takePairFromLocation(win) {
  const found = readPairFragment(win.location.hash);
  if (found === null) return null;
  // MUTATION TARGET: without this the key stays in the URL bar, the
  // session history and any bookmark made from this page.
  win.history.replaceState(win.history.state ?? null, "", urlWithoutFragment(win.location));
  return found;
}

/**
 * @param {{
 *   candidate: {key: string} | {error: string},
 *   getStoredApiKey: () => string,
 *   isDemoMode: () => boolean,
 *   setStoredApiKey: (key: string) => void,
 *   setDemoMode: (on: boolean) => void,
 *   checkKey: (key: string) => Promise<unknown>,
 *   confirmReplace: () => Promise<boolean>,
 *   notify: (text: string, opts?: {warn?: boolean}) => void,
 *   setPairedNotice: () => void,
 *   reload: () => void,
 *   authKind?: string,
 * }} deps
 * @returns {Promise<"invalid"|"same"|"kept"|"paired"|"rejected"|"unreachable">}
 */
export async function runPairIntake({
  candidate,
  getStoredApiKey,
  isDemoMode,
  setStoredApiKey,
  setDemoMode,
  checkKey,
  confirmReplace,
  notify,
  setPairedNotice,
  reload,
  authKind = "auth",
}) {
  if (!candidate || "error" in candidate) {
    notify(candidate ? candidate.error : "The pairing link is damaged.", { warn: true });
    return "invalid";
  }
  const key = candidate.key;
  const action = pairIntakeAction({ candidate: key, storedKey: getStoredApiKey(), demoMode: isDemoMode() });
  if (action === "same") {
    notify(PAIR_TEXT.same);
    return "same";
  }
  // MUTATION TARGET: a different stored key is never replaced without a yes.
  if (action === "confirm-replace" && !(await confirmReplace())) {
    notify(PAIR_TEXT.kept);
    return "kept";
  }
  notify(PAIR_TEXT.checking);
  try {
    await checkKey(key);
  } catch (err) {
    const rejected = err && err.kind === authKind;
    notify(rejected ? PAIR_TEXT.rejected : PAIR_TEXT.unreachable, { warn: true });
    return rejected ? "rejected" : "unreachable";
  }
  // MUTATION TARGET: only a key that passed the check is stored.
  setStoredApiKey(key);
  setDemoMode(false);
  setPairedNotice();
  reload();
  return "paired";
}

/**
 * Read and clear the "just paired" flag after the reload. Storage that
 * throws (blocked site data) reads as "no notice".
 * @param {() => Storage | null} getSessionStorage
 */
export function takePairedNotice(getSessionStorage) {
  try {
    const s = getSessionStorage();
    if (!s || s.getItem(PAIRED_NOTICE_KEY) !== "1") return false;
    s.removeItem(PAIRED_NOTICE_KEY);
    return true;
  } catch {
    return false;
  }
}

/** Set the flag; a storage failure only loses the toast, never the pairing. */
export function setPairedNotice(getSessionStorage) {
  try {
    const s = getSessionStorage();
    if (s) s.setItem(PAIRED_NOTICE_KEY, "1");
  } catch {
    // best effort: the pairing itself already happened
  }
}
