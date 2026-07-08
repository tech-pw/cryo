import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Input, Space, Table, Typography, message as antdMessage } from "antd";
import type { TablePaginationConfig } from "antd/es/table";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";
import {
  assignTables,
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
import type { Mode, TableRead, UUID } from "../../api/types";
import { BulkAssignBar } from "../../components/BulkAssignBar";
import { FilterBar } from "../../components/FilterBar";
import { EmptyState, HintLabel, Sparkline } from "../../components/common";
import { CostPrecisionBadge } from "../../components/CostPrecisionBadge";
import { PlanPreview } from "../../components/PlanPreview";
import { CompactionTag, GradePill, PriorityTag } from "../../components/status";
import { useAuth } from "../../lib/auth";
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

/**
 * Filterable, paginated table explorer section. Lives below CatalogDashboard on the
 * consolidated Catalog page. Body lifted verbatim from the legacy TableExplorer.tsx --
 * preserves the URL `?source=` filter so clicking a catalog row in the dashboard
 * above narrows this section.
 */
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

  const predicate = useMemo(() => tableFiltersToPredicate(filters), [filters]);
  const tables = useQuery({
    queryKey: ["tables", token, predicate, sort, page],
    queryFn: () =>
      listTables(api, { predicate, sort, limit: pageSize, offset: (page - 1) * pageSize })
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
      antdMessage.success(`Assigned ${result.affected.toLocaleString()} tables`);
      void queryClient.invalidateQueries({ queryKey: ["tables"] });
    },
    onError: (error) => antdMessage.error((error as Error).message)
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
    onSuccess: (result) => antdMessage.success(`Run queued: ${result.run_id}`),
    onError: (error) => antdMessage.error((error as Error).message)
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
    onSuccess: (result) => antdMessage.success(`Manifest rewrite queued: ${result.run_id}`),
    onError: (error) => antdMessage.error((error as Error).message)
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
      antdMessage.success(`Unassigned ${result.affected.toLocaleString()} tables`);
      void queryClient.invalidateQueries({ queryKey: ["tables"] });
    },
    onError: (error) => antdMessage.error((error as Error).message)
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
      antdMessage.success(result.message || `Scan queued: ${result.run_id}`);
      clearSelection();
      void queryClient.invalidateQueries({ queryKey: ["tables"] });
      void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
      navigate(`/scans/${result.run_id}`);
    },
    onError: (error) => antdMessage.error((error as Error).message)
  });
  const translator = useMutation({
    mutationFn: (query: string) => translateTableFilter(api, { query, use_llm: true }),
    onSuccess: (result) => {
      if (result.errors.length) {
        antdMessage.warning("Could not apply parts of the NL filter; review error chips.");
      }
      setFilters((current) => applyTranslatedFilter(current, result.filter as Record<string, unknown>));
      setPage(1);
      resetSelectionForFilterChange();
    },
    onError: (error) => antdMessage.error((error as Error).message)
  });

  const selectedTables = useMemo(
    () =>
      allMatching
        ? pageItems.slice(0, 10)
        : pageItems.filter((table) => selectedRowKeys.includes(table.id)),
    [allMatching, selectedRowKeys, pageItems]
  );

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

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Button
          type={savingsView ? "primary" : "default"}
          onClick={() => {
            const next = !savingsView;
            setSavingsView(next);
            if (next) setSort("-projected_savings_usd_month");
          }}
        >
          Savings view
        </Button>
        <Button onClick={exportTablesCsv} disabled={!pageItems.length}>
          Export CSV
        </Button>
        <Button onClick={() => void tables.refetch()}>Refresh</Button>
      </Space>
      <Space direction="vertical" style={{ width: "100%", marginBottom: 10 }} size={6}>
        <Input.Search
          allowClear
          value={nlQuery}
          placeholder='Find tables like... e.g. "grade D needs compaction savings above $200"'
          enterButton="Translate"
          loading={translator.isPending}
          onChange={(event) => setNlQuery(event.target.value)}
          onSearch={(value) => {
            const queryText = value.trim();
            if (!queryText) return;
            translator.mutate(queryText);
          }}
        />
        {translator.data?.chips.length ? (
          <Typography.Text type="secondary">
            Applied: {translator.data.chips.join(" · ")}
          </Typography.Text>
        ) : null}
        {translator.data?.errors.length ? (
          <Alert
            type="warning"
            showIcon
            message="Translation issues"
            description={translator.data.errors.join(" · ")}
          />
        ) : null}
      </Space>
      <FilterBar
        value={filters}
        catalogOptions={catalogOptions}
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
      <Table<TableRead>
        className="datalake-table"
        size="small"
        rowKey="id"
        loading={tables.isLoading || isDemotingSelection}
        dataSource={pageItems}
        rowSelection={{
          selectedRowKeys: rowSelectedKeys,
          preserveSelectedRowKeys: true,
          onChange: (keys) => {
            void handleRowSelectionChange(keys);
          }
        }}
        pagination={{
          current: page,
          pageSize,
          total: matchingTotal,
          showSizeChanger: false,
          showTotal: (total) => `${total.toLocaleString()} tables`
        }}
        onChange={(pagination: TablePaginationConfig, _filters, sorter) => {
          setPage(pagination.current ?? 1);
          const sortItem = Array.isArray(sorter) ? sorter[0] : sorter;
          if (sortItem?.field && sortItem.order) {
            const field = Array.isArray(sortItem.field)
              ? String(sortItem.field[sortItem.field.length - 1])
              : String(sortItem.field);
            setSort(`${sortItem.order === "descend" ? "-" : ""}${field}`);
          }
        }}
        columns={[
          {
            title: "Table",
            key: "table",
            fixed: "left",
            width: 280,
            className: "table-name-cell",
            ellipsis: { showTitle: false },
            render: (_: unknown, row) => {
              const name = schemaTableName(row);
              const tooltip = tableTooltipName(row);
              return (
                <Link to={`/tables/${row.id}`} className="table-name-link">
                  <Typography.Text strong ellipsis={{ tooltip }}>
                    {name}
                  </Typography.Text>
                </Link>
              );
            }
          },
          {
            title: "Catalog",
            key: "catalog",
            width: 140,
            ellipsis: { showTitle: false },
            render: (_: unknown, row) => (
              <Typography.Text
                ellipsis={{ tooltip: row.catalog_name ?? row.source_name ?? undefined }}
              >
                {row.catalog_name ?? row.source_name ?? "-"}
              </Typography.Text>
            )
          },
          {
            title: "Grade",
            dataIndex: "grade",
            key: "grade",
            width: 76,
            align: "center",
            sorter: true,
            render: (_: unknown, row) => <GradePill grade={row.stats?.grade} />
          },
          {
            title: "Priority",
            dataIndex: "compaction_priority",
            key: "compaction_priority",
            width: 104,
            sorter: true,
            render: (_: unknown, row) => <PriorityTag priority={row.stats?.compaction_priority} />
          },
          {
            title: "Compaction",
            key: "needs",
            width: 142,
            render: (_: unknown, row) => (
              <CompactionTag needsCompaction={row.stats?.needs_compaction} />
            )
          },
          {
            title: "Small files",
            dataIndex: ["stats", "small_files_count"],
            sorter: true,
            align: "right",
            width: 168,
            className: "numeric-cell sparkline-cell",
            render: (value, row) => (
              <span className="metric-with-sparkline">
                <span>{formatNumber(value)}</span>
                <Sparkline
                  values={[
                    row.stats?.total_files,
                    row.stats?.small_files_count,
                    row.stats?.max_files_per_partition,
                    row.stats?.avg_files_per_partition
                  ]}
                />
              </span>
            )
          },
          {
            title: "Files",
            dataIndex: ["stats", "total_files"],
            sorter: true,
            align: "right",
            width: 104,
            className: "numeric-cell",
            render: (value) => formatNumber(value, true)
          },
          {
            title: "Size",
            dataIndex: ["stats", "total_size_gb"],
            sorter: true,
            align: "right",
            width: 96,
            className: "numeric-cell",
            render: formatGb
          },
          {
            title: "Savings/mo",
            dataIndex: ["stats", "projected_savings_usd_month"],
            sorter: true,
            align: "right",
            width: 120,
            className: "numeric-cell",
            render: (value) => formatCurrency(value)
          },
          {
            title: "Reclaimable",
            dataIndex: ["stats", "reclaimable_bytes"],
            sorter: true,
            align: "right",
            width: 128,
            className: "numeric-cell",
            render: (value) => formatBytesGb(value)
          },
          {
            title: (
              <HintLabel
                label="Savings basis"
                tooltip="How Cryo calculated savings and reclaimable storage for this table."
              />
            ),
            key: "cost_precision",
            width: 150,
            render: (_: unknown, row) => (
              <CostPrecisionBadge
                precision={row.stats?.cost_precision}
                lastFullCostScanAt={row.stats?.cost_last_full_scan_at}
                orphanScanStale={isOrphanScanStale(
                  row.stats?.cost_precision,
                  row.stats?.cost_last_full_scan_at
                )}
              />
            )
          },
          {
            title: "Partition",
            width: 156,
            ellipsis: { showTitle: false },
            render: (_: unknown, row) => (
              <Typography.Text ellipsis={{ tooltip: row.stats?.partition_col }}>
                {row.stats?.partition_col ?? "-"}
              </Typography.Text>
            )
          },
          {
            title: "Seen",
            dataIndex: "first_seen_at",
            width: 152,
            render: formatTime
          }
        ]}
        locale={{
          emptyText: (
            <EmptyState
              title="No tables match this view"
              description="Run a scan or clear filters to browse the datalake."
            />
          )
        }}
        scroll={{ x: 1760 }}
      />
      <BulkAssignBar
        selection={selection}
        presets={presets.data ?? []}
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
      <PlanPreview
        open={previewOpen}
        tables={selectedTables}
        onClose={() => setPreviewOpen(false)}
      />
    </>
  );
};