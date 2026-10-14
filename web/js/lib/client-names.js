/**
 * Client names (WP D15-NAMES): validation and wording for the Clients
 * page's Rename action. Pure: no DOM, no fetch. Unit-tested in
 * web/tests/client-names.test.js.
 *
 * The server owns the rule (api/vault_api/client_names.py): a name is
 * stored per ADDRESS (`PUT /v1/client-names/ip/{addr}`), every client row
 * carries `name` (operator-set or null), `name_addr` (the address a rename
 * writes to, null = cannot be named) and `display_name` (the name when set,
 * else the agent's PC name, else the address). This module never recomputes
 * which name wins; it reads `display_name`.
 *
 * `normalizeClientName` mirrors the server's validation so the dialog can
 * say what is wrong before a round trip; the server stays the authority
 * (a 422 from it is shown as is).
 */

import { ERROR_KINDS } from "../errors.js";

/** Longest name in characters (code points) after trimming; the server's
 * `client_names.MAX_NAME_LENGTH`. */
export const MAX_CLIENT_NAME_LENGTH = 40;

// Control (Cc), format (Cf: zero-width, bidi), surrogate, private use,
// unassigned, line and paragraph separators: the server's
// `_FORBIDDEN_CATEGORIES`.
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/u;

/**
 * @param {unknown} raw
 * @returns {{ok: true, value: string} | {ok: false, error: string}}
 */
export function normalizeClientName(raw) {
  if (typeof raw !== "string") return { ok: false, error: "Enter a name." };
  const value = raw.trim();
  if (!value) return { ok: false, error: "Enter a name." };
  if ([...value].length > MAX_CLIENT_NAME_LENGTH) {
    return { ok: false, error: `Use at most ${MAX_CLIENT_NAME_LENGTH} characters.` };
  }
  if (FORBIDDEN.test(value)) return { ok: false, error: "Remove line breaks, tabs and invisible characters." };
  return { ok: true, value };
}

/** True when the server gave this client an address to store a name under. */
export function canRename(client) {
  return !!client && typeof client.name_addr === "string" && client.name_addr !== "";
}

/** True when the client has an operator-set name. */
export function hasCustomName(client) {
  return !!client && typeof client.name === "string" && client.name !== "";
}

/** Dialog title. */
export function renameTitle(displayName) {
  return `Rename ${displayName}`;
}

/** What the name is stored for, under the field. */
export function renameHint(client) {
  const base = `Stored for the address ${client.name_addr}: a machine that gets this address later shows this name too.`;
  if (client && client.kind === "agent" && typeof client.client_id === "string" && client.client_id) {
    return `${base} vault-agent keeps reporting this PC as ${client.client_id}; the name you set is shown instead.`;
  }
  return base;
}

/** Toasts. */
export function renamedToastText(name) {
  return `Renamed to ${name}.`;
}
export function nameRemovedToastText(fallback) {
  return `Name removed. Showing ${fallback} again.`;
}

/** The server's own 404 for "no name stored": clearing it already happened. */
export function isNameAlreadyGone(err) {
  return !!err && err.kind === ERROR_KINDS.NOT_FOUND && typeof err.detail === "string" && err.detail.startsWith("No name stored");
}

/** Error line for a failed save or remove. A 404 that is not the handler's
 * own answer means the route does not exist: a server older than
 * D15-NAMES. */
export function renameErrorText(err) {
  if (err && err.kind === ERROR_KINDS.NOT_FOUND && !isNameAlreadyGone(err)) {
    return "This server cannot store client names yet. Update vault-api to rename clients.";
  }
  const detail = err && typeof err.detail === "string" && err.detail ? err.detail : (err && err.message) || "Request failed.";
  return `Could not save the name: ${detail}`;
}
