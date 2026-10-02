# Deploying SteamHangar (Phase 1, WP 1.9)

Docker Compose deployment for the five server-side components:

| Service        | What it is                                        | Port  | Enabled |
|----------------|---------------------------------------------------|-------|---------|
| `vault-core`   | nginx `proxy_store` cache, path-faithful depot storage | 80 (HTTP) | always |
| `vault-api`    | FastAPI + SQLite control plane                    | 8080  | always |
| `vault-runner` | the SteamPrefill runner (WP S-2, ADR-0012) — same image as `vault-api`, runs `python -m vault_api.prefill_runner` instead | none (no HTTP) | always |
| `vault-proxy`  | the egress-lock allowlist proxy (WP EG-1, ADR-0011) — `vault-api`'s route for an arbitrary destination beyond the LAN (two narrower channels stay open regardless, see "Egress lock" below) | none (LAN-internal only) | always |
| `vault-dns`    | optional dnsmasq that redirects `*.steamcontent.com` | 53 (UDP+TCP) | `--profile dns` |

**`vault-api` no longer runs SteamPrefill itself.** As of WP S-2, this
compose file ships `VAULT_PREFILL_MODE=queue` (ADR-0012): vault-api hands a
prefill job off through the database, and the separate `vault-runner`
container claims and executes it. This is what made it possible to lock
vault-api's own container down to LAN-only egress (WP EG-1, below) without
also cutting off the one thing that genuinely needs the wider internet — see
`docs/adr/0012-prefill-runner-split.md` for the full design. `vault-runner`
has no port mapping and serves nothing: it polls the same database vault-api
uses, runs SteamPrefill for the job it claims, and reports the result back
the same way. The bare-metal/native dev setup (`api/README.md`
"Quickstart") is unaffected and keeps the older `subprocess` mode, where
vault-api runs SteamPrefill in its own process — there is no second process
to run a runner in outside a container, and nothing here changes that path.

**`vault-api`'s own container has no default route for an arbitrary
outbound connection.** As of WP EG-1 (ADR-0011), the split above is what
this was building toward: reaching an arbitrary WAN or other-LAN-device
destination now requires passing through a new `vault-proxy` service,
which refuses every such destination not on an allowlist. Two narrower
channels are not closed by this and are named plainly, not glossed over:
DNS resolution still works from inside `vault-api` (it can leak data one
query label at a time), and the Docker host's own reachable addresses
(including anything published on `0.0.0.0`, `vault-core:80` by default)
remain directly reachable. See
[Egress lock](#egress-lock-vault-api-loses-its-default-route-out)
below for the full mechanism, both of those channels, and a five-minute
recipe to verify all of it yourself.

Everything is LAN-only. Nothing here should ever be reachable from the
internet — see [Security posture](#security-posture) before you expose
anything.

```
deploy/
├── compose.yaml            # the deployment
├── .env.example            # committed template -> copy to .env
├── proxy/                  # vault-proxy: the egress-lock allowlist proxy (WP EG-1)
│   ├── Dockerfile
│   ├── tinyproxy.conf
│   └── docker-entrypoint.sh
├── examples/
│   └── truenas-scale-dockge.md   # NAS-specific layout (dedicated ZFS cache dataset, etc.)
├── tests/
│   └── verify-stack.sh     # container verification suite (see "Verifying")
└── VERIFICATION-*.md       # recorded evidence from a real run
```

---

## Requirements

- Docker Engine with Compose v2 (`docker compose`, not `docker-compose`).
  Verified against **Docker Engine 29.1.3 / Compose 2.40.3** on Ubuntu 26.04
  (WSL2), also verified on **Docker 28.3.1 / Compose 2.38.1** (WP
  DEPLOY-FIX-3, including the `VAULT_CACHE_PATH` bind mode) — and, as of the 2026-08-17 packaging work package, `deploy/tests/
  verify-stack.sh` has now actually run against that real host: 105/109
  checks passed on the final run, across three total runs spanning two
  review rounds. **The 4 failures were a genuine pre-existing bug in step
  5i**, unrelated to the packaging work package that finally ran it for
  real: nginx's cache-event `access_log` uses `buffer=64k flush=5s`
  (`core/nginx/nginx.conf`), and step 5i grepped the log file immediately
  after the triggering request with no wait for that flush — an isolated
  repro confirmed the correct 9-field line appears once you wait past the
  5-second buffer (a fresh `docker run` of vault-core, one real MISS, and a
  check 7 s later shows the expected line every time; checking at 1 s does
  not). **Reproducible, not deterministic:** the same 4 lines failed on
  every run so far, but the pass/fail line was genuinely timing-dependent —
  a slower host could clear the 5 s window before the grep and pass by
  chance, so a green 5i by itself would not have proven the underlying bug
  was fixed. The feature itself was always correct; only the test's timing
  wasn't. **Fixed in WP 4g** (2026-08-18): step 5i now polls for the line
  with a bounded wait-for-line loop (up to 10 s) instead of reading
  immediately, so a green run means the flush-and-read path actually
  worked within budget — see `verify-stack.sh`'s comment above step 5i.
  Every check the packaging work package itself added, across both review
  rounds, passed on every run.
- Outbound internet (the cache fetches from the Steam CDN on a miss).
- Disk space for the cache. There is no eviction, ever — that is the
  project's whole point (`docs/PROJECT_PLAN.md` §3). You delete games
  explicitly via the API.

---

## Quickstart

```bash
cd deploy
cp .env.example .env
$EDITOR .env                      # set VAULT_API_KEY (only mandatory value)
docker compose pull               # fetch the published release images from ghcr.io
docker compose up -d              # no --build: run what was just pulled
```

**Pulling the published images is the default path.** Every `image:` line
in `compose.yaml` names `ghcr.io/steamhangar/<service>:${VAULT_IMAGE_TAG}`
(default: the release baked into this checkout, `0.1.0`), which is exactly
where `.github/workflows/publish.yml` pushes on a release tag -- so
`docker compose pull` fetches three images (`vault-core`, `vault-api`,
`vault-proxy`; four with `--profile dns`, which adds `vault-dns`;
`vault-runner` runs `vault-api`'s image with a different command, so it needs no image of its own) and `up -d` runs them
as published, multi-arch where the component supports it (`vault-api` is
amd64-only, see publish.yml's matrix comment). Set `VAULT_IMAGE_TAG` in
`.env` to pin a different release. No local toolchain, no build context.
A tag can be moved; a digest cannot. Each release body lists, under
"Verify this release", the exact `ghcr.io/steamhangar/<service>@sha256:...`
digests its workflow run pushed. To run exactly those bytes, pin them in a
`deploy/compose.override.yaml` (`vault-runner` runs the vault-api image,
so it gets the same digest):

```yaml
services:
  vault-core:   { image: "ghcr.io/steamhangar/vault-core@sha256:<from the release body>" }
  vault-api:    { image: "ghcr.io/steamhangar/vault-api@sha256:<from the release body>" }
  vault-runner: { image: "ghcr.io/steamhangar/vault-api@sha256:<from the release body>" }
  vault-proxy:  { image: "ghcr.io/steamhangar/vault-proxy@sha256:<from the release body>" }
  # with --profile dns only:
  # vault-dns:  { image: "ghcr.io/steamhangar/vault-dns@sha256:<from the release body>" }
```

or compare what a tag pull gave you against the list with
`docker inspect --format '{{index .RepoDigests 0}}' <image>`. The
published ghcr.io packages must be public: until the maintainer flips their
visibility, an unauthenticated pull fails -- build locally instead (below).

**Building locally instead** is the other supported path -- for a checkout
ahead of the latest release, a fork, or an offline host:

```bash
docker compose up -d --build      # builds the same three images (four with --profile dns) from this checkout
```

`--build` stores the results under the SAME `ghcr.io/steamhangar/...` tags
the `image:` lines name, which is deliberate: the compose file has one set
of image references, not one per deployment style, and a later
`docker compose pull` simply replaces the local builds with the published
layers for that tag. Do not mix the two casually on one host without
bumping `VAULT_IMAGE_TAG` -- the tag says which release, not where the
bytes came from.

Check it:

```bash
curl http://<server>/health                     # -> ok            (vault-core)
curl -I http://<server>/lancache-heartbeat      # -> X-LanCache-Processed-By: steamhangar
curl http://<server>:8080/v1/health             # -> {"status":"ok"}
curl -H "X-Api-Key: $VAULT_API_KEY" http://<server>:8080/v1/games
```

### Health and liveness at a glance

Every image carries its own `HEALTHCHECK`, so `docker compose ps` tells you the
truth without external tooling. Each probe was chosen to prove the thing that
actually matters for that service, not merely that a process exists:

| Service | Container `HEALTHCHECK` | Externally pollable | Proves |
|---|---|---|---|
| `vault-core` | `wget -q -O /dev/null http://127.0.0.1/health` | `GET http://<server>/health` → `ok` | nginx is up and serving. Local-only location: no Host allowlist entry needed, no upstream contact — a liveness probe, *not* an "is the internet reachable" probe |
| `vault-api`  | `python -c "urllib.request.urlopen('http://127.0.0.1:8080/v1/health')"` (no extra packages in the image) | `GET http://<server>:8080/v1/health` → `{"status":"ok"}` | the app is serving. The **one** unauthenticated route by design (`api/README.md` "Auth"): fixed body, no data, meant for exactly this |
| `vault-runner` | **disabled** (`deploy/compose.yaml`'s `healthcheck: disable: true`) | n/a | nothing over HTTP — this process never listens on a port at all, so inheriting the image's baked-in `/v1/health` probe unmodified would make `docker compose ps` show it permanently *unhealthy* despite working correctly. Liveness is instead proven by its own poll-tick log line (`docker compose logs vault-runner`, look for `"prefill_runner ... starting (poll every ...)"` right after start, and a `"claimed job ..."` line once something is actually handed off) — see `deploy/tests/verify-stack.sh`'s smoke check for the exact pattern |
| `vault-dns`  | `nslookup -type=a healthcheck.steamcontent.com 127.0.0.1` must answer `$CACHE_IP` | `dig +short A <any>.steamcontent.com @<server>` | the **redirect is live**, not just that dnsmasq is running — a resolver answering the wrong address would pass a process check and fail this one |

Interval 30 s, 3 retries; start period 5 s (core, dns) / 10 s (api).
`docs/PROJECT_PLAN.md` §10 designates `/v1/health` for external monitoring —
point your uptime checker at that one.

Then pick a DNS mode (below), and do the one-time SteamPrefill login.

`docker compose up` **refuses to start without `VAULT_API_KEY`** — that is
deliberate. There is no default API key anywhere in this project; a shipped
default is a shipped vulnerability.

---

## First run: the one-time SteamPrefill login

SteamPrefill needs a Steam session, created **once, interactively, by you** —
vault-api never sees, stores, transmits or logs Steam credentials (ADR-0004),
and no login ever happens during an image build.

**As of WP S-2 (ADR-0012 §5), this runs against the `vault-runner` container,
not `vault-api`.** This compose file ships `VAULT_PREFILL_MODE=queue`
(see the service table above): SteamPrefill's binary and its `Config/`
session directory now live in `vault-runner`, so that is where the
interactive login has to happen too — vault-api itself has nothing left to
log into. `vault-runner` is a long-running service (it is always polling for
handed-off jobs, same as every other container here), so this is `docker
compose exec` into the already-running container, not `compose run`:

```bash
cd deploy
docker compose up -d          # make sure vault-runner is actually running first
docker compose exec -it vault-runner \
    /opt/steamprefill/SteamPrefill select-apps
```

`docker compose exec` resolves `vault-runner` to whichever container is
actually running for this Compose project, so the command above works
regardless of project name. If you need the container's literal name for
some other tool (`docker exec` without going through Compose, log
aggregation, ...), `deploy/compose.yaml` gives it a stable one:
`<project>-vault-runner` — `steamhangar-vault-runner` for a default
deployment (the `name: steamhangar` at the top of `compose.yaml`):

```bash
docker exec -it steamhangar-vault-runner \
    /opt/steamprefill/SteamPrefill select-apps
```

Enter your account name, password and Steam Guard code when prompted, then
exit the app selector (vault-api overwrites the app selection per job anyway —
`Config/selectedAppsToPrefill.json` is how it tells SteamPrefill which app to
prefill, see `api/README.md`). The session lands in the `vault-steamprefill`
volume at `/opt/steamprefill/Config` and survives restarts and image upgrades.

**If you have set `VAULT_PREFILL_MODE=subprocess`** (reverting to the
pre-WP-S-2 shape, vault-api running SteamPrefill itself — see
`deploy/.env.example`): log in against `vault-api` instead, the same way
this section used to document:

```bash
docker compose run --rm --no-deps -it vault-api \
    /opt/steamprefill/SteamPrefill select-apps
```

Note that `vault-api`'s `Config/` volume mount was removed in WP S-2 (queue
mode has no use for it — see `deploy/compose.yaml`'s comment on that
service's volumes for the evidence), so this fallback command only produces
a persistent session if you also restore that mount; the supported path for
this compose file is the `vault-runner` login above.

Until you do this, everything else works — `/v1/games`, `/v1/mapping`,
`/v1/cache/*`, the cache itself — and only *prefill jobs* fail, with an
actionable message telling you to run the command above.

**Treat the `vault-steamprefill` volume as sensitive.** It holds a logged-in
Steam session.

---

## A container-specific trap: does SteamPrefill actually reach your cache?

This has only ever been proven **natively** (Phase 0, WP 1.7 — via a Windows
hosts-file entry) and never inside the container SteamPrefill actually ships
in here, which matters because the mechanism is different from every DNS mode
above — and, measured, matters in only ONE of the two `VAULT_CORE_BIND`
layouts, not both. Read to the end before adding anything to your setup.

### The real detection mechanism (four candidates, not one)

**As of WP S-2 (ADR-0012), SteamPrefill runs inside the `vault-runner`
container, not `vault-api`** — this whole section originally described
`vault-api`, back when it ran SteamPrefill itself; every command below now
targets `vault-runner` instead, and every finding in this section has been
RE-VERIFIED, not just find-and-replaced, against a real `vault-runner`
container on this same Compose network (same fixed-literal gateway result,
same `VAULT_CORE_BIND`-dependent trap, same fix — see the re-run evidence
inline below). If you are running the older `VAULT_PREFILL_MODE=subprocess`
fallback instead, substitute `vault-api` back in everywhere below; that path
still runs SteamPrefill exactly where this section originally described.

SteamPrefill does **not** simply trust the Windows client's hosts-file
hostname. Per its own source (confirmed by this project's own read,
`poc/steamprefill/PROTOCOL.md` §0 "SteamPrefill's cache-detection contract",
and independently confirmed by scanning the shipped SteamPrefill binary
itself for embedded strings), it tries, **in this order**, resolving each to
an RFC1918-or-loopback IPv4 address:

1. `lancache.steamcontent.com` (DNS — the same name the Windows client and
   vault-agent's hosts mode use)
2. `localhost`
3. **the fixed literal `172.17.0.1`** — the classic Docker default bridge's
   gateway address, hardcoded verbatim inside the binary (confirmed present
   as a UTF-16LE string in the shipped `.NET` executable; it is the only
   private IPv4 literal there matching SteamPrefill's documented candidate
   list — `127.0.0.1` also appears, as candidate 2 — and no
   `host.docker.internal`-style hostname appears at all). This
   is NOT SteamPrefill dynamically detecting "whatever this container's own
   gateway happens to be" — it is one specific, unconditional address.
4. the local machine's own hostname

For **each** candidate that resolves to a private/loopback IPv4, it sends
`GET http://<ip>/lancache-heartbeat` and accepts the candidate only if the
response carries `X-LanCache-Processed-By` — vault-core answers this at
`core/nginx/nginx.conf`'s `/lancache-heartbeat` location with `steamhangar`.
It stops at the first candidate that passes. If none does, SteamPrefill
quietly downloads straight from Valve instead: **the job still finishes and
reports success, and the cache stays empty.** No error, no red job status —
the same silent-failure shape requirement A12 is scoped to catch for
*client* traffic, except here it is vault-runner's own prefill traffic
bypassing itself.

### Whether this bites you depends entirely on `VAULT_CORE_BIND`

**Default layout (`VAULT_CORE_BIND` unset, i.e. `0.0.0.0`): candidate 3
already succeeds, DNS-independently, even though `172.17.0.1` is not
`vault-runner`'s own network's gateway.** `deploy/compose.yaml` puts every
service on its own Compose-managed bridge network (a DIFFERENT subnet from
the classic default bridge — `172.19.0.0/16` in one measured run, not
`172.17.0.0/16`), so `172.17.0.1` is not directly reachable the way a
same-network address would be. It works anyway, for a specific, checked
reason: Docker publishes vault-core's port 80 on **every** host interface
when bound to `0.0.0.0`, including the classic default bridge's own gateway
address `172.17.0.1` (that bridge always exists on a Docker host, used or
not). A packet from `vault-runner`'s container aimed at `172.17.0.1` leaves
via its own network's gateway, arrives at the HOST, and the host — which has
a direct, local route to `172.17.0.0/16` via its own `docker0` interface —
forwards it the rest of the way to vault-core's published port. This is
ordinary host-level routing between two of the host's own interfaces, not
container-to-container traffic crossing Docker's inter-network isolation
(which does block THAT).

**Re-measured for WP S-2** (this used to say `vault-api` throughout, back
when it ran SteamPrefill itself — this is a real re-run against
`vault-runner`, not a find-and-replace): a fresh `docker compose up` with
`VAULT_CORE_BIND`/`VAULT_CORE_PORT` left at their defaults, probing the
literal `172.17.0.1` from inside the real `vault-runner` container:

```
$ docker exec steamhangar-vault-runner python3 -c "import urllib.request as u; r=u.urlopen('http://172.17.0.1/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"
200 steamhangar
```

So in the layout `docker compose up` gives you out of the box, **there is
nothing to fix here** — candidate 1 (DNS) may well fail exactly as
`docs/PROJECT_PLAN.md`'s evidence note records, but candidate 3 catches it
DNS-independently before SteamPrefill ever falls back to Valve, as long as
the host's default bridge is up (it is, by default, on any Docker
installation) and nothing has firewalled inter-bridge host routing.

**The trap is real in the *other* layout: `VAULT_CORE_BIND` set to a
dedicated address** — the port-80-conflict recipe above, and exactly what
[the TrueNAS guide](examples/truenas-scale-dockge.md) instructs whenever
something else already owns port 80. Binding to one specific address means
Docker publishes port 80 **only** there — not on `172.17.0.1`, not on
loopback. Re-measured for WP S-2, same command, against `vault-runner` this
time, on a stack with `VAULT_CORE_BIND` set to a dedicated (in this
re-check, loopback) address instead of `0.0.0.0`:

```
$ docker exec steamhangar-vault-runner python3 -c "import urllib.request as u; r=u.urlopen('http://172.17.0.1/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"
[...]
urllib.error.URLError: <urlopen error [Errno 111] Connection refused>
```

(`[...]` above elides the Python traceback's middle frames for readability —
the meaningful line is the final `URLError`/`ConnectionRefusedError`; nothing
is hidden except stack-frame noise, and the exit still happened with no
response.) Candidate 3 refuses here exactly as it did for `vault-api` before
the split; candidate 2 (`127.0.0.1`/`localhost`) refuses too, for the same
reason it always did — it is `vault-runner`'s OWN loopback, never
vault-core's, regardless of which container SteamPrefill runs in. Candidate
1 (DNS) is your only remaining chance in this layout, and only if your
resolver rewrites the zone for the CONTAINER too (not a given — see the
earlier DNS section). If it doesn't, this is exactly where prefill jobs
silently fill nothing. (The third, LAN-address probe from the pre-split
version of this section — `http://192.168.1.50/lancache-heartbeat` —
is illustrative of your own dedicated `VAULT_CORE_BIND` value, not something
reproducible on a throwaway dev host with no such address; the mechanism is
identical to the fixed-literal probe just re-measured above once you
substitute your own address.)

### Check it

Probe the heartbeat directly, from inside `vault-runner` — a DNS lookup
answers the wrong question, since candidates 2–4 never involve DNS at all.
**`curl` and `ip` are not installed in the `vault-runner` image** (it's the
same `python:3.13-slim`-based image as `vault-api`, not vault-core's
nginx/Alpine one) — use `python3`, which is:

```bash
# the fixed-literal candidate (works out of the box on the default 0.0.0.0 bind):
docker compose exec vault-runner python3 -c "import urllib.request as u; r=u.urlopen('http://172.17.0.1/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"

# the address you actually bound VAULT_CORE_BIND to, if you set one:
docker compose exec vault-runner python3 -c "import urllib.request as u; r=u.urlopen('http://<VAULT_CORE_BIND value>/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"
```

A line printing `200 steamhangar` means that candidate works. A traceback
ending in `ConnectionRefusedError`/`URLError` means it doesn't — check the
next candidate down the list. If you're on the default `0.0.0.0` bind and
the first command already prints `200 steamhangar`, you are done — skip the
fix below. (If you'd rather probe from vault-core's own shell instead,
vault-core's Alpine/nginx image does have `curl` — but that checks
vault-core's OWN reachability of an address, a related but different
question from what `vault-runner` can reach; the commands above check the
right container. If you are running `VAULT_PREFILL_MODE=subprocess` instead
of the shipped `queue` default, substitute `vault-api` back in — that is
where SteamPrefill runs in that mode.)

### Fix it (only needed with a dedicated `VAULT_CORE_BIND`)

Two options, neither baked into `compose.yaml` by default (a wrong default
here would break setups where the container already finds the cache without
it):

1. **Preferred, DNS-independent, and confirmed sufficient on its own:**
   pin `lancache.steamcontent.com` directly via `extra_hosts` on
   `vault-runner` only (the container that actually resolves it, in the
   shipped `queue` mode — `vault-api` in `subprocess` mode instead), in a
   `deploy/compose.override.yaml` you create yourself:
   ```yaml
   services:
     vault-runner:
       extra_hosts:
         - "lancache.steamcontent.com:192.168.1.50"   # vault-core's own address
   ```
   **The value must be a plain private IPv4 address, not a hostname** —
   SteamPrefill's own resolution step requires an RFC1918-or-loopback IPv4
   before it ever sends the heartbeat probe, so anything else (a hostname, an
   IPv6 literal) is rejected before it gets that far. Pinning only this one
   name is enough: once cache detection succeeds, SteamPrefill uses the
   resolved IP for every subsequent depot request too (confirmed in this
   project's own testing, WP 0.4: 1272 chunks prefilled through a single
   hosts entry), and vault-core accepts requests under the real CDN Host
   header regardless of which address they arrived on — depot hostnames
   themselves never need to resolve to the cache.
   Then `docker compose -f compose.yaml -f compose.override.yaml up -d`.
2. **Alternative:** point the container's own resolver at your LAN's
   rewriting DNS server instead:
   ```yaml
   services:
     vault-runner:
       dns:
         - 192.168.1.50   # your AdGuard Home / Pi-hole / vault-dns address
   ```
   Caveat: this makes vault-runner resolve *everything* it looks up through
   that resolver too — for this container that is just Steam's own CM/CDN
   hostnames during login and depot fetches (WP S-2: vault-runner never
   makes a manifest-oracle or webhook request, unlike vault-api — those stay
   vault-api-side regardless of `VAULT_PREFILL_MODE`, so this caveat is
   narrower here than it is for a `vault-api`-targeted override under
   `subprocess` mode). The `extra_hosts` route above changes nothing except
   this one hostname either way, which is why it is the preferred fix.

Re-run the heartbeat probe above after either change to confirm it actually
took.

---

## DNS: pick one of three modes

The Steam client has to be told to fetch from your cache instead of Valve's
CDN. `docs/PROJECT_PLAN.md` §10 lists three ways; **`dns/README.md` has
copy-paste instructions for each** — read it, it is the most consequential
configuration decision in this project.

1. **You already run a local DNS server** (AdGuard Home, Pi-hole, dnsmasq,
   Unbound) — *recommended*. Add the rewrite there. One less container.
2. **Bundled `vault-dns`** — for LANs with no DNS server of their own:
   ```bash
   # in .env:
   #   CACHE_IP=192.168.1.50        <- the LAN IP of THIS host
   #   VAULT_DNS_BIND=192.168.1.50  <- publish :53 on that LAN IP only
   docker compose --profile dns up -d
   ```
   Then point your router's DHCP-advertised DNS server at that address.
3. **Hosts-file mode** — a single Windows gaming PC, no DNS server involved.

Whichever you pick: **the AAAA record must be handled too.** If your resolver
answers `AAAA` for `*.steamcontent.com` with Valve's real IPv6 address,
IPv6-capable clients silently bypass the cache entirely — no error, no log
entry, the cache just never gets used. `vault-dns` closes this by design
(`address=` paired with `local=`, ADR-0001 req 6, verified live in the
transcript in this directory). For modes 1 and 3 it is on you; `dns/README.md`
shows exactly what to add and how to verify it with `dig`.

> `VAULT_RESOLVER` (vault-core's own upstream resolver) must **never** point at
> **any** resolver that rewrites `*.steamcontent.com` to this cache. `vault-dns`
> is the obvious case — it answers `*.steamcontent.com` with vault-core's own
> address by design — but the identical failure hits an **AdGuard Home or
> Pi-hole instance running on this same host** if you configured the
> `*.steamcontent.com` rewrite there instead (mode 1 above; a very common
> homelab layout — AdGuard Home/Pi-hole and this stack side by side on a NAS
> or a small server). Point `VAULT_RESOLVER` at that resolver and vault-core
> would resolve Valve's CDN names to its own address: `40-vault-preflight.sh`
> then refuses to boot (it probes the first resolver in `VAULT_RESOLVER` for a Steam CDN name and
> stops on a private answer), and should the resolver start rewriting after
> boot, every cache MISS is answered `508 Loop Detected` after one hop with
> nothing cached — the cache stops filling, it does not hang.
> `deploy/.env.example` carries the same warning next to the setting itself.

**Router port-53 DNAT.** If your router transparently redirects all port-53
traffic (DNAT) to a Pi-hole/AdGuard that rewrites `*.steamcontent.com` to
vault-core, vault-core's own upstream lookups are rewritten too and every
cache MISS would be proxied back into itself. vault-core detects this two
ways: at boot, `40-vault-preflight.sh` refuses to start if `VAULT_RESOLVER`
answers a Steam CDN name with a private address, and at runtime any request
that comes back carrying its own `X-SteamHangar-Hop` header is answered `508
Loop Detected` after one hop, with nothing cached. The fix is to exempt the
vault-core host from the router's port-53 redirect, or to point
`VAULT_RESOLVER` at a resolver that answers Steam's CDN names truthfully.

**What `/depot/` accepts.** Depot requests are GET-only: any other method,
including `HEAD`, is answered `405` locally and never relayed to Valve. A URI
ending in `/` is answered `404` locally before any cache lookup (no real depot
object URI ends in `/`). Use `GET` when probing the cache by hand, e.g.
`curl -s -o /dev/null -w '%{http_code}\n' http://<cache>/depot/...` rather
than `curl -I`.

---

## Port 80 and the dedicated-IP question

vault-core **must** answer on port 80. Steam CDN traffic is plain HTTP and the
Steam client only ever asks for port 80 — a cache on another port is a cache
nothing uses. `VAULT_CORE_PORT` exists for testing, not as a way out of a port
clash.

If something else on this host already owns port 80 (another reverse proxy, a
web UI), give SteamHangar **its own address** instead
(`docs/PROJECT_PLAN.md` §10):

```bash
# IP alias on the existing NIC (persist it the way your distro does)
sudo ip addr add 192.168.1.50/24 dev eth0

# deploy/.env
VAULT_CORE_BIND=192.168.1.50
```

…then point your DNS rewrite (or `CACHE_IP`) at `192.168.1.50`. A macvlan
network or a dedicated VLAN interface works equally well.

---

## Volumes and backup

Three named volumes, created automatically (a fourth location,
`vault-cache`, becomes a bind mount instead if `VAULT_CACHE_PATH` is set --
see ["Using a dedicated cache mount"](#using-a-dedicated-cache-mount) above):

| Volume               | Mounted at                 | Contains | Back up? |
|----------------------|----------------------------|----------|----------|
| `vault-cache` (or `VAULT_CACHE_PATH` if set) | `/vault` in vault-core, vault-api **and** vault-runner (WP S-2) | the depot cache (`cache/depot/…`) plus nginx's `tmp/` | **No** — it is a cache; large, and re-fillable by prefilling again |
| `vault-db`           | `/data` in **both** vault-api and vault-runner (WP S-2) | `vault.db` — depot→app mapping, jobs, agent reports | **Yes** — small, and it is the knowledge the cache cannot rebuild |
| `vault-steamprefill` | `/opt/steamprefill/Config` in **vault-runner** (moved here from vault-api in WP S-2, ADR-0012 §5 — see "First run" above) | SteamPrefill's Steam **session** and selection state | **Yes**, and treat it as a secret |
| `vault-steamprefill-home` | `/opt/steamprefill/home` in **both** vault-api and vault-runner (WP S-2 — see `deploy/compose.yaml`'s comment on this mount for why vault-api still needs it even though SteamPrefill itself now runs in vault-runner: manifest ingestion stays vault-api-side and reads the `.cache/SteamPrefill/v1` files vault-runner writes under this same shared directory) | `HOME` for the container user — SteamPrefill's manifest/depot cache | **No** — regenerable, and it grows |

```bash
# back up the two small ones
docker run --rm -v steamhangar_vault-db:/data:ro \
                -v steamhangar_vault-steamprefill:/cfg:ro \
                -v "$PWD:/backup" alpine:3.23.5 \
                tar czf /backup/steamhangar-state-$(date +%F).tar.gz /data /cfg
```

### Why SteamPrefill gets a HOME volume

SteamPrefill creates a directory under `$HOME` in a **static constructor**,
before it parses a single argument. The usual service-account idiom
(`--home-dir /nonexistent`) therefore does not merely break login — it kills
every invocation, including prefill jobs, with a
`TypeInitializationException` and no useful message. That was a real defect in
this package's first build, caught in review and fixed by giving uid 101 a
genuine home in both the passwd entry and `ENV HOME`, backed by its own volume
so the cache it builds there survives restarts.

Two practical consequences:

- **Don't override `HOME`** for `vault-api` OR `vault-runner` in `.env` or an
  override file, and don't drop the `vault-steamprefill-home` mount from
  EITHER service (WP S-2: it is shared between them now, not vault-api-only —
  see the volumes table above). The image asserts both definitions agree at
  build time, and `deploy/tests/verify-stack.sh` re-checks it plus a
  credential-free SteamPrefill smoke run on all three invocation paths.
- **It is safe to delete this volume** to reclaim space; SteamPrefill rebuilds
  it. Deleting `vault-steamprefill` instead logs you out — that is the one you
  back up.

### One volume for cache/ and tmp/ — not negotiable

`vault-cache` is mounted as a **single** volume at `/vault`, containing both
`cache/` and `tmp/`. nginx's `proxy_store` finishes every cached object by
`rename()`-ing it out of `tmp/` into `cache/depot/…`, which is atomic only
within one filesystem; split across two mounts it degrades to a full copy —
slower, and briefly double the disk usage per chunk
(`core/README.md`, "Same-filesystem requirement").

You do not have to remember this: vault-core compares the two directories'
`st_dev` at every start and **refuses to boot** if they differ.

### Using a dedicated cache mount

To put the cache on a specific disk -- a second drive, a NAS's own storage
pool, anything other than wherever Docker keeps its named volumes -- set
`VAULT_CACHE_PATH` in `deploy/.env` (`.env.example` documents it in full).
No `compose.yaml` edit needed: both services' `/vault` mount source is
already driven by this variable, and Compose's volume short-syntax resolves
a bare name (the default, unset case) as the named volume declared under
`volumes:` and an absolute path as a bind mount to that path instead -- so
leaving the variable unset (or blank) renders exactly what these lines
always were.

```bash
# deploy/.env
VAULT_CACHE_PATH=/srv/steamhangar-cache
```

Prepare the directory **before the first start**: `<path>/cache/depot` and
`<path>/tmp` must both exist and be owned by `101:101`. A bind mount, unlike
a fresh named volume, does not get seeded with the image's pre-created
`cache/depot/` and `tmp/` (that seeding only happens for an empty named
volume; see `core/Dockerfile`'s `VOLUME ["/vault"]` step). Skipping this
step is not silent: vault-core's preflight will refuse to start with
`/vault/cache is missing`.

In the default named-volume mode only vault-core seeds the volume: vault-api
mounts it with `nocopy`, because two containers created at the same moment
on a fresh volume would otherwise race on Docker's copy-up and one fails
with `mkdir .../_data/tmp: file exists`. In bind-mount mode there is no
copy-up, so vault-api's line drops `nocopy` by itself when `VAULT_CACHE_PATH`
is set. That is a requirement, not tidiness: Docker 28 (observed 28.3.1)
refuses a bind that carries it (`invalid mount config for type "bind": field VolumeOptions must
not be specified`). If you see that error, your `compose.yaml` predates this
fix; update it.

```bash
sudo mkdir -p /srv/steamhangar-cache/cache/depot /srv/steamhangar-cache/tmp
sudo chown -R 101:101 /srv/steamhangar-cache
```

**uid/gid 101 is required, not a suggestion.** It is the numeric identity of
the nginx image's worker user, and vault-api's container user is created with
the same numbers so both services can write the shared cache. Named volumes
get this right automatically; bind mounts do not. If you get it wrong,
vault-core refuses to start and tells you the exact `chown` to run.

**`VAULT_CACHE_PATH` must be an absolute path** (start with `/`). Compose
treats `./` and `~/` paths as binds too, but relative to the compose
project directory or your home, which is rarely what you mean here; anything
else (a bare name) is a *named-volume reference* rather than a bind path;
since only `vault-cache` is declared under the top-level `volumes:` key, a
typo here fails loudly at `docker compose config`/`up` time (`refers to
undefined volume ...: invalid compose project`), not silently.

It always covers `cache/` **and** `tmp/` together, because it redirects the
single `/vault` mount point both services already share -- the
same-filesystem requirement below is only satisfiable by moving both at
once, and there is no way to move just one with this variable.

**TrueNAS SCALE + Dockge users:** `deploy/examples/truenas-scale-dockge.md`
has the full recipe for putting this on a dedicated ZFS dataset, including
`recordsize`/`atime`/`compression` reasoning specific to Steam depot chunks
and the port-80/DNS gotchas that come up on a NAS specifically.

---

## Phase-3 knobs: cache-event log and garbage collection

Four Phase-3 settings, documented in full in `.env.example`:

| Variable               | Required | Default                                    | Purpose                                                            |
|-------------------------|----------|---------------------------------------------|---------------------------------------------------------------------|
| `VAULT_EVENT_LOG`       | no       | `/vault/logs/event.log` (**on** by default as of the 2026-08-17 packaging WP) | vault-core: path to the machine-readable cache-event log (WP 3.10, ADR-0008) — its WRITE side |
| `VAULT_EVENT_LOG_PATH`  | no       | `/vault/logs/event.log` (**on** by default, same WP) | vault-api: the SAME path — its READ side. WP 3.11's sweeper tails it to drive miss-triggered prefill completion, per-client hit stats and bypass detection (requirement A12). Must equal `VAULT_EVENT_LOG` above |
| `VAULT_GC_GRACE_DAYS`   | no       | `14`         | vault-api: days a freshly stored chunk is protected from garbage collection purely by its own store time (protects beta-branch/demo content GC cannot otherwise see); `0` disables the window. See `api/README.md` "The recently-stored grace window" |
| `VAULT_AUTO_GC`         | no       | `execute` (**since WP SWEEP-1, 2026-08-22 — was `off` through WP 3.12**) | vault-api: `off` \| `dry-run` \| `execute` — automatically queue a GC job after a prefill that actually updated something. See `api/README.md` "Auto-GC" |

A fifth, `VAULT_MANIFEST_ORACLE` (WP 3.9), stays off by default and is
**not** in this table on purpose — see `.env.example`'s privacy note before
touching it: enabling it sends outbound queries to a third party.

**A sixth, newly forwarded here by WP SWEEP-1: `VAULT_SWEEP_INCLUDE_CACHED`
(WP 4d) now also passes through, defaulting to `true`, paired with
`VAULT_AUTO_GC`'s own default above flipping to `execute` in the same
change (operator decision, 2026-08-22 — see
`docs/adr/0014-sweep-cached-and-auto-gc-default-on.md`).** It is a Phase 4d
setting, not Phase 3, and it stays DB-overridable at runtime via `PATCH
/v1/settings` exactly as before (ADR-0009) — this is only a NEW env
fallback path, added specifically so a `VAULT_SETTINGS_READONLY=1`
deployment (which refuses every `PATCH`) has a way to turn the now-default-on
cached sweep back off at all. **The same readonly argument now covers every
settings-API key** (pre-freeze project review, finding S3): `VAULT_NAME`,
`VAULT_SCHEDULE_INTERVAL_MINUTES`, `VAULT_SCHEDULE_CLIENT_STALE_DAYS`,
`VAULT_WEBHOOK_URL` and `VAULT_WEBHOOK_EVENTS` were the five keys
`compose.yaml` still left unforwarded on the "PATCH is the supported path"
reasoning, which left a hard-locked deployment with no way to name its
vault, tune the sweep cadence or configure a webhook at all. All five now
pass through (no-colon form, empty default — unset or blank both leave
vault-api on its own built-in default, nothing compose-side to drift) and
have stanzas in `.env.example` under "Settings-API keys reachable from this
file"; `PATCH /v1/settings` still wins over them on a read-write deployment
(db > env > default). `api/README.md`'s "Sweep target set" section
has the full cost model and the auto-GC coupling. To keep the exact
pre-2026-08-22 behavior, set all three (see the seventh/eighth note just
below for why the window line is required too — without it the scheduler
still runs a plain installed-only sweep every night, which is not what
"pre-2026-08-22" means):
```
VAULT_SWEEP_INCLUDE_CACHED=false
VAULT_AUTO_GC=off
VAULT_SCHEDULE_WINDOW=
```

**A seventh and eighth, also newly forwarded here as a WP SWEEP-1 follow-up
(review round S3 finding, operator decision): `VAULT_SCHEDULE_WINDOW`
(default `03:00-07:00`) and `TZ` (default `UTC`).** Without a window, the
scheduler thread never sweeps at all — the sixth note's pairing above
needs the scheduler actually RUNNING to mean anything, and no version of
this file forwarded a window before this. `03:00-07:00` is a suggested
quiet-hours default, not a claim about any specific operator's actual
schedule, and it is measured against `TZ` — `UTC` unless you also set that,
deliberately not a guessed populated zone (see `deploy/compose.yaml`'s own
comment on the `TZ` key for the full argument against guessing). A wrong
or absent `TZ` is made visible, not silent: vault-api logs one line on its
first tick naming the requested `TZ` value, the resolved zone/offset, and
the next window opening in both local and UTC time (`api/README.md`
"Timezone"). **The blank-disables-it path needs the exact right Compose
syntax to actually work, and review round 2 caught a real bug here**: the
no-colon substitution form (`${VAULT_SCHEDULE_WINDOW-03:00-07:00}`, no
colon before the dash) is required for `VAULT_SCHEDULE_WINDOW=` (present,
blank) to actually disable the scheduler — the more common colon form
(`${VAR:-default}`) substitutes the default for a blank value too, which
would have silently kept the scheduler running for anyone following the
recipe above. `deploy/compose.yaml` uses the no-colon form for exactly this
reason; `TZ` deliberately keeps the colon form (blank and `UTC` are the
same thing for that one variable, so the distinction does not matter
there). Still DB-overridable at runtime via `PATCH /v1/settings`
(`schedule_window`, ADR-0009) exactly as before. A window changed that way
moves the scheduler only, not the upstream rate cap's window, which stays
env-only (see "Upstream rate cap" below).

**The cache-event log is now the feed for a real feature, and needs no extra
volume.** `VAULT_EVENT_LOG` writes into `/vault/logs/`, which lives on the
exact same `/vault` volume `cache/` and `tmp/` already share —
`core/Dockerfile` pre-creates it there. vault-api's matching
`VAULT_EVENT_LOG_PATH` reads from that identical volume (see the "Volumes"
table above) with zero extra `compose.yaml` wiring. It now feeds WP 3.11's
sweeper: miss-triggered prefill completion (a cache miss on an
unknown/partial app queues a prefill job for it), per-client hit statistics,
and bypass detection (`GET /v1/clients`, `GET /v1/stats`) — this was
groundwork with no consumer through WP 3.10, and stayed off-by-default
UNTIL the packaging work package that closed the actual
`deploy/compose.yaml` forwarding gap (`VAULT_EVENT_LOG_PATH` existed in
`config.py` since WP 3.11 but was never wired into vault-api's
`environment:` block, so the whole feature was unreachable in the shipped
stack even though the code was correct — see `docs/LEARNINGS.md`
"Containers"). To turn it back off, set BOTH variables to empty in `.env` —
`.env.example` has the exact wording. It genuinely grows over time (one TSV
line per request); `.env.example` also states plainly that vault-api can
read the file but not truncate it, so rotation is on the operator.

**Turning on auto-GC deletes files automatically once you pick `execute`.**
Start with `dry-run` and read a few job logs (`GET /v1/jobs/{id}`) before
trusting `execute` on a deployment you care about — `api/README.md` "Auto-GC"
has the full decision tree for when it fires.

---

## Upstream rate cap

Optional, off by default (WP TH-1a/TH-1b,
[ADR-0015](../docs/adr/0015-upstream-rate-cap.md)). Caps how fast vault-core
downloads cache MISSes from Steam; HITs keep serving the LAN at full speed.

```bash
# deploy/.env
VAULT_UPSTREAM_RATE=800k          # ONE aggregate limit, bytes/s; empty = no cap
#VAULT_UPSTREAM_RATE_WINDOW=      # see below
```

- **`VAULT_UPSTREAM_RATE`**: nginx size syntax, digits with an optional
  `k` (x1024) or `m` (x1048576). `800k` is 819,200 B/s (about 6.5 Mbit/s),
  not 800,000. Empty = off. The total is divided at download time by the
  number of requests vault-core is serving.
- **`VAULT_UPSTREAM_RATE_WINDOW`**: `HH:MM-HH:MM`, the
  `VAULT_SCHEDULE_WINDOW` grammar. Full speed **inside** the window, capped
  outside. Unset (the line commented out) = it follows
  `VAULT_SCHEDULE_WINDOW`, default `03:00-07:00`, so the cap lifts while
  the scheduler runs. Set it explicitly blank (`VAULT_UPSTREAM_RATE_WINDOW=`)
  to cap around the clock. Only the env window is followed, not one stored
  via `PATCH /v1/settings`. `VAULT_SCHEDULE_WINDOW=` (blank, scheduler
  off) together with a set rate and this line unset also means the cap
  applies around the clock.
- **Time zone:** the window is evaluated in vault-core's local time, which
  is `TZ` (forwarded to vault-core too, default `UTC`). Set `TZ` once in
  `.env` and the scheduler and the cap agree.
- **An invalid value refuses to boot.** vault-core stops with
  `27-vault-upstream-rate.sh: FATAL` rather than run uncapped by accident
  (`docker compose logs vault-core`).
- **Recreate vault-core after changing either variable**; the cap is baked
  at container start:
  `docker compose up -d --force-recreate vault-core`.
- **Zero-code alternative:** per-device QoS on your router throttles the
  same Steam-facing direction and needs no SteamHangar setting at all.

The cap is per request, divided by the live count, and has stated limits
(HITs in flight dilute the share and leave WAN bandwidth unused, the LAN
client that triggers a MISS is slowed too, the window edge stops nothing): see
[`core/README.md` "Upstream rate cap"](../core/README.md).
It is a bandwidth cap, not a request limit or DoS control.

### Prefill concurrency behind a carrier-grade NAT

Separate from the bandwidth cap: `VAULT_PREFILL_MAX_THREADS` (default `8`,
whole number 1-64) sets how many chunk requests one SteamPrefill run keeps
in flight. vault-api passes it as SteamPrefill's hidden `--max-threads`
flag on every prefill (SteamPrefill's own default is 30). Each chunk
vault-core fetches is a new upstream connection, so on a line behind a
carrier-grade NAT (DS-Lite and similar) too many at once exhaust the NAT's
port mappings, and vault-core logs `connect() failed (113: Host is
unreachable)` (see [Troubleshooting](#troubleshooting)). Forwarded to
vault-api and vault-runner; recreate both after a change:
`docker compose up -d vault-api vault-runner`. An invalid value refuses to
boot both. Details: [`api/README.md` "SteamPrefill concurrency"](../api/README.md).

---

## Logs and rotation

All three containers log to stdout/stderr, so `docker compose logs -f` is the
single place to look, and **rotation is the json-file driver's job**:

```yaml
logging:
  driver: json-file
  options: { max-size: "10m", max-file: "5" }
```

That is ~50 MB per service worst case, enforced by the Docker daemon — no
logrotate, no cron job, no `SIGUSR1` reopen dance, and nothing unbounded inside
a container. (This closes the "log rotation is documented but not implemented"
caveat `core/README.md` left open for this work package; the logrotate sketch
there is superseded by this.)

Tune with `VAULT_LOG_MAX_SIZE` / `VAULT_LOG_MAX_FILE` in `.env`. vault-core
writes **one line per depot request**, so a large prefill can churn through
10 MB quickly — raise `max-file` if you want that history to survive.

`vault-dns` deliberately does **not** log queries. It is your LAN's forwarding
resolver for *every* domain, so query logging would record full
browsing-metadata-level history for every device. See `dns/README.md`
("Privacy note") for how to enable it temporarily when debugging.

---

## Upgrading

Running the published images (the Quickstart default):

```bash
cd deploy
git pull                          # picks up compose.yaml/.env.example changes
docker compose pull               # fetches the release VAULT_IMAGE_TAG now resolves to
docker compose up -d              # recreates only the containers whose image changed
```

`git pull` moves the `image:` lines' baked-in default tag to the new
release; an explicit `VAULT_IMAGE_TAG=` in your `.env` overrides that and
must be bumped by hand. Building locally instead:

```bash
cd deploy
git pull
docker compose up -d --build
```

**Newly-enforced `.env` keys (2026-08-17 packaging work package).** A dozen
settings that Compose used to silently drop — set them in `.env` and nothing
happened, no error, no effect — are forwarded and validated now (the full
list is in `.env.example`'s upgrade note and `docs/PROJECT_PLAN.md` §7
Phase 5). If your existing `.env` already has a stale or malformed value for
one of them, it was harmless before this upgrade and becomes vault-api
refusing to start, with an explicit error naming the bad key, after it.
Check `docker compose logs vault-api` for exactly that message if a
previously-working `.env` suddenly fails to start post-upgrade.

**Database schema.** vault-api creates and upgrades its schema itself at
startup (`init_db`, `api/README.md` "Database schema"). Every change so far is
additive and applied with `CREATE … IF NOT EXISTS`, so a newer image simply
brings the existing `vault.db` up to date and records the new
`schema_version`. There is nothing to run by hand.

**Rolling back is the direction that bites.** If a database has been upgraded
to `schema_version` N and you then start an *older* image that only knows
N-1, vault-api raises `RuntimeError` and refuses to start rather than operate
on a schema it does not understand. That is intentional (silent data damage is
worse than a failed start), but it means: **back up the `vault-db` volume
before an upgrade** if you might want to roll back.

**Do not scale vault-api.** Exactly one process may own the database: it runs a
single job worker and, at startup, fails any job still marked `running` as a
crash orphan — a second instance would kill the first one's live prefill
(`api/README.md` "Worker lifecycle"). `docker compose up --scale vault-api=2`
is not supported.

**`vault-runner` is a different story (WP S-2, ADR-0012 §3):** its atomic
claim (`jobs.claim_run`'s `BEGIN IMMEDIATE` compare-and-swap plus a
`WHERE run_claimed_by IS NULL` guard, TWO independent mechanisms either
alone sufficient) is measured safe under real concurrent OS processes racing
to claim the same job — the ADR's own review round re-ran it 8-way and got
exactly one winner every time. `docker compose up --scale vault-runner=2` is
not a documented or tested deployment shape for this compose file. Measured
directly (review round 3, Compose 2.40.3, re-checked after an earlier
piped measurement mis-reported the exit code as 0 — the pipe's own last
command was what was actually being checked, not `docker compose`):
`docker compose up -d --scale vault-runner=2 vault-runner` **exits 1**. It
prints `WARNING: The "vault-runner" service is using the custom container
name "<name>" ... Remove the custom name to scale the service`, creates no
`vault-runner` container at all (not one, not two), and — because this
form names `vault-runner` as the target, pulling in only `vault-api` as its
`depends_on` dependency — `vault-api` itself gets no further than `Created`
either; nothing in this form reaches a running state. The underlying claim
mechanism is not the reason not to scale it, either way — if you have a
real multi-runner use case, drop the
`container_name` override in your own `compose.override.yaml` first.

Bumping a base image or the pinned SteamPrefill release is a deliberate edit to
the relevant `Dockerfile` (tag **and** digest together) — nothing here tracks
a floating tag.

---

## Egress lock: vault-api loses its default route out

WP EG-1 (ADR-0011). `vault-api`'s own container has no default route for
an **arbitrary** outbound connection — not to Valve, not to the manifest
oracle, not to a webhook receiver on a genuinely separate device, LAN or
WAN. Reaching such a destination requires passing through a new
`vault-proxy` container, which refuses any destination not on an
allowlist. This is **on by default** in this compose file — there is no
environment variable that turns it off (see "Removing the lock" below for
the supported, deliberately non-trivial way to do that anyway).

**Read "Two channels this does NOT close" below before treating this as
"vault-api cannot reach the internet."** It cannot reach an arbitrary
destination — that is the real, useful guarantee — but DNS resolution and
the Docker host's own reachable addresses are different questions, with
different (and open) answers.

### The mechanism, in the fewest possible lines

`deploy/compose.yaml`'s own top-level `networks:` block states this
explicitly as a banner comment — read that first; this section explains it
in prose and gives you a way to check it yourself, rather than repeating
it:

1. `vault-api` is attached to `vault-lan` (a network with Docker's outbound
   NAT/masquerade turned OFF) and `vault-egress` (`internal: true` — no
   route out of it exists at all, to anything). It is attached to nothing
   else — no `default` network.
2. `vault-egress` is shared with exactly one other container: `vault-proxy`.
   `vault-api`'s `HTTP_PROXY`/`HTTPS_PROXY` environment variables point at
   it by name.
3. `vault-proxy` is also attached to `default` (an ordinary, masquerading
   network) — its own real route to Valve, the oracle, or a webhook
   receiver. It refuses every destination that is not in a filter file
   rendered from `VAULT_EGRESS_ALLOW` (`deploy/proxy/docker-entrypoint.sh`)
   plus one host baked into its image unconditionally: `api.steampowered.com`,
   for the Steam Web API relay (see that script's own comment for why this
   one host is not gated behind the variable).

Nothing in `vault-api`'s own Python code changed to make this work — it
never needed to. `steam_relay.py`, `oracle.py` and `webhooks.py` all use
Python's standard `urllib`, which already honours `HTTP_PROXY`/
`HTTPS_PROXY` on its own; the lock is enforced entirely by the network
topology above, underneath any code that container runs.

### Two channels this does NOT close

Measured, not theoretical, and not proposed to be fixed here — read
`docs/adr/0011-egress-lock.md`'s "What this ADR does NOT claim to defend
against" for the full reasoning behind leaving both open:

- **DNS resolution.** A lookup from inside `vault-api` still reaches the
  real internet: Docker's embedded resolver answers from the HOST's own
  network namespace, not the container's, so `vault-lan`'s disabled
  masquerade is simply irrelevant to it. A process that controls what
  hostname it looks up controls what data leaves inside that name (one
  DNS label comfortably holds a 32-character Steam key). Closing this
  would mean removing vault-api's own name resolution entirely, which is
  not attempted here.
- **The Docker host's own reachable addresses.** A raw connection from
  `vault-api` straight to the Docker host's non-loopback address (bypassing
  `HTTP_PROXY` entirely) reaches a real listener there — a reply from the
  host to a container needs no masquerade, so the disabled-masquerade rule
  never applies to it. In this stack, that means `vault-core:80`
  specifically (its `0.0.0.0` bind is deliberate, see that service's own
  section above), and anything else this same host has published the
  ordinary way.

Neither of these is "vault-api can reach an arbitrary WAN or LAN device" —
that specific claim is what the lock actually makes false, and step 1
below still measures exactly that. They are narrower, real exceptions
worth knowing about before assuming the lock means more than it does.

### What still needs `VAULT_EGRESS_ALLOW`

Two real cases, both documented in `deploy/.env.example`:

- **The manifest oracle** (`VAULT_MANIFEST_ORACLE`). Turn it on without
  adding its host here and `vault-api` **refuses to boot**, naming the
  missing host — this is deliberate; the alternative is a silent, permanent
  filtered-403 on every oracle query with nothing pointing back at the
  cause.
- **Webhooks** (`VAULT_WEBHOOK_URL`, set via `PATCH /v1/settings`). If your
  webhook stops firing after upgrading to this version, **that is the lock
  working, not a bug** — add the receiver's host to `VAULT_EGRESS_ALLOW`
  and restart `vault-api`/`vault-proxy`. This applies identically whether
  the receiver is on your own LAN (a local ntfy/Home Assistant instance) or
  on the internet: measured directly, `vault-api`'s own network has **no
  working direct route to an arbitrary device** once this lock is in
  effect — not just WAN addresses, other LAN devices too (Docker's
  masquerade-disable is a blanket "leaving this bridge" rule with no
  destination-based exception). There is no "skip the proxy for local
  traffic" shortcut to configure; every outbound call to a separate device
  goes through `vault-proxy`, or it does not go out at all. **One real
  exception, named above, not hidden:** a receiver bound to the Docker
  HOST's own address (rather than a separate device) is reachable directly
  regardless of any of this — see "Two channels this does NOT close".

### Running two stacks on one host

`vault-egress` has a fixed subnet, `172.30.238.0/24` by default, so that
`vault-proxy` can admit exactly that range. A Compose project name keeps
container, volume and network *names* apart, but not subnets: a second
SteamHangar stack on the same host (a test instance next to production, say),
or anything else already holding that range, makes `docker compose up -d`
fail with "Pool overlaps with other one on this address space". Give the
second stack its own private /24 in its `deploy/.env`, for example
`VAULT_EGRESS_SUBNET=172.30.239.0/24`, and pick one that overlaps no
existing Docker network on the host. Compose uses the value for the network
and forwards it to `vault-proxy`, which renders its client `Allow` line from
it at every start. `vault-proxy` accepts only a strict IPv4 CIDR (prefix
8-30, host bits zero) and refuses to start on anything else; a blank value
in `.env` means the default (compose substitutes it).
`deploy/tests/verify-stack.sh` runs on `172.30.239.0/24` for this
reason, so do not give a long-lived stack that range if you run the suite
on the same host.

### Verify it yourself in five minutes

Trust none of the words above — check the running containers directly.

**1. Inbound still works; outbound direct does not (from `vault-api` itself).**

```bash
# From vault-api's own container: a real destination, no proxy involved.
# Expect this to HANG until it times out -- that is the pass condition.
docker compose exec vault-api sh -c 'curl -v --max-time 8 --noproxy "*" https://1.1.1.1/'

# The same request, letting the container's own HTTP_PROXY/HTTPS_PROXY
# apply (the default -- no --noproxy flag). Expect a real response from
# whichever allowlisted host you point this at instead; example.com is not
# allowlisted by default, so expect "403 Filtered" for it specifically:
docker compose exec vault-api sh -c 'curl -v --max-time 8 https://example.com/'
```

See exactly what the proxy enforces right now. Both files are rendered at
every start into `/run/tinyproxy/` from a root-owned, read-only template in
`/etc/tinyproxy/`, which the proxy itself cannot change:

```bash
docker compose exec vault-proxy cat /run/tinyproxy/filter
docker compose exec vault-proxy grep -i '^allow' /run/tinyproxy/tinyproxy.conf
# Expect: Allow 127.0.0.1 and Allow <your VAULT_EGRESS_SUBNET>, nothing else.
```

The lock is IPv4-only by construction, so vault-api must have no IPv6
address on its two networks (`enable_ipv6: false` in `deploy/compose.yaml`).
Expect an empty line for each:

```bash
docker inspect steamhangar-vault-api-1 --format '{{range .NetworkSettings.Networks}}{{.GlobalIPv6Address}}{{println}}{{end}}'
```

**2. Watch it with `tcpdump` on the host, not just from inside a container.**
This is the counter-check that trusts nothing this project says about its
own containers — a packet capture on the Docker host itself, watching for
any packet from `vault-api`'s container IP that is NOT going to
`vault-proxy`'s container IP:

```bash
# Find vault-api's and vault-proxy's addresses on vault-egress:
docker inspect steamhangar-vault-api-1 --format '{{.NetworkSettings.Networks.steamhangar_vault-egress.IPAddress}}'
docker inspect steamhangar-vault-proxy-1 --format '{{.NetworkSettings.Networks.steamhangar_vault-egress.IPAddress}}'

# Capture on the host while you trigger some vault-api activity (an API
# call, a scheduled sweep, whatever you have configured). Replace
# <vault-api-ip> with the first command's output above. Expect to see
# packets destined ONLY for vault-proxy's address (or nothing at all, if
# vault-api made no outbound call during the capture window) -- never a
# packet addressed to anything else.
sudo tcpdump -i any -n "src host <vault-api-ip> and not dst host <vault-proxy-ip>"
```

**This capture does NOT prove DNS is also blocked — and it is not, by
design (see "Two channels this does NOT close" above).** Docker's embedded
resolver forwards a container's DNS queries from a process running in the
HOST's own network namespace, not from a socket carrying vault-api's
container address — so a DNS-based exfiltration attempt is invisible to a
capture filtered on `src host <vault-api-ip>` specifically. Steps 3 and 4
below check the two open channels directly, rather than leaving them as
something this capture might misleadingly seem to rule out.

**3. Confirm DNS resolution still works from inside vault-api (it does,
and this is expected, not a bug to report).**

```bash
# A wildcard-DNS test host that encodes its own answer in the query name --
# resolving successfully here IS the channel docs/adr/0011-egress-lock.md
# names as open. Substitute any similar service, or your own domain, if you
# want to see a payload of your choosing survive the round trip.
docker compose exec vault-api sh -c \
  'python3 -c "import socket; print(socket.gethostbyname(\"7-7-7-7.sslip.io\"))"'
# Expect: 7.7.7.7 -- the resolution reached the real, public authoritative
# nameserver for sslip.io, through vault-lan, with no proxy involved at all.
```

**4. Confirm the Docker host's own address is directly reachable (it is,
and this is expected too).**

```bash
# The gateway address vault-lan assigns is the Docker host's own address on
# that bridge -- reachable directly, HTTP_PROXY or not, because a reply
# from the host to a container needs no masquerade.
docker compose exec vault-api sh -c \
  'python3 -c "
import socket
gw = [l.split()[2] for l in open(\"/proc/net/route\") if l.startswith(\"eth0\")][0]
import struct
ip = socket.inet_ntoa(struct.pack(\"<L\", int(gw, 16)))
print(ip)
"'
# Then, from a SEPARATE terminal on the Docker host, confirm something is
# actually listening there (e.g. vault-core's published port, if you know
# the host's own LAN IP) -- or simply trust the raw-socket connect below,
# which needs no second terminal:
docker compose exec vault-api sh -c \
  'python3 -c "
import socket
s = socket.create_connection((\"host.docker.internal\", 80), timeout=3)
print(\"connected:\", s.recv(200))
"' 2>&1 | head -5
# host.docker.internal may not resolve on every Docker version/platform --
# if it does not, substitute the gateway address the first command printed.
```

If either of steps 3 or 4 FAILS instead of succeeding, that is itself worth
investigating (it would mean this document's own claims about these two
channels are stale) — but succeeding is the documented, expected result,
not a finding to report as a vulnerability.

If step 2's capture ever shows a packet to anywhere other than
`vault-proxy`'s address, the lock is not doing what
this document claims — that is the point of running it yourself instead of
trusting this sentence.

**5. On your router, if you want a fully independent vantage point.**
Most home routers (or a managed switch with port mirroring / a `pfSense`/
OPNsense box) can show live connections or a traffic log per internal IP.
Find the Docker host's LAN IP and watch its connection log while triggering
vault-api activity the same way as step 2 — you should see outbound
connections only to the hosts you actually allowlisted (plus
`api.steampowered.com` if the relay is configured), never to `vault-api`'s
own outbound attempts directly (those never leave the Docker host's
internal bridge at all, per step 1 — the router should not see them
either way, which is itself a confirmation: if your router logs show
NOTHING for the container's un-proxied attempt, that is consistent with
the packet never having a working return path, exactly as claimed).

### Removing the lock

Not recommended, and not a single env flag on purpose (ADR-0011 §2 has the
full reasoning: an easy toggle becomes the path of least resistance for
"fixing" a filtered request instead of understanding why it was filtered).
If you genuinely need `vault-api` to have a normal, unrestricted route out
— e.g. you already run your own network-level egress control and find this
one redundant — write a `compose.override.yaml`
(`deploy/examples/tuned-setup.md` has this project's house style for such
overrides) that removes `vault-api`'s `networks:` override entirely (letting
it fall back to Compose's implicit `default` network) and clears its
`HTTP_PROXY`/`HTTPS_PROXY` values. `docker compose -f compose.yaml -f
compose.override.yaml up -d` applies it.

**One thing this override does NOT remove (round-2 review S3):**
`vault_api/config.py`'s manifest-oracle startup check is unconditional —
it fires regardless of whether the network lock is actually in effect.
With the lock removed this way, turning `VAULT_MANIFEST_ORACLE` on
without also setting `VAULT_EGRESS_ALLOW` still refuses to boot, even
though there is no proxy left to filter anything. Set
`VAULT_EGRESS_ALLOW` to the oracle's host regardless of whether you kept
the lock — see api/README.md's "Egress lock" section for why this check
does not (and cannot cheaply) know the difference.

---

## Security posture

What this deployment assumes, stated plainly so it can be checked:

- **vault-core has no authentication and cannot have any.** The Steam client
  can't present a credential. Its only protections are that it serves nothing
  but stored depot chunks, and that a cache *miss* will only ever connect
  upstream to a `*.steamcontent.com` / `*.steamserver.net` host (the Host
  allowlist, ADR-0001 req 4 — this is what stops it being an open HTTP proxy).
  **Never port-forward it, never put it behind a public reverse proxy**
  (`docs/PROJECT_PLAN.md` §10).
- **vault-api is API-key authenticated on every route except `/v1/health`**,
  which returns a fixed body and exists so external monitoring can poll it.
  For access from outside the LAN use Tailscale, Twingate, or your own TLS
  reverse proxy with forward-auth on top of the key — never a bare port
  forward.
- **vault-dns is an open resolver if you publish it wrong.** It forwards
  arbitrary queries upstream with no source-address ACL, which is fine on a
  trusted LAN and a DNS amplification/reflection weapon on the internet.
  Publish it on one specific LAN IP (`VAULT_DNS_BIND=192.168.1.50`), never on
  `0.0.0.0`. If you leave the variable unset it publishes on `127.0.0.1`, i.e.
  it fails *closed* — visibly broken rather than invisibly dangerous.
- **A host firewall does not protect published ports.** Docker writes its
  own iptables/nftables rules for every `ports:` entry, ahead of the
  chains `ufw` and `firewalld` manage, so a `ufw deny 8080` does not stop
  LAN (or WAN, on a host with a public interface) access to a published
  port. Restrict exposure where Docker honours it: bind each published
  port to one LAN IP with the `VAULT_*_BIND` variables in `deploy/.env`
  (`VAULT_CORE_BIND`, `VAULT_API_BIND`, `VAULT_DNS_BIND`), or put your
  filter rules in the `DOCKER-USER` chain, which Docker evaluates before
  its own forwarding rules.
- **No secrets in `compose.yaml`.** `VAULT_API_KEY` appears only as a required
  `${…}` reference; the real value lives in `deploy/.env`, which is gitignored.
- All five services run with `no-new-privileges`; vault-api, vault-runner
  and vault-proxy each drop **all** Linux capabilities; vault-dns keeps only
  the four it needs to bind :53 and drop privileges. vault-core's nginx
  master needs root to bind :80 and runs its workers as uid 101. vault-proxy
  never runs as root at any point (its listen port, 8888, is unprivileged),
  so unlike vault-core it has no privilege-drop dance to do at all.
- **vault-api has no default route to the internet** (WP EG-1, ADR-0011) —
  see [Egress lock](#egress-lock-vault-api-loses-its-default-route-out)
  above for the full mechanism and how to verify it yourself.

---

## Verifying a deployment

```bash
sudo sh deploy/tests/verify-stack.sh
```

Builds every image (`vault-core`, `vault-api`, `vault-proxy`, `vault-dns` —
`vault-runner` reuses `vault-api`'s) and runs **204 checks** (measured
2026-10-01) against real
containers: the config-drift contract (both directions), **the web UI baked
into the vault-api image and served from it with no bind mount involved**
(packaging work package), all twelve env-forwarding-audit keys
(`VAULT_EVENT_LOG_PATH`, `VAULT_MANIFEST_ORACLE` and the ten more B1 found —
see §7 Phase 5 in `docs/PROJECT_PLAN.md` for the full list) actually
reaching vault-api's process environment with the correct default (not just
rendering in the YAML), a **real Steam CDN** cache MISS → stored → HIT with
byte-identical bodies, the LanCache heartbeat, the Host allowlist, the
`?nocache=1` bypass, API auth and a mapping round-trip, vault-api reading
the same cache volume vault-core just wrote, DNS A/AAAA behaviour, a
**credential-free SteamPrefill smoke run on all three invocation paths** (it
must reach the username prompt, never a `TypeInitializationException`), the
runner split's own empirical evidence (WP S-2, step 6k), **the egress
lock's own empirical evidence** (WP EG-1, steps 3k-3o and 6l — an allowlisted
call succeeding through `vault-proxy`, a non-allowlisted one refused with a
real `403 Filtered`, a raw direct socket bypassing the proxy reaching no
ARBITRARY destination, DNS resolution and the Docker host's own address
staying reachable exactly as documented (round-2 review B1/B2), and the
mutation-bar proof that widening the allowlist actually flips the result),
and every fail-fast guard (split filesystems, empty/injected resolver,
unrendered template, unwritable cache, missing/invalid `CACHE_IP`).

**Historical result (2026-08-17 packaging work package, three real runs
across two review rounds):** 105/109 pass on the final run; the 4 failures
were step 5i's own timing bug (nginx's event-log buffer flushes after 5 s,
the step checked immediately, and this was reproducible rather than
strictly deterministic — see "Requirements" above). Everything else,
including every check the packaging work package added across both rounds,
was green.

**Fixed in WP 4g (2026-08-18):** step 5i now waits for the event-log line
with a bounded poll (up to 10 s — the 5 s flush plus scheduling slack)
instead of grepping immediately, and fails loudly (with a message that
distinguishes "the line never arrived" from "the line arrived but didn't
parse") if the line still hasn't shown up when the deadline passes — see
`verify-stack.sh`'s comment above step 5i. **Measured, not expected:** a
full run passes **109/109**, exit 0, with clean teardown — confirmed twice
on 2026-08-18 (the fixing run and an independent review re-run) against
Docker Engine 29.1.3 / Compose 2.40.3, with the event line arriving after
~4 s. Note the wait bound is 10 polls, each preceded by a
`docker compose exec` round trip, so the effective window is 2-4x the 5 s
flush and widens on exactly the slow hosts that need it.

**WP EG-1 (2026-08-19), the egress lock:** 30 new checks in the original
round (steps 3k-3o's static network/env-forwarding pins, and 6l's
empirical proxy behaviour and mutation-bar proof), bringing the suite from
149 to 179. **Round-2 review (same day)** added the `vault-proxy` build
itself to section 2 (B4 — this script claimed to build it already and did
not), the `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` process-environment guard
(S4), and two empirical checks for the channels the lock does NOT close
(B1: DNS resolution; B2: the Docker host's own address) — **6 more,
185 total**. Measured, a real run: **185/185 pass**, exit 0, clean
teardown, against Docker Engine 29.1.3 / Compose 2.40.3 — including the
mutation-bar sequence (deny → widen-allowlist-and-recreate-vault-proxy →
now-succeeds → restore → deny
again → stop-vault-proxy-entirely → every outbound call fails) run against
real containers, not simulated.

**WP SWEEP-1 (2026-08-22):** 2 more checks — `VAULT_SWEEP_INCLUDE_CACHED`
newly joins `VAULT_AUTO_GC` in step 3e's env-forwarding block (ADR-0014, the
operator decision that flipped both keys' defaults together; see
`docs/adr/0014-sweep-cached-and-auto-gc-default-on.md`) — bringing the suite
from 185 to **187 total**. Measured, a real run: **187/187 pass**, exit 0,
clean teardown, against Docker Engine 29.1.3 / Compose 2.40.3.

**WP SWEEP-1 follow-up (2026-08-22, same day, review round 2 fix round):**
6 more checks — 4 in step 3e (`VAULT_SCHEDULE_WINDOW` and `TZ` join the
env-forwarding block, presence + value, same precondition-then-value
pattern as every other forwarded key), plus 2 in a new step 3e-bis that
renders config against a SECOND `.env` with an explicit blank
`VAULT_SCHEDULE_WINDOW=` and asserts it renders empty rather than the
`03:00-07:00` default — the live proof for review round 2's blocker R2-B1
(a colon-form substitution had silently defeated that exact recipe; fixed
by switching to the no-colon form, see the seventh/eighth `.env.example`
note above) — bringing the suite from 187 to **193 total**. Measured
twice, both real runs: **193/193 pass**, exit 0, clean teardown, against
Docker Engine 29.1.3 / Compose 2.40.3.

**WP TH-1b (2026-10-01):** 10 more checks. Step 3e-ter (8) renders
vault-core's `VAULT_UPSTREAM_RATE_WINDOW` under four `.env` variants
(nothing set, `VAULT_SCHEDULE_WINDOW=` blank, `VAULT_SCHEDULE_WINDOW=01:00-02:00`,
and `VAULT_UPSTREAM_RATE_WINDOW=` blank with that schedule window), key
present once plus expected value each, same mechanics as 3e-bis. Step
6i-core (2) checks the VALUES of `TZ` (`UTC`) and
`VAULT_UPSTREAM_RATE_WINDOW` (`03:00-07:00`) inside the running vault-core
container; presence alone proves nothing there, because `core/Dockerfile`
sets both rate variables blank as image `ENV`. Plus 1 in step 1b: the
drift-copy completeness check. 193 + 10 + 1 = **204 total**, measured
2026-10-01 in a real run: **204/204 pass**, 0 failed.

**WP DEPLOY-FIX-2 (2026-10-01):** 5 more checks, and a failed build or
`up -d` now aborts the run with `FATAL` and exit 2 instead of cascading into
dozens of FAILs (the cleanup trap still runs). The suite runs on its own
`VAULT_EGRESS_SUBNET=172.30.239.0/24`, so it can run beside a test or
production stack. Step 3k (+2): the network and vault-proxy's environment
render this run's subnet, and a blank `VAULT_EGRESS_SUBNET=` renders the
default. Step 6l (+3): the live network has exactly that subnet,
vault-proxy's rendered `tinyproxy.conf` allows exactly loopback plus that
subnet, and vault-api's vault-egress address lies inside it. A live run on
2026-10-01 counted **216** checks (215 pass; the one FAIL was the IPv4
subnet check tripping over an IPv6 ULA the daemon had added). Round 2 adds
4 more in step 6l: vault-lan and vault-egress each have IPv6 disabled, and
vault-api has no IPv6 address on either. 216 + 4 = **220 total** (expected;
not yet measured in a real run when this was written).

It never enters credentials — reaching the login prompt is the pass condition.

It uses its own Compose project name and loopback-only, non-default ports, so
it cannot touch a running deployment, and it removes its own containers and
volumes afterwards. A recorded run is in `VERIFICATION-*.md` in this directory.

Component-level tests live with their components: `core/tests/test-core.ps1`,
`api/tests/` (pytest), `dns/tests/test-dnsmasq-config.ps1`, and
`core/docker/check-config-drift.sh`.

**One check `verify-stack.sh` deliberately does NOT cover** (it never enters
Steam credentials and this trap only shows up with a dedicated
`VAULT_CORE_BIND`, which the suite doesn't use): the SteamPrefill
cache-detection trap above. **Only relevant if you set `VAULT_CORE_BIND` to
a dedicated address** — the default `0.0.0.0` bind already works, measured
(see ["A container-specific
trap"](#a-container-specific-trap-does-steamprefill-actually-reach-your-cache)
above for why). If you did set a dedicated bind, add the heartbeat probe
from that section to your own post-deploy checklist — a DNS lookup answers
the wrong question here, since the mechanism that actually matters in this
layout is which address the heartbeat reaches, not what `lancache.
steamcontent.com` resolves to.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `required variable VAULT_API_KEY is missing a value` | `.env` missing or the key not set. Copy `.env.example`. |
| vault-core exits at boot with `FATAL: … DIFFERENT filesystems` | `cache/` and `tmp/` were split across two mounts. Mount one volume at `/vault`. |
| vault-core exits with `FATAL: … not writable by the nginx worker user` | bind-mounted cache directory not owned by `101:101`. |
| vault-core exits with `FATAL: /vault/cache is missing` | `VAULT_CACHE_PATH` is set but `<path>/cache/depot` and `<path>/tmp` weren't created first — a bind mount isn't seeded the way a fresh named volume is. See ["Using a dedicated cache mount"](#using-a-dedicated-cache-mount). |
| `up` fails with `invalid mount config for type "bind": field VolumeOptions must not be specified` | `VAULT_CACHE_PATH` is set and your `compose.yaml` still carries `nocopy` on vault-api's `/vault` bind (releases up to v0.1.0-rc4). Docker 28 (observed 28.3.1) refuses that. Update `compose.yaml`; the current one drops `nocopy` in bind mode. |
| `docker compose config`/`up` fails with `refers to undefined volume ...: invalid compose project` | `VAULT_CACHE_PATH` doesn't start with `/`, `./` or `~/` — Compose parsed it as a named-volume reference instead of a bind path. Use an absolute path. |
| vault-dns exits with `FATAL: CACHE_IP is not set` | the `dns` profile is enabled but `CACHE_IP` is empty in `.env`. |
| Clients download at internet speed and the cache stays empty | DNS redirection isn't reaching them, or the AAAA leak is open. Check with `dig A` **and** `dig AAAA` against your resolver (`dns/README.md`). |
| Prefill jobs fail with "A Steam account is required" | the one-time interactive login hasn't been done — see [First run](#first-run-the-one-time-steamprefill-login). |
| Prefills stall or fail with many errors; vault-core's log shows `connect() failed (113: Host is unreachable) while connecting to upstream` for Steam CDN addresses, single downloads work; your router may log an "ICMP flood" from your provider's gateway | your line is behind a carrier-grade NAT (DS-Lite, many fibre/cable/mobile lines) and a prefill used up its port mappings: every chunk vault-core fetches is a new upstream connection. Lower `VAULT_PREFILL_MAX_THREADS` in `.env` (default `8`; try `4`), then `docker compose up -d` to recreate vault-api and vault-runner. The job output starts with `Will download using at most N threads` when it took effect. Wait a few minutes before retrying so the NAT can expire old mappings. |
| Port 80 already in use on the host | use a dedicated IP, not a different port — see [Port 80](#port-80-and-the-dedicated-ip-question). |
