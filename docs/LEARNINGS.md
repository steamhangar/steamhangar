# Engineering learnings (living document)

Standing, project-proven findings. **Every coder and reviewer reads this
before their first task of a session.** The orchestrator appends distilled
findings after each review cycle — one line each, with the WP that proved it.
These are not style preferences; each entry cost a review round to learn.

## nginx / vault-core
- `$upstream_status` becomes a comma list ("502, 200") when
  `proxy_next_upstream` retries — any map/guard keyed on it must handle the
  list form (WP 1.1 blocker).
- Unguarded `proxy_store` stores ONLY status-200 bodies on this nginx line —
  404/206/301/302 are not stored; don't claim otherwise, and keep explicit
  guards anyway for retry-list handling (WP 1.1, reviewer claim corrected by
  empirical rig).
- `proxy_pass` with a variable resolves at request time via the configured
  `resolver`; without a variable it resolves once at startup via the OS
  (hosts-file-poisonable) path (WP 0.1).
- Strip `Range`, `Accept-Encoding`, `If-Range` upstream on the store path —
  gzip bodies get stored raw and served corrupt otherwise (WP 1.1).
- nginx official image envsubst: always set `NGINX_ENVSUBST_FILTER` — but know
  why. It is a no-op today (the entrypoint's allowlist is built from env vars
  that EXIST, none named like an nginx variable, so filtered and unfiltered
  renders are byte-identical). It guards a future lowercase env var named
  `host`/`uri`, which envsubst would substitute INTO the config as its value —
  not blank it — leaving `nginx -t` green and the cache quietly wrong (WP 1.9).
- Temp paths must live OUTSIDE the document root but on the SAME filesystem
  as the cache (atomic rename) (WP 1.1/1.9).
- `access_log` inheritance is REPLACE, not merge: declaring a second log in
  a location silently kills the inherited one — restate every log per
  location (WP 3.10).
- `$upstream_cache_status` is literally `-` under proxy_store on both HIT
  and MISS (measured) — cache-status truth must come from structural
  location markers (`set` in the serving location), never from
  proxy_cache-only or `$upstream_status` variables (WP 3.10).
- PCRE `.` stops at newlines — regexes bounding DECODED URIs need `(?s)`
  or an embedded %0A silently truncates the match (WP 3.10).
- A guard grepping a log_format NAME also matches the `log_format`
  declaration and every `map` named after it — anchor config guards to the
  DIRECTIVE (`^\s*access_log\s.*name`). WP 3.10 shipped a fail-closed hook
  that failed 100% of the time in the default (event-log-off) deployment;
  container hooks without a container-level test stayed unexecuted until
  WP 5.1's first CI gate exposed it (WP 5.1 blocker).

## SQLite / FastAPI / vault-api
- Connections must be created, used, and closed in ONE thread. A generator
  dependency holding a connection segfaults (native access violation) when
  request cancellation unwinds AsyncExitStack on another thread —
  `check_same_thread=False` converts the loud error into a use-after-free
  (WP 1.4 fixing WP 1.3's review advice).
- Every check-then-act needs `BEGIN IMMEDIATE` (job claim/dedupe, report
  chains) (WP 1.4/2.4).
- Order report/diff chains by rowid, not second-precision timestamps
  (WP 2.4). WAL + busy_timeout on every connection (WP 1.2).
- FastAPI ships /docs, /redoc, /openapi.json world-readable — disable via
  `openapi_url=None`; auth belongs on the APIRouter, not per route; compare
  API keys with `hmac.compare_digest` on BYTES (non-ASCII str raises)
  (WP 1.2/1.3).

## Windows filesystem semantics
- `shutil.rmtree` deletes part of the tree BEFORE raising — "every attempt
  failed" never implies "nothing changed" (WP 1.6 blocker).
- Deleted-while-handle-open files report PermissionError (delete pending),
  not FileNotFoundError; racing removals need settle-and-recheck with
  `lexists` as the last word (WP 1.6 Fable blocker).
- Junction detection: `os.path.islink()` is False for junctions,
  `DirEntry.is_dir(follow_symlinks=False)` is True — use an
  islink-OR-isjunction helper; rmtree refuses links (target survives) but
  removal code must unlink links itself, never traverse (WP 1.6).
- `os.path.lexists` swallows EVERY `OSError` into False ("gone") — a
  protective branch keyed on it collapses "unreadable" into "deletable".
  Decide on the error TYPE: only `FileNotFoundError` means gone
  (WP 3.8b).
- Attribute Windows errors per SYSCALL: a delete-pending name answers
  `lstat` with `FileNotFoundError [WinError 2]` but `unlink` with
  `PermissionError [WinError 5]` — WP 1.6's finding is about unlink only;
  measured with a FILE_SHARE_DELETE handle rig (WP 3.8b).
- A path-shape guard measured only on the dev OS is not a guard:
  `ntpath.basename` splits on `\` (and strips `X:` drives), `posixpath`
  does neither — the Windows measurement hid a POSIX hole (`a\b`, `C:x`
  accepted as direct children) for two work packages until CI ran pytest
  on the production OS for the first time. Reject separator-ish
  characters (`\`, `:`) by LITERAL check, never via the host's `os.path`;
  pin each rejection arm with its own mutation-tested case (WP 3.8 →
  caught by WP 5.1 CI run #1).

- **Never round-trip a file through Python text mode on Windows.**
  `read_text()` + `write_text()` rewrites EVERY line ending to CRLF, which
  bites three different ways — all three hit in one session: (a) `dash`
  refuses to parse a shell script with CRLF, so a patched `verify-stack.sh`
  died with `Syntax error: word unexpected`; (b) CRLF silently defeated a
  brand-new `verify-stack.sh` guard whose regex ended in `$` — the guard
  asserts ABSENCE, so it passed for the wrong reason on every run (P1
  review); (c) it corrupts embedded `
` literals elsewhere in the file
  (WP 4e.7, caught only by `git diff --stat` showing an unexpected hunk).
  Read and write **bytes**, or `open(..., newline="")`, and remember that
  `read_text()` also normalises on the way IN — a search string written with
  `
` will not match a CRLF file, which is a separate failure that looks
  like "anchor not found" (WP 4f). After any scripted edit on Windows, check
  `git diff --stat` for hunks you did not intend and `git ls-files --eol`
  for the file's declared ending.

## PowerShell 5.1 (dev/test harnesses)
- `2>&1` on native commands wraps stderr lines in NativeCommandError and
  kills the script under `$ErrorActionPreference=Stop` (WP 1.8/0.6) — and
  so does `2>$null` (measured, WP 3.10): ANY stderr redirection triggers
  it; the only fix around stderr-writing natives like `nginx -t` is a
  locally restored `$ErrorActionPreference = "Continue"`.
- `return $arrayVar` re-flattens a single-element array even when the
  helper wrapped it — the CALL SITE needs `@(...)` too (WP 3.10).
- `"\n"` in double quotes is a literal backslash-n; locale decimal commas
  corrupt reports — force InvariantCulture (WP 0.3).
- `$null` numeric comparisons pass silently (`$x -ge $null` is true) — gate
  assertions on preconditions or they become false positives (WP 1.7).
- `$ErrorActionPreference = "Stop"` makes every later `Write-Error`
  terminating: `exit 2` lines after it are unreachable and the script
  reports exit 1. Set Stop only after input validation (WP 2.6, measured
  on all four usage paths).
- `Set-Acl` on an already-protected ACL fails with SeSecurityPrivilege for
  non-admin accounts on the SECOND call — use `icacls /inheritance:r
  /grant:r` (idempotent, measured 3x); Task Scheduler XML rejects
  `[TimeSpan]::MaxValue` repetition durations; em dashes in BOM-less UTF-8
  break the PS 5.1 parser under the system codepage — packaging scripts
  are pure ASCII (WP 2.6).

## CI / GitHub Actions
- The stock nginx image entrypoint soft-fails: `20-envsubst-on-templates.sh`
  returns 0 WITHOUT rendering when the template dir is missing or the output
  dir is unwritable, and the image ships its own nginx.conf — a gate driving
  the real entrypoint must assert the rendered config is actually ours
  (grep a repo-owned token like the log_format name) or it green-lights the
  stock config (WP 5.1 reviewer catch).
- Values hand-copied from a Dockerfile into CI (image ref, ENV wiring) are
  drift surfaces — derive them (`sed -n 's/^FROM[[:space:]]\{1,\}//p'`) or
  grep-assert they still match before use, and require `@sha256:` (WP 5.1).
- PSScriptAnalyzer's PSUseCompatibleSyntax checks the AST against stored
  version profiles — it does not need to run UNDER 5.1; only the raw
  `Parser::ParseFile` check does. Split: parser under `shell: powershell`,
  analyzer under `pwsh` (5.1 hosts may not see the runner's preinstalled
  module path) (WP 5.1).

## dnsmasq / vault-dns
- `address=/zone/ip` alone FORWARDS AAAA upstream on modern dnsmasq —
  pair with `local=/zone/` or IPv6 silently bypasses the cache (WP 0.6).
- Pi-hole v6 ignores /etc/dnsmasq.d by default — instructions must target
  `misc.dnsmasq_lines` (WP 1.8).

## Steam ecosystem facts
- SteamPrefill v3.7.1 has no app-id CLI — selection via
  Config/selectedAppsToPrefill.json; exits 0 with "Prefilled 0 apps" for
  unowned apps (job-outcome trap, Phase 3 item); requires the
  /lancache-heartbeat + X-LanCache-Processed-By contract; sends
  ?nocache=1 + Range bytes=0-0 speed probes (WP 0.4/1.4/1.7).
- Real Steam clients (Windows AND current Linux) send ZERO Range headers
  and use lancache discovery; manifest URLs carry per-request codes (no URL
  dedupe); Steam LAN P2P transfers can legitimately replace cache traffic
  (WP 0.3/0.6).

- SteamPrefill's cache discovery probes **four** candidates in order —
  `lancache.steamcontent.com`, `localhost`, the Docker gateway
  `172.17.0.1`, then the local hostname — and accepts one only if it is a
  private/loopback **IPv4** AND answers `/lancache-heartbeat` with
  `X-LanCache-Processed-By` (`poc/steamprefill/PROTOCOL.md`, source-read).
  Two consequences that invert each other: with vault-core on the default
  `0.0.0.0` bind, the **gateway** candidate carries detection inside a
  container with no DNS involved — so a DNS lookup is the wrong
  diagnostic and reports a bypass that isn't happening; but with core
  bound to a dedicated address (the port-80-conflict/NAS recipe), gateway
  and loopback both refuse and detection hangs entirely on candidate 1,
  i.e. on container DNS. Probe the heartbeat, never the name. Pinning that
  ONE hostname is sufficient: after detection SteamPrefill talks to the
  resolved IP for all depot traffic (WP 0.4: 1272 chunks through a single
  hosts entry), so depot hostnames need no rewrite — and the pin value
  must be a private IPv4 or it is rejected before any probe
  (P1 review, measured in both layouts).

## Parsers / input handling
- Recursive-descent parsers need an explicit depth limit raising the
  module's own error type — RecursionError escapes the documented catch
  contract and crashes the caller (WP 2.1 blocker).
- Python `int()` accepts " 4 ", "+4", "1_0" (=10) and non-ASCII digits —
  any value that later feeds Go, SQL, or a filesystem path needs
  strip + isascii + isdigit validation (WP 1.6/2.1).
- Pydantic lax mode coerces `true`→1 on int fields — reject bools
  explicitly on any id field (WP 2.4).
- `re`'s `\d` matches Unicode digits unless `re.ASCII` is passed — strict
  numeric env/config grammars are safer as `isascii()+isdigit()` on the
  partitioned string; and a long digit string is a VALID decimal literal
  that `float()` rounds to `inf`, so check `math.isfinite` after
  conversion (WP 3.12).
- One-shot request flags (stop/cancel columns) must be cleared at EVERY
  terminal or parking transition (finish, park, resume) — a stale flag
  re-fires on the next run of the same row (WP 3.12).
- Cursor-based tailers: "no newline in the batch" conflates a partial
  tail (wait) with an oversized line (skip past the next newline, loudly)
  — a full-sized batch can never become a valid line, and treating it as
  a tail stalls the sweep silently forever. Never advance a cursor past
  unterminated bytes (half-written lines would parse as fresh records),
  and never let a progress log line fire when nothing was consumed
  (WP 3.11).
- `urllib.request` does not handle userinfo in URLs (`https://u:p@host/`
  fails getaddrinfo) — strip it and send a real Basic Authorization
  header; redact userinfo in every log line, and test the redaction
  against `@` inside passwords and IPv6 hosts (WP 3.13).
- Transition detectors: persist state changes in BOTH directions
  regardless of which notifications are enabled — filtering belongs at
  delivery, or enabling an event later fires falsely on first sight
  (WP 3.13).
- envsubst on config templates: a colliding env var REPLACES runtime
  variables with its value (not blanks) and `nginx -t` still passes —
  always restrict with an allowlist filter (WP 1.9).
- A consumer that renames a producer's files breaks filename-contract
  parsers: manifest_archive stores `{depotid}_{manifestid}.bin` while the
  parser demanded SteamPrefill's 4-field name — keep payload parsing and
  filename-contract validation separable, with the caller supplying the
  expected ids so the corruption cross-check survives (WP 3.7).

- `ntpath.join` has two surprising drive cases: `join(parent, "C:x")`
  silently drops the drive (yields `parent\x`) and `join(parent, "a:b")`
  discards the parent entirely — strict-child path guards must compare
  `dirname`/`basename` equality on the joined result, never prefixes
  (WP 3.8, measured).
- CPython's `json` scanner recurses per nesting level — a bounded response
  body is NOT a bounded parse; catch `RecursionError` by name around
  `json.loads` on untrusted input and convert it to the module's own error
  type (WP 3.9, same failure class as the WP 2.1 blocker).
- `urllib` follows redirects by default — an operator-configured outbound
  URL needs an explicit no-redirect opener, or the host actually contacted
  is not the one configured (WP 3.9).

## Containers
- A non-root container user needs a real, writable HOME — tools that
  write caches (SteamPrefill: $HOME/.cache) crash on /nonexistent before
  printing anything, so error-pattern matching never fires. Set both the
  passwd home-dir AND ENV HOME; compose run vs docker exec derive HOME
  differently (WP 1.9 Fable blocker).
- "Verified by inspection" is not verified: every binary that ships in an
  image needs at least a credential-free smoke RUN in the verify suite
  (WP 1.9).
- A `.env` beside `compose.yaml` only feeds `${...}` interpolation inside
  that file — it is NOT a pass-through into container environments. Any
  setting config.py reads is dead unless the service's `environment:`
  block forwards it by name; WP 5.4 found four such dead vars
  (VAULT_SCHEDULE_WINDOW, VAULT_SCHEDULE_INTERVAL_MINUTES,
  VAULT_SCHEDULE_CLIENT_STALE_DAYS, VAULT_GC_GRACE_DAYS) and documented a
  compose.override.yaml recipe instead of a silently-broken .env example.
- Third occurrence of the above, so treat it as a rule, not a war story:
  **a doc sentence telling an operator to set a variable IS a claim that
  the shipped stack forwards it, and must be verified in the same diff.**
  The P1 packaging review caught a privacy mitigation ("point
  VAULT_MANIFEST_ORACLE_URL at your own mirror instead") whose key
  compose never forwarded and which is not DB-overridable either — an
  operator following our own advice still shipped their cached app IDs to
  api.steamcmd.net, with no error. The cheap systematic guard: when a
  package touches env plumbing, produce the FULL table of variables
  config.py reads against what compose forwards, and decide each one
  explicitly (forward it, or say in the docs that it needs a
  compose.override.yaml). Key-presence preconditions in the verify suite
  are what actually catch this — a value-only assertion passes happily on
  the empty string an unforwarded key renders as. (Scope note, WP 4f: that
  is true of a SHELL assertion, where `printenv` prints nothing for absent
  and for empty alike. In Python it is not: `os.environ.get(key, default)`
  supplies `default` only for an ABSENT key, so an unforwarded key gets the
  default fine and only a forwarded-but-empty one arrives as `""`. Do not
  carry the shell framing into config code — see the entry below.)
- `os.environ.get(key, default)` supplies `default` only when the key is
  **absent**; a key that is present-but-blank sails through as `""`. The
  realistic source is a *forwarded* key with empty interpolation
  (`KEY: ${KEY}` with nothing in `.env`) or a bare `KEY:` / `ENV KEY=` in a
  derived image — **not** an unforwarded key, which is simply absent. Any
  path-ish setting with a usable default therefore needs an explicit
  `not value.strip()` refusal at boot, or a blank value defers the failure
  into a background thread that catches everything and retries forever
  (WP 4f: a blank `VAULT_CACHE_ROOT` plus the cached-sweep mode silently
  killed the installed half of every sweep, once per interval, forever).
- Two call sites computing the same domain predicate WILL diverge, and
  behavioural fixtures will not notice. WP 4c-api's route and WP 4d's sweep
  disagreed on one real post-delete state (`[440, 730]` vs `[730]`), which
  let a button re-queue a game the sweep correctly treated as deleted. The
  durable fix is a **structural** pin: monkeypatch the shared function to a
  sentinel-returning fake and assert every caller returns *exactly* its
  result — not a superset or subset, which also catches a caller layering an
  extra filter on top. Measured after unification: reverting either caller
  to its own predicate leaves 1540 behavioural tests green and kills only
  the structural test (WP 4f).

## Subprocess output handling
- SteamPrefill writes its summary table in the OS OEM codepage (cp850
  here), not UTF-8 — decode strict-UTF-8 first, OS-queried OEM second,
  lossy LAST (a decode path that can raise turns a successful download
  into a crashed job: terminate() leaves truncated multibyte tails as
  the NORMAL case) (WP 3.3 blocker).
- "Contains a digit" is a terrible row-detector: SGR remnants
  (\x1b[38;5;226m) and timestamps are digits too — require
  digits-and-separators-only (WP 3.3).

## systemd / packaging
- Persistent=true only works with OnCalendar= timers — on monotonic
  triggers (OnBootSec/OnUnitActiveSec) it is a silent no-op, so
  "catch up after suspend" claims need OnCalendar (WP 2.5).
- network-online.target does not exist in the systemd USER scope —
  Wants=/After= on it are no-ops there (WP 2.5).
- Secret env files: umask 077 BEFORE creating, not chmod 600 after
  (world-readable window) (WP 2.5).

## Go / CLI
- Never register a secret env value as a flag DEFAULT — Go's
  flag.PrintDefaults() prints non-empty defaults verbatim on -h and every
  parse error, leaking the secret to stderr on the recommended
  operational path. Register empty, apply env fallback after parsing
  (WP 2.2 blocker).
- Python str.isprintable() rejects Cf/Zs/Co/Cn — Go parity needs
  !unicode.IsPrint, not IsControl+Zl/Zp (WP 2.2).
- time.Sleep in retry loops is not ctx-cancellable — select on
  ctx.Done() vs time.After (WP 2.2). CGO_ENABLED=0 explicitly or
  "static binary" claims are false on the native target (WP 2.2).
- With core.autocrlf=true and no `*.go eol=lf` in .gitattributes,
  `gofmt -l .` lists EVERY Go file — a checkout property, not a style
  violation. Judge gofmt only from an LF checkout (measured, WP 5.4).
- On this dev host the Go toolchain lives only in WSL (no go.exe) —
  Go verification claims must state where they ran, and reviewers should
  check WSL before treating a Go claim as unverifiable (WP 5.4).

- Removal reviews audit CONTENT, not just references: a deleted reference
  implementation can be the only home of documentation living code still
  cites (the EAppState bit table existed nowhere else) — port embedded
  knowledge before deleting its host (WP 2.6 blocker).

## Testing discipline
- Fail-closed defaults need tests that pin the DEFAULT direction: flip each
  "unknown ⇒ protected" branch and watch a test die — two such flips passed
  the entire 434-test suite unnoticed in review (WP 3.6 Opus).
- A protection rule keyed on data that deletion deliberately preserves
  (mapping rows, ADR-0003) leaks resources forever unless it has an explicit
  last-remnant escape — audit every "skip if referenced" guard for the
  all-referrers-gone end state (WP 3.6).
- Flake-hunt concurrency tests: run the module isolated in a 20-40x loop —
  full-suite green means nothing for timing bugs (WP 1.6 Fable).
- Enumerate the package's GUARANTEES and mutation-test each one by name —
  six killed mutations mean nothing if the seventh (the primary
  fail-closed promise) was never targeted (WP 2.3).
- Mutation-test regression tests (revert the fix, watch the test fail)
  before trusting them (multiple WPs).
- Fixtures: synthetic only, modeled on real structure — never personal data
  (WP 2.1).
- Verify empirically over believing docs or reviewers — several review
  claims were corrected by rigs (WP 1.1 301/302, WP 1.6 rmtree).

- nginx's event log is written with `buffer=64k flush=5s`, so a
  "request it, then grep the log" test is **never** raceless: measured
  0 lines at t≈0 s and t≈2 s, the correct 9-field line at t≈7 s. Any such
  check needs a bounded wait-for-line loop, not a fixed sleep — and the
  same 5 s applies to WP 3.11's sweeper, which therefore sees every line
  up to 5 s late (harmless against a 5-minute sweep interval, but it is
  the reason the naive test shape cannot be made reliable). Found as four
  failures in `verify-stack.sh` step 5i — reproducible, but **inherently
  timing-dependent, not deterministic**: the direction depends on how fast
  the grep follows the request, and on a slow or loaded host the
  `docker compose exec` alone can exceed 5 s and the step passes. Do not
  record it as deterministic, or a later green 5i reads as a regression.
  Pre-existing, and independently reproduced on the untouched baseline
  during the P1 review. **CLOSED in WP 4g** (2026-08-18): step 5i now polls
  for the line — bounded at 10 polls, each preceded by a
  `docker compose exec` round trip, so the effective window is 2-4x the
  flush and widens on exactly the slow hosts that need it. Two details worth
  copying into any future write-then-read check against this log: a flat
  sleep was rejected because it keeps the race with better odds while hiding
  the failure mode, and on timeout the step reports the log's line COUNT plus
  its tail instead of a diagnosis — the wait predicate greps the chunk id, so
  a format regression that dropped field 6 writes a line the grep cannot
  match and would otherwise be misreported as "nothing was written". Suite
  109/109, verified twice.

## Docs / community release
- Entry-point docs describe SHIPPED behavior, not ADR designs: an ADR
  records a decision that may be unimplemented — grep the code for the
  mechanism (and check PLAN checkboxes) before claiming it works
  (WP 5.2 blocker: staleness FAQ described the unshipped manifest oracle
  as the live mechanism).
- Quote container-real paths in docs: the cache lives at
  `/vault/cache/depot/...` (nginx `-p /vault`, `VAULT_CACHE_ROOT`), not
  `/cache/...` (WP 5.2 blocker).
- Never present a planned license as granted — a `## License: Apache-2.0`
  heading without a LICENSE file reads as an effective grant that does not
  exist; word it "planned" until the file lands (WP 5.2 blocker).

## Web UI (Phase 4a)
- A Starlette `Mount("/")` catch-all pre-empts the router's partial-match
  bookkeeping app-wide: every trailing-slash 307 became a 404 and
  HEAD-on-real-route 405s became 404s. Serve a SPA with exact GET+HEAD
  routes plus narrow asset-subtree mounts (/css, /js) instead — then API
  routing is untouched by construction (WP 4a.1 blocker).
- Routing changes are verified by running the SAME request matrix against
  a pristine pre-change baseline (git archive HEAD), with the feature both
  enabled and disabled — README claims about "unchanged behavior" were
  measurably false until the rig existed (WP 4a.1).
- httpx/TestClient normalizes `../` client-side before sending: un-encoded
  traversal tests assert on the wrong path. Real traversal pins need
  %2e%2e-encoded forms or a raw ASGI scope with un-normalized raw_path
  (WP 4a.1 review).
- The frozen mockup is binding for UI surfaces: scaffolding must not add
  navigation items or views the mockup lacks (Clients is a sheet, not a nav
  entry) — any divergence is a user decision, recorded like the paused-slot
  one (WP 4a.1 blocker).
- Security-constant pins must assert STRING LITERALS, not the module's own
  constants: a test comparing the built URL against STEAM_API_BASE stays
  green when STEAM_API_BASE itself is mutated to attacker.example or http.
  Capture the real outbound Request object and urlsplit it against literal
  scheme/host/path (WP 4a.6r blocker).
- An outbound-HTTP module's headline guarantee (host pin, HTTPS) needs its
  own named mutation kill — 83 passing tests proved everything except where
  the secret is actually sent (WP 4a.6r).
- An async poll tick without an in-flight guard forks the loop: while the
  fetch awaits, the stored timer id is already dead, so any nudge
  (visibilitychange, manual refresh) clears nothing and starts a second
  self-re-arming chain — compounding poll rate and duplicating every
  diffed notification. Coalesce nudges into a pendingRefresh flag plus a
  generation token; a ~40-line bare-Node harness (fake document object
  injected before import, gated fake fetcher) tests this glue headlessly —
  "timer glue can't be tested without a browser" was false (WP 4a.2
  blocker).
- Demo-mode fixtures are a shipped surface: they must demonstrate the
  product's invariants (ADR-0003 shared-depot protection), not violate
  them, and a "shapes match the real API 1:1" claim is verified endpoint
  by endpoint against the Pydantic models at git HEAD (WP 4a.2 blocker).
- Before exposing a DB column in the API, verify its actual WRITE matrix
  in code — the plan claimed last_manifest_check was "written on every
  run"; the shipped rule stamps it only on confirmed-current runs
  (done + Updated==0 + UpToDate>0), it survives cache deletion unlike
  last_prefill_at, and the UI wording must say "confirmed current at X",
  not "checked at X" (last_manifest_check mini-WP).
- A documented mechanism with zero callers is the WP 4a.1 failure class in
  module-header form: the card docstring described patch-in-place while
  every path full-rendered and cardStructuralKey had no caller. Grep for
  callers of any mechanism a header claims before believing it (WP 4a.3
  blocker).
- Proving "no animated node touched" is instrumentable headless: stub the
  card, record every querySelector/mutation, and wire createElementNS to
  THROW — a patch path that would rebuild an icon then fails loudly
  instead of passing on trust (WP 4a.3 review technique).
- A mounted()/isConnected gate on a freshly BUILT section no-ops the first
  paint: render() runs synchronously before app.js attaches the node, so
  isConnected is false at exactly that moment. Nulling sectionEl in the
  view-change listener is the complete staleness signal on its own — and
  with a per-card patch planner the bug stops self-healing (re-navigation
  leaves an empty grid until real data changes) (WP 4a.5; twin bug in
  library.js fixed separately).
- Status-icon kinds follow the SHIPPED status set, not the mockup's: a
  terminal 'cancelled' needs its own neutral glyph and sr word — reusing
  the error glyph would misreport an operator action as a failure
  (WP 4a.5, same class as the Failed-chip divergence).

## Android (Phase 4b)
- allowBackup="false" alone is insufficient on API 31+: AGP lint flags it,
  and cloud backup / device-to-device transfer need explicit
  dataExtractionRules + fullBackupContent excluding the app-data domain —
  mandatory posture for an app that will store a vault-api key
  (WP 4b.1).
- Cross-frontend contracts (status-kind wire names, label words, theme
  hexes) are pinned with LITERAL expected sets in tests, never derived
  from the enum under test — a derived round-trip is circular and cannot
  detect drift from the other frontend (WP 4b.1; same class as the
  4a.6r constants-vs-literals rule).
- Gradle wrapper distributions get distributionSha256Sum — the same
  integrity bar the repo already applies to Docker images and CI actions
  (WP 4b.1).
- A background thread whose EXISTENCE is decided from the boot config
  snapshot makes every runtime-settings claim for its keys false ("next
  sweep" that never comes, /v1/schedule publishing a next_eligible_at
  that cannot arrive). Start such threads unconditionally with a cheap
  no-op tick — same shape as the worker thread; measured cost: noise
  (settings-WP blocker).
- The poll-based wait_for_job flake class has now hit TWO different
  tests under full-suite load (test_cache_delete WP 3.6, test_worker
  needs_force) — both pass isolated. Follow-up: scale wait deadlines
  under load instead of re-diagnosing each flake (settings-WP review).
- OkHttp application interceptors run ONCE PER CALL, never per redirect
  hop — a cleartext gate registered only via addInterceptor cannot see an
  https→http downgrade, OkHttp forwards custom auth headers (X-Api-Key)
  across host changes (it strips only Authorization-class headers), and
  followSslRedirects defaults to TRUE. TLS-mandatory profiles need
  followSslRedirects(false) PLUS a network-interceptor gate, pinned by a
  two-server redirect test asserting the canary key never reaches hop 2
  (WP 4b.2 blocker, measured on pinned OkHttp 4.12.0).
- Redundant defence layers cannot be pinned by one end-to-end test: each
  layer alone survives mutation because the other covers it. Pin each
  layer standalone (isolated-component test + configuration assertion)
  in addition to the conjunction (WP 4b.2 review).
- followSslRedirects(false) only blocks SCHEME-CHANGING redirects; a
  same-scheme cross-host 302 still forwards custom auth headers. A
  client with no legitimate redirects sets followRedirects(false) too
  (WP 4b.2 review).
- BigInt("0x...") accepts hex-prefixed strings — a 17-char "0x1100001
  00000000" equals STEAM_ID64_BASE, so an ASCII-digit guard in front of
  BigInt is load-bearing against HEX, not whitespace (whitespace always
  sacrifices a digit position and can never reach the 17-digit base —
  verified empirically). Verify a reviewer's assumed failure mechanism
  before writing the pin for it; pin the mechanism that actually kills
  the mutation (WP 4a.6 fix round).
- Kotlin toLongOrNull honours Unicode Nd digits (Character.digit), so an
  ASCII-digit walk in front of it is load-bearing against IN-RANGE
  Arabic-Indic spellings — a below-range non-ASCII fixture tests the
  range check, not the digit walk. Pin with the in-range non-ASCII
  spelling (WP 4b.3 review; JS BigInt had the analogous 0x-hex case).
- Custom-scheme OAuth/OpenID callbacks are unverifiable on EVERY Android
  version (verified App Links are http/https-only). Injection is fully
  mitigated by re-verifying assertions with the provider over a pinned
  host; the residual is REPLAY of a genuine assertion (attacker's own
  account) absent request<->callback binding — record it and plan a
  per-login random state (WP 4b.3 review).
- Third instance of the pinned-the-fake pattern (4b.2, 4b.3, 4b.8): a
  test fake that RE-IMPLEMENTS production logic (its own JSON decode +
  catch) proves nothing about the shipped path. Extract the logic into
  one shared function called by both production and fake — then the
  existing test pins the real code (WP 4b.8 review).
- WorkManager ExistingPeriodicWorkPolicy.KEEP freezes the schedule spec
  at whatever the FIRST app version enqueued, for the life of the app
  data — interval/constraint changes never reach existing installs.
  UPDATE (2.8+) applies changed specs without resetting the period; the
  common KEEP-vs-REPLACE dichotomy is false on modern WorkManager
  (WP 4b.8 review).
- Native inert (+ aria-hidden) beats hand-rolled Tab interception for
  modal traps: one attribute removes the subtree from tab order,
  hit-testing and the a11y tree by spec, with no focusable-enumeration
  drift; centralize Escape in one stack dispatcher — two independent
  document listeners closed two stacked overlays with one keypress
  (WP 4a.8, live-reproduced).
- An explicit scrollIntoView({behavior:"smooth"}) argument beats the
  CSS scroll-behavior:auto !important reduced-motion override per
  CSSOM-View — JS smooth-scroll call sites need their own matchMedia
  guard (WP 4a.8 review).
- Author display rules (.btn{display:inline-flex}) silently defeat the
  UA's [hidden]{display:none} — every class that is both display-styled
  and hidden-toggled needs a .class[hidden]{display:none} guard; sweep
  by cross-referencing display rules against .hidden= call sites
  (WP 4a.8; third instance of the class after h4.sec and .onbnav).

## Process (2026-08-18 decision audit, Fable)

- A control that protects a person other than the operator (privacy
  switches, data-collection gates) gets a written failure-mode analysis
  BEFORE any coder brief: for each store the setting can live in (env,
  db volume, image default), state what happens when that store is lost,
  and which direction the failure points. The 4h.0 ceiling design would
  have shipped a switch whose volume-loss failure mode was "collection
  silently resumes"; it was caught by the operator's question, not by
  the pipeline — coder and reviewer both had ADR-0009 in front of them
  and neither flagged it. Named options go to the operator first.
- A package is committable only when the reviewer's round-2 REPORT is in
  hand — orchestrator self-verification of the fixes is a supplement,
  never a substitute for the verdict. WP 5.3 was committed on
  self-verification after a round-1 FAIL; the retroactive round 2 had to
  be commissioned by the audit. "Review complete" means report received,
  not verdict word received (the 4e.6 post-commit report carried the
  finding that became WP 4e.8).
- The reviewer's read-only mandate now includes git explicitly, enforced
  in .claude/agents/reviewer.md after the WP 4e.6 incident (a reviewer's
  `git checkout` destroyed uncommitted CSS in a foreign worktree;
  recovery was byte-exact from the served stylesheets, luck that should
  never be load-bearing). Mutations happen in a scratch copy, never in
  any working tree.
- A mutation report's STATED MECHANISM is a claim of its own and drifts
  independently of the result: WP 4h.4 shipped four in one package (a
  compile failure described as a parse error; a JsonDecodingException
  described as a timeout; a fixture pin described as behaviour-testing
  production; a Gradle-input theory the review measured and refuted).
  All four errors ran in different directions — two overstated
  protection, two over-warned — so the fix is procedural, not
  directional: when documenting a mutation, quote the actual failure
  output, name the failing assertion, and let the reviewer re-run the
  exact form documented. "Pin the mechanism that actually kills the
  mutation" (WP 4a.6) extends to the prose describing it (WP 4h.4
  rounds 1-3, 2026-08-19).
- A viewport-width column-count claim is meaningless without naming the
  ELEMENT and the SCROLLBAR STATE: .view-root border-box, .grid content-box
  and viewport-minus-tokens differ by 47px (32 padding + 15 classic
  scrollbar) — enough to straddle an auto-fill track floor and produce two
  irreconcilable, individually-correct measurements (WP 4h.2, three review
  rounds). State the box, state the page state, and record the decisive
  intermediate (grid content-box width) next to the count so the next
  dispute checks one number.
- A DOM-last child of a display:grid container with named areas has NO
  defined visual position: auto-placement drops it into whatever cell is
  vacant, and which cell is vacant can depend on a SIBLING's runtime state
  (.banner-wrap's [hidden] toggled the failure between above-the-content
  and a 148px rail-column sliver). Text-only CSS pins cannot see it; a
  vertical-only live pin catches one of the two states. New grid children
  get an explicit grid-area in EVERY breakpoint block that defines a
  template (WP 4h.2 blocker, browser-measured both states).
- A Docker per-container egress lock (masquerade-disabled bridge +
  internal net + filtering proxy) does NOT close two channels, both
  measured live in WP EG-1 by actually exfiltrating from the locked
  container: (1) DNS — the embedded resolver forwards from the HOST
  namespace, so a unique label resolves against the public authoritative
  NS regardless of the container's missing route; a 32-char secret fits in
  one label. (2) the Docker HOST's own addresses — replies to a container
  bridge address need no SNAT, so host-bound listeners incl. every
  0.0.0.0-published port stay directly reachable. Only a container on an
  internal:true net ALONE loses DNS too. State both as accepted open gaps;
  a security doc that claims "no packet can leave" is the worse failure.
- The reviewer of a security package must ATTEMPT the violation, not read
  the claim: EG-1's two overstatements passed every static test and were
  caught only by the reviewer standing the stack up and exfiltrating. The
  mechanism was sound in both directions it targets; the FAIL was pure
  claim-honesty, which for a doc strangers use to decide whether to run
  this on their network is exactly the defect that matters most.
- Docker attaches a service's networks in REVERSE of the `networks:` list
  order, so a hardcoded `eth0` inside a multi-network container may be the
  wrong interface (WP EG-1: picked the internal net with no gateway); find
  the gateway-bearing interface via /proc/net/route instead.
- A model name hard-coded in an agent definition (`model: sonnet`/`opus`)
  or in the working agreement pins a TIER by name regardless of the
  session's model, and nothing fails loudly when that tier is weaker or
  outdated — the coder ran one tier below the session model and no file
  showed it. (Family aliases do track the newest model of their family;
  the defect is the fixed tier, not a stale generation.) Fix: `model:
  inherit` in every agent frontmatter so delegation rides the session's
  current model, plus call-time selection ("strongest available")
  wherever the working agreement asks for a stronger second pass — never
  a model name in a file or brief. Basis: user decision 2026-09-29, not a
  failed review round (WP R-0).

## 2026-08-22 — the defaults-flip wave (SWEEP-1, APP-DEMO, 4d-web, AG-0, CI-3/AGENT-BIN)

- A guarantee stated one notch stronger than its mechanism is this
  project's most-repeated defect, and WP APP-DEMO hit it three times in
  ONE package at increasing depth: a seven-literal denylist whose KDoc
  claimed type-level impossibility (defeated through the project's own
  net.steam package), its allowlist replacement claiming "fails closed BY
  CONSTRUCTION" (defeated via the unwatched javax. prefix family), and
  the structural ceiling itself — Kotlin's implicit imports make
  ProcessBuilder/Runtime.getRuntime().exec usable with NO dotted name, so
  no fully-qualified-name scan can ever see them. Rule: document the
  ceiling instead of denying it; an identifier denylist to "close" it
  would be the same defect a fourth time. Corollary proven the same day:
  a test-driven production refactor can move a property from code the
  file owns onto framework structure one level out (the banner into
  ModalBottomSheet's own non-scrolling Column) — the review that
  confirmed it disassembled the pinned material3 AAR rather than reading
  docs, and the resulting guarantee is VERSION-COUPLED and must be
  written down as such.
- A UI sentence built from ONE API field over-claims unless that field
  alone carries the meaning (WP 4d-web, both round-1 blockers).
  last_sweep_targets=null has three server-documented meanings (never
  ran / in flight / died mid-run — claim stamps the timestamp and NULLs
  counters in one statement), so rendering it as "has not completed a
  run yet" told a crashed vault's operator to debug the wrong thing
  forever. sweep_cached_gc_risk is a pure configuration predicate, so
  "cached games are being refreshed" asserted activity it cannot know.
  Convergent proof: SWEEP-1 independently added "not a promise a sweep
  is happening" to the same field's server docstring the same day.
  Write state-only copy; when a second field separates the cases (here
  last_sweep_at), branch on it.
- compose `${VAR:-default}` substitutes on unset AND on explicitly-blank,
  so "leave it empty to disable" documentation around a `:-` forwarding
  is false — there is NO .env expression of disabled, and under
  VAULT_SETTINGS_READONLY=1 no PATCH escape either (WP SWEEP-1 R2-B1,
  measured). The no-colon form `${VAR-default}` distinguishes them:
  unset -> default, blank -> "". Choose per variable and record why
  (TZ deliberately stays `:-` because blank==UTC there, measured). Pin
  the substitution FORM structurally and the blank case live.
- A startup line that exists to make unattended behaviour visible must
  (a) print the REQUESTED value next to the RESOLVED one — glibc
  answers a typo'd IANA zone with a plausible POSIX fallback
  ("Europe/Berlinn" -> "Europe (UTC+00:00)") and no error — and
  (b) read the same effective-settings resolution the behaviour itself
  uses, not the boot snapshot: with a db-stored window the boot-config
  line announced "scheduler DISABLED" while unattended deletion was
  scheduled (WP SWEEP-1 R2-B2, both directions measured in the image).
- Text-scrape drift guards (asserting another language's constants) work
  iff every failure message names which of two edits applies: VALUE
  drift -> fix the fixture, GRAMMAR drift -> widen this regex. A guard
  whose header forbids touching the regex while its most likely misfire
  REQUIRES touching the regex gets deleted in irritation. After ANY
  extractor widening, re-perturb for false passes (4d-web's survived 13
  shapes; every silent path exact-match only). The 4d-web guard also
  proved the design end-to-end: deliberately red in its own worktree
  naming the stale sibling config, green on the post-merge run, both
  for the stated reason.
- Twin config files need twin pins: api/.env.example drifted into
  actively setting AND arguing for pre-flip defaults precisely because
  only deploy/.env.example had a config.DEFAULT_*-derived test. Fixing
  the instance without the asymmetry invites the next drift.
- A defaults flip must verify its ACTIVATION precondition ships: SWEEP-1
  flipped "keep cached games current" on while no schedule window
  shipped, so the growing half was inert out of the box while the
  deleting half (auto-GC, queued by every prefill) went live — inverting
  the pairing that justified the flip. The fix was the operator's call
  (ship a window), not the packager's.
- Windows: $env:COMPUTERNAME is the uppercased NetBIOS name; Go's
  os.Hostname() returns the case-preserving DNS name. With a
  case-sensitive persisted identity key, a preview printing the former
  mints ghost identities (WP AG-0, measured DEMON vs Demon). Preview
  from [System.Net.Dns]::GetHostName(), and pin with -clike/-ceq —
  PowerShell's default comparisons are case-insensitive, so a
  case-defect pin using -like passes either way.
- Go vcs.revision stamping inside a linked worktree NESTED IN another
  repo records the ENCLOSING repo's HEAD (root detection needs a .git
  DIRECTORY, so the worktree's redirect file is skipped and the walk
  lands above); a non-nested worktree yields NO stamp at all. The
  orchestrator's first confident root cause here was wrong and survived
  into a README draft — the round-2 report rule (no self-verified
  commits after a fix round) is what caught it.
- Between review rounds, a DROPPING test count is where lost coverage
  hides: APP-DEMO's 612->611 merge of two pins silently stopped
  requiring the banner call to be GUARDED — a bare DemoModeBanner()
  (permanent banner for real users) built green. Diff the guarantees,
  not the counts.

## 2026-08-22, second wave — the AG series (AG-0..AG-3)

- Pinning the pure/model layer proves NOTHING about the pixels: both
  frontend packages of this series FAILed review the same day with the
  same shape — web: all six DOM wirings deletable at 735/735 green;
  Android: ten of eleven UI/controller wirings deletable at 643/643.
  The one thing that fired on Android was an UnusedResources lint, i.e.
  luck (and deleting a LARGER set kept even that green, because dead
  private composables keep their resource references alive). Every
  wiring from model to pixel needs a named witness. The two idioms that
  work here: fake-dom tests for DOM-building web components, and the
  comment-stripped source-scan pin for Compose call sites.
- Root-cause the missing witnesses before writing them: the web DOM
  layer was unpinned because the SHARED TEST HARNESS (fake-dom.js) was
  a stub whose querySelector/appendChild were silent no-ops — the
  coder had written tests against it and they passed vacuously. After
  extending a shared harness, prove the OLD suites still bite (mutate
  their subjects once each), and make the harness THROW on unsupported
  input instead of returning empty — a harness that silently no-ops is
  the silent-pass class one layer down.
- Source-scan wiring pins have generations, and the weaker one has two
  measured failure modes: a dead never-called helper satisfies a
  contains/window scan (false negative — the feature silently gone),
  and a legitimately-moved-and-called helper fails with "deleted" (false
  positive with a wrong diagnosis — how guards get deleted in
  irritation). The strong generation requires the call's
  nearest-enclosing function to BE the intended top-level composable,
  and its message distinguishes deleted from moved. Use it from the
  start; both frontend packages had to be upgraded to it in review.
- The shared-function/unshared-BOUND divergence: AG-1 unified the
  freshness predicate into one function and still diverged, because one
  caller fed it the boot settings snapshot while the other resolved
  effective settings (db>env>default) per tick — one PATCH made the API
  assert exactly the report the scheduler had just refused. Sharing the
  predicate is half the job; both callers must share its INPUTS
  (settings resolution and clock). Third instance of the
  boot-snapshot-vs-effective class in one week (SWEEP-1 startup line,
  AG-1, and the games clock seam).
- Demo fixtures drift back the moment a twin lacks the twin pin,
  proven within 48 hours of writing the rule: Android's demo carried
  the pre-ADR-0014 auto_gc "off" and would have shipped screenshots
  showing a disk-growth warning no real fresh install shows — under a
  kdoc asserting the shipped defaults produce it. Every platform that
  restates a server default needs its own config-drift guard the day
  the fixture is born, not after the first drift.

## 2026-09-30/10-01 — pre-freeze review (the *-FIX-1/2 packages, TH-0b)

- In fake-dom tests, assert on TEXT, never on nodes: a failing node
  assertion makes `node:assert` dump the whole fake-DOM graph, and the
  test process was OOM-killed (SIGKILL) instead of reporting a failure
  (WP WEB-FIX-1).
- Polling/loop tests must stop their loops in `finally`. A broken gate
  then fails the test instead of HANGING the whole suite — measured when
  a mutation forced the N4 store gate open (WP WEB-FIX-1).
- A lease timeout must be measured from the moment the counterparty could
  first act (the hand-off), not from an earlier lifecycle stamp:
  `started_at` predates vault-api's own pre-hand-off cache walk, so every
  job on a cold cache failed as `runner_lost` before a runner saw it. Same
  package: a reattach lookup filtered on "not yet complete" excludes
  exactly the row a restart most commonly leaves behind (WP API-FIX-1).
- A live redirect test whose recording server only implements POST cannot
  see a POST→GET redirect being followed — it passed against the
  vulnerable code. Mutation-check every security test against the OLD
  code before trusting it (WP API-FIX-2).
- Asserting a raw path against a `%q`-formatted log line passes on Linux
  and fails on Windows (`%q` escapes the backslashes). Format the
  expectation the same way the code does (WP AGENT-FIX-1).
- nginx facts measured in the throttling spike: `load_module` relative
  paths resolve against `-p`, not the config file; a `js_set` error fails
  OPEN on `proxy_limit_rate` (no cap, no error); `$connections_active`
  counts idle keep-alive connections too; nginx's `k` suffix is 1024, not
  1000 (WP TH-0b).
- Linux file mtimes are tick-coarse (kernel 6.12): two files written in
  quick succession can share an mtime, so never rank files by mtime alone;
  in tests force the times with `os.utime`. A pre-existing retention bug
  hid behind this and failed 5 of 6 runs at HEAD (WP API-FIX-2).
- Process: delegating to more than ~3 parallel agents hit the session
  limit repeatedly during this review cycle. Queue packages instead of
  fanning out wider; the throughput gained by a fourth parallel agent was
  lost to restarts (WP pre-freeze review, cross-package).
- Process: review-then-fix with mutation checks caught six tests that
  pinned nothing — the webhook redirect test (API-FIX-2, above), the
  AGENT Windows `%q` assertion (above), three APP wiring pins, and the WEB
  OOM-ing node assertion. Each was green against the code it claimed to
  guard. A test that has never been seen failing for the right reason is
  not yet evidence; mutate its subject once before counting it (WP
  pre-freeze review, cross-package).
- Two services mounting the same fresh named volume, both from images
  that populate that path, race on Docker's copy-up ("mkdir … file
  exists"). A green first run proves nothing; the race needs a fresh
  volume to show. Fix: `:nocopy` on every consumer but the one seeder
  (WP TH-1-FIX).
- A self-test that copies a subset of files for a mutation check
  silently starts failing for the wrong reason once the checked script
  gains a dependency outside the subset. Assert the unmutated copy passes
  first, and assert that the failure names the injected difference
  (WP TH-1-FIX).
- A presence check on an env var the image already sets via `ENV KEY=`
  pins nothing: the key is present whether or not compose forwards it.
  Check values (WP TH-1b).
- FastAPI parses the request body before router-level dependencies run,
  so an auth check that must precede body handling belongs in an ASGI
  layer, not a `Depends` (WP SEC-FIX-4).
- `O_NOFOLLOW` guards only the last path component. A symlinked parent
  directory on the same device passes an `st_dev` check, so `lstat` the
  parent too (WP SEC-FIX-4).
- A concurrent-delete test asserting an exact byte sum failed about 1% of
  runs: whoever wins the final `rmdir` gets the credit. Assert the
  documented contract (a floor), and measure flake rates in-process at
  1000 runs rather than calling a test flaky (WP SEC-FIX-4 follow-up).
- nginx 1.29.8 rejects `?@#\` and space in Host with 400 before any map
  runs, but `%` and `_` pass. A status-only Host test cannot tell refusal
  from relay, so check the logs too (WP SEC-FIX-1).
- windows-latest failed `go test` on a commit with no Go change. Fixed
  sleeps and tight wall-clock bounds (80ms, <100ms, a 5ms client
  timeout) do not survive Windows' ~15.6ms timer tick: poll for the
  condition with a generous deadline, and keep bounds at a fraction of
  the forced delay. In Go, a handler's `r.Context()` does not see a
  client disconnect before the body is read; block a test handler on an
  explicit release channel closed before `srv.Close()` (WP
  AGENT-TEST-FIX-1).
