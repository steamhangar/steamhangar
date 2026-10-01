"""WP 3.13: generic webhook notifications.

A local HTTP receiver (``_Receiver``, a real ``ThreadingHTTPServer`` on
127.0.0.1) stands in for the operator's endpoint throughout — these tests
exercise the real ``urllib.request`` delivery path, not a mock of it, because
the whole point of the feature is what actually goes over the wire (timeouts,
retries, the JSON shape).

Threads are involved (the delivery thread, this module's receiver thread), so
per docs/LEARNINGS.md ("Flake-hunt concurrency tests: run the module isolated
in a 20-40x loop") these were run standalone, repeatedly, before being trusted
— see the work package report for the command and result.
"""

from __future__ import annotations

import base64
import http.server
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api import event_sweep, webhooks
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.jobs import to_utc_iso
from vault_api.main import create_app
from vault_api.webhooks import WebhookNotifier

AUTH = {"X-Api-Key": TEST_API_KEY}


# --------------------------------------------------------------------------
# The local HTTP receiver
# --------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        server: "_Receiver" = self.server  # type: ignore[assignment]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if server.hang_seconds:
            time.sleep(server.hang_seconds)
        with server.lock:
            server.requests.append(json.loads(body))
        self.send_response(server.status_code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silence — the test output does not need an access log


class _Receiver(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self.hang_seconds = 0.0
        self.status_code = 200


@pytest.fixture
def receiver():
    server = _Receiver(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def receiver_url(server: _Receiver) -> str:
    host, port = server.server_address[:2]
    return f"http://127.0.0.1:{port}/hook"


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --------------------------------------------------------------------------
# Settings helper
# --------------------------------------------------------------------------


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        webhook_timeout_seconds=1.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


# --------------------------------------------------------------------------
# Off by default (mutation target: the `if not settings.webhook_enabled`
# guard in WebhookNotifier.enqueue)
# --------------------------------------------------------------------------


def test_disabled_by_default_start_spawns_no_thread(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, webhook_url="")
    notifier = WebhookNotifier(settings)

    notifier.start()

    assert notifier._thread is None


def test_disabled_by_default_enqueue_is_a_true_no_op(tmp_path: Path) -> None:
    """Pins the ENQUEUE guard specifically, not just start()'s.

    Even with no delivery thread ever running, a broken ``enqueue`` could
    still stuff events into the queue that simply sit there forever — this
    would be a silent memory leak, and it is exactly what removing the
    ``if not settings.webhook_enabled: return`` line would do. Mutation-tested
    by deleting that line: this test fails (queue is no longer empty).
    """
    settings = make_settings(tmp_path, webhook_url="")
    notifier = WebhookNotifier(settings)

    notifier.enqueue(
        "job.done", {"id": 1, "type": "prefill", "appid": 440, "status": "done"}
    )

    assert notifier._queue.qsize() == 0
    assert notifier.dropped_count == 0


# --------------------------------------------------------------------------
# Happy path — one event per class, real HTTP delivery
# --------------------------------------------------------------------------


def test_job_done_is_delivered_with_the_generic_envelope(
    tmp_path: Path, receiver: _Receiver
) -> None:
    settings = make_settings(tmp_path, webhook_url=receiver_url(receiver))
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        webhooks.notify_job_event(
            notifier,
            {"id": 7, "type": "prefill", "appid": 440, "status": "done"},
        )
        assert _wait_until(lambda: len(receiver.requests) == 1)
    finally:
        notifier.stop()

    (body,) = receiver.requests
    assert body["event"] == "job.done"
    assert "timestamp" in body and body["timestamp"].endswith("Z")
    assert "vault_name" not in body  # VAULT_NAME unset -> omitted, not ""
    assert body["payload"] == {
        "id": 7,
        "type": "prefill",
        "appid": 440,
        "status": "done",
    }


def test_job_error_and_job_cancelled_are_delivered(
    tmp_path: Path, receiver: _Receiver
) -> None:
    settings = make_settings(tmp_path, webhook_url=receiver_url(receiver))
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        webhooks.notify_job_event(
            notifier, {"id": 1, "type": "prefill", "appid": 10, "status": "error"}
        )
        webhooks.notify_job_event(
            notifier, {"id": 2, "type": "prefill", "appid": 20, "status": "cancelled"}
        )
        assert _wait_until(lambda: len(receiver.requests) == 2)
    finally:
        notifier.stop()

    events = {body["event"] for body in receiver.requests}
    assert events == {"job.error", "job.cancelled"}


def test_gc_job_payload_includes_mode_and_bytes(
    tmp_path: Path, receiver: _Receiver
) -> None:
    settings = make_settings(tmp_path, webhook_url=receiver_url(receiver))
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        webhooks.notify_job_event(
            notifier,
            {"id": 3, "type": "gc", "appid": 440, "status": "done", "gc_execute": 1},
            bytes_freed=12345,
        )
        assert _wait_until(lambda: len(receiver.requests) == 1)
    finally:
        notifier.stop()

    (body,) = receiver.requests
    assert body["payload"]["mode"] == "execute"
    assert body["payload"]["bytes"] == 12345


def test_gc_dry_run_reports_dry_run_mode(tmp_path: Path, receiver: _Receiver) -> None:
    settings = make_settings(tmp_path, webhook_url=receiver_url(receiver))
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        webhooks.notify_job_event(
            notifier,
            {"id": 4, "type": "gc", "appid": 440, "status": "done", "gc_execute": 0},
            bytes_freed=0,
        )
        assert _wait_until(lambda: len(receiver.requests) == 1)
    finally:
        notifier.stop()

    assert receiver.requests[0]["payload"]["mode"] == "dry-run"


def test_prefill_job_never_carries_a_bytes_field(
    tmp_path: Path, receiver: _Receiver
) -> None:
    """SteamPrefill's summary is a formatted string, not a byte count — see
    api/README.md's "Webhooks" section. No caller passes ``bytes_freed`` for
    a prefill job, so the field must never appear."""
    settings = make_settings(tmp_path, webhook_url=receiver_url(receiver))
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        webhooks.notify_job_event(
            notifier, {"id": 5, "type": "prefill", "appid": 440, "status": "done"}
        )
        assert _wait_until(lambda: len(receiver.requests) == 1)
    finally:
        notifier.stop()

    assert "bytes" not in receiver.requests[0]["payload"]


def test_paused_job_never_fires_anything(tmp_path: Path, receiver: _Receiver) -> None:
    """paused is not a conclusion — the fail-closed default in
    ``_JOB_EVENT_NAMES`` must hold even though the DB does allow the value."""
    settings = make_settings(tmp_path, webhook_url=receiver_url(receiver))
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        webhooks.notify_job_event(
            notifier, {"id": 6, "type": "prefill", "appid": 440, "status": "paused"}
        )
        # Give delivery every chance to (wrongly) fire before asserting nothing did.
        time.sleep(0.3)
    finally:
        notifier.stop()

    assert receiver.requests == []


def test_notifier_none_is_a_safe_no_op() -> None:
    """Every real call site defaults to ``notifier=None`` — this must never raise."""
    webhooks.notify_job_event(None, {"id": 1, "type": "prefill", "appid": 1, "status": "done"})
    webhooks.notify_bypass_event(
        None,
        event=webhooks.WEBHOOK_EVENT_BYPASS_SUSPECTED,
        client_id="c",
        addresses=["1.2.3.4"],
        last_seen=None,
    )


def test_bypass_event_payload_shape(tmp_path: Path, receiver: _Receiver) -> None:
    settings = make_settings(
        tmp_path, webhook_url=receiver_url(receiver), vault_name="homelab"
    )
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        webhooks.notify_bypass_event(
            notifier,
            event=webhooks.WEBHOOK_EVENT_BYPASS_SUSPECTED,
            client_id="steam-deck-01",
            addresses=["192.168.1.55"],
            last_seen=None,
        )
        assert _wait_until(lambda: len(receiver.requests) == 1)
    finally:
        notifier.stop()

    body = receiver.requests[0]
    assert body["event"] == "client.bypass_suspected"
    assert body["vault_name"] == "homelab"
    assert body["payload"] == {
        "client_id": "steam-deck-01",
        "address": ["192.168.1.55"],
        "last_seen": None,
    }


# --------------------------------------------------------------------------
# Events filter
# --------------------------------------------------------------------------


def test_webhook_events_filter_excludes_configured_out_events(
    tmp_path: Path, receiver: _Receiver
) -> None:
    settings = make_settings(
        tmp_path,
        webhook_url=receiver_url(receiver),
        webhook_events=frozenset({"job.done"}),
    )
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        webhooks.notify_job_event(
            notifier, {"id": 1, "type": "prefill", "appid": 1, "status": "error"}
        )
        webhooks.notify_job_event(
            notifier, {"id": 2, "type": "prefill", "appid": 2, "status": "done"}
        )
        assert _wait_until(lambda: len(receiver.requests) == 1)
        time.sleep(0.2)  # give the excluded event every chance to show up too
    finally:
        notifier.stop()

    assert len(receiver.requests) == 1
    assert receiver.requests[0]["event"] == "job.done"


# --------------------------------------------------------------------------
# Retry / backoff, bounded
# --------------------------------------------------------------------------


def test_retry_gives_up_after_three_attempts_and_warns(
    tmp_path: Path, receiver: _Receiver, caplog: pytest.LogCaptureFixture
) -> None:
    receiver.status_code = 500  # every attempt "arrives" but is treated as a failure
    settings = make_settings(
        tmp_path, webhook_url=receiver_url(receiver), webhook_timeout_seconds=1.0
    )
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        with caplog.at_level("WARNING", logger="vault_api.webhooks"):
            webhooks.notify_job_event(
                notifier, {"id": 1, "type": "prefill", "appid": 1, "status": "done"}
            )
            assert _wait_until(lambda: len(receiver.requests) >= webhooks.DELIVERY_ATTEMPTS)
            # No further attempts once DELIVERY_ATTEMPTS is reached.
            time.sleep(0.3)
    finally:
        notifier.stop()

    assert len(receiver.requests) == webhooks.DELIVERY_ATTEMPTS

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("failed after 3 attempt" in r.getMessage() for r in warnings)
    # Never a traceback at ERROR for a receiver simply saying "no" — the
    # module docstring's explicit promise.
    assert not any(r.levelname == "ERROR" for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)


# --------------------------------------------------------------------------
# A hanging receiver must not delay the worker
# --------------------------------------------------------------------------


def test_hanging_receiver_does_not_delay_job_completion(
    tmp_path: Path, receiver: _Receiver
) -> None:
    """The literal WP requirement: sleep > timeout on the receiver side, and
    the WORKER STEP is timed around it.

    Uses the real app (create_app + TestClient as a context manager, so the
    lifespan actually starts the worker and the webhook notifier) and the
    fake SteamPrefill (tests/stub_prefill.py) for a fast, real end-to-end job.
    """
    webhook_timeout = 0.3
    receiver.hang_seconds = 2.0  # > webhook_timeout, by a wide margin
    assert receiver.hang_seconds > webhook_timeout * 3

    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(bindir, cache_root=str(cache_root))

    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
        steamprefill_path=executable,
        worker_poll_seconds=0.02,
        steamprefill_cache_dir=str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=str(tmp_path / "manifest-archive"),
        webhook_url=receiver_url(receiver),
        webhook_timeout_seconds=webhook_timeout,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH)
        assert response.status_code == 202, response.text
        job_id = response.json()[0]["job_id"]

        started = time.monotonic()
        deadline = started + 5.0
        job = None
        while time.monotonic() < deadline:
            job = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        elapsed = time.monotonic() - started

        assert job is not None and job["status"] == "done", job
        # Comfortably under one hang period, let alone three attempts' worth.
        assert elapsed < receiver.hang_seconds, (
            f"job completion took {elapsed:.2f}s -- the hanging webhook "
            "receiver must never be able to delay it"
        )


# --------------------------------------------------------------------------
# Bounded queue: drop the OLDEST, count it, never block the producer
# --------------------------------------------------------------------------


def test_full_queue_drops_the_oldest_event_and_counts_it(
    tmp_path: Path, receiver: _Receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhooks, "MAX_QUEUE_SIZE", 5)
    settings = make_settings(tmp_path, webhook_url=receiver_url(receiver))
    notifier = WebhookNotifier(settings)  # NOT started -- nothing drains the queue

    total = webhooks.MAX_QUEUE_SIZE + 3
    for i in range(total):
        webhooks.notify_job_event(
            notifier, {"id": i, "type": "prefill", "appid": i, "status": "done"}
        )

    assert notifier.dropped_count == 3
    assert notifier._queue.qsize() == webhooks.MAX_QUEUE_SIZE

    # The oldest 3 (ids 0, 1, 2) were dropped; the rest survive, in order.
    notifier.start()
    try:
        assert _wait_until(lambda: len(receiver.requests) == webhooks.MAX_QUEUE_SIZE)
    finally:
        notifier.stop()

    ids = [body["payload"]["id"] for body in receiver.requests]
    assert ids == list(range(3, total))


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


def test_redact_url_hides_basic_auth_userinfo() -> None:
    assert (
        webhooks.redact_url("https://user:secret@example.invalid/hook")
        == "https://***@example.invalid/hook"
    )


def test_redact_url_leaves_a_plain_url_unchanged() -> None:
    assert webhooks.redact_url("https://example.invalid/hook") == "https://example.invalid/hook"


def test_redact_url_never_raises_on_garbage() -> None:
    assert webhooks.redact_url("not a url at all") == "not a url at all"


def test_credentials_in_the_url_are_redacted_in_the_failure_log(
    tmp_path: Path, receiver: _Receiver, caplog: pytest.LogCaptureFixture
) -> None:
    receiver.status_code = 500
    host, port = receiver.server_address[:2]
    url_with_creds = f"http://user:s3cr3t@127.0.0.1:{port}/hook"
    settings = make_settings(tmp_path, webhook_url=url_with_creds, webhook_timeout_seconds=0.5)
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        with caplog.at_level("WARNING", logger="vault_api.webhooks"):
            webhooks.notify_job_event(
                notifier, {"id": 1, "type": "prefill", "appid": 1, "status": "done"}
            )
            assert _wait_until(lambda: len(receiver.requests) >= webhooks.DELIVERY_ATTEMPTS)
            time.sleep(0.2)
    finally:
        notifier.stop()

    full_log = "\n".join(r.getMessage() for r in caplog.records)
    assert "s3cr3t" not in full_log
    assert "***@127.0.0.1" in full_log


# --------------------------------------------------------------------------
# Bypass: fires on the TRANSITION only, never on the steady state -- in
# EITHER direction (suspected AND its all-clear, resolved)
# --------------------------------------------------------------------------

BOTH_BYPASS_EVENTS = frozenset(
    {webhooks.WEBHOOK_EVENT_BYPASS_SUSPECTED, webhooks.WEBHOOK_EVENT_BYPASS_RESOLVED}
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ago(moment: datetime, **kwargs) -> str:
    return to_utc_iso(moment - timedelta(**kwargs))


def _seed_suspected_client(
    conn, client_id: str = "steam-deck-01", *, moment: datetime | None = None
) -> None:
    """A client that reports recently, has games, a known address, and has
    NEVER appeared in the cache log -- i.e. bypass_suspected == True as soon
    as the feed is old enough to accuse anyone."""
    moment = moment or utc_now()
    conn.execute(
        "INSERT INTO agent_reports (client_id, reported_at, appids, source_addr) "
        "VALUES (?, ?, ?, ?)",
        (client_id, to_utc_iso(moment), "[440, 730]", "192.168.1.55"),
    )
    conn.execute(
        """
        INSERT INTO event_sweep_state (id, first_sweep_at, last_sweep_at)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            first_sweep_at = excluded.first_sweep_at,
            last_sweep_at = excluded.last_sweep_at
        """,
        (ago(moment, days=10), to_utc_iso(moment)),
    )
    conn.commit()


def _refresh_agent_report(conn, client_id: str, *, moment: datetime) -> None:
    """A fresh report at ``moment`` -- keeps disqualification #3 (own report
    recency) from firing when a test jumps ``now`` forward in time."""
    conn.execute(
        "INSERT INTO agent_reports (client_id, reported_at, appids, source_addr) "
        "VALUES (?, ?, ?, ?)",
        (client_id, to_utc_iso(moment), "[440, 730]", "192.168.1.55"),
    )
    conn.commit()


def _mark_seen_in_cache(conn, addr: str = "192.168.1.55", *, moment: datetime) -> None:
    """Cache-log presence returns for ``addr`` at ``moment`` -- what flips a
    suspected client back to not-suspected (disqualification #6)."""
    stamp = to_utc_iso(moment)
    conn.execute(
        """
        INSERT INTO client_cache_stats (
            client_addr, window_at, requests, hits, misses, bypasses, errors,
            bytes_served, last_seen
        ) VALUES (?, ?, 1, 1, 0, 0, 0, 100, ?)
        ON CONFLICT(client_addr, window_at) DO UPDATE SET last_seen = excluded.last_seen
        """,
        (addr, stamp, stamp),
    )
    conn.commit()


def test_bypass_webhook_fires_once_on_transition_not_on_steady_state(
    tmp_path: Path, receiver: _Receiver
) -> None:
    """Mutation target: if the ``suspected and not was_suspected`` guard in
    ``check_bypass_transitions`` were loosened to just ``suspected``, this
    test dies (three fires instead of one)."""
    settings = make_settings(
        tmp_path,
        webhook_url=receiver_url(receiver),
        webhook_events=frozenset({"client.bypass_suspected"}),
        bypass_window_days=3,
        event_log_path=str(tmp_path / "event.log"),
    )
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        _seed_suspected_client(conn)
    finally:
        conn.close()

    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        conn = get_connection(settings.db_path)
        try:
            now = utc_now()
            first = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
            second = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
            third = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
        finally:
            conn.close()

        expected = event_sweep.BypassTransition(
            event="client.bypass_suspected", client_id="steam-deck-01"
        )
        assert first == (expected,)
        assert second == ()
        assert third == ()

        assert _wait_until(lambda: len(receiver.requests) == 1)
        time.sleep(0.2)  # give a wrongly-repeated fire every chance to show up
    finally:
        notifier.stop()

    assert len(receiver.requests) == 1
    assert receiver.requests[0]["payload"]["client_id"] == "steam-deck-01"


def test_bypass_resolved_fires_once_on_the_flip_back(
    tmp_path: Path, receiver: _Receiver
) -> None:
    """The all-clear: suspected, then cache-log presence returns. Fires
    ``client.bypass_resolved`` exactly once, not on every later sweep."""
    settings = make_settings(
        tmp_path,
        webhook_url=receiver_url(receiver),
        webhook_events=BOTH_BYPASS_EVENTS,
        bypass_window_days=3,
        event_log_path=str(tmp_path / "event.log"),
    )
    init_db(settings.db_path)
    now = utc_now()
    conn = get_connection(settings.db_path)
    try:
        _seed_suspected_client(conn, moment=now)
    finally:
        conn.close()

    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        conn = get_connection(settings.db_path)
        try:
            first = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
            assert first == (
                event_sweep.BypassTransition(
                    event="client.bypass_suspected", client_id="steam-deck-01"
                ),
            )

            _mark_seen_in_cache(conn, moment=now)
            second = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
            assert second == (
                event_sweep.BypassTransition(
                    event="client.bypass_resolved", client_id="steam-deck-01"
                ),
            )

            # Steady state afterwards (still seen, nothing changed): silent.
            third = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
            assert third == ()
        finally:
            conn.close()

        assert _wait_until(lambda: len(receiver.requests) == 2)
        time.sleep(0.2)
    finally:
        notifier.stop()

    assert [r["event"] for r in receiver.requests] == [
        "client.bypass_suspected",
        "client.bypass_resolved",
    ]
    assert receiver.requests[1]["payload"]["last_seen"] is not None


def test_bypass_steady_not_suspected_state_stays_silent(
    tmp_path: Path, receiver: _Receiver
) -> None:
    """A client that has ALWAYS been seen in the cache log never fires
    anything, no matter how many sweeps run."""
    settings = make_settings(
        tmp_path,
        webhook_url=receiver_url(receiver),
        webhook_events=BOTH_BYPASS_EVENTS,
        bypass_window_days=3,
        event_log_path=str(tmp_path / "event.log"),
    )
    init_db(settings.db_path)
    now = utc_now()
    conn = get_connection(settings.db_path)
    try:
        _seed_suspected_client(conn, moment=now)
        _mark_seen_in_cache(conn, moment=now)  # never actually bypassing
    finally:
        conn.close()

    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        conn = get_connection(settings.db_path)
        try:
            first = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
            second = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
        finally:
            conn.close()
        assert first == ()
        assert second == ()
        time.sleep(0.3)
    finally:
        notifier.stop()

    assert receiver.requests == []


def test_bypass_suspect_resolved_suspect_cycle_fires_three_events_in_order(
    tmp_path: Path, receiver: _Receiver
) -> None:
    """A full cycle -- alarm, all-clear, alarm again -- fires exactly three
    events, in order, never collapsing or repeating a steady state."""
    settings = make_settings(
        tmp_path,
        webhook_url=receiver_url(receiver),
        webhook_events=BOTH_BYPASS_EVENTS,
        bypass_window_days=3,
        event_log_path=str(tmp_path / "event.log"),
    )
    init_db(settings.db_path)
    now = utc_now()
    conn = get_connection(settings.db_path)
    try:
        _seed_suspected_client(conn, moment=now)
    finally:
        conn.close()

    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        conn = get_connection(settings.db_path)
        try:
            t1 = event_sweep.check_bypass_transitions(conn, settings, notifier, now)

            _mark_seen_in_cache(conn, moment=now)
            t2 = event_sweep.check_bypass_transitions(conn, settings, notifier, now)

            # Time moves well past the bypass window with no further cache-log
            # sighting -- the old sighting above ages out and the client goes
            # quiet again. A fresh agent report keeps disqualification #3 (own
            # report recency) from masking the re-flag.
            later = now + timedelta(days=settings.bypass_window_days + 1)
            _refresh_agent_report(conn, "steam-deck-01", moment=later)
            t3 = event_sweep.check_bypass_transitions(conn, settings, notifier, later)
        finally:
            conn.close()

        suspected = event_sweep.BypassTransition(
            event="client.bypass_suspected", client_id="steam-deck-01"
        )
        resolved = event_sweep.BypassTransition(
            event="client.bypass_resolved", client_id="steam-deck-01"
        )
        assert t1 == (suspected,)
        assert t2 == (resolved,)
        assert t3 == (suspected,)

        assert _wait_until(lambda: len(receiver.requests) == 3)
        time.sleep(0.2)
    finally:
        notifier.stop()

    assert [r["event"] for r in receiver.requests] == [
        "client.bypass_suspected",
        "client.bypass_resolved",
        "client.bypass_suspected",
    ]


def test_bypass_resolved_guard_is_transition_only(
    tmp_path: Path, receiver: _Receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation target for the RESOLVED direction, mirroring the suspected
    one above: if ``was_suspected and not suspected`` were loosened to just
    ``not suspected``, a client that was never suspected in the first place
    (steady not-suspected) would wrongly fire ``client.bypass_resolved`` too.
    """
    settings = make_settings(
        tmp_path,
        webhook_url=receiver_url(receiver),
        webhook_events=BOTH_BYPASS_EVENTS,
        bypass_window_days=3,
        event_log_path=str(tmp_path / "event.log"),
    )
    init_db(settings.db_path)
    now = utc_now()
    conn = get_connection(settings.db_path)
    try:
        _seed_suspected_client(conn, moment=now)
        _mark_seen_in_cache(conn, moment=now)  # not suspected from the very start
    finally:
        conn.close()

    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        conn = get_connection(settings.db_path)
        try:
            result = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
        finally:
            conn.close()
        assert result == ()
        time.sleep(0.3)
    finally:
        notifier.stop()

    # A client that was NEVER suspected must never get a "resolved" webhook.
    assert receiver.requests == []


def test_bypass_webhook_needs_the_event_enabled(
    tmp_path: Path, receiver: _Receiver
) -> None:
    settings = make_settings(
        tmp_path,
        webhook_url=receiver_url(receiver),
        webhook_events=frozenset({"job.done"}),  # bypass events NOT included
        bypass_window_days=3,
        event_log_path=str(tmp_path / "event.log"),
    )
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        _seed_suspected_client(conn)
        result = event_sweep.check_bypass_transitions(
            conn, settings, WebhookNotifier(settings), utc_now()
        )
    finally:
        conn.close()

    assert result == ()


def test_bypass_webhook_fires_if_only_the_resolved_event_is_configured(
    tmp_path: Path, receiver: _Receiver
) -> None:
    """VAULT_WEBHOOK_EVENTS may name only 'client.bypass_resolved' -- the
    checker must still run (it needs to track state for the direction that
    IS wanted), it just never actually sends 'client.bypass_suspected'."""
    settings = make_settings(
        tmp_path,
        webhook_url=receiver_url(receiver),
        webhook_events=frozenset({"client.bypass_resolved"}),
        bypass_window_days=3,
        event_log_path=str(tmp_path / "event.log"),
    )
    init_db(settings.db_path)
    now = utc_now()
    conn = get_connection(settings.db_path)
    try:
        _seed_suspected_client(conn, moment=now)
    finally:
        conn.close()

    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        conn = get_connection(settings.db_path)
        try:
            t1 = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
            _mark_seen_in_cache(conn, moment=now)
            t2 = event_sweep.check_bypass_transitions(conn, settings, notifier, now)
        finally:
            conn.close()

        # Both transitions are still reported/tracked...
        assert t1[0].event == "client.bypass_suspected"
        assert t2[0].event == "client.bypass_resolved"

        assert _wait_until(lambda: len(receiver.requests) == 1)
        time.sleep(0.2)
    finally:
        notifier.stop()

    # ...but only the configured one was actually delivered.
    assert [r["event"] for r in receiver.requests] == ["client.bypass_resolved"]


def test_bypass_webhook_needs_the_sweep_itself_enabled(
    tmp_path: Path, receiver: _Receiver
) -> None:
    """Webhook on, bypass event included, but VAULT_EVENT_LOG_PATH unset —
    the same first gate ``routers/clients.py``'s ``feed_can_accuse`` applies."""
    settings = make_settings(
        tmp_path,
        webhook_url=receiver_url(receiver),
        webhook_events=frozenset({"client.bypass_suspected"}),
        bypass_window_days=3,
        event_log_path="",
    )
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        _seed_suspected_client(conn)
        result = event_sweep.check_bypass_transitions(
            conn, settings, WebhookNotifier(settings), utc_now()
        )
    finally:
        conn.close()

    assert result == ()


def test_bypass_webhook_needs_a_notifier(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, webhook_url="")
    init_db(settings.db_path)
    conn = get_connection(settings.db_path)
    try:
        _seed_suspected_client(conn)
        result = event_sweep.check_bypass_transitions(conn, settings, None, utc_now())
    finally:
        conn.close()

    assert result == ()


# --------------------------------------------------------------------------
# Redirects are refused (WP API-FIX-2, S1): a 3xx is a failed attempt, and
# the Authorization header built from the URL's userinfo never reaches the
# host the redirect pointed at. Two real local servers, like the Android
# two-server redirect test docs/LEARNINGS.md describes.
# --------------------------------------------------------------------------


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Records every request's headers; answers with the server's status and,
    for a 3xx, a ``Location`` pointing at ``server.redirect_to``."""

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._record_and_answer()

    # Stock urllib follows a POST 302 as GET (and a HEAD as HEAD), so hop 2
    # must record those too -- with only do_POST it answered 501 and recorded
    # nothing, and the redirect test passed against code that followed.
    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        self._record_and_answer()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib method name
        self._record_and_answer()

    def _record_and_answer(self) -> None:
        server: "_HopServer" = self.server  # type: ignore[assignment]
        with server.lock:
            seen = {k: v for k, v in self.headers.items()}
            seen["__method__"] = self.command
            server.seen_headers.append(seen)
        self.send_response(server.status_code)
        if 300 <= server.status_code < 400 and server.redirect_to:
            self.send_header("Location", server.redirect_to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class _HopServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.seen_headers: list[dict[str, str]] = []
        self.lock = threading.Lock()
        self.status_code = 200
        self.redirect_to = ""


@pytest.fixture
def two_hops():
    hop1 = _HopServer(("127.0.0.1", 0), _RecordingHandler)
    hop2 = _HopServer(("127.0.0.1", 0), _RecordingHandler)
    threads = [
        threading.Thread(target=hop1.serve_forever, daemon=True),
        threading.Thread(target=hop2.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        yield hop1, hop2
    finally:
        for server in (hop1, hop2):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def test_webhook_opener_refuses_redirects_and_never_forwards_basic_auth(
    tmp_path: Path, two_hops, caplog: pytest.LogCaptureFixture
) -> None:
    hop1, hop2 = two_hops
    hop2_port = hop2.server_address[1]
    hop1.status_code = 302
    hop1.redirect_to = f"http://127.0.0.1:{hop2_port}/elsewhere"
    hop1_port = hop1.server_address[1]

    settings = make_settings(
        tmp_path,
        webhook_url=f"http://user:s3cr3t@127.0.0.1:{hop1_port}/hook",
        webhook_timeout_seconds=1.0,
    )
    notifier = WebhookNotifier(settings)
    notifier.start()
    try:
        with caplog.at_level("WARNING", logger="vault_api.webhooks"):
            webhooks.notify_job_event(
                notifier, {"id": 1, "type": "prefill", "appid": 1, "status": "done"}
            )
            assert _wait_until(lambda: len(hop1.seen_headers) >= webhooks.DELIVERY_ATTEMPTS)
            time.sleep(0.3)
    finally:
        notifier.stop()

    # The 302 counted as a FAILED attempt: every retry went to hop 1 ...
    assert len(hop1.seen_headers) == webhooks.DELIVERY_ATTEMPTS
    assert all(
        h.get("Authorization") == "Basic " + base64.b64encode(b"user:s3cr3t").decode()
        for h in hop1.seen_headers
    )
    # ... and hop 2 was never contacted at all, so the header could not leak.
    assert hop2.seen_headers == []
    assert any("failed after 3 attempt" in r.getMessage() for r in caplog.records)
    assert "s3cr3t" not in "\n".join(r.getMessage() for r in caplog.records)


def test_webhook_opener_keeps_the_stock_proxy_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0011 rests on webhooks honouring HTTP_PROXY through urllib's stock
    ProxyHandler; swapping in the redirect handler must not have dropped it.

    Measured while writing this test: ``ProxyHandler`` only registers an
    ``http_open``/``https_open`` method per proxy it finds in the environment
    at construction, and ``OpenerDirector.add_handler`` only keeps a handler
    that registered SOMETHING -- so with no ``HTTP_PROXY`` set the stock
    handler is silently absent from ``.handlers``. The module-level
    ``_OPENER`` therefore snapshots the proxy environment at import, which is
    exactly the container case (compose sets ``HTTP_PROXY`` before Python
    starts) and the same shape ``oracle._OPENER``/``steam_relay._OPENER``
    already have. The assertion below builds the opener the way
    ``webhooks._OPENER`` is built, under a configured proxy."""
    import urllib.request

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8888")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8888")
    opener = urllib.request.build_opener(webhooks._RefuseRedirects)
    handler_types = {type(h) for h in opener.handlers}
    assert urllib.request.ProxyHandler in handler_types
    assert webhooks._RefuseRedirects in handler_types
    assert urllib.request.HTTPRedirectHandler not in handler_types

    # And the module-level opener itself carries the refusing handler, not
    # the stock one -- regardless of proxy environment.
    live_types = {type(h) for h in webhooks._OPENER.handlers}
    assert webhooks._RefuseRedirects in live_types
    assert urllib.request.HTTPRedirectHandler not in live_types
