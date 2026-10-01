/**
 * Self-tests for the shared DOM shim (WP WEB-FIX-1 round 2, N1/N2): the
 * shim must throw on input it cannot represent instead of silently
 * producing a wrong tree, and keep text nodes out of `children`. WP
 * DOCS-FIX-2 adds the real-DOM `textContent` getter/setter pair.
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

test("textContent getter joins child text when childNodes is non-empty", () => {
  const p = document.createElement("p");
  const b = document.createElement("b");
  b.textContent = "bold";
  p.append(document.createTextNode("hello "), b, document.createTextNode("!"));
  assert.equal(p.textContent, "hello bold!");
  // An element with no children and no text reads as "", never undefined.
  assert.equal(document.createElement("i").textContent, "");
});

test("textContent setter replaces the children with one #text node and detaches the old ones", () => {
  const p = document.createElement("p");
  const b = document.createElement("b");
  p.append(document.createTextNode("old "), b);
  p.textContent = "new";
  assert.equal(p.childNodes.length, 1);
  assert.equal(p.firstChild.tagName, "#TEXT");
  assert.equal(p.firstChild.parentNode, p);
  assert.deepEqual(p.children, []);
  assert.equal(b.parentNode, null);
  assert.equal(p.querySelector("b"), null);
  assert.equal(p.textContent, "new");
});

test("textContent setter with an empty string leaves no children", () => {
  const p = document.createElement("p");
  p.append(document.createTextNode("old"));
  p.textContent = "";
  assert.equal(p.childNodes.length, 0);
  assert.equal(p.textContent, "");
});

test("textContent set, then append, keeps the text in document order", () => {
  const p = document.createElement("p");
  const b = document.createElement("b");
  b.textContent = "B";
  p.textContent = "x";
  p.append(b);
  assert.equal(p.textContent, "xB");
  assert.equal(p.childNodes.length, 2);
});

test("append(string) inserts a text node, as in a real DOM (WP WEB-FIX-2)", () => {
  const h = document.createElement("h4");
  const n = document.createElement("span");
  n.textContent = "3";
  h.append("Queue ", n);
  assert.equal(h.textContent, "Queue 3");
  assert.equal(h.childNodes.length, 2);
  assert.equal(h.children.length, 1, "the text node is not an element child");
});

test("bare [attr] presence selector (WP WEB-FEAT-1): matches by presence, value-less; other operators still throw", () => {
  const grid = document.createElement("div");
  const a = document.createElement("div");
  a.className = "card";
  a.dataset.appid = "10";
  const b = document.createElement("div");
  b.className = "card"; // no data-appid
  grid.append(a, b);
  assert.equal(grid.querySelectorAll(".card[data-appid]").length, 1);
  assert.equal(grid.querySelector(".card[data-appid]").dataset.appid, "10");
  assert.equal(grid.querySelectorAll('.card[data-appid="10"]').length, 1, "the exact-match form is unchanged");
  assert.equal(grid.querySelectorAll('.card[data-appid="11"]').length, 0);
  assert.throws(() => grid.querySelectorAll('.card[data-appid^="1"]'), /does not support/);
});
