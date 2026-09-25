import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Search,
  Wrench,
  TriangleAlert,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  XAxis,
  YAxis
} from "recharts";
import { Link, useSearchParams } from "react-router-dom";
import { getSlaStatusDashboard, type SLAResponse } from "../api/client";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { PageHeader } from "../components/common";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { DataTable } from "../components/ui/data-table";
import { Input } from "../components/ui/input";
import { Switch } from "../components/ui/switch";
import { MetricTile } from "../components/ui/stat";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { useAuth } from "../lib/auth";
import { formatSlaDays as formatDays, formatSlaHours as formatHours, formatTime } from "../lib/format";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../lib/theme";

type SLAStatusRow = SLAResponse["slas"][number];
const LOCAL_TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;

const ProgressBar = ({
  percent,
  tone
}: {
  percent: number;
  tone: "good" | "bad";
}) => (
  <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-surface-sunken)]">
    <div
      className="h-full rounded-full transition-all"
      style={{
        width: `${Math.min(100, Math.max(0, percent))}%`,
        backgroundColor: tone === "bad" ? cryoColors.gradeF : cryoColors.gradeA
      }}
    />
  </div>
);

const formatLatency = (ms: number | null) => {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
};

const getCompliancePercent = (actual: number | null, sla: number | null): number => {
  if (actual === null || sla === null || sla === 0) return 100;
  return Math.min(100, Math.max(0, 100 - (actual / sla) * 100));
};

const isRowBreached = (row: SLAStatusRow) =>
  row.freshness_breached || row.maintenance_breached || row.p95_latency_breached;

interface SchemaMatrixRow {
  schema_name: string;
  tables: number;
  breached: number;
  compliancePercent: number;
}

export const FRESHNESS_BUCKETS: Array<{ key: string; label: string; min: number; max: number }> = [
  { key: "lt1h", label: "< 1h", min: 0, max: 1 },
  { key: "1to6h", label: "1–6h", min: 1, max: 6 },
  { key: "6to24h", label: "6–24h", min: 6, max: 24 },
  { key: "1to3d", label: "1–3d", min: 24, max: 72 },
  { key: "3to7d", label: "3–7d", min: 72, max: 168 },
  { key: "gt7d", label: "> 7d", min: 168, max: Infinity }
];

export const bucketForFreshness = (hours: number | null): string | null => {
  if (hours === null) return null;
  const bucket = FRESHNESS_BUCKETS.find((b) => hours >= b.min && hours < b.max);
  return bucket?.key ?? null;
};

const KpiSkeleton = () => (
  <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
    {Array.from({ length: 4 }).map((_, index) => (
      <MetricTile key={index} label="Loading" value="—" loading />
    ))}
  </div>
);

const breachCount = (record: SLAStatusRow) =>
  [record.freshness_breached, record.maintenance_breached, record.p95_latency_breached].filter(Boolean).length;

export const SLAStatus = () => {
  const { api } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  const breachedOnly = searchParams.get("breached") === "1";
  const tableSearch = searchParams.get("q") ?? "";
  const freshnessBucket = searchParams.get("bucket");
  const pageIndex = Number(searchParams.get("page") ?? "0") || 0;
  const [pageSize, setPageSize] = useState(20);
  const [schemaPageIndex, setSchemaPageIndex] = useState(0);

  const updateParams = (updates: Record<string, string | null>) => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === "") params.delete(key);
        else params.set(key, value);
      }
      return params;
    });
  };

  const setBreachedOnly = (next: boolean) => updateParams({ breached: next ? "1" : null, page: null });
  const setTableSearch = (next: string) => updateParams({ q: next || null, page: null });
  const setFreshnessBucket = (next: string | null) => updateParams({ bucket: next, page: null });
  const setPageIndex = (next: number) => updateParams({ page: next ? String(next) : null });

  const { data, isLoading, error, refetch, dataUpdatedAt } = useQuery<SLAResponse>({
    queryKey: ["sla-status", api.token, breachedOnly],
    queryFn: () => getSlaStatusDashboard(api, breachedOnly),
    refetchInterval: 60000
  });

  const slas = data?.slas ?? [];

  const freshnessHistogram = useMemo(
    () =>
      FRESHNESS_BUCKETS.map((bucket) => ({
        key: bucket.key,
        label: bucket.label,
        count: slas.filter((row) => bucketForFreshness(row.freshness_hours) === bucket.key).length
      })),
    [slas]
  );

  const visibleRows = useMemo(() => {
    const query = tableSearch.trim().toLowerCase();
    return slas.filter((row) => {
      if (freshnessBucket && bucketForFreshness(row.freshness_hours) !== freshnessBucket) return false;
      if (query && !row.table_name.toLowerCase().includes(query)) return false;
      return true;
    });
  }, [slas, tableSearch, freshnessBucket]);

  const [announcement, setAnnouncement] = useState("");
  const previousBreachedRef = useRef<number | null>(null);
  const previousFilterRef = useRef<{ tableSearch: string; freshnessBucket: string | null; count: number } | null>(
    null
  );

  useEffect(() => {
    if (!data) return;
    const total = data.total_breached;
    const prev = previousBreachedRef.current;
    if (prev !== null && prev !== total) {
      setAnnouncement(
        total === 0
          ? "All SLA breaches cleared."
          : total > prev
            ? `${total} table(s) now breach SLA thresholds, up from ${prev}.`
            : `${total} table(s) now breach SLA thresholds, down from ${prev}.`
      );
    }
    previousBreachedRef.current = total;
  }, [data]);

  useEffect(() => {
    if (!data) return;
    const prev = previousFilterRef.current;
    const changedFilter = !prev || prev.tableSearch !== tableSearch || prev.freshnessBucket !== freshnessBucket;
    if (changedFilter) {
      const timer = window.setTimeout(() => {
        setAnnouncement(
          `Showing ${visibleRows.length} table${visibleRows.length === 1 ? "" : "s"} of ${slas.length}.`
        );
        previousFilterRef.current = { tableSearch, freshnessBucket, count: visibleRows.length };
      }, 400);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [data, tableSearch, freshnessBucket, visibleRows.length, slas.length]);

  const breachByType = useMemo(
    () => ({
      freshness: slas.filter((s) => s.freshness_breached).length,
      maintenance: slas.filter((s) => s.maintenance_breached).length,
      latency: slas.filter((s) => s.p95_latency_breached).length
    }),
    [slas]
  );

  const breachedWithoutPreset = useMemo(
    () => slas.filter((s) => !s.has_preset && isRowBreached(s)).length,
    [slas]
  );

  const schemaMatrix: SchemaMatrixRow[] = useMemo(() => {
    if (breachedOnly) return [];
    const bySchema = new Map<string, { tables: number; breached: number }>();
    for (const row of slas) {
      const entry = bySchema.get(row.schema_name) ?? { tables: 0, breached: 0 };
      entry.tables += 1;
      if (isRowBreached(row)) entry.breached += 1;
      bySchema.set(row.schema_name, entry);
    }
    return Array.from(bySchema.entries())
      .map(([schema_name, v]) => ({
        schema_name,
        tables: v.tables,
        breached: v.breached,
        compliancePercent: ((v.tables - v.breached) / Math.max(1, v.tables)) * 100
      }))
      .sort((a, b) => a.compliancePercent - b.compliancePercent);
  }, [slas, breachedOnly]);

  const slaColumns: ColumnDef<SLAStatusRow, unknown>[] = [
    {
      id: "table_name",
      header: "Table",
      cell: ({ row }) => (
        <Link to={`/tables/${row.original.table_id}`} className="hover:text-primary">
          {row.original.table_name}
        </Link>
      )
    },
    {
      id: "freshness",
      header: "Data freshness",
      cell: ({ row }) => {
        const record = row.original;
        const breached = record.freshness_breached;
        const percent = getCompliancePercent(record.freshness_hours, record.freshness_sla_hours);
        return (
          <div className="min-w-[10rem] space-y-1">
            <div className="flex items-center gap-1.5 text-sm">
              {breached ? (
                <TriangleAlert className="size-3.5 text-[var(--grade-f)]" aria-hidden="true" />
              ) : (
                <CheckCircle2 className="size-3.5 text-[var(--grade-a)]" aria-hidden="true" />
              )}
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className={breached ? "text-destructive" : "text-muted-foreground"}>
                    {formatHours(record.freshness_hours)} / {formatHours(record.freshness_sla_hours)}
                  </span>
                </TooltipTrigger>
                <TooltipContent>Hours since last Iceberg snapshot commit</TooltipContent>
              </Tooltip>
            </div>
            {record.scan_staleness_hours != null ? (
              <p className="text-[11px] text-muted-foreground">
                Scan lag: {formatHours(record.scan_staleness_hours)}
              </p>
            ) : null}
            <ProgressBar percent={percent} tone={breached ? "bad" : "good"} />
          </div>
        );
      }
    },
    {
      id: "maintenance",
      header: "Maintenance",
      cell: ({ row }) => {
        const record = row.original;
        const breached = record.maintenance_breached;
        const percent = getCompliancePercent(record.maintenance_days, record.maintenance_sla_days);
        return (
          <div className="min-w-[9rem] space-y-1">
            <div className="flex items-center gap-1.5 text-sm">
              {breached ? (
                <TriangleAlert className="size-3.5 text-[var(--grade-f)]" aria-hidden="true" />
              ) : (
                <CheckCircle2 className="size-3.5 text-[var(--grade-a)]" aria-hidden="true" />
              )}
              <span className={breached ? "text-destructive" : "text-muted-foreground"}>
                {formatDays(record.maintenance_days)} / {formatDays(record.maintenance_sla_days)}
              </span>
            </div>
            <ProgressBar percent={percent} tone={breached ? "bad" : "good"} />
          </div>
        );
      }
    },
    {
      id: "latency",
      header: "P95 Latency",
      cell: ({ row }) => {
        const record = row.original;
        const breached = record.p95_latency_breached;
        const percent = getCompliancePercent(record.p95_latency_ms, record.p95_latency_sla_ms);
        return (
          <div className="min-w-[9rem] space-y-1">
            <div className="flex items-center gap-1.5 text-sm">
              {breached ? (
                <TriangleAlert className="size-3.5 text-[var(--grade-f)]" aria-hidden="true" />
              ) : (
                <CheckCircle2 className="size-3.5 text-[var(--grade-a)]" aria-hidden="true" />
              )}
              <span className={breached ? "text-destructive" : "text-muted-foreground"}>
                {formatLatency(record.p95_latency_ms)} / {formatLatency(record.p95_latency_sla_ms)}
              </span>
            </div>
            <ProgressBar percent={percent} tone={breached ? "bad" : "good"} />
          </div>
        );
      }
    },
    {
      id: "coverage",
      header: "Coverage",
      cell: ({ row }) =>
        row.original.has_preset ? (
          <Badge variant="secondary">Assigned</Badge>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="warning">No preset</Badge>
            </TooltipTrigger>
            <TooltipContent>
              No preset assignment or matching rule — maintenance SLA can never be met until one is configured.
            </TooltipContent>
          </Tooltip>
        )
    },
    {
      id: "status",
      header: "Status",
      cell: ({ row }) => {
        const count = breachCount(row.original);
        if (count === 0) {
          return (
            <Badge variant="success">
              <CheckCircle2 className="mr-1 size-3" aria-hidden="true" />
              OK
            </Badge>
          );
        }
        if (count === 1) {
          return (
            <Badge variant="warning">
              <AlertTriangle className="mr-1 size-3" aria-hidden="true" />
              WARNING
            </Badge>
          );
        }
        return (
          <Badge variant="danger">
            <TriangleAlert className="mr-1 size-3" aria-hidden="true" />
            CRITICAL
          </Badge>
        );
      }
    }
  ];

  const schemaColumns: ColumnDef<SchemaMatrixRow, unknown>[] = [
    { id: "schema_name", header: "Schema", cell: ({ row }) => row.original.schema_name },
    { id: "tables", header: "Tables", cell: ({ row }) => row.original.tables },
    { id: "breached", header: "Breached", cell: ({ row }) => row.original.breached },
    {
      id: "compliance",
      header: "Compliance",
      cell: ({ row }) => (
        <div className="flex items-center gap-2">
          <span className={row.original.compliancePercent < 50 ? "text-destructive" : undefined}>
            {row.original.compliancePercent.toFixed(1)}%
          </span>
          <div className="w-20">
            <ProgressBar
              percent={row.original.compliancePercent}
              tone={row.original.compliancePercent < 50 ? "bad" : "good"}
            />
          </div>
        </div>
      )
    }
  ];

  const complianceRate = data
    ? ((data.total_monitored - data.total_breached) / Math.max(1, data.total_monitored)) * 100
    : 0;

  const pagedVisibleRows = visibleRows.slice(pageIndex * pageSize, (pageIndex + 1) * pageSize);
  const pagedSchemaMatrix = schemaMatrix.slice(schemaPageIndex * 10, (schemaPageIndex + 1) * 10);

  return (
    <div>
      <PageHeader
        title="SLA monitoring"
        subtitle="Freshness uses last Iceberg snapshot commit time; scan staleness shows Cryo scan lag."
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              <span>Show breached only</span>
              <Switch checked={breachedOnly} onCheckedChange={setBreachedOnly} aria-label="Show breached only" />
            </label>
            <Link to="/catalogs">
              <Button variant="secondary">Open table explorer</Button>
            </Link>
          </div>
        }
      />
      <p className="mb-3 text-xs text-muted-foreground" aria-live="off">
        Refreshes every 60s
        {dataUpdatedAt ? ` \u2022 Last refreshed ${formatTime(new Date(dataUpdatedAt).toISOString())} (${LOCAL_TIMEZONE})` : ""}
      </p>
      <span aria-live="polite" className="sr-only">
        {announcement}
      </span>

      <AsyncBoundary
        isLoading={isLoading}
        isError={Boolean(error)}
        error={error}
        onRetry={() => void refetch()}
        skeleton={<KpiSkeleton />}
      >
        <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <MetricTile label="Total monitored" value={data?.total_monitored ?? 0} suffix="tables" />
          <MetricTile label="SLA compliance" value={`${complianceRate.toFixed(1)}%`} />
          <MetricTile
            label="Breached"
            value={`${data?.total_breached ?? 0} / ${data?.total_monitored ?? 0}`}
            emphasis={(data?.total_breached ?? 0) > 0}
          />
          <MetricTile
            label="Healthy"
            value={`${(data?.total_monitored ?? 0) - (data?.total_breached ?? 0)} / ${data?.total_monitored ?? 0}`}
          />
        </div>

        <div className="space-y-3">
          {(data?.total_breached || 0) > 0 && !breachedOnly ? (
            <Alert
              variant="warning"
              title="SLA breaches detected"
              description={`${data?.total_breached} table(s) currently breach SLA thresholds (${breachByType.freshness} freshness, ${breachByType.maintenance} maintenance, ${breachByType.latency} latency). Use breached-only mode for focused triage.`}
            />
          ) : null}

          {breachedWithoutPreset > 0 ? (
            <Alert
              variant="info"
              title="Missing maintenance coverage"
              description={
                <>
                  {breachedWithoutPreset} breached table(s) have no preset assigned or matching rule — their
                  maintenance SLA can never be met until one is configured.{" "}
                  <Link to="/presets" className="text-primary hover:underline">
                    Open Presets
                  </Link>
                </>
              }
            />
          ) : null}

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
              <div>
                <CardTitle>Freshness distribution</CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">
                  Choose a freshness range to filter the table below.
                </p>
              </div>
              {freshnessBucket ? (
                <Button size="sm" variant="secondary" onClick={() => setFreshnessBucket(null)}>
                  Clear filter
                </Button>
              ) : null}
            </CardHeader>
            <CardContent className="space-y-3">
              <div
                className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6"
                role="group"
                aria-label="Filter tables by freshness range"
              >
                {freshnessHistogram.map((bucket) => (
                  <Button
                    key={bucket.key}
                    type="button"
                    size="sm"
                    variant={freshnessBucket === bucket.key ? "primary" : "secondary"}
                    className="h-auto min-h-9 justify-between gap-2 px-3 py-2"
                    aria-label={`Filter to ${bucket.label}: ${bucket.count} table${bucket.count === 1 ? "" : "s"}`}
                    aria-pressed={freshnessBucket === bucket.key}
                    onClick={() => setFreshnessBucket(freshnessBucket === bucket.key ? null : bucket.key)}
                  >
                    <span>{bucket.label}</span>
                    <span className="font-mono text-xs">{bucket.count}</span>
                  </Button>
                ))}
              </div>
              <p className="sr-only">
                Freshness distribution:{" "}
                {freshnessHistogram.map((bucket) => `${bucket.label} ${bucket.count}`).join(", ")}.
              </p>
              <div aria-hidden="true" inert>
                <ResponsiveContainer width="100%" height={160}>
                  <BarChart data={freshnessHistogram} margin={{ top: 16, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke={cryoColors.chartGrid} vertical={false} />
                    <XAxis dataKey="label" stroke={cryoColors.inkFaint} tick={chartAxisTick} />
                    <YAxis stroke={cryoColors.inkFaint} tick={chartAxisTick} allowDecimals={false} width={32} />
                    <RechartsTooltip {...chartTooltipProps} />
                    <Bar
                      dataKey="count"
                      name="Tables"
                      cursor="pointer"
                      radius={[2, 2, 0, 0]}
                      onClick={(point) => {
                        const key = (point?.payload as { key?: string } | undefined)?.key;
                        if (!key) return;
                        setFreshnessBucket(freshnessBucket === key ? null : key);
                      }}
                    >
                      {freshnessHistogram.map((bucket) => (
                        <Cell
                          key={bucket.key}
                          fill={
                            freshnessBucket === null || freshnessBucket === bucket.key
                              ? cryoColors.primary
                              : cryoColors.chartGrid
                          }
                        />
                      ))}
                      <LabelList dataKey="count" position="top" fontSize={11} fill={cryoColors.inkFaint} />
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          {schemaMatrix.length > 0 ? (
            <details className="rounded-[var(--radius-default)] border border-border">
              <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
                SLA matrix by schema ({schemaMatrix.length})
              </summary>
              <div className="border-t border-border p-0">
                <DataTable
                  columns={schemaColumns}
                  data={pagedSchemaMatrix}
                  getRowId={(row) => row.schema_name}
                  pagination={{
                    pageIndex: schemaPageIndex,
                    pageSize: 10,
                    totalRows: schemaMatrix.length,
                    onPageChange: setSchemaPageIndex
                  }}
                />
              </div>
            </details>
          ) : null}

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle>SLA status by table</CardTitle>
              <div className="relative w-full max-w-[220px]">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search table name"
                  value={tableSearch}
                  onChange={(e) => setTableSearch(e.target.value)}
                  className="h-8 pl-8 text-sm"
                />
              </div>
            </CardHeader>
            <CardContent className="space-y-3 p-0 pt-0">
              <div className="flex flex-wrap gap-2 px-4">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant="secondary">
                      <Clock className="mr-1 size-3" aria-hidden="true" />
                      Freshness
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>Last Iceberg snapshot commit vs SLA threshold</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant="secondary">
                      <Wrench className="mr-1 size-3" aria-hidden="true" />
                      Maintenance
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>Days since last maintenance</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant="secondary">
                      <Zap className="mr-1 size-3" aria-hidden="true" />
                      Query latency
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>P95 query latency in last 24h</TooltipContent>
                </Tooltip>
              </div>
              <DataTable
                columns={slaColumns}
                data={pagedVisibleRows}
                getRowId={(row) => row.table_id}
                getRowClassName={(row) => {
                  const count = breachCount(row);
                  return count >= 2 ? "row-critical" : count === 1 ? "row-warning" : "";
                }}
                pagination={{
                  pageIndex,
                  pageSize,
                  totalRows: visibleRows.length,
                  onPageChange: setPageIndex,
                  onPageSizeChange: (size) => {
                    setPageSize(size);
                    setPageIndex(0);
                  }
                }}
              />
            </CardContent>
          </Card>
        </div>
      </AsyncBoundary>
    </div>
  );
};
