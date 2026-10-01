package dev.steamvault.app.ui.onboarding

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.ui.onboarding.logic.OnboardingStep
import dev.steamvault.app.ui.onboarding.logic.onboardingProgressPercent
import kotlinx.coroutines.launch

/**
 * The onboarding flow (WP 4b.7 brief): 3 steps per the mockup (Connect ->
 * Steam identity (optional) -> Done), adapted to what this app's step 1
 * actually needs -- see `ui/onboarding/logic/OnboardingSteps.kt`'s kdoc for
 * why that differs from the web port's step 1.
 *
 * **Full-screen swap, not a modal overlay.** `web/js/onboarding.js` renders
 * this as a `role="dialog"` overlay above the app shell; this app instead
 * swaps it in as `MainActivity`'s ENTIRE `setContent` body (no bottom nav,
 * no Scaffold behind it), the same "plain state-based switcher" navigation
 * choice `ui/nav/Destination.kt`'s kdoc already establishes for this
 * codebase rather than introducing a second, heavier overlay/dialog
 * mechanism just for this one screen -- a first-run open has nothing behind
 * it to reveal anyway, and a reconnect open's [OnboardingController
 * .canCancelWithoutFinishing] "Cancel" action gives the same "bail out with
 * the existing connection untouched" escape web's Escape-in-reconnect-mode
 * gives, just as an explicit button instead of a keyboard shortcut (Android
 * has no universal "Escape" input).
 *
 * @param onFinished called once, after [OnboardingController.finish] has
 *   already run -- the caller ([dev.steamvault.app.MainActivity]) is
 *   responsible for rebuilding its [dev.steamvault.app.net.VaultApiClient]
 *   from the now-populated [dev.steamvault.app.storage.CredentialStore] and
 *   returning to the normal app shell.
 * @param onCancelled called when a reconnect attempt is dismissed without
 *   finishing -- never called in first-run mode ([OnboardingController
 *   .canCancelWithoutFinishing] is `false` there, so the action is not shown).
 * @param onDemoSkip WP APP-DEMO: called when "Skip for now — browse in demo
 *   mode" is tapped ([OnboardingController.canSkipToDemo] gates when this is
 *   even shown). The caller ([dev.steamvault.app.MainActivity]) enters demo
 *   mode with a fresh [dev.steamvault.app.demo.DemoState] and dismisses this
 *   screen -- nothing here is persisted to
 *   [dev.steamvault.app.storage.CredentialStore], mirroring
 *   [OnboardingController.finish]'s own "only Done finishes onboarding for
 *   real" boundary.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OnboardingScreen(
    controller: OnboardingController,
    onFinished: () -> Unit,
    onCancelled: () -> Unit,
    onLaunchSteamLogin: (String) -> Unit,
    onDemoSkip: () -> Unit = {},
) {
    val scope = rememberCoroutineScope()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stepTitle(controller.step)) },
                actions = {
                    if (controller.canCancelWithoutFinishing) {
                        TextButton(onClick = onCancelled) { Text(stringResource(R.string.onboarding_cancel)) }
                    }
                },
            )
        },
        bottomBar = {
            Column {
                OnboardingNav(
                    controller = controller,
                    scope = scope,
                    onFinished = onFinished,
                )
                if (controller.canSkipToDemo) {
                    TextButton(onClick = onDemoSkip, modifier = Modifier.fillMaxWidth()) {
                        Text(stringResource(R.string.onboarding_demo_skip))
                    }
                }
            }
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            LinearProgressIndicator(
                progress = { onboardingProgressPercent(controller.step) / 100f },
                modifier = Modifier.fillMaxWidth(),
            )
            when (controller.step) {
                OnboardingStep.CONNECT -> ConnectStep(controller, scope)
                OnboardingStep.STEAM -> SteamStep(controller, scope, onLaunchSteamLogin)
                OnboardingStep.DONE -> DoneStep(controller)
            }
        }
    }
}

@Composable
private fun stepTitle(step: OnboardingStep): String = when (step) {
    OnboardingStep.CONNECT -> stringResource(R.string.onboarding_step1_title)
    OnboardingStep.STEAM -> stringResource(R.string.onboarding_step2_title)
    OnboardingStep.DONE -> stringResource(R.string.onboarding_step3_title)
}

@Composable
private fun OnboardingNav(
    controller: OnboardingController,
    scope: kotlinx.coroutines.CoroutineScope,
    onFinished: () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(16.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (controller.step != OnboardingStep.CONNECT) {
            OutlinedButton(onClick = { controller.back() }, modifier = Modifier.weight(1f)) {
                Text(stringResource(R.string.onboarding_back))
            }
        }
        Button(
            onClick = {
                if (controller.step == OnboardingStep.DONE) {
                    controller.finish()
                    onFinished()
                } else {
                    controller.next()
                }
            },
            enabled = controller.canAdvance,
            modifier = Modifier.weight(1f),
        ) {
            Text(
                if (controller.step == OnboardingStep.DONE) {
                    stringResource(R.string.onboarding_finish)
                } else {
                    stringResource(R.string.onboarding_continue)
                },
            )
        }
    }
}

// ---------------------------------------------------------------------
// Step 1 -- Connect
// ---------------------------------------------------------------------

@Composable
private fun ConnectStep(controller: OnboardingController, scope: kotlinx.coroutines.CoroutineScope) {
    Text(stringResource(R.string.onboarding_step1_lede), style = MaterialTheme.typography.bodyMedium)

    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        SegmentedButton(
            selected = controller.profileChoice == ConnectivityProfileChoice.SYSTEM_VPN,
            onClick = { controller.profileChoice = ConnectivityProfileChoice.SYSTEM_VPN },
            shape = SegmentedButtonDefaults.itemShape(index = 0, count = 2),
        ) { Text(stringResource(R.string.onboarding_profile_system_vpn)) }
        SegmentedButton(
            selected = controller.profileChoice == ConnectivityProfileChoice.PUBLIC_DOMAIN,
            onClick = { controller.profileChoice = ConnectivityProfileChoice.PUBLIC_DOMAIN },
            shape = SegmentedButtonDefaults.itemShape(index = 1, count = 2),
        ) { Text(stringResource(R.string.onboarding_profile_public_domain)) }
    }
    Text(
        text = if (controller.profileChoice == ConnectivityProfileChoice.SYSTEM_VPN) {
            stringResource(R.string.onboarding_profile_hint_system_vpn)
        } else {
            stringResource(R.string.onboarding_profile_hint_public_domain)
        },
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    OutlinedTextField(
        value = controller.baseUrlText,
        onValueChange = { controller.baseUrlText = it },
        label = { Text(stringResource(R.string.onboarding_base_url_label)) },
        placeholder = {
            Text(
                if (controller.profileChoice == ConnectivityProfileChoice.SYSTEM_VPN) {
                    stringResource(R.string.onboarding_base_url_placeholder_vpn)
                } else {
                    stringResource(R.string.onboarding_base_url_placeholder_public)
                },
            )
        },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )

    // WP APP-FIX-1 (S1): the vault API key is a secret and is masked like a
    // password by default -- shoulder-surfing and screen recording were
    // the reviewer's concern, not storage (CredentialStore already encrypts
    // it). `rememberSaveable` keeps the user's show/hide choice across a
    // rotation only; the key text itself lives in the controller. No eye
    // glyph ships with material-icons-core (NavIcons.kt's kdoc: the extended
    // pack is deliberately not a dependency), so the toggle is a plain text
    // button -- same "no icon pack for one glyph" call as WP 4b.4.
    var apiKeyVisible by rememberSaveable { mutableStateOf(false) }
    OutlinedTextField(
        value = controller.apiKeyText,
        onValueChange = { controller.apiKeyText = it },
        label = { Text(stringResource(R.string.onboarding_api_key_label)) },
        singleLine = true,
        visualTransformation = if (apiKeyVisible) VisualTransformation.None else PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false),
        trailingIcon = {
            TextButton(onClick = { apiKeyVisible = !apiKeyVisible }) {
                Text(
                    stringResource(
                        if (apiKeyVisible) R.string.onboarding_api_key_hide else R.string.onboarding_api_key_show,
                    ),
                )
            }
        },
        modifier = Modifier.fillMaxWidth(),
    )

    Button(
        onClick = { scope.launch { controller.testConnection() } },
        enabled = !controller.testing,
        modifier = Modifier.fillMaxWidth(),
    ) {
        if (controller.testing) {
            CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp))
        }
        Text(
            if (controller.testing) stringResource(R.string.onboarding_testing) else stringResource(R.string.onboarding_test_connection),
        )
    }

    controller.connectionMessage?.let { message ->
        Text(
            text = message,
            color = if (controller.connectionOk) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
            style = MaterialTheme.typography.bodyMedium,
        )
    }

    Text(
        text = stringResource(R.string.onboarding_connect_hint),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

// ---------------------------------------------------------------------
// Step 2 -- Steam identity (optional)
// ---------------------------------------------------------------------

@Composable
private fun SteamStep(
    controller: OnboardingController,
    scope: kotlinx.coroutines.CoroutineScope,
    onLaunchSteamLogin: (String) -> Unit,
) {
    Text(stringResource(R.string.onboarding_step2_lede), style = MaterialTheme.typography.bodyMedium)

    val identity = controller.identityState
    if (!identity.isSignedIn) {
        controller.loginError?.let {
            Text(
                text = stringResource(R.string.identity_login_failed, it),
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
            )
        }
        Button(onClick = { onLaunchSteamLogin(controller.buildSteamLoginUrl()) }) {
            Text(stringResource(R.string.identity_sign_in))
        }
    } else {
        Text(
            text = stringResource(R.string.identity_steamid_label, identity.steamId64.orEmpty()),
            style = MaterialTheme.typography.bodyLarge,
        )
        Text(
            text = identity.personaName?.let { stringResource(R.string.identity_persona_label, it) }
                ?: stringResource(R.string.identity_persona_unknown),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedButton(onClick = { controller.signOutSteam() }) {
            Text(stringResource(R.string.identity_sign_out))
        }
    }
}

// ---------------------------------------------------------------------
// Step 3 -- Done
// ---------------------------------------------------------------------

@Composable
private fun DoneStep(controller: OnboardingController) {
    Text(stringResource(R.string.onboarding_done_lede), style = MaterialTheme.typography.bodyMedium)

    val identity = controller.identitySummary()
    Text(
        stringResource(
            R.string.onboarding_summary_connection,
            stringResource(R.string.onboarding_summary_connection_verified),
        ),
    )
    Text(
        stringResource(
            R.string.onboarding_summary_steam,
            if (identity.isSignedIn) {
                stringResource(R.string.onboarding_summary_steam_configured)
            } else {
                stringResource(R.string.onboarding_summary_steam_not_linked)
            },
        ),
    )
}
