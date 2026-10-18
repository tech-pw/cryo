import type { ColumnDef, RowSelectionState, VisibilityState } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, Download, RefreshCw, ScanLine, Sparkles, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { toastScanResult } from "../../lib/operations";
import {
  assignTables,
  exportTablesCsv as requestTablesCsv,
  groupTables,
  deleteExplorerView,
  listDestinations,
  listExplorerViews,
  listPresets,
  listSources,
  listTables,
  saveExplorerView,
  translateTableFilter,
  runMaintenance,
  runManifestRewrite,
  triggerScan,
  unassignTables
} from "../../api/client";
import type {
  ExplorerViewRead,
  JsonObject,
  Mode,
  TableGroupByField,
  TableMaintenanceState,
  TableRead,
  UUID
} from "../../api/types";
import { BulkAssignBar } from "../../components/BulkAssignBar";
import { FilterBar } from "../../components/FilterBar";
import { HintLabel } from "../../components/common";
import { CostPrecisionBadge } from "../../components/CostPrecisionBadge";
import { PlanPreview } from "../../components/PlanPreview";
import { CompactionTag, GradePill, PriorityTag } from "../../components/status";
import { QueryErrorAlert } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { ColumnVisibilityMenu, DataTable, type ColumnVisibilityOption } from "../../components/ui/data-table";
import { Input } from "../../components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { TruncationTooltip } from "../../components/ui/tooltip";
import { Select } from "../../components/ui/select";
import { useAuth } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { filterPresetsForSources } from "../../lib/catalogBindings";
import { isOrphanScanStale } from "../../lib/costStaleness";
import { FRESHNESS_LABELS } from "../../lib/freshness";
import { formatBytesGb, formatCurrency, formatGb, formatNumber, formatSavings, formatTime } from "../../lib/format";
import { type TableFilterState } from "../../lib/predicate";
import {
  EMPTY_TABLES_TITLE,
  EXPLICIT_QUERY_KEYS,
  HIDEABLE_COLUMN_IDS,
  SAVINGS_COLUMN_IDS,
  TRIAGE_VIEW_IDS,
  isTriageView,
  type HideableColumnId,
  clearFilterParams,
  allColumnVisibility,
  colsFromVisibility,
  explorerPredicate,
  operatorColumnVisibility,
  savingsColumnVisibility,
  filtersToSearchParamEntries,
  readExplorerQuery,
  searchParamsForSavedView,
  slaBulletState,
  stripFilterParams,
  unusedCoverageCopy,
  viewFilterSpec,
  visibilityFromCols
} from "../../lib/tableFilters";
import { schemaTableName, tableTooltipName } from "../../lib/tableLabels";
import { useTableExplorerSelection } from "../useTableExplorerSelection";
import { formatRelative } from "../operations/runState";
import { useExplorerViewParam } from "./CatalogDashboard";
import { TRIAGE_VIEWS, useTriageCounts } from "./tablesTriage";

const pageSize = 50;

const applyTranslatedFilter = (
  current: TableFilterState,
  translated: Record<string, unknown>
): TableFilterState => ({
  ...current,
  ...(typeof translated.activeOnly === "boolean" ? { activeOnly: translated.activeOnly } : {}),
  ...(Array.isArray(translated.grades)
    ? { grades: translated.grades.map((v) => String(v)) as TableFilterState["grades"] }
    : {}),
  ...(Array.isArray(translated.priorities)
    ? { priorities: translated.priorities.map((v) => String(v)) as TableFilterState["priorities"] }
    : {}),
  ...(typeof translated.needsCompaction === "boolean"
    ? { needsCompaction: translated.needsCompaction }
    : {}),
  ...(typeof translated.schemaSearch === "string"
    ? { schemaSearch: translated.schemaSearch }
    : {}),
  ...(typeof translated.minSizeGb === "number" ? { minSizeGb: translated.minSizeGb } : {}),
  ...(typeof translated.minSmallFiles === "number"
    ? { minSmallFiles: Math.round(translated.minSmallFiles) }
    : {}),
  ...(typeof translated.minMonthlySavings === "number"
    ? { minMonthlySavings: translated.minMonthlySavings }
    : {}),
  ...(typeof translated.minReclaimableGb === "number"
    ? { minReclaimableGb: translated.minReclaimableGb }
    : {})
});

const CATALOG_VIEWS_STORAGE_KEY = "cryo.catalog.views.v1";
const CATALOG_DEFAULT_VIEW_STORAGE_KEY = "cryo.catalog.defaultView.v1";
const CATALOG_COLUMNS_STORAGE_KEY = "cryo.catalog.columnVisibility.v4";
const CATALOG_COLUMN_SIZING_STORAGE_KEY = "cryo.catalog.columnSizing.v2";

interface CatalogSavedView {
  id?: string;
  name: string;
  filters: TableFilterState;
  schema?: string;
  sort?: string;
  groupBy?: TableGroupByField | null;
  savingsView?: boolean;
  view?: string;
  cols?: string;
}

const storedString = (filters: JsonObject, key: string): string | undefined => {
  const value = filters[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
};

const viewFromServer = (view: ExplorerViewRead): CatalogSavedView => ({
  id: view.id,
  name: view.name,
  filters: view.filters as TableFilterState,
  schema: storedString(view.filters, "schema"),
  sort: view.sort ?? undefined,
  groupBy: (view.group_by as TableGroupByField | null) ?? null,
  savingsView: view.savings_view,
  view: view.savings_view ? "savings" : storedString(view.filters, "view"),
  cols: storedString(view.filters, "cols")
});

const BUILTIN_CATALOG_VIEWS: CatalogSavedView[] = [
  {
    name: "Health triage (grade D and F)",
    filters: { activeOnly: true, grades: ["D", "F"] },
    sort: "-small_files_count"
  },
  {
    name: "Biggest tables that need work",
    filters: { activeOnly: true },
    sort: "-total_size_gb",
    view: "needs-work"
  },
  {
    name: "Most-read tables",
    filters: { activeOnly: true },
    sort: "-query_count_30d"
  },
  {
    name: "Top savings opportunities",
    filters: { activeOnly: true },
    sort: "-projected_savings_usd_month",
    savingsView: true,
    view: "savings"
  }
];

const readJson = <T,>(key: string, fallback: T): T => {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
};

const writeJson = (key: string, value: unknown): void => {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage unavailable (private mode, quota, ...) -- degrade silently.
  }
};

const GROUP_BY_LABELS: Record<TableGroupByField, string> = {
  catalog: "Catalog",
  schema_name: "Schema",
  grade: "Grade",
  compaction_priority: "Priority",
  needs_compaction: "Needs compaction",
  size_bucket: "Size"
};

const groupByLabel = (field: TableGroupByField): string => GROUP_BY_LABELS[field];

const SORT_LABELS: Record<string, string> = {
  small_files_count: "small files",
  total_size_gb: "size",
  projected_savings_usd_month: "estimated savings",
  overall_score: "health score",
  score_delta_7d: "7-day health change",
  query_count_30d: "reads",
  scan_at: "last scan",
  total_files: "files",
  reclaimable_bytes: "reclaimable storage",
  grade: "grade",
  compaction_priority: "priority"
};

const sortCue = (sort: string): string | null => {
  const field = sort.replace(/^-/, "");
  const label = SORT_LABELS[field];
  if (!label) return null;
  return `Sorted by ${label}, ${sort.startsWith("-") ? "highest" : "lowest"} first`;
};

const ExplorerTableMeta = ({
  filters,
  matchingTotal,
  sort,
  note
}: {
  filters: TableFilterState;
  matchingTotal: number;
  sort?: string;
  note?: string | null;
}) => {
  const cue = sort ? sortCue(sort) : null;
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-1 pb-1.5 pt-1">
      <p className="text-xs tabular-nums text-muted-foreground">
        <span className="font-medium text-foreground">{matchingTotal.toLocaleString()}</span>{" "}
        {filters.activeOnly === false ? "" : "active "}
        {matchingTotal === 1 ? "table" : "tables"}
        {note ? <span> · {note}</span> : null}
      </p>
      {cue ? (
        <p className="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <ArrowDown className="size-3" aria-hidden="true" />
          {cue}
        </p>
      ) : null}
    </div>
  );
};

/** The "Table" identity column is intentionally excluded -- hiding it would leave rows unlabeled. */
const COLUMN_META: Record<HideableColumnId, { label: string; group: string }> = {
  health: { label: "Health", group: "Health" },
  grade: { label: "Grade only", group: "Health" },
  compaction_priority: { label: "Priority", group: "Health" },
  needs: { label: "Needs compaction", group: "Health" },
  scan_failures: { label: "Scan failures", group: "Health" },
  maintenance: { label: "Maintenance status", group: "Maintenance" },
  last_run: { label: "Last run", group: "Maintenance" },
  workflow: { label: "Workflow", group: "Maintenance" },
  small_files: { label: "Small files", group: "Storage & cost" },
  total_files: { label: "Total files", group: "Storage & cost" },
  total_size_gb: { label: "Size", group: "Storage & cost" },
  projected_savings_usd_month: { label: "Savings/mo", group: "Storage & cost" },
  reclaimable_bytes: { label: "Reclaimable", group: "Storage & cost" },
  cost_precision: { label: "Savings basis", group: "Storage & cost" },
  reads_30d: { label: "Reads (30d)", group: "Usage & metadata" },
  last_scan: { label: "Last scan", group: "Usage & metadata" },
  freshness: { label: "Last commit", group: "Usage & metadata" },
  catalog: { label: "Catalog", group: "Usage & metadata" },
  owner: { label: "Owner", group: "Usage & metadata" },
  partition: { label: "Partition", group: "Usage & metadata" },
  first_seen_at: { label: "First seen", group: "Usage & metadata" }
};

const COLUMN_GROUP_ORDER = ["Health", "Maintenance", "Storage & cost", "Usage & metadata"];

const COLUMN_VISIBILITY_OPTIONS: ColumnVisibilityOption[] = [...HIDEABLE_COLUMN_IDS]
  .sort(
    (a, b) =>
      COLUMN_GROUP_ORDER.indexOf(COLUMN_META[a].group) - COLUMN_GROUP_ORDER.indexOf(COLUMN_META[b].group)
  )
  .map((id) => ({ id, label: COLUMN_META[id].label, group: COLUMN_META[id].group }));

const STATE_META: Record<TableMaintenanceState, { label: string; dot: string }> = {
  running: { label: "Running", dot: "bg-[var(--color-primary)] motion-safe:animate-pulse" },
  queued: { label: "Queued", dot: "bg-[var(--color-primary)]" },
  failed: { label: "Failed", dot: "bg-[var(--color-status-danger)]" },
  held: { label: "Held", dot: "bg-[var(--color-status-warning)]" },
  excluded: { label: "Excluded", dot: "bg-muted-foreground/50" },
  scan_failing: { label: "Scan failing", dot: "bg-[var(--color-status-danger)]" },
  not_scanned: { label: "Not scanned", dot: "bg-muted-foreground/50" },
  skipped: { label: "Skipped", dot: "bg-[var(--color-status-warning)]" },
  needs_work: { label: "Needs work", dot: "bg-[var(--color-status-warning)]" },
  healthy: { label: "Up to date", dot: "bg-[var(--color-status-success)]" },
  inactive: { label: "Dropped", dot: "bg-muted-foreground/50" }
};

/** Rows a manual run would most likely skip: already in flight, held back, or opted out. */
export const LIKELY_SKIPPED_STATES = new Set<TableMaintenanceState>(["running", "queued", "held", "excluded", "inactive"]);

export const MaintenanceStatusCell = ({ table }: { table: TableRead }) => {
  const maintenance = table.maintenance;
  if (!maintenance) return <span className="text-muted-foreground">-</span>;
  const meta = STATE_META[maintenance.state];
  return (
    <TruncationTooltip content={maintenance.reason ?? meta.label}>
      <span className="flex min-w-0 flex-col">
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm">
          <span className={cn("size-2 shrink-0 rounded-full", meta.dot)} aria-hidden="true" />
          {meta.label}
        </span>
        {maintenance.reason ? (
          <span className="truncate pl-3.5 text-xs text-muted-foreground">{maintenance.reason}</span>
        ) : null}
      </span>
    </TruncationTooltip>
  );
};

const RUN_DOT: Record<string, string> = {
  succeeded: "bg-[var(--color-status-success)]",
  failed: "bg-[var(--color-status-danger)]",
  partial: "bg-[var(--color-status-warning)]",
  skipped: "bg-muted-foreground/50"
};

export const LastRunCell = ({ table }: { table: TableRead }) => {
  const active = table.maintenance?.active_run;
  const last = table.maintenance?.last_run;
  if (active) {
    return (
      <Link to={`/runs/${active.run_id}`} className="block whitespace-nowrap text-sm hover:underline">
        {active.status === "running" ? "Running now" : "Queued"}
        <span className="block text-xs text-muted-foreground">
          {active.started_at ? `started ${formatRelative(active.started_at)}` : "waiting for capacity"}
        </span>
      </Link>
    );
  }
  if (!last) return <span className="text-sm text-muted-foreground">Never</span>;
  return (
    <Link
      to={`/runs/${last.run_id}`}
      className="flex min-w-0 flex-col whitespace-nowrap hover:underline"
      title={last.finished_at ? formatTime(last.finished_at) : undefined}
    >
      <span className="inline-flex items-center gap-1.5 text-sm">
        <span className={cn("size-2 rounded-full", RUN_DOT[last.status] ?? "bg-muted-foreground/50")} aria-hidden="true" />
        {formatRelative(last.finished_at)}
      </span>
      <span className="pl-3.5 text-xs text-muted-foreground">
        {last.status.charAt(0).toUpperCase() + last.status.slice(1)}
        {last.trigger === "schedule" ? " · nightly" : last.trigger ? " · manual" : ""}
      </span>
    </Link>
  );
};

export const HealthCell = ({ table }: { table: TableRead }) => {
  const score = table.stats?.overall_score;
  const delta = table.maintenance?.score_delta_7d;
  if (score == null) return <span className="text-sm text-muted-foreground">Not scanned</span>;
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <GradePill grade={table.stats?.grade} />
      <span className="text-sm tabular-nums">{Math.round(score)}</span>
      {delta != null && Math.abs(delta) >= 1 ? (
        <span
          className={cn(
            "text-xs tabular-nums",
            delta < 0 ? "text-[var(--color-status-danger)]" : "text-[var(--color-status-success)]"
          )}
          title="Change in health score over the last 7 days"
        >
          {delta < 0 ? "▼" : "▲"}
          {Math.abs(delta)}
        </span>
      ) : null}
    </span>
  );
};

export const smallFilesRatioText = (
  smallFiles: number | null | undefined,
  totalFiles: number | null | undefined
): string => {
  if (smallFiles == null || totalFiles == null || Number.isNaN(smallFiles) || Number.isNaN(totalFiles)) {
    return "Not measured";
  }
  const counts = `${formatNumber(smallFiles)} / ${formatNumber(totalFiles)} files`;
  if (totalFiles <= 0) return counts;
  return `${counts} (${Math.round((smallFiles / totalFiles) * 100)}%)`;
};

export const SmallFileRatio = ({
  smallFiles,
  totalFiles
}: {
  smallFiles?: number | null;
  totalFiles?: number | null;
}) => {
  if (smallFiles == null || totalFiles == null || Number.isNaN(smallFiles) || Number.isNaN(totalFiles)) {
    return <span className="block text-right text-sm text-muted-foreground">Not measured</span>;
  }
  const pct = totalFiles > 0 ? Math.round((smallFiles / totalFiles) * 100) : 0;
  return (
    <span className="flex flex-col items-end gap-0.5" title={smallFilesRatioText(smallFiles, totalFiles)}>
      <span className="numeric-cell whitespace-nowrap text-sm">
        {pct}%
        <span className="ml-1.5 text-xs text-muted-foreground">
          {formatNumber(smallFiles, true)} / {formatNumber(totalFiles, true)}
        </span>
      </span>
      <span className="h-1 w-20 overflow-hidden rounded-full bg-[var(--color-surface-sunken)]" aria-hidden="true">
        <span
          className="block h-full rounded-full bg-[var(--color-text-tertiary)]"
          style={{ width: `${Math.min(100, pct)}%` }}
        />
      </span>
    </span>
  );
};

export const SlaBulletBar = ({
  label,
  actual,
  threshold
}: {
  label: string;
  actual: number | null;
  threshold: number | null;
}) => {
  const state = slaBulletState(actual, threshold);
  const width = state.status === "measured" ? Math.round(state.ratio * 100) : null;
  return (
    <div className="min-w-[9rem] space-y-1" aria-label={label}>
      {state.status === "measured" ? (
        <span className="text-sm text-muted-foreground">
          {actual} / {threshold}
        </span>
      ) : (
        <span className="text-sm">Unknown</span>
      )}
      {width == null ? null : (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-surface-sunken)]">
          <div data-bullet-bar className="h-full rounded-full bg-[var(--color-text-secondary)]" style={{ width: `${width}%` }} />
        </div>
      )}
    </div>
  );
};

const SLA_VIEWS = new Set(["freshness", "maintenance-age", "p95-latency"]);

const SortHeader = ({
  field,
  label,
  sort,
  onSort,
  align = "left"
}: {
  field: string;
  label: string;
  sort?: string;
  onSort: (next: string | undefined) => void;
  align?: "left" | "right";
}) => {
  const desc = sort === `-${field}`;
  const asc = sort === field;
  return (
    <button
      type="button"
      className={cn(
        "inline-flex w-full items-center gap-1 whitespace-nowrap text-xs font-medium text-muted-foreground hover:text-foreground",
        align === "right" ? "justify-end text-right" : "text-left"
      )}
      onClick={() => {
        if (!asc && !desc) onSort(`-${field}`);
        else if (desc) onSort(field);
        else onSort(undefined);
      }}
    >
      {label}
      {desc ? " ↓" : asc ? " ↑" : null}
    </button>
  );
};

const QuickViews = ({ active, onChange }: { active: string | null; onChange: (view: string | null) => void }) => {
  const { counts } = useTriageCounts();
  const item = (id: string | null, label: string, count?: number) => {
    const selected = id === null ? !isTriageView(active) : active === id;
    return (
      <button
        key={id ?? "all"}
        type="button"
        aria-pressed={selected}
        onClick={() => onChange(id)}
        className={cn(
          "inline-flex h-7 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-full px-3 text-xs font-medium transition-colors duration-[var(--motion-fast)]",
          selected
            ? "bg-[var(--color-ink)] text-[var(--color-bg)]"
            : "text-muted-foreground hover:bg-[var(--color-surface-sunken)] hover:text-foreground"
        )}
      >
        {label}
        {count != null ? (
          <span className={cn("tabular-nums", selected ? "opacity-70" : "text-muted-foreground")}>
            {formatNumber(count, true)}
          </span>
        ) : null}
      </button>
    );
  };
  return (
    <nav aria-label="Quick views" className="-mx-1 flex max-w-full items-center gap-0.5 overflow-x-auto px-1">
      {item(null, "All tables")}
      {TRIAGE_VIEW_IDS.map((id) => item(id, TRIAGE_VIEWS[id].label, counts[id]))}
    </nav>
  );
};

type GroupRow = {
  group_value: string | null;
  count: number;
  total_size_gb: number;
  total_small_files: number;
  total_savings_usd_month: number;
};

export const TableExplorerSection = () => {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { api, token } = useAuth();

  const updateParams = (updates: Record<string, string | null>) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(updates)) {
          if (value === null) next.delete(key);
          else next.set(key, value);
        }
        return next;
      },
      { replace: true }
    );
  };

  const { setView: setQuickView } = useExplorerViewParam();
  const query = useMemo(() => readExplorerQuery(searchParams), [searchParams]);
  const filters = query.filters;
  const schema = query.schema;
  const page = query.page;
  const sort = query.sort;
  const view = query.view;
  const viewSpec = useMemo(() => viewFilterSpec(view, searchParams), [view, searchParams]);
  const savingsView = view === "savings";
  const groupByParam = query.groupBy;
  const groupByField = (groupByParam && groupByParam in GROUP_BY_LABELS ? (groupByParam as TableGroupByField) : null);

  const applyFilters = (next: TableFilterState, nextSchema?: string) => {
    const params = new URLSearchParams(searchParams);
    stripFilterParams(params);
    filtersToSearchParamEntries(next, { schema: nextSchema }).forEach(([key, value]) => params.set(key, value));
    params.delete("page");
    setSearchParams(params, { replace: true });
  };

  const applySort = (next: string | undefined) => updateParams({ sort: next ?? null, page: null });

  const [previewOpen, setPreviewOpen] = useState(false);
  const [nlQuery, setNlQuery] = useState("");
  const [nlOpen, setNlOpen] = useState(false);
  const [exportingCsv, setExportingCsv] = useState(false);

  const [storedVisibility, setStoredVisibility] = useState<VisibilityState | null>(() =>
    readJson<VisibilityState | null>(CATALOG_COLUMNS_STORAGE_KEY, null)
  );
  const columnVisibility = query.cols
    ? visibilityFromCols(query.cols)
    : (storedVisibility ?? operatorColumnVisibility());
  const shownVisibility = savingsView ? savingsColumnVisibility() : columnVisibility;
  useEffect(() => {
    writeJson(CATALOG_COLUMNS_STORAGE_KEY, columnVisibility);
  }, [columnVisibility]);

  const setColumnVisibility = (next: VisibilityState) => {
    setStoredVisibility(next);
    updateParams({ cols: colsFromVisibility(next) });
  };

  const [userViews, setUserViews] = useState<CatalogSavedView[]>(() =>
    readJson(CATALOG_VIEWS_STORAGE_KEY, [])
  );
  const serverViews = useQuery({
    queryKey: ["explorer-views", token],
    queryFn: () => listExplorerViews(api),
    enabled: Boolean(token)
  });
  useEffect(() => {
    if (!serverViews.data) return;
    setUserViews((local) => {
      const server = serverViews.data.map(viewFromServer);
      const serverNames = new Set(server.map((view) => view.name));
      // Server rows are the source of truth. A local copy with an id that the server
      // no longer returns was deleted elsewhere and must not be resurrected.
      const localOnly = local.filter((view) => !view.id && !serverNames.has(view.name));
      const next = [...server, ...localOnly];
      writeJson(CATALOG_VIEWS_STORAGE_KEY, next);
      return next;
    });
  }, [serverViews.data]);
  const allViews = useMemo(() => [...BUILTIN_CATALOG_VIEWS, ...userViews], [userViews]);

  const applyView = (saved: CatalogSavedView) => {
    const params = searchParamsForSavedView({
      filters: saved.filters,
      schema: saved.schema,
      sort: saved.sort,
      groupBy: saved.groupBy,
      view: saved.view,
      savingsView: saved.savingsView,
      cols: saved.cols ?? searchParams.get("cols")
    });
    setSearchParams(params, { replace: true });
  };

  // Apply a locally-saved default view once, only when the page is opened with no explicit
  // filter/sort/grouping params -- an incoming scoped link (e.g. Datalake health's "Fix in
  // catalog") must always win over a stored default.
  useEffect(() => {
    const hasExplicitParams = EXPLICIT_QUERY_KEYS.some((key) => searchParams.has(key));
    if (hasExplicitParams) return;
    const defaultName = (() => {
      try {
        return window.localStorage.getItem(CATALOG_DEFAULT_VIEW_STORAGE_KEY);
      } catch {
        return null;
      }
    })();
    if (!defaultName) return;
    const view = allViews.find((candidate) => candidate.name === defaultName);
    if (view) applyView(view);
    // Intentionally run once on mount only -- this is a one-time "land with my default" effect,
    // not a live sync with allViews/searchParams.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveCurrentAsView = () => {
    const name = window.prompt("Name this view:");
    if (!name?.trim()) return;
    const view: CatalogSavedView = {
      name: name.trim(),
      filters,
      schema,
      sort,
      groupBy: groupByField,
      savingsView,
      view: savingsView ? "savings" : query.view === "scan-failed" ? "scan-failed" : query.view ?? undefined,
      cols: query.cols ?? undefined
    };
    void saveExplorerView(api, {
      name: view.name,
      filters: {
        ...view.filters,
        ...(view.schema ? { schema: view.schema } : {}),
        ...(view.view && view.view !== "savings" ? { view: view.view } : {}),
        ...(view.cols ? { cols: view.cols } : {})
      } as JsonObject,
      sort: view.sort,
      group_by: view.groupBy,
      savings_view: Boolean(view.savingsView)
    })
      .then((saved) => {
        const stored = viewFromServer(saved);
        setUserViews((current) => {
          const next = [...current.filter((existing) => existing.name !== stored.name), stored];
          writeJson(CATALOG_VIEWS_STORAGE_KEY, next);
          return next;
        });
        toast.success(`Saved view "${stored.name}"`);
        void queryClient.invalidateQueries({ queryKey: ["explorer-views", token] });
      })
      .catch(() => toast.error("Could not save this view."));
  };

  const deleteSavedView = (name: string) => {
    const view = userViews.find((candidate) => candidate.name === name);
    const drop = () => {
      setUserViews((current) => {
        const next = current.filter((existing) => existing.name !== name);
        writeJson(CATALOG_VIEWS_STORAGE_KEY, next);
        return next;
      });
    };
    if (!view?.id) {
      drop();
      return;
    }
    void deleteExplorerView(api, view.id)
      .then(() => {
        drop();
        void queryClient.invalidateQueries({ queryKey: ["explorer-views", token] });
      })
      .catch(() => toast.error("Could not delete this view."));
  };

  const setCurrentAsDefaultView = (name: string) => {
    try {
      window.localStorage.setItem(CATALOG_DEFAULT_VIEW_STORAGE_KEY, name);
      toast.success(`"${name}" is now your default catalog view`);
    } catch {
      toast.error("Could not save a default view on this browser.");
    }
  };

  const predicate = useMemo(
    () => explorerPredicate(filters, { schema, view }),
    [filters, schema, view]
  );
  const tables = useQuery({
    queryKey: ["tables", token, predicate, sort, page, viewSpec],
    queryFn: () =>
      listTables(api, {
        predicate,
        sort,
        limit: pageSize,
        offset: (page - 1) * pageSize,
        freshness_breach: viewSpec?.freshness_breach,
        maintenance_overdue: viewSpec?.maintenance_overdue,
        p95_breach: viewSpec?.p95_breach,
        no_reads_days: viewSpec?.no_reads_days
      }),
    enabled: !groupByField
  });
  const grouped = useQuery({
    queryKey: ["tables-group-by", token, groupByField, predicate],
    queryFn: () => groupTables(api, { field: groupByField as TableGroupByField, predicate }),
    enabled: Boolean(groupByField)
  });
  const presets = useQuery({
    queryKey: ["presets", token],
    queryFn: () => listPresets(api)
  });
  const destinations = useQuery({
    queryKey: ["destinations", token],
    queryFn: () => listDestinations(api)
  });
  const sources = useQuery({
    queryKey: ["sources", token],
    queryFn: () => listSources(api)
  });

  const catalogOptions = useMemo(
    () => (sources.data ?? []).map((source) => ({ label: source.name, value: source.id })),
    [sources.data]
  );
  const presetOptions = useMemo(
    () => (presets.data ?? []).map((preset) => ({ label: preset.name, value: preset.id })),
    [presets.data]
  );

  const pageItems = tables.data?.items ?? [];
  const matchingTotal = tables.data?.total ?? 0;

  const {
    selectedRowKeys,
    allMatching,
    bulkSelection: selection,
    rowSelectedKeys,
    hasSelection,
    filterSelectionActive,
    isDemotingSelection,
    clearSelection,
    selectAllMatching,
    resetSelectionForFilterChange,
    handleRowSelectionChange
  } = useTableExplorerSelection({
    api,
    predicate,
    sort,
    matchingTotal,
    pageItems
  });

  const clearFilters = () => {
    setSearchParams(clearFilterParams(searchParams), { replace: true });
    resetSelectionForFilterChange();
  };

  const rowSelectionState = useMemo<RowSelectionState>(() => {
    const state: RowSelectionState = {};
    for (const key of rowSelectedKeys) state[String(key)] = true;
    return state;
  }, [rowSelectedKeys]);

  const assign = useMutation({
    mutationFn: (presetId: UUID) =>
      assignTables(api, {
        selection:
          selection?.mode === "filter"
            ? { mode: "filter", filter: selection.filter }
            : { mode: "ids", ids: selectedRowKeys },
        preset_id: presetId
      }),
    onSuccess: (result) => {
      toast.success(`Assigned ${result.affected.toLocaleString()} tables`);
      void queryClient.invalidateQueries({ queryKey: ["tables"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const run = useMutation({
    mutationFn: (args: { destinationId: UUID; presetId: UUID; mode: Mode }) => {
      if (selection?.mode === "filter") {
        return runMaintenance(api, {
          selection: { mode: "filter", filter: selection.filter },
          destination_id: args.destinationId,
          preset_id: args.presetId,
          mode: args.mode,
          trigger: "web"
        });
      }
      return runMaintenance(api, {
        table_ids: selectedRowKeys,
        destination_id: args.destinationId,
        preset_id: args.presetId,
        mode: args.mode,
        trigger: "web"
      });
    },
    onSuccess: (result) => toast.success(`Run queued: ${result.run_id}`),
    onError: (error) => toast.error((error as Error).message)
  });

  const manifestRewrite = useMutation({
    mutationFn: (args: { destinationId: UUID; presetId: UUID }) => {
      if (selection?.mode === "filter") {
        return runManifestRewrite(api, {
          selection: { mode: "filter", filter: selection.filter },
          destination_id: args.destinationId,
          preset_id: args.presetId,
          trigger: "web-manifest-rewrite"
        });
      }
      return runManifestRewrite(api, {
        table_ids: selectedRowKeys,
        destination_id: args.destinationId,
        preset_id: args.presetId,
        trigger: "web-manifest-rewrite"
      });
    },
    onSuccess: (result) => toast.success(`Manifest rewrite queued: ${result.run_id}`),
    onError: (error) => toast.error((error as Error).message)
  });

  const unassign = useMutation({
    mutationFn: () => {
      if (!selection) throw new Error("No tables selected");
      if (selection.mode === "filter") {
        return unassignTables(api, {
          selection: { mode: "filter", filter: selection.filter }
        });
      }
      return unassignTables(api, { selection: { mode: "ids", ids: selectedRowKeys } });
    },
    onSuccess: (result) => {
      toast.success(`Unassigned ${result.affected.toLocaleString()} tables`);
      void queryClient.invalidateQueries({ queryKey: ["tables"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const scan = useMutation({
    mutationFn: () => {
      if (!selection) throw new Error("No tables selected");
      if (selection.mode === "filter") {
        return triggerScan(api, {
          scope: "tables",
          selection: { mode: "filter", filter: selection.filter },
          full_scan: true,
          trigger: "web-table-selection"
        });
      }
      return triggerScan(api, {
        scope: "tables",
        table_ids: selectedRowKeys,
        full_scan: true,
        trigger: "web-table-selection"
      });
    },
    onSuccess: (result) => {
      toastScanResult(result);
      clearSelection();
      void queryClient.invalidateQueries({ queryKey: ["tables"] });
      void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
      navigate(`/scans/${result.run_id}`);
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const scanOne = useMutation({
    mutationFn: (tableId: UUID) =>
      triggerScan(api, { scope: "tables", table_ids: [tableId], full_scan: true, trigger: "web-table-row" }),
    onSuccess: (result) => {
      toastScanResult(result);
      void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const translator = useMutation({
    mutationFn: (query: string) => translateTableFilter(api, { query, use_llm: true }),
    onSuccess: (result) => {
      if (result.errors.length) {
        toast.warning("Could not apply parts of the NL filter; review error chips.");
      }
      applyFilters(applyTranslatedFilter(filters, result.filter as Record<string, unknown>), schema);
      resetSelectionForFilterChange();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const selectedTables = useMemo(
    () =>
      allMatching
        ? pageItems.slice(0, 10)
        : pageItems.filter((table) => selectedRowKeys.includes(table.id)),
    [allMatching, selectedRowKeys, pageItems]
  );

  const assignablePresets = useMemo(() => {
    const allPresets = presets.data ?? [];
    const dests = destinations.data ?? [];
    if (selection?.mode === "filter") {
      const filterSourceIds = filters.sourceIds;
      if (filterSourceIds?.length) {
        return filterPresetsForSources(allPresets, dests, filterSourceIds);
      }
      return allPresets;
    }
    if (!selectedRowKeys.length) return allPresets;
    const sourceIds = pageItems
      .filter((table) => selectedRowKeys.includes(table.id))
      .map((table) => table.source_id);
    return filterPresetsForSources(allPresets, dests, sourceIds);
  }, [
    presets.data,
    destinations.data,
    selection?.mode,
    filters.sourceIds,
    selectedRowKeys,
    pageItems
  ]);

  const exportMatchingTablesCsv = async () => {
    setExportingCsv(true);
    try {
      const csv = await requestTablesCsv(api, {
        predicate,
        sort,
        freshness_breach: viewSpec?.freshness_breach,
        maintenance_overdue: viewSpec?.maintenance_overdue,
        p95_breach: viewSpec?.p95_breach,
        no_reads_days: viewSpec?.no_reads_days
      });
      const href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      const anchor = document.createElement("a");
      const suffix = filters.freshnessBucket ?? "filtered";
      anchor.href = href;
      anchor.download = `cryo-tables-${suffix}.csv`;
      anchor.click();
      URL.revokeObjectURL(href);
      toast.success(`Exported ${matchingTotal.toLocaleString()} matching tables`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not export matching tables");
    } finally {
      setExportingCsv(false);
    }
  };

  const groupColumns = useMemo<ColumnDef<GroupRow, unknown>[]>(
    () =>
      groupByField
        ? [
            {
              accessorKey: "group_value",
              header: groupByLabel(groupByField),
              cell: ({ getValue }) => (getValue() as string | null) ?? "(not scanned)"
            },
            {
              accessorKey: "count",
              header: () => <span className="block text-right">Tables</span>,
              cell: ({ getValue }) => <span className="block text-right">{formatNumber(getValue() as number)}</span>
            },
            {
              accessorKey: "total_size_gb",
              header: () => <span className="block text-right">Total Size</span>,
              cell: ({ getValue }) => <span className="block text-right">{formatGb(getValue() as number)}</span>
            },
            {
              accessorKey: "total_small_files",
              header: () => <span className="block text-right">Small Files</span>,
              cell: ({ getValue }) => <span className="block text-right">{formatNumber(getValue() as number)}</span>
            },
            {
              accessorKey: "total_savings_usd_month",
              header: () => <span className="block text-right">Savings/mo</span>,
              cell: ({ getValue }) => <span className="block text-right">{formatCurrency(getValue() as number)}</span>
            }
          ]
        : [],
    [groupByField]
  );

  const tableColumns = useMemo<ColumnDef<TableRead, unknown>[]>(
    () => [
      {
        id: "table",
        header: "Table",
        size: 220,
        minSize: 160,
        maxSize: 640,
        cell: ({ row }) => {
          const name = schemaTableName(row.original);
          return (
            <div className="flex min-w-0 items-center gap-1">
              <TruncationTooltip content={tableTooltipName(row.original)}>
                <Link to={`/tables/${row.original.id}`} className="table-name-link block min-w-0 flex-1">
                  <span className="table-name-cell block min-w-0 truncate font-medium">{name}</span>
                </Link>
              </TruncationTooltip>
              <span className="hidden shrink-0 items-center group-focus-within:flex group-hover:flex">
                <button
                  type="button"
                  className="inline-flex size-6 cursor-pointer items-center justify-center rounded-[var(--radius-sm)] text-muted-foreground hover:bg-[var(--color-panel)] hover:text-foreground"
                  aria-label={`Scan ${name} now`}
                  title="Scan now"
                  onClick={() => scanOne.mutate(row.original.id)}
                >
                  <ScanLine className="size-3.5" aria-hidden="true" />
                </button>
                <Link
                  to={`/tables/${row.original.id}?tab=maintenance`}
                  className="inline-flex size-6 items-center justify-center rounded-[var(--radius-sm)] text-muted-foreground hover:bg-[var(--color-panel)] hover:text-foreground"
                  aria-label={`Maintenance for ${name}`}
                  title="Maintenance history"
                >
                  <Wrench className="size-3.5" aria-hidden="true" />
                </Link>
              </span>
            </div>
          );
        }
      },
      {
        id: "health",
        header: () => <SortHeader field="overall_score" label="Health" sort={sort} onSort={applySort} />,
        size: 132,
        cell: ({ row }) => <HealthCell table={row.original} />
      },
      {
        id: "maintenance",
        header: "Maintenance",
        size: 210,
        minSize: 120,
        cell: ({ row }) => <MaintenanceStatusCell table={row.original} />
      },
      {
        id: "last_run",
        header: "Last run",
        size: 136,
        cell: ({ row }) => <LastRunCell table={row.original} />
      },
      {
        id: "workflow",
        header: "Workflow",
        size: 150,
        minSize: 96,
        cell: ({ row }) => {
          const maintenance = row.original.maintenance;
          if (!maintenance?.workflow_name) {
            return (
              <span className="text-sm text-muted-foreground" title="No pin. Preset rules pick the workflow at run time.">
                By rule
              </span>
            );
          }
          return (
            <TruncationTooltip content={maintenance.workflow_name}>
              <span className="block min-w-0 truncate text-sm">{maintenance.workflow_name}</span>
            </TruncationTooltip>
          );
        }
      },
      {
        id: "catalog",
        header: "Catalog",
        size: 120,
        minSize: 80,
        cell: ({ row }) => {
          const catalog = row.original.catalog_name ?? row.original.source_name;
          return (
            <TruncationTooltip content={catalog}>
              <span className="block min-w-0 truncate">{catalog ?? "-"}</span>
            </TruncationTooltip>
          );
        }
      },
      {
        id: "grade",
        header: () => <SortHeader field="grade" label="Grade" sort={sort} onSort={applySort} />,
        size: 76,
        cell: ({ row }) => <GradePill grade={row.original.stats?.grade} />
      },
      {
        id: "compaction_priority",
        header: () => <SortHeader field="compaction_priority" label="Priority" sort={sort} onSort={applySort} />,
        size: 96,
        cell: ({ row }) => <PriorityTag priority={row.original.stats?.compaction_priority} />
      },
      {
        id: "needs",
        header: "Needs compaction",
        size: 168,
        cell: ({ row }) => <CompactionTag needsCompaction={row.original.stats?.needs_compaction} />
      },
      {
        id: "small_files",
        header: () => (
          <SortHeader field="small_files_count" label="Small files" sort={sort} onSort={applySort} align="right" />
        ),
        size: 136,
        cell: ({ row }) => (
          <SmallFileRatio
            smallFiles={row.original.stats?.small_files_count}
            totalFiles={row.original.stats?.total_files}
          />
        )
      },
      {
        id: "total_files",
        header: () => <SortHeader field="total_files" label="Files" sort={sort} onSort={applySort} align="right" />,
        size: 104,
        cell: ({ row }) => (
          <span className="numeric-cell block whitespace-nowrap text-right">
            {row.original.stats?.total_files == null || Number.isNaN(row.original.stats.total_files)
              ? "Not measured"
              : formatNumber(row.original.stats.total_files, true)}
          </span>
        )
      },
      {
        id: "total_size_gb",
        header: () => <SortHeader field="total_size_gb" label="Size" sort={sort} onSort={applySort} align="right" />,
        size: 88,
        cell: ({ row }) => (
          <span className="numeric-cell block whitespace-nowrap text-right">
            {formatGb(row.original.stats?.total_size_gb)}
          </span>
        )
      },
      {
        id: "reads_30d",
        header: () => <SortHeader field="query_count_30d" label="Reads 30d" sort={sort} onSort={applySort} align="right" />,
        size: 96,
        cell: ({ row }) => (
          <span className="numeric-cell block whitespace-nowrap text-right">
            {formatNumber(row.original.stats?.query_count_30d, true)}
          </span>
        )
      },
      {
        id: "last_scan",
        header: () => <SortHeader field="scan_at" label="Last scan" sort={sort} onSort={applySort} align="right" />,
        size: 112,
        cell: ({ row }) => {
          const failing = (row.original.consecutive_scan_failures ?? 0) > 0;
          return (
            <span
              className={cn(
                "table-date-cell block whitespace-nowrap text-right text-sm",
                failing && "text-[var(--color-status-danger)]"
              )}
              title={row.original.stats?.scan_at ? formatTime(row.original.stats.scan_at) : undefined}
            >
              {failing
                ? `Failing ×${row.original.consecutive_scan_failures}`
                : row.original.stats?.scan_at
                  ? formatRelative(row.original.stats.scan_at)
                  : "Never"}
            </span>
          );
        }
      },
      {
        id: "owner",
        header: "Owner",
        size: 160,
        minSize: 96,
        cell: ({ row }) => (
          <TruncationTooltip content={row.original.owner_email}>
            <span className="block min-w-0 truncate text-sm">{row.original.owner_email ?? "-"}</span>
          </TruncationTooltip>
        )
      },
      {
        id: "projected_savings_usd_month",
        header: () => (
          <SortHeader
            field="projected_savings_usd_month"
            label="Savings/mo"
            sort={sort}
            onSort={applySort}
            align="right"
          />
        ),
        size: 104,
        cell: ({ row }) => (
          <span
            className="numeric-cell block whitespace-nowrap text-right"
            title={row.original.stats?.projected_savings_usd_month == null ? "No savings estimate yet" : undefined}
          >
            {formatSavings(row.original.stats?.projected_savings_usd_month)}
          </span>
        )
      },
      {
        id: "reclaimable_bytes",
        header: () => (
          <SortHeader field="reclaimable_bytes" label="Reclaimable" sort={sort} onSort={applySort} align="right" />
        ),
        size: 128,
        cell: ({ row }) => (
          <span className="numeric-cell block whitespace-nowrap text-right">
            {formatBytesGb(row.original.stats?.reclaimable_bytes)}
          </span>
        )
      },
      {
        id: "cost_precision",
        header: () => (
          <HintLabel
            label="Savings basis"
            tooltip="How Cryo calculated savings and reclaimable storage for this table."
          />
        ),
        size: 196,
        cell: ({ row }) => (
          <CostPrecisionBadge
            precision={row.original.stats?.cost_precision}
            lastFullCostScanAt={row.original.stats?.cost_last_full_scan_at}
            orphanScanStale={isOrphanScanStale(
              row.original.stats?.cost_precision,
              row.original.stats?.cost_last_full_scan_at
            )}
          />
        )
      },
      {
        id: "partition",
        header: "Partition",
        size: 120,
        minSize: 80,
        cell: ({ row }) => (
          <TruncationTooltip content={row.original.stats?.partition_col}>
            <span className="block min-w-0 truncate">{row.original.stats?.partition_col ?? "-"}</span>
          </TruncationTooltip>
        )
      },
      {
        id: "freshness",
        header: () => (
          <HintLabel label="Last commit" tooltip="When a writer last committed a snapshot to this table." />
        ),
        size: 152,
        cell: ({ row }) => (
          <span className="table-date-cell block whitespace-nowrap text-right">
            {row.original.stats?.last_snapshot_committed_at
              ? formatTime(row.original.stats.last_snapshot_committed_at)
              : "-"}
          </span>
        )
      },
      {
        id: "scan_failures",
        header: () => <span className="block whitespace-nowrap text-right">Scan failures</span>,
        size: 112,
        cell: ({ row }) => (
          <span className="numeric-cell block whitespace-nowrap text-right">
            {formatNumber(row.original.consecutive_scan_failures ?? 0, true)}
          </span>
        )
      },
      {
        accessorKey: "first_seen_at",
        header: () => <span className="block whitespace-nowrap text-right">First seen</span>,
        size: 152,
        cell: ({ getValue }) => (
          <span className="table-date-cell block whitespace-nowrap text-right">{formatTime(getValue() as string)}</span>
        )
      },
      ...(SLA_VIEWS.has(view ?? "")
        ? [
            {
              id: "sla_headroom",
              header: "SLA",
              size: 160,
              cell: ({ row }: { row: { original: TableRead } }) => (
                <SlaBulletBar
                  label="SLA"
                  actual={
                    view === "freshness" && row.original.stats?.last_snapshot_committed_at
                      ? (Date.now() - Date.parse(row.original.stats.last_snapshot_committed_at)) / 36e5
                      : null
                  }
                  threshold={null}
                />
              )
            }
          ]
        : [])
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sort, view]
  );

  const orderedColumns = useMemo(() => {
    if (!savingsView) return tableColumns;
    const rank = (id: string | undefined) => {
      if (id === "table") return -1;
      const index = (SAVINGS_COLUMN_IDS as readonly string[]).indexOf(id ?? "");
      return index === -1 ? SAVINGS_COLUMN_IDS.length : index;
    };
    return [...tableColumns].sort((a, b) => rank(a.id) - rank(b.id));
  }, [savingsView, tableColumns]);

  const savedViewsMenu = (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="sm" variant="secondary">
          Saved views
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-1.5">
        <p className="px-2 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
          Views
        </p>
        <ul className="flex max-h-64 flex-col gap-0.5 overflow-y-auto">
          {allViews.map((saved) => {
            const own = userViews.some((candidate) => candidate.name === saved.name);
            return (
              <li key={saved.name} className="group/view flex items-center gap-1">
                <button
                  type="button"
                  className="min-w-0 flex-1 cursor-pointer truncate rounded-[var(--radius-sm)] px-2 py-1.5 text-left text-sm hover:bg-[var(--color-surface-sunken)]"
                  onClick={() => applyView(saved)}
                >
                  {saved.name}
                </button>
                <button
                  type="button"
                  className="hidden cursor-pointer rounded-[var(--radius-sm)] px-1.5 py-1 text-xs text-muted-foreground hover:bg-[var(--color-surface-sunken)] hover:text-foreground group-hover/view:block"
                  onClick={() => setCurrentAsDefaultView(saved.name)}
                >
                  Default
                </button>
                {own ? (
                  <button
                    type="button"
                    aria-label={`Delete view ${saved.name}`}
                    className="hidden cursor-pointer rounded-[var(--radius-sm)] px-1.5 py-1 text-xs text-[var(--color-status-danger)] hover:bg-[var(--color-surface-sunken)] group-hover/view:block"
                    onClick={() => deleteSavedView(saved.name)}
                  >
                    Delete
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
        <div className="mt-1 border-t border-border pt-1">
          <button
            type="button"
            className="w-full cursor-pointer rounded-[var(--radius-sm)] px-2 py-1.5 text-left text-sm font-medium hover:bg-[var(--color-surface-sunken)]"
            onClick={saveCurrentAsView}
          >
            Save current view…
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );

  const likelySkipped = allMatching
    ? 0
    : pageItems.filter(
        (table) =>
          selectedRowKeys.includes(table.id) &&
          table.maintenance != null &&
          LIKELY_SKIPPED_STATES.has(table.maintenance.state)
      ).length;

  return (
    <div id="table-explorer" className="scroll-mt-4">
      <div className="mb-2">
        <QuickViews active={view} onChange={(next) => setQuickView(next)} />
      </div>
      <FilterBar
        value={{ ...filters, ...(schema ? { schema } : {}) } as TableFilterState}
        catalogOptions={catalogOptions}
        presetOptions={presetOptions}
        matchingTotal={matchingTotal}
        hideSummary
        hideNeedsToggle
        onReset={clearFilters}
        actions={
          <>
            <Select
              aria-label="Group tables by"
              className="h-[var(--control-height-sm)] w-[8.5rem] text-xs"
              value={groupByField ?? ""}
              onChange={(next) => updateParams({ groupBy: (next as string) || null, page: null })}
              allowClear
              placeholder="No grouping"
              options={[
                { label: "Group: catalog", value: "catalog" },
                { label: "Group: schema", value: "schema_name" },
                { label: "Group: grade", value: "grade" },
                { label: "Group: priority", value: "compaction_priority" },
                { label: "Group: compaction", value: "needs_compaction" },
                { label: "Group: size", value: "size_bucket" }
              ]}
            />
            <ColumnVisibilityMenu
              options={COLUMN_VISIBILITY_OPTIONS}
              visibility={shownVisibility}
              onChange={setColumnVisibility}
              onReset={() => setColumnVisibility(operatorColumnVisibility())}
              onShowAll={() => setColumnVisibility(allColumnVisibility())}
              disabledReason={
                savingsView ? "The savings view pins its own columns. Switch to another view to customise them." : undefined
              }
            />
            {savedViewsMenu}
            {matchingTotal > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                aria-label="Export CSV"
                onClick={() => void exportMatchingTablesCsv()}
                disabled={Boolean(groupByField) || exportingCsv}
              >
                <Download size={14} aria-hidden="true" />
                {exportingCsv ? "Exporting..." : "Export"}
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              aria-label={tables.isFetching || grouped.isFetching ? "Refreshing tables" : "Refresh tables"}
              onClick={() => void (groupByField ? grouped.refetch() : tables.refetch())}
            >
              <RefreshCw
                size={14}
                aria-hidden="true"
                className={tables.isFetching || grouped.isFetching ? "motion-safe:animate-spin" : ""}
              />
            </Button>
          </>
        }
        trailing={
          <Button
            size="sm"
            variant={nlOpen ? "outline" : "ghost"}
            className="gap-1.5"
            aria-expanded={nlOpen}
            onClick={() => setNlOpen((open) => !open)}
          >
            <Sparkles className="size-3.5" aria-hidden="true" />
            Ask in plain English
          </Button>
        }
        onChange={(next) => {
          const nextSchema = (next as TableFilterState & { schema?: string }).schema;
          applyFilters(next, typeof nextSchema === "string" ? nextSchema : undefined);
          resetSelectionForFilterChange();
        }}
      />
      {nlOpen ? (
        <form
          className="mb-2 mt-2 flex flex-col gap-2 rounded-[var(--radius-lg)] border border-border bg-[var(--color-surface-sunken)] p-2.5 sm:flex-row sm:items-center"
          onSubmit={(event) => {
            event.preventDefault();
            const queryText = nlQuery.trim();
            if (!queryText) return;
            translator.mutate(queryText);
          }}
        >
          <Input
            autoFocus
            aria-label="Describe the tables you want"
            value={nlQuery}
            placeholder="e.g. grade D tables over 100 GB that need compaction"
            onChange={(event) => setNlQuery(event.target.value)}
            className="flex-1 bg-background"
          />
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant="primary" loading={translator.isPending}>
              Apply as filters
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setNlOpen(false)}>
              Close
            </Button>
          </div>
        </form>
      ) : null}
      {translator.data?.chips.length ? (
        <p className="mb-2 mt-1 text-xs text-muted-foreground">Applied: {translator.data.chips.join(" · ")}</p>
      ) : null}
      {translator.data?.errors.length ? (
        <Alert
          className="mb-2"
          variant="warning"
          title="Some of that couldn't be turned into filters"
          description={translator.data.errors.join(" · ")}
        />
      ) : null}
      {filters.freshnessBucket ? (
        <Alert
          className="mb-3 mt-2"
          variant={filters.freshnessBucket === "failed" ? "error" : "info"}
          title={`Freshness view: ${FRESHNESS_LABELS[filters.freshnessBucket]}`}
          description="Showing the complete server-filtered table set for this freshness bucket. CSV export includes every match, not only this page."
        />
      ) : null}
      {view === "scan-failed" ? (
        <Alert
          className="mb-3 mt-2"
          variant="error"
          title="Scan failed"
          description="Tables with repeated scan failures. Filtered on the server."
        />
      ) : null}
      {view === "unused" ? (
        <Alert
          className="mb-3 mt-2"
          variant="info"
          title={`No reads in the last ${viewSpec?.no_reads_days ?? 30} days`}
          description="This is the unused-tables view, not the full catalog. Coverage gaps are excluded. Export uses the same filter."
        />
      ) : null}
      {viewSpec ? (
        <div className="mb-3 flex flex-wrap items-center gap-3" data-testid="view-filter-bar">
          {view === "unused" ? (
            <p className="text-sm text-muted-foreground">
              {unusedCoverageCopy(
                tables.data?.telemetry_coverage,
                tables.data?.telemetry_gap_tables,
                tables.data?.telemetry_window_days ?? viewSpec.no_reads_days
              )}
            </p>
          ) : (
            <>
              <SlaBulletBar label="SLA" actual={null} threshold={null} />
              <p className="text-sm text-muted-foreground">
                {tables.data?.unknown_count == null
                  ? "Unknown commit or SLA is excluded, not counted as a breach."
                  : `${tables.data.unknown_count} tables have an unknown commit or SLA and are excluded, not counted as a breach.`}
              </p>
            </>
          )}
        </div>
      ) : null}
      <BulkAssignBar
        selection={selection}
        presets={assignablePresets}
        destinations={destinations.data ?? []}
        assigning={assign.isPending || unassign.isPending}
        running={run.isPending || manifestRewrite.isPending}
        scanning={scan.isPending}
        matchingTotal={matchingTotal}
        onSelectAllMatching={viewSpec ? undefined : selectAllMatching}
        likelySkipped={likelySkipped}
        onClear={clearSelection}
        onAssign={(presetId) => assign.mutate(presetId)}
        onPreview={() => setPreviewOpen(true)}
        onRun={(args) => run.mutate(args)}
        onManifestRewrite={(args) => manifestRewrite.mutate(args)}
        onUnassign={() => unassign.mutate()}
        onScan={() => scan.mutate()}
      />
      {tables.isError || grouped.isError ? (
        <QueryErrorAlert error={tables.error ?? grouped.error} title="Failed to load tables" />
      ) : groupByField ? (
        <>
          <ExplorerTableMeta filters={filters} matchingTotal={matchingTotal} />
        <DataTable
          className="datalake-table"
          fillAvailableWidth
          columns={groupColumns}
          data={(grouped.data?.rows ?? []) as GroupRow[]}
          density="compact"
          loading={grouped.isLoading}
          getRowId={(row) => row.group_value ?? "not-scanned"}
          emptyTitle={EMPTY_TABLES_TITLE}
          emptyAction={
            <Button type="button" onClick={clearFilters}>
              Clear filters
            </Button>
          }
        />
        </>
      ) : (
        <>
          <ExplorerTableMeta
            filters={filters}
            matchingTotal={matchingTotal}
            sort={sort}
            note={isTriageView(view) ? TRIAGE_VIEWS[view].hint : null}
          />
          <DataTable
            className="datalake-table"
            enableColumnResizing
            columnSizingStorageKey={CATALOG_COLUMN_SIZING_STORAGE_KEY}
            columns={orderedColumns}
            data={pageItems}
            density="compact"
            loading={tables.isLoading || isDemotingSelection}
            stickyFirstColumn
            getRowId={(row) => row.id}
            enableRowSelection
            rowSelection={rowSelectionState}
            onRowSelectionChange={(updater) => {
              const next = typeof updater === "function" ? updater(rowSelectionState) : updater;
              const keys = Object.keys(next).filter((key) => next[key]);
              void handleRowSelectionChange(keys);
            }}
            columnVisibility={shownVisibility}
            onColumnVisibilityChange={(updater) => {
              if (savingsView) return;
              setColumnVisibility(typeof updater === "function" ? updater(columnVisibility) : updater);
            }}
            emptyTitle={EMPTY_TABLES_TITLE}
            emptyAction={
              <Button type="button" onClick={clearFilters}>
                Clear filters
              </Button>
            }
            pagination={{
              pageIndex: page - 1,
              pageSize,
              totalRows: matchingTotal,
              onPageChange: (pageIndex) => updateParams({ page: pageIndex > 0 ? String(pageIndex + 1) : null })
            }}
          />
          <PlanPreview open={previewOpen} tables={selectedTables} onClose={() => setPreviewOpen(false)} />
        </>
      )}
    </div>
  );
};
