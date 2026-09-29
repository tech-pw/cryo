import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { Link } from "react-router-dom";
import {
  getDatalakeDashboard,
  getFastPathFindings,
  getScannerStatus,
  listPendingApprovals,
  listRuns
} from "../api/client";
import type { FastPathFindingRead } from "../api/client";
import { getEngineLoad } from "../api/engineLoad";
import { getCryoVitals } from "../api/vitals";
import { EmptyState, QueryErrorAlert } from "../components/common";
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
import { cryoColors } from "../lib/theme";
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

const NOT_MEASURED = "Not measured";
const COST_UNAVAILABLE = "Cost data unavailable";
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

export const tablesAtGradeDf = (
  distribution: Array<{ grade: string; count: number }> | null | undefined
): number | null => {
  if (distribution == null) return null;
  let total = 0;
  for (const entry of distribution) {
    if (entry.grade !== "D" && entry.grade !== "F") continue;
    if (!Number.isFinite(entry.count)) return null;
    total += entry.count;
  }
  return total;
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
                      to={`/tables/${finding.table_id}?tab=files#files-properties`}
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
  </div>
);

export const DatalakeHealth = () => {
  const { api, token } = useAuth();
  const dashboard = useQuery({
    queryKey: ["dashboard", token, TOP_OFFENDERS_LIMIT],
    queryFn: () => getDatalakeDashboard(api, { top_limit: TOP_OFFENDERS_LIMIT }),
    retry: 1
  });
  const scannerStatus = useQuery({
    queryKey: ["scanner-status", token],
    queryFn: () => getScannerStatus(api),
    retry: false
  });
  const engineLoad = useQuery({
    queryKey: ["engine-load", token],
    queryFn: () => getEngineLoad(api),
    retry: false
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
  const pendingApprovals = useQuery({
    queryKey: ["pending-approvals", token],
    queryFn: () => listPendingApprovals(api),
    retry: 1
  });

  const data = dashboard.data;

  const gradeDistribution = data?.grade_distribution;
  const freshnessAsOf = data?.freshness_as_of ?? new Date().toISOString();
  const freshnessDistribution = data?.freshness_distribution ?? [];
  const freshnessTotal = freshnessDistribution.reduce((sum, bucket) => sum + bucket.count, 0);
  const freshnessChartData = freshnessDistribution.map((bucket) => ({
    ...bucket,
    percentage: freshnessTotal ? (bucket.count / freshnessTotal) * 100 : 0,
    href: freshnessCatalogHref(bucket.key, freshnessAsOf)
  }));
  const freshnessMaxCount = Math.max(...freshnessChartData.map((bucket) => bucket.count), 1);
  const tablesAtDf = tablesAtGradeDf(gradeDistribution);
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
  const awaitingPersonValue = pendingApprovals.isSuccess
    ? formatNumber(pendingApprovals.data.length)
    : "Not recorded";
  return (
    <div>
      <PageHeader
        title="Home"
        subtitle="What happened last night, and is the lake worse?"
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
      <nav aria-label="Platform" className="mb-4 flex flex-wrap gap-2">
        <Link to="/runs/scanner" className="rounded-full border px-2 py-1 text-xs">
          Scanner: {scannerStatus.isError ? "Unavailable" : scannerStatus.data?.active_run ? "Running" : scannerStatus.data ? "Idle" : "Not recorded"}
        </Link>
        <Link to="/runs?tab=holds" className="rounded-full border px-2 py-1 text-xs">
          Holds
        </Link>
        <Link to="/runs" className="rounded-full border px-2 py-1 text-xs">
          Queue: {scannerStatus.data?.queue_depth == null ? "Not recorded" : formatNumber(scannerStatus.data.queue_depth)}
        </Link>
        <Link to="/runs" className="rounded-full border px-2 py-1 text-xs">
          Engines: {engineLoad.isError ? "Unavailable" : engineLoad.data ? `${engineLoad.data.engines.length} configured` : "Not recorded"}
        </Link>
        <span className="rounded-full border px-2 py-1 text-xs text-muted-foreground">Scheduler: Not recorded</span>
        <span className="rounded-full border px-2 py-1 text-xs text-muted-foreground">Governor: Not recorded</span>
        <span className="rounded-full border px-2 py-1 text-xs text-muted-foreground">Workers: Not recorded</span>
      </nav>
      <section aria-labelledby="needs-person-heading" className="mb-4">
        <h2 id="needs-person-heading" className="mb-2 mt-0 text-sm font-semibold text-foreground">
          What needs a person
        </h2>
        <MetricGrid columns={2}>
          <MetricTile
            metricId="awaiting_person"
            loading={pendingApprovals.isPending}
            value={awaitingPersonValue}
            emphasis={pendingApprovals.isSuccess && pendingApprovals.data.length > 0}
            hint={
              <Link to="/governance" className="text-xs text-primary hover:underline">
                Review
              </Link>
            }
          />
        </MetricGrid>
      </section>
      <section aria-labelledby="needs-engine-heading" className="mb-4">
        <h2 id="needs-engine-heading" className="mb-1 mt-0 text-sm font-semibold text-foreground">
          What needs an engine
        </h2>
        <Link to="/runs?tab=holds" className="text-sm text-primary hover:underline">
          View holds
        </Link>
      </section>
      {data ? (
        <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {tablesAtDf == null
            ? "Tables at D/F unknown."
            : tablesAtDf > 0
              ? `${formatNumber(tablesAtDf, true)} tables at grade D or F. ${
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
      {dashboard.isLoading ? (
        <HealthPageSkeleton />
      ) : data ? (
        <>
          <h2 className="mb-2 mt-0 text-sm font-semibold text-foreground">What got worse</h2>
          <MetricGrid columns={2} className="mb-3">
            <MetricTile
              metricId="tables_grade_d_or_f"
              value={tablesAtDf == null ? "Unknown" : formatNumber(tablesAtDf, true)}
              emphasis={(tablesAtDf ?? 0) > 0}
              hint={
                tablesAtDf != null && tablesAtDf > 0 ? (
                  <Link to="/catalogs?grades=D,F" className="text-xs text-primary hover:underline">
                    Fix in catalog
                  </Link>
                ) : undefined
              }
            />
            <MetricTile
              metricId="tracked_tables"
              value={displayCount(data.totals?.tables, true)}
              hint={
                <span title={TRACKED_TABLES_HINT} className="cursor-help">
                  ⓘ
                </span>
              }
            />
          </MetricGrid>
          {gradeDistribution ? (
            <HealthGradeBar distribution={gradeDistribution} tracked={data.totals?.tables} />
          ) : null}
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
          <FastPathFindingsCard
            findings={fastPathFindings.data ?? []}
            isLoading={fastPathFindings.isLoading}
            error={fastPathFindings.error}
          />
          <section className="mb-4">
            <h2 className="mb-1 text-sm font-semibold">Changed this week</h2>
            <p className="text-sm text-foreground">
              Measured reclaim: {reclaimLabel}.{" "}
              <Link to="/cost?tab=impact" className="text-primary hover:underline">
                Open Cost → Impact
              </Link>
            </p>
          </section>
        </>
      ) : null}
    </div>
  );
};
