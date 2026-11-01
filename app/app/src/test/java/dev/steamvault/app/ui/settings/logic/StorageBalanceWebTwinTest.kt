package dev.steamvault.app.ui.settings.logic

import dev.steamvault.app.net.model.CacheSummaryOut
import dev.steamvault.app.net.model.StorageBalanceOut
import dev.steamvault.app.net.model.UnmappedDepotsOut
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

/**
 * Twin pin for WP ANDROID-RC21-1-TWINS: the storage balance card's words
 * and rules must still be the web's (`web/js/lib/storage-balance.js`, WP
 * STORAGE-BALANCE-1). The Kotlin side of each word is taken from the
 * Kotlin port itself (its constants and a rendered view), so this test
 * pins Kotlin == web directly. VALUE drift -> update the Kotlin port;
 * GRAMMAR drift (the web changed the string form) -> adjust this test.
 * Never edit the web literals to make this pass.
 */
class StorageBalanceWebTwinTest {

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

    private val gb = 1L shl 30

    /** A balance that renders every tile, the residual one included. */
    private val view = storageBalanceView(
        CacheSummaryOut(
            total_bytes = 1,
            unmapped_depots = UnmappedDepotsOut(0, 0),
            storage_balance = StorageBalanceOut(
                on_disk_bytes = 100 * gb,
                planned_bytes = 70 * gb,
                present_bytes = 60 * gb,
                measured_depots = 3,
                old_builds_bytes = 20 * gb,
                unmeasured_bytes = 15 * gb,
                unmeasured_depots = 2,
                unmapped_bytes = 5 * gb,
                unmapped_depots = 1,
                unexplained_bytes = -1,
                warning = false,
            ),
        ),
    )!!

    @Test
    fun `storage-balance literals`() {
        assertQuoted(
            "lib/storage-balance.js",
            listOf(
                StorageBalanceText.TITLE,
                StorageBalanceText.NOTE,
                StorageBalanceText.ACCOUNTED,
                StorageBalanceText.WARNING_FALLBACK,
                "1 depot",
            ) + view.tiles.flatMap { listOf(it.key, it.label) } +
                view.tiles.filter { it.key != "current" }.map { it.note },
        )
        assertFragments(
            "lib/storage-balance.js",
            listOf(
                // The current-builds note (Kotlin: the same words around the two figures).
                "`On disk, of the \${size(b.planned_bytes)} the current builds of the measured games need (\${depots(b.measured_depots)}).`",
                "return n === 1 ? \"1 depot\" : `\${n} depots`;",
                // The residual is signed with U+2212, sizes never are.
                "if (bytes < 0) return `−\${formatBytesGBOrZero(-bytes)}`;",
                // Only `warning === true` warns; a blank reason falls back.
                "const warning = b.warning === true",
                "typeof b.warning_reason === \"string\" && b.warning_reason.trim() ? b.warning_reason.trim() : STORAGE_BALANCE_WARNING_FALLBACK",
                // The Unexplained tile only while the residual is not 0.
                "const accounted = b.unexplained_bytes === 0;",
                // Old builds and the residual are highlighted while warning.
                "hi: warning !== null,",
                // Only the residual may be negative.
                "if (f !== \"unexplained_bytes\" && b[f] < 0) return null;",
            ),
        )
        // The tile order (web `tiles` array, Unexplained last).
        val order = view.tiles.map { "key: \"${it.key}\"" }
        val js = web("lib/storage-balance.js")
        val positions = order.map { js.indexOf(it) }
        if (positions.any { it < 0 } || positions != positions.sorted()) fail("VALUE DRIFT: the web tile order is no longer $order")
    }

    @Test
    fun `the Expert panel note and the card's place`() {
        assertFragments(
            "views/settings.js",
            listOf(
                "const note = view.accounted ? `\${STORAGE_BALANCE_ACCOUNTED} \${STORAGE_BALANCE_NOTE}` : STORAGE_BALANCE_NOTE;",
                "panel.appendChild(buildStorageBalanceCard());",
                "balanceCard.card.hidden = view === null;",
            ),
        )
        assertQuoted("api.js", listOf("/v1/cache/summary"))
    }
}
