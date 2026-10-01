package dev.steamvault.app

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import dev.steamvault.app.demo.DemoCacheRepository
import dev.steamvault.app.demo.DemoClientsRepository
import dev.steamvault.app.demo.DemoGamesRepository
import dev.steamvault.app.demo.DemoJobsRepository
import dev.steamvault.app.demo.DemoMappingRepository
import dev.steamvault.app.demo.DemoScheduleRepository
import dev.steamvault.app.demo.DemoSettingsRepository
import dev.steamvault.app.demo.DemoState
import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.JobSummary
import dev.steamvault.app.net.profile.buildConnectivityProfile
import dev.steamvault.app.net.steam.PendingLoginState
import dev.steamvault.app.net.steam.SteamOpenIdConfig
import dev.steamvault.app.notifications.NotificationRouting
import dev.steamvault.app.repo.SteamIdentityRepository
import dev.steamvault.app.repo.SteamIdentityRepositoryImpl
import dev.steamvault.app.repo.VaultCacheRepository
import dev.steamvault.app.repo.VaultClientsRepository
import dev.steamvault.app.repo.VaultGamesRepository
import dev.steamvault.app.repo.VaultJobsRepository
import dev.steamvault.app.repo.VaultMappingRepository
import dev.steamvault.app.repo.VaultScheduleRepository
import dev.steamvault.app.repo.VaultSettingsRepository
import dev.steamvault.app.storage.EncryptedCredentialStore
import dev.steamvault.app.storage.SharedPreferencesLibraryPreferences
import dev.steamvault.app.ui.clients.AndroidClientsStrings
import dev.steamvault.app.ui.clients.ClientsController
import dev.steamvault.app.ui.clients.ClientsSheet
import dev.steamvault.app.ui.downloads.DownloadsScreen
import dev.steamvault.app.ui.downloads.logic.countPending
import dev.steamvault.app.ui.library.LibraryScreen
import dev.steamvault.app.ui.nav.BottomNavBar
import dev.steamvault.app.ui.nav.Destination
import dev.steamvault.app.ui.onboarding.AndroidOnboardingStrings
import dev.steamvault.app.ui.onboarding.OnboardingController
import dev.steamvault.app.ui.onboarding.OnboardingMode
import dev.steamvault.app.ui.onboarding.OnboardingScreen
import dev.steamvault.app.ui.onboarding.logic.shouldShowOnboarding
import dev.steamvault.app.ui.settings.AndroidSettingsStrings
import dev.steamvault.app.ui.settings.SettingsController
import dev.steamvault.app.ui.settings.SettingsScreen
import dev.steamvault.app.ui.theme.SteamVaultTheme
import kotlinx.coroutines.launch

/**
 * Single-activity app shell. As of WP 4b.7, [vaultApiClientState] and
 * [showOnboarding] are the two pieces of state that decide what the whole
 * app shows: onboarding (this WP's [OnboardingScreen]) when there is no
 * working vault-api connection, the normal three-destination shell
 * otherwise -- see `ui/onboarding/logic/OnboardingSteps.kt::shouldShowOnboarding`
 * for the underlying pure rule this mirrors.
 *
 * **The WP 4b.4/4b.7 gap this WP closes.** Before this WP, no screen wrote
 * a vault-api base URL/API key/connectivity-profile kind into
 * [dev.steamvault.app.storage.CredentialStore] at all -- [vaultApiClientState]
 * was permanently `null` on every real install, and
 * `net/profile/ConnectivityProfileFactory.kt`'s `buildConnectivityProfile`
 * kdoc documented this as
 * "WP 4b.7's job, not a prerequisite of this one". [OnboardingScreen] /
 * [OnboardingController] are that missing write path; [refreshVaultApiClient]
 * is what makes the rest of the app shell notice a connection appeared (or
 * disappeared -- Settings' Disconnect).
 *
 * **Full-screen swap, not a modal overlay -- see [OnboardingScreen]'s own
 * kdoc** for why this differs from `web/js/onboarding.js`'s dialog-overlay
 * approach: this codebase already committed to a plain state-based screen
 * switcher (`ui/nav/Destination.kt`'s kdoc), and onboarding is simply one
 * more top-level state alongside the three [Destination]s, gating them
 * entirely rather than floating above them.
 */
class MainActivity : ComponentActivity() {

    private val credentialStore by lazy { EncryptedCredentialStore(applicationContext) }

    /** WP 4h.4: [dev.steamvault.app.net.steam.VaultRelayLibraryFetcher] (this
     * repository's default library fetcher) needs the CURRENT
     * [VaultApiClient], read fresh on every call -- `vaultApiClientState`
     * may still be `null` at the moment this lazy block itself runs (Steam
     * OpenID sign-in, unlike library fetching, is reachable during
     * onboarding, before any connection exists), but the lambda below
     * re-reads the field every time it is invoked, same "read fresh"
     * pattern [refreshVaultApiClient]'s own `apiKeyProvider` lambda uses.
     *
     * WP APP-FIX-1 (P1): this repository is per-ACTIVITY-instance (a `by
     * lazy` field on an Activity is gone the moment the Activity is
     * recreated -- rotation, or the OS reclaiming it while the Custom Tab
     * is in front), so the pending OpenID `state` it checks callbacks
     * against is handed in from [PROCESS_PENDING_LOGIN_STATE], the one
     * holder that outlives recreation. See that constant's kdoc. */
    private val identityRepository: SteamIdentityRepository by lazy {
        SteamIdentityRepositoryImpl(
            credentialStore,
            vaultApiClientProvider = { vaultApiClientState },
            pendingLoginState = PROCESS_PENDING_LOGIN_STATE,
        )
    }
    private val libraryPreferences by lazy { SharedPreferencesLibraryPreferences(applicationContext) }

    /** Long-lived for this Activity INSTANCE (same category as
     * [identityRepository]/[credentialStore] -- not the process: a
     * recreated Activity builds a fresh one, WP APP-FIX-1 P1) --
     * `onNewIntent` needs a stable reference to route a Steam OpenID
     * callback into while onboarding is the active screen. */
    private val onboardingController: OnboardingController by lazy {
        OnboardingController(credentialStore, identityRepository, AndroidOnboardingStrings(resources))
    }

    /** `null` until a vault-api connection has been configured -- see this
     * class's kdoc. Rebuilt by [refreshVaultApiClient] whenever the
     * connection changes (onboarding finishes, Settings disconnects). */
    private var vaultApiClientState by mutableStateOf<VaultApiClient?>(null)

    /**
     * WP APP-DEMO: `null` unless the user tapped "Skip for now — browse in
     * demo mode" during onboarding ([enterDemoMode], only reachable from
     * [OnboardingMode.FIRST_RUN] per [OnboardingController.canSkipToDemo]).
     * Deliberately NOT persisted anywhere (same category as [destination]/
     * [showOnboarding] below -- a plain in-memory field, gone the moment
     * this `Activity` instance is, which this class's own kdoc already
     * documents as neither a regression nor an improvement, just where this
     * kind of short-lived state lives in this codebase). Two consequences
     * that are both intentional (WP brief constraints 4/5):
     *  - [refreshVaultApiClient] clears this unconditionally, so finishing
     *    onboarding with a REAL connection (or Settings' Disconnect) always
     *    leaves demo mode cleanly -- there is exactly one place a real
     *    connection gets built, and it is also the one place demo state
     *    gets torn down.
     *  - [enterDemoMode] builds a brand-new [DemoState.fresh] every time,
     *    so re-entering demo mode never carries over a previous session's
     *    mutations (deleted cache, finished jobs, settings overrides).
     */
    private var demoState by mutableStateOf<DemoState?>(null)

    /** Rebuilt alongside [vaultApiClientState] so it always reflects the
     * SAME client (and can be reached directly from `onNewIntent` -- unlike
     * `LibraryDestinationContent`'s repositories, this one needs a stable
     * identity outside Compose's `remember`, for the same reason
     * [onboardingController] does). */
    private var settingsControllerState by mutableStateOf<SettingsController?>(null)

    /** WP 4b.10: the clients sheet's controller -- rebuilt alongside
     * [vaultApiClientState]/[settingsControllerState], for the same reason
     * [settingsControllerState] needs a stable, outside-composition
     * identity (`handleNotificationTap` opens it directly on a bypass
     * notification tap, per [ClientsController]'s kdoc: this sheet is
     * hoisted at this level, not owned by any one [Destination], since
     * "Clients is a sheet, not a nav item" per `ui/nav/Destination.kt`). */
    private var clientsControllerState by mutableStateOf<ClientsController?>(null)

    private var showOnboarding by mutableStateOf(false)
    private var onboardingMode by mutableStateOf(OnboardingMode.FIRST_RUN)

    /** Hoisted out of the `setContent` composable (WP 4b.8) so a
     * notification tap (routed through [handleIntent]) can change it from
     * outside composition -- previously a plain `remember { mutableStateOf(...) }`
     * local to the composable lambda, unreachable from anywhere else.
     *
     * N2: this hoist is scoped to exactly that -- it does NOT add
     * configuration-change/process-death state restoration (no
     * `rememberSaveable`-equivalent, no `SavedStateHandle`). A plain
     * instance field is just as gone as the old `remember { }` local was
     * the moment this `Activity` instance itself is recreated (rotation
     * without a matching `android:configChanges`, or true process death) --
     * both before and after this change, [destination] resets to
     * [Destination.LIBRARY] across either event. Neither a regression nor
     * an improvement over the prior behaviour, just a change of WHERE the
     * same short-lived state lives. */
    private var destination by mutableStateOf(Destination.LIBRARY)

    /** WP 4b.8: POST_NOTIFICATIONS is a runtime permission on API 33+
     * (`AndroidManifest.xml`). Must be registered here, not lazily inside a
     * click handler -- `registerForActivityResult` requires the launcher to
     * exist before the Activity reaches STARTED, same rule every other
     * `ActivityResultContract` registration in Android follows. The result
     * itself needs no handling beyond letting the system remember the
     * grant/denial: [dev.steamvault.app.notifications.AndroidNotificationPoster]
     * re-checks the live permission state on every post, so nothing here
     * needs to react to grant vs. denial explicitly (WP brief: "gracefully
     * degrade if denied -- the worker still runs, just no visible
     * notifications"). */
    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    /** Latest `GET /v1/jobs` snapshot from WHICHEVER screen is currently
     * polling jobs (Library or Downloads -- both now report through
     * `onJobsSnapshot`, see `LibraryScreen.kt`/`DownloadsScreen.kt`'s own
     * kdoc for that parameter). Feeds the bottom-nav pip
     * ([dev.steamvault.app.ui.downloads.logic.countPending]).
     *
     * **Honest scope limitation (WP 4b.5).** This is NOT a background poll
     * -- it is only ever updated while a jobs-polling screen is on screen,
     * same foreground-only constraint every poll loop in this app has
     * before WP 4b.8's WorkManager wiring lands. */
    private var pendingJobsSnapshot by mutableStateOf<List<JobSummary>>(emptyList())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // WP APP-DEMO review round 2 (S2): refreshVaultApiClient() FIRST,
        // unconditionally, exactly as it always has -- it is the ONE place
        // demoState gets cleared, and that invariant (WP brief constraint
        // 4: "switching to a real connection must not leave demo state
        // behind") must hold on every onCreate, rotation included, not only
        // on a fresh process start. The demo-mode restore below runs ONLY
        // AFTER this line, and ONLY if it did NOT itself produce a real
        // connection -- a real connection found in CredentialStore always
        // wins over a saved "was in demo mode" flag from before rotation.
        refreshVaultApiClient()
        if (savedInstanceState?.getBoolean(KEY_WAS_IN_DEMO_MODE) == true && vaultApiClientState == null) {
            // A configuration change (typically rotation) re-runs onCreate
            // with no `android:configChanges` declared for this Activity,
            // which -- before this fix -- silently dropped demoState (a
            // plain in-memory field) and fell through to onboarding, even
            // though nothing about the user's demo session actually ended.
            // Re-entering builds a FRESH DemoState (`enterDemoMode()`'s own
            // contract), which is the same "no stale state carried over"
            // rule this WP already applies to every other way of entering
            // demo mode -- there is no saved DemoState to restore even if
            // Bundle-based Parcelable support were added, so this is not a
            // shortcut, it is the correct behaviour either way.
            enterDemoMode()
        }
        if (shouldShowOnboarding(hasVaultConnection = vaultApiClientState != null, demoMode = demoState != null)) {
            openOnboarding(OnboardingMode.FIRST_RUN)
        }
        handleIntent(intent)

        setContent {
            SteamVaultTheme {
                if (showOnboarding) {
                    OnboardingScreen(
                        controller = onboardingController,
                        onFinished = {
                            refreshVaultApiClient()
                            showOnboarding = false
                        },
                        onCancelled = { showOnboarding = false },
                        onLaunchSteamLogin = { url -> launchSteamLogin(url) },
                        onDemoSkip = { enterDemoMode() },
                    )
                } else {
                    val pendingJobsCount = countPending(pendingJobsSnapshot)

                    Scaffold(
                        bottomBar = {
                            BottomNavBar(
                                current = destination,
                                pendingJobsCount = pendingJobsCount,
                                onSelect = {
                                    destination = it
                                    // Mockup rule: navigation dismisses transient
                                    // surfaces (docs/design/vault-app-mockup-NOTES.md
                                    // -- the clients sheet is explicitly named
                                    // alongside the detail sheet and the
                                    // notifications panel).
                                    clientsControllerState?.close()
                                },
                            )
                        },
                    ) { innerPadding ->
                        Box(modifier = Modifier.fillMaxSize().padding(innerPadding)) {
                            when (destination) {
                                Destination.LIBRARY -> LibraryDestinationContent()
                                Destination.DOWNLOADS -> DownloadsDestinationContent()
                                Destination.SETTINGS -> SettingsDestinationContent()
                            }
                        }
                    }

                    // WP 4b.10: hoisted at this level, not inside any one
                    // [Destination]'s content -- see [clientsControllerState]'s
                    // kdoc. Only composed (and only then polling, see
                    // ClientsSheet.kt) while a connection exists and the
                    // sheet is actually open.
                    clientsControllerState?.let { controller ->
                        if (controller.isOpen) {
                            ClientsSheet(controller = controller, demoMode = demoState != null)
                        }
                    }
                }
            }
        }
    }

    /** Recomputes [vaultApiClientState]/[settingsControllerState] from
     * whatever is currently in [credentialStore] -- call after anything
     * that writes the connection ([OnboardingController.finish] via
     * `onFinished`, [SettingsController.disconnect] via `onDisconnected`).
     *
     * WP APP-DEMO: also the ONE place [demoState] is torn down. A real
     * connection is only ever built here, so clearing demo state
     * unconditionally at the top means "finish onboarding with a real
     * vault" and "leave demo mode" are the same action by construction --
     * there is no separate "exit demo mode" code path that could drift
     * from this one (WP brief constraint 4: "switching to a real
     * connection must not leave demo state behind"). */
    private fun refreshVaultApiClient() {
        demoState = null
        val profile = buildConnectivityProfile(credentialStore)
        val hasApiKey = !credentialStore.getApiKey().isNullOrBlank()
        val client = if (profile != null && hasApiKey) {
            // Review fix (N4): read the store FRESH inside the lambda, not a
            // captured local -- VaultApiClient's own kdoc promises
            // apiKeyProvider is "read fresh on every call... so a key change
            // in CredentialStore takes effect on the very next request
            // without rebuilding this client"; capturing a snapshot string
            // here would have silently broken that promise for this app's
            // only caller.
            VaultApiClient(profile, apiKeyProvider = { credentialStore.getApiKey().orEmpty() })
        } else {
            null
        }
        vaultApiClientState = client
        settingsControllerState = client?.let {
            SettingsController(
                VaultSettingsRepository(it),
                VaultScheduleRepository(it),
                credentialStore,
                identityRepository,
                AndroidSettingsStrings(resources),
            )
        }
        clientsControllerState = client?.let {
            ClientsController(VaultClientsRepository(it), AndroidClientsStrings(resources))
        }
        // Review fix (N3): a stale pip count from the connection that just
        // disappeared (Settings' Disconnect) must not linger on the bottom
        // nav once Library/Downloads are unreachable -- there is no poll
        // left running to correct it on its own.
        pendingJobsSnapshot = emptyList()
    }

    /**
     * WP APP-DEMO: "Skip for now — browse in demo mode"
     * ([dev.steamvault.app.ui.onboarding.OnboardingScreen]'s `onDemoSkip`).
     * Only reachable from [OnboardingMode.FIRST_RUN]
     * ([OnboardingController.canSkipToDemo]), so [vaultApiClientState] is
     * always already `null` here -- nothing is persisted to
     * [credentialStore] at all, matching [OnboardingController.finish]'s
     * own "only Done finishes onboarding for real" boundary.
     *
     * [settingsControllerState]/[clientsControllerState] are rebuilt here
     * exactly the way [refreshVaultApiClient] rebuilds them for a real
     * connection -- `ui/settings/SettingsScreen.kt`/`ui/clients/ClientsSheet.kt`
     * need no demo-mode branch of their own, they are simply handed a
     * [SettingsController]/[ClientsController] backed by
     * [DemoSettingsRepository]/[DemoClientsRepository] instead of the real
     * `Vault*Repository`.
     */
    private fun enterDemoMode() {
        val demo = DemoState.fresh()
        demoState = demo
        settingsControllerState = SettingsController(
            DemoSettingsRepository(demo),
            DemoScheduleRepository(demo),
            credentialStore,
            identityRepository,
            AndroidSettingsStrings(resources),
        )
        clientsControllerState = ClientsController(DemoClientsRepository(demo), AndroidClientsStrings(resources))
        pendingJobsSnapshot = emptyList()
        showOnboarding = false
    }

    private fun openOnboarding(mode: OnboardingMode) {
        onboardingController.start(mode)
        onboardingMode = mode
        showOnboarding = true
    }

    @Composable
    private fun LibraryDestinationContent() {
        val demo = demoState
        if (demo != null) {
            LibraryScreen(
                gamesRepository = remember(demo) { DemoGamesRepository(demo) },
                jobsRepository = remember(demo) { DemoJobsRepository(demo) },
                mappingRepository = remember(demo) { DemoMappingRepository(demo) },
                cacheRepository = remember(demo) { DemoCacheRepository(demo) },
                identityRepository = identityRepository,
                libraryPreferences = libraryPreferences,
                onJobsSnapshot = { pendingJobsSnapshot = it },
                demoMode = true,
            )
            return
        }
        val client = vaultApiClientState
        if (client == null) {
            NotConnectedPlaceholder()
            return
        }
        LibraryScreen(
            gamesRepository = remember(client) { VaultGamesRepository(client) },
            jobsRepository = remember(client) { VaultJobsRepository(client) },
            mappingRepository = remember(client) { VaultMappingRepository(client) },
            cacheRepository = remember(client) { VaultCacheRepository(client) },
            identityRepository = identityRepository,
            libraryPreferences = libraryPreferences,
            onJobsSnapshot = { pendingJobsSnapshot = it },
            demoMode = false,
        )
    }

    /** WP 4b.5's screen (Downloads + job control). */
    @Composable
    private fun DownloadsDestinationContent() {
        val demo = demoState
        if (demo != null) {
            DownloadsScreen(
                jobsRepository = remember(demo) { DemoJobsRepository(demo) },
                gamesRepository = remember(demo) { DemoGamesRepository(demo) },
                onJobsSnapshot = { pendingJobsSnapshot = it },
                demoMode = true,
            )
            return
        }
        val client = vaultApiClientState
        if (client == null) {
            NotConnectedPlaceholder()
            return
        }
        DownloadsScreen(
            jobsRepository = remember(client) { VaultJobsRepository(client) },
            gamesRepository = remember(client) { VaultGamesRepository(client) },
            onJobsSnapshot = { pendingJobsSnapshot = it },
            demoMode = false,
        )
    }

    /** WP 4b.7's screen -- replaces the previous bare
     * `ui.identity.IdentityScreen` placeholder (removed outright in WP
     * 4h.4, once the device-local Steam Web API key concept it rendered no
     * longer existed to describe -- see `app/README.md`'s "Steam library
     * via the vault relay" section). */
    @Composable
    private fun SettingsDestinationContent() {
        val controller = settingsControllerState
        if (controller == null) {
            NotConnectedPlaceholder()
            return
        }
        SettingsScreen(
            controller = controller,
            onSignInSteamClick = { launchSteamLogin(controller.buildSteamLoginUrl()) },
            onReconnectClick = { openOnboarding(OnboardingMode.RECONNECT) },
            onDisconnected = {
                refreshVaultApiClient()
                openOnboarding(OnboardingMode.FIRST_RUN)
            },
            onRequestNotificationPermission = { requestNotificationPermission() },
            onOpenClientsClick = { clientsControllerState?.open(lifecycleScope) },
            demoMode = demoState != null,
        )
    }

    /** WP APP-DEMO review round 2 (S2): the ONE bit of state this Activity
     * saves across a configuration change -- see [onCreate]'s restore
     * logic for why a plain boolean (not the [DemoState] itself) is
     * exactly the right amount of persistence here. */
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(KEY_WAS_IN_DEMO_MODE, demoState != null)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    /** Dispatches an incoming [Intent] to whichever of this Activity's two
     * intent-driven entry points it matches (WP 4b.8 added the second):
     * a Steam OpenID redirect (data URI, see below) or a notification tap
     * (a plain extra, see [handleNotificationTap]). Neither carries the
     * other's payload, so the two checks are independent -- a notification
     * Intent has no `dataString` at all and falls through the first check
     * immediately.
     *
     * WP APP-FIX-1 (S2): a consumed OpenID callback is STRIPPED from the
     * Intent (`intent.data = null`) once it has been read, because the
     * Activity's current Intent is re-delivered to `onCreate` on a
     * configuration-change recreation (rotation) -- without this, the same
     * callback URL would be re-processed after a rotation and burn the next
     * pending login attempt via `PendingLoginState.consume()`'s single-use
     * rule (fail closed, but a needless "expired" error on the user's NEXT
     * sign-in). The same applies to a notification tap's extras, see
     * [handleNotificationTap]. After process death the strip does not
     * survive: Android hands back the ORIGINAL launch Intent, which is
     * harmless -- the process-scoped pending state is empty then, and a
     * callback (singleTask) arrives via `onNewIntent`, not the launch
     * Intent. */
    private fun handleIntent(intent: Intent?) {
        handleNotificationTap(intent)

        val data = intent?.dataString ?: return
        if (!data.startsWith(SteamOpenIdConfig.RETURN_TO)) return
        intent.data = null

        lifecycleScope.launch {
            val settings = settingsControllerState
            when {
                showOnboarding -> onboardingController.completeSteamLogin(data)
                // WP APP-DEMO review round 2 (S1): a demo-backed
                // SettingsController never shows Steam identity at all
                // (SettingsScreen.kt's SteamIdentitySection is gated off
                // entirely while demoMode is true) -- routing a completion
                // into it would update state nothing on screen reads, and
                // the ONLY way this callback can even arrive while
                // `demoState != null` is the narrow race below, not a
                // sign-in legitimately started from THIS (demo) screen,
                // since that action is not offered here. Falls through to
                // the same "unroutable" handling the pre-existing else
                // branch already gives a dropped callback.
                settings != null && demoState == null -> settings.completeSteamLogin(data)
                else -> {
                    // Review fix (N2): neither screen is currently active to
                    // route this into (e.g. the connection was disconnected
                    // between launching the Custom Tab and the redirect
                    // arriving) -- still consume the pending login state
                    // directly through the repository, ignoring the result,
                    // so a dropped/unroutable callback cannot leave
                    // PendingLoginState holding a value forever. This is
                    // what makes "single-use" literally true regardless of
                    // which screen happens to be showing when the redirect
                    // lands, not just when a controller is listening.
                    //
                    // WP APP-DEMO residual (S1, not fixed -- documented):
                    // this still calls SteamIdentityRepository.completeLogin,
                    // which persists steamId64 to CredentialStore on a VALID
                    // completion regardless of caller -- unchanged, pre-
                    // existing behaviour this WP does not touch (brief
                    // constraint 5: no OpenID/identity code changes). The
                    // narrow race this can combine with (a sign-in started
                    // during onboarding, completed only AFTER the user
                    // skipped to demo mid-flow) is not closed by this WP;
                    // recorded here rather than silently.
                    identityRepository.completeLogin(data)
                }
            }
        }
    }

    private fun launchSteamLogin(url: String) {
        CustomTabsIntent.Builder().build().launchUrl(this, Uri.parse(url))
    }

    /**
     * WP 4b.8: a tap on a notification posted by
     * [dev.steamvault.app.notifications.AndroidNotificationPoster] carries
     * [NotificationRouting.EXTRA_DESTINATION] (a [Destination.name] string,
     * see that object's kdoc). Ignored while onboarding is showing --
     * there is no bottom nav to switch yet, and onboarding's own completion
     * flow already lands on [Destination.LIBRARY] via [refreshVaultApiClient].
     * An unrecognized/missing extra value is a silent no-op (`valueOf`
     * throwing is caught defensively: this Activity is `exported="true"`,
     * so any app on the device can send it an Intent carrying an arbitrary
     * string under this extra's key, and a stale `PendingIntent` from a
     * previous app version's differently-named enum constant is a real, if
     * unlikely, forward-compat edge case -- neither is worth crashing on).
     *
     * WP APP-FIX-1 (S2): both extras are REMOVED from the Intent once
     * consumed, for the same reason [handleIntent] strips a consumed OpenID
     * callback -- the Activity's current Intent is re-delivered on every
     * recreation, and re-applying a stale tap would yank the user back to
     * the notification's destination (or re-open the clients sheet) after
     * a rotation they made minutes later.
     *
     * WP 4b.10: [NotificationRouting.EXTRA_OPEN_CLIENTS_SHEET] is checked
     * independently of [NotificationRouting.EXTRA_DESTINATION] -- a bypass
     * event carries the sheet extra and NO destination extra at all (see
     * `NotificationRouting.destinationFor`'s kdoc: bypass events no longer
     * switch [Destination]), so neither branch below early-returns on the
     * other being absent; a plain launch [Intent] (neither extra set)
     * degrades to two no-ops, same net effect the old single early-return
     * had for that case.
     */
    private fun handleNotificationTap(intent: Intent?) {
        if (intent == null || showOnboarding) return

        intent.getStringExtra(NotificationRouting.EXTRA_DESTINATION)?.let { destinationName ->
            intent.removeExtra(NotificationRouting.EXTRA_DESTINATION)
            try {
                destination = Destination.valueOf(destinationName)
            } catch (_: IllegalArgumentException) {
                // unrecognized/forward-compat value -- ignored, see kdoc above.
            }
        }

        if (intent.getBooleanExtra(NotificationRouting.EXTRA_OPEN_CLIENTS_SHEET, false)) {
            intent.removeExtra(NotificationRouting.EXTRA_OPEN_CLIENTS_SHEET)
            clientsControllerState?.open(lifecycleScope)
        }
    }

    /** Launched from [dev.steamvault.app.ui.settings.SettingsScreen]'s
     * Notifications section. No-op below API 33 -- see
     * [notificationPermissionLauncher]'s kdoc. */
    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33) {
            notificationPermissionLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    companion object {
        /** WP APP-DEMO review round 2 (S2) -- see [onSaveInstanceState]/[onCreate]. */
        private const val KEY_WAS_IN_DEMO_MODE = "dev.steamvault.app.WAS_IN_DEMO_MODE"

        /**
         * WP APP-FIX-1 (P1): the pending Steam OpenID `state` holder lives
         * here, at PROCESS scope, not on the Activity instance. The login
         * round trip leaves this Activity for a Custom Tab, and Android may
         * recreate the Activity in the meantime (rotation while the browser
         * is in front; the OS reclaiming it) -- a per-instance holder would
         * then be empty when the callback arrives and every such sign-in
         * would fail "expired" once for no user-visible reason. This is the
         * smallest wiring that makes `SteamIdentityRepositoryImpl`'s
         * "outlives Activity recreation, not process death" kdoc TRUE: a
         * companion `val` is created once per process, is never persisted
         * (process death still fails closed, by design -- the state is a
         * one-shot secret and belongs in no Bundle or file), and there is no
         * other process-scoped wiring in this app to mirror
         * (`VaultApplication` only runs idempotent `ensure*` calls).
         */
        private val PROCESS_PENDING_LOGIN_STATE = PendingLoginState()
    }
}

/** Shown for Library/Downloads/Settings if, somehow, the connection
 * disappears out from under a still-composed screen (e.g. a stale
 * recomposition mid-disconnect) -- normally unreachable in practice since
 * [dev.steamvault.app.ui.nav.Destination] is a plain state switch and
 * disconnecting always routes through [MainActivity.openOnboarding]. */
@Composable
private fun NotConnectedPlaceholder() {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = stringResource(R.string.not_connected_title),
            style = MaterialTheme.typography.bodyLarge,
        )
        Text(
            text = stringResource(R.string.not_connected_body),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
