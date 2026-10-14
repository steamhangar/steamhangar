/**
 * WP D15-NAMES, pure layer: `lib/client-names.js` (validation mirroring
 * api/vault_api/client_names.py, wording), `lib/history-delete.js`, and the
 * Clients page title/kind line reading the server's name fields
 * (`lib/clients-page.js`). Literal expectations, never derived from the
 * module under test.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, ERROR_KINDS } from "../js/errors.js";
import {
  MAX_CLIENT_NAME_LENGTH,
  isolate,
  nameRemovedToastText,
  renameTitle,
  renamedToastText,
  canRename,
  hasCustomName,
  isNameAlreadyGone,
  normalizeClientName,
  renameErrorText,
  renameHint,
} from "../js/lib/client-names.js";
import {
  HISTORY_DELETE_OPTIONS,
  historyDeleteButtonText,
  historyDeleteErrorText,
  historyDeletedText,
  isHistoryScope,
} from "../js/lib/history-delete.js";
import { clientKindText, clientName } from "../js/lib/clients-page.js";

const notFound = (detail) => new ApiError(ERROR_KINDS.NOT_FOUND, detail, { status: 404, detail });

test("names: trimmed, 1..40 code points, the server's limit", () => {
  assert.equal(MAX_CLIENT_NAME_LENGTH, 40);
  assert.deepEqual(normalizeClientName("  Living room PC \t"), { ok: true, value: "Living room PC" });
  assert.deepEqual(normalizeClientName("x".repeat(40)), { ok: true, value: "x".repeat(40) });
  // 40 emoji are 80 UTF-16 units but 40 characters, as on the server.
  assert.equal(normalizeClientName("\u{1F3AE}".repeat(40)).ok, true);
  assert.deepEqual(normalizeClientName("x".repeat(41)), { ok: false, error: "Use at most 40 characters." });
});

test("MUTATION TARGET: names: empty, non-strings and control or invisible characters are refused", () => {
  for (const raw of ["", "   ", null, undefined, 7]) {
    assert.deepEqual(normalizeClientName(raw), { ok: false, error: "Enter a name." }, String(raw));
  }
  for (const raw of ["a\nb", "tab\tin", "nul\u0000", "zero\u200bwidth", "bidi\u202eflip", "line\u2028sep", "pua\ue000"]) {
    assert.deepEqual(
      normalizeClientName(raw),
      { ok: false, error: "Remove line breaks, tabs and invisible characters." },
      JSON.stringify(raw),
    );
  }
});

test("MUTATION TARGET: names need one letter, digit, symbol or punctuation mark (the server's rule)", () => {
  assert.deepEqual(normalizeClientName("-"), { ok: true, value: "-" });
  assert.deepEqual(normalizeClientName("e\u0301"), { ok: true, value: "e\u0301" });
  for (const raw of ["\u0301\u0301", "\u0301"]) {
    assert.deepEqual(
      normalizeClientName(raw),
      { ok: false, error: "Use at least one letter, digit, symbol or punctuation mark." },
      JSON.stringify(raw),
    );
  }
});

test("MUTATION TARGET: operator text is wrapped in first-strong isolates in titles and toasts", () => {
  assert.equal(isolate("x"), "\u2068x\u2069");
  assert.equal(renameTitle("\u05e9\u05dc\u05d5\u05dd PC"), "Rename \u2068\u05e9\u05dc\u05d5\u05dd PC\u2069");
  assert.equal(renamedToastText("Desk"), "Renamed to \u2068Desk\u2069.");
  assert.equal(nameRemovedToastText("DEMON"), "Name removed. Now shown as \u2068DEMON\u2069.");
  assert.equal(nameRemovedToastText(null), "Name removed.");
});

test("canRename / hasCustomName read the server's fields only", () => {
  assert.equal(canRename({ name_addr: "10.0.0.5" }), true);
  assert.equal(canRename({ name_addr: null }), false);
  assert.equal(canRename({}), false, "a server older than D15-NAMES: no Rename");
  assert.equal(hasCustomName({ name: "Desk" }), true);
  assert.equal(hasCustomName({ name: null }), false);
});

test("the hint names the address and, for an agent, the PC name it keeps reporting", () => {
  assert.equal(
    renameHint({ kind: "address", name_addr: "10.0.0.9" }),
    "Stored for the address 10.0.0.9: a machine that gets this address later shows this name too.",
  );
  assert.equal(
    renameHint({ kind: "agent", client_id: "DEMON", name_addr: "10.0.0.5" }),
    "Stored for the address 10.0.0.5: a machine that gets this address later shows this name too. vault-agent keeps reporting this PC as \u2068DEMON\u2069; the name you set is shown instead.",
  );
});

test("MUTATION TARGET: only the handler's own 404 means 'already removed'; another 404 is an old server", () => {
  assert.equal(isNameAlreadyGone(notFound("No name stored for '10.0.0.5'")), true);
  assert.equal(isNameAlreadyGone(notFound("Not Found")), false);
  assert.equal(
    renameErrorText(notFound("Not Found")),
    "This server cannot store client names yet. Update vault-api to rename clients.",
  );
  const bad = new ApiError(ERROR_KINDS.VALIDATION, "x", { status: 422, detail: "name must not be empty" });
  assert.equal(renameErrorText(bad), "Could not save the name: name must not be empty");
});

test("MUTATION TARGET: the card title is the server's display_name; the kind line keeps what it replaced", () => {
  const agent = { kind: "agent", client_id: "DEMON", addresses: ["10.0.0.5"], name: "Living room PC", display_name: "Living room PC" };
  assert.equal(clientName(agent), "Living room PC");
  assert.equal(clientKindText(agent), "vault-agent on \u2068DEMON\u2069 · 10.0.0.5");
  const plain = { ...agent, name: null, display_name: "DEMON" };
  assert.equal(clientName(plain), "DEMON");
  assert.equal(clientKindText(plain), "vault-agent · 10.0.0.5");
  const addr = { kind: "address", client_id: null, addresses: ["10.0.0.9"], name: "frame", display_name: "frame" };
  assert.equal(clientName(addr), "frame");
  assert.equal(clientKindText(addr), "No agent · 10.0.0.9 · seen in the cache log");
  assert.equal(clientKindText({ ...addr, name: null, display_name: "10.0.0.9" }), "No agent · seen in the cache log");
  // A server older than D15-NAMES: no display_name, the old rule.
  assert.equal(clientName({ kind: "agent", client_id: "DEMON", addresses: [] }), "DEMON");
  assert.equal(clientName({ kind: "address", addresses: ["10.0.0.9"] }), "10.0.0.9");
});

test("history delete: two variants, the safe one first; wording per variant", () => {
  assert.deepEqual(
    HISTORY_DELETE_OPTIONS.map((o) => `${o.value}=${o.label}`),
    ["older-than-30d=Older than 30 days", "all=Everything"],
  );
  assert.equal(isHistoryScope("all"), true);
  assert.equal(isHistoryScope("older-than-30d"), true);
  assert.equal(isHistoryScope("30d"), false);
  assert.equal(historyDeleteButtonText("all"), "Delete everything");
  assert.equal(historyDeleteButtonText("older-than-30d"), "Delete older than 30 days");
  assert.equal(
    historyDeletedText({ scope: "all", jobs_deleted: 1 }),
    "History deleted: 1 finished download and all traffic statistics.",
  );
  assert.equal(
    historyDeletedText({ scope: "older-than-30d", jobs_deleted: 12 }),
    "History older than 30 days deleted: 12 finished downloads and the traffic statistics from that time.",
  );
  assert.equal(historyDeleteErrorText(notFound("Not Found")), "This server cannot delete history yet. Update vault-api to use this.");
});
