/**
 * Downloads hero (WP DOWNLOADS-2, docs/design/rc20/kit.html "Downloads,
 * width-aware"): one surface card with
 *
 *   - a head row: the "Speed" section label and the statistics range seg;
 *   - the live block: the current speed as the big number, a status pill
 *     (Downloading / Idle), and one caption line with the upstream limit and
 *     how many devices are loading (the former "Client downloading" row,
 *     WP CLIENT-ROW-1, folded in here);
 *   - the throughput graph (96px, grid lines) beside it;
 *   - a mini line under it: average while loading, peak, the prefill
 *     runner's share;
 *   - the four totals as 2x2 kit tiles on the right (Served, From internet,
 *     Saved by cache, Hit rate with a meter).
 *
 * Data: the same `GET /v1/throughput` (+ `/series`, or the live endpoint
 * merged by `mergeLive`) answer the old statistics header read. Every
 * figure, title and honest-state rule comes from `lib/traffic-view.js`
 * (`headerTiles`, `feedNote`, `seriesGeometry` ...); this module only maps
 * them onto the kit anatomy. `heroModel` and `graphModel` are pure (tested
 * without a DOM); `createDownloadsHero` builds the card once per mount and
 * `paint` patches it on every statistics tick. The status pill is rebuilt
 * only when its kind changes, so the running glyph's animation does not
 * restart on every 3 s live tick. Nothing in the card except the range
 * buttons is focusable, so repainting never takes focus.
 *
 * Built with createElement + textContent only (vault-api's CSP has no
 * 'unsafe-inline'); the meter width goes through the CSSOM.
 */
import { createRangeSegs } from "./range-segs.js";
import { createPill } from "./pill.js";
import {
  DASH,
  FEED_TEXT,
  STATS_RANGES,
  clientDownloadingRow,
  feedNote,
  feedState,
  formatSpeed,
  formatTrafficBytes,
  headerTiles,
  joinNotes,
  refreshFailedText,
  seriesGeometry,
  seriesLabel,
  seriesStartText,
  trimSeries,
} from "../lib/traffic-view.js";

const SVG_NS = "http://www.w3.org/2000/svg";

/** The graph's viewBox (kit.html: 600 x 96, stretched to the box). */
export const GRAPH_W = 600;
export const GRAPH_H = 96;
/** Horizontal grid lines at a quarter, half and three quarters. */
export const GRAPH_GRID = Object.freeze([24, 48, 72]);

/** The four hero tiles: which `headerTiles` figure, kit label and caption. */
export const HERO_TILES = Object.freeze([
  Object.freeze({ key: "loaded", label: "Served", caption: "cache + prefill" }),
  Object.freeze({ key: "internet", label: "From internet", caption: "not in cache" }),
  Object.freeze({ key: "saved", label: "Saved by cache", caption: "instead of internet" }),
  Object.freeze({ key: "hit-rate", label: "Hit rate", caption: null }),
]);

/** The status pill words (kit.html: "Downloading", mockup idle: "Idle"). */
export const PILL_DOWNLOADING = "Downloading";
export const PILL_IDLE = "Idle";

/**
 * A formatted figure split into the number and its unit, the kit's
 * `<div class="v">48.1<small>GB</small></div>` shape: "60.0 GB" ->
 * {value:"60.0", unit:"GB"}, "83%" -> {value:"83", unit:"%"},
 * "< 1 MB" -> {value:"< 1", unit:"MB"}; the dash (or nothing) stays a dash
 * with no unit.
 * @param {string | null | undefined} text
 * @returns {{value: string, unit: string}}
 */
export function splitFigure(text) {
  if (text === null || text === undefined || text === "" || text === DASH) return { value: DASH, unit: "" };
  const s = String(text);
  if (s.endsWith("%")) return { value: s.slice(0, -1), unit: "%" };
  const i = s.lastIndexOf(" ");
  return i > 0 ? { value: s.slice(0, i), unit: s.slice(i + 1) } : { value: s, unit: "" };
}

/**
 * "N devices loading" for the caption (WP CLIENT-ROW-1 folded into the
 * hero): only with a fresh (`ok`) feed and a known count; the server's own
 * prefill runner is never counted (`clientDownloadingRow`'s rule). Zero
 * reads "no device loading"; an unknown count shows nothing.
 * @param {object | null | undefined} throughput
 * @returns {{text: string, title: string} | null}
 */
export function devicesLoading(throughput) {
  if (!throughput || feedState(throughput) !== "ok") return null;
  const n = throughput.active_clients_now;
  if (!Number.isInteger(n) || n < 0) return null;
  const row = clientDownloadingRow(throughput);
  if (!row) {
    return {
      text: "no device loading",
      title: "No machine other than this server loaded anything from the cache recently. The server's own prefill runner is not counted.",
    };
  }
  return { text: `${n} ${n === 1 ? "device" : "devices"} loading`, title: `${row.detail}. ${row.title}` };
}

/** "Limit 8 MB/s, all day" -> parts with the speed in bold (kit caption). */
function limitParts(rateCap) {
  const m = /^(Limit )(\S+ \S+?)(,.*)?$/.exec(rateCap);
  if (!m) return [rateCap];
  return [m[1], { b: m[2] }, m[3] || ""];
}

/** The runner's share for the mini line, `null` when the server sent none. */
function runnerParts(summary) {
  const runner = summary && summary.prefill_runner;
  if (!runner || !Number.isFinite(runner.bytes_served) || runner.bytes_served <= 0) return null;
  const size = formatTrafficBytes(runner.bytes_served);
  if (!size) return null;
  const cached = Number.isFinite(runner.hit_bytes) && runner.hit_bytes > 0 ? formatTrafficBytes(runner.hit_bytes) : null;
  return cached ? ["Prefill runner ", { b: size }, " of it, ", { b: cached }, " from the cache"] : ["Prefill runner ", { b: size }, " of it"];
}

/** `title` + optional detail line as one tooltip. */
function tooltip(tile) {
  return [tile.title, tile.detail].filter(Boolean).join(" — ");
}

/**
 * Everything the hero shows, from the statistics state. Pure.
 *
 * `note`: the line under the head row (feed off / stale / loading / an old
 * server / a failed refresh), or `null`. `figures`: `null` when there is
 * nothing to show (no answer yet, feed disabled, server without the
 * endpoints) — the note then stands alone and the range seg is hidden
 * when `noFigures` says the server has no figures at all.
 *
 * @param {{throughput?: object|null, rateCap?: string|null, unsupported?: boolean, error?: string|null}} state
 * @returns {{note: string|null, figures: null | {
 *   big: {value: string, unit: string, title: string},
 *   status: null | {kind: string, text: string},
 *   caption: Array<string|{b: string}>, captionTitle: string,
 *   mini: Array<{key: string, parts: Array<string|{b: string}>, title: string}>,
 *   tiles: Array<{key: string, label: string, caption: string|null, value: string, unit: string, title: string, meter: number|null}>
 * }}}
 */
export function heroModel({ throughput = null, rateCap = null, unsupported = false, error = null } = {}) {
  const t = throughput;
  let note;
  if (unsupported) note = FEED_TEXT.unsupported;
  else if (error && !t) note = `Could not load the statistics: ${error}`;
  else if (t) note = joinNotes(error ? refreshFailedText(error) : null, feedNote(t));
  else note = "Loading statistics…";

  const tiles = unsupported ? null : headerTiles(t, rateCap);
  if (!tiles) return { note, figures: null };
  const byKey = new Map(tiles.map((tile) => [tile.key, tile]));
  const summary = t.summary || {};

  const current = byKey.get("current");
  const bps = t.current ? t.current.bytes_per_second : null;
  let status = null;
  if (typeof bps === "number" && Number.isFinite(bps) && bps >= 0) {
    status = bps > 0 ? { kind: "running", text: PILL_DOWNLOADING } : { kind: "notinuse", text: PILL_IDLE };
  }

  const devices = devicesLoading(t);
  const caption = [];
  if (rateCap) caption.push(...limitParts(rateCap));
  if (devices) caption.push(...(caption.length ? [" · "] : []), devices.text);

  const mini = [
    { key: "average", parts: ["Avg while loading ", { b: byKey.get("average").value }], title: byKey.get("average").title },
    { key: "peak", parts: ["Peak ", { b: byKey.get("peak").value }], title: byKey.get("peak").title },
  ];
  const runner = runnerParts(summary);
  if (runner) mini.push({ key: "runner", parts: runner, title: byKey.get("loaded").title });

  const rate = summary.byte_hit_rate;
  const meter = typeof rate === "number" && Number.isFinite(rate) && rate >= 0 ? Math.min(rate, 1) : null;

  return {
    note,
    figures: {
      big: { ...splitFigure(current.value), title: tooltip(current) },
      status,
      caption,
      captionTitle: devices ? devices.title : "",
      mini,
      tiles: HERO_TILES.map((spec) => {
        const tile = byKey.get(spec.key);
        return {
          key: spec.key,
          label: spec.label,
          caption: spec.caption,
          ...splitFigure(tile.value),
          title: tooltip(tile),
          meter: spec.key === "hit-rate" && tile.value !== DASH ? meter : null,
        };
      }),
    },
  };
}

/**
 * The graph for the selected range, or `null` (no answer for it yet, feed
 * off, or an all-zero series: a flat line on the floor shows nothing).
 * Pure.
 * @param {object | null | undefined} series
 * @param {string} range the range the user has selected
 * @param {{unsupported?: boolean, nowMs?: number}} [opts]
 * @returns {null | {line: string, area: string, label: string, axis: string[]}}
 */
export function graphModel(series, range, { unsupported = false, nowMs = Date.now() } = {}) {
  if (unsupported || !series || feedState(series) === "disabled" || series.range !== range) return null;
  // WP GRAPH-ZOOM: only the span that has data; the axis caption follows it.
  const shown = { ...series, points: trimSeries(series.points) };
  const geo = seriesGeometry(shown.points, { width: GRAPH_W, height: GRAPH_H });
  if (!geo || geo.maxBytesPerSecond === 0) return null;
  return {
    line: geo.line,
    area: geo.area,
    label: seriesLabel(series, geo.maxBytesPerSecond),
    axis: [seriesStartText(shown, nowMs), `peak ${formatSpeed(geo.maxBytesPerSecond) || "0 MB/s"}`, "now"],
  };
}

// ---------------------------------------------------------------------
// DOM
// ---------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

/** Text parts with `{b}` runs in bold, into `parent`. */
function fillParts(parent, parts) {
  parent.replaceChildren();
  for (const part of parts) {
    if (typeof part === "string") parent.append(part);
    else parent.appendChild(el("b", null, part.b));
  }
}

/** The kit's value line: number + small unit; a dash is hidden from screen
 * readers and replaced by "Not available" for them. */
function fillFigure(node, { value, unit }) {
  node.replaceChildren();
  if (value === DASH) {
    const dash = el("span", null, DASH);
    dash.setAttribute("aria-hidden", "true");
    node.append(dash, el("span", "sr-only", "Not available"));
    return;
  }
  node.append(value);
  if (unit) node.appendChild(el("small", null, unit));
}

function buildTile(tile) {
  const box = el("div", "tile");
  box.dataset.stat = tile.key;
  box.title = tile.title;
  box.appendChild(el("div", "k", tile.label));
  const v = el("div", "v");
  fillFigure(v, tile);
  box.appendChild(v);
  if (tile.meter !== null) {
    const meter = el("div", "meter");
    meter.setAttribute("aria-hidden", "true");
    const bar = el("i");
    bar.style.setProperty("width", `${Math.round(tile.meter * 100)}%`);
    meter.appendChild(bar);
    box.appendChild(meter);
  } else if (tile.caption) {
    box.appendChild(el("div", "n", tile.caption));
  }
  return box;
}

/**
 * Build the hero once per mount.
 * @param {{range: string, onRangeChange: (range: string) => void}} options
 * @returns {{el: HTMLElement, rangeEl: HTMLElement, paint: (state: object) => void}}
 */
export function createDownloadsHero({ range, onRangeChange }) {
  const root = el("section", "hero");
  root.setAttribute("aria-label", "Download statistics");
  root.dataset.role = "dl-stats";

  const head = el("div", "hh");
  const label = el("span", "sec", "Speed");
  const seg = createRangeSegs({
    label: "Statistics range",
    value: range,
    onChange: onRangeChange,
    role: "stats-range",
    ranges: STATS_RANGES,
    className: "seg rangesegs",
  });
  const note = el("p", "hint");
  note.dataset.role = "stats-note";
  note.hidden = true;
  head.append(label, seg.el, note);

  const live = el("div", "live");
  live.dataset.role = "stats-live";
  live.hidden = true;
  const left = el("div", "lv");
  const big = el("div", "big");
  big.dataset.role = "stats-current";
  const pillSlot = el("div", "hpill");
  pillSlot.dataset.role = "stats-pill";
  const cap = el("div", "hcap");
  cap.dataset.role = "stats-caption";
  left.append(big, pillSlot, cap);
  const graph = el("figure", "graph");
  graph.dataset.role = "stats-graph";
  graph.hidden = true;
  live.append(left, graph);

  const mini = el("div", "mini");
  mini.dataset.role = "stats-mini";
  mini.hidden = true;

  const tiles = el("div", "tiles");
  tiles.dataset.role = "stats-grid";
  tiles.hidden = true;

  root.append(head, live, mini, tiles);

  function paintGraph(model) {
    graph.replaceChildren();
    graph.hidden = !model;
    if (!model) return;
    const svg = svgEl("svg", {
      viewBox: `0 0 ${GRAPH_W} ${GRAPH_H}`,
      preserveAspectRatio: "none",
      role: "img",
      "aria-label": model.label,
    });
    svg.dataset.role = "graph-svg";
    const grid = svgEl("g", { class: "grid" });
    for (const y of GRAPH_GRID) grid.appendChild(svgEl("line", { x1: 0, y1: y, x2: GRAPH_W, y2: y }));
    svg.append(grid, svgEl("path", { class: "area", d: model.area }), svgEl("path", { class: "line", d: model.line }));
    const axis = el("div", "ax");
    axis.setAttribute("aria-hidden", "true");
    for (const text of model.axis) axis.appendChild(el("span", null, text));
    graph.append(svg, axis);
  }

  /**
   * @param {{throughput: object|null, series: object|null, range: string, rateCap: string|null,
   *   unsupported: boolean, error: string|null, noFigures: boolean}} state
   */
  function paint(state) {
    // An answer for another range is never painted (the view drops it), so
    // the device count always belongs to the range on screen.
    const throughput = state.throughput && state.throughput.range === state.range ? state.throughput : null;
    const model = heroModel({
      throughput,
      rateCap: state.rateCap,
      unsupported: state.unsupported,
      error: state.error,
    });
    note.textContent = model.note || "";
    note.hidden = !model.note;
    // No figures at all (feed disabled, or a server without the endpoints):
    // no range to choose either. While an answer is pending the last known
    // verdict holds, so a range click never hides the control it came from.
    seg.el.hidden = !!state.noFigures;

    const f = model.figures;
    live.hidden = !f;
    mini.hidden = !f;
    tiles.hidden = !f;
    if (!f) {
      big.replaceChildren();
      cap.replaceChildren();
      mini.replaceChildren();
      tiles.replaceChildren();
      pillSlot.replaceChildren();
      delete pillSlot.dataset.kind;
      paintGraph(null);
      return;
    }
    fillFigure(big, f.big);
    big.title = f.big.title;
    const kind = f.status ? f.status.kind : "";
    if (pillSlot.dataset.kind !== kind) {
      pillSlot.replaceChildren();
      if (f.status) pillSlot.appendChild(createPill({ kind: f.status.kind, text: f.status.text, tone: f.status.kind === "running" ? "run" : null }));
      pillSlot.dataset.kind = kind;
    }
    pillSlot.hidden = !f.status;
    fillParts(cap, f.caption);
    cap.title = f.captionTitle;
    cap.hidden = cap.textContent === "";
    mini.replaceChildren();
    for (const item of f.mini) {
      const span = el("span");
      span.dataset.stat = item.key;
      span.title = item.title;
      fillParts(span, item.parts);
      mini.appendChild(span);
    }
    tiles.replaceChildren(...f.tiles.map(buildTile));
    paintGraph(graphModel(state.series, state.range, { unsupported: state.unsupported }));
  }

  return { el: root, rangeEl: seg.el, paint };
}
