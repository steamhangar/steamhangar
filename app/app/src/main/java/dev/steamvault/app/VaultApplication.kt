package dev.steamvault.app

import android.app.Application
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.disk.DiskCache
import coil.memory.MemoryCache
import dev.steamvault.app.notifications.NotificationChannels
import dev.steamvault.app.notifications.NotificationScheduler

/**
 * Process-wide `Application` (WP 4b.8). The app had no custom `Application`
 * class before this WP (the manifest's `<application>` used the framework
 * default) -- this is the first thing this app needs that must run exactly
 * once per process start regardless of which entry point (launcher icon,
 * notification tap, OpenID redirect) created the task, which is precisely
 * what `Application.onCreate` guarantees and `MainActivity.onCreate` does
 * not (an Activity can be recreated multiple times per process, e.g. on
 * configuration change).
 *
 * Both calls are idempotent (see [NotificationChannels.ensureChannels] and
 * [NotificationScheduler.ensureScheduled]'s own kdocs), so running them on
 * every process start -- including ones where the user never finished
 * onboarding, or has since disconnected -- is safe:
 * [dev.steamvault.app.notifications.NotificationPollWorker] itself is what
 * checks whether a vault-api connection actually exists and succeeds
 * silently if not (see its kdoc's "Fail-soft rules").
 */
class VaultApplication : Application(), ImageLoaderFactory {
    override fun onCreate() {
        super.onCreate()
        NotificationChannels.ensureChannels(this)
        NotificationScheduler.ensureScheduled(this)
    }

    /**
     * WP D10 COVER-CACHE-1: the process-wide Coil loader, with an explicit,
     * sized disk cache for cover art in `cacheDir` (Android may clear it
     * under storage pressure; it is a cache, nothing more). Coil asks for
     * this once, lazily, the first time an image is loaded. Covers come from
     * vault-api's `/v1/covers/...` (long `Cache-Control`, ETag) or Steam's
     * CDN (ten-year `Cache-Control`); Coil honours both headers, so a cover
     * seen once loads from disk afterwards, also offline. A test that
     * installs its own loader with `Coil.setImageLoader` (APP-TEST-1's
     * screenshot test) overrides this factory; `Coil.reset()` restores it.
     */
    override fun newImageLoader(): ImageLoader = buildCoverImageLoader(this)
}

/** Size of Coil's cover disk cache (WP D10). Covers are 100-400 KB, so this
 * holds several hundred of them. */
const val COVER_DISK_CACHE_BYTES: Long = 128L * 1024 * 1024

/** Directory name of that cache inside `cacheDir` (WP D10): the same one
 * Coil 2's default loader used, so covers cached before this change are
 * reused instead of orphaned. */
const val COVER_DISK_CACHE_DIR: String = "image_cache"

/** The loader [VaultApplication] hands Coil (WP D10). */
fun buildCoverImageLoader(app: Application): ImageLoader = ImageLoader.Builder(app)
    .memoryCache { MemoryCache.Builder(app).maxSizePercent(0.20).build() }
    .diskCache {
        DiskCache.Builder()
            .directory(app.cacheDir.resolve(COVER_DISK_CACHE_DIR))
            .maxSizeBytes(COVER_DISK_CACHE_BYTES)
            .build()
    }
    .respectCacheHeaders(true)
    .build()
