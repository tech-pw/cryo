import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AppstoreOutlined,
  ClearOutlined,
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
  ToolOutlined,
  BranchesOutlined
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
  getQueryPerformanceDashboard
} from "../api/client";
import type { AuditEntry } from "../api/types";
import { EmptyState, QueryErrorAlert } from "../components/common";
import { ConfirmAction } from "../components/confirm";
import { CostPrecisionBadge } from "../components/CostPrecisionBadge";
import { CostSavingsWaterfall } from "../components/CostSavingsWaterfall";
import { HealthScoreBreakdown, buildHealthScoreFactors } from "../components/HealthScoreBreakdown";
import { SavingsByOperation } from "../components/SavingsByOperation";
import { PlanPreview } from "../components/PlanPreview";
import { TableAiInsights } from "../components/TableAiInsights";
import { TableCompactionAnalytics } from "../components/TableCompactionAnalytics";
import { TableDiagnosisPanel, buildDiagnosisVerdict } from "../components/TableDiagnosisPanel";
import { HealthGauge } from "../components/table-detail/HealthGauge";
import { KpiStripe } from "../components/table-detail/KpiStripe";
import { RecommendedActions } from "../components/table-detail/RecommendedActions";
import { StorageDonut } from "../components/table-detail/StorageDonut";
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
import { chartTooltipProps, cryoColors } from "../lib/theme";

const provenanceLabel = (provenance?: string | null): string => {
  switch (provenance) {
    case "assignment":
      return "manually assigned";
    case "rule":
      return "matched a rule";
    case "none":
      return "no preset assigned";
    default:
      return "unknown";
  }
};

const actionIcon = (action: string, failed: boolean): React.ReactNode => {
  if (failed) return <WarningOutlined />;
  const normalized = action.toLowerCase();
  if (normalized.includes("orphan")) return <ClearOutlined />;
  if (normalized.includes("expire")) return <HistoryOutlined />;
  if (normalized.includes("manifest")) return <AppstoreOutlined />;
  return <ThunderboltOutlined />;
};

interface SignalPointerDetail {
  title: string;
  recommendation: string;
  impactText: string;
}

const getSignalPointerDetail = (signalName: string, severity: string): SignalPointerDetail => {
  const normName = (signalName || "").toLowerCase();
  if (normName.includes("degradation") || normName.includes("file")) {
    return {
      title: "Small-File Fragmentation Pressure",
      recommendation: "Execute table compaction (OPTIMIZE) to merge fragmented small files and rewrite manifests.",
      impactText: severity === "critical" ? "-20 pts" : "-8 pts"
    };
  }
  if (normName.includes("latency") || normName.includes("query")) {
    return {
      title: "Query Scanning Performance Degradation",
      recommendation: "Run layout optimization or tune partition filters to avoid scanning overhead.",
      impactText: severity === "critical" ? "-20 pts" : "-8 pts"
    };
  }
  if (normName.includes("cost") || normName.includes("anomaly")) {
    return {
      title: "Storage Cost & Resource Overhead Anomaly",
      recommendation: "Expire historical snapshots and purge orphaned storage files to recover dead bytes.",
      impactText: severity === "critical" ? "-20 pts" : "-8 pts"
    };
  }
  return {
    title: "Unhealthy Maintenance Alarm",
    recommendation: "Inspect background logs and schedule a full optimization sweep.",
    impactText: severity === "critical" ? "-20 pts" : "-8 pts"
  };
};

interface SignalMatrixDetail {
  what: string;
  why: string;
  current: string;
  ideal: string;
}

const getSignalMatrix = (
  signalName: string,
  stats: any,
  statsRaw: any,
  reclaimableBytes: number,
  savingsUsd: number,
  small_pct: number
): SignalMatrixDetail => {
  const normName = (signalName || "").toLowerCase();
  
  if (normName.includes("degradation") || normName.includes("file")) {
    return {
      what: "Small-File Fragmentation Ratio",
      why: "A high volume of small files forces query engines (Trino/Spark) to execute sequential S3 list & GET calls during split planning, which compounds query latency and inflates network overhead.",
      current: `${small_pct.toFixed(1)}% small files (${(stats?.small_files_count ?? 0).toLocaleString()} / ${(stats?.total_files ?? 0).toLocaleString()} total files)`,
      ideal: "< 20.0% small files ratio"
    };
  }
  
  if (normName.includes("latency") || normName.includes("query")) {
    return {
      what: "Query Planning & Scan Latency Overheads",
      why: "Too many partitioned files or excessive manifest metadata directories require compilers to parse complex filter expressions and build large FilterNode planning trees, which causes engine planning timeouts.",
      current: `${(stats?.manifest_count ?? 0).toLocaleString()} manifest files (avg files/partition: ${(stats?.avg_files_per_partition ?? 0).toFixed(1)})`,
      ideal: "< 100 manifest files & < 50 files per partition"
    };
  }
  
  if (normName.includes("cost") || normName.includes("anomaly")) {
    const reclaimGb = (reclaimableBytes / (1024 * 1024 * 1024)).toFixed(1);
    return {
      what: "Storage Footprint & Orphan File Buildup",
      why: "Deleted transactional records are retained in older snapshots for point-in-time snapshot recovery. Without automated expiry and orphan removal, cold S3 storage files accumulate indefinitely.",
      current: `${reclaimGb} GB reclaimable storage (approx. $${savingsUsd.toFixed(2)}/month potential savings across ${(stats?.total_snapshots ?? 0)} snapshots)`,
      ideal: "< 50 snapshots & near-zero orphaned storage"
    };
  }
  
  // Fallback
  const ratio = statsRaw?.metadata_to_data_ratio ?? 0;
  return {
    what: "Iceberg Metadata Overhead & Layout Balance",
    why: "Imbalanced metadata-to-data sizes indicate rapid committing patterns or suboptimal transaction volumes, which slows down snapshot reads and partition listing speed.",
    current: `Metadata ratio: ${(ratio * 100).toFixed(1)}% (Skew ratio: ${(stats?.partition_skew_ratio ?? 0).toFixed(1)}x)`,
    ideal: "< 5.0% metadata ratio & < 10x partition skew"
  };
};

export const TableDetail = () => {
  const { tableId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { api, token, role } = useAuth();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [queuedScanRunId, setQueuedScanRunId] = useState<string | null>(null);
  const [queuedMaintenanceRunId, setQueuedMaintenanceRunId] = useState<string | null>(null);
  const [protectedLocal, setProtectedLocal] = useState<Array<{ snapshot_id: number; label?: string | null }>>([]);

  useEffect(() => {
    setQueuedScanRunId(null);
    setQueuedMaintenanceRunId(null);
    setProtectedLocal([]);
  }, [tableId]);

  const table = useQuery({ queryKey: ["table", tableId, token], queryFn: () => getTable(api, tableId) });
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
      const preset =
        presets.data?.find((item) => item.id === effectivePreset.data?.preset_id) ?? presets.data?.[0];
      const destinationId = preset?.destination_id ?? destinations.data?.[0]?.id;
      if (!destinationId) throw new Error("No engine configured. Add one under Connections.");
      return runManifestRewrite(api, {
        table_ids: [tableId],
        destination_id: destinationId,
        preset_id: preset?.id,
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
      const preset =
        presets.data?.find((item) => item.id === effectivePreset.data?.preset_id) ?? presets.data?.[0];
      const destinationId = preset?.destination_id ?? destinations.data?.[0]?.id;
      if (!destinationId) {
        throw new Error("No engine configured. Add one under Connections or run make seed-config.");
      }
      return runMaintenance(api, {
        table_ids: [tableId],
        destination_id: destinationId,
        preset_id: preset?.id,
        mode: preset?.mode ?? "standard",
        trigger: "web-detail"
      });
    },
    onSuccess: (result) => {
      message.success(`Run queued: ${result.run_id}`);
      setQueuedMaintenanceRunId(result.run_id);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["cost-dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error) => {
      setQueuedMaintenanceRunId(null);
      message.error((error as Error).message);
    }
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

  const tabItems = [
    {
      key: "schema",
      label: (
        <span>
          <TableOutlined />
          Schema
        </span>
      ),
      children: (
        <div className="td-tab-content">
          <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
            <div className="td-chart-card-header">
              <span className="td-chart-card-title">Columns & Schema Metrics</span>
              <span className="td-chart-card-subtitle">
                {mergedColumns.length} columns detected
              </span>
            </div>
            <div className="td-chart-card-content">
              <Table
                size="small"
                rowKey="name"
                dataSource={mergedColumns}
                loading={table.isLoading || pii.isLoading || dataQuality.isLoading}
                pagination={{ pageSize: 12, size: "small" }}
                className="td-schema-table"
                columns={[
                  {
                    title: "Name",
                    dataIndex: "name",
                    width: 220,
                    sorter: (a, b) => a.name.localeCompare(b.name),
                    render: (name, item) => (
                      <div className="td-schema-col-name-wrap">
                        <span className="td-schema-col-name">{name}</span>
                        {item.isPartition ? (
                          <Tag color="blue" className="td-schema-badge td-schema-badge-partition">
                            Partition Key
                          </Tag>
                        ) : null}
                        {item.piiCategory && item.piiCategory !== "NONE" ? (
                          <Tag color="warning" className="td-schema-badge td-schema-badge-pii">
                            <LockOutlined style={{ marginRight: 3 }} />
                            PII: {item.piiCategory}
                          </Tag>
                        ) : null}
                      </div>
                    )
                  },
                  {
                    title: "Type",
                    dataIndex: "type",
                    width: 140,
                    render: (type) => {
                      const lower = String(type || "unknown").toLowerCase();
                      let colorClass = "td-type-other";
                      if (["int", "integer", "bigint", "double", "float", "decimal", "long", "short"].some(t => lower.includes(t))) {
                        colorClass = "td-type-numeric";
                      } else if (["string", "varchar", "char", "text"].some(t => lower.includes(t))) {
                        colorClass = "td-type-textual";
                      } else if (["timestamp", "date", "time", "datetime"].some(t => lower.includes(t))) {
                        colorClass = "td-type-temporal";
                      } else if (lower.includes("bool")) {
                        colorClass = "td-type-boolean";
                      }
                      return <span className={`td-type-badge ${colorClass}`}>{type || "unknown"}</span>;
                    }
                  },
                  {
                    title: "Nullability",
                    dataIndex: "nullRate",
                    align: "left",
                    width: 200,
                    sorter: (a, b) => (a.nullRate ?? 0) - (b.nullRate ?? 0),
                    render: (nullRate, item) => {
                      const rate = nullRate != null ? nullRate * 100 : 0;
                      const popRate = 100 - rate;
                      return (
                        <div className="td-schema-null-wrap">
                          <div className="td-schema-null-bar-container">
                            <div className="td-schema-null-bar-pop" style={{ width: `${popRate}%` }} />
                            <div className="td-schema-null-bar-null" style={{ width: `${rate}%` }} />
                          </div>
                          <span className="td-schema-null-text">
                            {(rate).toFixed(1)}% null
                          </span>
                          {item.dqNullSpike ? (
                            <Tag color="error" className="td-schema-badge-spike animate-pulse">
                              <WarningOutlined style={{ marginRight: 3 }} />
                              Spike
                            </Tag>
                          ) : null}
                        </div>
                      );
                    }
                  },
                  {
                    title: "Value Range / Bounds",
                    dataIndex: "lowerBound",
                    align: "left",
                    width: 220,
                    render: (_, item) => {
                      const hasBounds = item.lowerBound !== undefined && item.upperBound !== undefined && item.lowerBound !== null && item.upperBound !== null;
                      if (!hasBounds) return <span className="td-bounds-empty">—</span>;
                      return (
                        <div className="td-schema-bounds">
                          <span className="td-bounds-text" title={`${item.lowerBound} to ${item.upperBound}`}>
                            {String(item.lowerBound)} <span className="td-bounds-sep">to</span> {String(item.upperBound)}
                          </span>
                          {item.dqValueRangeChanged ? (
                            <span className="td-bounds-warning" title={item.dqValueRangeExplanation || "Value range shifted"}>
                              <WarningOutlined style={{ color: "var(--warning, #f0a020)", marginLeft: 6 }} />
                            </span>
                          ) : null}
                        </div>
                      );
                    }
                  },
                  {
                    title: "Status",
                    key: "status",
                    align: "center",
                    width: 110,
                    render: (_, item) => {
                      const hasIssues = item.dqNullSpike || item.dqValueRangeChanged;
                      return (
                        <Tag color={hasIssues ? "warning" : "success"} className="td-schema-status-tag">
                          {hasIssues ? "Warning" : "Healthy"}
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
        </div>
      ),
      forceRender: true
    },
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
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={14}>
              <Card className={`td-chart-card${trendData.length ? "" : " td-chart-card-quiet"}`} classNames={{ body: "td-chart-card-body" }} style={{ marginBottom: 16 }}>
                <div className="td-chart-card-header">
                  <span className="td-chart-card-title">Health trends</span>
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
                          <Area yAxisId="left" type="monotone" dataKey="score" fill="url(#scoreGrad2)" stroke="none" />
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
                          borderTop: "1px solid var(--line, #e1e6ed)",
                          borderRight: "1px solid var(--line, #e1e6ed)",
                          borderBottom: "1px solid var(--line, #e1e6ed)",
                          borderLeft: `4px solid ${colCheck.status === "optimal" ? "var(--success, #28a745)" : "var(--warning, #f0a020)"}`,
                          backgroundColor: colCheck.status === "optimal" ? "rgba(40, 167, 69, 0.01)" : "rgba(240, 160, 32, 0.01)",
                          padding: "14px 16px",
                          borderRadius: "0 8px 8px 0",
                          display: "flex",
                          flexDirection: "column",
                          gap: 10
                        }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 12 }}>
                              {colCheck.status === "optimal" ? (
                                <CheckCircleOutlined style={{ color: "var(--success, #28a745)", fontSize: 13 }} />
                              ) : (
                                <WarningOutlined style={{ color: "var(--warning, #f0a020)", fontSize: 13 }} />
                              )}
                              <span style={{ color: "var(--foreground, #1a202c)" }}>Check 1: Target Column Alignment</span>
                            </div>
                            <Tag color={colCheck.tagColor} style={{ margin: 0, fontSize: 10, fontWeight: 700, borderRadius: 4 }}>
                              {colCheck.tagText}
                            </Tag>
                          </div>
                          
                          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", padding: "10px 12px", borderRadius: 6, backgroundColor: "var(--background, #ffffff)", border: "1px dashed var(--line, #e1e6ed)", fontSize: 11 }}>
                            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                              <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Metric / Condition</span>
                              <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 700 }}>{colCheck.what}</span>
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                              <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Impact Description</span>
                              <span style={{ color: "var(--muted, #5c667a)", fontSize: 10, lineHeight: "14px" }}>{colCheck.why}</span>
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 2, borderTop: "1px solid var(--line, #e1e6ed)", paddingTop: 8 }}>
                              <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Current Spec</span>
                              <span><Tag style={{ margin: 0 }}>{colCheck.current}</Tag></span>
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 2, borderTop: "1px solid var(--line, #e1e6ed)", paddingTop: 8 }}>
                              <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Recommended Optimal Spec</span>
                              <span><Tag color="blue" style={{ margin: 0 }}>{colCheck.ideal}</Tag></span>
                            </div>
                          </div>
                        </div>

                        {/* Checklist row 2: Granularity */}
                        <div style={{
                          borderTop: "1px solid var(--line, #e1e6ed)",
                          borderRight: "1px solid var(--line, #e1e6ed)",
                          borderBottom: "1px solid var(--line, #e1e6ed)",
                          borderLeft: `4px solid ${granCheck.status === "optimal" ? "var(--success, #28a745)" : granCheck.status === "critical" ? "var(--error, #dc3545)" : "var(--warning, #f0a020)"}`,
                          backgroundColor: granCheck.status === "optimal" ? "rgba(40, 167, 69, 0.01)" : granCheck.status === "critical" ? "rgba(220, 53, 69, 0.01)" : "rgba(240, 160, 32, 0.01)",
                          padding: "14px 16px",
                          borderRadius: "0 8px 8px 0",
                          display: "flex",
                          flexDirection: "column",
                          gap: 10
                        }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 12 }}>
                              {granCheck.status === "optimal" ? (
                                <CheckCircleOutlined style={{ color: "var(--success, #28a745)", fontSize: 13 }} />
                              ) : granCheck.status === "critical" ? (
                                <CloseCircleOutlined style={{ color: "var(--error, #dc3545)", fontSize: 13 }} />
                              ) : (
                                <WarningOutlined style={{ color: "var(--warning, #f0a020)", fontSize: 13 }} />
                              )}
                              <span style={{ color: "var(--foreground, #1a202c)" }}>Check 2: Partition Granularity Validation</span>
                            </div>
                            <Tag color={granCheck.tagColor} style={{ margin: 0, fontSize: 10, fontWeight: 700, borderRadius: 4 }}>
                              {granCheck.tagText}
                            </Tag>
                          </div>
                          
                          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", padding: "10px 12px", borderRadius: 6, backgroundColor: "var(--background, #ffffff)", border: "1px dashed var(--line, #e1e6ed)", fontSize: 11 }}>
                            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                              <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Metric / Condition</span>
                              <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 700 }}>{granCheck.what}</span>
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                              <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Impact Description</span>
                              <span style={{ color: "var(--muted, #5c667a)", fontSize: 10, lineHeight: "14px" }}>{granCheck.why}</span>
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 2, borderTop: "1px solid var(--line, #e1e6ed)", paddingTop: 8 }}>
                              <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Current Ingestion State</span>
                              <span><Tag style={{ margin: 0 }}>{granCheck.current}</Tag></span>
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 2, borderTop: "1px solid var(--line, #e1e6ed)", paddingTop: 8 }}>
                              <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Target Healthy Bounds</span>
                              <span><Tag color="blue" style={{ margin: 0 }}>{granCheck.ideal}</Tag></span>
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
                  <span className="td-chart-card-title">Diagnosis & Score Factors</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 16 }}>
                  <HealthGauge
                    score={score}
                    trendDirection={trendDirection}
                    trendLabel={trendLabel}
                    daysToCritical={healthTrends.data?.days_to_critical}
                  />
                </div>
                {row ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                    <Alert
                      type={alertType}
                      showIcon
                      title={<strong>{verdict.message}</strong>}
                      style={{ fontSize: 11 }}
                    />
                    <div>
                      <div className="td-diagnosis-section-title" style={{ fontWeight: 600, fontSize: 11, marginBottom: 8, color: "var(--muted, #5c667a)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Score Factors</div>
                      <div className="td-sidebar-score-bars" style={{ marginBottom: 12 }}>
                        <HealthScoreBreakdown factors={scoreFactors} />
                      </div>
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
                          border: score < 60 ? '1px solid rgba(220, 53, 69, 0.12)' : score < 80 ? '1px solid rgba(240, 160, 32, 0.15)' : '1px solid var(--line, #e1e6ed)',
                          marginBottom: 14
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
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                        <div className="td-diagnosis-section-title" style={{ fontWeight: 600, fontSize: 11, margin: 0, color: "var(--muted, #5c667a)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Detected Health Issues & Remediation</div>
                        {triggered.length > 0 ? (
                          <Badge
                            count={triggered.length}
                            style={{ backgroundColor: "var(--error, #dc3545)" }}
                            size="small"
                          />
                        ) : null}
                      </div>
                      <QueryErrorAlert error={tableSignals.error} title="Failed to load table signals" />
                      {tableSignals.isLoading ? (
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>Checking diagnostic signals…</Typography.Text>
                      ) : triggered.length ? (
                        <div className="td-diagnosis-signals" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                          {triggered.map((signal, idx) => {
                            const details = getSignalPointerDetail(signal.signal_name, signal.severity);
                            const total_files = stats?.total_files ?? 0;
                            const small_files_count = stats?.small_files_count ?? 0;
                            const small_pct = total_files > 0 ? (small_files_count / total_files) * 100 : 0;
                            const matrix = getSignalMatrix(signal.signal_name, stats, statsRaw, reclaimableBytes, savingsUsd, small_pct);
                            const borderLeftColor = signal.severity === "critical" ? "var(--error, #dc3545)" : "var(--warning, #f0a020)";
                            const bgStyleColor = signal.severity === "critical" ? "rgba(220, 53, 69, 0.015)" : "rgba(240, 160, 32, 0.015)";
                            
                            return (
                              <div
                                key={signal.signal_name ?? `signal-${idx}`}
                                className={`td-diagnosis-signal-row td-diagnosis-signal-${signal.severity}`}
                                style={{
                                  borderLeft: `4px solid ${borderLeftColor}`,
                                  backgroundColor: bgStyleColor,
                                  padding: '14px 16px',
                                  borderRadius: '0 8px 8px 0',
                                  boxShadow: '0 1px 4px rgba(0,0,0,0.02)',
                                  border: '1px solid var(--line, #e1e6ed)',
                                  borderLeftColor: borderLeftColor,
                                  display: "flex",
                                  flexDirection: "column",
                                  gap: 12
                                }}
                              >
                                {/* 1. Header with Title & Impact Tag */}
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                                  <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13 }}>
                                    {signal.severity === "critical" ? (
                                      <CloseCircleOutlined style={{ color: "var(--error, #dc3545)", fontSize: 14 }} />
                                    ) : (
                                      <WarningOutlined style={{ color: "var(--warning, #f0a020)", fontSize: 14 }} />
                                    )}
                                    <span style={{ color: "var(--foreground, #1a202c)" }}>{details.title}</span>
                                  </div>
                                  <Tag color={signal.severity === "critical" ? "red" : "orange"} style={{ margin: 0, fontSize: 10, fontWeight: 700, borderRadius: 4 }}>
                                    {details.impactText}
                                  </Tag>
                                </div>

                                {/* 2. Metrics & Explanation Matrix */}
                                <div style={{
                                  display: "grid",
                                  gridTemplateColumns: "1fr 1fr",
                                  gap: "10px",
                                  padding: "10px 12px",
                                  borderRadius: 6,
                                  backgroundColor: "var(--background, #ffffff)",
                                  border: "1px dashed var(--line, #e1e6ed)",
                                  fontSize: 11
                                }}>
                                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                                    <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Metric Name</span>
                                    <span style={{ color: "var(--foreground, #1a202c)", fontWeight: 700 }}>{matrix.what}</span>
                                  </div>
                                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                                    <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Impact Description</span>
                                    <span style={{ color: "var(--muted, #5c667a)", fontSize: 10, lineHeight: "14px" }}>{matrix.why}</span>
                                  </div>
                                  <div style={{ display: "flex", flexDirection: "column", gap: 2, borderTop: "1px solid var(--line, #e1e6ed)", paddingTop: 8 }}>
                                    <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Current State</span>
                                    <span style={{ color: signal.severity === "critical" ? "var(--error, #dc3545)" : "var(--warning, #f0a020)", fontWeight: 700 }}>{matrix.current}</span>
                                  </div>
                                  <div style={{ display: "flex", flexDirection: "column", gap: 2, borderTop: "1px solid var(--line, #e1e6ed)", paddingTop: 8 }}>
                                    <span style={{ fontWeight: 600, color: "var(--muted, #5c667a)", textTransform: "uppercase", fontSize: 9, letterSpacing: "0.5px" }}>Ideal Target</span>
                                    <span style={{ color: "var(--success, #28a745)", fontWeight: 700 }}>{matrix.ideal}</span>
                                  </div>
                                </div>

                                {/* 3. Recommendation block */}
                                <div style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 11 }}>
                                  <ToolOutlined style={{ color: "var(--primary, #3d6fd4)", marginTop: 2, fontSize: 13 }} />
                                  <span style={{ color: "var(--foreground, #1a202c)", lineHeight: '16px' }}>
                                    <strong style={{ color: "var(--primary, #3d6fd4)" }}>Action Recommended:</strong> {details.recommendation}
                                  </span>
                                </div>

                                {/* 4. Action Button Bar (Fix & Engine controls) */}
                                <div style={{
                                  marginTop: 4,
                                  paddingTop: 8,
                                  borderTop: "1px dashed var(--line, #e1e6ed)",
                                  display: "flex",
                                  justifyContent: "space-between",
                                  alignItems: "center",
                                  gap: 12
                                }}>
                                  {/* Preset/Engine Selector */}
                                  <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                                    <span style={{ fontSize: 10, color: "var(--muted, #5c667a)", fontWeight: 600, flexShrink: 0 }}>Engine Preset:</span>
                                    <Select
                                      placeholder="Assign Preset"
                                      value={effectivePreset.data?.preset_id ?? undefined}
                                      options={(presets.data ?? []).map((p) => ({ label: p.name, value: p.id }))}
                                      onChange={(presetId) => assign.mutate(presetId)}
                                      loading={assign.isPending || presets.isLoading}
                                      disabled={!row}
                                      size="small"
                                      style={{ width: 150 }}
                                      popupMatchSelectWidth={false}
                                    />
                                  </div>

                                  {/* Quick Action Trigger */}
                                  <div style={{ flexShrink: 0 }}>
                                    {queuedMaintenanceRunId || run.isPending ? (
                                      <Button
                                        type="primary"
                                        size="small"
                                        icon={<ThunderboltOutlined />}
                                        loading={run.isPending}
                                        onClick={handleRunAction}
                                        disabled={!row || run.isPending || !queuedMaintenanceRunId}
                                      >
                                        {run.isPending ? "Queueing..." : "Open Run"}
                                      </Button>
                                    ) : (
                                      <ConfirmAction
                                        title="Execute Maintenance Job?"
                                        description={`This will trigger compaction and optimization for ${row ? `${row.schema_name}.${row.table_name}` : 'this table'} using the assigned engine preset.`}
                                        onConfirm={() => run.mutate()}
                                      >
                                        <Button
                                          type="primary"
                                          size="small"
                                          icon={<ThunderboltOutlined />}
                                          disabled={!row}
                                          aria-label="Fix Now"
                                          style={{ backgroundColor: "var(--success, #28a745)", borderColor: "var(--success, #28a745)" }}
                                        >
                                          Fix Now
                                        </Button>
                                      </ConfirmAction>
                                    )}
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      ) : tableSignals.data ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: '12px 14px', borderRadius: 6, border: '1px solid var(--line, #e1e6ed)', backgroundColor: 'rgba(40, 167, 69, 0.03)' }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, fontSize: 12, color: "var(--success, #28a745)" }}>
                            <CheckCircleOutlined style={{ color: "var(--success, #28a745)" }} />
                            <span>Diagnostic Profile Healthy</span>
                          </div>
                          <div style={{ fontSize: 11, color: "var(--muted, #5c667a)", lineHeight: '16px', display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
                            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                              <CheckCircleOutlined style={{ fontSize: 10, color: "var(--success, #28a745)" }} />
                              <span>[Fragmentation Check] Healthy — file ratios are under threshold.</span>
                            </div>
                            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                              <CheckCircleOutlined style={{ fontSize: 10, color: "var(--success, #28a745)" }} />
                              <span>[Latency Check] Healthy — scan latency conforms to standard baseline.</span>
                            </div>
                            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                              <CheckCircleOutlined style={{ fontSize: 10, color: "var(--success, #28a745)" }} />
                              <span>[Cost Spike Check] Healthy — S3 storage overhead is fully optimized.</span>
                            </div>
                          </div>
                          <Typography.Text type="secondary" style={{ fontSize: 10, display: "block", marginTop: 4, borderTop: '1px solid var(--line, #e1e6ed)', paddingTop: 6 }}>
                            All {tableSignals.data.signals.length} background telemetry signals checks completed, 0 alarms triggered.
                          </Typography.Text>
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : (
                  <EmptyState title="No health details" description="Fetch or scan table to load diagnostics." />
                )}
              </Card>
            </Col>
          </Row>
        </div>
      ),
      forceRender: true
    },
    {
      key: "cost",
      label: (
        <span>
          <DollarOutlined />
          Cost & Savings
        </span>
      ),
      children: (
        <div className="td-tab-content">
          <Card className={`td-chart-card${costView || hasStatsCostFallback ? "" : " td-chart-card-quiet"}`} classNames={{ body: "td-chart-card-body" }}>
            <div className="td-chart-card-header">
              <span className="td-chart-card-title">Cost & Savings</span>
              <CostPrecisionBadge
                precision={costView?.precision ?? stats?.cost_precision}
                lastFullCostScanAt={costView?.last_full_cost_scan_at ?? stats?.cost_last_full_scan_at}
                orphanScanStale={isOrphanScanStale(
                  costView?.precision ?? stats?.cost_precision,
                  costView?.last_full_cost_scan_at ?? stats?.cost_last_full_scan_at
                )}
              />
            </div>
            <div className="td-chart-card-content">
              {costView || hasStatsCostFallback ? (
                <Row gutter={[16, 16]}>
                  <Col xs={24} xl={12}>
                    <div className="td-cost-summary" style={{ paddingLeft: 0, paddingRight: 0, marginBottom: 12 }}>
                      <div className="td-cost-row">
                        <span className="td-cost-delta">
                          <span className="td-cost-label">Current</span>
                          <span className="td-cost-value">{formatCurrency(currentCostUsd)}</span>
                        </span>
                        <span className="td-cost-arrow">→</span>
                        <span className="td-cost-delta td-cost-projected">
                          <span className="td-cost-label">Projected</span>
                          <span className="td-cost-value">{formatCurrency(projectedCostUsd)}</span>
                        </span>
                        <span className="td-cost-save">
                          Save <strong>{formatCurrency(savingsUsd)}</strong>/mo
                        </span>
                      </div>
                    </div>
                    {costView ? (
                      <StorageDonut
                        liveBytes={costView.live_bytes}
                        deadBytes={costView.dead_bytes}
                        metadataBytes={costView.metadata_bytes}
                        orphanBytes={costView.orphan_bytes}
                        pricePerGbMonth={costView.price_per_gb_month}
                      />
                    ) : null}
                  </Col>
                  <Col xs={24} xl={12}>
                    {costByOperation.length ? (
                      <CostSavingsWaterfall
                        currentCostUsd={currentCostUsd}
                        currentStorageBytes={storageBytes || (stats?.total_size_gb ?? 0) * 1024 ** 3}
                        byOperation={costByOperation}
                      />
                    ) : null}
                    {costByOperation.length ? <SavingsByOperation items={costByOperation} /> : null}
                  </Col>
                  {s3Tiering.data ? (
                    <Col span={24}>
                      <Tag color="processing">{s3Tiering.data.recommendation}</Tag>
                      <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                        {s3Tiering.data.reason}
                      </Typography.Text>
                    </Col>
                  ) : null}
                </Row>
              ) : (
                <EmptyState title="No cost breakdown yet" description="Cost breakdown appears after the scanner writes table_cost." />
              )}
            </div>
          </Card>
        </div>
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
                                {dataQuality.data.new_columns.map((c) => (
                                  <Tag color="green" key={c}>{c}</Tag>
                                ))}
                              </div>
                            ) : null}
                            {dataQuality.data.dropped_columns.length ? (
                              <div>
                                <strong className="td-quality-alert-removed">Dropped columns:</strong>{" "}
                                {dataQuality.data.dropped_columns.map((c) => (
                                  <Tag color="red" key={c}>{c}</Tag>
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
      children: (
        <div className="td-tab-content">
          <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
            <div className="td-chart-card-header">
              <span className="td-chart-card-title">Recent activity</span>
              <span className="td-chart-card-subtitle">
                {audit.data?.length ? `Last ${Math.min(audit.data.length, 12)} events` : null}
              </span>
            </div>
            <div className="td-chart-card-content">
              {(audit.data ?? []).length ? (
                <div className="td-timeline td-timeline-wide">
                  {(audit.data ?? []).slice(0, 12).map((item: AuditEntry, index) => {
                    const failed = item.status === "failed";
                    return (
                      <div key={item.id ?? index} className={`td-timeline-item ${failed ? "td-timeline-failed" : ""}`}>
                        <div className="td-timeline-dot-wrap">
                          <span className={`td-timeline-dot ${failed ? "td-timeline-dot-fail" : "td-timeline-dot-ok"}`} />
                          {index < Math.min(audit.data?.length ?? 0, 12) - 1 ? (
                            <span className="td-timeline-line" />
                          ) : null}
                        </div>
                        <div className="td-timeline-content">
                          <div className="td-timeline-head">
                            <span className="td-timeline-icon">{actionIcon(item.action ?? "maintenance", failed)}</span>
                            <span className="td-timeline-action">{item.action ?? "maintenance"}</span>
                            {failed ? (
                              <Tag
                                color="error"
                                style={{ fontSize: 10, lineHeight: "16px", padding: "0 4px", margin: 0 }}
                              >
                                failed
                              </Tag>
                            ) : null}
                          </div>
                          <div className="td-timeline-detail">
                            {formatTime(item.ts ?? item.created_at)}
                            {item.engine ? <> · {item.engine}</> : null}
                            {item.duration_ms ? <> · {formatDuration(item.duration_ms)}</> : null}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <EmptyState title="No audit entries" description="Completed maintenance actions will appear here." />
              )}
            </div>
          </Card>
        </div>
      ),
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
      {/* 1. Deep Breadcrumbs & Contextual Hierarchy */}
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

      {/* 2. Header Title & Intentional Interaction Toolbar */}
      <div className="td-header-row">
        <div className="td-header-info">
          <Typography.Title level={3} className="td-header-title">
            {row ? `${row.schema_name}.${row.table_name}` : "Loading..."}
          </Typography.Title>
          <div className="td-header-tags">
            {stats?.grade ? <GradePill grade={stats.grade} /> : null}
            {stats?.compaction_priority ? <PriorityTag priority={stats.compaction_priority} /> : null}
            <CompactionTag needsCompaction={stats?.needs_compaction} />
          </div>
        </div>

        {/* Intentional Interaction Toolbar */}
        <div className="td-interaction-toolbar">
          <Select
            placeholder="Assign preset"
            value={effectivePreset.data?.preset_id ?? undefined}
            options={(presets.data ?? []).map((p) => ({ label: p.name, value: p.id }))}
            onChange={(presetId) => assign.mutate(presetId)}
            loading={assign.isPending || presets.isLoading}
            disabled={!row}
            style={{ width: 150 }}
            size="small"
            className="td-toolbar-select"
          />
          <Button size="small" onClick={() => setPreviewOpen(true)} disabled={!row} className="td-toolbar-btn">
            Preview
          </Button>
          <Button
            size="small"
            type={queuedScanRunId ? "primary" : "default"}
            className={scan.isPending ? "scan-action-pending td-toolbar-btn" : "td-toolbar-btn"}
            loading={scan.isPending}
            onClick={handleScanAction}
            disabled={!row || scan.isPending}
            aria-label={scan.isPending ? "Queueing stats refresh" : queuedScanRunId ? "Open scan run" : "Refresh stats"}
          >
            {scan.isPending ? "Queueing stats refresh…" : queuedScanRunId ? "Open scan run" : "Refresh stats"}
          </Button>
          {queuedMaintenanceRunId || run.isPending ? (
            <Button
              type="primary"
              size="small"
              className={run.isPending ? "scan-action-pending td-toolbar-btn" : "td-toolbar-btn"}
              loading={run.isPending}
              onClick={handleRunAction}
              disabled={!row || run.isPending || !queuedMaintenanceRunId}
              aria-label={
                run.isPending
                  ? "Queueing maintenance run"
                  : queuedMaintenanceRunId
                    ? "Open maintenance run"
                    : "Run maintenance"
              }
            >
              {run.isPending ? "Queueing run…" : "Open run"}
            </Button>
          ) : (
            <ConfirmAction
              title="Run maintenance?"
              description="Queues a real maintenance job using the assigned preset and destination."
              onConfirm={() => run.mutate()}
            >
              <Button type="primary" size="small" disabled={!row} aria-label="Run maintenance" className="td-toolbar-btn td-toolbar-btn-run">
                Run
              </Button>
            </ConfirmAction>
          )}
          <ConfirmAction
            title="Rewrite manifests?"
            description="Queues manifest-only maintenance without data file compaction."
            onConfirm={() => manifestRewrite.mutate()}
          >
            <Button size="small" loading={manifestRewrite.isPending} disabled={!row} className="td-toolbar-btn">
              Manifest
            </Button>
          </ConfirmAction>
          {effectivePreset.data?.preset_id ? (
            <ConfirmAction
              title="Unassign preset?"
              description="Clears the manual preset assignment."
              onConfirm={() => unassign.mutate()}
            >
              <Button size="small" loading={unassign.isPending} disabled={!row} className="td-toolbar-btn">
                Unassign
              </Button>
            </ConfirmAction>
          ) : null}
        </div>
      </div>

      {/* 3. Quick-Attribution Bar */}
      <div className="td-quick-attribution-bar">
        <div className="td-attribution-item">
          <span className="td-attribution-label">Health Score</span>
          <span className="td-attribution-value td-health-score" style={{ color: healthScoreColor(score) }}>
            {formatNumber(score)}
          </span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Total Size</span>
          <span className="td-attribution-value">{formatGb(stats?.total_size_gb)}</span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Records</span>
          <span className="td-attribution-value">{formatNumber(stats?.total_records, true)}</span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Partition col</span>
          <span className="td-attribution-value" title={stats?.partition_col ?? "-"}>
            {stats?.partition_col ?? "-"}
            {stats?.partition_col_type ? (
              <span className="td-attribution-subtle">
                ({stats.partition_col_type})
              </span>
            ) : null}
          </span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Savings / Mo</span>
          <span className="td-attribution-value td-savings-value">
            {formatCurrency(savingsUsd)}
            {reclaimableBytes > 0 ? (
              <span className="td-attribution-subtle">
                {formatBytesGb(reclaimableBytes)} rec.
              </span>
            ) : null}
          </span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Partitions</span>
          <span className="td-attribution-value">{formatNumber(stats?.total_partitions)}</span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Files</span>
          <span className="td-attribution-value">{formatNumber(stats?.total_files, true)}</span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
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
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Snapshots</span>
          <span className="td-attribution-value">{formatNumber(stats?.total_snapshots)}</span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Manifests</span>
          <span className="td-attribution-value">{formatNumber(stats?.manifest_count)}</span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Delete files</span>
          <span className="td-attribution-value">{formatNumber(stats?.total_delete_files)}</span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Avg file size</span>
          <span className="td-attribution-value">
            {stats?.avg_file_size_mb ? `${stats.avg_file_size_mb.toFixed(1)} MB` : "-"}
          </span>
        </div>
        <div className="td-attribution-divider" />
        <div className="td-attribution-item">
          <span className="td-attribution-label">Partition skew</span>
          <span className="td-attribution-value">
            {stats?.partition_skew_ratio != null ? `${stats.partition_skew_ratio.toFixed(2)}x` : "-"}
          </span>
        </div>
      </div>

      {/* 4. 70/30 Asymmetric Split-Screen Layout */}
      <Row gutter={[24, 24]} className="td-split-layout">
        {/* LEFT COLUMN: Main Interactive Workspace (70%) */}
        <Col xs={24} lg={17} className="td-main-column">
          <RecommendedActions
            costView={costView}
            signals={tableSignals.data}
            benefitScores={benefitScores.data}
            onPreview={() => setPreviewOpen(true)}
            onRun={() => run.mutate()}
          />

          <Tabs defaultActiveKey="schema" items={tabItems} className="td-main-tabs" />
        </Col>

        {/* RIGHT COLUMN: Metadata & Compliance Sidebar (30%) */}
        <Col xs={24} lg={7} className="td-sidebar-column">
          <div className="td-sidebar-sticky">

            {/* Card 2: Configuration & Engine Status */}
            <Card className="td-sidebar-card" title="Preset & Engine" size="small">
              <div className="td-sidebar-meta-list">
                <div className="td-sidebar-meta-item">
                  <span className="td-sidebar-meta-label">Active Preset</span>
                  <span className="td-sidebar-meta-value">
                    <strong>{effectivePreset.data?.preset_name ?? "None"}</strong>
                    <span className="td-meta-faint" style={{ fontSize: 10, marginLeft: 4 }}>
                      ({provenanceLabel(effectivePreset.data?.provenance)})
                    </span>
                  </span>
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
              </div>
            </Card>

            {/* Card 3: Compliance & Snapshot Protection */}
            <Card className="td-sidebar-card" title="Compliance & Protection" size="small">
              <div className="td-sidebar-compliance-wrap">
                {compliance.data ? (
                  <Descriptions size="small" column={1} className="td-meta-desc">
                    <Descriptions.Item label="Table Ref">
                      <span className="td-sidebar-desc-val">{compliance.data.table_ref}</span>
                    </Descriptions.Item>
                    <Descriptions.Item label="ABAC count">
                      <Badge count={compliance.data.abac_policy_count} showZero style={{ backgroundColor: 'var(--primary, #3d6fd4)' }} />
                    </Descriptions.Item>
                    <Descriptions.Item label="Deletion delay">
                      <PendingDeletionTag pending={Boolean(compliance.data.pending_deletion)} />
                    </Descriptions.Item>
                    {compliance.data.abac_enforcement_note ? (
                      <Descriptions.Item label="ABAC Note">
                        <span className="td-sidebar-desc-val">{compliance.data.abac_enforcement_note}</span>
                      </Descriptions.Item>
                    ) : null}
                    {compliance.data.pending_deletion ? (
                      <Descriptions.Item label="Deletion Blocks">
                        <span className="td-sidebar-desc-val">
                          {compliance.data.pending_deletion.pending_rows?.toLocaleString() ?? 0} rows blocked
                          {compliance.data.pending_deletion.blocked_until
                            ? ` until ${formatTime(compliance.data.pending_deletion.blocked_until)}`
                            : ""}
                        </span>
                      </Descriptions.Item>
                    ) : null}
                  </Descriptions>
                ) : compliance.isLoading ? (
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>Loading compliance status…</Typography.Text>
                ) : (
                  <EmptyState title="Compliance unavailable" description="Could not load compliance status." />
                )}

                {(role === "admin" || role === "operator") ? (
                  <div className="td-sidebar-protect-form" style={{ marginTop: 14, paddingTop: 14, borderTop: "1.5px solid var(--line, #e1e6ed)" }}>
                    <Typography.Text strong style={{ fontSize: 11, display: "block", marginBottom: 6 }}>Protect snap</Typography.Text>
                    <Form
                      layout="vertical"
                      onFinish={(values: { snapshot_id: number; label?: string }) => protect.mutate(values)}
                    >
                      <Row gutter={6}>
                        <Col span={10}>
                          <Form.Item
                            name="snapshot_id"
                            rules={[{ required: true, message: "Req" }]}
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
                  <Typography.Text strong style={{ fontSize: 11, display: "block", marginBottom: 4 }}>Protected snapshots</Typography.Text>
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
          </div>
        </Col>
      </Row>

      <PlanPreview open={previewOpen} tables={row ? [row] : []} onClose={() => setPreviewOpen(false)} />
    </div>
  );
};
