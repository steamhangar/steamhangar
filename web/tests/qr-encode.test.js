/**
 * web/js/lib/qr-encode.js (WP PAIR-1): the byte-mode QR encoder behind the
 * Android pairing code.
 *
 *  - module matrices equal an independent encoder's (segno 1.6.6,
 *    qr-reference-fixtures.js) for five inputs: versions 1, 5, 6 (exactly
 *    full, no terminator room), 8 (with version information), levels
 *    L/M/H, several masks;
 *  - format bits, version bits, alignment positions and byte capacities
 *    equal the ISO/IEC 18004 tables for spot values;
 *  - every symbol has the three finders, both timing lines and the dark
 *    module, and its format information in both copies;
 *  - automatic mask choice is deterministic and is one of the eight masks,
 *    producing the same matrix as forcing that mask;
 *  - too much data throws instead of truncating;
 *  - the SVG geometry draws exactly the dark modules, inside a 4-module
 *    quiet zone.
 *
 * Decoding is not run here (no decoder in the repo). During the WP every
 * version 1-40 at every level was cross-checked against segno (320 forced-
 * mask cases, all equal) and decoded with jsQR 1.4.0; see the README entry.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ECC_LEVELS,
  QrDataTooLongError,
  QUIET_ZONE,
  alignmentPatternPositions,
  byteCapacity,
  encodeQrBytes,
  encodeQrText,
  formatBits,
  qrSvgGeometry,
  versionBits,
} from "../js/lib/qr-encode.js";
import { QR_REFERENCE } from "./qr-reference-fixtures.js";

const rowsOf = (qr) => qr.modules.map((r) => r.map((b) => (b ? "1" : "0")).join(""));

for (const ref of QR_REFERENCE) {
  test(`MUTATION TARGET: matrix equals the reference encoder (v${ref.version}, ${ref.ecl}, mask ${ref.mask}, ${ref.text.length} bytes)`, () => {
    const qr = encodeQrText(ref.text, { ecl: ref.ecl, mask: ref.mask });
    assert.equal(qr.version, ref.version, "smallest version that fits");
    assert.equal(qr.size, ref.version * 4 + 17);
    const got = rowsOf(qr);
    const firstBad = got.findIndex((row, y) => row !== ref.rows[y]);
    assert.equal(firstBad, -1, `first differing row: ${firstBad}`);
  });
}

test("format bits equal the ISO/IEC 18004 Annex C values", () => {
  // (level, mask) -> 15-bit sequence after the 0x5412 XOR mask.
  assert.equal(formatBits(ECC_LEVELS.M, 0), 0x5412);
  assert.equal(formatBits(ECC_LEVELS.L, 0), 0x77c4);
  assert.equal(formatBits(ECC_LEVELS.L, 7), 0x6976);
  assert.equal(formatBits(ECC_LEVELS.H, 0), 0x1689);
  assert.equal(formatBits(ECC_LEVELS.Q, 3), 0x3a06);
});

test("version bits equal the ISO/IEC 18004 Annex D values", () => {
  assert.equal(versionBits(7), 0x07c94);
  assert.equal(versionBits(8), 0x085bc);
  assert.equal(versionBits(40), 0x28c69);
});

test("alignment pattern centres equal the ISO/IEC 18004 Annex E table", () => {
  assert.deepEqual(alignmentPatternPositions(1), []);
  assert.deepEqual(alignmentPatternPositions(2), [6, 18]);
  assert.deepEqual(alignmentPatternPositions(7), [6, 22, 38]);
  assert.deepEqual(alignmentPatternPositions(32), [6, 34, 60, 86, 112, 138]);
  assert.deepEqual(alignmentPatternPositions(40), [6, 30, 58, 86, 114, 142, 170]);
});

test("byte capacities equal the ISO/IEC 18004 Table 7 values", () => {
  assert.equal(byteCapacity(1, ECC_LEVELS.L), 17);
  assert.equal(byteCapacity(1, ECC_LEVELS.M), 14);
  assert.equal(byteCapacity(1, ECC_LEVELS.H), 7);
  assert.equal(byteCapacity(10, ECC_LEVELS.M), 213);
  assert.equal(byteCapacity(40, ECC_LEVELS.L), 2953);
  assert.equal(byteCapacity(40, ECC_LEVELS.H), 1273);
});

test("the smallest fitting version is chosen at the capacity boundary", () => {
  assert.equal(encodeQrBytes(new Array(14).fill(65), { ecl: "M" }).version, 1);
  assert.equal(encodeQrBytes(new Array(15).fill(65), { ecl: "M" }).version, 2);
});

function finderAt(m, x0, y0) {
  for (let dy = 0; dy < 7; dy++) {
    for (let dx = 0; dx < 7; dx++) {
      const ring = Math.max(Math.abs(dx - 3), Math.abs(dy - 3));
      if (m[y0 + dy][x0 + dx] !== (ring !== 2)) return false;
    }
  }
  return true;
}

test("structure: three finders, timing lines, the dark module, both format copies", () => {
  for (const text of ["x", "steamhangar://pair?v=1&url=https%3A%2F%2Fh.example&key=" + "k".repeat(90)]) {
    const qr = encodeQrText(text, { ecl: "M" });
    const m = qr.modules;
    const n = qr.size;
    assert.equal(finderAt(m, 0, 0), true, "top-left finder");
    assert.equal(finderAt(m, n - 7, 0), true, "top-right finder");
    assert.equal(finderAt(m, 0, n - 7), true, "bottom-left finder");
    for (let i = 8; i < n - 8; i++) {
      assert.equal(m[6][i], i % 2 === 0, `horizontal timing at ${i}`);
      assert.equal(m[i][6], i % 2 === 0, `vertical timing at ${i}`);
    }
    assert.equal(m[n - 8][8], true, "dark module");
    const bits = formatBits(ECC_LEVELS.M, qr.mask);
    const bit = (i) => ((bits >>> i) & 1) === 1;
    for (let i = 0; i < 8; i++) assert.equal(m[8][n - 1 - i], bit(i), `format copy 2, bit ${i}`);
    for (let i = 0; i <= 5; i++) assert.equal(m[i][8], bit(i), `format copy 1, bit ${i}`);
  }
});

test("automatic mask: deterministic, 0-7, and identical to forcing that mask", () => {
  const text = "steamhangar://pair?v=1&url=https%3A%2F%2Fhangar.example.org&key=abc";
  const a = encodeQrText(text);
  const b = encodeQrText(text);
  assert.equal(a.ecl, "M", "level M by default");
  assert.equal(Number.isInteger(a.mask) && a.mask >= 0 && a.mask <= 7, true);
  assert.equal(a.mask, b.mask);
  assert.deepEqual(rowsOf(a), rowsOf(encodeQrText(text, { mask: a.mask })));
});

test("UTF-8: a non-ASCII string is encoded as its UTF-8 bytes", () => {
  const viaText = encodeQrText("Schlüssel", { mask: 2 });
  const viaBytes = encodeQrBytes([...new TextEncoder().encode("Schlüssel")], { mask: 2 });
  assert.deepEqual(rowsOf(viaText), rowsOf(viaBytes));
});

test("MUTATION TARGET: data that does not fit throws instead of being cut off", () => {
  assert.throws(() => encodeQrBytes(new Array(2332).fill(0), { ecl: "M" }), QrDataTooLongError);
  assert.throws(() => encodeQrBytes(new Array(15).fill(0), { ecl: "M", maxVersion: 1 }), QrDataTooLongError);
  assert.throws(() => encodeQrText("x", { mask: 8 }), RangeError);
  assert.throws(() => encodeQrText("x", { ecl: "X" }), RangeError);
});

test("SVG geometry: one unit square per dark module, offset by a 4-module quiet zone", () => {
  const qr = encodeQrText("steamhangar", { mask: 3 });
  const { side, d } = qrSvgGeometry(qr.modules);
  assert.equal(QUIET_ZONE, 4);
  assert.equal(side, qr.size + 8);
  const dark = qr.modules.flat().filter(Boolean).length;
  let painted = 0;
  const cells = new Set();
  for (const m of d.matchAll(/M(\d+) (\d+)h(\d+)v1h-(\d+)z/g)) {
    const [x, y, w, back] = m.slice(1).map(Number);
    assert.equal(w, back);
    for (let i = 0; i < w; i++) {
      const cx = x + i - QUIET_ZONE;
      const cy = y - QUIET_ZONE;
      assert.equal(qr.modules[cy][cx], true, `painted (${cx},${cy}) is dark`);
      cells.add(`${cx},${cy}`);
    }
    painted += w;
  }
  assert.equal(painted, dark);
  assert.equal(cells.size, dark);
});
