package dev.steamvault.app.net

import dev.steamvault.app.net.model.CacheDeletionOut
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.ui.library.logic.groupGames
import kotlinx.serialization.decodeFromString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP TOOL-APPS-2d (ADR-0022 §1 "Compatibility, both directions"): a new app
 * against an OLD server (no `parent_appid`/`app_type`/`group_size_bytes`)
 * decodes and renders the flat list unchanged; the new fields decode when
 * present; a plain delete response (no grouped fields) still decodes.
 */
class ToolApps2CompatTest {

    private val preToolApps2Games = """
        [
          {"appid": 553850, "name": "HELLDIVERS 2", "status": "done", "last_prefill_at": "2026-10-01T00:00:00Z",
           "depot_count": 2, "size_bytes": 1000, "needs_force": false},
          {"appid": 2506210, "name": null, "status": "done", "last_prefill_at": "2026-10-01T00:00:00Z",
           "depot_count": 1, "size_bytes": 300, "needs_force": false}
        ]
    """.trimIndent()

    @Test
    fun `MUTATION PIN -- an old server's games list decodes with nulls and stays flat`() {
        val games = VaultJson.decodeFromString<List<GameSummary>>(preToolApps2Games)
        assertTrue(games.all { it.parent_appid == null && it.app_type == null && it.group_size_bytes == null })
        val grouped = groupGames(games)
        assertEquals(games, grouped.rows)
        assertTrue(grouped.packagesByParent.isEmpty())
    }

    @Test
    fun `the new fields decode`() {
        val json = """
            [{"appid": 2506210, "status": "done", "depot_count": 1, "parent_appid": 553850,
              "app_type": "dlc", "group_size_bytes": null},
             {"appid": 553850, "status": "done", "depot_count": 2, "app_type": "game", "group_size_bytes": 1300}]
        """.trimIndent()
        val games = VaultJson.decodeFromString<List<GameSummary>>(json)
        assertEquals(553850, games[0].parent_appid)
        assertEquals("dlc", games[0].app_type)
        assertNull(games[0].group_size_bytes)
        assertEquals(1300L, games[1].group_size_bytes)
    }

    @Test
    fun `a plain delete response decodes without the grouped fields`() {
        val out = VaultJson.decodeFromString<CacheDeletionOut>(
            """{"appid": 440, "deleted_depots": [{"depotid": 441, "size_bytes_freed": 10, "shared_with_uncached": []}],
               "skipped_shared": [], "failed": [], "total_bytes_freed": 10}""",
        )
        assertFalse(out.with_packages)
        assertTrue(out.members.isEmpty() && out.kept_children.isEmpty())
    }

    @Test
    fun `a grouped delete response decodes its members`() {
        val out = VaultJson.decodeFromString<CacheDeletionOut>(
            """{"appid": 1, "deleted_depots": [], "skipped_shared": [], "failed": [], "total_bytes_freed": 0,
               "with_packages": true,
               "members": [{"appid": 1, "role": "parent", "name": "G", "app_type": "game", "deleted_depots": [11], "failed_depots": []}],
               "kept_children": [{"appid": 3, "name": null, "app_type": null, "reason": "type_unknown"}]}""",
        )
        assertTrue(out.with_packages)
        assertEquals(listOf(11), out.members.single().deleted_depots)
        assertEquals("type_unknown", out.kept_children.single().reason)
    }
}
