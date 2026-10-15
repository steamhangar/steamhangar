package dev.steamvault.app.ui.library.logic

/**
 * The one fallback title for an app nothing could name (WP ID-AUDIT).
 *
 * Twin of web `lib/app-names.js` (`UNKNOWN_APP_NAME`): names reach the UI
 * only from vault-api's stored rows, the tool-app list and the owned-games
 * list the relay key unlocks; when none knows an app the title is this
 * neutral text, never "App <id>". The technical id stays on the detail lines
 * built for it (package facts, the sheet's id line). Pinned by string
 * equality in `AppNamesTest` and the existing twin tests; keep both in sync.
 */
const val UNKNOWN_APP_NAME = "Unknown app"
