/**
 * Minimal in-memory DOM shim shared by the WP 4a.8 DOM-wiring regression
 * tests (`dialog-wiring.test.js`, `modal-stack.test.js`), extended by WP 4e.6
 * (`rail-panel-wiring.test.js`) with `replaceChildren` — the shim grows just
 * far enough for each new consumer's actual DOM-API surface, per its own
 * established pattern, rather than trying to anticipate every method up
 * front.
 *
 * Same spirit as `store-poll-loop.test.js`'s fake `document` (a plain object
 * exposing only the members the module under test actually touches, set
 * BEFORE importing it) — extended just far enough to run
 * `js/lib/modal-stack.js`, `js/components/sheet-dialog.js`,
 * `js/components/status-icon.js` and `js/router.js` headlessly: createElement/
 * createElementNS returning a real (if tiny) element graph with
 * classList/attributes/children/focus/dispatchEvent, because those modules
 * do more DOM-API surface than store.js's three-member touch. This is NOT a
 * general-purpose DOM implementation — no layout, no CSS, no querySelector
 * beyond what these specific modules need — it exists to prove the WIRING
 * (an element ends up with the right class/attribute, a callback fires),
 * not to be a jsdom replacement.
 */

// ---------------------------------------------------------------------
// Minimal CSS selector engine (WP AG-2, `game-card-installed.test.js`):
// `game-card.js`'s `patchCardVolatile`/`syncInstalledTag` genuinely call
// `cardEl.querySelector(".cappill")` / `.querySelector(".meta .size")` /
// `.querySelector(".meta")` / `.querySelector(".instbadge")`, and
// `buildCard`'s own interaction wiring calls
// `card.querySelectorAll("button.cappill, button.icnact")` — the previous
// `querySelector() { return null; }` stub (this file's own "grows just far
// enough" policy) made every one of those a silent no-op, which would have
// made a patch-path test pass VACUOUSLY (the call under test never actually
// reaches the code it exists to exercise). Supports exactly what this
// codebase's call sites use: a comma-separated list of simple
// tag[.class[.class...]] compound selectors, chained by whitespace
// (descendant combinator only) — no ID selectors, no child (`>`) or sibling
// combinators, since nothing in web/js/ needs them via this harness. A
// SINGLE `[attr="value"]` exact-match clause is supported per compound
// (added for `game-detail-sheet.js`'s `row.querySelector('[data-role="
// iwhen"]')` — WP AG-2) since that idiom already existed in this component
// before this WP (`[data-role="size"]` etc.) and needed to keep working.
//
// **THROWS on anything outside that grammar (WP AG-2 review round 2), on
// purpose.** The original stub's "no match, never throws" posture is
// exactly the silent-no-op class this whole fix round exists to kill one
// layer up: a future consumer reaching for `#id`, a `>`/sibling combinator,
// or a bare `[attr]` (no value) would get an empty result indistinguishable
// from "selector matched nothing" instead of "this harness doesn't support
// that yet" — the identical failure mode `patchCardVolatile`'s badge sync
// silently no-opped under before this WP added real selector support at
// all. Verified non-regressive: every selector actually used across
// web/js/ (`.cappill`, `.pv`, `.meta`, `.meta .size`, `.instbadge`,
// `.installed-list`, `.installed-row`, `[data-role="..."]`,
// `button.cappill, button.icnact`) is inside this grammar, and the full
// suite stays green with the throw live.
// ---------------------------------------------------------------------

function parseCompoundToken(token) {
  const attrMatch = /\[([\w-]+)=["']([^"']*)["']\]/.exec(token);
  let rest = token;
  let attr = null;
  if (attrMatch) {
    attr = { name: attrMatch[1], value: attrMatch[2] };
    rest = token.slice(0, attrMatch.index) + token.slice(attrMatch.index + attrMatch[0].length);
  }
  const m = /^([a-zA-Z][\w-]*)?(\.[\w-]+)*$/.exec(rest);
  if (!m) return null;
  const tag = m[1] ? m[1].toUpperCase() : null;
  const classes = [...rest.matchAll(/\.([\w-]+)/g)].map((c) => c[1]);
  return { tag, classes, attr };
}

function matchesCompound(el, compound) {
  if (!compound) return false;
  if (compound.tag && el.tagName !== compound.tag) return false;
  for (const c of compound.classes) {
    if (!el._classes || !el._classes.has(c)) return false;
  }
  if (compound.attr) {
    if (typeof el.getAttribute !== "function") return false;
    if (el.getAttribute(compound.attr.name) !== compound.attr.value) return false;
  }
  return true;
}

/** Pre-order depth-first, matching real DOM document order (review round 2:
 * the previous breadth-first walk made `querySelector` return the
 * SHALLOWEST match instead of the FIRST one in document order — latent
 * only because nothing yet relied on cross-depth ordering, but a silent
 * surprise waiting for the next consumer that does). */
function descendantsOf(el) {
  const out = [];
  function visit(node) {
    for (const child of node.children) {
      out.push(child);
      visit(child);
    }
  }
  visit(el);
  return out;
}

/** One comma-free selector (possibly multiple whitespace-separated
 * descendant tokens) resolved against `root`, in document order. Throws on
 * any token outside this file's supported grammar (see the module comment
 * above) rather than silently matching nothing. */
function queryAllSingle(root, selector) {
  const parts = selector.trim().split(/\s+/);
  const tokens = parts.map(parseCompoundToken);
  const badIndex = tokens.findIndex((t) => t === null);
  if (badIndex !== -1) {
    throw new Error(
      `fake-dom.js's minimal selector engine does not support "${parts[badIndex]}" (in selector "${selector}"). ` +
        `Supported grammar: whitespace-chained tag[.class[.class...]] compounds, each optionally carrying ONE ` +
        `[attr="value"] exact-match clause — no ID selectors, no combinators other than descendant (space), no ` +
        `bare [attr] without a value. Extend parseCompoundToken/matchesCompound in web/tests/fake-dom.js if a ` +
        `real consumer now genuinely needs more, rather than letting it silently match nothing.`,
    );
  }
  let candidates = [root];
  for (const compound of tokens) {
    const next = [];
    for (const c of candidates) {
      for (const d of descendantsOf(c)) {
        if (matchesCompound(d, compound)) next.push(d);
      }
    }
    candidates = next;
  }
  return candidates;
}

/** Full selector list (comma-separated), union of each part's matches,
 * de-duplicated, first-seen order. */
function queryAll(root, selectorList) {
  const parts = String(selectorList)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const seen = new Set();
  const out = [];
  for (const part of parts) {
    for (const el of queryAllSingle(root, part)) {
      if (!seen.has(el)) {
        seen.add(el);
        out.push(el);
      }
    }
  }
  return out;
}

/** Number of top-level elements in `markup` (round 2, N1): walks open/close
 * tags with a depth counter; self-closing tags (`<x/>`) and comments are
 * handled, void HTML elements are not (no consumer passes them). */
function countTopLevelElements(markup) {
  let depth = 0;
  let count = 0;
  const re = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][\w-]*)[^>]*?(\/?)>/g;
  let m;
  while ((m = re.exec(markup))) {
    if (!m[2]) continue; // comment
    if (m[1]) {
      depth--;
    } else {
      if (depth === 0) count++;
      if (!m[3]) depth++;
    }
  }
  return count;
}

class FakeClassList {
  constructor(el) {
    this._el = el;
  }
  add(...names) {
    for (const n of names) this._el._classes.add(n);
  }
  remove(...names) {
    for (const n of names) this._el._classes.delete(n);
  }
  toggle(name, force) {
    const has = this._el._classes.has(name);
    const want = force === undefined ? !has : !!force;
    if (want) this._el._classes.add(name);
    else this._el._classes.delete(name);
    return want;
  }
  contains(name) {
    return this._el._classes.has(name);
  }
}

class FakeElement {
  constructor(tag) {
    this.tagName = String(tag || "").toUpperCase();
    this._classes = new Set();
    this.classList = new FakeClassList(this);
    // WP WEB-FIX-1 round 2 (N2): `childNodes` is the backing list (elements
    // AND text nodes); `children` is the element-only view, as in a real DOM.
    this.childNodes = [];
    this.parentNode = null;
    this._attrs = new Map();
    this._listeners = new Map();
    // WP AG-2: a plain `{}` let `el.dataset.role = "iwhen"` be READ back by
    // the SAME name, but never reflected into `_attrs` — so `getAttribute
    // ("data-role")` (and this file's own `[data-role="..."]` selector
    // support, added the same WP) never saw it, unlike the real DOM's
    // bidirectional dataset<->attribute sync. Proxied straight onto
    // `_attrs` with the standard camelCase<->kebab-case `data-` mapping —
    // real enough for every `dataset.foo`/`dataset.fooBar` site in web/js/.
    const attrs = this._attrs;
    this.dataset = new Proxy(
      {},
      {
        get(_target, prop) {
          if (typeof prop !== "string") return undefined;
          return attrs.get("data-" + prop.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()));
        },
        set(_target, prop, value) {
          if (typeof prop === "string") {
            attrs.set("data-" + prop.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()), String(value));
          }
          return true;
        },
        has(_target, prop) {
          return typeof prop === "string" && attrs.has("data-" + prop.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()));
        },
        deleteProperty(_target, prop) {
          if (typeof prop === "string") attrs.delete("data-" + prop.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()));
          return true;
        },
      },
    );
    // WP AG-2 (game-card-installed.test.js): game-card.js's buildCover()
    // calls `cap.style.setProperty("--h1", ...)` — a plain `{}` had no such
    // method. `setProperty`/`getPropertyValue` are real (custom properties
    // land as own-enumerable-ish string values via a private map), enough
    // for anything this harness's consumers actually call; nothing here
    // needs `removeProperty`/`cssText`/shorthand expansion.
    const styleProps = new Map();
    this.style = {
      setProperty(name, value) {
        styleProps.set(name, String(value));
      },
      getPropertyValue(name) {
        return styleProps.get(name) ?? "";
      },
    };
    this.tabIndex = 0;
  }
  get className() {
    return [...this._classes].join(" ");
  }
  set className(v) {
    this._classes = new Set(String(v).split(/\s+/).filter(Boolean));
  }
  // WP AG-2 (game-card-installed.test.js): game-card.js's buildCard() reads
  // `card.firstChild` right after appending the cover — a real DOM
  // property this shim never had, since no earlier consumer needed it.
  get firstChild() {
    return this.childNodes[0] || null;
  }
  get children() {
    return this.childNodes.filter((c) => c.tagName !== "#TEXT");
  }
  // WP DOCS-FIX-2 (follow-up to WEB-FIX-1 round 2): `textContent` follows
  // the real DOM. Only a "#text" node stores its own text (`_text`). The
  // getter joins the children's text in document order, so
  // `p.append(text, b)` reads back as the visible text; a node with no
  // children reads its own `_text` ("" for an empty element). The setter on
  // an element detaches every old child and, for a non-empty string, puts a
  // single new "#text" child in their place (an empty string leaves no
  // children), exactly as `el.textContent = "..."` does in a browser — so a
  // later `append` keeps the text: `p.textContent = "x"; p.append(b)` reads
  // back "xB".
  get textContent() {
    if (this.childNodes.length > 0) {
      return this.childNodes.map((c) => c.textContent).join("");
    }
    return this._text ?? "";
  }
  set textContent(value) {
    const text = value == null ? "" : String(value);
    if (this.tagName === "#TEXT") {
      this._text = text;
      return;
    }
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    if (text !== "") {
      const node = new FakeElement("#text");
      node._text = text;
      node._ownerDoc = this._ownerDoc;
      node.parentNode = this;
      this.childNodes = [node];
    }
  }
  setAttribute(name, value) {
    this._attrs.set(name, String(value));
  }
  getAttribute(name) {
    return this._attrs.has(name) ? this._attrs.get(name) : null;
  }
  hasAttribute(name) {
    return this._attrs.has(name);
  }
  removeAttribute(name) {
    this._attrs.delete(name);
  }
  // WP WEB-FIX-2 (connection-downloads-wiring.test.js): real `append`
  // accepts strings and inserts them as text nodes (views/downloads.js's
  // `queueHeading.append("Queue ")`). `appendChild` itself still takes
  // nodes only, as in a real DOM.
  append(...nodes) {
    for (const n of nodes) {
      if (typeof n === "string") {
        if (!this._ownerDoc) throw new Error("fake-dom.js: append(string) needs an owner document");
        this.appendChild(this._ownerDoc.createTextNode(n));
      } else {
        this.appendChild(n);
      }
    }
  }
  // WP AG-2 (game-detail-sheet-installed.test.js): spec-correct MOVE
  // semantics — `Node.appendChild` on a node that is already someone's
  // child (including this same parent's) first detaches it from its
  // current position, then appends. The previous plain `.push()` left a
  // DUPLICATE reference behind at the old index on a re-append-to-reorder
  // call, which `queryAll`'s identity-based dedup then silently hid,
  // making a real "did this actually reorder" test pass on the OLD
  // (wrong) order without ever throwing.
  appendChild(node) {
    // WP WEB-FIX-1 (settings-view-wiring.test.js): spec-correct fragment
    // semantics — appending a DocumentFragment moves its CHILDREN into this
    // node and leaves the fragment empty; the fragment itself never becomes
    // a child. views/settings.js builds every section as a fragment and
    // `els.body.append(...)`s them, so without this the section contents
    // would sit one level too deep and every `querySelector` in the test
    // would still find them — a silently-wrong tree, not a failing one.
    if (node._isFragment) {
      for (const child of node.childNodes.slice()) this.appendChild(child);
      return node;
    }
    if (node.parentNode) {
      const oldParent = node.parentNode;
      const idx = oldParent.childNodes.indexOf(node);
      if (idx !== -1) oldParent.childNodes.splice(idx, 1);
    }
    this.childNodes.push(node);
    node.parentNode = this;
    return node;
  }
  // WP WEB-FIX-1 (onboarding-wiring.test.js): onboarding.js's `staticIcon`
  // does `span.innerHTML = "<svg ...>...</svg>"; return span.firstElementChild`
  // to turn an SVG string constant into a node. This shim does not parse
  // markup; it creates ONE child element named after the FIRST tag in the
  // string (attributes and nested markup are dropped) — enough for a call
  // site that only wants "an element to append", and honest about the
  // rest. Throws on a string that does not start with a tag, or that holds
  // more than one top-level element (round 2, N1 — everything after the
  // first would otherwise be dropped silently), per this file's "throw on
  // unsupported input instead of silently no-opping" rule.
  set innerHTML(markup) {
    const text = String(markup);
    if (text === "") {
      this.replaceChildren();
      return;
    }
    const m = /^\s*<([a-zA-Z][\w-]*)/.exec(text);
    if (!m) {
      throw new Error(
        `fake-dom.js's innerHTML setter only understands markup starting with an element tag (got ${JSON.stringify(text.slice(0, 40))}). ` +
          `Extend it in web/tests/fake-dom.js if a real consumer now needs more.`,
      );
    }
    if (countTopLevelElements(text) > 1) {
      throw new Error(
        `fake-dom.js's innerHTML setter only understands ONE top-level element (got ${JSON.stringify(text.slice(0, 40))}). ` +
          `Extend it in web/tests/fake-dom.js if a real consumer now needs more.`,
      );
    }
    const child = new FakeElement(m[1]);
    child._ownerDoc = this._ownerDoc;
    this.replaceChildren(child);
  }
  // Round 2 (N2): reading markup back would need a serializer this shim
  // does not have — throw rather than return something plausible.
  get innerHTML() {
    throw new Error("fake-dom.js: reading innerHTML is unsupported. Extend web/tests/fake-dom.js if a real consumer now needs it.");
  }
  get firstElementChild() {
    return this.children[0] || null;
  }
  // WP 4e.6 (rail-panel-wiring.test.js): rail-content rendering clears and
  // rebuilds its container on every tick the same way notifications.js's
  // log list already does in production — added here rather than assuming
  // a DOM shim only needs what existed before this WP.
  replaceChildren(...nodes) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    this.append(...nodes);
  }
  contains(node) {
    let cur = node;
    while (cur) {
      if (cur === this) return true;
      cur = cur.parentNode;
    }
    return false;
  }
  // WP 4h.3 (header-art.test.js): components/header-art.js calls
  // `wrap.remove()` on an image load failure, real DOM's `Element.remove()`
  // — added here per this file's own "grows just far enough" policy.
  remove() {
    if (!this.parentNode) return;
    const idx = this.parentNode.childNodes.indexOf(this);
    if (idx !== -1) this.parentNode.childNodes.splice(idx, 1);
    this.parentNode = null;
  }
  addEventListener(type, handler) {
    if (!this._listeners.has(type)) this._listeners.set(type, new Set());
    this._listeners.get(type).add(handler);
  }
  removeEventListener(type, handler) {
    this._listeners.get(type)?.delete(handler);
  }
  dispatchEvent(event) {
    event.target = event.target || this;
    for (const handler of this._listeners.get(event.type) || []) handler(event);
    return !event.defaultPrevented;
  }
  focus() {
    if (this._ownerDoc) this._ownerDoc.activeElement = this;
  }
  // WP AG-2: real (if minimal) implementations — see the module-level
  // selector-engine comment above this class for exactly what grammar they
  // support and why the previous `return null` stub was insufficient.
  querySelector(selector) {
    return queryAll(this, selector)[0] || null;
  }
  querySelectorAll(selector) {
    return queryAll(this, selector);
  }
}

/** Creates a fresh, isolated `{document, window}` pair. Each test that needs
 * one calls this itself (rather than sharing a module-level singleton) so
 * tests cannot leak DOM state into each other — `resetModalStack()` still
 * needs calling between cases for `lib/modal-stack.js`'s own module-level
 * stack, since THAT state is shared regardless of which fake document a
 * test built. */
export function createFakeDom() {
  const bodyListeners = new Map();
  const docListeners = new Map();

  const body = new FakeElement("body");
  const appRoot = new FakeElement("div");
  appRoot.id = "app";
  body.appendChild(appRoot);

  const document = {
    body,
    activeElement: body,
    getElementById(id) {
      return id === "app" ? appRoot : null;
    },
    createElement(tag) {
      const el = new FakeElement(tag);
      el._ownerDoc = document;
      return el;
    },
    createElementNS(_ns, tag) {
      return document.createElement(tag);
    },
    // WP WEB-FIX-1 (settings-view-wiring.test.js / onboarding-wiring.test.js):
    // views/settings.js builds each section as a DocumentFragment;
    // onboarding.js's step 2 appends a text node. Both are real DOM APIs
    // this shim never needed before. A fragment is a FakeElement flagged
    // `_isFragment` so `appendChild` above can apply move-the-children
    // semantics; a text node is a FakeElement tagged "#text" carrying
    // `textContent` — it participates in the tree (parentNode/childNodes)
    // but, as in a real DOM, never appears in `children` (round 2, N2) and
    // can never match a tag or class selector.
    createDocumentFragment() {
      const frag = new FakeElement("#document-fragment");
      frag._isFragment = true;
      frag._ownerDoc = document;
      return frag;
    },
    createTextNode(text) {
      const node = new FakeElement("#text");
      node.textContent = String(text);
      node._ownerDoc = document;
      return node;
    },
    addEventListener(type, handler) {
      if (!docListeners.has(type)) docListeners.set(type, new Set());
      docListeners.get(type).add(handler);
    },
    removeEventListener(type, handler) {
      docListeners.get(type)?.delete(handler);
    },
    dispatchEvent(event) {
      event.target = event.target || document;
      for (const handler of docListeners.get(event.type) || []) handler(event);
      return !event.defaultPrevented;
    },
  };
  body._ownerDoc = document;
  appRoot._ownerDoc = document;

  let pathname = "/library";
  const windowListeners = new Map();
  const window = {
    location: {
      get pathname() {
        return pathname;
      },
    },
    history: {
      pushState(_state, _title, path) {
        pathname = path;
      },
    },
    addEventListener(type, handler) {
      if (!windowListeners.has(type)) windowListeners.set(type, new Set());
      windowListeners.get(type).add(handler);
    },
    removeEventListener(type, handler) {
      windowListeners.get(type)?.delete(handler);
    },
  };

  return { document, window, appRoot, FakeElement };
}

/** A minimal `KeyboardEvent`-shaped object good enough for the `keydown`
 * handlers under test (`event.key`, `event.preventDefault()`,
 * `event.defaultPrevented`, `event.target`). */
export function fakeKeyEvent(key) {
  let prevented = false;
  return {
    type: "keydown",
    key,
    preventDefault() {
      prevented = true;
    },
    get defaultPrevented() {
      return prevented;
    },
  };
}

/** A minimal `MouseEvent`-shaped `click` object. */
export function fakeClickEvent(target) {
  return { type: "click", target };
}
