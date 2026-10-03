# Handover: post-v0.1.0 roadmap (items D1-D5)

Written 2026-10-02 by the orchestrator session that ran the first
production rollout. For whoever picks up the work after `v0.1.0`. Read
`CLAUDE.md`, `docs/PROJECT_PLAN.md` (§11 item 13) and `docs/LEARNINGS.md`
first. The working agreement does not change: orchestrator plans, coder
implements, reviewer reviews every package, one commit per passed package.

## Ground rules for this track

- **Timing.** Code for D1-D3 merges to `main` only after the `v0.1.0`
  tag. The api/core freeze (ADR-0016) holds until then, and anything
  merged before the tag ships in the release. You can draft designs and
  ADRs on their own branches now.
- **Parallel work.** Use a separate git worktree, e.g. `git worktree add
  ../steamhangar-next <branch>`. The repo's dev tooling may assume the
  main checkout path. In that case run api tests (pytest in the uv venv)
  and web tests (`node --test "web/tests/*.test.js"`) directly in the
  worktree, and run stack verification only from the main checkout. Keep
  to about three agents at a time.
- **ADR first.** Each of D1-D3 changes architecture or a security
  decision. Write the ADR, have it reviewed, and get the user's decision
  on its open questions before any code.
- **Language.** Communicate with the user in German. The repo, code and
  commits are in English.

## Context: what the rollout taught us (2026-10-01/02)

- **Network.** The operator's internet line is DS-Lite: IPv4 runs through
  the provider's CGNAT (AFTR 192.0.0.1). When the per-subscriber port
  quota runs out, the CGN answers with ICMP host-unreachable (RFC 6888
  REQ-11). In nginx that shows up as `connect() failed (113: Host is
  unreachable)`. A prefill sent about 62k chunk requests in about 2
  minutes, and 94% failed. Single requests always work. From a container
  on the same host, 50 parallel new connections work, while 200 fail
  about half the time. Many residential lines in Germany are DS-Lite, so
  treat this as the normal case, not an edge case.
- **vault-core opens one upstream TCP connection per chunk.** The cause is
  `proxy_pass http://$vault_upstream_host$request_uri` with a resolver:
  an implicit upstream has no keepalive, even on nginx 1.29.7+ where
  explicit upstreams default to `keepalive 32 local`. lancache does the
  same (two nginx tiers, variable `proxy_pass`, no pool).
- **SteamPrefill 3.7.1** runs 30 concurrent requests by default
  (`Models/DownloadArguments.cs`). On failure it retries up to 2 more
  rounds right away, with `?nocache=1`. The hidden flag `--max-threads N`
  (`Program.cs`, since v2.8.1) caps the concurrency. WP CORE-FIX-2 uses
  it before `v0.1.0`; this is stage 1 of the fix, and D1 is stage 2.
- **SteamPrefill has no QR login.** This is verified in the v3.7.1 and
  v3.7.2 source. Login uses `BeginAuthSessionViaCredentialsAsync` (account
  name plus password typed on the console), followed by Steam Guard
  approval in the app or by code. The session lives in
  `Config/account.config` next to the binary: protobuf-net
  `UserAccountStore`, field 3 `CurrentUsername`, field 4 `SessionId`,
  field 5 `AccessToken`, which actually holds the refresh token. It is
  valid for about 200 days. ADR-0004 claimed otherwise; DOCS-FIX-3 (item
  13 B, 2026-10-03) corrected it in ADR-0004 addendum 4.
- **Steam's anti-phishing location check** blocked an app approval when
  the approving phone was in another country than the server ("Steam has blocked
  this sign in"). Entering the 5-character Steam Guard code is reported
  to bypass the check (community threads, no Valve documentation).
  Approving after routing the phone through a VPN exit at home
  worked. Any QR flow inherits this limit.
- **The cache does not depend on the account.** Chunks are stored and
  served by depot path (`cache/depot/<id>/chunk/<hash>`, `try_files
  $uri`). Ownership is checked earlier, when Steam hands out manifest
  request codes. Once a chunk is cached, every client in the house gets a
  HIT.

Sources and full research notes are in the session's research reports,
summarised above: the SteamPrefill source at tags v3.7.1/v3.7.2,
SteamKit2 3.4.0 `Authentication/*` and sample 001, lancachenet/monolithic
HEAD b9213a9, the nginx 1.29.8 source (`ngx_http_upstream.c` ~738-749,
the keepalive module) and RFC 6333/6888/5382. Re-verify anything you
build on; the pins may have moved.

## D1: CORE-FEAT-1, upstream keepalive to the Steam CDN

**Goal.** Reuse upstream connections, so that a prefill or a client
download opens about as many TCP connections as there are workers,
instead of one per chunk. This removes the CGNAT ceiling for good. It
also helps Steam clients, whose concurrency we do not control.

**Mechanism (verified in the nginx source).** With a variable
`proxy_pass`, nginx first looks the evaluated host name up among the
defined `upstream` groups (host and port match; no port in the URL means
port 0). It falls back to the resolver only if no group matches. Since
1.27.3, OSS nginx supports `server <name> resolve;` inside an `upstream`
that has a `zone`. Together with `keepalive`, this gives a pooled,
re-resolving group per edge host:

```nginx
upstream cache9-ams1.steamcontent.com {
    zone steam_edges 256k;
    server cache9-ams1.steamcontent.com resolve;
    keepalive 16;
}
```

The existing `proxy_pass` line, the Host header, the path-faithful store
path and the 508 loop guard stay unchanged. Unknown hosts take today's
resolver path, so a missing entry costs performance but never breaks
anything. Keepalive also needs `proxy_http_version 1.1` (already set) and
an empty `Connection` header toward the upstream; check that the loop-guard
hop header still goes out.

**Open design questions, for the ADR and the user:**
1. **Where the edge list comes from.**
   - Static seed list rendered by the entrypoint (env or file).
   - Learned from the access log's `$host` histogram, plus a reload.
   - A union of both.

   Steam's CDN host set varies by region and over time (`cacheN-<pop>`,
   plus ISP-hosted "CDN" servers that may return 403 for some depots).
2. **Keepalive size and timeouts per edge.** Mind the CGNAT quota: total
   pooled connections must stay well below it.
3. **Reload strategy** when the list changes (`nginx -s reload` from
   inside the container, or restart).
4. **Interaction with `VAULT_UPSTREAM_RATE`** (ADR-0015): `limit_rate`
   per connection versus pooled connections.

**Prove before building.** On production, with a handful of sequential
requests and no burst, check whether Valve edges keep a connection alive
across requests (count the TCP handshakes, or check
`$upstream_connect_time` going to 0 on reused connections). The project's
test instance has no internet access by design.

**Tests.** `nginx -t` on the rendered config; verify-stack: a fake edge
upstream that counts accepted connections, N chunk requests must cost
fewer than N connections; the unknown-host fallback still works; the loop
guard still fires.

## D2: AUTH-FEAT-1, Steam login by QR code from the web UI

**Goal.** No terminal, no password: the web UI shows a QR code, the user
approves it in the Steam app, and the session lands where SteamPrefill
reads it.

**Design sketch (inference, to be confirmed in the ADR):**
1. **Helper.** A small helper (C#, about 100 lines, SteamKit2 pinned to the
   version SteamPrefill uses) runs in the container that holds
   SteamPrefill's `Config/` (vault-runner in queue mode). It calls
   `BeginAuthSessionViaQRAsync(new AuthSessionDetails { IsPersistentSession
   = true, DeviceFriendlyName = ... })`. On stdout it prints JSON lines with
   the rotating `ChallengeURL` (via `ChallengeURLChanged`) and a status,
   and it polls `PollingWaitForResultAsync()`.
2. **Session file.** On success the helper writes `Config/account.config`
   in SteamPrefill's protobuf format itself: account name, a random
   session id, and the refresh token in field 5. It prints only "ok" (and
   maybe the account name), never the token.
3. **vault-api.** Two endpoints behind the API key: start (returns a
   short-lived login id) and status/URL (polled by the web UI, which
   renders the QR client-side). Add a rate limit, a timeout, cancellation,
   and allow only one login at a time.
4. **Fallback.** The terminal login with a Steam Guard code stays
   documented. It is the escape hatch for the location check.
5. **Session status in Settings.** Show "present / missing / expired"
   (the JWT `exp` of the stored token, read by the runner and never
   returned), plus the copyable terminal command.

**Security notes for the ADR (amends ADR-0004):**
- No code path accepts, forwards or stores a Steam password.
- The refresh token never crosses vault-api's HTTP layer, logs or DB.
- It does still sit on a filesystem the vault-api uid can read. That is
  true today as well; real isolation would need a separate uid or
  container.
- A challenge URL is not a credential, but it does carry authority:
  whoever approves it logs their account into the server. That is why it
  must sit behind the API key and be short-lived.
- The token has full Steam-client scope for about 200 days. This is the
  same exposure as today; say so in the threat model.

**Risks.** We depend on a private serialization format and on a SteamKit2
version. Pin both, and add a CI test that a SteamPrefill binary accepts a
helper-written file (offline parse, or a dry run against a mock if
possible). Upstream is unlikely to take a QR patch: the maintainer
declined multi-account (#113), credential storage (#303) and
non-interactive select-apps (#427).

## D3: MULTI-1, several Steam accounts per household

**Goal.** Each household member's games can be prefilled with their own
session, and the library shows everyone's games.

**Facts.**
- SteamPrefill supports exactly one session per install: `Config/` is
  hard-wired to `AppContext.BaseDirectory/Config`, and there is no
  `--user` or config-path flag. The FAQ and maintainer say to run one
  install per account.
- A symlinked binary probably resolves to the real BaseDirectory; test
  this.
- The library setting is one SteamID today (`steam_library_steamid`,
  API-FEAT-1).

**Design sketch.**
- One directory per account (a copy of the binary dir, or a per-account
  bind mount over `Config/`).
- A job carries the account; the runner picks the session that owns the
  app (from each account's owned-games list via the relay).
- The library setting becomes a list of SteamIDs. The web/Android library
  shows the union, with an owner hint per game.
- QR login (D2) per account.
- The privacy stance (PROJECT_PLAN §7 Phase 4h: no judgemental per-person
  numbers in a shared living room) applies to owner hints.
- Steam Families sharing is deliberately not pursued (user decision
  2026-10-02).

**Depends on D2** for a usable login per account.

## D4: IPv6 egress (operator network, optional)

Steam CDN hosts publish AAAA records, and IPv6 bypasses the CGNAT
entirely. The operator's host runs without global IPv6 on purpose, so
this is a network decision for the operator's documentation and process,
not a code change. If it ever happens, vault-core would need
`resolver ... ipv6=on` and IPv6-capable Docker networks; check the
egress-lock networks, which set `enable_ipv6: false` on purpose.

## D5: named, scoped API keys and per-target payload scoping

Already specified in PROJECT_PLAN §7 Phase 6. No new findings.

## Suggested order

1. DOCS-FIX-3 lands before `v0.1.0` (it is in item 13 B).
2. Write the ADR drafts for D1, D2 and D3, review them, and settle the
   open questions with the user.
3. D1 first: it is the widest benefit, and the production measurement is
   cheap.
4. D2, then D3 on top of it.
