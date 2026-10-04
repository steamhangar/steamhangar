/**
 * web/js/lib/pair-link.js (WP PAIR-1): the Android pairing URI contract,
 * the browser pairing link, reading `#pair=` back, and the intake decision.
 *
 * The URI shape is shared with the Android package; the expected strings
 * below are LITERALS on purpose (LEARNINGS "security-constant pins must
 * assert string literals"), so a change to the module's own constants
 * cannot keep these green.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  apiBaseUrl,
  buildAppPairUri,
  buildBrowserPairLink,
  encodePairComponent,
  isAppPairableKey,
  isUsableKey,
  pairIntakeAction,
  readPairFragment,
  urlWithoutFragment,
} from "../js/lib/pair-link.js";

test("MUTATION TARGET: the Android URI is exactly steamhangar://pair?v=1&url=<enc>&key=<enc>", () => {
  assert.equal(
    buildAppPairUri("https://hangar.example.org", "abcDEF123"),
    "steamhangar://pair?v=1&url=https%3A%2F%2Fhangar.example.org&key=abcDEF123",
  );
  assert.equal(
    buildAppPairUri("http://192.0.2.10:8080", "k"),
    "steamhangar://pair?v=1&url=http%3A%2F%2F192.0.2.10%3A8080&key=k",
  );
});

test("MUTATION TARGET: special characters are encoded like encodeURIComponent; never a raw '+'", () => {
  // Agreed with the Android side (APP-PAIR-1): encodeURIComponent for both
  // values; the app decodes a raw "+" as a space, so "+" must be %2B.
  const key = "a+b/c=d&e?f#g%h i'j(k)l*m!n~o.p_q-r";
  assert.equal(encodePairComponent(key), "a%2Bb%2Fc%3Dd%26e%3Ff%23g%25h%20i'j(k)l*m!n~o.p_q-r");
  assert.equal(encodePairComponent("ü€"), "%C3%BC%E2%82%AC", "UTF-8 bytes, upper-case hex");
  const uri = buildAppPairUri("https://h.example:8443", key);
  assert.equal(uri, "steamhangar://pair?v=1&url=https%3A%2F%2Fh.example%3A8443&key=a%2Bb%2Fc%3Dd%26e%3Ff%23g%25h%20i'j(k)l*m!n~o.p_q-r");
  assert.equal(uri.includes("+"), false, "no raw plus anywhere");
});

test("MUTATION TARGET: url is scheme://host[:port] only — path, query, fragment and trailing slash dropped", () => {
  assert.equal(buildAppPairUri("https://hangar.example.org/", "k"), "steamhangar://pair?v=1&url=https%3A%2F%2Fhangar.example.org&key=k");
  assert.equal(
    buildAppPairUri("https://hangar.example.org:8443/settings?x=1#y", "k"),
    "steamhangar://pair?v=1&url=https%3A%2F%2Fhangar.example.org%3A8443&key=k",
  );
});

test("the URI parses back to the same url and key with a standard query parser", () => {
  const key = "x+y&z=1 %41/é";
  const base = "https://hangar.example.org:8443";
  const uri = buildAppPairUri(base, key);
  const parsed = new URL(uri);
  assert.equal(parsed.protocol, "steamhangar:");
  assert.equal(parsed.searchParams.get("v"), "1");
  assert.equal(parsed.searchParams.get("url"), base);
  assert.equal(parsed.searchParams.get("key"), key);
  assert.deepEqual([...parsed.searchParams.keys()], ["v", "url", "key"], "exactly these three, in this order");
});

test("base URL is the page origin without a trailing slash", () => {
  assert.equal(apiBaseUrl({ origin: "https://hangar.example.org" }), "https://hangar.example.org");
  assert.equal(apiBaseUrl({ origin: "http://192.0.2.10:8080/" }), "http://192.0.2.10:8080");
});

test("MUTATION TARGET: browser link carries the key in the fragment only", () => {
  assert.equal(buildBrowserPairLink("https://hangar.example.org", "k+1/2"), "https://hangar.example.org/#pair=k%2B1%2F2");
  const u = new URL(buildBrowserPairLink("https://hangar.example.org/", "secret"));
  assert.equal(u.pathname, "/");
  assert.equal(u.search, "", "no query string: nothing of the key reaches a server");
  assert.equal(u.hash, "#pair=secret");
});

test("readPairFragment: round trip, absent, damaged, empty", () => {
  for (const key of ["abc", "a+b/c=d&e?f#g%h i", "ü€", "x".repeat(200)]) {
    const link = buildBrowserPairLink("https://h.example", key);
    assert.deepEqual(readPairFragment(new URL(link).hash), { key });
  }
  assert.equal(readPairFragment(""), null);
  assert.equal(readPairFragment("#"), null);
  assert.equal(readPairFragment("#section-2"), null);
  assert.equal(readPairFragment("#pairing=x"), null, "only the exact parameter name");
  assert.deepEqual(readPairFragment("#foo=1&pair=k2"), { key: "k2" });
  assert.match(readPairFragment("#pair=%E0%A4%A").error, /damaged/);
  assert.match(readPairFragment("#pair=").error, /no usable/);
  assert.match(readPairFragment("#pair").error, /no usable/);
  assert.match(readPairFragment("#pair=a%0Ab").error, /no usable/, "control characters are refused");
});

test("isUsableKey: non-empty string without control characters", () => {
  assert.equal(isUsableKey("abc"), true);
  assert.equal(isUsableKey(""), false);
  assert.equal(isUsableKey(null), false);
  assert.equal(isUsableKey("a\tb"), false);
  assert.equal(isUsableKey("a\u007fb"), false);
  assert.equal(isUsableKey("a\ud800b"), false, "a lone surrogate cannot be percent-encoded");
});

test("MUTATION TARGET: the app's key rule — printable ASCII, no space at either end", () => {
  for (const ok of ["a", "abc", "a b", "!~", "s3cr3t+key/&=(x) ~!", "x".repeat(200)]) {
    assert.equal(isAppPairableKey(ok), true, JSON.stringify(ok));
  }
  for (const bad of ["", " ", " a", "a ", "schlüssel", "a\tb", "a\u007fb", " a", null, 42]) {
    assert.equal(isAppPairableKey(bad), false, JSON.stringify(bad));
  }
});

test("urlWithoutFragment keeps path and query", () => {
  assert.equal(urlWithoutFragment({ pathname: "/", search: "" }), "/");
  assert.equal(urlWithoutFragment({ pathname: "/settings", search: "?x=1" }), "/settings?x=1");
});

test("MUTATION TARGET: a different stored key is never replaced without asking", () => {
  assert.equal(pairIntakeAction({ candidate: "new", storedKey: "old", demoMode: false }), "confirm-replace");
  assert.equal(pairIntakeAction({ candidate: "new", storedKey: "old", demoMode: true }), "confirm-replace");
  assert.equal(pairIntakeAction({ candidate: "same", storedKey: "same", demoMode: false }), "same");
  assert.equal(pairIntakeAction({ candidate: "same", storedKey: "same", demoMode: true }), "verify", "demo mode still needs to be left");
  assert.equal(pairIntakeAction({ candidate: "new", storedKey: "", demoMode: false }), "verify");
  assert.equal(pairIntakeAction({ candidate: "new", storedKey: "", demoMode: true }), "verify");
});
