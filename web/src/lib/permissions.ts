import type { RoleToken } from "../api/types";

export type WriteAction =
  | "createSource"
  | "patchSource"
  | "deleteSource"
  | "purgeSource"
  | "createDestination"
  | "patchDestination"
  | "deleteDestination"
  | "purgeDestination"
  | "testConnection"
  | "cancelRun"
  | "manifestRewrite"
  | "runMaintenance"
  | "triggerScan"
  | "assignPreset"
  | "approveLayout"
  | "dismissLayout"
  | "executeLayout"
  | "protectSnapshot"
  | "unprotectSnapshot"
  | "scheduleMaintenance"
  | "silentLossDryRun"
  | "applyProperty"
  | "enableBloom"
  | "runPropertyCompaction"
  | "qualityConstraint"
  | "wairAnalyze"
  | "patchTable"
  | "editCompliance"
  | "dismissDarkTable"
  | "refreshAnomalies"
  | "runDedup";

type MinRole = "operator" | "admin";

/** Mirrors cryo/api route dependencies. Admin implies operator. */
export const WRITE_MIN_ROLE: Record<WriteAction, MinRole> = {
  createSource: "admin",
  patchSource: "admin",
  deleteSource: "admin",
  purgeSource: "admin",
  createDestination: "admin",
  patchDestination: "admin",
  deleteDestination: "admin",
  purgeDestination: "admin",
  testConnection: "operator",
  cancelRun: "operator",
  manifestRewrite: "operator",
  runMaintenance: "operator",
  triggerScan: "operator",
  assignPreset: "operator",
  approveLayout: "operator",
  dismissLayout: "operator",
  executeLayout: "operator",
  protectSnapshot: "operator",
  unprotectSnapshot: "operator",
  scheduleMaintenance: "operator",
  silentLossDryRun: "operator",
  applyProperty: "operator",
  enableBloom: "operator",
  runPropertyCompaction: "operator",
  qualityConstraint: "operator",
  wairAnalyze: "operator",
  patchTable: "operator",
  editCompliance: "operator",
  dismissDarkTable: "operator",
  refreshAnomalies: "operator",
  runDedup: "operator"
};

const DESTRUCTIVE_ADMIN: ReadonlySet<WriteAction> = new Set([
  "deleteSource",
  "purgeSource",
  "deleteDestination",
  "purgeDestination"
]);

const MAINTENANCE_WRITES: ReadonlySet<WriteAction> = new Set([
  "cancelRun",
  "manifestRewrite",
  "runMaintenance",
  "approveLayout",
  "dismissLayout",
  "executeLayout",
  "protectSnapshot",
  "unprotectSnapshot",
  "scheduleMaintenance"
]);

const RANK: Record<RoleToken, number> = { viewer: 0, operator: 1, admin: 2 };

export const VIEWER_REASON =
  "Viewer access is read-only. Operator or admin role is required to change this.";
export const ADMIN_REASON = "Admin role is required for this action.";
export const SCAN_ONLY_REASON = "This install can scan and report only";

export interface WriteAccess {
  allowed: boolean;
  hidden: boolean;
  reason: string | null;
}

export const roleAllows = (role: RoleToken, min: MinRole): boolean => RANK[role] >= RANK[min];

export const writeAccess = (
  role: RoleToken,
  action: WriteAction,
  maintenanceWritesEnabled = true
): WriteAccess => {
  if (DESTRUCTIVE_ADMIN.has(action) && role !== "admin") {
    return { allowed: false, hidden: true, reason: ADMIN_REASON };
  }
  if (!roleAllows(role, WRITE_MIN_ROLE[action])) {
    return {
      allowed: false,
      hidden: false,
      reason: role === "viewer" ? VIEWER_REASON : ADMIN_REASON
    };
  }
  if (MAINTENANCE_WRITES.has(action) && !maintenanceWritesEnabled) {
    return { allowed: false, hidden: false, reason: SCAN_ONLY_REASON };
  }
  return { allowed: true, hidden: false, reason: null };
};
