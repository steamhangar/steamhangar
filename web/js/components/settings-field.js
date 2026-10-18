/**
 * One Settings field (WP SETTINGS-2, docs/design/rc20/kit.html "field card"
 * and settings.html). `views/settings.js` composes the page from these; the
 * field itself owns nothing but its own DOM and calls `onDraft` on an edit.
 *
 * Anatomy (kit.css `.field`, inside a page that carries `.kit`):
 *
 *   div.field[data-key]
 *     div.lh        label (13px/500) · (i) `.ib` 18px · state chip
 *     control       text `.in` | `.seg` buttons | `.checks` pills
 *                   (+ Reset for a stored value, in `.frow`)
 *     div.hint      behind the (i), collapsed by default: the timing line
 *                   first (`field-caption`), then the help paragraphs.
 *
 * Wording rule (audit 1.2): state stays visible (label, chip, value), the
 * explanation sits behind the (i). Env pins (Q1 2026-10-14: env always
 * wins): a row with `editable: false` or `env_only: true` is a DISABLED
 * input showing the value as text — never a seg or checkbox control, no
 * Reset, no input listener — with the "From env" chip and the env note (and
 * the shadow note) behind the (i). A pinned row has no timing line: nothing
 * here can change it.
 *
 * Every label, help text and note comes from lib/settings-layout.js and
 * lib/settings-presentation.js; this file adds no wording of its own.
 */
import {
  displayValue,
  envNote,
  fieldChip,
  fieldHelp,
  fieldMeta,
  isPinned,
  shadowNote,
} from "../lib/settings-layout.js";
import { appliesText, canReset, effectiveAsInputValue } from "../lib/settings-presentation.js";
import { watchScrollCue } from "../lib/scroll-cue.js";

/** Keys whose control spans both grid columns (rc20 settings.html `.f.w`). */
const WIDE_KEYS = new Set(["webhook_url"]);
/** Free-text keys shown in the UI font; every other text field is mono
 * (values, sizes, hosts — settings.html `.inp.m`). */
const PROSE_KEYS = new Set(["vault_name", "schedule_window"]);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * The (i) button plus its collapsed text block (kit.html: an 18px bordered
 * "i"). A native <button> with aria-expanded + aria-controls; the user's
 * open/closed choice is kept in `memory` under `id`, so a re-render keeps
 * it. `lead` nodes come first (a field's timing line), then `paragraphs`.
 *
 * @param {{id: string, label: string, paragraphs?: string[], lead?: Node[], memory?: Map<string, boolean>}} options
 * @returns {{btn: HTMLButtonElement, help: HTMLDivElement}}
 */
export function createInfoDisclosure({ id, label, paragraphs = [], lead = [], memory = new Map() }) {
  const help = el("div", "hint fhelp");
  help.id = `${id}-help`;
  help.dataset.role = "field-help";
  for (const node of lead) help.appendChild(node);
  for (const text of paragraphs) help.appendChild(el("p", null, text));
  const btn = el("button", "ib", "i");
  btn.type = "button";
  btn.dataset.role = "field-info";
  btn.setAttribute("aria-label", `More about ${label}`);
  btn.setAttribute("aria-controls", help.id);
  const apply = (open) => {
    btn.setAttribute("aria-expanded", String(open));
    help.hidden = !open;
  };
  apply(memory.get(id) === true);
  btn.addEventListener("click", () => {
    const open = memory.get(id) !== true;
    memory.set(id, open);
    apply(open);
  });
  return { btn, help };
}

/** The state chip: Default / Changed here / From env (`fieldChip`). */
export function createFieldChip(entry) {
  const chip = fieldChip(entry);
  const cls = chip.kind === "changed" ? "chip ch" : chip.kind === "env" ? "chip env" : "chip";
  const node = el("span", cls, chip.text);
  node.dataset.role = "field-chip";
  node.dataset.chip = chip.kind;
  return node;
}

function resetButton(entry, readonly, onReset) {
  const btn = el("button", "btn ghost sm", "Reset");
  btn.type = "button";
  btn.dataset.role = "field-reset";
  btn.title = "Remove the override — back to the environment or default value";
  btn.hidden = readonly || !canReset(entry);
  btn.addEventListener("click", () => onReset());
  return btn;
}

function textControl(entry, meta, fieldId, readonly, onDraft) {
  const input = el("input", PROSE_KEYS.has(entry.key) ? "in" : "in mono");
  input.id = fieldId;
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  if (meta.placeholder) input.placeholder = meta.placeholder;
  input.value = effectiveAsInputValue(entry);
  input.disabled = readonly;
  input.addEventListener("input", () => onDraft({ value: input.value }));
  const row = el("div", "frow");
  row.append(
    input,
    resetButton(entry, readonly, () => {
      input.value = effectiveAsInputValue({ ...entry, effective: entry.fallback });
      onDraft({ reset: true });
    }),
  );
  return row;
}

function segControl(entry, meta, labelId, readonly, draftValue, onDraft) {
  const seg = el("div", "seg");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-labelledby", labelId);
  watchScrollCue(seg); // WP MOBILE-1b: overflow cue when it scrolls
  const buttons = [];
  const press = (value) => {
    for (const b of buttons) b.setAttribute("aria-pressed", String(b.dataset.value === value));
  };
  for (const [value, label] of meta.options) {
    const btn = el("button", null, label);
    btn.type = "button";
    btn.dataset.value = value;
    btn.disabled = readonly;
    btn.addEventListener("click", () => {
      press(value);
      onDraft({ value });
    });
    buttons.push(btn);
    seg.appendChild(btn);
  }
  press(draftValue ?? String(entry.effective));
  const row = el("div", "frow");
  row.append(
    seg,
    resetButton(entry, readonly, () => {
      press(String(entry.fallback));
      onDraft({ reset: true });
    }),
  );
  return row;
}

function checksControl(entry, meta, labelId, readonly, draftValue, onDraft) {
  const wrap = el("div", "frow frow-col");
  const grid = el("div", "checks");
  grid.setAttribute("role", "group");
  grid.setAttribute("aria-labelledby", labelId);
  const selected = new Set(draftValue ?? (Array.isArray(entry.effective) ? entry.effective : []));
  const boxes = [];
  for (const [value, label] of meta.options) {
    const pill = el("label", "check");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = selected.has(value);
    cb.disabled = readonly;
    cb.dataset.event = value;
    cb.addEventListener("change", () => {
      onDraft({ value: boxes.filter((c) => c.checked).map((c) => c.dataset.event) });
    });
    boxes.push(cb);
    pill.append(cb, el("span", null, label));
    grid.appendChild(pill);
  }
  wrap.append(
    grid,
    resetButton(entry, readonly, () => {
      const back = new Set(Array.isArray(entry.fallback) ? entry.fallback : []);
      for (const cb of boxes) cb.checked = back.has(cb.dataset.event);
      onDraft({ reset: true });
    }),
  );
  return wrap;
}

/**
 * Build one field.
 *
 * @param {object} entry one `GET /v1/settings` row
 * @param {{
 *   readonly?: boolean,          // VAULT_SETTINGS_READONLY: controls disabled, no Reset
 *   draftValue?: unknown,        // the user's unsaved value for segs/checks, if any
 *   onDraft?: (draft: {value: unknown} | {reset: true}) => void,
 *   memory?: Map<string, boolean>, // (i) open/closed choices across re-renders
 * }} [options]
 * @returns {HTMLDivElement}
 */
export function createSettingsField(entry, { readonly = false, draftValue, onDraft = () => {}, memory = new Map() } = {}) {
  const meta = fieldMeta(entry);
  const fieldId = `settings-${entry.key}`;
  const pinned = isPinned(entry);
  const wide = WIDE_KEYS.has(entry.key) || (!pinned && meta.kind === "events");
  const field = el("div", `field${wide ? " wide" : ""}${pinned ? " pinned" : ""}`);
  field.dataset.key = entry.key;

  // A <label for> wherever there is one input to name (text rows and every
  // pinned row); a group's name comes from aria-labelledby on the group.
  const labelled = pinned || meta.kind === "text" || !meta.options;
  const labelEl = el(labelled ? "label" : "span", labelled ? null : "lbl", meta.label);
  labelEl.id = `${fieldId}-label`;
  if (labelled) labelEl.htmlFor = fieldId;

  const lead = [];
  if (pinned) {
    const note = el("p", null, envNote(entry));
    note.dataset.role = "env-note";
    lead.push(note);
    const shadow = shadowNote(entry);
    if (shadow) {
      const s = el("p", null, shadow);
      s.dataset.role = "shadow-note";
      lead.push(s);
    }
  } else {
    const caption = el("p", null, appliesText(entry.applies));
    caption.dataset.role = "field-caption";
    lead.push(caption);
  }
  const info = createInfoDisclosure({ id: fieldId, label: meta.label, paragraphs: fieldHelp(entry), lead, memory });

  const head = el("div", "lh");
  head.append(labelEl, info.btn, createFieldChip(entry));
  field.append(head);

  if (pinned) {
    const input = el("input", PROSE_KEYS.has(entry.key) ? "in" : "in mono");
    input.id = fieldId;
    input.type = "text";
    input.disabled = true;
    input.value = displayValue(entry);
    input.dataset.role = "setting-value";
    field.append(input);
  } else if (meta.kind === "segs" && meta.options) {
    field.append(segControl(entry, meta, labelEl.id, readonly, draftValue, onDraft));
  } else if (meta.kind === "events" && meta.options) {
    field.append(checksControl(entry, meta, labelEl.id, readonly, draftValue, onDraft));
  } else {
    field.append(textControl(entry, meta, fieldId, readonly, onDraft));
  }
  field.append(info.help);
  return field;
}
