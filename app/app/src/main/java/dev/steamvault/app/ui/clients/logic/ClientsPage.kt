package dev.steamvault.app.ui.clients.logic

import dev.steamvault.app.PRODUCT_NAME
import dev.steamvault.app.net.model.ClientActivityOut
import dev.steamvault.app.net.model.ClientsOverviewOut
import dev.steamvault.app.ui.library.logic.UNKNOWN_APP_NAME
import dev.steamvault.app.ui.traffic.logic.DASH
import dev.steamvault.app.ui.traffic.logic.FeedState
import dev.steamvault.app.ui.traffic.logic.feedState

/**
 * Clients page presentation (WP D15d) -- a Kotlin port of
 * `web/js/lib/clients-page.js` (WP D15c), one card per entry of
 * `GET /v1/clients/overview` (WP D15b): agent PCs, agentless addresses and
 * the server's own prefill runner (WP D15-RUNNER, `kind: "runner"`).
 * Pure. Since WP ANDROID-REDESIGN-1a the card itself is `ClientCard.kt`'s
 * [ClientCardView] (web CLIENTS-1..4); this file keeps the title, kind
 * line, summary and note texts the card and page use.
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
 *    product's name ([clientTitle], WP RUNNER-NAME-1: [PRODUCT_NAME], not
 *    the server's `display_name` "Prefill runner") and the line under it
 *    says it is this server's prefill downloads. It is not an agentless machine, so it does not raise the agentless
 *    note. It cannot be renamed (the server sends no `name_addr`).
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

/**
 * WP RUNNER-NAME-1 (web `clientTitle`): the title a card shows. The prefill
 * runner is the server itself, so it is titled [PRODUCT_NAME], whatever
 * `display_name` the server sent ("Prefill runner"; the runner cannot be
 * renamed); every other client keeps [clientName].
 */
fun clientTitle(client: ClientActivityOut): String = if (isRunnerClient(client)) PRODUCT_NAME else clientName(client)

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

/** Name of an app in a list: the server's name, else the neutral [UNKNOWN_APP_NAME] (never a bare id). */
fun appLabel(appid: Int, name: String?): String = if (!name.isNullOrBlank()) name else UNKNOWN_APP_NAME

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
