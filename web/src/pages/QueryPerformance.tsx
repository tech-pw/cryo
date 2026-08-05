import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { CheckCircle2, Clock, Copy, Database, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getQueryPerformanceDashboard, type QueryPerformanceData } from "../api/client";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { PageHeader, TelemetryRequiredNotice } from "../components/common";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { DataTable } from "../components/ui/data-table";
import { Label } from "../components/ui/label";
import { Select } from "../components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../components/ui/sheet";
import { MetricTile } from "../components/ui/stat";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatDuration, formatTime } from "../lib/format";

type QueryRow = QueryPerformanceData["queries"][number];

const durationBadgeVariant = (ms: number): "danger" | "warning" | "success" => {
  if (ms > 10000) return "danger";
  if (ms > 5000) return "warning";
  return "success";
};

const ENGINE_OPTIONS = [
  { label: "Trino", value: "trino" },
  { label: "Spark", value: "spark" },
  { label: "Athena", value: "athena" },
  { label: "DuckDB", value: "duckdb" }
];

const HOURS_OPTIONS = [
  { label: "Last 1 hour", value: "1" },
  { label: "Last 6 hours", value: "6" },
  { label: "Last 24 hours", value: "24" },
  { label: "Last 7 days", value: "168" }
];

const KpiSkeleton = () => (
  <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
    {Array.from({ length: 6 }).map((_, index) => (
      <MetricTile key={index} label="Loading" value="—" loading />
    ))}
  </div>
);

export const QueryPerformance = () => {
  const { api } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "error">("idle");

  const engine = searchParams.get("engine") ?? undefined;
  const hoursParam = Number(searchParams.get("hours") ?? "24");
  const hours = HOURS_OPTIONS.some((option) => Number(option.value) === hoursParam) ? hoursParam : 24;
  const selectedQueryId = searchParams.get("query") ?? undefined;

  const setEngine = (next: string | undefined) => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      if (next) params.set("engine", next);
      else params.delete("engine");
      return params;
    });
  };

  const setHours = (next: number) => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.set("hours", String(next));
      return params;
    });
  };

  const openQueryDetail = (queryId: string) => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.set("query", queryId);
      return params;
    });
  };

  const closeQueryDetail = () => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.delete("query");
      return params;
    });
  };

  const { data, isLoading, error, refetch } = useQuery<QueryPerformanceData>({
    queryKey: ["query-performance", api.token, engine, hours],
    queryFn: () => getQueryPerformanceDashboard(api, hours, engine),
    refetchInterval: 30000
  });

  const selectedQuery = useMemo(
    () => data?.queries.find((row) => row.query_id === selectedQueryId),
    [data, selectedQueryId]
  );

  const handleCopyQuery = (text: string) => {
    navigator.clipboard
      .writeText(text)
      .then(() => setCopyStatus("copied"))
      .catch(() => setCopyStatus("error"));
    window.setTimeout(() => setCopyStatus("idle"), 2000);
  };

  const columns: ColumnDef<QueryRow, unknown>[] = [
    {
      id: "query_text",
      header: "Query",
      cell: ({ row }) => {
        const text = row.original.query_text;
        const preview = text ? text.slice(0, 80) + (text.length > 80 ? "..." : "") : "—";
        if (!text) return <code className="text-[11px] text-muted-foreground">{preview}</code>;
        return (
          <Button
            variant="ghost"
            size="sm"
            className="h-auto whitespace-normal p-0 text-left font-normal"
            onClick={() => openQueryDetail(row.original.query_id)}
            aria-label="View full query text and details"
          >
            <code className="text-[11px]">{preview}</code>
          </Button>
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
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <Button variant="outline" size="sm" onClick={() => openQueryDetail(row.original.query_id)}>
          Details
        </Button>
      )
    }
  ];

  return (
    <div>
      <PageHeader
        title="Query performance"
        subtitle="Monitor query latency, throughput, and failure rates."
        actions={
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="qp-engine-filter">Engine</Label>
              <Select
                id="qp-engine-filter"
                value={engine ?? ""}
                onChange={setEngine}
                options={ENGINE_OPTIONS}
                allowClear
                placeholder="All engines"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="qp-hours-filter">Time range</Label>
              <Select
                id="qp-hours-filter"
                value={String(hours)}
                onChange={(next) => setHours(Number(next ?? 24))}
                options={HOURS_OPTIONS}
              />
            </div>
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

      <Sheet open={Boolean(selectedQueryId)} onOpenChange={(open) => !open && closeQueryDetail()}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>Query detail</SheetTitle>
          </SheetHeader>
          {selectedQuery ? (
            <div className="mt-4 flex flex-col gap-4">
              <div className="flex flex-wrap gap-2">
                <Badge variant="secondary">{selectedQuery.engine.toUpperCase()}</Badge>
                <Badge variant={durationBadgeVariant(selectedQuery.duration_ms)}>
                  <Clock className="mr-1 size-3" aria-hidden="true" />
                  {formatDuration(selectedQuery.duration_ms)}
                </Badge>
                <Badge variant={selectedQuery.status === "failed" ? "danger" : "success"}>
                  {selectedQuery.status === "failed" ? (
                    <TriangleAlert className="mr-1 size-3" aria-hidden="true" />
                  ) : (
                    <CheckCircle2 className="mr-1 size-3" aria-hidden="true" />
                  )}
                  {selectedQuery.status.toUpperCase()}
                </Badge>
              </div>

              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs text-muted-foreground">Started</dt>
                  <dd>{formatTime(selectedQuery.started_at)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">User</dt>
                  <dd>{selectedQuery.user ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Data scanned</dt>
                  <dd>{formatBytesGb(selectedQuery.bytes_scanned)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Rows produced</dt>
                  <dd>{selectedQuery.rows_produced ? selectedQuery.rows_produced.toLocaleString() : "—"}</dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-xs text-muted-foreground">Table</dt>
                  <dd>
                    {selectedQuery.table_name ? (
                      <Link
                        to={selectedQuery.table_id ? `/tables/${selectedQuery.table_id}` : "/catalogs"}
                        className="hover:text-primary"
                      >
                        {selectedQuery.table_name}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </dd>
                </div>
              </dl>

              <div>
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-muted-foreground">Full query text</span>
                  {selectedQuery.query_text ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleCopyQuery(selectedQuery.query_text ?? "")}
                    >
                      <Copy className="mr-1 size-3" aria-hidden="true" />
                      {copyStatus === "copied" ? "Copied" : copyStatus === "error" ? "Copy failed" : "Copy"}
                    </Button>
                  ) : null}
                </div>
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-[var(--radius-default)] border border-border bg-[var(--color-surface-sunken)] p-3 text-xs">
                  {selectedQuery.query_text ?? "No query text captured for this execution."}
                </pre>
              </div>
            </div>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">
              This query is no longer present in the current results window.
            </p>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
};
