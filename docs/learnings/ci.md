# Learnings: CI / GitHub Actions

Scope: workflows, annotations without a token, goldens branches, runner differences (SQLite, inodes), API rate limits. Part of the learnings index
[`docs/LEARNINGS.md`](../LEARNINGS.md); entries moved verbatim by
DOC-DIET-1 (rc22), original section headings kept. Append new findings
at the end, under a dated heading, one entry each with the WP that proved it.

## CI / GitHub Actions
- The stock nginx image entrypoint soft-fails: `20-envsubst-on-templates.sh`
  returns 0 WITHOUT rendering when the template dir is missing or the output
  dir is unwritable, and the image ships its own nginx.conf — a gate driving
  the real entrypoint must assert the rendered config is actually ours
  (grep a repo-owned token like the log_format name) or it green-lights the
  stock config (WP 5.1 reviewer catch).
- Values hand-copied from a Dockerfile into CI (image ref, ENV wiring) are
  drift surfaces — derive them (`sed -n 's/^FROM[[:space:]]\{1,\}//p'`) or
  grep-assert they still match before use, and require `@sha256:` (WP 5.1).
- PSScriptAnalyzer's PSUseCompatibleSyntax checks the AST against stored
  version profiles — it does not need to run UNDER 5.1; only the raw
  `Parser::ParseFile` check does. Split: parser under `shell: powershell`,
  analyzer under `pwsh` (5.1 hosts may not see the runner's preinstalled
  module path) (WP 5.1).

## 2026-10-09/10 — rc13 night run (CORE-FIX-4d, WEB-FEAT-6, DEPS-1, HYG-1, WEB-FIX-10, AGENT-FIX-3)
- A red CI where only the docker jobs fail (`image / docker build *`,
  `core / nginx -t`) on a package that touches none of them is usually
  the runner's registry access: the check-run annotations endpoint
  (readable without auth, unlike job logs) showed `auth.docker.io` 504
  and Docker Hub's unauthenticated pull limit. Prove it by pushing
  unchanged main to a throwaway `wp/` branch; once it clears, one
  integration branch with all passed packages needs only one CI run.

## 2026-10-13/14 — rc15/rc16 (D9, D10, UPD-5, UPD-6, D15a-c)
- Golden screenshots are refreshed on a dedicated `wp/*-goldens` branch
  where CI commits them as a bot. Commits pushed with `GITHUB_TOKEN`
  trigger no new CI run, so push an empty or follow-up commit to get the
  branch checked.

## 2026-10-14 afternoon — rc17 integration (D15c/d, NAMES, RUNNER, AGENT-ADDR-1, CACHE-COMPLETE-1a-c)
- A red CI job without log access: failed test names can still be read
  without a token through check-run annotations. A throwaway `wp/` branch
  whose conftest prints one `::error` line per failed test
  (pytest_terminal_summary) exposes them via the public
  check-runs/{id}/annotations endpoint. Match failures to the packages a
  tree contains before calling something a flake.

## 2026-10-14/15 — rc18 night run (UPD-FIX-1, D16a-c, DIST-1a/b)
- GitHub silently drops a job output whose value contains any secret of
  that job as a substring ("Skip output ... may contain secret", only a
  warning annotation); the consumer reads "". A job holding Environment
  secrets outputs only literals and hex digests; consumers derive names
  from a secret-free job (build-version) and format-check every output
  (rc18 publish failure, CI-FIX-DIST, re-cut as v0.1.0-rc18.1).
- The CI filesystem reuses inode numbers after an unlink. A test that
  replaces a file and expects "different inode" must keep the old file
  alive (rename it aside) until the check is done, or it passes locally and
  fails in CI (GRAPH-LIVE replacement test).
- To diagnose a CI-only pytest failure without log access: read the
  check-run annotations; if they are empty, push a temp branch whose job
  echoes the pytest FAILED lines as `::error::` annotations and delete the
  branch afterwards.
- Screenshot goldens are refreshed by the bot only on `wp/*-goldens`
  branches, and bot commits do not trigger CI: push the branch, wait for the
  bot commit, then push an empty commit to get a CI run on the new goldens.

## 2026-10-17 — rc20 rework (UI-KIT-1, WEB-GOLDENS-1, TOKENS-SYNC-1, CLIENTS-4, ADD-DEVICE-2, DOWNLOADS-2, SETTINGS-2, LIBRARY-2)
- **Unauthenticated GitHub API polling is 60 requests per hour per IP,
  shared by everything on ZEUS.** Polling CI every 30 s for several
  branches burned it in minutes. Poll every 2-3 minutes and only one run
  at a time.

## 2026-10-31 — rc21 (GRAPH-RECENT-1, LOADING-NOW-1)
- **Rerunning a flaky branch CI without `gh` or a token:** push the same
  commit to a second `wp/` branch name (`git push origin <sha>:refs/heads/wp/<name>-ci2`);
  GitHub starts a fresh run on the same sha. No force push needed.
- **Golden refresh = push the head to `wp/<name>-goldens`**; the bot
  commits there, then fast-forward the normal branch to the bot commit and
  push it for the verifying run (the bot commit itself starts no run).
- **A goldens-branch bot run records every frontend's goldens**: an
  Android-only package fast-forwards only to the bot commit for its own
  platform, never to unrelated web golden commits.

## 2026-11-01 — rc22 (CI-SIGN-PIN-1)
- **A public value that gates a release lives in ONE committed file, not
  in a secret or a repo variable** (`app/signing-cert.sha256`): a change
  is then a reviewed commit, tests can read it, and nothing about it gets
  masked or dropped like a secret-like job output. Before pinning, check
  the value is still current: the token-free releases API showed the rc10
  certificate SHA-256 in every release body up to rc21.1.
- **A new gate on release-only runs: fail where it matters, warn where it
  cannot be satisfied yet.** The pin check takes its strictness from the
  same expression as the job's `environment:`
  (`startsWith(github.ref, 'refs/tags/v')`), so an unset pin fails a tag
  run but only warns on a rehearsal; a mismatch or malformed pin fails
  everywhere.

### 2026-11-01 RELEASE-SPLIT-1a

- A job-level `if:` cannot read `matrix`: per-leg gating in a matrix job
  needs an early decision step (`id: mode`) with every build step gated on
  its output; a reuse step writes the same digest output so later steps
  read `steps.build.outputs.digest || steps.reuse.outputs.digest`.
- Editing publish.yml by string replacement on a repeated job header
  (`needs:` + `if:` + `permissions:`) silently hit the wrong job; anchor on
  the `  <job>:` line, slice to the next key and assert the match count.
- A release-time gate that cannot be undone (tags are never deleted) warns
  in dry run and fails only once enforced; and a gate must fail closed on
  empty outputs, or a skipped builder skips `publish` and the tag run ends
  green with nothing published.

### 2026-11-01 RELEASE-SPLIT-1b

- A rehearsal only proves the path the plan takes: from a HEAD where every
  component changed, `workflow_dispatch` never runs the reuse jobs. To
  rehearse reuse, restore the component paths to the previous tag on a
  throwaway branch so the plan says `*_build=false`.
- Before calling a component "unchanged except tests", diff its manifest
  paths, not the directory: SAMPLE-IP-1 also changed two compiled strings
  in the agent, so rc22 really is a new agent build.
- Gates that accept a skipped builder must tie that acceptance to the
  plan's `*_build == 'false'` AND the reuse job's success, or a skipped
  builder ends the tag run green with missing assets.
