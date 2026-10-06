import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { toastScanResult } from "../../lib/operations";
import {
  assignTables,
  getBloomFilterRecommendations,
  getComplianceStatus,
  getEffectivePreset,
  getFastPathFindings,
  getPartitionEvolutionRecommendation,
  getTable,
  getTableAudit,
  getTableRuns,
  getTableBenefitScores,
  getTableCost,
  getTableHealthTrends,
  getTableHistory,
  getTablePartitionAccess,
  getTablePartitions,
  getTableS3Tiering,
  getTableSignals,
  listDestinations,
  listCompatiblePresets,
  listPresets,
  listSources,
  protectSnapshot,
  runMaintenance,
  runManifestRewrite,
  triggerScan,
  unassignTables,
  getTablePiiClassification,
  getDataQualityProfile,
  getSlaStatusDashboard,
  getQueryPerformanceDashboard,
  getSilentDataLoss,
  repairSilentDataLoss,
  patchTable,
  listTableAlertSubscriptions,
  createTableAlertSubscription,
  deleteTableAlertSubscription,
  getRun,
  getRunJobs
} from "../../api/client";
import type { HistoryPoint } from "../../api/types";
import { buildHealthScoreFactors } from "../../components/HealthScoreBreakdown";
import { buildDiagnosisVerdict } from "../../components/TableDiagnosisPanel";
import { useAuth } from "../../lib/auth";
import { formatCurrency } from "../../lib/format";
import { formatPartitionColumnLabel } from "../../components/table-detail/helpers";
import {
  TABLE_DETAIL_TABS,
  TABLE_HASH_ALIASES,
  TABLE_TAB_ALIASES,
  translateTableDetailLocation
} from "./tabAliases";
import { tableKeys } from "./tableKeys";

export { TABLE_TAB_ALIASES };
export const TABLE_TABS = new Set<string>(TABLE_DETAIL_TABS);

const finiteOrNull = (value: number | null | undefined): number | null =>
  value == null || !Number.isFinite(value) ? null : value;

export const firstFinite = (...values: Array<number | null | undefined>): number | null => {
  for (const value of values) {
    const finite = finiteOrNull(value);
    if (finite != null) return finite;
  }
  return null;
};

export const tableSavingsDisplay = ({
  hasStats,
  hasCostView,
  projectedSavingsUsd,
  showLatencyBenefit
}: {
  hasStats: boolean;
  hasCostView: boolean;
  projectedSavingsUsd: number | null;
  showLatencyBenefit: boolean;
}): string => {
  if (!hasStats && !hasCostView) return "Not scanned";
  if (projectedSavingsUsd == null) return "Cost data unavailable";
  if (showLatencyBenefit) return "Latency benefit";
  return formatCurrency(projectedSavingsUsd);
};
import {
  formatEngineLabel,
  hasSqlFallbackConfigured,
  nativeGoDeleteFilesFallbackNotice,
  nativeGoDeleteFilesRewriteError,
  resolveNativeEngineImpl
} from "../../lib/engines";

const isNotFoundError = (error: unknown): boolean => {
  if (!error || typeof error !== "object") return false;
  const maybeHttpError = error as { status?: number; response?: { status?: number } };
  return maybeHttpError.status === 404 || maybeHttpError.response?.status === 404;
};

export function useTableDetail() {
  const { tableId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { api, token, role } = useAuth();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [queuedScanRunId, setQueuedScanRunId] = useState<string | null>(null);
  const [queuedMaintenanceRunId, setQueuedMaintenanceRunId] = useState<string | null>(null);
  // Tracks a run to completion independently of queuedMaintenanceRunId (which the "in flight"
  // banner owns) so we can surface it even after that banner clears. A run can report
  // status=succeeded while its plan skipped the one operation that would've fixed the
  // diagnostic that prompted it (e.g. a table just under the preset's min_small_files
  // threshold) -- silently returning to the same "Act now" card as if nothing happened reads
  // as a broken product, not a no-op. See cryo/planner/planner.py's "Skipped optimize" notes.
  const [trackedRunId, setTrackedRunId] = useState<string | null>(null);
  const [lastRunSkipNotes, setLastRunSkipNotes] = useState<string[]>([]);
  const [protectedLocal, setProtectedLocal] = useState<Array<{ snapshot_id: string; label?: string | null }>>([]);
  const [ownerEmailInput, setOwnerEmailInput] = useState("");
  const [newSubscriberEmail, setNewSubscriberEmail] = useState("");
  const requestedTab = searchParams.get("tab");
  const canonicalTab = requestedTab ? TABLE_TAB_ALIASES[requestedTab] ?? requestedTab : "overview";
  const activeTab = TABLE_TABS.has(canonicalTab) ? canonicalTab : "overview";
  const onTab = (...names: string[]) => names.includes(activeTab);

  useEffect(() => {
    setQueuedScanRunId(null);
    setQueuedMaintenanceRunId(null);
    setTrackedRunId(null);
    setLastRunSkipNotes([]);
    setProtectedLocal([]);
  }, [tableId]);

  const trackedRun = useQuery({
    queryKey: ["maintenance-run-tracker", trackedRunId, token],
    queryFn: () => getRun(api, trackedRunId as string),
    enabled: Boolean(trackedRunId),
    refetchInterval: (query) => (query.state.data?.finished_at ? false : 2000)
  });

  useEffect(() => {
    if (!trackedRunId || !trackedRun.data?.finished_at) return;
    let cancelled = false;
    getRunJobs(api, trackedRunId)
      .then((jobs) => {
        if (cancelled) return;
        const notes = jobs.flatMap((job) => {
          const raw = (job.plan as { notes?: unknown } | null | undefined)?.notes;
          const planNotes = Array.isArray(raw)
            ? raw.filter((note): note is string => typeof note === "string")
            : [];
          if (typeof job.error === "string" && job.error.includes("total_delete_files")) {
            return [...planNotes, job.error];
          }
          return planNotes;
        });
        setLastRunSkipNotes(
          notes.filter((note) => {
            const lower = note.toLowerCase();
            return lower.startsWith("skipped") || lower.includes("total_delete_files");
          })
        );
      })
      .catch(() => {
        // Best-effort: if we can't fetch the job breakdown, just show no skip banner rather
        // than block on it -- the run's own status is still visible via "Open run".
      })
      .finally(() => {
        if (!cancelled) setTrackedRunId(null);
      });
    return () => {
      cancelled = true;
    };
  }, [api, trackedRunId, trackedRun.data?.finished_at]);

  const table = useQuery({ queryKey: ["table", tableId, token], queryFn: () => getTable(api, tableId) });

  useEffect(() => {
    setOwnerEmailInput(table.data?.owner_email ?? "");
  }, [table.data?.owner_email]);

  const audit = useQuery({
    queryKey: tableKeys.audit(tableId, token),
    queryFn: () => getTableAudit(api, tableId),
    enabled: onTab("maintenance"),
    retry: 1
  });
  const runs = useQuery({
    queryKey: tableKeys.runs(tableId, token),
    queryFn: () => getTableRuns(api, tableId),
    enabled: onTab("maintenance"),
    retry: 1
  });
  const healthTrends = useQuery({
    queryKey: ["table-health-trends", tableId, token],
    queryFn: () => getTableHealthTrends(api, tableId, 30),
    retry: 1
  });
  const effectivePreset = useQuery({
    queryKey: ["effective-preset", tableId, token],
    queryFn: () => getEffectivePreset(api, tableId),
    retry: 1
  });
  const tableCost = useQuery({
    queryKey: ["table-cost", tableId, token],
    queryFn: () => getTableCost(api, tableId),
    retry: false
  });
  const tableSignals = useQuery({
    queryKey: ["table-signals", tableId, token, "detail"],
    queryFn: () => getTableSignals(api, tableId),
    retry: false
  });
  const benefitScores = useQuery({
    queryKey: ["table-benefit-scores", tableId, token, "detail"],
    queryFn: () => getTableBenefitScores(api, tableId),
    enabled: onTab("cost"),
    retry: false
  });
  const s3Tiering = useQuery({
    queryKey: tableKeys.tiering(tableId, token),
    queryFn: () => getTableS3Tiering(api, tableId),
    enabled: onTab("cost"),
    retry: false
  });
  const partitionEvolution = useQuery({
    queryKey: ["table-partition-evolution", tableId, token],
    queryFn: () => getPartitionEvolutionRecommendation(api, tableId),
    enabled: onTab("files"),
    retry: false
  });
  const fastPathFindings = useQuery({
    queryKey: tableKeys.fastPath(tableId, token),
    queryFn: () => getFastPathFindings(api, tableId),
    enabled: onTab("overview") && Boolean(tableId),
    retry: false
  });
  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const compatiblePresets = useQuery({
    queryKey: ["compatible-presets", tableId, token],
    queryFn: () => listCompatiblePresets(api, tableId)
  });
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });
  const compliance = useQuery({
    queryKey: ["table-compliance", tableId, token],
    queryFn: () => getComplianceStatus(api, tableId),
    enabled: onTab("quality") || settingsOpen,
    retry: false
  });
  const partitions = useQuery({
    queryKey: tableKeys.partitions(tableId, token),
    queryFn: () => getTablePartitions(api, tableId),
    enabled: onTab("files"),
    retry: false
  });
  const partitionAccess = useQuery({
    queryKey: tableKeys.partitionAccess(tableId, token),
    queryFn: () => getTablePartitionAccess(api, tableId),
    enabled: onTab("files", "usage"),
    retry: false
  });
  const history = useQuery({
    queryKey: tableKeys.history(tableId, token),
    queryFn: () => getTableHistory(api, tableId) as Promise<HistoryPoint[]>,
    enabled: onTab("files"),
    retry: false
  });
  const bloomRecommendations = useQuery({
    queryKey: tableKeys.bloom(tableId, token),
    queryFn: () => getBloomFilterRecommendations(api, tableId),
    enabled: onTab("files"),
    retry: false
  });
  const sources = useQuery({
    queryKey: tableKeys.sources(token),
    queryFn: () => listSources(api),
    enabled: Boolean(table.data) && !table.data?.source_name,
    retry: 1
  });

  const pii = useQuery({
    queryKey: tableKeys.pii(tableId, token),
    queryFn: () => getTablePiiClassification(api, tableId),
    enabled: onTab("files", "quality"),
    retry: false
  });

  const dataQuality = useQuery({
    queryKey: tableKeys.dataQuality(tableId, token),
    queryFn: () => getDataQualityProfile(api, tableId, 30),
    enabled: onTab("files", "quality"),
    retry: false
  });

  const tableSla = useQuery({
    queryKey: tableKeys.sla(tableId, token),
    queryFn: () => getSlaStatusDashboard(api, false, tableId),
    enabled: onTab("quality"),
    retry: false
  });
  const slaStatus = tableSla.data?.slas?.[0] ?? null;

  const tableQueries = useQuery({
    queryKey: ["table-query-performance", tableId, token],
    queryFn: () => getQueryPerformanceDashboard(api, 168, undefined, tableId),
    enabled: onTab("usage"),
    retry: false
  });

  const alertSubscriptions = useQuery({
    queryKey: ["table-alert-subscriptions", tableId, token],
    queryFn: () => listTableAlertSubscriptions(api, tableId),
    enabled: settingsOpen,
    retry: false
  });

  const setOwner = useMutation({
    mutationFn: (ownerEmail: string) => patchTable(api, tableId, { owner_email: ownerEmail || null }),
    onSuccess: () => {
      toast.success("Owner updated");
      void queryClient.invalidateQueries({ queryKey: ["table", tableId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const addSubscriber = useMutation({
    mutationFn: (email: string) => createTableAlertSubscription(api, tableId, email),
    onSuccess: () => {
      toast.success("Subscribed to breach alerts");
      void queryClient.invalidateQueries({ queryKey: ["table-alert-subscriptions", tableId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const removeSubscriber = useMutation({
    mutationFn: (subscriptionId: string) => deleteTableAlertSubscription(api, tableId, subscriptionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["table-alert-subscriptions", tableId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const assign = useMutation({
    mutationFn: (presetId: string) =>
      assignTables(api, { selection: { mode: "ids", ids: [tableId] }, preset_id: presetId }),
    onSuccess: () => {
      toast.success("Preset assigned");
      void effectivePreset.refetch();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const unassign = useMutation({
    mutationFn: () => unassignTables(api, { selection: { mode: "ids", ids: [tableId] } }),
    onSuccess: () => {
      toast.success("Preset unassigned");
      void effectivePreset.refetch();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const manifestRewrite = useMutation({
    mutationFn: () => {
      const preset = presets.data?.find((item) => item.id === effectivePreset.data?.preset_id);
      const destinationId = preset?.destination_id;
      if (!preset || !destinationId) {
        throw new Error("No preset assigned to this table. Assign a preset before running maintenance.");
      }
      return runManifestRewrite(api, {
        table_ids: [tableId],
        destination_id: destinationId,
        preset_id: preset.id,
        trigger: "web-detail-manifest"
      });
    },
    onSuccess: (result) => toast.success(`Manifest rewrite queued: ${result.run_id}`),
    onError: (error) => toast.error((error as Error).message)
  });

  const protect = useMutation({
    mutationFn: (values: { snapshot_id: string | number; label?: string }) =>
      protectSnapshot(api, tableId, values),
    onSuccess: (result) => {
      toast.success("Snapshot protected");
      setProtectedLocal((prev) =>
        [{ snapshot_id: String(result.snapshot_id), label: result.label }, ...prev].slice(0, 8)
      );
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const run = useMutation({
    mutationFn: () => {
      setQueuedMaintenanceRunId(null);
      setTrackedRunId(null);
      setLastRunSkipNotes([]);
      const preset = presets.data?.find((item) => item.id === effectivePreset.data?.preset_id);
      const destinationId = preset?.destination_id;
      if (!preset || !destinationId) {
        throw new Error("No preset assigned to this table. Assign a preset before running maintenance.");
      }
      return runMaintenance(api, {
        table_ids: [tableId],
        destination_id: destinationId,
        preset_id: preset.id,
        mode: preset.mode ?? "standard",
        trigger: "web-detail"
      });
    },
    onSuccess: (result) => {
      const preset = presets.data?.find((item) => item.id === effectivePreset.data?.preset_id);
      const mode = preset?.mode ?? "standard";
      const destination = destinations.data?.find((item) => item.id === preset?.destination_id);
      const deleteFiles = table.data?.stats?.total_delete_files ?? 0;
      const rewriteAffected =
        destination?.engine === "native" &&
        resolveNativeEngineImpl(destination.config) === "go" &&
        deleteFiles > 0 &&
        (mode === "standard" || mode === "deep");
      const rewriteHasFallback = rewriteAffected && hasSqlFallbackConfigured(destination?.config);
      if (rewriteAffected && !rewriteHasFallback) {
        toast.error(`${nativeGoDeleteFilesRewriteError} Run queued: ${result.run_id}`);
      } else if (rewriteHasFallback) {
        toast.info(`${nativeGoDeleteFilesFallbackNotice} Run queued: ${result.run_id}`);
      } else {
        toast.success(`Run queued: ${result.run_id}`);
      }
      setQueuedMaintenanceRunId(result.run_id);
      setTrackedRunId(result.run_id);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["cost-dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error) => {
      setQueuedMaintenanceRunId(null);
      toast.error((error as Error).message);
    }
  });

  const schedule = useMutation({
    mutationFn: (scheduledAtIso: string) => {
      const preset = presets.data?.find((item) => item.id === effectivePreset.data?.preset_id);
      const destinationId = preset?.destination_id;
      if (!preset || !destinationId) {
        throw new Error("No preset assigned to this table. Assign a preset before scheduling maintenance.");
      }
      return runMaintenance(api, {
        table_ids: [tableId],
        destination_id: destinationId,
        preset_id: preset.id,
        mode: preset.mode ?? "standard",
        trigger: "web-detail",
        scheduled_at: scheduledAtIso
      });
    },
    onSuccess: (result) => {
      const preset = presets.data?.find((item) => item.id === effectivePreset.data?.preset_id);
      const mode = preset?.mode ?? "standard";
      const destination = destinations.data?.find((item) => item.id === preset?.destination_id);
      const deleteFiles = table.data?.stats?.total_delete_files ?? 0;
      const rewriteAffected =
        destination?.engine === "native" &&
        resolveNativeEngineImpl(destination.config) === "go" &&
        deleteFiles > 0 &&
        (mode === "standard" || mode === "deep");
      const rewriteHasFallback = rewriteAffected && hasSqlFallbackConfigured(destination?.config);
      if (rewriteAffected && !rewriteHasFallback) {
        toast.error(`${nativeGoDeleteFilesRewriteError} Run scheduled: ${result.run_id}`);
      } else if (rewriteHasFallback) {
        toast.info(`${nativeGoDeleteFilesFallbackNotice} Run scheduled: ${result.run_id}`);
      } else {
        toast.success(`Run scheduled: ${result.run_id}`);
      }
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const scan = useMutation({
    mutationFn: () => {
      setQueuedScanRunId(null);
      return triggerScan(api, {
        scope: "tables",
        table_ids: [tableId],
        full_scan: true,
        trigger: "web-table-detail"
      });
    },
    onSuccess: (result) => {
      toastScanResult(result);
      if (result.queued) setQueuedScanRunId(result.run_id);
      void queryClient.invalidateQueries({ queryKey: ["table", tableId] });
      void queryClient.invalidateQueries({ queryKey: ["tables"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["cost-dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
    },
    onError: (error) => {
      setQueuedScanRunId(null);
      toast.error((error as Error).message);
    }
  });

  const silentLoss = useQuery({
    queryKey: ["silent-data-loss", tableId, token],
    queryFn: () => getSilentDataLoss(api, tableId),
    // Rendered as a page-level banner in TableDetailShell (outside any tab panel), so it must
    // fetch regardless of which tab is active -- gating on onTab("quality") meant the banner
    // could only ever appear if the operator happened to already be on the Quality tab,
    // defeating the point of a page-wide silent-data-loss warning.
    enabled: Boolean(tableId),
    refetchOnWindowFocus: true,
    retry: false
  });

  const repairSilentLoss = useMutation({
    mutationFn: () => repairSilentDataLoss(api, tableId),
    onSuccess: (result) => {
      toast.success(result.message || "Repair queued");
      void queryClient.invalidateQueries({ queryKey: ["silent-data-loss", tableId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const handleRunAction = () => {
    if (queuedMaintenanceRunId) navigate(`/runs/${queuedMaintenanceRunId}`);
  };

  const handleScanAction = () => {
    if (queuedScanRunId) {
      navigate(`/scans/${queuedScanRunId}`);
      return;
    }
    scan.mutate();
  };

  const row = table.data;
  const sourceName = sources.data?.find((s) => s.id === row?.source_id)?.name ?? "Catalog";
  const triggered = tableSignals.data?.signals.filter((s) => s.triggered) ?? [];
  const stats = row?.stats;
  const verdict = buildDiagnosisVerdict(stats);
  const alertType = verdict.level === "critical" ? "error" : verdict.level === "warning" ? "warning" : "success";
  const statsRaw = stats?.raw ?? {};
  const lastSnapshotCommittedAt =
    typeof statsRaw.last_snapshot_committed_at === "string" ? statsRaw.last_snapshot_committed_at : null;
  const hasStatsCostFallback = Boolean(
    stats?.projected_savings_usd_month || stats?.projected_monthly_s3_cost_usd
  );
  const costView = tableCost.data;
  const costByOperation = costView?.by_operation ?? [];
  const currentCostUsd = firstFinite(
    costView?.current_monthly_cost_usd,
    stats?.estimated_monthly_s3_cost_usd
  );
  const projectedCostUsd = firstFinite(
    costView?.projected_monthly_cost_usd,
    stats?.projected_monthly_s3_cost_usd
  );
  const projectedSavingsRaw = firstFinite(
    costView?.projected_savings_usd_month,
    stats?.projected_savings_usd_month
  );
  const hasProjectedSavings = projectedSavingsRaw != null;
  const savingsUsd: number | null = projectedSavingsRaw;
  const showLatencyBenefit =
    hasProjectedSavings &&
    projectedSavingsRaw === 0 &&
    (Boolean(stats?.needs_compaction) || triggered.length > 0);
  const savingsDisplay = tableSavingsDisplay({
    hasStats: Boolean(stats),
    hasCostView: Boolean(costView),
    projectedSavingsUsd: projectedSavingsRaw,
    showLatencyBenefit
  });
  const reclaimableBytes: number | null = firstFinite(costView?.reclaimable_bytes, stats?.reclaimable_bytes);
  const storageParts = [
    costView?.live_bytes,
    costView?.dead_bytes,
    costView?.metadata_bytes,
    costView?.orphan_bytes
  ];
  const storageComplete = storageParts.every((value) => value != null && Number.isFinite(value));
  const storageBytes = storageComplete
    ? storageParts.reduce<number>((sum, value) => sum + Number(value), 0)
    : null;
  const partitionColLabel = formatPartitionColumnLabel(stats?.partition_col);
  const hideSinglePartitionKpi =
    partitionColLabel === "Unpartitioned" && (stats?.total_partitions ?? 0) <= 1;
  const complianceNotFound = isNotFoundError(compliance.error);
  const score = stats?.overall_score == null || Number.isNaN(stats.overall_score) ? 0 : stats.overall_score;
  const scoreFactors = useMemo(
    () =>
      buildHealthScoreFactors(
        stats,
        costView ? costView.orphan_bytes : null,
        storageComplete ? storageBytes : null
      ),
    [stats, costView, storageComplete, storageBytes]
  );

  // Rendered as-is from cryo/scanner/health.py::compute_health_score, persisted into
  // stats.raw.health_deductions at scan time — this is the same computation that produced
  // `score`, so it can never drift from what the UI shows (no client-side reimplementation).
  const pointDeductions = useMemo(() => {
    const raw = (stats?.raw ?? {}) as Record<string, any>;
    const deductions = raw.health_deductions;
    return Array.isArray(deductions)
      ? (deductions as Array<{ label: string; deduction: number; severity: "critical" | "warning"; reason: string }>)
      : [];
  }, [stats]);

  const trendData = useMemo(
    () =>
      (healthTrends.data?.trend_points ?? []).map((p) => ({
        ...p,
        timestamp: new Date(p.timestamp).getTime()
      })),
    [healthTrends.data?.trend_points]
  );

  const degradationRate = healthTrends.data?.degradation_rate;
  const degradationBadge =
    degradationRate != null && degradationRate < -0.5
      ? `${Math.abs(degradationRate).toFixed(1)} pts/day`
      : null;
  const trendDirection: "up" | "down" | "flat" =
    degradationRate == null ? "flat" : degradationRate < 0 ? "down" : degradationRate > 0 ? "up" : "flat";
  const trendLabel =
    degradationRate == null
      ? null
      : `${degradationRate > 0 ? "+" : ""}${degradationRate.toFixed(1)} pts/day`;

  const protectedFromAudit = useMemo(() => {
    const items: Array<{ snapshot_id: string; label?: string | null; ts?: string | null }> = [];
    for (const entry of audit.data ?? []) {
      const action = (entry.action ?? "").toLowerCase();
      if (!action.includes("protect")) continue;
      const detail =
        typeof entry.detail === "string" ? entry.detail : entry.detail ? JSON.stringify(entry.detail) : "";
      const snapshotMatch = detail.match(/snapshot[_\s-]?id[=: ]+(\d+)/i) || detail.match(/(\d{4,})/);
      items.push({
        snapshot_id: snapshotMatch ? snapshotMatch[1] : "",
        label: detail || null,
        ts: entry.ts ?? entry.created_at ?? null
      });
    }
    return items.slice(0, 8);
  }, [audit.data]);

  const effectiveDestination = useMemo(() => {
    const preset = presets.data?.find((item) => item.id === effectivePreset.data?.preset_id);
    if (!preset) return null;
    return destinations.data?.find((item) => item.id === preset.destination_id) ?? null;
  }, [presets.data, destinations.data, effectivePreset.data?.preset_id]);

  const assignPresetOptions = useMemo(() => {
    const compatible = compatiblePresets.data ?? [];
    const options = compatible.map((p) => ({ label: p.name, value: p.id }));
    const effectiveId = effectivePreset.data?.preset_id;
    if (effectiveId && !options.some((o) => o.value === effectiveId)) {
      const fromAll = presets.data?.find((p) => p.id === effectiveId);
      if (fromAll) {
        options.unshift({ label: `${fromAll.name} (catalog not linked)`, value: fromAll.id });
      }
    }
    return options;
  }, [compatiblePresets.data, presets.data, effectivePreset.data?.preset_id]);

  const protectedItems: Array<{ snapshot_id: string; label?: string | null; ts?: string | null }> = [
    ...protectedLocal,
    ...protectedFromAudit
  ];

  const rawColumnQuality = useMemo(() => {
    const rawData = stats?.raw as any;
    return (rawData?.column_quality || []) as Array<{
      field_id: number;
      column_name: string;
      column_type?: string | null;
      null_count: number;
      value_count: number;
      null_rate: number;
      lower_bound?: any;
      upper_bound?: any;
    }>;
  }, [stats]);

  const mergedColumns = useMemo(() => {
    const piiMatches = pii.data?.matches || [];
    const dqMetrics = dataQuality.data?.column_metrics || [];
    const isPartitionCol = (colName: string) => {
      if (!stats?.partition_col) return false;
      const normalizedPartition = stats.partition_col.toLowerCase();
      const normalizedCol = colName.toLowerCase();
      return normalizedPartition.includes(normalizedCol);
    };

    return rawColumnQuality.map((col) => {
      const piiMatch = piiMatches.find((p) => p.column_name.toLowerCase() === col.column_name.toLowerCase());
      const dqMatch = dqMetrics.find((d) => d.column_name.toLowerCase() === col.column_name.toLowerCase());
      const colType =
        piiMatch?.column_type ||
        col.column_type ||
        dqMatch?.column_type ||
        (isPartitionCol(col.column_name) ? stats?.partition_col_type : null) ||
        "unknown";

      return {
        key: col.column_name,
        name: col.column_name,
        fieldId: col.field_id,
        type: colType,
        nullCount: col.null_count,
        valueCount: col.value_count,
        nullRate: col.null_rate,
        lowerBound: col.lower_bound,
        upperBound: col.upper_bound,
        isPartition: isPartitionCol(col.column_name),
        piiCategory: piiMatch?.pii_category,
        piiConfidence: piiMatch?.similarity,
        isAlreadyTagged: piiMatch?.already_tagged,
        dqNullBaseline: dqMatch?.null_rate_baseline_pct,
        dqNullSpike: dqMatch?.null_rate_spike,
        dqValueRangeChanged: dqMatch?.value_range_changed,
        dqValueRangeExplanation: dqMatch?.value_range_explanation,
      };
    });
  }, [rawColumnQuality, pii.data, dataQuality.data, stats]);

  const qualityBreakdown = useMemo(() => {
    const metrics = dataQuality.data?.column_metrics ?? [];
    const signals = dataQuality.data?.commit_signals;
    return {
      nullSpikeCount: metrics.filter((c) => c.null_rate_spike).length,
      rangeShiftCount: metrics.filter((c) => c.value_range_changed).length,
      schemaChangeCount:
        (dataQuality.data?.new_columns.length ?? 0) + (dataQuality.data?.dropped_columns.length ?? 0),
      arrivalOverdue: signals?.arrival_overdue ?? null,
      hoursSinceDataCommit: signals?.hours_since_data_commit ?? null,
      rowCountAnomaly: signals?.row_count_anomaly ?? null,
      rowCountBaselined: signals?.net_rows_baseline_median != null
    };
  }, [dataQuality.data]);

  const historyPoints = useMemo(
    () =>
      (history.data ?? [])
        .filter((point) => Boolean(point.scan_at))
        .map((point) => ({
          ...point,
          timestamp: new Date(point.scan_at as string).getTime()
        }))
        .sort((a, b) => a.timestamp - b.timestamp),
    [history.data]
  );

  useEffect(() => {
    const hash = window.location.hash;
    const hashId = hash.replace(/^#/, "");
    const tabNeedsAlias = Boolean(requestedTab && TABLE_TAB_ALIASES[requestedTab] && requestedTab !== activeTab);
    if (!hashId.startsWith("hq-") && !tabNeedsAlias) return;
    const translated = translateTableDetailLocation(searchParams, hash);
    const query = translated.search.toString();
    const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${translated.hash}`;
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (translated.search.toString() !== searchParams.toString()) {
      setSearchParams(translated.search, { replace: true });
    }
    if (currentUrl !== nextUrl) {
      window.history.replaceState(null, "", nextUrl);
    }
  }, [activeTab, requestedTab, searchParams, setSearchParams]);

  useEffect(() => {
    const raw = (location.hash || window.location.hash).replace(/^#/, "");
    const mapped = TABLE_HASH_ALIASES[raw];
    const hashId = mapped?.hash || (raw.startsWith("hq-") ? "" : raw);
    if (!hashId) return;
    let attempts = 0;
    const timer = window.setInterval(() => {
      const target = document.getElementById(hashId);
      attempts += 1;
      if (target) {
        target.scrollIntoView({ block: "start" });
        window.clearInterval(timer);
      } else if (attempts > 20) {
        window.clearInterval(timer);
      }
    }, 150);
    return () => window.clearInterval(timer);
  }, [activeTab, location.hash]);

  const handleTabChange = (tabKey: string) => {
    const next = new URLSearchParams(searchParams);
    const canonical = TABLE_TAB_ALIASES[tabKey] ?? tabKey;
    if (canonical === "overview") {
      next.delete("tab");
    } else {
      next.set("tab", canonical);
    }
    setSearchParams(next, { replace: true });
  };

  return {
    tableId,
    api,
    role,
    token,
    navigate,
    searchParams,
    setSearchParams,
    queryClient,
    previewOpen,
    setPreviewOpen,
    settingsOpen,
    setSettingsOpen,
    queuedScanRunId,
    queuedMaintenanceRunId,
    trackedRunId,
    lastRunSkipNotes,
    protectedLocal,
    ownerEmailInput,
    setOwnerEmailInput,
    newSubscriberEmail,
    setNewSubscriberEmail,
    table,
    audit,
    runs,
    healthTrends,
    effectivePreset,
    tableCost,
    tableSignals,
    benefitScores,
    s3Tiering,
    partitionEvolution,
    fastPathFindings,
    presets,
    compatiblePresets,
    destinations,
    compliance,
    partitions,
    partitionAccess,
    history,
    bloomRecommendations,
    sources,
    pii,
    dataQuality,
    tableSla,
    slaStatus,
    tableQueries,
    alertSubscriptions,
    setOwner,
    addSubscriber,
    removeSubscriber,
    assign,
    unassign,
    manifestRewrite,
    protect,
    run,
    schedule,
    scan,
    silentLoss,
    repairSilentLoss,
    handleRunAction,
    handleScanAction,
    row,
    sourceName,
    triggered,
    stats,
    verdict,
    statsRaw,
    lastSnapshotCommittedAt,
    hasStatsCostFallback,
    costView,
    costByOperation,
    currentCostUsd,
    projectedCostUsd,
    savingsUsd,
    showLatencyBenefit,
    savingsDisplay,
    reclaimableBytes,
    storageBytes,
    partitionColLabel,
    hideSinglePartitionKpi,
    complianceNotFound,
    score,
    scoreFactors,
    pointDeductions,
    trendData,
    degradationRate,
    degradationBadge,
    trendDirection,
    trendLabel,
    protectedFromAudit,
    effectiveDestination,
    assignPresetOptions,
    protectedItems,
    mergedColumns,
    qualityBreakdown,
    historyPoints,
    activeTab,
    handleTabChange,
  };
}

export type TableDetailState = ReturnType<typeof useTableDetail>;
