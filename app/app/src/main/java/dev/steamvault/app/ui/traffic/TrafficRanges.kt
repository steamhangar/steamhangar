package dev.steamvault.app.ui.traffic

/**
 * The ranges the user chose on the Clients page, the Downloads statistics
 * and the Downloads history (WP D15d review). Held by `MainActivity` above
 * the destinations, so a tab switch keeps them -- the web keeps them as
 * module state of its views. Like `MainActivity.destination` it is a plain
 * field of the Activity instance: a recreation (rotation, process death)
 * starts again from the defaults. Plain vars: the screens read them only
 * when they build their polls, never during composition.
 */
class TrafficRanges {
    /** Clients page, web `views/clients.js` default. */
    var clients: String = "7d"

    /** Downloads statistics header, web `traffic.range` default. */
    var statistics: String = "24h"

    /** Downloads history filter, web `history.range` default. */
    var history: String = "all"
}
