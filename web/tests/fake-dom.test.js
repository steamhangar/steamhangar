/**
 * Self-tests for the shared DOM shim (WP WEB-FIX-1 round 2, N1/N2): the
 * shim must throw on input it cannot represent instead of silently
 * producing a wrong tree, and keep text nodes out of `children`.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFakeDom } from "./fake-dom.js";

const { document } = createFakeDom();

test("N1 — innerHTML setter accepts ONE top-level element (nested and self-closing children are fine)", () => {
  const span = document.createElement("span");
  span.innerHTML = '<svg viewBox="0 0 1 1"><path d="M0 0"/><g><circle r="1"></circle></g></svg>';
  assert.equal(span.children.length, 1);
  assert.equal(span.firstElementChild.tagName, "SVG");
});

test("N1 — innerHTML setter throws on more than one top-level element", () => {
  const span = document.createElement("span");
  assert.throws(() => (span.innerHTML = "<b></b><i></i>"), /ONE top-level element/);
  assert.throws(() => (span.innerHTML = "<br/><br/>"), /ONE top-level element/);
});

test("N2 — reading innerHTML throws 'unsupported'", () => {
  const span = document.createElement("span");
  assert.throws(() => span.innerHTML, /unsupported/);
});

test("N2 — text nodes live in childNodes, never in children", () => {
  const p = document.createElement("p");
  const b = document.createElement("b");
  p.append(document.createTextNode("hello "), b, document.createTextNode("!"));
  assert.equal(p.childNodes.length, 3);
  assert.deepEqual(p.children, [b]);
  assert.equal(p.firstElementChild, b);
  assert.equal(p.firstChild.tagName, "#TEXT");
  assert.equal(p.querySelector("b"), b);
});
