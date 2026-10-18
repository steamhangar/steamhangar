#!/usr/bin/env node
// Screenshot tool for the web UI (WP WEB-GOLDENS-1).
//
// Drives a Chromium binary over the DevTools protocol with nothing but
// Node's built-in WebSocket and fetch -- no playwright npm dependency. The
// binary is the one Playwright downloads (`npx playwright-core@<pin> install
// chromium`), so CI and the devbox render with the same build.
//
// What makes the pixels reproducible:
//   - demo mode via localStorage, written by a script that runs before the
//     app's own modules on every navigation (no API, no onboarding overlay);
//   - a frozen clock (`Date` shim), fixed timezone and locale, so demo
//     timestamps and "x minutes ago" labels never move;
//   - `prefers-reduced-motion: reduce`, hidden scrollbars, device scale 1;
//   - every request that leaves the served origin fails (Fetch domain), so
//     cover art always shows its fallback tile instead of a CDN download;
//   - one self-contained fontconfig (tools/web-shot-fonts.conf) that maps the
//     stylesheet's families to Liberation Sans/Mono, admits no other font,
//     and renders unhinted with grey antialiasing.
//
// Besides one full-page shot per page and width, the tool shoots CASES
// (WP MOBILE-1b): one page at one width with an overlay opened by a setup
// script (a sheet or a confirm), captured at the viewport size, with touch
// emulation on so `@media (pointer:coarse)` applies (headless Chromium has
// no pointer at all: `(pointer:fine)` and `(pointer:coarse)` are both
// false for the page shots). See CASES below.
//
// Usage (every flag optional; see DEFAULTS and tools/README.md):
//   node tools/web-shot.mjs --base http://127.0.0.1:8787 --out shots \
//     --widths 1400,1000,400 --pages library,clients,downloads,settings,kit \
//     --cases library-detail-400,clients-pair-400   (or --cases none)
// Exit status: 0 when every requested page that the server serves was
// captured at every width; a page the server answers with 404 is skipped
// with a warning (UI-KIT-1 adds /kit in a parallel package); any other
// failure makes the exit status non-zero.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { readdirSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Keep each of the next two on ONE line: ci.yml's web-goldens-push job greps
// them to validate the recorded artifact (api/tests/test_web_goldens_1_ci.py).
export const DEFAULT_PAGES = ["library", "clients", "downloads", "settings", "kit"];
export const DEFAULT_WIDTHS = [1400, 1000, 400];
// The same job validates case shots: `<case>.png` for each name on this line.
export const DEFAULT_CASES = ["library-detail-400", "downloads-delete-400", "downloads-row-400", "clients-pair-400", "clients-pair-shown-400", "settings-savebar-400"];

/**
 * Overlay cases (WP MOBILE-1b). Each: the page to load, the width (the
 * name ends in it), `touch` (CDP touch emulation: `pointer:coarse`), a
 * `setup` script evaluated after the page has rendered, and a `ready`
 * expression polled until it is truthy. Captured at width x viewportHeight
 * (an overlay is fixed to the viewport, a full-page shot would only grow
 * the scrim).
 *
 * clients-pair-400 needs the Add-a-device options, which demo mode hides
 * (nothing to share): its setup stores a stand-in key and turns demo mode
 * off only for the synchronous click, so the sheet opens with its options
 * while every poll stays in demo mode. The sheet's GET /v1/dist is answered
 * from the demo fixture (DIST_FROM_DEMO), so step 1 shows the normal app
 * download. clients-pair-400 never shows the key;
 * clients-pair-shown-400 taps the stand-in (the operator's reveal overlay)
 * and shows the QR of the stand-in key, never a real one.
 */
/** The pairing cases turn demo mode off for one click, so the sheet's
 * GET /v1/dist would reach the static server (404, an error line in the
 * shot). This answers exactly that request from the demo fixture instead. */
const DIST_FROM_DEMO = `{
      const { demoRequest } = await import("/js/demo-data.js");
      const realFetch = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const url = new URL(input instanceof Request ? input.url : String(input), location.href); // api.js passes a URL object
        if (url.pathname === "/v1/dist") {
          return new Response(JSON.stringify(await demoRequest("GET", "/v1/dist")), { status: 200, headers: { "content-type": "application/json" } });
        }
        return realFetch(input, init);
      };
    }`;
/** Ready once step 1 shows the normal app download (never the error). */
const PAIR_READY = `(() => { const s = document.querySelector('.sheet-backdrop.shown .add-device');
  const d = s && s.querySelector('[data-role="app-download"]');
  return !!d && !d.querySelector('[data-role="app-download-error"]') && /Android app ·/.test(d.textContent); })()`;

export const CASES = Object.freeze({
  "library-detail-400": {
    page: "library", width: 400, touch: true,
    setup: `document.querySelector(".kit.view-library .grid .card").click()`,
    ready: `!!document.querySelector(".sheet-backdrop.shown .game-detail .gd-body:not(.gd-state)")`,
  },
  "downloads-delete-400": {
    page: "downloads", width: 400, touch: true,
    setup: `document.querySelector('[data-role="history-delete"]').click()`,
    ready: `!!document.querySelector('.dialog-backdrop.on [data-role="history-delete-confirm"]')`,
  },
  "downloads-row-400": {
    page: "downloads", width: 400, touch: true,
    setup: `(document.querySelector(".hitem .hrow .sic.k-error")?.closest(".hrow") || document.querySelector(".hitem .hrow")).click()`,
    ready: `(() => { const p = document.querySelector('.sheet-backdrop.shown [data-role="history-sheet-panel"]'); return !!p && !p.querySelector(".loading"); })()`,
  },
  "clients-pair-400": {
    page: "clients", width: 400, touch: true,
    setup: `(async () => {
      ${DIST_FROM_DEMO}
      localStorage.setItem("steamvault.apiKey", "web-shot-stand-in-key");
      localStorage.setItem("steamvault.demoMode", "0");
      try { document.querySelector('[data-role="add-device"]').click(); }
      finally { localStorage.setItem("steamvault.demoMode", "1"); }
    })()`,
    ready: PAIR_READY,
  },
  "clients-pair-shown-400": {
    page: "clients", width: 400, touch: true,
    setup: `(async () => {
      ${DIST_FROM_DEMO}
      localStorage.setItem("steamvault.apiKey", "web-shot-stand-in-key");
      localStorage.setItem("steamvault.demoMode", "0");
      try {
        document.querySelector('[data-role="add-device"]').click();
        document.querySelector('[data-role="show-phone-cover"]').click();
      } finally { localStorage.setItem("steamvault.demoMode", "1"); }
      document.querySelector('.add-device [data-role="pair-qr"]')?.scrollIntoView({ block: "center" });
    })()`,
    ready: `${PAIR_READY} && !!document.querySelector('.sheet-backdrop.shown .add-device [data-role="pair-qr"]')`,
  },
  "settings-savebar-400": {
    page: "settings", width: 400, touch: true,
    setup: `document.querySelector('.view-settings .settings-main .seg button[aria-pressed="false"]:not(:disabled)').click()`,
    ready: `!!document.querySelector(".view-settings .savebar:not([hidden])")`,
  },
});

export const DEFAULTS = Object.freeze({
  base: "http://127.0.0.1:8787",
  out: "web-shots",
  widths: DEFAULT_WIDTHS,
  pages: DEFAULT_PAGES,
  cases: DEFAULT_CASES,
  chrome: null, // resolved by findChrome()
  reducedMotion: true,
  hideScrollbars: true,
  blockOffsite: true,
  waitSelector: "#view-root .view",
  timeoutMs: 20000,
  settleMs: 400,
  clock: "2026-10-17T12:00:00.000Z",
  timezone: "UTC",
  locale: "en-US",
  viewportHeight: 900,
  maxHeight: 8000,
  fontsConf: join(dirname(fileURLToPath(import.meta.url)), "web-shot-fonts.conf"),
});

/** localStorage the app must see before its modules run. */
export const DEMO_STORAGE = Object.freeze({
  "steamvault.demoMode": "1", // web/js/api.js: every request answered from demo-data.js
  "steamvault.downloadsRange": "6h", // web/js/lib/traffic-view.js: a fixed range, no live window
  "steamvault.desktopSiteHintDismissed": "1", // web/js/lib/desktop-site-hint.js
});

// The Playwright download: ~/.cache/ms-playwright/chromium-<rev>/chrome-linux64/chrome,
// newest revision first. CHROME_PATH wins when set.
export function findChrome(env = process.env) {
  if (env.CHROME_PATH) return env.CHROME_PATH;
  const cache = join(env.PLAYWRIGHT_BROWSERS_PATH || join(homedir(), ".cache", "ms-playwright"));
  if (!existsSync(cache)) return null;
  const revisions = readdirSync(cache)
    .filter((name) => /^chromium-\d+$/.test(name))
    .sort((a, b) => Number(b.slice(9)) - Number(a.slice(9)));
  for (const rev of revisions) {
    const bin = join(cache, rev, "chrome-linux64", "chrome");
    if (existsSync(bin)) return bin;
  }
  return null;
}

export function parseArgs(argv) {
  const opts = { ...DEFAULTS };
  const bools = { "reduced-motion": "reducedMotion", "hide-scrollbars": "hideScrollbars", "block-offsite": "blockOffsite" };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const take = () => {
      const value = argv[i + 1];
      if (value === undefined || value.startsWith("--")) throw new Error(`${arg} needs a value`);
      i += 1;
      return value;
    };
    const boolMatch = /^--(no-)?([a-z-]+)$/.exec(arg);
    if (boolMatch && bools[boolMatch[2]]) { opts[bools[boolMatch[2]]] = !boolMatch[1]; continue; }
    switch (arg) {
      case "--base": opts.base = take().replace(/\/+$/, ""); break;
      case "--out": opts.out = take(); break;
      case "--chrome": opts.chrome = take(); break;
      case "--widths": opts.widths = take().split(",").map((w) => Number(w.trim())); break;
      case "--pages": opts.pages = take().split(",").map((p) => p.trim()).filter(Boolean); break;
      case "--cases": {
        const v = take().trim();
        opts.cases = v === "none" ? [] : v.split(",").map((c) => c.trim()).filter(Boolean);
        break;
      }
      case "--wait-selector": opts.waitSelector = take(); break;
      case "--timeout": opts.timeoutMs = Number(take()); break;
      case "--settle": opts.settleMs = Number(take()); break;
      case "--clock": opts.clock = take(); break;
      case "--timezone": opts.timezone = take(); break;
      case "--locale": opts.locale = take(); break;
      case "--fonts-conf": opts.fontsConf = take(); break;
      case "--max-height": opts.maxHeight = Number(take()); break;
      case "--help": case "-h": opts.help = true; break;
      default: throw new Error(`unknown argument: ${arg}`);
    }
  }
  if (!opts.widths.length || opts.widths.some((w) => !Number.isInteger(w) || w < 200 || w > 4000)) {
    throw new Error("--widths: comma-separated integers between 200 and 4000");
  }
  if (!opts.pages.length || opts.pages.some((p) => !/^[a-z][a-z0-9-]*$/.test(p))) {
    throw new Error("--pages: comma-separated lower-case names");
  }
  for (const name of opts.cases) {
    if (!CASES[name]) throw new Error(`--cases: unknown case ${name} (known: ${Object.keys(CASES).join(", ")})`);
  }
  if (Number.isNaN(Date.parse(opts.clock))) throw new Error("--clock: ISO-8601 timestamp");
  for (const key of ["timeoutMs", "settleMs", "maxHeight"]) {
    if (!Number.isFinite(opts[key]) || opts[key] < 0) throw new Error(`--${key}: non-negative number`);
  }
  return opts;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Minimal CDP client over one page WebSocket: request/response plus events. */
class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Map();
    ws.onmessage = (e) => {
      const msg = JSON.parse(typeof e.data === "string" ? e.data : Buffer.from(e.data).toString("utf8"));
      if (msg.id !== undefined && this.pending.has(msg.id)) {
        const { resolve: ok, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message} (${msg.error.code})`));
        else ok(msg.result);
      } else if (msg.method && this.listeners.has(msg.method)) {
        for (const fn of this.listeners.get(msg.method)) fn(msg.params);
      }
    };
  }
  send(method, params = {}) {
    return new Promise((ok, reject) => {
      const id = ++this.nextId;
      this.pending.set(id, { resolve: ok, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(method, fn) {
    if (!this.listeners.has(method)) this.listeners.set(method, []);
    this.listeners.get(method).push(fn);
  }
  async evaluate(expression) {
    const { result, exceptionDetails } = await this.send("Runtime.evaluate", {
      expression, awaitPromise: true, returnByValue: true,
    });
    if (exceptionDetails) throw new Error(`page script failed: ${exceptionDetails.text} ${result?.description ?? ""}`);
    return result?.value;
  }
}

/** Script injected before any page script: frozen clock + demo storage. */
export function bootstrapScript(clockIso, storage) {
  const fixed = Date.parse(clockIso);
  return `(() => {
    const FIXED = ${fixed};
    const RealDate = Date;
    class FrozenDate extends RealDate {
      constructor(...args) { super(...(args.length ? args : [FIXED])); }
      static now() { return FIXED; }
    }
    Object.defineProperty(FrozenDate, "name", { value: "Date" });
    window.Date = FrozenDate;
    try {
      const entries = ${JSON.stringify(Object.entries(storage))};
      for (const [k, v] of entries) localStorage.setItem(k, v);
    } catch (e) { /* no storage (about:blank) -- harmless */ }
  })();`;
}

async function launchChrome(opts, width, userDataDir) {
  const args = [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--remote-debugging-port=0", `--user-data-dir=${userDataDir}`,
    `--window-size=${width},${opts.viewportHeight}`, "--force-device-scale-factor=1",
    // Grey antialiasing, glyphs on whole pixels. There is deliberately no
    // --font-render-hinting: build 1243's `chrome` binary does not know the
    // switch (only chrome-headless-shell does; measured 2026-10-17, no
    // pixel changed with it), so hinting is set in web-shot-fonts.conf.
    "--force-color-profile=srgb", "--disable-lcd-text", "--disable-font-subpixel-positioning",
    `--lang=${opts.locale}`, "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--disable-background-networking", "--disable-component-update",
    "--disable-sync", "--disable-translate", "--metrics-recording-only", "--mute-audio",
    "--disable-breakpad", "--disable-crash-reporter", "--no-crashpad",
    "about:blank",
  ];
  if (opts.hideScrollbars) args.unshift("--hide-scrollbars");
  const env = { ...process.env, LANG: `${opts.locale.replace("-", "_")}.UTF-8`, TZ: opts.timezone };
  if (opts.fontsConf) {
    if (!existsSync(opts.fontsConf)) throw new Error(`fontconfig file not found: ${opts.fontsConf}`);
    env.FONTCONFIG_FILE = resolve(opts.fontsConf);
  }
  const child = spawn(opts.chrome, args, { stdio: ["ignore", "ignore", "pipe"], env });
  let stderr = "";
  child.stderr.on("data", (d) => { stderr += d; if (stderr.length > 20000) stderr = stderr.slice(-20000); });
  const portFile = join(userDataDir, "DevToolsActivePort");
  const deadline = Date.now() + 30000;
  while (!existsSync(portFile)) {
    if (child.exitCode !== null) throw new Error(`chrome exited early (${child.exitCode}):\n${stderr}`);
    if (Date.now() > deadline) { child.kill("SIGKILL"); throw new Error(`chrome did not open its DevTools port:\n${stderr}`); }
    await sleep(100);
  }
  const port = Number(readFileSync(portFile, "utf8").split("\n")[0]);
  let targets = [];
  for (let i = 0; i < 50 && !targets.some((t) => t.type === "page"); i += 1) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch { /* retry */ }
    if (!targets.some((t) => t.type === "page")) await sleep(100);
  }
  const page = targets.find((t) => t.type === "page");
  if (!page) { child.kill("SIGKILL"); throw new Error("chrome opened no page target"); }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((ok, reject) => { ws.onopen = ok; ws.onerror = () => reject(new Error("CDP websocket failed")); });
  return { child, cdp: new Cdp(ws), ws, stderrText: () => stderr };
}

async function preparePage(cdp, opts, width, { touch = false } = {}) {
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width, height: opts.viewportHeight, deviceScaleFactor: 1, mobile: width < 700,
  });
  // Touch emulation makes `(pointer:coarse)` and `(hover:none)` match.
  if (touch) await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  await cdp.send("Emulation.setTimezoneOverride", { timezoneId: opts.timezone });
  await cdp.send("Emulation.setLocaleOverride", { locale: opts.locale });
  await cdp.send("Emulation.setEmulatedMedia", {
    features: [
      { name: "prefers-reduced-motion", value: opts.reducedMotion ? "reduce" : "no-preference" },
      { name: "prefers-color-scheme", value: "dark" },
    ],
  });
  if (opts.hideScrollbars) await cdp.send("Emulation.setScrollbarsHidden", { hidden: true });
  await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: bootstrapScript(opts.clock, DEMO_STORAGE) });
  if (opts.blockOffsite) {
    const origin = new URL(opts.base).origin;
    cdp.on("Fetch.requestPaused", ({ requestId, request }) => {
      const allowed = request.url.startsWith(origin + "/") || request.url === origin || request.url.startsWith("data:");
      const reply = allowed
        ? cdp.send("Fetch.continueRequest", { requestId })
        : cdp.send("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" });
      reply.catch(() => { /* the request may already be gone */ });
    });
    await cdp.send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] });
  }
}

async function waitFor(cdp, expression, timeoutMs, what) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await cdp.evaluate(expression)) return;
    if (Date.now() > deadline) throw new Error(`timed out after ${timeoutMs} ms waiting for ${what}`);
    await sleep(100);
  }
}

async function capture(cdp, opts, width, url) {
  await cdp.send("Page.navigate", { url });
  await waitFor(cdp, `document.readyState === "complete"`, opts.timeoutMs, "load");
  await waitFor(cdp, `document.querySelector(${JSON.stringify(opts.waitSelector)}) !== null`, opts.timeoutMs,
    `selector ${opts.waitSelector}`);
  await waitFor(cdp, `document.fonts.status === "loaded"`, opts.timeoutMs, "fonts");
  // Full-page capture: grow the viewport to the document height, then wait
  // until every <img> has loaded or run its fallback chain (the grid's
  // covers are `loading="lazy"`, so they only start once they are inside
  // the viewport -- hence resize first, wait second), and re-measure, since
  // fallback tiles can change the height. Repeats until the height is
  // stable, which in practice is the second pass.
  const measure = () => cdp.evaluate(
    "Math.max(document.documentElement.scrollHeight, document.body.scrollHeight, document.documentElement.clientHeight)",
  );
  const clamp = (h) => Math.min(Math.max(Number(h) || opts.viewportHeight, opts.viewportHeight), opts.maxHeight);
  let height = clamp(await measure());
  for (let pass = 0; pass < 4; pass += 1) {
    await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 700 });
    await waitFor(cdp, `Array.from(document.images).every((i) => i.complete)`, opts.timeoutMs, "images");
    await sleep(opts.settleMs);
    const next = clamp(await measure());
    if (next === height) break;
    height = next;
  }
  await cdp.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))");
  const { data } = await cdp.send("Page.captureScreenshot", { format: "png", fromSurface: true });
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width, height: opts.viewportHeight, deviceScaleFactor: 1, mobile: width < 700,
  });
  return { png: Buffer.from(data, "base64"), height };
}

/** One overlay case: load the page, run its setup, wait until it is ready,
 * capture the viewport (not the full page). */
async function captureCase(cdp, opts, spec, url) {
  await cdp.send("Page.navigate", { url });
  await waitFor(cdp, `document.readyState === "complete"`, opts.timeoutMs, "load");
  await waitFor(cdp, `document.querySelector(${JSON.stringify(opts.waitSelector)}) !== null`, opts.timeoutMs,
    `selector ${opts.waitSelector}`);
  await waitFor(cdp, `document.fonts.status === "loaded"`, opts.timeoutMs, "fonts");
  await sleep(opts.settleMs);
  await cdp.evaluate(spec.setup);
  await waitFor(cdp, spec.ready, opts.timeoutMs, "the case's overlay");
  // Only images on screen: a lazy image below the viewport or in a hidden
  // box (the detail sheet's compact header) never starts loading.
  await waitFor(cdp, `Array.from(document.images).every((i) => i.complete || !i.getClientRects().length
    || i.getBoundingClientRect().top >= innerHeight)`, opts.timeoutMs, "images");
  await sleep(opts.settleMs);
  await cdp.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))");
  const { data } = await cdp.send("Page.captureScreenshot", { format: "png", fromSurface: true });
  return { png: Buffer.from(data, "base64"), height: opts.viewportHeight };
}

/** Stop a launched browser and remove its profile directory. */
async function stopChrome(chrome, userDataDir, log) {
  if (chrome) {
    try { chrome.ws.close(); } catch { /* already closed */ }
    const exited = new Promise((r) => chrome.child.once("exit", r));
    chrome.child.kill("SIGTERM");
    if (await Promise.race([exited.then(() => true), sleep(5000).then(() => false)]) === false) {
      chrome.child.kill("SIGKILL");
      await exited;
    }
  }
  // Chrome's helper processes can still be flushing the profile for a
  // moment after the main process exits; retry briefly, then warn.
  for (let attempt = 0; existsSync(userDataDir); attempt += 1) {
    try {
      rmSync(userDataDir, { recursive: true, force: true });
    } catch (err) {
      if (attempt >= 20) { log.warn(`warning: could not remove ${userDataDir}: ${err.message}`); break; }
      await sleep(250);
    }
  }
}

/** HTTP status the server gives a page path; a 404 means "skip this page". */
async function probe(base, page) {
  const res = await fetch(`${base}/${page}`, { method: "GET", redirect: "manual" });
  return res.status;
}

export async function run(opts, log = console) {
  if (!opts.chrome) opts.chrome = findChrome();
  if (!opts.chrome || !existsSync(opts.chrome)) {
    throw new Error("no Chromium binary: pass --chrome <path>, set CHROME_PATH, or `npx playwright-core@<pin> install chromium`");
  }
  mkdirSync(opts.out, { recursive: true });

  const served = [];
  const skipped = [];
  for (const page of opts.pages) {
    const status = await probe(opts.base, page);
    if (status === 404) skipped.push(page);
    else if (status === 200) served.push(page);
    else throw new Error(`${opts.base}/${page} answered ${status}`);
  }
  for (const page of skipped) log.warn(`warning: ${opts.base}/${page} is not served (404) - skipped`);
  if (served.length === 0) throw new Error("no page to capture");

  const failures = [];
  const written = [];
  for (const width of opts.widths) {
    const userDataDir = mkdtempSync(join(tmpdir(), "web-shot-"));
    let chrome;
    try {
      chrome = await launchChrome(opts, width, userDataDir);
      await preparePage(chrome.cdp, opts, width);
      for (const page of served) {
        const name = `${page}-${width}.png`;
        try {
          const { png, height } = await capture(chrome.cdp, opts, width, `${opts.base}/${page}`);
          writeFileSync(join(opts.out, name), png);
          written.push(name);
          log.log(`${name}  ${width}x${height}`);
        } catch (err) {
          failures.push(`${name}: ${err.message}`);
          log.error(`error: ${name}: ${err.message}`);
        }
      }
    } catch (err) {
      failures.push(`width ${width}: ${err.message}`);
      log.error(`error: width ${width}: ${err.message}`);
    } finally {
      await stopChrome(chrome, userDataDir, log);
    }
  }
  // Overlay cases: one browser per case (touch emulation is per target and
  // must be on before the page loads).
  for (const name of opts.cases) {
    const spec = CASES[name];
    if (!served.includes(spec.page)) {
      if (!skipped.includes(spec.page)) log.warn(`warning: case ${name} skipped (page ${spec.page} not requested)`);
      continue;
    }
    const userDataDir = mkdtempSync(join(tmpdir(), "web-shot-"));
    let chrome;
    try {
      chrome = await launchChrome(opts, spec.width, userDataDir);
      await preparePage(chrome.cdp, opts, spec.width, { touch: spec.touch });
      const { png, height } = await captureCase(chrome.cdp, opts, spec, `${opts.base}/${spec.page}`);
      writeFileSync(join(opts.out, `${name}.png`), png);
      written.push(`${name}.png`);
      log.log(`${name}.png  ${spec.width}x${height}`);
    } catch (err) {
      failures.push(`${name}.png: ${err.message}`);
      log.error(`error: ${name}.png: ${err.message}`);
    } finally {
      await stopChrome(chrome, userDataDir, log);
    }
  }
  return { written, skipped, failures };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (err) {
    console.error(`web-shot: ${err.message}`);
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
  run(opts).then(({ written, skipped, failures }) => {
    console.log(`${written.length} screenshot(s) written to ${opts.out}` +
      (skipped.length ? `, skipped: ${skipped.join(", ")}` : "") +
      (failures.length ? `, ${failures.length} failure(s)` : ""));
    process.exit(failures.length ? 1 : 0);
  }, (err) => {
    console.error(`web-shot: ${err.message}`);
    process.exit(1);
  });
}
