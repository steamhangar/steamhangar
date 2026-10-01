# ADR-0013: Product rename, SteamVault → SteamHangar

Date: 2026-08-22
Status: Accepted (operator decision 2026-08-22, WP RN-1)

## Context

The working title "SteamVault" collides with existing, unrelated software:
Steam piracy/DRM-bypass tools that use the same name, and an established
Steam achievement tracker. Neither collision is acceptable for a project
that intends a public release (Phase 5) — a search for "SteamVault" should
not surface this project next to a tool it has nothing to do with and no
wish to be associated with.

**Two other names were considered and dropped the same day, before either
was ever committed to the default branch:** SteamKeep and SteamSilo. Both
were rejected on the same session as candidates once SteamHangar was found
to be free of the same collision and to read better against the project's
actual shape (a place aircraft/vehicles are stored and maintained, not a
sealed container) — they are recorded here only because the hosts-file
managed-block detection in `agent/go/hostsfile/hostsfile.go` defensively
recognizes both as legacy marker prefixes (see below); neither ever shipped
in a commit under either name.

The operator's decision: rename the product to **SteamHangar** everywhere
the product name itself appears, while leaving alone everything that is
either (a) already shipped, user-facing, and expensive or unsafe to rename,
or (b) a historical record of a decision made when the old name was live.

## Decision

Three tiers, applied uniformly across the whole repository:

### Tier 1 — renamed: the product word

Every occurrence of "SteamVault"/"steamvault" as the PRODUCT NAME, in prose,
titles, and headers: root docs (`README.md`, `CONTRIBUTING.md`,
`SECURITY.md`, `LICENSE`'s copyright line, `CLAUDE.md`, `.claude/agents/*.md`),
every component README (`agent/`, `api/`, `app/`, `core/`, `deploy/`,
`dns/`), `.github/` (issue templates, the nginx CI verification script's
comments), `docs/PROJECT_PLAN.md`'s prose, `docs/security/threat-model.md`'s
prose, `deploy/examples/*.md`, the frozen design mockup
(`docs/design/vault-app-mockup.html` and its NOTES), the Android app's two
user-visible strings (`app_name`, `settings_notifications_desc` in
`app/app/src/main/res/values/strings.xml`), four Kotlin doc-comment prose
lines that named the product without touching the package/scheme/theme
identifiers around them, and the header comments in five Android build
files (`app/build.gradle.kts`, `app/gradle/libs.versions.toml`,
`app/app/proguard-rules.pro`, `app/.gitignore`,
`app/keystore.properties.example`) plus their illustrative example keystore
filenames/aliases (cross-referenced 1:1 between `app/README.md`'s `keytool`
walkthrough and `app/keystore.properties.example` — both renamed together
so the two stay in agreement).

### Tier 2 — renamed: the shipped namespace

Everything that is not prose but IS safe to rename because nothing depends
on the OLD value surviving an upgrade:

- Docker image namespace `steamvault/vault-*` → `steamhangar/vault-*`
  (`deploy/compose.yaml`, all four Dockerfiles, and the tests that pin it:
  `api/tests/test_version_pin.py`'s regex and docstring,
  `deploy/tests/verify-stack.sh`).
- The Compose **project name**, `name: steamvault` → `name: steamhangar`
  in `deploy/compose.yaml` — see the volume-orphan consequence below.
- The `vault-runner` container name default and `deploy/tests/verify-stack.sh`'s
  own isolated project name (`steamvault-verify` → `steamhangar-verify`).
- The OCI `org.opencontainers.image.source` labels in all four Dockerfiles,
  corrected to the REAL remote — `https://github.com/Riviera822/steamhangar`
  — not a speculative org (the pre-rename value,
  `https://github.com/steamvault/steamvault`, was already wrong on both the
  org and the repo name; this WP fixes both, not just the product word).
- The Go module path, `github.com/Riviera822/steamvault/agent` →
  `github.com/Riviera822/steamhangar/agent`, and every internal import across
  `agent/go/**`.
- **The `X-LanCache-Processed-By` heartbeat header VALUE — wire-visible,
  handled deliberately.** nginx emits `add_header X-LanCache-Processed-By
  "steamvault";` at `/lancache-heartbeat` (`core/nginx/nginx.conf`,
  `core/docker/nginx.conf.template`); both were changed to `"steamhangar"`
  together, and `deploy/tests/verify-stack.sh`'s matching assertion
  (`assert_contains "$hb" "X-LanCache-Processed-By: steamhangar" ...`) was
  updated in the same commit — confirmed green in a real run (185/185, see
  Consequences). The header NAME, `X-LanCache-Processed-By`, is UNCHANGED —
  that is LanCache's own compatibility contract (the shape SteamPrefill's
  cache-discovery probe looks for), not this project's product name, and
  renaming it would be a Tier-3-class mistake even though it is not
  literally on the Tier-3 list. The VALUE is free to change: SteamPrefill's
  probe only checks that the header is *present*, per
  `poc/steamprefill/PROTOCOL.md` — no shipped or historical code in this
  repository matches on the specific string `"steamvault"` inside that
  header (confirmed by grep across the live tree; the only remaining
  `X-LanCache-Processed-By: steamvault` occurrences are the two frozen
  `deploy/VERIFICATION-*.md` transcripts and `poc/`'s own PoC-era config,
  both historical and both out of this WP's footprint). Because this value
  is answered to anything on the LAN that probes the heartbeat endpoint —
  not just to this project's own tooling — it is called out here explicitly
  rather than folded silently into the general Tier-2 list: an operator or
  third-party script that happened to assert on the OLD literal value would
  see it change with this rename, unlike almost everything else in Tier 2.
  **Stronger than a source-read: this repository's own history already
  proved the value is free-form.** `poc/conf/nginx.conf` and
  `poc/conf/nginx-passthrough.conf` shipped `"steamvault-poc"` — already a
  DIFFERENT string from production's `"steamvault"` — and SteamPrefill
  detected the cache through it and pulled 1272 chunks anyway
  (`poc/steamprefill/PROTOCOL.md:57`,
  `poc/steamprefill/RESULTS-STEAMPREFILL-20260804-195348.md`: 1272 of 1277
  requests were `/depot/<id>/chunk/<sha1>`, 99.6%). Two different literal
  values, one real client, both worked.
- **The outbound `User-Agent` string — wire-visible to third parties,
  same reasoning as the heartbeat value.** `api/vault_api/oracle.py:195`
  and `api/vault_api/steam_relay.py:207` now send
  `"SteamHangar-vault-api/0.1 (...)"` instead of `"SteamVault-vault-api/0.1
  (...)"` on every outbound request the optional manifest oracle
  (`api.steamcmd.net`) and the optional Steam Web API relay (Valve) make.
  This belongs in Tier 2 for the identical reason the heartbeat value does:
  it is answered to a party outside this project's own tooling, not just
  read back by it. Unlike the heartbeat value, there is no existing test
  pinning either literal (`rg "SteamHangar-vault-api" api/tests` → zero
  matches) — a pre-release gap this WP found but does not fix, recorded
  here rather than silently carried forward unremarked.
- The nginx internal-only `/__steamvault_force_miss__` location (both
  `core/nginx/nginx.conf` and `core/docker/nginx.conf.template`, kept in
  agreement) → `/__steamhangar_force_miss__`. Verified no other file
  referred to this location.
- The hosts-file managed-block markers `agent/go/hostsfile/hostsfile.go`
  writes: `BeginMarker`/`EndMarker`/`beginPrefix`/`endPrefix` now read
  `steamhangar-agent`, and `BackupSuffix` is now `.steamhangar.bak`
  (propagated into `write.go`'s temp-file prefix and every doc/test that
  quotes either value).

### Tier 3 — deliberately NOT renamed

- `dev.steamvault.app` — the Android Kotlin package name and
  `applicationId`. Changing it would break every existing install's update
  path (Android treats a changed `applicationId` as a different app); there
  is no compiler available in this environment to even verify a rename
  compiles. Left untouched in `app/app/build.gradle.kts`, every Kotlin
  source file's `package`/`import` lines, `AndroidManifest.xml`, and every
  real file path that names it in documentation.
- The `steamvault://` URI scheme the app registers for its Steam OpenID
  return, and the Kotlin identifiers `SteamVaultTheme` /
  `@style/Theme.SteamVault` — all load-bearing identifiers, not label text.
- Internal `vault-*`/`VAULT_`/`vault_api`/`/vault`/volume names — these were
  never spelled "steamvault" in the first place (the shared word is "vault",
  not "steamvault"), so the rename does not touch them at all.
- Historical records: `docs/adr/0001` through `0012`, `docs/WORKPACKAGES.md`,
  `deploy/VERIFICATION-*.md`, all of `poc/`, and root `.gitignore`'s
  `steamvault_projektplan.md` line (the operator's own private, uncommitted
  external planning file — its name is not this repository's to rename).
  These describe decisions and evidence AS THEY WERE, under the name live
  at the time; rewriting them under the new name would misrepresent when
  each was actually true.
- `docs/PROJECT_PLAN.md`'s one `steamvault://` line (WP 4b.3's note) — it
  quotes the shipped, Tier-3 URI scheme, not the product name.

### Two additional persisted-state identifiers, found during this WP and
### held to the same Tier-3 standard even though the brief did not name them

The brief's four call-out traps are about markers/paths that are DETECTED
or CITED — this WP found two more values that are neither, but share the
same failure shape (a string baked into already-shipped client state, whose
silent rename orphans that state on upgrade):

- **Android**: `EncryptedCredentialStore.kt`'s
  `PREFS_FILE_NAME = "steamvault_secure_prefs"` — the `EncryptedSharedPreferences`
  file name. Renaming it would make an existing install's already-stored,
  encrypted settings (including a device-local Steam Web API key, where
  configured) invisible after an app update, silently reverting the user to
  first-run state with no error.
- **Web**: every `steamvault.*` `localStorage` key
  (`web/js/api.js`'s `apiKey`/`demoMode`, `web/js/views/library.js`'s
  `libraryLayout`, `web/js/components/decision-panel.js`'s `dismissed`/
  `collapsed`, `web/js/demo-data.js`'s `demoLibrarySize`) — renaming the key
  string would silently drop a returning browser's saved API key, layout
  choice, and dismissed-banner state on the next page load, with no error
  either.

Both are left exactly as they were, including every comment, test, and doc
line that quotes them (`docs/security/threat-model.md`, `web/tests/README.md`,
`docs/PROJECT_PLAN.md`, `web/css/app.css`'s comment). A future work package
could migrate either under its own explicit read-old-key-once mechanism;
this WP's footprint is the product-name rename, not a state-migration
design, so it deliberately stops at "found and left alone, on the record."

**Reconciling this against the volume-orphan consequence below, which
looks like the opposite call at a glance:** both are defensible, but for
different reasons that pull in different directions. The volume-prefix
change was a *forced* consequence of a Tier-2 rename this WP was already
doing (the Compose project name), and its blast radius was *measured*:
`docker volume ls | grep -i steamvault` returns nothing on any host this
project has touched, so the rename is verified to strand no live data.
The two persisted-state identifiers are the opposite on both counts: an
identical-behavior rename with zero product-name benefit (nothing reads
`"steamvault_secure_prefs"` or `"steamvault.apiKey"` as a name — they are
opaque keys), and their blast radius is NOT measurable from here — unlike
a Docker host this project controls, an operator's own already-installed
phone or already-open browser tab is not something this WP can query
before deciding. Absence of evidence there is not evidence of absence.
The honest position is not "both renames are equally safe," it is "one was
required and checked; the other was optional and uncheckable, so it does
not happen."

## Consequences

- **Volume orphaning, measured, not just asserted.** `deploy/compose.yaml`'s
  `name: steamvault` → `name: steamhangar` changes the prefix Docker Compose
  gives every named volume with no `name:` override
  (`vault-cache`/`vault-db`/`vault-steamprefill`/`vault-steamprefill-home`
  become `steamhangar_vault-*` instead of `steamvault_vault-*` on a fresh
  `docker compose up`). This orphans any EXISTING `steamvault_*` volume on a
  host that already ran the stack under the old project name — Compose will
  create fresh, empty volumes under the new name rather than reusing the old
  ones. Measured before shipping this change: **no `steamvault_*` volumes
  exist on any host this project has been deployed to** (pre-release,
  Phase 5 not yet reached, no known external deployment) — so no live cache,
  database, or SteamPrefill session is stranded by this rename. An operator
  who deploys between this commit and a hypothetical future release under
  the old name would need to either rename their volumes by hand
  (`docker volume` has no rename primitive; the real fix is a one-time
  `docker run --rm -v old:/from -v new:/to alpine cp -a /from/. /to/`) or
  accept fresh volumes. This is called out explicitly rather than left
  implicit because §4's "no LRU, no automatic eviction" design means an
  orphaned `vault-db` volume silently drops schedule state, job history,
  and depot mappings — not just cache bytes an operator can easily refill.
- The Go module path change means any external consumer of
  `github.com/Riviera822/steamvault/agent` (there are none known — the
  module has never been tagged or published) would need to update its
  import path. No compatibility shim is provided; this is a pre-release
  rename, not a deprecation.
- The hosts-file marker rename is self-healing by construction: detection
  matches the old prefix (`steamvault-agent`, plus the never-shipped
  `steamkeep-agent`/`steamsilo-agent` defensively), and the very next
  `vault-agent hosts apply` rewrites any such block to the current marker
  text, because `Apply` always renders with the CURRENT `BeginMarker`/
  `EndMarker` regardless of which prefix matched during detection — there is
  no separate migration step. `agent/go/hostsfile/hostsfile_test.go` pins
  all three legacy prefixes: found, healed on `Apply`, and fully removable
  on `Remove`.
- Every doc, test literal, and code comment that CITES one of the Tier-2
  values (the nginx header value, the force-miss location, the hosts
  markers/backup suffix, the image namespace, the Go import paths) was
  updated in the same commit as the value itself, so no citation now points
  at a string the shipped code no longer produces.
- No commits exist under "SteamKeep" or "SteamSilo" — this ADR is the only
  place either name appears outside the hostsfile legacy-detection code and
  its tests.
- **Two non-rename corrections made in passing, in files this WP was
  already editing for the rename itself** (the project's own
  stated-mechanism-honesty rule, `docs/LEARNINGS.md`, applies to any file
  touched, not only the lines that motivated touching it):
  `README.md`'s "62-check verification script" line was stale (the suite
  has grown to 185 checks since WP 1.9) — corrected to 185, matching the
  figure `deploy/README.md` already carries from its own measured run.
  `deploy/tests/verify-stack.sh:293`'s "109 checks total, up from WP D1's
  73" is a historical measurement from the packaging WP's own review and
  was NOT rewritten (that would falsify a real past result: that run
  genuinely executed 109 checks, not 185) — instead reworded to
  "109 checks total AT THAT TIME" with a parenthetical noting the suite
  now stands at 185, so neither the historical fact nor the current total
  is misstated.
- **A pre-existing divergence this WP did NOT fix, recorded so WP 5.5 does
  not ship it silently:** `docs/WORKPACKAGES.md`'s WP 5.5 brief ("Multi-arch
  images to ghcr.io") still names `ghcr.io/steamvault/{core,api,dns}` and
  `ghcr.io/steamvault/*` as the publish target. `docs/WORKPACKAGES.md` is
  historical/out-of-footprint by this WP's own rule (the orchestrator logs
  RN-1 there at commit), so it was correctly left untouched — but WP 5.5
  itself is UNEXECUTED, unlike the other historical entries in that file,
  which describe WPs that already shipped. Whoever eventually executes WP
  5.5 from that brief as written would publish to the now-dead
  `steamvault` namespace instead of `steamhangar`. Not this WP's file to
  edit; flagged here so the discrepancy is not silently inherited.
- **B1 fix (review round 1):** `agent/README.md`'s two `wsl` command
  examples (Go build/vet/test; the hosts sandbox driver) and
  `agent/tests/sandbox/run-hosts-sandbox.sh`'s own usage comment had
  hardcoded the LOCAL dev machine's checkout path
  (`/mnt/c/claude-dev/SteamVault/...`). The first pass of this WP
  renamed the product word inside that path, producing
  `/mnt/c/claude-dev/SteamHangar/...` — a path that does not exist on this
  or any other checkout (measured: `cd` into it fails with "No such file
  or directory"), breaking the only documented way to run either command.
  These are filesystem paths, the same category as the ADR-0004 links and
  the `dev/steamvault/app/...` citations already left alone elsewhere in
  this WP — the fix is to de-hardcode them
  (`/mnt/c/path/to/your/checkout/...`) rather than revert to the old
  literal, since an operator's checkout directory is never this project's
  to name and may be renamed again later. Verified by running
  `agent/README.md`'s documented command verbatim, with a real path
  substituted for the placeholder, against this WP's own worktree: `wsl
  bash -c "cd /mnt/c/claude-dev/SteamVault/.claude/worktrees/<this
  worktree>/agent/go && go build ./... && go vet ./... && gofmt -l . &&
  go test ./..."` — build and vet silent (success), `gofmt -l` listed
  every file (the pre-existing CRLF-checkout artifact `docs/LEARNINGS.md`
  already names, not a new issue), and all six packages `ok` (cached from
  the prior identical run).
- **Measured, not asserted:** `deploy/tests/verify-stack.sh` run against the
  renamed stack in WSL2 (Docker Engine 29.1.3/Compose 2.40.3) — **185/185
  checks passed**, all four images built and listed as `steamhangar/vault-*`,
  the renamed `X-LanCache-Processed-By: steamhangar` heartbeat assertion
  passed, the renamed `<title>SteamHangar</title>` web-shell assertion
  passed, and the isolated verify run's own project/container/volume names
  (`steamhangar-verify-*`) came up and tore down cleanly.
  `core/docker/check-config-drift.sh` (native vs. container nginx config)
  passed standalone after the header-value and force-miss-path renames.
  `pytest` from `api/`: 1809 passed, 1 skipped. `node --test
  "web/tests/*.test.js"`: 680 passed. Go build/vet/test
  (`agent/go`, WSL2): all six packages green, module resolved as
  `github.com/Riviera822/steamhangar/agent`, zero stale
  `Riviera822/steamvault` imports.

## Addendum (2026-09-30): the remote moved to the `steamhangar` organization

The "REAL remote" recorded above (`https://github.com/Riviera822/steamhangar`,
and the Go module path `github.com/Riviera822/steamhangar/agent`) was
accurate on 2026-08-16. The repository has since moved into a GitHub
organization and lives at `https://github.com/steamhangar/steamhangar`
(`docs/PROJECT_PLAN.md` §11 item 7; GitHub redirects the old URL). The
issue-template Discussions link and the OCI `image.source` labels in the
Dockerfiles point at the organization; images publish as
`ghcr.io/steamhangar/*`. The Go module path is still
`github.com/Riviera822/steamhangar/agent` (`agent/go/go.mod`) — an
internal identifier, not a second remote; moving it is an agent-side
change for its own package, not part of this addendum.
