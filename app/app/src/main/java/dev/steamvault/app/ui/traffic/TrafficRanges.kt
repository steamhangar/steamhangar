package dev.steamvault.app.ui.traffic

import dev.steamvault.app.ui.traffic.logic.DEFAULT_STATS_RANGE

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

    /** Downloads statistics header, web `traffic.range` default (6 h, WP
     * GRAPH-ZOOM). Kept for the Activity's lifetime like the other ranges;
     * the web additionally remembers it per browser. */
    var statistics: String = DEFAULT_STATS_RANGE

    /** Downloads history filter, web `history.range` default. */
    var history: String = "all"
}
