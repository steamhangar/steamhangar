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

    /** WP ANDROID-DL-2: the "Loading now" block's words and rules (NOW-LOADING-1). */
    @Test
    fun `loading now words`() {
        assertFragments(
            listOf(
                "export const LOADING_NOW_LABEL = \"$LOADING_NOW_LABEL\";",
                "export const LOADING_UNATTRIBUTED = \"$LOADING_UNATTRIBUTED\";",
                "export const LOADING_NEW_DEVICE = \"$LOADING_NEW_DEVICE\";",
                "export const LOADING_NO_JOB = \"$LOADING_NO_JOB\";",
                "return names.length ? names.join(\", \") : \"$LOADING_NO_GAME\";",
                "if (hit >= total) return \"$LOADING_FROM_CACHE\";",
                "if (hit <= 0 && miss > 0) return \"$LOADING_FROM_INTERNET\";",
                "return `\${Math.round((hit / total) * 100)}% $LOADING_FROM_CACHE`;",
                "if (job && job.stop_request === \"cancel\") parts.push(\"$LOADING_CANCEL_REQUESTED\");",
                "else if (job && job.stop_request === \"pause\") parts.push(\"$LOADING_PAUSE_REQUESTED\");",
                "src.name : \"$LOADING_RUNNER_NAME\", detail: parts.join(\" · \"), speed });",
                "name: LOADING_UNATTRIBUTED, detail: \"$LOADING_UNATTRIBUTED_DETAIL\", speed });",
            ),
        )
    }

    @Test
    fun `loadingNowModel rules`() {
        assertFragments(
            listOf(
                "if (!throughput || feedState(throughput) !== \"ok\") return null;",
                "const sources = throughput.loading_sources;",
                "if (!Array.isArray(sources) || sources.length === 0) return null;",
                "const speed = formatSpeed(src.bytes_per_second) || DASH;",
                "if (Number.isInteger(job.job_id)) parts.push(`job #\${job.job_id}`);",
                "if (Array.isArray(src.apps) && src.apps.length) parts.push(gamesText(src.apps));",
                "parts.push(src.phase === \"verify\" ? PILL_VERIFYING : PILL_DOWNLOADING);",
                "const known = src.known !== false;",
                "const name = known && typeof src.name === \"string\" && src.name ? src.name : LOADING_NEW_DEVICE;",
                "if (!known && addresses.length) parts.push(addresses.join(\", \"));",
                "if (total <= 0) return null;",
                "return rows.length ? { label: LOADING_NOW_LABEL, rows } : null;",
                // The block shows only with figures (heroModel's figures), from the merged answer.
                "loadingNow: loadingNowModel(t),",
            ),
        )
        // WP NOW-LOADING-1: the rows travel through the merges like the web's.
        val js = File("../../web/js/lib/traffic-view.js").readText(Charsets.UTF_8)
        for (f in listOf(
            "const merged = { ...summary, range: \"live\", live: false, loading_sources: null };",
            "if (Array.isArray(live.loading_sources)) merged.loading_sources = live.loading_sources;",
            "if (Array.isArray(now.loading_sources)) merged.loading_sources = now.loading_sources;",
        )) {
            if (!js.contains(f)) fail("VALUE DRIFT: lib/traffic-view.js no longer contains \"$f\" -- update mergeLive / mergeNow (TrafficView.kt).")
        }
    }

    /** WP ANDROID-REDESIGN-1c: the hero's anatomy (`heroModel`): label, the
     * four tiles, the figure split, the caption and the mini line. */
    @Test
    fun `hero anatomy words and rules`() {
        val tiles = HERO_TILES.joinToString(",\n  ") { t ->
            "Object.freeze({ key: \"${t.key}\", label: \"${t.label}\", caption: ${t.caption?.let { "\"$it\"" } ?: "null"} })"
        }
        assertFragments(
            listOf(
                "export const HERO_LABEL = \"$HERO_LABEL\";",
                "export const HERO_TILES = Object.freeze([\n  $tiles,\n]);",
                "if (text === null || text === undefined || text === \"\" || text === DASH) return { value: DASH, unit: \"\" };",
                "if (s.endsWith(\"%\")) return { value: s.slice(0, -1), unit: \"%\" };",
                "return i > 0 ? { value: s.slice(0, i), unit: s.slice(i + 1) } : { value: s, unit: \"\" };",
                "if (!throughput || feedState(throughput) !== \"ok\") return null;",
                "text: \"no device loading\",",
                "return { text: `\${n} \${n === 1 ? \"device\" : \"devices\"} loading`, title: `\${row.detail}. \${row.title}` };",
                "const m = /^(Limit )(\\S+ \\S+?)(,.*)?$/.exec(rateCap);",
                "return cached ? [\"Prefill runner \", { b: size }, \" of it, \", { b: cached }, \" from the cache\"] : [\"Prefill runner \", { b: size }, \" of it\"];",
                "if (devices) caption.push(...(caption.length ? [\" · \"] : []), devices.text);",
                "{ key: \"average\", parts: [\"Avg while loading \", { b: byKey.get(\"average\").value }], title: byKey.get(\"average\").title },",
                "{ key: \"peak\", parts: [\"Peak \", { b: byKey.get(\"peak\").value }], title: byKey.get(\"peak\").title },",
                "meter: spec.key === \"hit-rate\" && tile.value !== DASH ? meter : null,",
            ),
        )
    }
}
