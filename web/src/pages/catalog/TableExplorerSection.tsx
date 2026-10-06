import type { ColumnDef, RowSelectionState, VisibilityState } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import type { ExplorerViewRead, JsonObject, Mode, TableGroupByField, TableRead, UUID } from "../../api/types";
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
import { Select } from "../../components/ui/select";
import { useAuth } from "../../lib/auth";
import { filterPresetsForSources } from "../../lib/catalogBindings";
import { isOrphanScanStale } from "../../lib/costStaleness";
import { FRESHNESS_LABELS } from "../../lib/freshness";
import { formatBytesGb, formatCurrency, formatGb, formatNumber, formatTime } from "../../lib/format";
import { type TableFilterState } from "../../lib/predicate";
import {
  EMPTY_TABLES_TITLE,
  EXPLICIT_QUERY_KEYS,
  HIDEABLE_COLUMN_IDS,
  clearFilterParams,
  colsFromVisibility,
  explorerPredicate,
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
const CATALOG_COLUMNS_STORAGE_KEY = "cryo.catalog.columnVisibility.v1";

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
    name: "Health triage",
    filters: { activeOnly: true, grades: ["D", "F"] },
    sort: "-small_files_count"
  },
  {
    name: "Needs compaction",
    filters: { activeOnly: true, needsCompaction: true },
    sort: "-small_files_count"
  },
  {
    name: "Top savings opportunities",
    filters: { activeOnly: true },
    sort: "-projected_savings_usd_month",
    savingsView: true,
    view: "savings"
  },
  {
    name: "Scan failed",
    filters: { activeOnly: true },
    view: "scan-failed"
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

/** The "Table" identity column is intentionally excluded -- hiding it would leave rows unlabeled. */
const COLUMN_LABELS: Record<(typeof HIDEABLE_COLUMN_IDS)[number], string> = {
  catalog: "Catalog",
  grade: "Grade",
  compaction_priority: "Priority",
  needs: "Compaction",
  small_files: "Small / total files",
  total_files: "Files",
  total_size_gb: "Size",
  last_scan: "Last scan",
  projected_savings_usd_month: "Savings/mo",
  reclaimable_bytes: "Reclaimable",
  cost_precision: "Savings basis",
  partition: "Partition",
  freshness: "Last updated",
  scan_failures: "Scan failures",
  first_seen_at: "Seen"
};

const COLUMN_VISIBILITY_OPTIONS: ColumnVisibilityOption[] = HIDEABLE_COLUMN_IDS.map((id) => ({
  id,
  label: COLUMN_LABELS[id]
}));

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
}) => <span className="numeric-cell block text-right">{smallFilesRatioText(smallFiles, totalFiles)}</span>;

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

const hiddenColumnFacts = (table: TableRead, visibility: VisibilityState): string | null => {
  const hidden = (id: string) => visibility[id] === false;
  const parts: string[] = [];
  if (hidden("catalog")) {
    const catalog = table.catalog_name ?? table.source_name;
    if (catalog) parts.push(catalog);
  }
  if (hidden("grade")) parts.push(table.stats?.grade ? `Grade ${table.stats.grade}` : "Not graded");
  if (hidden("compaction_priority") && table.stats?.compaction_priority) {
    parts.push(table.stats.compaction_priority);
  }
  if (hidden("needs")) {
    parts.push(
      table.stats?.needs_compaction == null
        ? "Compaction not measured"
        : table.stats.needs_compaction
          ? "Needs compaction"
          : "Does not need compaction"
    );
  }
  if (hidden("small_files") || hidden("total_files")) {
    parts.push(smallFilesRatioText(table.stats?.small_files_count, table.stats?.total_files));
  }
  if (hidden("total_size_gb")) {
    const size = table.stats?.total_size_gb;
    parts.push(size == null || Number.isNaN(size) ? "Not measured" : formatGb(size));
  }
  if (hidden("last_scan")) {
    parts.push(table.stats?.scan_at ? `Scanned ${formatTime(table.stats.scan_at)}` : "Not measured");
  }
  if (hidden("freshness")) {
    parts.push(
      table.stats?.last_snapshot_committed_at
        ? `Updated ${formatTime(table.stats.last_snapshot_committed_at)}`
        : "Not measured"
    );
  }
  if (hidden("scan_failures")) {
    const failures = table.consecutive_scan_failures;
    parts.push(failures == null ? "Scan failures not measured" : `${failures} scan failures`);
  }
  return parts.length ? parts.join(" · ") : null;
};

const SortHeader = ({
  field,
  label,
  sort,
  onSort
}: {
  field: string;
  label: string;
  sort?: string;
  onSort: (next: string | undefined) => void;
}) => {
  const desc = sort === `-${field}`;
  const asc = sort === field;
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 text-left text-xs font-medium text-muted-foreground hover:text-foreground"
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
  const [exportingCsv, setExportingCsv] = useState(false);

  const [storedVisibility, setStoredVisibility] = useState<VisibilityState>(() =>
    readJson(CATALOG_COLUMNS_STORAGE_KEY, {})
  );
  const columnVisibility = query.cols ? visibilityFromCols(query.cols) : storedVisibility;
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
              header: "Tables",
              cell: ({ getValue }) => <span className="block text-right">{formatNumber(getValue() as number)}</span>
            },
            {
              accessorKey: "total_size_gb",
              header: "Total Size",
              cell: ({ getValue }) => <span className="block text-right">{formatGb(getValue() as number)}</span>
            },
            {
              accessorKey: "total_small_files",
              header: "Small Files",
              cell: ({ getValue }) => <span className="block text-right">{formatNumber(getValue() as number)}</span>
            },
            {
              accessorKey: "total_savings_usd_month",
              header: "Savings/mo",
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
        size: 280,
        cell: ({ row }) => {
          const name = schemaTableName(row.original);
          const facts = hiddenColumnFacts(row.original, columnVisibility);
          const tooltip = [tableTooltipName(row.original), facts].filter(Boolean).join(" · ");
          return (
            <Link to={`/tables/${row.original.id}`} className="table-name-link" title={tooltip}>
              <span className="table-name-cell block max-w-[16rem] truncate font-medium">{name}</span>
              {facts ? (
                <span className="block max-w-[16rem] truncate text-xs text-muted-foreground">{facts}</span>
              ) : null}
            </Link>
          );
        }
      },
      {
        id: "catalog",
        header: "Catalog",
        size: 140,
        cell: ({ row }) => (
          <span
            className="block max-w-[8rem] truncate"
            title={row.original.catalog_name ?? row.original.source_name ?? undefined}
          >
            {row.original.catalog_name ?? row.original.source_name ?? "-"}
          </span>
        )
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
        size: 104,
        cell: ({ row }) => <PriorityTag priority={row.original.stats?.compaction_priority} />
      },
      {
        id: "needs",
        header: "Compaction",
        size: 142,
        cell: ({ row }) => <CompactionTag needsCompaction={row.original.stats?.needs_compaction} />
      },
      {
        id: "small_files",
        header: () => <SortHeader field="small_files_count" label="Small / total files" sort={sort} onSort={applySort} />,
        size: 220,
        cell: ({ row }) => (
          <SmallFileRatio
            smallFiles={row.original.stats?.small_files_count}
            totalFiles={row.original.stats?.total_files}
          />
        )
      },
      {
        id: "total_files",
        header: () => <SortHeader field="total_files" label="Files" sort={sort} onSort={applySort} />,
        size: 104,
        cell: ({ row }) => (
          <span className="numeric-cell block text-right">
            {row.original.stats?.total_files == null || Number.isNaN(row.original.stats.total_files)
              ? "Not measured"
              : formatNumber(row.original.stats.total_files, true)}
          </span>
        )
      },
      {
        id: "total_size_gb",
        header: () => <SortHeader field="total_size_gb" label="Size" sort={sort} onSort={applySort} />,
        size: 96,
        cell: ({ row }) => (
          <span className="numeric-cell block text-right">{formatGb(row.original.stats?.total_size_gb)}</span>
        )
      },
      {
        id: "last_scan",
        header: "Last scan",
        size: 152,
        cell: ({ row }) => (
          <span className="block text-right">
            {row.original.stats?.scan_at ? formatTime(row.original.stats.scan_at) : "Not measured"}
          </span>
        )
      },
      {
        id: "projected_savings_usd_month",
        header: () => <SortHeader field="projected_savings_usd_month" label="Savings/mo" sort={sort} onSort={applySort} />,
        size: 120,
        cell: ({ row }) => (
          <span className="numeric-cell block text-right">
            {formatCurrency(row.original.stats?.projected_savings_usd_month)}
          </span>
        )
      },
      {
        id: "reclaimable_bytes",
        header: () => <SortHeader field="reclaimable_bytes" label="Reclaimable" sort={sort} onSort={applySort} />,
        size: 128,
        cell: ({ row }) => (
          <span className="numeric-cell block text-right">
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
        size: 150,
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
        size: 156,
        cell: ({ row }) => (
          <span className="block max-w-[9rem] truncate" title={row.original.stats?.partition_col ?? undefined}>
            {row.original.stats?.partition_col ?? "-"}
          </span>
        )
      },
      {
        id: "freshness",
        header: "Last updated",
        size: 152,
        cell: ({ row }) =>
          row.original.stats?.last_snapshot_committed_at
            ? formatTime(row.original.stats.last_snapshot_committed_at)
            : "-"
      },
      {
        id: "scan_failures",
        header: "Scan failures",
        size: 112,
        cell: ({ row }) => (
          <span className="numeric-cell block text-right">
            {formatNumber(row.original.consecutive_scan_failures ?? 0, true)}
          </span>
        )
      },
      {
        accessorKey: "first_seen_at",
        header: "Seen",
        size: 152,
        cell: ({ getValue }) => formatTime(getValue() as string)
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
    [sort, columnVisibility, view]
  );

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button variant="secondary" onClick={() => void (groupByField ? grouped.refetch() : tables.refetch())}>
          Refresh
        </Button>
        <ColumnVisibilityMenu
          options={COLUMN_VISIBILITY_OPTIONS}
          visibility={columnVisibility}
          onChange={setColumnVisibility}
        />
        <Button
          variant={savingsView ? "primary" : "secondary"}
          onClick={() => {
            const next = !savingsView;
            updateParams({
              view: next ? "savings" : null,
              sort: next ? "-projected_savings_usd_month" : sort,
              page: null
            });
          }}
        >
          Savings view
        </Button>
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="secondary">Views</Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 space-y-2.5">
            <Select
              aria-label="Group tables by"
              value={groupByField ?? ""}
              onChange={(next) => updateParams({ groupBy: (next as string) || null, page: null })}
              allowClear
              placeholder="No grouping"
              options={[
                { label: "Group by catalog", value: "catalog" },
                { label: "Group by schema", value: "schema_name" },
                { label: "Group by grade", value: "grade" },
                { label: "Group by priority", value: "compaction_priority" },
                { label: "Group by compaction", value: "needs_compaction" },
                { label: "Group by size", value: "size_bucket" }
              ]}
            />
            <Select
              aria-label="Catalog view"
              value=""
              placeholder="Load a view..."
        onReset={clearFilters}
        onChange={(next) => {
                const saved = allViews.find((candidate) => candidate.name === next);
                if (saved) applyView(saved);
              }}
              options={allViews.map((saved) => ({ label: saved.name, value: saved.name }))}
            />
            <Button type="button" variant="secondary" onClick={saveCurrentAsView}>
              Save view
            </Button>
            {userViews.length ? (
              <Select
                aria-label="Delete saved view"
                value=""
                placeholder="Delete view..."
                onChange={(next) => next && deleteSavedView(next)}
                options={userViews.map((saved) => ({ label: saved.name, value: saved.name }))}
              />
            ) : null}
            {userViews.length ? (
              <Select
                aria-label="Set default catalog view"
                value=""
                placeholder="Set default..."
                onChange={(next) => next && setCurrentAsDefaultView(next)}
                options={allViews.map((saved) => ({ label: saved.name, value: saved.name }))}
              />
            ) : null}
          </PopoverContent>
        </Popover>
      </div>
      <div className="mb-2.5 flex w-full flex-col gap-1.5">
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const queryText = nlQuery.trim();
            if (!queryText) return;
            translator.mutate(queryText);
          }}
        >
          <Input
            value={nlQuery}
            placeholder='Find tables like... e.g. "grade D needs compaction savings above $200"'
            onChange={(event) => setNlQuery(event.target.value)}
          />
          <Button type="submit" loading={translator.isPending}>
            Translate
          </Button>
        </form>
        {translator.data?.chips.length ? (
          <p className="text-sm text-muted-foreground">Applied: {translator.data.chips.join(" · ")}</p>
        ) : null}
        {translator.data?.errors.length ? (
          <Alert
            variant="warning"
            title="Translation issues"
            description={translator.data.errors.join(" · ")}
          />
        ) : null}
      </div>
      {filters.freshnessBucket ? (
        <Alert
          className="mb-3"
          variant={filters.freshnessBucket === "failed" ? "error" : "info"}
          title={`Freshness view: ${FRESHNESS_LABELS[filters.freshnessBucket]}`}
          description="Showing the complete server-filtered table set for this freshness bucket. CSV export includes every match, not only this page."
        />
      ) : null}
      {view === "scan-failed" ? (
        <Alert
          className="mb-3"
          variant="error"
          title="Scan failed"
          description="Tables with repeated scan failures. Filtered on the server."
        />
      ) : null}
      {view === "unused" ? (
        <Alert
          className="mb-3"
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
      <FilterBar
        value={{ ...filters, ...(schema ? { schema } : {}) } as TableFilterState}
        catalogOptions={catalogOptions}
        presetOptions={presetOptions}
        matchingTotal={matchingTotal}
        hasSelection={hasSelection}
        filterSelectionActive={filterSelectionActive}
        onSelectAllMatching={viewSpec ? undefined : selectAllMatching}
        onClearSelection={clearSelection}
        bulkActions={
          <Button
            variant="secondary"
            aria-label="Export CSV"
            onClick={() => void exportMatchingTablesCsv()}
            disabled={!matchingTotal || Boolean(groupByField) || exportingCsv}
          >
            {exportingCsv ? "Exporting..." : `Export ${matchingTotal.toLocaleString()} CSV`}
          </Button>
        }
        onChange={(next) => {
          const nextSchema = (next as TableFilterState & { schema?: string }).schema;
          applyFilters(next, typeof nextSchema === "string" ? nextSchema : undefined);
          resetSelectionForFilterChange();
        }}
      />
      {tables.isError || grouped.isError ? (
        <QueryErrorAlert error={tables.error ?? grouped.error} title="Failed to load tables" />
      ) : groupByField ? (
        <DataTable
          className="datalake-table"
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
      ) : (
        <>
          <DataTable
            className="datalake-table"
            columns={tableColumns}
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
            columnVisibility={columnVisibility}
            onColumnVisibilityChange={(updater) => {
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
          <BulkAssignBar
            selection={selection}
            presets={assignablePresets}
            destinations={destinations.data ?? []}
            assigning={assign.isPending}
            running={run.isPending || manifestRewrite.isPending}
            scanning={scan.isPending}
            onClear={clearSelection}
            onAssign={(presetId) => assign.mutate(presetId)}
            onPreview={() => setPreviewOpen(true)}
            onRun={(args) => run.mutate(args)}
            onManifestRewrite={(args) => manifestRewrite.mutate(args)}
            onUnassign={() => unassign.mutate()}
            onScan={() => scan.mutate()}
          />
          <PlanPreview open={previewOpen} tables={selectedTables} onClose={() => setPreviewOpen(false)} />
        </>
      )}
    </>
  );
};
