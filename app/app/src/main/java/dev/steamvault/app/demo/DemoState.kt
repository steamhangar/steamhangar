package dev.steamvault.app.demo

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.AboutOut
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.OwnedGamesRelayOut
import dev.steamvault.app.net.model.PlayerSummariesRelayOut
import dev.steamvault.app.net.model.PlayerSummaryEntry
import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.DeletedDepotOut
import dev.steamvault.app.net.model.GameDetail
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.GcJobRef
import dev.steamvault.app.net.model.JobControlOut
import dev.steamvault.app.net.model.JobDetail
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.model.MappingEntry
import dev.steamvault.app.net.model.PrefillJobRef
import dev.steamvault.app.net.model.ScheduleOut
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.SettingsOut
import dev.steamvault.app.net.model.SkippedSharedDepotOut
import dev.steamvault.app.net.model.settingAsBooleanOrNull
import dev.steamvault.app.net.model.settingAsIntOrNull
import dev.steamvault.app.net.model.settingAsStringOrNull
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant

/**
 * Demo mode's in-memory fixture data (WP APP-DEMO brief). The whole point of
 * this class -- mirroring `web/js/demo-data.js`'s module-level mutable state
 * exactly (see that file's own header) -- is that it is the ONLY place demo
 * data lives: no persistence, no network, reset to a fresh copy every time
 * [DemoState.fresh] is called (once per "enter demo mode" action in
 * [dev.steamvault.app.MainActivity], never reused across sessions -- WP
 * brief constraint 4: "re-entering must not carry stale state").
 *
 * **Shapes match the real API 1:1 by construction, not by convention.**
 * Every method below returns the SAME `@Serializable` data classes
 * [dev.steamvault.app.net.VaultApiClient]'s real endpoints return
 * (the `net/model` package) -- there is no separate "demo DTO" shape to drift from
 * the real one; adding a field to (say) [GameSummary] would fail to compile
 * here until this file's constructor call sites are updated too. This is a
 * stronger guarantee than a hand-checked "matches the docs" claim.
 *
 * **WP APP-FEAT-1 update:** the Library's owned games now come from the
 * vault's stored `steam_library_steamid` plus the relay for that id, both
 * vault-api-mediated, so this class now has a relay fixture
 * ([ownedGames]/[playerSummaries], [seedOwnedGames]) and the setting. The
 * paragraph below still holds for the device's own OpenID identity.
 *
 * **No Steam identity fixture on purpose (WP brief constraint 5:
 * "do not touch the credential store or the OpenID flow").** Demo mode
 * never builds a [dev.steamvault.app.net.VaultApiClient], so
 * [dev.steamvault.app.repo.SteamIdentityRepositoryImpl.ownedGames] already
 * fails closed on its own (`VaultRelayLibraryFetcher.requireClient` throws
 * `IllegalStateException` when there is no client) -- exactly the same
 * "vault-only view stays fully functional" path a real, never-connected
 * install takes today. Reusing that existing fail-closed behaviour, rather
 * than inventing a parallel Steam-relay fixture the way `web/js/demo-data.js`
 * has to (its identity model is entirely vault-api-mediated; this app's
 * OpenID sign-in is not), is what keeps this WP from ever touching identity
 * code at all. One consequence: the ADR-0010 "the two playtime fields are
 * absent by default" rule has no [dev.steamvault.app.net.model.OwnedGame]
 * surface in THIS fixture to violate -- there is no owned-games fixture,
 * full stop.
 *
 * Every job/game mutation is call-count driven ([tick]), never wall-clock --
 * same reasoning `web/js/demo-data.js`'s header gives ("demo mode needs no
 * timers of its own"). [tick] runs at the top of every jobs read
 * ([listJobSummaries]/[jobDetail]) so progress advances whether the caller
 * is polling `GET /v1/jobs` (Library/Downloads) or `GET /v1/jobs/{id}`
 * (the GC flow's own poll loop, `ui/detail/DetailController.kt`).
 */
class DemoState private constructor(
    private val games: MutableList<DemoGame>,
    private val jobs: MutableList<DemoJob>,
    private val clients: List<ClientOut>,
    /** WP AG-3: `GET /v1/schedule`'s `last_sweep_at` fixture, computed once
     * at [fresh] time (not on every [scheduleOut] call) so it reads as a
     * stable "42 minutes ago" for the lifetime of one demo session, the
     * same freshness posture [seedGames]/[seedJobs]/[seedClients] already
     * take for their own timestamps. */
    private val lastSweepAt: String,
    private val about: AboutOut,
    private val ownedGamesFixture: List<OwnedGame>,
) {
    private var nextJobId = (jobs.maxOfOrNull { it.id } ?: 900_099) + 1
    private val settingsOverrides = mutableMapOf<String, String>()

    // ---- games ------------------------------------------------------------

    @Synchronized
    fun listGameSummaries(): List<GameSummary> = games.map { it.toSummary() }

    @Synchronized
    fun gameDetail(appid: Int): GameDetail {
        val game = games.find { it.appid == appid }
            ?: throw VaultApiError.NotFound("no such app in the demo library", 404, "appid $appid not found")
        return game.toDetail()
    }

    @Synchronized
    fun mapping(): List<MappingEntry> =
        games.flatMap { game -> game.depots.map { MappingEntry(depotid = it.depotid, appid = game.appid) } }

    // ---- jobs ---------------------------------------------------------------

    @Synchronized
    fun listJobSummaries(limit: Int): List<JobSummary> {
        tick()
        return jobs.sortedByDescending { it.id }.take(limit).map { it.toSummary() }
    }

    @Synchronized
    fun jobDetail(id: Int): JobDetail {
        tick()
        val job = jobs.find { it.id == id }
            ?: throw VaultApiError.NotFound("no such job in the demo library", 404, "job $id not found")
        return job.toDetail()
    }

    @Synchronized
    fun enqueuePrefill(appids: List<Int>): List<PrefillJobRef> = appids.map { appid -> enqueueOnePrefill(appid) }

    /** `POST /v1/prefill/cached` (WP 4c-app's real contract) — only games
     * already `done` (on the cache) get requeued for a check, mirroring
     * `web/js/demo-data.js`'s `enqueuePrefillForAppid` reuse for this route. */
    @Synchronized
    fun enqueuePrefillCached(): List<PrefillJobRef> =
        games.filter { it.status == STATUS_DONE }.map { enqueueOnePrefill(it.appid) }

    private fun enqueueOnePrefill(appid: Int): PrefillJobRef {
        val existing = jobs.find { it.appid == appid && it.status in ACTIVE_JOB_STATUSES }
        if (existing != null) {
            return PrefillJobRef(appid = appid, job_id = existing.id, status = existing.status, deduplicated = true)
        }
        var game = games.find { it.appid == appid }
        if (game == null) {
            // WP APP-FIX-2 ("demo must mirror the API", docs/LEARNINGS.md WP
            // WEB-FIX-4): the real POST /v1/prefill inserts the apps row with
            // name = NULL (`INSERT OR IGNORE INTO apps (appid, status)`,
            // api/vault_api/jobs.py); only a depot mapping names it later.
            // Copying the owned title here would hide an "App <id>" bug the
            // real server shows. A never-filled row is needs_force = true.
            game = DemoGame(
                appid = appid,
                name = null,
                status = STATUS_IDLE,
                needsForce = true,
                depots = mutableListOf(),
                lastPrefillAt = null,
                lastManifestCheck = null,
                gcReclaimableBytes = 0L,
                gcHeldBackBytes = 0L,
            )
            games.add(game)
        }
        val job = DemoJob(
            id = nextJobId++,
            appid = appid,
            type = "prefill",
            status = STATUS_RUNNING,
            createdAt = nowIso(),
            startedAt = nowIso(),
            ticksLeft = PREFILL_TICKS,
        )
        jobs.add(job)
        game.status = STATUS_RUNNING
        return PrefillJobRef(appid = appid, job_id = job.id, status = job.status, deduplicated = false)
    }

    /**
     * Nitpick fix (WP APP-DEMO review round 2): the real server refuses
     * pause/resume/cancel against a job that is not in the matching state
     * (a `409`, api/README.md "Job control") -- the original version of
     * this method accepted any action against any job, including a
     * terminal (`done`/`error`/already-`cancelled`) one, which is a shape
     * the real API cannot produce. Each branch below now guards on the
     * ONE prior status that action is valid from.
     */
    @Synchronized
    fun controlJob(id: Int, action: JobControlAction): JobControlOut {
        val job = jobs.find { it.id == id }
            ?: throw VaultApiError.NotFound("no such job in the demo library", 404, "job $id not found")
        return when (action) {
            JobControlAction.PAUSE -> {
                if (job.status != STATUS_RUNNING) {
                    throw VaultApiError.Validation("job is not running", 409, "job $id is '${job.status}', cannot pause")
                }
                job.status = STATUS_PAUSED
                job.pausedAt = nowIso()
                JobControlOut(job_id = id, status = job.status, outcome = "paused", detail = "paused on request")
            }
            JobControlAction.RESUME -> {
                if (job.status != STATUS_PAUSED) {
                    throw VaultApiError.Validation("job is not paused", 409, "job $id is '${job.status}', cannot resume")
                }
                job.status = STATUS_RUNNING
                job.pausedAt = null
                JobControlOut(job_id = id, status = job.status, outcome = "resumed", detail = "resumed on request")
            }
            JobControlAction.CANCEL -> {
                if (job.status !in ACTIVE_JOB_STATUSES) {
                    throw VaultApiError.Validation("job is not active", 409, "job $id is '${job.status}', cannot cancel")
                }
                job.status = STATUS_CANCELLED
                job.finishedAt = nowIso()
                games.find { it.appid == job.appid }?.let { if (it.status == STATUS_RUNNING) it.status = STATUS_IDLE }
                JobControlOut(job_id = id, status = job.status, outcome = "cancelled", detail = "cancelled on request")
            }
        }
    }

    /** Advances every still-active job by one tick — see class kdoc. */
    private fun tick() {
        for (job in jobs) {
            if (job.status != STATUS_RUNNING || job.ticksLeft <= 0) continue
            job.ticksLeft -= 1
            if (job.ticksLeft > 0) continue
            when (job.type) {
                "prefill" -> finishPrefillJob(job)
                "gc" -> finishGcJob(job)
            }
        }
    }

    private fun finishPrefillJob(job: DemoJob) {
        job.status = STATUS_DONE
        job.finishedAt = nowIso()
        job.updated = 1
        job.upToDate = 0
        job.summaryParseOk = true
        job.logExcerpt = "[vault-api] worker claimed job ${job.id}\n" +
            "Downloading depot ${job.appid + 1} ...\n" +
            "Prefilled 1 app. Done."
        val game = games.find { it.appid == job.appid } ?: return
        game.status = STATUS_DONE
        game.needsForce = false
        game.lastPrefillAt = nowIso()
        if (game.depots.isEmpty()) {
            game.depots.add(DemoDepot(depotid = game.appid + 1, shared = false, sizeBytes = 1_200_000_000L))
        }
    }

    private fun finishGcJob(job: DemoJob) {
        job.finishedAt = nowIso()
        job.status = STATUS_DONE
        val game = games.find { it.appid == job.appid }
        val wouldDeleteBytes = game?.gcReclaimableBytes ?: 0L
        val heldBackBytes = game?.gcHeldBackBytes ?: 0L
        job.logExcerpt = if (job.gcExecute == true) {
            job.summaryParseOk = true
            "GC totals (EXECUTED): chunks_removed=1 bytes_freed=$wouldDeleteBytes " +
                "held_back=1 (${heldBackBytes} bytes) total_bytes_freed=$wouldDeleteBytes"
        } else {
            job.summaryParseOk = true
            "GC totals (DRY RUN): would_delete=1 ($wouldDeleteBytes bytes) held_back=1 (${heldBackBytes} bytes)"
        }
        if (job.gcExecute == true) {
            game?.gcReclaimableBytes = 0L
        }
    }

    // ---- cache / gc -----------------------------------------------------------

    @Synchronized
    fun deleteCache(appid: Int): CacheDeletionOut {
        val game = games.find { it.appid == appid }
            ?: throw VaultApiError.NotFound("no such app in the demo library", 404, "appid $appid not found")

        val deleted = mutableListOf<DeletedDepotOut>()
        val skipped = mutableListOf<SkippedSharedDepotOut>()
        var freed = 0L
        // Deletion always clears THIS game's own cache membership for every
        // depot it lists (api/README.md "Per-game deletion": "deletion
        // clears cache content, not mapping rows") -- a depot only ends up
        // in `skipped` (informational: bytes were NOT double-freed because
        // another still-cached game holds the same physical chunk) rather
        // than `deleted`, never kept on THIS game's own list either way.
        // ADR-0003's protection is about the underlying chunk files, not
        // about whether the game that just deleted still "has" the depot.
        for (depot in game.depots) {
            val coOwners = games.filter { it.appid != appid && it.depots.any { d -> d.depotid == depot.depotid } }
            if (depot.shared && coOwners.isNotEmpty()) {
                skipped.add(SkippedSharedDepotOut(depotid = depot.depotid, shared_with = coOwners.map { it.appid }))
            } else {
                deleted.add(DeletedDepotOut(depotid = depot.depotid, size_bytes_freed = depot.sizeBytes))
                freed += depot.sizeBytes
            }
        }
        game.depots.clear()
        game.status = STATUS_IDLE
        game.needsForce = true
        return CacheDeletionOut(
            appid = appid,
            deleted_depots = deleted,
            skipped_shared = skipped,
            failed = emptyList(),
            total_bytes_freed = freed,
        )
    }

    @Synchronized
    fun gc(appid: Int, execute: Boolean): GcJobRef {
        if (games.none { it.appid == appid }) {
            throw VaultApiError.NotFound("no such app in the demo library", 404, "appid $appid not found")
        }
        val job = DemoJob(
            id = nextJobId++,
            appid = appid,
            type = "gc",
            status = STATUS_RUNNING,
            createdAt = nowIso(),
            startedAt = nowIso(),
            ticksLeft = GC_TICKS,
            gcExecute = execute,
        )
        jobs.add(job)
        return GcJobRef(
            appid = appid,
            job_id = job.id,
            status = job.status,
            type = "gc",
            mode = if (execute) "execute" else "dry_run",
            execute = execute,
            deduplicated = false,
        )
    }

    // ---- clients ------------------------------------------------------------

    @Synchronized
    fun clientsOut(): List<ClientOut> = clients

    // ---- about (WP APP-FEAT-2) ------------------------------------------------

    @Synchronized
    fun aboutOut(): AboutOut = about

    // ---- Steam relay for an explicit SteamID64 (WP APP-FEAT-1) ----------------

    /**
     * `GET /v1/steam/owned-games` -- mirrors the real route's input check
     * (`422` for a steamid that is not a valid SteamID64) and answers with
     * the fictional [seedOwnedGames] list for any valid id. The demo has no
     * relay-key concept (Android has no key UI), so it never answers `409`.
     */
    @Synchronized
    fun ownedGames(steamId64: String): OwnedGamesRelayOut {
        requireDemoSteamId(steamId64)
        return OwnedGamesRelayOut(configured = true, game_count = ownedGamesFixture.size, games = ownedGamesFixture)
    }

    @Synchronized
    fun playerSummaries(steamId64: String): PlayerSummariesRelayOut {
        requireDemoSteamId(steamId64)
        return PlayerSummariesRelayOut(
            configured = true,
            players = listOf(PlayerSummaryEntry(steamid = steamId64, personaname = "vaultkeeper_demo", personastate = 1)),
        )
    }

    private fun requireDemoSteamId(steamId64: String) {
        if (!isDemoValidSteamId64(steamId64)) {
            throw VaultApiError.Validation("invalid steamid", 422, "steamid must be a SteamID64 (17 digits)")
        }
    }

    // ---- settings (ADR-0009 db > env > default precedence) ------------------

    @Synchronized
    fun settingsOut(): SettingsOut = SettingsOut(readonly = false, settings = describeSettings())

    @Synchronized
    fun patchSettings(updates: Map<String, JsonElement?>): SettingsOut {
        for ((key, value) in updates) {
            val spec = SETTINGS_SPECS[key]
                ?: throw VaultApiError.Validation("unknown setting", 422, "'$key': unknown setting key")
            if (spec.envOnly) {
                throw VaultApiError.Validation("environment-only setting", 422, "'$key': this key is environment-only")
            }
            if (value == null || value is JsonNull) {
                settingsOverrides.remove(key)
            } else {
                val raw = rawTextOf(value)
                // Mirrors config.parse_steam_library_steamid (WP API-FEAT-1):
                // trimmed, blank = explicitly not set, otherwise a SteamID64.
                if (key == STEAM_LIBRARY_STEAMID_KEY && raw.trim().isNotEmpty() && !isDemoValidSteamId64(raw.trim())) {
                    throw VaultApiError.Validation(
                        "invalid setting",
                        422,
                        "'$key': must be a SteamID64: exactly 17 ASCII digits in the individual-account range; " +
                            "blank (or null over the API) clears it",
                    )
                }
                settingsOverrides[key] = if (key == STEAM_LIBRARY_STEAMID_KEY) raw.trim() else raw
            }
        }
        return settingsOut()
    }

    private fun rawTextOf(value: JsonElement): String = when (value) {
        is kotlinx.serialization.json.JsonArray -> value.joinToString(",") { (it as? JsonPrimitive)?.content.orEmpty() }
        is JsonPrimitive -> value.content
        else -> ""
    }

    // ---- schedule (WP AG-3) ---------------------------------------------------

    /**
     * `GET /v1/schedule` fixture — mirrors `web/js/demo-data.js`'s
     * `handleGetSchedule()` decision, not merely its numbers: `last_sweep_*`/
     * `next_eligible_at` are a static, plausible fixture (this demo model
     * has no real scheduler tick to derive them from, same posture
     * [seedJobs]'s hand-authored history already takes), but
     * `sweep_include_cached`/`sweep_cached_gc_risk` are NOT static — both
     * are derived from [describeSettings]'s live result, so toggling
     * "Include cached games" or "Auto-GC" in the Settings screen and
     * re-fetching `/v1/schedule` (which [dev.steamvault.app.ui.settings.SettingsController.save]
     * does after every successful PATCH) reflects the change immediately.
     * `sweep_cached_gc_risk` is computed with the EXACT SAME formula as
     * `vault_api/scheduler.py::cached_sweep_gc_risk`
     * (`sweep_include_cached && auto_gc != "execute"`) — this is the ONE
     * place in this demo model allowed to restate that formula, mirroring
     * the real server's one place. `DemoScheduleContractTest` pins this
     * formula with the full six-combination truth table plus a named
     * mutation pin (both ported from `web/tests/demo-data-schedule.test.js`),
     * not merely trusted.
     *
     * **Round 2 correction (B4) — the defaults below MIRROR
     * `api/vault_api/config.py`'s real shipped values
     * ([CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED] = `true`,
     * [CONFIG_DEFAULT_AUTO_GC] = `"execute"`, ADR-0014), which means a
     * FRESH demo session reports `sweep_cached_gc_risk = false` and shows
     * NO warning out of the box — exactly what a fresh real vault reports
     * today. The previous version of this kdoc claimed "the shipped
     * defaults already produce it," which was true only because this
     * fixture's `auto_gc` default was still the pre-ADR-0014 `"off"` — a
     * twin-fixture drift `DemoConfigDefaultsDriftTest` now guards against
     * (`docs/LEARNINGS.md`: "twin config files need twin pins"). The
     * risk-warning state is still screenshottable in the demo: flip
     * "Include cached games" to Include and Auto-GC to Off or Dry run in
     * the Settings screen, and re-fetching `/v1/schedule` reflects that
     * immediately — it is simply no longer the FIRST thing a fresh session
     * shows.** Everything downstream (`ui/settings/logic/
     * SchedulePresentation.kt`) only ever reads this already-computed
     * field, never recomputes it (docs/LEARNINGS.md: "two call sites
     * computing the same domain predicate WILL diverge").
     */
    @Synchronized
    fun scheduleOut(): ScheduleOut {
        val effective = describeSettings().associate { it.key to it.effective }
        val window = effective["schedule_window"]?.settingAsStringOrNull()
        val sweepIncludeCached = effective["sweep_include_cached"]?.settingAsBooleanOrNull() ?: false
        val autoGc = effective["auto_gc"]?.settingAsStringOrNull()
        return ScheduleOut(
            enabled = !window.isNullOrBlank(),
            window = window,
            overnight = false,
            interval_minutes = effective["schedule_interval_minutes"]?.settingAsIntOrNull() ?: 180,
            client_stale_days = effective["schedule_client_stale_days"]?.settingAsIntOrNull() ?: 7,
            server_timezone = "UTC+00:00",
            last_sweep_at = lastSweepAt,
            last_sweep_targets = DEMO_LAST_SWEEP_TARGETS,
            last_sweep_enqueued = DEMO_LAST_SWEEP_ENQUEUED,
            next_eligible_at = null,
            sweep_include_cached = sweepIncludeCached,
            sweep_cached_gc_risk = sweepIncludeCached && autoGc != "execute",
        )
    }

    private fun describeSettings(): List<SettingInfoOut> {
        val overridable = SETTINGS_SPECS.values.filter { !it.envOnly }.map { spec ->
            val override = settingsOverrides[spec.key]
            val (effective, source) = when {
                override != null -> spec.parse(override) to "db"
                spec.env != null -> spec.env to "env"
                else -> spec.default to "default"
            }
            SettingInfoOut(
                key = spec.key,
                effective = effective,
                source = source,
                fallback = spec.env ?: spec.default,
                applies = spec.applies,
                env_only = false,
            )
        }
        val envOnly = SETTINGS_SPECS.values.filter { it.envOnly }.map { spec ->
            SettingInfoOut(
                key = spec.key,
                effective = spec.default,
                source = "env",
                fallback = spec.default,
                applies = spec.applies,
                env_only = true,
            )
        }
        return overridable + envOnly
    }

    companion object {
        internal const val STATUS_DONE = "done"
        internal const val STATUS_IDLE = "idle"
        internal const val STATUS_RUNNING = "running"
        internal const val STATUS_ERROR = "error"
        internal const val STATUS_PAUSED = "paused"
        internal const val STATUS_CANCELLED = "cancelled"
        private val ACTIVE_JOB_STATUSES = setOf(STATUS_RUNNING, STATUS_PAUSED, "queued")
        private const val PREFILL_TICKS = 2
        private const val GC_TICKS = 1

        /** WP AG-3 `GET /v1/schedule` fixture — a plausible, static
         * "last sweep" result (mirrors `web/js/demo-data.js`'s
         * `DEMO_LAST_SWEEP`; a non-zero, non-null pair so the demo exercises
         * `sweepTargetsMessage`'s "have real counts" branch, not the
         * never-ran/started-no-result ones). */
        private const val DEMO_LAST_SWEEP_TARGETS = 3
        private const val DEMO_LAST_SWEEP_ENQUEUED = 1
        private const val DEMO_LAST_SWEEP_AGO_SECONDS = 42L * 60L

        private fun nowIso(): String = Instant.now().toString()

        /** A brand-new, unmutated fixture set — call once per "enter demo
         * mode" action, never reused across sessions (WP brief constraint 4). */
        fun fresh(): DemoState = DemoState(
            seedGames(),
            seedJobs(),
            seedClients(),
            Instant.now().minusSeconds(DEMO_LAST_SWEEP_AGO_SECONDS).toString(),
            seedAbout(),
            seedOwnedGames(),
        )
    }
}

enum class JobControlAction { PAUSE, RESUME, CANCEL }

/** Env-only value pinned to demo mode's own SharedPreferences-free posture
 * (there is no real env for a demo session to read) — mirrors
 * `web/js/demo-data.js`'s `ENV_ONLY_DEMO`. */
private data class DemoSettingSpec(
    val key: String,
    val default: JsonElement,
    val env: JsonElement?,
    val applies: String,
    val envOnly: Boolean = false,
    val parse: (String) -> JsonElement = { JsonPrimitive(it) },
)

private val WEBHOOK_EVENTS_ALL = listOf(
    "job.done",
    "job.error",
    "job.cancelled",
    "client.bypass_suspected",
    "client.bypass_resolved",
)

/**
 * WP AG-3 round 2 fix (B4): the single source of truth this fixture's
 * `auto_gc`/`sweep_include_cached` defaults are built from, mirroring
 * `api/vault_api/config.py`'s real shipped `DEFAULT_AUTO_GC`/
 * `DEFAULT_SWEEP_INCLUDE_CACHED` (ADR-0014) — same exported-constant
 * pattern `web/js/demo-data.js`'s `CONFIG_DEFAULT_AUTO_GC`/
 * `CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED` already establishes, kept
 * `internal` (not `private`) specifically so `DemoConfigDefaultsDriftTest`
 * can read config.py's real text and compare against these Kotlin values
 * directly, instead of two independently-typed literals that could drift
 * apart silently (`docs/LEARNINGS.md`: "twin config files need twin
 * pins").
 */
internal const val CONFIG_DEFAULT_AUTO_GC = "execute"

private const val STEAM_LIBRARY_STEAMID_KEY = "steam_library_steamid"

private const val STEAM_ID64_BASE = 76561197960265728L

/**
 * The demo's own SteamID64 check, the same rule as
 * `net/steam/SteamId64.validate` and the server's `valid_steamid64`: exactly
 * 17 ASCII digits in the individual-account range. Restated here because
 * demo code may not import `net.steam` (DemoModeImportAllowlistTest);
 * `DemoAppFeat2Test` (its TWIN PIN test) checks it against `SteamId64.validate` on the same
 * inputs so the two cannot drift (the twin-pin rule).
 */
internal fun isDemoValidSteamId64(value: String): Boolean {
    if (value.length != 17 || value.any { it !in '0'..'9' }) return false
    val parsed = value.toLongOrNull() ?: return false
    return parsed in STEAM_ID64_BASE..(STEAM_ID64_BASE + 0xFFFFFFFFL)
}
internal const val CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED = true

private val SETTINGS_SPECS: Map<String, DemoSettingSpec> = listOf(
    DemoSettingSpec("vault_name", default = JsonPrimitive(""), env = JsonPrimitive("steamhangar-demo"), applies = "restart-required"),
    DemoSettingSpec("schedule_window", default = JsonNull, env = JsonPrimitive("22:00-06:00"), applies = "next_sweep"),
    DemoSettingSpec(
        "schedule_interval_minutes",
        default = JsonPrimitive(180),
        env = JsonPrimitive(180),
        applies = "next_sweep",
        parse = { it.toIntOrNull()?.let { n -> JsonPrimitive(n) } ?: JsonPrimitive(180) },
    ),
    DemoSettingSpec(
        "schedule_client_stale_days",
        default = JsonPrimitive(7),
        env = JsonPrimitive(7),
        applies = "next_sweep",
        parse = { it.toIntOrNull()?.let { n -> JsonPrimitive(n) } ?: JsonPrimitive(7) },
    ),
    // B4 fix: was JsonPrimitive("off") for both -- the pre-ADR-0014 value.
    // config.py's real DEFAULT_AUTO_GC is "execute" (ADR-0014); this
    // fixture must mirror it, not the value that predates the flip.
    DemoSettingSpec(
        "auto_gc",
        default = JsonPrimitive(CONFIG_DEFAULT_AUTO_GC),
        env = JsonPrimitive(CONFIG_DEFAULT_AUTO_GC),
        applies = "immediately",
    ),
    // ADR-0014 (WP SWEEP-1): config.py's real DEFAULT_SWEEP_INCLUDE_CACHED
    // is true. Combined with auto_gc's real "execute" default above,
    // scheduleOut()'s sweep_cached_gc_risk is FALSE on a fresh session --
    // matching a fresh real vault exactly (see DemoState.scheduleOut()'s
    // own kdoc for how to reach the risk-warning state in the demo
    // regardless). web/js/demo-data.js's own comment: default and env are
    // deliberately identical for this key in the demo fixture.
    DemoSettingSpec(
        "sweep_include_cached",
        default = JsonPrimitive(CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED),
        env = JsonPrimitive(CONFIG_DEFAULT_SWEEP_INCLUDE_CACHED),
        applies = "next_sweep",
        parse = { raw -> JsonPrimitive(raw.trim().lowercase() in setOf("true", "yes", "on", "1")) },
    ),
    DemoSettingSpec("webhook_url", default = JsonPrimitive(""), env = JsonPrimitive(""), applies = "restart-required"),
    DemoSettingSpec(
        "webhook_events",
        default = kotlinx.serialization.json.JsonArray(WEBHOOK_EVENTS_ALL.map { JsonPrimitive(it) }),
        env = kotlinx.serialization.json.JsonArray(WEBHOOK_EVENTS_ALL.map { JsonPrimitive(it) }),
        applies = "restart-required",
        parse = { raw ->
            kotlinx.serialization.json.JsonArray(
                raw.split(",").map { it.trim() }.filter { it.isNotEmpty() }.map { JsonPrimitive(it) },
            )
        },
    ),
    // WP APP-FEAT-1 (mirrors WP API-FEAT-1's `steam_library_steamid`): blank
    // by default, like a fresh real vault (VAULT_STEAM_LIBRARY_STEAMID unset
    // -> source "default"). Set it in the demo's Settings to see the
    // owned-games merge in the Library. A JSON string on the wire.
    DemoSettingSpec(STEAM_LIBRARY_STEAMID_KEY, default = JsonPrimitive(""), env = null, applies = "immediately"),
    DemoSettingSpec("db_path", default = JsonPrimitive("/data/vault.db"), env = null, applies = "restart-required", envOnly = true),
    DemoSettingSpec("cache_root", default = JsonPrimitive("/vault/cache"), env = null, applies = "restart-required", envOnly = true),
    DemoSettingSpec(
        "steamprefill_path",
        default = JsonPrimitive("/usr/local/bin/steamprefill"),
        env = null,
        applies = "restart-required",
        envOnly = true,
    ),
    DemoSettingSpec(
        "steamprefill_cache_dir",
        default = JsonPrimitive("/root/.local/share/SteamPrefill"),
        env = null,
        applies = "restart-required",
        envOnly = true,
    ),
    DemoSettingSpec(
        "manifest_archive_dir",
        default = JsonPrimitive("/vault/manifest-archive"),
        env = null,
        applies = "restart-required",
        envOnly = true,
    ),
    DemoSettingSpec("web_dir", default = JsonPrimitive("/app/web"), env = null, applies = "restart-required", envOnly = true),
    DemoSettingSpec("settings_readonly", default = JsonPrimitive(false), env = null, applies = "restart-required", envOnly = true),
    // ADR-0010: both default OFF, and (as this fixture has no owned-games
    // surface at all, see DemoState's class kdoc) there is nothing in this
    // WP's scope that could ever expose either field regardless.
    DemoSettingSpec("relay_expose_playtime", default = JsonPrimitive(false), env = null, applies = "restart-required", envOnly = true),
    DemoSettingSpec("relay_expose_last_played", default = JsonPrimitive(false), env = null, applies = "restart-required", envOnly = true),
).associateBy { it.key }
