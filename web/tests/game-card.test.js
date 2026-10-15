/**
 * Headless tests for web/js/components/game-card.js's `displayName` (WP
 * 4a.8 review fix, cycle 2).
 *
 * `displayName` is the one export from this DOM-building component that is
 * pure (no DOM touched — it only reads `game.name`/`game.appid` and returns
 * a string), so unlike the rest of this file's exports it needs no fake
 * document at all: `game-card.js` and everything it imports
 * (`components/status-icon.js`, `lib/game-status.js`, `lib/format.js`,
 * `lib/cover-art.js`) touch `document`/`window` only LAZILY inside
 * functions, never at module load, so importing the module in bare Node
 * (confirmed: `node --input-type=module -e "import(...)"` with no globals
 * set at all) is safe — this is the same "the module only needs a fake
 * environment if it touches one at load time" reasoning
 * `store-poll-loop.test.js`'s header documents for `store.js`.
 *
 * Regression target: the live e2e bug this WP's coder report described but
 * did not pin — a real, un-Steam-linked vault-api returns
 * `GameSummary.name: null` (demo-mode fixtures always seed a name, so this
 * was never exercised until testing against a real server), and before the
 * fix a card for such a game rendered a completely blank title and an
 * aria-label reading a bare `" — Failed"`.
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { displayName } from "../js/components/game-card.js";

test("displayName: null name falls back to 'Unknown app'", () => {
  assert.equal(displayName({ name: null, appid: 440 }), "Unknown app");
});

test("displayName: whitespace-only name falls back to 'Unknown app' (not the blank string)", () => {
  assert.equal(displayName({ name: "   ", appid: 440 }), "Unknown app");
});

test("displayName: a real name is used verbatim, trimmed", () => {
  assert.equal(displayName({ name: "Aurora Cascade", appid: 2010010 }), "Aurora Cascade");
  assert.equal(displayName({ name: "  Aurora Cascade  ", appid: 2010010 }), "Aurora Cascade");
});

test("displayName: undefined name (field absent entirely) also falls back", () => {
  assert.equal(displayName({ appid: 7 }), "Unknown app");
});
