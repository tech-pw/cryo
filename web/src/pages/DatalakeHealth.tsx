import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import {
  getDatalakeDashboard,
  getFastPathFindings,
  getScannerStatus,
  listPendingApprovals,
  listRuns
} from "../api/client";
import { getEngineLoad } from "../api/engineLoad";
import { getCryoVitals } from "../api/vitals";
import { Alert } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { useAuth } from "../lib/auth";
import { optionalNumber } from "../lib/batchSummary";
import { formatNumber } from "../lib/format";
import { freshnessCatalogHref } from "../lib/freshness";
import {
  latestBatchRun,
  reclaimInputFromImpact,
  reclaimYesterdayDisplay
} from "../lib/nowStrip";
import { DiscoveredList } from "./home/DiscoveredList";
import { FreshnessCard } from "./home/FreshnessCard";
import { LakeZone } from "./home/LakeZone";
import { StatusZone } from "./home/StatusZone";
import { MaintenanceCard } from "./home/MaintenanceCard";
import { ImpactCard } from "./home/ImpactCard";
import { AttentionCard } from "./home/AttentionCard";
import "./home/home.css";

const TOP_OFFENDERS_LIMIT = 1;

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
  const latest = nightlyRuns.isSuccess ? latestBatchRun(nightlyRuns.data ?? []) : null;
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
  const approvalCount = pendingApprovals.isSuccess ? pendingApprovals.data.length : null;
  const scannerLabel = scannerStatus.isPending
    ? null
    : scannerStatus.isError
      ? "Unavailable"
      : scannerStatus.data?.active_run
        ? "Running"
        : scannerStatus.data?.config && scannerStatus.data.config.enabled === false
          ? "Off"
          : scannerStatus.data?.latest_run?.status === "failed"
            ? "Last scan failed"
            : scannerStatus.data
              ? "Idle"
              : null;
  const scannerNeedsAttention =
    scannerLabel === "Off" || scannerLabel === "Last scan failed" || (scannerStatus.data?.queue_depth ?? 0) > 0;
  const engineLabel = engineLoad.isPending
    ? null
    : engineLoad.isError
      ? "Unavailable"
      : Array.isArray(engineLoad.data?.engines)
        ? `${engineLoad.data.engines.length} configured`
        : "Unavailable";
  const queries = [dashboard, scannerStatus, engineLoad, fastPathFindings, vitals, nightlyRuns, pendingApprovals];
  const refreshing = queries.some((query) => query.isFetching);

  return (
    <div className="home-overview">
      <header className="home-lead">
        <div>
          <h1>Home</h1>
          <p>Your lake at a glance. Health, maintenance and the next best action.</p>
        </div>
        <Button variant="ghost" size="icon" aria-label={refreshing ? "Refreshing overview" : "Refresh overview"} disabled={refreshing} onClick={() => void Promise.allSettled(queries.map((query) => query.refetch()))}>
          <RefreshCw size={16} aria-hidden="true" className={refreshing ? "motion-safe:animate-spin" : ""} />
        </Button>
      </header>
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
          {tablesAtGradeDf(data.grade_distribution) == null
            ? "Tables at D/F unknown."
            : `${formatNumber(tablesAtGradeDf(data.grade_distribution) ?? 0)} tables at grade D or F. ${
                data.totals?.needs_compaction == null
                  ? "Compaction need is not measured."
                  : `${formatNumber(data.totals.needs_compaction)} tables need compaction.`
              }`}
        </p>
      ) : null}
      <div className="home-grid">
      <LakeZone
        tracked={data?.totals?.tables ?? null}
        distribution={data?.grade_distribution ?? null}
        loading={dashboard.isLoading}
        error={dashboard.isError}
        sizeGb={data?.totals?.size_gb ?? null}
        smallFiles={data?.totals?.small_files ?? null}
      />
      <StatusZone
        vitals={vitals.data}
        vitalsLoading={vitals.isLoading}
        vitalsError={vitals.isError}
        scannerLabel={scannerLabel}
        queueDepth={scannerStatus.isError ? null : scannerStatus.data?.queue_depth ?? null}
        engineLabel={engineLabel}
      />
      <MaintenanceCard run={latest} loading={nightlyRuns.isLoading} error={nightlyRuns.isError} />
      <ImpactCard reclaim={reclaim} reclaimableBytes={data?.totals?.reclaimable_bytes ?? null} loading={dashboard.isLoading} error={dashboard.isError} />
      {dashboard.isError ? null : (
        <FreshnessCard rows={dashboard.isLoading ? [] : freshnessRows} asOf={freshnessAsOf} loading={dashboard.isLoading} />
      )}
      <AttentionCard approvals={approvalCount} approvalsLoading={pendingApprovals.isLoading} approvalsError={pendingApprovals.isError}
        needsCompaction={dashboard.isError ? null : data?.totals?.needs_compaction ?? null} failed={failedCount} runHref={latest ? `/runs/${latest.id}` : null} dashboardLoading={dashboard.isLoading}
        showScanner={scannerNeedsAttention} />
      <div className="home-discoveries">
          <DiscoveredList
            findings={fastPathFindings.data ?? []}
            loading={fastPathFindings.isLoading}
            error={fastPathFindings.error}
          />
      </div>
      </div>
      <p className="home-refresh-note">Overview uses the latest stored scans and maintenance results. Refreshing does not start a scan.</p>
    </div>
  );
};
