package dev.steamvault.app.ui.clients

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/**
 * Cross-frontend wording contract (WP 4b.10 brief: "port the web semantics
 * rather than re-deriving them... cross-frontend drift here is a defect —
 * hold that standard", referring to the 12/12 parity-mutation bar
 * `docs/LEARNINGS.md`'s Android section records for WP 4b.4/4b.5/4b.6).
 *
 * Every expected value below is a LITERAL, hand-transcribed from
 * `web/js/lib/clients-view.js` at the time of this WP — never derived from
 * `strings.xml` itself (same "a derived round-trip is circular" rule
 * `StatusIconCrossFrontendContractTest` already applies to the status-word
 * table). Reading the web source file at test time is out of scope for
 * this `app/`-only WP; these literals are the intended manual-sync point if
 * the web wording ever changes (same precedent that file records).
 */
class ClientsCrossFrontendContractTest {

    @Test
    fun `section headings match clients-view partitionClients's own section words`() {
        val xml = readResFile("strings.xml")
        assertEquals("Bypassing", extractStringResource(xml, "clients_section_bypassing"))
        assertEquals("Healthy", extractStringResource(xml, "clients_section_healthy"))
    }

    @Test
    fun `BYPASS_EXPLANATION is a verbatim, not-accusing port`() {
        val xml = readResFile("strings.xml")
        val expected = "This does not necessarily mean anything is wrong. Common causes: " +
            "DNS-over-HTTPS in the browser or OS, the machine resolving Steam's CDN " +
            "over IPv6 (bypassing vault-dns), or simply nothing downloaded yet in the " +
            "current reporting window."
        // strings.xml escapes the apostrophe as \' -- unescape before comparing.
        val actual = extractStringResource(xml, "clients_bypass_explanation").replace("\\'", "'")
        assertEquals(expected, actual)
    }

    @Test
    fun `describeBypassClient's observation-only tail is ported verbatim`() {
        val xml = readResFile("strings.xml")
        assertEquals(
            "none of its downloads have reached the cache recently",
            extractStringResource(xml, "clients_bypass_note"),
        )
    }

    @Test
    fun `the honest fallback wordings match clients-view's own literals`() {
        val xml = readResFile("strings.xml")
        assertEquals("no known address", extractStringResource(xml, "clients_addresses_unknown"))
        assertEquals("game count not reported", extractStringResource(xml, "clients_games_unknown"))
        // WP WEB-FIX-8: presence/last-seen/version words of clients-view.js.
        assertEquals("Not reported", extractStringResource(xml, "clients_presence_unknown"))
        assertEquals("last seen: not reported", extractStringResource(xml, "clients_last_seen_unknown"))
        assertEquals("version not reported", extractStringResource(xml, "clients_agent_version_unknown"))
        assertEquals("nothing served yet", extractStringResource(xml, "clients_bytes_none"))
        assertEquals("no cache requests yet", extractStringResource(xml, "clients_hit_rate_none"))
    }

    @Test
    fun `the games-reported plural matches the singular-plural split clients-view describes`() {
        val xml = readResFile("strings.xml")
        val plural = extractPluralsBlock(xml, "clients_games_reported")
        assertEquals("%1\$d game reported", extractPluralItem(plural, "one"))
        assertEquals("%1\$d games reported", extractPluralItem(plural, "other"))
    }

    // ---------- helpers (same technique StatusIconCrossFrontendContractTest uses) ----------

    private fun readResFile(fileName: String): String {
        val file = File("src/main/res/values/$fileName")
        check(file.exists()) { "expected resource file at ${file.absolutePath}" }
        return file.readText(Charsets.UTF_8)
    }

    private fun extractStringResource(xml: String, name: String): String {
        val regex = Regex("<string name=\"$name\"[^>]*>(.*?)</string>")
        val match = regex.find(xml) ?: error("no <string name=\"$name\"> found")
        return match.groupValues[1]
    }

    private fun extractPluralsBlock(xml: String, name: String): String {
        val regex = Regex("<plurals name=\"$name\">(.*?)</plurals>", RegexOption.DOT_MATCHES_ALL)
        val match = regex.find(xml) ?: error("no <plurals name=\"$name\"> found")
        return match.groupValues[1]
    }

    private fun extractPluralItem(pluralsBlock: String, quantity: String): String {
        val regex = Regex("<item quantity=\"$quantity\">(.*?)</item>")
        val match = regex.find(pluralsBlock) ?: error("no <item quantity=\"$quantity\"> found")
        return match.groupValues[1]
    }
}
