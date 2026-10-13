package dev.steamvault.app.demo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural pin for demo mode's network isolation (WP APP-DEMO review
 * round 2, blocker B1; narrowed further in round 3, F1). **Replaces the
 * earlier denylist-based `DemoModeNetworkIsolationTest` outright** -- that
 * test pinned seven SPELLINGS ("VaultApiClient", "okhttp3", ...), not
 * isolation itself. The reviewer demonstrated the gap directly: patching
 * [DemoClientsRepository.list] to call
 * `SteamOpenIdClient().checkAuthentication(...)` -- a live OkHttp POST to
 * `https://steamcommunity.com/openid/login`, fired every time the demo
 * clients sheet opens -- left the denylist scan AND the full 606-test suite
 * green, because `net.steam.*` (and every other symbol not on that
 * seven-item list) was simply never checked.
 *
 * The fix inverts the check to an ALLOWLIST: every qualified name `demo/`
 * and `ui/demo/` reference at all -- an `import` line or an inline
 * fully-qualified reference alike -- must fall inside [ALLOWED_PREFIXES],
 * for every name that starts with one of [WATCHED_PREFIXES].
 *
 * **What this actually checks, stated at the strength it actually has (F1
 * fix -- round 2's "fails closed BY CONSTRUCTION... including a class this
 * file's author has never heard of" overstated it, and the reviewer proved
 * the overstatement: `javax.net.SocketFactory` opened a real outbound TCP
 * socket and wrote a byte to it from `DemoClientsRepository.list()`, and
 * this test -- round 2's version, watching only `dev.steamvault.`/`java.`/
 * `okhttp`/`okio`/`coil` -- passed clean, 612/612, because `javax.` was
 * simply never on the list).** This is a FIXED, ENUMERATED denylist of
 * PREFIX FAMILIES ([WATCHED_PREFIXES]) crossed with an allowlist within
 * each -- not an unconditional guarantee. A qualified name rooted in a
 * family not in [WATCHED_PREFIXES] (this round added `javax.` and
 * `android.` after the reviewer's `javax.net.SocketFactory` mutation;
 * nothing else guarantees the NEXT unlisted family does not exist) is
 * invisible to it by the same mechanism that let `javax.` through before
 * this fix. What the test DOES guarantee, and what the reviewer verified
 * by attempting to construct every network-capable class actually reachable
 * from `repo/`: every constructible type in the repository interfaces this
 * package implements takes a `CredentialStore` or `VaultApiClient`
 * argument whose type name is off-allowlist, so reaching one requires a
 * qualified name this scan already catches -- and wildcard imports, type
 * aliases, inline fully-qualified names, and string-literal reflection are
 * all caught too, since the regex matches ANY dotted identifier chain in
 * the code, not just import lines. Call this a strong practical barrier
 * against the concrete network/platform-resource classes this codebase
 * and its dependencies expose today, not a proof against every possible
 * one.
 *
 * **A second, structural -- and UNFIXABLE by this technique -- gap (round
 * 4, Fix 2), distinct from an unlisted prefix family.** [QUALIFIED_NAME]
 * requires at least two dotted segments, so it only ever sees a
 * FULLY-QUALIFIED reference. Kotlin's implicit imports (`kotlin.*`,
 * `kotlin.collections.*`, and on the JVM target, `java.lang.*`) make a
 * whole class of JVM capability reachable with NO dotted name at all: the
 * reviewer measured `ProcessBuilder("/system/bin/ping", ...).start()` and
 * `Runtime.getRuntime().exec(...)` both building and running clean, 611
 * tests, zero failures, added to `DemoClientsRepository.list()` --
 * arbitrary process execution, a capability escape from the very sandbox
 * this test asserts. `ProcessBuilder(` is a bare, unqualified constructor
 * call (no segment to match at all); `Runtime.getRuntime` is rooted at
 * `Runtime`, a single identifier, not at `java.` -- neither is a
 * "qualified name" in the sense this scan understands, for the same
 * reason `println(...)` or `listOf(...)` are not. No amount of ADDING
 * prefixes to [WATCHED_PREFIXES] closes this: it is not a missing entry,
 * it is the technique's structural ceiling, because a fully-qualified-name
 * scan cannot see a name that was never qualified. The correct response is
 * NOT an identifier denylist (`"ProcessBuilder"`, `"Runtime"`, ...) added
 * here -- that would be a new instance of the exact defect class this
 * file's own history already ran through twice (round 2's seven-spelling
 * denylist, round 3's five-prefix-family denylist): a specific, enumerable
 * list standing in for an unconditional-sounding claim, one string short
 * of the next bypass. Recorded as an accepted structural gap instead.
 *
 * `stripComments` matches [DemoModeNetworkIsolationTest]'s original
 * reasoning: KDoc prose is allowed (and, in this package, expected) to NAME
 * a forbidden class when explaining why production code never references
 * it -- see e.g. `DemoRepositories.kt`'s own module kdoc, which says
 * "[dev.steamvault.app.net.VaultApiClient]" in exactly that sense.
 */
class DemoModeImportAllowlistTest {

    private val roots = listOf(
        File("src/main/java/dev/steamvault/app/demo"),
        File("src/main/java/dev/steamvault/app/ui/demo"),
    )

    private fun allKotlinFiles(): List<File> {
        for (root in roots) check(root.exists()) { "expected a demo source root at ${root.absolutePath}" }
        return roots.flatMap { root -> root.walkTopDown().filter { it.isFile && it.extension == "kt" }.toList() }
    }

    /**
     * Everything demo code is allowed to depend on. Deliberately narrow:
     * model/error DTOs (data only), the repository interfaces (the seam
     * itself), Compose/theme/resources (rendering), `kotlinx.serialization`
     * (building the JSON settings shape), and `java.time.Instant`
     * (timestamps). `dev.steamvault.app.net.steam.*`
     * (`SteamOpenIdClient` et al.), `dev.steamvault.app.net.VaultApiClient`
     * itself, `dev.steamvault.app.net.profile.*`, any `coil`/`okhttp`/`okio`
     * class, every OTHER `java.*` package (raw sockets, `java.net.*`,
     * `java.io.*` file access), and everything under `javax.*`/`android.*`
     * (added in round 3, F1 -- `javax.net.SocketFactory` is exactly the
     * gap the reviewer measured) are OUT -- not because anyone enumerated
     * them, but because they are not IN.
     */
    private val ALLOWED_PREFIXES = listOf(
        "kotlinx.serialization.json.",
        "java.time.Instant",
        "dev.steamvault.app.net.model.",
        "dev.steamvault.app.net.error.",
        "dev.steamvault.app.repo.",
        "androidx.compose.",
        "dev.steamvault.app.R",
        "dev.steamvault.app.ui.theme.",
        // same-package cross-references within the two roots this test scans.
        "dev.steamvault.app.demo.",
        "dev.steamvault.app.ui.demo.",
    )

    /**
     * A qualified name matching one of these prefixes, but none of
     * [ALLOWED_PREFIXES], is a violation. Round 2 shipped five families
     * (`dev.steamvault.`/`java.`/`okhttp`/`okio`/`coil`); round 3 (F1) adds
     * `javax.` and `android.` after the reviewer's `javax.net.SocketFactory`
     * mutation passed clean against the round-2 list. This list is
     * enumerated, not derived -- see the class kdoc for exactly what that
     * does and does not guarantee. It is not "everything": Kotlin's own
     * stdlib and Compose's transitive internals are deliberately left
     * unwatched so this test does not also have to allowlist them.
     */
    private val WATCHED_PREFIXES = listOf("dev.steamvault.", "java.", "javax.", "android.", "okhttp", "okio", "coil")

    private val QUALIFIED_NAME = Regex("""\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b""")

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("//.*"), "")
            // The file's OWN `package dev.steamvault.app.demo` declaration
            // is not a reference to anything -- without this, the bare
            // package name (no trailing segment) fails every ALLOWED_PREFIXES
            // check below, which all expect a trailing "." before more path.
            .replace(Regex("(?m)^\\s*package\\s+\\S+\\s*$"), "")

    private fun violationsIn(file: File): List<String> {
        val code = stripComments(file.readText(Charsets.UTF_8))
        val hits = mutableListOf<String>()
        for (match in QUALIFIED_NAME.findAll(code)) {
            val name = match.value
            if (WATCHED_PREFIXES.none { name.startsWith(it) }) continue
            if (ALLOWED_PREFIXES.any { name.startsWith(it) }) continue
            hits.add("${file.name}: $name")
        }
        return hits
    }

    @Test
    fun `MUTATION PIN -- every qualified name demo code references, import or inline, falls inside the fixed allowlist`() {
        val hits = allKotlinFiles().flatMap { violationsIn(it) }.distinct()
        assertEquals(
            "demo/ and ui/demo/ may reference ONLY the allowlisted packages in this test's own ALLOWED_PREFIXES, " +
                "for every name rooted in one of WATCHED_PREFIXES (WP brief constraint 2: \"not even to fail\") -- " +
                "ANY other dev.steamvault./java./javax./android./okhttp/okio/coil-rooted qualified name is a " +
                "network or platform-resource capability this WP must not grant, whether reached via an import " +
                "line or an inline fully-qualified reference. Hits: $hits",
            emptyList<String>(),
            hits,
        )
    }

    /** Guards against the pin above passing vacuously because the demo
     * package was emptied, renamed, or the source roots stopped resolving. */
    @Test
    fun `the allowlist is not vacuous -- both demo source roots still contain the expected classes`() {
        val text = allKotlinFiles().joinToString("\n") { it.readText(Charsets.UTF_8) }
        for (className in listOf(
            "DemoGamesRepository",
            "DemoJobsRepository",
            "DemoCacheRepository",
            "DemoClientsRepository",
            "DemoMappingRepository",
            "DemoSettingsRepository",
            "DemoScheduleRepository", // N3, WP AG-3 round 2 fix
            "DemoUpdatesRepository", // WP UPD-3
            "DemoModeBanner",
        )) {
            assertTrue("expected $className to still exist under demo/ or ui/demo/", text.contains(className))
        }
    }
}
