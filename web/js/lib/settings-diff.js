/**
 * `PATCH /v1/settings` body builder (WP 4a.6; ADR-0009).
 *
 * The settings view keeps a `drafts` map of ONLY the keys the user has
 * actually touched this session — it must never be pre-seeded with every
 * key's current value on load, or this module's "only changed keys" job
 * would be undermined before it even runs (the view is responsible for that
 * half; this module is responsible for the other half: a touched key whose
 * draft turns out to equal the server's current value must still be
 * dropped, e.g. the user typed something then typed it back).
 *
 * `entries`: the `settings` array from the last `GET /v1/settings` response
 * — `[{key, effective, source, fallback, applies, env_only}, ...]`.
 *
 * `drafts`: `{[key]: {reset: true} | {value: string | string[]}}`.
 *   - `{reset: true}`: the user asked to clear an explicit override (the
 *     mockup's "revert to default/env" action). Only meaningful, and only
 *     ever sent, when the key currently has a `db` override — clearing a
 *     key that already reads from env/default has nothing to clear, so it
 *     is silently dropped rather than sent as a no-op `null`.
 *   - `{value}`: the raw value currently in the field. `webhook_events`
 *     accepts either a comma string or an array (mirrors
 *     `vault_api/routers/settings.py::_coerce_patch_value`, which accepts
 *     both shapes for that one key); every other key is a plain string
 *     (ADR-0009: "blank is a valid override value" for `schedule_window`
 *     and `webhook_url`, so an empty string here is a REAL, intentional
 *     disable — never coerced to a `reset`).
 *
 * Returns a plain object with `null` for a clear and the raw value for a
 * set — exactly the shape `PATCH /v1/settings` expects — containing ONLY
 * the keys whose resolved action actually changes something server-side.
 * **This is the mutation-worthy pin (docs/LEARNINGS.md "Testing
 * discipline"): removing the `valueChanged`/`source === "db"` guards below
 * makes every touched key appear in the body regardless of whether it
 * differs from the server's current value, which must kill
 * `web/tests/settings-diff.test.js`'s "no-op edit is dropped" case.**
 */

function normalizeEventsList(value) {
  if (Array.isArray(value)) return value.map(String).map((s) => s.trim()).filter(Boolean).sort();
  return String(value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .sort();
}

function valueChanged(key, draftValue, effective) {
  if (key === "webhook_events") {
    return JSON.stringify(normalizeEventsList(draftValue)) !== JSON.stringify(normalizeEventsList(effective));
  }
  const draftText = typeof draftValue === "string" ? draftValue : String(draftValue ?? "");
  const effectiveText = effective === null || effective === undefined ? "" : String(effective);
  return draftText.trim() !== effectiveText.trim();
}

/**
 * @param {Array<{key: string, effective: unknown, source: string, env_only: boolean}>} entries
 * @param {Record<string, {reset?: boolean, value?: string | string[]}>} drafts
 * @returns {Record<string, unknown>} PATCH body (may be `{}`)
 */
export function buildSettingsPatch(entries, drafts) {
  const byKey = new Map((entries || []).map((e) => [e.key, e]));
  const body = {};

  for (const [key, draft] of Object.entries(drafts || {})) {
    const entry = byKey.get(key);
    // Defensive: an env-only or unrecognised key should never reach this
    // module (the view must not offer an editable control for one), but a
    // stray entry here must never crash a PATCH — just drop it.
    if (!entry || entry.env_only) continue;

    if (draft && draft.reset) {
      if (entry.source === "db") body[key] = null;
      continue; // nothing to clear when there is no override active
    }
    if (!draft || !("value" in draft)) continue;
    if (!valueChanged(key, draft.value, entry.effective)) continue;
    // WP WEB-FIX-1 (S3): `valueChanged` compares TRIMMED text, but the body
    // used to carry the raw field value — so `"90 "` was correctly judged a
    // change from `180` and then sent with its trailing space, which
    // `PATCH /v1/settings` rejects with a 422 the user cannot see the cause
    // of. Send what was compared. Arrays (`webhook_events` as a list) are
    // passed through verbatim; a string `webhook_events` draft is trimmed
    // like any other string, which the server's comma-split tolerates.
    body[key] = typeof draft.value === "string" ? draft.value.trim() : draft.value;
  }

  return body;
}
