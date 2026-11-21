package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.ui.status.StatusKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * ANDROID-INSTALLED-WORDING-1: [installedSpokenWording], the twin of web
 * `installedAriaFragment` (components/game-card.js). A not-cached game whose
 * row status is already "Not cached" ([StatusKind.NONE]) is spoken as plain
 * "Installed on <names>", so "not cached" is not said twice; any other kind
 * keeps the full not-cached wording.
 */
class InstalledSpokenWordingTest {

    private val display = InstalledOnDisplay(primaryClientId = "gaming-pc", primaryReportedAt = "2026-08-22T09:15:03Z", additionalClientCount = 0)

    @Test
    fun `MUTATION TARGET -- not cached with a Not cached status word is spoken as plain installed on`() {
        assertEquals(
            InstalledSpokenWording.INSTALLED_ON,
            installedSpokenWording(InstalledBadge.InstalledNotCached(display), StatusKind.NONE),
        )
    }

    @Test
    fun `not cached with any other status word keeps the not-cached wording`() {
        for (kind in StatusKind.entries.filter { it != StatusKind.NONE }) {
            assertEquals(
                "kind $kind",
                InstalledSpokenWording.INSTALLED_NOT_CACHED,
                installedSpokenWording(InstalledBadge.InstalledNotCached(display), kind),
            )
        }
    }

    @Test
    fun `cached is always installed on, no signal is always nothing`() {
        for (kind in StatusKind.entries) {
            assertEquals("kind $kind", InstalledSpokenWording.INSTALLED_ON, installedSpokenWording(InstalledBadge.InstalledAndCached(display), kind))
            assertEquals("kind $kind", InstalledSpokenWording.NONE, installedSpokenWording(InstalledBadge.NoSignal, kind))
        }
    }

    @Test
    fun `the web makes the same exception`() {
        val web = File("../../web/js/components/game-card.js")
        check(web.exists()) { "No file at ${web.absolutePath}: MOVED -> update this path." }
        val text = web.readText(Charsets.UTF_8)
        for (line in WEB_LINES) {
            assertTrue(
                "web components/game-card.js installedAriaFragment no longer contains `$line` -- if the web " +
                    "rule or wording changed, update installedSpokenWording and WEB_LINES; if only the line's " +
                    "code changed (names, quotes), update WEB_LINES only",
                text.contains(line),
            )
        }
    }

    private companion object {
        /** The web exception, line by line (installedAriaFragment). */
        val WEB_LINES = listOf(
            "if (state === INSTALLED_BADGE.NOT_CACHED && kind === KIND.NONE) {",
            "return `Installed on \${names}`;",
        )
    }
}
