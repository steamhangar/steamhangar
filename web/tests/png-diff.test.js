// WP WEB-GOLDENS-1: the dependency-free PNG codec and comparison behind
// tools/png-diff.mjs, exercised with synthetic images (no Chromium needed).
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { deflateSync } from "node:zlib";

import { compareDirs, compareImages, crc32, decodePng, encodePng, parseArgs } from "../../tools/png-diff.mjs";

function solid(width, height, [r, g, b, a = 255]) {
  const data = Buffer.alloc(width * height * 4);
  for (let i = 0; i < width * height; i += 1) data.set([r, g, b, a], i * 4);
  return { width, height, data };
}

function withPixels(image, pixels, rgba) {
  const copy = { ...image, data: Buffer.from(image.data) };
  for (const [x, y] of pixels) copy.data.set(rgba, (y * image.width + x) * 4);
  return copy;
}

/** Hand-rolled PNG writer for colour types the tool's encoder does not emit. */
function pngFromRows(width, height, colorType, rows, extraChunks = []) {
  const chunk = (type, body) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(body.length);
    const typeBuf = Buffer.from(type, "latin1");
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, body])));
    return Buffer.concat([len, typeBuf, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = colorType;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    ...extraChunks.map(([t, b]) => chunk(t, b)),
    chunk("IDAT", deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

test("crc32 matches the reference value for 'IEND'", () => {
  // The CRC every PNG carries in its last 4 bytes.
  assert.equal(crc32(Buffer.from("IEND", "latin1")), 0xae426082);
});

test("encodePng/decodePng round-trip RGBA pixels exactly", () => {
  const image = withPixels(solid(5, 3, [10, 20, 30]), [[0, 0], [4, 2]], [200, 100, 50, 128]);
  const decoded = decodePng(encodePng(image));
  assert.equal(decoded.width, 5);
  assert.equal(decoded.height, 3);
  assert.ok(decoded.data.equals(image.data));
});

test("decodePng unfilters every PNG filter type", () => {
  // 2x5 RGB, one row per filter type (0 none, 1 sub, 2 up, 3 average, 4 paeth),
  // all encoding the same two pixels (10,20,30) (40,50,60).
  const pixels = [10, 20, 30, 40, 50, 60];
  const rows = [
    Buffer.from([0, ...pixels]),
    Buffer.from([1, 10, 20, 30, 30, 30, 30]), // sub: second pixel minus first
    Buffer.from([2, 0, 0, 0, 0, 0, 0]), // up: identical to the row above
    Buffer.from([3, 10 - 5, 20 - 10, 30 - 15, 40 - ((10 + 40) >> 1), 50 - ((20 + 50) >> 1), 60 - ((30 + 60) >> 1)]),
    Buffer.from([4, 0, 0, 0, 0, 0, 0]), // paeth of identical neighbours is the pixel itself
  ];
  const decoded = decodePng(pngFromRows(2, 5, 2, rows));
  for (let y = 0; y < 5; y += 1) {
    assert.deepEqual([...decoded.data.subarray(y * 8, y * 8 + 8)], [10, 20, 30, 255, 40, 50, 60, 255], `row ${y}`);
  }
});

test("decodePng expands grey, grey+alpha and palette images to RGBA", () => {
  const grey = decodePng(pngFromRows(2, 1, 0, [Buffer.from([0, 7, 9])]));
  assert.deepEqual([...grey.data], [7, 7, 7, 255, 9, 9, 9, 255]);
  const greyAlpha = decodePng(pngFromRows(1, 1, 4, [Buffer.from([0, 7, 100])]));
  assert.deepEqual([...greyAlpha.data], [7, 7, 7, 100]);
  const palette = decodePng(pngFromRows(2, 1, 3, [Buffer.from([0, 1, 0])], [
    ["PLTE", Buffer.from([255, 0, 0, 0, 0, 255])],
    ["tRNS", Buffer.from([200])],
  ]));
  assert.deepEqual([...palette.data], [0, 0, 255, 255, 255, 0, 0, 200]);
});

test("decodePng rejects what it cannot decode instead of guessing", () => {
  assert.throws(() => decodePng(Buffer.from("not a png")), /bad signature/);
  const good = encodePng(solid(1, 1, [1, 2, 3]));
  const corrupt = Buffer.from(good);
  corrupt[corrupt.length - 5] ^= 0xff; // flip a byte of IEND's CRC
  assert.throws(() => decodePng(corrupt), /CRC mismatch/);
  const sixteenBit = Buffer.from(good);
  sixteenBit[16 + 8] = 16; // IHDR bit depth byte
  sixteenBit.writeUInt32BE(crc32(sixteenBit.subarray(12, 29)), 29);
  assert.throws(() => decodePng(sixteenBit), /bit depth 16/);
});

test("compareImages: identical images pass with zero differing pixels", () => {
  const image = solid(10, 10, [50, 60, 70]);
  const result = compareImages(image, solid(10, 10, [50, 60, 70]));
  assert.equal(result.differing, 0);
  assert.equal(result.pass, true);
  assert.equal(result.sameSize, true);
});

test("compareImages: per-channel tolerance absorbs small deltas, not larger ones", () => {
  const base = solid(10, 10, [100, 100, 100]);
  const within = withPixels(base, [[3, 3]], [108, 100, 100, 255]);
  const beyond = withPixels(base, [[3, 3]], [109, 100, 100, 255]);
  assert.equal(compareImages(base, within, { tolerance: 8, threshold: 0 }).differing, 0);
  assert.equal(compareImages(base, beyond, { tolerance: 8, threshold: 0 }).differing, 1);
  // Alpha counts as a channel too.
  const alpha = withPixels(base, [[0, 0]], [100, 100, 100, 0]);
  assert.equal(compareImages(base, alpha, { tolerance: 8 }).differing, 1);
});

test("compareImages: threshold is a fraction of all pixels, inclusive", () => {
  const base = solid(100, 10, [0, 0, 0]); // 1000 px; 0.002 allows exactly 2
  const two = withPixels(base, [[0, 0], [1, 0]], [255, 255, 255, 255]);
  const three = withPixels(base, [[0, 0], [1, 0], [2, 0]], [255, 255, 255, 255]);
  assert.equal(compareImages(base, two).pass, true);
  const failed = compareImages(base, three);
  assert.equal(failed.pass, false);
  assert.equal(failed.differing, 3);
  assert.ok(Math.abs(failed.fraction - 0.003) < 1e-12);
});

test("compareImages: a size mismatch fails even when the overlap is identical", () => {
  const result = compareImages(solid(4, 4, [1, 1, 1]), solid(4, 6, [1, 1, 1]));
  assert.equal(result.sameSize, false);
  assert.equal(result.pass, false);
  assert.equal(result.differing, 8); // the two extra rows
  assert.equal(result.diff.height, 6);
  // Out-of-bounds area is marked magenta, differing pixels red, the rest grey.
  assert.deepEqual([...result.diff.data.subarray((5 * 4) * 4, (5 * 4) * 4 + 4)], [255, 0, 255, 255]);
  const red = compareImages(solid(1, 1, [0, 0, 0]), solid(1, 1, [255, 255, 255]));
  assert.deepEqual([...red.diff.data], [255, 0, 0, 255]);
  const grey = compareImages(solid(1, 1, [0, 0, 0]), solid(1, 1, [0, 0, 0]));
  assert.equal(grey.diff.data[0], grey.diff.data[1]);
  assert.equal(grey.diff.data[3], 255);
});

test("compareDirs: pass / fail / missing / stale per file, diff PNG only for failures", () => {
  const root = mkdtempSync(join(tmpdir(), "png-diff-test-"));
  const expected = join(root, "expected");
  const actual = join(root, "actual");
  const diff = join(root, "diff");
  mkdirSync(expected);
  mkdirSync(actual);
  const base = solid(20, 20, [10, 10, 10]);
  writeFileSync(join(expected, "same.png"), encodePng(base));
  writeFileSync(join(actual, "same.png"), encodePng(base));
  writeFileSync(join(expected, "changed.png"), encodePng(base));
  writeFileSync(join(actual, "changed.png"), encodePng(solid(20, 20, [200, 10, 10])));
  writeFileSync(join(actual, "new.png"), encodePng(base));
  writeFileSync(join(expected, "old.png"), encodePng(base));
  writeFileSync(join(actual, "notes.txt"), "ignored");

  const strict = compareDirs(expected, actual, diff);
  const byName = Object.fromEntries(strict.rows.map((r) => [r.name, r.status]));
  assert.deepEqual(byName, { "same.png": "pass", "changed.png": "fail", "new.png": "missing", "old.png": "stale" });
  assert.equal(strict.ok, false);
  assert.equal(existsSync(join(diff, "changed.png")), true);
  assert.equal(existsSync(join(diff, "same.png")), false);
  const diffImage = decodePng(readFileSync(join(diff, "changed.png")));
  assert.deepEqual([...diffImage.data.subarray(0, 4)], [255, 0, 0, 255]);

  // `missing: warn` tolerates the golden-less screenshot but nothing else.
  const warn = compareDirs(expected, actual, diff, { missing: "warn" });
  assert.equal(warn.ok, false, "changed + stale still fail");
  unlinkSync(join(expected, "old.png"));
  writeFileSync(join(actual, "changed.png"), encodePng(base));
  assert.equal(compareDirs(expected, actual, diff, { missing: "warn" }).ok, true);
  assert.equal(compareDirs(expected, actual, diff, { missing: "fail" }).ok, false);
});

test("compareDirs: an empty actual directory is an error, not a pass", () => {
  const root = mkdtempSync(join(tmpdir(), "png-diff-test-"));
  mkdirSync(join(root, "a"));
  mkdirSync(join(root, "e"));
  assert.throws(() => compareDirs(join(root, "e"), join(root, "a"), join(root, "d")), /no PNG files/);
});

test("parseArgs: defaults and validation", () => {
  const opts = parseArgs(["--expected", "e", "--actual", "a"]);
  assert.equal(opts.threshold, 0.002);
  assert.equal(opts.tolerance, 8);
  assert.equal(opts.missing, "fail");
  assert.equal(opts.github, false);
  assert.throws(() => parseArgs(["--actual", "a"]), /--expected/);
  assert.throws(() => parseArgs(["--expected", "e", "--actual", "a", "--threshold", "2"]), /--threshold/);
  assert.throws(() => parseArgs(["--expected", "e", "--actual", "a", "--tolerance", "1.5"]), /--tolerance/);
  assert.throws(() => parseArgs(["--expected", "e", "--actual", "a", "--missing", "record"]), /--missing/);
  assert.throws(() => parseArgs(["--bogus"]), /unknown argument/);
});
