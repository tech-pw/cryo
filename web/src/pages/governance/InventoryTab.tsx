import type { ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  compactObservations,
  getObservationStats,
  listTables,
  searchObservations
} from "../../api/client";
import type { TableRead } from "../../api/types";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import { ConfirmAction } from "../../components/confirm";
import { EmptyState, QueryErrorAlert } from "../../components/common";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { Input } from "../../components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { useAuth } from "../../lib/auth";
import { formatBytesGb, formatTime } from "../../lib/format";
import { qualifiedTableName } from "../../lib/tableLabels";
import { leaf } from "../../lib/predicate";
import { BROKEN_TABLE_FAILURE_THRESHOLD, FAILURE_CATEGORY_COLORS } from "./constants";
import { TableSearchSelect } from "./TableSearchSelect";

const BROKEN_PAGE_SIZE = 8;

const failureBadgeVariant = (category: string | null | undefined): "danger" | "warning" | "secondary" => {
  const color = FAILURE_CATEGORY_COLORS[category ?? ""] ?? "default";
  if (color === "red" || color === "error") return "danger";
  if (color === "gold" || color === "warning" || color === "orange") return "warning";
  return "secondary";
};

export const InventoryTab = () => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const [observationFilter, setObservationFilter] = useState<{ table_id?: string; event_type?: string }>({});
  const [brokenPage, setBrokenPage] = useState(0);

  const brokenTables = useQuery({
    queryKey: ["broken-tables", api.token],
    queryFn: () =>
      listTables(api, {
        predicate: leaf("consecutive_scan_failures", ">=", BROKEN_TABLE_FAILURE_THRESHOLD),
        sort: "-consecutive_scan_failures",
        limit: 50
      }),
    refetchInterval: 60_000
  });
  const observationStats = useQuery({
    queryKey: ["observation-stats", api.token],
    queryFn: () => getObservationStats(api),
    refetchInterval: 60_000
  });
  const observationSearch = useQuery({
    queryKey: ["observation-search", api.token, observationFilter],
    queryFn: () => searchObservations(api, { ...observationFilter, limit: 50 })
  });

  const compactObservationsMutation = useMutation({
    mutationFn: () => compactObservations(api),
    onSuccess: (result) => {
      const shrinkFactor = result.bytes_out > 0 ? result.bytes_in / result.bytes_out : null;
      toast.success(
        `Compacted ${result.files_merged} files (${result.rows} rows` +
          (shrinkFactor ? `, ${shrinkFactor.toFixed(1)}x smaller)` : ")")
      );
      void queryClient.invalidateQueries({ queryKey: ["observation-stats"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const brokenItems = brokenTables.data?.items ?? [];
  const brokenPageData = brokenItems.slice(
    brokenPage * BROKEN_PAGE_SIZE,
    (brokenPage + 1) * BROKEN_PAGE_SIZE
  );

  const brokenColumns = useMemo<ColumnDef<TableRead, unknown>[]>(
    () => [
      {
        id: "table",
        header: "Table",
        cell: ({ row }) => (
          <Link to={`/tables/${row.original.id}`} className="text-primary hover:underline">
            {qualifiedTableName(row.original)}
          </Link>
        )
      },
      {
        accessorKey: "consecutive_scan_failures",
        header: "Consecutive failures",
        size: 160,
        cell: ({ getValue }) => (
          <Badge variant="danger" className="ml-auto block w-fit">
            {(getValue() as number | undefined) ?? 0}
          </Badge>
        )
      },
      {
        accessorKey: "failure_category",
        header: "Category",
        size: 170,
        cell: ({ row }) =>
          row.original.failure_category_label ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant={failureBadgeVariant(row.original.failure_category)}>
                  {row.original.failure_category_label}
                </Badge>
              </TooltipTrigger>
              <TooltipContent>{row.original.failure_category_guidance}</TooltipContent>
            </Tooltip>
          ) : (
            "—"
          )
      },
      {
        accessorKey: "last_scan_error",
        header: "Last error",
        cell: ({ getValue }) => (
          <span className="block max-w-xs truncate text-muted-foreground" title={String(getValue() ?? "")}>
            {(getValue() as string | null) ?? "—"}
          </span>
        )
      },
      {
        accessorKey: "last_scan_error_at",
        header: "Last failed",
        size: 160,
        cell: ({ getValue }) => formatTime(getValue() as string)
      }
    ],
    []
  );

  const observationColumns = useMemo<ColumnDef<Record<string, unknown>, unknown>[]>(
    () => [
      {
        accessorKey: "recorded_at",
        header: "Time",
        size: 160,
        cell: ({ getValue }) => formatTime(getValue() as string)
      },
      { accessorKey: "event_type", header: "Type", size: 140 },
      {
        accessorKey: "level",
        header: "Level",
        size: 90,
        cell: ({ getValue }) => {
          const level = String(getValue());
          return (
            <Badge variant={level === "error" ? "danger" : level === "warn" ? "warning" : "secondary"}>
              {level}
            </Badge>
          );
        }
      },
      { accessorKey: "subject", header: "Subject" },
      {
        accessorKey: "actor",
        header: "Actor",
        size: 140,
        cell: ({ getValue }) => (getValue() ? String(getValue()) : "—")
      },
      {
        accessorKey: "correlation_id",
        header: "Correlation",
        size: 140,
        cell: ({ getValue }) => (getValue() ? <code className="text-xs">{String(getValue()).slice(0, 8)}</code> : "—")
      }
    ],
    []
  );

  return (
    <div className="grid grid-cols-1 gap-3">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle>Broken tables</CardTitle>
          <Badge variant={brokenItems.length ? "danger" : "secondary"}>{brokenItems.length}</Badge>
        </CardHeader>
        <CardContent>
          <QueryErrorAlert error={brokenTables.error} title="Failed to load broken tables" />
          <AsyncBoundary
            isLoading={brokenTables.isLoading}
            isError={Boolean(brokenTables.error)}
            error={brokenTables.error}
          >
            <DataTable
              columns={brokenColumns}
              data={brokenPageData}
              density="compact"
              getRowId={(row) => row.id ?? `${row.source_id}-${row.schema_name}-${row.table_name}`}
              emptyTitle="No broken tables"
              emptyDescription={`Tables that fail to scan ${BROKEN_TABLE_FAILURE_THRESHOLD}+ times in a row (e.g. a stale metadata pointer) show up here.`}
              pagination={
                brokenItems.length > BROKEN_PAGE_SIZE
                  ? {
                      pageIndex: brokenPage,
                      pageSize: BROKEN_PAGE_SIZE,
                      totalRows: brokenItems.length,
                      onPageChange: setBrokenPage
                    }
                  : undefined
              }
            />
          </AsyncBoundary>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle>Observation lakehouse</CardTitle>
          {role === "admin" ? (
            <ConfirmAction
              title="Compact observation history?"
              description="Rewrites each hour file in place and drops duplicate event ids. It does not merge across hours."
              onConfirm={() => compactObservationsMutation.mutate()}
            >
              <Button size="sm" loading={compactObservationsMutation.isPending}>
                Compact now
              </Button>
            </ConfirmAction>
          ) : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            Append-only archive of every actuation (stimulus / response / context) for incident review and audit —
            separate from the Postgres audit log.
          </p>
          <QueryErrorAlert error={observationStats.error} title="Failed to load observation stats" />
          {observationStats.data ? (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <div>
                <p className="text-sm text-muted-foreground">Files</p>
                <p>{observationStats.data.total_files.toLocaleString()}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Size</p>
                <p>{formatBytesGb(observationStats.data.total_bytes)}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Oldest event</p>
                <p>
                  {observationStats.data.oldest_event ? formatTime(observationStats.data.oldest_event) : "—"}
                </p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Newest event</p>
                <p>
                  {observationStats.data.newest_event ? formatTime(observationStats.data.newest_event) : "—"}
                </p>
              </div>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <TableSearchSelect
              className="min-w-[260px]"
              value={observationFilter.table_id ?? ""}
              onChange={(tableId) =>
                setObservationFilter((prev) => ({
                  ...prev,
                  table_id: tableId || undefined
                }))
              }
              placeholder="Filter by table"
            />
            <select
              className="h-[var(--control-height)] min-w-[200px] rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
              value={observationFilter.event_type ?? ""}
              onChange={(event) =>
                setObservationFilter((prev) => ({
                  ...prev,
                  event_type: event.target.value || undefined
                }))
              }
            >
              <option value="">Event type</option>
              <option value="maintenance_run">Maintenance run</option>
              <option value="scan">Scan</option>
              <option value="approval">Approval</option>
              <option value="trigger">Signal trigger</option>
              <option value="ai_recommendation">AI recommendation</option>
            </select>
          </div>

          <QueryErrorAlert error={observationSearch.error} title="Failed to search observations" />
          <AsyncBoundary
            isLoading={observationSearch.isLoading}
            isError={Boolean(observationSearch.error)}
            error={observationSearch.error}
          >
            <div className="max-h-80 overflow-y-auto">
              <DataTable
                columns={observationColumns}
                data={(observationSearch.data?.items ?? []) as unknown as Record<string, unknown>[]}
                density="compact"
                getRowId={(row) => String(row.id)}
                emptyTitle="No observations found"
                emptyDescription="Adjust the filters above, or nothing has been recorded yet for this scope."
              />
            </div>
          </AsyncBoundary>
          {observationSearch.data ? (
            <p className="text-sm text-muted-foreground">
              Showing {observationSearch.data.items.length} of {observationSearch.data.total.toLocaleString()} matching
              events
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
};
