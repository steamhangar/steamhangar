/**
 * DOM building blocks of the "Add a device" drawer (WP ADD-DEVICE-2,
 * `docs/design/rc20/clients.html`, "Add a device"): numbered steps, the
 * tab and notice icons, the blurred stand-in for a secret and the QR code
 * as inline SVG. Stateless: every function returns fresh nodes, and none of
 * them ever sees the API key — the secret itself is only built by
 * `components/add-device-sheet.js` after an explicit "Show".
 *
 * Classes are the UI kit's (`css/kit.css`, "Add a device drawer"); the
 * drawer root carries `kit`, so they only apply inside it.
 */

import { encodeQrText, qrSvgGeometry } from "../lib/qr-encode.js";

const SVG_NS = "http://www.w3.org/2000/svg";

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Outline icons of the mockup (24px grid, currentColor stroke). */
const ICONS = {
  phone: { width: "1.8", shapes: [["rect", { x: "7", y: "2.5", width: "10", height: "19", rx: "2" }], ["path", { d: "M11 18.5h2" }]] },
  browser: { width: "1.8", shapes: [["circle", { cx: "12", cy: "12", r: "9" }], ["path", { d: "M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" }]] },
  windows: { width: "1.8", shapes: [["rect", { x: "3", y: "4", width: "18", height: "12", rx: "2" }], ["path", { d: "M8 20h8M12 16v4" }]] },
  linux: { width: "1.8", shapes: [["path", { d: "M4 6l5 5-5 5M12 18h8" }]] },
  warn: { width: "2", shapes: [["path", { d: "M12 3l10 18H2zM12 10v5M12 18h.01" }]] },
};

/** A decorative icon: no text, hidden from assistive technology (the
 * control next to it carries the name). */
export function icon(name) {
  const spec = ICONS[name];
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", spec.width);
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.dataset.icon = name;
  for (const [tag, attrs] of spec.shapes) {
    const shape = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) shape.setAttribute(k, v);
    svg.append(shape);
  }
  return svg;
}

/** The QR code as an inline SVG: dark modules on a white plate including
 * the quiet zone, in every theme (scanners want dark-on-light). */
export function buildQrSvg(text, { label = "QR code for pairing the SteamHangar app", role = "pair-qr" } = {}) {
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

/** Text the stand-in QR encodes: a scan of the blurred picture reads this
 * sentence, never a pairing address. */
export const QR_MASK_TEXT = "SteamHangar: press Show in the browser to see the pairing code.";

/** The blurred stand-in for the pairing QR code (before "Show"). It is a
 * real QR of `QR_MASK_TEXT`, so the blurred picture has the right texture,
 * and it carries nothing secret. Wrapped in a div because an SVG element
 * has no `hidden` property: the Show gate toggles the wrapper. */
export function qrMask() {
  const svg = buildQrSvg(QR_MASK_TEXT, { role: "pair-qr-mask-code" });
  svg.removeAttribute("role");
  svg.removeAttribute("aria-label");
  const wrap = el("div", "ad-qrmask");
  wrap.dataset.role = "pair-qr-mask";
  wrap.setAttribute("aria-hidden", "true");
  wrap.append(svg);
  return wrap;
}

/** The blurred stand-in for a link or command (before "Show"): bullets in
 * the shape of the real text, optionally after a non-secret `prefix`. */
export function codeMask(lines, prefix = "") {
  const box = el("div", "code hid ad-mask", prefix + lines.map((n) => "•".repeat(n)).join("\n"));
  box.setAttribute("aria-hidden", "true");
  return box;
}

/** `ol.steps`: one `li` per step with its number, a title, an optional
 * description and optional extra nodes (secret blocks, QR boxes). */
export function stepList(steps) {
  const ol = el("ol", "steps");
  steps.forEach((step, i) => {
    const li = el("li");
    const n = el("span", "n", String(i + 1));
    n.setAttribute("aria-hidden", "true");
    const body = el("div");
    body.append(el("h3", null, step.title));
    if (step.desc) {
      const p = el("p", "ad-desc", step.desc);
      if (step.role) p.dataset.role = step.role;
      body.append(p);
    }
    if (step.extra) body.append(...step.extra);
    li.append(n, body);
    ol.append(li);
  });
  return ol;
}
