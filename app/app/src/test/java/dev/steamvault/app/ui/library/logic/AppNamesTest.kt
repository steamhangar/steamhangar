package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.ui.clients.logic.appLabel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/** WP ID-AUDIT: twin of web `tests/id-audit-names.test.js`. */
class AppNamesTest {
    @Test
    fun fallbackIsTheNeutralWebString() {
        assertEquals("Unknown app", UNKNOWN_APP_NAME)
        assertEquals("Content package", CONTENT_PACKAGE_NAME)
        assertFalse(UNKNOWN_APP_NAME.any { it.isDigit() })
    }

    @Test
    fun titleHelpersNeverPrintABareId() {
        assertEquals(UNKNOWN_APP_NAME, appLabel(3419430, null))
        assertEquals(UNKNOWN_APP_NAME, appLabel(3419430, "  "))
        assertEquals(UNKNOWN_APP_NAME, appTitle(1962700, null, null))
        assertEquals("Real Game", appLabel(1, "Real Game"))
        assertEquals("Owned Name", appTitle(1, null, "Owned Name"))
    }
}
