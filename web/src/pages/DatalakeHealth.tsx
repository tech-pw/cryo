import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import {
  getDatalakeDashboard,
  getFastPathFindings,
  getFleetTrends,
  getGovernorOffPeakWindow,
  getScannerStatus,
  listMaintenanceControls,
  listPendingApprovals,
  listRuns
} from "../api/client";
import { getEngineLoad } from "../api/engineLoad";
import { getCryoVitals } from "../api/vitals";
import { Alert } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { PageHeader } from "../components/ui/page-header";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { useAuth } from "../lib/auth";
import { optionalNumber } from "../lib/batchSummary";
import { formatNumber } from "../lib/format";
import { freshnessCatalogHref } from "../lib/freshness";
import {
  latestBatchRun,
  reclaimInputFromImpact,
  reclaimYesterdayDisplay
} from "../lib/nowStrip";
import { AttentionCard } from "./home/AttentionCard";
import { DiscoveredList } from "./home/DiscoveredList";
import { FreshnessCard } from "./home/FreshnessCard";
import { HotTablesCard } from "./home/HotTablesCard";
import { ImpactCard } from "./home/ImpactCard";
import { ImprovedCard } from "./home/ImprovedCard";
import { LakeZone } from "./home/LakeZone";
import { MaintenanceCard } from "./home/MaintenanceCard";
import { NowStrip } from "./home/NowStrip";
import { StatusZone, platformReason } from "./home/StatusZone";
import {
  STALE_SCAN_HOURS,
  activePauses,
  dfDelta,
  dfSeries,
  formatAgo,
  hoursSince,
  nightOutcome,
  offPeakState
} from "./home/homeSignals";
import "./home/home.css";

const TOP_OFFENDERS_LIMIT = 1;
const HOT_LIMIT = 5;
const NIGHTS = 14;
const LIVE_MS = 60_000;
const SLOW_MS = 300_000;

export const tablesAtGradeDf = (
  distribution: Array<{ grade: string; count: number }> | null | undefined
): number | null => {
  if (distribution == null) return null;
  let total = 0;
  for (const entry of distribution) {
    if (!Number.isFinite(entry.count) || entry.count < 0) return null;
    if (entry.grade !== "D" && entry.grade !== "F") continue;
    total += entry.count;
  }
  return total;
};

const gradedTables = (distribution: Array<{ grade: string; count: number }> | null | undefined) =>
  distribution?.filter((entry) => ["A", "B", "C", "D", "F"].includes(entry.grade)).reduce((sum, entry) => sum + entry.count, 0) ?? null;

export const DatalakeHealth = () => {
  const { api, token } = useAuth();
  const dashboard = useQuery({
    queryKey: ["dashboard", token, TOP_OFFENDERS_LIMIT, HOT_LIMIT],
    queryFn: () => getDatalakeDashboard(api, { top_limit: TOP_OFFENDERS_LIMIT, hot_limit: HOT_LIMIT }),
    retry: 1,
    refetchInterval: SLOW_MS
  });
  const scannerStatus = useQuery({
    queryKey: ["scanner-status", token],
    queryFn: () => getScannerStatus(api),
    retry: false,
    refetchInterval: LIVE_MS
  });
  const engineLoad = useQuery({
    queryKey: ["engine-load", token],
    queryFn: () => getEngineLoad(api),
    retry: false,
    refetchInterval: LIVE_MS
  });
  const fastPathFindings = useQuery({
    queryKey: ["fast-path-findings", token],
    queryFn: () => getFastPathFindings(api),
    retry: 1,
    refetchInterval: SLOW_MS
  });
  const vitals = useQuery({
    queryKey: ["cryo-vitals", token],
    queryFn: () => getCryoVitals(api),
    retry: 1,
    refetchInterval: LIVE_MS
  });
  const nightlyRuns = useQuery({
    queryKey: ["health-nightly-runs", token, NIGHTS],
    queryFn: () => listRuns(api, { pipeline: "batch", limit: NIGHTS }),
    retry: 1,
    refetchInterval: LIVE_MS
  });
  const pendingApprovals = useQuery({
    queryKey: ["pending-approvals", token],
    queryFn: () => listPendingApprovals(api),
    retry: 1,
    refetchInterval: LIVE_MS
  });
  const controls = useQuery({
    queryKey: ["maintenance-controls", token],
    queryFn: () => listMaintenanceControls(api),
    retry: 1,
    refetchInterval: LIVE_MS
  });
  const fleetTrends = useQuery({
    queryKey: ["fleet-trends", token, NIGHTS],
    queryFn: () => getFleetTrends(api, { days: NIGHTS }),
    retry: 1,
    refetchInterval: SLOW_MS
  });
  const offPeakWindow = useQuery({
    queryKey: ["governor-offpeak-window", token],
    queryFn: () => getGovernorOffPeakWindow(api),
    retry: false,
    refetchInterval: SLOW_MS
  });

  const data = dashboard.data;
  const runs = nightlyRuns.data ?? [];
  const latest = nightlyRuns.isSuccess ? latestBatchRun(runs) : null;
  const activeRun = runs.find((run) => !run.finished_at && nightOutcome(run) === "running") ?? null;
  const failedCount = latest ? optionalNumber(latest.summary, "failed") : null;
  const pauses = controls.isSuccess ? activePauses(controls.data) : null;
  const globalPause = pauses?.find((pause) => pause.scope_type === "global") ?? null;
  const offPeak = offPeakWindow.isSuccess ? offPeakState(offPeakWindow.data) : null;
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
  const approvalCount = pendingApprovals.isSuccess ? pendingApprovals.data.length : null;
  const scanner = scannerStatus.data;
  const lastScanAt = scanner?.latest_run?.finished_at ?? scanner?.latest_run?.created_at ?? null;
  const scanAgeHours = hoursSince(lastScanAt);
  const scanStale = scanAgeHours != null && scanAgeHours > STALE_SCAN_HOURS && !scanner?.active_run;
  const scannerLabel = scannerStatus.isPending
    ? null
    : scannerStatus.isError
      ? "Unavailable"
      : scanner?.active_run
        ? "Running"
        : scanner?.config && scanner.config.enabled === false
          ? "Off"
          : scanner?.latest_run?.status === "failed"
            ? "Last scan failed"
            : scanner
              ? "Idle"
              : null;
  const scannerProblem = scannerLabel === "Off"
    ? "Scheduled scanning is turned off, so grades stop updating."
    : scannerLabel === "Last scan failed"
      ? "The last scan failed. Grades may be out of date."
      : scanStale
        ? `The last scan finished ${formatAgo(lastScanAt)}. Grades may be out of date.`
        : null;
  const currentDf = tablesAtGradeDf(data?.grade_distribution);
  const trend = fleetTrends.data?.health_trend;
  const vitalsReason = vitals.data?.status === "CRITICAL" ? platformReason(vitals.data)?.text ?? "Open the platform card for details." : null;
  const dataAge = formatAgo(freshnessAsOf);
  const queries = [dashboard, scannerStatus, engineLoad, fastPathFindings, vitals, nightlyRuns, pendingApprovals, controls, fleetTrends, offPeakWindow];
  const refreshing = queries.some((query) => query.isFetching);
  const flagged = fastPathFindings.data?.length ?? 0;

  return (
    <div className="home-overview">
      <PageHeader
        title="Home"
        subtitle={<>Your lake at a glance. Health, maintenance and the next best action.{dataAge ? <span className="home-data-age"> Scan data from {dataAge}.</span> : null}</>}
        breadcrumbs={false}
        actions={
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={refreshing ? "Refreshing overview" : "Refresh overview"}
                disabled={refreshing}
                onClick={() => void Promise.allSettled(queries.map((query) => query.refetch()))}
              >
                <RefreshCw size={16} aria-hidden="true" className={refreshing ? "motion-safe:animate-spin" : ""} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Reloads stored results. It does not start a scan. Updates every minute on its own.</TooltipContent>
          </Tooltip>
        }
      />
      {globalPause ? (
        <Alert
          className="page-alert"
          variant="error"
          title="Maintenance is paused globally"
          description={<>{globalPause.reason ? `${globalPause.reason} ` : ""}Paused by {globalPause.changed_by}. Nothing new is admitted until it resumes. <Link className="home-text-link" to="/runs?tab=holds">Manage holds</Link></>}
        />
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
      {data ? (
        <p className="sr-only" role="status" aria-live="polite">
          {currentDf == null
            ? "Tables at D/F unknown."
            : `${formatNumber(currentDf)} tables at grade D or F. ${
                data.totals?.needs_compaction == null
                  ? "Compaction need is not measured."
                  : `${formatNumber(data.totals.needs_compaction)} tables need compaction.`
              }`}
        </p>
      ) : null}
      <NowStrip
        pauses={pauses}
        activeRun={activeRun}
        runningJobs={data?.totals?.running_jobs ?? null}
        engines={engineLoad.isSuccess ? engineLoad.data.engines : null}
        enginesError={engineLoad.isError}
        workersOnline={engineLoad.isSuccess ? engineLoad.data.workers_online : null}
        offPeak={offPeak}
        scannerLabel={scannerLabel}
        queueDepth={scannerStatus.isError ? null : scanner?.queue_depth ?? null}
        lastScanAgo={formatAgo(lastScanAt)}
        scanStale={scanStale}
      />
      <div className="home-grid">
        <MaintenanceCard run={latest} loading={nightlyRuns.isLoading} error={nightlyRuns.isError} history={runs} offPeak={offPeak} />
        <AttentionCard
          pauses={pauses}
          platformCritical={vitalsReason}
          approvals={approvalCount}
          approvalsLoading={pendingApprovals.isLoading}
          approvalsError={pendingApprovals.isError}
          needsCompaction={dashboard.isError ? null : data?.totals?.needs_compaction ?? null}
          hotAtRisk={dashboard.isError ? null : data?.hot_at_risk_count ?? null}
          failed={failedCount}
          runHref={latest ? `/runs/${latest.id}` : null}
          dashboardLoading={dashboard.isLoading}
          scannerProblem={scannerProblem}
          flaggedTables={flagged}
        />
        <LakeZone
          tracked={data?.totals?.tables ?? null}
          distribution={data?.grade_distribution ?? null}
          loading={dashboard.isLoading}
          error={dashboard.isError}
          sizeGb={data?.totals?.size_gb ?? null}
          smallFiles={data?.totals?.small_files ?? null}
          totalFiles={data?.totals?.total_files ?? null}
          dfDelta={dfDelta(trend, currentDf, gradedTables(data?.grade_distribution))}
          dfSeries={dfSeries(trend)}
        />
        <ImpactCard
          reclaim={reclaim}
          reclaimableBytes={data?.totals?.reclaimable_bytes ?? null}
          loading={dashboard.isLoading}
          error={dashboard.isError}
          wowPct={vitals.data?.impact?.wow_pct ?? null}
          projectedSavingsUsd={data?.totals?.projected_savings_usd_month ?? null}
        />
        <HotTablesCard tables={data?.hot_at_risk ?? null} total={data?.hot_at_risk_count ?? null} loading={dashboard.isLoading} error={dashboard.isError} />
        <ImprovedCard tables={data?.recently_improved ?? null} total={data?.recently_improved_count ?? null} loading={dashboard.isLoading} error={dashboard.isError} />
        {dashboard.isError ? null : (
          <FreshnessCard rows={dashboard.isLoading ? [] : freshnessRows} asOf={freshnessAsOf} loading={dashboard.isLoading} />
        )}
        <StatusZone vitals={vitals.data} vitalsLoading={vitals.isLoading} vitalsError={vitals.isError} />
        <div className="home-discoveries" id="home-discovered">
          <DiscoveredList
            findings={fastPathFindings.data ?? []}
            loading={fastPathFindings.isLoading}
            error={fastPathFindings.error}
          />
        </div>
      </div>
    </div>
  );
};
