import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { CheckCircle2, Clock, Database, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { getQueryPerformanceDashboard, type QueryPerformanceData } from "../api/client";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { PageHeader, TelemetryRequiredNotice } from "../components/common";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { DataTable } from "../components/ui/data-table";
import { MetricTile } from "../components/ui/stat";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatDuration } from "../lib/format";

type QueryRow = QueryPerformanceData["queries"][number];

const durationBadgeVariant = (ms: number): "danger" | "warning" | "success" => {
  if (ms > 10000) return "danger";
  if (ms > 5000) return "warning";
  return "success";
};

const KpiSkeleton = () => (
  <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
    {Array.from({ length: 6 }).map((_, index) => (
      <MetricTile key={index} label="Loading" value="—" loading />
    ))}
  </div>
);

export const QueryPerformance = () => {
  const { api } = useAuth();
  const [engine, setEngine] = useState<string | undefined>(undefined);
  const [hours, setHours] = useState(24);

  const { data, isLoading, error, refetch } = useQuery<QueryPerformanceData>({
    queryKey: ["query-performance", api.token, engine, hours],
    queryFn: () => getQueryPerformanceDashboard(api, hours, engine),
    refetchInterval: 30000
  });

  const columns: ColumnDef<QueryRow, unknown>[] = [
    {
      id: "query_text",
      header: "Query",
      cell: ({ row }) => {
        const text = row.original.query_text;
        const preview = text ? text.slice(0, 80) + (text.length > 80 ? "..." : "") : "—";
        return (
          <Tooltip>
            <TooltipTrigger asChild>
              <code className="text-[11px]">{preview}</code>
            </TooltipTrigger>
            {text ? <TooltipContent className="max-w-md break-all">{text}</TooltipContent> : null}
          </Tooltip>
        );
      }
    },
    {
      id: "engine",
      header: "Engine",
      cell: ({ row }) => <Badge variant="secondary">{row.original.engine.toUpperCase()}</Badge>
    },
    {
      id: "duration_ms",
      header: "Duration",
      cell: ({ row }) => (
        <Badge variant={durationBadgeVariant(row.original.duration_ms)}>
          <Clock className="mr-1 size-3" aria-hidden="true" />
          {formatDuration(row.original.duration_ms)}
        </Badge>
      )
    },
    {
      id: "bytes_scanned",
      header: "Data Scanned",
      cell: ({ row }) => formatBytesGb(row.original.bytes_scanned)
    },
    {
      id: "rows_produced",
      header: "Rows",
      cell: ({ row }) => (row.original.rows_produced ? row.original.rows_produced.toLocaleString() : "—")
    },
    {
      id: "status",
      header: "Status",
      cell: ({ row }) => {
        const failed = row.original.status === "failed";
        return (
          <Badge variant={failed ? "danger" : "success"}>
            {failed ? (
              <TriangleAlert className="mr-1 size-3" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="mr-1 size-3" aria-hidden="true" />
            )}
            {row.original.status.toUpperCase()}
          </Badge>
        );
      }
    },
    {
      id: "table_name",
      header: "Table",
      cell: ({ row }) => {
        const name = row.original.table_name;
        if (!name) return "—";
        if (row.original.table_id) {
          return (
            <Link to={`/tables/${row.original.table_id}`} title="Open table detail" className="hover:text-primary">
              {name}
            </Link>
          );
        }
        return (
          <Link to="/catalogs" title="Open catalog explorer to locate this table" className="hover:text-primary">
            {name}
          </Link>
        );
      }
    }
  ];

  return (
    <div>
      <PageHeader
        title="Query performance"
        subtitle="Monitor query latency, throughput, and failure rates."
        actions={
          <div className="flex flex-wrap gap-2">
            <select
              className="h-[var(--control-height)] rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
              value={engine ?? ""}
              onChange={(event) => setEngine(event.target.value || undefined)}
            >
              <option value="">All Engines</option>
              <option value="trino">Trino</option>
              <option value="spark">Spark</option>
              <option value="athena">Athena</option>
              <option value="duckdb">DuckDB</option>
            </select>
            <select
              className="h-[var(--control-height)] rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
              value={hours}
              onChange={(event) => setHours(Number(event.target.value))}
            >
              <option value={1}>Last 1 hour</option>
              <option value={6}>Last 6 hours</option>
              <option value={24}>Last 24 hours</option>
              <option value={168}>Last 7 days</option>
            </select>
          </div>
        }
      />

      <AsyncBoundary
        isLoading={isLoading}
        isError={Boolean(error)}
        error={error}
        onRetry={() => void refetch()}
        skeleton={<KpiSkeleton />}
      >
        <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
          <MetricTile label="Total queries" value={data?.total ?? 0} suffix="queries" />
          <MetricTile label="P50 latency" value={`${data?.p50_latency_ms ?? 0} ms`} />
          <MetricTile label="P95 latency" value={`${data?.p95_latency_ms ?? 0} ms`} />
          <MetricTile label="P99 latency" value={`${data?.p99_latency_ms ?? 0} ms`} />
          <MetricTile label="Avg data scanned" value={formatBytesGb(data?.avg_bytes_scanned ?? null)} />
          <MetricTile
            label="Failed"
            value={`${data?.failed_count ?? 0} / ${data?.total ?? 0}`}
            emphasis={(data?.failed_count ?? 0) > 0}
          />
        </div>

        {(data?.total ?? 0) === 0 ? (
          <TelemetryRequiredNotice surface="Query performance" />
        ) : (
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle>Top slowest queries</CardTitle>
              <Database className="size-4 text-muted-foreground" aria-hidden="true" />
            </CardHeader>
            <CardContent className="p-0 pt-0">
              <DataTable
                columns={columns}
                data={data?.queries ?? []}
                getRowId={(row) => row.query_id}
                emptyTitle="No queries recorded"
                emptyDescription="Query telemetry will appear here once engines report execution stats."
              />
            </CardContent>
          </Card>
        )}
      </AsyncBoundary>
    </div>
  );
};
