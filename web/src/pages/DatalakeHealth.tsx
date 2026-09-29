import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  getDatalakeDashboard,
  getFastPathFindings,
  getFleetTrends,
  getScannerStatus,
  listPendingApprovals,
  listRuns
} from "../api/client";
import { getEngineLoad } from "../api/engineLoad";
import { getCryoVitals } from "../api/vitals";
import { Alert } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { PageHeader } from "../components/ui/page-header";
import { useAuth } from "../lib/auth";
import { optionalNumber } from "../lib/batchSummary";
import { formatNumber } from "../lib/format";
import { freshnessCatalogHref } from "../lib/freshness";
import {
  latestBatchRun,
  nightlyRunView,
  reclaimInputFromImpact,
  reclaimYesterdayDisplay
} from "../lib/nowStrip";
import { DiscoveredList } from "./home/DiscoveredList";
import { FreshnessCard } from "./home/FreshnessCard";
import { LakeZone } from "./home/LakeZone";
import { StatusZone } from "./home/StatusZone";

const TOP_OFFENDERS_LIMIT = 50;

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

const previousDfCount = (
  points: Array<{ day: string; tables_at_grade_df: number; tables_counted?: number; is_partial?: boolean }> | undefined,
  currentGraded: number | null
): number | null => {
  if (!points || currentGraded == null) return null;
  const today = new Date().toISOString().slice(0, 10);
  const prior = [...points]
    .filter((point) => point.day < today && !point.is_partial && Number.isFinite(point.tables_at_grade_df))
    .sort((left, right) => right.day.localeCompare(left.day))[0];
  if (!prior) return null;
  if (prior.tables_counted != null && currentGraded > 0 && prior.tables_counted < currentGraded / 5) return null;
  return prior.tables_at_grade_df;
};

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
  const fleetTrends = useQuery({
    queryKey: ["fleet-trends", token],
    queryFn: () => getFleetTrends(api, { days: 8 }),
    retry: false
  });

  const data = dashboard.data;
  const latest = nightlyRuns.isSuccess ? latestBatchRun(nightlyRuns.data ?? []) : null;
  const nightly = nightlyRuns.isLoading || nightlyRuns.isError ? null : nightlyRunView(latest);
  const failedCount = latest ? optionalNumber(latest.summary, "failed") : null;
  const reclaim = vitals.isLoading
    ? { hero: "…", unit: null, caption: null, tone: "muted" as const }
    : vitals.isError
      ? { hero: "Unavailable", unit: null, caption: null, tone: "plain" as const }
      : reclaimYesterdayDisplay(reclaimInputFromImpact(vitals.data?.impact), {
          measured: vitals.data?.north_star?.byte_actions_measured,
          succeeded: vitals.data?.north_star?.byte_actions_succeeded
        });
  const freshnessAsOf = data?.freshness_as_of ?? null;
  const freshnessDistribution = data?.freshness_distribution ?? [];
  const freshnessTotal = freshnessDistribution.reduce((sum, bucket) => sum + bucket.count, 0);
  const freshnessRows = freshnessDistribution.map((bucket) => ({
    ...bucket,
    percentage: freshnessTotal ? (bucket.count / freshnessTotal) * 100 : 0,
    href: freshnessCatalogHref(bucket.key, freshnessAsOf ?? new Date().toISOString())
  }));
  const approvalCount = pendingApprovals.isSuccess ? pendingApprovals.data.length : 0;
  const scannerLabel = scannerStatus.isPending
    ? null
    : scannerStatus.isError
      ? "Unavailable"
      : scannerStatus.data?.active_run
        ? "Running"
        : scannerStatus.data
          ? "Idle"
          : null;
  const engineLabel = engineLoad.isPending
    ? null
    : engineLoad.isError
      ? "Unavailable"
      : engineLoad.data
        ? `${engineLoad.data.engines.length} configured`
        : null;

  return (
    <div className="min-w-0">
      <PageHeader title="Home" subtitle="What happened last night, and is the lake worse?" />
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
      <StatusZone
        vitals={vitals.data}
        vitalsLoading={vitals.isLoading}
        vitalsError={vitals.isError}
        nightly={nightly}
        nightlyLoading={nightlyRuns.isLoading}
        nightlyError={nightlyRuns.isError}
        failedCount={failedCount}
        reclaim={reclaim}
        scannerLabel={scannerLabel}
        scannerHref="/runs/scanner"
        queueDepth={scannerStatus.data?.queue_depth ?? null}
        engineLabel={engineLabel}
        engineHref="/runs"
      />
      {data ? (
        <p className="sr-only" role="status" aria-live="polite">
          {tablesAtGradeDf(data.grade_distribution) == null
            ? "Tables at D/F unknown."
            : `${formatNumber(tablesAtGradeDf(data.grade_distribution) ?? 0)} tables at grade D or F. ${
                data.totals?.needs_compaction == null
                  ? "Compaction need is not measured."
                  : `${formatNumber(data.totals.needs_compaction)} tables need compaction.`
              }`}
        </p>
      ) : null}
      {approvalCount > 0 ? (
        <p className="mb-6">
          <Link to="/governance" className="text-sm text-[var(--color-link)] hover:underline">
            <span className="[font-variant-numeric:tabular-nums]">{formatNumber(approvalCount)}</span> awaiting review
          </Link>
        </p>
      ) : null}
      <LakeZone
        tracked={data?.totals?.tables ?? null}
        distribution={data?.grade_distribution ?? null}
        loading={dashboard.isLoading}
        error={dashboard.isError}
        sizeGb={data?.totals?.size_gb ?? null}
        smallFiles={data?.totals?.small_files ?? null}
        needsCompaction={data?.totals?.needs_compaction ?? null}
        reclaimableBytes={data?.totals?.reclaimable_bytes ?? null}
        previousDf={previousDfCount(fleetTrends.data?.health_trend, tablesAtGradeDf(data?.grade_distribution))}
        hotDf={null}
      />
      {dashboard.isError ? null : (
        <>
          <FreshnessCard rows={dashboard.isLoading ? [] : freshnessRows} asOf={freshnessAsOf} loading={dashboard.isLoading} />
          <DiscoveredList
            findings={fastPathFindings.data ?? []}
            loading={fastPathFindings.isLoading}
            error={fastPathFindings.error}
          />
        </>
      )}
    </div>
  );
};
