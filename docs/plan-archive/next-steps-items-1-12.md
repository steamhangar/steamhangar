# Plan archive: §11 Next Steps, items 1-12

Moved verbatim from `docs/PROJECT_PLAN.md` by DOC-DIET-1 (rc22, 2026-11-01).
Finished history; do not read whole, grep or read line ranges. Open
boxes of this file are copied to the core plan, section "12. Carried-over
open items", and are ticked THERE, not here. Index: [README.md](README.md).

1. [x] **Packaging package** (§7 Phase 5, first bullet) — DONE, WP P1: `web/`
   baked into the vault-api image, the full env-forwarding gap audited and
   closed (not just `VAULT_EVENT_LOG_PATH` + `VAULT_MANIFEST_ORACLE` —
   twelve keys total, see §7 Phase 5's own bullet for the complete list),
   `deploy/tests/verify-stack.sh` extended and run for real. What follows is
   unchanged by it: next up is item 2 below.
2. [ ] **First real-world rollout** — joint interactive session with the
   operator (see the Deployment section of `docs/WORKPACKAGES.md`). The two
   operator-side network blockers are cleared (measured by the operator,
   2026-10-01): the VPN subnet router advertises only the current networks,
   and LAN clients get no IPv6 DNS server. One check stays for the rollout
   itself: with the DNS rewrite active, the AAAA answer for the cache name
   must not be a public address. Precondition decided 2026-10-01: the
   egress subnet becomes configurable (WP DEPLOY-FIX-2) so a production
   stack can run next to a test stack; the rollout started with tag
   v0.1.0-rc3. Gate (b) found that a bind-mounted cache
   (VAULT_CACHE_PATH) fails on Docker 28 because of vault-api's
   `:nocopy` (fixed in WP DEPLOY-FIX-3, verify now runs bind mode too);
   production moves to v0.1.0-rc5, which carries the fix. Findings from
   the operator's first phone test on rc5 (2026-10-02), fixed in WP
   WEB-FIX-3 (phone layout hardening), WEB-FIX-4 (job titles for
   owned-only games; plain-language hints plus Retry for a missing Steam
   login and for a cache name resolving to a public IP) and WEB-FIX-5
   (dismissible hint when the browser's "Desktop site" mode renders the
   UI at desktop width - the root cause of the zoomed-out phone view).
   Prefill on the production stack needs vault-core on its own address
   and `extra_hosts` for the cache name on vault-runner. This session is also
   where the honest still-open lists in `web/tests/README.md` and
   `app/README.md` get verified — real screen reader, phone browser cover
   art, GC against real on-disk chunks, real multi-client bypass detection.
3. [x] **WP 4b.9** Android release build + signing docs + APK distribution
   (keystore creation is a user action), plus the review carry-over list in
   `docs/WORKPACKAGES.md` — done 2026-08-17, together with WP 4b.10 (the
   clients/bypass detail surface); see §7 Phase 4b's evidence notes.
4. [x] **Phase 4c / 4d** frontend halves — CLOSED 2026-08-22: the manual
   "check & update" trigger in both UIs (web WP 4c-web, Android WP 4c-app),
   and the now-default-on "keep the cache current" sweep mode (WP SWEEP-1
   flipped it — and its auto-GC pair — from opt-in to the shipped default,
   docs/adr/0014-sweep-cached-and-auto-gc-default-on.md). Its frontend
   surface shipped in both UIs the same day: web as WP 4d-web (commit
   c825625, see §7 Phase 4d's closing note), Android as part of WP AG-3
   (commit 0a3bfc5) — toggle, GC-risk warning and three-way last-sweep
   status, the Kotlin port pinned by string equality against the
   twice-reviewed web copy.
5. [x] **WP 5.3 docs half** — `SECURITY.md` + `docs/security/threat-model.md`,
   done 2026-08-18 (see §7 Phase 5). Round-2 review ran and FAILED (two
   blockers plus should-fixes, mostly citation drift from WP 4h.0 landing
   in the same commit range plus a false §5 exhaustiveness claim) —
   findings addressed in the WP 5.3-fix follow-up commit; round 3: PASS
   (2026-08-18).
6. [x] **WP 5.3 review half** — pre-release security review, DONE
   2026-10-01 after the api/core/dns freeze
   ([ADR-0016](adr/0016-api-core-code-freeze.md), commit 92e95be). Two
   passes on Opus 5.5 (Fable unavailable, user decided not to wait; since
   WP R-0, commit 80f750f, "[Fable]" names a boundary — the strongest
   available model at call time — not a model). Fix packages SEC-FIX-1..4
   (9aef5f4, eea667d, c20a33a, 901368e) plus test fix 907de53, each
   reviewed to PASS; api 2000 passed / 6 skipped, `verify-stack.sh`
   211/211. Known limits in threat-model §9, post-release items in §7
   Phase 5's evidence note.
7. [ ] **User-gated:** WP 5.5 — the machinery is no longer the gate. The
   GitHub org exists, the repo lives at `steamhangar/steamhangar`, and the
   full publish path is built and reviewed (WP CI-3 + WP AGENT-BIN, see §7
   Phase 5's CI bullet): images, signed APK, agent binaries, checksums.
   What remains are user actions only (steps (a)-(d) at the end of this
   item), and review's standing advice is a throwaway pre-release first, named
   `v0.1.0-rc1`: three things in the tree bake `0.1.0` —
   `vault_api.__version__` (served as `server_version` by the
   authenticated `GET /v1/settings`; `/v1/health` returns only
   `{"status":"ok"}`), the compose `VAULT_IMAGE_TAG` default, and the
   `org.opencontainers.image.version` LABEL in the api, core, dns and
   deploy/proxy Dockerfiles. (Superseded 2026-10-03 by WP VER-1: the
   Dockerfile labels and `server_version` now come from the build args
   publish.yml derives from the tag, `dev` for local builds; only
   `vault_api.BASE_VERSION`, the fallback outside an image, and the
   compose `VAULT_IMAGE_TAG` default still say `0.1.0`.) What the tag sets: since DEPLOY-FIX-1/APP-FIX-1 the APK's
   versionName is the tag without its `v` (build.gradle.kts falls back to
   `0.1.0` for local builds only), and the published images are tagged
   (and labelled, via docker/metadata-action) with the same string. With
   `v0.1.0-rc1` the APK and images say `0.1.0-rc1`, a semver pre-release
   of the baked `0.1.0` base; a `v0.0.1-rc1` would ship images and an APK
   whose version is older than the `server_version` inside them. The path has never executed and the
   release page is the only place its end-to-end result can be seen
   (re-pushing the SAME tag double-appends release notes; known, commented
   at the site). WP 5.6
   (announcement) stays gated on the user's own end-to-end test with the
   Android app. Phase 6 integrations are deliberately post-release.
   **Remaining release steps (2026-10-01, all user actions):**
   (a) merge PR `wp/pre-freeze-review` once its CI is green — the first
   android-tests run there is the first compile of the APP changes;
   (b) one-time GitHub settings BEFORE setting the signing secrets: an
   Environment `release` with a `v*`-tag deployment rule, the four
   `ANDROID_*` secrets placed in it (not as repo secrets), and a `v*` tag
   ruleset (see app/README "CI signing secrets"); (c) push the tag
   `v0.1.0-rc1` from main; (d) check the release page's "Verify this
   release" block. After the first push, the GHCR packages must be made
   public.
8. [x] **The AG series — agents become first-class residents** — COMPLETE
   2026-08-22, four packages in one day (user's go for the full chain).
   AG-0 (commit 74e7727): client identity visible and attributed at
   startup (source + sanitize note), install-time preview reading the
   same hostname source as Go (the COMPUTERNAME case defect found in
   review would have minted ghost identities), rename semantics
   documented honestly. AG-1 (commit 54e7622): installed_on on the games
   endpoints with the scheduler's own freshness gate resolved against
   effective settings (round 1 caught the shared-function/unshared-bound
   divergence), plus DELETE /v1/clients/{client_id} for ghost rows.
   AG-2 (commit 0971a1d): the web badge and the payoff statement
   "installed but not cached", after a round-1 FAIL in which every DOM
   surface was deletable green — root cause a stub fake-dom whose no-ops
   silently swallowed the calls; the harness itself was extended and now
   throws on unsupported selectors. AG-3 (commit 0a3bfc5): Android
   parity for the badge plus the sweep surface, after the same
   wiring-unpinned FAIL on the same day, fixed with the codebase's
   strongest anchor generation; the demo's stale pre-ADR-0014 default
   (which made screenshots show a warning no real fresh install shows)
   corrected and pinned by the Android twin of the web's config drift
   guard. Both frontends' demo modes now exercise every badge state.
9. [x] **WP APP-DEMO** — Android demo mode, done 2026-08-22 (commit
   bdb2c19): screenshot-grade fixtures behind the onboarding skip link,
   no account, no vault, no network; six repository seams, compile-time
   fixture/model coupling, DEMO MODE banner unscrollable on every
   surface. Four review rounds; the durable outcome is recorded in
   docs/LEARNINGS.md (the guarantee-vs-mechanism ceiling of name-based
   isolation scans). Real-device residuals listed in app/README.md.
10. [x] **Download throttling, time-dependent — done (WP TH-1a/TH-1b,
    TH-1a committed as 6aa4c0c, TH-1b as 47747b4, TH-1-FIX as 92e95be; ADR-0015)** (operator request 2026-08-30). Caps the
    upstream (Steam → vault) bandwidth; LAN serving stays uncapped. Scoping
    by the WP TH-0 spike (commit 9865237,
    `poc/throttle/RESULTS-THROTTLE-20260929.md`): SteamPrefill 3.7.1 has
    no rate option, nginx-native `proxy_limit_rate $var` caps the
    `proxy_store` miss path exactly, per request, HITs at LAN speed. User
    decision 2026-09-30: the operator sets ONE aggregate limit, divided at
    download time by the live number of parallel requests. WP TH-0b
    (commit e3f3f65, `poc/throttle/RESULTS-THROTTLE-DYNAMIC-20260930.md`)
    chose Option A (an nginx `map` on the live connection count) over Option B
    (njs counter: burst overshoot, leak on abort). WP TH-1a
    (`poc/throttle/RESULTS-TH1-20261001.md`) switched the divisor to
    `$connections_writing`, not `$connections_active` (measured: `_writing`
    0.98× of target with 8 idle keep-alives open, `_active` 0.50×). The map
    is generated at container start with buckets 1..1024 =
    `worker_connections` (worker_processes 1), so no live count reaches the
    default; the bound is pinned by `check-config-drift.sh`. Fail-closed:
    an invalid value refuses to boot. Env-only: `VAULT_UPSTREAM_RATE`
    (empty = off) and `VAULT_UPSTREAM_RATE_WINDOW` (full speed inside;
    unset follows `VAULT_SCHEDULE_WINDOW`, blank caps around the clock),
    evaluated in vault-core's `TZ`, which `deploy/compose.yaml` now
    forwards (WP TH-1b). Limits stated in `core/README.md` "Upstream rate
    cap": per request divided by the live count (HITs dilute the share),
    the LAN client of a MISS is slowed too, the window edge stops nothing,
    slow-ramp overshoot unmeasured. Router QoS stays the zero-code
    alternative.
11. [ ] **Macvlan deploy example — vault-core on its own LAN IP** (operator
    request 2026-08-30, roadmap entry — not yet a scoped work package).
    Steam clients require plain HTTP on port 80 (Steam's choice), so on
    a host whose port 80 is already owned by a reverse proxy (Traefik,
    NPM — the homelab default) vault-core needs a second address. Today
    that means a host-side IP alias created in the host platform's own
    UI (so it survives reboots). The comfortable
    shipped answer: a documented compose override giving vault-core its
    own LAN IP via a macvlan network — Docker-native, no host network
    clicks, the established lancache pattern. Must ship as an EXAMPLE
    with its caveats stated, not as a silent default: the host cannot
    reach a macvlan container directly (affects the host-side health
    probes and any same-host DNS rewrite target), the parent interface
    and address range are operator-specific, and the interaction with
    the host platform's container manager needs a real test on the
    deployment this was requested for. Belongs in deploy/examples/ beside
    minimal-lan and tuned-setup, with a verify recipe; footprint deploy/ +
    docs only.
12. [x] **Pre-freeze project review (2026-09-30/10-01) and its fix
    packages — done 2026-10-01; freeze declared (ADR-0016).** Before declaring the api/core freeze that
    item 6 waits on, the whole tree was reviewed in eight areas (api data
    layer, api surface, core + dns, agent, web, app, deploy, docs), each by
    its own reviewer, each report naming blockers (claims or code that must
    not reach the freeze as they are). One fix package per area with
    findings, each through the usual coder → reviewer → PASS pipeline:
    - [x] **AGENT-FIX-1** — commit 5ce6b50
    - [x] **CORE-FIX-1** (core + dns) — commit 30151f8
    - [x] **API-FIX-2** — commit 460897e
    - [x] **WEB-FIX-1** — commit 9572e6f (includes the WP 4f demo-mode
      carry-over open since 2026-08-18)
    - [x] **API-FIX-1** — commit 298029f
    - [x] **APP-FIX-1** — commit 95b684c
    - [x] **DEPLOY-FIX-1** (deploy, CI) — commit 4c8a67d
    - [x] **DOCS-FIX-1** (this plan, the root docs, the ADR addenda dated
      2026-09-30 — files no other fix package owns) — commit f70c031
    - [x] **DOCS-FIX-2** (the component docs those packages touch, after
      they land) — commit ec90ce2
    Follow-up commits after the review packages, all reviewed to PASS:
    - [x] **TH-1a** (core, aggregate upstream cap) — commit 6aa4c0c
    - [x] **WEB-FIX-2** (user decision 2026-10-01, connection-lost
      indicator) — commit 984d02a
    - [x] **TH-1b** (deploy, cap configurable from `.env`, follows the
      schedule window) — commit 47747b4
    - [x] **TH-1-FIX** (copy-up race on a fresh cache volume, found by a
      live verify; verify 204/204) — commit 92e95be
    Directly before the review, WP R-0 (commit 80f750f) moved agent
    delegation to the session model (see item 6). The freeze followed the
    last PASS: ADR-0016, freeze commit 92e95be.
