package dev.steamvault.app.demo

import dev.steamvault.app.net.model.AboutComponentOut
import dev.steamvault.app.net.model.AboutOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.InstalledOnEntry
import dev.steamvault.app.net.model.OwnedGame
import java.time.Instant

/**
 * The curated seed data for a fresh demo session (WP APP-DEMO brief:
 * "invented fixtures, clearly labelled, exercising every screen and
 * state"). Every appid/name/client id below is fictional -- modeled on
 * `web/js/demo-data.js`'s tone and shape (per the WP brief's own pointer),
 * not copied from it, so this app's demo library reads as its own fixture
 * rather than a re-skin. None of it is real Steam data
 * (docs/LEARNINGS.md "Testing discipline": fixtures are synthetic, modeled
 * on real structure, never personal data).
 *
 * Six games chosen to exercise the same handful of states a screenshot or
 * a first look needs:
 *  - Duskfall Array: a plain `done` game, fully confirmed current.
 *  - Cobalt Isthmus: `idle` + `needs_force` with a SURVIVING
 *    `last_manifest_check` -- the post-cache-deletion shape
 *    (api/README.md), exercising `ui/detail/logic/DetailWording.kt`'s
 *    "confirmed before the cache was cleared" branch.
 *  - Wrenfield Static: `running` -- backed by the one seed job that starts
 *    already in flight, so the Downloads screen has something live to show
 *    immediately.
 *  - Halcyon Ledger / Pale Meridian: a genuine SHARED depot between two
 *    `done` games -- deleting either alone must skip that one depot (the
 *    other is still cached), exercising `ui/library/logic/MultiPlan.kt`'s
 *    sharing arithmetic end to end.
 *  - Marrowlight: `error` + `needs_force`, with a non-zero GC dry-run
 *    result (`gcReclaimableBytes`/`gcHeldBackBytes`) so the detail sheet's
 *    GC flow has something to find on the very first check.
 */

private fun isoAgo(secondsAgo: Long): String = Instant.now().minusSeconds(secondsAgo).toString()

internal fun seedGames(): MutableList<DemoGame> = mutableListOf(
    DemoGame(
        appid = 4_010_010,
        name = "Duskfall Array",
        status = DemoState.STATUS_DONE,
        needsForce = false,
        depots = mutableListOf(DemoDepot(depotid = 4_010_011, shared = false, sizeBytes = 3_800_000_000L)),
        lastPrefillAt = isoAgo(3 * 3_600L),
        lastManifestCheck = isoAgo(3_600L),
        gcReclaimableBytes = 90_000_000L,
        gcHeldBackBytes = 0L,
        // WP AG-3 state 1: installed AND cached -- the ordinary badge.
        installedOn = listOf(InstalledOnEntry(client_id = "demo-livingroom-pc", reported_at = isoAgo(600L))),
    ),
    DemoGame(
        appid = 4_010_020,
        name = "Cobalt Isthmus",
        status = DemoState.STATUS_IDLE,
        needsForce = true,
        depots = mutableListOf(),
        lastPrefillAt = null,
        // Survives cache deletion (api/README.md) -- CONFIRMED_BEFORE_CACHE_CLEARED.
        lastManifestCheck = isoAgo(2 * 86_400L),
        gcReclaimableBytes = 0L,
        gcHeldBackBytes = 0L,
        // WP AG-3 state 2: installed but NOT cached -- the protection-gap
        // case the whole installed_on field exists for (api/README.md
        // "Installed state per app").
        installedOn = listOf(InstalledOnEntry(client_id = "demo-steamdeck", reported_at = isoAgo(1_800L))),
    ),
    DemoGame(
        appid = 4_010_030,
        name = "Wrenfield Static",
        status = DemoState.STATUS_RUNNING,
        needsForce = false,
        depots = mutableListOf(DemoDepot(depotid = 4_010_031, shared = false, sizeBytes = 900_000_000L)),
        lastPrefillAt = null,
        lastManifestCheck = null,
        gcReclaimableBytes = 0L,
        gcHeldBackBytes = 0L,
        // WP AG-3 state 3: no fresh signal -- installedOn stays [] (the
        // default). Never worded as "not installed anywhere" -- see
        // ui/library/logic/InstalledState.kt's copy rule.
    ),
    DemoGame(
        appid = 4_010_040,
        name = "Halcyon Ledger",
        status = DemoState.STATUS_DONE,
        needsForce = false,
        depots = mutableListOf(
            DemoDepot(depotid = 4_010_041, shared = false, sizeBytes = 700_000_000L),
            DemoDepot(depotid = 4_010_060, shared = true, sizeBytes = 250_000_000L),
        ),
        lastPrefillAt = isoAgo(4 * 3_600L),
        lastManifestCheck = null,
        gcReclaimableBytes = 0L,
        gcHeldBackBytes = 0L,
        // WP AG-3: installed+cached with TWO fresh reporters, exercising
        // InstalledOnDisplay.additionalClientCount > 0 ("+1 more").
        installedOn = listOf(
            InstalledOnEntry(client_id = "demo-livingroom-pc", reported_at = isoAgo(4 * 3_600L)),
            InstalledOnEntry(client_id = "demo-steamdeck", reported_at = isoAgo(7_200L)),
        ),
    ),
    DemoGame(
        appid = 4_010_050,
        name = "Pale Meridian",
        status = DemoState.STATUS_DONE,
        needsForce = false,
        depots = mutableListOf(DemoDepot(depotid = 4_010_060, shared = true, sizeBytes = 250_000_000L)),
        lastPrefillAt = isoAgo(3_600L),
        lastManifestCheck = isoAgo(3_600L),
        gcReclaimableBytes = 0L,
        gcHeldBackBytes = 0L,
    ),
    DemoGame(
        appid = 4_010_070,
        name = "Marrowlight",
        status = DemoState.STATUS_ERROR,
        needsForce = true,
        depots = mutableListOf(DemoDepot(depotid = 4_010_071, shared = false, sizeBytes = 1_500_000_000L)),
        lastPrefillAt = isoAgo(3 * 3_600L),
        lastManifestCheck = null,
        gcReclaimableBytes = 30_000_000L,
        gcHeldBackBytes = 10_000_000L,
    ),
)

internal fun seedJobs(): MutableList<DemoJob> = mutableListOf(
    DemoJob(
        id = 900_099,
        appid = 4_010_010,
        type = "prefill",
        status = DemoState.STATUS_DONE,
        createdAt = isoAgo(6 * 3_600L),
        startedAt = isoAgo(6 * 3_600L - 5L),
        finishedAt = isoAgo(6 * 3_600L - 1L),
        updated = 8,
        upToDate = 0,
        summaryParseOk = true,
        logExcerpt = "[vault-api] worker claimed job 900099\nPrefilled 8 apps. Done.",
        ticksLeft = 0,
    ),
    DemoJob(
        id = 900_100,
        appid = 4_010_070,
        type = "prefill",
        status = DemoState.STATUS_ERROR,
        createdAt = isoAgo(3 * 3_600L),
        startedAt = isoAgo(3 * 3_600L - 5L),
        finishedAt = isoAgo(3 * 3_600L - 1L),
        updated = 0,
        upToDate = 0,
        summaryParseOk = true,
        // WP APP-FIX-2: the real failure shape (api/vault_api/worker.py's
        // failure branch writes this reason line LAST), so the demo's
        // Downloads history shows the "Steam login missing" hint + Retry.
        logExcerpt = "Unhandled exception. System.InvalidOperationException: Failed to read input in non-interactive mode.\n" +
            "[vault-api] Prefill failed (reason=not_logged_in); the depot mapping for this app was left unchanged.",
        ticksLeft = 0,
    ),
    DemoJob(
        id = 900_101,
        appid = 4_010_030,
        type = "prefill",
        status = DemoState.STATUS_RUNNING,
        createdAt = isoAgo(60L),
        startedAt = isoAgo(45L),
        logExcerpt = "[vault-api] worker claimed job 900101\nDownloading depot 4010031 ...",
        ticksLeft = 2,
    ),
)

internal fun seedClients(): List<ClientOut> = listOf(
    ClientOut(
        client_id = "demo-livingroom-pc",
        first_seen = Instant.now().minusSeconds(30L * 86_400L).toString(),
        last_reported_at = Instant.now().minusSeconds(600L).toString(),
        app_count = 3,
        source_addrs = listOf("10.0.0.42"),
        cache_hits = 812,
        cache_misses = 14,
        bytes_served = 42_000_000_000L,
        last_seen_in_cache_log = Instant.now().minusSeconds(600L).toString(),
        bypass_suspected = false,
        // WP APP-FEAT-2: the four AGENT-FEAT-1 fields, real shape. A current
        // agent: version + 10-minute interval, reported 10 min ago, online
        // until last_reported_at + 2 x 600 s + 300 s.
        agent_version = "0.1.0",
        report_interval_seconds = 600,
        presence = "online",
        offline_after = Instant.now().minusSeconds(600L).plusSeconds(2 * 600L + 300L).toString(),
    ),
    ClientOut(
        client_id = "demo-steamdeck",
        first_seen = Instant.now().minusSeconds(10L * 86_400L).toString(),
        last_reported_at = Instant.now().minusSeconds(7_200L).toString(),
        app_count = 1,
        source_addrs = listOf("10.0.0.57"),
        cache_hits = 96,
        cache_misses = 40,
        bytes_served = 3_100_000_000L,
        last_seen_in_cache_log = Instant.now().minusSeconds(7_200L).toString(),
        bypass_suspected = true,
        // An agent from before AGENT-FEAT-1: no version, no stated interval
        // (the server assumes 30 min), last report 2 h ago -> offline since
        // last_reported_at + 2 x 1800 s + 300 s.
        agent_version = null,
        report_interval_seconds = null,
        presence = "offline",
        offline_after = Instant.now().minusSeconds(7_200L).plusSeconds(2 * 1_800L + 300L).toString(),
    ),
)

/**
 * `GET /v1/about` fixture (WP APP-FEAT-2), real shape
 * (`vault_api/routers/about.py::AboutOut`): the six components in the
 * server's fixed order, each status word one the server can actually send
 * for that component (vault-core and vault-dns are ALWAYS `unknown`, never
 * probed; vault-proxy never has a version). Detail sentences modeled on
 * `vault_api/about.py`'s own.
 */
internal fun seedAbout(): AboutOut {
    val checkedAt = Instant.now().minusSeconds(20L).toString().substringBefore('.').removeSuffix("Z") + "Z"
    val commit = "4f1c2d3e5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d"
    return AboutOut(
        components = listOf(
            AboutComponentOut("vault-api", "0.1.0", commit, "ok", checkedAt, "Also serves the web UI, so the web UI has this version."),
            AboutComponentOut(
                "vault-core", "0.1.0", commit, "unknown", checkedAt,
                "Recorded at vault-core's last start. vault-api has no network path to vault-core (egress lock, ADR-0011), " +
                    "so whether it is running now is not checked.",
            ),
            AboutComponentOut("vault-runner", "0.1.0", commit, "ok", checkedAt, "Last seen 12 s ago."),
            AboutComponentOut("steamprefill", "3.7.2", null, "ok", checkedAt, "Runs in vault-runner; version as the runner reported it."),
            AboutComponentOut(
                "vault-proxy", null, null, "ok", checkedAt,
                "Answers, and refuses a host that is not on the egress allowlist. Its version is not shown: reading it would " +
                    "need a hole in the egress filter.",
            ),
            AboutComponentOut(
                "vault-dns", null, null, "unknown", checkedAt,
                "Optional (compose profile dns); most setups use their own DNS rewrite instead. Not checked: vault-api has no " +
                    "network path to vault-dns (egress lock, ADR-0011), and a test query would itself be DNS traffic.",
            ),
        ),
    )
}

/**
 * Fictional owned-games fixture for the demo relay (WP APP-FEAT-1):
 * deliberately overlapping the vault library on two appids (Duskfall Array,
 * Marrowlight) and adding three owned-only games, so setting a library
 * SteamID64 in the demo's Settings shows both merge cases. The default-gate
 * shape: `playtime_forever`/`rtime_last_played` absent (ADR-0010 defaults
 * off).
 */
internal fun seedOwnedGames(): List<OwnedGame> = listOf(
    OwnedGame(appid = 4_010_010, name = "Duskfall Array"),
    OwnedGame(appid = 4_010_070, name = "Marrowlight"),
    OwnedGame(appid = 4_020_010, name = "Lanternfall Reach"),
    OwnedGame(appid = 4_020_020, name = "Quiet Orbit"),
    OwnedGame(appid = 4_020_030, name = "Tidewright"),
)
