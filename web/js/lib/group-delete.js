/**
 * Grouped delete: a game together with its content packages (WP
 * TOOL-APPS-2c; ADR-0022 §4, api/README.md "Grouped delete").
 *
 * The two-step server contract, so the user is always shown every app a
 * grouped delete removes:
 *   1. `GET /v1/cache/{appid}/delete-plan?with_packages=true` — the members
 *      (parent + packages the SERVER chose), the depots deleted with their
 *      bytes, the depots kept because an app outside the set still uses
 *      them, children kept because they are not content packages, and
 *      in-flight jobs (`blocked_by`) that would make the delete a 409.
 *   2. `DELETE /v1/cache/{appid}?with_packages=true&members=<the plan's
 *      member ids>` — 409 with `{server_members, confirmed_members}` when the
 *      server's set changed since the preview: nothing was deleted, the
 *      plan is fetched again and confirmed again.
 *
 * Pure: wording and decisions only. The dialogs (views/library.js,
 * components/game-detail-sheet.js) render through
 * components/group-delete-plan.js.
 */

/** The app ids to confirm, exactly the plan's members (parent included). */
export function planMemberIds(plan) {
  return (Array.isArray(plan?.members) ? plan.members : []).map((m) => m.appid);
}

/** True for the 409 "members differ" answer (fetch the plan again). */
export function isMembersMismatch(err) {
  const d = err?.detail;
  return err?.status === 409 && !!d && typeof d === "object" && Array.isArray(d.server_members);
}

/** True when an in-flight job of a member makes the delete a 409. */
export function isPlanBlocked(plan) {
  return Array.isArray(plan?.blocked_by) && plan.blocked_by.length > 0;
}

export const MEMBERS_CHANGED_NOTICE =
  "The list of content packages changed since this preview. Nothing was deleted — check the list below and confirm again.";
export const BLOCKED_HEADING = "Not possible right now";

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** "Starfall Legion and its 2 content packages" / "Starfall Legion". */
export function groupSubject(parentName, packageCount) {
  return packageCount > 0
    ? `${parentName} and its ${plural(packageCount, "content package", "content packages")}`
    : parentName;
}

const KEPT_CHILD_REASON = Object.freeze({
  not_a_package_type: "not a content package",
  type_unknown: "type unknown",
  type_only_inferred: "type not confirmed by Steam",
});

/** Why a child of the game stays: the server's reason as a phrase. */
export function keptChildReason(reason) {
  return KEPT_CHILD_REASON[reason] || "not a content package";
}

/** "Download running" / "Cleanup queued" for a `blocked_by` entry. */
export function blockingJobText(job) {
  const what = job.type === "gc" ? "Cleanup" : "Download";
  return `${what} ${job.status}`;
}

/**
 * The plan in display terms.
 * @param {object} plan `DeletePlanOut`
 * @param {(appid: number, serverName?: string | null) => string} nameFor
 */
export function describeGroupPlan(plan, nameFor) {
  const members = (plan.members || []).map((m) => ({
    appid: m.appid,
    role: m.role,
    name: nameFor(m.appid, m.name),
  }));
  const parent = members.find((m) => m.role === "parent") || { appid: plan.appid, name: nameFor(plan.appid) };
  const packages = members.filter((m) => m.role !== "parent");
  return {
    subject: groupSubject(parent.name, packages.length),
    parent,
    packages,
    freedBytes: typeof plan.total_bytes === "number" ? plan.total_bytes : 0,
    keptDepots: (plan.skipped_shared || []).map((s) => ({
      depotid: s.depotid,
      usedBy: (s.shared_with || []).map((id) => nameFor(id)),
    })),
    keptChildren: (plan.kept_children || []).map((c) => ({
      appid: c.appid,
      name: nameFor(c.appid, c.name),
      reason: keptChildReason(c.reason),
    })),
    blocked: (plan.blocked_by || []).map((j) => ({ appid: j.appid, name: nameFor(j.appid), text: blockingJobText(j) })),
  };
}
