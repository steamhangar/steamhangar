package dev.steamvault.app.ui.traffic.logic

import dev.steamvault.app.ui.clients.logic.AGENTLESS_NOTE
import dev.steamvault.app.ui.clients.logic.LOADING_CLIENTS_TEXT
import dev.steamvault.app.ui.clients.logic.NO_CLIENTS_TEXT
import dev.steamvault.app.ui.downloads.LOADING_STATISTICS_TEXT
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP D15d: the words the app shows on the Clients page and in
 * the Downloads statistics must still be the web's (WP D15c). Each entry
 * must appear in the named web source -- as one double-quoted string
 * (`quoted`), or as a fragment of a template literal (`fragments`). The
 * Kotlin side of each word is pinned by string equality in
 * `TrafficViewTest` / `ClientsPageTest`, so together the two pin
 * Kotlin == web. VALUE drift -> update the Kotlin port; GRAMMAR drift (the
 * web changed the string form) -> adjust this test.
 */
class TrafficWebTwinTest {

    private fun web(path: String): String {
        val file = File("../../web/js/$path")
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        return file.readText(Charsets.UTF_8)
    }

    private fun assertQuoted(path: String, literals: List<String>) {
        val js = web(path)
        for (l in literals) {
            if (!js.contains("\"$l\"")) {
                fail(
                    if (js.contains(l)) {
                        "GRAMMAR DRIFT: $path contains \"$l\" but not as one double-quoted string -- adjust this test."
                    } else {
                        "VALUE DRIFT: $path no longer contains \"$l\" -- update the Kotlin port."
                    },
                )
            }
        }
    }

    private fun assertFragments(path: String, fragments: List<String>) {
        val js = web(path)
        for (f in fragments) {
            if (!js.contains(f)) fail("VALUE DRIFT: $path no longer contains the fragment \"$f\" -- update the Kotlin port.")
        }
    }

    @Test
    fun `traffic-view literals`() {
        assertQuoted(
            "lib/traffic-view.js",
            RANGE_LABELS.values.toList() + RANGE_PHRASES.values.toList() + listOf(
                DASH,
                FeedText.DISABLED,
                FeedText.UNSUPPORTED,
                FeedText.STALE_NEVER,
                "0 MB/s",
                "< 1 KB/s",
                "0 GB",
                "< 1 MB",
                "Current speed",
                "Average while loading",
                "Peak speed",
                "Served (cache + prefill)",
                "Loaded from the internet",
                "Hit rate",
                "Saved by the cache",
                "Loading now",
                // WP GRAPH-ZOOM: the 1-minute tier's bucket word and the new range captions.
                "minute",
                "5 minutes",
                "hour",
                "day",
                "1 h ago",
                "6 h ago",
                "24 h ago",
                "7 days ago",
                "30 days ago",
                "Speed not measured yet: the server does not know this game's depots yet.",
                "Nothing finished yet.",
            ),
        )
        assertFragments(
            "lib/traffic-view.js",
            listOf(
                "Couldn't refresh: \${reason}",
                "No traffic data yet. The server reads the cache's event log every \${minutes} min; the first numbers appear after that.",
                "Traffic data is out of date: the server last read the cache's event log \${ago}. Current values show a dash until it reads it again.",
                "Average speed per \${per} \${phrase}. Highest: ",
                "since \${day}",
                " loaded so far",
                "Nothing finished \${RANGE_PHRASES[range]}.",
                // WP D15-RUNNER: the "Loaded" tile's second line.
                // WP RUNNER-NAME-1: named by the product (PRODUCT_NAME).
                "`\${size} by \${PRODUCT_NAME}, \${cached} of that from the cache`",
                "`\${size} by \${PRODUCT_NAME}`",
                // WP METRIC-AUDIT: the scope details.
                "`\${scope} · \${runner} by \${PRODUCT_NAME}`",
                "`all traffic incl. \${PRODUCT_NAME}, 5-10 min window`",
                "`\${liveScope} · \${liveRunner} by \${PRODUCT_NAME}`",
                "const scope = \"clients only, own prefill excluded\";",
                "`\${scope} (\${PRODUCT_NAME}: \${rate})`",
                "machines with cache traffic in the last \${windowMinutes} min",
                // WP LOADING-NOW-1 (ANDROID-DL-1): a live count's window in seconds.
                "machines the cache served in the last \${windowSeconds} s",
            ),
        )
    }

    /** WP GRAPH-LIVE: the live range's keys, words, cadence and endpoint are the web's. */
    @Test
    fun `live view literals`() {
        assertQuoted("lib/traffic-view.js", listOf("Live", "5 seconds", "15 seconds"))
        assertFragments(
            "lib/traffic-view.js",
            listOf(
                // The range keys, in the order the chips show them ("Live" first).
                "export const STATS_RANGES = Object.freeze([\"live\", \"1h\", \"6h\", \"24h\", \"7d\", \"30d\", \"all\"]);",
                "live: \"Live\",",
                "live: \"in the last hour\",",
                "{ live: \"1 h ago\",",
                "`all traffic incl. \${PRODUCT_NAME}, last \${liveSeconds} s`",
                "export const LIVE_POLL_MS = 3000;",
                "export const LIVE_SUMMARY_MS = 30000;",
                "export const LIVE_SUMMARY_RANGE = \"1h\";",
                "Math.round((p.bucket_seconds || 5) / 5)",
            ),
        )
        assertQuoted("api.js", listOf("/v1/throughput/live"))
        // WP ANDROID-DL-1: the Android default follows the web's (LIVE-DELAY-1).
        assertFragments("lib/traffic-view.js", listOf("export const DEFAULT_STATS_RANGE = \"$DEFAULT_STATS_RANGE\";"))
    }

    /** WP ANDROID-DL-1: the "now" merges read the same fields as the web's. */
    @Test
    fun `now merges`() {
        assertFragments(
            "lib/traffic-view.js",
            listOf(
                // mergeLive: "Loading now" from the ring, its window in seconds.
                "if (Number.isInteger(live.active_clients)) {\n      merged.active_clients_now = live.active_clients;",
                "merged.active_window_seconds = Number.isInteger(live.active_window_seconds) ? live.active_window_seconds : null;",
                // mergeNow: the `now` block, only with an ok feed.
                "if (!now || !now.current || feedState(throughput) !== \"ok\") return throughput;",
                "const merged = { ...throughput, current: now.current, live: true };",
                "merged.active_clients_now = now.active_clients;",
                "merged.active_window_seconds = Number.isInteger(now.window_seconds) ? now.window_seconds : null;",
                "if (typeof now.runner_active === \"boolean\") merged.runner_active_now = now.runner_active;",
            ),
        )
        // The page merges every persisted range's answer with its `now` block.
        assertFragments("views/downloads.js", listOf("return { range, throughput: mergeNow(throughput), series };"))
    }

    @Test
    fun `clients-page literals`() {
        assertQuoted(
            "lib/clients-page.js",
            listOf(
                "No agent · seen in the cache log",
                // WP D15-RUNNER: the runner's line without an address.
                "This server's prefill downloads",
                "Agent online",
                "Agent offline",
                "Loading",
                "No agent",
                "No current data",
                "Loading now: nothing",
                "Installed games: not reported",
                "Last active: never",
                AGENTLESS_NOTE,
                NO_CLIENTS_TEXT,
            ),
        )
        assertFragments(
            "lib/clients-page.js",
            listOf(
                // WP D15-NAMES: "vault-agent" or "vault-agent on <PC>" + the rest.
                // The unnamed prefix is pinned as the ternary's else-branch
                // (`: "vault-agent";`), not as a bare quoted "vault-agent":
                // that word alone would still match after the unnamed branch
                // changed, as long as any other quoted "vault-agent" remained.
                "`vault-agent on \${isolate(client.client_id)}` : \"vault-agent\";",
                // WP D15-NAMES: clientName checks display_name FIRST (Kotlin
                // `clientName`, pinned in ClientsPageTest); this fragment
                // fails if the web moves another rule ahead of it.
                "export function clientName(client) {\n  if (typeof client.display_name === \"string\" && client.display_name) return client.display_name;",
                "`\${agent} · \${addrs.join(\", \")}`",
                "`\${agent} · no address in the cache log`",
                "`No agent · \${addrs.join(\", \")} · seen in the cache log`",
                // WP D15-RUNNER.
                "`This server's prefill downloads · \${addrs.join(\", \")}`",
                "client.kind === \"runner\"",
                "Loading now: \${speed} · content not linked to one game",
                " game\${n === 1 ? \"\" : \"s\"} installed",
                " loaded \${phrase}",
                " · hit rate \${rate}",
                "+\${more} more",
                " not linked to one game",
                "Loaded recently: ",
                "Last active \${ago}",
                " connected",
            ),
        )
    }

    @Test
    fun `client-names isolate`() {
        // WP D15-NAMES: `isolate` wraps the PC name in FSI ... PDI; the
        // Kotlin `isolate` is pinned to the same two code points in
        // ClientsPageTest.
        assertFragments("lib/client-names.js", listOf("return `\\u2068\${text}\\u2069`;"))
    }

    @Test
    fun `view literals`() {
        assertQuoted("views/clients.js", listOf(LOADING_CLIENTS_TEXT, "Request failed."))
        assertFragments(
            "views/clients.js",
            listOf(
                "Could not load clients: \${state.error}",
                // WP D15-RUNNER: the agentless note is gated on address
                // clients only (Kotlin `showAgentlessNote`, ClientsPageTest).
                "els.agentlessNote.hidden = !clients.some((c) => isAddressClient(c));",
            ),
        )
        // WP DOWNLOADS-2: the statistics moved from views/downloads.js into
        // the hero component; its state comes in as `error`, not
        // `traffic.error`.
        assertQuoted("components/downloads-hero.js", listOf(LOADING_STATISTICS_TEXT, SERIES_END_TEXT, NOT_AVAILABLE))
        assertFragments(
            "components/downloads-hero.js",
            listOf("Could not load the statistics: \${error}", "`highest \${formatSpeed(geo.maxBytesPerSecond) || \"0 MB/s\"}`"),
        )
    }
}
