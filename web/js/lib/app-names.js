/**
 * The one fallback title for an app nothing could name (WP ID-AUDIT).
 *
 * Names reach the UI only from vault-api's stored rows, the tool-app list and
 * the owned-games list the relay key unlocks. When none of them knows an app
 * the title is this neutral text, never "App <id>": a bare Steam id reads as
 * a bug where a name belongs. The technical id stays available on detail
 * lines that exist for it (job cards, the sheet's id line, package facts).
 * The Android twin is `AppNames.kt` (`UNKNOWN_APP_NAME`); keep both in sync.
 */
export const UNKNOWN_APP_NAME = "Unknown app";
