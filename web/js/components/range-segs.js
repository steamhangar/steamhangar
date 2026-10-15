/**
 * Range selector (WP D15c): "24 h | 7 days | 30 days | All" as text
 * segments, the `.segs`/`aria-pressed` idiom Settings already uses for its
 * text choices (one idiom, not two). Used by the Clients page, the
 * Downloads statistics header and the Downloads history filter.
 *
 * The group is built once per mount and only its `aria-pressed` states
 * change afterwards, so a click never rebuilds the focused button. A click
 * on the already-selected range does nothing.
 */

import { RANGES, RANGE_LABELS } from "../lib/traffic-view.js";

/**
 * @param {{label: string, value: string, onChange: (range: string) => void, role?: string, ranges?: readonly string[]}} options
 *   `label` names the group for assistive tech ("Statistics range");
 *   `ranges` defaults to `RANGES` (the Downloads statistics pass
 *   `STATS_RANGES`, WP GRAPH-ZOOM).
 * @returns {{el: HTMLElement, set: (range: string) => void}}
 */
export function createRangeSegs({ label, value, onChange, role, ranges = RANGES }) {
  const group = document.createElement("div");
  group.className = "segs rangesegs";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", label);
  if (role) group.dataset.role = role;
  let current = value;
  const buttons = ranges.map((range) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = RANGE_LABELS[range];
    btn.dataset.range = range;
    btn.addEventListener("click", () => {
      if (range === current) return;
      set(range);
      onChange(range);
    });
    group.appendChild(btn);
    return btn;
  });
  function set(range) {
    current = range;
    for (const btn of buttons) btn.setAttribute("aria-pressed", String(btn.dataset.range === range));
  }
  set(value);
  return { el: group, set };
}
