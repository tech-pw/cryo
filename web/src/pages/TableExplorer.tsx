import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Table, Typography, message as antdMessage } from "antd";
import type { TablePaginationConfig } from "antd/es/table";
import { Link } from "react-router-dom";
import { assignTables, listDestinations, listPresets, listTables, runMaintenance } from "../api/client";
import type { TableRead, UUID } from "../api/types";
import { BulkAssignBar, type BulkSelection } from "../components/BulkAssignBar";
import { FilterBar } from "../components/FilterBar";
import { EmptyState, PageHeader, Sparkline } from "../components/common";
import { PlanPreview } from "../components/PlanPreview";
import { GradePill, PriorityTag, StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatGb, formatNumber, formatTime } from "../lib/format";
import { tableFiltersToPredicate, type TableFilterState } from "../lib/predicate";
import { useMemo, useState } from "react";

const pageSize = 50;

export const TableExplorer = () => {
  const queryClient = useQueryClient();
  const { api, token } = useAuth();
  const [filters, setFilters] = useState<TableFilterState>({ activeOnly: true });
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<string | undefined>("-small_files_count");
  const [selectedRowKeys, setSelectedRowKeys] = useState<UUID[]>([]);
  const [allMatching, setAllMatching] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  const predicate = useMemo(() => tableFiltersToPredicate(filters), [filters]);
  const tables = useQuery({
    queryKey: ["tables", token, predicate, sort, page],
    queryFn: () => listTables(api, { predicate, sort, limit: pageSize, offset: (page - 1) * pageSize })
  });
  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });

  const selection: BulkSelection | null = allMatching
    ? { mode: "filter", filter: predicate, totalSelected: tables.data?.total ?? 0 }
    : selectedRowKeys.length
      ? { mode: "ids", ids: selectedRowKeys, totalSelected: selectedRowKeys.length }
      : null;

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
    mutationFn: (args: { destinationId: UUID; presetId?: UUID; mode: "light" | "standard" | "deep" }) => {
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

  const selectedTables = useMemo(
    () =>
      allMatching
        ? (tables.data?.items ?? []).slice(0, 10)
        : (tables.data?.items ?? []).filter((table) => selectedRowKeys.includes(table.id)),
    [allMatching, selectedRowKeys, tables.data?.items]
  );

  return (
    <>
      <PageHeader
        title="Table explorer"
        subtitle="Filter the fleet, assign presets, and preview maintenance without relying on page-only selections."
        actions={<Button onClick={() => tables.refetch()}>Refresh</Button>}
      />
      <FilterBar
        value={filters}
        onChange={(next) => {
          setFilters(next);
          setPage(1);
          setAllMatching(false);
        }}
      />
      <Table<TableRead>
        className="fleet-table"
        size="small"
        rowKey="id"
        loading={tables.isLoading}
        dataSource={tables.data?.items ?? []}
        rowSelection={{
          selectedRowKeys,
          preserveSelectedRowKeys: true,
          onChange: (keys) => {
            setSelectedRowKeys(keys as UUID[]);
            setAllMatching(false);
          }
        }}
        pagination={{
          current: page,
          pageSize,
          total: tables.data?.total ?? 0,
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
            ellipsis: { showTitle: false },
            render: (_, row) => {
              const name = `${row.schema_name}.${row.table_name}`;
              return (
                <Link to={`/tables/${row.id}`} className="table-name-link">
                  <Typography.Text strong ellipsis={{ tooltip: name }}>
                    {name}
                  </Typography.Text>
                </Link>
              );
            }
          },
          {
            title: "Grade",
            dataIndex: "grade",
            key: "grade",
            width: 76,
            sorter: true,
            render: (_, row) => <GradePill grade={row.stats?.grade} />
          },
          {
            title: "Priority",
            dataIndex: "compaction_priority",
            key: "compaction_priority",
            sorter: true,
            render: (_, row) => <PriorityTag priority={row.stats?.compaction_priority} />
          },
          {
            title: "Needs",
            key: "needs",
            render: (_, row) => <StatusTag status={row.stats?.needs_compaction ? "running" : "succeeded"} />
          },
          {
            title: "Small files",
            dataIndex: ["stats", "small_files_count"],
            sorter: true,
            align: "right",
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
            title: "Total files",
            dataIndex: ["stats", "total_files"],
            sorter: true,
            align: "right",
            render: (value) => formatNumber(value, true)
          },
          {
            title: "Size",
            dataIndex: ["stats", "total_size_gb"],
            sorter: true,
            align: "right",
            render: formatGb
          },
          {
            title: "Partition",
            render: (_, row) => row.stats?.partition_col ?? "-"
          },
          {
            title: "Seen",
            dataIndex: "first_seen_at",
            render: formatTime
          }
        ]}
        locale={{
          emptyText: (
            <EmptyState
              title="No tables match this view"
              description="Run a scan or clear filters to browse the Iceberg fleet."
            />
          )
        }}
        scroll={{ x: 1080 }}
      />
      <BulkAssignBar
        selection={selection}
        presets={presets.data ?? []}
        destinations={destinations.data ?? []}
        assigning={assign.isPending}
        running={run.isPending}
        onClear={() => {
          setSelectedRowKeys([]);
          setAllMatching(false);
        }}
        onSelectAllMatching={() => setAllMatching(true)}
        onAssign={(presetId) => assign.mutate(presetId)}
        onPreview={() => setPreviewOpen(true)}
        onRun={(args) => run.mutate(args)}
      />
      <PlanPreview open={previewOpen} tables={selectedTables} onClose={() => setPreviewOpen(false)} />
    </>
  );
};
