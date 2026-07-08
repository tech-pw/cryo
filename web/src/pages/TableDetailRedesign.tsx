import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AppstoreOutlined,
  SyncOutlined,
  DatabaseOutlined,
  DollarOutlined,
  HistoryOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  WarningOutlined,
  TableOutlined,
  CloseOutlined,
  BellOutlined,
  PartitionOutlined,
  DeleteOutlined,
  SlidersOutlined,
  FileTextOutlined,
  CopyOutlined,
  InfoCircleOutlined,
  ExclamationCircleOutlined
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
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  assignTables,
  getComplianceStatus,
  getEffectivePreset,
  getPartitionEvolutionRecommendation,
  getTable,
  getTableAudit,
  getTableBenefitScores,
  getTableCost,
  getTableHealthTrends,
  getTableS3Tiering,
  getTableSignals,
  listDestinations,
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
import type {
  AuditEntry,
  CompactionCandidateRead,
  UnionScoreRead,
  ColumnStatsResponse,
  SilentDataLossReportRead,
  TableStatsRead
} from "../api/types";
import { EmptyState, QueryErrorAlert } from "../components/common";
import { ConfirmAction } from "../components/confirm";
import { CostPrecisionBadge } from "../components/CostPrecisionBadge";
import { CostSavingsWaterfall } from "../components/CostSavingsWaterfall";
import { SavingsByOperation } from "../components/SavingsByOperation";
import { PlanPreview } from "../components/PlanPreview";
import { TableAiInsights } from "../components/TableAiInsights";
import { TableCompactionAnalytics } from "../components/TableCompactionAnalytics";
import { TableDiagnosisPanel, buildDiagnosisVerdict } from "../components/TableDiagnosisPanel";
import { buildHealthScoreFactors } from "../components/HealthScoreBreakdown";
import { OverviewTab } from "../components/table-detail/OverviewTab";
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
import { formatEngineLabel } from "../lib/engines";
import { provenanceLabel } from "../components/table-detail/helpers";

const PARTITION_UNPARTITIONED_SENTINELS = new Set(["_unpartitioned", "unpartitioned", ""]);

const formatPartitionColumnLabel = (col: string | null | undefined): string => {
  if (!col || PARTITION_UNPARTITIONED_SENTINELS.has(col.toLowerCase())) return "Unpartitioned";
  return col;
};

const isUnpartitioned = (stats: TableStatsRead | null | undefined): boolean => {
  if (!stats) return false;
  return PARTITION_UNPARTITIONED_SENTINELS.has((stats.partition_col ?? "").toLowerCase());
};

const partitionValueCount = (stats: TableStatsRead | null | undefined): number => {
  if (isUnpartitioned(stats)) return 0;
  return stats?.total_partitions ?? 0;
};

/** Whether a savings projection has been computed. A 0 with no basis is not the same as "no projection". */
const hasSavingsProjection = (
  stats: TableStatsRead | null | undefined,
  savingsUsd: number,
  reclaimableBytes: number
): boolean => {
  if (savingsUsd > 0) return true;
  if (reclaimableBytes > 0) return true;
  if (!stats) return false;
  return stats.projected_savings_usd_month != null || stats.reclaimable_bytes != null;
};

export const TableDetail = () => {
  const { tableId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { api, token, role } = useAuth();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [queuedScanRunId, setQueuedScanRunId] = useState<string | null>(null);
  const [queuedMaintenanceRunId, setQueuedMaintenanceRunId] = useState<string | null>(null);
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
          return Array.isArray(raw) ? raw.filter((note): note is string => typeof note === "string") : [];
        });
        setLastRunSkipNotes(notes.filter((note) => note.toLowerCase().startsWith("skipped")));
      })
      .catch(() => undefined)
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
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });
  const compliance = useQuery({
    queryKey: ["table-compliance", tableId, token],
    queryFn: () => getComplianceStatus(api, tableId),
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
      message.success(`Run queued: ${result.run_id}`);
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
      message.success(`Run scheduled: ${result.run_id}`);
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
  const stats = row?.stats;
  const verdict = buildDiagnosisVerdict(stats);
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
  const savingsUsd = costView?.projected_savings_usd_month ?? stats?.projected_savings_usd_month ?? 0;
  const reclaimableBytes = costView?.reclaimable_bytes ?? stats?.reclaimable_bytes ?? 0;
  const storageBytes =
    (costView?.live_bytes ?? 0) +
    (costView?.dead_bytes ?? 0) +
    (costView?.metadata_bytes ?? 0) +
    (costView?.orphan_bytes ?? 0);
  const score = stats?.overall_score ?? 0;
  const scoreFactors = useMemo(
    () => buildHealthScoreFactors(stats, costView?.orphan_bytes ?? 0, storageBytes),
    [stats, costView?.orphan_bytes, storageBytes]
  );

  const pointDeductions = useMemo(() => {
    const raw = (stats?.raw ?? {}) as Record<string, any>;
    const deductions = raw.health_deductions;
    return Array.isArray(deductions)
      ? (deductions as Array<{ label: string; deduction: number; severity: "critical" | "warning"; reason: string }>)
      : [];
  }, [stats]);

  const degradationRate = healthTrends.data?.degradation_rate;
  const trendDirection: "up" | "down" | "flat" =
    degradationRate == null ? "flat" : degradationRate < 0 ? "down" : degradationRate > 0 ? "up" : "flat";
  // Suppress a misleading "0.0 pts/day" when there isn't enough history to know yet — issue #7.
  const hasTrendHistory = (healthTrends.data?.trend_points ?? []).length >= 2;
  const trendLabel: string | null =
    degradationRate == null || !hasTrendHistory
      ? null
      : `Trend: ${degradationRate > 0 ? "+" : ""}${degradationRate.toFixed(1)} pts/day`;

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

  const tableUnpartitioned = isUnpartitioned(stats);
  const savingsProjected = hasSavingsProjection(stats, savingsUsd, reclaimableBytes);

  const tabItems = [
    {
      key: "overview",
      label: (
        <Space size={6}>
          <AppstoreOutlined />
          <span>Health &amp; Diagnosis</span>
          {(tableSignals.data?.signals ?? []).filter((s) => s.triggered && s.severity !== "info").length > 0 ? (
            <Badge
              count={(tableSignals.data?.signals ?? []).filter((s) => s.triggered && s.severity !== "info").length}
              style={{ backgroundColor: "var(--error, #dc3545)" }}
              size="small"
            />
          ) : null}
        </Space>
      ),
      children: (
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
          stats={stats ?? undefined}
          statsRaw={statsRaw}
          reclaimableBytes={reclaimableBytes}
          savingsUsd={savingsUsd}
          presetOptions={(presets.data ?? []).map((p) => ({ label: p.name, value: p.id }))}
          presetsLoading={presets.isLoading}
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
          tableQueries={tableQueries.data}
          tableQueriesLoading={tableQueries.isLoading}
          pointDeductions={pointDeductions}
        />
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
      key: "quality",
      label: (
        <span>
          <HistoryOutlined />
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
                      {dataQuality.data.anomalies_detected}{" "}
                      {dataQuality.data.anomalies_detected === 1 ? "Anomaly" : "Anomalies"}
                    </Tag>
                  ) : null}
                </div>
                <div className="td-chart-card-content">
                  <QualityColumnsTable mergedColumns={mergedColumns} loading={dataQuality.isLoading || table.isLoading} />
                </div>
              </Card>
            </Col>
            <Col xs={24} xl={8}>
              <div className="td-quality-side-col">
                <FreshnessSlaCard slaStatus={slaStatus} loading={tableSla.isLoading} />
                <DriftAlertsCard
                  dataQuality={dataQuality.data}
                  qualityBreakdown={qualityBreakdown}
                />
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
      key: "ai",
      label: (
        <Tooltip title="Requires query-log ingestion to populate">
          <span>
            <RobotOutlined />
            AI &amp; Analytics
            <Tag color="default" style={{ marginLeft: 6, fontSize: 9 }}>requires ingestion</Tag>
          </span>
        </Tooltip>
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
      key: "candidates",
      label: (
        <Space size={6}>
          <AppstoreOutlined />
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
      <div className="td-breadcrumbs">
        <span className="td-breadcrumb-item td-breadcrumb-interactive" onClick={() => navigate("/")}>Home</span>
        <span className="td-breadcrumb-separator">/</span>
        <span className="td-breadcrumb-item td-breadcrumb-interactive" onClick={() => navigate("/tables")}>Catalogs</span>
        <span className="td-breadcrumb-separator">/</span>
        <span className="td-breadcrumb-item td-breadcrumb-interactive" onClick={() => navigate(`/tables?source_id=${row?.source_id}`)}>
          {sourceName}
        </span>
        <span className="td-breadcrumb-separator">/</span>
        <span className="td-breadcrumb-item">{row?.schema_name ?? "schema"}</span>
        <span className="td-breadcrumb-separator">/</span>
        <span className="td-breadcrumb-item active">{row?.table_name ?? "table"}</span>
      </div>

      <div className="td-header-row">
        <div className="td-header-info">
          <Typography.Title level={3} className="td-header-title">
            {row ? qualifiedTableName(row) : "Loading..."}
          </Typography.Title>
          <div className="td-header-tags">
            {stats?.grade ? (
              <Tooltip
                title={`Health grade ${stats.grade} based on the table's overall health score (0–100). Lower grades warrant earlier maintenance.`}
              >
                <span>
                  <GradePill grade={stats.grade} />
                </span>
              </Tooltip>
            ) : null}
            {stats?.compaction_priority ? <PriorityTag priority={stats.compaction_priority} /> : null}
            <CompactionTag needsCompaction={stats?.needs_compaction} />
          </div>
          <div className="td-hero-meta">
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

        <div className="td-interaction-toolbar">
          {stats?.scan_at ? (
            <span className="td-refresh-meta">
              Last scanned {formatTime(stats.scan_at)}
            </span>
          ) : null}
          <Button
            type="text"
            icon={<SyncOutlined spin={scan.isPending} />}
            loading={scan.isPending}
            onClick={handleScanAction}
            disabled={!row}
            style={{
              fontSize: "12px",
              fontWeight: 550,
              color: scan.isPending ? "var(--primary)" : "var(--muted, #5c667a)"
            }}
            className="td-refresh-btn"
            aria-label={
              scan.isPending
                ? "Refreshing stats"
                : queuedScanRunId
                  ? "Open scan run"
                  : stats?.scan_at
                    ? `Refresh stats — last run ${formatTime(stats.scan_at)}`
                    : "Refresh stats"
            }
          >
            {scan.isPending ? "Refreshing stats..." : queuedScanRunId ? "Open scan run" : "Refresh stats"}
          </Button>
        </div>
      </div>

      <div className="td-quick-attribution-bar" style={{ display: "flex", gap: "16px", padding: "10px 16px", overflowX: "auto" }}>
        <KpiTile
          icon={<AppstoreOutlined style={{ color: healthScoreColor(score) }} />}
          label="Health Score"
          value={formatNumber(score)}
        />
        <Divider />
        <KpiTile
          icon={<DatabaseOutlined />}
          label="Total Size"
          value={formatGb(stats?.total_size_gb)}
        />
        <Divider />
        <KpiTile
          icon={<TableOutlined />}
          label="Records"
          value={formatNumber(stats?.total_records, true)}
        />
        <Divider />
        {tableUnpartitioned ? (
          <KpiTile
            icon={<PartitionOutlined />}
            label="Partitioning"
            value="Unpartitioned"
            subtle=""
          />
        ) : (
          <>
            <KpiTile
              icon={<PartitionOutlined />}
              label="Partition col"
              value={formatPartitionColumnLabel(stats?.partition_col)}
              subtle={stats?.partition_col_type ?? undefined}
            />
            <Divider />
            <KpiTile
              icon={<AppstoreOutlined />}
              label="Partitions"
              value={formatNumber(partitionValueCount(stats))}
            />
          </>
        )}
        <Divider />
        <SavingsKpiTile
          savingsUsd={savingsUsd}
          reclaimableBytes={reclaimableBytes}
          hasProjection={savingsProjected}
          isSmallTable={(stats?.total_size_gb ?? 0) > 0 && (stats?.total_size_gb ?? 0) < 1}
        />
        <Divider />
        <KpiTile
          icon={<FileTextOutlined />}
          label="Files"
          value={formatNumber(stats?.total_files, true)}
        />
        <Divider />
        <KpiTile
          icon={<WarningOutlined />}
          iconActive={Boolean(stats?.small_files_count)}
          label="Small Files"
          value={formatNumber(stats?.small_files_count, true)}
          subtle={
            stats?.total_files && stats.small_files_count != null
              ? `${((stats.small_files_count / stats.total_files) * 100).toFixed(0)}% of total`
              : undefined
          }
        />
        <Divider />
        <KpiTile
          icon={<HistoryOutlined />}
          label="Snapshots"
          value={formatNumber(stats?.total_snapshots)}
        />
        <Divider />
        <KpiTile
          icon={<AppstoreOutlined />}
          label="Manifests"
          value={formatNumber(stats?.manifest_count)}
        />
        <Divider />
        <KpiTile
          icon={<DeleteOutlined />}
          iconActive={Boolean(stats?.total_delete_files)}
          label="Delete files"
          value={formatNumber(stats?.total_delete_files)}
        />
        <Divider />
        <KpiTile
          icon={<InfoCircleOutlined />}
          label="Avg file size"
          value={stats?.avg_file_size_mb ? `${stats.avg_file_size_mb.toFixed(1)} MB` : "-"}
        />
        <Divider />
        <KpiTile
          icon={<SlidersOutlined />}
          iconActive={Boolean(stats?.partition_skew_ratio && stats.partition_skew_ratio > 4)}
          label="Partition skew"
          value={stats?.partition_skew_ratio != null ? `${stats.partition_skew_ratio.toFixed(2)}x` : "-"}
        />
      </div>

      <Row gutter={[24, 24]} className="td-split-layout">
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
                    {" "}
                    {(silentLoss.data.orphan_size_bytes / 1024 / 1024).toFixed(1)} MB outside any committed snapshot.
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

          <Tabs defaultActiveKey="overview" items={tabItems} className="td-main-tabs" />
        </Col>

        <Col xs={24} lg={7} className="td-sidebar-column">
          <div className="td-sidebar-sticky">
            <Card className="td-sidebar-card" title="Preset & Engine" size="small">
              <div className="td-sidebar-meta-list">
                <div className="td-sidebar-meta-item" style={{ flexDirection: "column", alignItems: "stretch", gap: 6, marginBottom: 12 }}>
                  <span className="td-sidebar-meta-label">Preset</span>
                  <Select
                    placeholder="Assign preset"
                    value={effectivePreset.data?.preset_id ?? undefined}
                    options={(presets.data ?? []).map((p) => ({ label: p.name, value: p.id }))}
                    onChange={(presetId) => {
                      if (presetId) {
                        assign.mutate(presetId);
                      } else {
                        unassign.mutate();
                      }
                    }}
                    loading={assign.isPending || presets.isLoading || unassign.isPending}
                    disabled={!row}
                    style={{ width: "100%" }}
                    size="small"
                    allowClear
                  />
                  {effectivePreset.data?.preset_name ? (
                    <span className="td-meta-faint" style={{ fontSize: 10 }}>
                      Active: <strong>{effectivePreset.data.preset_name}</strong>{" "}
                      ({provenanceLabel(effectivePreset.data?.provenance)})
                      {effectiveDestination
                        ? ` · ${effectiveDestination.name} (${formatEngineLabel(effectiveDestination.engine, effectiveDestination.config)})`
                        : ""}
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
                      icon={<AppstoreOutlined />}
                      onClick={() => setPreviewOpen(true)}
                      disabled={!row || !effectivePreset.data?.preset_id}
                      size="small"
                      style={{ flex: 1, fontSize: 11 }}
                    >
                      Preview plan
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
                        Rewrite manifests
                      </Button>
                    </ConfirmAction>
                  </div>
                </div>
              </div>
            </Card>

            <ComplianceProtectionCard
              compliance={compliance.data}
              complianceLoading={compliance.isLoading}
              complianceError={compliance.error}
              role={role}
              protectPending={protect.isPending}
              onProtect={(values) => protect.mutate(values)}
              protectedItems={protectedItems}
              hasLocal={protectedLocal.length > 0}
            />

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
                {role === "admin" || role === "operator" ? (
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

const KpiTile = ({
  icon,
  iconActive,
  label,
  value,
  subtle
}: {
  icon: React.ReactNode;
  iconActive?: boolean;
  label: string;
  value: string;
  subtle?: string;
}) => (
  <div
    className="td-attribution-item"
    style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 120 }}
  >
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 28,
        height: 28,
        borderRadius: "6px",
        backgroundColor: iconActive ? "rgba(240, 160, 32, 0.08)" : "rgba(92, 102, 122, 0.05)",
        color: iconActive ? "var(--warning, #f0a020)" : "var(--muted, #5c667a)",
        fontSize: 13,
        flexShrink: 0
      }}
    >
      {icon}
    </div>
    <div style={{ display: "flex", flexDirection: "column" }}>
      <span className="td-attribution-label">{label}</span>
      <span className="td-attribution-value">
        {value}
        {subtle ? <span className="td-attribution-subtle"> {subtle}</span> : null}
      </span>
    </div>
  </div>
);

const Divider = () => <div className="td-attribution-divider" style={{ alignSelf: "center" }} />;

/**
 * Savings KPI — the single most-misread cell on the page (issue #2).
 * - No projection yet → "Not estimated" with a small hint, instead of "US$0".
 * - Real zero on a sub-1GB table → explain that storage savings are negligible and the
 *   win is query latency, not storage cost. Keeps the value, but makes the copy honest.
 */
const SavingsKpiTile = ({
  savingsUsd,
  reclaimableBytes,
  hasProjection,
  isSmallTable
}: {
  savingsUsd: number;
  reclaimableBytes: number;
  hasProjection: boolean;
  isSmallTable: boolean;
}) => {
  const renderValue = () => {
    if (!hasProjection) {
      return (
        <Tooltip title="Cost projection runs after the next scan completes; until then we can't estimate savings.">
          <span className="td-attribution-subtle">Not estimated</span>
        </Tooltip>
      );
    }
    if (savingsUsd > 0) {
      return (
        <>
          {formatCurrency(savingsUsd)}
          {reclaimableBytes > 0 ? (
            <span className="td-attribution-subtle">{formatBytesGb(reclaimableBytes)} rec.</span>
          ) : null}
        </>
      );
    }
    // Real zero — keep the truth but reframe it so a $0 next to "high priority" doesn't read as a bug.
    if (isSmallTable) {
      return (
        <Tooltip title="Storage savings are negligible for sub-1 GB tables; the real win from compaction is query latency on this table.">
          <span style={{ color: "var(--muted, #5c667a)", fontStyle: "italic", fontWeight: 600 }}>
            ≈ $0
          </span>
        </Tooltip>
      );
    }
    return <span style={{ color: "var(--muted, #5c667a)" }}>$0</span>;
  };

  return (
    <div
      className="td-attribution-item"
      style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10, minWidth: 140 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 28,
          height: 28,
          borderRadius: "6px",
          backgroundColor: hasProjection && savingsUsd > 0 ? "rgba(30, 142, 90, 0.06)" : "rgba(92, 102, 122, 0.05)",
          color:
            hasProjection && savingsUsd > 0
              ? "var(--savings, #1e8e5a)"
              : "var(--muted, #5c667a)",
          fontSize: 13,
          flexShrink: 0
        }}
      >
        <DollarOutlined />
      </div>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <span className="td-attribution-label">Savings / mo</span>
        <span className="td-attribution-value td-savings-value">{renderValue()}</span>
      </div>
    </div>
  );
};

const QualityColumnsTable = ({
  mergedColumns,
  loading
}: {
  mergedColumns: any[];
  loading: boolean;
}) => {
  return (
    <Table
      size="small"
      rowKey="name"
      dataSource={mergedColumns}
      loading={loading}
      pagination={{ pageSize: 8, size: "small" }}
      className="td-schema-table"
      scroll={{ x: 820 }}
      columns={[
        {
          title: "Column",
          dataIndex: "name",
          width: 170,
          ellipsis: true,
          render: (name: string) => <span className="td-schema-col-name">{name}</span>
        },
        {
          title: "Current Null %",
          dataIndex: "nullRate",
          width: 150,
          align: "right" as const,
          sorter: (a: any, b: any) => (a.nullRate ?? 0) - (b.nullRate ?? 0),
          render: (nullRate: number | null, item: any) => {
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
          sorter: (a: any, b: any) => (a.dqNullBaseline ?? 0) - (b.dqNullBaseline ?? 0),
          render: (v: number | null | undefined) => (v != null ? `${v.toFixed(1)}%` : "—")
        },
        {
          title: "Value Range Matrix",
          dataIndex: "lowerBound",
          width: 220,
          render: (_: unknown, item: any) => {
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
          render: (_: unknown, item: any) => {
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
  );
};

const FreshnessSlaCard = ({ slaStatus, loading }: { slaStatus: any; loading: boolean }) => (
  <Card
    className="td-chart-card"
    title="Freshness & SLA"
    classNames={{ body: "td-chart-card-body" }}
    style={{ margin: 0 }}
  >
    {loading ? (
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
            <span className="td-quality-check-value-ok">{formatSlaHours(slaStatus.scan_staleness_hours)}</span>
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
);

const DriftAlertsCard = ({
  dataQuality,
  qualityBreakdown
}: {
  dataQuality: any;
  qualityBreakdown: { nullSpikeCount: number; rangeShiftCount: number; schemaChangeCount: number };
}) => (
  <Card
    className="td-chart-card"
    title="Data Drift & Alerts"
    classNames={{ body: "td-chart-card-body" }}
    style={{ margin: 0 }}
  >
    {dataQuality ? (
      <div className="td-quality-side">
        {qualityBreakdown.schemaChangeCount > 0 ? (
          <Alert
            type="warning"
            showIcon
            className="td-quality-alert"
            title={<strong>Schema Drift Detected</strong>}
            description={
              <Space orientation="vertical" size={2}>
                {dataQuality.new_columns.length ? (
                  <div>
                    <strong className="td-quality-alert-added">New columns:</strong>{" "}
                    {dataQuality.new_columns.map((c: string, idx: number) => (
                      <Tag color="green" key={`new-${idx}-${c ?? ""}`}>
                        {c}
                      </Tag>
                    ))}
                  </div>
                ) : null}
                {dataQuality.dropped_columns.length ? (
                  <div>
                    <strong className="td-quality-alert-removed">Dropped columns:</strong>{" "}
                    {dataQuality.dropped_columns.map((c: string, idx: number) => (
                      <Tag color="red" key={`dropped-${idx}-${c ?? ""}`}>
                        {c}
                      </Tag>
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
                qualityBreakdown.nullSpikeCount > 0 ? "td-quality-check-value-bad" : "td-quality-check-value-ok"
              }
            >
              {qualityBreakdown.nullSpikeCount > 0 ? `${qualityBreakdown.nullSpikeCount} failing` : "Passed"}
            </span>
          </div>
          <div className="td-quality-check-row">
            <span className="td-quality-check-label">Value range checks</span>
            <span
              className={
                qualityBreakdown.rangeShiftCount > 0 ? "td-quality-check-value-warn" : "td-quality-check-value-ok"
              }
            >
              {qualityBreakdown.rangeShiftCount > 0 ? `${qualityBreakdown.rangeShiftCount} shifted` : "Passed"}
            </span>
          </div>
          <div className="td-quality-check-row">
            <span className="td-quality-check-label">Schema changes</span>
            <span
              className={
                qualityBreakdown.schemaChangeCount > 0 ? "td-quality-check-value-warn" : "td-quality-check-value-ok"
              }
            >
              {qualityBreakdown.schemaChangeCount > 0 ? `${qualityBreakdown.schemaChangeCount} changed` : "None"}
            </span>
          </div>
          <div className="td-quality-check-row">
            <span className="td-quality-check-label">Total tracked columns</span>
            <span className="td-quality-check-value-ok">{dataQuality.column_metrics.length}</span>
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
);

/**
 * Compliance & Protection card.
 *
 * Issue #1: a 404 from /tables/{id}/compliance was previously rendered as a red
 * "Failed to load compliance status — Not Found" failure alert. A missing compliance
 * row is an empty state, not a transport error — distinguish between "the endpoint
 * is misconfigured", "this table has no compliance data yet", and "real fetch error".
 */
const ComplianceProtectionCard = ({
  compliance,
  complianceLoading,
  complianceError,
  role,
  protectPending,
  onProtect,
  protectedItems,
  hasLocal
}: {
  compliance: any;
  complianceLoading: boolean;
  complianceError: unknown;
  role: string | undefined;
  protectPending: boolean;
  onProtect: (values: { snapshot_id: number; label?: string }) => void;
  protectedItems: Array<{ snapshot_id: number; label?: string | null; ts?: string | null }>;
  hasLocal: boolean;
}) => {
  const isNotFound =
    complianceError &&
    typeof complianceError === "object" &&
    "status" in complianceError &&
    (complianceError as { status?: number }).status === 404;
  const showEmptyState = !compliance && !complianceLoading && (isNotFound || (!complianceError && !compliance));

  return (
    <Card className="td-sidebar-card" title="Compliance & Protection" size="small">
      <div className="td-sidebar-compliance-wrap">
        {compliance ? (
          <Descriptions size="small" column={1} className="td-meta-desc">
            <Descriptions.Item label="Table Ref">
              <span className="td-sidebar-desc-val">{compliance.table_ref}</span>
            </Descriptions.Item>
            <Descriptions.Item label="ABAC Count">
              <Badge
                count={compliance.abac_policy_count}
                showZero
                style={{ backgroundColor: "var(--primary, #3d6fd4)" }}
              />
            </Descriptions.Item>
            <Descriptions.Item label="Deletion delay">
              <PendingDeletionTag pending={Boolean(compliance.pending_deletion)} />
            </Descriptions.Item>
            {compliance.abac_enforcement_note ? (
              <Descriptions.Item label="ABAC Note">
                <span className="td-sidebar-desc-val">{compliance.abac_enforcement_note}</span>
              </Descriptions.Item>
            ) : null}
            {compliance.pending_deletion ? (
              <Descriptions.Item label="Deletion Blocks">
                <span className="td-sidebar-desc-val">
                  {compliance.pending_deletion.pending_rows?.toLocaleString() ?? 0} rows blocked
                  {compliance.pending_deletion.blocked_until
                    ? ` until ${formatTime(compliance.pending_deletion.blocked_until)}`
                    : ""}
                </span>
              </Descriptions.Item>
            ) : null}
          </Descriptions>
        ) : complianceLoading ? (
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            Loading compliance status…
          </Typography.Text>
        ) : showEmptyState ? (
          <EmptyState
            title="No compliance data yet"
            description="Enable snapshot protection below to start tracking ABAC policies and deletion delays for this table."
          />
        ) : complianceError ? (
          <QueryErrorAlert error={complianceError} title="Failed to load compliance status" />
        ) : (
          <EmptyState title="Compliance unavailable" description="No compliance data has been recorded for this table yet." />
        )}

        {role === "admin" || role === "operator" ? (
          <div className="td-sidebar-protect-form" style={{ marginTop: 14, paddingTop: 14, borderTop: "1.5px solid var(--line, #e1e6ed)" }}>
            <Typography.Text strong style={{ fontSize: 11, display: "block", marginBottom: 6 }}>
              Protect Snapshot
            </Typography.Text>
            <Form
              layout="vertical"
              onFinish={(values: { snapshot_id: number; label?: string }) => onProtect(values)}
            >
              <Row gutter={6}>
                <Col span={10}>
                  <Form.Item
                    name="snapshot_id"
                    rules={[{ required: true, message: "Required" }]}
                    style={{ marginBottom: 6 }}
                  >
                    <InputNumber min={1} placeholder="ID" style={{ width: "100%" }} size="small" />
                  </Form.Item>
                </Col>
                <Col span={14}>
                  <Form.Item name="label" style={{ marginBottom: 6 }}>
                    <Input placeholder="Label" style={{ width: "100%" }} size="small" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" htmlType="submit" loading={protectPending} size="small" block>
                  Protect Snapshot
                </Button>
              </Form.Item>
            </Form>
          </div>
        ) : null}

        <div className="td-protected-list" style={{ marginTop: 14 }}>
          <Typography.Text strong style={{ fontSize: 11, display: "block", marginBottom: 4 }}>
            Protected Snapshots
          </Typography.Text>
          {protectedItems
            .filter((item) => item.snapshot_id > 0)
            .slice(0, 8)
            .map((item, index) => (
              <div
                className="td-protected-row"
                key={`${item.snapshot_id}-${index}`}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  fontSize: 11,
                  marginBottom: 4
                }}
              >
                <div>
                  <Badge status="processing" style={{ marginRight: 6 }} />
                  <span style={{ fontWeight: 600 }}>#{item.snapshot_id}</span>
                  {item.label ? (
                    <span style={{ marginLeft: 4, color: "var(--muted, #5c667a)", fontSize: 10 }}>
                      ({item.label})
                    </span>
                  ) : null}
                </div>
                {item.ts ? (
                  <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                    {formatTime(item.ts)}
                  </Typography.Text>
                ) : null}
              </div>
            ))}
          {!hasLocal && protectedItems.filter((i) => i.snapshot_id > 0).length === 0 ? (
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              No protected snapshots recorded yet.
            </Typography.Text>
          ) : null}
        </div>
      </div>
    </Card>
  );
};

// Keep linter happy about removed imports no longer in scope.
void ExclamationCircleOutlined;
void isOrphanScanStale;
void CostPrecisionBadge;
void CostSavingsWaterfall;
void SavingsByOperation;