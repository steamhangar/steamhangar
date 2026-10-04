/**
 * web/js/lib/pair-intake.js (WP PAIR-1): a browser opened with
 * `<origin>/#pair=<key>`.
 *
 *  - the fragment is stripped with history.replaceState (path and query
 *    kept) for a good AND a damaged link, and left alone without one;
 *  - no key stored: the key is checked, then stored exactly like onboarding
 *    (setStoredApiKey + setDemoMode(false)), the notice flag set, the page
 *    reloaded — in that order, nothing stored before the check passes;
 *  - same key: nothing stored, no check, no reload;
 *  - a DIFFERENT key stored: the user is asked first; "keep" changes
 *    nothing and sends no request; "replace" checks, then stores;
 *  - a key the server rejects (401) or a server that cannot be reached is
 *    never stored; each says so;
 *  - the "Paired." flag survives exactly one read; blocked storage is not
 *    fatal.
 *
 * Every collaborator is a recording fake; no DOM, no network.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  PAIRED_NOTICE_KEY,
  PAIR_TEXT,
  runPairIntake,
  setPairedNotice,
  takePairFromLocation,
  takePairedNotice,
} from "../js/lib/pair-intake.js";

function fakeWindow(hash, { pathname = "/", search = "" } = {}) {
  const calls = [];
  return {
    calls,
    location: { hash, pathname, search },
    history: {
      state: { view: "library" },
      replaceState(state, title, url) {
        calls.push({ state, title, url });
      },
    },
  };
}

test("MUTATION TARGET: a pairing fragment is stripped at once, keeping path and query", () => {
  const w = fakeWindow("#pair=abc%2B1", { pathname: "/settings", search: "?a=1" });
  assert.deepEqual(takePairFromLocation(w), { key: "abc+1" });
  assert.deepEqual(w.calls, [{ state: { view: "library" }, title: "", url: "/settings?a=1" }]);
});

test("a damaged pairing fragment is stripped too; an unrelated fragment is left alone", () => {
  const bad = fakeWindow("#pair=%E0%A4%A");
  assert.match(takePairFromLocation(bad).error, /damaged/);
  assert.equal(bad.calls.length, 1);
  const other = fakeWindow("#section");
  assert.equal(takePairFromLocation(other), null);
  assert.equal(other.calls.length, 0);
  const none = fakeWindow("");
  assert.equal(takePairFromLocation(none), null);
  assert.equal(none.calls.length, 0);
});

function harness({ stored = "", demo = false, check = async () => ({}), confirm = async () => true } = {}) {
  const log = [];
  let storedKey = stored;
  let demoMode = demo;
  const deps = {
    getStoredApiKey: () => storedKey,
    isDemoMode: () => demoMode,
    setStoredApiKey: (k) => {
      log.push(["store", k]);
      storedKey = k;
    },
    setDemoMode: (on) => {
      log.push(["demo", on]);
      demoMode = on;
    },
    checkKey: async (k) => {
      log.push(["check", k]);
      return check(k);
    },
    confirmReplace: async () => {
      log.push(["confirm"]);
      return confirm();
    },
    notify: (text, opts) => log.push(["notify", text, !!(opts && opts.warn)]),
    setPairedNotice: () => log.push(["flag"]),
    reload: () => log.push(["reload"]),
  };
  return { deps, log, stored: () => storedKey, demo: () => demoMode };
}
const actions = (log) => log.filter((e) => e[0] !== "notify").map((e) => e.join(":"));
const notices = (log) => log.filter((e) => e[0] === "notify").map((e) => e[1]);

test("MUTATION TARGET: no key stored — check, then store like onboarding, flag, reload", async () => {
  const h = harness();
  const outcome = await runPairIntake({ ...h.deps, candidate: { key: "new-key" } });
  assert.equal(outcome, "paired");
  assert.deepEqual(actions(h.log), ["check:new-key", "store:new-key", "demo:false", "flag", "reload"]);
  assert.equal(h.stored(), "new-key");
});

test("demo mode with no key: the same path, and demo mode ends", async () => {
  const h = harness({ demo: true });
  assert.equal(await runPairIntake({ ...h.deps, candidate: { key: "k" } }), "paired");
  assert.equal(h.demo(), false);
});

test("same key: nothing stored, no request, no reload", async () => {
  const h = harness({ stored: "k" });
  assert.equal(await runPairIntake({ ...h.deps, candidate: { key: "k" } }), "same");
  assert.deepEqual(actions(h.log), []);
  assert.deepEqual(notices(h.log), [PAIR_TEXT.same]);
});

test("MUTATION TARGET: different key — asked first; Keep changes nothing and sends no request", async () => {
  const h = harness({ stored: "old", confirm: async () => false });
  assert.equal(await runPairIntake({ ...h.deps, candidate: { key: "new" } }), "kept");
  assert.deepEqual(actions(h.log), ["confirm"]);
  assert.equal(h.stored(), "old");
  assert.deepEqual(notices(h.log), [PAIR_TEXT.kept]);
});

test("different key — Replace checks, then stores", async () => {
  const h = harness({ stored: "old", confirm: async () => true });
  assert.equal(await runPairIntake({ ...h.deps, candidate: { key: "new" } }), "paired");
  assert.deepEqual(actions(h.log), ["confirm", "check:new", "store:new", "demo:false", "flag", "reload"]);
});

test("MUTATION TARGET: a rejected key (401) is never stored", async () => {
  const h = harness({
    stored: "old",
    check: async () => {
      throw Object.assign(new Error("That API key was rejected."), { kind: "auth", status: 401 });
    },
  });
  assert.equal(await runPairIntake({ ...h.deps, candidate: { key: "new" } }), "rejected");
  assert.deepEqual(actions(h.log), ["confirm", "check:new"]);
  assert.equal(h.stored(), "old");
  assert.deepEqual(h.log.at(-1), ["notify", PAIR_TEXT.rejected, true]);
});

test("an unreachable server stores nothing and says so", async () => {
  const h = harness({
    check: async () => {
      throw Object.assign(new Error("Could not reach the server."), { kind: "network" });
    },
  });
  assert.equal(await runPairIntake({ ...h.deps, candidate: { key: "k" } }), "unreachable");
  assert.deepEqual(actions(h.log), ["check:k"]);
  assert.equal(h.stored(), "");
  assert.deepEqual(h.log.at(-1), ["notify", PAIR_TEXT.unreachable, true]);
});

test("a damaged link only shows its error", async () => {
  const h = harness();
  assert.equal(await runPairIntake({ ...h.deps, candidate: { error: "The pairing link is damaged (x)." } }), "invalid");
  assert.deepEqual(actions(h.log), []);
  assert.deepEqual(h.log, [["notify", "The pairing link is damaged (x).", true]]);
});

test("the 'Paired.' flag is read once; blocked storage is not fatal", () => {
  const map = new Map();
  const storage = {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
  assert.equal(takePairedNotice(() => storage), false);
  setPairedNotice(() => storage);
  assert.equal(map.get(PAIRED_NOTICE_KEY), "1", "a flag, never the key");
  assert.equal(takePairedNotice(() => storage), true);
  assert.equal(takePairedNotice(() => storage), false);
  const throwing = () => {
    throw new Error("SecurityError");
  };
  assert.equal(takePairedNotice(throwing), false);
  assert.doesNotThrow(() => setPairedNotice(throwing));
});

// ---- app.js wiring (comment-stripped source scan, the auth-recovery.test.js
// idiom: app.js itself cannot be imported headlessly) ----------------------

const here = dirname(fileURLToPath(import.meta.url));
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");
}
const appSource = () => stripComments(readFileSync(join(here, "..", "js", "app.js"), "utf8"));

test("MUTATION TARGET: app.js strips the fragment at module top level, before the first render", () => {
  const src = appSource();
  assert.match(src, /\nconst pairCandidate = takePairFromLocation\(window\);\n/, "top level, unindented");
  assert.ok(src.indexOf("takePairFromLocation(window)") < src.indexOf("renderView(currentView());"), "before the first paint");
});

test("MUTATION TARGET: app.js runs the intake with onboarding's own store/check functions and gates the first-run overlay", () => {
  const src = appSource();
  const call = /runPairIntake\(\{([\s\S]*?)\}\)\.then\(/.exec(src);
  assert.ok(call, "runPairIntake({...}).then(...) is called");
  const args = call[1];
  for (const piece of [
    "candidate: pairCandidate,",
    "getStoredApiKey,",
    "isDemoMode,",
    "setStoredApiKey,",
    "setDemoMode,",
    "checkKey: checkVaultApiKey,",
    "confirmReplace: confirmPairReplace,",
    "setPairedNotice: () => setPairedNotice(getSessionStorage),",
    "reload: () => window.location.reload(),",
  ]) {
    assert.ok(args.includes(piece), `argument: ${piece}`);
  }
  assert.match(src, /if \(pairCandidate\) \{\s*runPairIntake/);
  assert.match(src, /\} else \{\s*maybeShowOnboardingOnStartup\(\);\s*\}/, "first run waits while a link is handled");
  assert.match(src, /if \(outcome === "paired"\) return;\s*pairIntakeBusy = false;\s*maybeShowOnboardingOnStartup\(\);/);
  assert.match(src, /if \(takePairedNotice\(getSessionStorage\)\) showToast\(PAIR_TEXT\.paired/);
});
