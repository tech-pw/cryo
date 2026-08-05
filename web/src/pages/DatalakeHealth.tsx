import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis
} from "recharts";
import { Link } from "react-router-dom";
import { getDatalakeDashboard, getFleetTrends, getSmartMaintenanceOverview, listTables } from "../api/client";
import type {
  DatalakeDashboard,
  FleetCostTrendPoint,
  FleetHealthTrendPoint,
  FreshnessBucketKey,
  TableRead
} from "../api/types";
import {
  ChartFrame,
  EmptyState,
  QueryErrorAlert,
  FirstRunEmptyState,
  TruncatedResultsBanner
} from "../components/common";
import { GradeDistributionMiniBar } from "../components/GradeDistributionMiniBar";
import { GradePill, PriorityTag } from "../components/status";
import { Alert } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { ChartDataTable } from "../components/ui/chart-data-table";
import { MetricGrid } from "../components/ui/metric-grid";
import { PageHeader } from "../components/ui/page-header";
import { Skeleton } from "../components/ui/skeleton";
import { MetricTile } from "../components/ui/stat";
import { useAuth } from "../lib/auth";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../lib/theme";
import { formatBytesGb, formatCurrency, formatGb, formatNumber, formatTime } from "../lib/format";
import {
  BROKEN_TABLE_FAILURE_THRESHOLD,
  FRESHNESS_LABELS,
  freshnessCatalogHref
} from "../lib/freshness";
import { qualifiedTableName } from "../lib/tableLabels";

const TOP_OFFENDERS_LIMIT = 50;
const TREND_EMPTY_TITLE = "Trend available after your next scan";
const TREND_EMPTY_DESCRIPTION =
  "Fleet-wide trends need at least two daily scan snapshots. Run another scan to see health and cost movement.";

const formatTrendDay = (day: string): string => {
  const date = new Date(`${day}T00:00:00`);
  if (Number.isNaN(date.getTime())) return day;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
};

const ChartPanel = ({
  title,
  extra,
  children
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
}) => (
  <Card>
    <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
      <CardTitle className="text-sm font-semibold">{title}</CardTitle>
      {extra ? <div className="text-xs text-muted-foreground">{extra}</div> : null}
    </CardHeader>
    <CardContent>{children}</CardContent>
  </Card>
);

const FleetTrendChartPanel = <T extends FleetHealthTrendPoint | FleetCostTrendPoint>({
  title,
  points,
  ariaLabel,
  children,
  dataTable
}: {
  title: string;
  points: T[];
  ariaLabel: string;
  children: ReactNode;
  dataTable?: ReactNode;
}) => (
  <ChartPanel title={title}>
    {points.length < 2 ? (
      <EmptyState title={TREND_EMPTY_TITLE} description={TREND_EMPTY_DESCRIPTION} />
    ) : (
      <>
        <ChartFrame empty={false} ariaLabel={ariaLabel}>
          {children}
        </ChartFrame>
        {dataTable}
      </>
    )}
  </ChartPanel>
);

const HealthPageSkeleton = () => (
  <div className="space-y-4">
    <MetricGrid columns={2}>
      {Array.from({ length: 2 }).map((_, index) => (
        <Skeleton key={index} className="h-[88px] rounded-[var(--radius-default)]" />
      ))}
    </MetricGrid>
    <Skeleton className="h-8 w-full max-w-xl" />
    <MetricGrid columns={4}>
      {Array.from({ length: 4 }).map((_, index) => (
        <Skeleton key={index} className="h-[88px] rounded-[var(--radius-default)]" />
      ))}
    </MetricGrid>
    <div className="grid gap-3 lg:grid-cols-2">
      <Skeleton className="h-[320px] rounded-[var(--radius-default)]" />
      <Skeleton className="h-[320px] rounded-[var(--radius-default)]" />
    </div>
  </div>
);

export const DatalakeHealth = () => {
  const { api, token } = useAuth();
  const dashboard = useQuery({
    queryKey: ["dashboard", token, TOP_OFFENDERS_LIMIT],
    queryFn: () => getDatalakeDashboard(api, { top_limit: TOP_OFFENDERS_LIMIT }),
    retry: 1
  });
  const tables = useQuery({
    queryKey: ["dashboard-fallback-tables", token],
    queryFn: () => listTables(api, { limit: 500, offset: 0 }),
    enabled: dashboard.isError
  });
  const smartOverview = useQuery({
    queryKey: ["smart-maintenance-overview", token],
    queryFn: () => getSmartMaintenanceOverview(api),
    retry: 1
  });
  const fleetTrends = useQuery({
    queryKey: ["fleet-trends", token],
    queryFn: () => getFleetTrends(api, { days: 30 }),
    retry: 1
  });

  const data = dashboard.data ?? deriveDashboard(tables.data?.items ?? []);
  const topOffenders = data.top_offenders ?? [];
  const tableTotal = tables.data?.total;
  const healthTrend = fleetTrends.data?.health_trend ?? [];
  const costTrend = fleetTrends.data?.cost_trend ?? [];
  const healthTrendChartData = healthTrend.map((point) => ({
    ...point,
    label: formatTrendDay(point.day)
  }));
  const costTrendChartData = costTrend.map((point) => ({
    ...point,
    label: formatTrendDay(point.day)
  }));
  const gradeDistribution = data.grade_distribution ?? [];
  const freshnessAsOf = data.freshness_as_of ?? new Date().toISOString();
  const freshnessDistribution = data.freshness_distribution ?? [];
  const freshnessTotal = freshnessDistribution.reduce((sum, bucket) => sum + bucket.count, 0);
  const freshnessChartData = freshnessDistribution.map((bucket) => ({
    ...bucket,
    percentage: freshnessTotal ? (bucket.count / freshnessTotal) * 100 : 0,
    href: freshnessCatalogHref(bucket.key, freshnessAsOf)
  }));
  const freshnessMaxCount = Math.max(...freshnessChartData.map((bucket) => bucket.count), 1);
  const gradeDfCount = gradeDistribution
    .filter((entry) => entry.grade === "D" || entry.grade === "F")
    .reduce((sum, entry) => sum + entry.count, 0);
  const dfTrendDelta = (() => {
    if (healthTrend.length < 2) return undefined;
    const prev = healthTrend[healthTrend.length - 2].tables_at_grade_df;
    const cur = healthTrend[healthTrend.length - 1].tables_at_grade_df;
    const diff = cur - prev;
    if (diff === 0) return { value: "No change vs prior scan", tone: "neutral" as const };
    return {
      value: `${diff > 0 ? "+" : ""}${diff} vs prior scan`,
      tone: (diff > 0 ? "bad" : "good") as "bad" | "good"
    };
  })();

  return (
    <div>
      <PageHeader
        title="Datalake health"
        subtitle="Datalake posture, cost pressure, and tables most likely to benefit from maintenance."
        actions={
          <>
            <Link to="/catalogs?needsCompaction=1">
              <Button variant="primary">Fix top offenders</Button>
            </Link>
            <Link to="/ai">
              <Button variant="secondary">Intelligence hub</Button>
            </Link>
            <Link to="/cost">
              <Button variant="secondary">View cost savings</Button>
            </Link>
          </>
        }
      />
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {gradeDfCount > 0
          ? `${formatNumber(gradeDfCount, true)} tables at grade D or F. ${formatNumber(data.totals?.needs_compaction ?? 0)} tables need compaction.`
          : "No tables currently at grade D or F."}
      </p>
      {dashboard.isError ? (
        <Alert
          className="page-alert"
          variant="info"
          title="Dashboard endpoint is not available yet; showing a fallback view from /tables."
        />
      ) : null}
      <QueryErrorAlert error={dashboard.error} title="Failed to load datalake dashboard" />
      <QueryErrorAlert error={fleetTrends.error} title="Failed to load fleet trends" />
      {dashboard.isError ? (
        <TruncatedResultsBanner
          shown={tables.data?.items.length ?? 0}
          total={tableTotal ?? tables.data?.items.length ?? 0}
          noun="tables"
        />
      ) : null}
      {dashboard.isLoading || tables.isLoading ? (
        <HealthPageSkeleton />
      ) : (
        <>
          <MetricGrid columns={2} className="mb-3">
            <MetricTile
              label="Tables at grade D/F"
              value={formatNumber(gradeDfCount, true)}
              emphasis={gradeDfCount > 0}
              delta={dfTrendDelta}
              hint={
                gradeDfCount > 0 ? (
                  <Link to="/catalogs?grades=D,F" className="text-xs text-primary hover:underline">
                    Fix in catalog
                  </Link>
                ) : undefined
              }
            />
            <MetricTile label="Tracked tables" value={formatNumber(data.totals?.tables, true)} />
          </MetricGrid>
          <GradeDistributionMiniBar distribution={gradeDistribution} />
          <h2 className="mb-2 mt-0 text-sm font-semibold text-foreground">Health</h2>
          <MetricGrid columns={4} className="mb-4">
            <MetricTile
              label="Total data size"
              value={formatGb(data.totals?.size_gb)}
              hint={
                <span title="Combined size of every table Cryo is tracking, across all schemas." className="cursor-help">
                  ⓘ
                </span>
              }
            />
            <MetricTile
              label="Small files"
              value={formatNumber(data.totals?.small_files, true)}
              hint={
                <span title="Files under the target size threshold — too many of these slows down queries." className="cursor-help">
                  ⓘ
                </span>
              }
            />
            <MetricTile
              label="Needs compaction"
              value={formatNumber(data.totals?.needs_compaction)}
              emphasis={(data.totals?.needs_compaction ?? 0) > 0}
              hint={
                (data.totals?.needs_compaction ?? 0) > 0 ? (
                  <Link to="/catalogs?needsCompaction=1" className="text-xs text-primary hover:underline">
                    Fix in catalog
                  </Link>
                ) : undefined
              }
            />
            <MetricTile
              label="Reclaimable storage"
              value={formatBytesGb(data.totals?.reclaimable_bytes)}
              hint={
                <span
                  title="Dead/expired snapshots, orphan files, and metadata bloat accumulated across each table's full unexpired history."
                  className="cursor-help"
                >
                  ⓘ
                </span>
              }
            />
          </MetricGrid>
          <Card className="mb-4">
            <CardHeader className="flex-row items-start justify-between gap-3 space-y-0 pb-3">
              <div>
                <CardTitle className="text-sm font-semibold">Table freshness and staleness</CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">
                  Exclusive buckets based on each table&apos;s latest committed Iceberg snapshot. Select a bar to inspect
                  and export every matching table.
                </p>
              </div>
              <span className="shrink-0 text-xs text-muted-foreground">
                As of {formatTime(freshnessAsOf)}
              </span>
            </CardHeader>
            <CardContent>
              {freshnessChartData.length && freshnessTotal ? (
                <>
                  <div
                    role="group"
                    aria-label="Active tables grouped by snapshot freshness and scan failure status"
                    className="space-y-1.5"
                  >
                    {freshnessChartData.map((bucket) => (
                      <Link
                        key={bucket.key}
                        to={bucket.href}
                        className="group flex min-w-0 items-center gap-3 rounded-[var(--radius-sm)] px-1 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label={`${bucket.label}: ${formatNumber(bucket.count, true)} ${bucket.count === 1 ? "table" : "tables"}. Open matching tables.`}
                      >
                        <span className="w-[184px] shrink-0 truncate text-muted-foreground max-sm:w-[120px]">
                          {bucket.label}
                        </span>
                        <span className="h-6 min-w-0 flex-1 overflow-hidden rounded-[var(--radius-sm)] bg-[var(--color-surface-sunken)]">
                          <span
                            className="block h-full rounded-[var(--radius-sm)] transition-opacity group-hover:opacity-80"
                            style={{
                              width: `${(bucket.count / freshnessMaxCount) * 100}%`,
                              backgroundColor:
                                bucket.key === "failed"
                                  ? cryoColors.gradeF
                                  : bucket.key === "unknown"
                                    ? cryoColors.inkFaint
                                    : cryoColors.chartPrimary
                            }}
                          />
                        </span>
                        <strong className="w-10 shrink-0 text-right [font-variant-numeric:tabular-nums]">
                          {formatNumber(bucket.count, true)}
                        </strong>
                      </Link>
                    ))}
                  </div>
                  <ChartDataTable
                    caption="Table counts and fleet share by mutually exclusive snapshot freshness bucket. Each row links to the matching Catalog table list."
                    rows={freshnessChartData}
                    getRowKey={(row) => row.key}
                    columns={[
                      {
                        key: "bucket",
                        header: "Freshness bucket",
                        render: (row) => (
                          <Link to={row.href} className="font-medium text-primary hover:underline">
                            {row.label}
                          </Link>
                        )
                      },
                      {
                        key: "count",
                        header: "Tables",
                        align: "right",
                        render: (row) => formatNumber(row.count, true)
                      },
                      {
                        key: "share",
                        header: "Share",
                        align: "right",
                        render: (row) => `${row.percentage.toFixed(1)}%`
                      }
                    ]}
                  />
                </>
              ) : (
                <EmptyState
                  title="Freshness appears after tables are scanned"
                  description="Run a catalog scan to classify tables by their latest committed snapshot."
                />
              )}
            </CardContent>
          </Card>
          <div className="mb-2 flex items-center justify-between gap-3">
            <h2 className="m-0 text-sm font-semibold text-foreground">Cost</h2>
            <Link to="/cost" className="text-sm text-primary hover:underline">
              View cost breakdown
            </Link>
          </div>
          <MetricGrid columns={3} className="mb-4">
            <MetricTile label="S3 monthly now" value={formatCurrency(data.totals?.monthly_cost_usd)} />
            <MetricTile
              label="After maintenance"
              value={formatCurrency(data.totals?.projected_monthly_cost_usd ?? data.totals?.monthly_cost_usd)}
              hint={
                <span title="Projected S3 storage cost if all recommended maintenance ran today." className="cursor-help">
                  ⓘ
                </span>
              }
            />
            <MetricTile label="Monthly savings" value={formatCurrency(data.totals?.projected_savings_usd_month)} />
          </MetricGrid>
          {smartOverview.data ? (
            <Card className="my-3">
              <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
                <CardTitle className="text-sm font-semibold">Smart maintenance posture</CardTitle>
                <Link to="/smart-maintenance" className="text-xs text-primary hover:underline">
                  Open Smart Maintenance
                </Link>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-6 text-sm text-foreground">
                  <span>
                    HOT tables: <strong>{smartOverview.data.tiers.hot}</strong>
                  </span>
                  <span>
                    ROI shadow decisions: <strong>{smartOverview.data.roi_shadow_decisions_30d}</strong>
                  </span>
                  <span>
                    Pending layout actions: <strong>{smartOverview.data.pending_layout_recommendations}</strong>
                  </span>
                </div>
              </CardContent>
            </Card>
          ) : null}
          <div className="mb-3 grid gap-3 lg:grid-cols-2">
            <FleetTrendChartPanel
              title="Fleet health trend"
              points={healthTrend}
              ariaLabel="Fleet average health score over time"
              dataTable={
                <ChartDataTable
                  caption="Fleet average health score and count of tables at grade D or F, per scan day."
                  rows={healthTrendChartData}
                  getRowKey={(row) => row.day}
                  columns={[
                    { key: "day", header: "Day", render: (row) => row.label },
                    {
                      key: "avg_health_score",
                      header: "Avg health score",
                      align: "right",
                      render: (row) => formatNumber(row.avg_health_score)
                    },
                    {
                      key: "tables_at_grade_df",
                      header: "D/F tables",
                      align: "right",
                      render: (row) => formatNumber(row.tables_at_grade_df, true)
                    }
                  ]}
                />
              }
            >
              <LineChart data={healthTrendChartData}>
                <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
                <XAxis dataKey="label" tickLine={false} axisLine={false} tick={chartAxisTick} interval="preserveStartEnd" />
                <YAxis yAxisId="score" tickLine={false} axisLine={false} tick={chartAxisTick} domain={[0, 100]} />
                <YAxis yAxisId="count" orientation="right" tickLine={false} axisLine={false} tick={chartAxisTick} />
                <RechartsTooltip {...chartTooltipProps} />
                <Legend />
                <Line
                  yAxisId="score"
                  type="monotone"
                  dataKey="avg_health_score"
                  name="Avg health score"
                  stroke={cryoColors.primary}
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  yAxisId="count"
                  type="monotone"
                  dataKey="tables_at_grade_df"
                  name="D/F tables"
                  stroke={cryoColors.gradeD}
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </FleetTrendChartPanel>
            <FleetTrendChartPanel
              title="Fleet cost trend"
              points={costTrend}
              ariaLabel="Fleet monthly storage cost and projected savings over time"
              dataTable={
                <ChartDataTable
                  caption="Fleet monthly S3 storage cost and projected savings, per scan day."
                  rows={costTrendChartData}
                  getRowKey={(row) => row.day}
                  columns={[
                    { key: "day", header: "Day", render: (row) => row.label },
                    {
                      key: "total_monthly_cost_usd",
                      header: "Monthly cost",
                      align: "right",
                      render: (row) => formatCurrency(row.total_monthly_cost_usd)
                    },
                    {
                      key: "projected_savings_usd_month",
                      header: "Projected savings",
                      align: "right",
                      render: (row) => formatCurrency(row.projected_savings_usd_month)
                    }
                  ]}
                />
              }
            >
              <LineChart data={costTrendChartData}>
                <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
                <XAxis dataKey="label" tickLine={false} axisLine={false} tick={chartAxisTick} interval="preserveStartEnd" />
                <YAxis tickLine={false} axisLine={false} tick={chartAxisTick} />
                <RechartsTooltip {...chartTooltipProps} formatter={(value) => formatCurrency(Number(value))} />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="total_monthly_cost_usd"
                  name="Monthly cost"
                  stroke={cryoColors.costCurrent}
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="projected_savings_usd_month"
                  name="Projected savings"
                  stroke={cryoColors.savings}
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </FleetTrendChartPanel>
          </div>
          <div className="grid gap-3 xl:grid-cols-12">
            <Card className="xl:col-span-8">
              <CardHeader className="flex-row items-start justify-between space-y-0 pb-3">
                <CardTitle className="text-sm font-semibold">Top offenders</CardTitle>
                {(data.totals?.needs_compaction ?? 0) > topOffenders.length ? (
                  <p className="text-xs text-muted-foreground">
                    Showing top {topOffenders.length.toLocaleString()} of{" "}
                    {(data.totals?.needs_compaction ?? topOffenders.length).toLocaleString()} tables needing attention
                  </p>
                ) : null}
              </CardHeader>
              <CardContent className="p-0 pt-0">
                {topOffenders.length === 0 ? (
                  <div className="p-4">
                    {(data.totals?.tables ?? 0) === 0 ? (
                      <FirstRunEmptyState />
                    ) : (
                      <EmptyState
                        title="No tables need attention"
                        description="Every tracked table is below the compaction threshold right now."
                      />
                    )}
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="td-schema-table w-full">
                      <thead>
                        <tr>
                          <th>Table</th>
                          <th>Grade</th>
                          <th>Priority</th>
                          <th className="text-right">Small files</th>
                          <th className="text-right">Size</th>
                        </tr>
                      </thead>
                      <tbody>
                        {topOffenders.map((row) => (
                          <tr key={row.id ?? `${row.source_id}-${row.schema_name}-${row.table_name}`}>
                            <td>
                              <Link to={`/tables/${row.id}`} className="font-medium text-foreground hover:text-primary">
                                {qualifiedTableName(row)}
                              </Link>
                            </td>
                            <td>
                              <GradePill grade={row.stats?.grade} />
                            </td>
                            <td>
                              <PriorityTag priority={row.stats?.compaction_priority} />
                            </td>
                            <td className="text-right [font-variant-numeric:tabular-nums]">
                              {formatNumber(row.stats?.small_files_count, true)}
                            </td>
                            <td className="text-right [font-variant-numeric:tabular-nums]">
                              {formatGb(row.stats?.total_size_gb)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>
            <Card className="xl:col-span-4">
              <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
                <CardTitle className="text-sm font-semibold">Recent scan trend</CardTitle>
                <span className="text-xs text-muted-foreground">Small-file count per scan</span>
              </CardHeader>
              <CardContent>
                {(data.drift ?? []).length ? (
                  <div className="drift-list">
                    {(data.drift ?? []).slice(0, 8).map((item, index) => (
                      <div key={`${item.table_id ?? index}`} className="drift-row">
                        <span>{formatTime(item.scan_at)}</span>
                        <strong>{formatNumber(item.small_files_count, true)} small files</strong>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title="No scan history yet"
                    description="This fills in once the datalake has been scanned more than once, so you can see whether small-file counts are climbing."
                  />
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
};

const deriveDashboard = (tables: TableRead[]): DatalakeDashboard => {
  const grades = new Map<string, number>();
  const schemaCosts = new Map<
    string,
    { schema_name: string; cost_usd: number; projected_usd: number; savings_usd: number; size_gb: number }
  >();
  let size = 0;
  let smallFiles = 0;
  let cost = 0;
  let projected = 0;
  let savings = 0;
  let reclaimable = 0;
  let needsCompaction = 0;
  const freshnessAsOf = new Date();
  const freshness = new Map<FreshnessBucketKey, number>(
    (Object.keys(FRESHNESS_LABELS) as FreshnessBucketKey[]).map((key) => [key, 0])
  );

  tables.forEach((table) => {
    const stats = table.stats;
    const grade = stats?.grade ?? "-";
    grades.set(grade, (grades.get(grade) ?? 0) + 1);
    size += stats?.total_size_gb ?? 0;
    smallFiles += stats?.small_files_count ?? 0;
    const cur = stats?.estimated_monthly_s3_cost_usd ?? 0;
    const proj = stats?.projected_monthly_s3_cost_usd ?? cur;
    const sav = stats?.projected_savings_usd_month ?? Math.max(0, cur - proj);
    cost += cur;
    projected += proj;
    savings += sav;
    reclaimable += stats?.reclaimable_bytes ?? 0;
    if (stats?.needs_compaction) needsCompaction += 1;
    const freshnessKey = tableFreshnessBucket(table, freshnessAsOf);
    freshness.set(freshnessKey, (freshness.get(freshnessKey) ?? 0) + 1);
    const schema =
      schemaCosts.get(table.schema_name) ??
      { schema_name: table.schema_name, cost_usd: 0, projected_usd: 0, savings_usd: 0, size_gb: 0 };
    schema.cost_usd += cur;
    schema.projected_usd += proj;
    schema.savings_usd += sav;
    schema.size_gb += stats?.total_size_gb ?? 0;
    schemaCosts.set(table.schema_name, schema);
  });

  return {
    totals: {
      tables: tables.length,
      size_gb: size,
      small_files: smallFiles,
      monthly_cost_usd: cost,
      projected_monthly_cost_usd: projected,
      projected_savings_usd_month: savings,
      reclaimable_bytes: reclaimable,
      needs_compaction: needsCompaction
    },
    grade_distribution: Array.from(grades.entries()).map(([grade, count]) => ({ grade, count })),
    freshness_distribution: (Object.keys(FRESHNESS_LABELS) as FreshnessBucketKey[]).map((key) => ({
      key,
      label: FRESHNESS_LABELS[key],
      count: freshness.get(key) ?? 0
    })),
    freshness_as_of: freshnessAsOf.toISOString(),
    cost_by_schema: Array.from(schemaCosts.values())
      .map((item) => ({
        schema_name: item.schema_name,
        cost_usd: item.cost_usd,
        projected_usd: item.projected_usd,
        savings_usd: item.savings_usd,
        size_gb: item.size_gb
      }))
      .sort((a, b) => (b.savings_usd || b.cost_usd) - (a.savings_usd || a.cost_usd)),
    top_offenders: [...tables].sort((a, b) => (b.stats?.small_files_count ?? 0) - (a.stats?.small_files_count ?? 0)).slice(0, 10)
  };
};

const tableFreshnessBucket = (table: TableRead, asOf: Date): FreshnessBucketKey => {
  if ((table.consecutive_scan_failures ?? 0) >= BROKEN_TABLE_FAILURE_THRESHOLD) return "failed";
  const rawTimestamp = table.stats?.raw?.last_snapshot_committed_at;
  const value = table.stats?.last_snapshot_committed_at ?? (typeof rawTimestamp === "string" ? rawTimestamp : null);
  if (!value) return "unknown";
  const committedAt = new Date(value);
  if (Number.isNaN(committedAt.getTime())) return "unknown";
  const ageDays = (asOf.getTime() - committedAt.getTime()) / (24 * 60 * 60 * 1000);
  if (ageDays < 1) return "within_24h";
  if (ageDays < 3) return "1_3d";
  if (ageDays < 7) return "3_7d";
  if (ageDays < 15) return "7_15d";
  if (ageDays < 30) return "15_30d";
  if (ageDays < 90) return "1_3m";
  if (ageDays < 180) return "3_6m";
  if (ageDays < 365) return "6_12m";
  return "over_1y";
};
