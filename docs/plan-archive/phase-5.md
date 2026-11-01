# Plan archive: Phase 5 community release

Moved verbatim from `docs/PROJECT_PLAN.md` by DOC-DIET-1 (rc22, 2026-11-01).
Finished history; do not read whole, grep or read line ranges. Open
boxes of this file are copied to the core plan, section "12. Carried-over
open items", and are ticked THERE, not here. Index: [README.md](README.md).

### Phase 5 — Community Release
- [x] README with architecture diagram, quickstart (compose up in 5 minutes),
      and an explicit "works for guests" FAQ note: any Steam client behind
      the LAN DNS is served with zero setup — no account, agent, or app;
      store-on-miss means the first guest download fills the cache for
      everyone (ADR-0001)
      *(WP 5.2: root README with Mermaid diagram, quickstart verified
      command-by-command against deploy/, works-for-guests FAQ plus four
      more ADR-backed entries; feature status stated against shipped code,
      not ADR designs — license section says "planned Apache-2.0" until
      the LICENSE file lands with the release)*
- [x] License: Apache-2.0 (permissive for maximum adoption, includes patent
      grant; AGPL deliberately rejected as it deters contributors and
      companies in the early phase)
      *(WP 5.4: root `LICENSE`, canonical text, copyright line "Copyright
      2026 SteamHangar contributors")*
- [x] **Packaging: ship the web UI in the vault-api image + close the
      env-forwarding gaps** (gap recorded 2026-08-17, closed same day, WP
      P1, two Opus review rounds: FAIL → fix → FAIL → fix → re-verified).
      User decision: bake `web/` into the image (a mount-only fix cannot
      survive `docker pull` of a published image, WP 5.5) — vault-api's
      `build:` in `deploy/compose.yaml` moved to `context: .. / dockerfile:
      api/Dockerfile`, every `COPY` in `api/Dockerfile` became
      repo-root-relative, `COPY web /app/web` was added, and
      `ENV VAULT_WEB_DIR=/app/web` replaces the now-wrong relative default
      inside the image. New root `.dockerignore` keeps the wider context
      from also sending `app/` (Android), `poc/` (Phase-0 evidence) and
      api/'s own dev-only files (`README.md`, `pytest.ini`,
      `requirements-dev.txt` — round-2 review caught these missing from the
      first pass) to the daemon (measured 9.734 MB → 1.54 MB, 84% smaller);
      the old api/-rooted `api/.dockerignore` is deleted and its rules fully
      folded in (Docker only reads a `.dockerignore` at the context root,
      and an api/-rooted build no longer even works); `core/`/`dns/` keep
      their own build contexts, unaffected.
      **Env-forwarding: a complete audit, not a two-key patch** (the
      round-2 reviewer's B1: `.env.example` told operators to set
      `VAULT_MANIFEST_ORACLE_URL` for a privacy mitigation that could not
      work while unforwarded — the exact `docs/LEARNINGS.md` "Containers"
      bug class this package exists to close, re-introduced by an
      incomplete first pass). Every env var `config.py` reads was checked
      against `deploy/compose.yaml`: twelve were unforwarded with no
      good reason and now are —
      `VAULT_EVENT_LOG_PATH`/`VAULT_MANIFEST_ORACLE`/
      `VAULT_MANIFEST_ORACLE_URL`/`VAULT_MANIFEST_ORACLE_TIMEOUT`/
      `VAULT_MANIFEST_KEEP`/`VAULT_EVENT_SWEEP_INTERVAL_MINUTES`/
      `VAULT_MISS_TRIGGER_COOLDOWN_MINUTES`/`VAULT_MISS_TRIGGER_MAX_PER_SWEEP`/
      `VAULT_BYPASS_WINDOW_DAYS`/`VAULT_CLIENT_STATS_KEEP`/
      `VAULT_EVENT_LOG_MAX_BYTES`/`VAULT_WEBHOOK_TIMEOUT_SECONDS`
      (round-2 reviewer independently re-derived all 33 keys from
      `config.py` and reconciled this classification exactly, then proved
      every default byte-identical by diffing `Settings.from_env()` JSON
      dumped from a bare container against one fed exactly what `docker
      compose config` renders; this package additionally proved each key
      arrives with a non-default value too). New `api/tests/
      test_p1_compose_env_defaults.py` (round-2 should-fix S-D) keeps this
      from drifting again without Docker: it derives expected values from
      `config.py`'s own `DEFAULT_*` constants and diffs them against
      `compose.yaml`'s `${VAR:-default}` text on every `pytest` run,
      mutation-tested in both directions (a changed default, a deleted
      passthrough line) — and its first real run caught a genuine,
      harmless pre-existing drift of its own: `VAULT_SIZE_CACHE_TTL`'s
      compose default was the bare string `60` against `config.py`'s
      `60.0`; fixed to match (both parse to the same float, so no behavior
      changed). The rest stay unforwarded for one of three recorded
      reasons: baked into the image (`VAULT_DB_PATH`/`VAULT_CACHE_ROOT`/
      `VAULT_STEAMPREFILL_PATH`/`VAULT_WEB_DIR`); DB-overridable at runtime
      via `PATCH /v1/settings` instead (ADR-0009: `VAULT_NAME`,
      `VAULT_SCHEDULE_*`, `VAULT_WEBHOOK_URL`/`VAULT_WEBHOOK_EVENTS` —
      `deploy/examples/tuned-setup.md` §2 rewritten to point at this instead
      of its now-stale override-file workaround, and its `VAULT_GC_GRACE_DAYS`
      claim corrected, that one having already been forwarded before this
      package); or deliberately env-only, volume-topology-sensitive
      (`VAULT_MANIFEST_ARCHIVE_DIR`, `VAULT_STEAMPREFILL_CACHE_DIR` — new §3
      in `tuned-setup.md`); plus `VAULT_SETTINGS_READONLY`, env-only by
      definition — left unforwarded by P1, closed as a forwarding gap on
      2026-08-18 (Fable audit finding: the one env-only key with NO
      alternative path, so the shipped compose could not enable the
      documented hard-lock at all; now forwarded + pinned). `.env.example` makes the cache-event log pair default-ON
      (user decision, WP 3.11's consumer justifies it) with an honest
      read-not-truncate growth warning, a reachable
      `VAULT_EVENT_LOG_MAX_BYTES=0` escape hatch, an upgrade note that a
      dozen previously-silent-no-op keys now fail loudly at boot if
      malformed, and widens the `VAULT_RESOLVER` warning from
      vault-dns-only to any same-host rewriting resolver (AdGuard
      Home/Pi-hole).
      **The SteamPrefill container-detection trap — corrected twice.**
      Round 1 named only the DNS candidate and treated a DNS miss as
      universally fatal; round-2 review (B2) fixed that to a four-candidate
      model but used `curl`/`ip`, neither of which exists in the
      `python:3.13-slim`-based `vault-api` image (measured: `curl: not
      found`), and its worked-example transcript was mislabelled — the
      `200 OK` shown as `curl` output was actually produced by the
      `python3`/`urllib.request` command this package used throughout, not
      by running `curl` inside `vault-api`, which cannot happen. Round-2's
      OWN measurement (the container's dynamic default-route gateway) also
      turned out not to be the actual mechanism: round-3 review (S-A)
      settled, via a string scan of the shipped SteamPrefill binary, that
      candidate 3 is the FIXED LITERAL `172.17.0.1` (present as a managed
      UTF-16LE string; the only private IPv4 literal there matching the
      documented candidate list — `127.0.0.1` also appears, as candidate 2 —
      and no `host.docker.internal`-style name at all) — not something SteamPrefill
      dynamically detects from `vault-api`'s own routing table. Re-verified
      directly: a real Compose stack's `vault-api` container, whose OWN
      network gateway is `172.19.0.1` (a Compose-managed bridge, not the
      classic default bridge), still reaches the literal `172.17.0.1` and
      gets the heartbeat back when `VAULT_CORE_BIND=0.0.0.0` — ordinary
      host-level routing between the host's own `docker0` and Compose-bridge
      interfaces, not container-to-container traffic (which Docker's
      inter-network isolation WOULD block). The conclusion is unchanged
      (default layout: nothing to fix; dedicated `VAULT_CORE_BIND`: the
      trap is real), only the mechanism description and the diagnostic
      command are corrected. `deploy/README.md`, `deploy/examples/
      truenas-scale-dockge.md` §7.1 and its troubleshooting row now state
      the literal, use `docker compose exec vault-api python3 -c
      "import urllib.request as u; ..."` (verified present and working;
      `curl` stays available only in vault-core's own Alpine image, a
      different question), and every transcript shown was re-run with that
      exact command and pasted verbatim, including the real Python
      traceback tail for the refused-connection case. The `extra_hosts` fix
      recipe itself was correct in every round and stays unchanged —
      confirmed sufficient on its own (SteamPrefill uses the resolved IP
      for all subsequent depot traffic once detection succeeds, per WP
      0.4), with the added requirement that the value be a plain IPv4
      address, never a hostname.
      Verified against real Docker (WSL2, Engine 29.1.3/Compose 2.40.3),
      three review rounds: built the image from the repo-root context, ran
      it standalone with zero volumes and confirmed `GET /` serves the real
      app shell (`docker inspect` showed `Mounts: []`); proved all twelve
      newly-forwarded keys reach `Settings.from_env()` with non-default
      values in a running container; rendered `docker compose config`
      against both the shipped `.env.example` defaults and with every knob
      uncommented; re-ran the exact documented `python3`/`urllib.request`
      diagnostic (not a stand-in) in both `VAULT_CORE_BIND` layouts and
      pasted the real transcripts into the docs; and ran `deploy/tests/
      verify-stack.sh` to completion four times across three review rounds
      (109 checks, up from WP D1's 73): 105/109 passed on the final run,
      every time. The 4 failures are a pre-existing WP D1 bug in step 5i
      unrelated to this package (nginx's `flush=5s` event-log buffer vs. an
      immediate post-request grep with no wait; confirmed by an isolated
      repro) — reproducible on every run so far, but genuinely
      timing-dependent rather than strictly deterministic (a slower/faster
      host could tip the race either way), corrected wording per
      `docs/LEARNINGS.md`. Every check this package added, across all three
      rounds, passed on every run; api suite 1482 passed/1 skipped on the
      final run (1461 baseline + 21 new in `test_p1_compose_env_defaults.py`).
      **Fixed in WP 4g** (2026-08-18): step 5i's grep is now a bounded
      wait-for-line loop instead of an immediate read, so this 105/109
      result is historical — see `verify-stack.sh`'s step-5i comment and
      `deploy/README.md`.
- [x] **Product rename SteamVault → SteamHangar** (WP RN-1, 2026-08-19):
      the working title collided with piracy tools and an established
      achievement tracker. Three tiers — product word and image namespace
      renamed; Kotlin package `dev.steamvault.app`, the `steamvault://`
      scheme, internal `vault-*`/`VAULT_` names, and every dated historical
      record deliberately NOT renamed. Two persisted-state identifiers
      (Android prefs file, web localStorage keys) left on the old name so no
      existing install silently loses its saved connection — a migration is
      a separate package. See docs/adr/0013-product-rename-steamvault-to-
      steamhangar.md for the full change list, the wire-visible items, and
      the open follow-ups.
- [x] CI: GitHub Actions — lint, tests, multi-arch image build (amd64/arm64),
      publish to ghcr.io with pinned version tags
      *(BUILT, in four packages; the publish path itself has never
      executed — no tag exists — so every "nothing is published yet"
      statement elsewhere in this plan remains true until the first tag,
      which is the user's click. WP CI-1, 2026-08-21, commit 72a0aeb: the
      web suite (node --test) and the verify-stack integration suite join
      CI — verify-stack nightly/manual only, isolated from the PR gate.
      WP CI-2, 2026-08-21, commit e8a09ca: the Android Gradle suite joins
      the PR gate (assembleDebug + both unit-test variants + lintDebug),
      closing the last blind spot; known residual: a Gradle cache hit
      skips distributionSha256Sum re-verification, recorded in the threat
      model. WP CI-3, 2026-08-22, commit 3f5fd0e: publish.yml on v* tags
      + workflow_dispatch — four images to ghcr (vars.GHCR_NAMESPACE,
      flavor latest=false, unknown/unknown manifest guard, per-platform
      SteamPrefill smoke), plus android-release (secrets-gated signed
      APK, apksigner verify, if:always() keystore cleanup). WP AGENT-BIN,
      2026-08-22, commit 29d875c: vault-agent binaries for the three
      ADR-0005 targets + SHA256SUMS attached to the same release; static
      CGO_ENABLED=0, per-target file-type guard, VERSION sanitized for
      slashed refs (workflow_dispatch rehearsal), release notes generated
      by exactly one job per tag. Known, commented: re-pushing the SAME
      tag appends release notes a second time. First-tag advice recorded
      by review: a throwaway pre-release to exercise the never-run path
      end to end — originally named v0.0.1-rc1, superseded 2026-09-30 by
      `v0.1.0-rc1` so the tag is a pre-release of the baked `0.1.0` base
      (`__version__`, compose tag default, Dockerfile LABELs), see §11
      item 7.)*
      *(WP 5.1 done 2026-08-09 — the test/lint half: api pytest (Linux),
      agent go build/vet/test (Linux+Windows matrix), core `nginx -t`
      through the image's REAL entrypoint render path (pinned upstream
      image derived from core/Dockerfile) + config-drift check +
      shellcheck, PS 5.1 parser/pure-ASCII gate + PSScriptAnalyzer
      PSUseCompatibleSyntax(5.1); actions SHA-pinned, contents:read,
      NO publishing. Manual/network harnesses documented as CI-excluded.
      First gate exposed a real WP 3.10 bug — 25-vault-eventlog.sh's
      survivor check made vault-core unable to start in its default
      config; fixed in a separate fix(core) commit. Image build/publish
      remains WP 5.5, user-gated. Opus PASS + delta-confirmed. CI run #1
      on real runners: 4/5 jobs green incl. all three locally-unverifiable
      ones; the api/pytest Linux job found a second real bug — a
      Windows-only-measured path guard accepting `a\b`/`C:x` on POSIX —
      fixed as fix(api), run #2 green expected.)*
- [x] CONTRIBUTING.md, issue templates, example configs
      *(WP 5.4: root `CONTRIBUTING.md` with verified per-component test
      commands (api pytest: 704 passed/1 skipped; agent go build/vet/test:
      all packages green, incl. a documented CRLF/`gofmt -l` checkout
      caveat) and an honest local-only-vs-CI test matrix; GitHub issue-forms
      `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml`
      (YAML-validated) + `.github/PULL_REQUEST_TEMPLATE.md`;
      `deploy/examples/{minimal-lan,tuned-setup}.md` plus an examples
      `README.md` index — `tuned-setup.md` documents a real gap (scheduler/
      GC env vars are read by `vault_api/config.py` but not yet forwarded
      by `deploy/compose.yaml`) with a working Compose-override recipe
      rather than a silently-broken `.env` example)*
- [x] **SECURITY.md + threat model** (WP 5.3 docs half, 2026-08-18; the
      pre-release security review below is a separate, still-open half of
      the same work package — see that item). Root
      [`SECURITY.md`](../SECURITY.md): supported-versions note (pre-release,
      no tags yet), reporting via GitHub's private vulnerability reporting
      for this repository (no email address anywhere, per the operator's
      constraint). The substantial part is
      [`docs/security/threat-model.md`](security/threat-model.md): every
      behavioural claim cited by file/line (or, for `docs/PROJECT_PLAN.md`
      itself, a section-plus-quote anchor rather than a line number, since
      this file grows under active editing) against the shipped code, not
      the ADRs' designs (same discipline WP 5.2 established for the README).
      Covers the LAN trust boundary and what an untrusted device on it can
      already do (`vault-core`'s `/depot/` path is unauthenticated by
      design, and its Host allowlist restricts *where* it can relay, not
      *who* may use it), the cache contents and why they are not a licence
      bypass, ADR-0004's credentials claim verified against the code (and
      exactly where a real Steam session lives at rest —
      `vault-steamprefill` volume), the WP 4h.1 personal-data surface
      (playtime/last-played: the API-level half of the operator's privacy
      stance has landed since — WP 4h.0's two env-only, off-by-default
      gates mean the API no longer answers unconditionally to anyone
      holding the one shared key. What remains open is the display-side
      half: no UI renders either field yet, so "off by default or
      dismissible" cannot be said fully met until WP 4h.2/4h.3 ship;
      flagged for whoever builds those to read the threat model's own
      framing first), outbound data flows (the opt-in Steam relay,
      the opt-in manifest oracle, and the CSP's one external cover-art
      host — what leaves the LAN and under what conditions), the ADR-0008
      event log, precisely what `VAULT_SETTINGS_READONLY` and the bypass
      banner do and do not mean, and supply-chain pinning (digest-pinned
      base images and SHA-pinned CI actions vs. this project's own
      mutable-tag image references, pending WP 5.5's first publish). Named
      plainly as out of scope rather than glossed over: physical host
      access, a compromised LAN, a malicious operator, and in-LAN DoS via
      the unauthenticated relay-and-store path. Reviewed by Opus (round 1:
      FAIL, four must-fixes — two broken-by-drift citations into this very
      file, an understated personal-data claim, a missing outbound-data-flows
      section, and the plan tick overreaching the delivered scope — all
      fixed; round 2: FAIL, two blockers plus should-fixes — §4 citations
      drifted again when WP 4h.0 rewrote that section in the same commit
      that added 109 lines to `routers/steam.py`, and §5's outbound-flows
      list falsely claimed exhaustiveness while omitting the Android app's
      own direct Steam Web API and OpenID calls — addressed in the WP
      5.3-fix follow-up commit; round 3: PASS, 2026-08-18). Several claims
      explicitly could not be substantiated from code alone and are listed
      as open gaps in the document's own closing section rather than
      asserted anyway.
- [x] **Pre-release security review (WP 5.3 review half, Fable mandatory)**
      — DONE 2026-10-01, after the api/core code freeze
      ([ADR-0016](adr/0016-api-core-code-freeze.md), freeze commit
      92e95be). Two passes on the strongest available model (Opus 5.5):
      pass 1 with two reviewers (server; clients/release), pass 2 over the
      remaining server areas. Fix packages, each reviewed to PASS:
      SEC-FIX-1 (9aef5f4, full-match Host allowlist; vault-core
      `cap_drop: ALL` plus five caps), SEC-FIX-2 (eea667d, signing secrets
      in a tag-only Environment, verifiable release assets, client
      hardening), SEC-FIX-3 (c20a33a, starlette 1.3.1 for CVE-2025-62727
      and others, every runtime dependency pinned), SEC-FIX-4 (901368e, no
      symlink following on the shared volume, pre-auth body guard, bounded
      zip errors), plus test fix 907de53 (the racing-delete test asserts
      the documented byte floor). Evidence: api suite 2000 passed /
      6 skipped; `verify-stack.sh` 211/211. Known limits recorded in
      threat-model §9: tinyproxy ConnectPort, event-log boot TOCTOU, GC
      unlink TOCTOU, no client-count cap, depot base resolved per request,
      plain-HTTP misses. Post-release items: web "forget key", removal of
      the per-level `O_NOFOLLOW` GC walk, the deletion byte-count lock.
      See §11 item 6.
- [ ] Announcement: r/selfhosted, r/homelab, LanCache Discord (stay fair:
      frame as a complement/alternative, not a "LanCache killer").
      Waits for v0.6.0 (user decision 2026-10-16: after the D18 rename;
      2026-10-18, ADR-0029: once schedules are in).
