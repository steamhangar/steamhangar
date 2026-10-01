# vault-dns (Phase 1, WP 1.8)

Optional, bundled DNS component for users who don't already run a local DNS
server. Implements `docs/PROJECT_PLAN.md` section 10, mode 2. If you already
run AdGuard Home, Pi-hole, or plain dnsmasq, you almost certainly want mode 1
instead -- skip to ["When NOT to use vault-dns"](#when-not-to-use-vault-dns-mode-1-instead)
below.

This work package (WP 1.8) shipped the **config template and docs only**.
The container arrived with WP 1.9: `dns/Dockerfile` +
`dns/docker-entrypoint.sh` (which implements the placeholder contract at the
bottom of this file) and the `vault-dns` service behind `--profile dns` in
`deploy/compose.yaml`. **The binding open-resolver requirement below is
honoured there**: port 53 is published on `${VAULT_DNS_BIND}` with a
`127.0.0.1` fallback so an unset value fails closed, never on `0.0.0.0`.
Deployment instructions: `deploy/README.md`.

## Files

```
dns/
├── dnsmasq.conf.template   # the config, with ${VAR} placeholders (see below)
├── tests/
│   ├── test-dnsmasq-config.ps1   # entry point: renders + validates the template
│   └── functional-check.sh        # WSL2-side helper invoked by the .ps1 above
└── README.md                      # this file
```

## What vault-dns does

A single dnsmasq process, enabled via a Compose profile, that:

- answers `*.steamcontent.com` (any subdomain, wildcard) with your
  SteamHangar cache server's IP address for **A** (IPv4) queries
- answers **AAAA** queries for the same zone with **NODATA** (NOERROR, zero
  answers) -- not a real address, not NXDOMAIN, just "nothing here" --
  instead of forwarding them upstream (see the IPv6 section below for why
  this matters)
- forwards every other query (i.e. all normal internet DNS) to configurable
  upstream resolvers (default: `1.1.1.1` and `8.8.8.8`)

Point your router's DHCP-assigned DNS server at vault-dns's IP (see
["DHCP setup"](#dhcp-setup) below) and every device on the LAN that resolves
`*.steamcontent.com` -- Windows Steam client, Linux/Steam Deck client,
SteamPrefill -- gets redirected to your cache automatically, with no
per-device configuration.

## The IPv6 bypass, explained

This is the single most important thing to understand about *any* DNS-based
Steam cache redirection, not just vault-dns.

Modern OSes prefer IPv6 when a AAAA record is present ("happy eyeballs").
The Steam CDN (`*.steamcontent.com`) has real, routable IPv6 addresses. If
your local DNS answers the A query with your cache's IP but still forwards
the AAAA query upstream and gets back Valve's real IPv6 address, an
IPv6-capable client will silently connect over IPv6 directly to Valve --
completely bypassing your cache. No error, no log entry on your side, the
client just never shows up as a cache hit.

This was verified live during Phase 0 (`poc/linux-client-test`, WP 0.6
follow-up fix, recorded as requirement 6 in
[`docs/adr/0001-proxy-store-feasibility.md`](../docs/adr/0001-proxy-store-feasibility.md)):
on dnsmasq 2.92, a bare `address=/steamcontent.com/<ip>` line only
intercepts A queries -- AAAA queries for the exact same name are forwarded
upstream and come back with Valve's real address. The fix is pairing it
with `local=/steamcontent.com/`, which makes dnsmasq authoritative for the
whole zone (never forwards zone queries at all), turning the AAAA answer
into clean NODATA instead.

**This is why `dns/dnsmasq.conf.template` always pairs `address=` with
`local=`, and it's why the instructions below for AdGuard Home and Pi-hole
each include an explicit "close the AAAA leak" step.** Skipping it doesn't
break anything visibly -- it just quietly stops working for a growing
fraction of your devices.

## When NOT to use vault-dns (mode 1 instead)

If you already run a local DNS server, use it directly instead of adding a
second DNS hop for vault-dns to manage -- one less container, one less
thing that can conflict on port 53. `docs/PROJECT_PLAN.md` section 10 calls
this mode 1, the recommended path for most homelabs.

Whichever system you use, you need the **same two things** vault-dns's own
config does: an A-record rewrite for `*.steamcontent.com` to your cache
server's IP, AND an explicit AAAA closure for the same zone. Skipping the
second one re-opens the exact IPv6 bypass explained above.

### AdGuard Home

Unlike dnsmasq's `address=` (which is scoped to the one record type it was
given a literal for -- see the IPv6 section above), AdGuard Home's DNS
Rewrite matching is not record-type-scoped: once a query's name matches a
rewrite entry, AdGuard Home answers it authoritatively for every RR type,
not just the one you gave it an address for. Practically, that means **rule
1 below already closes the AAAA leak on current AdGuard Home** -- there is
no dnsmasq-style split behavior to work around here. Rule 2 is included
anyway as explicit belt-and-braces (cheap to add, makes the AAAA closure
visible/auditable in the custom-rules list instead of relying on an
internal matching detail, and covers older AdGuard Home versions if that
matching behavior ever changes) -- **always verify with the `dig` command
at the end of step 1 regardless of whether you add rule 2.**

1. **A-record rewrite (does the whole job on current AdGuard Home):**
   *Filters -> DNS rewrites -> Add DNS rewrite*
   - Domain: `*.steamcontent.com`
   - Answer: your cache server's IP address (e.g. `192.168.1.50`)
   - Save. This is a wildcard match (per AdGuard Home's own rewrite
     matching rules, a leading `*.` matches any subdomain but not the bare
     domain itself -- steamcontent.com itself has no legitimate use here,
     only its subdomains like `cache2-ams1.steamcontent.com` do).

   Verify immediately: `dig AAAA cache2-ams1.steamcontent.com @<adguard-ip>`
   should already return `status: NOERROR`, `ANSWER: 0` -- no address in
   the answer section at all, from this one rule.

2. **AAAA closure, belt-and-braces (optional, not required for the above
   to work):** *Filters -> Custom filtering rules* (a different screen
   from DNS rewrites -- this uses AdGuard's `$dnsrewrite` filter syntax,
   not the rewrite-list UI), add:
   ```
   ||steamcontent.com^$dnstype=AAAA,dnsrewrite=NOERROR;;
   ```
   This matches the domain and all its subdomains (`||...^`), applies only
   to AAAA queries (`$dnstype=AAAA`), and rewrites the response to an empty
   NOERROR answer (`dnsrewrite=NOERROR;;` -- the two trailing empty fields
   mean "no record type, no value", i.e. NODATA). A queries are untouched
   and still resolve via rule 1 above.

### Pi-hole

Pi-hole's web UI ("Local DNS Records") does not support wildcard domains --
only exact hostnames -- so it cannot express `*.steamcontent.com` on its
own; you need Pi-hole's underlying dnsmasq-derived engine (FTL) directly,
via one of two paths depending on your Pi-hole version. **Check your
version first (`pihole -v`) -- the two paths are not interchangeable, and
using the wrong one silently does nothing.**

#### Pi-hole v6 (default since 2025) -- primary path

Pi-hole v6 changed its config format to a single `/etc/pihole/pihole.toml`
file, and **no longer reads `/etc/dnsmasq.d/*.conf` by default** -- a
config file dropped there the old way is silently ignored (confirmed
against the current FTL source: the `misc.etc_dnsmasq_d` setting that
gates this defaults to `false`). Use the built-in raw-lines setting
instead:

1. Web UI: *Settings -> All Settings -> Miscellaneous ->
   `misc.dnsmasq_lines`*. Add these two lines (or edit
   `/etc/pihole/pihole.toml` directly if you prefer):
   ```toml
   [misc]
     dnsmasq_lines = [
       "address=/steamcontent.com/192.168.1.50",
       "local=/steamcontent.com/"
     ]
   ```
   (replace `192.168.1.50` with your actual cache server IP). This is
   the exact same two-line pairing as `dns/dnsmasq.conf.template` in this
   repo, for the same reason (ADR requirement 6, explained above) --
   Pi-hole's dnsmasq core (FTL) has the exact same AAAA-forwarding
   behavior as vault-dns's own dnsmasq.
2. Save via the Web UI (FTL reloads automatically), or if you edited
   `pihole.toml` by hand: `pihole restartdns` (or `service pihole-FTL
   restart`).
3. Verify both directions:
   ```
   dig A    cache2-ams1.steamcontent.com @<pihole-ip>   # -> your cache IP
   dig AAAA cache2-ams1.steamcontent.com @<pihole-ip>   # -> NOERROR, ANSWER: 0
   ```

#### Pi-hole v5 (or v6 with the legacy path re-enabled) -- fallback

If you're still on Pi-hole v5, or you've deliberately set
`misc.etc_dnsmasq_d = true` in `pihole.toml` (Web UI: *Settings -> All
Settings -> Miscellaneous -> `misc.etc_dnsmasq_d`*) to keep using
config-file drop-ins on v6:

1. Create `/etc/dnsmasq.d/02-steamhangar.conf` on the Pi-hole host:
   ```
   address=/steamcontent.com/192.168.1.50
   local=/steamcontent.com/
   ```
   (replace `192.168.1.50` with your actual cache server IP).
2. Apply it: `pihole restartdns` (or `service pihole-FTL restart` on
   older installs).
3. Verify both directions exactly as in step 3 above.

### Plain dnsmasq / Unbound

For a standalone dnsmasq install (not Pi-hole, not vault-dns), just add the
same `address=` + `local=` pair from the Pi-hole v5/fallback step above to
any file under your dnsmasq's `conf-file`/`conf-dir` and reload.

For **Unbound**, add this to `unbound.conf` (or a file under
`/etc/unbound/unbound.conf.d/`, whichever your install uses for local
overrides) inside the `server:` clause:

```
server:
    local-zone: "steamcontent.com." redirect
    local-data: "steamcontent.com. A 192.168.1.50"
```

(replace `192.168.1.50` with your actual cache server IP), then reload
(`unbound-control reload`, or restart the `unbound` service). Two things
worth understanding about why this is only one directive pair, not two:

- `redirect` is a *zone type*, not a per-record-type rewrite -- it makes
  Unbound authoritative for `steamcontent.com.` and all its subdomains, and
  answers every query in that zone from `local-data` regardless of query
  type or which exact subdomain was asked (this is the same "authoritative
  for the whole zone" property dnsmasq's `local=` provides, except Unbound
  bakes it into the zone type itself rather than needing it as a second
  line).
- Because only an `A` record was given in `local-data`, an AAAA query in
  this zone has no matching record to return -- Unbound (being
  authoritative here) answers NODATA (NOERROR, zero answers) directly,
  the same outcome as vault-dns's own `address=`/`local=` pairing, without
  a second directive. There is no dnsmasq-style A-only-vs-AAAA-leak
  behavior to work around with Unbound in the first place.

Verify the same way as the other systems above:
```
dig A    cache2-ams1.steamcontent.com @<unbound-ip>   # -> your cache IP
dig AAAA cache2-ams1.steamcontent.com @<unbound-ip>   # -> NOERROR, ANSWER: 0
```

## DHCP setup

However you got the AAAA leak closed above, clients need to actually *ask*
that resolver. Two ways to make that happen:

- **Recommended: set it at the router.** Change your router's DHCP server
  settings so the "DNS server" it hands out to clients is the DNS host's
  IP (vault-dns's container IP, or your existing AdGuard Home / Pi-hole
  box). Every device on the LAN picks this up automatically the next time
  it renews its DHCP lease (or after a reboot/reconnect) -- no per-device
  configuration, which matters for Steam Decks and consoles that are
  awkward to configure individually.
- **Per-device (fallback, if you can't or don't want to change the
  router):** manually set that same IP as the DNS server in each device's
  network settings. Works, but has to be repeated on every device and
  redone if the DNS host's IP ever changes.

Either way, keep a secondary/upstream DNS configured on the router or
device pointing at a real public resolver as a fallback **only if** your
DNS host (vault-dns or otherwise) going down should not take your whole
LAN's internet DNS down with it -- for vault-dns specifically, the
container itself already forwards non-`steamcontent.com` queries upstream
(`${UPSTREAM_DNS_1}`/`${UPSTREAM_DNS_2}`), so this is a resilience
choice, not a functional requirement.

## Operational notes: exposure, logging, and TTL

### Exposure: vault-dns is an open resolver if published wrong

vault-dns forwards arbitrary queries to real upstream resolvers
(`${UPSTREAM_DNS_1}`/`${UPSTREAM_DNS_2}`) with **no source-address ACL** --
that's normal for a home DNS server on a trusted LAN, but it becomes a
serious problem the moment it's reachable from the internet: an open
recursive resolver is a well-known abuse vector for DNS
amplification/reflection attacks against third parties, using your
homelab's bandwidth and IP reputation.

**Binding requirement, honoured by WP 1.9's Compose file:** port 53 must be
published on a specific LAN-only host IP, e.g. `"192.168.1.50:53:53/udp"`
(+ the matching `/tcp` line) -- **never** a bare `"53:53/udp"`, which
Docker maps to `0.0.0.0` on the host, i.e. every interface including any
WAN-facing one. WP 1.9 states this explicitly in `deploy/compose.yaml`
(the `vault-dns` ports comment, `VAULT_DNS_BIND`) and `deploy/README.md`,
rather than leaving it as an implicit assumption.

Container-networking caveat worth understanding: in a bridge-network
container, inbound connections are DNAT'd to the container's internal
`0.0.0.0:53`, but DNAT rewrites the *destination*, not the *source* -- the
real client source IP is preserved end-to-end. `dnsmasq.conf.template`'s
own `listen-address`/`bind-interfaces` settings see that real source IP
but have no ACL mechanism to filter on it. In other words: **exposure
control belongs entirely at the Compose/port-publishing level** (which
host interface actually gets mapped) -- there is nothing inside this
container's dnsmasq config that can retroactively fix a `0.0.0.0` publish.

### Privacy note: query logging

`log-queries` is **off by default** in `dns/dnsmasq.conf.template`. Unlike
vault-core (which only ever sees Steam CDN traffic), vault-dns is this
LAN's forwarding resolver for *every* domain every client asks it --
turning on query logging logs full browsing-metadata-level history (every
hostname every device on the LAN resolves, timestamped) to disk/stdout
indefinitely. That's a meaningfully bigger privacy footprint than "cache
debug logging" suggests, especially on a shared household network, so this
template ships it commented out.

To debug requirement A12's "is my client even asking vault-dns" question,
uncomment the `log-queries` line in the rendered/template config for the
duration of the debugging session only, then comment it back out (with
the WP 1.9 container: rebuild/redeploy without it). There is no partial
option to log only `*.steamcontent.com` queries and nothing else in
dnsmasq -- it's an all-or-nothing switch, which is exactly why it defaults
to nothing.

### Answer TTL (`local-ttl`)

Not set in `dns/dnsmasq.conf.template`, meaning dnsmasq's own default
applies: **0 seconds** for `address=`/`local=`-sourced answers (confirmed
against dnsmasq 2.92's own man page: "dnsmasq by default sets the
time-to-live field to zero, meaning the requester should not itself cache
the information"). That default is deliberately left alone here -- it
guarantees a client picks up a changed `CACHE_IP` (e.g. after moving the
cache server to new hardware) on its very next query, at the cost of
slightly more repeat traffic to vault-dns for very chatty clients. Setting
`--local-ttl=<seconds>` would trade that immediacy for reduced load, at the
risk of clients using a stale (moved-away) cache IP for up to that many
seconds -- not recommended for a component whose whole job is redirecting
to a specific box.

## Placeholder contract (`${VAR}` / envsubst)

`dns/dnsmasq.conf.template` is not consumed directly. It contains `${VAR}`
placeholders that must be substituted (this project uses `envsubst`,
already available in essentially any Linux base image) into a real config
file before dnsmasq starts. **WP 1.9 wired this substitution into the
container entrypoint, `dns/docker-entrypoint.sh`** -- WP 1.8 defined the
contract that wiring satisfies:

| Placeholder | Meaning | Required? | Documented default |
|---|---|---|---|
| `${CACHE_IP}` | IP address of the SteamHangar cache server (vault-core) that `*.steamcontent.com` should resolve to | Required, no sensible default -- every deployment's cache IP is different | none -- WP 1.9's entrypoint fails fast (like `api/.env.example`'s `VAULT_API_KEY` convention) if this is unset, rather than silently emitting a broken `address=/steamcontent.com/` line |
| `${UPSTREAM_DNS_1}` | Primary upstream forwarder for everything outside `*.steamcontent.com` | Optional | `1.1.1.1` (Cloudflare) |
| `${UPSTREAM_DNS_2}` | Secondary upstream forwarder | Optional | `8.8.8.8` (Google) |

Notes WP 1.9's entrypoint was built against (kept as the contract it
implements):

- `envsubst` has no built-in "use this default if unset" syntax by itself
  -- the entrypoint script is responsible for exporting the documented
  defaults for `UPSTREAM_DNS_1`/`UPSTREAM_DNS_2` *before* invoking
  `envsubst` if the corresponding environment variable is empty (a plain
  `: "${UPSTREAM_DNS_1:=1.1.1.1}"` shell idiom works). `CACHE_IP` should
  NOT get a silent default -- see the table above.
- Only exactly these three variable names appear in the template, so a
  plain `envsubst < dnsmasq.conf.template > dnsmasq.conf` (no
  `envsubst '$CACHE_IP $UPSTREAM_DNS_1 $UPSTREAM_DNS_2'` allowlist needed)
  is safe and won't accidentally mangle unrelated `$`-looking text, because
  there isn't any elsewhere in the file.
- Follow the `.env`/`.env.example` convention already established by
  `api/.env.example` (committed example, real `.env` gitignored,
  never hold secrets in the compose file itself) as WP 1.9 did for the
  Compose service and its env wiring -- none of these three values are
  secrets, but the convention should stay consistent project-wide.

## Validating the template

```powershell
dns\tests\test-dnsmasq-config.ps1
```

Renders the template with test placeholder values and runs
`dnsmasq --test` against the result inside WSL2 (dnsmasq 2.92 is already
installed there from Phase 0's `poc/linux-client-test/wsl-setup.sh`) to
confirm it's syntactically valid. Optionally also runs a short-lived,
throwaway dnsmasq instance on a non-standard port to prove the rendered
config actually behaves correctly -- for both an arbitrary synthetic
`*.steamcontent.com` subdomain and a realistic Steam CDN edge hostname
(`cache2-ams1.steamcontent.com`, the same one `core/tests/test-core.ps1`
and `poc/linux-client-test/scenario-b.sh` use): A -> cache IP, AAAA ->
NODATA -- see the script's header comment for exactly how it avoids
touching the live scenario-B dnsmasq instance that may already be running
in WSL2 on port 53.

## What this work package does NOT cover

- The actual container/Dockerfile and Compose service definition, the
  `--profile dns` wiring, and the entrypoint script that performs the
  `envsubst` substitution described above -- all shipped later by WP 1.9.
- Changes to `core/` or `api/` (owned by parallel work packages).
- Changes to `poc/` (frozen as Phase-0 evidence; `poc/linux-client-test`'s
  live scenario-B dnsmasq instance is read-only context for this work
  package's tests, never modified or restarted by them).
