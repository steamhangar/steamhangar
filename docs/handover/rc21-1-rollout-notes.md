# rc21.1 rollout notes (for the rollout Antrag)

Collected 2026-11-01 from the MAP-ATTRIB-1 review (round 3, b97a62e;
simulation of the real cleanup on a full read-only prod export plus a live
`dev.sh diag db` re-check). Depot dirs measured on prod by the operator the
same day.

## MAP-ATTRIB-1 one-time step

- Runs once at the first start of rc21.1 (`maintenance_steps` key
  `map-attrib-1-foreign-mapping-cleanup`), one transaction, before the
  worker starts.
- Removes 5 `depot_app_map` rows:
  - Wanderburg (3624140): 436781 (owner 436780), 553851 and 553854 (owner
    553850 HELLDIVERS 2), 1716751 (owners 1716750 and 3751950).
  - Enshrouded (1203620): 1465471 (owner 1465470).
  - Each true owner stays mapped and has a recorded manifest equal to the
    oracle public gid. Also deletes the 4 `no_target` `cache_completeness`
    rows of 3624140, so Wanderburg should no longer read "Cached · not
    verified".
- Restores 3 rows: (292033, 292030) and (292036, 292030) The Witcher 3,
  (1203621, 1203620) Enshrouded. Only from the app's own record whose gid
  equals the oracle public gid and only if the depot dir exists (measured
  2026-11-01: 3.47 GiB, 3.77 GiB, 42.97 GiB, all present). These three
  depots enter GC planning for the first time: old builds there may be
  collected, the current public build is kept.
- Unchanged: HD2 packages 2506210-2506280 and 4199890, the shared 229005,
  645393, 1716750 -> 1716751, the 22 Witcher 3 package depots.
- Expected startup log: 5 WARNING "removed depot ... from app ...", 3
  WARNING "restored depot ...", one WARNING summary "Mapping cleanup:
  removed 5 ..., restored 3 ...". A "NOT restored" or "left for the
  operator" line is unexpected: report it, do not act on it.
- Before the rollout (read-only): the three restore candidates still have
  recorded gid == public (1203621 4708593645837114731, 292033
  5639169236463762487, 292036 1350518231851738693); the 15 multi-owner
  depots unchanged.
- After the rollout: `depot_app_map` for 3624140 = {3624141}, for 1203620 =
  {1203621}, 292030 additionally has 292033 and 292036; the
  `maintenance_steps` row exists; a dry-run GC for 3624140 plans only
  3624141; dry-run GCs for 292030 and 1203620 show "planned" with the
  recorded manifest kept, before any execute run.
- Way back: re-insert the 5 rows with `PUT /v1/mapping`, or `DELETE
  /v1/mapping/{depot}/{app}` for the 3 restored ones; deleting the
  `maintenance_steps` row makes the step run again.
