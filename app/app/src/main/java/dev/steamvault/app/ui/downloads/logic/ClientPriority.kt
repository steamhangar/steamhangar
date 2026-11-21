package dev.steamvault.app.ui.downloads.logic

import dev.steamvault.app.net.model.ClientPriorityOut
import dev.steamvault.app.net.model.JobSummary
import java.time.DateTimeException
import java.time.Instant
import java.time.ZoneId

/**
 * Client priority on the Downloads screen (WP CAP-503-FIX-1b) -- Kotlin
 * twin of `web/js/lib/client-priority.js`; every literal of
 * [ClientPriorityWording] is pinned against the web file by
 * `ClientPriorityWebTwinTest`, so change one, change both.
 *
 * vault-api holds the prefill runner while a device loads through the cache
 * and lets it carry on one idle gap (120 s) after the last sighting
 * (CAP-503-FIX-1a, api/README.md "Client priority over the prefill
 * runner"). `GET /v1/client-priority` says whether it holds, for which
 * devices and until when ([ClientPriorityOut.resume_at]); a job row says why
 * it is paused (`pause_reason`) and whether the operator resumed it during a
 * hold (`client_override`, operator decision 2026-11-21 Weg B).
 *
 * One text per state: a job paused for a device names the device(s) and the
 * expected resume time ([clientHoldNote]); the queue gets one note while the
 * runner is held (queued GC jobs wait too, Weg A, [queueHoldNote]); a job
 * resumed during the hold reads "Runs alongside <device> by your choice."
 * ([clientOverrideNote]); a restored hold (vault-api restarted, no device
 * seen since) names no device and claims no sighting; a manual pause keeps
 * the screen's own note (these return `null`).
 */
object ClientPriorityWording {
    const val HELD_SEEN =
        "Paused to give {devices} the full speed through the cache. Resumes by itself at about {time} unless a device keeps loading. Resume runs it now, alongside {devices}."
    const val HELD_RESTORED =
        "Paused for device priority before vault-api restarted. No device has been seen loading since; resumes by itself at about {time} unless one starts."
    const val HELD_PENDING =
        "Paused so a device loading through the cache gets the full speed. Resumes by itself once no device is loading."
    const val PAUSING_CLIENT =
        "Pausing so a device loading through the cache gets the full speed. It resumes by itself, from the cache, not from zero."
    const val OVERRIDE_SEEN = "Runs alongside {devices} by your choice."
    const val OVERRIDE_ANY = "Runs alongside any loading device by your choice."
    const val QUEUE_SEEN =
        "Queued downloads wait to give {devices} the full speed through the cache. They carry on at about {time} unless a device keeps loading."
    const val QUEUE_RESTORED =
        "Queued downloads wait: device priority was on before vault-api restarted, and no device has been seen loading since. They carry on at about {time} unless one starts."
    const val QUEUE_PENDING = "Queued downloads wait so a device loading through the cache gets the full speed."
}

/** The hold poll's cadence (web `CLIENT_PRIORITY_POLL_MS`): the controller
 * looks every 5 s, so a faster poll would show nothing new. */
const val CLIENT_PRIORITY_POLL_MS = 5000L

/** Device names as one phrase: "A", "A and B", "A, B and C". */
fun joinDevices(names: List<String>): String =
    if (names.size <= 1) names.joinToString("") else "${names.dropLast(1).joinToString(", ")} and ${names.last()}"

/** `true` only when the answer says the runner is held right now. */
fun isHolding(cp: ClientPriorityOut?): Boolean = cp != null && cp.holding

/** The devices of the last sighting (Clients page name, else the address),
 * or `null` when there is none to name: a restored hold, or no device. */
fun holdDevices(cp: ClientPriorityOut?): String? {
    if (cp == null || !isHolding(cp) || cp.restored) return null
    val names = cp.clients.map { c -> c.name?.trim()?.takeIf { it.isNotEmpty() } ?: c.addr }.filter { it.isNotEmpty() }
    return if (names.isEmpty()) null else joinDevices(names)
}

/** The expected resume time as local "HH:MM" (24 h, web `formatClockHHMM`),
 * or `null` without a hold or a parseable `resume_at`. */
fun holdResumeClock(cp: ClientPriorityOut?, zone: ZoneId = ZoneId.systemDefault()): String? {
    if (cp == null || !isHolding(cp)) return null
    val iso = cp.resume_at ?: return null
    return try {
        val t = Instant.parse(iso).atZone(zone)
        "${t.hour.toString().padStart(2, '0')}:${t.minute.toString().padStart(2, '0')}"
    } catch (_: DateTimeException) {
        null
    }
}

/** The note of a job paused for a device, or `null` for any other job (a
 * manual pause keeps the screen's own note). Without a current hold it
 * names no device and no time. */
fun clientHoldNote(job: JobSummary, cp: ClientPriorityOut?, zone: ZoneId = ZoneId.systemDefault()): String? {
    if (job.status != "paused" || job.pause_reason != "client") return null
    val time = holdResumeClock(cp, zone) ?: return ClientPriorityWording.HELD_PENDING
    if (cp!!.restored) return fillTemplate(ClientPriorityWording.HELD_RESTORED, mapOf("time" to time))
    val devices = holdDevices(cp) ?: return ClientPriorityWording.HELD_PENDING
    return fillTemplate(ClientPriorityWording.HELD_SEEN, mapOf("devices" to devices, "time" to time))
}

/** The stop note of a running job vault-api is pausing for a device, else `null`. */
fun clientPausingNote(job: JobSummary): String? =
    if (job.status == "running" && job.stop_request == "pause" && job.pause_reason == "client") {
        ClientPriorityWording.PAUSING_CLIENT
    } else {
        null
    }

/** The note of a job the operator resumed during a hold, while the runner
 * is held; `null` otherwise. */
fun clientOverrideNote(job: JobSummary, cp: ClientPriorityOut?): String? {
    if (!job.client_override || !isHolding(cp)) return null
    if (job.status != "running" && job.status != "queued") return null
    val devices = holdDevices(cp) ?: return ClientPriorityWording.OVERRIDE_ANY
    return fillTemplate(ClientPriorityWording.OVERRIDE_SEEN, mapOf("devices" to devices))
}

/** The note over the queue while the runner is held and at least one
 * queued job waits (a job with `client_override` does not), else `null`. */
fun queueHoldNote(queued: List<JobSummary>, cp: ClientPriorityOut?, zone: ZoneId = ZoneId.systemDefault()): String? {
    if (!isHolding(cp) || queued.none { !it.client_override }) return null
    val time = holdResumeClock(cp, zone) ?: return ClientPriorityWording.QUEUE_PENDING
    if (cp!!.restored) return fillTemplate(ClientPriorityWording.QUEUE_RESTORED, mapOf("time" to time))
    val devices = holdDevices(cp) ?: return ClientPriorityWording.QUEUE_PENDING
    return fillTemplate(ClientPriorityWording.QUEUE_SEEN, mapOf("devices" to devices, "time" to time))
}

/**
 * Every client-priority text of one Downloads render, by job id: [hold]
 * replaces a paused card's manual note, [pausing] its stop note, [alongside]
 * is an extra line on a running card, [queue] the note over the queue.
 */
data class ClientPriorityNotes(
    val hold: Map<Int, String> = emptyMap(),
    val pausing: Map<Int, String> = emptyMap(),
    val alongside: Map<Int, String> = emptyMap(),
    val queue: String? = null,
)

fun clientPriorityNotes(
    partition: PartitionedJobs,
    cp: ClientPriorityOut?,
    zone: ZoneId = ZoneId.systemDefault(),
): ClientPriorityNotes = ClientPriorityNotes(
    hold = partition.paused.mapNotNull { j -> clientHoldNote(j, cp, zone)?.let { j.id to it } }.toMap(),
    pausing = partition.running.mapNotNull { j -> clientPausingNote(j)?.let { j.id to it } }.toMap(),
    alongside = partition.running.mapNotNull { j -> clientOverrideNote(j, cp)?.let { j.id to it } }.toMap(),
    queue = queueHoldNote(partition.queued, cp, zone),
)
