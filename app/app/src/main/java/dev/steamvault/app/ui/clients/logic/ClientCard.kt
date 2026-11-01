package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.model.ClientActivityOut
import dev.steamvault.app.net.model.ClientOut
import dev.steamvault.app.net.model.LoadingNowOut
import dev.steamvault.app.ui.traffic.logic.DASH
import dev.steamvault.app.ui.traffic.logic.RANGE_PHRASES
import dev.steamvault.app.ui.traffic.logic.formatSpeed
import dev.steamvault.app.ui.traffic.logic.formatTrafficBytes
import kotlin.math.floor

/**
 * The rc20 Clients card as data (WP ANDROID-REDESIGN-1a) -- a Kotlin port of
 * the CLIENTS-1..4 half of `web/js/lib/clients-page.js` (`mergeAgentData`,
 * `clientCardModel`, `summaryTiles` and their helpers). Pure:
 * `ui/clients/ClientsScreen.kt` draws the card in Material terms from
 * [ClientCardView] and the tiles from [SummaryTile].
 *
 * **Verbatim-port exception (app/README.md "String resources").** Every
 * user-visible literal is the web module's own, pinned by string equality
 * in `ClientCardTest` and against the web source in `ClientCardWebTwinTest`.
 *
 * Rules (web's):
 *  - One card per device: the `GET /v1/clients` row joins the overview row
 *    of the same `client_id`, never by address ([mergeAgentData]).
 *  - "Loading now" reads [currentLoading]: `live_now` (seconds old) first,
 *    else the swept `loading_now`.
 *  - An agent is outdated when its version DIFFERS from the server's (newer
 *    too), both in the release grammar; anything else never warns.
 *  - A figure the server did not send is the dash, never a zero.
 */

/** A device on the Clients page: the overview row plus the agent row of
 * the same PC (`null` for an address, the runner, or no agent data). */
data class MergedClient(val client: ClientActivityOut, val agent: ClientOut?)

/** What a client loads now (WP CLIENTS-LIVE-1): `live_now`, else `loading_now`. */
fun currentLoading(client: ClientActivityOut): LoadingNowOut? = client.live_now ?: client.loading_now

/**
 * Merge the agent rows into the overview rows (web `mergeAgentData`):
 * equal `client_id` only; an agent the overview does not list becomes a
 * card of its own with no traffic figures (never zeros). A `null` agent
 * list (no answer yet) leaves the overview as sent.
 */
fun mergeAgentData(overviewClients: List<ClientActivityOut>, agentClients: List<ClientOut>?): List<MergedClient> {
    val agents = LinkedHashMap<String, ClientOut>()
    for (a in agentClients.orEmpty()) {
        if (a.client_id.isNotEmpty() && !agents.containsKey(a.client_id)) agents[a.client_id] = a
    }
    val used = HashSet<String>()
    val merged = overviewClients.map { row ->
        val id = row.client_id
        if (!isAgentClient(row) || id == null) return@map MergedClient(row, null)
        val agent = agents[id]
        if (agent != null) used.add(id)
        MergedClient(row, agent)
    }.toMutableList()
    for ((id, a) in agents) {
        if (id in used) continue
        val known = a.presence == "online" || a.presence == "offline"
        merged.add(
            MergedClient(
                ClientActivityOut(
                    key = "agent:$id",
                    kind = "agent",
                    client_id = id,
                    addresses = a.source_addrs,
                    connected = if (known) a.presence == "online" else null,
                    agent_presence = if (known) a.presence else null,
                    cache_active = null,
                    last_active = a.last_reported_at,
                    last_seen_in_cache_log = a.last_seen_in_cache_log,
                    last_reported_at = a.last_reported_at,
                    installed_appids = null,
                    installed_app_count = a.app_count,
                    stats = null,
                    loading_now = null,
                    live_now = null,
                    recent_apps = null,
                    recent_unattributed_bytes = null,
                    name = a.name,
                    name_addr = a.name_addr,
                    display_name = a.display_name,
                ),
                a,
            ),
        )
    }
    return merged
}

/**
 * The Clients page's cards (web `paint()`): a PC removed less than
 * [REMOVED_HIDE_MS] ago is hidden from the overview rows AND from the agent
 * rows (web `hiddenRemoved` / `agentSnapshot`), then the two are merged.
 * MUTATION TARGET (review S2: the agent side alone would bring the PC back
 * as an agent-only card).
 */
fun visibleClients(
    overviewClients: List<ClientActivityOut>,
    agentClients: List<ClientOut>?,
    removedAt: Map<String, Long>,
    nowMs: Long,
): List<MergedClient> {
    val rows = overviewClients.filter { !(isAgentClient(it) && hiddenRemoved(it.client_id, removedAt, nowMs)) }
    val agents = agentClients?.filter { !hiddenRemoved(it.client_id, removedAt, nowMs) }
    return mergeAgentData(rows, agents)
}

// ---- versions (web agent-install.js `isReleaseVersion`, CLIENTS-3) --------

private val RELEASE_VERSION = Regex("^[0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z]+(?:\\.[0-9A-Za-z]+)*)?$")

/** True when [version] has the release grammar (`0.1.0`, `0.1.0-rc19.3`; not `dev`). */
fun isReleaseVersion(version: String?): Boolean = version != null && RELEASE_VERSION.matches(version)

/** Outdated = both versions in the release grammar and different. MUTATION TARGET. */
fun isAgentOutdated(agentVersion: String?, serverVersion: String?): Boolean {
    val a = agentVersion?.trim().orEmpty()
    val s = serverVersion?.trim().orEmpty()
    return isReleaseVersion(a) && isReleaseVersion(s) && a != s
}

data class OutdatedAgent(val agent: String, val server: String)

fun outdatedAgentInfo(merged: MergedClient, serverVersion: String?): OutdatedAgent? {
    val agent = merged.agent?.agent_version
    if (!isAgentOutdated(agent, serverVersion)) return null
    return OutdatedAgent(agent!!.trim(), serverVersion!!.trim())
}

fun outdatedAgentText(info: OutdatedAgent): String =
    "Agent ${info.agent} · server ${info.server}. Update vault-agent on this PC to match the server."

/** Heading of the outdated-agent box ("differs", not "older": CLIENTS-3). */
const val OUTDATED_BOX_TITLE = "Agent version differs from the server"

/** The outdated pill on a card. */
const val AGENT_OUTDATED_PILL = "Agent outdated"

// ---- pill, now box, figures ----------------------------------------------

/** The pill looks of the kit (`.pill` kinds running/cached/notinuse/unknown). */
enum class PillLook { RUNNING, ONLINE, OFFLINE, UNKNOWN }

data class CardPill(val text: String, val look: PillLook)

/**
 * Status pill (web `connectionPill`): runner and address read
 * "Downloading" while loading; an agent reads the server's presence
 * verdict, never computed here.
 */
fun connectionPill(client: ClientActivityOut): CardPill {
    val now = currentLoading(client)
    if (!isAgentClient(client) && now != null && now.loading) return CardPill("Downloading", PillLook.RUNNING)
    var on = client.connected
    if (isAgentClient(client) && (client.agent_presence == "online" || client.agent_presence == "offline")) {
        on = client.agent_presence == "online"
    }
    return when (on) {
        true -> CardPill("Online", PillLook.ONLINE)
        false -> CardPill("Offline", PillLook.OFFLINE)
        null -> CardPill("No current data", PillLook.UNKNOWN)
    }
}

/** Text of the dashed box when nothing loads, and when nothing is known. */
const val IDLE_TEXT = "Loading nothing right now"
const val NOW_UNKNOWN_TEXT = "Loading now: $DASH"

/** Zone 2's "now" box: game(s) and speed while loading, else the dashed idle box. */
sealed interface NowBox {
    data class Loading(val title: String, val sub: String, val speed: String) : NowBox
    data class Idle(val text: String) : NowBox
}

fun nowBox(client: ClientActivityOut): NowBox {
    val now = currentLoading(client) ?: return NowBox.Idle(NOW_UNKNOWN_TEXT)
    if (!now.loading) return NowBox.Idle(IDLE_TEXT)
    val apps = now.apps.map { appLabel(it.appid, it.name) }
    return NowBox.Loading(
        title = if (apps.isNotEmpty()) apps.joinToString(", ") else "Content not linked to one game",
        sub = "Loading into the cache",
        speed = formatSpeed(now.bytes_per_second) ?: DASH,
    )
}

/** A figure split for the `value<small>unit</small>` anatomy. */
data class ValueUnit(val value: String, val unit: String)

private val PERCENT = Regex("^(\\d+)%$")
private val NUMBER_UNIT = Regex("^(<?\\s?[\\d.]+)\\s([A-Za-z/]+)$")

/** "38.2 GB" -> 38.2 + GB, "75%" -> 75 + %, "< 1 MB" -> "< 1" + MB; else whole. */
fun splitValueUnit(text: String?): ValueUnit {
    if (text.isNullOrEmpty()) return ValueUnit(DASH, "")
    PERCENT.matchEntire(text)?.let { return ValueUnit(it.groupValues[1], "%") }
    val m = NUMBER_UNIT.matchEntire(text) ?: return ValueUnit(text, "")
    return ValueUnit(m.groupValues[1], m.groupValues[2])
}

/** Hit rate as a whole percentage for a meter width, or null. */
fun ratePercent(rate: Double?): Int? {
    if (rate == null || !rate.isFinite() || rate < 0) return null
    return floor(minOf(rate, 1.0) * 100 + 0.5).toInt()
}

/** One figure of a card's stat grid; [meter] 0..100 draws the bar. */
data class CardStat(val key: String, val label: String, val value: String, val unit: String, val meter: Int? = null)

/**
 * Zone 2's three figures (web `cardStats`). Agent: Installed, Loaded, Hit
 * rate. Runner and address: Loaded, Hit rate, Unlinked.
 */
fun cardStats(client: ClientActivityOut): List<CardStat> {
    val s = client.stats
    val loadedSplit = splitValueUnit(s?.let { formatTrafficBytes(it.bytes_served) })
    val loaded = CardStat("loaded", "Loaded", loadedSplit.value, loadedSplit.unit)
    val pct = s?.let { ratePercent(it.hit_rate) }
    val rate = if (pct == null) CardStat("hitrate", "Hit rate", DASH, "") else CardStat("hitrate", "Hit rate", pct.toString(), "%", pct)
    if (isAgentClient(client)) {
        val n = client.installed_app_count
        val installed = if (n != null) {
            CardStat("installed", "Installed", n.toString(), if (n == 1) "game" else "games")
        } else {
            CardStat("installed", "Installed", DASH, "")
        }
        return listOf(installed, loaded, rate)
    }
    val un = splitValueUnit(formatTrafficBytes(client.recent_unattributed_bytes))
    return listOf(loaded, rate, CardStat("unlinked", "Unlinked", un.value, un.unit))
}

data class RecentChip(val name: String, val size: String?)

data class RecentChips(val chips: List<RecentChip>, val more: Int)

/** Zone 3: top recently loaded games plus "+N more"; `null` = no list sent. */
fun recentChips(client: ClientActivityOut): RecentChips? {
    val apps = client.recent_apps ?: return null
    return RecentChips(
        chips = apps.take(RECENT_APPS_SHOWN).map { RecentChip(appLabel(it.appid, it.name), formatTrafficBytes(it.bytes_served)) },
        more = maxOf(0, apps.size - RECENT_APPS_SHOWN),
    )
}

fun moreChipText(n: Int): String = "+$n more"

/** Zone 3 with an empty list. */
fun noRecentText(range: String): String {
    val phrase = RANGE_PHRASES[range]
    return if (phrase != null) "Nothing loaded $phrase" else "Nothing loaded"
}

/** Zone 3's heading. */
const val RECENT_HEADING = "Recently loaded"

/** A figure or list the server sent no statistics for (feed disabled). */
const val NO_STATS_TEXT = "No traffic statistics"

// ---- agent health (web clients-view.js presenceLine / describeBypassClient) --

/** "last seen 4 min ago" (words only, never a presence decision). */
fun lastSeenText(agent: ClientOut, nowMs: Long): String {
    val ago = formatAgo(agent.last_reported_at, nowMs)
    return if (ago != null) "last seen $ago" else "last seen: not reported"
}

/** "agent 0.1.0", or "version not reported". */
fun agentVersionText(agent: ClientOut): String {
    val v = agent.agent_version?.trim()
    return if (!v.isNullOrEmpty()) "agent $v" else "version not reported"
}

fun presenceLine(agent: ClientOut, nowMs: Long): String = "${lastSeenText(agent, nowMs)} · ${agentVersionText(agent)}"

/** The careful bypass observation (never a cause). */
fun describeBypassClient(agent: ClientOut): String {
    val n = agent.app_count
    val games = if (n == null) "game count not reported" else "$n game${if (n == 1) "" else "s"} reported"
    return "$games · none of its downloads have reached the cache recently"
}

/** The bypass hint's toggle (web `<details>` summary). */
const val BYPASS_HINT_SUMMARY = "What does this mean?"

data class AgentHealth(val bypass: Boolean, val head: String, val text: String)

/** The agent health row (agent data only): "Healthy" or "Bypassing the cache". */
fun agentHealth(agent: ClientOut?, nowMs: Long): AgentHealth? {
    if (agent == null) return null
    if (agent.bypass_suspected) return AgentHealth(true, "Bypassing the cache", describeBypassClient(agent))
    return AgentHealth(false, "Healthy", presenceLine(agent, nowMs))
}

/** Footer note for an agent card's bytes not linked to one game. */
fun unlinkedNote(client: ClientActivityOut): String? {
    if (!isAgentClient(client)) return null
    val b = client.recent_unattributed_bytes
    val text = formatTrafficBytes(b)
    return if (text != null && b != null && b > 0) "$text not linked to a game" else null
}

// ---- the card ---------------------------------------------------------------

/** The card's kind icon (web `CLIENT_ICONS`). */
enum class ClientKind { AGENT, RUNNER, ADDRESS }

/** Everything one card shows (web `clientCardModel`). */
data class ClientCardView(
    val key: String,
    val kind: ClientKind,
    val name: String,
    val meta: String,
    val pill: CardPill,
    /** The outdated box text; `null` = not outdated (no pill, no box). */
    val outdatedText: String?,
    val health: AgentHealth?,
    val now: NowBox,
    val stats: List<CardStat>,
    val recent: RecentChips?,
    val noRecent: String,
    val lastActive: String,
    val unlinked: String?,
    val rename: Boolean,
    /** The client_id the card's Remove addresses; `null` = no Remove button. */
    val removeId: String?,
    /** An agent card whose id the remove route cannot address: the note instead. */
    val removeBlocked: Boolean,
    /** The last failed remove's message; `null` = none. */
    val removeError: String?,
)

fun clientCardView(
    merged: MergedClient,
    range: String,
    serverVersion: String?,
    nowMs: Long,
    removeError: String? = null,
): ClientCardView {
    val client = merged.client
    val agent = merged.agent
    val outdated = outdatedAgentInfo(merged, serverVersion)
    val removeId = agent?.client_id
    val removable = isRemovableClientId(removeId)
    return ClientCardView(
        key = client.key,
        kind = when {
            isAgentClient(client) -> ClientKind.AGENT
            isRunnerClient(client) -> ClientKind.RUNNER
            else -> ClientKind.ADDRESS
        },
        name = clientTitle(client),
        meta = clientKindText(client),
        pill = connectionPill(client),
        outdatedText = outdated?.let { outdatedAgentText(it) },
        health = agentHealth(agent, nowMs),
        now = nowBox(client),
        stats = cardStats(client),
        recent = recentChips(client),
        noRecent = noRecentText(range),
        lastActive = lastActiveText(client, nowMs),
        unlinked = unlinkedNote(client),
        rename = canRename(client),
        removeId = if (removable) removeId else null,
        removeBlocked = agent != null && !removable,
        removeError = if (agent != null) removeError else null,
    )
}

// ---- summary tiles -----------------------------------------------------------

/** One of the page's four tiles (web `summaryTiles`). */
data class SummaryTile(
    val key: String,
    val label: String,
    val value: String,
    val unit: String,
    val detail: String,
    val meter: Int? = null,
    /** Agents tile: outdated count (`null` = server version unknown). */
    val outdated: Int? = null,
    val hi: Boolean = false,
)

/** Request hit rate over several clients: hits / (hits + misses), or null. */
fun combinedHitRate(clients: List<ClientActivityOut>): Double? {
    var hits = 0L
    var misses = 0L
    for (c in clients) {
        val s = c.stats ?: continue
        if (s.hits > 0) hits += s.hits
        if (s.misses > 0) misses += s.misses
    }
    return if (hits + misses > 0) hits.toDouble() / (hits + misses) else null
}

fun outdatedAgentsPillText(n: Int): String = "$n outdated"

/** The fixed four tiles: Connected, Loaded, Hit rate, Agents. */
fun summaryTiles(clients: List<MergedClient>, range: String, serverVersion: String?): List<SummaryTile> {
    val list = clients.map { it.client }
    val connected = list.count { it.connected == true }
    val loading = list.count { currentLoading(it)?.loading == true }
    val tiles = mutableListOf<SummaryTile>()
    tiles.add(
        SummaryTile(
            key = "connected",
            label = "Connected",
            value = connected.toString(),
            unit = "of ${list.size}",
            detail = if (loading > 0) "$loading loading now" else "Nothing loading now",
        ),
    )
    val withStats = list.filter { it.stats != null }
    val phrase = RANGE_PHRASES[range] ?: ""
    if (withStats.isNotEmpty()) {
        val total = withStats.sumOf { it.stats!!.bytes_served }
        val split = splitValueUnit(formatTrafficBytes(total))
        tiles.add(SummaryTile("loaded", "Loaded", split.value, split.unit, phrase))
    } else {
        tiles.add(SummaryTile("loaded", "Loaded", DASH, "", NO_STATS_TEXT))
    }
    val pct = ratePercent(combinedHitRate(withStats))
    if (pct != null) {
        tiles.add(SummaryTile("hitrate", "Hit rate", pct.toString(), "%", "from the cache", meter = pct))
    } else {
        tiles.add(SummaryTile("hitrate", "Hit rate", DASH, "", if (withStats.isNotEmpty()) "No cache requests yet" else NO_STATS_TEXT))
    }
    val agents = clients.filter { isAgentClient(it.client) }
    val online = agents.count { it.client.connected == true }
    val serverKnown = isReleaseVersion(serverVersion?.trim().orEmpty())
    val outdated = if (serverKnown) agents.count { outdatedAgentInfo(it, serverVersion) != null } else null
    val detail = when {
        agents.isEmpty() -> "No agent paired"
        outdated == null -> "${agents.size} paired · server version not known"
        outdated == 0 -> "${agents.size} paired · all match the server"
        else -> "${agents.size} paired"
    }
    tiles.add(SummaryTile("agents", "Agents", online.toString(), "online", detail, outdated = outdated, hi = (outdated ?: 0) > 0))
    return tiles
}

/** The section label above the cards (kit "Devices"). */
const val DEVICES_HEADING = "Devices"
