import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
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
import {
  getDatalakeDashboard,
  getFastPathFindings,
  getFleetTrends,
  getSmartMaintenanceOverview,
  listRuns
} from "../api/client";
import type { FastPathFindingRead } from "../api/client";
import type { FleetCostTrendPoint, FleetHealthTrendPoint } from "../api/types";
import { getCryoVitals } from "../api/vitals";
import { ChartFrame, EmptyState, QueryErrorAlert, FirstRunEmptyState } from "../components/common";
import { FleetChecklistMatrix } from "../components/ChecklistMatrix";
import { GradePill, PriorityTag } from "../components/status";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { ChartDataTable } from "../components/ui/chart-data-table";
import { MetricGrid } from "../components/ui/metric-grid";
import { PageHeader } from "../components/ui/page-header";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { Skeleton } from "../components/ui/skeleton";
import { MetricTile } from "../components/ui/stat";
import { useAuth } from "../lib/auth";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../lib/theme";
import { formatBytesGb, formatCurrency, formatGb, formatNumber, formatTime } from "../lib/format";
import { freshnessCatalogHref } from "../lib/freshness";
import {
  latestBatchRun,
  nightlyRunView,
  reclaimInputFromImpact,
  reclaimYesterdayLabel,
  vitalsScoreLabel,
  vitalsStatusLabel
} from "../lib/nowStrip";
import { qualifiedTableName } from "../lib/tableLabels";

const NOT_MEASURED = "Not measured";
const COST_UNAVAILABLE = "Cost data unavailable";
const HEALTH_S3_TOOLTIP =
  "Modeled from each table's cost row when one exists, otherwise the scanner's estimated monthly S3 cost, summed over active tables.";
const TRACKED_TABLES_HINT = "Every table row Cryo tracks.";
const GRADE_ORDER = ["F", "D", "C", "B", "A"] as const;

const gradePalette: Record<string, string> = {
  A: cryoColors.gradeA,
  B: cryoColors.gradeB,
  C: cryoColors.gradeC,
  D: cryoColors.gradeD,
  F: cryoColors.gradeF
};

export const notGradedCount = (
  tracked: number | null | undefined,
  distribution: Array<{ count: number }>
): number | null => {
  if (tracked == null || Number.isNaN(tracked)) return null;
  const graded = distribution.reduce((sum, entry) => sum + entry.count, 0);
  return Math.max(0, tracked - graded);
};

export const trendPointMarker = (isPartial: boolean | undefined): "hollow" | "solid" =>
  isPartial ? "hollow" : "solid";

export const trendTooltipLabel = (dayLabel: string, isPartial: boolean | undefined): string =>
  isPartial ? `${dayLabel} (partial)` : dayLabel;

export const trendDayCell = (dayLabel: string, isPartial: boolean | undefined): string =>
  isPartial ? `${dayLabel} partial` : dayLabel;

export const trendTablesCountedLabel = (count: number | null | undefined): string =>
  count == null || Number.isNaN(count) ? NOT_MEASURED : formatNumber(count, true);

export const trendTooltipFromPayload = (
  label: unknown,
  payload: ReadonlyArray<{ payload?: { is_partial?: boolean } }> | undefined
): string => {
  const partial = payload?.some((item) => item.payload?.is_partial === true) ?? false;
  return trendTooltipLabel(typeof label === "string" ? label : "", partial);
};

export const TrendPointDot = ({
  cx,
  cy,
  stroke,
  payload
}: {
  cx?: number;
  cy?: number;
  stroke?: string;
  payload?: { is_partial?: boolean };
}) => {
  if (cx == null || cy == null || trendPointMarker(payload?.is_partial) !== "hollow") return null;
  return <circle cx={cx} cy={cy} r={4} fill="none" stroke={stroke ?? "currentColor"} strokeWidth={2} />;
};

const displayCount = (value: number | null | undefined, compact = false): string =>
  value == null || Number.isNaN(value) ? NOT_MEASURED : formatNumber(value, compact);

const displayGb = (value: number | null | undefined): string =>
  value == null || Number.isNaN(value) ? NOT_MEASURED : formatGb(value);

const displayBytes = (value: number | null | undefined): string =>
  value == null || Number.isNaN(value) ? NOT_MEASURED : formatBytesGb(value);

const displayCost = (value: number | null | undefined): string =>
  value == null || Number.isNaN(value) ? COST_UNAVAILABLE : formatCurrency(value);

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

const FastPathFindingsCard = ({
  findings,
  isLoading,
  error
}: {
  findings: FastPathFindingRead[];
  isLoading: boolean;
  error: unknown;
}) => {
  const severityVariant = (severity: string): "success" | "warning" | "danger" | "secondary" => {
    const normalized = severity.toLowerCase();
    if (normalized === "critical" || normalized === "error") return "danger";
    if (normalized === "warning") return "warning";
    if (normalized === "info") return "secondary";
    return "secondary";
  };

  return (
    <Card className="mb-4">
      <CardHeader className="space-y-1 pb-3">
        <CardTitle className="text-sm font-semibold">Recently discovered</CardTitle>
        <p className="text-xs text-muted-foreground">
          Fast-path flags for new or barely-scanned tables. These are not auto-compacted.
        </p>
      </CardHeader>
      <CardContent>
        <QueryErrorAlert error={error} title="Failed to load fast-path findings" />
        {isLoading ? (
          <Skeleton className="h-16 w-full rounded-[var(--radius-default)]" />
        ) : findings.length === 0 ? (
          <EmptyState
            title="No recently discovered flags"
            description="New tables that fail an early file-count or target-file-size check will show up here."
          />
        ) : (
          <ul className="space-y-3">
            {findings.map((finding, index) => (
              <li
                key={`${finding.table_id}-${finding.finding_type}-${finding.detected_at}-${index}`}
                className="rounded-[var(--radius-default)] border border-border p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    to={`/tables/${finding.table_id}`}
                    className="font-medium text-foreground hover:text-primary"
                  >
                    {finding.table_name}
                  </Link>
                  <Badge variant={severityVariant(finding.severity)}>{finding.severity}</Badge>
                  <span className="text-xs text-muted-foreground">
                    {finding.finding_type.replaceAll("_", " ")}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{finding.reason}</p>
                {finding.finding_type === "missing_target_file_size" ? (
                  <p className="mt-1 text-xs">
                    <Link
                      to={`/tables/${finding.table_id}?tab=health-quality#hq-properties`}
                      className="text-primary hover:underline"
                    >
                      Open property recommendations
                    </Link>
                  </p>
                ) : null}
                <p className="mt-1 text-xs text-muted-foreground">{formatTime(finding.detected_at)}</p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
};

const HealthGradeBar = ({
  distribution,
  tracked
}: {
  distribution: Array<{ grade: string; count: number }>;
  tracked: number | null | undefined;
}) => {
  const notGraded = notGradedCount(tracked, distribution);
  const segments = [
    ...GRADE_ORDER.map((grade) => ({
      key: grade,
      label: grade,
      count: distribution.find((entry) => entry.grade === grade)?.count ?? 0,
      color: gradePalette[grade] ?? cryoColors.mutedSubtle
    })),
    ...distribution
      .filter((entry) => !(GRADE_ORDER as readonly string[]).includes(entry.grade) && entry.count > 0)
      .map((entry) => ({
        key: entry.grade,
        label: entry.grade,
        count: entry.count,
        color: cryoColors.inkFaint
      })),
    ...(notGraded != null && notGraded > 0
      ? [
          {
            key: "not-graded",
            label: "Not graded",
            count: notGraded,
            color: cryoColors.mutedSubtle
          }
        ]
      : [])
  ].filter((segment) => segment.count > 0);
  const total = segments.reduce((sum, segment) => sum + segment.count, 0);
  if (!total) return null;

  return (
    <div className="mb-4" aria-label="Grade distribution">
      <div className="flex h-2 overflow-hidden rounded-[var(--radius-sm)] bg-muted">
        {segments.map((segment) => (
          <div
            key={segment.key}
            className="h-full min-w-[2px]"
            style={{ width: `${(segment.count / total) * 100}%`, background: segment.color }}
            title={`${segment.label}: ${segment.count.toLocaleString()}`}
          />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {segments.map((segment) => (
          <span key={segment.key} className="inline-flex items-center gap-1.5 [font-variant-numeric:tabular-nums]">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: segment.color }} />
            {segment.label} {segment.count.toLocaleString()}
          </span>
        ))}
      </div>
    </div>
  );
};

const VitalsStatusIcon = ({ status }: { status: string | null | undefined }) => {
  if (status === "HEALTHY") {
    return <CheckCircle2 className="size-3.5 text-[var(--color-status-success)]" aria-hidden="true" />;
  }
  if (status === "DEGRADED") {
    return <AlertTriangle className="size-3.5 text-[var(--color-status-warning)]" aria-hidden="true" />;
  }
  if (status === "CRITICAL") {
    return <XCircle className="size-3.5 text-[var(--color-status-danger)]" aria-hidden="true" />;
  }
  return null;
};

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
  const fastPathFindings = useQuery({
    queryKey: ["fast-path-findings", token],
    queryFn: () => getFastPathFindings(api),
    retry: 1
  });
  const vitals = useQuery({
    queryKey: ["cryo-vitals", token],
    queryFn: () => getCryoVitals(api),
    retry: 1
  });
  const nightlyRuns = useQuery({
    queryKey: ["health-nightly-runs", token],
    queryFn: () => listRuns(api, { limit: 200 }),
    retry: 1
  });

  const data = dashboard.data;
  const topOffenders = data?.top_offenders ?? [];
  const topOffenderIds = topOffenders
    .map((row) => (row as { id?: string; table_id?: string }).id ?? (row as { id?: string; table_id?: string }).table_id)
    .filter((id): id is string => Boolean(id));
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
  const gradeDistribution = data?.grade_distribution ?? [];
  const freshnessAsOf = data?.freshness_as_of ?? new Date().toISOString();
  const freshnessDistribution = data?.freshness_distribution ?? [];
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
  const vitalsPayload = vitals.data;
  const vitalsScore = vitals.isLoading ? "…" : vitals.isError ? "Vitals unavailable" : vitalsScoreLabel(vitalsPayload?.score);
  const vitalsStatus = vitals.isLoading || vitals.isError ? null : vitalsStatusLabel(vitalsPayload?.status);
  const deductions = vitalsPayload?.deductions ?? [];
  const nightly = nightlyRuns.isLoading
    ? null
    : nightlyRuns.isError
      ? null
      : nightlyRunView(latestBatchRun(nightlyRuns.data ?? []));
  const reclaimLabel = vitals.isLoading
    ? "…"
    : vitals.isError
      ? NOT_MEASURED
      : reclaimYesterdayLabel(reclaimInputFromImpact(vitalsPayload?.impact));
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
      <section aria-label="Now" className="mb-4 grid gap-3 border-b border-border pb-3 sm:grid-cols-3">
        <div>
          <p className="text-[11px] font-semibold text-muted-foreground">Cryo vitals</p>
          {vitals.isError ? (
            <p className="mt-1 text-sm text-foreground">Vitals unavailable</p>
          ) : (
            <Popover>
              <PopoverTrigger asChild>
                <button type="button" className="mt-1 inline-flex items-center gap-1.5 text-sm text-foreground">
                  <VitalsStatusIcon status={vitalsPayload?.status} />
                  <span className="[font-variant-numeric:tabular-nums]">{vitalsScore}</span>
                  {vitalsStatus ? <span>{vitalsStatus}</span> : null}
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-80">
                <p className="text-xs font-semibold">Deductions</p>
                {deductions.length === 0 ? (
                  <p className="mt-2 text-xs text-muted-foreground">No deductions</p>
                ) : (
                  <ul className="mt-2 space-y-1 text-xs">
                    {deductions.map((deduction, index) => (
                      <li key={`${deduction.reason}-${index}`}>
                        {deduction.reason}
                        {typeof deduction.points === "number" ? ` (${deduction.points})` : null}
                      </li>
                    ))}
                  </ul>
                )}
              </PopoverContent>
            </Popover>
          )}
        </div>
        <div>
          <p className="text-[11px] font-semibold text-muted-foreground">Last nightly run</p>
          {nightlyRuns.isLoading ? (
            <p className="mt-1 text-sm text-foreground">…</p>
          ) : nightlyRuns.isError || !nightly ? (
            <p className="mt-1 text-sm text-foreground">Nightly run unavailable</p>
          ) : nightly.href ? (
            <p className="mt-1 text-sm text-foreground">
              <Link to={nightly.href} className="text-primary hover:underline">
                {nightly.status}
              </Link>
              <span className="text-muted-foreground"> · {nightly.succeededSelected} · {nightly.finished}</span>
            </p>
          ) : (
            <p className="mt-1 text-sm text-foreground">
              {nightly.status}
              <span className="text-muted-foreground"> · {nightly.succeededSelected}</span>
            </p>
          )}
        </div>
        <div>
          <p className="text-[11px] font-semibold text-muted-foreground">Reclaim yesterday</p>
          <p className="mt-1 text-sm text-foreground [font-variant-numeric:tabular-nums]">{reclaimLabel}</p>
        </div>
      </section>
      {data ? (
        <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {gradeDfCount > 0
            ? `${formatNumber(gradeDfCount, true)} tables at grade D or F. ${
                data.totals?.needs_compaction == null
                  ? "Compaction need is not measured."
                  : `${formatNumber(data.totals.needs_compaction)} tables need compaction.`
              }`
            : "No tables currently at grade D or F."}
        </p>
      ) : null}
      {dashboard.isError ? (
        <Alert
          className="page-alert"
          variant="error"
          title="Dashboard unavailable"
          description={
            <Button type="button" variant="secondary" size="sm" className="mt-2" onClick={() => void dashboard.refetch()}>
              Retry
            </Button>
          }
        />
      ) : null}
      <QueryErrorAlert error={dashboard.error} title="Failed to load datalake dashboard" />
      <QueryErrorAlert error={fleetTrends.error} title="Failed to load fleet trends" />
      {dashboard.isLoading ? (
        <HealthPageSkeleton />
      ) : data ? (
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
            <MetricTile
              label="Tracked tables"
              value={displayCount(data.totals?.tables, true)}
              hint={
                <span title={TRACKED_TABLES_HINT} className="cursor-help">
                  ⓘ
                </span>
              }
            />
          </MetricGrid>
          <HealthGradeBar distribution={gradeDistribution} tracked={data.totals?.tables} />
          <h2 className="mb-2 mt-0 text-sm font-semibold text-foreground">Health</h2>
          <MetricGrid columns={4} className="mb-4">
            <MetricTile
              label="Total data size"
              value={displayGb(data.totals?.size_gb)}
              hint={
                <span title="Combined size of every table Cryo is tracking, across all schemas." className="cursor-help">
                  ⓘ
                </span>
              }
            />
            <MetricTile
              label="Small files"
              value={displayCount(data.totals?.small_files, true)}
              hint={
                <span title="Files under the target size threshold — too many of these slows down queries." className="cursor-help">
                  ⓘ
                </span>
              }
            />
            <MetricTile
              label="Needs compaction"
              value={displayCount(data.totals?.needs_compaction)}
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
              value={displayBytes(data.totals?.reclaimable_bytes)}
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
            <MetricTile
              label="S3 monthly now"
              value={displayCost(data.totals?.monthly_cost_usd)}
              hint={
                <span title={HEALTH_S3_TOOLTIP} className="cursor-help text-[11px] font-semibold text-muted-foreground">
                  Modeled
                </span>
              }
            />
            <MetricTile
              label="After maintenance"
              value={displayCost(data.totals?.projected_monthly_cost_usd)}
              hint={
                <span title="Projected S3 storage cost if all recommended maintenance ran today." className="cursor-help">
                  ⓘ
                </span>
              }
            />
            <MetricTile label="Monthly savings" value={displayCost(data.totals?.projected_savings_usd_month)} />
          </MetricGrid>
          <FastPathFindingsCard
            findings={fastPathFindings.data ?? []}
            isLoading={fastPathFindings.isLoading}
            error={fastPathFindings.error}
          />
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
                    { key: "day", header: "Day", render: (row) => trendDayCell(row.label, row.is_partial) },
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
                    },
                    {
                      key: "tables_counted",
                      header: "Tables counted",
                      align: "right",
                      render: (row) => trendTablesCountedLabel(row.tables_counted)
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
                <RechartsTooltip {...chartTooltipProps} labelFormatter={trendTooltipFromPayload} />
                <Legend />
                <Line
                  yAxisId="score"
                  type="monotone"
                  dataKey="avg_health_score"
                  name="Avg health score"
                  stroke={cryoColors.primary}
                  strokeWidth={2}
                  dot={TrendPointDot}
                />
                <Line
                  yAxisId="count"
                  type="monotone"
                  dataKey="tables_at_grade_df"
                  name="D/F tables"
                  stroke={cryoColors.gradeD}
                  strokeWidth={2}
                  dot={TrendPointDot}
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
                    { key: "day", header: "Day", render: (row) => trendDayCell(row.label, row.is_partial) },
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
                    },
                    {
                      key: "tables_counted",
                      header: "Tables counted",
                      align: "right",
                      render: (row) => trendTablesCountedLabel(row.tables_counted)
                    }
                  ]}
                />
              }
            >
              <LineChart data={costTrendChartData}>
                <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
                <XAxis dataKey="label" tickLine={false} axisLine={false} tick={chartAxisTick} interval="preserveStartEnd" />
                <YAxis tickLine={false} axisLine={false} tick={chartAxisTick} />
                <RechartsTooltip
                  {...chartTooltipProps}
                  labelFormatter={trendTooltipFromPayload}
                  formatter={(value) =>
                    value == null || value === "" ? COST_UNAVAILABLE : formatCurrency(Number(value))
                  }
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="total_monthly_cost_usd"
                  name="Monthly cost"
                  stroke={cryoColors.costCurrent}
                  strokeWidth={2}
                  dot={TrendPointDot}
                />
                <Line
                  type="monotone"
                  dataKey="projected_savings_usd_month"
                  name="Projected savings"
                  stroke={cryoColors.savings}
                  strokeWidth={2}
                  dot={TrendPointDot}
                />
              </LineChart>
            </FleetTrendChartPanel>
          </div>
          <div className="grid gap-3 xl:grid-cols-12">
            <Card className="xl:col-span-8">
              <CardHeader className="flex-row items-start justify-between space-y-0 pb-3">
                <CardTitle className="text-sm font-semibold">Top offenders</CardTitle>
                {typeof data.totals?.needs_compaction === "number" &&
                data.totals.needs_compaction > topOffenders.length ? (
                  <p className="text-xs text-muted-foreground">
                    Showing top {topOffenders.length.toLocaleString()} of{" "}
                    {data.totals.needs_compaction.toLocaleString()} tables needing attention
                  </p>
                ) : null}
              </CardHeader>
              <CardContent className="p-0 pt-0">
                {topOffenders.length === 0 ? (
                  <div className="p-4">
                    {data.totals?.tables == null ? (
                      <EmptyState
                        title="Not scanned"
                        description="Fleet totals appear after a catalog scan."
                      />
                    ) : data.totals.tables === 0 ? (
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
                              {row.stats?.grade ? <GradePill grade={row.stats.grade} /> : "Not scanned"}
                            </td>
                            <td>
                              <PriorityTag priority={row.stats?.compaction_priority} />
                            </td>
                            <td className="text-right [font-variant-numeric:tabular-nums]">
                              {displayCount(row.stats?.small_files_count, true)}
                            </td>
                            <td className="text-right [font-variant-numeric:tabular-nums]">
                              {displayGb(row.stats?.total_size_gb)}
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
          {topOffenderIds.length > 0 ? <FleetChecklistMatrix tableIds={topOffenderIds} /> : null}
        </>
      ) : null}
    </div>
  );
};
