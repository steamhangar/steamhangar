# Deploying SteamHangar on TrueNAS SCALE with Dockge

A community layout for running SteamHangar on TrueNAS SCALE 25.x, managed
through [Dockge](https://github.com/louislam/dockge) instead of the SCALE
Apps UI. This is one way to do it, not the only way -- adapt paths and pool
names to your own system. Every address below is an
[RFC 1918](https://en.wikipedia.org/wiki/Private_network) example
(`192.168.1.0/24`), not a real deployment's configuration.

Read [`deploy/README.md`](../README.md) first -- this document only adds the
TrueNAS/Dockge-specific parts: where the stack lives, how to give the cache
its own ZFS dataset (the point of this document), and the two things a NAS
box typically collides with that a bare Linux host doesn't (port 80, and
running your own DNS as a SCALE app).

---

## 1. Prerequisites

- TrueNAS SCALE 25.x (Debian-based "Fangtooth"-series SCALE, `docker`/`docker
  compose` available on the host -- this is standard on SCALE 24.10+; SCALE's
  own "Apps" feature is itself Kubernetes/Docker underneath, but this guide
  bypasses it in favor of a plain Dockge-managed stack).
- Dockge already installed and reachable (its own setup is out of scope
  here -- see the Dockge project's own install instructions). Dockge's job in
  this guide is exactly what `deploy/README.md`'s quickstart does by hand:
  `cp .env.example .env`, edit it, `docker compose up -d`, plus a UI to watch
  logs and restart the stack.
- At least one ZFS pool with free space for the cache. **It does not need to
  be your fastest pool** -- see [§3](#3-why-an-hdd-pool-is-fine-for-this-cache)
  for why an HDD pool is a perfectly reasonable choice here, which matters
  because it's usually the pool with the most free space on a home NAS.

---

## 2. Stack layout under Dockge

Dockge discovers stacks as subdirectories of the directory you pointed it at
during its own setup (commonly `/mnt/<pool>/dockge/stacks/`). Give SteamHangar
its own subdirectory there and put `deploy/compose.yaml` (and your `.env`)
inside it -- Dockge treats any directory containing a `compose.yaml`/
`docker-compose.yaml` as a stack it can start, stop, and show logs for:

```
/mnt/<pool>/dockge/stacks/steamhangar/
├── compose.yaml     # copy (or symlink) of deploy/compose.yaml from a checkout
└── .env             # copied from deploy/.env.example, then edited -- see below
```

Either check out the SteamHangar repo directly under that path and use
`deploy/` as the stack directory (simplest -- Dockge just needs *some*
directory with a compose file and an `.env` next to it, subdirectories of the
repo are fine), or copy just `deploy/compose.yaml` and `deploy/.env.example`
there if you don't want the whole repo on the NAS. Either way, `deploy/.env`
itself is still never committed anywhere (`deploy/README.md`, `.gitignore`) --
it lives only in the stack directory.

---

## 3. Why an HDD pool is fine for this cache

The instinct is "cache = fast disk, so SSD pool." For SteamHangar specifically,
that instinct costs you SSD space for no measurable benefit, because the
bottleneck for this cache is never the disk:

- **The LAN, not the disk, is the ceiling.** A 1 Gbit LAN link tops out at
  ~118 MB/s of actual payload throughput. A single modern 7200 RPM HDD does
  ~150-250 MB/s sequential -- comfortably *above* what one gigabit client can
  even ask for. Multiple simultaneous LAN clients divide that same ~118 MB/s
  link further, not multiply the disk's load. Steam depot chunks are also
  fetched and served whole (`docs/PROJECT_PLAN.md` §10; `core/README.md`
  measured chunks at ~1 MiB), so this is sequential-ish read/write traffic --
  an HDD's weak point (random IOPS/seeks) is not what this workload exercises.
- **ZFS ARC absorbs the hot set anyway.** Repeat requests for the same chunk
  (multiple PCs prefilling or reinstalling the same game) are served from
  RAM-resident ARC, not re-read from the HDD at all, once a chunk has been
  read once.
- **This is a cache with no eviction** (`docs/PROJECT_PLAN.md` §3) -- it only
  grows, so raw capacity-per-dollar (an HDD pool's actual strength) matters
  more here than latency.

None of this is an argument against an SSD/NVMe pool if that's what you have
spare space on -- the recipe below works identically on either. It is an
argument against feeling obligated to use one.

---

## 4. Dedicated ZFS dataset for the cache

This is what WP 1.9's `deploy/README.md` "Using a bind mount for the cache"
section described as a manual compose-file edit; `deploy/compose.yaml` now
exposes it as the `VAULT_CACHE_PATH` variable instead, so nothing below
requires touching `compose.yaml` itself.

### 4.1 Create the dataset

```bash
# Run on the TrueNAS SCALE host shell (or via Storage > Datasets > Add
# Dataset in the UI, then set the same properties there).
zfs create -o recordsize=1M -o atime=off -o compression=off \
    <pool>/steamhangar-cache
```

Why each property, specifically for this workload:

- **`recordsize=1M`** -- Steam depot chunks run up to ~1 MiB
  (`core/README.md`; the project's own test fixtures and
  `docs/PROJECT_PLAN.md` §10 both use 1,048,576-byte chunks as the
  representative size). ZFS's default `recordsize=128K` would split a single
  chunk across 8 records; matching the record size to the object size cuts
  metadata overhead and read/write amplification for the dominant file size
  in this dataset. (Small files -- manifests, the depot directory tree
  itself -- still get variable, smaller records; `recordsize` is a ceiling,
  not a fixed block size.)
- **`atime=off`** -- every read of a cached chunk (i.e. every cache HIT)
  would otherwise trigger an access-time metadata write. This dataset has no
  workload that reads `atime`, so it's a pure write-amplification tax with
  nothing consuming the value.
- **`compression=off`** -- **verified against this project's own evidence,
  not assumed:** `docs/research/phase3-manifests.md` (§"Correctness proof")
  parsed real Steam manifests and diffed `cb_compressed` (the manifest's
  declared *compressed* chunk length) against the actual on-disk chunk file
  size across ~12,000 real cached chunks, with **zero size mismatches**.
  That means the bytes SteamHangar stores under `cache/depot/.../chunk/<id>`
  are already Valve's own compressed chunk payload -- there is no
  uncompressed data downstream of Valve's own pipeline for ZFS to
  additionally compress. Turning ZFS compression on for this dataset spends
  CPU cycles attempting to compress data that is already
  entropy-dense/compressed, typically for a negligible or even negative
  ratio, for every single write. `lz4` (SCALE's default when compression is
  left on) is cheap per call, but "cheap and pointless" is still not "free":
  turning it off removes a per-write CPU cost that buys nothing here.

Optional, if you want a hard ceiling or a guaranteed floor on this dataset
(neither is required -- the cache has no eviction, so an operator-set quota
is the only ceiling that exists at all):

```bash
zfs set quota=500G <pool>/steamhangar-cache        # hard ceiling
zfs set reservation=50G <pool>/steamhangar-cache   # guaranteed floor (optional)
```

### 4.2 Ownership

`compression=off`/`recordsize=1M` are ZFS properties on the dataset;
the layout and ownership of the *mount point* are plain POSIX, same as any
bind mount (`deploy/README.md` "Using a dedicated cache mount"). Both
`cache/depot` and `tmp` must exist and belong to 101:101 before the first
start -- a bind mount is not seeded the way a fresh named volume is:

```bash
mkdir -p /mnt/<pool>/steamhangar-cache/cache/depot /mnt/<pool>/steamhangar-cache/tmp
chown -R 101:101 /mnt/<pool>/steamhangar-cache
```

uid/gid 101 is not a placeholder -- it's the exact numeric identity of the
stock nginx image's `nginx` user that `vault-core`'s workers run as, and
`vault-api`'s container user is created with the same numbers on purpose
(`core/README.md`, `deploy/README.md`). Get this wrong and `vault-core`
refuses to start and tells you the exact command to run -- it does not
silently fail to cache anything.

### 4.3 Point the stack at it

In the stack's `.env` (`deploy/.env.example` documents this variable in
full):

```bash
VAULT_CACHE_PATH=/mnt/<pool>/steamhangar-cache
```

That's the entire knob -- one line, no `compose.yaml` edit. Leaving
`VAULT_CACHE_PATH` unset (the default for anyone not following this guide)
keeps the plain Docker-managed named volume exactly as before; setting it
bind-mounts this dataset at `/vault` in **both** `vault-core` and
`vault-api`, covering `cache/` and `tmp/` together as the one mount the
same-filesystem constraint requires (`core/README.md`) -- there is no way to
move only one of them with this variable, by design.

`docker compose up -d` (or the equivalent Dockge "Start"/"Restart" button)
picks it up on next start.

### 4.4 A quota note about the cache-event log

`VAULT_EVENT_LOG` / `VAULT_EVENT_LOG_PATH` (`deploy/README.md` "Phase-3
knobs") ship **ON by default** as of the 2026-08-17 packaging work package --
earlier phases left this off because nothing consumed it yet, but WP 3.11's
sweeper is the consumer now (miss-triggered prefill completion, per-client
hit stats, bypass detection), so there is no longer a reason to leave it off
by default. Its path lands inside this same dataset either way:
`core/Dockerfile` pre-creates `logs/` alongside `cache/` and `tmp/` under the
one `/vault` mount, and `VAULT_CACHE_PATH` redirects all three together, not
just `cache/`. Worth knowing before you set a tight `zfs set quota=...` on
`<pool>/steamhangar-cache` (§4.1) -- the event log competes for the same
quota as the depot cache itself. It is a much smaller scale (one TSV line
per request, not per-chunk bytes) but genuinely unbounded over time, and
`deploy/README.md`/`.env.example` are explicit that vault-api can read the
file but has no permission to truncate it in the shipped containers -- if
this quota matters to you, rotating/truncating `/vault/logs/event.log` is on
you (e.g. a periodic TrueNAS cron task), not something either image does
automatically.

---

## 5. The port-80 conflict (Traefik and friends)

TrueNAS SCALE's own Apps stack commonly runs a Traefik ingress already
listening on the host's port 80/443 (SCALE's "Apps" -> ingress feature), and
plenty of other self-hosted stacks (Nginx Proxy Manager, another reverse
proxy) do the same. `vault-core` **must** stay on port 80 -- Steam CDN
traffic is plain HTTP and the Steam client only ever asks for port 80
(`deploy/README.md` "Port 80 and the dedicated-IP question"; do not "solve"
this by moving `VAULT_CORE_PORT`, that just makes a cache nothing uses).

Give SteamHangar its own IP address on the NIC instead, via the SCALE UI:

1. **Network > Interfaces > (your NIC) > Edit > Aliases > Add** -- add an
   alias IP in your LAN's range, e.g. `192.168.1.50/24`, distinct from the
   TrueNAS host's primary address.
2. In the stack's `.env`:
   ```bash
   VAULT_CORE_BIND=192.168.1.50
   VAULT_TLS_BIND=192.168.1.50
   ```
   `deploy/compose.yaml` already publishes `vault-core` as
   `${VAULT_CORE_BIND:-0.0.0.0}:${VAULT_CORE_PORT:-80}:80` -- this is an
   existing knob, not something this guide adds. `VAULT_TLS_BIND` publishes
   vault-core's HTTPS passthrough on port 443 of the same address. The DNS
   rewrite in §6 also sends HTTPS for `*.steamcontent.com` there, for
   example SteamPrefill's manifest downloads (`deploy/README.md` "Port 443:
   the HTTPS passthrough"). Port 443 on the alias must be free exactly
   like port 80: a service listening on all interfaces owns it on the alias
   too.
3. Point your DNS rewrite (§6 below) at `192.168.1.50`, not the host's main
   address.

A dedicated VLAN interface or a `macvlan` Docker network works equally well
if you'd rather not use an IP alias; the IP-alias route above needs nothing
beyond the SCALE UI.

---

## 6. DNS: use AdGuard Home (or Pi-hole), not the bundled `vault-dns` profile

TrueNAS SCALE users very commonly already run AdGuard Home as an app (it's a
one-click SCALE "Apps" catalog item). If you do, **use it** instead of
enabling `deploy/compose.yaml`'s `--profile dns` (`vault-dns`) --
[`dns/README.md`](../../dns/README.md) mode 1 has the exact copy-paste
rewrite rules for AdGuard Home (and Pi-hole/plain dnsmasq, if you run one of
those instead). One less container, and no second thing on the network fighting
over port 53.

Whichever local resolver you use, `dns/README.md`'s point stands and is worth
restating here: **the AAAA record for `*.steamcontent.com` must be closed
too**, or IPv6-capable clients silently bypass the cache over IPv6 with no
error and no log entry on your side. `dns/README.md` mode 1 shows the exact
AdGuard Home / Pi-hole steps for this.

### A router-level bypass that DNS rewrites alone don't fix

Even with the AAAA leak above closed at your resolver, there's a second,
independent way IPv6 quietly defeats DNS-based redirection on a home LAN:
many consumer/ISP routers advertise **themselves** as a DNS resolver via
IPv6 Router Advertisements (the RDNSS option in SLAAC/RA), separately from
whatever DNS server your DHCPv4 lease hands out. A dual-stack client that
prefers its RA-provided IPv6 DNS server over its DHCPv4-provided one will
resolve `*.steamcontent.com` (and everything else) through the router's own
upstream resolver -- never touching AdGuard Home / Pi-hole / `vault-dns` at
all, regardless of how correctly that resolver is configured. No cache
rewrite fixes this from the DNS-server side, because the client never asks
that server in the first place; the fix has to be at the router (disable RA
DNS advertisement / RDNSS, or push the same DNS server via both DHCPv4 and
RA) and is router-firmware-specific, which is why this is one paragraph and
not a recipe. This is exactly the class of silent bypass requirement **A12**
(`docs/PROJECT_PLAN.md` §2, "Detect clients silently bypassing the cache") is
scoped to catch after the fact, for the LANs where locking down the router
itself isn't practical or wasn't done -- see `docs/adr/0001-proxy-store-feasibility.md`
consequence 7 for why bypass detection has to stay aware of legitimate
non-cache traffic (Steam's own LAN P2P transfers) rather than treating all of
it as a leak.

---

## 7. Verifying the layout

After `docker compose up -d` (or Dockge's Start), the same checks
`deploy/README.md`'s quickstart lists apply unchanged:

```bash
curl http://192.168.1.50/health                     # vault-core -> ok
curl -I http://192.168.1.50/lancache-heartbeat       # X-LanCache-Processed-By: steamhangar
curl http://<truenas-host>:8080/v1/health            # vault-api -> {"status":"ok"}
```

Confirm the dedicated dataset is actually the thing being written to, not the
fallback named volume (i.e. that `VAULT_CACHE_PATH` took effect):

```bash
docker compose exec vault-core sh -c 'stat -c "%n %d" /vault /vault/cache /vault/tmp'
# then, on the TrueNAS host:
zfs list <pool>/steamhangar-cache
```

After a client's first download, `zfs list -o space <pool>/steamhangar-cache`
should show `USED` growing, and `ls /mnt/<pool>/steamhangar-cache/cache/depot`
should show real depot-ID directories -- not an empty dataset with all the
bytes landing somewhere else.

### 7.1 Also check: does SteamPrefill *inside the container* actually reach the cache?

This is a NAS-specific enough gotcha to call out separately here, on top of
`deploy/README.md`'s general "A container-specific trap" section (read that
one first -- it explains the full four-candidate detection mechanism and
which layout actually needs this check; this section only restates why the
TrueNAS/§5 layout specifically is the one that DOES need it).

SteamPrefill runs as a subprocess inside `vault-api` and finds the cache by
probing, in order, DNS for `lancache.steamcontent.com`, `localhost`, the
**fixed literal `172.17.0.1`** (the classic Docker default bridge's gateway
address, hardcoded in the SteamPrefill binary -- NOT dynamically detected
from whatever network `vault-api` is actually on), then the local hostname
-- accepting the first one that answers `/lancache-heartbeat` with
`X-LanCache-Processed-By`. **§5 of this guide has you set `VAULT_CORE_BIND`
to a dedicated alias IP** (the port-80-conflict fix), and that is precisely
the layout where candidates 2 and 3 (loopback and `172.17.0.1`) BOTH refuse
to connect -- measured in `deploy/README.md`: binding to one specific
address means Docker publishes port 80 only there, not on `172.17.0.1` or
loopback (on the DEFAULT `0.0.0.0` bind, `172.17.0.1` actually does work,
via ordinary host-level routing between the host's own bridge interfaces --
see `deploy/README.md` for the full explanation and measurement; that case
just doesn't apply once you've followed §5). That leaves DNS (candidate 1)
as the only remaining chance, and on TrueNAS SCALE specifically the Docker
daemon's own resolver is very often NOT the AdGuard Home instance §6 has
you configure the rewrite in (AdGuard Home there is just another
app-managed container/VM on the same box, not necessarily what the host's
Docker daemon resolves through) -- so DNS often doesn't save you here
either. If none of the four candidates succeeds, prefill jobs silently
download straight from Valve -- the job still finishes green, and
`zfs list -o space <pool>/steamhangar-cache` never grows.

Check it with the heartbeat probe, not a DNS lookup -- a DNS lookup only
tells you about candidate 1, and candidates 2-4 never touch DNS at all.
**`curl` is not installed in the `vault-api` image** (`python:3.13-slim`) --
use `python3`, which is:

```bash
docker compose exec vault-api python3 -c "import urllib.request as u; r=u.urlopen('http://<your §5 alias IP>/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"
```

A line printing `200 steamhangar` is success. A traceback ending in
`ConnectionRefusedError`/`URLError` means it's missing --
`deploy/README.md`'s fix recipe applies unchanged here: pin
`lancache.steamcontent.com` to your §5 alias IP via `extra_hosts` on
`vault-api` in a `compose.override.yaml` next to this stack's
`compose.yaml` -- **the value must be that plain IPv4 alias address, not a
hostname**, since SteamPrefill's own resolution step requires an
RFC1918-or-loopback IPv4 before it ever sends the heartbeat probe. This is
DNS-independent by design, which is exactly what you want on a NAS where you
may not control what the Docker daemon's own resolver is doing.

---

## 8. Troubleshooting additions specific to this layout

| Symptom | Likely cause |
|---|---|
| `vault-core` exits at boot: `... is not writable by the nginx worker user` | `chown -R 101:101` on the dataset's mount point was skipped or ran before the dataset existed. |
| `vault-core` exits at boot: `... DIFFERENT filesystems` | Something (a snapshot mount, an unrelated bind mount) is layered inside `/mnt/<pool>/steamhangar-cache` on a different device than the dataset root. `VAULT_CACHE_PATH` must point at one filesystem boundary, not a directory with something else mounted inside it. |
| `docker compose up` fails with `invalid mount config for type "bind": field VolumeOptions must not be specified` | `VAULT_CACHE_PATH` is set and your `compose.yaml` still carries `nocopy` on vault-api's `/vault` bind (releases up to v0.1.0-rc4). Docker 28 (observed 28.3.1) refuses that. Update `compose.yaml`; the current one drops `nocopy` in bind mode. |
| `docker compose up` starts but nothing ever gets cached, and there's no ownership error | Check `VAULT_CACHE_PATH` was actually picked up (Dockge/`docker compose config | grep -B2 /vault`) -- a value without a leading `/` is parsed as a *named volume reference*, not a bind path, and Compose refuses with `refers to undefined volume ...: invalid compose project` if it doesn't match `vault-cache` exactly. |
| Every request is a cache MISS at internet speed, cache empty | DNS redirection isn't reaching the client, or the AAAA/RA bypass (§6) is open. `dig A` and `dig AAAA` against your resolver from an actual client, not just the NAS. |
| Port 80 already answers something else (a Traefik/NPM welcome page) | You bound `vault-core` to the host's primary address instead of the alias from §5 -- double-check `VAULT_CORE_BIND` in `.env` against `ip addr show` on the TrueNAS host. |
| Prefill jobs finish `done` but `zfs list -o space <pool>/steamhangar-cache` never grows | SteamPrefill inside the container isn't finding the cache via any of its four detection candidates (§7.1 above -- this is the expected risk of the §5 dedicated-alias layout specifically). Check with `docker compose exec vault-api python3 -c "import urllib.request as u; r=u.urlopen('http://<your §5 alias IP>/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"` (`curl` is not in the `vault-api` image), looking for `200 steamhangar`, and fix with the `extra_hosts` override in `deploy/README.md`. |
