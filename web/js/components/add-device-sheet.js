/**
 * Settings → "Add a device" sheet (WP PAIR-1).
 *
 * Three ways to set up another device with this hangar's address and the
 * ONE shared API key this browser already holds (user decision 2026-10-04,
 * "Weg A": no API change; per-device keys are the later D9 PAIR-2):
 *
 *  - Phone (Android app): a QR code of the pairing URI
 *    (lib/pair-link.js `buildAppPairUri`, the contract with the app),
 *    generated on this page by lib/qr-encode.js — no third party sees it —
 *    plus the same URI as an "Open on this phone" link for a phone that
 *    already has the app, and a copy button.
 *  - Another browser: `<origin>/#pair=<key>`, consumed by
 *    lib/pair-intake.js on the receiving page.
 *  - Windows PC (vault-agent): a PowerShell install command
 *    (lib/agent-install.js) for the release vault-api reports in
 *    `GET /v1/about`; a development build gets a note instead. The agent's
 *    server address is an editable field, prefilled with this page's origin
 *    and remembered per browser, because the agent must reach vault-api
 *    directly, not through a reverse proxy (lib/agent-install.js header).
 *    The command contains NO key (user decision "Weg A"): it asks for it,
 *    and a separate "Copy key" button next to it (inside the same Show
 *    gate) puts the key on the clipboard without ever showing it.
 *  - Linux/SteamOS: a pointer to agent/README.md.
 *
 * The key is a secret, so every option's content is built only after an
 * explicit "Show" press, next to a warning, and removed from the DOM again
 * on "Hide" and whenever the sheet closes (any path: sheet-dialog.js
 * `onClose`). Nothing here logs the key. In demo mode, or with no key
 * stored, the sheet says there is nothing to share and shows no option.
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
  agentReleaseFromAbout,
  KEY_PROMPT,
  installKey,
  isInstallableKey,
  noReleaseText,
  validateAgentServerUrl,
  windowsInstallSnippet,
} from "../lib/agent-install.js";

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
function buildQrSvg(text) {
  const qr = encodeQrText(text, { ecl: "M" });
  const { side, d } = qrSvgGeometry(qr.modules);
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "pair-qr");
  svg.setAttribute("viewBox", `0 0 ${side} ${side}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "QR code for pairing the SteamHangar app");
  svg.setAttribute("shape-rendering", "crispEdges");
  svg.dataset.role = "pair-qr";
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
  "Set up a phone, another browser or a Windows PC with this hangar's address and API key, without typing them.",
);
const unavailable = el("p", "hint");
unavailable.dataset.role = "pair-unavailable";
unavailable.hidden = true;
const optionsWrap = el("div", "pair-options");
const closeBtn = el("button", "btn wide ghost", "Close");
closeBtn.type = "button";
closeBtn.addEventListener("click", () => dialog.close());

/** One option: heading, description, the warning, a Show/Hide toggle and the
 * (initially empty) reveal area. `build(revealEl)` fills it on Show. */
function buildOption({ id, title, desc, showLabel, extra, build }) {
  const wrap = el("div", "pair-option");
  wrap.dataset.option = id;
  const h = el("h4", "sec", title);
  const p = el("p", "foot-note", desc);
  wrap.append(h, p);
  if (extra) wrap.append(extra);
  const warn = el("p", "pair-warn", SECRET_WARNING);
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
  wrap.append(warn, toggle, reveal);
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
  windows.gen++; // an About answer still in flight paints nothing
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

const windows = {
  gen: 0,
  about: null, // last GET /v1/about answer for this open, or null
};

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
  if (windowsOption.shown) paintWindows(windowsOption.reveal);
});

function paintWindows(reveal) {
  // MUTATION TARGET (guard A): an About answer that lands after "Hide"
  // must not paint the command (with the key) back into a hidden option.
  if (!windowsOption.shown) return;
  const key = getStoredApiKey();
  if (!isUsableKey(key)) return;
  if (windows.about === null) {
    reveal.replaceChildren(el("p", "foot-note", "Reading the server's release version…"));
    return;
  }
  if (windows.about.error) {
    const line = el("p", "errline", `Could not read the server version: ${windows.about.error}`);
    line.dataset.role = "agent-error";
    reveal.replaceChildren(line);
    return;
  }
  const release = agentReleaseFromAbout(windows.about.response);
  if (!release.ok) {
    const note = el("p", "hint pair-left", noReleaseText(release.version));
    note.dataset.role = "agent-no-release";
    reveal.replaceChildren(note);
    return;
  }
  const url = agentUrlCheck();
  if (!url.ok) {
    const note = el("p", "foot-note", "Fix the address above to see the command.");
    note.dataset.role = "agent-url-blocked";
    reveal.replaceChildren(note);
    return;
  }
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
  const snippet = windowsInstallSnippet({ version: release.version, serverUrl: url.url });
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
      `1. Copy the command and paste it into a normal PowerShell window on the PC (not "Run as administrator"). It downloads vault-agent ${release.version} from the project's GitHub release, checks it against the release's SHA256SUMS, installs the scheduled task for this Windows user and starts the first report.`,
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

async function buildWindows(reveal) {
  const gen = ++windows.gen;
  windows.about = null;
  paintWindows(reveal);
  try {
    const response = await api.about();
    // MUTATION TARGET (guard B): an answer for an earlier Show (or an
    // earlier open of the sheet) is dropped; only the latest request paints.
    if (gen !== windows.gen) return;
    windows.about = { response };
  } catch (err) {
    if (gen !== windows.gen) return;
    const detail = err && typeof err.detail === "string" && err.detail ? err.detail : (err && err.message) || "request failed";
    windows.about = { error: err && err.status === 404 ? "this server is older than GET /v1/about" : detail };
  }
  paintWindows(reveal);
}

// ---- Assemble --------------------------------------------------------------

optionsWrap.append(
  buildOption({
    id: "phone",
    title: "Phone (Android app)",
    desc: "A QR code with this hangar's address and API key for the SteamHangar app.",
    showLabel: "Show QR code",
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
  build: (reveal) => {
    buildWindows(reveal);
  },
});
const windowsOption = options[options.length - 1];
const linuxNote = el("p", "foot-note pair-linux");
linuxNote.append("Linux / SteamOS: install vault-agent with its systemd timer as described in ");
const readme = el("a", null, "agent/README.md");
readme.href = AGENT_README_URL;
readme.target = "_blank";
readme.rel = "noopener noreferrer";
linuxNote.append(readme, ".");
optionsWrap.append(windowsWrap, linuxNote);

dialog.body.append(heading, intro, unavailable, optionsWrap, closeBtn);

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
