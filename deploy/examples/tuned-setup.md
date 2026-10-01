# Tuned setup: dedicated cache disk, scheduler cadence, GC grace window

Start from [`minimal-lan.md`](minimal-lan.md) or your existing install, then
apply the pieces below once the default setup is working. Each section is
independent — take what you need.

## 1. Dedicated cache disk/dataset (works out of the box)

The cache defaults to a plain Docker-managed named volume. To put it on a
specific disk or dataset instead — a second drive, a NAS storage pool, a ZFS
dataset tuned for ~1 MiB Steam chunks — set one variable in `deploy/.env`:

```bash
# deploy/.env
VAULT_CACHE_PATH=/srv/steamhangar-cache
```

`deploy/compose.yaml` already resolves both services' `/vault` mount from
this variable (a bind of this path when set, the `vault-cache` named volume
when unset; vault-api's line drops its named-volume-only `nocopy` option by
itself in bind mode), so no `compose.yaml` edit is needed — this is the one
setting in this document that is fully wired end to end today.

Before the first start, create the directory and hand it to the nginx worker
user (uid/gid 101 — not a placeholder, see
[`deploy/README.md`](../README.md#using-a-dedicated-cache-mount)):

```bash
sudo mkdir -p /srv/steamhangar-cache/cache/depot /srv/steamhangar-cache/tmp
sudo chown -R 101:101 /srv/steamhangar-cache
```

If you're on TrueNAS SCALE with ZFS specifically,
[`truenas-scale-dockge.md`](truenas-scale-dockge.md) has the full dataset
recipe (`recordsize=1M`, `atime=off`, `compression=off`, and the measured
reasoning behind each — Steam chunks are already Valve-compressed, so ZFS
compression buys nothing here).

## 2. Scheduler cadence (now DB-settable — no override file needed)

`vault-api` reads three more settings that control when the scheduler sweeps
installed-app lists — documented in [`api/README.md`](../../api/README.md)
("Scheduler"):

| Variable | Meaning | Default |
|---|---|---|
| `VAULT_SCHEDULE_WINDOW` | Daytime window the scheduler sweeps in, `HH:MM-HH:MM` server-local time (overnight windows like `22:00-06:00` are supported). Empty = scheduler off. | *(empty — off)* |
| `VAULT_SCHEDULE_INTERVAL_MINUTES` | Minimum spacing between two sweeps. | `180` |
| `VAULT_SCHEDULE_CLIENT_STALE_DAYS` | A client whose newest agent report is older than this drops out of the sweep's target set. | `7` |

**Updated 2026-08-17 (packaging work package B1 audit) — the "honest gap"
this section used to describe is closed, and the fix is better than the
override-file workaround it originally recommended.** These three are
DB-overridable at runtime via `PATCH /v1/settings` (ADR-0009, shipped in the
settings-API work package) — set them from the web UI / Android app
Settings screen, or directly:

```bash
curl -X PATCH -H "X-Api-Key: $VAULT_API_KEY" -H 'Content-Type: application/json' \
  http://<server>:8080/v1/settings \
  -d '{"schedule_window": "09:00-17:00", "schedule_interval_minutes": 180, "schedule_client_stale_days": 7}'
curl -H "X-Api-Key: $VAULT_API_KEY" http://<server>:8080/v1/schedule   # confirm the scheduler is enabled
```

No `docker compose up -d --build` or override file needed — this takes
effect at the next scheduler tick, no restart.

**All three also have `deploy/.env` lines now** (`VAULT_SCHEDULE_WINDOW`
since WP SWEEP-1 / ADR-0014; `VAULT_SCHEDULE_INTERVAL_MINUTES` and
`VAULT_SCHEDULE_CLIENT_STALE_DAYS` since the pre-freeze project review's
S3 fix — see `.env.example`'s "Settings-API keys reachable from this file").
An earlier revision of this page called their absence from `.env.example`
"correct, not a gap"; it stopped being correct the moment
`VAULT_SETTINGS_READONLY=1` became a documented deployment mode, because a
hard-locked vault refuses the `PATCH` above and then had no way to reach
two of the three at all. An env value sets the STARTUP fallback a DB row
overrides once the stack has booted (db > env > default, ADR-0009) — which
is what a first-boot/infra-as-code deployment wants. It buys little *once
the stack is up* on a read-write deployment: a DB row already wins over
whatever the env value says, so editing `.env` and restarting is strictly
more work than the `PATCH` above for tuning an already-running vault.

(`VAULT_GC_GRACE_DAYS`, listed here in earlier revisions of this document,
is a plain forwarded `deploy/.env` value today — see `.env.example` — and
was never one of these three's problems to begin with; this section was
simply written before that got fixed and never updated.)

## 3. Two path overrides that genuinely need the override-file route

Unlike the schedule settings above, `VAULT_MANIFEST_ARCHIVE_DIR` and
`VAULT_STEAMPREFILL_CACHE_DIR` (`vault_api/config.py`) are deliberately
**not** forwarded by `deploy/compose.yaml` and have no DB-backed
alternative either — both are directory paths tied to the container's
volume layout, not general tuning knobs, and a wrong value here silently
loses persistence (no volume backs an arbitrary path) rather than merely
picking a different valid number. If you have a genuine reason to move
either — e.g. redirecting the manifest archive off the `vault-db` volume
onto its own mount — a Compose override file is still the right tool.

**`VAULT_STEAMPREFILL_CACHE_DIR` CANNOT relocate where SteamPrefill writes —
review round 3 correction, because round 2's version of this section got
that backwards and the recipe it gave was actively harmful, not just
inert.** Grepped, not assumed: `prefill.py`'s child-process launch
(`subprocess.Popen(command, cwd=workdir, ...)`) passes SteamPrefill no
`env=`, no `HOME`/`XDG_*` override and no cache-directory flag — nothing in
this codebase ever tells the SteamPrefill BINARY where to put its manifest
temp-cache. `VAULT_STEAMPREFILL_CACHE_DIR` is read in exactly one place,
`manifest_ingest.py`'s scan step, and its default
(`config._default_steamprefill_cache_dir`) is explicitly documented as a
**guess** at where SteamPrefill's OWN, independent logic decides to write
(mirroring the same `$HOME`-relative computation SteamPrefill itself uses
internally) — vault-api never forwards this value anywhere, it only reads
FROM the path it names. Setting it to a custom path — on one service or
both, identically — does not move SteamPrefill's writes there; it only
moves where vault-api's ingestion step LOOKS, and if that no longer matches
where SteamPrefill actually writes (still `$HOME/.cache/SteamPrefill/v1`,
unconditionally, no matter what this variable says), you get the exact
silent "ingestion finds nothing" failure the paragraph above this one
warns about — self-inflicted this time, by the very override meant to avoid
it.

**The default (unset, on both services) is correct precisely because it is
the one value guaranteed to track wherever SteamPrefill really writes** —
both resolve the same `$HOME`-relative computation, and `deploy/compose.yaml`
already mounts the SAME `vault-steamprefill-home` volume at the SAME
`/opt/steamprefill/home` path on both `vault-api` and `vault-runner` for
exactly this reason (see that file's own comment on the mount). **If you
have a genuine reason to move this data onto a dedicated disk, the movable
thing is the VOLUME, not the path `VAULT_STEAMPREFILL_CACHE_DIR` names** —
leave that variable unset on both services, and instead replace what backs
`/opt/steamprefill/home` itself:

```yaml
# deploy/compose.override.yaml (not tracked by git -- your local addition)
services:
  vault-api:
    environment:
      VAULT_MANIFEST_ARCHIVE_DIR: "/data/manifests"  # default: sibling of VAULT_DB_PATH
    volumes:
      - /srv/steamhangar-manifest-cache:/opt/steamprefill/home
  vault-runner:
    volumes:
      # Same host path as vault-api's above -- both services must still
      # agree on ONE underlying location for /opt/steamprefill/home, exactly
      # as the shipped vault-steamprefill-home volume already does. Nothing
      # about VAULT_STEAMPREFILL_CACHE_DIR needs to change: it stays unset,
      # its default computation is still $HOME-relative, and $HOME is still
      # /opt/steamprefill/home regardless of what physically backs it.
      - /srv/steamhangar-manifest-cache:/opt/steamprefill/home
```

Create and `chown -R 101:101` the host directory first, same as the
dedicated-cache-disk recipe in section 1 — and note this relocates ALL of
`/opt/steamprefill/home`, not only the manifest temp-cache subdirectory
under it (there is little else there in practice, but it is the whole
directory that moves, not a scoped piece of it).

`VAULT_MANIFEST_ARCHIVE_DIR` does not have any of this two-service concern:
manifest *archiving* (copying a `.bin` file out durably, as opposed to the
temp-cache SteamPrefill itself writes and vault-api merely scans) happens
entirely on `vault-api`, in every mode, unaffected by the runner split — set
it on `vault-api` alone, as shown above, exactly as earlier revisions of
this document already had it.

`docker compose` picks up a file named `compose.override.yaml` (or
`docker-compose.override.yml`) next to `compose.yaml` automatically — no
extra `-f` flag needed. Most deployments never need this section at all:
both defaults already resolve inside volumes the stack mounts anyway
(`vault-db` and, as of WP S-2, the now-shared `vault-steamprefill-home`
respectively).
