export const BROKEN_TABLE_FAILURE_THRESHOLD = 2;

// Neither category is a one-click fix from Cryo today (see failure_category_actionable_from_cryo
// on the API response) -- colors distinguish cause, not severity or fixability.
export const FAILURE_CATEGORY_COLORS: Record<string, string> = {
  permission_denied: "gold",
  metadata_not_found: "volcano",
  other: "default"
};

export type GovernanceTabKey = "attention" | "policies" | "intelligence" | "inventory";

export const DEFAULT_GOVERNANCE_TAB: GovernanceTabKey = "attention";

export const isGovernanceTabKey = (value: string | null): value is GovernanceTabKey =>
  value === "attention" ||
  value === "policies" ||
  value === "intelligence" ||
  value === "inventory";
