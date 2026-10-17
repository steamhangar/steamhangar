#!/usr/bin/env node
// Pixel comparison of two PNG directories (WP WEB-GOLDENS-1).
//
// No dependencies: PNGs are decoded and encoded here with node:zlib (8-bit
// grey / grey+alpha / RGB / RGBA / palette, non-interlaced -- everything
// Chromium's screenshot writer and this tool's own encoder produce; anything
// else is rejected with a clear error instead of a wrong answer).
//
// A pair passes when the fraction of differing pixels is at or below
// --threshold (default 0.002 = 0.2 %). A pixel differs when any channel
// differs by more than --tolerance (default 8 of 255), which absorbs
// anti-aliasing noise without hiding a moved or recoloured element. Two
// images of different size always fail. Every failing pair gets a diff
// PNG in --diff: the expected image faded to grey, differing pixels red,
// area outside the smaller image magenta.
//
//   node tools/png-diff.mjs --expected web/tests/goldens --actual shots \
//     --diff diff [--threshold 0.002] [--tolerance 8] [--missing fail|warn] [--github]
//
// --missing decides what an actual PNG without a golden means: `fail`
// (default) or `warn` (CI verify mode: the first run after a new page is
// added has nothing to compare against, like the Android goldens). `warn`
// covers wholly new pages only: a screenshot whose page already has goldens
// at other widths is `partial` and fails. A golden without an actual PNG
// always fails: it is stale or its page was not shot.
// --github additionally prints workflow commands (::error / ::notice) and a
// table into $GITHUB_STEP_SUMMARY when that variable is set.
import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync, inflateSync } from "node:zlib";

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

// ---------------------------------------------------------------------------
// PNG codec
// ---------------------------------------------------------------------------

const CRC_TABLE = new Int32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c;
});

export function crc32(buf) {
  let c = -1;
  for (let i = 0; i < buf.length; i += 1) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  return pb <= pc ? b : c;
}

/**
 * Decode a PNG buffer to `{ width, height, data }` with `data` RGBA, 8 bits
 * per channel, row-major (4 bytes per pixel).
 */
export function decodePng(buf) {
  if (buf.length < 8 || !buf.subarray(0, 8).equals(SIGNATURE)) throw new Error("not a PNG (bad signature)");
  let pos = 8;
  let header = null;
  let palette = null;
  let trns = null;
  const idat = [];
  while (pos + 8 <= buf.length) {
    const length = buf.readUInt32BE(pos);
    const type = buf.toString("latin1", pos + 4, pos + 8);
    const start = pos + 8;
    const end = start + length;
    if (end + 4 > buf.length) throw new Error(`truncated PNG chunk ${type}`);
    const expected = buf.readUInt32BE(end);
    if (crc32(buf.subarray(pos + 4, end)) !== expected) throw new Error(`PNG chunk ${type}: CRC mismatch`);
    const body = buf.subarray(start, end);
    if (type === "IHDR") {
      header = {
        width: body.readUInt32BE(0), height: body.readUInt32BE(4), bitDepth: body[8],
        colorType: body[9], compression: body[10], filter: body[11], interlace: body[12],
      };
    } else if (type === "PLTE") palette = Buffer.from(body);
    else if (type === "tRNS") trns = Buffer.from(body);
    else if (type === "IDAT") idat.push(body);
    else if (type === "IEND") break;
    pos = end + 4;
  }
  if (!header) throw new Error("PNG without IHDR");
  if (header.bitDepth !== 8) throw new Error(`unsupported PNG bit depth ${header.bitDepth} (only 8)`);
  if (header.interlace !== 0) throw new Error("interlaced PNGs are not supported");
  if (header.compression !== 0 || header.filter !== 0) throw new Error("unknown PNG compression/filter method");
  const channels = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[header.colorType];
  if (channels === undefined) throw new Error(`unsupported PNG colour type ${header.colorType}`);
  if (header.colorType === 3 && !palette) throw new Error("palette PNG without PLTE");
  if (idat.length === 0) throw new Error("PNG without IDAT");

  const { width, height } = header;
  const stride = width * channels;
  const raw = inflateSync(Buffer.concat(idat));
  if (raw.length !== (stride + 1) * height) throw new Error("PNG image data has the wrong length");

  const data = Buffer.alloc(width * height * 4);
  let prev = Buffer.alloc(stride);
  for (let y = 0; y < height; y += 1) {
    const filterType = raw[y * (stride + 1)];
    const line = Buffer.from(raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1)));
    for (let i = 0; i < stride; i += 1) {
      const a = i >= channels ? line[i - channels] : 0;
      const b = prev[i];
      const c = i >= channels ? prev[i - channels] : 0;
      switch (filterType) {
        case 0: break;
        case 1: line[i] = (line[i] + a) & 0xff; break;
        case 2: line[i] = (line[i] + b) & 0xff; break;
        case 3: line[i] = (line[i] + ((a + b) >> 1)) & 0xff; break;
        case 4: line[i] = (line[i] + paeth(a, b, c)) & 0xff; break;
        default: throw new Error(`unknown PNG filter type ${filterType} on row ${y}`);
      }
    }
    for (let x = 0; x < width; x += 1) {
      const o = (y * width + x) * 4;
      const s = x * channels;
      switch (header.colorType) {
        case 0: data[o] = data[o + 1] = data[o + 2] = line[s]; data[o + 3] = 255; break;
        case 2: data[o] = line[s]; data[o + 1] = line[s + 1]; data[o + 2] = line[s + 2]; data[o + 3] = 255; break;
        case 3: {
          const idx = line[s];
          if (idx * 3 + 2 >= palette.length) throw new Error(`palette index ${idx} out of range`);
          data[o] = palette[idx * 3]; data[o + 1] = palette[idx * 3 + 1]; data[o + 2] = palette[idx * 3 + 2];
          data[o + 3] = trns && idx < trns.length ? trns[idx] : 255;
          break;
        }
        case 4: data[o] = data[o + 1] = data[o + 2] = line[s]; data[o + 3] = line[s + 1]; break;
        default: data[o] = line[s]; data[o + 1] = line[s + 1]; data[o + 2] = line[s + 2]; data[o + 3] = line[s + 3];
      }
    }
    prev = line;
  }
  return { width, height, data };
}

function chunk(type, body) {
  const out = Buffer.alloc(12 + body.length);
  out.writeUInt32BE(body.length, 0);
  out.write(type, 4, "latin1");
  body.copy(out, 8);
  out.writeUInt32BE(crc32(out.subarray(4, 8 + body.length)), 8 + body.length);
  return out;
}

/** Encode RGBA 8-bit `{ width, height, data }` as a PNG (filter 0 rows). */
export function encodePng({ width, height, data }) {
  if (data.length !== width * height * 4) throw new Error("encodePng: data length does not match width*height*4");
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y += 1) {
    raw[y * (width * 4 + 1)] = 0;
    data.copy(raw, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
  }
  return Buffer.concat([SIGNATURE, chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

// ---------------------------------------------------------------------------
// Comparison
// ---------------------------------------------------------------------------

/**
 * Compare two decoded images. Returns `{ sameSize, total, differing,
 * fraction, pass, diff }` where `diff` is an RGBA image of the union size.
 */
export function compareImages(expected, actual, { threshold = 0.002, tolerance = 8 } = {}) {
  const width = Math.max(expected.width, actual.width);
  const height = Math.max(expected.height, actual.height);
  const sameSize = expected.width === actual.width && expected.height === actual.height;
  const diff = Buffer.alloc(width * height * 4);
  let differing = 0;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const o = (y * width + x) * 4;
      const inExpected = x < expected.width && y < expected.height;
      const inActual = x < actual.width && y < actual.height;
      if (!inExpected || !inActual) {
        diff[o] = 255; diff[o + 1] = 0; diff[o + 2] = 255; diff[o + 3] = 255;
        differing += 1;
        continue;
      }
      const e = (y * expected.width + x) * 4;
      const a = (y * actual.width + x) * 4;
      const delta = Math.max(
        Math.abs(expected.data[e] - actual.data[a]),
        Math.abs(expected.data[e + 1] - actual.data[a + 1]),
        Math.abs(expected.data[e + 2] - actual.data[a + 2]),
        Math.abs(expected.data[e + 3] - actual.data[a + 3]),
      );
      if (delta > tolerance) {
        diff[o] = 255; diff[o + 1] = 0; diff[o + 2] = 0; diff[o + 3] = 255;
        differing += 1;
      } else {
        // Faded grey of the expected pixel, so the diff stays readable.
        const grey = Math.round((expected.data[e] * 0.299 + expected.data[e + 1] * 0.587 + expected.data[e + 2] * 0.114) * 0.35 + 150);
        diff[o] = diff[o + 1] = diff[o + 2] = grey; diff[o + 3] = 255;
      }
    }
  }
  const total = width * height;
  const fraction = total === 0 ? 0 : differing / total;
  return { sameSize, total, differing, fraction, pass: sameSize && fraction <= threshold, diff: { width, height, data: diff } };
}

const PNG_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]*\.png$/;

function listPngs(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isFile() && PNG_NAME.test(e.name))
    .map((e) => e.name)
    .sort();
}

/** Row statuses that always fail, whatever --missing says. */
export const FAILING = new Set(["fail", "error", "stale", "partial"]);

/**
 * Compare every PNG in `actualDir` with its namesake in `expectedDir`.
 * Returns `{ rows, ok }`; `rows` carry `status` of pass | fail | missing |
 * partial | stale | error. Diff PNGs for failing pairs are written to
 * `diffDir`.
 */
export function compareDirs(expectedDir, actualDir, diffDir, opts = {}) {
  const { threshold = 0.002, tolerance = 8, missing = "fail" } = opts;
  const expectedNames = listPngs(expectedDir);
  const actualNames = listPngs(actualDir);
  const rows = [];
  if (actualNames.length === 0) throw new Error(`no PNG files in ${actualDir}`);
  // `<page>-` of `<page>-<width>.png`; null for names of another shape.
  const pageOf = (name) => /^(.+)-\d+\.png$/.exec(name)?.[1] ?? null;
  const goldenPages = new Set(expectedNames.map(pageOf).filter((p) => p !== null));
  for (const name of actualNames) {
    if (!expectedNames.includes(name)) {
      // Under --missing warn only a wholly new page is a notice. A page that
      // already has goldens at other widths is a broken record set (a new
      // width, a lost file) and fails, or the gap would never close.
      if (missing === "warn" && goldenPages.has(pageOf(name))) {
        rows.push({ name, status: "partial", detail: "page has goldens at other widths but not this one" });
      } else {
        rows.push({ name, status: "missing", detail: "no golden for this screenshot" });
      }
      continue;
    }
    try {
      const expected = decodePng(readFileSync(join(expectedDir, name)));
      const actual = decodePng(readFileSync(join(actualDir, name)));
      const result = compareImages(expected, actual, { threshold, tolerance });
      const pct = (result.fraction * 100).toFixed(3);
      const sizes = `${expected.width}x${expected.height}` + (result.sameSize ? "" : ` vs ${actual.width}x${actual.height}`);
      let detail = `${sizes}, ${result.differing}/${result.total} px differ (${pct} %)`;
      if (!result.sameSize) detail = `size differs: ${detail}`;
      if (!result.pass) {
        mkdirSync(diffDir, { recursive: true });
        writeFileSync(join(diffDir, name), encodePng(result.diff));
      }
      rows.push({ name, status: result.pass ? "pass" : "fail", detail, fraction: result.fraction, sameSize: result.sameSize });
    } catch (err) {
      rows.push({ name, status: "error", detail: err.message });
    }
  }
  for (const name of expectedNames) {
    if (!actualNames.includes(name)) rows.push({ name, status: "stale", detail: "golden exists but no screenshot was taken" });
  }
  const failing = rows.filter((r) => FAILING.has(r.status) || (r.status === "missing" && missing !== "warn"));
  return { rows, ok: failing.length === 0 };
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

export function parseArgs(argv) {
  const opts = { expected: null, actual: null, diff: "diff", threshold: 0.002, tolerance: 8, missing: "fail", github: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const take = () => {
      const value = argv[i + 1];
      if (value === undefined) throw new Error(`${arg} needs a value`);
      i += 1;
      return value;
    };
    switch (arg) {
      case "--expected": opts.expected = take(); break;
      case "--actual": opts.actual = take(); break;
      case "--diff": opts.diff = take(); break;
      case "--threshold": opts.threshold = Number(take()); break;
      case "--tolerance": opts.tolerance = Number(take()); break;
      case "--missing": opts.missing = take(); break;
      case "--github": opts.github = true; break;
      case "--help": case "-h": opts.help = true; break;
      default: throw new Error(`unknown argument: ${arg}`);
    }
  }
  if (opts.help) return opts;
  if (!opts.expected || !opts.actual) throw new Error("--expected and --actual are required");
  if (!(opts.threshold >= 0 && opts.threshold <= 1)) throw new Error("--threshold: fraction between 0 and 1");
  if (!(Number.isInteger(opts.tolerance) && opts.tolerance >= 0 && opts.tolerance <= 255)) throw new Error("--tolerance: integer 0..255");
  if (!["fail", "warn"].includes(opts.missing)) throw new Error("--missing: fail or warn");
  return opts;
}

/** GitHub workflow-command escaping for the message part. */
function escapeMessage(text) {
  return String(text).replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A");
}

export function formatTable(rows) {
  const width = Math.max(4, ...rows.map((r) => r.name.length));
  const lines = [`${"name".padEnd(width)}  status   detail`];
  for (const r of rows) lines.push(`${r.name.padEnd(width)}  ${r.status.padEnd(7)}  ${r.detail}`);
  return lines.join("\n");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (err) {
    console.error(`png-diff: ${err.message}`);
    process.exit(2);
  }
  if (opts.help) {
    const header = [];
    for (const line of readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n").slice(1)) {
      if (!line.startsWith("//")) break;
      header.push(line.replace(/^\/\/ ?/, ""));
    }
    console.log(header.join("\n"));
    process.exit(0);
  }
  let result;
  try {
    result = compareDirs(opts.expected, opts.actual, opts.diff, opts);
  } catch (err) {
    console.error(`png-diff: ${err.message}`);
    if (opts.github) console.log(`::error title=Screenshot comparison failed::${escapeMessage(err.message)}`);
    process.exit(1);
  }
  console.log(formatTable(result.rows));
  const missingRows = result.rows.filter((r) => r.status === "missing");
  if (opts.github) {
    for (const r of result.rows) {
      if (FAILING.has(r.status) || (r.status === "missing" && opts.missing !== "warn")) {
        console.log(`::error title=Screenshot golden ${r.status}::${escapeMessage(`${r.name}: ${r.detail}`)}`);
      }
    }
    if (missingRows.length && opts.missing === "warn") {
      console.log(`::notice title=Screenshot goldens missing::${escapeMessage(
        `${missingRows.length} screenshot(s) have no golden yet: ${missingRows.map((r) => r.name).join(", ")}. Record them on a wp/<name>-goldens branch (tools/README.md).`,
      )}`);
    }
    if (process.env.GITHUB_STEP_SUMMARY) {
      const md = ["### Web screenshot goldens", "", "| screenshot | status | detail |", "|---|---|---|",
        ...result.rows.map((r) => `| ${r.name} | ${r.status} | ${r.detail.replace(/\|/g, "\\|")} |`), ""].join("\n");
      appendFileSync(process.env.GITHUB_STEP_SUMMARY, md);
    }
  }
  const counts = result.rows.reduce((acc, r) => ({ ...acc, [r.status]: (acc[r.status] ?? 0) + 1 }), {});
  console.log(Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(", ") + (result.ok ? " - OK" : " - FAILED"));
  process.exit(result.ok ? 0 : 1);
}
