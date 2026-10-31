package dev.steamvault.app.ui.traffic.logic

import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP ANDROID-DL-1: the Downloads status pill and the split
 * graph follow web `components/downloads-hero.js` (WEB-FIX-20.2,
 * RUNNER-SPLIT-1). Words are pinned as double-quoted literals, the
 * decisions as source fragments of `heroStatus` and `splitGeometry`; the
 * Kotlin side is pinned by literal equality and the mirrored cases in
 * `DownloadsHeroTest`. VALUE drift (the web changed a word or a rule) ->
 * update the Kotlin port; GRAMMAR drift (same rule, new spelling) -> adjust
 * this test. Never edit the web literals to satisfy it.
 */
class DownloadsHeroWebTwinTest {

    private val path = "components/downloads-hero.js"

    private fun web(): String {
        val file = File("../../web/js/$path")
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        return file.readText(Charsets.UTF_8)
    }

    private fun assertFragments(fragments: List<String>) {
        val js = web()
        for (f in fragments) {
            if (!js.contains(f)) fail("VALUE DRIFT: $path no longer contains the fragment \"$f\" -- update the Kotlin port (DownloadsHero.kt).")
        }
    }

    @Test
    fun `pill and legend words`() {
        assertFragments(
            listOf(
                "export const PILL_DOWNLOADING = \"$PILL_DOWNLOADING\";",
                "export const PILL_SERVING = \"$PILL_SERVING\";",
                "export const PILL_VERIFYING = \"$PILL_VERIFYING\";",
                "export const PILL_IDLE = \"$PILL_IDLE\";",
                "export const LEGEND_SERVED = \"$LEGEND_SERVED\";",
                "export const LEGEND_INTERNET = \"$LEGEND_INTERNET\";",
            ),
        )
    }

    @Test
    fun `heroStatus rules and kinds`() {
        assertFragments(
            listOf(
                "if (typeof bps !== \"number\" || !Number.isFinite(bps) || bps < 0) return null;",
                "const runner = rateOf(current.runner_bytes_per_second);",
                "if (runner !== null && runner > 0) {",
                "const hit = rateOf(current.runner_hit_bytes_per_second);",
                "if (hit !== null && hit > 0 && hit >= runner) return { kind: \"verify\", text: PILL_VERIFYING, tone: \"run\" };",
                "return { kind: \"running\", text: PILL_DOWNLOADING, tone: \"run\" };",
                "if (bps > 0) return { kind: \"cached\", text: PILL_SERVING, tone: \"ok\" };",
                "return { kind: \"notinuse\", text: PILL_IDLE, tone: null };",
                // The pill shows only with figures, from the merged `current`.
                "const status = heroStatus(t.current);",
            ),
        )
    }

    @Test
    fun `splitGeometry rules`() {
        assertFragments(
            listOf(
                "const cacheBytes = (p) => p.hit_bytes + (num(p.runner_hit_bytes) ? p.runner_hit_bytes : 0);",
                "const served = points.map((p, i) => (p.bytes_served > 0 ? totals[i] * Math.min(1, cacheBytes(p) / p.bytes_served) : 0));",
                "const max = Math.max(...totals);",
            ),
        )
    }
}
