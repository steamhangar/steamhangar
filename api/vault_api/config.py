"""Application configuration, read once from environment variables at startup.

No config framework is used on purpose (plan §9: keep vault-api simple). A
plain frozen dataclass plus a small ``.env`` loader is enough for the four
settings this project needs.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from vault_api.schedule_window import (
    ScheduleWindow,
    ScheduleWindowError,
    parse_window,
)

try:
    # Optional convenience for local/native dev: load a .env file if present.
    # Never overrides variables already set in the real environment.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - python-dotenv is a pinned dependency,
    # but we don't want a missing optional import to break the app.
    pass


#: Default wall-clock budget for one SteamPrefill subprocess run. A large
#: game legitimately takes hours on a slow line, so the default is
#: deliberately generous — this is a runaway/hang backstop, not a
#: performance knob (WP 1.4).
DEFAULT_PREFILL_TIMEOUT_SECONDS = 14400  # 4 hours

#: How long the job worker sleeps between polls of an empty queue.
DEFAULT_WORKER_POLL_SECONDS = 1.0

#: Default TTL for the in-process per-game size cache (plan §3: "du over
#: depot folders, cached", WP 1.5). A disk walk over the whole depot/ tree is
#: not free, so repeated GET /v1/games / /v1/cache/summary calls within this
#: window reuse the last scan instead of re-walking.
DEFAULT_SIZE_CACHE_TTL_SECONDS = 60.0

#: How many agent report snapshots to keep per client_id (WP 2.4). The agent
#: reports its full installed list every ~30 min (plan §3), so without a
#: retention policy ``agent_reports`` grows forever. 20 keeps roughly the last
#: 10 hours of a default reporting interval — enough to look back at what a
#: machine installed/removed during a day, and still tiny on disk.
#: The floor is 2 (mirrored as ``agent_reports.MIN_REPORTS_KEPT``, which clamps
#: defensively): the diff needs the previous snapshot next to the one being
#: written.
DEFAULT_AGENT_REPORT_KEEP = 20

#: Hard floor for VAULT_AGENT_REPORT_KEEP — see above.
MIN_AGENT_REPORT_KEEP = 2

#: How many archived manifest ``.bin`` files ``prune_archive`` keeps per
#: depot (WP 3.2, ADR-0006 decision 3 / research risk 4: "the archive needs
#: its own retention"). **Decision, stated plainly:** this is the TOTAL count
#: kept per depot (the current manifest plus its predecessors), the same
#: "keep the last N" semantics as ``VAULT_AGENT_REPORT_KEEP`` above — not "N
#: previous in addition to the current one". 3 is enough to look back across
#: a couple of game updates without the archive growing unbounded.
DEFAULT_MANIFEST_KEEP = 3

#: Hard floor for VAULT_MANIFEST_KEEP — 0 would mean "keep nothing", which
#: defeats the point of archiving at all.
MIN_MANIFEST_KEEP = 1

#: How long chunks are protected from garbage collection after they were
#: stored (WP 3.8b, ADR-0007's beta-branch addendum, decision A).
#:
#: **Why this exists.** Opt-in Steam beta branches reach the cache only via
#: store-on-miss — SteamPrefill has no branch selection — and their chunks
#: appear in no ``public`` manifest, so plain manifest-diff GC classifies every
#: one of them as an orphan and collects it. The same is true of anything else
#: a real client pulled through vault-core against a manifest vault-api never
#: recorded. The window buys those chunks time: whatever was stored in the last
#: N days is held back, so the beta tester who downloaded a build on Monday
#: still has it after Tuesday night's GC.
#:
#: 14 days is chosen to cover a normal beta/demo test cycle plus a weekend,
#: while still letting a stale one-off download age out within a fortnight.
#: ``0`` disables the window entirely (the predicate is then not even
#: constructed) — the pre-WP-3.8b behaviour, i.e. every orphan the plan names
#: is deleted.
DEFAULT_GC_GRACE_DAYS = 14

#: ``VAULT_MANIFEST_ORACLE`` value meaning "no oracle" — **the default**
#: (WP 3.9, ADR-0006 decision 4: "Default off; unaffiliated with Valve").
#: With this value vault-api makes no outbound third-party request at all,
#: which is the only posture a fresh install may have: enabling the oracle
#: sends this vault's app ids to a service outside the LAN (see
#: ``vault_api/oracle.py``'s privacy section and api/README.md).
MANIFEST_ORACLE_OFF = ""

#: The one implemented oracle: ``api.steamcmd.net``'s public mirror of Steam's
#: PICS app info. Also used verbatim as the ``source`` provenance tag on every
#: row it produces (``oracle.SOURCE_STEAMCMD_API``).
MANIFEST_ORACLE_STEAMCMD_API = "steamcmd_api"

#: Everything ``VAULT_MANIFEST_ORACLE`` may be set to. An unrecognised value
#: is refused at STARTUP rather than silently treated as "off": "off" is what
#: the operator gets by leaving the variable alone, so a non-empty value is an
#: explicit request for a feature — and a typo in it must not look like it
#: worked (the misconfiguration is loud, the oracle's own failures stay soft).
SUPPORTED_MANIFEST_ORACLES = (MANIFEST_ORACLE_OFF, MANIFEST_ORACLE_STEAMCMD_API)

#: Where ``steamcmd_api`` asks. Overridable so an operator can point at their
#: own mirror of the same API (or at a LAN proxy, which is the only way to use
#: this feature without traffic leaving the network). The value is
#: operator-supplied and therefore trusted by definition — vault-api only
#: insists on http/https and refuses to follow redirects away from it.
DEFAULT_MANIFEST_ORACLE_URL = "https://api.steamcmd.net/v1/info"

#: Socket timeout for one oracle request. Short on purpose: the oracle is
#: optional information, and a slow third party must never turn into a slow
#: vault-api. A timeout is an ordinary "no data" outcome (fail-soft).
DEFAULT_MANIFEST_ORACLE_TIMEOUT = 10.0

#: How long the scheduler waits between sweeps of the installed list (WP 3.5).
#: Plan §7 Phase 3 spells the cron window out as "e.g. 09:00-17:00, every 3 h"
#: — 180 minutes is that "every 3 h", verbatim.
DEFAULT_SCHEDULE_INTERVAL_MINUTES = 180

#: A client whose most recent agent report is older than this is left OUT of
#: the scheduler's target set (WP 3.5). Rationale: the target set is "what is
#: installed on the gaming machines right now" (plan A8). A machine that has
#: been off for a week is not reporting *anything* right now, and its last
#: snapshot is a guess about the past — continuing to prefill (and keep
#: current) a library nobody has confirmed in days quietly burns bandwidth and
#: disk on a decommissioned PC. 7 days is generous enough to cover a holiday
#: without the Steam Deck dropping out of the set.
DEFAULT_SCHEDULE_CLIENT_STALE_DAYS = 7

#: Upper bounds for every integer setting that ends up inside a
#: ``datetime.timedelta`` (WP API-FIX-2, review finding S2). ``timedelta``
#: raises ``OverflowError`` past ~999 999 999 days, and a digits-only grammar
#: with no ceiling let an operator (or a ``PATCH /v1/settings`` typo) store a
#: value that made ``GET /v1/games``, ``GET /v1/games/{appid}`` and
#: ``GET /v1/schedule`` answer ``500`` and every scheduler tick log a
#: traceback -- the exact "fails hours later in a thread" failure the strict
#: grammar exists to prevent. The caps are generous operational maxima, not
#: tuning knobs: one leap year of minutes, ten years of days.
MAX_INTERVAL_MINUTES = 527_040  # 366 * 24 * 60
MAX_COOLDOWN_MINUTES = MAX_INTERVAL_MINUTES
MAX_DAYS = 3_650


#: WP 3.12. ``VAULT_AUTO_GC`` — should a successful prefill that actually
#: updated something queue a garbage-collection job for that app?
#:
#: ``off`` keeps GC entirely operator-driven, which was the safe shape WP 3.12
#: shipped for a feature that can delete files. ``dry-run`` queues a
#: reporting-only GC job, so an operator can watch what automatic collection
#: *would* reclaim before trusting it. ``execute`` (**the default since
#: WP SWEEP-1 / ADR-0014**) queues a deleting one.
#:
#: Deliberately a three-value string rather than a boolean: the dry-run rung is
#: the whole point of an opt-in ladder for a destructive feature, and a boolean
#: would have forced the operator to choose between "no information" and "it
#: deletes now".
AUTO_GC_OFF = "off"
AUTO_GC_DRY_RUN = "dry-run"
AUTO_GC_EXECUTE = "execute"
AUTO_GC_MODES = (AUTO_GC_OFF, AUTO_GC_DRY_RUN, AUTO_GC_EXECUTE)

#: WP SWEEP-1 / ADR-0014 (operator decision, 2026-08-22): was ``AUTO_GC_OFF``
#: through WP 3.12. Flipped to ``AUTO_GC_EXECUTE`` **together with**
#: ``DEFAULT_SWEEP_INCLUDE_CACHED`` below, as one change, not two — see that
#: constant's own comment for why splitting them is exactly the
#: half-configuration ``scheduler.cached_sweep_gc_risk`` exists to warn about.
#: The cost this used to protect against is real and unchanged: ``execute``
#: deletes files, on every app whose prefill just updated something, with no
#: further confirmation. It is accepted as the default now because pairing it
#: with the cached-apps sweep (below) is the only way that sweep mode does not
#: grow a vault's disk usage without bound — see docs/adr/0014-sweep-cached-
#: and-auto-gc-default-on.md for the full argument, the upgrade impact on an
#: existing install, and the exact two lines that restore the pre-ADR-0014
#: behaviour (``VAULT_AUTO_GC=off`` and/or ``VAULT_SWEEP_INCLUDE_CACHED=false``
#: in the environment, or the equivalent ``PATCH /v1/settings`` call — both
#: keys stay ordinary, individually overridable settings, ADR-0009).
DEFAULT_AUTO_GC = AUTO_GC_EXECUTE

#: WP 4d (plan §7 Phase 4d, "Sweep target set — installed PLUS cached").
#: ``VAULT_SWEEP_INCLUDE_CACHED`` — should ``scheduler.compute_targets`` widen
#: its target set to every app with SOME cache content on disk, not only the
#: union of installed apps from fresh agent reports?
#:
#: **On by default since WP SWEEP-1 (ADR-0014, operator decision,
#: 2026-08-22) — was off by default through WP 4d.** The cost that made WP 4d
#: ship this off is real and has not gone away, and is worth stating exactly
#: as it was then: the installed-based set (plan A8) is "what somebody asked
#: to have on their machine"; the cached set is "what is already sitting on
#: this vault's disk" — a much larger, operator-unbounded set that spends
#: bandwidth (checking) and, on real updates, disk (fresh chunks) on games
#: nobody currently asked for. WP 4d's own words were that turning this on
#: "must be an explicit operator opt-in, not a byte-for-byte-free upgrade to
#: the existing sweep" — that sentence was correct advice for a sweep mode
#: shipped *without* a paired collector, which is exactly what changed.
#:
#: **Why the operator chose to accept that cost as the shipped default
#: anyway, and why doing so is defensible rather than reckless:** this key
#: does not move alone — ``DEFAULT_AUTO_GC`` above flips to
#: ``AUTO_GC_EXECUTE`` in the same change, so every fresh chunk this mode's
#: extra bandwidth spends on a game update has its superseded predecessor
#: actually reclaimed, not merely reported on. Turning the sweep on without
#: also turning on real collection is precisely the "keeps itself current
#: straight into a full disk" condition ``scheduler.cached_sweep_gc_risk``
#: names — pairing the two defaults is what keeps that condition from being
#: the out-of-the-box experience. See docs/adr/0014-sweep-cached-and-auto-gc-
#: default-on.md for the full pairing argument, the presented-twice cost
#: this decision knowingly accepts, the upgrade impact on an existing
#: install, and the opt-out (env or ``PATCH /v1/settings``, ADR-0009 — this
#: key remains fully overridable both ways).
#:
#: **Why turning it on is still cheap** (the reasoning that made it worth
#: having at all, unchanged since WP 4d): a non-forced SteamPrefill run
#: against an already-current app is a ~3 s no-op that transfers zero bytes
#: (ADR-0006 decision 1, the same fact Phase 4c's manual-check feature rests
#: on) — real traffic only happens for apps that actually have an update. See
#: ``scheduler.cached_appids``/``compute_targets`` for the mechanics and
#: api/README.md's "Sweep target set" section for the full cost model and the
#: auto-GC coupling (every kept-current game leaves its superseded chunks as
#: fresh orphans — ``scheduler.cached_sweep_gc_risk`` names that condition and
#: ``GET /v1/schedule`` surfaces it).
DEFAULT_SWEEP_INCLUDE_CACHED = True


# --------------------------------------------------------------------------
# WP 3.11 — the cache-event sweep (ADR-0008). OFF by default: the whole
# feature hangs off ``VAULT_EVENT_LOG_PATH`` being set to vault-core's
# structured event log. Empty (the default) means no sweeper thread work, no
# tables growing, no miss trigger — exactly the "optional at runtime"
# boundary ADR-0008 draws, and the same default vault-core itself ships
# (``VAULT_EVENT_LOG`` is empty in core/Dockerfile).
# --------------------------------------------------------------------------

#: How often the sweeper reads new lines out of the event log.
#:
#: **Deliberately its own interval, and deliberately NOT gated on
#: ``VAULT_SCHEDULE_WINDOW``** (decision, WP 3.11 — see api/README.md "Why the
#: sweep ignores the schedule window"). Three reasons, in order of weight:
#:
#: 1. The sweeper is the *only* thing that rotates the event log (ADR-0008:
#:    cursor + truncate, nothing in core/ rotates it). A window-gated sweeper
#:    would let the file grow unattended for the 16 hours a day the window is
#:    closed — the feature would create the unbounded-file problem it is
#:    supposed to own.
#: 2. Bypass detection must not have blind hours. "This machine never appears
#:    in the cache log" is only trustworthy if the log is read around the
#:    clock; a windowed sweep would make every evening gamer look like a
#:    bypass suspect at 22:00 and innocent again at 09:00.
#: 3. A sweep is cheap and bounded (one bounded read + a handful of small
#:    SQLite writes), unlike the prefill sweep the window exists for, which
#:    starts Steam logins and downloads.
#:
#: 5 minutes is comfortably coarser than vault-core's ``flush=5s`` log buffer
#: (core/README.md), so a line is never missed for being unflushed, and fine
#: enough that a miss-triggered prefill starts while the player is still
#: downloading.
DEFAULT_EVENT_SWEEP_INTERVAL_MINUTES = 5

#: Per-app cooldown for the miss trigger (ADR-0008: "a per-app cooldown so a
#: busy download night cannot enqueue storms").
#:
#: **``0`` means the trigger is OFF, not "no cooldown".** That reads backwards
#: at first glance and is chosen on purpose: "no cooldown" is precisely the
#: storm shape this setting exists to prevent, so it is not a value an
#: operator can select at all. Statistics and bypass detection keep working
#: with the trigger disabled — they are the other half of the sweep.
#:
#: 60 minutes against a default cap of 5 enqueues per sweep bounds the worst
#: case at 5 new jobs per 5-minute sweep and one job per app per hour, which
#: on a homelab is "the game somebody started downloading gets completed",
#: not a queue flood.
DEFAULT_MISS_TRIGGER_COOLDOWN_MINUTES = 60

#: Hard cap on how many prefill jobs ONE sweep may enqueue from misses. The
#: storm backstop of last resort: the cooldown bounds repeats per app, this
#: bounds the *first* enqueue for many different apps at once (a LAN party
#: where six machines each start a different game). Dropped candidates are
#: logged by app id — never silently capped (docs/LEARNINGS.md).
#:
#: Minimum 1, not 0: ``VAULT_MISS_TRIGGER_COOLDOWN_MINUTES=0`` is the single
#: off switch for the trigger, and two ways to spell "off" is one too many.
DEFAULT_MISS_TRIGGER_MAX_PER_SWEEP = 5

#: How far back ``GET /v1/clients`` looks for a client's cache-log presence
#: before calling it ``bypass_suspected`` (plan §5's DNS-bypass pain point).
#:
#: 3 days rather than 1: ADR-0001's production requirement 7 warns that Steam
#: LAN peer-to-peer transfers can legitimately replace cache traffic, and a
#: machine that simply did not launch Steam yesterday is not evidence of
#: anything. Three days of a reporting agent with zero cache lines is a real
#: signal.
DEFAULT_BYPASS_WINDOW_DAYS = 3

#: How many per-sweep statistics rows are kept per client address. Same
#: bounded-retention shape as ``VAULT_AGENT_REPORT_KEEP``: the sweep writes one
#: row per active address per sweep, so without a cap the table grows forever.
#: 48 rows at the default 5-minute interval is roughly the last 4 hours of
#: fine-grained history; the totals ``GET /v1/clients`` reports are sums over
#: the RETAINED rows, which is stated in the response's own documentation.
DEFAULT_CLIENT_STATS_KEEP = 48

#: Truncate the event log once it is at least this large — and only when the
#: cursor has consumed all of it (see ``event_sweep.maybe_truncate`` for the
#: exact conditions and the one residual race). ``0`` disables truncation
#: entirely, leaving the file to an external rotation strategy.
#:
#: 64 MiB is a few hundred thousand event lines: large enough that truncation
#: is a rare event (which is what keeps the residual race rare), small enough
#: that a forgotten deployment does not fill a homelab volume.
DEFAULT_EVENT_LOG_MAX_BYTES = 64 * 1024 * 1024


# --------------------------------------------------------------------------
# WP 3.13 — generic webhook notifications. OFF by default: the whole feature
# hangs off ``VAULT_WEBHOOK_URL`` being set, the same one-switch shape as
# ``VAULT_EVENT_LOG_PATH`` above. See ``vault_api/webhooks.py`` for delivery.
# --------------------------------------------------------------------------

#: The five events a webhook can be told about — job outcomes (three, one per
#: terminal ``jobs.status`` value that is not ``paused``) plus the two client
#: bypass-detection TRANSITIONS (never the steady state in either direction):
#: a client NEWLY flagged (``BYPASS_SUSPECTED``), and a previously-flagged
#: client whose cache-log presence returned (``BYPASS_RESOLVED`) — the
#: all-clear that closes the loop the suspected event opened. Named here, not
#: in ``webhooks.py``, so ``config`` never has to import that module to
#: validate ``VAULT_WEBHOOK_EVENTS`` at startup — ``webhooks.py`` imports
#: these constants instead, keeping the dependency one-directional.
WEBHOOK_EVENT_JOB_DONE = "job.done"
WEBHOOK_EVENT_JOB_ERROR = "job.error"
WEBHOOK_EVENT_JOB_CANCELLED = "job.cancelled"
WEBHOOK_EVENT_BYPASS_SUSPECTED = "client.bypass_suspected"
WEBHOOK_EVENT_BYPASS_RESOLVED = "client.bypass_resolved"
WEBHOOK_EVENTS_ALL = (
    WEBHOOK_EVENT_JOB_DONE,
    WEBHOOK_EVENT_JOB_ERROR,
    WEBHOOK_EVENT_JOB_CANCELLED,
    WEBHOOK_EVENT_BYPASS_SUSPECTED,
    WEBHOOK_EVENT_BYPASS_RESOLVED,
)

#: Default delivery timeout for one HTTP attempt. Short on purpose: a webhook
#: receiver is expected to be a fast local/LAN endpoint (a chat bridge, a
#: notification gateway), and delivery already retries — a long timeout here
#: would only make a hanging receiver hold the delivery thread longer per
#: attempt, never help correctness (see ``webhooks.py``'s single-thread design
#: for why that thread, and only that thread, may block).
DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 5.0


def _env_webhook_events(name: str = "VAULT_WEBHOOK_EVENTS") -> frozenset[str]:
    """Read ``VAULT_WEBHOOK_EVENTS``: a comma list of event names, or blank
    for "all five" (WP 3.13, extended to five in the same WP's review cycle).

    Strict by the same house rule as every other list-shaped setting in this
    module: a typo'd event name must fail loudly at startup, not silently
    mean "this event is never sent" for the lifetime of the deployment. Empty
    entries (a stray comma, ``"job.done,,job.error"``) are refused rather than
    skipped for the same reason.
    """
    raw = os.environ.get(name, "")
    try:
        return parse_webhook_events(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} {exc}") from exc


def parse_webhook_events(raw: str) -> frozenset[str]:
    """Pure grammar half of :func:`_env_webhook_events` — see
    ``parse_strict_int``'s note (ADR-0009 decision 4: shared with
    ``PATCH /v1/settings``). Blank -> all five, same as the env default: an
    operator (or a settings override) that names no events almost always
    means "send everything".
    """
    text = raw.strip()
    if not text:
        return frozenset(WEBHOOK_EVENTS_ALL)
    tokens = [token.strip() for token in text.split(",")]
    if any(not token for token in tokens):
        raise ValueError(
            "must be a comma-separated list of event names with no "
            f"empty entries (valid names: {', '.join(WEBHOOK_EVENTS_ALL)}). "
            f"Got {raw!r}."
        )
    unknown = sorted(set(tokens) - set(WEBHOOK_EVENTS_ALL))
    if unknown:
        raise ValueError(
            f"contains unknown event name(s) {unknown!r}; valid names "
            f"are {', '.join(WEBHOOK_EVENTS_ALL)}. Got {raw!r}."
        )
    return frozenset(tokens)


def validate_webhook_url(raw: str) -> str:
    """Scheme-only check for an operator-supplied webhook URL.

    **Not used by** :meth:`Settings.from_env` **today.** WP 3.13 deliberately
    treats ``VAULT_WEBHOOK_URL`` as trusted operator configuration and never
    refuses to boot over it (see ``vault_api/webhooks.py``'s "SSRF / trust
    posture" section): a malformed URL fails per-delivery, at WARNING, the
    same as an unreachable receiver, and that design is preserved here
    unchanged. This function exists ONLY for ``PATCH /v1/settings``
    (ADR-0009 decision 4) — a documented, deliberate gap between "the exact
    same grammars config.py applies at startup" the ADR calls for and what
    WP 3.13 actually shipped, since no such startup grammar exists for this
    field to reuse. A settings PATCH is a human typing a value into a form
    RIGHT NOW; "what you just typed is not even a URL" is worth a ``422``
    immediately, rather than a silent per-delivery WARNING hours later. Blank
    means "disabled" (the same one-enable-switch convention every optional
    URL/path setting in this module uses) and is accepted unconditionally.
    """
    text = raw.strip()
    if not text:
        return ""
    scheme = urlsplit(text).scheme.lower()
    if scheme not in ("http", "https"):
        # Deliberately no ``Got {raw!r}`` echo here (WP API-FIX-2, P2): a
        # webhook URL can carry Basic-Auth userinfo, and this message ends up
        # verbatim in a 422 response body and in the effective_settings log
        # line for a corrupt stored override.
        # The parsed scheme is not echoed either (WP API-FIX-2, N1): for a
        # scheme-less ``admin:s3cr3t@host`` urlsplit reports ``admin`` as the
        # "scheme", i.e. the username.
        raise ValueError(
            "must be an http:// or https:// URL (the scheme must be http or "
            "https), or blank to disable webhooks."
        )
    return text


#: ``vault_name`` is a label that ends up in every webhook envelope and in
#: log lines, so it gets the same "printable, bounded" rule ``client_id`` has
#: (``routers/agent.py::_validate_client_id``). Shared by ``Settings.from_env``
#: (``VAULT_NAME``) and ``PATCH /v1/settings`` (WP API-FIX-2, P3/S2).
MAX_VAULT_NAME_LENGTH = 64


def parse_vault_name(raw: str) -> str:
    """Strip, then refuse anything over 64 characters or non-printable."""
    text = raw.strip()
    if len(text) > MAX_VAULT_NAME_LENGTH:
        raise ValueError(
            f"must be at most {MAX_VAULT_NAME_LENGTH} characters after "
            f"trimming whitespace, got {len(text)}"
        )
    if not text.isprintable():
        raise ValueError(
            "must be printable (no control or non-printing characters); "
            "use a plain label such as a hostname"
        )
    return text


def _env_vault_name(name: str = "VAULT_NAME") -> str:
    try:
        return parse_vault_name(os.environ.get(name, ""))
    except ValueError as exc:
        raise RuntimeError(f"{name} {exc}") from exc


#: Accepted spellings for :func:`_env_bool`, case-insensitive.
_BOOL_TRUE_VALUES = ("1", "true", "yes", "on")
_BOOL_FALSE_VALUES = ("0", "false", "no", "off")


def parse_strict_bool(raw: str) -> bool:
    """Pure grammar half of :func:`_env_bool` — see ``parse_strict_int``'s
    note (ADR-0009 decision 4: the exact same function backs both the
    startup path and ``PATCH /v1/settings``'s validation, first needed here
    by ``VAULT_SWEEP_INCLUDE_CACHED``, WP 4d). Case- and whitespace-
    insensitive; anything outside the two named word sets is refused. ``raw``
    must already be known non-blank — same contract as ``parse_strict_int``/
    ``parse_strict_float``, since blank means something different to each
    caller ("unset" at startup, "invalid" at PATCH time).
    """
    text = raw.strip().lower()
    if text in _BOOL_TRUE_VALUES:
        return True
    if text in _BOOL_FALSE_VALUES:
        return False
    raise ValueError(
        f"must be one of {', '.join(_BOOL_TRUE_VALUES)} (true) or "
        f"{', '.join(_BOOL_FALSE_VALUES)} (false), case-insensitive. "
        f"Got {raw!r}."
    )


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a strict boolean env var. Blank/unset = ``default``.

    Same house rule as every other enum-shaped setting in this module: a
    typo must not silently mean the wrong thing, which matters more than
    usual for ``VAULT_SETTINGS_READONLY`` — the flag that decides whether
    the settings-write API is locked.

    ``raw`` is deliberately passed to :func:`parse_strict_bool` UNSTRIPPED —
    same as every sibling wrapper (``_env_int``/``_env_float``/
    ``_env_auto_gc``) hands its OWN unstripped value to its ``parse_*``
    counterpart, which does its own internal stripping for comparison but
    keeps the original in its error message. Reviewer nitpick N4
    (2026-08-18 review round): an earlier version of this function stripped
    ``raw`` itself before the call, so a value like ``" 7 "`` reported as
    ``'7'`` in the error rather than the actual ``' 7 '`` that was typed —
    behaviourally identical (both are rejected), but a worse error message
    than every other wrapper in this file gives.
    """
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        return parse_strict_bool(raw)
    except ValueError as exc:
        # N4: restore the "or leave it blank for the default" hint the
        # pre-refactor message had, which the shared parse_strict_bool
        # (deliberately generic — see its own docstring) cannot phrase
        # itself, since it has no notion of "this caller has a default".
        raise RuntimeError(f"{name} {exc} Blank/unset uses the default ({default!r}).") from exc


def _default_steamprefill_cache_dir() -> str:
    """Platform default for SteamPrefill's manifest temp-cache directory
    (docs/research/phase3-manifests.md §1a): ``%LOCALAPPDATA%\\SteamPrefill\\v1``
    on Windows, ``$HOME/.cache/SteamPrefill/v1`` everywhere else (the path
    inside the container, volume-backed — wiring that volume into deploy/'s
    compose file is explicitly NOT this work package's scope, see
    api/README.md's WP 3.2 note).

    Falls back to ``~/AppData/Local`` if ``LOCALAPPDATA`` is unset on Windows
    (matches how Windows itself derives the variable), so this never raises —
    a wrong guess here only means a job's manifest ingestion finds nothing to
    ingest (warn-and-skip, never a startup failure), not a crash.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if not base:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "SteamPrefill", "v1")
    return os.path.join(os.path.expanduser("~"), ".cache", "SteamPrefill", "v1")


def _default_web_dir() -> str:
    """Default location of the built-in web UI's static files (WP 4a.1):
    the ``web/`` directory at the repo root, computed relative to THIS
    file's own location so it resolves correctly no matter what the
    process's current working directory is (native dev may launch uvicorn
    from ``api/`` or from the repo root; both must find the same ``web/``).

    ``api/vault_api/config.py`` -> parent is ``api/vault_api``, its parent
    is ``api/``, and ITS parent is the repo root in a native checkout
    (``api/`` and ``web/`` are sibling top-level directories, plan
    structure). In the shipped Docker image this resolves to a path that
    does not exist: the Dockerfile only ``COPY``s ``vault_api/`` in, and
    ``web/`` lives outside the ``api/`` build context entirely, so
    packaging it is explicitly NOT this work package's scope (see
    api/README.md "Web UI static serving"). ``create_app`` / ``webui.py``
    treat a missing directory as "no UI to serve, API only" rather than a
    startup failure — the same soft-degradation shape as every other
    optional path setting in this module (``steamprefill_path``,
    ``event_log_path``, ...).
    """
    vault_api_dir = os.path.dirname(os.path.abspath(__file__))  # .../api/vault_api
    api_dir = os.path.dirname(vault_api_dir)  # .../api
    repo_root = os.path.dirname(api_dir)  # repo root in a native checkout
    return os.path.join(repo_root, "web")


def _default_manifest_archive_dir(db_path: str) -> str:
    """Default archive location: a ``manifests`` sibling of the database file.

    WP 3.2 scope note: consistent with a single-volume deployment (db +
    archive on the same persistent volume) is the intent, but wiring a
    dedicated volume/mount for it in ``deploy/`` is explicitly NOT this work
    package's scope — a follow-up on top of WP 1.9's Compose file.

    **``db_path`` is resolved as a plain filesystem path (WP 3.2 review
    note), not with any sqlite-specific awareness.** ``os.path.abspath``
    doesn't know sqlite's special ``":memory:"`` URI, so
    ``VAULT_DB_PATH=":memory:"`` would resolve to a literal (and harmless,
    if slightly odd-looking) sibling directory ``.../:memory:/manifests``
    under the current working directory — this project never sets
    ``VAULT_DB_PATH`` that way (every test and deployment uses a real file
    path), so this is a documented quirk, not a bug worth guarding against. A
    relative ``db_path`` (the ``"./vault.db"`` default) resolves against the
    process's current working directory, same as ``db_path`` itself already
    does everywhere else in this codebase (``db.init_db``,
    ``deletion.resolve_depot_root``).
    """
    parent = os.path.dirname(os.path.abspath(db_path)) or "."
    return os.path.join(parent, "manifests")


def _env_int(
    name: str, default: int, minimum: int = 1, maximum: int | None = None
) -> int:
    """Read an integer env var >= ``minimum`` (and <= ``maximum`` when one is
    given), falling back to ``default``.

    **Strict by house rule (WP 3.12, docs/LEARNINGS.md "Parsers"):** Python's
    ``int()`` is far more permissive than anybody configuring a service
    expects. It accepts ``" 7 "`` (surrounding whitespace), ``"+7"`` and
    ``"-7"`` (signs), ``"1_0"`` — which is **ten**, not one-zero — and non-ASCII
    digits such as ``"٧"``. Each of those is a typo that would start the
    service with a number nobody wrote down, on settings that decide how long a
    download may run and how long deleted-file protection lasts. So a non-blank
    value must be ASCII digits and nothing else:

    ``raw.isascii() and raw.isdigit()`` rejects all four cases in one check
    (``"1_0".isdigit()`` is ``False``; ``"+7".isdigit()`` is ``False``;
    ``" 7 ".isdigit()`` is ``False``; ``"٧".isascii()`` is ``False``), and
    requires at least one character.

    **Blank still means "unset".** ``VAULT_GC_GRACE_DAYS=`` (or a value that is
    only whitespace, which is what a stray space after ``=`` in a ``.env`` file
    produces) falls back to ``default``, exactly as it always has and exactly as
    every non-numeric setting in this module treats a blank. The strict rule
    applies to values that actually carry content: an operator who typed
    something meant something, and ``" 7 "`` is then a typo worth reporting
    rather than one worth guessing about.

    Consequence worth stating: because digits-only excludes a leading ``-``,
    this function cannot express a negative ``minimum``. No setting needs one
    (the floors in use are 0 and 1), and the assert below makes that a loud
    programming error rather than a silently unreachable branch if one ever
    does.
    """
    assert minimum >= 0, "_env_int cannot validate a negative minimum"
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        return parse_strict_int(raw, minimum=minimum, maximum=maximum)
    except ValueError as exc:
        raise RuntimeError(f"{name} {exc}") from exc


def parse_strict_int(
    raw: str, *, minimum: int = 1, maximum: int | None = None
) -> int:
    """Pure grammar half of :func:`_env_int` (ADR-0009 decision 4).

    Everything above this function's docstring is the RATIONALE for the
    grammar; this is the grammar itself, deliberately split out so
    ``PATCH /v1/settings`` (``vault_api/settings_store.py``) can validate a
    value with EXACTLY this rule instead of a reimplementation — "the
    grammar functions live in one importable place ... no duplicated
    parsing". Raises ``ValueError`` (not ``RuntimeError``): this function
    knows no env var name to embed in a message, so every caller wraps it —
    ``_env_int`` re-raises as ``RuntimeError`` for the startup path,
    ``settings_store`` re-raises as ``SettingValidationError`` for the PATCH
    path. ``raw`` must already be known non-blank (blank has a different
    meaning — "unset" at startup, "invalid" at PATCH time — that only the
    caller knows how to handle).

    ``maximum`` (WP API-FIX-2, S2) is the ceiling for values that feed a
    ``timedelta``: see ``MAX_INTERVAL_MINUTES``/``MAX_DAYS``. ``None`` means
    "no ceiling", which is right for pure counts (``VAULT_MANIFEST_KEEP``,
    ``VAULT_EVENT_LOG_MAX_BYTES``) that overflow nothing.
    """
    if maximum is not None and maximum < minimum:
        raise ValueError("parse_strict_int cannot validate a maximum below the minimum")
    if minimum < 0:
        # A programming error in a CALLER (every call site in this codebase
        # passes a literal >= 0), not something ``raw`` can trigger — but a
        # bare ``assert`` disappears under ``python -O``, which would turn a
        # loud crash into a silently wrong floor check instead (reviewer nit
        # N4). ``ValueError`` survives -O and every other caller here already
        # catches ``ValueError`` from this function, so this stays inside the
        # same exception contract rather than adding a new one.
        raise ValueError("parse_strict_int cannot validate a negative minimum")
    if not (raw.isascii() and raw.isdigit()):
        raise ValueError(
            "must be a plain integer written in ASCII digits only — no "
            "signs, spaces, underscores or thousands separators, and no "
            f"negative values (the smallest accepted value is {minimum}). "
            f"Got {raw!r}."
        )
    try:
        value = int(raw)
    except ValueError as exc:  # pragma: no cover - only for absurdly long input
        # Digits-only input can still be refused by CPython's
        # int_max_str_digits limit (4300 digits by default), so this stays a
        # guarded conversion rather than a bare int().
        raise ValueError(f"is not a usable integer: {raw!r} ({exc})") from exc
    if value < minimum:
        # The default floor is the plain "positive integer" case; phrase it the
        # way it has always been phrased so existing messages don't change.
        limit = "> 0" if minimum == 1 else f">= {minimum}"
        raise ValueError(f"must be {limit}, got {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"must be <= {maximum}, got {value}")
    return value


def parse_auto_gc(raw: str) -> str:
    """Pure grammar half of :func:`_env_auto_gc` — see ``parse_strict_int``'s
    note on why this is split out (ADR-0009 decision 4: shared with
    ``PATCH /v1/settings``). Blank is NOT accepted here (unlike the env
    wrapper, which treats blank as ``off``): a settings override that clears
    to blank is meaningless — clearing an override is spelled ``null``, not
    an empty string — so a blank value is simply not one of ``AUTO_GC_MODES``
    and falls into the same error as any other unrecognised word.
    """
    text = raw.strip().lower()
    if text not in AUTO_GC_MODES:
        raise ValueError(f"must be one of {', '.join(AUTO_GC_MODES)}, got {raw!r}.")
    return text


def _env_auto_gc(name: str = "VAULT_AUTO_GC") -> str:
    """Read ``VAULT_AUTO_GC`` (WP 3.12). Blank/unset = ``off``.

    Validated at STARTUP rather than at the call site in the worker, for the
    same reason ``VAULT_SCHEDULE_WINDOW`` is: a typo must not surface hours
    later inside a background thread, and ``VAULT_AUTO_GC=exectue`` silently
    falling back to ``off`` would leave an operator believing automatic
    collection is running when nothing is. Case-insensitive and
    whitespace-tolerant (this one IS a word, not a number, and ``Execute`` is
    unambiguous), but a value that is not one of the three is refused with all
    three named.
    """
    raw = os.environ.get(name, "")
    if not raw.strip():
        return DEFAULT_AUTO_GC
    try:
        return parse_auto_gc(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} {exc}") from exc


def _env_manifest_oracle(name: str = "VAULT_MANIFEST_ORACLE") -> str:
    """Read the oracle selector, refusing anything not implemented (WP 3.9).

    Case- and whitespace-insensitive so ``STEAMCMD_API`` and a stray trailing
    space still select the oracle the operator meant; anything else raises at
    startup. See ``SUPPORTED_MANIFEST_ORACLES`` for why an unknown value is a
    hard error rather than a silent "off".
    """
    raw = os.environ.get(name, "").strip().lower()
    if raw not in SUPPORTED_MANIFEST_ORACLES:
        known = ", ".join(repr(v) for v in SUPPORTED_MANIFEST_ORACLES if v)
        raise RuntimeError(
            f"{name}={raw!r} is not a supported manifest oracle "
            f"(known: {known}; leave it unset or empty to disable the oracle)"
        )
    return raw


def _env_manifest_oracle_url(name: str = "VAULT_MANIFEST_ORACLE_URL") -> str:
    """Read the oracle base URL, validated at startup even when the oracle is
    off (same reasoning as the ``VAULT_SCHEDULE_*`` numbers: a typo surfaces
    the day it is made, not the day the feature is switched on).

    Only the scheme is checked. The host is the operator's decision — this is
    a self-hosted service and pointing it at a private mirror is a supported,
    documented use — but a non-http(s) scheme (``file:``, ``ftp:``, a pasted
    ``api.steamcmd.net/v1/info`` with no scheme at all) is a mistake with a
    surprising failure mode, so it is refused here rather than once per
    request inside ``oracle.http_fetch``'s fail-soft path, where it would only
    ever show up as "the oracle never works".
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return DEFAULT_MANIFEST_ORACLE_URL
    scheme = urlsplit(raw).scheme.lower()
    if scheme not in ("http", "https"):
        raise RuntimeError(
            f"{name}={raw!r} must be an http:// or https:// URL "
            f"(got scheme {scheme!r})"
        )
    return raw


#: Characters a syntactically valid hostname may contain (RFC 1123):
#: letters, digits, ``.`` and ``-``. Shared, byte-for-byte, with the
#: character allowlist ``deploy/proxy/validate-hostname.sh`` applies to the
#: SAME environment variable on the proxy side — two independent renderers
#: of one operator-supplied value should refuse the same inputs, not agree
#: only on the happy path (WP EG-1, ADR-0011; round-2 review S2 found three
#: real disagreements before that shared script existed — see its own
#: header comment for what they were and how each was fixed).
_HOSTNAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
)


def _env_egress_allow(name: str = "VAULT_EGRESS_ALLOW") -> frozenset[str]:
    """Read the extra outbound-hostname allowlist (WP EG-1, ADR-0011).

    Comma-separated, blank/unset = empty (the shipped default — see
    ``deploy/compose.yaml``'s comment on this key: the egress lock ships
    default-on with an empty allowlist, not a wide-open one). Every entry is
    lowercased (DNS names are case-insensitive, RFC 4343) and stripped of
    surrounding whitespace; a blank entry from a trailing comma or repeated
    commas is silently dropped rather than treated as an error — that is a
    typo an operator will notice immediately from a missing host, not one
    that hides anything.

    A non-blank entry containing anything other than ``_HOSTNAME_CHARS`` is a
    hard error, not a silently-dropped or silently-passed-through one: this
    value ends up rendered verbatim into ``deploy/proxy``'s tinyproxy filter
    file on the OTHER container, and a value valid enough to reach here but
    rejected there would fail *that* container's boot instead — better to
    name the actual bad entry from the process an operator is looking at
    (`docker compose logs vault-api` after a boot failure) than from the one
    they are not.

    Round-2 review S2: the SHAPE checks below (leading/trailing ``.``/``-``,
    and now an empty label, ``".."``) exist for that exact reason and must
    match ``deploy/proxy/validate-hostname.sh``'s own ``case`` arm
    byte-for-byte, not just the character class. The empty-label case was
    the one this function was missing: ``"a..b"`` passed here (every
    character is individually allowed, and it neither starts nor ends with
    ``.``/``-``) while the proxy's own script already refused it — vault-api
    would boot "clean" on a value the OTHER container then crash-looped on.
    """
    raw = os.environ.get(name, "")
    hosts: set[str] = set()
    for entry in raw.split(","):
        host = entry.strip().lower()
        if not host:
            continue
        implausible_shape = (
            host.startswith((".", "-")) or host.endswith((".", "-")) or ".." in host
        )
        if not set(host) <= _HOSTNAME_CHARS or implausible_shape:
            raise RuntimeError(
                f"{name} contains an entry {entry.strip()!r} that is not a "
                "plausible hostname (letters, digits, '.', '-' only; no "
                "leading/trailing '.' or '-'; no empty label \"..\"). Fix "
                "deploy/.env's VAULT_EGRESS_ALLOW."
            )
        hosts.add(host)
    return frozenset(hosts)


def _is_plain_decimal(raw: str) -> bool:
    """Is ``raw`` a plain ASCII decimal literal — ``digits`` or ``digits.digits``?

    The float counterpart of ``_env_int``'s ``isascii() and isdigit()`` check,
    and written the same way (``str.isdigit`` on ASCII text is exactly
    ``[0-9]+``) rather than with a regex, because ``re``'s ``\\d`` matches
    Unicode digits unless ``re.ASCII`` is passed — the very trap the
    ``isascii()`` gate exists to close.

    Accepts: ``"60"``, ``"1.0"``, ``"0.25"``, ``"3.5"``.
    Rejects everything else, including ``".5"`` and ``"5."`` (write ``0.5`` and
    ``5``), signs, whitespace, underscores, exponents, and — the reason this
    function exists at all — ``"nan"``/``"inf"``.
    """
    if not raw.isascii():
        return False
    whole, dot, fraction = raw.partition(".")
    if dot:
        return whole.isdigit() and fraction.isdigit()
    return whole.isdigit()


def _env_float(name: str, default: float) -> float:
    """Read a positive float env var, falling back to ``default``.

    **Strict by the same house rule as ``_env_int`` (WP 3.12 review), and for a
    sharper reason.** ``float()`` accepts everything ``int()`` does — ``" 1.5 "``,
    ``"+1.5"``, ``"1_0"`` (**ten**), non-ASCII digits — *plus* ``"nan"`` and
    ``"inf"``, and ``nan`` is the one input that gets past a range check
    silently: ``nan <= 0`` is ``False``, so the old guard below let it through.
    The two settings this reads are not harmless if that happens:

    - ``VAULT_SIZE_CACHE_TTL=nan`` makes ``SizeCache``'s freshness test
      ``(now - computed_at) < ttl`` **always false**, so every single request
      re-walks the whole ``depot/`` tree — precisely the footgun the
      "``0`` is rejected, there is no disable switch" rule was written to
      prevent, reintroduced through the back door.
    - ``VAULT_WORKER_POLL_SECONDS=nan`` is handed straight to
      ``threading.Event.wait(nan)`` on every idle tick.

    So the value must be a plain ASCII decimal literal (see
    ``_is_plain_decimal``). **Blank still means "unset"**, same as everywhere
    else in this module.
    """
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        return parse_strict_float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} {exc}") from exc


def parse_strict_float(raw: str) -> float:
    """Pure grammar half of :func:`_env_float` — see ``parse_strict_int``'s
    note (ADR-0009 decision 4: the exact same function backs both the
    startup path and ``PATCH /v1/settings``'s validation).
    """
    if not _is_plain_decimal(raw):
        raise ValueError(
            "must be a plain positive number written in ASCII digits "
            "with at most one decimal point (e.g. '60' or '0.25') — no signs, "
            "spaces, underscores, exponents, and not 'nan' or 'inf'. "
            f"Got {raw!r}."
        )
    try:
        value = float(raw)
    except ValueError as exc:  # pragma: no cover - the grammar above precludes it
        raise ValueError(f"is not a usable number: {raw!r} ({exc})") from exc
    # A grammatically valid but absurdly long digit string (400 digits, say)
    # still overflows to `inf` in float() — the one way `inf` can survive the
    # literal check above, so it is refused here rather than assumed away.
    if not math.isfinite(value):
        raise ValueError(f"is too large to represent as a number, got {raw!r}.")
    if value <= 0:
        raise ValueError(f"must be > 0, got {value}")
    return value


# --------------------------------------------------------------------------
# WP 4h.0 (ADR-0010). Server-side privacy gate for the Steam relay's
# ``playtime_forever`` / ``rtime_last_played`` fields
# (``vault_api/routers/steam.py``). Both are ENV-ONLY, deliberately outside
# ``settings_store.OVERRIDABLE_SPECS`` even though they are booleans exactly
# like ``sweep_include_cached``/``auto_gc``, which ARE DB-overridable.
#
# **Why no runtime toggle, argued in full in docs/adr/0010-*.md:** the
# ``settings`` table (ADR-0009) lives in the ``vault-db`` Docker volume.
# ``docker compose down -v``, a lost volume, or a rebuild on new hardware
# erases every override row, and the value then falls back to whatever the
# ENVIRONMENT says. For an ordinary tuning knob that is a harmless "reverts
# to a sane default, someone notices". For a privacy opt-out the direction
# of that fallback is the whole question: an operator who deliberately set
# a DB override to turn exposure OFF while the environment still says "on"
# would have that override silently erased by a lost volume, and collection
# would resume with no notification and nothing in the log. A control whose
# failure mode is "quietly starts collecting personal data again" is not a
# control, so these two keys have exactly one source of truth -- the
# environment, which lives in the operator's own compose file/`.env`,
# covered by whatever backs THAT up, not the database volume.
# --------------------------------------------------------------------------

#: Off by default. The Phase 4h privacy stance (docs/PROJECT_PLAN.md, user
#: decision 2026-08-18) already treats playtime itself -- not only
#: rtime_last_played -- as something a shared-household vault must not
#: surface without an explicit opt-in: "playtime makes the UI judgemental
#: ... off by default or dismissible at any time, no nagging, and no number
#: that gets held up to somebody else." That is the house style the WP 3.11
#: event sweep already follows for every cost-sensitive switch that has no
#: OTHER party's data at stake: ship off, let an operator who wants it read
#: the README and turn it on. **`DEFAULT_SWEEP_INCLUDE_CACHED` above is no
#: longer a same-direction example of that house style (WP SWEEP-1,
#: ADR-0014) -- it now ships ON by default.** That is not a contradiction:
#: the two settings differ in KIND, not just in value. Disk/bandwidth spent
#: refreshing a cached game is a cost the OPERATOR who runs this vault pays
#: and can see (disk usage, a log line); a moment of someone else's playtime
#: surfaced without their knowledge is a cost a DIFFERENT person pays, who
#: may never know it happened -- the failure-mode asymmetry the section
#: banner above this constant explains at length is what keeps this one
#: default at "off" while the sweep/auto-GC pair moved to "on". "The
#: decision-support panel needs it" (docs/PROJECT_PLAN.md Phase 4h) is
#: explicitly NOT treated as a sufficient reason to default this on --
#: WP 4h.0's own brief asks for a stronger argument than that, and the
#: privacy stance above is the one actually used. With no runtime override
#: for either key (see the section note above), this default is also the
#: ONLY value most installs will ever see unless an operator edits the
#: environment, which makes getting it right here more consequential than
#: an ordinary DEFAULT_*, not less.
DEFAULT_RELAY_EXPOSE_PLAYTIME = False

#: Off by default, for the same reason, and doubly so: WP 4h.1's own note
#: calls this "the sharper fact of the two" -- "when did this person last
#: play" reads as surveillance in a way an aggregate hour count does not.
DEFAULT_RELAY_EXPOSE_LAST_PLAYED = False


# --------------------------------------------------------------------------
# WP S-1 (ADR-0012): the runner split. vault-api's worker keeps owning job
# lifecycle; whether it also OWNS the SteamPrefill subprocess itself
# (``subprocess``, the byte-identical pre-WP-S-1 behaviour) or hands
# execution off to a separate ``python -m vault_api.prefill_runner`` process
# through the jobs table (``queue``, what S-2's compose wiring and EG-1's
# egress lock both need) is this one switch.
# --------------------------------------------------------------------------

#: The default, and the only mode that existed before this work package —
#: vault-api's worker calls ``prefill.run_prefill`` directly, in its own
#: process, exactly as it always has. Required for the bare-metal/native dev
#: setup (api/README.md), where there is no second container to run a
#: runner in at all.
PREFILL_MODE_SUBPROCESS = "subprocess"

#: vault-api's worker hands a claimed job off via the ``jobs`` table
#: (``vault_api/jobs.py``'s ``handoff_run``/queue-mode columns,
#: ``vault_api/prefill_queue.py``) instead of calling ``run_prefill`` itself,
#: then waits for a ``prefill_runner`` process (S-2 wires it into
#: ``compose.yaml`` as its own container) to claim, execute and report back.
PREFILL_MODE_QUEUE = "queue"

SUPPORTED_PREFILL_MODES = (PREFILL_MODE_SUBPROCESS, PREFILL_MODE_QUEUE)


def _env_prefill_mode(name: str = "VAULT_PREFILL_MODE") -> str:
    """Read ``VAULT_PREFILL_MODE``. Blank/unset = ``subprocess`` (unchanged
    behaviour). Same strict-enum house rule as ``VAULT_AUTO_GC``/
    ``VAULT_MANIFEST_ORACLE`` — a typo here would silently keep vault-api
    running SteamPrefill in-process on a host where the operator believes
    the egress lock (EG-1) is already in effect, which is a security-relevant
    misunderstanding, not a cosmetic one.
    """
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return PREFILL_MODE_SUBPROCESS
    if raw not in SUPPORTED_PREFILL_MODES:
        raise RuntimeError(
            f"{name}={raw!r} is not a supported prefill mode "
            f"(known: {', '.join(SUPPORTED_PREFILL_MODES)})"
        )
    return raw


#: How often ``prefill_runner`` refreshes ``jobs.run_heartbeat_at`` for the
#: job it currently holds, piggybacked on ``prefill.run_prefill``'s existing
#: 0.2s subprocess poll tick (throttled to this interval — seven writes a
#: second to the shared database for one job is needless churn). Also the
#: resolution of the "did the runner die" signal: a dead runner's last
#: heartbeat is at most this old when it stops updating.
DEFAULT_RUNNER_HEARTBEAT_SECONDS = 5.0

#: How stale ``run_heartbeat_at`` (or, if never claimed, ``run_claimed_at`` /
#: the job's own ``started_at``) must be before vault-api's worker treats a
#: queue-mode job's runner as dead (``jobs.run_is_stale``). Several multiples
#: of the heartbeat interval on purpose: a single missed heartbeat (a GC
#: pause, a slow disk fsync under WAL) must not read as a dead process — see
#: ADR-0012 §4 for the margin reasoning and §3 for the measured WAL
#: contention numbers it is weighed against. Also note (round-2 review S6):
#: this project's stored timestamps are second-precision
#: (``jobs.TIMESTAMP_FORMAT``), so a live staleness check against one can
#: OVER-report elapsed time by almost a full second — a value set close to
#: that floor risks a false "presumed dead" against a genuinely alive
#: runner, independently of the heartbeat-interval margin above.
DEFAULT_RUNNER_LEASE_TIMEOUT_SECONDS = 30.0

#: How often ``prefill_runner`` polls for a job to claim when idle.
#: **Only consumed by the runner process** — vault-api's worker reuses
#: ``VAULT_WORKER_POLL_SECONDS`` for its OWN wait-on-hand-off loop in queue
#: mode (``PrefillWorker._run_prefill_via_queue``); this is a separate
#: setting (not a shared one) because the runner is a separate
#: process/container with its own tuning story once S-2 wires it up, even
#: though the two defaults happen to start out equal.
DEFAULT_RUNNER_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the environment at startup."""

    vault_api_key: str
    db_path: str
    cache_root: str
    log_level: str
    # WP 1.4. Path to the SteamPrefill executable. Deliberately NOT required
    # at startup: vault-api must still serve /v1/games, /v1/mapping and
    # /v1/health on a box where SteamPrefill hasn't been set up yet. A prefill
    # job then fails with a clear per-job error instead of taking the whole
    # app down (see vault_api/prefill.py).
    steamprefill_path: str = ""
    prefill_timeout_seconds: int = DEFAULT_PREFILL_TIMEOUT_SECONDS
    worker_poll_seconds: float = DEFAULT_WORKER_POLL_SECONDS
    # WP 1.5. TTL (seconds) for the in-process per-game size cache.
    size_cache_ttl_seconds: float = DEFAULT_SIZE_CACHE_TTL_SECONDS
    # WP 2.4. Snapshots kept per client in agent_reports (retention).
    agent_report_keep: int = DEFAULT_AGENT_REPORT_KEEP
    # WP 3.2. Where archived manifest .bin files are copied durably (they do
    # NOT survive SteamPrefill's own `clear-temp`). Literal default here is
    # deliberately dumb ("./manifests", mirroring db_path/cache_root's own
    # plain literal defaults) — `from_env()` computes the smarter
    # db-relative default (see `_default_manifest_archive_dir`) when the env
    # var is unset, since only `from_env()` knows the real `db_path`.
    manifest_archive_dir: str = "./manifests"
    # WP 3.2. How many archived manifests `prune_archive` keeps per depot.
    manifest_keep: int = DEFAULT_MANIFEST_KEEP
    # WP 3.2. SteamPrefill's own manifest temp-cache directory to scan after a
    # successful prefill job (docs/research/phase3-manifests.md §1a).
    # `default_factory` (not a class-body literal) so each direct
    # construction re-reads LOCALAPPDATA/HOME at call time, same as
    # `from_env()` does when the env var is unset.
    steamprefill_cache_dir: str = field(default_factory=_default_steamprefill_cache_dir)
    # WP 3.8b. Days a stored chunk is protected from GC (ADR-0007 beta-branch
    # addendum, decision A). 0 = no window, every planned orphan is deleted.
    gc_grace_days: int = DEFAULT_GC_GRACE_DAYS
    # WP 3.5. The daytime window the scheduler sweeps in (plan §7 Phase 3).
    # ``None`` = scheduler disabled, and that is the DEFAULT on purpose: the
    # scheduler starts Steam logins and downloads on its own schedule, which
    # is not something a fresh install should begin doing because the operator
    # never got around to reading the docs. Opt in by setting
    # VAULT_SCHEDULE_WINDOW.
    schedule_window: ScheduleWindow | None = None
    # WP 3.5. Minimum spacing between two sweeps.
    schedule_interval_minutes: int = DEFAULT_SCHEDULE_INTERVAL_MINUTES
    # WP 3.5. Clients whose newest agent report is older than this are
    # excluded from the sweep's target set.
    schedule_client_stale_days: int = DEFAULT_SCHEDULE_CLIENT_STALE_DAYS
    # WP 3.12. 'off' | 'dry-run' | 'execute' — see AUTO_GC_MODES above.
    auto_gc: str = DEFAULT_AUTO_GC
    # WP 4d (plan §7 Phase 4d). Additive sweep target-set mode: every app with
    # SOME cache content on disk joins the installed-based union
    # `scheduler.compute_targets` already sweeps. ON by default since
    # WP SWEEP-1 (ADR-0014) — see DEFAULT_SWEEP_INCLUDE_CACHED above for the
    # cost model, the pairing with `auto_gc` below, and api/README.md
    # "Sweep target set" for the full write-up, including the auto-GC
    # coupling this setting deliberately does not hide.
    sweep_include_cached: bool = DEFAULT_SWEEP_INCLUDE_CACHED
    # WP 3.11 (ADR-0008). Path to vault-core's structured cache-event log.
    # EMPTY = the whole feature is off: no sweeping, no statistics tables
    # growing, no miss trigger. This is the one enable switch.
    event_log_path: str = ""
    # WP 3.11. Minutes between sweeps of that log. NOT gated on
    # schedule_window — see DEFAULT_EVENT_SWEEP_INTERVAL_MINUTES.
    event_sweep_interval_minutes: int = DEFAULT_EVENT_SWEEP_INTERVAL_MINUTES
    # WP 3.11. Per-app cooldown for the miss trigger. 0 = trigger OFF.
    miss_trigger_cooldown_minutes: int = DEFAULT_MISS_TRIGGER_COOLDOWN_MINUTES
    # WP 3.11. Hard cap on miss-triggered enqueues per sweep.
    miss_trigger_max_per_sweep: int = DEFAULT_MISS_TRIGGER_MAX_PER_SWEEP
    # WP 3.11. Cache-log silence beyond this many days makes a still-reporting
    # client `bypass_suspected` in GET /v1/clients.
    bypass_window_days: int = DEFAULT_BYPASS_WINDOW_DAYS
    # WP 3.11. Per-sweep statistics rows retained per client address.
    client_stats_keep: int = DEFAULT_CLIENT_STATS_KEEP
    # WP 3.11. Truncate the event log at/above this size once fully consumed.
    # 0 = never truncate.
    event_log_max_bytes: int = DEFAULT_EVENT_LOG_MAX_BYTES
    # WP 3.13. Generic webhook target. EMPTY = the whole feature is off — the
    # one enable switch, same shape as event_log_path above.
    webhook_url: str = ""
    # WP 3.13. Which of WEBHOOK_EVENTS_ALL to actually send. Defaults to all
    # five (an operator who turns the feature on almost always wants every
    # event; opting OUT of one is the less common case and is what the comma
    # list is for).
    webhook_events: frozenset[str] = field(
        default_factory=lambda: frozenset(WEBHOOK_EVENTS_ALL)
    )
    # WP 3.13. Per-attempt HTTP timeout for one delivery try.
    webhook_timeout_seconds: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS
    # WP 3.13. Optional operator-chosen label for this vault instance, carried
    # in every webhook payload's "vault_name" field so a receiver aggregating
    # several installs can tell them apart. Blank = the field is omitted
    # entirely (see webhooks.py) rather than sent as "".
    vault_name: str = ""
    # WP 3.9 / ADR-0006 decision 4. Which third-party manifest oracle to use.
    # "" (the default) = none, and that default is load-bearing: it is the
    # difference between a vault-api that talks only to the LAN and one that
    # queries an external service. See vault_api/oracle.py.
    manifest_oracle: str = MANIFEST_ORACLE_OFF
    manifest_oracle_url: str = DEFAULT_MANIFEST_ORACLE_URL
    manifest_oracle_timeout: float = DEFAULT_MANIFEST_ORACLE_TIMEOUT
    # WP 4a.1. Directory the built-in web UI is served from. `default_factory`
    # (not a class-body literal) for the same reason `steamprefill_cache_dir`
    # uses one: it re-resolves against this file's own location at
    # construction time, which is what makes the default correct regardless
    # of process cwd. A missing directory is not an error — see
    # `_default_web_dir` and `webui.mount_web_ui`.
    web_dir: str = field(default_factory=_default_web_dir)
    # Settings-API work package (ADR-0009 decision 3): the operator hard-lock
    # for PATCH /v1/settings. Env-only BY DEFINITION — a flag that disables
    # the settings-write API could not itself be turned back on through that
    # same API without defeating the point. False (read-write) is the
    # default: a fresh install gets the writable settings API described in
    # the README, and a GitOps/headless deployment opts into the pre-ADR-0009
    # pure-env posture explicitly.
    settings_readonly: bool = False
    # WP 4h.0 (ADR-0010). Whether the Steam relay (routers/steam.py) may
    # include playtime_forever / rtime_last_played in its response at all.
    # Env-only -- see this file's own "WP 4h.0 (ADR-0010)" section above for
    # why these two are NOT in settings_store.OVERRIDABLE_SPECS despite
    # being ordinary booleans. Off by default (DEFAULT_RELAY_EXPOSE_PLAYTIME/
    # _LAST_PLAYED above).
    relay_expose_playtime: bool = DEFAULT_RELAY_EXPOSE_PLAYTIME
    relay_expose_last_played: bool = DEFAULT_RELAY_EXPOSE_LAST_PLAYED
    # WP S-1 (ADR-0012). 'subprocess' (default, unchanged behaviour) |
    # 'queue' (hand off execution to a separate prefill_runner process). See
    # the PREFILL_MODE_* constants above.
    prefill_mode: str = PREFILL_MODE_SUBPROCESS
    # WP S-1. Queue-mode-only tuning; harmless and unused in 'subprocess' mode.
    runner_heartbeat_seconds: float = DEFAULT_RUNNER_HEARTBEAT_SECONDS
    runner_lease_timeout_seconds: float = DEFAULT_RUNNER_LEASE_TIMEOUT_SECONDS
    runner_poll_seconds: float = DEFAULT_RUNNER_POLL_SECONDS
    # WP EG-1 (ADR-0011): extra outbound hostnames the egress-lock proxy
    # (`deploy/proxy`) should let vault-api reach, beyond the one host baked
    # into that proxy's own image (the Steam Web API relay's host — see
    # `deploy/proxy/docker-entrypoint.sh`'s comment for why that one is not
    # gated behind this setting). Empty by default: the shipped lock is
    # default-on with an empty allowlist, not a wide-open one. vault-api
    # itself never makes an HTTP request gated by this value directly — it
    # only cross-checks it, at startup, against the manifest oracle's host
    # when that feature is enabled (`from_env`'s "oracle host missing from
    # allowlist" check, immediately before it returns) — the actual
    # enforcement is entirely on the proxy container's side, over a network
    # vault-api has no visibility into. Deliberately checked in `from_env`,
    # not `__post_init__`: this is a check on the ENVIRONMENT (the real boot
    # path), not an invariant of `Settings` itself, and this project's own
    # tests construct `Settings` directly, with `manifest_oracle` set and no
    # matching allowlist, in contexts that have nothing to do with EG-1
    # (e.g. `test_oracle.py`) — those must keep working unchanged.
    egress_allow: frozenset[str] = field(default_factory=frozenset)

    @property
    def prefill_mode_queue(self) -> bool:
        """True iff execution is delegated to a separate runner process."""
        return self.prefill_mode == PREFILL_MODE_QUEUE

    @property
    def webhook_enabled(self) -> bool:
        """True iff a webhook URL is configured — the one enable switch."""
        return bool(self.webhook_url)

    @property
    def scheduler_enabled(self) -> bool:
        """True iff a window is configured (WP 3.5) — the one enable switch."""
        return self.schedule_window is not None

    @property
    def event_sweep_enabled(self) -> bool:
        """True iff an event-log path is configured (WP 3.11, ADR-0008).

        The single enable switch for the whole cache-event feature. With no
        path there is nothing to read, so the sweeper does no work, none of the
        statistics tables grow, the miss trigger cannot fire, and
        ``bypass_suspected`` is ``False`` for everyone (no data is not
        evidence — see ``routers/clients.py``).
        """
        return bool(self.event_log_path)

    @property
    def miss_trigger_enabled(self) -> bool:
        """True iff a sweep may enqueue miss-triggered prefills (ADR-0001).

        Two conditions, and the first one is the interesting one: the trigger
        is **on by default whenever the sweep runs**. The operator already made
        the opt-in decision by pointing ``VAULT_EVENT_LOG_PATH`` at the log —
        miss→prefill completion is not a bonus feature bolted onto that, it is
        the *reason* ADR-0001 chose the hybrid miss handling, staged to "lands
        in Phase 3 together with the scheduler/job infrastructure". Shipping it
        behind a second switch defaulted to off would mean the hybrid decision
        never actually runs anywhere unless an operator reads far enough into
        the docs, which is how a decided architecture quietly becomes
        store-on-miss only.

        ``VAULT_MISS_TRIGGER_COOLDOWN_MINUTES=0`` is the explicit off switch
        for operators who want the statistics without the enqueues.
        """
        return self.event_sweep_enabled and self.miss_trigger_cooldown_minutes > 0

    @property
    def auto_gc_enabled(self) -> bool:
        """True iff a successful, updating prefill should queue a GC job."""
        return self.auto_gc != AUTO_GC_OFF

    @property
    def auto_gc_executes(self) -> bool:
        """True iff auto-queued GC jobs are allowed to DELETE.

        Spelled as its own property so the one place that turns configuration
        into ``enqueue_gc(execute=...)`` reads as a single named fact rather
        than a string comparison inlined in the worker.
        """
        return self.auto_gc == AUTO_GC_EXECUTE

    @property
    def manifest_oracle_enabled(self) -> bool:
        """True iff an oracle is configured (WP 3.9) — the one enable switch.

        Every read path in ``vault_api/oracle.py`` consults this rather than
        assuming its caller did, so "the oracle is off" cannot be bypassed by
        forgetting a check at one call site.
        """
        return self.manifest_oracle != MANIFEST_ORACLE_OFF

    @staticmethod
    def from_env(*, require_api_key: bool = True) -> "Settings":
        """Build Settings from the current environment.

        Raises RuntimeError if VAULT_API_KEY is missing or empty — there is
        deliberately no default (plan §9: no unauthenticated endpoints beyond
        the documented /v1/health exception).

        ``require_api_key=False`` (WP S-1 round-2 review, S2; ADR-0012 §2/§5
        addendum): the ONE other caller of this method,
        ``vault_api.prefill_runner.main``, loads the full ``Settings``
        dataclass too — one validation path for every setting, rather than a
        second parser class duplicating this one's grammar rules — but
        ``prefill_runner`` is a process with broad Steam CM/CDN egress that
        never serves HTTP and never authenticates a request, so it has no
        legitimate use for the LAN control-plane secret every OTHER consumer
        of this dataclass needs. Requiring it there anyway would mean EG-1's
        egress-locked, broad-network container also has to hold the one
        secret that gates every non-health vault-api endpoint — a posture
        cost with no corresponding benefit, and inconsistent with this same
        ADR rejecting an HTTP sidecar specifically for needing "a second API
        key... to design and operate" (ADR-0012 §2). Exempting only this one
        field, rather than writing a second config loader, keeps "one
        validation path" for every other setting `prefill_runner` also reads
        (``db_path``, ``steamprefill_path``, ``prefill_timeout_seconds``,
        the three ``runner_*`` tunables) with a single line of difference.
        """
        api_key = os.environ.get("VAULT_API_KEY", "").strip()
        if require_api_key and not api_key:
            raise RuntimeError(
                "VAULT_API_KEY is required and must not be empty. "
                "Set it in the environment or in a .env file "
                "(copy api/.env.example to api/.env and fill it in)."
            )

        db_path = os.environ.get("VAULT_DB_PATH", "./vault.db")
        # Same present-but-blank gap as VAULT_CACHE_ROOT below (WP API-FIX-2,
        # P1), with a nastier consequence: ``sqlite3.connect("")`` opens a
        # PRIVATE temporary database per connection, so every request and
        # every background thread would see its own empty schema and nothing
        # would ever persist -- no error, just a vault-api that forgets
        # everything between two calls.
        if not db_path.strip():
            raise RuntimeError(
                "VAULT_DB_PATH must not be blank. An absent variable falls back "
                "to './vault.db'; a present-but-empty one (e.g. a compose key "
                "forwarded via ${VAULT_DB_PATH} interpolation with nothing set "
                "in .env) does not, and sqlite3 would then open a private "
                "in-memory-like temporary database per connection -- nothing "
                "would persist. Set VAULT_DB_PATH to a real path, or unset it "
                "entirely to accept the './vault.db' default."
            )

        # WP 4f. `.get(..., "./cache")` only supplies that default when the
        # key is ABSENT from the environment. A key that is simply not
        # forwarded by a compose file/derived image is exactly that -- absent
        # -- and the default applies fine; that is NOT the failure mode this
        # guards (S3, reviewer correction, 2026-08-18 review round: an
        # earlier draft of this comment named the wrong mechanism). The real
        # blank case is a key that IS present with an EMPTY value: a compose
        # `environment:` entry that forwards it via `${VAULT_CACHE_ROOT}`
        # interpolation with nothing set in `.env` renders as
        # `VAULT_CACHE_ROOT=` in the container's environment, and a bare
        # `KEY:` (compose) or `ENV KEY=` (a derived Dockerfile) does the same
        # thing explicitly. Either way `os.environ.get` returns `""`, not the
        # default, and that bypasses the default entirely -- `cache_root=""`
        # used to sail straight through. That is not a cosmetic gap: with
        # VAULT_SWEEP_INCLUDE_CACHED on, every sweep then calls
        # `scheduler.compute_targets(include_cached=True, cache_root="")`,
        # which raises `ValueError` (WP 4d's own S1 loud-failure guard) —
        # inside a background thread that catches every exception and
        # retries next tick (`PrefillScheduler._tick`), so the INSTALLED-based
        # half of the sweep silently stops too, forever, once per interval,
        # with nothing but a repeating traceback in the log. Refusing to boot
        # is what turns `compute_targets`' `ValueError` back into what it was
        # meant to be: an internal-contract assertion that can only fire on a
        # programming mistake, never on operator misconfiguration reaching it
        # live. Same strict-grammar house style as every other startup check
        # in this function: fail loudly now, not hours later in a thread.
        raw_cache_root = os.environ.get("VAULT_CACHE_ROOT", "./cache")
        if not raw_cache_root.strip():
            raise RuntimeError(
                "VAULT_CACHE_ROOT must not be blank. An absent variable falls "
                "back to './cache'; a present-but-empty one (e.g. a compose "
                "key forwarded via ${VAULT_CACHE_ROOT} interpolation with "
                "nothing set in .env, or a bare 'KEY:'/'ENV KEY=' in a "
                "derived image) does not, and would let vault-api boot "
                "pointed at no cache directory at all -- every deletion, "
                "size calculation and (with VAULT_SWEEP_INCLUDE_CACHED on) "
                "sweep would then fail or silently do nothing. Set "
                "VAULT_CACHE_ROOT to a real path, or unset it entirely to "
                "accept the './cache' default (see api/.env.example)."
            )

        manifest_archive_dir = os.environ.get("VAULT_MANIFEST_ARCHIVE_DIR", "").strip()
        steamprefill_cache_dir = os.environ.get("VAULT_STEAMPREFILL_CACHE_DIR", "").strip()

        # WP 3.5. Unset/blank = the scheduler stays off (the safe default);
        # anything else must parse NOW, at startup, rather than failing on the
        # first tick hours later inside a background thread where nobody is
        # looking. The interval and staleness bound are validated even when no
        # window is set, so a typo in them surfaces immediately too rather
        # than the day the operator enables the window.
        raw_window = os.environ.get("VAULT_SCHEDULE_WINDOW", "").strip()
        schedule_window: ScheduleWindow | None = None
        if raw_window:
            try:
                schedule_window = parse_window(raw_window)
            except ScheduleWindowError as exc:
                raise RuntimeError(f"VAULT_SCHEDULE_WINDOW is invalid: {exc}") from exc

        # WP EG-1 (ADR-0011). Computed here, ahead of the `Settings(...)`
        # call below, so the two can be cross-checked BEFORE vault-api
        # finishes booting: an operator who turns the oracle on but forgets
        # to allowlist its host would otherwise boot cleanly and then watch
        # every oracle query fail with a filtered-403 from the proxy
        # container, forever, with no hint pointing back at this file. See
        # `_env_egress_allow`'s own docstring for why a bad hostname
        # character is a hard error here specifically (not just on the
        # proxy's side, which also validates the identical value).
        egress_allow = _env_egress_allow()
        manifest_oracle = _env_manifest_oracle()
        manifest_oracle_url = _env_manifest_oracle_url()
        if manifest_oracle != MANIFEST_ORACLE_OFF:
            oracle_host = (urlsplit(manifest_oracle_url).hostname or "").lower()
            if not oracle_host or oracle_host not in egress_allow:
                raise RuntimeError(
                    f"VAULT_MANIFEST_ORACLE={manifest_oracle!r} is enabled, but "
                    f"its host ({oracle_host or manifest_oracle_url!r}) is not "
                    "listed in VAULT_EGRESS_ALLOW. Once the egress lock "
                    "(deploy/proxy, ADR-0011) is in effect, every oracle query "
                    "would otherwise fail with a filtered-403 from the proxy "
                    "container, forever, with nothing pointing back at this "
                    "as the cause. Add the host to VAULT_EGRESS_ALLOW in "
                    "deploy/.env (see deploy/.env.example), or turn the oracle "
                    "back off."
                )

        return Settings(
            vault_api_key=api_key,
            db_path=db_path,
            cache_root=raw_cache_root,
            log_level=os.environ.get("VAULT_LOG_LEVEL", "INFO"),
            steamprefill_path=os.environ.get("VAULT_STEAMPREFILL_PATH", "").strip(),
            prefill_timeout_seconds=_env_int(
                "VAULT_PREFILL_TIMEOUT_SECONDS", DEFAULT_PREFILL_TIMEOUT_SECONDS
            ),
            worker_poll_seconds=_env_float(
                "VAULT_WORKER_POLL_SECONDS", DEFAULT_WORKER_POLL_SECONDS
            ),
            size_cache_ttl_seconds=_env_float(
                "VAULT_SIZE_CACHE_TTL", DEFAULT_SIZE_CACHE_TTL_SECONDS
            ),
            agent_report_keep=_env_int(
                "VAULT_AGENT_REPORT_KEEP",
                DEFAULT_AGENT_REPORT_KEEP,
                minimum=MIN_AGENT_REPORT_KEEP,
            ),
            manifest_archive_dir=manifest_archive_dir
            or _default_manifest_archive_dir(db_path),
            manifest_keep=_env_int(
                "VAULT_MANIFEST_KEEP", DEFAULT_MANIFEST_KEEP, minimum=MIN_MANIFEST_KEEP
            ),
            steamprefill_cache_dir=steamprefill_cache_dir
            or _default_steamprefill_cache_dir(),
            # minimum=0 because 0 is a meaningful value here ("no grace
            # window"), unlike VAULT_MANIFEST_KEEP where 0 would defeat the
            # feature. A negative value or anything non-integer is still
            # refused at startup — a typo must not silently become "protect
            # nothing" on a deletion path.
            gc_grace_days=_env_int(
                "VAULT_GC_GRACE_DAYS", DEFAULT_GC_GRACE_DAYS, minimum=0, maximum=MAX_DAYS
            ),
            schedule_window=schedule_window,
            # maximum= on every timedelta-fed key (WP API-FIX-2, S2): see
            # MAX_INTERVAL_MINUTES/MAX_DAYS for the OverflowError this closes.
            schedule_interval_minutes=_env_int(
                "VAULT_SCHEDULE_INTERVAL_MINUTES",
                DEFAULT_SCHEDULE_INTERVAL_MINUTES,
                maximum=MAX_INTERVAL_MINUTES,
            ),
            schedule_client_stale_days=_env_int(
                "VAULT_SCHEDULE_CLIENT_STALE_DAYS",
                DEFAULT_SCHEDULE_CLIENT_STALE_DAYS,
                maximum=MAX_DAYS,
            ),
            auto_gc=_env_auto_gc(),
            # WP 4d / WP SWEEP-1 (ADR-0014). Blank/unset = on (see
            # DEFAULT_SWEEP_INCLUDE_CACHED above for why the default flipped,
            # and for how an operator opts back out).
            sweep_include_cached=_env_bool(
                "VAULT_SWEEP_INCLUDE_CACHED", DEFAULT_SWEEP_INCLUDE_CACHED
            ),
            # WP 3.11 (ADR-0008). Blank/unset = the whole cache-event feature
            # stays off. The path itself is NOT validated here beyond
            # stripping: vault-api may legitimately start before vault-core has
            # created the file (fresh volume, core not up yet), so "does it
            # exist / can I read it" is a per-sweep, warn-and-skip question,
            # never a reason to refuse to boot — the same degradation
            # ADR-0008 promises ("vault-api without read access to it degrades
            # to today's behavior").
            event_log_path=os.environ.get("VAULT_EVENT_LOG_PATH", "").strip(),
            event_sweep_interval_minutes=_env_int(
                "VAULT_EVENT_SWEEP_INTERVAL_MINUTES",
                DEFAULT_EVENT_SWEEP_INTERVAL_MINUTES,
                maximum=MAX_INTERVAL_MINUTES,
            ),
            # minimum=0 because 0 is a meaningful value here: it is the ONE
            # documented way to run the sweep (statistics, bypass detection,
            # rotation) with the miss trigger disabled.
            miss_trigger_cooldown_minutes=_env_int(
                "VAULT_MISS_TRIGGER_COOLDOWN_MINUTES",
                DEFAULT_MISS_TRIGGER_COOLDOWN_MINUTES,
                minimum=0,
                maximum=MAX_COOLDOWN_MINUTES,
            ),
            miss_trigger_max_per_sweep=_env_int(
                "VAULT_MISS_TRIGGER_MAX_PER_SWEEP",
                DEFAULT_MISS_TRIGGER_MAX_PER_SWEEP,
            ),
            bypass_window_days=_env_int(
                "VAULT_BYPASS_WINDOW_DAYS", DEFAULT_BYPASS_WINDOW_DAYS, maximum=MAX_DAYS
            ),
            client_stats_keep=_env_int(
                "VAULT_CLIENT_STATS_KEEP", DEFAULT_CLIENT_STATS_KEEP
            ),
            # minimum=0 because 0 disables truncation entirely (leave rotation
            # to something else), which is a legitimate operational choice.
            event_log_max_bytes=_env_int(
                "VAULT_EVENT_LOG_MAX_BYTES", DEFAULT_EVENT_LOG_MAX_BYTES, minimum=0
            ),
            # WP 3.13. Blank/unset = the whole webhook feature stays off. Not
            # validated as a URL here beyond stripping: an operator-supplied
            # target is trusted by definition (see webhooks.py's SSRF/trust
            # posture note) and a malformed one fails per-delivery, at WARNING,
            # exactly like a receiver that is merely unreachable — never a
            # reason to refuse to boot.
            webhook_url=os.environ.get("VAULT_WEBHOOK_URL", "").strip(),
            webhook_events=_env_webhook_events(),
            webhook_timeout_seconds=_env_float(
                "VAULT_WEBHOOK_TIMEOUT_SECONDS", DEFAULT_WEBHOOK_TIMEOUT_SECONDS
            ),
            vault_name=_env_vault_name(),
            # WP 3.9. Unset/blank = no oracle, no outbound third-party request
            # — the default, and the reason the URL and timeout below are
            # harmless to have a default for.
            manifest_oracle=manifest_oracle,
            manifest_oracle_url=manifest_oracle_url,
            manifest_oracle_timeout=_env_float(
                "VAULT_MANIFEST_ORACLE_TIMEOUT", DEFAULT_MANIFEST_ORACLE_TIMEOUT
            ),
            # WP 4a.1. Blank/unset = the computed repo-relative default (see
            # `_default_web_dir`). Not validated as an existing path here —
            # same reasoning as `steamprefill_cache_dir` above: a wrong or
            # not-yet-populated value only means "no UI to serve", never a
            # reason to refuse to boot.
            web_dir=os.environ.get("VAULT_WEB_DIR", "").strip() or _default_web_dir(),
            # Settings-API work package. Env-only (see the field's own
            # docstring); False (read-write) unless explicitly turned on.
            settings_readonly=_env_bool("VAULT_SETTINGS_READONLY", False),
            # WP 4h.0 (ADR-0010). Env-only privacy gate for the Steam relay's
            # playtime_forever / rtime_last_played fields -- see this
            # module's own "WP 4h.0 (ADR-0010)" section above for why these
            # two have no DB-override counterpart at all. Off unless the
            # operator explicitly turns either on.
            relay_expose_playtime=_env_bool(
                "VAULT_RELAY_EXPOSE_PLAYTIME", DEFAULT_RELAY_EXPOSE_PLAYTIME
            ),
            relay_expose_last_played=_env_bool(
                "VAULT_RELAY_EXPOSE_LAST_PLAYED", DEFAULT_RELAY_EXPOSE_LAST_PLAYED
            ),
            # WP S-1 (ADR-0012). Unset/blank = 'subprocess', byte-identical to
            # every version of vault-api before this work package.
            prefill_mode=_env_prefill_mode(),
            runner_heartbeat_seconds=_env_float(
                "VAULT_RUNNER_HEARTBEAT_SECONDS", DEFAULT_RUNNER_HEARTBEAT_SECONDS
            ),
            runner_lease_timeout_seconds=_env_float(
                "VAULT_RUNNER_LEASE_TIMEOUT_SECONDS",
                DEFAULT_RUNNER_LEASE_TIMEOUT_SECONDS,
            ),
            runner_poll_seconds=_env_float(
                "VAULT_RUNNER_POLL_SECONDS", DEFAULT_RUNNER_POLL_SECONDS
            ),
            egress_allow=egress_allow,
        )
