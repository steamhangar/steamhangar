# ADR-0020: HTTPS SNI passthrough on port 443 for *.steamcontent.com

Date: 2026-10-02
Status: Accepted (user decision 2026-10-02, "HTTPS-Durchreichung, Weg A (im
Produkt, rc7)"; publication via `VAULT_TLS_BIND` accepted by the user
2026-10-03, Weg A; implemented in WP CORE-FIX-3; ships in rc7 together
with the upstream keepalive pool, ADR-0017, by user decision 2026-10-03,
each switchable off independently)

## Context

DNS mode is the documented way to send Steam traffic to the cache: the LAN
resolver (AdGuard Home, Pi-hole, vault-dns) answers `*.steamcontent.com`
with vault-core's address, the same as lancache-dns. Since the
production rollout of 2026-10-02 we know that this also catches HTTPS:

- A prefill failed with `HttpRequestException ... while downloading
  manifests`. SteamPrefill (SteamKit2) fetches depot manifests over HTTPS
  from the CDN host. With the rewrite, that host resolves to vault-core,
  and vault-core listened on port 80 only.
- The operator worked around it by removing the runner's DNS override.
  That only helps the one container. Steam clients in the LAN that use
  the rewriting resolver can hit the same problem.

lancache solves this with an SNI proxy on 443 that passes TLS through
without terminating it. The user decided to ship the same in the product,
in rc7, and set the requirements: no TLS termination and no own
certificate; only `*.steamcontent.com` SNI names, everything else closed;
the same resolver as vault-core, with a boot preflight against a looping
resolver; an `.env` switch that is on by default; 443 published only on a
bound address, never 0.0.0.0 without an explicit setting; and egress like
vault-core, one upstream connection per client connection.

## Decision

### Where: a `stream {}` block in vault-core, not a separate service

The passthrough is a `stream {}` block in vault-core's nginx config (both
copies, `core/nginx/nginx.conf` and `core/docker/nginx.conf.template`),
listening on 443 next to the HTTP cache on 80.

- **For:** it shares `VAULT_RESOLVER`, the resolver-loop preflight, the
  image, the pin and the publish pipeline. It needs no new image or service
  definition. It also sits on the same container and the same bind address
  that DNS mode already points clients at.
- **Against:** it changes frozen `core/` code (allowed by the user decision,
  recorded in the ADR-0016 addendum of 2026-10-02). A broken stream block
  would stop the HTTP cache too. `nginx -t` in CI, the drift pins and the
  off switch cover that. The two paths share `worker_connections 1024`;
  the passthrough is capped at 256 sessions (512 connections, see "Loop
  bound").
- **Modules:** the pinned `nginx:1.29.8-alpine3.23` image has the stream
  and ssl_preread modules compiled in statically (`nginx -V`:
  `--with-stream --with-stream_ssl_preread_module`, no `=dynamic`, measured
  2026-10-02). No `load_module` line is needed. `core/Dockerfile` fails
  the build and `.github/scripts/verify-core-nginx.sh` fails CI if that
  changes.
- **The HTTP cache path is untouched:** the drift check pins that the
  `http {}` block listens on port 80 only and is byte-identical with the
  passthrough on or off.

### How: `ssl_preread`, one map as allowlist and target

`ssl_preread on` reads the ClientHello without decrypting anything. One map
is both the allowlist and the target (the SEC-FIX-1 lesson from the HTTP
Host allowlist):

```nginx
map $ssl_preread_server_name $vault_tls_upstream {
    default                                         "";
    "~*^(?=.{1,253}\z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+steamcontent\.com\z"  $ssl_preread_server_name:443;
}
proxy_pass $vault_tls_upstream;
```

An empty target fails in `proxy_pass` before any DNS lookup or connect
(`no host in upstream`), and the connection is closed. Decisions, one per
case:

| SNI | Result | Why |
|---|---|---|
| `cache2-ams1.steamcontent.com`, any subdomain | passed | Content lives on subdomains. |
| `CACHE2-AMS1.SteamContent.COM` | passed, forwarded as sent | DNS is case-insensitive; `~*`. |
| `steamcontent.com` (apex) | closed | Nothing in Steam's download path uses the apex; the HTTP allowlist refuses it too. |
| `x.steamcontent.com.` (trailing dot) | closed | RFC 6066 section 3 forbids it in SNI; no real client sends it. |
| `evilsteamcontent.com`, `steamcontent.com.evil.example` | closed | Label-boundary full match: a dot must precede `steamcontent`, and the name must end in `steamcontent.com`. |
| empty label, `_`, `:port`, `%`, NUL, newline | closed | Strict labels: 1-63 of `[a-z0-9-]`, no leading or trailing `-`, name at most 253 characters. `\z`, not `$`: `$` would also match before a trailing newline. |
| `*.steamserver.net` | closed | The LAN rewrite covers `*.steamcontent.com` only, so no other name is expected on this port (unlike the HTTP allowlist). |
| no SNI (IP literal, non-TLS, no server_name extension) | closed | Nothing to check against the allowlist. |

`check-config-drift.sh` pins the map to exactly these two entries in both
files. `40-vault-preflight.sh` compares the rendered map with them at every
boot. `.github/scripts/verify-core-nginx.sh` sends raw ClientHellos for
every row above at a live listener with no network, and
`deploy/tests/verify-stack.sh` step 5j repeats the main cases against the
real CDN.

### Resolver and loop guard

- The stream block uses `resolver ${VAULT_RESOLVER}`, the same value and
  delta as the HTTP block, never the LAN DNS.
- The existing boot probe in `40-vault-preflight.sh` (a Steam CDN name
  asked of the first `VAULT_RESOLVER`) now guards both paths. It refuses a
  private, loopback, link-local or CGNAT answer, as before. It now also
  refuses an answer equal to one of the container's own interface
  addresses, which matters with `network_mode: host`. An unreachable
  resolver still only logs a note.
- **A runtime destination filter is not feasible in stock nginx.** The
  stream proxy has no hook between "name resolved" and "connect", so it
  cannot refuse an RFC1918, loopback or link-local answer at request time.
  njs was not considered, as a second language and a module the native rig
  lacks, for a filter on a name space Valve controls. Instead there is a
  **loop bound**: `limit_conn` of 64 sessions per client address and 256
  in total. A resolver that starts rewriting after boot sends passthrough
  connections back into the listener from a single source address, so the
  loop stops at the per-client cap instead of eating every worker
  connection. The total cap keeps half of `worker_connections` for the
  HTTP cache. That half also holds the upstream keepalive pool's idle
  connections (at most 32, [ADR-0017](0017-upstream-keepalive-pool.md)
  decision 3A), so at least 480 stay for live HTTP requests;
  `check-config-drift.sh` step 2f pins `worker_processes 1`,
  `worker_connections 1024` and this arithmetic. TLS carries no header, so there is no equivalent of the HTTP
  side's 508 hop guard.

### Switch and publication

- **`VAULT_TLS_PASSTHROUGH`**, default `1`. Accepted values are `1/true/on/yes`
  and `0/false/off/no`; anything else stops the boot. Compose forwards it as
  `${VAULT_TLS_PASSTHROUGH:-1}`, so a blank `.env` line means on and off
  needs an explicit `0`. `26-vault-tls-passthrough.sh` deletes the marked
  stream block for off. The preflight checks the result in both
  directions.
- **Publication follows a separate variable, `VAULT_TLS_BIND`, not
  `VAULT_CORE_BIND`:**
  `"${VAULT_TLS_BIND:-127.0.0.1}:${VAULT_TLS_BIND:+${VAULT_TLS_PORT:-443}}:443"`.
  Unset or blank gives `127.0.0.1` with no fixed host port: Docker picks a
  random loopback port, so nothing is visible on the LAN and nothing can
  clash. Set, it gives `<address>:443` (`VAULT_TLS_PORT` moves the host port
  for testing). `0.0.0.0` happens only if the operator writes it. The
  documented DNS-mode recipe sets `VAULT_TLS_BIND` to the same dedicated
  address as `VAULT_CORE_BIND`.
- **Why not follow `VAULT_CORE_BIND`:**
  - Unset, `VAULT_CORE_BIND` means `0.0.0.0`, which would publish 443 on
    every interface: ruled out by the requirements.
  - Set, `VAULT_CORE_BIND` is not always a dedicated address. The project's
    own test instance binds it to the host's main address with a test port
    (`VAULT_CORE_PORT`), on a host whose reverse proxy owns `<that
    address>:443`. Publishing 443 there would stop vault-core from
    starting after an upgrade.
  - Port 443 is taken far more often than 80 (NAS web UIs, reverse
    proxies). So an explicit opt-in per address is the safe default. The
    price is one more line in `.env` for DNS mode.

### Decision on publication (user, 2026-10-03)

The review flagged the `VAULT_TLS_BIND` rule as a deviation from the brief
("publish only on `${VAULT_CORE_BIND}:443`"). The user accepted it as built
(Weg A): publication is opt-in per address, and with `VAULT_TLS_BIND`
unset the listener is reachable only on the container's loopback publish
(`127.0.0.1:<random port>`), not from the LAN. Chosen over following
`VAULT_CORE_BIND` because an upgrade must never break a host whose reverse
proxy already owns 443 on the core address. Consequence for operators:
**in DNS mode, set `VAULT_TLS_BIND` to vault-core's own address** (the
address the rewrite answers, normally the same as `VAULT_CORE_BIND`);
without it, HTTPS to rewritten names still fails.

### Egress

`proxy_next_upstream off`, so one upstream connection per client
connection, against the stream module's default `on`, which would try the
next A record on a connect error. Behind a carrier-grade NAT (DS-Lite)
that has run out of mappings and answers `113: Host is unreachable` (WP
CORE-FIX-2), every retry is another SYN against the full NAT; the client
retries instead. `proxy_connect_timeout 3s` matches the HTTP path.
`preread_timeout 5s` drops a client that sends no ClientHello.

## Consequences, the honest limits

- **Nothing on 443 is cached or rate-capped.** `VAULT_UPSTREAM_RATE`
  applies to HTTP misses only (the stream connections are not in
  `$connections_writing` either). A client that downloads chunks over HTTPS
  gets a working download at internet speed, not a cache hit.
- **The passthrough sees only the SNI and the byte counts.** It never sees
  URLs or content, and it logs one line per connection: client, SNI,
  target, upstream address, status and byte counts.
- **CGNAT:** each passthrough session is one upstream connection held
  open for its lifetime. Many parallel HTTPS clients behind a DS-Lite line
  use NAT mappings like HTTP misses do. Manifest fetches are few and long
  lived, but nobody has measured it.
- **No runtime destination filter** (see above). The residual risk: a name
  in Valve's own zone that publicly resolves to a private address would be
  dialled. That is bounded by the allowlist (Valve's zone only) and the
  connection caps.
- **The per-client cap of 64** might limit a client that opens very many
  parallel HTTPS connections. Unmeasured with a real Steam client.
  Connections that reach vault-core through Docker's userland proxy (from
  the host itself, or the loopback publish) all carry the bridge gateway's
  address, so they share one per-client budget. verify-stack step 5j's
  log lines show exactly that: the bridge gateway, not 127.0.0.1. LAN
  clients on a published address keep their own source address.
- **The native Windows rig** runs the same stream block and therefore
  needs port 443 free too.
- **Docker publishes the unset case on a random loopback port.** That port
  is reachable from the host itself, and only the allowlist guards it, the
  same as on the LAN.
- **Not measured end to end:** a real SteamPrefill run and a real Steam
  client in DNS mode through the passthrough. `verify-stack.sh` performs
  one real TLS handshake through the cache to a Valve edge and checks
  Valve's certificate. The SteamPrefill fix itself is inferred from the
  production failure.

Operator view: `deploy/README.md` "Port 443: the HTTPS passthrough".
Mechanism: `core/README.md` "HTTPS passthrough on port 443". Threat:
`docs/security/threat-model.md` §1.
