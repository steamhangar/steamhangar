# Engineering learnings (living document)

Standing, project-proven findings. These are not style preferences; each
entry cost a review round to learn. Since DOC-DIET-1 (rc22) this file is
only the index; the entries live in one file per area under
[`docs/learnings/`](learnings/), moved verbatim with their original section
headings (topic headings and dated wave headings).

## Mandatory read

- Every coder and reviewer reads this index plus the area file(s) of the
  package before their first task of a session. Pick the areas from the
  table below by the paths the package touches; `process.md` and
  `testing.md` apply to every package.
- Do not read all area files whole for a single package; grep the others
  (`grep -rn '<term>' docs/learnings/`) when a topic crosses areas.
- The entries are binding (see `CONTRIBUTING.md`). Older references such as
  "LEARNINGS "Parsers" section" or "docs/LEARNINGS.md, systemd / packaging"
  resolve through the "Original topic headings" column.

## Appending new findings

The orchestrator appends distilled findings after each review cycle: one
entry each, with the WP that proved it, at the end of the matching area
file under a dated heading (`## YYYY-MM-DD — <rc or wave>`). An entry that
spans areas goes to its main area, with a one-line "See also" entry in the
other area file. Keep each area file under ~25 KB; split an area before it
grows past that and add the new file to the table.

## Areas

| Area file | Scope | Original topic headings |
| --- | --- | --- |
| [process.md](learnings/process.md) | orchestration, delegation, review loop, agent limits, branches and merges, Antraege, docs and community release | Process (2026-08-18 decision audit, Fable); Docs / community release |
| [testing.md](learnings/testing.md) | cross-cutting test technique: mutation checks, pins and their generations, flakes, fixtures, twin pins | Testing discipline |
| [api.md](learnings/api.md) | vault-api: FastAPI, SQLite and schema steps, parsers and input handling, subprocess output, scheduler, jobs, oracle, mapping | SQLite / FastAPI / vault-api; Parsers / input handling; Subprocess output handling |
| [web.md](learnings/web.md) | web UI: views, kit.css, fake-dom and node tests, demo data, web goldens, a11y | Web UI (Phase 4a) |
| [android.md](learnings/android.md) | Android app: Compose UI, Robolectric/Roborazzi screenshots, twin pins, lint, MockWebServer | Android (Phase 4b) |
| [agent.md](learnings/agent.md) | vault-agent (Go, CLI), Windows filesystem, PowerShell 5.1, Scheduled Task, systemd packaging | Windows filesystem semantics; PowerShell 5.1 (dev/test harnesses); systemd / packaging; Go / CLI |
| [core-nginx.md](learnings/core-nginx.md) | vault-core nginx config, proxy_store, upstream pool, rate caps, log formats; vault-dns (dnsmasq) | nginx / vault-core; dnsmasq / vault-dns |
| [steam.md](learnings/steam.md) | Steam CDN, depots and manifests, SteamPrefill behaviour | Steam ecosystem facts |
| [ci.md](learnings/ci.md) | GitHub Actions workflows, annotations without a token, goldens branches, runner differences (SQLite, inodes), API rate limits | CI / GitHub Actions |
| [deploy.md](learnings/deploy.md) | compose, images and entrypoints, verify-stack, production line (DS-Lite), ZFS, throughput and cold-game measurements | Production rollout (operator line, DS-Lite); Containers |

Dated wave sections (2026-08-22 to 2026-11-01) were split by entry: each
area file carries the wave heading followed by that wave's entries for the
area, in the original order.

## Typical reads per package type

- web package: `process.md`, `testing.md`, `web.md` (plus `android.md` when
  a web string or template that Android pins changes)
- api package: `process.md`, `testing.md`, `api.md` (plus `core-nginx.md` or
  `steam.md` when the package touches nginx or SteamPrefill)
- android package: `process.md`, `testing.md`, `android.md`, `web.md` twin
  entries via grep
- agent package: `process.md`, `testing.md`, `agent.md`
- core / deploy / ci package: `process.md`, `testing.md` and the matching
  `core-nginx.md`, `deploy.md` or `ci.md`
- docs/process package: `process.md`
