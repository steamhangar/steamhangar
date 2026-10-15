/**
 * Settings → "Add a device" sheet (WP PAIR-1).
 *
 * Three ways to set up another device with this hangar's address and the
 * ONE shared API key this browser already holds (user decision 2026-10-04,
 * "Weg A": no API change; per-device keys are the later D9 PAIR-2):
 *
 *  - Phone (Android app): first the app itself (WP DIST-1b): a "Download
 *    app" button and a QR code of the APK's address, from this vault when
 *    `GET /v1/dist` says the APK is baked in (page origin +
 *    `/dist/steamhangar-app.apk`), else the GitHub Release asset of the
 *    same version, else a note (lib/dist-downloads.js). Not a secret, so it
 *    is shown without the Show gate. Then a QR code of the pairing URI
 *    (lib/pair-link.js `buildAppPairUri`, the contract with the app),
 *    generated on this page by lib/qr-encode.js — no third party sees it —
 *    plus the same URI as an "Open on this phone" link for a phone that
 *    already has the app, and a copy button.
 *  - Another browser: `<origin>/#pair=<key>`, consumed by
 *    lib/pair-intake.js on the receiving page.
 *  - Windows PC (vault-agent): a PowerShell install command
 *    (lib/agent-install.js) for the release `GET /v1/dist` reports; it
 *    downloads from this vault when the files are baked in, else from the
 *    GitHub Release of the same version (WP DIST-1b); a development build
 *    gets a note instead. The agent's
 *    server address is an editable field, prefilled with this page's origin
 *    and remembered per browser, because the agent must reach vault-api
 *    directly, not through a reverse proxy (lib/agent-install.js header).
 *    The command contains NO key (user decision "Weg A"): it asks for it,
 *    and a separate "Copy key" button next to it (inside the same Show
 *    gate) puts the key on the clipboard without ever showing it.
 *  - Linux/SteamOS: a download-and-check shell command from the same
 *    source (no key, no installer: agent/README.md does the install).
 *
 * `GET /v1/dist` is read once per open of the sheet; a late answer for an
 * earlier open is dropped (generation token).
 *
 * The key is a secret, so every option's content is built only after an
 * explicit "Show" press, next to a warning, and removed from the DOM again
 * on "Hide" and whenever the sheet closes (any path: sheet-dialog.js
 * `onClose`). Nothing here logs the key. In demo mode, or with no key
 * stored, the sheet says there is nothing to share and shows no option;
 * demo mode still shows the app download (from the demo's fake index).
 *
 * Same module-singleton shape as clients-sheet.js; DOM wiring pinned with
 * fake-dom in web/tests/add-device-wiring.test.js.
 */

import { api, getStoredApiKey, isDemoMode } from "../api.js";
import { onViewChange } from "../router.js";
import { showToast } from "./toast.js";
import { createSheetDialog } from "./sheet-dialog.js";
import { encodeQrText, qrSvgGeometry } from "../lib/qr-encode.js";
import {
  APP_KEY_UNSUPPORTED_TEXT,
  apiBaseUrl,
  buildAppPairUri,
  buildBrowserPairLink,
  isAppPairableKey,
  isUsableKey,
} from "../lib/pair-link.js";
import {
  AGENT_README_URL,
  AGENT_SERVER_URL_NOTE,
  AGENT_SERVER_URL_STORAGE_KEY,
  KEY_PROMPT,
  installKey,
  isInstallableKey,
  linuxDownloadSnippet,
  noReleaseText,
  validateAgentServerUrl,
  windowsInstallSnippet,
} from "../lib/agent-install.js";
import {
  RELEASES_PAGE_URL,
  agentDownloadPlan,
  appDownloadPlan,
  formatDownloadSize,
  linuxArtifactNames,
  noAppText,
  windowsArtifactNames,
} from "../lib/dist-downloads.js";

export const SECRET_WARNING =
  "Anyone who sees this can control your hangar. Only show it on your own screen; copied text can also end up in clipboard history or cloud clipboard sync.";

const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function readAgentUrl() {
  try {
    return window.localStorage.getItem(AGENT_SERVER_URL_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeAgentUrl(value) {
  try {
    window.localStorage.setItem(AGENT_SERVER_URL_STORAGE_KEY, value);
  } catch {
    // a blocked storage only loses the convenience
  }
}

/** Copy `text`; falls back to selecting `field` + execCommand("copy") where
 * the async clipboard API is missing (it needs a secure context, and a LAN
 * hangar is often plain http). Without a `field` (the "Copy key" button:
 * the key is never shown) the fallback uses a temporary off-screen
 * textarea that is removed again in every case. */
async function copyText(text, field) {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the selection fallback
  }
  let temp = null;
  try {
    let target = field;
    if (!target) {
      temp = document.createElement("textarea");
      temp.className = "pair-offscreen";
      temp.setAttribute("aria-hidden", "true");
      temp.readOnly = true;
      temp.value = text;
      document.body.appendChild(temp);
      target = temp;
    }
    target.focus();
    target.select();
    return typeof document.execCommand === "function" && document.execCommand("copy") === true;
  } catch {
    return false;
  } finally {
    // MUTATION TARGET: the key must not stay in the DOM after a copy.
    if (temp) temp.remove();
  }
}

function copyButton(label, getText, field) {
  const btn = el("button", "btn ghost sm", label);
  btn.type = "button";
  btn.addEventListener("click", async () => {
    const ok = await copyText(getText(), field);
    if (ok) showToast("Copied.");
    else showToast("Could not copy. Select the text and copy it by hand.", { warn: true, duration: 5000 });
  });
  return btn;
}

function readonlyField(value, { multiline = false, label }) {
  const field = document.createElement(multiline ? "textarea" : "input");
  field.className = multiline ? "inp txt pair-snippet" : "inp txt pair-value";
  if (!multiline) field.type = "text";
  field.readOnly = true;
  field.spellcheck = false;
  field.setAttribute("aria-label", label);
  if (multiline) field.rows = 12;
  field.value = value;
  return field;
}

/** The QR code as an inline SVG: dark modules on a white plate including
 * the quiet zone, in every theme (scanners want dark-on-light). */
function buildQrSvg(text, { label = "QR code for pairing the SteamHangar app", role = "pair-qr" } = {}) {
  const qr = encodeQrText(text, { ecl: "M" });
  const { side, d } = qrSvgGeometry(qr.modules);
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "pair-qr");
  svg.setAttribute("viewBox", `0 0 ${side} ${side}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  svg.setAttribute("shape-rendering", "crispEdges");
  svg.dataset.role = role;
  svg.dataset.qrVersion = String(qr.version);
  const bg = document.createElementNS(SVG_NS, "rect");
  bg.setAttribute("width", String(side));
  bg.setAttribute("height", String(side));
  bg.setAttribute("fill", "#ffffff");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "#000000");
  svg.append(bg, path);
  return svg;
}

// ---------------------------------------------------------------------
// Sheet scaffold
// ---------------------------------------------------------------------

const dialog = createSheetDialog({ ariaLabel: "Add a device", variant: "drawer", onClose: hideAll });

const heading = el("h2", null, "Add a device");
const intro = el(
  "p",
  "hint pair-intro",
  "Set up a phone, another browser or a PC with this hangar's address and API key, without typing them.",
);
const unavailable = el("p", "hint");
unavailable.dataset.role = "pair-unavailable";
unavailable.hidden = true;
const optionsWrap = el("div", "pair-options");
// Demo mode (and only there): the phone's app download on its own, since
// the options themselves are hidden (nothing to share).
const demoPhone = el("div", "pair-option");
demoPhone.dataset.role = "demo-app-download";
demoPhone.append(el("h4", "sec", "Phone (Android app)"));
demoPhone.hidden = true;
const closeBtn = el("button", "btn wide ghost", "Close");
closeBtn.type = "button";
closeBtn.addEventListener("click", () => dialog.close());

/** One option: heading, description, the warning, a Show/Hide toggle and the
 * (initially empty) reveal area. `build(revealEl)` fills it on Show. */
function buildOption({ id, title, desc, showLabel, extra, build, secret = true }) {
  const wrap = el("div", "pair-option");
  wrap.dataset.option = id;
  const h = el("h4", "sec", title);
  const p = el("p", "foot-note", desc);
  wrap.append(h, p);
  if (extra) wrap.append(extra);
  const warn = secret ? el("p", "pair-warn", SECRET_WARNING) : null;
  const reveal = el("div", "pair-reveal");
  reveal.id = `pair-reveal-${id}`;
  reveal.dataset.role = `reveal-${id}`;
  reveal.hidden = true;
  const toggle = el("button", "btn ghost sm", showLabel);
  toggle.type = "button";
  toggle.dataset.role = `show-${id}`;
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", reveal.id);
  const option = { id, toggle, reveal, showLabel, build, shown: false };
  toggle.addEventListener("click", () => {
    if (option.shown) hideOption(option);
    else showOption(option);
  });
  if (warn) wrap.append(warn);
  wrap.append(toggle, reveal);
  options.push(option);
  return wrap;
}

const options = [];

function showOption(option) {
  const key = getStoredApiKey();
  if (isDemoMode() || !isUsableKey(key)) return; // the sheet shows the note instead
  option.shown = true;
  option.reveal.replaceChildren();
  option.build(option.reveal, key);
  option.reveal.hidden = false;
  option.toggle.textContent = "Hide";
  option.toggle.setAttribute("aria-expanded", "true");
}

function hideOption(option) {
  option.shown = false;
  // MUTATION TARGET: the secret leaves the DOM, not only the screen.
  option.reveal.replaceChildren();
  option.reveal.hidden = true;
  option.toggle.textContent = option.showLabel;
  option.toggle.setAttribute("aria-expanded", "false");
}

function hideAll() {
  for (const option of options) hideOption(option);
  // MUTATION TARGET: an index answer still in flight when the sheet closes
  // paints nothing (not even the app download) into the closed sheet.
  dist.gen++;
}

// ---- The download index (GET /v1/dist) -------------------------------------

const dist = {
  gen: 0,
  state: null, // null while loading, {response} or {error}, for this open
};

async function loadDist() {
  const gen = ++dist.gen;
  dist.state = null;
  paintDistConsumers();
  try {
    const response = await api.dist();
    // MUTATION TARGET (guard B): an answer for an earlier open of the sheet
    // is dropped; only the latest request paints.
    if (gen !== dist.gen) return;
    dist.state = { response };
  } catch (err) {
    if (gen !== dist.gen) return;
    const detail = err && typeof err.detail === "string" && err.detail ? err.detail : (err && err.message) || "request failed";
    dist.state = { error: err && err.status === 404 ? "this server is older than GET /v1/dist" : detail };
  }
  paintDistConsumers();
}

// Callers do NOT check `shown`: the one guard is inside paintWindows /
// paintLinux (review S1, one layer, one pin).
function paintDistConsumers() {
  paintAppDownload();
  paintWindows(windowsOption.reveal);
  paintLinux(linuxOption.reveal);
}

// ---- Phone: the app itself (not a secret) -----------------------------------

const appDownload = el("div", "pair-download");
appDownload.dataset.role = "app-download";

function paintAppDownload() {
  if (dist.state === null) {
    appDownload.replaceChildren(el("p", "foot-note", "Looking up the app download…"));
    return;
  }
  if (dist.state.error) {
    const line = el("p", "errline", `Could not read the vault's download list: ${dist.state.error}`);
    line.dataset.role = "app-download-error";
    appDownload.replaceChildren(line);
    return;
  }
  const plan = appDownloadPlan(dist.state.response, window.location.origin);
  if (plan.kind !== "download") {
    const note = el("p", "hint pair-left", noAppText(plan.version) + " ");
    note.dataset.role = "app-download-none";
    const link = el("a", null, "Releases");
    link.href = RELEASES_PAGE_URL;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    note.append(link);
    appDownload.replaceChildren(note);
    return;
  }
  const where = plan.source === "local" ? "from this vault" : "from the project's GitHub release (this vault has no copy)";
  const size = formatDownloadSize(plan.size);
  const acts = el("div", "pair-acts");
  if (isDemoMode()) {
    // Review N3: the demo's index is fake, so no link that leads nowhere.
    const note = el("p", "foot-note", `Demo mode: on a connected vault a "Download app" button here downloads ${plan.name} ${where}.`);
    note.dataset.role = "app-download-demo";
    acts.append(note);
  } else {
    const btn = el("a", "btn primary sm", "Download app");
    btn.href = plan.url;
    btn.dataset.role = "app-download-link";
    btn.dataset.source = plan.source;
    btn.title = `${plan.name}${size ? `, ${size}` : ""}, ${where}.`;
    acts.append(btn);
  }
  // DIST-1b review nit: the demo's code points into a fake index, so the
  // demo caption must not invite a scan.
  const caption = el(
    "p",
    "foot-note",
    isDemoMode()
      ? "Demo: the code shows where a connected vault offers the app."
      : "Scan the code with the phone's camera to download and install the app.",
  );
  caption.dataset.role = "app-download-caption";
  if (!isDemoMode()) caption.title = `${plan.url}\nAndroid asks once to allow installs from the browser. ${plan.name} ${where}.`;
  appDownload.replaceChildren(
    caption,
    buildQrSvg(plan.url, { label: "QR code to download the SteamHangar app", role: "app-download-qr" }),
    acts,
  );
}

// ---- Phone ---------------------------------------------------------------

function buildPhone(reveal, key) {
  // Review finding 8: mirror the app's key rule; a code the app refuses is
  // worse than a plain note.
  if (!isAppPairableKey(key)) {
    const note = el("p", "hint pair-left", APP_KEY_UNSUPPORTED_TEXT);
    note.dataset.role = "pair-app-unsupported";
    reveal.append(note);
    return;
  }
  const uri = buildAppPairUri(apiBaseUrl(window.location), key);
  reveal.append(buildQrSvg(uri));
  const link = el("a", "pair-open", "Open on this phone");
  link.href = uri;
  link.dataset.role = "pair-app-link";
  const field = readonlyField(uri, { label: "Pairing link for the SteamHangar app" });
  field.dataset.role = "pair-app-uri";
  const acts = el("div", "pair-acts");
  acts.append(link, copyButton("Copy link", () => uri, field));
  reveal.append(
    el("p", "foot-note", "Scan it with the phone, or open the link on a phone that already has the SteamHangar app."),
    acts,
    field,
  );
}

// ---- Browser -------------------------------------------------------------

function buildBrowser(reveal, key) {
  const link = buildBrowserPairLink(window.location.origin, key);
  const field = readonlyField(link, { label: "Pairing link for another browser" });
  field.dataset.role = "pair-browser-link";
  const acts = el("div", "pair-acts");
  acts.append(copyButton("Copy link", () => link, field));
  reveal.append(
    field,
    acts,
    el(
      "p",
      "foot-note",
      "Open it in the other browser. The key sits after the #, which browsers never send to a server. The page removes it from the address bar and from this tab's history at once and asks before replacing a different key, but the browser's own history (and its sync) still holds the link: delete it there afterwards, and from wherever you sent it.",
    ),
  );
}

// ---- Windows -------------------------------------------------------------

const agentUrlField = el("div", "field pair-agent-url");
const agentUrlLabel = el("label", null, "vault-api address for the agent");
agentUrlLabel.htmlFor = "pair-agent-url";
const agentUrlInput = document.createElement("input");
agentUrlInput.id = "pair-agent-url";
agentUrlInput.className = "inp txt";
agentUrlInput.type = "url";
agentUrlInput.autocomplete = "off";
agentUrlInput.spellcheck = false;
agentUrlInput.dataset.role = "agent-url";
agentUrlInput.setAttribute("aria-describedby", "pair-agent-url-note pair-agent-url-error");
const agentUrlNote = el("p", "foot-note", AGENT_SERVER_URL_NOTE);
agentUrlNote.id = "pair-agent-url-note";
const agentUrlError = el("p", "errline");
agentUrlError.id = "pair-agent-url-error";
agentUrlError.dataset.role = "agent-url-error";
agentUrlError.hidden = true;
agentUrlField.append(agentUrlLabel, agentUrlInput, agentUrlNote, agentUrlError);

function agentUrlCheck() {
  const result = validateAgentServerUrl(agentUrlInput.value);
  agentUrlError.textContent = result.ok ? "" : result.message;
  agentUrlError.hidden = result.ok;
  if (result.ok) agentUrlInput.removeAttribute("aria-invalid");
  else agentUrlInput.setAttribute("aria-invalid", "true");
  return result;
}

agentUrlInput.addEventListener("input", () => {
  const result = agentUrlCheck();
  if (result.ok) writeAgentUrl(result.url);
  paintWindows(windowsOption.reveal);
  paintLinux(linuxOption.reveal);
});

function paintWindows(reveal) {
  // MUTATION TARGET (guard A): an index answer that lands after "Hide"
  // must not paint the command (with the key) back into a hidden option.
  if (!windowsOption.shown) return;
  const key = getStoredApiKey();
  if (!isUsableKey(key)) return;
  const pre = agentPrecheck(reveal, windowsArtifactNames);
  if (!pre) return;
  const { plan, url } = pre;
  if (!isInstallableKey(key)) {
    const note = el(
      "p",
      "hint pair-left",
      "This hangar's API key has characters the install command does not accept (printable ASCII only). Install the agent by hand as agent/README.md describes.",
    );
    note.dataset.role = "agent-key-unsupported";
    reveal.replaceChildren(note);
    return;
  }
  // User decision "Weg A" (review finding 1): the command carries NO key;
  // it asks for it, and the key travels only through "Copy key".
  const snippet = windowsInstallSnippet({ version: plan.version, serverUrl: url.url, downloadBase: plan.base });
  const field = readonlyField(snippet, { multiline: true, label: "PowerShell install command" });
  field.dataset.role = "agent-snippet";
  const acts = el("div", "pair-acts");
  const copyKey = copyButton("Copy key", () => installKey(getStoredApiKey()), null);
  copyKey.dataset.role = "agent-copy-key";
  acts.append(copyButton("Copy command", () => snippet, field), copyKey);
  reveal.replaceChildren(
    el(
      "p",
      "foot-note",
      `1. Copy the command and paste it into a normal PowerShell window on the PC (not "Run as administrator"). It downloads vault-agent ${plan.version} ${sourceWords(plan)}, checks it against the release's SHA256SUMS, installs the scheduled task for this Windows user and starts the first report.`,
    ),
    el(
      "p",
      "foot-note",
      `2. Paste the whole command at once; it runs only after the last line is in (press Enter once more if nothing happens). When it then asks "${KEY_PROMPT}", press Copy key here and paste into the window (it shows only asterisks). The key is never part of the command, so it is not in PowerShell's history, a transcript or a script-block log; the copy stays in the clipboard (and clipboard history or cloud clipboard sync, where on) until you copy something else.`,
    ),
    field,
    acts,
  );
}

/** Shared by the Windows and Linux commands: the index state, the release
 * and download source, and the agent address. Paints the reason and returns
 * null when there is no command to show. */
function agentPrecheck(reveal, namesFor) {
  if (dist.state === null) {
    reveal.replaceChildren(el("p", "foot-note", "Reading the vault's download list…"));
    return null;
  }
  if (dist.state.error) {
    const line = el("p", "errline", `Could not read the vault's download list: ${dist.state.error}`);
    line.dataset.role = "agent-error";
    reveal.replaceChildren(line);
    return null;
  }
  const url = agentUrlCheck();
  // The URL only matters for a local download, but the Windows command
  // always needs it; a bad address blocks both commands alike.
  if (!url.ok) {
    const note = el("p", "foot-note", "Fix the agent address above to see the command.");
    note.dataset.role = "agent-url-blocked";
    reveal.replaceChildren(note);
    return null;
  }
  const plan = agentDownloadPlan(dist.state.response, url.url, namesFor);
  if (!plan.ok) {
    const note = el("p", "hint pair-left", noReleaseText(plan.version));
    note.dataset.role = "agent-no-release";
    reveal.replaceChildren(note);
    return null;
  }
  return { plan, url };
}

function sourceWords(plan) {
  return plan.source === "local" ? `from this vault (${plan.base})` : "from the project's GitHub release";
}

// ---- Linux ---------------------------------------------------------------

function paintLinux(reveal) {
  // MUTATION TARGET (guard A, Linux): nothing is painted into a hidden option.
  if (!linuxOption.shown) return;
  if (!isUsableKey(getStoredApiKey())) return;
  const pre = agentPrecheck(reveal, linuxArtifactNames);
  if (!pre) return;
  const { plan } = pre;
  const snippet = linuxDownloadSnippet({ version: plan.version, downloadBase: plan.base });
  const field = readonlyField(snippet, { multiline: true, label: "Shell download command" });
  field.dataset.role = "linux-snippet";
  field.title = "Fetches the binary for this machine (amd64 or arm64) and the systemd units, checks them against SHA256SUMS, installs nothing.";
  const acts = el("div", "pair-acts");
  const readme = el("a", "pair-open", "Install steps (agent/README.md)");
  readme.href = AGENT_README_URL;
  readme.target = "_blank";
  readme.rel = "noopener noreferrer";
  acts.append(copyButton("Copy command", () => snippet, field), readme);
  reveal.replaceChildren(
    el("p", "foot-note", `Paste it into a terminal on the PC. It downloads vault-agent ${plan.version} ${sourceWords(plan)} and checks it; the install itself follows agent/README.md.`),
    field,
    acts,
  );
}

// ---- Assemble --------------------------------------------------------------

const phoneSlot = el("div");
optionsWrap.append(
  buildOption({
    id: "phone",
    title: "Phone (Android app)",
    desc: "Install the SteamHangar app, then pair it with this hangar.",
    showLabel: "Show pairing QR code",
    extra: phoneSlot,
    build: buildPhone,
  }),
  buildOption({
    id: "browser",
    title: "Another browser",
    desc: "A link that connects another browser to this hangar.",
    showLabel: "Show link",
    build: buildBrowser,
  }),
);
const windowsWrap = buildOption({
  id: "windows",
  title: "Windows PC (vault-agent)",
  desc: "A PowerShell command that installs vault-agent, which reports the PC's installed games to the hangar.",
  showLabel: "Show command",
  extra: agentUrlField,
  build: (reveal) => paintWindows(reveal),
});
const windowsOption = options[options.length - 1];
const linuxWrap = buildOption({
  id: "linux",
  title: "Linux / SteamOS PC (vault-agent)",
  desc: "A shell command that downloads and checks vault-agent from the agent address above; the systemd install follows agent/README.md.",
  showLabel: "Show command",
  secret: false,
  build: (reveal) => paintLinux(reveal),
});
const linuxOption = options[options.length - 1];
optionsWrap.append(windowsWrap, linuxWrap);

dialog.body.append(heading, intro, unavailable, demoPhone, optionsWrap, closeBtn);

/** Open the sheet with every option hidden. */
export function openAddDeviceSheet() {
  hideAll();
  const demo = isDemoMode();
  const hasKey = isUsableKey(getStoredApiKey());
  if (demo || !hasKey) {
    unavailable.textContent = demo
      ? "Demo mode: there is no hangar key to share. Connect to a vault first (Settings → Connection)."
      : "This browser has no API key stored, so there is nothing to share. Connect to a vault first (Settings → Connection).";
    unavailable.hidden = false;
    optionsWrap.hidden = true;
  } else {
    unavailable.hidden = true;
    optionsWrap.hidden = false;
  }
  // The app download needs the index (behind the key): demo mode answers it
  // from its fixtures, a browser without a key gets nothing to show.
  demoPhone.hidden = !demo;
  if (demo) demoPhone.append(appDownload);
  else phoneSlot.append(appDownload);
  if (demo || hasKey) loadDist();
  else appDownload.replaceChildren();
  const stored = readAgentUrl();
  agentUrlInput.value = stored && validateAgentServerUrl(stored).ok ? stored : apiBaseUrl(window.location);
  agentUrlCheck();
  dialog.open();
}

/** For tests and callers that need to know. */
export function isAddDeviceSheetOpen() {
  return dialog.isOpen();
}

// Navigation closes transient surfaces (same rule as clients-sheet.js).
onViewChange(() => dialog.close());
