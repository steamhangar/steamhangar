package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.net.model.ClientActivityOut
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.net.model.LoadingAppOut
import dev.steamvault.app.net.model.RecentAppOut
import dev.steamvault.app.ui.library.logic.UNKNOWN_APP_NAME
import dev.steamvault.app.ui.traffic.logic.DASH
import dev.steamvault.app.ui.traffic.logic.FeedState
import dev.steamvault.app.ui.traffic.logic.RANGE_PHRASES
import dev.steamvault.app.ui.traffic.logic.feedState
import dev.steamvault.app.ui.traffic.logic.formatRate
import dev.steamvault.app.ui.traffic.logic.formatSpeed
import dev.steamvault.app.ui.traffic.logic.formatTrafficBytes

/**
 * Clients page presentation (WP D15d) -- a Kotlin port of
 * `web/js/lib/clients-page.js` (WP D15c), one card per entry of
 * `GET /v1/clients/overview` (WP D15b): agent PCs, agentless addresses and
 * the server's own prefill runner (WP D15-RUNNER, `kind: "runner"`).
 * Pure: `ui/clients/ClientsScreen.kt` draws the cards from these strings.
 *
 * **Verbatim-port exception (app/README.md "String resources").** Every
 * user-visible literal is the web module's own, pinned by string equality
 * in `ClientsPageTest` and against the web source in `TrafficWebTwinTest`.
 *
 * Wording rules (web's):
 *  - The card title is the server's `display_name` (WP D15-NAMES): the
 *    operator-set name when there is one, else the agent's PC name, else
 *    the address. A server older than D15-NAMES sends none; the title then
 *    falls back to the pre-D15-NAMES rule. The page never invents a name.
 *  - When an operator-set name replaces the title, the line under it keeps
 *    what it replaced (the agent's PC name, or the address).
 *  - An address client is "No agent · seen in the cache log" and nothing
 *    more: no line may claim the address is a PC, a person or a console.
 *  - The prefill runner is the server itself downloading; its title is the
 *    server's `display_name` ("Prefill runner") and the line under it says
 *    so. It is not an agentless machine, so it does not raise the agentless
 *    note. Renaming (and its runner exclusion) is web-only for now.
 *  - "Installed" exists for agent clients only (`installed_app_count`).
 *  - A `null` the server sent (feed not `ok`) shows the dash; a real zero
 *    prints.
 */

/** How many recently loaded apps a card names before "+N more". */
const val RECENT_APPS_SHOWN = 3

fun isAgentClient(client: ClientActivityOut): Boolean = client.kind == "agent"

/** WP D15-RUNNER: the server's own prefill runner. */
fun isRunnerClient(client: ClientActivityOut): Boolean = client.kind == "runner"

/** An agentless address (neither an agent nor the prefill runner). */
fun isAddressClient(client: ClientActivityOut): Boolean = !isAgentClient(client) && !isRunnerClient(client)

/**
 * Wrap operator-chosen text in Unicode first-strong isolates (FSI ... PDI,
 * web `client-names.js` `isolate`) so a right-to-left name cannot reorder
 * the words around it. Escapes on purpose: no invisible characters in the
 * source.
 */
fun isolate(text: String): String = "\u2068$text\u2069"

/** The card's title: the server's `display_name` (an operator-set name
 * wins), else the agent's client_id, or the address. */
fun clientName(client: ClientActivityOut): String {
    if (!client.display_name.isNullOrEmpty()) return client.display_name
    if (isAgentClient(client) && !client.client_id.isNullOrEmpty()) return client.client_id
    client.addresses.firstOrNull { it.isNotEmpty() }?.let { return it }
    val key = client.key
    return if (key.startsWith("addr:")) key.removePrefix("addr:") else key.ifEmpty { DASH }
}

/** The line under the title: what kind of client this is and its addresses. */
fun clientKindText(client: ClientActivityOut): String {
    val named = !client.name.isNullOrEmpty()
    val addrs = client.addresses.filter { it.isNotEmpty() }
    if (isRunnerClient(client)) {
        return if (addrs.isNotEmpty()) "This server's prefill downloads · ${addrs.joinToString(", ")}" else "This server's prefill downloads"
    }
    if (!isAgentClient(client)) {
        return if (named && addrs.isNotEmpty()) {
            "No agent · ${addrs.joinToString(", ")} · seen in the cache log"
        } else {
            "No agent · seen in the cache log"
        }
    }
    val agent = if (named && !client.client_id.isNullOrEmpty()) "vault-agent on ${isolate(client.client_id)}" else "vault-agent"
    return if (addrs.isNotEmpty()) "$agent · ${addrs.joinToString(", ")}" else "$agent · no address in the cache log"
}

/** The connection chip's three looks (web `.pchip-on/-off/-unknown`). */
enum class ConnectionLook { ON, OFF, UNKNOWN }

data class ConnectionChip(val word: String, val look: ConnectionLook)

fun connectionChip(client: ClientActivityOut): ConnectionChip {
    // "Agent" wording only for a vault-agent client; an address has no agent.
    val agent = isAgentClient(client)
    return when (client.connected) {
        true -> ConnectionChip(if (agent) "Agent online" else "Loading", ConnectionLook.ON)
        false -> ConnectionChip(if (agent) "Agent offline" else "No agent", ConnectionLook.OFF)
        null -> ConnectionChip("No current data", ConnectionLook.UNKNOWN)
    }
}

/** Name of an app in a list: the server's name, else "App <id>". */
fun appLabel(appid: Int, name: String?): String = if (!name.isNullOrBlank()) name else UNKNOWN_APP_NAME

private fun LoadingAppOut.label() = appLabel(appid, name)
private fun RecentAppOut.label() = appLabel(appid, name)

/** "Loading now" line. */
fun loadingNowText(client: ClientActivityOut): String {
    val now = client.loading_now ?: return "Loading now: $DASH"
    if (!now.loading) return "Loading now: nothing"
    val speed = formatSpeed(now.bytes_per_second) ?: DASH
    val apps = now.apps.map { it.label() }
    return if (apps.isNotEmpty()) "Loading now: $speed · ${apps.joinToString(", ")}" else "Loading now: $speed · content not linked to one game"
}

/** Installed games line; agent clients only (`null` for an address). */
fun installedText(client: ClientActivityOut): String? {
    if (!isAgentClient(client)) return null
    val n = client.installed_app_count ?: return "Installed games: not reported"
    return "$n game${if (n == 1) "" else "s"} installed"
}

/** Bytes and hit rate over the selected range; `null` with no stats (feed
 * disabled). */
fun statsText(client: ClientActivityOut, range: String): String? {
    val s = client.stats ?: return null
    val loaded = formatTrafficBytes(s.bytes_served) ?: DASH
    val rate = formatRate(s.hit_rate)
    val phrase = RANGE_PHRASES[range] ?: ""
    return "$loaded loaded $phrase" + (if (rate != null) " · hit rate $rate" else "")
}

/** The top recently loaded apps; `null` when the server sent none (feed
 * disabled) or the range has nothing. */
fun recentAppsText(client: ClientActivityOut): String? {
    val apps = client.recent_apps ?: return null
    val parts = apps.take(RECENT_APPS_SHOWN).map { a ->
        val size = formatTrafficBytes(a.bytes_served)
        if (size != null) "${a.label()} ($size)" else a.label()
    }.toMutableList()
    val more = apps.size - RECENT_APPS_SHOWN
    if (more > 0) parts.add("+$more more")
    val unattributed = client.recent_unattributed_bytes
    val other = formatTrafficBytes(unattributed)
    if (other != null && unattributed != null && unattributed > 0) parts.add("$other not linked to one game")
    return if (parts.isNotEmpty()) "Loaded recently: ${parts.joinToString(", ")}" else null
}

/** "Last active 4 min ago". */
fun lastActiveText(client: ClientActivityOut, nowMs: Long = System.currentTimeMillis()): String {
    val ago = formatAgo(client.last_active, nowMs)
    return if (ago != null) "Last active $ago" else "Last active: never"
}

/** The page's sub-line: how many clients, how many connected. */
fun clientsSummaryText(clients: List<ClientActivityOut>): String {
    val connected = clients.count { it.connected == true }
    return "${clients.size} client${if (clients.size == 1) "" else "s"} · $connected connected"
}

/** Note under the list, for the agentless rows. */
const val AGENTLESS_NOTE =
    "Machines without vault-agent show their address and cache traffic only. A PC with vault-agent can also show " +
        "up here by its address until the server can match the two."

/** Empty list: nothing reported and nothing in the cache log. */
const val NO_CLIENTS_TEXT = "No clients to show. A machine appears here once it loads through the cache or runs vault-agent."

/** The list's line before the first answer (web `views/clients.js`). */
const val LOADING_CLIENTS_TEXT = "Loading clients…"

/** Prefix of the note when the first load fails (web `views/clients.js`). */
const val COULD_NOT_LOAD_CLIENTS = "Could not load clients"

/** The Clients page has figures to range over unless the feed is off. */
fun clientsHaveFigures(overview: ClientsOverviewOut): Boolean = feedState(overview.event_feed) != FeedState.DISABLED

/** The note under the list shows while any card is an agentless address
 * (the prefill runner is not one). */
fun showAgentlessNote(clients: List<ClientActivityOut>): Boolean = clients.any { isAddressClient(it) }

/** Everything one card shows, in order; `null` lines are left out. */
data class ClientCardModel(
    val key: String,
    val name: String,
    val kindText: String,
    val chip: ConnectionChip,
    val loadingNow: String,
    val installed: String?,
    val stats: String?,
    val recent: String?,
    val lastActive: String,
)

fun buildClientCardModel(client: ClientActivityOut, range: String, nowMs: Long = System.currentTimeMillis()): ClientCardModel =
    ClientCardModel(
        key = client.key,
        name = clientName(client),
        kindText = clientKindText(client),
        chip = connectionChip(client),
        loadingNow = loadingNowText(client),
        installed = installedText(client),
        stats = statsText(client, range),
        recent = recentAppsText(client),
        lastActive = lastActiveText(client, nowMs),
    )
