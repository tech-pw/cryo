import type { ColumnDef, RowSelectionState } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  assignTables,
  groupTables,
  listDestinations,
  listPresets,
  listSources,
  listTables,
  translateTableFilter,
  runMaintenance,
  runManifestRewrite,
  triggerScan,
  unassignTables
} from "../../api/client";
import type { Mode, TableGroupByField, TableRead, UUID } from "../../api/types";
import { BulkAssignBar } from "../../components/BulkAssignBar";
import { FilterBar } from "../../components/FilterBar";
import { HintLabel, Sparkline } from "../../components/common";
import { CostPrecisionBadge } from "../../components/CostPrecisionBadge";
import { PlanPreview } from "../../components/PlanPreview";
import { CompactionTag, GradePill, PriorityTag } from "../../components/status";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { DataTable } from "../../components/ui/data-table";
import { Input } from "../../components/ui/input";
import { Select } from "../../components/ui/select";
import { useAuth } from "../../lib/auth";
import { filterPresetsForSources } from "../../lib/catalogBindings";
import { isOrphanScanStale } from "../../lib/costStaleness";
import { exportCsv } from "../../lib/exportCsv";
import { formatBytesGb, formatCurrency, formatGb, formatNumber, formatTime } from "../../lib/format";
import { tableFiltersToPredicate, type TableFilterState } from "../../lib/predicate";
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

const initialFiltersFromSearch = (sourceParam: string | null): TableFilterState => {
  const base: TableFilterState = { activeOnly: true };
  if (sourceParam) {
    return { ...base, sourceIds: [sourceParam] };
  }
  return base;
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
  const [searchParams] = useSearchParams();
  const { api, token } = useAuth();
  const [filters, setFilters] = useState<TableFilterState>(() =>
    initialFiltersFromSearch(searchParams.get("source"))
  );
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<string | undefined>("-small_files_count");
  const [savingsView, setSavingsView] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [nlQuery, setNlQuery] = useState("");
  const [groupByField, setGroupByField] = useState<TableGroupByField | null>(null);

  const predicate = useMemo(() => tableFiltersToPredicate(filters), [filters]);
  const tables = useQuery({
    queryKey: ["tables", token, predicate, sort, page],
    queryFn: () =>
      listTables(api, { predicate, sort, limit: pageSize, offset: (page - 1) * pageSize }),
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

  useEffect(() => {
    const sourceParam = searchParams.get("source");
    if (!sourceParam) return;
    setFilters((current) => {
      if (current.sourceIds?.includes(sourceParam)) return current;
      return { ...current, sourceIds: [sourceParam] };
    });
  }, [searchParams]);

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
      toast.success(result.message || `Scan queued: ${result.run_id}`);
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
      setFilters((current) => applyTranslatedFilter(current, result.filter as Record<string, unknown>));
      setPage(1);
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

  const exportTablesCsv = () => {
    const rows = pageItems.map((row) => [
      row.catalog_name ?? "",
      row.qualified_table_name ?? schemaTableName(row),
      schemaTableName(row),
      row.stats?.grade ?? "",
      row.stats?.compaction_priority ?? "",
      row.stats?.needs_compaction ? "yes" : "no",
      row.stats?.small_files_count ?? "",
      row.stats?.total_files ?? "",
      row.stats?.total_size_gb ?? "",
      row.stats?.projected_savings_usd_month ?? "",
      row.stats?.reclaimable_bytes ?? "",
      row.stats?.cost_precision ?? "",
      row.stats?.partition_col ?? "",
      row.first_seen_at
    ]);
    exportCsv(
      `cryo-tables-page-${page}`,
      [
        "catalog",
        "qualified_table",
        "table",
        "grade",
        "priority",
        "needs_compaction",
        "small_files",
        "total_files",
        "size_gb",
        "savings_usd_month",
        "reclaimable_bytes",
        "cost_precision",
        "partition_col",
        "first_seen_at"
      ],
      rows
    );
  };

  const applySort = (next: string | undefined) => {
    setSort(next);
    setPage(1);
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
          const tooltip = tableTooltipName(row.original);
          return (
            <Link to={`/tables/${row.original.id}`} className="table-name-link" title={tooltip}>
              <span className="table-name-cell block max-w-[16rem] truncate font-medium">{name}</span>
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
        header: () => <SortHeader field="small_files_count" label="Small files" sort={sort} onSort={applySort} />,
        size: 168,
        cell: ({ row }) => (
          <span className="metric-with-sparkline numeric-cell sparkline-cell flex items-center justify-end gap-2">
            <span>{formatNumber(row.original.stats?.small_files_count)}</span>
            <Sparkline
              values={[
                row.original.stats?.total_files,
                row.original.stats?.small_files_count,
                row.original.stats?.max_files_per_partition,
                row.original.stats?.avg_files_per_partition
              ]}
            />
          </span>
        )
      },
      {
        id: "total_files",
        header: () => <SortHeader field="total_files" label="Files" sort={sort} onSort={applySort} />,
        size: 104,
        cell: ({ row }) => (
          <span className="numeric-cell block text-right">{formatNumber(row.original.stats?.total_files, true)}</span>
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
        accessorKey: "first_seen_at",
        header: "Seen",
        size: 152,
        cell: ({ getValue }) => formatTime(getValue() as string)
      }
    ],
    [sort]
  );

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button
          variant={savingsView ? "primary" : "secondary"}
          onClick={() => {
            const next = !savingsView;
            setSavingsView(next);
            if (next) setSort("-projected_savings_usd_month");
          }}
        >
          Savings view
        </Button>
        <Button variant="secondary" onClick={exportTablesCsv} disabled={!pageItems.length || Boolean(groupByField)}>
          Export CSV
        </Button>
        <Button variant="secondary" onClick={() => void (groupByField ? grouped.refetch() : tables.refetch())}>
          Refresh
        </Button>
        <Select
          aria-label="Group tables by"
          className="w-[200px]"
          value={groupByField ?? ""}
          onChange={(next) => setGroupByField((next as TableGroupByField) || null)}
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
      <FilterBar
        value={filters}
        catalogOptions={catalogOptions}
        presetOptions={presetOptions}
        matchingTotal={matchingTotal}
        hasSelection={hasSelection}
        filterSelectionActive={filterSelectionActive}
        onSelectAllMatching={selectAllMatching}
        onClearSelection={clearSelection}
        onChange={(next) => {
          setFilters(next);
          setPage(1);
          resetSelectionForFilterChange();
        }}
      />
      {groupByField ? (
        <DataTable
          className="datalake-table"
          columns={groupColumns}
          data={(grouped.data?.rows ?? []) as GroupRow[]}
          density="compact"
          loading={grouped.isLoading}
          getRowId={(row) => row.group_value ?? "not-scanned"}
          emptyTitle="No tables match this view"
          emptyDescription="Run a scan or clear filters to browse the datalake."
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
            emptyTitle="No tables match this view"
            emptyDescription="Run a scan or clear filters to browse the datalake."
            pagination={{
              pageIndex: page - 1,
              pageSize,
              totalRows: matchingTotal,
              onPageChange: (pageIndex) => setPage(pageIndex + 1)
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
