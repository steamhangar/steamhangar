"""Generic webhook notifications (WP 3.13).

vault-api can POST a small, STABLE JSON envelope to one operator-supplied URL
when a job concludes or a client newly looks like it is bypassing the cache.
There are no vendor-specific templates (Discord/Slack/ntfy embeds, etc.) —
one schema for every receiver, because a stable generic shape is easier to
adapt on the receiving side (a tiny relay script, or a service that already
ingests generic JSON webhooks) than N vendor formats are to keep in sync here.

The envelope (documented in full in api/README.md "Webhooks")
------------------------------------------------------------
::

    {
      "event": "job.done",
      "timestamp": "2026-08-09T14:03:11Z",
      "vault_name": "homelab",           // omitted entirely if VAULT_NAME is unset
      "payload": { ... event-specific fields, see below ... }
    }

Five events, one schema, ``payload`` is the only part that varies:

* ``job.done`` / ``job.error`` / ``job.cancelled`` — a prefill or GC job
  reached that terminal ``jobs.status`` (``paused`` is NOT terminal and never
  fires anything here). ``payload``: ``id``, ``type``, ``appid``, ``status``,
  plus ``mode`` ("execute"/"dry-run") for a GC job whose mode is known, plus
  ``bytes`` when a byte count was already computed by the caller (GC's
  ``bytes_freed`` — never computed freshly just for the webhook, since that
  would make notification an extra source of work rather than a report on
  work already done).
* ``client.bypass_suspected`` / ``client.bypass_resolved`` — a client flips
  the ``bypass_suspected`` verdict (``GET /v1/clients``) in either direction:
  NEWLY suspected, or a previously-suspected client whose cache-log presence
  returned. Both are TRANSITIONS ONLY, never the steady state in either
  direction — see ``event_sweep.check_bypass_transitions``. ``resolved`` is
  the all-clear that closes the loop ``suspected`` opened: an operator who
  got paged for a possible DNS/IPv6 bypass wants to know when it stopped
  being true, not just when it started. ``payload`` (same shape for both):
  ``client_id``, ``address`` (every address the client's retained agent
  reports arrived from), ``last_seen`` (its most recent cache-log timestamp
  across those addresses — ``null`` for ``suspected`` unless it was seen
  outside the window, and always a real timestamp for ``resolved``, since
  cache-log presence is exactly what caused the flip back).

Hook points — exactly ONE call site per event class
----------------------------------------------------
**Job events**: ``finish_job_and_notify`` below is the single integration
point. Every place a job concludes (``worker.py``'s prefill success/failure/
unowned/exception/cancelled branches, its unknown-job-type guard, and
``gc_execute.run_gc_job``'s crash and normal-completion paths) calls THIS
function instead of ``jobs.finish_job`` directly. Deliberately **not** wired
inside ``jobs.finish_job`` itself: that function is called directly by most
of ``tests/test_job_control.py`` and friends with no ``Settings``/notifier in
scope at all, and plenty of those calls describe states (a stale job
recovered at startup, a unit test's synthetic transition) that were never
real webhook-worthy events. Putting the hook in the callers, where a real
``Settings``/notifier is genuinely in hand, keeps "a job really just
concluded, on a real worker/GC run" as the one thing that can fire this.

**Bypass events**: ``event_sweep.check_bypass_transitions``, called from
``event_sweep.sweep_once`` strictly AFTER ``commit_batch``/``maybe_truncate``
— see that function's docstring. The rule that matters here too: a webhook
must announce a state that has already been committed, never one that could
still roll back.

Delivery: fire-and-forget, bounded, single background thread
--------------------------------------------------------------
``WebhookNotifier.enqueue`` is what every hook point above calls, and it
NEVER blocks and NEVER raises — it does at most a dict->JSON conversion and a
``queue.Queue.put_nowait``. Actual HTTP delivery happens on one dedicated
daemon thread (``_run``/``_deliver``), so a slow or hanging receiver can only
ever delay itself, never the worker thread finishing a job or the scheduler
thread sweeping the event log.

* **Bounded queue** (``MAX_QUEUE_SIZE``). A full queue means delivery is
  falling behind the rate events are produced — dropping the OLDEST queued
  event (not the newest, and not the one just produced) makes room, because
  the newest event is the one most likely to still be actionable. The drop is
  never silent: it is logged at WARNING and counted
  (``WebhookNotifier.dropped_count``).
* **At-most-once delivery, bounded retry.** Each event gets up to
  ``DELIVERY_ATTEMPTS`` (3) HTTP attempts with a short backoff between them.
  Giving up after the last attempt is a WARNING naming the event — never a
  full traceback at ERROR for what is usually just "the receiver is down
  right now", which is an operational fact, not a bug in this code.
* **Per-attempt timeout** (``VAULT_WEBHOOK_TIMEOUT_SECONDS``) bounds each
  attempt so a receiver that accepts the TCP connection and then never
  responds cannot wedge the delivery thread forever — it only ever delays
  the NEXT queued event, never the producers.

SSRF / trust posture
---------------------
``VAULT_WEBHOOK_URL`` is operator-supplied configuration, exactly like
``VAULT_STEAMPREFILL_PATH`` or ``VAULT_CACHE_ROOT`` — this project's whole
threat model (plan §9, api/README.md "Auth") is a single homelab operator
running their own trusted stack, not a multi-tenant service accepting
attacker-influenced URLs. There is deliberately **no allowlist, no DNS
rebinding defense, no blocking of RFC1918/loopback targets**: the operator
who sets this value is the same person who can already read
``vault_api/config.py``'s source, edit ``.env``, or run arbitrary code in the
container. Treating their own configuration as untrusted input would be
security theatre, not a mitigation. Basic-auth userinfo embedded in the URL
(``https://user:pass@host/path``) is converted into a real ``Authorization:
Basic`` header before delivery (``_build_request`` — ``urllib.request``
does NOT do this on its own; a userinfo-carrying URL handed to it directly
fails to resolve, measured) and is redacted from every LOG line
(``redact_url``) — that is about not leaking a secret into ``docker logs``,
not about distrusting the operator.

**Redirects are refused** (WP API-FIX-2, S1; docs/LEARNINGS.md "urllib
follows redirects by default"). Delivery goes through ``_OPENER``, built
with ``_RefuseRedirects`` exactly like ``oracle.py`` and ``steam_relay.py``:
a ``3xx`` from the receiver is a failed attempt, never a second request to
whatever ``Location`` named. The reason is the ``Authorization`` header
above — ``urllib``'s stock redirect handler re-sends the original headers
to the new host, so following would hand the operator's Basic-Auth
credentials to a host the operator never configured. ``build_opener`` keeps
the stock ``ProxyHandler`` (``_RefuseRedirects`` replaces only the redirect
handler), so ADR-0011's "honours ``HTTP_PROXY``/``HTTPS_PROXY``" fact stays
true.
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import sqlite3
import threading
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from vault_api import jobs
from vault_api.config import (
    Settings,
    WEBHOOK_EVENT_BYPASS_RESOLVED,
    WEBHOOK_EVENT_BYPASS_SUSPECTED,
    WEBHOOK_EVENT_JOB_CANCELLED,
    WEBHOOK_EVENT_JOB_DONE,
    WEBHOOK_EVENT_JOB_ERROR,
)

logger = logging.getLogger(__name__)

#: How many events may sit in the delivery queue at once. Generous for a
#: homelab (a handful of jobs and clients), tight enough that a receiver that
#: has been down for a while cannot let memory grow without bound.
MAX_QUEUE_SIZE = 100

#: How many HTTP attempts one event gets before delivery gives up on it.
DELIVERY_ATTEMPTS = 3

#: Backoff between attempts (attempt 1->2, then 2->3). Short on purpose — see
#: the module docstring: this thread is the only thing that could ever be
#: delayed by it, never a caller.
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.2, 0.5)

_REQUEST_METHOD = "POST"
_CONTENT_TYPE = "application/json"

#: jobs.status -> the webhook event name. ``STATUS_PAUSED`` is deliberately
#: absent: pause is not a conclusion, so it is never looked up here (see
#: ``notify_job_event``).
_JOB_EVENT_NAMES: Mapping[str, str] = {
    jobs.STATUS_DONE: WEBHOOK_EVENT_JOB_DONE,
    jobs.STATUS_ERROR: WEBHOOK_EVENT_JOB_ERROR,
    jobs.STATUS_CANCELLED: WEBHOOK_EVENT_JOB_CANCELLED,
}


def redact_url(url: str) -> str:
    """Redact HTTP Basic-Auth userinfo from a URL before it reaches a log line.

    ``https://user:secret@host/path`` -> ``https://***@host/path``. A URL with
    no userinfo (the common case) passes through unchanged. Never raises: an
    unparsable string is returned as-is rather than blowing up a log
    statement over a value that was only ever going to be logged, not used to
    connect.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if "@" not in parts.netloc:
        return url
    host_part = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, f"***@{host_part}", parts.path, parts.query, parts.fragment))


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Turn any redirect into an error instead of following it — same
    reasoning as ``oracle._RefuseRedirects``, plus one more: this module's
    requests carry an ``Authorization`` header built from the configured
    URL's userinfo, and following a redirect would forward that header to a
    host the operator never named.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


#: Module-level like ``oracle._OPENER``: ``build_opener`` adds every stock
#: handler (including ``ProxyHandler``, which is what makes ADR-0011's
#: ``HTTP_PROXY`` egress lock apply to webhooks) and swaps in only the
#: redirect handler. ``ProxyHandler`` reads ``HTTP_PROXY``/``HTTPS_PROXY``
#: when it is constructed, i.e. at import time here (measured: without a
#: proxy in the environment it registers nothing and is not even listed in
#: the opener's handlers) -- fine for the container, where compose sets the
#: variables before Python starts, and the same shape the other two openers
#: already have.
_OPENER = urllib.request.build_opener(_RefuseRedirects)


def _build_request(url: str, body: bytes) -> urllib.request.Request:
    """One outgoing ``POST``, with embedded Basic-Auth userinfo (if any)
    converted into a real ``Authorization`` header.

    **Why this exists at all.** ``urllib.request`` does NOT strip userinfo
    out of ``user:pass@host`` before resolving the host — measured: a
    ``Request`` built from such a URL carries ``req.host ==
    "user:pass@host"``, and ``getaddrinfo`` then fails on that literal
    string. A webhook URL with embedded credentials (a common, convenient
    way to point at a receiver that wants Basic Auth) would therefore never
    connect at all unless this module does the split itself.
    """
    parts = urlsplit(url)
    headers = {"Content-Type": _CONTENT_TYPE}
    if "@" in parts.netloc:
        userinfo, host_part = parts.netloc.rsplit("@", 1)
        url = urlunsplit((parts.scheme, host_part, parts.path, parts.query, parts.fragment))
        headers["Authorization"] = "Basic " + base64.b64encode(
            userinfo.encode("utf-8")
        ).decode("ascii")
    return urllib.request.Request(url, data=body, method=_REQUEST_METHOD, headers=headers)


def _build_body(settings: Settings, event: str, payload: Mapping[str, object]) -> bytes:
    """Render one event as the JSON envelope described in the module docstring."""
    envelope: dict[str, object] = {
        "event": event,
        "timestamp": jobs.utcnow_iso(),
        "payload": dict(payload),
    }
    if settings.vault_name:
        envelope["vault_name"] = settings.vault_name
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class _QueuedEvent:
    event: str
    body: bytes


class WebhookNotifier:
    """Background, best-effort delivery of webhook events.

    Constructed unconditionally (same pattern as ``SizeCache``/
    ``PrefillScheduler`` in ``main.py``) so it can be handed to the worker and
    the scheduler whether or not the feature is on; ``start()`` and
    ``enqueue()`` both no-op when ``settings.webhook_enabled`` is false, so
    the thread simply never exists for an installation that never set
    ``VAULT_WEBHOOK_URL``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue: "queue.Queue[_QueuedEvent]" = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        #: Total events dropped because the queue was full. Protected by
        #: ``_lock`` (the producer side; the delivery thread never touches it).
        self.dropped_count = 0

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """No-op when the feature is off. Otherwise starts the one delivery thread."""
        if not self._settings.webhook_enabled:
            return
        if self._thread is not None:  # pragma: no cover - guarded by lifespan
            raise RuntimeError("webhook notifier already started")
        self._thread = threading.Thread(
            target=self._run, name="vault-webhook-notifier", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():  # pragma: no cover - only an unresponsive receiver
            logger.warning(
                "Webhook notifier did not stop within %.0fs; leaving it as a "
                "daemon thread. Queued events are lost.",
                timeout,
            )

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- producer side: never blocks, never raises ----------------------

    def enqueue(self, event: str, payload: Mapping[str, object]) -> None:
        """Queue one event for delivery.

        Two independent ways this is a no-op, checked here so every call
        site (``notify_job_event``, ``notify_bypass_event``) can call this
        unconditionally without its own ``if settings.webhook_enabled``
        guard: the feature is off entirely, or this particular event is not
        in ``VAULT_WEBHOOK_EVENTS``.
        """
        if not self._settings.webhook_enabled:
            return
        if event not in self._settings.webhook_events:
            return

        body = _build_body(self._settings, event, payload)
        try:
            self._queue.put_nowait(_QueuedEvent(event=event, body=body))
            return
        except queue.Full:
            pass

        # Full queue: drop the OLDEST, never the newest, to make room (module
        # docstring). A race with the delivery thread draining the queue at
        # the same moment is harmless either way -- worst case this drops
        # nothing and the put below still succeeds.
        try:
            self._queue.get_nowait()
            with self._lock:
                self.dropped_count += 1
                dropped_total = self.dropped_count
            logger.warning(
                "webhook: delivery queue is full (%d); dropped the OLDEST "
                "queued event to make room for %r. %d event(s) dropped in "
                "total so far -- the receiver at %s is not keeping up.",
                MAX_QUEUE_SIZE,
                event,
                dropped_total,
                redact_url(self._settings.webhook_url),
            )
        except queue.Empty:  # pragma: no cover - race with the delivery thread
            pass

        try:
            self._queue.put_nowait(_QueuedEvent(event=event, body=body))
        except queue.Full:  # pragma: no cover - only with concurrent producers
            pass

    # -- delivery thread --------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._deliver(item)

    def _deliver(self, item: _QueuedEvent) -> None:
        """Up to ``DELIVERY_ATTEMPTS`` tries, short backoff between them.

        Runs entirely on the background thread — the only place an HTTP call
        happens in this module. Any failure (connection refused, timeout, a
        non-2xx status raised by the opener as ``HTTPError`` — which includes
        every ``3xx``, since ``_OPENER`` refuses redirects — a malformed
        URL) is treated identically: try again, and after the last attempt,
        log once at WARNING with the event name and the reason. Never a
        traceback at ERROR — a receiver being down is an operational fact
        about the OTHER end, not a bug here.
        """
        url = self._settings.webhook_url
        timeout = self._settings.webhook_timeout_seconds
        last_error: BaseException | None = None

        for attempt in range(1, DELIVERY_ATTEMPTS + 1):
            try:
                request = _build_request(url, item.body)
                with _OPENER.open(request, timeout=timeout):
                    pass
                return
            except Exception as exc:  # noqa: BLE001 - any failure just means "retry"
                last_error = exc
                if attempt < DELIVERY_ATTEMPTS:
                    backoff = RETRY_BACKOFF_SECONDS[
                        min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                    ]
                    if self._stop.wait(backoff):
                        break

        logger.warning(
            "webhook: delivery of %r to %s failed after %d attempt(s): %s",
            item.event,
            redact_url(url),
            DELIVERY_ATTEMPTS,
            last_error,
        )


# --------------------------------------------------------------------------
# Job events — the single integration point every job-concluding call site
# in worker.py / gc_execute.py goes through.
# --------------------------------------------------------------------------


def notify_job_event(
    notifier: "WebhookNotifier | None",
    job: Mapping[str, object],
    *,
    bytes_freed: int | None = None,
) -> None:
    """Turn a just-finished job row into a webhook event, if any applies.

    ``notifier is None`` (the default for every ``PrefillWorker``/
    ``run_gc_job`` caller that does not pass one, e.g. most of the existing
    test suite) is a silent no-op — this function must never be the reason a
    job-processing test needs a notifier in scope.

    Silently does nothing for ``paused`` or any other non-terminal status:
    ``_JOB_EVENT_NAMES`` only maps the three that matter, so an unrecognised
    status is a fail-CLOSED default (no event), never a guess.
    """
    if notifier is None:
        return
    status = str(job.get("status"))
    event = _JOB_EVENT_NAMES.get(status)
    if event is None:
        return

    payload: dict[str, object] = {
        "id": job.get("id"),
        "type": job.get("type"),
        "appid": job.get("appid"),
        "status": status,
    }
    if str(job.get("type")) == jobs.JOB_TYPE_GC:
        gc_execute_flag = job.get("gc_execute")
        if gc_execute_flag is not None:
            payload["mode"] = "execute" if gc_execute_flag else "dry-run"
    if bytes_freed is not None:
        payload["bytes"] = bytes_freed

    notifier.enqueue(event, payload)


def finish_job_and_notify(
    conn: sqlite3.Connection,
    notifier: "WebhookNotifier | None",
    job_id: int,
    status: str,
    log_excerpt: str,
    *,
    updated: int | None = None,
    up_to_date: int | None = None,
    summary_parse_ok: bool | None = None,
    bytes_freed: int | None = None,
) -> None:
    """``jobs.finish_job`` plus a webhook — see the module docstring's "hook
    points" section for why the notification lives HERE and not inside
    ``jobs.finish_job`` itself.

    The webhook is fired only AFTER ``jobs.finish_job`` has committed (that
    function calls ``conn.commit()`` before returning) and after re-reading
    the row via ``jobs.get_job`` — never from the arguments this function was
    called with — so the payload describes exactly the state a concurrent
    reader (``GET /v1/jobs/{id}``) would now see, never a state that could
    still roll back.
    """
    jobs.finish_job(
        conn,
        job_id,
        status,
        log_excerpt,
        updated=updated,
        up_to_date=up_to_date,
        summary_parse_ok=summary_parse_ok,
    )
    if notifier is None:
        return
    job = jobs.get_job(conn, job_id)
    if job is None:  # pragma: no cover - the row just committed must exist
        return
    notify_job_event(notifier, job, bytes_freed=bytes_freed)


# --------------------------------------------------------------------------
# Bypass events — called from event_sweep.check_bypass_transitions, itself
# the sweep's persist step (see event_sweep.py).
# --------------------------------------------------------------------------


#: The two valid ``event`` values ``notify_bypass_event`` accepts. Named here
#: so a caller (and this module's own tests) can assert against them without
#: hardcoding either string a second time.
BYPASS_TRANSITION_EVENTS = (WEBHOOK_EVENT_BYPASS_SUSPECTED, WEBHOOK_EVENT_BYPASS_RESOLVED)


def notify_bypass_event(
    notifier: "WebhookNotifier | None",
    *,
    event: str,
    client_id: str,
    addresses: Sequence[str],
    last_seen: str | None,
) -> None:
    """Fire one bypass TRANSITION for one client — ``client.bypass_suspected``
    (newly flagged) or ``client.bypass_resolved`` (flag lifted). ``event``
    is a required keyword, spelled out at the one call site
    (``event_sweep.check_bypass_transitions``) rather than defaulted, for the
    same reason ``jobs.enqueue_gc``'s ``execute`` has no default: this
    decides which of two meanings — an alarm or an all-clear — the payload
    carries, and a reviewer should see it named at the call site.

    Takes plain data rather than ``agent_reports.ClientSummary``/
    ``event_sweep.AddrTotals`` on purpose: this module has no need to import
    either (nor, therefore, any risk of an import cycle with them) when a
    handful of scalars say everything the payload needs.
    """
    if notifier is None:
        return
    notifier.enqueue(
        event,
        {
            "client_id": client_id,
            "address": list(addresses),
            "last_seen": last_seen,
        },
    )
