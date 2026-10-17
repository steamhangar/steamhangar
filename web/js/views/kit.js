/**
 * UI kit page (WP UI-KIT-1, ADR-0028) — the hidden `/kit` route.
 *
 * Renders every primitive of css/kit.css with the demo data of
 * docs/design/rc20/kit.html, from the REAL stylesheets: once at full width
 * and once more inside a 400px inline-size container (`.kit-frame`), so a
 * reviewer or the screenshot goldens (WP WEB-GOLDENS-1) can see how the
 * kit behaves at card width without resizing the window. No store, no
 * API, no polling: the page is static by design and reachable by URL only
 * (router.js, no nav button).
 *
 * Built with createElement like the other views — vault-api's CSP has no
 * 'unsafe-inline', so there is no innerHTML and no style attribute here;
 * the meter widths go through the CSSOM.
 */
import { createPill } from "../components/pill.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(className, text, pressed) {
  const b = el("button", className, text);
  b.type = "button";
  if (pressed !== undefined) b.setAttribute("aria-pressed", pressed ? "true" : "false");
  return b;
}

function section(title, ...children) {
  const s = el("section", "kit-section");
  s.appendChild(el("h2", null, title));
  s.append(...children);
  return s;
}

function row(...children) {
  const r = el("div", "kit-row");
  r.append(...children);
  return r;
}

/** The scale, one line per step (kit.html "Scale"). */
function scaleBlock() {
  const steps = [
    ["--fs-1", "Captions, mono meta, chips. Never below this."],
    ["--fs-2", "Hints, pills, secondary lines"],
    ["--fs-3", "Body text, labels, buttons"],
    ["--fs-4", "Card titles, row names"],
    ["--fs-5", "Section titles, card stats"],
    ["--fs-6", "Page title"],
    ["--fs-7", "Hero number"],
  ];
  const list = el("div", "kit-list");
  for (const [token, role] of steps) {
    const line = row(el("code", null, token));
    const sample = el("span", null, role);
    sample.style.setProperty("font-size", `var(${token})`);
    line.appendChild(sample);
    list.appendChild(line);
  }
  return list;
}

function pageHead() {
  const head = el("div", "page-head");
  const left = el("div");
  left.appendChild(el("h1", null, "Page title"));
  left.appendChild(el("div", "sub", "Subline in --dim, 13 px · counts and period"));
  head.append(left, button("btn pri", "+ Primary action"));
  return head;
}

function tile(label, value, unit, extra) {
  const t = el("div", "tile");
  t.appendChild(el("div", "k", label));
  const v = el("div", "v", value);
  v.appendChild(el("small", null, unit));
  t.appendChild(v);
  if (extra) t.appendChild(extra);
  return t;
}

function meter(percent, accent) {
  const m = el("div", accent ? "meter acc" : "meter");
  const bar = el("i");
  bar.style.setProperty("width", `${percent}%`);
  m.appendChild(bar);
  return m;
}

function tiles() {
  const grid = el("div", "tiles");
  grid.appendChild(tile("Connected", "2", "of 2", el("div", "n", "1 loading now")));
  grid.appendChild(tile("Loaded", "529", "GB", el("div", "n", "in 7 days")));
  grid.appendChild(tile("Hit rate", "77", "%", meter(77)));
  const agents = tile("Agents", "1", "online", null);
  agents.classList.add("hi");
  const note = el("div", "n");
  note.appendChild(createPill({ kind: "warn", text: "1 outdated", tone: "stale" }));
  agents.appendChild(note);
  grid.appendChild(agents);
  return grid;
}

function seg(labels, pressedIndex) {
  const s = el("div", "seg");
  labels.forEach((label, i) => s.appendChild(button(null, label, i === pressedIndex)));
  return s;
}

function sectionHead(label, right) {
  const h = el("div", "sechd");
  h.appendChild(el("span", "sec", label));
  if (right) h.appendChild(right);
  return h;
}

function buttons() {
  return row(
    button("btn pri", "Primary"),
    button("btn", "Default"),
    button("btn ghost", "Ghost"),
    button("btn sm", "Small"),
    button("btn sm danger", "Danger"),
    button("btn sm w72", "Update"),
    button("btn sm w72", "Verify"),
  );
}

/** kit.html "Status vocabulary": every status word the app uses. */
function pills() {
  const items = [
    ["cached", "Online", "ok"],
    ["cached", "Current", "ok"],
    ["running", "Downloading", "run"],
    ["updating", "Updating", "run"],
    ["verify", "Verifying", "run"],
    ["stale", "Update ready", "acc"],
    ["warn", "Agent outdated", "stale"],
    ["none", "Not cached", "none"],
    ["partial", "Partially cached", "none"],
    ["unverified", "Not verified", "none"],
    ["paused", "Paused", null],
    ["cancelled", "Cancelled", null],
    ["error", "Failed", "danger"],
    ["notinuse", "Idle", null],
    ["notinuse", "Offline", null],
  ];
  return row(...items.map(([kind, text, tone]) => createPill({ kind, text, tone })));
}

function chipWithCount(className, label, count, pressed) {
  const c = pressed === undefined ? el("span", className, label) : button(className, label, pressed);
  c.appendChild(el("b", null, count));
  return c;
}

function chips() {
  return row(
    chipWithCount("fchip", "Cached", "22", true),
    chipWithCount("fchip", "Not cached", "417", false),
    chipWithCount("fchip", "Updates", "1", false),
    chipWithCount("gchip", "Satisfactory", "24.8 GB"),
    el("span", "gchip more", "+2 more"),
  );
}

function field(label, chipClass, chipText, value, { num = false, disabled = false, hint } = {}) {
  const f = el("div", "field");
  const lh = el("div", "lh");
  const lab = el("label", null, label);
  const info = button("ib", "i");
  info.setAttribute("aria-label", "Info");
  lh.append(lab, info, el("span", chipClass, chipText));
  f.appendChild(lh);
  const input = el("input", num ? "in num" : "in");
  input.type = "text";
  input.value = value;
  input.setAttribute("value", value);
  if (disabled) input.disabled = true;
  f.appendChild(input);
  if (hint) f.appendChild(el("div", "hint", hint));
  return f;
}

function fieldCard() {
  const card = el("div", "fcard");
  card.appendChild(el("h3", null, "Field card · group title"));
  const grid = el("div", "fg");
  grid.append(
    field("Vault name", "chip ch", "Changed here", "SteamVAULT"),
    field("Rate limit", "chip", "Default", "16 MB/s", { num: true, hint: "Takes effect within a minute." }),
    field("Cache root", "chip env", "From env", "/vault/cache", { disabled: true }),
  );
  card.appendChild(grid);
  return card;
}

function jobRow(name, meta, size, actionLabel, actionClass, withGrip) {
  const r = el("div", "jr");
  const left = el("div");
  if (withGrip) {
    left.appendChild(el("span", "grip", "⋮⋮"));
    left.append(" ");
  }
  left.appendChild(el(withGrip ? "span" : "div", "nm", name));
  if (meta) left.appendChild(el("div", "meta", meta));
  r.appendChild(left);
  r.appendChild(size ? el("span", "sz", size) : el("span"));
  r.appendChild(button(actionClass, actionLabel));
  return r;
}

function rows() {
  const wrap = el("div", "kit-list");
  const updatesRight = row(el("span", "badge", "3"), button("btn sm", "Update all"));
  wrap.appendChild(sectionHead("Updates available", updatesRight));
  wrap.appendChild(jobRow("HELLDIVERS™ 2", "build 19 834 112 · 1.2 GB", null, "Update", "btn sm w72", false));
  wrap.appendChild(jobRow("Factorio", "build 19 701 002 · 0.3 GB", null, "Update", "btn sm w72", false));
  wrap.appendChild(sectionHead("Queue", el("span", "badge", "2")));
  wrap.appendChild(jobRow("Satisfactory", null, "24.8 GB", "Remove", "btn sm ghost w72", true));
  wrap.appendChild(jobRow("Enshrouded", null, "86.6 GB", "Remove", "btn sm ghost w72", true));
  wrap.appendChild(el("p", "hint", "Drag the handle to change the order."));
  wrap.appendChild(el("div", "empty", "Nothing is waiting."));
  return wrap;
}

/** Every primitive once, in kit.html's order. */
function primitives() {
  const frag = document.createDocumentFragment();
  frag.append(
    section("Page head", pageHead()),
    section("Tiles", tiles()),
    section("Section head and segmented control", sectionHead("Section label", seg(["24 h", "7 days", "30 days", "All"], 1))),
    section("Buttons", buttons()),
    section("Status pills", pills()),
    section("Filter and game chips", chips()),
    section("Field card", fieldCard()),
    section("Rows, badges, hint, empty", rows()),
  );
  return frag;
}

/** @returns {HTMLElement} the view root for app.js's renderView. */
export function renderKit() {
  const root = el("section", "view-kit kit kit-page");
  const head = el("div", "page-head");
  const left = el("div");
  left.appendChild(el("h1", null, "UI kit"));
  left.appendChild(el("div", "sub", "Every primitive of css/kit.css from the real stylesheets · full width, then a 400px container"));
  head.appendChild(left);
  root.appendChild(head);
  root.appendChild(section("Scale", scaleBlock()));
  root.appendChild(primitives());
  const frame = el("div", "kit-frame");
  frame.appendChild(primitives());
  root.appendChild(section("Inside a 400px container", frame));
  return root;
}
