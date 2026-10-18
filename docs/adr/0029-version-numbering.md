# ADR-0029: Version numbering and release candidates

Status: Accepted (sections 1-4); section 5 proposed, open decision VERSION-1.0

Date: 2026-10-18

Related: `docs/PROJECT_PLAN.md` §11 item 13 (version blocks),
`docs/handover/post-0.1.0-roadmap.md` "Release plan", ADR-0016 (freeze),
WP VER-1, RELEASE-SPLIT-1 (rc22).

## Context

The original `v0.1.0` was Phases 0-5 (requirements A1-A14 without the
embedded tsnet profile), one tag and the announcement. That scope was
essentially reached with `v0.1.0-rc1` to `rc12` (2026-10-01 to
2026-10-04). From `rc13` on, almost every candidate carried new features
(update check, content packages, live graph, depot preview, the rc20 web
redesign) and the database schema grew from about v17 to v33, all under
the `0.1.0-rc` label. A release candidate stopped meaning "the thing we
intend to ship, under test" and became "the next feature build". Nothing
in the working agreement said what an rc may contain, so every new idea
went into the next rc instead of the next version.

## Decision (user, 2026-10-18)

1. **Path to `v0.1.0` final is unchanged** (Weg C): rc21 keeps its
   feature scope (Android parity and cache health), rc22 is release
   hardening including RELEASE-SPLIT-1, then `v0.1.0` final. No scope
   freeze is imposed on rc21/rc22; the rules below apply from 0.2.0 on.

2. **Minor (`0.X.0`) = one themed feature set.** Each minor has a goal,
   an entry and exit criterion and a package list (typically 5-10
   packages) written into the roadmap before its first package starts.
   An idea that comes up while a minor is in progress goes into a later
   minor unless the user explicitly pulls it in. Schema bumps and
   breaking changes (config, API, volume layout) only happen in a minor,
   and each one names its migration in the minor's exit criterion.

3. **Release candidate (`0.X.0-rcN`) = feature freeze.** From `rc1` of a
   minor on, only fixes for that minor's scope land; no new packages.
   If a minor reaches `rc4` without being ready, the orchestrator stops
   and puts the scope back to the user (cut, move or continue) before an
   `rc5`.

4. **Patch (`0.X.Y`) = fixes only** on a released minor: no features, no
   schema bump unless the fix itself needs one.

5. **Major (`1.0.0`), proposed, not decided:** `1.0.0` is tagged when
   (a) the product is publicly announced under its final name, (b) the
   operator-facing contract is declared stable and documented (`/v1` API,
   compose file and env keys, volume layout, agent and app pairing),
   (c) every upgrade from the previous minor has a tested migration, and
   (d) at least one minor ran in production without a breaking change.
   After `1.0.0` a breaking change of that contract raises the major
   (`2.0.0`), features raise the minor, fixes the patch (SemVer). Until
   then, in `0.x`, a minor may break the contract as long as it ships a
   migration.

## Versions after 0.1.0 (user, 2026-10-18)

The former v0.2.0 block (GameHangar, privacy, schedules, agents) and
the later blocks are re-cut into small minors:

- 0.2.0 rename to GameHangar (D18 incl. logo)
- 0.3.0 privacy mode (D22) and the store night fill (LIBRARY-META-1d)
- 0.4.0 agents and devices (Weg B for agents with the installer, D28,
  D29, D5)
- 0.5.0 Steam accounts (D2, D3), then the public announcement (WP 5.6)
- 0.6.0 schedules (D8)
- 0.7.0 and later: beyond Steam and integrations

The announcement comes with the second-to-last themed minor (0.5.0),
so every public user starts on the final name with privacy mode, the
real agent install and Steam accounts in place.

## Consequences

- The `0.1.0-rc` series ends at rc22 (plus rc22.x fixes); its tag
  history stays as it is.
- Every minor's planning session writes its package list into both
  roadmap places before delegating; additions during the minor need the
  user's yes and are recorded there.
- Status reports name the minor and, during an rc, state that only
  fixes landed.
