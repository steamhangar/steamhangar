package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.model.JobSummary

/**
 * Why a prefill job failed, and the hint block for two known causes (WP
 * APP-FIX-2) -- Kotlin port of `web/js/lib/job-failure.js` (WP WEB-FIX-4,
 * extended by WP API-FIX-3's `prefill_failed`). Same semantics, same
 * strings.
 *
 * **Where the reason comes from.** `JobSummary`/`JobDetail` carry no
 * structured failure reason. vault-api writes its own machine-formatted
 * line as the LAST line of every failed prefill's log
 * (`api/vault_api/worker.py`, both failure branches):
 *
 *     [vault-api] Prefill failed (reason=not_logged_in); the depot mapping ...
 *     [vault-api] Prefill failed (reason=prefill_failed): SteamPrefill reported ...
 *
 * so the reason is read from that line, never from SteamPrefill's own
 * wording. Only the last non-empty line counts (a reason-shaped line
 * anywhere else is not vault-api's verdict for this job), and only a
 * `prefill` job with `status == "error"` is read.
 *
 * **Hints.** Two causes get a plain-language hint plus Retry:
 *  - `not_logged_in` (from the reason line alone);
 *  - the public-IP cache detection: only on top of `reason=exit_code`, and
 *    only for the narrow phrase "is resolving to a public IP" -- NOT the
 *    exception type name `LancacheNotFoundException`, which SteamPrefill
 *    also throws for "Unable to detect Lancache server!" when the heartbeat
 *    fails for any other reason (docs/LEARNINGS.md, WP WEB-FIX-4).
 *
 * **`prefill_failed` is recognised but gets NO hint block, on purpose,
 * exactly like the web** (`web/tests/job-failure.test.js`, "prefill_failed
 * (WP API-FIX-3) is read from the reason line and gets no hint block"): a
 * hint block collapses the raw output, and for this reason the cause IS the
 * output's last line (vault-api names what failed and the likely cause
 * there). So the row keeps showing the output, uncollapsed.
 *
 * **Strings.** The hint texts below are a verbatim port of
 * `job-failure.js`'s `HINTS`/`LOGIN_COMMAND`/`NEWER_JOB_LINE` literals and
 * stay Kotlin literals under app/README.md's "verbatim, diffable port"
 * exception: (1) the wording is whatever the web source decided, and (2)
 * `JobFailureTest` pins each one by string equality against a
 * hand-transcribed value, while `JobFailureWebTwinTest` checks the same
 * literals still appear in the web source and in deploy/README.md (the
 * twin pin, docs/LEARNINGS.md "twin config files need twin pins").
 *
 * Pure: no Android/Compose dependency.
 */
object FailureReason {
    const val NOT_LOGGED_IN = "not_logged_in"
    const val EXIT_CODE = "exit_code"
    const val PREFILL_FAILED = "prefill_failed"
}

/** Which hint block (if any) a failed job's expanded row shows. [wireName]
 * is web's `FAILURE_HINT` value. */
enum class FailureHint(val wireName: String) {
    NOT_LOGGED_IN("not_logged_in"),
    PUBLIC_IP("public_ip"),
}

private val REASON_LINE = Regex("""^\[vault-api] Prefill failed \(reason=([a-z_]+)\)""")

/** The narrow SteamPrefill phrase, see the file kdoc. */
private val PUBLIC_IP_MARKER = Regex("is resolving to a public IP")

/**
 * The failure reason vault-api recorded for this job, or `null`.
 * @param excerpt `GET /v1/jobs/{id}`'s `log_excerpt`.
 */
fun jobFailureReason(type: String?, status: String?, excerpt: String?): String? {
    if (type != "prefill" || status != "error") return null
    if (excerpt == null) return null
    val last = excerpt.split("\n")
        .map { it.removeSuffix("\r") }
        .lastOrNull { it.isNotBlank() }
        ?: return null
    return REASON_LINE.find(last)?.groupValues?.get(1)
}

fun jobFailureReason(job: JobSummary?, excerpt: String?): String? =
    jobFailureReason(job?.type, job?.status, excerpt)

/** Which hint block (if any) a failed job's expanded row shows. */
fun jobFailureHint(job: JobSummary?, excerpt: String?): FailureHint? {
    val reason = jobFailureReason(job, excerpt) ?: return null
    if (reason == FailureReason.NOT_LOGGED_IN) return FailureHint.NOT_LOGGED_IN
    if (reason == FailureReason.EXIT_CODE && excerpt != null && PUBLIC_IP_MARKER.containsMatchIn(excerpt)) {
        return FailureHint.PUBLIC_IP
    }
    return null
}

/**
 * Whether [job] is the newest PREFILL job for its app in [jobs] (highest
 * id; ids are SQLite autoincrement, so higher = later). Retry is offered
 * there only: an older failed row would queue a second run of a game that
 * already has a newer job. A later GC job for the same app does not count.
 */
fun isNewestPrefillJobForApp(job: JobSummary?, jobs: List<JobSummary>): Boolean {
    if (job == null) return false
    return jobs.none { it.type == "prefill" && it.appid == job.appid && it.id > job.id }
}

/** One hint block, top to bottom. [code] is shown in its own monospace,
 * selectable box below [codeIntro]. */
data class FailureHintText(
    val title: String,
    val body: String,
    val codeIntro: String,
    val code: String,
    val after: String,
    val retry: String,
    val outputSummary: String,
)

/** The queue-mode login command (deploy/README.md, "First run: the one-time
 * SteamPrefill login"). Twin-pinned against the web and the README. */
const val LOGIN_COMMAND = "docker compose exec -it vault-runner /opt/steamprefill/SteamPrefill select-apps"

/** The deploy/README.md heading the public-IP hint points to (rendered
 * text, backticks dropped). Twin-pinned against the web and the README. */
const val PUBLIC_IP_README_SECTION = "Fix it (only needed with a dedicated VAULT_CORE_BIND)"

const val OUTPUT_SUMMARY = "Show the full SteamPrefill output"
const val RETRY_LINE = "Then press Retry."

/** Shown instead of the Retry line on an older failed job for an app that
 * already has a newer prefill job. */
const val NEWER_JOB_LINE = "A newer job for this game exists (see above)."

val FAILURE_HINTS: Map<FailureHint, FailureHintText> = mapOf(
    FailureHint.NOT_LOGGED_IN to FailureHintText(
        title = "Steam login missing",
        body = "SteamPrefill on the server has no Steam session yet. It needs a one-time interactive login on the server. " +
            "vault-api never sees or stores your Steam credentials.",
        codeIntro = "In the folder with SteamHangar's compose.yaml (deploy/ by default), run:",
        code = LOGIN_COMMAND,
        after = "Enter your account name, password and Steam Guard code, then exit the app selector. " +
            "This is the command for the default queue mode. With VAULT_PREFILL_MODE=subprocess it differs; " +
            "see deploy/README.md, “First run: the one-time SteamPrefill login”.",
        retry = RETRY_LINE,
        outputSummary = OUTPUT_SUMMARY,
    ),
    FailureHint.PUBLIC_IP to FailureHintText(
        title = "The prefill cannot find the cache",
        body = "Inside the container that runs SteamPrefill (vault-runner in the default queue mode; " +
            "vault-api instead with VAULT_PREFILL_MODE=subprocess), " +
            "lancache.steamcontent.com resolves to a public address, so SteamPrefill does not find vault-core.",
        codeIntro = "Fix it one of two ways. Either pin the name with extra_hosts on vault-runner to vault-core's " +
            "private IPv4 address (a plain IP, not a hostname; vault-api instead with VAULT_PREFILL_MODE=subprocess), " +
            "for example in a compose.override.yaml, then re-run docker compose -f compose.yaml -f compose.override.yaml up -d:",
        code = "services:\n  vault-runner:\n    extra_hosts:\n      - \"lancache.steamcontent.com:<vault-core private IPv4>\"",
        after = "Or set up a DNS rewrite for lancache.steamcontent.com on the resolver the container uses. " +
            "See deploy/README.md, “$PUBLIC_IP_README_SECTION”.",
        retry = RETRY_LINE,
        outputSummary = OUTPUT_SUMMARY,
    ),
)

/**
 * Everything a history row needs to decide its failure presentation.
 * `null` from [failureHintViewFor] means "no hint: show the output as
 * before" (every other failure, `prefill_failed` included).
 *
 * @param retryOffered `true` only on the newest prefill job for the app
 *   ([isNewestPrefillJobForApp]); otherwise the block shows [NEWER_JOB_LINE].
 */
data class FailureHintView(
    val hint: FailureHint,
    val text: FailureHintText,
    val retryOffered: Boolean,
)

fun failureHintViewFor(job: JobSummary?, excerpt: String?, jobs: List<JobSummary>): FailureHintView? {
    val hint = jobFailureHint(job, excerpt) ?: return null
    return FailureHintView(hint = hint, text = FAILURE_HINTS.getValue(hint), retryOffered = isNewestPrefillJobForApp(job, jobs))
}
