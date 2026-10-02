/**
 * Why a prefill job failed, and the hint blocks for two known causes (WP
 * WEB-FIX-4).
 *
 * How the reason reaches the web: the API has no structured field for it.
 * `PrefillResult.failure_reason` (api/vault_api/prefill.py) is an enum
 * (`not_logged_in`, `timeout`, `aborted`, `exit_code`, `setup`, ...), but
 * `JobSummary`/`JobDetail` (api/vault_api/routers/jobs.py) do not expose
 * it, and the API is frozen (ADR-0016). What vault-api DOES write is its own
 * machine-formatted line as the LAST line of every failed prefill's log
 * (api/vault_api/worker.py, the failure branch, same in subprocess and
 * queue mode):
 *
 *   [vault-api] Prefill failed (reason=not_logged_in); the depot mapping ...
 *
 * That line carries the enum value verbatim, so the reason is read from it
 * instead of SteamPrefill's own wording, which a SteamPrefill release can
 * change at any time. Only the last non-empty line counts: a reason-shaped
 * line anywhere else (echoed output, an older run quoted in the log) is not
 * vault-api's verdict for this job. The 4 KiB tail cap
 * (`jobs.tail_excerpt`) keeps that line. Only a `prefill` job with
 * `status: "error"` is read: a done or cancelled job never had a failure
 * reason.
 *
 * Pure: no DOM, no fetch. Covered in web/tests/job-failure.test.js.
 */

export const FAILURE_REASON = Object.freeze({
  NOT_LOGGED_IN: "not_logged_in",
  EXIT_CODE: "exit_code",
});

const REASON_LINE = /^\[vault-api\] Prefill failed \(reason=([a-z_]+)\)/;

/**
 * The failure reason vault-api recorded for this job, or null.
 * @param {{type?: string, status?: string} | null | undefined} job
 * @param {string | null | undefined} excerpt `GET /v1/jobs/{id}`'s `log_excerpt`
 * @returns {string | null}
 */
export function jobFailureReason(job, excerpt) {
  if (!job || job.type !== "prefill" || job.status !== "error") return null;
  if (typeof excerpt !== "string") return null;
  const lines = excerpt.split("\n").map((l) => l.replace(/\r$/, ""));
  const last = lines.reverse().find((l) => l.trim() !== "");
  const m = last ? REASON_LINE.exec(last) : null;
  return m ? m[1] : null;
}

/**
 * The one exception to "read only vault-api's line": SteamPrefill's own
 * cache-detection failure. vault-api records it as a plain `exit_code`
 * failure; nothing vault-api writes says WHY. The only signal is
 * SteamPrefill's text, e.g.
 *
 *    Warning!  lancache.steamcontent.com is resolving to a public IP address
 *   LancacheNotFoundException: Lancache server is resolving to a public IP : 162.254.197.25
 *
 * Kept safe by being narrow and cosmetic: it only applies on top of
 * vault-api's `reason=exit_code`, it matches only that exact phrase, and a
 * miss changes nothing but whether a hint is
 * shown (the raw output stays one click away either way). If SteamPrefill
 * rewords it, the row simply falls back to the plain output.
 *
 * NOT the exception type name: `LancacheNotFoundException` is also what
 * SteamPrefill throws as "Unable to detect Lancache server!" when the
 * heartbeat fails for ANY reason (poc/steamprefill/PROTOCOL.md,
 * Troubleshooting). Matching the name would state a false cause there.
 */
const PUBLIC_IP_MARKERS = /is resolving to a public IP/;

export const FAILURE_HINT = Object.freeze({
  NOT_LOGGED_IN: "not_logged_in",
  PUBLIC_IP: "public_ip",
});

/**
 * Which hint block (if any) a failed job's expanded row shows.
 * @returns {string | null} a FAILURE_HINT value
 */
export function jobFailureHint(job, excerpt) {
  const reason = jobFailureReason(job, excerpt);
  if (reason === FAILURE_REASON.NOT_LOGGED_IN) return FAILURE_HINT.NOT_LOGGED_IN;
  if (reason === FAILURE_REASON.EXIT_CODE && PUBLIC_IP_MARKERS.test(excerpt)) return FAILURE_HINT.PUBLIC_IP;
  return null;
}

/** The queue-mode login command (deploy/README.md, "First run: the
 * one-time SteamPrefill login"), the shipped compose default. Pinned
 * against the README by a drift test. */
export const LOGIN_COMMAND = "docker compose exec -it vault-runner /opt/steamprefill/SteamPrefill select-apps";

/** The deploy/README.md heading the public-IP hint points to (rendered
 * text, backticks dropped). Pinned against the README by a drift test. */
export const PUBLIC_IP_README_SECTION = "Fix it (only needed with a dedicated VAULT_CORE_BIND)";

const OUTPUT_SUMMARY = "Show the full SteamPrefill output";
const RETRY_LINE = "Then press Retry.";
/** Shown instead of the Retry line on an older failed job for an app that
 * already has a newer job (Retry is offered on the newest one only). */
export const NEWER_JOB_LINE = "A newer job for this game exists (see above).";

/**
 * Text of each hint block, top to bottom. `code` is a literal shown in its
 * own wrapping box (`codeIntro` above it).
 */
export const HINTS = Object.freeze({
  [FAILURE_HINT.NOT_LOGGED_IN]: Object.freeze({
    title: "Steam login missing",
    body:
      "SteamPrefill on the server has no Steam session yet. It needs a one-time interactive login on the server. " +
      "vault-api never sees or stores your Steam credentials.",
    codeIntro: "In the folder with SteamHangar's compose.yaml (deploy/ by default), run:",
    code: LOGIN_COMMAND,
    after:
      "Enter your account name, password and Steam Guard code, then exit the app selector. " +
      "This is the command for the default queue mode. With VAULT_PREFILL_MODE=subprocess it differs; " +
      "see deploy/README.md, “First run: the one-time SteamPrefill login”.",
    retry: RETRY_LINE,
    outputSummary: OUTPUT_SUMMARY,
  }),
  [FAILURE_HINT.PUBLIC_IP]: Object.freeze({
    title: "The prefill cannot find the cache",
    body:
      "Inside the container that runs SteamPrefill (vault-runner in the default queue mode; " +
      "vault-api instead with VAULT_PREFILL_MODE=subprocess), " +
      "lancache.steamcontent.com resolves to a public address, so SteamPrefill does not find vault-core.",
    codeIntro:
      "Fix it one of two ways. Either pin the name with extra_hosts on vault-runner to vault-core's " +
      "private IPv4 address (a plain IP, not a hostname; vault-api instead with VAULT_PREFILL_MODE=subprocess), " +
      "for example in a compose.override.yaml, then re-run docker compose -f compose.yaml -f compose.override.yaml up -d:",
    code: 'services:\n  vault-runner:\n    extra_hosts:\n      - "lancache.steamcontent.com:<vault-core private IPv4>"',
    after:
      "Or set up a DNS rewrite for lancache.steamcontent.com on the resolver the container uses. " +
      `See deploy/README.md, “${PUBLIC_IP_README_SECTION}”.`,
    retry: RETRY_LINE,
    outputSummary: OUTPUT_SUMMARY,
  }),
});

/** Kept for callers/tests that name the login hint directly. */
export const LOGIN_HINT = HINTS[FAILURE_HINT.NOT_LOGGED_IN];

/**
 * Whether `job` is the newest PREFILL job for its app in `jobs` (highest
 * id; ids are SQLite autoincrement, so higher = later). Retry is offered
 * there only. A later GC job for the same app does not count.
 * @param {{id: number, appid: number}} job
 * @param {Array<{id: number, appid: number}>} jobs
 */
export function isNewestJobForApp(job, jobs) {
  if (!job || !Array.isArray(jobs)) return false;
  return !jobs.some((j) => j && j.type === "prefill" && j.appid === job.appid && j.id > job.id);
}
