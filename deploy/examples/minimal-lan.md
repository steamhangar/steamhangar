# Minimal LAN setup

The smallest working SteamHangar deployment: `vault-core` + `vault-api` only,
using a local DNS server you already run to redirect Steam's CDN traffic —
no `vault-dns` container, no dedicated cache disk, no scheduler tuning. If
this is your first SteamHangar install, start here; the other examples in
this directory are variations on top of this one.

Read [`deploy/README.md`](../README.md) first — this page only shows the
minimal path through it.

## Prerequisites

- A Docker host with Compose v2 (`docker compose`), reachable from your LAN.
- A local DNS server you already run: AdGuard Home, Pi-hole, dnsmasq, or
  Unbound. If you have **none of these**, either use the bundled `vault-dns`
  container instead (see [`deploy/README.md`](../README.md#dns-pick-one-of-three-modes)
  mode 2) or, for a single Windows gaming PC only, `vault-agent`'s hosts-file
  mode (mode 3, `agent/README.md` "Hosts-file mode") — both are one step up
  in complexity from this example, which assumes mode 1.

## 1. Bring up the stack

```bash
cd deploy
cp .env.example .env
$EDITOR .env      # set VAULT_API_KEY -- the only value you must change
docker compose pull
docker compose up -d
```

`pull` fetches the published release images from `ghcr.io/steamhangar/`
(the `image:` lines in `compose.yaml` name exactly what the release
workflow pushes); building from this checkout instead is
`docker compose up -d --build` — `deploy/README.md` "Quickstart" has the
one paragraph on when to prefer which.

That's it for Compose. `vault-dns` is defined behind the `dns` Compose
profile (`profiles: ["dns"]` in `deploy/compose.yaml`) and a plain
`docker compose up -d` never starts it — nothing extra to disable for this
minimal path.

Verify the two services answer:

```bash
curl http://<server>/health                 # vault-core -> ok
curl http://<server>:8080/v1/health         # vault-api  -> {"status":"ok"}
```

## 2. Point Steam traffic at the cache (DNS mode 1)

This is the step that actually makes clients use the cache — Compose alone
does not redirect any traffic. Add a rewrite for `*.steamcontent.com` to
your cache server's LAN IP in whichever local DNS server you already run,
**and** close the IPv6 (AAAA) leak for the same zone — skipping the AAAA
part doesn't break anything visibly, it just lets IPv6-capable clients
silently bypass the cache.

[`dns/README.md`](../../dns/README.md) has copy-paste instructions for
AdGuard Home, Pi-hole (both v5 and v6), plain dnsmasq, and Unbound, plus the
explanation of *why* the AAAA step matters
("[The IPv6 bypass, explained](../../dns/README.md#the-ipv6-bypass-explained)").
Two sentences of the two DNS modes this example does NOT use, for context:

- **Mode 2 (bundled `vault-dns`):** for a LAN with no DNS server of its own —
  `docker compose --profile dns up -d` plus two more `.env` values
  (`CACHE_IP`, `VAULT_DNS_BIND`). One more container to run and update.
- **Mode 3 (hosts-file, `vault-agent hosts apply`):** for a single Windows
  gaming PC with no DNS server involved at all — no network-wide effect,
  only that one machine.

## 3. Do the one-time SteamPrefill login

Needed once, before the first prefill job — see
[`deploy/README.md` "First run"](../README.md#first-run-the-one-time-steamprefill-login).
Everything else (`/v1/games`, `/v1/mapping`, the cache itself) works without
this step.

## What this example deliberately leaves out

- **Dedicated cache disk/dataset** (`VAULT_CACHE_PATH`) — the cache lives in
  the default Docker-managed named volume, which is fine for trying
  SteamHangar out or a small install. See
  [`tuned-setup.md`](tuned-setup.md) once you outgrow it, or
  [`truenas-scale-dockge.md`](truenas-scale-dockge.md) for a ZFS-specific
  recipe.
- **Scheduler / garbage-collection tuning** — also in
  [`tuned-setup.md`](tuned-setup.md).
- **A dedicated IP for port 80** — only needed if something else on this
  host already listens on port 80; see
  [`deploy/README.md` "Port 80 and the dedicated-IP question"](../README.md#port-80-and-the-dedicated-ip-question).
