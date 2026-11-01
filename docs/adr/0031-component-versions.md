# ADR-0031: Component versions under one product version

Status: Accepted (operator, 2026-10-18); written 2026-11-01; amended
2026-11-01 (operator decisions ENFORCE-RC22 Weg B: enforce from rc22;
AGENT-RC22 Weg A: the agent is rebuilt as 0.1.0-rc22)

Date: 2026-11-01

Related: ADR-0029 (version numbering, extended here), ADR-0016 addendum
"freeze exception: build version in every artifact (WP VER-1)" (its
single-version part is superseded here), roadmap entry RELEASE-SPLIT-1
(`docs/handover/post-0.1.0-roadmap.md`), `docs/PROJECT_PLAN.md` rc22
block, WP RELEASE-SPLIT-1a/1b/1c, WP DIST-1a (vault-api bakes the agent
files and APK), WP CI-SIGN-PIN-1 (APK certificate pin).

Supersedes in part: VER-1's rule that every artifact of a release run is
built and stamped with that run's version. The build-version plumbing
itself (build args, `vault-agent --version`, `GET /v1/about`) stays.

## Context

Every `v0.1.0-rcN` tag rebuilt and re-released every product with the
same version, also a component nobody had touched. The agent's code has
not changed since rc17, yet rc18 to rc21.1 each shipped a "new" agent;
operators saw an update hint for a binary that differed only in its
version stamp. The operator wants a component released only when it
changed, and its version to say when that was (decisions 2026-10-18,
revised the same day: no separate component tags, no own numbering).

## Decision

1. **One product version** stays (ADR-0029): `v0.X.Y(-rcN)` tags only.
   There are no component tags.

2. **Three components**, each carrying the product version in which it
   last changed:
   - **server**: the images vault-core, vault-api (with `web/`; the
     vault-runner role runs the vault-api image), vault-dns, vault-proxy.
     One component: one version for all four images.
   - **agent**: the vault-agent binaries plus the Windows Scheduled Task
     scripts and systemd units attached next to them.
   - **app**: the signed Android release APK.

   Components drift apart naturally: agent `1.2.0` next to server `1.5.0`
   when the agent last changed in 1.2.0. An agent-only fix ships as a
   product patch (`1.5.1`) and only the agent becomes `1.5.1`.

3. **Manifest.** `.github/components.json` (schema 1) is the one
   committed record. It lives under `.github/` next to the workflow and
   the script that read it, not in a top-level `release/` folder, because
   it is CI release metadata, not product code, and a new top-level
   directory would fall outside the repo structure of the project plan:

   ```json
   {
     "schema": 1,
     "tag_prefix": "v",
     "enforce": false,
     "components": {
       "agent": {
         "description": "...",
         "version": "0.1.0-rc17",
         "reuse": "none",
         "paths": ["agent/go/", "agent/packaging/systemd/", "..."],
         "exclude": ["agent/go/**/*_test.go", "**/README.md"],
         "artifacts": {"files": ["vault-agent-v{version}-linux-amd64", "..."]}
       }
     }
   }
   ```

   - `paths` are every build input of the component, shared ones
     included (Dockerfiles, `requirements.txt`, `.dockerignore`, `web/`
     for the server; `go.mod`/`go.sum` under `agent/go/`; the Gradle files
     and wrapper for the app). `dir/` is a directory, `*` a glob inside one
     segment, `**` across segments, anything else an exact file.
     `exclude` wins over `paths` (tests, READMEs: they do not change the
     artifact). The manifest itself belongs to no component.
     `app/gradlew.bat` (Windows launcher, CI builds with `gradlew`) and
     `app/keystore.properties.example` (template; signing comes from CI
     secrets) are deliberately not component paths.
   - `version` is the product version in which the component last
     changed, without the `v`.
   - `artifacts` names what that release published, with `{version}`
     filled from `version`: the image references, the agent release files,
     the APK name.
   - `reuse` says how an unchanged component's artifacts are reused:
     `retag` (images), `release-assets` (files attached from the recorded
     release), or `none` (not wired yet, the component is rebuilt).
   - `minimum_version` (agent and app only, optional; RELEASE-SPLIT-1b)
     is the oldest client the server still supports; it must not be newer
     than `version`. Both start at `0.1.0-rc17` (the build every alias in
     point 7 maps to).
   - `enforce` is the switch for the whole mechanism (below).
   - Readers of an OLDER tag's manifest take only
     `components.<name>.version` from it, whatever its `schema`
     (RELEASE-SPLIT-1b), so a schema change cannot fail the next tag
     run; a missing or malformed version there is an error.

4. **Detection rule.** At release time
   `.github/scripts/component-versions.py plan` diffs the trees of the
   previous release tag (`git describe --tags --abbrev=0 --match 'v[0-9]*'
   <commit>^`) and the release commit. A component with at least one
   changed file under its `paths` (minus `exclude`) is **changed**: it
   takes the new product version and is rebuilt. Otherwise it is
   **unchanged**: it keeps the version recorded in the manifest of the
   previous tag (the committed manifest on the first run, when the
   previous tag has none) and its artifacts are reused.

5. **The release commit records the plan.** Before a tag is set, the
   release commit runs `component-versions.py write --version <new>
   --previous-tag <prev>`; on the tag run, CI runs `check` and fails when
   the committed versions differ from the plan. Other runs (rehearsals,
   `dev-<sha>` versions) skip the check. The check fails the run only
   with `enforce: true`; in dry run (`enforce: false`) a stale manifest is
   a `::warning::` naming the exact `write` command and the run continues,
   because a failed tag run burns the version (tags are never deleted).
   With enforce on, the manifest in a tagged tree always tells the truth
   about that release.

   **Stale-manifest guard (RELEASE-SPLIT-1b).** Every plan also replays
   the previous tag's own plan (that tag's manifest paths, the versions
   recorded at the tag before it) and compares it with what the previous
   tag recorded (`previous_check` output: `match`, `mismatch`,
   `no-manifest`, `first-tag`, `unreadable`). With enforce on, anything
   but `match`, `no-manifest` (the previous tag predates the manifest; the
   committed versions come from the history table below) or `first-tag`
   fails the `component-plan` job (exit 2, "stale-manifest guard"): a
   dry-run tag whose release commit skipped `write` would otherwise make
   the next enforcing release reuse an outdated artifact. In dry run the
   verdict is only reported. Accepting `no-manifest` and `first-tag` is
   the deliberate bootstrap exception: rc22, the first enforcing release,
   has a previous tag (`v0.1.0-rc21.1`) without a manifest, so there is
   nothing to replay and it starts from the committed manifest, whose
   versions come from the git history table below.

   **Release checklist (from rc22).** Before tagging `v<new>`:
   1. `python3 .github/scripts/component-versions.py write --version <new>
      --previous-tag <prev>`
   2. `python3 .github/scripts/component-versions.py check --version <new>
      --previous-tag <prev>` (must print "versions match the plan")
   3. commit `.github/components.json` in the release commit, then tag
      that commit.

   Recovery: if a tag's release commit skipped `write` while enforce was
   on, that tag's manifest is stale and the guard fails the next
   enforcing plan. The next tag must then be one dry-run tag
   (`"enforce": false`, release commit runs `write`); the tag after it
   may enforce again.

6. **Reuse of an unchanged component** (by tag and digest):
   - server images: the recorded tag (`<image>:<recorded version>`) is
     resolved to its manifest-list digest, and that digest gets the new
     product version tag (`docker buildx imagetools create`), no `sha-*`
     tag (that one names the commit an image was built from). Compose's
     `${VAULT_IMAGE_TAG}` pins keep working with the new product version,
     and the release body lists the digest, which is the old one.
   - agent files and APK (RELEASE-SPLIT-1b): the jobs `agent-reuse` and
     `android-reuse` replace `agent-build` / `android-build` for an
     unchanged client. They download the files named in `artifacts` from
     the release of the recorded version (`gh release download` with the
     run's read-only `GITHUB_TOKEN`; no other token is needed) and
     `.github/scripts/release-assets.py verify` checks them, failing
     closed: every file must match GitHub's own sha256 asset digest (a
     file without one is refused), the SHA-256 the release body records
     in its verify block when it records one (`agent-binaries` writes
     those lines from 1b on: `<sha256>  SHA256SUMS` and
     `<sha256>  steamhangar-app-v<version>.apk`), and for the agent
     `SHA256SUMS` must list exactly the other files with matching hashes.
     They upload the files under the builders' artifact names
     (`release-agent`, `release-apk`), so the release jobs attach them to
     the new release unchanged, under their original versioned names. A
     reused APK skips the CI-SIGN-PIN-1 certificate check with a summary
     line (it was checked when it was built).
   - vault-api bakes the agent files and APK of their recorded versions
     (from the reuse download or this run's build, whichever the plan
     chose) and is rebuilt in every run, also when the server is
     unchanged: a retag of the server's recorded vault-api image would
     carry the client files of THAT release. Rebuilding an image to bake
     client files does not change the server's recorded version. The
     clients' versions are build args (`VAULT_AGENT_VERSION` /
     `_BUILD` / `_MINIMUM`, `VAULT_APP_*`; `_VERSION` = component version,
     `_BUILD` = the version stamped on the baked files, i.e. the run's
     version when rebuilt and the recorded one when reused), so
     `vault_api/dist.py` names and links each file by its own version.
   - Job gating: `publish`, `android-release` and `agent-binaries` run on
     `!cancelled()` and accept per client exactly one of "the builder
     succeeded" or "the plan said do not build (builder skipped) and the
     reuse job succeeded"; a failed builder or reuse job, or a builder
     skipped for any other reason, stops the release.

7. **Alias list** (implemented in RELEASE-SPLIT-1b). Agent builds
   `0.1.0-rc18` to `0.1.0-rc21.1` are the rc17 source with another
   version stamp. A small alias list in the server maps them to
   `0.1.0-rc17`, so the Clients "version differs" hint does not flag them.
   The roadmap named rc18 to rc20.2; git shows rc21 and rc21.1 unchanged
   too (table below), so the list covers rc18 to rc21.1
   (`vault_api/client_versions.py` `AGENT_BUILD_ALIASES`, only existing
   tags; a test checks it against the git history when the tags are
   present).

8. **Minimum versions** (RELEASE-SPLIT-1b). The Clients hint compares an
   agent with the bundled agent version (after the alias list), not with
   the server version. The server states a minimum agent and app version;
   below it the hint says an update is needed. (This pulls that part of
   the v0.4.0 "Weg B for agents" forward; the OS report stays in v0.4.0.)
   `GET /v1/dist` and `GET /v1/settings` carry the same `clients` object:
   per client `{version, build, minimum, same_build}` (`same_build` =
   component version, build stamp and, for the agent, the aliases of the
   component version), `null` for a local build. The web and Android
   Clients pages read it from `GET /v1/settings` (the request they already
   make for `server_version`): an agent version in `same_build` is
   current, one below `minimum` "is older than the minimum", any other
   differs from "the server's agent"; without `clients` (a server before
   1b) they compare with the server version as before. Versions compare in
   natural pre-release order (`rc9` < `rc17` < `rc21.1` < `rc22` <
   release), the same rule in the script, the web and the app.

9. **About and release notes** list each component with its version and
   "unchanged since ..." where it applies (RELEASE-SPLIT-1c).

10. **History:** no retro tags. The table below is the record.

## Rollout in three packages

- **1a (this ADR, the manifest, the plan script, the CI plan):** the
  `component-plan` job in `publish.yml` runs before every build job, puts
  the plan into the run summary and as a workflow artifact, and gates the
  builds with its `*_build` outputs. `enforce` is `false` (dry run): every
  component is still built. With `enforce: true` the server's vault-core,
  vault-dns and vault-proxy legs retag instead of building; agent and app
  (`reuse: none`) are still rebuilt.
- **1b (done 2026-11-01):** vault-api bakes the recorded agent/APK;
  agent and app get `reuse: release-assets`; alias list; minimum
  versions; tolerant previous-manifest read; stale-manifest guard.
  `enforce` is `true` from the rc22 tag on (operator decision
  2026-11-01 ENFORCE-RC22 Weg B, section "Switching enforce on" below).
  rc22 still rebuilds all three components (operator decision 2026-11-01
  AGENT-RC22 Weg A): SAMPLE-IP-1 changed two help/error strings in
  `agent/go/cmd/vault-agent/hosts.go` and `agent/go/hostsfile/hostsfile.go`
  that are compiled into the binary, so the agent honestly becomes
  `0.1.0-rc22`. Agents rc17 to rc21.1 then show "differs from the
  server's agent" once (they are at or above the minimum `0.1.0-rc17`).
- **1c (done 2026-11-01):** About page and release notes per component.
  Decision ABOUT-VERSION-1 (roadmap, operator 2026-11-01: Weg A):
  `GET /v1/about` gains `release` = `{version, components: [{name,
  version}]}` (product version; server, agent, app); the server's
  component version comes from the new build arg
  `VAULT_SERVER_COMPONENT_VERSION` (`server_version` of the plan;
  `VAULT_VERSION` stays the product version). The About pages (web and
  Android) show "SteamHangar <product>" as the main line and Server, Agent,
  App below it, "(unchanged since <version>)" for a component older than
  the product; a local build shows no list. The release body's verify block
  starts with "## Components": each component with its version and
  "rebuilt" or "unchanged since <version>" (", rebuilt" when a dry run still
  built an unchanged one). Open: ABOUT-CORE-RETAG-1 (a retagged vault-core
  reports its old version, see PROJECT_PLAN).

## Rehearsing the reuse path and switching enforce on

**Rehearsals (required before the rc22 tag, the first enforcing tag).**
A `workflow_dispatch` from the rc22 HEAD exercises no reuse path, because
all three components changed since `v0.1.0-rc21.1`. Two runs, both of
`publish.yml` with `workflow_dispatch` on a `wp/` branch with
`"enforce": true` (never on a `v*` tag ref: a dispatch on a tag ref
publishes like a tag push; off a tag nothing is pushed or attached):

1. **Reuse rehearsal** on a throwaway `wp/` branch whose commit restores
   the agent paths and the app paths of the manifest (`agent/go/`, the
   agent packaging files, `app/app/src/main/`, the app Gradle files and
   wrapper, ...) to their `v0.1.0-rc21.1` content; the server stays
   changed. The plan must show `agent_build=false` and `app_build=false`;
   the reuse jobs download and verify the recorded releases, `publish`
   builds vault-api with the reused files. Check the run summary:
   component plan table, the "reused, NOT rebuilt" sections, and the
   baked-downloads listing of vault-api with the recorded file names.
   The operator deletes the branch afterwards (the devbox may not delete
   remote branches).
2. **Plain rehearsal** from HEAD (all three components changed): checks
   the all-build path with enforce on.

The server's retag path (unchanged server) resolves the recorded images
with an anonymous `docker buildx imagetools inspect` of
`ghcr.io/steamhangar/<image>:<recorded version>` (off a tag only
"rehearsal: would run ... imagetools create" is printed); that needs the
ghcr packages to be **public** (they are meant to be, see the S6 note in
publish.yml); a private package fails the retag step.

**Switching enforce on (operator decision 2026-11-01, ENFORCE-RC22
Weg B).** Reuse is enforced from the rc22 tag on: `.github/components.json`
carries `"enforce": true` in the rc22 release commit. The original rule
(switch on only after a dry-run tag whose check printed "versions match
the plan") is waived for rc22 because no tag before it can satisfy it:
`v0.1.0-rc21.1` has no manifest. rc22 therefore bootstraps from the
committed manifest (versions from the history table below), and the
stale-manifest guard accepts `no-manifest` and `first-tag` as that
deliberate exception. Every later enforcing release is guarded
mechanically: its plan fails when the previous tag's manifest does not
match a replayed plan. Preconditions for the rc22 tag:

1. both rehearsals above passed (reuse rehearsal on a restored `wp/`
   branch, plain rehearsal from HEAD), enforce on;
2. the ghcr packages are public (anonymous `imagetools inspect`);
3. the release checklist (section 5) ran: `write`, then `check` prints
   "versions match the plan", manifest committed in the release commit.

## History (git, per component paths of the manifest)

`git diff` of the component paths between consecutive tags
(`component-versions.py history $(git tag -l 'v*' --sort=creatordate)`).
"= X" is the version the component would have carried.

| Tag | server | agent | app |
| --- | --- | --- | --- |
| `v0.1.0-rc1` | changed | changed | changed |
| `v0.1.0-rc2` | = 0.1.0-rc1 | = 0.1.0-rc1 | = 0.1.0-rc1 |
| `v0.1.0-rc3` | changed | = 0.1.0-rc1 | = 0.1.0-rc1 |
| `v0.1.0-rc4` | changed | = 0.1.0-rc1 | = 0.1.0-rc1 |
| `v0.1.0-rc5` | changed | = 0.1.0-rc1 | = 0.1.0-rc1 |
| `v0.1.0-rc6` | changed | = 0.1.0-rc1 | = 0.1.0-rc1 |
| `v0.1.0-rc7` | changed | = 0.1.0-rc1 | = 0.1.0-rc1 |
| `v0.1.0-rc8` | changed | = 0.1.0-rc1 | = 0.1.0-rc1 |
| `v0.1.0-rc9` | changed | changed | = 0.1.0-rc1 |
| `v0.1.0-rc10` | changed | changed | changed |
| `v0.1.0-rc11` | = 0.1.0-rc10 | = 0.1.0-rc10 | = 0.1.0-rc10 |
| `v0.1.0-rc12` | changed | = 0.1.0-rc10 | changed |
| `v0.1.0-rc13` | changed | changed | changed |
| `v0.1.0-rc14` | changed | = 0.1.0-rc13 | changed |
| `v0.1.0-rc15` | changed | = 0.1.0-rc13 | changed |
| `v0.1.0-rc16` | changed | = 0.1.0-rc13 | changed |
| `v0.1.0-rc17` | changed | changed | changed |
| `v0.1.0-rc18` | changed | = 0.1.0-rc17 | = 0.1.0-rc17 |
| `v0.1.0-rc18.1` | = 0.1.0-rc18 | = 0.1.0-rc17 | = 0.1.0-rc17 |
| `v0.1.0-rc19` | changed | = 0.1.0-rc17 | changed |
| `v0.1.0-rc19.2` | changed | = 0.1.0-rc17 | changed |
| `v0.1.0-rc19.3` | changed | = 0.1.0-rc17 | changed |
| `v0.1.0-rc20` | changed | = 0.1.0-rc17 | changed |
| `v0.1.0-rc20.1` | changed | = 0.1.0-rc17 | = 0.1.0-rc20 |
| `v0.1.0-rc20.2` | changed | = 0.1.0-rc17 | = 0.1.0-rc20 |
| `v0.1.0-rc21` | changed | = 0.1.0-rc17 | changed |
| `v0.1.0-rc21.1` | changed | = 0.1.0-rc17 | changed |

Checked against the roadmap's analysis: the app was unchanged in rc11,
rc18, rc18.1, rc20.1 and rc20.2 (confirmed). The agent was unchanged
since rc17 not only in rc18 to rc20.2 but also in rc21 and rc21.1
(corrected). rc2, rc11 and rc18.1 changed no component at all (CI-only
releases). The manifest therefore starts at server `0.1.0-rc21.1`, agent
`0.1.0-rc17`, app `0.1.0-rc21.1`.

## Consequences

- A component's version answers "which release last changed it"; the
  product version answers "which release is this". Both are shown
  (RELEASE-SPLIT-1c).
- The release commit gains one step (`component-versions.py write`);
  a tag without it warns in `component-plan` in dry run and fails
  there with enforce.
- A component path that is missing from the manifest is a silent
  "unchanged" for that file. New build inputs (a new `COPY` in a
  Dockerfile, a new Gradle module) must be added to the manifest in the
  same package; the 1a tests pin today's Dockerfile `COPY` sources.
- The unchanged path is only as good as the recorded release: if its
  artifacts are gone (deleted release, expired image), the component has
  to be rebuilt by touching it or by a manual run with `enforce: false`.
