package dev.steamvault.app.ui.library.logic

import dev.steamvault.app.net.error.VaultApiError
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.net.model.OwnedGame
import dev.steamvault.app.net.model.OwnedGamesRelayOut
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.net.model.SettingsOut
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WP APP-FEAT-1 / APP-FIX-2: the owned list comes from the vault's stored
 * `steam_library_steamid` (never this device's own sign-in), the title
 * order, and the loader state machine (port of web `owned-library.js`).
 */
class OwnedLibraryTest {

    private fun settings(effective: kotlinx.serialization.json.JsonElement?, present: Boolean = true) = SettingsOut(
        readonly = false,
        settings = if (present) {
            listOf(SettingInfoOut(key = STEAM_LIBRARY_SETTING_KEY, effective = effective ?: JsonNull, source = "db", applies = "immediately"))
        } else {
            emptyList()
        },
    )

    private val validId = "76561198042117903"

    // ---- steamIdFromSettings --------------------------------------------------

    @Test
    fun `MUTATION PIN -- only a JSON string counts as the stored id, trimmed; a number is never coerced`() {
        assertEquals(validId, steamIdFromSettings(settings(JsonPrimitive(" $validId "))))
        assertEquals("", steamIdFromSettings(settings(JsonPrimitive(76561198042117903L))))
        assertEquals("", steamIdFromSettings(settings(JsonNull)))
        assertEquals("", steamIdFromSettings(settings(null, present = false)))
        assertEquals("", steamIdFromSettings(null))
    }

    // ---- titles ---------------------------------------------------------------

    @Test
    fun `appTitle order -- vault name, then owned name, then App N; blank counts as missing`() {
        assertEquals("Vault Five", appTitle(5, "Vault Five", "Steam Five"))
        assertEquals("Steam Five", appTitle(5, null, "Steam Five"))
        assertEquals("Steam Five", appTitle(5, "   ", "Steam Five"))
        assertEquals("App 5", appTitle(5, null, null))
        assertEquals("App 5", appTitle(5, "", " "))
    }

    @Test
    fun `fillMissingNames fills a missing vault name only and adds no rows`() {
        val vault = listOf(
            GameSummary(appid = 1, name = "Vault One", status = "done", depot_count = 1),
            GameSummary(appid = 2, name = null, status = "error", depot_count = 0),
        )
        val owned = listOf(OwnedGame(1, "Steam One"), OwnedGame(2, "Steam Two"), OwnedGame(3, "Steam Three"))
        val out = fillMissingNames(vault, owned)
        assertEquals(listOf("Vault One", "Steam Two"), out.map { it.name })
        assertEquals("error", out[1].status)
        assertSame(vault, fillMissingNames(vault, emptyList()))
    }

    @Test
    fun `mergeLibrary also fills a missing vault name from the owned list (web mergeOwnedLibrary)`() {
        val vault = listOf(GameSummary(appid = 2, name = null, status = "running", depot_count = 0))
        val merged = mergeLibrary(vault, listOf(OwnedGame(2, "Steam Two")))
        assertEquals(1, merged.size)
        assertEquals("Steam Two", merged.single().name)
        assertEquals("running", merged.single().status)
    }

    @Test
    fun `anyJobLacksVaultName is the Downloads trigger for the one owned-list load`() {
        val games = mapOf(1 to GameSummary(appid = 1, name = "Named", status = "done", depot_count = 1))
        assertFalse(anyJobLacksVaultName(listOf(1), games))
        assertTrue(anyJobLacksVaultName(listOf(1, 2), games))
    }

    // ---- loader ---------------------------------------------------------------

    @Test
    fun `blank stored id -- UNSET, the relay is never called`() = runTest {
        var relayCalls = 0
        val loader = OwnedLibraryLoader(getSettings = { settings(JsonPrimitive("")) }, ownedGames = { relayCalls++; OwnedGamesRelayOut() })
        loader.load()
        assertEquals(OwnedStatus.UNSET, loader.state.status)
        assertEquals(0, relayCalls)
        assertEquals(OwnedNotice.UNSET, ownedNoticeFor(loader.state))
        assertNull(loader.state.ownedGamesOrNull)
    }

    @Test
    fun `MUTATION PIN -- the relay is asked for the STORED id and READY carries its games`() = runTest {
        var asked: String? = null
        val games = listOf(OwnedGame(10, "Ten"))
        val loader = OwnedLibraryLoader(
            getSettings = { settings(JsonPrimitive(validId)) },
            ownedGames = { asked = it; OwnedGamesRelayOut(game_count = 1, games = games) },
        )
        loader.load()
        assertEquals(validId, asked)
        assertEquals(OwnedStatus.READY, loader.state.status)
        assertEquals(games, loader.state.ownedGamesOrNull)
        assertNull(ownedNoticeFor(loader.state))
    }

    @Test
    fun `zero games is the private-profile notice, never a count`() = runTest {
        val loader = OwnedLibraryLoader(getSettings = { settings(JsonPrimitive(validId)) }, ownedGames = { OwnedGamesRelayOut() })
        loader.load()
        assertEquals(OwnedNotice.PRIVATE_OR_EMPTY, ownedNoticeFor(loader.state))
    }

    @Test
    fun `relay 409 and 422 offer Settings, other failures only Reload, and vault games stay`() = runTest {
        fun loaderFailing(status: Int) = OwnedLibraryLoader(
            getSettings = { settings(JsonPrimitive(validId)) },
            ownedGames = { throw VaultApiError.Validation("x", status, "detail $status") },
        )
        val l409 = loaderFailing(409).also { it.load() }
        assertEquals(OwnedNotice.NO_RELAY_KEY, ownedNoticeFor(l409.state))
        assertTrue(noticeOffersSettings(OwnedNotice.NO_RELAY_KEY))
        val l422 = loaderFailing(422).also { it.load() }
        assertEquals(OwnedNotice.STORED_ID_REJECTED, ownedNoticeFor(l422.state))
        val l502 = loaderFailing(502).also { it.load() }
        assertEquals(OwnedNotice.FAILED, ownedNoticeFor(l502.state))
        assertEquals("detail 502", l502.state.errorDetail)
        assertFalse(noticeOffersSettings(OwnedNotice.FAILED))
        assertTrue(noticeOffersReload(OwnedNotice.FAILED))
        assertNull(l502.state.ownedGamesOrNull)
    }

    @Test
    fun `a settings failure is an error of the SETTINGS phase, never a relay call`() = runTest {
        var relayCalls = 0
        val loader = OwnedLibraryLoader(
            getSettings = { throw VaultApiError.Validation("x", 409, "not the relay") },
            ownedGames = { relayCalls++; OwnedGamesRelayOut() },
        )
        loader.load()
        assertEquals(OwnedErrorPhase.SETTINGS, loader.state.errorPhase)
        assertEquals(OwnedNotice.FAILED, ownedNoticeFor(loader.state))
        assertEquals(0, relayCalls)
    }

    @Test
    fun `loadIfNeverLoaded loads once and never again`() = runTest {
        var settingsCalls = 0
        val loader = OwnedLibraryLoader(getSettings = { settingsCalls++; settings(JsonPrimitive("")) }, ownedGames = { OwnedGamesRelayOut() })
        loader.loadIfNeverLoaded()
        loader.loadIfNeverLoaded()
        assertEquals(1, settingsCalls)
        loader.load()
        assertEquals(2, settingsCalls)
    }

    @Test
    fun `MUTATION PIN -- a superseded load's late answer is dropped (generation token)`() = runTest {
        val first = CompletableDeferred<OwnedGamesRelayOut>()
        var call = 0
        val loader = OwnedLibraryLoader(
            getSettings = { settings(JsonPrimitive(validId)) },
            ownedGames = {
                call++
                if (call == 1) first.await() else OwnedGamesRelayOut(game_count = 1, games = listOf(OwnedGame(2, "Second")))
            },
        )
        val job = launch { loader.load() }
        testScheduler.advanceUntilIdle()
        loader.load()
        first.complete(OwnedGamesRelayOut(game_count = 1, games = listOf(OwnedGame(1, "First"))))
        job.join()
        assertEquals(listOf(OwnedGame(2, "Second")), loader.state.games)
    }

    @Test
    fun `MUTATION PIN -- a server without the setting is ABSENT (server too old), never "set it in Settings"`() = runTest {
        var relayCalls = 0
        val loader = OwnedLibraryLoader(getSettings = { settings(null, present = false) }, ownedGames = { relayCalls++; OwnedGamesRelayOut() })
        loader.load()
        assertEquals(OwnedStatus.ABSENT, loader.state.status)
        assertEquals(OwnedNotice.SERVER_TOO_OLD, ownedNoticeFor(loader.state))
        assertFalse(noticeOffersSettings(OwnedNotice.SERVER_TOO_OLD))
        assertFalse(noticeOffersReload(OwnedNotice.SERVER_TOO_OLD))
        assertEquals(0, relayCalls)
        assertFalse(librarySettingPresent(settings(null, present = false)))
        assertTrue(librarySettingPresent(settings(JsonPrimitive(""))))
    }

    @Test
    fun `MUTATION PIN -- a 409 from the SETTINGS read is not reported as a missing relay key`() = runTest {
        val loader = OwnedLibraryLoader(
            getSettings = { throw VaultApiError.Validation("x", 409, "settings conflict") },
            ownedGames = { OwnedGamesRelayOut() },
        )
        loader.load()
        assertNull(loader.state.errorStatus)
        assertEquals(OwnedNotice.FAILED, ownedNoticeFor(loader.state))
    }

    @Test
    fun `MUTATION PIN -- a load cancelled mid-request (user left Library) resets loading and lets Downloads load again`() = runTest {
        val gate = CompletableDeferred<OwnedGamesRelayOut>()
        var relayCalls = 0
        val loader = OwnedLibraryLoader(
            getSettings = { settings(JsonPrimitive(validId)) },
            ownedGames = { relayCalls++; if (relayCalls == 1) gate.await() else OwnedGamesRelayOut(game_count = 1, games = listOf(OwnedGame(3, "Three"))) },
        )
        val job = launch { loader.load() }
        testScheduler.advanceUntilIdle()
        assertTrue(loader.state.loading)
        job.cancel()
        job.join()
        assertFalse("loading must not stick after a cancel", loader.state.loading)
        assertFalse("a cancelled first load must not count as loaded", loader.everStarted)
        loader.loadIfNeverLoaded()
        assertEquals(2, relayCalls)
        assertEquals(OwnedStatus.READY, loader.state.status)
    }

    @Test
    fun `a cancel after an earlier completed load keeps everStarted, so Downloads does not reload`() = runTest {
        val gate = CompletableDeferred<OwnedGamesRelayOut>()
        var relayCalls = 0
        val loader = OwnedLibraryLoader(
            getSettings = { settings(JsonPrimitive(validId)) },
            ownedGames = { relayCalls++; if (relayCalls == 2) gate.await() else OwnedGamesRelayOut() },
        )
        loader.load()
        val job = launch { loader.load() }
        testScheduler.advanceUntilIdle()
        job.cancel()
        job.join()
        assertTrue(loader.everStarted)
        assertFalse(loader.state.loading)
    }
}
