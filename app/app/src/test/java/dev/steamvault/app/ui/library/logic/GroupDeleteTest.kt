package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.VaultJson
import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.BlockingJobOut
import dev.steamvault.app.net.model.DeletePlanOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** WP TOOL-APPS-2d: the grouped-delete decisions (ADR-0022 §4). */
class GroupDeleteTest {

    /** The server's `DeletePlanOut`, shaped as api/README.md "Grouped delete" shows it. */
    private val planJson = """
        {
          "appid": 553850, "with_packages": true,
          "members": [
            {"appid": 553850, "role": "parent", "name": "HELLDIVERS 2", "app_type": "game", "depot_ids": [553851, 553852]},
            {"appid": 2506210, "role": "package", "name": "HELLDIVERS 2 - Pack", "app_type": "dlc", "depot_ids": [2506211]}
          ],
          "kept_children": [{"appid": 4000001, "name": null, "app_type": "game", "reason": "not_a_package_type"}],
          "delete_depots": [{"depotid": 553851, "size_bytes": 1000, "owners": [553850], "shared_with_uncached": []}],
          "skipped_shared": [{"depotid": 553853, "shared_with": [4000001]}],
          "unusable": [], "total_bytes": 1000, "blocked_by": []
        }
    """.trimIndent()

    private val plan: DeletePlanOut = VaultJson.decodeFromString(DeletePlanOut.serializer(), planJson)

    @Test
    fun `the plan decodes and confirms exactly its members, parent included`() {
        assertEquals(listOf(553850, 2506210), planMemberIds(plan))
        assertFalse(isPlanBlocked(plan))
        assertTrue(isPlanBlocked(plan.copy(blocked_by = listOf(BlockingJobOut(2506210, 7, "prefill", "running")))))
    }

    @Test
    fun `MUTATION PIN -- only a 409 whose detail object carries server_members is a members mismatch`() {
        // VaultApiClient keeps a non-string detail as its JSON text.
        val mismatch = VaultApiError.Validation(
            "DELETE failed", 409,
            """{"message":"differ","server_members":[1,2],"confirmed_members":[1]}""",
        )
        assertTrue(isMembersMismatch(mismatch))
        assertFalse(isMembersMismatch(VaultApiError.Validation("x", 409, "Prefill job 3 for app 1 is running.")))
        assertFalse(isMembersMismatch(VaultApiError.Validation("x", 422, """{"server_members":[1]}""")))
        assertFalse(isMembersMismatch(VaultApiError.Validation("x", 409, null)))
        assertFalse(isMembersMismatch(VaultApiError.Validation("x", 409, """{"server_members":"1"}""")))
    }

    @Test
    fun `the plan in display terms`() {
        val names = mapOf(553850 to "Helldivers 2 (vault)")
        val d = describeGroupPlan(plan) { id, server -> names[id] ?: server ?: "App $id" }
        assertEquals("Helldivers 2 (vault) and its 1 content package", d.subject)
        assertEquals(listOf(2506210), d.packages.map { it.appid })
        assertEquals(1000L, d.freedBytes)
        assertEquals(listOf(GroupPlanKeptDepot(553853, listOf("App 4000001"))), d.keptDepots)
        assertEquals(listOf(GroupPlanKeptChild(4000001, "App 4000001", "not a content package")), d.keptChildren)
        assertTrue(d.blocked.isEmpty())
    }

    @Test
    fun `wording, word for word`() {
        assertEquals("Starfall Legion", groupSubject("Starfall Legion", 0))
        assertEquals("Starfall Legion and its 2 content packages", groupSubject("Starfall Legion", 2))
        assertEquals("Download running", blockingJobText(BlockingJobOut(1, 2, "prefill", "running")))
        assertEquals("Cleanup queued", blockingJobText(BlockingJobOut(1, 2, "gc", "queued")))
        assertEquals("another app still uses it", keptDepotWhy(emptyList()))
        assertEquals("A still uses it", keptDepotWhy(listOf("A")))
        assertEquals("A, B still use it", keptDepotWhy(listOf("A", "B")))
        assertEquals("type not confirmed by Steam", keptChildReason("type_only_inferred"))
        assertEquals("type unknown", keptChildReason("type_unknown"))
        assertEquals("not a content package", keptChildReason("something_new"))
    }
}
