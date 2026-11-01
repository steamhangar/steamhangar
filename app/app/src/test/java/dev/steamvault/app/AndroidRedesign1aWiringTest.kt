package dev.steamvault.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pins for WP ANDROID-REDESIGN-1a's model-to-pixel wiring
 * (docs/LEARNINGS.md: "pinning the pure/model layer proves NOTHING about
 * the pixels"). The decisions are pinned in `ClientCardTest`; these pins
 * prove the Clients page merges the agent rows, draws the tiles and the
 * card zones, and wires Remove on the card to the PCs sheet's controller
 * and confirm. Same comment-stripped source scan, strong generation, as
 * `D15dWiringTest`.
 */
class AndroidRedesign1aWiringTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "expected a source file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")

    private val FUN = Regex("""(?:@Composable\s+)?(?:private\s+)?fun\s+(?:<[^>]+>\s+)?(\w+)""")

    private fun nearestEnclosingFun(code: String, callIdx: Int): String? =
        FUN.findAll(code)
            .map { it.range.first to it.groupValues[1] }
            .filter { (start, _) -> start < callIdx }
            .maxByOrNull { (start, _) -> start }
            ?.second

    private fun callIndex(code: String, call: String): Int {
        var from = 0
        while (true) {
            val idx = code.indexOf(call, from)
            if (idx < 0) return -1
            if (!code.substring(0, idx).trimEnd().endsWith("fun")) return idx
            from = idx + call.length
        }
    }

    private fun assertCalledFrom(path: String, call: String, expectedFun: String) {
        val code = stripComments(read(path))
        val idx = callIndex(code, call)
        assertTrue("$path: '$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals(
            "$path: '$call' MOVED -- its nearest enclosing function is no longer $expectedFun",
            expectedFun,
            nearestEnclosingFun(code, idx),
        )
    }

    private val base = "src/main/java/dev/steamvault/app"
    private val main = "$base/MainActivity.kt"
    private val screen = "$base/ui/clients/ClientsScreen.kt"
    private val views = "$base/ui/clients/ClientCardViews.kt"
    private val controller = "$base/ui/clients/ClientsController.kt"

    @Test
    fun `MUTATION PIN -- the page merges the agent rows, hides a just-removed PC and reads the server version`() {
        assertCalledFrom(screen, "clientsController.pollForPage()", "ClientsScreen")
        val code = stripComments(read(screen))
        assertTrue(
            "the agent poll must be the body of repeatOnLifecycle(STARTED)",
            Regex("""repeatOnLifecycle\(Lifecycle\.State\.STARTED\)\s*\{\s*clientsController\.pollForPage\(\)\s*\}""").containsMatchIn(code),
        )
        assertCalledFrom(screen, "visibleClients(data?.clients.orEmpty(), agentRows, removedAt, nowMs)", "ClientsScreen")
        assertCalledFrom(screen, "merged = merged,", "ClientsScreen")
        assertCalledFrom(screen, "if (removedAt.isNotEmpty()) poll.refreshOnce()", "ClientsScreen")
        assertCalledFrom(screen, "serverVersion = readServerVersion(settingsRepository)", "ClientsScreen")
        assertCalledFrom(screen, "settingsRepository.get().server_version", "readServerVersion")
        assertCalledFrom(controller, "removedAt = removedAt.filterValues { now - it <= REMOVED_HIDE_MS } + (clientId to now)", "confirmRemove")
        assertCalledFrom(controller, "refreshOnce()\n            delay(PollingIntervals.CLIENTS_MS)", "pollForPage")
    }

    @Test
    fun `MUTATION PIN -- tiles, devices head, cards and the empty state are drawn`() {
        assertCalledFrom(screen, "summaryTiles(merged, range, serverVersion)", "ClientsBody")
        assertCalledFrom(screen, "item(key = \"tiles\") { if (tiles.isNotEmpty()) SummaryTiles(tiles) }", "ClientsBody")
        // MUTATION PIN (round-3 golden): the tiles item exists before the first
        // answer, so the list never opens scrolled past it.
        assertCalledFrom(screen, "if (!poll.unsupported) {\n            item(key = \"tiles\")", "ClientsBody")
        assertCalledFrom(screen, "clientCardView(it, range, serverVersion, nowMs,", "ClientsBody")
        assertCalledFrom(screen, "SectionLabel(DEVICES_HEADING,", "DevicesHead")
        assertCalledFrom(screen, "cards.isEmpty() -> item(key = \"empty\") { EmptyState() }", "ClientsBody")
        assertCalledFrom(screen, "text = NO_CLIENTS_TEXT,", "EmptyState")
        assertCalledFrom(views, "TileView(tile, Modifier.weight(1f))", "SummaryTiles")
        assertCalledFrom(views, "StatusPill(CardPill(outdatedAgentsPillText(outdated), PillLook.RUNNING), stale = true)", "TileView")
        assertCalledFrom(views, "StatCell(stat, Modifier.weight(1f))", "StatGrid")
        assertCalledFrom(views, "stat.meter?.let { Meter(it", "StatCell")
        assertCalledFrom(views, "if (model.outdatedText != null) StatusPill(CardPill(AGENT_OUTDATED_PILL, PillLook.RUNNING), stale = true)", "ClientCard")
        assertCalledFrom(views, "Text(OUTDATED_BOX_TITLE", "OutdatedBox")
        assertCalledFrom(views, "Text(BYPASS_HINT_SUMMARY", "AgentHealthRow")
        assertCalledFrom(views, "stringResource(R.string.clients_bypass_explanation)", "AgentHealthRow")
        assertCalledFrom(views, "is NowBox.Loading -> Row(", "NowBoxView")
        assertCalledFrom(views, "now.speed,", "NowBoxView")
        assertCalledFrom(views, "moreChipText(recent.more)", "RecentZone")
        assertCalledFrom(views, "recent.chips.isEmpty() -> HintText(model.noRecent)", "RecentZone")
    }

    @Test
    fun `MUTATION PIN -- Remove agent on the card reuses the PCs sheet's controller and confirm`() {
        assertCalledFrom(screen, "controller.requestRemove(card.removeId)", "ClientsBody")
        assertCalledFrom(screen, "if (clientsController != null && !clientsController.isOpen) RemovePcConfirm(clientsController, scope)", "ClientsScreen")
        assertCalledFrom(screen, "controller.clearRemoveNotice()", "RemoveNotice")
        assertCalledFrom(views, "if (showRemove) RemoveAgentButton(model.removeId!!, onRemove!!)", "CardFooter")
        assertCalledFrom(views, "if (model.removeBlocked) HintText(UNREMOVABLE_SLASH_NOTE)", "CardFooter")
        assertCalledFrom(views, "Modifier.semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Assertive }", "CardFooter")
        assertCalledFrom(views, "stringResource(R.string.clients_remove_agent_button_cd, clientId)", "RemoveAgentButton")
        assertCalledFrom(views, "semantics { contentDescription = label }", "RenameButton")
        assertCalledFrom(views, "modifier = modifier.semantics { contentDescription = text },", "SectionLabel")
        val code = stripComments(read(main))
        assertEquals("real and demo Clients pages get the hoisted controller", 2, Regex("""clientsController = clientsControllerState,""").findAll(code).count())
        assertCalledFrom(main, "settingsRepository = remember(client) { VaultSettingsRepository(client) },\n        )", "ClientsDestinationContent")
        assertCalledFrom(main, "settingsRepository = remember(demo) { DemoSettingsRepository(demo) },\n            )", "ClientsDestinationContent")
    }
}
