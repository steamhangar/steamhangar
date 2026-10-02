/**
 * Pure pins for WP WEB-FIX-4:
 *   - `lib/job-failure.js`: the failure reason comes from vault-api's own
 *     `[vault-api] Prefill failed (reason=...)` line, never from
 *     SteamPrefill's wording, and only for a failed prefill job;
 *   - the one exception: SteamPrefill's public-IP cache-detection error,
 *     matched narrowly and only on top of `reason=exit_code`;
 *   - Retry on the newest prefill job per app only;
 *   - drift guards: the login command and the public-IP README heading
 *     must exist in deploy/README.md;
 *   - `lib/owned-library.js`'s title helpers: vault name, then owned name,
 *     then "App <id>"; `fillMissingNames` fills names only.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  jobFailureReason,
  jobFailureHint,
  isNewestJobForApp,
  FAILURE_REASON,
  FAILURE_HINT,
  HINTS,
  LOGIN_COMMAND,
  LOGIN_HINT,
  PUBLIC_IP_README_SECTION,
} from "../js/lib/job-failure.js";
import { appTitle, fillMissingNames, mergeOwnedLibrary, ownedNamesByAppid } from "../js/lib/owned-library.js";

const failed = { id: 7, appid: 20, type: "prefill", status: "error" };

// Modeled on the real excerpt shape (api/vault_api/prefill.py + worker.py):
// SteamPrefill's output, vault-api's hint, then the reason line.
const NOT_LOGGED_IN_EXCERPT = [
  "[...truncated...]",
  "   at SteamPrefill.Handlers.Steam.Steam3Session.LoginAsync() in /src/Steam3Session.cs:line 101",
  "Unhandled exception. System.InvalidOperationException: Failed to read input in non-interactive mode.",
  "[vault-api] SteamPrefill has no usable Steam session, and vault-api runs it non-interactively ...",
  "[vault-api] Prefill failed (reason=not_logged_in); the depot mapping for this app was left unchanged.",
].join("\n");

test("not_logged_in is read from vault-api's reason line", () => {
  assert.equal(jobFailureReason(failed, NOT_LOGGED_IN_EXCERPT), FAILURE_REASON.NOT_LOGGED_IN);
  assert.equal(
    jobFailureReason(failed, "boom\n[vault-api] Prefill failed (reason=exit_code); the depot mapping for this app was left unchanged."),
    "exit_code",
  );
});

test("MUTATION PIN (no text sniffing): SteamPrefill's login prompt text alone is NOT a reason", () => {
  const promptOnly = "A Steam account is required\nPlease enter your Steam account name\n[vault-api] SteamPrefill exited with code 1.";
  assert.equal(jobFailureReason(failed, promptOnly), null);
});

test("MUTATION PIN (last line only): a reason line that is not the last non-empty line is not the verdict", () => {
  const notLast = "[vault-api] Prefill failed (reason=not_logged_in); x\nsomething SteamPrefill printed later";
  assert.equal(jobFailureReason(failed, notLast), null);
  assert.equal(jobFailureReason(failed, NOT_LOGGED_IN_EXCERPT + "\n\n  \n"), "not_logged_in", "trailing blank lines are skipped");
  assert.equal(jobFailureReason(failed, NOT_LOGGED_IN_EXCERPT.replace(/\n/g, "\r\n")), "not_logged_in", "CRLF output");
});

test("only a failed prefill job has a reason; a reason line mid-line does not count; the last line wins", () => {
  assert.equal(jobFailureReason({ ...failed, status: "done" }, NOT_LOGGED_IN_EXCERPT), null);
  assert.equal(jobFailureReason({ ...failed, status: "cancelled" }, NOT_LOGGED_IN_EXCERPT), null);
  assert.equal(jobFailureReason({ ...failed, type: "gc" }, NOT_LOGGED_IN_EXCERPT), null);
  assert.equal(jobFailureReason(failed, null), null);
  assert.equal(jobFailureReason(failed, undefined), null);
  assert.equal(jobFailureReason(failed, "echo [vault-api] Prefill failed (reason=not_logged_in)"), null);
  const two = "[vault-api] Prefill failed (reason=timeout); x\n[vault-api] Prefill failed (reason=not_logged_in); y";
  assert.equal(jobFailureReason(failed, two), "not_logged_in");
});

test("the login hint names the queue-mode command from deploy/README.md and says what to do next", () => {
  assert.equal(LOGIN_COMMAND, "docker compose exec -it vault-runner /opt/steamprefill/SteamPrefill select-apps");
  assert.match(LOGIN_HINT.body, /one-time interactive login on the server/);
  assert.match(LOGIN_HINT.body, /never sees or stores your Steam credentials/);
  assert.equal(LOGIN_HINT.code, LOGIN_COMMAND);
  assert.match(LOGIN_HINT.codeIntro, /In the folder with SteamHangar's compose\.yaml \(deploy\/ by default\)/);
  assert.match(LOGIN_HINT.after, /subprocess/);
  assert.match(LOGIN_HINT.after, /deploy\/README\.md/);
  assert.equal(LOGIN_HINT.retry, "Then press Retry.");
});

// Real SteamPrefill wording from the user's report.
const PUBLIC_IP_OUTPUT = [
  " Warning!  lancache.steamcontent.com is resolving to a public IP address",
  "(162.254.197.25).",
  "LancacheNotFoundException: Lancache server is resolving to a public IP : 162.254.197.25",
].join("\n");
const EXIT_CODE_LINE = "[vault-api] Prefill failed (reason=exit_code); the depot mapping for this app was left unchanged.";

test("public IP: detected on reason=exit_code with the exact phrase 'is resolving to a public IP'", () => {
  assert.equal(jobFailureHint(failed, `${PUBLIC_IP_OUTPUT}\n${EXIT_CODE_LINE}`), FAILURE_HINT.PUBLIC_IP);
  assert.equal(jobFailureHint(failed, `host is resolving to a public IP address\n${EXIT_CODE_LINE}`), FAILURE_HINT.PUBLIC_IP);
  assert.equal(jobFailureHint(failed, NOT_LOGGED_IN_EXCERPT), FAILURE_HINT.NOT_LOGGED_IN);
});

test("MUTATION PIN (narrow + exit_code only): other exit_code output, other reasons, near-miss wording -> no public-IP hint", () => {
  assert.equal(jobFailureHint(failed, `Depot 301 failed: disk full\n${EXIT_CODE_LINE}`), null);
  // SteamPrefill's OTHER detection failure (poc/steamprefill/PROTOCOL.md):
  // the heartbeat failed for any reason. Same exception type, different
  // cause, so the exception name alone must not trigger the public-IP hint.
  assert.equal(
    jobFailureHint(failed, `LancacheNotFoundException: Unable to detect Lancache server!\n${EXIT_CODE_LINE}`),
    null,
    "'Unable to detect Lancache server!' is not a public-IP failure",
  );
  assert.equal(jobFailureHint(failed, `lancache.steamcontent.com resolved fine\npublic IP 1.2.3.4\n${EXIT_CODE_LINE}`), null);
  assert.equal(
    jobFailureHint(failed, `${PUBLIC_IP_OUTPUT}\n[vault-api] Prefill failed (reason=timeout); x`),
    null,
    "the text alone is not enough: vault-api must have recorded exit_code",
  );
  assert.equal(jobFailureHint(failed, PUBLIC_IP_OUTPUT), null, "no vault-api line at all");
  assert.equal(jobFailureHint({ ...failed, status: "done" }, `${PUBLIC_IP_OUTPUT}\n${EXIT_CODE_LINE}`), null);
});

test("public-IP hint text names the container, both fixes and the README section", () => {
  const h = HINTS[FAILURE_HINT.PUBLIC_IP];
  assert.match(h.body, /lancache\.steamcontent\.com resolves to a public address/);
  assert.match(h.body, /vault-runner in the default queue mode/);
  assert.match(h.codeIntro, /extra_hosts on vault-runner to vault-core's private IPv4 address \(a plain IP, not a hostname;/);
  assert.match(h.body, /vault-api instead with VAULT_PREFILL_MODE=subprocess/);
  assert.match(h.codeIntro, /vault-api instead with VAULT_PREFILL_MODE=subprocess/);
  assert.match(h.codeIntro, /then re-run docker compose -f compose\.yaml -f compose\.override\.yaml up -d:/);
  assert.match(h.after, /DNS rewrite/);
  assert.equal(h.after.includes(`deploy/README.md, \u201c${PUBLIC_IP_README_SECTION}\u201d`), true);
  assert.equal(h.retry, "Then press Retry.");
});

test("MUTATION PIN (newest only): Retry belongs to the newest PREFILL job of the app; a later GC job does not count", () => {
  const jobs = [
    { id: 1, appid: 20, type: "prefill" },
    { id: 2, appid: 30, type: "prefill" },
    { id: 3, appid: 20, type: "prefill" },
    { id: 4, appid: 30, type: "gc" },
  ];
  assert.equal(isNewestJobForApp(jobs[0], jobs), false, "job 3 is newer for app 20");
  assert.equal(isNewestJobForApp(jobs[2], jobs), true);
  assert.equal(isNewestJobForApp(jobs[1], jobs), true, "a later GC job is not a newer prefill");
});

// ---------------------------------------------------------------------
// Drift guards against deploy/README.md
// ---------------------------------------------------------------------
const readme = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "deploy", "README.md"), "utf8");

test("DRIFT GUARD: LOGIN_COMMAND is the command deploy/README.md documents (backslash continuations joined)", () => {
  const joined = readme.replace(/\\\r?\n\s*/g, " ").replace(/[ \t]+/g, " ");
  assert.equal(joined.includes(LOGIN_COMMAND), true, `deploy/README.md no longer contains: ${LOGIN_COMMAND}`);
});

test("DRIFT GUARD: the public-IP hint's README section heading exists (backticks dropped)", () => {
  const headings = readme
    .split("\n")
    .filter((l) => /^#{1,6} /.test(l))
    .map((l) => l.replace(/^#{1,6} /, "").replace(/`/g, "").trim());
  assert.equal(headings.includes(PUBLIC_IP_README_SECTION), true, `no heading "${PUBLIC_IP_README_SECTION}" in deploy/README.md`);
});

test("appTitle order: vault name, then owned name, then 'App <id>'; blank counts as missing", () => {
  assert.equal(appTitle(5, "Vault Five", "Steam Five"), "Vault Five");
  assert.equal(appTitle(5, null, "Steam Five"), "Steam Five");
  assert.equal(appTitle(5, "   ", "Steam Five"), "Steam Five");
  assert.equal(appTitle(5, undefined, undefined), "App 5");
  assert.equal(appTitle(5, "", " "), "App 5");
});

test("fillMissingNames fills a missing vault name only, adds no rows, and never mutates its input", () => {
  const vault = [
    { appid: 1, name: "Vault One" },
    { appid: 2, name: null, status: "error" },
  ];
  const owned = [
    { appid: 1, name: "Steam One" },
    { appid: 2, name: "Steam Two" },
    { appid: 3, name: "Steam Three" },
  ];
  const out = fillMissingNames(vault, owned);
  assert.deepEqual(out.map((g) => [g.appid, g.name]), [[1, "Vault One"], [2, "Steam Two"]]);
  assert.equal(out[1].status, "error");
  assert.equal(vault[1].name, null, "input untouched");
  assert.equal(fillMissingNames(vault, null), vault, "no owned list: the same array back");
  assert.deepEqual([...ownedNamesByAppid(owned).keys()], [1, 2, 3]);
});

test("mergeOwnedLibrary (refactored onto fillMissingNames) still adds owned-only rows and does not mutate the vault array", () => {
  const vault = [{ appid: 1, name: "Vault One" }];
  const merged = mergeOwnedLibrary(vault, [{ appid: 1, name: "x" }, { appid: 9, name: "Nine" }]);
  assert.deepEqual(merged.map((g) => g.appid), [1, 9]);
  assert.equal(vault.length, 1, "the vault array is not pushed into");
  // The owned entries carry no names: fillMissingNames hands back its INPUT
  // array, which the merge must copy before pushing owned-only rows.
  const vault2 = [{ appid: 1, name: "Vault One" }];
  const merged2 = mergeOwnedLibrary(vault2, [{ appid: 9 }]);
  assert.deepEqual(merged2.map((g) => g.appid), [1, 9]);
  assert.equal(vault2.length, 1, "nameless owned list: the vault array is still not pushed into");
});
