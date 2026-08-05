export const BROKEN_TABLE_FAILURE_THRESHOLD = 2;

// Neither category is a one-click fix from Cryo today (see failure_category_actionable_from_cryo
// on the API response) -- colors distinguish cause, not severity or fixability.
export const FAILURE_CATEGORY_COLORS: Record<string, string> = {
  permission_denied: "gold",
  metadata_not_found: "volcano",
  other: "default"
};

/**
 * Task-oriented governance tabs (audit: reorganize around operator jobs, not data categories).
 * - `triage` — urgent items needing action now (was "Attention"): approvals, anomalies.
 * - `access` — tier policies and ABAC/GitOps access rules (was "Policies").
 * - `signals` — fleet anomalies, maintenance signals, cost forecast, and lineage (was
 *   "Intelligence"); named for its actual content rather than a generic "Intelligence" label.
 * - `ownership` — table inventory and ownership assignment (was "Inventory").
 */
export type GovernanceTabKey = "triage" | "access" | "signals" | "ownership";

export const DEFAULT_GOVERNANCE_TAB: GovernanceTabKey = "triage";

const GOVERNANCE_TAB_KEYS: GovernanceTabKey[] = ["triage", "access", "signals", "ownership"];

export const isGovernanceTabKey = (value: string | null): value is GovernanceTabKey =>
  value !== null && (GOVERNANCE_TAB_KEYS as string[]).includes(value);

/** Old tab query values, kept resolving to their new task-oriented equivalent so bookmarked
 * and previously-shared links (`?tab=attention` etc.) keep working. */
const LEGACY_GOVERNANCE_TAB_ALIASES: Record<string, GovernanceTabKey> = {
  attention: "triage",
  policies: "access",
  intelligence: "signals",
  inventory: "ownership"
};

/** Resolves a raw `?tab=` URL value (current key, legacy alias, or unknown/absent) to a valid tab. */
export const resolveGovernanceTab = (value: string | null): GovernanceTabKey => {
  if (value === null) return DEFAULT_GOVERNANCE_TAB;
  if (isGovernanceTabKey(value)) return value;
  return LEGACY_GOVERNANCE_TAB_ALIASES[value] ?? DEFAULT_GOVERNANCE_TAB;
};
