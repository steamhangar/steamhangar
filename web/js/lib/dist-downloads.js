/**
 * Where the "Add a device" sheet downloads the app and the agent from
 * (WP DIST-1b; user decisions APP-DIST-1 / AGENT-DIST-1, 2026-10-14).
 *
 * Input: the `GET /v1/dist` index (api/README.md "Release downloads",
 * contract of WP DIST-1a): `{version, release, local_base, release_base,
 * apk_alias_url, artifacts: [{id, name, local, size, sha256, local_url,
 * release_url, url, source}]}`. A file baked into the vault-api image has
 * `local: true` and a `local_url` RELATIVE to the vault origin; a file that
 * is not baked has the GitHub Release asset of the same version as
 * `release_url` when the server version is a published release; a dev
 * server has neither (`url: null`).
 *
 * Rules:
 *  - The app: a baked APK is offered at `<page origin>` + the version-free
 *    alias (`apk_alias_url`, `/dist/steamhangar-app.apk`), the address a
 *    phone in the LAN can open. Otherwise the release asset; otherwise an
 *    honest note, never a dead button.
 *  - The agent: ALL files a command needs must come from one place. Every
 *    one baked -> `<agent server URL>/dist` (the PC must reach vault-api
 *    directly anyway, and that address skips a reverse proxy's
 *    forward-auth); otherwise, on a release server, the GitHub Release of
 *    the same version; otherwise no command.
 *  - Only what the index can prove is used: a local path must start with
 *    `local_base` (`/dist/`) and stay on the vault origin, a release URL
 *    must be the project's own GitHub Release of exactly this version, and
 *    a file must carry the name the command expects. Anything else counts
 *    as "not available" (fail closed), not as a link to wherever the
 *    answer points.
 *
 * Pure: no DOM, no storage, no fetch.
 */

import { linuxReleaseAssets, releaseAssets, releaseDownloadBase } from "./agent-install.js";

export const APP_ARTIFACT_ID = "app-android";
/** The project's releases page (the note's link when nothing is offered). */
export const RELEASES_PAGE_URL = "https://github.com/steamhangar/steamhangar/releases";

/** The files the Windows command fetches, with the names it uses. */
export function windowsArtifactNames(version) {
  const a = releaseAssets(version);
  return {
    "agent-windows-amd64": a.exe,
    "install-task": "install-task.ps1",
    "run-vault-agent": "run-vault-agent.ps1",
    "uninstall-task": "uninstall-task.ps1",
    sha256sums: a.sums,
  };
}

/** The files the Linux command fetches, with the names it uses. */
export function linuxArtifactNames(version) {
  const a = linuxReleaseAssets(version);
  return {
    "agent-linux-amd64": a.amd64,
    "agent-linux-arm64": a.arm64,
    "systemd-service": "vault-agent-report.service",
    "systemd-timer": "vault-agent-report.timer",
    sha256sums: a.sums,
  };
}

const SAFE_PATH = /^\/[A-Za-z0-9._\-/]+$/;

/**
 * The index if it has the fields this module reads, else null.
 * @param {unknown} response
 */
export function readDistIndex(response) {
  if (!response || typeof response !== "object") return null;
  const r = /** @type {Record<string, unknown>} */ (response);
  if (typeof r.version !== "string" || !r.version) return null;
  if (typeof r.release !== "boolean") return null;
  if (typeof r.local_base !== "string" || !/^\/[a-z]+\/$/.test(r.local_base)) return null;
  if (!Array.isArray(r.artifacts)) return null;
  return r;
}

function findArtifact(index, id) {
  const art = index.artifacts.find((a) => a && typeof a === "object" && a.id === id);
  return art || null;
}

/** Path of a baked file, relative to the vault origin, or null. */
function localPath(index, art, path) {
  if (!art || art.local !== true) return null;
  if (typeof path !== "string" || !SAFE_PATH.test(path) || path.includes("//") || path.includes("..")) return null;
  if (!path.startsWith(index.local_base)) return null;
  return path;
}

/** The release URL of a file, if it is the project's release of this version. */
function releaseUrl(index, art) {
  if (!art || index.release !== true || typeof art.release_url !== "string") return null;
  return art.release_url === `${releaseDownloadBase(index.version)}/${art.name}` ? art.release_url : null;
}

/**
 * The app download.
 * @param {unknown} response  the `GET /v1/dist` answer
 * @param {string} origin     the page origin (`window.location.origin`)
 * @returns {{kind: "download", source: "local" | "release", url: string, name: string, size: number | null, version: string}
 *          | {kind: "none", version: string | null}}
 */
export function appDownloadPlan(response, origin) {
  const index = readDistIndex(response);
  if (!index) return { kind: "none", version: null };
  const art = findArtifact(index, APP_ARTIFACT_ID);
  if (!art || typeof art.name !== "string") return { kind: "none", version: index.version };
  const size = typeof art.size === "number" ? art.size : null;
  const local = localPath(index, art, index.apk_alias_url) || localPath(index, art, art.local_url);
  if (local) return { kind: "download", source: "local", url: `${origin}${local}`, name: art.name, size, version: index.version };
  const release = releaseUrl(index, art);
  if (release) return { kind: "download", source: "release", url: release, name: art.name, size: null, version: index.version };
  return { kind: "none", version: index.version };
}

/** The note shown instead of the "Download app" button. */
export function noAppText(version) {
  const what = version ? `version "${version}"` : "a version this page cannot read";
  return (
    `This server reports ${what} and has no app file to offer (it is not a published release). ` +
    "Get the app from a released SteamHangar version."
  );
}

/**
 * Where an agent command downloads from.
 * @param {unknown} response   the `GET /v1/dist` answer
 * @param {string} serverUrl   the validated agent server URL (an origin)
 * @param {(version: string) => Record<string, string>} namesFor
 *   {@link windowsArtifactNames} or {@link linuxArtifactNames}
 * @returns {{ok: true, source: "local" | "release", base: string, version: string}
 *          | {ok: false, version: string | null}}
 */
export function agentDownloadPlan(response, serverUrl, namesFor) {
  const index = readDistIndex(response);
  if (!index) return { ok: false, version: null };
  const names = namesFor(index.version);
  const arts = Object.entries(names).map(([id, name]) => {
    const art = findArtifact(index, id);
    return art && art.name === name ? art : null;
  });
  if (arts.some((a) => a === null)) return { ok: false, version: index.version };
  const base = index.local_base.replace(/\/$/, "");
  if (base && arts.every((a) => localPath(index, a, a.local_url) === `${base}/${a.name}`)) {
    return { ok: true, source: "local", base: `${serverUrl}${base}`, version: index.version };
  }
  if (arts.every((a) => releaseUrl(index, a) !== null)) {
    return { ok: true, source: "release", base: releaseDownloadBase(index.version), version: index.version };
  }
  return { ok: false, version: index.version };
}

/** Human size for the download tooltip ("9.3 MB"). */
export function formatDownloadSize(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes < 1_000_000) return `${Math.max(1, Math.round(bytes / 1000))} kB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}
