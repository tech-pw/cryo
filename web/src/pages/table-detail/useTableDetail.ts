import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
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
  const [protectedLocal, setProtectedLocal] = useState<Array<{ snapshot_id: number; label?: string | null }>>([]);
  const [ownerEmailInput, setOwnerEmailInput] = useState("");
  const [newSubscriberEmail, setNewSubscriberEmail] = useState("");

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
    queryKey: ["table-audit", tableId, token],
    queryFn: () => getTableAudit(api, tableId),
    retry: 1
  });
  const runs = useQuery({
    queryKey: ["table-runs", tableId, token],
    queryFn: () => getTableRuns(api, tableId),
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
    retry: false
  });
  const s3Tiering = useQuery({
    queryKey: ["table-s3-tiering", tableId, token],
    queryFn: () => getTableS3Tiering(api, tableId),
    retry: false
  });
  const partitionEvolution = useQuery({
    queryKey: ["table-partition-evolution", tableId, token],
    queryFn: () => getPartitionEvolutionRecommendation(api, tableId),
    retry: false
  });
  const fastPathFindings = useQuery({
    queryKey: ["fast-path-findings", token],
    queryFn: () => getFastPathFindings(api),
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
    retry: false
  });
  const partitions = useQuery({
    queryKey: ["table-partitions", tableId, token],
    queryFn: () => getTablePartitions(api, tableId),
    retry: false
  });
  const partitionAccess = useQuery({
    queryKey: ["table-partition-access", tableId, token],
    queryFn: () => getTablePartitionAccess(api, tableId),
    retry: false
  });
  const history = useQuery({
    queryKey: ["table-history", tableId, token],
    queryFn: () => getTableHistory(api, tableId) as Promise<HistoryPoint[]>,
    retry: false
  });
  const bloomRecommendations = useQuery({
    queryKey: ["table-bloom-recommendations", tableId, token],
    queryFn: () => getBloomFilterRecommendations(api, tableId),
    retry: false
  });
  const sources = useQuery({
    queryKey: ["sources", token],
    queryFn: () => listSources(api),
    retry: 1
  });

  const pii = useQuery({
    queryKey: ["table-pii", tableId, token],
    queryFn: () => getTablePiiClassification(api, tableId),
    retry: false
  });

  const dataQuality = useQuery({
    queryKey: ["table-dq", tableId, token],
    queryFn: () => getDataQualityProfile(api, tableId, 30),
    retry: false
  });

  const tableSla = useQuery({
    queryKey: ["table-sla", tableId, token],
    queryFn: () => getSlaStatusDashboard(api, false, tableId),
    retry: false
  });
  const slaStatus = tableSla.data?.slas?.[0] ?? null;

  const tableQueries = useQuery({
    queryKey: ["table-query-performance", tableId, token],
    queryFn: () => getQueryPerformanceDashboard(api, 168, undefined, tableId),
    retry: false
  });

  const alertSubscriptions = useQuery({
    queryKey: ["table-alert-subscriptions", tableId, token],
    queryFn: () => listTableAlertSubscriptions(api, tableId),
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
    mutationFn: (values: { snapshot_id: number; label?: string }) => protectSnapshot(api, tableId, values),
    onSuccess: (result) => {
      toast.success("Snapshot protected");
      setProtectedLocal((prev) => [{ snapshot_id: result.snapshot_id, label: result.label }, ...prev].slice(0, 8));
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
      toast.success(result.message || `Scan queued: ${result.run_id}`);
      setQueuedScanRunId(result.run_id);
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
    queryKey: ["silent-data-loss", tableId],
    queryFn: () => getSilentDataLoss(api, tableId),
    enabled: Boolean(tableId),
    refetchInterval: 60_000,
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
  const currentCostUsd = costView?.current_monthly_cost_usd ?? stats?.estimated_monthly_s3_cost_usd ?? 0;
  const projectedCostUsd =
    costView?.projected_monthly_cost_usd ??
    stats?.projected_monthly_s3_cost_usd ??
    stats?.estimated_monthly_s3_cost_usd ??
    0;
  const projectedSavingsRaw =
    costView?.projected_savings_usd_month ?? stats?.projected_savings_usd_month;
  const hasProjectedSavings = typeof projectedSavingsRaw === "number";
  const savingsUsd = projectedSavingsRaw ?? 0;
  const showLatencyBenefit =
    hasProjectedSavings &&
    savingsUsd === 0 &&
    (Boolean(stats?.needs_compaction) || triggered.length > 0);
  const savingsDisplay = !hasProjectedSavings
    ? "Not estimated"
    : showLatencyBenefit
      ? "Latency benefit"
      : formatCurrency(savingsUsd);
  const reclaimableBytes = costView?.reclaimable_bytes ?? stats?.reclaimable_bytes ?? 0;
  const storageBytes =
    (costView?.live_bytes ?? 0) +
    (costView?.dead_bytes ?? 0) +
    (costView?.metadata_bytes ?? 0) +
    (costView?.orphan_bytes ?? 0);
  const partitionColLabel = formatPartitionColumnLabel(stats?.partition_col);
  const hideSinglePartitionKpi =
    partitionColLabel === "Unpartitioned" && (stats?.total_partitions ?? 0) <= 1;
  const complianceNotFound = isNotFoundError(compliance.error);
  const score = stats?.overall_score ?? 0;
  const scoreFactors = useMemo(
    () => buildHealthScoreFactors(stats, costView?.orphan_bytes ?? 0, storageBytes),
    [stats, costView?.orphan_bytes, storageBytes]
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
    const items: Array<{ snapshot_id: number; label?: string | null; ts?: string | null }> = [];
    for (const entry of audit.data ?? []) {
      const action = (entry.action ?? "").toLowerCase();
      if (!action.includes("protect")) continue;
      const detail =
        typeof entry.detail === "string" ? entry.detail : entry.detail ? JSON.stringify(entry.detail) : "";
      const snapshotMatch = detail.match(/snapshot[_\s-]?id[=: ]+(\d+)/i) || detail.match(/(\d{4,})/);
      items.push({
        snapshot_id: snapshotMatch ? Number(snapshotMatch[1]) : 0,
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

  const protectedItems: Array<{ snapshot_id: number; label?: string | null; ts?: string | null }> = [
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

  const allowedTabKeys = new Set([
    "overview",
    "structure",
    "health-quality",
    "cost",
    "lineage",
    "activity"
  ]);
  const requestedTab = searchParams.get("tab");
  const activeTab = requestedTab && allowedTabKeys.has(requestedTab) ? requestedTab : "overview";

  const handleTabChange = (tabKey: string) => {
    const next = new URLSearchParams(searchParams);
    if (tabKey === "overview") {
      next.delete("tab");
    } else {
      next.set("tab", tabKey);
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
