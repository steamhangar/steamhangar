/**
 * WP CAP-503-FIX-1b: the client-priority texts of the Downloads view
 * (`web/js/lib/client-priority.js`), one test per state: held (devices
 * seen), queued while holding, override, restored (no sighting claimed),
 * manual (no text, the view keeps its own note). Expected texts are string
 * literals, never built from WORDING (a mutated constant must fail here).
 * The Android twin pins the same literals (`ClientPriorityWebTwinTest`).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  WORDING,
  CLIENT_PRIORITY_POLL_MS,
  clientHoldNote,
  clientOverrideNote,
  clientPausingNote,
  holdDevices,
  holdResumeClock,
  joinDevices,
  queueHoldNote,
} from "../js/lib/client-priority.js";

const RESUME = "2026-11-21T10:02:00Z";
// Local wall clock of RESUME, computed independently of the module.
const d = new Date(Date.parse(RESUME));
const CLOCK = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

const holding = (over = {}) => ({
  enabled: true,
  holding: true,
  since: "2026-11-21T10:00:00Z",
  last_client_at: "2026-11-21T10:00:00Z",
  resume_at: RESUME,
  idle_gap_seconds: 120,
  clients: [{ addr: "10.0.0.21", name: "Desk PC", miss_bytes_per_second: 9_000_000 }],
  paused_job_ids: [1],
  override_job_ids: [],
  restored: false,
  runner_address_unknown: false,
  ...over,
});
const idle = () => holding({ holding: false, since: null, resume_at: null, clients: [], paused_job_ids: [] });
const restored = () => holding({ restored: true, clients: [] });
const job = (id, status, over = {}) => ({ id, appid: 10 + id, type: "prefill", status, stop_request: null, pause_reason: null, client_override: false, ...over });

test("poll cadence is the controller's 5 s", () => {
  assert.equal(CLIENT_PRIORITY_POLL_MS, 5000);
});

test("joinDevices: one, two, three names", () => {
  assert.equal(joinDevices([]), "");
  assert.equal(joinDevices(["A"]), "A");
  assert.equal(joinDevices(["A", "B"]), "A and B");
  assert.equal(joinDevices(["A", "B", "C"]), "A, B and C");
});

test("holdDevices: the Clients page name, else the address; none while restored or idle", () => {
  assert.equal(holdDevices(holding()), "Desk PC");
  assert.equal(
    holdDevices(holding({ clients: [{ addr: "10.0.0.21", name: "Desk PC" }, { addr: "10.0.0.22", name: null }, { addr: "10.0.0.23", name: "  " }] })),
    "Desk PC, 10.0.0.22 and 10.0.0.23",
  );
  assert.equal(holdDevices(restored()), null);
  assert.equal(holdDevices(idle()), null);
  assert.equal(holdDevices(null), null);
});

test("holdResumeClock: local HH:MM of resume_at, null without a hold or a time", () => {
  assert.equal(holdResumeClock(holding()), CLOCK);
  assert.match(holdResumeClock(holding()), /^\d\d:\d\d$/);
  assert.equal(holdResumeClock(idle()), null);
  assert.equal(holdResumeClock(holding({ resume_at: null })), null);
  assert.equal(holdResumeClock(holding({ resume_at: "garbage" })), null);
});

test("MUTATION PIN: held (client-paused, device seen) names the device and the resume time", () => {
  assert.equal(
    clientHoldNote(job(1, "paused", { pause_reason: "client" }), holding()),
    `Paused to give Desk PC the full speed through the cache. Resumes by itself at about ${CLOCK} unless a device keeps loading. Resume runs it now, alongside Desk PC.`,
  );
  assert.equal(
    clientHoldNote(job(1, "paused", { pause_reason: "client" }), holding({ clients: [{ addr: "10.0.0.21", name: "Desk PC" }, { addr: "10.0.0.30", name: "Deck" }] })),
    `Paused to give Desk PC and Deck the full speed through the cache. Resumes by itself at about ${CLOCK} unless a device keeps loading. Resume runs it now, alongside Desk PC and Deck.`,
  );
});

test("MUTATION PIN: restored names no device and claims no sighting", () => {
  const text = clientHoldNote(job(1, "paused", { pause_reason: "client" }), restored());
  assert.equal(
    text,
    `Paused for device priority before vault-api restarted. No device has been seen loading since; resumes by itself at about ${CLOCK} unless one starts.`,
  );
  // Even with a stale client in the answer, a restored hold names nobody.
  assert.equal(clientHoldNote(job(1, "paused", { pause_reason: "client" }), holding({ restored: true })), text);
  assert.doesNotMatch(text, /Desk PC|to give/);
});

test("client-paused without a current hold (answer not in yet, hold just ended): no device, no time", () => {
  const pending = "Paused so a device loading through the cache gets the full speed. Resumes by itself once no device is loading.";
  assert.equal(clientHoldNote(job(1, "paused", { pause_reason: "client" }), null), pending);
  assert.equal(clientHoldNote(job(1, "paused", { pause_reason: "client" }), idle()), pending);
  assert.equal(clientHoldNote(job(1, "paused", { pause_reason: "client" }), holding({ clients: [] })), pending);
});

test("MUTATION PIN: a manual pause (pause_reason manual or null) gets no client text, also during a hold", () => {
  assert.equal(clientHoldNote(job(1, "paused", { pause_reason: "manual" }), holding()), null);
  assert.equal(clientHoldNote(job(1, "paused", { pause_reason: null }), holding()), null);
  assert.equal(clientHoldNote(job(1, "paused", { pause_reason: undefined }), holding()), null);
  assert.equal(clientHoldNote(job(1, "running", { pause_reason: "client" }), holding()), null, "not paused yet");
});

test("pausing for a device: its own stop note; a manual pause request keeps the view's", () => {
  assert.equal(
    clientPausingNote(job(1, "running", { stop_request: "pause", pause_reason: "client" })),
    "Pausing so a device loading through the cache gets the full speed. It resumes by itself, from the cache, not from zero.",
  );
  assert.equal(clientPausingNote(job(1, "running", { stop_request: "pause", pause_reason: "manual" })), null);
  assert.equal(clientPausingNote(job(1, "running", { stop_request: "cancel", pause_reason: "client" })), null);
});

test("MUTATION PIN: override reads 'Runs alongside <device> by your choice.' only while holding", () => {
  const over = job(2, "running", { client_override: true });
  assert.equal(clientOverrideNote(over, holding()), "Runs alongside Desk PC by your choice.");
  assert.equal(clientOverrideNote(job(2, "queued", { client_override: true }), holding()), "Runs alongside Desk PC by your choice.");
  assert.equal(clientOverrideNote(over, restored()), "Runs alongside any loading device by your choice.");
  assert.equal(clientOverrideNote(over, idle()), null, "no hold: nothing runs alongside anything");
  assert.equal(clientOverrideNote(over, null), null);
  assert.equal(clientOverrideNote(job(2, "running"), holding()), null, "no override");
});

test("MUTATION PIN: queued while holding names the device and the time; override-only queues get none", () => {
  const queued = [job(3, "queued"), job(4, "queued", { type: "gc" })];
  assert.equal(
    queueHoldNote(queued, holding()),
    `Queued downloads wait to give Desk PC the full speed through the cache. They carry on at about ${CLOCK} unless a device keeps loading.`,
  );
  assert.equal(
    queueHoldNote(queued, restored()),
    `Queued downloads wait: device priority was on before vault-api restarted, and no device has been seen loading since. They carry on at about ${CLOCK} unless one starts.`,
  );
  assert.equal(queueHoldNote(queued, holding({ resume_at: null })), "Queued downloads wait so a device loading through the cache gets the full speed.");
  assert.equal(queueHoldNote(queued, idle()), null);
  assert.equal(queueHoldNote([], holding()), null);
  assert.equal(queueHoldNote([job(5, "queued", { client_override: true })], holding()), null);
});

test("every WORDING literal is used by a test above (no untested text)", () => {
  assert.deepEqual(Object.keys(WORDING).sort(), [
    "heldPending",
    "heldRestored",
    "heldSeen",
    "overrideAny",
    "overrideSeen",
    "pausingClient",
    "queuePending",
    "queueRestored",
    "queueSeen",
  ]);
});
