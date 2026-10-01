import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// ---------------------------------------------------------------------
// Release signing (WP 4b.9). See app/README.md's "Release build, signing,
// and distribution" section for the full walkthrough (creating the
// keystore, verifying the signed APK, where the artefact lands).
//
// Never a secret in the repo/tree: values come from a GITIGNORED
// `app/keystore.properties` file (reserved in app/.gitignore since
// WP 4b.1; template committed at `app/keystore.properties.example`) or
// from environment variables as a fallback (CI-style) -- the properties
// file wins when both are present. This app never generates a keystore
// itself; the user creates one with `keytool` (README) and points these
// values at it.
//
// `assembleDebug` is completely unaffected either way: debug builds keep
// using AGP's own auto-generated debug keystore, which this file never
// touches. `releaseSigningConfigured` gates BOTH whether `signingConfigs
// ["release"]` exists at all (so an absent config can never be silently
// treated as "use the debug/no-op default") and the `gradle.taskGraph
// .whenReady` guard below, which turns "no keystore configured" into an
// immediate, actionable build failure for the specific tasks that would
// otherwise happily produce an UNSIGNED release APK/AAB.
// ---------------------------------------------------------------------
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.exists()) {
        keystorePropertiesFile.inputStream().use { load(it) }
    }
}

fun releaseSigningValue(propertyKey: String, envVarName: String): String? =
    keystoreProperties.getProperty(propertyKey)?.takeIf { it.isNotBlank() }
        ?: System.getenv(envVarName)?.takeIf { it.isNotBlank() }

val releaseStoreFilePath = releaseSigningValue("storeFile", "VAULT_RELEASE_STORE_FILE")
val releaseStorePassword = releaseSigningValue("storePassword", "VAULT_RELEASE_STORE_PASSWORD")
val releaseKeyAlias = releaseSigningValue("keyAlias", "VAULT_RELEASE_KEY_ALIAS")
val releaseKeyPassword = releaseSigningValue("keyPassword", "VAULT_RELEASE_KEY_PASSWORD")

val releaseSigningConfigured: Boolean =
    releaseStoreFilePath != null && releaseStorePassword != null &&
        releaseKeyAlias != null && releaseKeyPassword != null

// Fail BEFORE any task executes (task-graph-ready time, not mid-build) when
// the requested tasks would package/install a release artefact but no
// signing config is present -- deliberately scoped to just these task
// names, not every task whose name contains "Release": `test`,
// `testReleaseUnitTest`, `lintDebug`, `compileReleaseKotlin` etc. need no
// signing config at all and must keep working with zero keystore setup
// (this WP's DoD). Matched by task NAME rather than path since this is a
// single-module build (`:app` only, settings.gradle.kts), and `allTasks`
// already contains the fully-resolved graph, so a bare `assemble`/`bundle`
// that transitively pulls in `assembleRelease`/`bundleRelease` is caught
// exactly the same as requesting that task directly.
//
// Review fix (S1): `assembleRelease`/`bundleRelease` are aggregate
// lifecycle tasks that DEPEND ON `packageRelease`/`packageReleaseBundle`
// -- but `./gradlew packageRelease` (or `packageReleaseBundle`) directly
// bypassed this guard entirely, measured: `BUILD SUCCESSFUL`, producing
// `app/build/outputs/apk/release/app-release-unsigned.apk` with no
// `META-INF` signature and no silent fallback to the debug key (bounded
// severity, but still a falsifiable "never a silently unsigned APK" claim
// in one command). Both are now in the guarded set; `installRelease` is
// ALSO listed even though it is currently inert on its own (AGP refuses
// to even create an install task for an unsignable variant, and once
// signing IS configured this guard can never fire for it) -- kept as a
// forward-compat belt for whatever future AGP version might change that,
// and because listing it costs nothing.
gradle.taskGraph.whenReady {
    val releasePackagingTaskNames = setOf(
        "assembleRelease", "bundleRelease", "installRelease",
        "packageRelease", "packageReleaseBundle",
    )
    val requestsReleasePackaging = allTasks.any { it.name in releasePackagingTaskNames }
    if (requestsReleasePackaging && !releaseSigningConfigured) {
        throw GradleException(
            """
            |Release signing is not configured -- refusing to build an unsigned release artefact.
            |
            |Create app/keystore.properties (gitignored, NEVER commit it) from the
            |committed template app/keystore.properties.example, filling in your own
            |keystore's storeFile/storePassword/keyAlias/keyPassword. Equivalently, set
            |the VAULT_RELEASE_STORE_FILE / VAULT_RELEASE_STORE_PASSWORD /
            |VAULT_RELEASE_KEY_ALIAS / VAULT_RELEASE_KEY_PASSWORD environment variables.
            |
            |See app/README.md's "Release build, signing, and distribution" section for
            |the exact keytool command to create a keystore and how to verify the signed
            |APK afterwards. assembleDebug/test/lintDebug are unaffected by this check.
            """.trimMargin(),
        )
    }
}

// ---------------------------------------------------------------------
// Release version (WP APP-FIX-1, S5/S7). versionName/versionCode were
// hard-wired literals, so every published APK carried 0.1.0 / 1 no matter
// which tag built it. Both now come from the environment with the old
// literals as the fallback (a local `assembleDebug` with nothing set keeps
// building exactly as before). .github/workflows/publish.yml sets both
// (name from the release tag, code = github.run_number; see app/README.md
// "Release build ...") -- and a code that is not a positive integer fails
// CONFIGURATION with the offending value, never a silent 1. Caveat:
// run_number restarts if the workflow is renamed or recreated, and Android
// rejects the lower versionCode as a downgrade.
// ---------------------------------------------------------------------
val releaseVersionName: String =
    System.getenv("VAULT_RELEASE_VERSION_NAME")?.takeIf { it.isNotBlank() } ?: "0.1.0"

val releaseVersionCode: Int =
    System.getenv("VAULT_RELEASE_VERSION_CODE")?.takeIf { it.isNotBlank() }?.let { raw ->
        raw.toIntOrNull()?.takeIf { it > 0 }
            ?: throw GradleException(
                "VAULT_RELEASE_VERSION_CODE must be a positive integer (Android versionCode), got '$raw'.",
            )
    } ?: 1

android {
    // Application id is PROVISIONAL — see app/README.md "Provisional
    // decisions". Final naming (and therefore the id) is a user/release
    // decision, not an engineering one.
    namespace = "dev.steamvault.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.steamvault.app"
        minSdk = 26
        targetSdk = 35
        versionCode = releaseVersionCode
        versionName = releaseVersionName

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        // Only created when the four values above actually resolved -- an
        // absent config is the whole point (see the taskGraph guard above),
        // not something to paper over with empty-string defaults that
        // `signingConfigs.create` would accept without complaint.
        if (releaseSigningConfigured) {
            create("release") {
                storeFile = file(releaseStoreFilePath!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            if (releaseSigningConfigured) {
                signingConfig = signingConfigs.getByName("release")
            }
            // No `else` branch: leaving `signingConfig` unset when not
            // configured is intentional -- AGP's own behaviour for an
            // unsigned release build type is exactly what the
            // `gradle.taskGraph.whenReady` guard above exists to intercept
            // before it can produce a real (unsigned) artefact.
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = false
        }
    }

    lint {
        // AGP lint task is this WP's chosen static-analysis gate (documented
        // in app/README.md — ktlint was the alternative, not used). Treat
        // warnings as build-breaking so the gate is meaningful in CI later.
        warningsAsErrors = true
        abortOnError = true
        // No device/emulator in this environment (WP brief) — baseline
        // profile / resource-shrinker checks that need one are irrelevant
        // at this stage.
        //
        // AndroidGradlePluginVersion / GradleDependency are disabled
        // deliberately, not out of neglect: every version in
        // gradle/libs.versions.toml is pinned to what is contemporaneous
        // with and known-compatible with the Gradle 8.10.2 bootstrap this
        // WP was handed (repo rule: pinned versions only — see the catalog
        // file's header comment for the exact compatibility reasoning).
        // These two checks just nag for "is a newer release out" and would
        // otherwise fail the build every time upstream ships a release,
        // regardless of whether it is actually compatible here — that is
        // a human upgrade decision (a future WP), not a lint-fixable defect.
        //
        // OldTargetApi joins them for the same reason (first CI compile,
        // 2026-10-01): lint's "latest API level" moved past 35 by the
        // calendar, not by any change here. Raising targetSdk switches on
        // new platform behaviour (e.g. enforced edge-to-edge) and needs a
        // real-device pass, so it is a planned upgrade WP, not a lint fix.
        disable += setOf("AndroidGradlePluginVersion", "GradleDependency", "OldTargetApi")
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)

    // WP 4b.2: API client + connectivity profiles + credential storage.
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.okhttp)
    implementation(libs.androidx.security.crypto)

    // WP 4b.3: Steam identity -- CustomTabsIntent for the OpenID login page.
    implementation(libs.androidx.browser)

    // WP 4b.4: Library view -- Steam CDN cover-art loading (see
    // gradle/libs.versions.toml's WP 4b.4 comment for the Coil-vs-hand-rolled
    // justification).
    implementation(libs.coil.compose)

    // WP 4b.8: notifications via WorkManager -- see gradle/libs.versions.toml's
    // WP 4b.8 comment for both pins' justification.
    implementation(libs.androidx.work.runtime.ktx)
    implementation(libs.androidx.lifecycle.process)

    debugImplementation(libs.androidx.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.okhttp.tls)
    testImplementation(libs.kotlinx.coroutines.test)
}
