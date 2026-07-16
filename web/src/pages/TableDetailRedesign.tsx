import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AppstoreOutlined,
  SyncOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DollarOutlined,
  HistoryOutlined,
  RobotOutlined,
  SafetyOutlined,
  ThunderboltOutlined,
  WarningOutlined,
  LockOutlined,
  TableOutlined,
  CheckCircleOutlined,
  HeartOutlined,
  CloseCircleOutlined,
  InfoCircleOutlined,
  ArrowRightOutlined,
  FileTextOutlined,
  BranchesOutlined,
  CloseOutlined,
  BellOutlined,
  PartitionOutlined,
  DeleteOutlined,
  SlidersOutlined,
  DownOutlined,
  RightOutlined,
  CopyOutlined
} from "@ant-design/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message
} from "antd";
import {
  Area,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis
} from "recharts";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  assignTables,
  getBloomFilterRecommendations,
  getComplianceStatus,
  getEffectivePreset,
  getPartitionEvolutionRecommendation,
  getTable,
  getTableAudit,
  getTableBenefitScores,
  getTableCost,
  getTableHealthTrends,
  getTableHistory,
  getTablePartitions,
  getTableS3Tiering,
  getTableSignals,
  getTableRoiBreakdown,
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
  getCompactionCandidates,
  getUnionable,
  getColumnStats,
  getSilentDataLoss,
  repairSilentDataLoss,
  patchTable,
  listTableAlertSubscriptions,
  createTableAlertSubscription,
  deleteTableAlertSubscription,
  getRun,
  getRunJobs
} from "../api/client";
import type { AuditEntry, CompactionCandidateRead, UnionScoreRead, ColumnStatsResponse, SilentDataLossReportRead, HistoryPoint } from "../api/types";
import { EmptyState, QueryErrorAlert } from "../components/common";
import { ConfirmAction } from "../components/confirm";
import { CostPrecisionBadge } from "../components/CostPrecisionBadge";
import { CostSavingsWaterfall } from "../components/CostSavingsWaterfall";
import { HealthScoreBreakdown, buildHealthScoreFactors } from "../components/HealthScoreBreakdown";
import { SavingsByOperation } from "../components/SavingsByOperation";
import { PlanPreview } from "../components/PlanPreview";
import { QueryState } from "../components/QueryState";
import { TableAiInsights } from "../components/TableAiInsights";
import { TableCompactionAnalytics } from "../components/TableCompactionAnalytics";
import { TableDiagnosisPanel, buildDiagnosisVerdict } from "../components/TableDiagnosisPanel";
import { ActNowSection } from "../components/table-detail/ActNowSection";
import { HealthGauge } from "../components/table-detail/HealthGauge";
import { KpiStripe } from "../components/table-detail/KpiStripe";
import { OverviewTab } from "../components/table-detail/OverviewTab";
import { PartitionHistogram } from "../components/table-detail/PartitionHistogram";
import { RecommendedActions } from "../components/table-detail/RecommendedActions";
import { SectionNav } from "../components/table-detail/SectionNav";
import { StorageDonut } from "../components/table-detail/StorageDonut";
import { CompactionCandidatesTab } from "../components/table-detail/CompactionCandidatesTab";
import { UnionableTab } from "../components/table-detail/UnionableTab";
import { ColumnStatsTab } from "../components/table-detail/ColumnStatsTab";
import { ActivityTab } from "../components/table-detail/ActivityTab";
import { CostTab, CostTabLabel } from "../components/table-detail/CostTab";
import { SchemaTab, SchemaTabLabel, type SchemaColumn } from "../components/table-detail/SchemaTab";
import {
  CompactionTag,
  GradePill,
  PendingDeletionTag,
  PriorityTag,
  healthScoreColor
} from "../components/status";
import { useAuth } from "../lib/auth";
import { isOrphanScanStale } from "../lib/costStaleness";
import {
  formatBytesGb,
  formatCurrency,
  formatDuration,
  formatGb,
  formatNumber,
  formatSlaDays,
  formatSlaHours,
  formatTime
} from "../lib/format";
import { qualifiedTableName } from "../lib/tableLabels";
import { chartTooltipProps, cryoColors } from "../lib/theme";
import { formatEngineLabel, nativeGoDeleteFilesRewriteError, resolveNativeEngineImpl } from "../lib/engines";
import { formatPartitionColumnLabel, provenanceLabel } from "../components/table-detail/helpers";

const isNotFoundError = (error: unknown): boolean => {
  if (!error || typeof error !== "object") return false;
  const maybeHttpError = error as { status?: number; response?: { status?: number } };
  return maybeHttpError.status === 404 || maybeHttpError.response?.status === 404;
};

export const TableDetail = () => {
  const { tableId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { api, token, role } = useAuth();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
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
  const tableRoi = useQuery({
    queryKey: ["table-roi-breakdown", tableId, token],
    queryFn: () => getTableRoiBreakdown(api, tableId),
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
      message.success("Owner updated");
      void queryClient.invalidateQueries({ queryKey: ["table", tableId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const addSubscriber = useMutation({
    mutationFn: (email: string) => createTableAlertSubscription(api, tableId, email),
    onSuccess: () => {
      message.success("Subscribed to breach alerts");
      void queryClient.invalidateQueries({ queryKey: ["table-alert-subscriptions", tableId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const removeSubscriber = useMutation({
    mutationFn: (subscriptionId: string) => deleteTableAlertSubscription(api, tableId, subscriptionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["table-alert-subscriptions", tableId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const assign = useMutation({
    mutationFn: (presetId: string) =>
      assignTables(api, { selection: { mode: "ids", ids: [tableId] }, preset_id: presetId }),
    onSuccess: () => {
      message.success("Preset assigned");
      void effectivePreset.refetch();
    },
    onError: (error) => message.error((error as Error).message)
  });

  const unassign = useMutation({
    mutationFn: () => unassignTables(api, { selection: { mode: "ids", ids: [tableId] } }),
    onSuccess: () => {
      message.success("Preset unassigned");
      void effectivePreset.refetch();
    },
    onError: (error) => message.error((error as Error).message)
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
    onSuccess: (result) => message.success(`Manifest rewrite queued: ${result.run_id}`),
    onError: (error) => message.error((error as Error).message)
  });

  const protect = useMutation({
    mutationFn: (values: { snapshot_id: number; label?: string }) => protectSnapshot(api, tableId, values),
    onSuccess: (result) => {
      message.success("Snapshot protected");
      setProtectedLocal((prev) => [{ snapshot_id: result.snapshot_id, label: result.label }, ...prev].slice(0, 8));
    },
    onError: (error) => message.error((error as Error).message)
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
      const rewriteBlocked =
        destination?.engine === "native" &&
        resolveNativeEngineImpl(destination.config) === "go" &&
        deleteFiles > 0 &&
        (mode === "standard" || mode === "deep");
      if (rewriteBlocked) {
        message.error(`${nativeGoDeleteFilesRewriteError} Run queued: ${result.run_id}`);
      } else {
        message.success(`Run queued: ${result.run_id}`);
      }
      setQueuedMaintenanceRunId(result.run_id);
      setTrackedRunId(result.run_id);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["cost-dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error) => {
      setQueuedMaintenanceRunId(null);
      message.error((error as Error).message);
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
      const rewriteBlocked =
        destination?.engine === "native" &&
        resolveNativeEngineImpl(destination.config) === "go" &&
        deleteFiles > 0 &&
        (mode === "standard" || mode === "deep");
      if (rewriteBlocked) {
        message.error(`${nativeGoDeleteFilesRewriteError} Run scheduled: ${result.run_id}`);
      } else {
        message.success(`Run scheduled: ${result.run_id}`);
      }
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error) => message.error((error as Error).message)
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
      message.success(result.message || `Scan queued: ${result.run_id}`);
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
      message.error((error as Error).message);
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
      message.success(result.message || "Repair queued");
      void queryClient.invalidateQueries({ queryKey: ["silent-data-loss", tableId] });
    },
    onError: (error) => message.error((error as Error).message)
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
    return {
      nullSpikeCount: metrics.filter((c) => c.null_rate_spike).length,
      rangeShiftCount: metrics.filter((c) => c.value_range_changed).length,
      schemaChangeCount:
        (dataQuality.data?.new_columns.length ?? 0) + (dataQuality.data?.dropped_columns.length ?? 0)
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

  const tabItems = [
    {
      key: "overview",
      label: (
        <Space size={6}>
          <HeartOutlined />
          <span>Health & Diagnosis</span>
          {triggered.length > 0 ? (
            <Badge
              count={triggered.length}
              style={{ backgroundColor: "var(--error, #dc3545)" }}
              size="small"
            />
          ) : null}
        </Space>
      ),
      children: (
        <div className="td-tab-content">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <HealthGauge
              score={score}
              trendDirection={trendDirection}
              trendLabel={trendLabel}
              daysToCritical={healthTrends.data?.days_to_critical}
            />
            {row ? (
              <Alert
                type={alertType}
                showIcon
                title={<strong>{verdict.message}</strong>}
                style={{ fontSize: 12, flex: 1 }}
              />
            ) : null}
          </div>

          <ActNowSection
            signals={tableSignals.data}
            signalsLoading={tableSignals.isLoading}
            signalsError={tableSignals.error}
            costView={costView}
            benefitScores={benefitScores.data}
            stats={stats}
            statsRaw={statsRaw}
            reclaimableBytes={reclaimableBytes}
            savingsUsd={savingsUsd}
            presetOptions={assignPresetOptions}
            presetsLoading={presets.isLoading || compatiblePresets.isLoading}
            effectivePresetId={effectivePreset.data?.preset_id ?? undefined}
            onAssignPreset={(presetId) => assign.mutate(presetId)}
            assignPending={assign.isPending}
            onRun={() => run.mutate()}
            runPending={run.isPending}
            queuedMaintenanceRunId={queuedMaintenanceRunId}
            lastRunSkipNotes={lastRunSkipNotes}
            onOpenRun={handleRunAction}
            onPreview={() => setPreviewOpen(true)}
            onSchedule={(scheduledAtIso) => schedule.mutate(scheduledAtIso)}
            schedulePending={schedule.isPending}
            tableLabel={row ? `${row.schema_name}.${row.table_name}` : "this table"}
          />

          {tableRoi.data ? (
            <Card size="small" style={{ marginTop: 12 }}>
              <Space size={12} wrap>
                <Tag
                  color={
                    tableRoi.data.decision.bcr >= tableRoi.data.decision.threshold
                      ? "green"
                      : "orange"
                  }
                >
                  ROI {tableRoi.data.decision.action.toUpperCase()}
                </Tag>
                <Typography.Text type="secondary">
                  BCR {tableRoi.data.decision.bcr.toFixed(2)} /{" "}
                  {tableRoi.data.decision.threshold.toFixed(2)}
                </Typography.Text>
                <Typography.Text type="secondary">
                  Benefit {formatCurrency(tableRoi.data.monthly_benefit_usd)} vs cost{" "}
                  {formatCurrency(tableRoi.data.monthly_cost_usd)}
                </Typography.Text>
              </Space>
            </Card>
          ) : null}

          <div style={{ marginBottom: 16 }} />

          <div>
            <div
              className="td-diagnostics-toggle"
              role="button"
              tabIndex={0}
              onClick={() => setDiagnosticsOpen((v) => !v)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setDiagnosticsOpen((v) => !v); }}
            >
              {diagnosticsOpen ? <DownOutlined /> : <RightOutlined />}
              <span className="td-chart-card-title">Diagnostics</span>
              <span className="td-chart-card-subtitle">Health trend, partitioning, query load, score breakdown</span>
            </div>
            {!diagnosticsOpen ? null : (
          <Row gutter={[16, 16]} style={{ marginTop: 12 }}>
            <Col xs={24} xl={14}>
              <Card className={`td-chart-card${trendData.length ? "" : " td-chart-card-quiet"}`} classNames={{ body: "td-chart-card-body" }} style={{ marginBottom: 16 }}>
                <div className="td-chart-card-header">
                  <span className="td-chart-card-title">Health Trends</span>
                  <span className="td-chart-card-subtitle">30 days</span>
                  {degradationBadge ? <Tag color="error">{degradationBadge}</Tag> : null}
                </div>
                <div className="td-chart-card-content">
                  {trendData.length ? (
                    <div className="chart-frame" style={{ height: 260 }}>
                      <ResponsiveContainer width="100%" height={260}>
                        <LineChart data={trendData}>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
                          <XAxis
                            dataKey="timestamp"
                            tickFormatter={(ts) =>
                              new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" })
                            }
                            tick={{ fontSize: 11 }}
                            interval="preserveStartEnd"
                            stroke="var(--muted, #5c667a)"
                          />
                          <YAxis yAxisId="left" domain={[0, 100]} tick={{ fontSize: 11 }} stroke="var(--muted, #5c667a)" tickLine={false} />
                          <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} stroke="var(--muted, #5c667a)" tickLine={false} />
                          <RechartsTooltip
                            {...chartTooltipProps}
                            labelFormatter={(ts) => new Date(ts).toLocaleString()}
                            formatter={(value, name) => {
                              const n = Number(value ?? 0);
                              const label = String(name);
                              if (label === "score") return [n.toFixed(1), "Health Score"];
                              if (label === "small_files") return [n.toLocaleString(), "Small Files"];
                              if (label === "total_size_gb") return [n.toFixed(1) + " GB", "Size"];
                              return [n, label];
                            }}
                          />
                          <defs>
                            <linearGradient id="scoreGrad2" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="0%" stopColor={cryoColors.gradeA} stopOpacity={0.15} />
                              <stop offset="100%" stopColor={cryoColors.gradeA} stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <Area yAxisId="left" type="monotone" dataKey="score" fill="url(#scoreGrad2)" stroke="none" tooltipType="none" />
                          <Line yAxisId="left" type="monotone" dataKey="score" stroke={cryoColors.gradeA} strokeWidth={2.5} dot={false} name="score" />
                          <Line yAxisId="right" type="monotone" dataKey="small_files" stroke={cryoColors.chartPrimary} strokeWidth={2} dot={false} name="small_files" strokeDasharray="4 3" />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <EmptyState title="No trends yet" description="Health trends appear after multiple scans over time." />
                  )}
                </div>
              </Card>

              {/* Partition Columns Analyzer */}
              <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }} style={{ margin: 0 }}>
                {(() => {
                  const getColsFromSpec = (spec: string | undefined): string[] => {
                    if (!spec) return [];
                    if (spec.toLowerCase() === "unpartitioned") return [];
                    const parts = spec.split(",").map(p => p.trim());
                    return parts.map(part => {
                      const match = part.match(/\((.*?)\)/);
                      return match ? match[1] : part;
                    });
                  };

                  const getGranularitiesFromSpec = (spec: string | undefined): string[] => {
                    if (!spec) return [];
                    if (spec.toLowerCase() === "unpartitioned") return [];
                    const parts = spec.split(",").map(p => p.trim());
                    return parts.map(part => {
                      const match = part.match(/^(.*?)\(/);
                      return match ? match[1] : "identity";
                    });
                  };

                  const checkPartitionColumn = () => {
                    const currentCol = stats?.partition_col;
                    const hasRec = !!partitionEvolution.data;
                    const currentSpec = partitionEvolution.data?.current_partition_spec || (currentCol ? `identity(${currentCol})` : "unpartitioned");
                    const recommendedSpec = partitionEvolution.data?.recommended_partition_spec;

                    if (!currentCol || currentCol === "unpartitioned" || currentSpec.toLowerCase() === "unpartitioned") {
                      if (!hasRec || !recommendedSpec || recommendedSpec.toLowerCase() === "unpartitioned") {
                        return {
                          status: "optimal" as const,
                          tagText: "Optimal (Unpartitioned)",
                          tagColor: "success",
                          what: "Table is optimally unpartitioned",
                          why: "Table scale or range filters do not warrant partitioning overhead. Keeping the table unpartitioned avoids planning overhead and file fragmentation.",
                          current: "Unpartitioned",
                          ideal: "Unpartitioned"
                        };
                      } else {
                        return {
                          status: "suboptimal" as const,
                          tagText: "Partition Recommended",
                          tagColor: "warning",
                          what: "Table is unpartitioned but needs partition keys",
                          why: `High-volume queries frequently filter on columns aligned with "${recommendedSpec}". Partitioning on these columns would reduce plan-time overhead and accelerate searches.`,
                          current: "Unpartitioned",
                          ideal: recommendedSpec
                        };
                      }
                    }

                    if (!hasRec || !recommendedSpec) {
                      return {
                        status: "optimal" as const,
                        tagText: "Aligned Column",
                        tagColor: "success",
                        what: `Partition column "${currentCol}" is optimal`,
                        why: "Queries targeting this table align with the existing partition key, achieving successful pruning boundaries. No column changes are suggested.",
                        current: currentSpec,
                        ideal: currentSpec
                      };
                    }

                    const normCurrent = currentSpec.toLowerCase().replace(/\s+/g, "");
                    const normRec = recommendedSpec.toLowerCase().replace(/\s+/g, "");

                    if (normCurrent === normRec) {
                      return {
                        status: "optimal" as const,
                        tagText: "Optimal (Aligned)",
                        tagColor: "success",
                        what: `Partition column "${currentCol}" is optimal`,
                        why: `Historical access patterns successfully utilize "${currentCol}" as the pruning index. No partition column evolution required.`,
                        current: currentSpec,
                        ideal: recommendedSpec
                      };
                    }

                    const currentCols = getColsFromSpec(currentSpec).map(c => c.toLowerCase());
                    const recCols = getColsFromSpec(recommendedSpec).map(c => c.toLowerCase());
                    const missingCols = recCols.filter(c => !currentCols.includes(c));

                    if (missingCols.length > 0) {
                      return {
                        status: "suboptimal" as const,
                        tagText: "Evolution Suggested",
                        tagColor: "warning",
                        what: "Additional partition keys recommended",
                        why: `Queries frequently filter on "${missingCols.join(", ")}" alongside your current partition key. Evolving the partition spec to "${recommendedSpec}" will reduce raw bytes scanned.`,
                        current: currentSpec,
                        ideal: recommendedSpec
                      };
                    }

                    return {
                      status: "suboptimal" as const,
                      tagText: "Column Mismatch",
                      tagColor: "error",
                      what: "Suboptimal partition column layout",
                      why: `Access patterns show query filters target "${recommendedSpec}" instead of current key "${currentSpec}". This layout forces the execution engine to perform costly full table scans.`,
                      current: currentSpec,
                      ideal: recommendedSpec
                    };
                  };

                  const checkPartitionGranularity = () => {
                    const currentCol = stats?.partition_col;
                    const hasRec = !!partitionEvolution.data;
                    const currentSpec = partitionEvolution.data?.current_partition_spec || (currentCol ? `identity(${currentCol})` : "unpartitioned");
                    const recommendedSpec = partitionEvolution.data?.recommended_partition_spec;

                    const totalPartitions = stats?.total_partitions ?? 0;
                    const avgFilesPerPartition = stats?.avg_files_per_partition ?? 0;

                    if (!currentCol || currentCol === "unpartitioned" || currentSpec.toLowerCase() === "unpartitioned") {
                      return {
                        status: "optimal" as const,
                        tagText: "Optimal (No Skew)",
                        tagColor: "success",
                        what: "No partition granularity overhead",
                        why: "Being unpartitioned prevents directory-level metadata fragmentation and limits metadata catalog checks.",
                        current: "None (Unpartitioned)",
                        ideal: "None"
                      };
                    }

                    if (totalPartitions > 10000) {
                      return {
                        status: "critical" as const,
                        tagText: "Granularity Too Fine",
                        tagColor: "error",
                        what: "Over-partitioning complexity triggered",
                        why: `The table contains ${totalPartitions.toLocaleString()} partitions, causing partition bloating. This high directory complexity degrades query compilation and catalog list performance.`,
                        current: `${totalPartitions.toLocaleString()} partitions (${stats?.partition_col_type || 'identity'})`,
                        ideal: "< 5,000 partitions (e.g. Month transform)"
                      };
                    }

                    if (avgFilesPerPartition > 0 && avgFilesPerPartition < 2 && totalPartitions > 500) {
                      return {
                        status: "suboptimal" as const,
                        tagText: "Suboptimal (Too Granular)",
                        tagColor: "warning",
                        what: "Oversized partition catalog metadata",
                        why: `Average files per partition is too low (${avgFilesPerPartition.toFixed(1)}). The current transform creates too many sparse directories. Consolidate to daily or monthly.`,
                        current: `${avgFilesPerPartition.toFixed(1)} files/partition`,
                        ideal: "10 to 100 files/partition"
                      };
                    }

                    if (hasRec && recommendedSpec) {
                      const currentTrans = getGranularitiesFromSpec(currentSpec).join(", ");
                      const recTrans = getGranularitiesFromSpec(recommendedSpec).join(", ");

                      if (currentTrans.toLowerCase() !== recTrans.toLowerCase()) {
                        const isFiner = (currentTrans.includes("hour") && !recTrans.includes("hour")) ||
                                        (currentTrans.includes("day") && (recTrans.includes("month") || recTrans.includes("year"))) ||
                                        (currentTrans.includes("month") && recTrans.includes("year"));

                        return {
                          status: "suboptimal" as const,
                          tagText: isFiner ? "Suboptimal (Too Fine)" : "Suboptimal (Too Coarse)",
                          tagColor: "warning",
                          what: isFiner ? "Partition transform is too granular" : "Partition transform is too coarse",
                          why: isFiner
                            ? `Current transform "${currentTrans}" creates sparse directories. Evolving to "${recTrans}" will combine small write commits and speed up snapshot metadata planning.`
                            : `Current transform "${currentTrans}" is too broad. Query engines must scan large ranges. Evolving to "${recTrans}" restricts bounds and speeds up execution.`,
                          current: currentTrans || "identity",
                          ideal: recTrans || "identity"
                        };
                      }
                    }

                    return {
                      status: "optimal" as const,
                      tagText: "Optimal Granularity",
                      tagColor: "success",
                      what: "Partition granularity is correct",
                      why: `The average file count (${avgFilesPerPartition.toFixed(1)} files/partition) and partition count (${totalPartitions}) represent a balanced data-to-metadata distribution.`,
                      current: stats?.partition_col_type ? `${stats.partition_col_type} granularity` : "Balanced",
                      ideal: stats?.partition_col_type ? `${stats.partition_col_type} granularity` : "Balanced"
                    };
                  };

                  const colCheck = checkPartitionColumn();
                  const granCheck = checkPartitionGranularity();

                  return (
                    <>
                      <div className="td-chart-card-header" style={{ marginBottom: 16 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <BranchesOutlined style={{ color: "var(--primary, #3d6fd4)", fontSize: 16 }} />
                          <span className="td-chart-card-title">Partition Columns Analyzer</span>
                        </div>
                        <span className="td-chart-card-subtitle">
                          Analyzing schema keys, access patterns, and pruning granularity
                        </span>
                      </div>

                      <div className="td-chart-card-content" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                        {/* Summary Stats Grid */}
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 12, padding: "12px 16px", borderRadius: 8, backgroundColor: "rgba(92, 102, 122, 0.03)", border: "1px solid var(--line, #e1e6ed)" }}>
                          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                            <span style={{ fontSize: 9, fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Active Partition Column</span>
                            <strong style={{ fontSize: 12, color: "var(--foreground, #1a202c)" }}>{stats?.partition_col ?? "None"}</strong>
                          </div>
                          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                            <span style={{ fontSize: 9, fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Total Partitions</span>
                            <strong style={{ fontSize: 12, color: "var(--foreground, #1a202c)" }}>{(stats?.total_partitions ?? 0).toLocaleString()}</strong>
                          </div>
                          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                            <span style={{ fontSize: 9, fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Avg Files / Partition</span>
                            <strong style={{ fontSize: 12, color: "var(--foreground, #1a202c)" }}>{(stats?.avg_files_per_partition ?? 0).toFixed(1)}</strong>
                          </div>
                          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                            <span style={{ fontSize: 9, fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Volume Skew Ratio</span>
                            <strong style={{ fontSize: 12, color: "var(--foreground, #1a202c)" }}>{(stats?.partition_skew_ratio ?? 0).toFixed(1)}x</strong>
                          </div>
                        </div>

                        {/* Checklist row 1: Target Column */}
                        <div style={{
                          border: "1px solid var(--line, #e1e6ed)",
                          borderLeft: `4px solid ${colCheck.status === "optimal" ? "var(--success, #28a745)" : "var(--warning, #f0a020)"}`,
                          backgroundColor: "var(--background, #ffffff)",
                          padding: "16px 20px",
                          borderRadius: "8px",
                          display: "flex",
                          flexDirection: "column",
                          gap: 14,
                          boxShadow: "0 2px 8px rgba(0,0,0,0.02)",
                          transition: "all 0.2s ease-in-out"
                        }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                              <div style={{
                                width: 24,
                                height: 24,
                                borderRadius: "50%",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                backgroundColor: colCheck.status === "optimal" ? "rgba(40, 167, 69, 0.08)" : "rgba(240, 160, 32, 0.08)"
                              }}>
                                {colCheck.status === "optimal" ? (
                                  <CheckCircleOutlined style={{ color: "var(--success, #28a745)", fontSize: 13 }} />
                                ) : (
                                  <WarningOutlined style={{ color: "var(--warning, #f0a020)", fontSize: 13 }} />
                                )}
                              </div>
                              <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 600, fontSize: 13 }}>Check 1: Target Column Alignment</span>
                            </div>
                            <Tag color={colCheck.tagColor} style={{ margin: 0, fontSize: 10, fontWeight: 700, borderRadius: 4, padding: "2px 8px" }}>
                              {colCheck.tagText}
                            </Tag>
                          </div>
                          
                          <div style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: 12,
                            padding: "14px 16px",
                            borderRadius: 6,
                            backgroundColor: "rgba(92, 102, 122, 0.02)",
                            border: "1px solid rgba(92, 102, 122, 0.06)",
                            fontSize: 11
                          }}>
                            {/* Metric Name & Impact Row */}
                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
                              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                                <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Metric Name</span>
                                <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 600, fontSize: 11.5 }}>{colCheck.what}</span>
                              </div>
                              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                                <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Impact Description</span>
                                <span style={{ color: "var(--muted, #5c667a)", fontSize: 11, lineHeight: "15px" }}>{colCheck.why}</span>
                              </div>
                            </div>

                            {/* State Transition Strip -- only shows an arrow when there's an actual change to make;
                                an "optimal" check has current === ideal by construction, and an arrow from a
                                value to itself just says "change nothing", which reads as a pending diff it isn't. */}
                            <div style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 16,
                              borderTop: "1px solid var(--line, #e1e6ed)",
                              paddingTop: 12,
                              marginTop: 4
                            }}>
                              {colCheck.current === colCheck.ideal ? (
                                <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                                  <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Current (already optimal)</span>
                                  <div><Tag color="success" style={{ margin: 0, padding: "3px 8px", fontWeight: 550 }}>{colCheck.current}</Tag></div>
                                </div>
                              ) : (
                                <>
                                  <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                                    <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Current State</span>
                                    <div><Tag style={{ margin: 0, padding: "3px 8px", fontWeight: 550 }}>{colCheck.current}</Tag></div>
                                  </div>

                                  <div style={{ display: "flex", alignItems: "center", justifyContent: "center", alignSelf: "flex-end", height: 26, paddingBottom: 2 }}>
                                    <ArrowRightOutlined style={{ color: "var(--muted, #5c667a)", opacity: 0.5, fontSize: 12 }} />
                                  </div>

                                  <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                                    <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Ideal Target</span>
                                    <div><Tag color="blue" style={{ margin: 0, padding: "3px 8px", fontWeight: 550 }}>{colCheck.ideal}</Tag></div>
                                  </div>
                                </>
                              )}
                            </div>
                          </div>
                        </div>

                        {/* Checklist row 2: Granularity */}
                        <div style={{
                          border: "1px solid var(--line, #e1e6ed)",
                          borderLeft: `4px solid ${granCheck.status === "optimal" ? "var(--success, #28a745)" : granCheck.status === "critical" ? "var(--error, #dc3545)" : "var(--warning, #f0a020)"}`,
                          backgroundColor: "var(--background, #ffffff)",
                          padding: "16px 20px",
                          borderRadius: "8px",
                          display: "flex",
                          flexDirection: "column",
                          gap: 14,
                          boxShadow: "0 2px 8px rgba(0,0,0,0.02)",
                          transition: "all 0.2s ease-in-out"
                        }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                              <div style={{
                                width: 24,
                                height: 24,
                                borderRadius: "50%",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                backgroundColor: granCheck.status === "optimal" ? "rgba(40, 167, 69, 0.08)" : granCheck.status === "critical" ? "rgba(220, 53, 69, 0.08)" : "rgba(240, 160, 32, 0.08)"
                              }}>
                                {granCheck.status === "optimal" ? (
                                  <CheckCircleOutlined style={{ color: "var(--success, #28a745)", fontSize: 13 }} />
                                ) : granCheck.status === "critical" ? (
                                  <CloseCircleOutlined style={{ color: "var(--error, #dc3545)", fontSize: 13 }} />
                                ) : (
                                  <WarningOutlined style={{ color: "var(--warning, #f0a020)", fontSize: 13 }} />
                                )}
                              </div>
                              <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 600, fontSize: 13 }}>Check 2: Partition Granularity Validation</span>
                            </div>
                            <Tag color={granCheck.tagColor} style={{ margin: 0, fontSize: 10, fontWeight: 700, borderRadius: 4, padding: "2px 8px" }}>
                              {granCheck.tagText}
                            </Tag>
                          </div>
                          
                          <div style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: 12,
                            padding: "14px 16px",
                            borderRadius: 6,
                            backgroundColor: "rgba(92, 102, 122, 0.02)",
                            border: "1px solid rgba(92, 102, 122, 0.06)",
                            fontSize: 11
                          }}>
                            {/* Metric Name & Impact Row */}
                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
                              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                                <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Metric Name</span>
                                <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 600, fontSize: 11.5 }}>{granCheck.what}</span>
                              </div>
                              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                                <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Impact Description</span>
                                <span style={{ color: "var(--muted, #5c667a)", fontSize: 11, lineHeight: "15px" }}>{granCheck.why}</span>
                              </div>
                            </div>

                            {/* State Transition Strip -- only shows an arrow when there's an actual change to make. */}
                            <div style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 16,
                              borderTop: "1px solid var(--line, #e1e6ed)",
                              paddingTop: 12,
                              marginTop: 4
                            }}>
                              {granCheck.current === granCheck.ideal ? (
                                <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                                  <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Current (already optimal)</span>
                                  <div><Tag color="success" style={{ margin: 0, padding: "3px 8px", fontWeight: 550 }}>{granCheck.current}</Tag></div>
                                </div>
                              ) : (
                                <>
                                  <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                                    <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Current State</span>
                                    <div><Tag style={{ margin: 0, padding: "3px 8px", fontWeight: 550 }}>{granCheck.current}</Tag></div>
                                  </div>

                                  <div style={{ display: "flex", alignItems: "center", justifyContent: "center", alignSelf: "flex-end", height: 26, paddingBottom: 2 }}>
                                    <ArrowRightOutlined style={{ color: "var(--muted, #5c667a)", opacity: 0.5, fontSize: 12 }} />
                                  </div>

                                  <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                                    <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Ideal Target</span>
                                    <div><Tag color="blue" style={{ margin: 0, padding: "3px 8px", fontWeight: 550 }}>{granCheck.ideal}</Tag></div>
                                  </div>
                                </>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    </>
                  );
                })()}
              </Card>

              {/* Query Load */}
              <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }} style={{ marginTop: 16 }}>
                <div className="td-chart-card-header">
                  <span className="td-chart-card-title">Query load</span>
                  <span className="td-chart-card-subtitle">Last 7 days</span>
                  {tableQueries.data && tableQueries.data.failed_count > 0 ? (
                    <Tag color="error" className="td-quality-anomaly-tag">
                      {tableQueries.data.failed_count} failed
                    </Tag>
                  ) : null}
                </div>
                <div className="td-chart-card-content">
                  {tableQueries.isLoading ? (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>Loading query activity…</Typography.Text>
                  ) : tableQueries.data && tableQueries.data.total > 0 ? (
                    <>
                      <div className="td-query-load-stats">
                        <div className="td-query-load-stat">
                          <span className="td-query-load-stat-label">Queries</span>
                          <span className="td-query-load-stat-value">{formatNumber(tableQueries.data.total)}</span>
                        </div>
                        <div className="td-query-load-stat">
                          <span className="td-query-load-stat-label">P50 latency</span>
                          <span className="td-query-load-stat-value">{formatDuration(tableQueries.data.p50_latency_ms)}</span>
                        </div>
                        <div className="td-query-load-stat">
                          <span className="td-query-load-stat-label">P95 latency</span>
                          <span className="td-query-load-stat-value">{formatDuration(tableQueries.data.p95_latency_ms)}</span>
                        </div>
                        <div className="td-query-load-stat">
                          <span className="td-query-load-stat-label">P99 latency</span>
                          <span className="td-query-load-stat-value">{formatDuration(tableQueries.data.p99_latency_ms)}</span>
                        </div>
                        <div className="td-query-load-stat">
                          <span className="td-query-load-stat-label">Avg bytes scanned</span>
                          <span className="td-query-load-stat-value">{formatBytesGb(tableQueries.data.avg_bytes_scanned)}</span>
                        </div>
                      </div>
                      <Table
                        size="small"
                        rowKey="query_id"
                        dataSource={tableQueries.data.queries.slice(0, 5)}
                        pagination={false}
                        className="td-schema-table"
                        style={{ marginTop: 12 }}
                        columns={[
                          { title: "Engine", dataIndex: "engine", width: 90 },
                          { title: "User", dataIndex: "user", ellipsis: true, render: (v) => v ?? "—" },
                          {
                            title: "Duration",
                            dataIndex: "duration_ms",
                            width: 110,
                            align: "right" as const,
                            render: (v) => formatDuration(v)
                          },
                          {
                            title: "Status",
                            dataIndex: "status",
                            width: 100,
                            render: (v: string) => (
                              <Tag color={v === "failed" ? "error" : "success"} className="td-schema-status-tag">
                                {v === "failed" ? "Failed" : "OK"}
                              </Tag>
                            )
                          },
                          {
                            title: "Started",
                            dataIndex: "started_at",
                            align: "right" as const,
                            render: (v) => formatTime(v)
                          }
                        ]}
                      />
                    </>
                  ) : (
                    <EmptyState
                      title="No query activity"
                      description="No queries against this table were recorded in the last 7 days."
                    />
                  )}
                </div>
              </Card>
            </Col>
            <Col xs={24} xl={10}>
              <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }} style={{ height: "100%", margin: 0 }}>
                <div className="td-chart-card-header" style={{ marginBottom: 16 }}>
                  <span className="td-chart-card-title">Score Factors</span>
                </div>
                {row ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                    <div className="td-sidebar-score-bars">
                      <HealthScoreBreakdown factors={scoreFactors} />
                    </div>
                    {pointDeductions.length > 0 ? (
                      <div>
                        <div className="td-diagnosis-section-title" style={{ fontWeight: 600, fontSize: 11, marginBottom: 8, color: "var(--muted, #5c667a)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Deduction Breakdown</div>
                        <div style={{
                          display: "flex",
                          flexDirection: "column",
                          gap: 6,
                          padding: '10px 12px',
                          borderRadius: 6,
                          backgroundColor: score < 60 ? 'rgba(220, 53, 69, 0.025)' : score < 80 ? 'rgba(240, 160, 32, 0.025)' : 'rgba(92, 102, 122, 0.015)',
                          border: score < 60 ? '1px solid rgba(220, 53, 69, 0.12)' : score < 80 ? '1px solid rgba(240, 160, 32, 0.15)' : '1px solid var(--line, #e1e6ed)'
                        }}>
                          {pointDeductions.map((item, idx) => (
                            <div key={idx} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, gap: 12 }}>
                              <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, flex: 1 }}>
                                <span style={{ color: item.severity === "critical" ? "var(--error, #dc3545)" : "var(--warning, #f0a020)", fontWeight: 700, flexShrink: 0 }}>•</span>
                                <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 600, flexShrink: 0 }}>{item.label}</span>
                                <span style={{ color: "var(--muted, #5c667a)", opacity: 0.8, fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.reason}>{item.reason}</span>
                              </div>
                              <span style={{ fontWeight: 700, color: item.severity === "critical" ? "var(--error, #dc3545)" : "var(--warning, #f0a020)", flexShrink: 0 }}>
                                -{item.deduction} pts
                              </span>
                            </div>
                          ))}
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: "1px dashed var(--line, #e1e6ed)", paddingTop: 6, marginTop: 4, fontWeight: 700, fontSize: 11 }}>
                            <span style={{ color: "var(--foreground, #1a202c)" }}>Total Deductions:</span>
                            <span style={{ color: "var(--error, #dc3545)" }}>
                              -{pointDeductions.reduce((acc, curr) => acc + curr.deduction, 0)} pts
                            </span>
                          </div>
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <EmptyState title="No health details" description="Fetch or scan table to load diagnostics." />
                )}
              </Card>
            </Col>
          </Row>
            )}
          </div>
        </div>
      ),
      forceRender: true
    },
    {
      key: "schema",
      label: <SchemaTabLabel />,
      children: (
        <SchemaTab
          columns={mergedColumns as SchemaColumn[]}
          loading={table.isLoading || pii.isLoading || dataQuality.isLoading}
        />
      ),
      forceRender: true
    },
    {
      key: "cost",
      label: <CostTabLabel />,
      children: (
        <CostTab
          costView={costView}
          stats={stats}
          storageBytes={storageBytes}
          hasStatsCostFallback={hasStatsCostFallback}
          currentCostUsd={currentCostUsd}
          projectedCostUsd={projectedCostUsd}
          savingsUsd={savingsUsd}
          costByOperation={costByOperation}
          s3Tiering={s3Tiering.data ?? null}
        />
      ),
      forceRender: true
    },
    {
      key: "ai",
      label: (
        <span>
          <RobotOutlined />
          AI & Analytics
        </span>
      ),
      children: (
        <div className="td-tab-content">
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={12}>
              <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
                <div className="td-chart-card-header">
                  <span className="td-chart-card-title">Compaction analytics</span>
                </div>
                <div className="td-chart-card-content">
                  {row ? <TableCompactionAnalytics tableId={tableId} embedded /> : null}
                </div>
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
                <div className="td-chart-card-header">
                  <span className="td-chart-card-title">AI observability</span>
                </div>
                <div className="td-chart-card-content">
                  {row ? (
                    <TableAiInsights
                      tableId={tableId}
                      lastSnapshotCommittedAt={lastSnapshotCommittedAt}
                      embedded
                      visibleTabs={["pii", "similar", "risk", "anomalies", "autocomp"]}
                    />
                  ) : null}
                </div>
              </Card>
            </Col>
          </Row>
        </div>
      ),
      forceRender: true
    },
    {
      key: "quality",
      label: (
        <span>
          <SafetyOutlined />
          Data Quality
        </span>
      ),
      children: (
        <div className="td-tab-content">
          <Row gutter={[16, 16]} align="stretch">
            <Col xs={24} xl={16}>
              <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }} style={{ height: "100%", margin: 0 }}>
                <div className="td-chart-card-header" style={{ justifyContent: "space-between" }}>
                  <div>
                    <span className="td-chart-card-title">Column Quality Metrics</span>
                    <span className="td-chart-card-subtitle" style={{ marginLeft: 8 }}>
                      {dataQuality.data?.profiled_at ? `Profiled ${formatTime(dataQuality.data.profiled_at)}` : "Baseline metrics"}
                    </span>
                  </div>
                  {dataQuality.data?.anomalies_detected !== undefined ? (
                    <Tag
                      color={dataQuality.data.anomalies_detected > 0 ? "error" : "success"}
                      className="td-quality-anomaly-tag"
                    >
                      {dataQuality.data.anomalies_detected} {dataQuality.data.anomalies_detected === 1 ? "Anomaly" : "Anomalies"}
                    </Tag>
                  ) : null}
                </div>
                <div className="td-chart-card-content">
                  <Table
                    size="small"
                    rowKey="name"
                    dataSource={mergedColumns}
                    loading={dataQuality.isLoading || table.isLoading}
                    pagination={{ pageSize: 8, size: "small" }}
                    className="td-schema-table"
                    scroll={{ x: 820 }}
                    columns={[
                      {
                        title: "Column",
                        dataIndex: "name",
                        width: 170,
                        ellipsis: true,
                        render: (name) => <span className="td-schema-col-name">{name}</span>
                      },
                      {
                        title: "Current Null %",
                        dataIndex: "nullRate",
                        width: 150,
                        align: "right" as const,
                        sorter: (a, b) => (a.nullRate ?? 0) - (b.nullRate ?? 0),
                        render: (nullRate, item) => {
                          const rate = nullRate != null ? nullRate * 100 : 0;
                          return (
                            <Space size={4}>
                              <span className="td-quality-cell-strong">{rate.toFixed(1)}%</span>
                              {item.dqNullSpike ? (
                                <Tag color="error" className="td-schema-badge-spike">
                                  Spike
                                </Tag>
                              ) : null}
                            </Space>
                          );
                        }
                      },
                      {
                        title: "Baseline Null %",
                        dataIndex: "dqNullBaseline",
                        width: 150,
                        align: "right" as const,
                        sorter: (a, b) => (a.dqNullBaseline ?? 0) - (b.dqNullBaseline ?? 0),
                        render: (v) => (v != null ? `${v.toFixed(1)}%` : "—")
                      },
                      {
                        title: "Value Range Matrix",
                        dataIndex: "lowerBound",
                        width: 220,
                        render: (_, item) => {
                          const hasBounds =
                            item.lowerBound !== undefined &&
                            item.lowerBound !== null &&
                            item.lowerBound !== "" &&
                            item.upperBound !== undefined &&
                            item.upperBound !== null &&
                            item.upperBound !== "";
                          if (!hasBounds) return <span className="td-bounds-empty">—</span>;
                          return (
                            <div className="td-schema-bounds">
                              <span className="td-bounds-text" title={`${item.lowerBound} to ${item.upperBound}`}>
                                {String(item.lowerBound)} <span className="td-bounds-sep">to</span> {String(item.upperBound)}
                              </span>
                              {item.dqValueRangeChanged ? (
                                <Tag
                                  color="warning"
                                  className="td-quality-shift-tag"
                                  title={item.dqValueRangeExplanation || "Value range shifted"}
                                >
                                  Shifted
                                </Tag>
                              ) : null}
                            </div>
                          );
                        }
                      },
                      {
                        title: "Status",
                        key: "status",
                        width: 100,
                        align: "center" as const,
                        render: (_, item) => {
                          const hasIssues = item.dqNullSpike || item.dqValueRangeChanged;
                          return (
                            <Tag color={hasIssues ? "error" : "success"} className="td-schema-status-tag">
                              {hasIssues ? "Failed" : "Passed"}
                            </Tag>
                          );
                        }
                      }
                    ]}
                    locale={{
                      emptyText: (
                        <EmptyState
                          title="No column metadata yet"
                          description="Ensure this table has successfully run its initial metadata scan."
                        />
                      )
                    }}
                  />
                </div>
              </Card>
            </Col>
            <Col xs={24} xl={8}>
              <div className="td-quality-side-col">
              <Card
                className="td-chart-card"
                title="Freshness & SLA"
                classNames={{ body: "td-chart-card-body" }}
                style={{ margin: 0 }}
              >
                {tableSla.isLoading ? (
                  <div className="td-quality-checks-title">Loading…</div>
                ) : slaStatus ? (
                  <div className="td-quality-side">
                    <div className="td-quality-checks">
                      <div className="td-quality-check-row">
                        <span className="td-quality-check-label">Freshness (since last commit)</span>
                        <span
                          className={
                            slaStatus.freshness_breached
                              ? "td-quality-check-value-bad"
                              : "td-quality-check-value-ok"
                          }
                        >
                          {formatSlaHours(slaStatus.freshness_hours)} / {formatSlaHours(slaStatus.freshness_sla_hours)}
                        </span>
                      </div>
                      <div className="td-quality-check-row">
                        <span className="td-quality-check-label">Scan staleness</span>
                        <span className="td-quality-check-value-ok">
                          {formatSlaHours(slaStatus.scan_staleness_hours)}
                        </span>
                      </div>
                      <div className="td-quality-check-row">
                        <span className="td-quality-check-label">Maintenance recency</span>
                        <span
                          className={
                            slaStatus.maintenance_breached
                              ? "td-quality-check-value-bad"
                              : "td-quality-check-value-ok"
                          }
                        >
                          {formatSlaDays(slaStatus.maintenance_days)} / {formatSlaDays(slaStatus.maintenance_sla_days)}
                        </span>
                      </div>
                      {slaStatus.p95_latency_ms !== null ? (
                        <div className="td-quality-check-row">
                          <span className="td-quality-check-label">P95 query latency</span>
                          <span
                            className={
                              slaStatus.p95_latency_breached
                                ? "td-quality-check-value-bad"
                                : "td-quality-check-value-ok"
                            }
                          >
                            {formatDuration(slaStatus.p95_latency_ms)} / {formatDuration(slaStatus.p95_latency_sla_ms)}
                          </span>
                        </div>
                      ) : null}
                    </div>
                    {slaStatus.freshness_breached || slaStatus.maintenance_breached || slaStatus.p95_latency_breached ? (
                      <Alert
                        type="error"
                        showIcon
                        className="td-quality-alert"
                        title={<strong>SLA Breached</strong>}
                        description="One or more SLA thresholds are currently exceeded for this table."
                      />
                    ) : (
                      <Alert
                        type="success"
                        showIcon
                        className="td-quality-alert"
                        title={<strong>Within SLA</strong>}
                        description="Freshness, maintenance, and query latency are all within configured thresholds."
                      />
                    )}
                  </div>
                ) : (
                  <EmptyState
                    title="No SLA data"
                    description="This table isn't monitored for SLA thresholds yet."
                  />
                )}
              </Card>
              <Card
                className="td-chart-card"
                title="Data Drift & Alerts"
                classNames={{ body: "td-chart-card-body" }}
                style={{ margin: 0 }}
              >
                {dataQuality.data ? (
                  <div className="td-quality-side">
                    {qualityBreakdown.schemaChangeCount > 0 ? (
                      <Alert
                        type="warning"
                        showIcon
                        className="td-quality-alert"
                        title={<strong>Schema Drift Detected</strong>}
                        description={
                          <Space orientation="vertical" size={2}>
                            {dataQuality.data.new_columns.length ? (
                              <div>
                                <strong className="td-quality-alert-added">New columns:</strong>{" "}
                                {dataQuality.data.new_columns.map((c, idx) => (
                                  <Tag color="green" key={`new-${idx}-${c ?? ""}`}>{c}</Tag>
                                ))}
                              </div>
                            ) : null}
                            {dataQuality.data.dropped_columns.length ? (
                              <div>
                                <strong className="td-quality-alert-removed">Dropped columns:</strong>{" "}
                                {dataQuality.data.dropped_columns.map((c, idx) => (
                                  <Tag color="red" key={`dropped-${idx}-${c ?? ""}`}>{c}</Tag>
                                ))}
                              </div>
                            ) : null}
                          </Space>
                        }
                      />
                    ) : (
                      <Alert
                        type="success"
                        showIcon
                        className="td-quality-alert"
                        title={<strong>No Schema Drift</strong>}
                        description="Schema matches the verified baseline configuration."
                      />
                    )}
                    <div className="td-quality-checks">
                      <Typography.Text className="td-quality-checks-title">Quality Checks Summary</Typography.Text>
                      <div className="td-quality-check-row">
                        <span className="td-quality-check-label">Null rate checks</span>
                        <span
                          className={
                            qualityBreakdown.nullSpikeCount > 0
                              ? "td-quality-check-value-bad"
                              : "td-quality-check-value-ok"
                          }
                        >
                          {qualityBreakdown.nullSpikeCount > 0
                            ? `${qualityBreakdown.nullSpikeCount} failing`
                            : "Passed"}
                        </span>
                      </div>
                      <div className="td-quality-check-row">
                        <span className="td-quality-check-label">Value range checks</span>
                        <span
                          className={
                            qualityBreakdown.rangeShiftCount > 0
                              ? "td-quality-check-value-warn"
                              : "td-quality-check-value-ok"
                          }
                        >
                          {qualityBreakdown.rangeShiftCount > 0
                            ? `${qualityBreakdown.rangeShiftCount} shifted`
                            : "Passed"}
                        </span>
                      </div>
                      <div className="td-quality-check-row">
                        <span className="td-quality-check-label">Schema changes</span>
                        <span
                          className={
                            qualityBreakdown.schemaChangeCount > 0
                              ? "td-quality-check-value-warn"
                              : "td-quality-check-value-ok"
                          }
                        >
                          {qualityBreakdown.schemaChangeCount > 0
                            ? `${qualityBreakdown.schemaChangeCount} changed`
                            : "None"}
                        </span>
                      </div>
                      <div className="td-quality-check-row">
                        <span className="td-quality-check-label">Total tracked columns</span>
                        <span className="td-quality-check-value-ok">{dataQuality.data.column_metrics.length}</span>
                      </div>
                    </div>
                    <div>
                      <Typography.Text className="td-quality-baseline-title">Statistical Baselines</Typography.Text>
                      <Typography.Text className="td-quality-baseline-note">
                        Historical baselines are dynamically updated every 30 days based on query execution history
                        and background scan snapshots.
                      </Typography.Text>
                    </div>
                  </div>
                ) : (
                  <EmptyState
                    title="No quality insights"
                    description="Fetch or scan the table to load drift and quality metrics."
                  />
                )}
              </Card>
              </div>
            </Col>
          </Row>
        </div>
      ),
      forceRender: true
    },
    {
      key: "activity",
      label: (
        <span>
          <HistoryOutlined />
          Activity Feed
        </span>
      ),
      children: <ActivityTab entries={audit.data} />,
      forceRender: true
    },
    {
      key: "candidates",
      label: (
        <Space size={6}>
          <BranchesOutlined />
          <span>Compaction Candidates</span>
        </Space>
      ),
      children: <CompactionCandidatesTab api={api} tableId={tableId} />,
      forceRender: true
    },
    {
      key: "unionable",
      label: (
        <Space size={6}>
          <AppstoreOutlined />
          <span>Union With</span>
        </Space>
      ),
      children: <UnionableTab api={api} tableId={tableId} />,
      forceRender: true
    },
    {
      key: "column-stats",
      label: (
        <Space size={6}>
          <DatabaseOutlined />
          <span>Column Stats</span>
        </Space>
      ),
      children: <ColumnStatsTab api={api} tableId={tableId} />,
      forceRender: true
    }
  ];

  const tabsByKey = Object.fromEntries(tabItems.map((item) => [item.key, item]));
  const consolidatedTabItems = [
    {
      key: "overview",
      label: (
        <span>
          <HeartOutlined />
          Overview
        </span>
      ),
      children: (
        <div className="td-tab-content">
          <OverviewTab
            score={score}
            scoreFactors={scoreFactors}
            rowExists={Boolean(row)}
            verdict={verdict}
            healthTrends={healthTrends.data}
            trendDirection={trendDirection}
            trendLabel={trendLabel}
            tableSignals={tableSignals.data}
            tableSignalsLoading={tableSignals.isLoading}
            tableSignalsError={tableSignals.error}
            costView={costView}
            benefitScores={benefitScores.data}
            stats={stats}
            statsRaw={statsRaw}
            reclaimableBytes={reclaimableBytes}
            savingsUsd={savingsUsd}
            presetOptions={assignPresetOptions}
            presetsLoading={presets.isLoading || compatiblePresets.isLoading}
            effectivePresetId={effectivePreset.data?.preset_id ?? undefined}
            onAssignPreset={(presetId) => assign.mutate(presetId)}
            assignPending={assign.isPending}
            onRun={() => run.mutate()}
            runPending={run.isPending}
            queuedMaintenanceRunId={queuedMaintenanceRunId}
            lastRunSkipNotes={lastRunSkipNotes}
            onOpenRun={handleRunAction}
            onPreview={() => setPreviewOpen(true)}
            onSchedule={(scheduledAtIso) => schedule.mutate(scheduledAtIso)}
            schedulePending={schedule.isPending}
            tableLabel={row ? `${row.schema_name}.${row.table_name}` : "this table"}
            partitionEvolution={partitionEvolution.data ?? undefined}
            tableQueries={tableQueries.data ?? undefined}
            tableQueriesLoading={tableQueries.isLoading}
            pointDeductions={pointDeductions}
          />
          <RecommendedActions
            costView={costView}
            signals={tableSignals.data}
            benefitScores={benefitScores.data}
            onPreview={() => setPreviewOpen(true)}
            onRun={() => run.mutate()}
          />
        </div>
      ),
      forceRender: true
    },
    {
      key: "structure",
      label: (
        <span>
          <DatabaseOutlined />
          Structure
        </span>
      ),
      children: (
        <div className="td-tab-content">
          <SectionNav
            sections={[
              { id: "structure-schema", label: "Schema" },
              { id: "structure-partitions", label: "Partitions" },
              { id: "structure-history", label: "History" },
              { id: "structure-bloom", label: "Bloom Filters" }
            ]}
          />
          <div id="structure-schema">{tabsByKey["schema"]?.children}</div>
          <div id="structure-partitions" style={{ marginTop: 16 }}>
            <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
              <div className="td-chart-card-header">
                <span className="td-chart-card-title">Partition breakdown</span>
                <span className="td-chart-card-subtitle">
                  Top partitions by data size and small-file pressure
                </span>
              </div>
              <div className="td-chart-card-content">
                {partitions.isLoading ? (
                  <Typography.Text type="secondary">Loading partition breakdown…</Typography.Text>
                ) : partitions.error ? (
                  <QueryErrorAlert error={partitions.error} title="Failed to load partitions" />
                ) : (
                  <PartitionHistogram partitions={partitions.data ?? []} />
                )}
              </div>
            </Card>
            {tabsByKey["column-stats"]?.children}
          </div>
          <div id="structure-history" style={{ marginTop: 16 }}>
            <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
              <div className="td-chart-card-header">
                <span className="td-chart-card-title">Stats history</span>
                <span className="td-chart-card-subtitle">Scan-over-scan trend</span>
              </div>
              <div className="td-chart-card-content">
                {history.isLoading ? (
                  <Typography.Text type="secondary">Loading historical stats…</Typography.Text>
                ) : history.error ? (
                  <QueryErrorAlert error={history.error} title="Failed to load table history" />
                ) : historyPoints.length ? (
                  <div className="chart-frame" style={{ height: 260 }}>
                    <ResponsiveContainer width="100%" height={260}>
                      <LineChart data={historyPoints}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
                        <XAxis
                          dataKey="timestamp"
                          tickFormatter={(ts) =>
                            new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" })
                          }
                          tick={{ fontSize: 11 }}
                          stroke="var(--muted, #5c667a)"
                        />
                        <YAxis yAxisId="left" tick={{ fontSize: 11 }} stroke="var(--muted, #5c667a)" />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} stroke="var(--muted, #5c667a)" />
                        <RechartsTooltip
                          {...chartTooltipProps}
                          labelFormatter={(ts) => new Date(Number(ts)).toLocaleString()}
                          formatter={(value, name) => {
                            const n = Number(value ?? 0);
                            if (String(name) === "total_size_gb") return [`${n.toFixed(1)} GB`, "Size"];
                            return [n.toLocaleString(), String(name)];
                          }}
                        />
                        <Line yAxisId="left" type="monotone" dataKey="small_files_count" stroke={cryoColors.chartPrimary} strokeWidth={2} dot={false} />
                        <Line yAxisId="right" type="monotone" dataKey="total_size_gb" stroke={cryoColors.gradeA} strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="No stats history yet" description="History appears after multiple scans." />
                )}
              </div>
            </Card>
          </div>
          <div id="structure-bloom" style={{ marginTop: 16 }}>
            <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
              <div className="td-chart-card-header">
                <span className="td-chart-card-title">Bloom filter recommendations</span>
                <span className="td-chart-card-subtitle">Equality-heavy high-cardinality columns</span>
              </div>
              <div className="td-chart-card-content">
                {bloomRecommendations.isLoading ? (
                  <Typography.Text type="secondary">Loading bloom recommendations…</Typography.Text>
                ) : bloomRecommendations.error ? (
                  <QueryErrorAlert error={bloomRecommendations.error} title="Failed to load bloom recommendations" />
                ) : (bloomRecommendations.data?.recommendations.length ?? 0) === 0 ? (
                  <EmptyState
                    title="No bloom recommendations yet"
                    description={bloomRecommendations.data?.message ?? "Ingest query telemetry to generate bloom-filter candidates."}
                  />
                ) : (
                  <Table
                    size="small"
                    rowKey="column"
                    pagination={false}
                    dataSource={bloomRecommendations.data?.recommendations ?? []}
                    columns={[
                      { title: "Column", dataIndex: "column" },
                      { title: "Cardinality", dataIndex: "cardinality", width: 120 },
                      {
                        title: "Skip rate",
                        dataIndex: "estimated_skip_rate",
                        width: 120,
                        align: "right" as const,
                        render: (value: number) => `${(value * 100).toFixed(0)}%`
                      },
                      {
                        title: "Equality filters",
                        dataIndex: "equality_filter_count",
                        width: 140,
                        align: "right" as const
                      }
                    ]}
                  />
                )}
              </div>
            </Card>
          </div>
        </div>
      ),
      forceRender: true
    },
    {
      key: "health-quality",
      label: (
        <span>
          <SafetyOutlined />
          Health & Quality
        </span>
      ),
      children: (
        <div className="td-tab-content">
          {tabsByKey["quality"]?.children}
          <div style={{ marginTop: 16 }}>{tabsByKey["ai"]?.children}</div>
        </div>
      ),
      forceRender: true
    },
    {
      key: "cost",
      label: <CostTabLabel />,
      children: (
        <div className="td-tab-content">
          {tabsByKey["cost"]?.children}
          <div style={{ marginTop: 16 }}>{tabsByKey["candidates"]?.children}</div>
        </div>
      ),
      forceRender: true
    },
    {
      key: "activity",
      label: (
        <span>
          <HistoryOutlined />
          Activity
        </span>
      ),
      children: (
        <div className="td-tab-content">
          {tabsByKey["activity"]?.children}
          <div style={{ marginTop: 16 }}>{tabsByKey["unionable"]?.children}</div>
        </div>
      ),
      forceRender: true
    }
  ];
  const allowedTabKeys = new Set(consolidatedTabItems.map((tab) => tab.key));
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

  if (!row && !table.isLoading) {
    return (
      <div className="table-detail">
        <div className="td-breadcrumbs">
          <span className="td-breadcrumb-item" onClick={() => navigate("/")}>Home</span>
          <span className="td-breadcrumb-separator">/</span>
          <span className="td-breadcrumb-item">Catalogs</span>
        </div>
        <div className="td-identity-bar" style={{ marginTop: 8 }}>
          <div className="td-identity-info">
            <Typography.Title level={3} style={{ margin: 0, color: "inherit" }}>
              Table not found
            </Typography.Title>
            <Typography.Text style={{ color: "var(--muted, #5c667a)", opacity: 0.6, fontSize: 12 }}>
              The table may not have been scanned yet or has been removed.
            </Typography.Text>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="table-detail">
      {/* 1. Deep Breadcrumbs & Contextual Hierarchy */}
      <div className="td-breadcrumbs">
        <span className="td-breadcrumb-item td-breadcrumb-interactive" onClick={() => navigate("/")}>Home</span>
        <span className="td-breadcrumb-separator">/</span>
        <span className="td-breadcrumb-item td-breadcrumb-interactive" onClick={() => navigate("/catalogs")}>Catalogs</span>
        <span className="td-breadcrumb-separator">/</span>
        <span className="td-breadcrumb-item td-breadcrumb-interactive" onClick={() => navigate(`/catalogs?source=${row?.source_id}`)}>
          {sourceName}
        </span>
        <span className="td-breadcrumb-separator">/</span>
        <span className="td-breadcrumb-item">{row?.schema_name ?? "schema"}</span>
        <span className="td-breadcrumb-separator">/</span>
        <span className="td-breadcrumb-item active">{row?.table_name ?? "table"}</span>
      </div>

      {/* 2. Header Title & Intentional Interaction Toolbar */}
      <div className="td-header-row">
        <div className="td-header-info">
          <Typography.Title level={3} className="td-header-title">
            {row ? qualifiedTableName(row) : "Loading..."}
          </Typography.Title>
          <div className="td-header-tags">
            {stats?.grade ? <GradePill grade={stats.grade} /> : null}
            {stats?.compaction_priority ? <PriorityTag priority={stats.compaction_priority} /> : null}
            <CompactionTag needsCompaction={stats?.needs_compaction} />
          </div>
          <div className="td-hero-meta">
            <span>
              Preset: <strong>{effectivePreset.data?.preset_name ?? "None"}</strong>
              <span className="td-meta-faint"> ({provenanceLabel(effectivePreset.data?.provenance)})</span>
              {effectiveDestination ? (
                <span className="td-meta-faint">
                  {" "}
                  · {effectiveDestination.name} ({formatEngineLabel(effectiveDestination.engine)})
                </span>
              ) : null}
            </span>
            {stats?.scan_at ? (
              <span>
                Last scan: <strong>{formatTime(stats.scan_at)}</strong>
              </span>
            ) : null}
            {lastSnapshotCommittedAt ? (
              <span>
                Last commit: <strong>{formatTime(lastSnapshotCommittedAt)}</strong>
              </span>
            ) : null}
            {row?.location ? (
              <span>
                Path:{" "}
                <Typography.Text code className="td-path-value" ellipsis={{ tooltip: row.location }}>
                  {row.location}
                </Typography.Text>
                <Tooltip title="Copy path">
                  <Button
                    type="text"
                    size="small"
                    icon={<CopyOutlined />}
                    className="td-path-copy"
                    aria-label="Copy table path"
                    onClick={() => {
                      void navigator.clipboard.writeText(row.location ?? "").then(
                        () => message.success("Path copied"),
                        () => message.error("Could not copy to clipboard")
                      );
                    }}
                  />
                </Tooltip>
              </span>
            ) : null}
          </div>
        </div>

        {/* Intentional Interaction Toolbar */}
        <div className="td-interaction-toolbar">
          <Button
            type="text"
            icon={<SyncOutlined spin={scan.isPending} />}
            loading={scan.isPending}
            onClick={handleScanAction}
            disabled={!row}
            style={{
              fontSize: '12px',
              fontWeight: 550,
              color: scan.isPending ? 'var(--primary)' : 'var(--muted, #5c667a)',
            }}
            className="td-refresh-btn"
            aria-label={scan.isPending ? "Refreshing stats" : queuedScanRunId ? "Open scan run" : "Refresh stats"}
          >
            {scan.isPending ? "Refreshing stats..." : queuedScanRunId ? "Open scan run" : "Refresh stats"}
          </Button>
        </div>
      </div>

      {/* 3. Quick-Attribution Bar */}
      <div className="td-quick-attribution-bar" style={{ display: "flex", gap: "16px", padding: "10px 16px", overflowX: "auto" }}>
        {/* Health Score */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 120 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(92, 102, 122, 0.05)", color: "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <HeartOutlined style={{ color: healthScoreColor(score) }} />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Health Score</span>
            <span className="td-attribution-value td-health-score" style={{ color: healthScoreColor(score) }}>
              {formatNumber(score)}
            </span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Total Size */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 110 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(92, 102, 122, 0.05)", color: "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <DatabaseOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Total Size</span>
            <span className="td-attribution-value">{formatGb(stats?.total_size_gb)}</span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Records */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 110 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(92, 102, 122, 0.05)", color: "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <TableOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Records</span>
            <span className="td-attribution-value">{formatNumber(stats?.total_records, true)}</span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Partition col */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 140 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(92, 102, 122, 0.05)", color: "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <PartitionOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Partition col</span>
            <span className="td-attribution-value" title={partitionColLabel}>
              {partitionColLabel}
              {stats?.partition_col_type ? (
                <span className="td-attribution-subtle">
                  ({stats.partition_col_type})
                </span>
              ) : null}
            </span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Savings / Mo */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 140 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(30, 142, 90, 0.06)", color: "var(--savings, #1e8e5a)", fontSize: 13, flexShrink: 0 }}>
            <DollarOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Savings / Mo</span>
            <span className="td-attribution-value td-savings-value">
              {savingsDisplay}
              {reclaimableBytes > 0 ? (
                <span className="td-attribution-subtle">
                  {formatBytesGb(reclaimableBytes)} rec.
                </span>
              ) : null}
            </span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Partitions */}
        {!hideSinglePartitionKpi ? (
          <>
            <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 110 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(92, 102, 122, 0.05)", color: "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
                <AppstoreOutlined />
              </div>
              <div style={{ display: "flex", flexDirection: "column" }}>
                <span className="td-attribution-label">Partitions</span>
                <span className="td-attribution-value">{formatNumber(stats?.total_partitions)}</span>
              </div>
            </div>
            <div className="td-attribution-divider" style={{ alignSelf: "center" }} />
          </>
        ) : null}

        {/* Files */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 110 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(92, 102, 122, 0.05)", color: "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <FileTextOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Files</span>
            <span className="td-attribution-value">{formatNumber(stats?.total_files, true)}</span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Small Files */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 130 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: stats?.small_files_count ? "rgba(240, 160, 32, 0.08)" : "rgba(92, 102, 122, 0.05)", color: stats?.small_files_count ? "var(--warning, #f0a020)" : "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <WarningOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Small Files</span>
            <span className="td-attribution-value">
              {formatNumber(stats?.small_files_count, true)}
              {stats?.total_files && stats.small_files_count != null ? (
                <span className="td-attribution-subtle">
                  {((stats.small_files_count / stats.total_files) * 100).toFixed(0)}%
                </span>
              ) : null}
            </span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Snapshots */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 110 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(92, 102, 122, 0.05)", color: "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <HistoryOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Snapshots</span>
            <span className="td-attribution-value">{formatNumber(stats?.total_snapshots)}</span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Manifests */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 110 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(92, 102, 122, 0.05)", color: "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <BranchesOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Manifests</span>
            <span className="td-attribution-value">{formatNumber(stats?.manifest_count)}</span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Delete files */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 120 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: stats?.total_delete_files ? "rgba(220, 53, 69, 0.08)" : "rgba(92, 102, 122, 0.05)", color: stats?.total_delete_files ? "var(--error, #dc3545)" : "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <DeleteOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Delete files</span>
            <span className="td-attribution-value">{formatNumber(stats?.total_delete_files)}</span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Avg file size */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 130 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: "rgba(92, 102, 122, 0.05)", color: "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <InfoCircleOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Avg file size</span>
            <span className="td-attribution-value">
              {stats?.avg_file_size_mb ? `${stats.avg_file_size_mb.toFixed(1)} MB` : "-"}
            </span>
          </div>
        </div>
        <div className="td-attribution-divider" style={{ alignSelf: "center" }} />

        {/* Partition skew */}
        <div className="td-attribution-item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 130 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: "6px", backgroundColor: stats?.partition_skew_ratio && stats.partition_skew_ratio > 4 ? "rgba(240, 160, 32, 0.08)" : "rgba(92, 102, 122, 0.05)", color: stats?.partition_skew_ratio && stats.partition_skew_ratio > 4 ? "var(--warning, #f0a020)" : "var(--muted, #5c667a)", fontSize: 13, flexShrink: 0 }}>
            <SlidersOutlined />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span className="td-attribution-label">Partition skew</span>
            <span className="td-attribution-value">
              {stats?.partition_skew_ratio != null ? `${stats.partition_skew_ratio.toFixed(2)}x` : "-"}
            </span>
          </div>
        </div>
      </div>

      {/* 4. 70/30 Asymmetric Split-Screen Layout */}
      <Row gutter={[24, 24]} className="td-split-layout">
        {/* LEFT COLUMN: Main Interactive Workspace (70%) */}
        <Col xs={24} lg={17} className="td-main-column">
          {silentLoss.data && !silentLoss.data.is_clean ? (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              title="Silent data loss detected"
              description={
                <Space orientation="vertical" size={4}>
                  <span>
                    {silentLoss.data.orphan_data_files.length} orphan data file(s) totaling
                    {" "}{(silentLoss.data.orphan_size_bytes / 1024 / 1024).toFixed(1)} MB outside any committed snapshot.
                    Root cause: {silentLoss.data.root_cause}.
                  </span>
                  <Button
                    size="small"
                    type="primary"
                    loading={repairSilentLoss.isPending}
                    onClick={() => repairSilentLoss.mutate()}
                  >
                    Repair (rewrite manifests)
                  </Button>
                </Space>
              }
            />
          ) : null}

          <Tabs activeKey={activeTab} onChange={handleTabChange} items={consolidatedTabItems} className="td-main-tabs" />
        </Col>

        {/* RIGHT COLUMN: Metadata & Compliance Sidebar (30%) */}
        <Col xs={24} lg={7} className="td-sidebar-column">
          <div className="td-sidebar-sticky">

            {/* Card 2: Configuration & Engine Status */}
            <Card className="td-sidebar-card" title="Preset & Engine" size="small">
              <div className="td-sidebar-meta-list">
                <div className="td-sidebar-meta-item" style={{ flexDirection: "column", alignItems: "stretch", gap: 6, marginBottom: 12 }}>
                  <span className="td-sidebar-meta-label">Assign Preset</span>
                  <Select
                    placeholder="Assign preset"
                    value={effectivePreset.data?.preset_id ?? undefined}
                    options={assignPresetOptions}
                    onChange={(presetId) => {
                      if (presetId) {
                        assign.mutate(presetId);
                      } else {
                        unassign.mutate();
                      }
                    }}
                    loading={assign.isPending || presets.isLoading || compatiblePresets.isLoading || unassign.isPending}
                    disabled={!row}
                    style={{ width: '100%' }}
                    size="small"
                    allowClear
                  />
                  {effectivePreset.data?.provenance ? (
                    <span className="td-meta-faint" style={{ fontSize: 10 }}>
                      Active: <strong>{effectivePreset.data?.preset_name}</strong> ({provenanceLabel(effectivePreset.data?.provenance)})
                    </span>
                  ) : null}
                </div>

                {stats?.scan_at ? (
                  <div className="td-sidebar-meta-item">
                    <span className="td-sidebar-meta-label">Last scan</span>
                    <span className="td-sidebar-meta-value">{formatTime(stats.scan_at)}</span>
                  </div>
                ) : null}
                {lastSnapshotCommittedAt ? (
                  <div className="td-sidebar-meta-item">
                    <span className="td-sidebar-meta-label">Last commit</span>
                    <span className="td-sidebar-meta-value">{formatTime(lastSnapshotCommittedAt)}</span>
                  </div>
                ) : null}

                {/* Maintenance Actions Consolidated */}
                <div style={{ marginTop: 14, paddingTop: 14, borderTop: "1.5px solid var(--line, #e1e6ed)", display: "flex", flexDirection: "column", gap: 8 }}>
                  {queuedMaintenanceRunId || run.isPending ? (
                    <Button
                      type="primary"
                      icon={<ThunderboltOutlined />}
                      loading={run.isPending}
                      onClick={handleRunAction}
                      disabled={!row || run.isPending || !queuedMaintenanceRunId}
                      size="small"
                      block
                      aria-label={run.isPending ? "Queueing run" : "Open maintenance run"}
                    >
                      {run.isPending ? "Queueing run..." : "Open maintenance run"}
                    </Button>
                  ) : (
                    <ConfirmAction
                      title="Run maintenance?"
                      description="Queues a real maintenance job using the assigned preset and destination."
                      onConfirm={() => run.mutate()}
                    >
                      <Button
                        type="primary"
                        icon={<ThunderboltOutlined />}
                        disabled={!row || !effectivePreset.data?.preset_id}
                        size="small"
                        block
                        aria-label="Run maintenance"
                      >
                        Run maintenance
                      </Button>
                    </ConfirmAction>
                  )}
                  <div style={{ display: "flex", gap: 6 }}>
                    <Button
                      icon={<DashboardOutlined />}
                      onClick={() => setPreviewOpen(true)}
                      disabled={!row || !effectivePreset.data?.preset_id}
                      size="small"
                      style={{ flex: 1, fontSize: 11 }}
                    >
                      Preview
                    </Button>
                    <ConfirmAction
                      title="Rewrite manifests?"
                      description="Queues manifest-only maintenance without data file compaction."
                      onConfirm={() => manifestRewrite.mutate()}
                    >
                      <Button
                        icon={<RobotOutlined />}
                        loading={manifestRewrite.isPending}
                        disabled={!row}
                        size="small"
                        style={{ flex: 1, fontSize: 11 }}
                      >
                        Manifest
                      </Button>
                    </ConfirmAction>
                  </div>
                </div>
              </div>
            </Card>

            {/* Card 3: Compliance & Snapshot Protection */}
            <Card className="td-sidebar-card" title="Compliance & Protection" size="small">
              <div className="td-sidebar-compliance-wrap">
                <QueryState
                  isLoading={compliance.isLoading}
                  isError={Boolean(compliance.error) && !complianceNotFound}
                  error={compliance.error}
                  data={compliance.data}
                  isEmpty={complianceNotFound || !compliance.data}
                  emptyTitle="No deletion-tracking data yet"
                  emptyDescription="This table has no compliance events yet. ABAC and deletion-delay details will appear after governance policies or delete workflows are applied."
                >
                  {(complianceData) => (
                    <Descriptions size="small" column={1} className="td-meta-desc">
                      <Descriptions.Item label="Table Ref">
                        <span className="td-sidebar-desc-val">{complianceData.table_ref}</span>
                      </Descriptions.Item>
                      <Descriptions.Item label="ABAC Count">
                        <Badge count={complianceData.abac_policy_count} showZero style={{ backgroundColor: 'var(--primary, #3d6fd4)' }} />
                      </Descriptions.Item>
                      <Descriptions.Item label="Deletion delay">
                        <PendingDeletionTag pending={Boolean(complianceData.pending_deletion)} />
                      </Descriptions.Item>
                      {complianceData.abac_enforcement_note ? (
                        <Descriptions.Item label="ABAC Note">
                          <span className="td-sidebar-desc-val">{complianceData.abac_enforcement_note}</span>
                        </Descriptions.Item>
                      ) : null}
                      {complianceData.pending_deletion ? (
                        <Descriptions.Item label="Deletion Blocks">
                          <span className="td-sidebar-desc-val">
                            {complianceData.pending_deletion.pending_rows?.toLocaleString() ?? 0} rows blocked
                            {complianceData.pending_deletion.blocked_until
                              ? ` until ${formatTime(complianceData.pending_deletion.blocked_until)}`
                              : ""}
                          </span>
                        </Descriptions.Item>
                      ) : null}
                    </Descriptions>
                  )}
                </QueryState>

                {(role === "admin" || role === "operator") ? (
                  <div className="td-sidebar-protect-form" style={{ marginTop: 14, paddingTop: 14, borderTop: "1.5px solid var(--line, #e1e6ed)" }}>
                    <Typography.Text strong style={{ fontSize: 11, display: "block", marginBottom: 6 }}>Protect Snapshot</Typography.Text>
                    <Form
                      layout="vertical"
                      onFinish={(values: { snapshot_id: number; label?: string }) => protect.mutate(values)}
                    >
                      <Row gutter={6}>
                        <Col span={10}>
                          <Form.Item
                            name="snapshot_id"
                            rules={[{ required: true, message: "Required" }]}
                            style={{ marginBottom: 6 }}
                          >
                            <InputNumber min={1} placeholder="ID" style={{ width: '100%' }} size="small" />
                          </Form.Item>
                        </Col>
                        <Col span={14}>
                          <Form.Item name="label" style={{ marginBottom: 6 }}>
                            <Input placeholder="Label" style={{ width: '100%' }} size="small" />
                          </Form.Item>
                        </Col>
                      </Row>
                      <Form.Item style={{ marginBottom: 0 }}>
                        <Button type="primary" htmlType="submit" loading={protect.isPending} size="small" block>
                          Protect Snapshot
                        </Button>
                      </Form.Item>
                    </Form>
                  </div>
                ) : null}

                <div className="td-protected-list" style={{ marginTop: 14 }}>
                  <Typography.Text strong style={{ fontSize: 11, display: "block", marginBottom: 4 }}>Protected Snapshots</Typography.Text>
                  {protectedItems
                    .filter((item) => item.snapshot_id > 0)
                    .slice(0, 8)
                    .map((item, index) => (
                      <div className="td-protected-row" key={`${item.snapshot_id}-${index}`} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, marginBottom: 4 }}>
                        <div>
                          <Badge status="processing" style={{ marginRight: 6 }} />
                          <span style={{ fontWeight: 600 }}>#{item.snapshot_id}</span>
                          {item.label ? <span style={{ marginLeft: 4, color: "var(--muted, #5c667a)", fontSize: 10 }}>({item.label})</span> : null}
                        </div>
                        {item.ts ? <Typography.Text type="secondary" style={{ fontSize: 10 }}>{formatTime(item.ts)}</Typography.Text> : null}
                      </div>
                    ))}
                  {!protectedLocal.length && !protectedFromAudit.length ? (
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>No protected snapshots recorded yet.</Typography.Text>
                  ) : null}
                </div>
              </div>
            </Card>

            <Card
              className="td-sidebar-card"
              title={
                <span>
                  <BellOutlined style={{ marginRight: 6 }} />
                  Ownership &amp; Alerts
                </span>
              }
              size="small"
            >
              <div className="td-sidebar-alerts-wrap">
                <Typography.Text strong style={{ fontSize: 11, display: "block", marginBottom: 6 }}>
                  Owner
                </Typography.Text>
                {(role === "admin" || role === "operator") ? (
                  <Space.Compact style={{ width: "100%", marginBottom: 14 }}>
                    <Input
                      value={ownerEmailInput}
                      onChange={(e) => setOwnerEmailInput(e.target.value)}
                      placeholder="owner@example.com"
                      size="small"
                    />
                    <Button
                      size="small"
                      type="primary"
                      loading={setOwner.isPending}
                      onClick={() => setOwner.mutate(ownerEmailInput.trim())}
                    >
                      Save
                    </Button>
                  </Space.Compact>
                ) : (
                  <Typography.Text
                    type={row?.owner_email ? undefined : "secondary"}
                    style={{ fontSize: 12, display: "block", marginBottom: 14 }}
                  >
                    {row?.owner_email || "No owner assigned"}
                  </Typography.Text>
                )}

                <Typography.Text strong style={{ fontSize: 11, display: "block", marginBottom: 6 }}>
                  Alert subscribers
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 10.5, display: "block", marginBottom: 8 }}>
                  Owner and subscribers get an email (and Slack, if configured) when this table
                  breaches its SLA or data-quality thresholds.
                </Typography.Text>

                <div style={{ marginBottom: 8 }}>
                  {(alertSubscriptions.data ?? []).map((sub) => (
                    <div
                      key={sub.id}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        fontSize: 11,
                        marginBottom: 4
                      }}
                    >
                      <span>{sub.email}</span>
                      <Button
                        type="text"
                        size="small"
                        icon={<CloseOutlined style={{ fontSize: 10 }} />}
                        aria-label={`Remove subscriber ${sub.email}`}
                        loading={removeSubscriber.isPending}
                        onClick={() => removeSubscriber.mutate(sub.id)}
                      />
                    </div>
                  ))}
                  {alertSubscriptions.data && alertSubscriptions.data.length === 0 ? (
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      No subscribers yet.
                    </Typography.Text>
                  ) : null}
                </div>

                <Space.Compact style={{ width: "100%" }}>
                  <Input
                    value={newSubscriberEmail}
                    onChange={(e) => setNewSubscriberEmail(e.target.value)}
                    placeholder="consumer@example.com"
                    size="small"
                    onPressEnter={() => {
                      if (!newSubscriberEmail.trim()) return;
                      addSubscriber.mutate(newSubscriberEmail.trim());
                      setNewSubscriberEmail("");
                    }}
                  />
                  <Button
                    size="small"
                    loading={addSubscriber.isPending}
                    onClick={() => {
                      if (!newSubscriberEmail.trim()) return;
                      addSubscriber.mutate(newSubscriberEmail.trim());
                      setNewSubscriberEmail("");
                    }}
                  >
                    Subscribe
                  </Button>
                </Space.Compact>
              </div>
            </Card>
          </div>
        </Col>
      </Row>

      <PlanPreview open={previewOpen} tables={row ? [row] : []} onClose={() => setPreviewOpen(false)} />
    </div>
  );
};
