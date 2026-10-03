/**
 * Headless tests for web/js/lib/rail-content.js (WP 4e.6).
 *
 * The whole point of this module is the "absence renders as unknown, never
 * as zero" rule (the brief's own wording) applied to two new pieces of
 * data — pinned here as PURE functions, no DOM, so the guarantee is
 * testable without a browser and without the store/component wiring layer
 * around it. Every test below states which of the three degrade cases it
 * targets (no data yet / malformed data / a genuine zero) so a future
 * reader can tell "this is the unknown case" from "this is the zero case"
 * without re-deriving it from the assertion alone.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { vaultNameFromSettings, cacheFootFromSummary, versionFromSettings } from "../js/lib/rail-content.js";

// ---------------------------------------------------------------------
// vaultNameFromSettings
// ---------------------------------------------------------------------

test("vaultNameFromSettings: a real, non-empty effective value is returned trimmed", () => {
  assert.equal(
    vaultNameFromSettings({ readonly: false, settings: [{ key: "vault_name", effective: "  homelab  " }] }),
    "homelab",
  );
});
test("vaultNameFromSettings: null/undefined settings response is unknown, not a fabricated name", () => {
  assert.equal(vaultNameFromSettings(null), null);
  assert.equal(vaultNameFromSettings(undefined), null);
});
test("vaultNameFromSettings: a response with no settings array (malformed) is unknown", () => {
  assert.equal(vaultNameFromSettings({ readonly: false }), null);
  assert.equal(vaultNameFromSettings({ settings: "not an array" }), null);
});
test("vaultNameFromSettings: no vault_name entry in the settings list is unknown", () => {
  assert.equal(vaultNameFromSettings({ settings: [{ key: "webhook_url", effective: "" }] }), null);
});
test("vaultNameFromSettings: an empty/whitespace-only effective value is unknown, not a printed blank", () => {
  assert.equal(vaultNameFromSettings({ settings: [{ key: "vault_name", effective: "" }] }), null);
  assert.equal(vaultNameFromSettings({ settings: [{ key: "vault_name", effective: "   " }] }), null);
});
test("vaultNameFromSettings: a non-string effective value is unknown", () => {
  assert.equal(vaultNameFromSettings({ settings: [{ key: "vault_name", effective: null }] }), null);
});

// ---------------------------------------------------------------------
// cacheFootFromSummary — the headline unknown-vs-zero guarantee.
// ---------------------------------------------------------------------

test("cacheFootFromSummary: no summary yet (before the first poll, or after a failed one) is unknown", () => {
  assert.equal(cacheFootFromSummary(null), null);
  assert.equal(cacheFootFromSummary(undefined), null);
});
test("cacheFootFromSummary: a non-object summary is unknown", () => {
  assert.equal(cacheFootFromSummary("not an object"), null);
  assert.equal(cacheFootFromSummary(42), null);
});
test("cacheFootFromSummary: total_bytes missing/non-numeric means 'not a real summary', unknown — not zero", () => {
  assert.equal(cacheFootFromSummary({ free_disk_bytes: 10 }), null);
  assert.equal(cacheFootFromSummary({ total_bytes: "10", free_disk_bytes: 10 }), null);
});
// The mutation this test is aimed at: swapping cacheFootFromSummary's
// formatBytesGBOrZero import back to formatBytesGB (the tile-badge helper
// that collapses 0 into null) makes total_bytes:0 produce `null` here
// instead of a real "0.0 GB" — silently re-introducing the exact ambiguity
// (a real empty cache vs. "we don't know yet") this module exists to kill.
test("cacheFootFromSummary: a GENUINE zero total_bytes (empty cache) renders as a real 0.0 GB, not unknown", () => {
  const result = cacheFootFromSummary({ total_bytes: 0, free_disk_bytes: 500 });
  assert.notEqual(result, null, "an empty cache is a real, known state — must not collapse into 'unknown'");
  assert.equal(result.usedText, "0.0 GB");
});
// Same mutation target, opposite field: free_disk_bytes:0 is "the disk is
// full" — an alarming, real fact — and must not become "0 B" hidden as
// "unknown" either.
test("cacheFootFromSummary: a GENUINE zero free_disk_bytes (disk full) renders, not unknown", () => {
  const result = cacheFootFromSummary({ total_bytes: 100, free_disk_bytes: 0 });
  assert.notEqual(result.freeText, null, "0 bytes free is a real, alarming fact — must render, not hide as unknown");
  assert.equal(result.freeText, "0.0 GB");
});
test("cacheFootFromSummary: free_disk_bytes:null (vault-api's own 'undeterminable' contract) stays unknown", () => {
  const result = cacheFootFromSummary({ total_bytes: 100, free_disk_bytes: null });
  assert.notEqual(result, null, "total_bytes is still real — usedText must still render");
  assert.equal(result.freeText, null, "free_disk_bytes:null must stay unknown, never become a fabricated 0 B");
});
test("cacheFootFromSummary: free_disk_bytes absent entirely is treated the same as null (unknown)", () => {
  const result = cacheFootFromSummary({ total_bytes: 100 });
  assert.equal(result.freeText, null);
});
test("cacheFootFromSummary: a real, non-zero pair renders both fields", () => {
  const result = cacheFootFromSummary({ total_bytes: 5_000_000_000, free_disk_bytes: 150_000_000_000 });
  assert.deepEqual(result, { usedText: "4.7 GB", freeText: "140 GB" });
});

// ---------------------------------------------------------------------
// versionFromSettings (coordinator addition, WP 4e.7 lands the server
// field this reads — see this module's own header for the CONFIRMED shape
// (WP 4e.7 report): a top-level `server_version` string, sibling of
// `readonly`, on the GET /v1/settings response).
// ---------------------------------------------------------------------

test("versionFromSettings: the field absent entirely renders nothing", () => {
  assert.equal(versionFromSettings({ readonly: false, settings: [] }), null);
  assert.equal(versionFromSettings({}), null);
});
test("versionFromSettings: null/undefined settings response renders nothing", () => {
  assert.equal(versionFromSettings(null), null);
  assert.equal(versionFromSettings(undefined), null);
});
test("versionFromSettings: a non-string server_version value renders nothing (never coerced)", () => {
  assert.equal(versionFromSettings({ server_version: 140 }), null);
  assert.equal(versionFromSettings({ server_version: null }), null);
});
test("versionFromSettings: an empty/whitespace-only server_version renders nothing", () => {
  assert.equal(versionFromSettings({ server_version: "" }), null);
  assert.equal(versionFromSettings({ server_version: "   " }), null);
});
test("versionFromSettings: a real value is trimmed and prefixed with 'v' for display", () => {
  assert.equal(versionFromSettings({ server_version: "  0.1.0  " }), "v0.1.0");
});
// WP VER-2: a locally built image reports server_version "dev" (WP VER-1's
// Dockerfile default); the old "always prefix v" rule rendered "vdev".
test("versionFromSettings: the local-build value 'dev' reads 'dev build', never 'vdev'", () => {
  assert.equal(versionFromSettings({ server_version: "dev" }), "dev build");
  assert.equal(versionFromSettings({ server_version: "  DEV  " }), "dev build");
});
test("versionFromSettings: only a digit-leading value gets the 'v' prefix (dev-/ci- builds pass through)", () => {
  assert.equal(versionFromSettings({ server_version: "dev-1a2b3c4" }), "dev-1a2b3c4");
  assert.equal(versionFromSettings({ server_version: "ci-1a2b3c4" }), "ci-1a2b3c4");
  assert.equal(versionFromSettings({ server_version: "0.1.0-rc9" }), "v0.1.0-rc9");
  assert.equal(versionFromSettings({ server_version: "devel" }), "devel", "only the exact word dev is the local-build default");
});
test("versionFromSettings: a value that already starts with 'v'/'V' is not double-prefixed", () => {
  assert.equal(versionFromSettings({ server_version: "v0.1.0" }), "v0.1.0");
  assert.equal(versionFromSettings({ server_version: "V2" }), "V2");
});
// A settings row with an unrecognised key is exactly what api/README.md
// documents PATCH rejecting for this field — GET still round-trips whatever
// is actually stored server-side, which this test treats as opaque (this
// module never validates FORMAT, only "is it a non-empty string").
test("versionFromSettings: an unusual but real string value passes through unmodified (only length is clamped, never format)", () => {
  assert.equal(versionFromSettings({ server_version: "2026.08.18-nightly" }), "v2026.08.18-nightly");
});
// The mutation this test is aimed at: an absent field must be
// STRUCTURALLY unable to reach the render path — a bug that instead
// defaulted to e.g. "" or "unknown" and then rendered THAT string would
// pass a looser "returns falsy" check but fail this exact-null pin.
test("versionFromSettings: an absent field returns exactly null, never a placeholder string", () => {
  assert.equal(versionFromSettings({ readonly: true, settings: [] }), null, "must be null, not \"\", \"unknown\", or any other placeholder");
});
test("versionFromSettings: a pathologically long value is clamped, never rendered in full", () => {
  const long = "1.4.0-beta.12+build." + "9".repeat(200);
  const result = versionFromSettings({ server_version: long });
  assert.ok(result.length < long.length, "a long value must be clamped shorter, not passed through verbatim");
  assert.ok(result.endsWith("…"), "a clamped value must end with an ellipsis so truncation is visible, not silent");
});
