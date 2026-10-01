/**
 * Pins for `web/js/components/auth-recovery.js` (WP WEB-FIX-1, B2): the one
 * consumer of an AUTH-kind polling-store error. Before it existed a rotated
 * or revoked vault API key left the UI dead — every subscriber drops
 * `{error}` payloads by convention and no code path consumed
 * `ERROR_KINDS.AUTH` outside `checkVaultApiKey`.
 *
 * Driven with a fake store (records subscriptions per resource, lets the
 * test emit a payload) and a recording `openOnboarding` — the same
 * dependency-injected posture `rail-panel-wiring.test.js` established. No
 * `document`, no network. A second section pins the WIRING in `app.js`
 * (comment-stripped source scan): the factory is actually called at top
 * level with the real collaborators, since a factory nobody calls is the
 * WP 4a.1 "documented mechanism with zero callers" class.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createAuthRecovery, AUTH_RECOVERY_NOTICE } from "../js/components/auth-recovery.js";
import { ApiError, ERROR_KINDS } from "../js/errors.js";

function makeFakeStore() {
  const subs = new Map();
  return {
    subscribe(kind, cb) {
      if (!subs.has(kind)) subs.set(kind, new Set());
      subs.get(kind).add(cb);
      return () => subs.get(kind).delete(cb);
    },
    emit(kind, payload) {
      for (const cb of subs.get(kind) || []) cb(payload);
    },
    kinds: () => [...subs.keys()],
  };
}

function harness({ storedKey = "k", onboardingOpen = false } = {}) {
  const store = makeFakeStore();
  const opens = [];
  let open = onboardingOpen;
  const recovery = createAuthRecovery({
    store,
    openOnboarding: (opts) => {
      opens.push(opts);
      open = true;
    },
    isOnboardingOpen: () => open,
    getStoredApiKey: () => storedKey,
  });
  return {
    store,
    opens,
    recovery,
    setOpen: (v) => {
      open = v;
    },
  };
}

const authError = () => new ApiError(ERROR_KINDS.AUTH, "GET /v1/games failed (401)", { status: 401 });
const networkError = () => new ApiError(ERROR_KINDS.NETWORK, "Network request failed");
const serverError = () => new ApiError(ERROR_KINDS.SERVER, "GET /v1/jobs failed (503)", { status: 503 });

test("subscribes to all four store resources — a 401 on ANY loop is enough", () => {
  const { store } = harness();
  assert.deepEqual(store.kinds().sort(), ["cache", "clients", "games", "jobs"]);
});

test("first AUTH-kind error opens onboarding in reconnect mode, once, with the explanatory notice", () => {
  const { store, opens, recovery } = harness();
  assert.equal(recovery.fired(), false);
  store.emit("games", { error: authError() });
  assert.equal(opens.length, 1);
  assert.equal(opens[0].mode, "reconnect");
  assert.equal(opens[0].notice, AUTH_RECOVERY_NOTICE);
  assert.match(opens[0].notice, /rejected/i);
  assert.equal(recovery.fired(), true);
});

test("MUTATION PIN (once-guard): four loops 401ing every cycle must not re-open the dialog", () => {
  const { store, opens, setOpen } = harness();
  store.emit("games", { error: authError() });
  setOpen(false); // the user pressed Escape on the reconnect dialog
  for (let i = 0; i < 3; i++) {
    store.emit("jobs", { error: authError() });
    store.emit("games", { error: authError() });
    store.emit("clients", { error: authError() });
    store.emit("cache", { error: authError() });
  }
  assert.equal(opens.length, 1, "deleting the `fired` guard re-throws the dialog at the user on every tick");
});

test("MUTATION PIN (kind check): NETWORK and SERVER errors — the transient class — never open it", () => {
  const { store, opens, recovery } = harness();
  store.emit("games", { error: networkError() });
  store.emit("jobs", { error: serverError() });
  store.emit("cache", { error: new Error("not even an ApiError") });
  assert.equal(opens.length, 0, "loosening `kind !== AUTH` to any error turns every offline blip into a reconnect dialog");
  assert.equal(recovery.fired(), false);
});

test("successful ticks ({items}/{item} payloads) are ignored", () => {
  const { store, opens } = harness();
  store.emit("games", { items: [], diff: { added: [], updated: [], removed: [] } });
  store.emit("cache", { item: { used_bytes: 1 } });
  store.emit("jobs", null);
  assert.equal(opens.length, 0);
});

test("no stored key (a first run) never opens it — the first-run overlay owns that case", () => {
  const { store, opens, recovery } = harness({ storedKey: "" });
  store.emit("games", { error: authError() });
  assert.equal(opens.length, 0);
  assert.equal(recovery.fired(), false, "must not consume the one-shot on a case it declined");
});

test("while the overlay is already open it neither opens again NOR consumes the one-shot", () => {
  const { store, opens, recovery, setOpen } = harness({ onboardingOpen: true });
  store.emit("games", { error: authError() });
  assert.equal(opens.length, 0, "openOnboarding() would reset a flow the user is already in");
  assert.equal(recovery.fired(), false);
  // The user closes that dialog unresolved; the next 401 still gets its one chance.
  setOpen(false);
  store.emit("clients", { error: authError() });
  assert.equal(opens.length, 1);
  assert.equal(recovery.fired(), true);
});

test("dispose() unsubscribes every resource", () => {
  const { store, opens, recovery } = harness();
  recovery.dispose();
  store.emit("games", { error: authError() });
  assert.equal(opens.length, 0);
});

// ---------------------------------------------------------------------
// Wiring pin: app.js actually calls the factory, at top level, with the
// real collaborators (strong-generation source scan — LEARNINGS AG series:
// a dead never-called helper must not satisfy this).
// ---------------------------------------------------------------------

const here = dirname(fileURLToPath(import.meta.url));
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1");
}
/** Brace depth at `index` in `src`, ignoring braces inside string literals. */
function braceDepthAt(src, index) {
  let depth = 0;
  let quote = null;
  for (let i = 0; i < index; i++) {
    const ch = src[i];
    if (quote) {
      if (ch === "\\") i++;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") quote = ch;
    else if (ch === "{") depth++;
    else if (ch === "}") depth--;
  }
  return depth;
}

test("app.js wires createAuthRecovery at top level with the real store/openOnboarding/isOnboardingOpen/getStoredApiKey", () => {
  const src = stripComments(readFileSync(join(here, "..", "js", "app.js"), "utf8"));
  assert.match(src, /import \{ createAuthRecovery \} from "\.\/components\/auth-recovery\.js"/);
  assert.match(src, /import \{ openOnboarding, isOnboardingOpen \} from "\.\/onboarding\.js"/);
  const callRe = /createAuthRecovery\(\{\s*store,\s*openOnboarding,\s*isOnboardingOpen,\s*getStoredApiKey\s*\}\)/;
  const m = callRe.exec(src);
  assert.ok(m, "app.js must call createAuthRecovery({ store, openOnboarding, isOnboardingOpen, getStoredApiKey }) — the factory exists but nothing wires it (deleted), or its argument list drifted");
  assert.equal(braceDepthAt(src, m.index), 0, "the createAuthRecovery call moved INSIDE a function — it must run unconditionally at module top level, like createRailPanel/createDecisionPanel");
});
