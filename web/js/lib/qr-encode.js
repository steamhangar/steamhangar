/**
 * Minimal QR Code encoder, byte mode only (WP PAIR-1).
 *
 * Settings → "Add a device" shows the Android pairing URI as a QR code. The
 * code carries the vault API key, so it must be generated on this page: no
 * third-party service, no network call. The repo had no QR library, and the
 * web UI has no build step or package manager (plain ES modules served by
 * vault-api), so this is a small encoder written for this project instead of
 * a vendored file. Reasons, recorded for the review:
 *
 *  - Only one feature is needed: one byte-mode segment (the UTF-8 bytes of
 *    a URI) at a fixed error-correction level. No numeric/alphanumeric/
 *    kanji/ECI segments, no structured append.
 *  - The well-known single-file JS libraries are UMD/CommonJS bundles, not
 *    ES modules; vendoring one means a wrapper or a global, in a codebase
 *    whose CSP (`script-src 'self'`) and module layout make an ES module the
 *    natural fit.
 *  - Everything here is checked in web/tests/qr-encode.test.js against
 *    module matrices produced by an independent encoder (segno 1.6.6,
 *    BSD-3-Clause, used once on the developer's machine to produce the
 *    fixtures, not shipped), plus structural invariants (finder, timing,
 *    format and version bits) and a capacity check for every version and
 *    level.
 *
 * The algorithm follows ISO/IEC 18004 in the structure popularised by
 * Project Nayuki's "QR Code generator library" (MIT): pick the smallest
 * version that fits, add terminator and pad bytes, split into blocks, append
 * Reed-Solomon error correction over GF(2^8) mod 0x11D, interleave, place
 * function patterns and data in the zigzag order, then try all eight masks
 * and keep the one with the lowest penalty score (N1..N4 rules).
 *
 * Pure: no DOM, no fetch, no globals beyond `TextEncoder`.
 */

/** Error-correction levels: `ordinal` indexes the tables below, `formatBits`
 * is the two-bit value written into the format information. */
export const ECC_LEVELS = Object.freeze({
  L: Object.freeze({ ordinal: 0, formatBits: 1 }),
  M: Object.freeze({ ordinal: 1, formatBits: 0 }),
  Q: Object.freeze({ ordinal: 2, formatBits: 3 }),
  H: Object.freeze({ ordinal: 3, formatBits: 2 }),
});

export const MIN_VERSION = 1;
export const MAX_VERSION = 40;

// Error-correction codewords per block, indexed [level ordinal][version].
// Index 0 is unused (there is no version 0).
const ECC_CODEWORDS_PER_BLOCK = [
  [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
  [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
];

// Number of error-correction blocks, indexed [level ordinal][version].
const NUM_ERROR_CORRECTION_BLOCKS = [
  [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
  [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
  [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
  [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],
];

const PENALTY_N1 = 3;
const PENALTY_N2 = 3;
const PENALTY_N3 = 40;
const PENALTY_N4 = 10;

/** Thrown when the data does not fit any allowed version. */
export class QrDataTooLongError extends Error {}

function getBit(value, index) {
  return ((value >>> index) & 1) !== 0;
}

/** Data modules available in a version, after all function patterns
 * (finders, timing, alignment, format and version information). */
export function numRawDataModules(version) {
  let result = (16 * version + 128) * version + 64;
  if (version >= 2) {
    const numAlign = Math.floor(version / 7) + 2;
    result -= (25 * numAlign - 10) * numAlign - 55;
    if (version >= 7) result -= 36;
  }
  return result;
}

/** 8-bit data codewords a version holds at a level (error correction
 * excluded). */
export function numDataCodewords(version, ecl) {
  return (
    Math.floor(numRawDataModules(version) / 8) -
    ECC_CODEWORDS_PER_BLOCK[ecl.ordinal][version] * NUM_ERROR_CORRECTION_BLOCKS[ecl.ordinal][version]
  );
}

/** Bits of the byte-mode character count field. */
function byteModeCountBits(version) {
  return version <= 9 ? 8 : 16;
}

/** Most bytes one byte-mode segment can carry in a version at a level. */
export function byteCapacity(version, ecl) {
  const bits = numDataCodewords(version, ecl) * 8 - 4 - byteModeCountBits(version);
  return Math.min(Math.floor(bits / 8), (1 << byteModeCountBits(version)) - 1);
}

// ---- Reed-Solomon over GF(2^8), primitive polynomial 0x11D -------------

function gfMultiply(x, y) {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xff;
}

function reedSolomonDivisor(degree) {
  const result = new Array(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < result.length; j++) {
      result[j] = gfMultiply(result[j], root);
      if (j + 1 < result.length) result[j] ^= result[j + 1];
    }
    root = gfMultiply(root, 0x02);
  }
  return result;
}

function reedSolomonRemainder(data, divisor) {
  const result = new Array(divisor.length).fill(0);
  for (const b of data) {
    const factor = b ^ result.shift();
    result.push(0);
    divisor.forEach((coef, i) => {
      result[i] ^= gfMultiply(coef, factor);
    });
  }
  return result;
}

// ---- Codeword construction ---------------------------------------------

function buildDataCodewords(bytes, version, ecl) {
  const bits = [];
  const push = (value, length) => {
    for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4); // byte mode
  push(bytes.length, byteModeCountBits(version));
  for (const b of bytes) push(b, 8);

  const capacityBits = numDataCodewords(version, ecl) * 8;
  push(0, Math.min(4, capacityBits - bits.length)); // terminator
  push(0, (8 - (bits.length % 8)) % 8); // byte alignment
  for (let pad = 0xec; bits.length < capacityBits; pad ^= 0xec ^ 0x11) push(pad, 8);

  const codewords = new Array(bits.length / 8).fill(0);
  bits.forEach((bit, i) => {
    codewords[i >>> 3] |= bit << (7 - (i & 7));
  });
  return codewords;
}

function addEccAndInterleave(data, version, ecl) {
  const numBlocks = NUM_ERROR_CORRECTION_BLOCKS[ecl.ordinal][version];
  const blockEccLen = ECC_CODEWORDS_PER_BLOCK[ecl.ordinal][version];
  const rawCodewords = Math.floor(numRawDataModules(version) / 8);
  const numShortBlocks = numBlocks - (rawCodewords % numBlocks);
  const shortBlockLen = Math.floor(rawCodewords / numBlocks);

  const divisor = reedSolomonDivisor(blockEccLen);
  const blocks = [];
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const dat = data.slice(k, k + shortBlockLen - blockEccLen + (i < numShortBlocks ? 0 : 1));
    k += dat.length;
    const ecc = reedSolomonRemainder(dat, divisor);
    if (i < numShortBlocks) dat.push(0); // placeholder, skipped below
    blocks.push(dat.concat(ecc));
  }

  const result = [];
  for (let i = 0; i < blocks[0].length; i++) {
    blocks.forEach((block, j) => {
      if (i !== shortBlockLen - blockEccLen || j >= numShortBlocks) result.push(block[i]);
    });
  }
  return result;
}

// ---- Matrix ------------------------------------------------------------

/** Centre coordinates of the alignment patterns of a version. */
export function alignmentPatternPositions(version) {
  if (version === 1) return [];
  const size = version * 4 + 17;
  const numAlign = Math.floor(version / 7) + 2;
  const step = Math.floor((version * 8 + numAlign * 3 + 5) / (numAlign * 4 - 4)) * 2;
  const result = [6];
  for (let pos = size - 7; result.length < numAlign; pos -= step) result.splice(1, 0, pos);
  return result;
}

/** The 15 format bits (level + mask, BCH-coded, XOR-masked). */
export function formatBits(ecl, mask) {
  const data = (ecl.formatBits << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

/** The 18 version bits (versions 7 and up). */
export function versionBits(version) {
  let rem = version;
  for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
  return (version << 12) | rem;
}

class Matrix {
  constructor(version) {
    this.version = version;
    this.size = version * 4 + 17;
    this.modules = Array.from({ length: this.size }, () => new Array(this.size).fill(false));
    this.isFunction = Array.from({ length: this.size }, () => new Array(this.size).fill(false));
  }

  setFunction(x, y, dark) {
    this.modules[y][x] = dark;
    this.isFunction[y][x] = true;
  }

  drawFunctionPatterns(ecl) {
    const { size } = this;
    for (let i = 0; i < size; i++) {
      this.setFunction(6, i, i % 2 === 0);
      this.setFunction(i, 6, i % 2 === 0);
    }
    this.drawFinder(3, 3);
    this.drawFinder(size - 4, 3);
    this.drawFinder(3, size - 4);
    const align = alignmentPatternPositions(this.version);
    const n = align.length;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if ((i === 0 && j === 0) || (i === 0 && j === n - 1) || (i === n - 1 && j === 0)) continue;
        this.drawAlignment(align[i], align[j]);
      }
    }
    this.drawFormatBits(ecl, 0); // reserved now, rewritten after masking
    this.drawVersion();
  }

  drawFinder(x, y) {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const dist = Math.max(Math.abs(dx), Math.abs(dy));
        const xx = x + dx;
        const yy = y + dy;
        if (xx >= 0 && xx < this.size && yy >= 0 && yy < this.size) {
          this.setFunction(xx, yy, dist !== 2 && dist !== 4);
        }
      }
    }
  }

  drawAlignment(x, y) {
    for (let dy = -2; dy <= 2; dy++) {
      for (let dx = -2; dx <= 2; dx++) {
        this.setFunction(x + dx, y + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    }
  }

  drawFormatBits(ecl, mask) {
    const bits = formatBits(ecl, mask);
    const { size } = this;
    for (let i = 0; i <= 5; i++) this.setFunction(8, i, getBit(bits, i));
    this.setFunction(8, 7, getBit(bits, 6));
    this.setFunction(8, 8, getBit(bits, 7));
    this.setFunction(7, 8, getBit(bits, 8));
    for (let i = 9; i < 15; i++) this.setFunction(14 - i, 8, getBit(bits, i));
    for (let i = 0; i < 8; i++) this.setFunction(size - 1 - i, 8, getBit(bits, i));
    for (let i = 8; i < 15; i++) this.setFunction(8, size - 15 + i, getBit(bits, i));
    this.setFunction(8, size - 8, true); // the always-dark module
  }

  drawVersion() {
    if (this.version < 7) return;
    const bits = versionBits(this.version);
    for (let i = 0; i < 18; i++) {
      const dark = getBit(bits, i);
      const a = this.size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      this.setFunction(a, b, dark);
      this.setFunction(b, a, dark);
    }
  }

  drawCodewords(codewords) {
    const { size } = this;
    let i = 0;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5; // skip the vertical timing column
      for (let vert = 0; vert < size; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j;
          const upward = ((right + 1) & 2) === 0;
          const y = upward ? size - 1 - vert : vert;
          if (!this.isFunction[y][x] && i < codewords.length * 8) {
            this.modules[y][x] = getBit(codewords[i >>> 3], 7 - (i & 7));
            i++;
          }
        }
      }
    }
  }

  applyMask(mask) {
    for (let y = 0; y < this.size; y++) {
      for (let x = 0; x < this.size; x++) {
        if (this.isFunction[y][x]) continue;
        let invert;
        switch (mask) {
          case 0: invert = (x + y) % 2 === 0; break;
          case 1: invert = y % 2 === 0; break;
          case 2: invert = x % 3 === 0; break;
          case 3: invert = (x + y) % 3 === 0; break;
          case 4: invert = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; break;
          case 5: invert = ((x * y) % 2) + ((x * y) % 3) === 0; break;
          case 6: invert = (((x * y) % 2) + ((x * y) % 3)) % 2 === 0; break;
          case 7: invert = (((x + y) % 2) + ((x * y) % 3)) % 2 === 0; break;
          default: throw new RangeError(`QR mask must be 0-7, got ${mask}`);
        }
        if (invert) this.modules[y][x] = !this.modules[y][x];
      }
    }
  }

  penaltyScore() {
    const { size, modules } = this;
    let result = 0;
    const line = (get) => {
      for (let a = 0; a < size; a++) {
        let runColor = false;
        let run = 0;
        const history = [0, 0, 0, 0, 0, 0, 0];
        for (let b = 0; b < size; b++) {
          if (get(a, b) === runColor) {
            run++;
            if (run === 5) result += PENALTY_N1;
            else if (run > 5) result++;
          } else {
            this.addRunHistory(run, history);
            if (!runColor) result += this.countFinderLike(history) * PENALTY_N3;
            runColor = get(a, b);
            run = 1;
          }
        }
        result += this.terminateRunHistory(runColor, run, history) * PENALTY_N3;
      }
    };
    line((y, x) => modules[y][x]); // rows
    line((x, y) => modules[y][x]); // columns

    for (let y = 0; y < size - 1; y++) {
      for (let x = 0; x < size - 1; x++) {
        const c = modules[y][x];
        if (c === modules[y][x + 1] && c === modules[y + 1][x] && c === modules[y + 1][x + 1]) result += PENALTY_N2;
      }
    }

    let dark = 0;
    for (const row of modules) for (const m of row) if (m) dark++;
    const total = size * size;
    const k = Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1;
    result += k * PENALTY_N4;
    return result;
  }

  countFinderLike(h) {
    const n = h[1];
    const core = n > 0 && h[2] === n && h[3] === n * 3 && h[4] === n && h[5] === n;
    return (core && h[0] >= n * 4 && h[6] >= n ? 1 : 0) + (core && h[6] >= n * 4 && h[0] >= n ? 1 : 0);
  }

  terminateRunHistory(runColor, run, history) {
    if (runColor) {
      this.addRunHistory(run, history);
      run = 0;
    }
    run += this.size; // light border after the line
    this.addRunHistory(run, history);
    return this.countFinderLike(history);
  }

  addRunHistory(run, history) {
    if (history[0] === 0) run += this.size; // light border before the line
    history.pop();
    history.unshift(run);
  }
}

/**
 * Encode bytes as a QR Code in byte mode.
 *
 * @param {Uint8Array | number[]} bytes
 * @param {{ecl?: "L"|"M"|"Q"|"H", mask?: number | null, minVersion?: number, maxVersion?: number}} [options]
 *   `mask` null (default) picks the lowest-penalty mask; a number forces it
 *   (tests compare forced masks against the reference encoder).
 * @returns {{version: number, size: number, mask: number, ecl: string, modules: boolean[][]}}
 *   `modules[y][x]`, true = dark. No quiet zone included.
 */
export function encodeQrBytes(bytes, { ecl = "M", mask = null, minVersion = MIN_VERSION, maxVersion = MAX_VERSION } = {}) {
  const level = ECC_LEVELS[ecl];
  if (!level) throw new RangeError(`unknown QR error-correction level ${ecl}`);
  if (mask !== null && !(Number.isInteger(mask) && mask >= 0 && mask <= 7)) {
    throw new RangeError(`QR mask must be 0-7, got ${mask}`);
  }
  const data = Array.from(bytes, (b) => b & 0xff);
  let version = Math.max(MIN_VERSION, minVersion);
  for (; ; version++) {
    if (version > Math.min(MAX_VERSION, maxVersion)) {
      throw new QrDataTooLongError(`${data.length} bytes do not fit a QR code up to version ${maxVersion} at level ${ecl}`);
    }
    if (data.length <= byteCapacity(version, level)) break;
  }

  const codewords = addEccAndInterleave(buildDataCodewords(data, version, level), version, level);
  const matrix = new Matrix(version);
  matrix.drawFunctionPatterns(level);
  matrix.drawCodewords(codewords);

  let chosen = mask;
  if (chosen === null) {
    let best = Infinity;
    for (let m = 0; m < 8; m++) {
      matrix.applyMask(m);
      matrix.drawFormatBits(level, m);
      const score = matrix.penaltyScore();
      if (score < best) {
        best = score;
        chosen = m;
      }
      matrix.applyMask(m); // XOR again: undo
    }
  }
  matrix.applyMask(chosen);
  matrix.drawFormatBits(level, chosen);

  return { version, size: matrix.size, mask: chosen, ecl, modules: matrix.modules };
}

/** Encode a string's UTF-8 bytes. Same options and result as
 * {@link encodeQrBytes}. */
export function encodeQrText(text, options) {
  return encodeQrBytes(new TextEncoder().encode(String(text)), options);
}

/** Light modules around the symbol; ISO/IEC 18004 asks for at least 4. */
export const QUIET_ZONE = 4;

/**
 * SVG geometry for a symbol: the `viewBox` side (symbol plus quiet zone on
 * both sides) and one path `d` drawing every dark module as a unit square,
 * merged into horizontal runs. The caller paints a light background over the
 * whole viewBox and this path dark on top.
 * @param {boolean[][]} modules
 * @param {number} [quiet]
 * @returns {{side: number, d: string}}
 */
export function qrSvgGeometry(modules, quiet = QUIET_ZONE) {
  const size = modules.length;
  let d = "";
  for (let y = 0; y < size; y++) {
    let x = 0;
    while (x < size) {
      if (!modules[y][x]) {
        x++;
        continue;
      }
      const start = x;
      while (x < size && modules[y][x]) x++;
      d += `M${start + quiet} ${y + quiet}h${x - start}v1h-${x - start}z`;
    }
  }
  return { side: size + 2 * quiet, d };
}
