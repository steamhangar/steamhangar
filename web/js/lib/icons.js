// Inline SVG icons shared by more than one view (WP GOLDENS-LOCAL-1).
//
// Icons are SVG, never text glyphs: a glyph such as U+22EE ("⋮") is not in
// every font, so it renders from whatever fallback font a system has -- or
// as a missing-glyph box (the screenshot tool admits only Liberation fonts,
// tools/web-shot-fonts.conf).

/** Six-dot drag handle (Downloads queue rows, the kit page's job rows). */
export const GRIP_SVG =
  '<svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><circle cx="7" cy="4" r="1.5"/><circle cx="13" cy="4" r="1.5"/><circle cx="7" cy="10" r="1.5"/><circle cx="13" cy="10" r="1.5"/><circle cx="7" cy="16" r="1.5"/><circle cx="13" cy="16" r="1.5"/></svg>';

/** Parse a constant SVG string into an element (markup from this module only). */
export function svgIcon(svgMarkup, doc = document) {
  const span = doc.createElement("span");
  span.innerHTML = svgMarkup;
  return span.firstElementChild;
}
