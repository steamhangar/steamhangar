package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.PRODUCT_NAME
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP ANDROID-RC21-1-TWINS: the job card's expected size, ETA
 * and EMA (WP JOB-SIZE-1, `web/js/lib/traffic-view.js` `jobEtaSeconds`,
 * `formatEta`, `jobSpeedText`, `jobSpeedNote`, `EtaSmoother`) must still
 * be the web's. Kotlin constants are read from the port itself, so the
 * pins tie Kotlin == web. VALUE drift -> update the Kotlin port; GRAMMAR
 * drift -> adjust this test. Never edit the web literals to make this pass.
 */
class JobSizeWebTwinTest {

    private fun web(path: String): String {
        val file = File("../../web/js/$path")
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        return file.readText(Charsets.UTF_8)
    }

    private fun assertFragments(path: String, fragments: List<String>) {
        val js = web(path)
        for (f in fragments) {
            if (!js.contains(f)) fail("VALUE DRIFT: $path no longer contains the fragment \"$f\" -- update the Kotlin port.")
        }
    }

    @Test
    fun `job size and ETA literals`() {
        assertFragments(
            "lib/traffic-view.js",
            listOf(
                "export const JOB_SPEED_NOTE = \"$JOB_SPEED_NOTE\";",
                // WP RUNNER-NAME-1: the expected note names the product.
                "`" + JOB_SPEED_EXPECTED_NOTE.replace(PRODUCT_NAME, "\${PRODUCT_NAME}") + "`",
                "return entry && entry.expected === true ? JOB_SPEED_EXPECTED_NOTE : JOB_SPEED_NOTE;",
                "export const EXPECTED_SIZE_NOTE = \"$EXPECTED_SIZE_NOTE\";",
                "export const JOB_ETA_MIN_AGE_MS = 20_000;",
                "export const JOB_ETA_MAX_SECONDS = 86_400;",
                "export const ETA_SMOOTHING = 0.3;",
                // The line: "of ~X" only for an expected entry with a size, the ETA as a tail.
                "const tail = eta ? ` · \${eta}` : \"\";",
                "const total = entry.expected === true ? formatTrafficBytes(entry.expected_download_bytes) : null;",
                "if (sofar && total) return `\${speed} · \${sofar} of ~\${total} loaded so far` + EXPECTED_SIZE_NOTE + tail;",
                "return (sofar ? `\${speed} · \${sofar} loaded so far` : speed) + tail;",
                // jobEtaSeconds: the hide rules and the formula.
                "if (verifying || !entry) return null;",
                "const speed = rate === undefined ? entry.eta_bytes_per_second : rate;",
                "if (!finiteNonNegative(remaining) || remaining <= 0) return null;",
                "if (!finiteNonNegative(speed) || speed <= 0) return null;",
                "nowMs - started < JOB_ETA_MIN_AGE_MS",
                "const seconds = remaining / speed;",
                "seconds > JOB_ETA_MAX_SECONDS",
                // formatEta.
                "const minutes = Math.max(1, Math.ceil(seconds / 60));",
                "if (minutes < 60) return `about \${minutes} min left`;",
                "return rest ? `about \${hours} h \${rest} min left` : `about \${hours} h left`;",
                // The EMA: reset on no speed, first sample as is, one step otherwise.
                "if (typeof sample !== \"number\" || !Number.isFinite(sample) || sample <= 0) return null;",
                "if (typeof previous !== \"number\" || !Number.isFinite(previous) || previous <= 0) return sample;",
                "return previous + alpha * (sample - previous);",
                // EtaSmoother forgets jobs the answer no longer lists.
                "const next = new Map();",
                "this.rates = next;",
            ),
        )
        assertEquals(20_000L, JOB_ETA_MIN_AGE_MS)
        assertEquals(86_400.0, JOB_ETA_MAX_SECONDS, 0.0)
        assertEquals(0.3, ETA_SMOOTHING, 0.0)
    }

    /** WP ANDROID-VERIFY-SPEED-1: the Verifying line (web DOWNLOADS-1) is the web's. */
    @Test
    fun `the Verifying speed line`() {
        assertFragments(
            "lib/traffic-view.js",
            listOf(
                "export const VERIFY_SPEED_TEXT = \"$VERIFY_SPEED_TEXT\";",
                // The condition: Verifying and no measured speed; a measured one wins.
                "if (verifying) {",
                "const measured = entry && state === \"ok\" && entry.bytes_per_second !== null && entry.bytes_per_second !== undefined;",
                "if (!measured) return VERIFY_SPEED_TEXT;",
            ),
        )
        // "with or without statistics": a card the answer does not cover gets the line too.
        assertFragments(
            "views/downloads.js",
            listOf(
                "const fresh = t && t.range === traffic.range;",
                "const entry = fresh ? entries.get(Number(card.dataset.jid)) : undefined;",
                "const text = jobSpeedText(entry, fresh ? state : \"disabled\", {",
            ),
        )
        assertEquals("Verifying files, no download speed", VERIFY_SPEED_TEXT)
    }

    @Test
    fun `the Downloads view feeds the smoothed rate and the Verifying flag`() {
        assertFragments(
            "views/downloads.js",
            listOf(
                "etaSmoother.observe(t)",
                "verifying: card.dataset.dk === KIND.VERIFY,",
                "etaRate: entry ? etaSmoother.rate(entry.job_id) : null,",
                "line.title = text ? jobSpeedNote(entry) : \"\";",
            ),
        )
    }
}
