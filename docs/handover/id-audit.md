# ID-AUDIT findings (rc19, 2026-10-15)

Question: why does "App <id>" still show where a game name belongs (Clients
"Loaded recently: App 1465470 / App 3419430", Library cards "APP 1716750",
Downloads job card)?

## Where names come from

1. `apps.name` -- written by depot mappings (`mapping.upsert_mapping`) and
   nothing else. `POST /v1/prefill` and every traffic-seen app that is only
   mapped by depot get an `apps` row with `name = NULL`.
2. `app_classification.name` -- Steam's store answer (cover refresher,
   `cover_art.py`, GetItems) and, while the oracle is on, the oracle's
   `common.name`. Display fallback, never written into `apps`.
3. `tool_apps.TOOL_APPS` -- fixed names for 228980 and 250820.
4. The owned-games list (relay key) -- web fills it in client side
   (`fillMissingNames`), Library and Downloads only.

## Findings

- **F1 (cause of the Clients page ids): read-path gap.** `GET /v1/games`
  names an app by 1 -> 2 -> 3 (`display_name` plus `tool_app_name`), but
  `traffic.app_names` (used by `/v1/traffic/clients` `recent_apps` /
  `loading_now` and `/v1/traffic/throughput` job rows) read `apps.name`
  only. An app that Steam had already named in `app_classification`, or a
  tool app, came back `name: null` and the UI printed `App <id>`. Every
  traffic-seen app has an `apps` row (traffic attribution goes through
  `depot_app_map`, which `upsert_mapping` fills together with `apps`), so the
  store lookup already covers them; there is no "never looked up" class.
- **F2: apps nobody names.** Tool/runtime packages such as 3419430 and apps
  Steam's store hides (`success != 1`, region lock) have no row in 1 or 2 and
  are not on the tool list. Only the owned list can name them, and only when
  they are owned by the configured account. For these a name does not exist;
  the correct output is a neutral fallback.
- **F3: UI fallback was a bare id.** Eight web call sites (and their Android
  twins) fell back to `App <id>` for the title.

## Fix (this WP)

- API: `traffic.app_names` now resolves `apps.name` -> stored classification
  name (`app_classification.fallback_names`, store first, oracle only while
  the oracle is on) -> tool-app name. Read-only; no new request, no new
  egress, nothing new on the request path.
- Web and Android: one shared fallback, `UNKNOWN_APP_NAME = "Unknown app"`
  (`web/js/lib/app-names.js`, `AppNames.kt`), used by every title helper.
  Deliberate technical id lines stay: job card `job #N · appid N`, the
  sheet's id line, package facts (`App 11 · DLC · ...`).

## Open point for Jan (not changed here)

The cover refresher's store lookup is **anonymous** (no relay key) and runs
for all tracked apps (ADR-0016 addendum, Jan's "Weg B" of 2026-10-10). That
is the source of most `app_classification.name` values. The ID-AUDIT rule
"names only with the relay key" would mean gating that thread (or only the
name it stores) on the key. Doing that changes covers and package grouping
too, so it is a separate decision: this WP adds no lookup and leaves the
refresher as it is.

Not done: listing which ids on prod lack a name (needs a read of the prod
database; a `dev.sh diag`-style query, not available to this WP).
