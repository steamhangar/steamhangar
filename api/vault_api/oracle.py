"""Third-party manifest oracle — **opt-in, off by default, fail-soft**
(WP 3.9; ADR-0006 decision 4; ADR-0007 beta addendum **decision B**).

## What this is

Tier 1 of staleness detection (ADR-0006 decision 1) is a non-forced
SteamPrefill run: it answers "this app was current as of <timestamp>" but
only *while a job runs*, and each answer costs a Steam login. Between cron
ticks vault-api therefore knows nothing new. This module is the optional
Tier-2 answer: with ``VAULT_MANIFEST_ORACLE=steamcmd_api`` set, vault-api
asks a **third-party, unaffiliated** public mirror of Steam's PICS app info
(``api.steamcmd.net``) two questions per app:

1. which depots does this app have, and what is each depot's **current
   public manifest gid** — enough for a *pre-emptive* stale badge (compare
   against ``depot_manifests``) and for showing depot information about a
   game that has never been cached at all;
2. which **open (non-passworded) branches** exist, and what manifest gid does
   each of them currently point at — ADR-0007's decision B: those gids join
   the GC keep set, so a beta build a LAN client pulled through vault-core is
   not collected as an orphan the moment the grace window (decision A,
   WP 3.8b) expires.

## The three rules everything here obeys

**1. Off unless asked.** ``Settings.manifest_oracle`` defaults to ``""``.
Every read path in this module (``gc_keepset_gids``, ``refresh_app``) checks
that flag itself rather than trusting a caller to have checked; with the
oracle off they return "nothing", and the rest of vault-api behaves exactly
as it did before this work package existed.

**2. Fail-soft, always.** The oracle is a remote service outside this
project's control. Unreachable, slow, redirected, HTML instead of JSON,
JSON with the wrong shape, an app it has never heard of, hostile content —
every one of those means *no oracle data*, which means *behave as if the
oracle were off*. ``refresh_app`` never raises; it returns a result object
carrying the error. A failing oracle must never fail a job, block GC, or
take the API down.

**3. Oracle data never becomes vault-api's own record.** Nothing here writes
to ``depot_manifests``: that table is vault-api's first-hand knowledge of
manifests it has actually parsed (ADR-0006 decision 3), and a third-party
claim must never be indistinguishable from it. Oracle facts live in their own
two tables (``oracle_app_state``, ``oracle_branch_manifests``, schema v8),
each row additionally carrying a ``source`` provenance tag.

## The one safety invariant, stated as a promise

**Oracle data can only ADD protection, never remove it.** Its single effect
on the deletion path is that extra manifest gids join a depot's GC keep set
(``gc.resolve_depot_chunkset``'s ``extra_manifest_ids``), and a keep set can
only grow. Consequences, each of which is a test in ``tests/test_oracle.py``:

- with the oracle on, the planned orphan set is always a **subset** of what
  the same cache plans with the oracle off;
- a garbage, poisoned or missing oracle answer therefore cannot cause a
  deletion — the worst it can do is fail to prevent one, which is exactly the
  pre-oracle baseline (and the WP 3.8b grace window still covers that case);
- the readiness gate is never fed by the oracle: an open beta branch whose
  manifest vault-api cannot read does **not** block GC. Blocking would freeze
  every depot of every app that ever had a beta branch, permanently, which is
  the same leak ADR-0007's own addendum refused for uncached co-owners.

## Password-protected branches: dropped at the door

A passworded branch's manifest is encrypted and its chunks are uncoverable
(ADR-0007 addendum: the grace window is their only protection). Rather than
storing those gids with a "do not use" flag and relying on every future query
to remember the filter, this module **never stores them at all** — the
validator drops them and records a count. A gid that is not in the database
cannot leak into a keep set through a forgotten ``WHERE``.

Symmetrically, ``public`` **is** stored (the stale badge needs it) but is
excluded from the keep-set query: the public manifest already reaches the
keep set through vault-api's own ``depot_manifests`` record, and decision B
is about *beta* branches. Two independent, individually simple filters.

## Everything returned is validated before it is stored

The response is attacker-shaped input by definition: it arrives over the
network from a service this project does not run. ``docs/LEARNINGS.md``'s
"Parsers" rules are binding here because these ids feed SQL parameters and —
via the GC keep set — filesystem paths:

- app/depot ids: ``deletion.coerce_positive_id`` (strict ASCII digits, no
  ``" 4 "``/``"+4"``/``"1_0"``/non-ASCII digits, ``bool`` rejected);
- manifest gids: ``gc.valid_manifest_id`` — the *same* validator GC uses on
  the ``depot_manifests`` column, so an oracle gid and a recorded gid cannot
  drift apart in what they accept;
- branch names: ``valid_branch_name`` below (bounded ASCII, no path
  separators, no dot-only names);
- the raw body is bounded before it is decoded, the decoded JSON is bounded
  in depth-by-consequence (``RecursionError`` is caught explicitly and
  converted — WP 2.1's finding: a ``RecursionError`` escaping a parser's
  documented exception contract crashes the caller), and the number of
  depots/branches/rows is capped.

## Privacy — this is the one thing in SteamHangar that leaves the LAN

Every other component talks only to the LAN, to Steam's CDN through
vault-core, or to Valve through SteamPrefill. **With the oracle enabled,
vault-api makes outbound HTTPS requests to a third party** (default
``api.steamcmd.net``) carrying the Steam **app id** it is asking about — i.e.
which games this vault tracks, and roughly when. No API key, no client id, no
user identity and no Steam credentials are ever sent (ADR-0004: vault-api
never has any). This is why the feature is opt-in and off by default, and it
is spelled out in api/README.md's "Manifest oracle" section for the operator
who has to make that call.
"""

from __future__ import annotations

import json
import logging
import socket
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from vault_api import deletion, jobs
from vault_api.config import (
    MANIFEST_ORACLE_STEAMCMD_API,
    Settings,
)
from vault_api.gc import valid_manifest_id

logger = logging.getLogger(__name__)

#: ``source`` provenance tag written into every oracle row (rule 3 above).
#: Deliberately equal to the config value that selects the oracle: an operator
#: reading a row must be able to tell which oracle produced it, and today
#: there is exactly one.
SOURCE_STEAMCMD_API = MANIFEST_ORACLE_STEAMCMD_API

#: Steam's default branch. Stored (the stale badge compares against it) but
#: never part of the GC keep-set query — see the module docstring.
BRANCH_PUBLIC = "public"

#: Keys that appear *inside* an app's ``depots`` object without being depot
#: ids. ``parse_app_info`` skips these by name as a fast path, but the skip is
#: not *load-bearing*: the parser's real depot test is ``coerce_positive_id``
#: succeeding on the key, which already rejects every name here. This tuple is
#: therefore a documented shortcut for the known siblings, and an unknown
#: future one is skipped by the validator anyway rather than misparsed — see
#: ``test_sibling_keys_of_depots_are_not_mistaken_for_depots``, which includes
#: a key that is deliberately NOT in this tuple.
KNOWN_NON_DEPOT_KEYS = (
    "branches",
    "baselanguages",
    "hasdepotsindlc",
    "overridescddb",
    "privatebranches",
)

#: Hard ceiling on the response body this module will even look at. A real
#: app-info document for a very large title is a few hundred KiB; 4 MiB is
#: generous headroom while refusing a body designed to exhaust memory. Read
#: with one extra byte so "exactly at the limit" and "over it" are
#: distinguishable.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

#: Bounds on the parsed structure. Real apps top out around a hundred depots
#: and a handful of branches; these are large multiples of that, and exist so
#: a hostile document turns into a refusal rather than into a million SQL
#: parameters.
MAX_DEPOTS = 1024
MAX_BRANCHES = 256
MAX_BRANCH_MANIFEST_ROWS = 4096

#: Longest accepted branch name. Steam's own are short ("public", "beta",
#: "prerelease"); this bounds a TEXT column and a JSON response field.
MAX_BRANCH_NAME_LEN = 64

#: Characters a branch name may consist of. ASCII alphanumerics plus the three
#: separators Steam actually uses. Everything else — a slash, a backslash, a
#: NUL, a space, anything non-ASCII — makes the branch unusable, which costs
#: nothing but the extra protection that branch would have contributed.
_BRANCH_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)

#: How many parse warnings one refresh carries (and how many are logged). A
#: document full of junk must not turn one refresh into megabytes of log —
#: the same bound and reasoning as ``gc.MAX_REPORTED_NAMES``.
MAX_WARNINGS = 10

#: How long ``http_fetch`` will wait, if no timeout is configured. Also the
#: connect timeout: ``urlopen``'s ``timeout`` applies to socket operations.
DEFAULT_FETCH_TIMEOUT_SECONDS = 10.0

#: Sent so the operators of a free public service can see who is calling.
USER_AGENT = "SteamHangar-vault-api/0.1 (+https://github.com/, self-hosted)"

#: ``AppStaleness.verdict`` / ``DepotStaleness.verdict`` values.
VERDICT_CURRENT = "current"
VERDICT_STALE = "stale"
VERDICT_UNKNOWN = "unknown"
#: The oracle knows this depot but vault-api has never recorded a manifest for
#: it — "depot info for a never-cached game", not a staleness statement.
VERDICT_NOT_CACHED = "not_cached"


class OracleError(Exception):
    """Anything that made an oracle answer unusable.

    The one exception type ``http_fetch``/``parse_app_info`` are allowed to
    raise: no ``URLError``, ``socket.timeout``, ``JSONDecodeError``,
    ``UnicodeDecodeError``, ``RecursionError``, ``KeyError`` or ``TypeError``
    escapes them. ``refresh_app`` catches even this one — see rule 2.
    """


# --------------------------------------------------------------------------
# Validators (LEARNINGS "Parsers" — every value below feeds SQL, and gids
# additionally feed filesystem paths through the GC keep set)
# --------------------------------------------------------------------------


def valid_branch_name(value: object) -> str | None:
    """A usable Steam branch name, or ``None``.

    Bounded ASCII made of alphanumerics and ``._-`` only. ``"."`` and ``".."``
    are refused explicitly even though this value never becomes a path
    component today — a name that would be a path traversal if anyone ever
    joined it onto a directory is not one worth storing, and the check costs a
    comparison.

    Rejecting a name is **safe by construction**: an unstorable branch simply
    contributes no extra keep-set protection (module docstring's invariant).
    """
    if not isinstance(value, str):
        return None
    if not value or len(value) > MAX_BRANCH_NAME_LEN:
        return None
    if value in (".", ".."):
        return None
    if any(char not in _BRANCH_NAME_CHARS for char in value):
        return None
    return value


def _is_password_required(branch: Mapping[str, object]) -> bool:
    """Is this branch password protected?

    **The posture, stated exactly.** An *absent* ``pwdrequired`` key means
    "no" — PICS omits the field entirely for open branches, so treating its
    absence as "protected" would classify every ordinary branch, ``public``
    included, as private and make the feature do nothing at all. A *present*
    key is read strictly, and anything not recognisably "no" is "yes": PICS
    spells the flag as the string ``"1"``/``"0"``, but a mirror may render it
    as an int or a JSON boolean, and an unrecognised type (a list, an object,
    ``"yes"``) is a value this code cannot interpret. Erring towards
    "protected" there costs nothing but a branch's extra keep-set protection,
    while erring the other way would store a gid this project has no business
    storing: a passworded branch's manifest is encrypted and its chunks can
    never be covered anyway.

    So: absence is trusted, presence is parsed, unparseable presence is
    protected. Pinned case by case in
    ``tests/test_oracle.py::test_password_state_is_never_guessed_optimistically``.
    """
    raw = branch.get("pwdrequired")
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip() not in ("0", "")
    return True


def _extract_gid(value: object) -> str | None:
    """The manifest gid out of either shape ``manifests`` is written in.

    Observed/documented spellings of ``depots.<id>.manifests.<branch>``:

    - the modern object form ``{"gid": "123", "size": "...", "download": "..."}``;
    - the older bare-string form ``"123"``.

    Both are accepted; anything else (a number — Steam gids are u64 and JSON
    numbers would lose precision, a list, ``null``) is rejected rather than
    coerced. Validation is ``gc.valid_manifest_id``, the same function GC
    applies to its own ``depot_manifests`` column.
    """
    if isinstance(value, str):
        return valid_manifest_id(value)
    if isinstance(value, dict):
        gid = value.get("gid")
        return valid_manifest_id(gid) if isinstance(gid, str) else None
    return None


# --------------------------------------------------------------------------
# The parsed shape
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BranchManifest:
    """One (depot, branch) → manifest gid fact, as the oracle stated it."""

    depotid: int
    branch: str
    manifestid: str


@dataclass(frozen=True)
class OracleAppInfo:
    """One validated app-info answer. Nothing unvalidated survives this far."""

    appid: int
    #: ``branches.public.buildid`` if it was usable, else ``None``. Operator
    #: information only — nothing branches on it.
    buildid: str | None
    #: Every (depot, open branch) → gid pair, sorted, passworded branches
    #: already dropped.
    branch_manifests: tuple[BranchManifest, ...]
    #: Names of the open branches the answer declared, sorted.
    open_branches: tuple[str, ...]
    #: How many branches were dropped for being (or possibly being) passworded.
    skipped_password_branches: int
    #: Bounded human-readable notes about what was ignored and why.
    warnings: tuple[str, ...] = ()

    @property
    def depotids(self) -> tuple[int, ...]:
        return tuple(sorted({bm.depotid for bm in self.branch_manifests}))


@dataclass(frozen=True)
class OracleRefreshResult:
    """What one ``refresh_app`` call did. **Never an exception** — see rule 2."""

    appid: int
    #: Was the oracle enabled at all? ``False`` ⇒ nothing was fetched or
    #: written, and ``error`` is empty (that is not a failure).
    enabled: bool
    #: Did a usable answer reach the database?
    ok: bool
    error: str = ""
    checked_at: str | None = None
    depot_count: int = 0
    branch_manifest_count: int = 0
    open_branches: tuple[str, ...] = ()
    skipped_password_branches: int = 0
    warnings: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Fetching (bounded, no redirects, one exception type)
# --------------------------------------------------------------------------


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Turn any redirect into an error instead of following it.

    An operator points ``VAULT_MANIFEST_ORACLE_URL`` at a host they trust; a
    redirect is that host handing the request to a different one, which is
    precisely the decision the operator made and this code must not silently
    re-make. Refusing keeps the request's destination equal to the configured
    URL — and a redirect loop cannot burn the request timeout either.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def app_info_url(base_url: str, appid: int) -> str:
    """``<base>/<appid>``, with the base's trailing slashes normalised.

    ``appid`` is an ``int`` by the time it reaches here (the router's path
    type plus a ``> 0`` check), so no string interpolation of untrusted text
    happens: the only variable part of the URL is a positive integer.
    """
    if appid <= 0:
        raise OracleError(f"appid {appid!r} is not a positive integer")
    return f"{base_url.rstrip('/')}/{appid}"


def url_for_log(url: str) -> str:
    """``url`` as it may appear in a log line or an ``OracleError`` (N-1).

    WP SEC-FIX-4: the oracle base URL is operator-configured and may carry
    userinfo or a token query parameter. Userinfo becomes ``***@``; the query
    and fragment are dropped. Never raises.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparsable url>"
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[1]
    elif "@" in parts.path:
        # An unencoded '/' in a password pushes the userinfo into the path.
        return f"{parts.scheme}://***"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def http_fetch(url: str, *, timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS) -> bytes:
    """GET ``url`` and return at most ``MAX_RESPONSE_BYTES`` of body.

    Raises ``OracleError`` — and only ``OracleError`` — for a non-http(s)
    scheme, a DNS/connection/TLS failure, a timeout, a redirect, a non-200
    status, or a body over the size bound. Success is "200 with a bounded
    body"; interpreting it is ``parse_app_info``'s job.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise OracleError(
            # No URL or scheme echo: for a scheme-less "user:pw@host" urlsplit
            # reports the username as the scheme (the WP API-FIX-2 N1 trap).
            "refusing to fetch the configured oracle URL: only http/https "
            "URLs are allowed"
        )

    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is not None and status != 200:
                raise OracleError(f"{url_for_log(url)} answered HTTP {status}")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except OracleError:
        raise
    except urllib.error.HTTPError as exc:
        raise OracleError(f"{url_for_log(url)} answered HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OracleError(f"{url_for_log(url)} is unreachable: {exc.reason}") from exc
    except socket.timeout as exc:  # pragma: no cover - timing dependent
        raise OracleError(f"{url_for_log(url)} timed out after {timeout}s") from exc
    except (OSError, ValueError) as exc:
        raise OracleError(f"{url_for_log(url)} could not be fetched: {exc}") from exc

    if len(body) > MAX_RESPONSE_BYTES:
        raise OracleError(
            f"{url_for_log(url)} returned more than the "
            f"{MAX_RESPONSE_BYTES}-byte bound"
        )
    return body


#: What ``refresh_app`` calls to get bytes. Injected in tests with recorded
#: fixtures — **this suite never touches the network** (work package rule).
Fetcher = Callable[[str], bytes]


# --------------------------------------------------------------------------
# Parsing (every branch of it fail-soft)
# --------------------------------------------------------------------------


def _decode_json(payload: bytes) -> object:
    """Bytes → Python objects, with every failure as ``OracleError``.

    ``RecursionError`` is caught **explicitly**: CPython's JSON scanner
    recurses per nesting level, so a body of ten thousand ``[`` raises it, and
    ``docs/LEARNINGS.md`` (WP 2.1) records what happens when that escapes a
    parser's documented exception contract — the caller crashes on an
    exception it was never told to catch. Converting it here keeps
    "``parse_app_info`` raises ``OracleError`` or returns" true.
    """
    if len(payload) > MAX_RESPONSE_BYTES:
        raise OracleError(
            f"response of {len(payload)} bytes exceeds the "
            f"{MAX_RESPONSE_BYTES}-byte bound"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleError(f"response is not valid UTF-8: {exc}") from exc
    try:
        return json.loads(text)
    except RecursionError as exc:
        raise OracleError(
            "response nests too deeply to parse (rejected before it could "
            "exhaust the stack)"
        ) from exc
    except ValueError as exc:
        raise OracleError(f"response is not valid JSON: {exc}") from exc


def _app_object(appid: int, document: object) -> Mapping[str, object]:
    """Dig ``data.<appid>`` out of the envelope, cross-checking the id."""
    if not isinstance(document, dict):
        raise OracleError("response is not a JSON object")

    status = document.get("status")
    if isinstance(status, str) and status != "success":
        raise OracleError(f"oracle reported status {status!r}")

    data = document.get("data")
    if not isinstance(data, dict):
        raise OracleError("response has no 'data' object")

    app = data.get(str(appid))
    if app is None:
        raise OracleError(f"response carries no data for app {appid}")
    if not isinstance(app, dict):
        raise OracleError(f"data for app {appid} is not an object")

    # Same corruption cross-check the manifest parsers apply to filenames vs.
    # payloads: the answer must be about the app we asked for. A mismatch here
    # means the response is not what it claims to be, and attributing another
    # app's depots to this one is exactly how a keep set ends up wrong.
    claimed = app.get("appid")
    if claimed is not None and deletion.coerce_positive_id(claimed) != appid:
        raise OracleError(
            f"response claims appid {claimed!r} but was requested for {appid}"
        )
    return app


def _parse_branches(
    branches: object, warnings: list[str]
) -> tuple[dict[str, bool], str | None]:
    """``{branch name: is_open}`` plus the public branch's build id.

    A missing or malformed ``branches`` object yields **no open branches at
    all** — not "assume they are all open". Password state is the one thing
    this module refuses to guess (see ``_is_password_required``), and with the
    branch list unreadable it knows nothing about any of them.
    """
    if not isinstance(branches, dict):
        _warn(warnings, "no usable 'branches' object: no branch is treated as open")
        return {}, None

    open_state: dict[str, bool] = {}
    buildid: str | None = None
    for index, (raw_name, raw_branch) in enumerate(branches.items()):
        if index >= MAX_BRANCHES:
            _warn(warnings, f"more than {MAX_BRANCHES} branches: the rest were ignored")
            break
        name = valid_branch_name(raw_name)
        if name is None:
            _warn(warnings, f"ignored unusable branch name {raw_name!r}")
            continue
        if not isinstance(raw_branch, dict):
            _warn(warnings, f"ignored branch {name!r}: not an object")
            continue
        open_state[name] = not _is_password_required(raw_branch)
        if name == BRANCH_PUBLIC:
            raw_build = raw_branch.get("buildid")
            if isinstance(raw_build, (str, int)) and not isinstance(raw_build, bool):
                coerced = deletion.coerce_positive_id(raw_build)
                buildid = None if coerced is None else str(coerced)
    return open_state, buildid


def _warn(warnings: list[str], message: str) -> None:
    """Append a warning, bounded — a junk document must not become a log flood."""
    if len(warnings) < MAX_WARNINGS:
        warnings.append(message)
    elif len(warnings) == MAX_WARNINGS:
        warnings.append("... further warnings suppressed")


def parse_app_info(appid: int, payload: bytes) -> OracleAppInfo:
    """Validate one raw app-info response into an ``OracleAppInfo``.

    Raises ``OracleError`` when the document is unusable as a whole (not
    JSON, wrong app, no ``data``). Anything *locally* wrong — one unreadable
    depot key, one branch without a usable gid, an unknown manifest spelling —
    is skipped with a bounded warning rather than discarding the whole answer:
    a partially-readable answer still protects the depots it did describe, and
    the ones it did not are simply back at the pre-oracle baseline.
    """
    document = _decode_json(payload)
    app = _app_object(appid, document)

    warnings: list[str] = []
    depots = app.get("depots")
    if not isinstance(depots, dict):
        _warn(warnings, "no usable 'depots' object: nothing was recorded")
        return OracleAppInfo(
            appid=appid,
            buildid=None,
            branch_manifests=(),
            open_branches=(),
            skipped_password_branches=0,
            warnings=tuple(warnings),
        )

    open_state, buildid = _parse_branches(depots.get("branches"), warnings)
    skipped_password = sum(1 for is_open in open_state.values() if not is_open)

    found: list[BranchManifest] = []
    depot_count = 0
    for raw_depotid, raw_depot in depots.items():
        if raw_depotid in KNOWN_NON_DEPOT_KEYS:
            continue
        depotid = deletion.coerce_positive_id(raw_depotid)
        if depotid is None:
            # Not a depot id at all: either one of the sibling keys above or a
            # future one. Skipping by "the id does not validate" rather than by
            # a name list means a new sibling key needs no code change.
            continue
        depot_count += 1
        if depot_count > MAX_DEPOTS:
            _warn(warnings, f"more than {MAX_DEPOTS} depots: the rest were ignored")
            break
        if not isinstance(raw_depot, dict):
            _warn(warnings, f"ignored depot {depotid}: not an object")
            continue
        manifests = raw_depot.get("manifests")
        if not isinstance(manifests, dict):
            # Perfectly normal: DLC-only and shared-install depots carry no
            # manifests of their own. Not a warning.
            continue

        for raw_branch, raw_value in manifests.items():
            branch = valid_branch_name(raw_branch)
            if branch is None:
                _warn(warnings, f"ignored unusable branch name {raw_branch!r}")
                continue
            if not open_state.get(branch, False):
                # Either passworded, or a branch the 'branches' object never
                # declared — both are "password state unknown or known-bad",
                # and neither gets stored. Counted, not warned: a private
                # branch is normal, not a document defect.
                continue
            gid = _extract_gid(raw_value)
            if gid is None:
                _warn(
                    warnings,
                    f"ignored depot {depotid} branch {branch!r}: no usable manifest gid",
                )
                continue
            if len(found) >= MAX_BRANCH_MANIFEST_ROWS:
                _warn(
                    warnings,
                    f"more than {MAX_BRANCH_MANIFEST_ROWS} (depot, branch) rows: "
                    "the rest were ignored",
                )
                break
            found.append(
                BranchManifest(depotid=depotid, branch=branch, manifestid=gid)
            )

    found.sort(key=lambda bm: (bm.depotid, bm.branch))
    return OracleAppInfo(
        appid=appid,
        buildid=buildid,
        branch_manifests=tuple(found),
        open_branches=tuple(sorted(n for n, is_open in open_state.items() if is_open)),
        skipped_password_branches=skipped_password,
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------
# Storage (its own tables, provenance-tagged, snapshot semantics)
# --------------------------------------------------------------------------


def store_app_info(
    conn: sqlite3.Connection,
    info: OracleAppInfo,
    *,
    checked_at: str,
    source: str = SOURCE_STEAMCMD_API,
) -> None:
    """Replace everything stored for this app with ``info``, atomically.

    **Snapshot semantics, not upsert semantics.** An app-info answer is a
    complete statement about the app right now, so a branch that has been
    deleted upstream, or a depot that lost its beta manifest, must *disappear*
    from the keep set rather than linger as a row nothing ever refreshes. The
    delete and the inserts run inside one ``BEGIN IMMEDIATE`` (the
    check-then-act rule from ``docs/LEARNINGS.md``) so a concurrent GC read
    sees either the whole old snapshot or the whole new one, never a depot
    whose rows have been deleted but not yet rewritten.
    """
    with jobs.immediate_transaction(conn):
        conn.execute("DELETE FROM oracle_branch_manifests WHERE appid = ?", (info.appid,))
        conn.executemany(
            """
            INSERT INTO oracle_branch_manifests
                (appid, depotid, branch, manifestid, recorded_at, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (info.appid, bm.depotid, bm.branch, bm.manifestid, checked_at, source)
                for bm in info.branch_manifests
            ],
        )
        conn.execute(
            """
            INSERT INTO oracle_app_state
                (appid, buildid, checked_at, source, depot_count, branch_count)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (appid) DO UPDATE SET
                buildid      = excluded.buildid,
                checked_at   = excluded.checked_at,
                source       = excluded.source,
                depot_count  = excluded.depot_count,
                branch_count = excluded.branch_count
            """,
            (
                info.appid,
                info.buildid,
                checked_at,
                source,
                len(info.depotids),
                len(info.open_branches),
            ),
        )


def refresh_app(
    conn: sqlite3.Connection,
    appid: int,
    *,
    settings: Settings,
    fetch: Fetcher | None = None,
) -> OracleRefreshResult:
    """Fetch, validate and store one app's oracle data. **Never raises.**

    Returns ``enabled=False`` (and touches nothing) when the oracle is off —
    that is a normal answer, not an error. Any other failure comes back as
    ``ok=False`` with a message; the previously stored snapshot is left alone,
    because a stale-but-validated snapshot is strictly better than none (it
    can only add keep-set protection, and its ``checked_at`` says how old it
    is).
    """
    if not settings.manifest_oracle_enabled:
        return OracleRefreshResult(appid=appid, enabled=False, ok=False)

    fetcher = fetch if fetch is not None else _configured_fetcher(settings)

    try:
        url = app_info_url(settings.manifest_oracle_url, appid)
        payload = fetcher(url)
        info = parse_app_info(appid, payload)
    except OracleError as exc:
        logger.warning("manifest oracle: refresh for app %s failed: %s", appid, exc)
        return OracleRefreshResult(appid=appid, enabled=True, ok=False, error=str(exc))
    except Exception as exc:
        # A bug in this module, or an exception type a future fetcher raises,
        # must not propagate into a request handler or the job worker (rule 2).
        # Reachable and covered:
        # tests/test_oracle.py::test_a_fetcher_that_raises_something_unexpected_is_still_contained.
        logger.exception("manifest oracle: refresh for app %s crashed", appid)
        return OracleRefreshResult(
            appid=appid, enabled=True, ok=False, error=f"internal error: {exc}"
        )

    checked_at = jobs.utcnow_iso()
    try:
        store_app_info(conn, info, checked_at=checked_at)
    except sqlite3.Error as exc:
        logger.warning("manifest oracle: could not store app %s: %s", appid, exc)
        return OracleRefreshResult(
            appid=appid, enabled=True, ok=False, error=f"database error: {exc}"
        )

    for warning in info.warnings:
        logger.info("manifest oracle: app %s: %s", appid, warning)
    logger.info(
        "manifest oracle: app %s refreshed — %d depot(s), %d open-branch manifest(s) "
        "across %s, %d passworded branch(es) skipped",
        appid, len(info.depotids), len(info.branch_manifests),
        list(info.open_branches), info.skipped_password_branches,
    )
    return OracleRefreshResult(
        appid=appid,
        enabled=True,
        ok=True,
        checked_at=checked_at,
        depot_count=len(info.depotids),
        branch_manifest_count=len(info.branch_manifests),
        open_branches=info.open_branches,
        skipped_password_branches=info.skipped_password_branches,
        warnings=info.warnings,
    )


def _configured_fetcher(settings: Settings) -> Fetcher:
    timeout = settings.manifest_oracle_timeout

    def fetch(url: str) -> bytes:
        logger.info(
            "manifest oracle: querying %s (this request LEAVES the LAN — see "
            "api/README.md 'Manifest oracle')",
            url_for_log(url),
        )
        return http_fetch(url, timeout=timeout)

    return fetch


# --------------------------------------------------------------------------
# Reads: the GC keep-set contribution (ADR-0007 decision B)
# --------------------------------------------------------------------------


def gc_keepset_gids(
    conn: sqlite3.Connection, appid: int, *, settings: Settings
) -> dict[int, list[str]]:
    """``{depotid: [extra manifest gids]}`` for one GC run, or ``{}``.

    Empty — meaning "GC behaves exactly as it did before WP 3.9" — whenever
    the oracle is off, nothing has been stored, or the query fails. This
    function is where decision B enters the deletion path, and it is the ONLY
    place it does.

    Scope of the query mirrors ``gc.load_recorded_manifests``: every depot
    mapped to this app, and every oracle row naming such a depot **regardless
    of which app recorded it** — a shared depot's beta chunks belong to the
    depot, not to the app that happened to be refreshed. Same subquery shape
    as the recorded-manifest loader, so the two cannot disagree about which
    depots are in scope.

    ``BRANCH_PUBLIC`` is excluded: the current public manifest already reaches
    the keep set through vault-api's own ``depot_manifests`` record, and
    decision B is specifically about beta branches. Rows are re-validated on
    the way out even though only validated values are ever written — the
    database is a file an operator can edit, and this value ends up in a
    filename.
    """
    if not settings.manifest_oracle_enabled:
        return {}

    try:
        rows = conn.execute(
            """
            SELECT depotid, manifestid FROM oracle_branch_manifests
            WHERE branch <> ?
              AND depotid IN (SELECT depotid FROM depot_app_map WHERE appid = ?)
            """,
            (BRANCH_PUBLIC, appid),
        ).fetchall()
    except Exception as exc:
        # Deliberately ``Exception``, not ``sqlite3.Error``. This call is made
        # from inside ``gc_execute.run_gc_job``'s try block, where anything
        # that escapes ends the GC job as 'error' — so a non-sqlite failure
        # here (a bug in this module, a future caller passing something odd)
        # would turn "the optional oracle had a problem" into "garbage
        # collection failed", which is exactly the coupling rule 2 exists to
        # prevent. Returning {} degrades to the pre-WP-3.9 behaviour.
        logger.warning(
            "manifest oracle: keep-set query for app %s failed (%s); GC proceeds "
            "without oracle protection",
            appid, exc,
        )
        return {}

    gids: dict[int, list[str]] = {}
    for row in rows:
        depotid = deletion.coerce_positive_id(row["depotid"])
        manifestid = valid_manifest_id(row["manifestid"])
        if depotid is None or manifestid is None:
            continue
        bucket = gids.setdefault(depotid, [])
        if manifestid not in bucket:
            bucket.append(manifestid)

    for bucket in gids.values():
        bucket.sort()
    return gids


# --------------------------------------------------------------------------
# Reads: the pre-emptive stale badge and never-cached depot info
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DepotStaleness:
    """One depot's oracle-vs-record comparison."""

    depotid: int
    #: What ``depot_manifests`` says vault-api last parsed for this depot.
    recorded_manifestid: str | None
    #: What the oracle says the current ``public`` manifest is.
    oracle_manifestid: str | None
    verdict: str
    #: Open branches the oracle knows for this depot, other than ``public``.
    beta_branches: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppOracleView:
    """Everything the oracle currently says about one app.

    ``verdict`` is the pre-emptive badge (ADR-0006 decision 4): ``stale`` if
    ANY depot's recorded manifest differs from the oracle's current public
    one, ``current`` if at least one depot compared equal and none differed,
    otherwise ``unknown``. Deliberately **not** derived from a depot the
    oracle knows nothing about — an unknown depot must not turn a real "stale"
    into a comfortable "current", and must not invent a "stale" either.
    """

    appid: int
    enabled: bool
    checked_at: str | None
    buildid: str | None
    verdict: str
    depots: tuple[DepotStaleness, ...] = ()
    source: str | None = None

    @property
    def known(self) -> bool:
        """Is there a stored snapshot for this app at all?"""
        return self.checked_at is not None


def app_view(
    conn: sqlite3.Connection, appid: int, *, settings: Settings
) -> AppOracleView:
    """The stored oracle view of one app — read-only, no network, never raises
    for missing data.

    Answers with ``enabled=False`` and an empty view when the oracle is off,
    so a caller renders "not configured" rather than "nothing is stale".
    """
    if not settings.manifest_oracle_enabled:
        return AppOracleView(
            appid=appid,
            enabled=False,
            checked_at=None,
            buildid=None,
            verdict=VERDICT_UNKNOWN,
        )

    state = conn.execute(
        "SELECT buildid, checked_at, source FROM oracle_app_state WHERE appid = ?",
        (appid,),
    ).fetchone()
    rows = conn.execute(
        """
        SELECT depotid, branch, manifestid FROM oracle_branch_manifests
        WHERE appid = ? ORDER BY depotid, branch
        """,
        (appid,),
    ).fetchall()
    recorded_rows = conn.execute(
        "SELECT depotid, manifestid FROM depot_manifests WHERE appid = ?",
        (appid,),
    ).fetchall()

    recorded: dict[int, str | None] = {}
    for row in recorded_rows:
        depotid = deletion.coerce_positive_id(row["depotid"])
        if depotid is not None:
            recorded[depotid] = valid_manifest_id(row["manifestid"])

    public: dict[int, str] = {}
    betas: dict[int, list[str]] = {}
    for row in rows:
        depotid = deletion.coerce_positive_id(row["depotid"])
        manifestid = valid_manifest_id(row["manifestid"])
        branch = valid_branch_name(row["branch"])
        if depotid is None or manifestid is None or branch is None:
            continue
        if branch == BRANCH_PUBLIC:
            public[depotid] = manifestid
        else:
            betas.setdefault(depotid, []).append(branch)

    depots: list[DepotStaleness] = []
    for depotid in sorted(set(public) | set(betas)):
        oracle_gid = public.get(depotid)
        recorded_gid = recorded.get(depotid)
        if oracle_gid is None:
            verdict = VERDICT_UNKNOWN
        elif depotid not in recorded:
            verdict = VERDICT_NOT_CACHED
        elif recorded_gid is None:
            verdict = VERDICT_UNKNOWN
        elif recorded_gid == oracle_gid:
            verdict = VERDICT_CURRENT
        else:
            verdict = VERDICT_STALE
        depots.append(
            DepotStaleness(
                depotid=depotid,
                recorded_manifestid=recorded_gid,
                oracle_manifestid=oracle_gid,
                verdict=verdict,
                beta_branches=tuple(sorted(betas.get(depotid, ()))),
            )
        )

    return AppOracleView(
        appid=appid,
        enabled=True,
        checked_at=None if state is None else state["checked_at"],
        buildid=None if state is None else state["buildid"],
        verdict=app_verdict(depots),
        depots=tuple(depots),
        source=None if state is None else state["source"],
    )


def app_verdict(depots: Sequence[DepotStaleness]) -> str:
    """Roll per-depot verdicts up into the app's badge — see ``AppOracleView``."""
    if any(depot.verdict == VERDICT_STALE for depot in depots):
        return VERDICT_STALE
    if any(depot.verdict == VERDICT_CURRENT for depot in depots):
        return VERDICT_CURRENT
    return VERDICT_UNKNOWN


def clear_app(conn: sqlite3.Connection, appid: int) -> None:
    """Forget everything the oracle said about one app (operator escape hatch).

    Used by ``DELETE /v1/oracle/{appid}``. Not wired into any automatic path:
    oracle rows are cheap, and dropping them silently would remove keep-set
    protection — the one direction this module is careful never to take on its
    own.
    """
    with jobs.immediate_transaction(conn):
        conn.execute("DELETE FROM oracle_branch_manifests WHERE appid = ?", (appid,))
        conn.execute("DELETE FROM oracle_app_state WHERE appid = ?", (appid,))
