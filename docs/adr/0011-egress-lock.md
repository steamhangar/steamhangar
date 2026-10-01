# ADR-0011: The egress lock — network-enforced, not code-promised

Date: 2026-08-19
Status: Accepted (operator decision 2026-08-18/19, WP EG-1 resumed)

## Context

WP EG-1's first attempt stopped honestly before writing any compose or
network code at all: SteamPrefill ran as a `subprocess.Popen` child of
vault-api's own process, so vault-api's container could never be locked to
LAN-only egress without also cutting off the one thing in the whole stack
that legitimately needs the wider internet. Docker network namespaces are
per-**container**, not per-process — there was no honest way to lock a
container that itself spawned the broad-egress work.

The operator's decision was **split first, then lock**. WP S-1 (ADR-0012)
moved SteamPrefill execution into its own process; WP S-2 wired that process
into `compose.yaml` as `vault-runner`, its own container. With that split
landed, vault-api's own container genuinely has no remaining need to reach
anywhere beyond the LAN by default — every outbound flow it still makes
(the manifest oracle, the Steam Web API relay, webhooks) is small, named,
and enumerable, which is exactly the shape an allowlist can enforce. This
ADR is that lock.

Before writing any of this, the topology below was pre-verified in real
Docker sandboxes during the stopped first attempt and **re-verified
empirically against the actual shipped artifact** during this package (not
re-derived from first principles) — every claim below that says "measured"
was run against a real container, not assumed from documentation:

- A bridge network with `driver_opts:
  com.docker.network.bridge.enable_ip_masquerade: "false"` gives
  inbound-published-port-still-works, outbound-direct-connection-blocked.
  Measured directly (this package): a busybox HTTP responder on such a
  network, published on a host port, returns `200` to a host-side `curl`;
  a `curl` from a container attached only to that same network, to a real
  public IP, times out after the full timeout window (no RST, no immediate
  refusal — the SYN leaves, nothing routable comes back).
- `internal: true` is Docker's own, *stronger* guarantee: no route out of
  that network exists at all, to anything, regardless of masquerade.
  Measured: even DNS *resolution* of an external name fails from a
  container attached only to such a network (`curl: (6) Could not resolve
  host ... DNS server returned general failure`) — not merely the eventual
  TCP connect, which is what makes it the right choice for the network
  vault-api shares with the proxy (nothing about that path should work
  without the proxy, including name resolution).
- tinyproxy (pinned to the exact same `alpine:3.23.5` digest
  `dns/Dockerfile` already carries — reusing an already-vetted base beats
  adding a second one to track) with `Filter` + `FilterDefaultDeny Yes`
  correctly filters both plain HTTP forwarding and HTTPS `CONNECT` tunnels.
  Measured against the actual shipped `deploy/proxy/tinyproxy.conf`: an
  allowlisted host (`api.steamcmd.net`) returns a real `200` from
  `api.steamcmd.net`'s own servers through the proxy; a non-allowlisted
  host (`example.com`) gets `403 Filtered` from tinyproxy itself, for a
  `CONNECT` tunnel specifically (the harder of the two cases, since a
  refused `CONNECT` cannot fall back to inspecting the plaintext request
  the way a refused plain-HTTP forward could).
- All three of vault-api's HTTP clients (`steam_relay.py`, `oracle.py`,
  `webhooks.py`) honour `HTTP_PROXY`/`HTTPS_PROXY` with **zero code
  changes**. Verified by reading, not assumed: `steam_relay.py` and
  `oracle.py` each build their opener with
  `urllib.request.build_opener(_RefuseRedirects)`, and `webhooks.py` calls
  `urllib.request.urlopen` directly with no custom opener at all — neither
  shape ever constructs or overrides a `ProxyHandler`, so
  `urllib.request`'s own default handler list (which `build_opener` always
  adds unless a passed-in handler subclasses it) still includes the stock
  `ProxyHandler`, and that handler is what reads these two environment
  variables. `_RefuseRedirects` subclasses `HTTPRedirectHandler`, which has
  nothing to do with proxying, so it replaces only the redirect handler,
  never the proxy one. (Fixed since, WP API-FIX-2 2026-09-30: `webhooks.py`
  now uses the same `build_opener(_RefuseRedirects)` shape as the other two
  — the direct-`urlopen` fact above described a client that followed
  redirects and forwarded its Basic-Auth header to the redirect target;
  the proxy claim stays true, since `build_opener` still adds the stock
  `ProxyHandler`.)

**Round-2 review found two more measured facts, both real and both left
open by design** (§4 and "What this ADR does NOT claim to defend against"
below have the full argument; stated here as the empirical findings first,
matching this ADR's own house style of leading with what was measured):

- **DNS resolution over `vault-lan` reaches the real, public internet.**
  A unique per-request label queried against a wildcard-DNS host that
  encodes its own answer in the name (e.g. `s1493173.7-7-7-7.sslip.io`,
  which only resolves to `7.7.7.7` if the query reached that public
  authoritative nameserver) resolves successfully from inside vault-api.
  This is NOT a gap in the masquerade-disable measurement above — Docker's
  embedded resolver (`127.0.0.11` inside the container) forwards queries
  from a process the DAEMON runs in the HOST's own network namespace, not
  from a socket inside the container's namespace, so the container's own
  missing route is simply irrelevant to it. It is specifically `vault-lan`
  that carries this (required for the published port to exist at all;
  `vault-egress`'s `internal: true` DOES block even DNS, per the bullet
  above — the two networks are not equivalent here). A 32-character Steam
  API key fits inside one DNS label; this is a real, working, unfiltered
  exfiltration channel, not a theoretical one.
- **The Docker host's own non-loopback addresses are directly reachable,
  `HTTP_PROXY` or not.** A raw socket from vault-api straight to the host's
  bridge-facing address (bypassing `HTTP_PROXY`/`HTTPS_PROXY` entirely, no
  `urllib` involved) reaches a real listener there and gets a real
  response. This is the mirror image of the inbound-published-port finding
  above: a REPLY from the host to a container's bridge address needs no
  SNAT (it is not "leaving the bridge" the way an outbound packet to an
  external destination is), so vault-lan's disabled masquerade does not
  apply to it. In scope: the Docker host's own address(es) and every port
  any container has published on `0.0.0.0` — which, in the shipped stack,
  is `vault-core:80` by default (`deploy/compose.yaml`'s own comment
  explains why `0.0.0.0` is the deliberate, correct bind for that service).
  Arbitrary WAN or other-LAN-host reach is still blocked; the host itself,
  and anything it has published, is not. **Measurement footnote, for
  anyone reproducing this:** the container's network interfaces are not
  reliably named in `networks:` list order — a container attached to
  `vault-lan` then `vault-egress` (that order, in `deploy/compose.yaml`)
  was observed with `vault-egress` as `eth0` and `vault-lan` as `eth1`, the
  reverse. `vault-egress` (`internal: true`) never has a gateway-bearing
  default route at all, so the robust way to find "the host's address" is
  the interface whose `/proc/net/route` entry has BOTH an all-zero
  Destination and a non-zero Gateway — not a hardcoded interface name.
  `deploy/tests/verify-stack.sh`'s own probe does exactly this, after an
  earlier version hardcoded `eth0` and got vault-egress's addressless
  subnet line instead, decoded that to `0.0.0.0`, and got a same-host
  `ConnectionRefusedError` — a false negative in the CHECK, not evidence
  against the underlying claim, but worth recording exactly because it
  looked like the claim failing at first.

## Decision

### 1. The mechanism: two networks, one proxy container, zero application code

`deploy/compose.yaml`'s top-level `networks:` block (see its own banner
comment for the complete, line-by-line map — that comment states the audit
surface explicitly rather than leaving a reader to infer it) defines:

- **`vault-lan`** — a bridge network with masquerade disabled. vault-api's
  published port (Android app, vault-agent, the web UI) still works
  inbound; no direct outbound TCP/UDP connection to an arbitrary WAN or
  LAN destination succeeds. Two things this does NOT close, precisely
  because they are not "an outbound connection from the container" in that
  sense — see the two round-2 bullets above and §4 below: DNS resolution
  (Docker's embedded resolver forwards from the host's own namespace, not
  the container's) and the Docker host's own reachable addresses,
  including anything published on `0.0.0.0` (a reply from the host needs
  no SNAT, unlike an outbound connection leaving the bridge).
- **`vault-egress`** — `internal: true`, with a pinned subnet
  (`172.30.238.0/24`, not Compose's auto-picked one) shared with exactly
  one other container: `vault-proxy`.

vault-api is attached to exactly those two networks — not `default`. It
never talks to vault-core, vault-runner, or vault-dns over the network at
all (every cross-container fact it needs already comes through a shared
*volume*: vault-core's event log, the sqlite `jobs` table), so losing
`default` costs it nothing functionally and is what makes the lock
structural rather than a promise.

`vault-proxy` (new: `deploy/proxy/`) is dual-attached: `vault-egress` (to
receive vault-api's requests) and `default` (its own real, masquerading
route out — to Valve, the manifest oracle, or a webhook receiver, LAN or
WAN). It runs tinyproxy, refusing any destination not in a filter file its
entrypoint renders at container start from `VAULT_EGRESS_ALLOW` plus one
baked-in host (§3 below). `vault-api`'s `HTTP_PROXY`/`HTTPS_PROXY` env
lines point at it by container name; per the empirical finding above, this
required **no change to any application code** — every outbound call this
project already makes goes through the standard library's own proxy
support, which was already there, unused, the whole time.

**Zero code changes is also why this is a real network-enforcement
guarantee, not a code-promise wearing a network costume — stated to its
actual, narrower scope, not the wider one an earlier draft of this ADR
claimed.** A compromised vault-api image — a malicious dependency, an RCE
in a route handler, anything that gets arbitrary code execution inside
that one container — gains nothing by *not* setting `HTTP_PROXY`, or by
trying to open a raw socket DIRECTLY TO AN ARBITRARY WAN OR LAN
DESTINATION: `vault-lan`'s masquerade is disabled and `vault-egress` is
`internal: true` at the **network** layer, underneath any application
code that container could possibly run, for exactly that class of
connection. **The precise claim, corrected (round-2 review B1/B2):** no
environment variable, library call, or code path inside vault-api's own
container can make a direct TCP/UDP connection to an arbitrary destination
succeed. Two channels remain open regardless of what code runs in that
container, and this ADR does not claim otherwise: DNS *resolution* (a
compromised process can still exfiltrate data one DNS label at a time —
a 32-character key fits in one query — because Docker's embedded resolver
answers from the HOST's own network namespace, not the container's) and
the Docker host's own reachable addresses, including any container
published on `0.0.0.0` (`vault-core:80` in the shipped default). Neither
is a code-level gap this package chose to leave in vault-api's own
process; both are structural properties of `vault-lan` existing at all
(§4, and "What this ADR does NOT claim to defend against" below, cover
why closing them is not attempted here). The guarantee that DOES hold even
against an attacker who fully controls the process is the narrower one
just stated — which a "vault-api promises to only call approved hosts"
code-level convention could never have provided either, for the exact same
two channels.

### 2. Rejected: an env-based lock-toggle

An earlier framing considered a `VAULT_EGRESS_LOCK=on|off`-style variable,
gating whether the restrictive network topology applied at all. **Rejected
outright, by explicit operator decision:** the lock ships default-on in
`deploy/compose.yaml` itself, not behind a flag. Two reasons converge:

- A toggle implies a supported "off" position reachable by editing `.env`
  — one line, no `compose.override.yaml`, no second thought. Given how
  easy that makes it to disable, and how little most operators will
  understand about *why* an oracle query or a webhook stopped working
  before reaching for the toggle, a flag would become the path of least
  resistance for "fixing" a filtered-403 rather than reading why the 403
  happened.
- Unlocking is still possible, and documented (`deploy/README.md`) — but
  as a `compose.override.yaml` recipe, matching this project's existing
  house style for every other "advanced, rarely-needed" deviation
  (`deploy/examples/tuned-setup.md`). That is a deliberate, visible act —
  writing a second file that overrides `vault-api`'s `networks:` list — not
  a value typed into the same `.env` file the rest of a normal deployment
  already edits.

### 3. `VAULT_EGRESS_ALLOW`, and the one host that is not gated behind it

`VAULT_EGRESS_ALLOW` (default: empty) is the allowlist's operator-facing
half — comma-separated bare hostnames, forwarded to both `vault-api` and
`vault-proxy` identically. Two real, documented cases need an entry:

- **The manifest oracle.** `VAULT_MANIFEST_ORACLE` is an env-only, boot-time
  switch — its value (and `VAULT_MANIFEST_ORACLE_URL`'s host) is fixed for
  the whole life of the process. That makes a **startup check** cheap and
  honest: `vault_api/config.py`'s `Settings.from_env` parses the oracle
  URL's host and refuses to boot if the oracle is on and that host is
  absent from `VAULT_EGRESS_ALLOW`, naming the missing host in the error.
  The alternative — booting clean and letting every oracle query fail with
  a filtered-403 forever, with nothing pointing back at the missing
  allowlist entry as the cause — is exactly the "advertised but
  unreachable" bug class `docs/LEARNINGS.md` ("Containers") already names
  from an earlier packaging pass, and this check exists specifically so
  this feature does not become its next instance. Deliberately placed in
  `Settings.from_env`, not `Settings.__post_init__`: this is a check on the
  *environment* (the real boot path), not an invariant of the `Settings`
  type itself — several of this project's own existing tests construct
  `Settings` directly with the oracle on and no allowlist at all
  (`test_oracle.py`, `test_gc_execute.py`), in contexts unrelated to this
  package, and must keep passing unchanged.
- **Webhook receivers.** `VAULT_WEBHOOK_URL` is DB-overridable at runtime
  (ADR-0009, `PATCH /v1/settings`) — it can change with no vault-api
  restart, so there is no boot-time value to check it against, and no
  startup check for it is implemented (the honest, cheap version stops at
  the one case that is actually checkable). Documented instead, plainly:
  "if your webhook stops firing after this update, that is the lock
  working" — add the receiver's host to `VAULT_EGRESS_ALLOW`.

**One host is baked into `vault-proxy`'s own image, unconditionally, and is
NOT gated behind `VAULT_EGRESS_ALLOW` at all: `api.steampowered.com`**, the
Steam Web API relay's host (ADR-0004's addendum; `api/README.md`, "Steam
Web API relay"). This is the one deliberate asymmetry in an otherwise
consistent design, and the reason is structural, not an oversight: the
relay's key is a *runtime, DB-stored* setting (ADR-0009,
`PATCH /v1/settings`) that can be turned on with no vault-api restart at
all — unlike the oracle, there is no boot-time moment at which "is this
feature on" is knowable, so a boot-time allowlist check cannot reliably
track it. The chosen trade-off widens the always-on default by exactly one
host in exchange for the relay working the first time an operator turns it
on, rather than failing with a filtered-403 the operator would have no
prior reason to connect to a missing allowlist entry. The manifest oracle
and webhook cases above do not share this problem (the oracle is env-only
and fixed for the process's life; a filtered webhook fails visibly and
loudly documented, not silently), so neither gets the same treatment.

### 4. `NO_PROXY`, and the LAN-webhook question — answered by measurement

The brief this package was written against asked directly: does a
LAN-destined webhook need to go **direct** (bypassing the proxy for local
traffic) or **through the proxy** — and what does `vault-lan`'s no-
masquerade design actually do to a LAN-destined packet, not just a WAN one?

**Measured, not assumed:** a container attached to a masquerade-disabled
bridge network has no working direct route to **an arbitrary** destination
outside that bridge — LAN or WAN alike, no distinction between the two.
Docker's masquerade rule is a blanket "traffic leaving this bridge" rule
with no destination-address exception; disabling it removes the one thing
that lets a reply find its way back to a container-internal address,
regardless of whether the other end is on the operator's own physical LAN
or on the public internet. A direct `curl` from a container on such a
network to a real external address timed out exactly the same way a
request to a private RFC 1918 address would have — there is no code path
in the kernel's iptables rule that treats the two cases differently.

**Two qualifications on "arbitrary," found in round-2 review (B1/B2), that
do not change the webhook conclusion below but must be stated honestly:**
DNS *resolution* of the receiver's hostname still succeeds regardless (§1's
round-2 bullet — the embedded resolver answers from the host's own
namespace), but resolving a name is not delivering a webhook: the actual
HTTP POST still needs a real TCP connection to an arbitrary address, which
is exactly what stays blocked. And if a receiver happens to be bound to
the Docker HOST's own address (rather than a genuinely separate LAN
device) — a notification tool running on the same box as SteamVault,
published the ordinary way — vault-api CAN reach it directly, unproxied,
because a reply from the host needs no SNAT (§1's other round-2 bullet).
That is a real, narrow exception (see "What this ADR does NOT claim to
defend against" below), not a reason to change the advice for the common
case: a receiver on a DIFFERENT device, which is what "LAN webhook" means
for nearly every real deployment.

**The answer this settles for that common case: every one of vault-api's
outbound calls to a genuinely separate device goes through vault-proxy, or
it does not go out at all — there is no "skip the proxy for local traffic"
case to design for, because that case would not work anyway.** `NO_PROXY`
is therefore set to `127.0.0.1,localhost` on vault-api — **loopback
only** — and is **not** exposed as an operator-configurable variable:
adding a LAN host to it would not create a working shortcut for a separate
device, it would simply make that one host unreachable through either
path (and would do nothing at all for a host-bound receiver, which was
already directly reachable regardless of `NO_PROXY`). `deploy/.env.example`
and `deploy/README.md` both state the practical consequence in the same
words, because it is the one surprising behaviour change an operator with
an existing LAN webhook receiver will actually hit: "if your webhook stops
firing after this update, that is the lock working" — the fix is
`VAULT_EGRESS_ALLOW`, on the SAME footing as a public webhook target, not
a `NO_PROXY` entry.

**The loopback exception was not a design choice made in advance — it was
found by running the actual container.** The first version of this design
set `NO_PROXY` to a literal empty string ("proxy absolutely everything, no
exceptions at all"), on the reasoning that any exception was a potential
LAN-bypass hole. Brought up as a real container, `vault-api` immediately
went `unhealthy` and stayed that way: `api/Dockerfile`'s own `HEALTHCHECK`
is a plain `urllib.request.urlopen('http://127.0.0.1:8080/v1/health')`
call from *inside* vault-api's own process — the exact same standard-library
code path §1's empirical finding already established honours
`HTTP_PROXY`/`HTTPS_PROXY` with no code change, which means it obeyed them
here too, tried to reach `127.0.0.1` *through* `vault-proxy`, and got a
real `403 Filtered` back (`docker inspect`'s `State.Health.Log`, verbatim:
`urllib.error.HTTPError: HTTP Error 403: Filtered`), because `127.0.0.1` is
not — and must never be — an allowlisted destination. Excluding loopback
from the proxy costs nothing security-wise (a request to `127.0.0.1` can
only ever reach a listener inside that SAME container's own network
namespace, never anything the lock is meant to block), so the fix is the
narrow, deliberate exception now shipped, not a reason to reconsider the
"empty otherwise" design.

### 5. The audit-surface arithmetic

The operator's brief asked for "a clearly-marked compose block — these ~20
lines are the entire audit surface." Counted directly, DIRECTIVE lines only
(comments excluded, since the point is that a reader should be able to
verify the mechanism without reading prose):

- Top-level `networks:` block: `vault-lan:` + `driver: bridge` +
  `driver_opts:` + the masquerade-disable line (4 lines); `vault-egress:` +
  `internal: true` + `ipam:` + `driver: default` + `config:` + the
  `subnet:` line (6 lines) — **10 lines**.
- vault-api's own `networks:` sub-key + its 2 entries — **3 lines** (the
  `networks:` header line counted here, consistently with how vault-proxy's
  block below counts ITS OWN `networks:` header — round-2 review N1
  corrected an earlier version of this arithmetic that counted the header
  for one service's block and not the other, undercounting by one).
- vault-api's proxy env lines: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`,
  `VAULT_EGRESS_ALLOW` — **4 lines**.
- vault-proxy's service block: `networks:` + 2 entries (3 lines),
  `security_opt:` + 1 entry (2 lines), `cap_drop:` + 1 entry (2 lines),
  `VAULT_EGRESS_ALLOW` (1 line) — **8 lines**, not counting the service
  header and `image:`/`build:`/`restart:`/`logging:` lines, which are
  identical boilerplate to every other service in the file and carry no
  lock-specific meaning.

**Total: 25 directive lines** — a bit over the brief's "~20" ballpark, not
under it, and stated here as counted rather than rounded down to fit:
honesty about the arithmetic matters more than hitting the exact number.
Still a small, fully enumerable surface, spread across two places (the
top-level `networks:` block and the two services' own attachments) rather
than one contiguous block — YAML's structure does not allow a single
compose file to co-locate a top-level network definition with the service
blocks that reference it, so "the audit surface" here means "these named
lines, wherever they sit," which is exactly what the banner comment above
`networks:` in `compose.yaml` states and cross-references from both
services. `deploy/proxy/tinyproxy.conf` and `docker-entrypoint.sh` are the
implementation *underneath* this surface (how the Filter/Allow directives
actually work), not additional audit surface an operator needs to read to
verify the *shape* of the lock — `deploy/README.md`'s "verify in five
minutes" section is what actually walks a skeptical operator through
checking the claim empirically, from outside this project's own words
entirely.

## What this ADR does NOT claim to defend against

Stated plainly, matching this project's own house style
(`docs/security/threat-model.md` §9):

- **The `vault-runner` container.** Deliberately, structurally out of
  scope — its whole reason for existing (ADR-0012) is that SteamPrefill
  needs broad, unenumerable access to Steam's CM/CDN fleet, which cannot be
  allowlisted the way vault-api's small, named set of outbound calls can.
  This is a **stated boundary, not an oversight**: the risk this leaves is
  bounded specifically by WP S-2's own decision to drop the depot-cache
  volume mount from this container (review round 2, should-fix S3) — a
  compromised SteamPrefill process cannot use its broad egress to poison
  the cache every LAN client reads from, because it has no read-write mount
  onto that cache at all, only the sqlite jobs table and its own
  Config/HOME volumes. vault-runner also holds no vault-api web-API key
  (`Settings.from_env(require_api_key=False)`, ADR-0012 §2) — a compromise
  there cannot pivot into an authenticated call against vault-api's own
  control plane either. What it CAN still do: exfiltrate whatever
  SteamPrefill's own process can read (the Steam session under
  `Config/`) over its broad, real egress. That is the accepted residual
  risk this ADR does not attempt to close.
- **DNS is a working, unfiltered exfiltration channel this lock does NOT
  close — corrected framing, round-2 review B1.** An earlier draft of this
  ADR described the DNS path as merely "untouched," alongside
  vault-core's Host allowlist and vault-dns's zone rewrite (both real, but
  answering a different question: those two are about which Steam CDN
  hostnames vault-core will proxy TO, not about what vault-api itself can
  resolve or leak through a query). The honest statement is stronger:
  `vault-lan` gives vault-api a working DNS resolution path to the real
  internet (§1's round-2 bullet — measured, not inferred), and a process
  that controls what name it looks up controls what data leaves inside
  that name (one DNS label comfortably holds a 32-character Steam key).
  **No code change is proposed to close this**: the only way to remove it
  would be to also remove vault-api's own DNS resolution, which breaks the
  published port's own usefulness (nothing else recorded a hostname for it
  to answer requests under, but vault-api's OTHER outbound needs — however
  narrow — still require resolving `vault-proxy` by name on `vault-egress`,
  and container-local DNS cannot be selectively disabled per-destination).
  This is a real, accepted gap, not a mitigated one — recorded here so it
  is never mistaken for closed.
- **The Docker host's own reachable addresses, including anything
  published on `0.0.0.0` — round-2 review B2.** vault-api can reach the
  Docker host's own non-loopback address directly, bypassing
  `HTTP_PROXY`/`HTTPS_PROXY` entirely (§1's other round-2 bullet: a reply
  from the host to a container needs no SNAT, so the masquerade-disable
  never applies to it). In the shipped stack that means `vault-core:80`
  specifically (its `0.0.0.0` bind is deliberate and documented,
  `deploy/compose.yaml`'s own comment on that port) — and, on a host
  running anything else published the ordinary way, that too. This is not
  "arbitrary WAN/LAN reach" (a genuinely separate device is still blocked,
  §4), but it is more than the network topology's own description implies
  if read as "vault-api can reach nothing but vault-proxy": it can reach
  the host it runs on, and whatever that host has chosen to expose.
- **Any container ever attached to `vault-egress` becomes an authorized
  proxy client, unconditionally — round-2 review N2.** tinyproxy's `Allow`
  directive (`deploy/proxy/tinyproxy.conf`) grants by CIDR
  (`172.30.238.0/24`), not by container identity — it has no way to ask
  "is this connection really from vault-api." Today that distinction is
  moot (`vault-egress` is `internal: true` with exactly two containers ever
  attached to it, vault-api and vault-proxy, by construction — nothing else
  in `deploy/compose.yaml` references it), but a future edit that attaches
  a THIRD container to `vault-egress` would, by that act alone, hand it the
  same egress vault-api has, with no additional gate. The CIDR-based
  `Allow` line is the only thing standing between "on this network" and
  "trusted to use this proxy" for any container this network ever gains.
- **`vault-proxy` now logs every outbound destination — round-2 review
  N3.** `LogLevel Info` (`tinyproxy.conf`) means each request tinyproxy
  handles, allowed or filtered, is a line in `docker compose logs
  vault-proxy` (captured by the same `json-file` driver as every other
  service). This is a new place, LAN-local to the Docker host and never
  leaving it, where the shape of vault-api's outbound activity (which
  hosts, how often) now exists as metadata that did not have a single
  collection point before. Not a new data-leaves-the-LAN flow —
  `docs/security/threat-model.md` §6's existing treatment of vault-core's
  own access log is the closer analogy than anything in that document's §5.
- **An operator who removes the proxy, or edits `compose.override.yaml` to
  reattach vault-api to `default`.** The lock is enforced by the network
  topology this repository ships, not by anything that could stop a host
  administrator from changing it. `deploy/README.md`'s "verify in five
  minutes" section exists precisely because this ADR's claims are meant to
  be checked, not trusted — an operator who has *edited away* the lock and
  wants to know it is gone can run the exact same check and see a different
  result.
- **A compromised `vault-proxy` image itself.** If the proxy container is
  compromised (a tinyproxy CVE, a poisoned base image), the lock's
  guarantee is only as good as that one process's own correctness — this
  ADR narrows vault-api's blast radius to "whatever vault-proxy allows,"
  it does not add a second, independent layer verifying the proxy's own
  behaviour from outside itself.

## Alternative documented: a secondary Steam account for the relay/oracle

Independent of the network mechanism above, ADR-0004 already documents the
main-account-key default for the Steam Web API relay. Restated here because
it is the operator-facing alternative to "just allowlist
`api.steampowered.com`" this package's own review round asked to have
spelled out with its exact trade-off: an operator uncomfortable with the
relay's key (main-account by default) reaching Valve at all may configure
a **secondary, throwaway Steam account's** Web API key instead. The precise
cost of that choice: `GetOwnedGames`/`GetPlayerSummaries` then read only
**public** Steam profiles — including the operator's own library, which
becomes visible to this relay only if the operator's own game-details
privacy setting is Public. A private profile under a secondary key returns
an empty or missing games list from Valve's own API, not an error this
project could recover from — this is a limitation of the Steam Web API
itself, not something SteamVault's relay could work around. Choosing the
secondary-account path trades "the relay can see anyone's owned games
regardless of their privacy setting, using the operator's own logged-in
session's implicit trust" for "the relay can only see what is already
public," which is the whole point for an operator who wants that
narrowing specifically.

## Consequences

- `deploy/compose.yaml`: top-level `networks:` block (`vault-lan`,
  `vault-egress`), a new `vault-proxy` service, `networks:` added to
  `vault-api`'s (dropping `default`), and four new env lines on
  `vault-api` (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`,
  `VAULT_EGRESS_ALLOW`). `vault-core`, `vault-runner`, `vault-dns`
  unchanged (§"What this ADR does NOT claim to defend against").
- New `deploy/proxy/` directory: `Dockerfile` (tinyproxy, pinned to
  `dns/Dockerfile`'s exact alpine digest), `tinyproxy.conf`, and
  `docker-entrypoint.sh` (renders the filter file from
  `VAULT_EGRESS_ALLOW`).
- `api/vault_api/config.py`: new `Settings.egress_allow` field,
  `_env_egress_allow` parser (shared character-allowlist reasoning with the
  proxy's own entrypoint script), and a startup cross-check against
  `VAULT_MANIFEST_ORACLE` in `from_env` (not `__post_init__` — see §3).
- No change to `vault_api/steam_relay.py`, `oracle.py`, or `webhooks.py` —
  the empirical finding in this ADR's Context section is that none was
  needed.
- `docs/security/threat-model.md` §5 gains an enforcement column stating,
  per outbound flow, whether it is now proxy-gated, baked-in, or
  unaffected; §8 gains a note that `deploy/proxy/Dockerfile`'s base-image
  pin follows the same digest-pinning discipline as every other component.

## Addendum 2026-10-01 (WP DEPLOY-FIX-2): the vault-egress subnet is configurable

Context: a production stack and a test stack must run side by side on one
host (maintainer decision 2026-10-01, precondition for v0.1.0-rc3). The
Compose project name scopes network *names*, not subnets, so the second
stack's `up -d` failed with "Pool overlaps" on the literal
`172.30.238.0/24`, and `deploy/tests/verify-stack.sh` cascaded into dozens
of FAILs instead of stopping.

Decision:

- `deploy/compose.yaml` states the subnet as
  `${VAULT_EGRESS_SUBNET:-172.30.238.0/24}`. **The default is unchanged**, so
  a single-stack deployment needs no action. The colon form is deliberate: a
  blank value is never meaningful for a subnet, so blank falls back to the
  default exactly like unset.
- The same expression is forwarded into `vault-proxy`'s environment.
  `deploy/proxy/docker-entrypoint.sh` now renders
  `/run/tinyproxy/tinyproxy.conf` and `/run/tinyproxy/filter` at every start
  from a template (the shipped `tinyproxy.conf`, installed as
  `/etc/tinyproxy/tinyproxy.conf.template`; the package's stock config is
  deleted at build time), replacing the template's one static
  `Allow 172.30.238.0/24` line with the value. Only `/run/tinyproxy/` is
  owned by the `tinyproxy` account; `/etc/tinyproxy/` stays root:root 0755
  and the template root:root 0444. So the proxy can change its running
  config, but never the source that config is rebuilt from at each start. §1's "exact,
  auditable CIDR" property holds: the client allowlist is still one exact
  CIDR, now the one the network actually has.
- Fail closed: unset or blank in the container, or anything that is not a
  strict IPv4 CIDR (four octets 0-255 without leading zeros, prefix 8-30,
  nothing else; `deploy/proxy/validate-subnet.sh`), is FATAL and the proxy
  does not start. After rendering, the entrypoint asserts the Allow lines
  are exactly `Allow 127.0.0.1` plus one line equal to the value; a template
  drift (no CIDR line, a second one, an extra host line) is FATAL too. There
  is no fallback inside the container, because compose always supplies a
  value and a silent default would reintroduce the drift this replaces.
- `verify-stack.sh` runs on its own `172.30.239.0/24`, aborts with FATAL on a
  failed build or `up -d`, and checks live that the proxy's rendered Allow
  line and vault-api's vault-egress address match that subnet.

Consequences: operators running two stacks set `VAULT_EGRESS_SUBNET` in the
second stack's `deploy/.env` (`deploy/.env.example`, `deploy/README.md`
"Running two stacks on one host"). The value is not checked for overlap with
other Docker networks; Docker itself refuses an overlapping pool at `up -d`,
which is loud. Host bits must be zero (`172.30.239.5/24` is refused), so
Docker and tinyproxy always receive a plain network address.

IPv6 (added 2026-10-01, found by a live `verify-stack.sh` run): the host's
Docker daemon gave new networks an IPv6 ULA subnet as well
(`172.30.239.0/24 fdd0:0:0:b::/64` on vault-egress). The IPv4 lock relies on
masquerade being off on `vault-lan`, and IPv6 has no NAT to turn off, so on a
host with a routed IPv6 prefix and IPv6 forwarding `vault-api` might egress
over IPv6 past the proxy. `vault-lan` and `vault-egress` now set
`enable_ipv6: false` explicitly, so `vault-api` has no IPv6 address at all;
`verify-stack.sh` step 6l checks both networks and both of vault-api's
attachments live. The `default` network, used only by services that are
allowed out anyway, is left as the daemon configures it.
