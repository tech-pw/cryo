import type { VisibilityState } from "@tanstack/react-table";
import {
  BROKEN_TABLE_FAILURE_THRESHOLD,
  isFreshnessBucketKey
} from "./freshness";
import {
  all,
  any,
  leaf,
  tableFiltersToPredicate,
  type Predicate,
  type TableFilterState
} from "./predicate";

export const EMPTY_TABLES_TITLE = "No tables match these filters";
export const DEFAULT_TABLE_SORT = "-small_files_count";

export const HIDEABLE_COLUMN_IDS = [
  "health",
  "maintenance",
  "last_run",
  "workflow",
  "small_files",
  "total_size_gb",
  "reads_30d",
  "projected_savings_usd_month",
  "last_scan",
  "catalog",
  "owner",
  "grade",
  "compaction_priority",
  "needs",
  "total_files",
  "reclaimable_bytes",
  "cost_precision",
  "partition",
  "freshness",
  "scan_failures",
  "first_seen_at"
] as const;

export type HideableColumnId = (typeof HIDEABLE_COLUMN_IDS)[number];

/** First screen answers "is it healthy, is maintenance keeping up, and what is it worth." */
export const OPERATOR_COLUMN_IDS = [
  "health",
  "maintenance",
  "last_run",
  "small_files",
  "total_size_gb",
  "reads_30d",
  "projected_savings_usd_month"
] as const;

/** The savings view pins its own columns, in this order, after the table name. */
export const SAVINGS_COLUMN_IDS = [
  "projected_savings_usd_month",
  "reclaimable_bytes",
  "cost_precision",
  "small_files",
  "total_size_gb",
  "health"
] as const;

export const savingsColumnVisibility = (): VisibilityState => {
  const visible = new Set<string>(SAVINGS_COLUMN_IDS);
  return Object.fromEntries(HIDEABLE_COLUMN_IDS.map((id) => [id, visible.has(id)]));
};

export const operatorColumnVisibility = (): VisibilityState => {
  const visible = new Set<string>(OPERATOR_COLUMN_IDS);
  const state: VisibilityState = {};
  for (const id of HIDEABLE_COLUMN_IDS) {
    if (!visible.has(id)) state[id] = false;
  }
  return state;
};

/** Every hideable column on. `colsFromVisibility` encodes this as "no cols param." */
export const allColumnVisibility = (): VisibilityState =>
  Object.fromEntries(HIDEABLE_COLUMN_IDS.map((id) => [id, true]));

const CANONICAL_FILTER_KEYS = [
  "q",
  "catalog",
  "schema",
  "grade",
  "needs",
  "freshness",
  "size",
  "small",
  "delete",
  "activeOnly",
  "priorities",
  "preset",
  "minMonthlySavings",
  "precision",
  "freshnessAsOf"
] as const;

/** Old links. Read and cleared; never written. */
export const FILTER_ALIAS_KEYS = [
  "grades",
  "needsCompaction",
  "source",
  "minSizeGb",
  "minSmallFiles",
  "minReclaimableGb"
] as const;

export const FILTER_PARAM_KEYS = [...CANONICAL_FILTER_KEYS, ...FILTER_ALIAS_KEYS];

export const EXPLICIT_QUERY_KEYS = [...FILTER_PARAM_KEYS, "sort", "groupBy", "view", "cols", "page"];

const NEEDS_TRUE = new Set(["compaction", "1", "true", "yes"]);
const NEEDS_FALSE = new Set(["healthy", "0", "false", "no", "none"]);

const csvParam = (value: string | null): string[] | undefined => {
  if (!value) return undefined;
  const parts = value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  return parts.length ? parts : undefined;
};

const boolParam = (value: string | null): boolean | undefined => {
  if (value === "1" || value === "true") return true;
  if (value === "0" || value === "false") return false;
  return undefined;
};

const numberParam = (value: string | null): number | null | undefined => {
  if (value === null || value === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
};

const firstParam = (params: URLSearchParams, ...keys: string[]): string | null => {
  for (const key of keys) {
    const value = params.get(key);
    if (value !== null && value !== "") return value;
  }
  return null;
};

export const needsFromSearchParams = (params: URLSearchParams): boolean | undefined => {
  const canonical = params.get("needs");
  if (canonical !== null && canonical !== "") {
    const value = canonical.trim().toLowerCase();
    if (NEEDS_TRUE.has(value)) return true;
    if (NEEDS_FALSE.has(value)) return false;
  }
  return boolParam(params.get("needsCompaction"));
};

export const filtersFromSearchParams = (params: URLSearchParams): TableFilterState => {
  const activeOnly = boolParam(params.get("activeOnly"));
  const grades = csvParam(firstParam(params, "grade", "grades"))?.map((grade) => grade.toUpperCase());
  const freshness = params.get("freshness");
  return {
    activeOnly: activeOnly === undefined ? true : activeOnly,
    grades,
    priorities: csvParam(params.get("priorities")),
    needsCompaction: needsFromSearchParams(params),
    schemaSearch: params.get("q") ?? undefined,
    sourceIds: csvParam(firstParam(params, "catalog", "source")),
    presetFilter: csvParam(params.get("preset")),
    minSizeGb: numberParam(firstParam(params, "size", "minSizeGb")) ?? null,
    minSmallFiles: numberParam(firstParam(params, "small", "minSmallFiles")) ?? null,
    minMonthlySavings: numberParam(params.get("minMonthlySavings")) ?? null,
    minReclaimableGb: numberParam(firstParam(params, "delete", "minReclaimableGb")) ?? null,
    costPrecision: (params.get("precision") as TableFilterState["costPrecision"]) ?? null,
    freshnessBucket: isFreshnessBucketKey(freshness)
      ? (freshness as TableFilterState["freshnessBucket"])
      : null,
    freshnessAsOf: params.get("freshnessAsOf")
  };
};

export const schemaFromSearchParams = (params: URLSearchParams): string | undefined => {
  const value = params.get("schema")?.trim();
  return value || undefined;
};

export const pageFromSearchParams = (params: URLSearchParams): number =>
  Math.max(1, Number(params.get("page")) || 1);

export interface ExplorerQuery {
  filters: TableFilterState;
  schema?: string;
  page: number;
  sort: string;
  view: string | null;
  groupBy: string | null;
  cols: string | null;
}

export const readExplorerQuery = (params: URLSearchParams): ExplorerQuery => ({
  filters: filtersFromSearchParams(params),
  schema: schemaFromSearchParams(params),
  page: pageFromSearchParams(params),
  sort: params.get("sort") || DEFAULT_TABLE_SORT,
  view: params.get("view"),
  groupBy: params.get("groupBy"),
  cols: params.get("cols")
});

export const filtersToSearchParamEntries = (
  filters: TableFilterState,
  extras: { schema?: string } = {}
): [string, string][] => {
  const entries: [string, string][] = [];
  if (filters.activeOnly === false) entries.push(["activeOnly", "0"]);
  if (filters.grades?.length) entries.push(["grade", filters.grades.join(",")]);
  if (filters.priorities?.length) entries.push(["priorities", filters.priorities.join(",")]);
  if (filters.needsCompaction === true) entries.push(["needs", "compaction"]);
  else if (filters.needsCompaction === false) entries.push(["needs", "healthy"]);
  if (filters.schemaSearch?.trim()) entries.push(["q", filters.schemaSearch.trim()]);
  if (filters.sourceIds?.length) entries.push(["catalog", filters.sourceIds.join(",")]);
  if (extras.schema?.trim()) entries.push(["schema", extras.schema.trim()]);
  if (filters.presetFilter?.length) entries.push(["preset", filters.presetFilter.join(",")]);
  if (typeof filters.minSizeGb === "number") entries.push(["size", String(filters.minSizeGb)]);
  if (typeof filters.minSmallFiles === "number") entries.push(["small", String(filters.minSmallFiles)]);
  if (typeof filters.minMonthlySavings === "number") {
    entries.push(["minMonthlySavings", String(filters.minMonthlySavings)]);
  }
  if (typeof filters.minReclaimableGb === "number") {
    entries.push(["delete", String(filters.minReclaimableGb)]);
  }
  if (filters.costPrecision) entries.push(["precision", filters.costPrecision]);
  if (filters.freshnessBucket) entries.push(["freshness", filters.freshnessBucket]);
  if (filters.freshnessAsOf) entries.push(["freshnessAsOf", filters.freshnessAsOf]);
  return entries;
};

export const stripFilterParams = (params: URLSearchParams): void => {
  for (const key of FILTER_PARAM_KEYS) params.delete(key);
};

export const clearFilterParams = (params: URLSearchParams): URLSearchParams => {
  const next = new URLSearchParams(params);
  stripFilterParams(next);
  next.delete("view");
  next.delete("window");
  next.delete("page");
  return next;
};

export interface TableViewFilterSpec {
  freshness_breach?: boolean;
  maintenance_overdue?: boolean;
  p95_breach?: boolean;
  no_reads_days?: number;
}

export const DEFAULT_NO_READS_DAYS = 30;

const positiveDays = (value: string | null): number | null => {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 1) return null;
  return Math.floor(parsed);
};

export const viewFilterSpec = (
  view: string | null,
  params: URLSearchParams = new URLSearchParams()
): TableViewFilterSpec | null => {
  switch (view) {
    case "freshness":
      return { freshness_breach: true };
    case "maintenance-age":
      return { maintenance_overdue: true };
    case "p95-latency":
      return { p95_breach: true };
    case "unused":
      return {
        no_reads_days:
          positiveDays(params.get("window") ?? params.get("no_reads_days")) ?? DEFAULT_NO_READS_DAYS
      };
    default:
      return null;
  }
};

export const unsupportedViewNotice = (_view: string | null): string | null => null;

export const slaBulletState = (
  actual: number | null | undefined,
  threshold: number | null | undefined
): { status: "unknown" } | { status: "measured"; ratio: number } => {
  if (actual == null || threshold == null || !Number.isFinite(actual) || !Number.isFinite(threshold)) {
    return { status: "unknown" };
  }
  const ratio = threshold > 0 ? Math.min(1, actual / threshold) : 1;
  return { status: "measured", ratio };
};

export const unusedCoverageCopy = (
  coverage: number | null | undefined,
  gapTables: number | null | undefined,
  windowDays: number | null | undefined
): string => {
  const windowLabel = windowDays != null ? `${windowDays}-day` : "requested";
  if (coverage == null || !Number.isFinite(coverage)) {
    return `Unknown telemetry coverage for the ${windowLabel} window. A coverage gap is not listed as unused.`;
  }
  const percent = Math.round(coverage * 100);
  if (coverage <= 0) {
    return `Telemetry coverage is 0% for the ${windowLabel} window. These tables are not unused.`;
  }
  const gap = gapTables ?? 0;
  if (gap > 0) {
    return `Telemetry coverage is ${percent}% for the ${windowLabel} window. ${gap} tables are outside coverage and are not listed as unused.`;
  }
  return `Telemetry covers the ${windowLabel} window.`;
};

export const SCAN_STALE_HOURS = 48;
export const GRADE_DROP_POINTS = 10;

const HOUR_MS = 3_600_000;

/** Rounded to the hour so query keys built from it stay stable between renders. */
export const scanStaleCutoff = (now: number = Date.now()): string =>
  new Date(Math.floor(now / HOUR_MS) * HOUR_MS - SCAN_STALE_HOURS * HOUR_MS).toISOString();

export const TRIAGE_VIEW_IDS = [
  "needs-work",
  "failed",
  "in-progress",
  "skipped",
  "grade-dropped",
  "scan-issues",
  "savings"
] as const;

export type TriageViewId = (typeof TRIAGE_VIEW_IDS)[number];

export const isTriageView = (view: string | null | undefined): view is TriageViewId =>
  (TRIAGE_VIEW_IDS as readonly string[]).includes(view ?? "");

// Mirrors REASON_COOLDOWN/REASON_BACKOFF/REASON_BUDGET_CAP/REASON_STALE_STATS/REASON_EXCLUDED
// in cryo/maintenance/skip_history.py -- not REASON_NO_WORK, which only ever pairs with
// STAGE_EVALUATED (no-work tables), never STAGE_NEEDS_WORK/STAGE_ELIGIBLE, so it can't appear
// here anyway. These aren't exposed as a typed enum from the backend; if that reason list
// changes, update this array to match.
const SKIP_REASONS = ["cooldown", "backoff", "budget_cap", "stale_stats", "excluded"];

/** Server-side predicate for each quick view. Counts and table rows both use this, so they agree. */
export const triageViewPredicate = (
  view: string | null | undefined,
  cutoff: string = scanStaleCutoff()
): Predicate | null => {
  switch (view) {
    case "needs-work":
      return leaf("needs_compaction", "=", true);
    case "failed":
      return leaf("last_job_status", "in", ["failed", "partial"]);
    case "in-progress":
      return leaf("last_job_status", "in", ["queued", "admitted", "running", "deferred"]);
    case "skipped":
      return all([
        leaf("needs_compaction", "=", true),
        leaf("last_selection_stage", "in", ["needs_work", "eligible"]),
        leaf("last_selection_reason", "in", SKIP_REASONS)
      ]);
    case "grade-dropped":
      return leaf("score_delta_7d", "<=", -GRADE_DROP_POINTS);
    case "scan-issues":
      return any([
        leaf("consecutive_scan_failures", ">", 0),
        leaf("last_scan_error_kind", "!=", null),
        leaf("scan_at", "=", null),
        leaf("scan_at", "<", cutoff)
      ]);
    case "savings":
      return leaf("projected_savings_usd_month", ">", 0);
    default:
      return null;
  }
};

export const explorerPredicate = (
  filters: TableFilterState,
  extras: { schema?: string; view?: string | null } = {}
): Predicate | null =>
  all([
    tableFiltersToPredicate(filters),
    extras.schema
      ? leaf("schema_name", "in", extras.schema.split(",").map((part) => part.trim()).filter(Boolean))
      : null,
    extras.view === "scan-failed" && filters.freshnessBucket !== "failed"
      ? leaf("consecutive_scan_failures", ">=", BROKEN_TABLE_FAILURE_THRESHOLD)
      : null,
    triageViewPredicate(extras.view)
  ]);

export interface SavedViewUrlInput {
  filters: TableFilterState;
  schema?: string;
  sort?: string;
  groupBy?: string | null;
  view?: string | null;
  savingsView?: boolean;
  cols?: string | null;
}

export const searchParamsForSavedView = (view: SavedViewUrlInput): URLSearchParams => {
  const params = new URLSearchParams();
  for (const [key, value] of filtersToSearchParamEntries(view.filters, { schema: view.schema })) {
    params.set(key, value);
  }
  if (view.sort) params.set("sort", view.sort);
  if (view.groupBy) params.set("groupBy", view.groupBy);
  const viewKey = view.view ?? (view.savingsView ? "savings" : null);
  if (viewKey) params.set("view", viewKey);
  if (view.cols) params.set("cols", view.cols);
  return params;
};

export const visibilityFromCols = (value: string | null): VisibilityState => {
  if (!value) return {};
  const visible = new Set(
    value
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean)
  );
  if (!visible.size) return {};
  const state: VisibilityState = {};
  for (const id of HIDEABLE_COLUMN_IDS) {
    if (!visible.has(id)) state[id] = false;
  }
  return state;
};

export const colsFromVisibility = (visibility: VisibilityState): string | null => {
  const hidden = HIDEABLE_COLUMN_IDS.filter((id) => visibility[id] === false);
  if (!hidden.length) return null;
  return HIDEABLE_COLUMN_IDS.filter((id) => visibility[id] !== false).join(",");
};
