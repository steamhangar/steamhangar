# SteamHangar — Working Agreement

Canonical source for scope, architecture, phases, and definition of done:
`docs/PROJECT_PLAN.md`. Read it before doing anything else.

## Roles

The main session (orchestrator) plans, decomposes, and delegates. It writes
no production code and performs no reviews itself. Two subagents exist in
`.claude/agents/` and must be used consistently:

- **coder**: implements one clearly scoped work package at a time
- **reviewer**: reviews every completed work package before it counts
  as done; read-only

Agent definitions use `model: inherit` so delegation always rides the
session's current model instead of one pinned by name; where the working
agreement asks for a stronger second pass (the `[Fable]` marker in
`docs/WORKPACKAGES.md`), the orchestrator picks the strongest available
model at call time — never by name in a file or brief.

## Shared learnings (mandatory)

`docs/LEARNINGS.md` is the living list of project-proven findings. Every
coder and reviewer reads it before their first task of a session; work
package briefs reference it instead of repeating its content. The
orchestrator appends newly distilled findings after each review cycle.

## Roadmap rule (mandatory)

Every feature or package planned for a future version or rc is written to
the repo the moment it is agreed, never only to chat or session memory:
a checkbox in the matching version block of `docs/PROJECT_PLAN.md` and, for
larger themes, an entry in `docs/handover/post-0.1.0-roadmap.md`. Both go
to `main` with the next release-chain push (docs-only changes may ride on
the current integration branch), so every other session finds the same
plan. When scope moves between versions, update both places in the same
commit.

The same applies to everything that is still open, not only to agreed
features (user decision 2026-10-16, after an audit found items that lived
only in old sessions):
- **Open decisions:** every Weg A / Weg B question put to the user that is
  not answered in the same session is recorded as an "open decision" in
  the roadmap (ID, options, recommendation) until the user decides.
- **Findings:** a measurement, Antrag result or inbox note that implies a
  repo task gets an ID in the roadmap (or a LEARNINGS entry if it is
  knowledge only), never just a chat message or an Antrag line.
- **Deferred review items:** should-fix and nit findings that are not fixed
  in the package go to the nits list of the matching version block.
- **Handover check:** before every handover ("Übergabe") or session end,
  the orchestrator checks that all open items of the session are in the
  repo as above and pushed; the status report says so explicitly.

## Work mode (applies to the whole project)

- Work strictly phase by phase per `docs/PROJECT_PLAN.md`. Phase 0
  (feasibility PoC) comes first — NO production code before Phase 0 has
  answered the proxy_store question (Plan B viable vs. fallback Plan A).
- Split each phase into work packages of at most ~1-2 hours of effort.
  Show the package list of a phase to the user BEFORE delegating.
- Every package goes through: coder implements → reviewer reviews →
  on FAIL back to coder with the findings → only on PASS does it count
  as done and gets committed.
- One commit per passed work package, Conventional Commits format.
- Gate principle: after each completed package, a short status report to
  the user (done / open / blockers). For architecture decisions that would
  deviate from the plan: stop and ask the user, with clearly named options
  (Plan A / Plan B) — never decide alone.
- Keep `docs/PROJECT_PLAN.md` up to date: tick completed checkboxes,
  record decisions as short ADR notes in `docs/adr/`.
- Language: communication with the user in German; code, comments, commits,
  and everything in the repo in English.
