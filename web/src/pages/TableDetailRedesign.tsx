import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AppstoreOutlined,
  ClearOutlined,
  HistoryOutlined,
  ThunderboltOutlined,
  WarningOutlined
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
  getTable,
  getTableAudit,
  getTableBenefitScores,
  getTableCost,
  getTableHealthTrends,
  getTablePartitions,
  getTableS3Tiering,
  getTableSignals,
  listDestinations,
  listPresets,
  protectSnapshot,
  runMaintenance,
  runManifestRewrite,
  triggerScan,
  unassignTables
} from "../api/client";
import type { AuditEntry, PartitionRead } from "../api/types";
import { EmptyState } from "../components/common";
import { ConfirmAction } from "../components/confirm";
import { CostPrecisionBadge } from "../components/CostPrecisionBadge";
import { CostSavingsWaterfall } from "../components/CostSavingsWaterfall";
import { buildHealthScoreFactors } from "../components/HealthScoreBreakdown";
import { SavingsByOperation } from "../components/SavingsByOperation";
import { PlanPreview } from "../components/PlanPreview";
import { TableAiInsights } from "../components/TableAiInsights";
import { TableCompactionAnalytics } from "../components/TableCompactionAnalytics";
import { TableDiagnosisPanel } from "../components/TableDiagnosisPanel";
import { TableMetadataGrid } from "../components/TableMetadataGrid";
import { HealthGauge } from "../components/table-detail/HealthGauge";
import { KpiStripe } from "../components/table-detail/KpiStripe";
import { PartitionHistogram } from "../components/table-detail/PartitionHistogram";
import { RecommendedActions } from "../components/table-detail/RecommendedActions";
import { SectionNav, type TableDetailSection } from "../components/table-detail/SectionNav";
import { StorageDonut } from "../components/table-detail/StorageDonut";
import {
  CompactionTag,
  GradePill,
  PendingDeletionTag,
  PriorityTag
} from "../components/status";
import { useAuth } from "../lib/auth";
import { isOrphanScanStale } from "../lib/costStaleness";
import {
  formatBytesGb,
  formatCurrency,
  formatDuration,
  formatGb,
  formatNumber,
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

const sections: TableDetailSection[] = [
  { id: "overview", label: "Overview" },
  { id: "cost", label: "Cost" },
  { id: "partitions", label: "Partitions" },
  { id: "compliance", label: "Compliance" },
  { id: "ai", label: "AI" },
  { id: "data-quality", label: "Data quality" },
  { id: "activity", label: "Activity" },
  { id: "metadata", label: "Metadata" }
];

const actionIcon = (action: string, failed: boolean): React.ReactNode => {
  if (failed) return <WarningOutlined />;
  const normalized = action.toLowerCase();
  if (normalized.includes("orphan")) return <ClearOutlined />;
  if (normalized.includes("expire")) return <HistoryOutlined />;
  if (normalized.includes("manifest")) return <AppstoreOutlined />;
  return <ThunderboltOutlined />;
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
  const partitions = useQuery({
    queryKey: ["table-partitions", tableId, token],
    queryFn: () => getTablePartitions(api, tableId),
    retry: 1
  });
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
  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });
  const compliance = useQuery({
    queryKey: ["table-compliance", tableId, token],
    queryFn: () => getComplianceStatus(api, tableId),
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
  const stats = row?.stats;
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

  if (!row && !table.isLoading) {
    return (
      <div className="table-detail">
        <div className="td-identity-bar">
          <div className="td-identity-info">
            <Typography.Title level={3} style={{ margin: 0, color: "inherit" }}>
              Table not found
            </Typography.Title>
            <Typography.Text style={{ color: "var(--sidebar-text, #c7d2de)", opacity: 0.6 }}>
              The table may not have been scanned yet.
            </Typography.Text>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="table-detail">
      <div className="td-hero">
        <div className="td-hero-info">
          <div className="td-hero-title-row">
            <Typography.Title level={3} style={{ margin: 0, color: "inherit" }}>
              {row ? `${row.schema_name}.${row.table_name}` : "Loading..."}
            </Typography.Title>
            {stats?.grade ? <GradePill grade={stats.grade} /> : null}
            {stats?.compaction_priority ? <PriorityTag priority={stats.compaction_priority} /> : null}
            <CompactionTag needsCompaction={stats?.needs_compaction} />
          </div>
          <div className="td-hero-meta">
            <span>
              Preset: <strong>{effectivePreset.data?.preset_name ?? "None"}</strong>
              <span className="td-meta-faint"> ({provenanceLabel(effectivePreset.data?.provenance)})</span>
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
          </div>
        </div>

        <HealthGauge
          score={score}
          trendDirection={trendDirection}
          trendLabel={trendLabel}
          daysToCritical={healthTrends.data?.days_to_critical}
        />

        <div className="td-hero-actions">
          <Select
            placeholder="Assign preset"
            value={effectivePreset.data?.preset_id ?? undefined}
            options={(presets.data ?? []).map((p) => ({ label: p.name, value: p.id }))}
            onChange={(presetId) => assign.mutate(presetId)}
            loading={assign.isPending || presets.isLoading}
            disabled={!row}
            style={{ width: 190 }}
            size="small"
          />
          <Button size="small" onClick={() => setPreviewOpen(true)} disabled={!row}>
            Preview
          </Button>
          <Button
            size="small"
            type={queuedScanRunId ? "primary" : "default"}
            className={scan.isPending ? "scan-action-pending" : undefined}
            loading={scan.isPending}
            onClick={handleScanAction}
            disabled={!row || scan.isPending}
            aria-label={scan.isPending ? "Queueing scan" : queuedScanRunId ? "Open scan run" : "Scan table"}
          >
            {scan.isPending ? "Queueing scan…" : queuedScanRunId ? "Open scan run" : "Scan"}
          </Button>
          {queuedMaintenanceRunId || run.isPending ? (
            <Button
              type="primary"
              size="small"
              className={run.isPending ? "scan-action-pending" : undefined}
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
              <Button type="primary" size="small" disabled={!row} aria-label="Run maintenance">
                Run
              </Button>
            </ConfirmAction>
          )}
          <ConfirmAction
            title="Rewrite manifests?"
            description="Queues manifest-only maintenance without data file compaction."
            onConfirm={() => manifestRewrite.mutate()}
          >
            <Button size="small" loading={manifestRewrite.isPending} disabled={!row}>
              Manifest
            </Button>
          </ConfirmAction>
          {effectivePreset.data?.preset_id ? (
            <ConfirmAction
              title="Unassign preset?"
              description="Clears the manual preset assignment."
              onConfirm={() => unassign.mutate()}
            >
              <Button size="small" loading={unassign.isPending} disabled={!row}>
                Unassign
              </Button>
            </ConfirmAction>
          ) : null}
        </div>
      </div>

      <RecommendedActions
        costView={costView}
        signals={tableSignals.data}
        benefitScores={benefitScores.data}
        onPreview={() => setPreviewOpen(true)}
        onRun={() => run.mutate()}
      />

      <KpiStripe stats={stats} savingsUsd={savingsUsd} reclaimableBytes={reclaimableBytes} />
      <SectionNav sections={sections} />
      <Alert
        className="page-alert"
        type="info"
        showIcon
        message="Action flow: review top recommendations -> validate Data Quality -> preview plan -> run maintenance."
      />

      <section id="overview" className="td-section">
        <Row gutter={[12, 12]}>
          <Col xs={24} xl={14}>
            {row ? <TableDiagnosisPanel tableId={tableId} stats={stats} factors={scoreFactors} /> : null}
          </Col>
          <Col xs={24} xl={10}>
            <Card className={`td-chart-card${trendData.length ? "" : " td-chart-card-quiet"}`} classNames={{ body: "td-chart-card-body" }}>
              <div className="td-chart-card-header">
                <span className="td-chart-card-title">Health trends</span>
                <span className="td-chart-card-subtitle">30 days</span>
                {degradationBadge ? <Tag color="error">{degradationBadge}</Tag> : null}
              </div>
              <div className="td-chart-card-content">
                {trendData.length ? (
                  <div className="chart-frame">
                    <ResponsiveContainer>
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
          </Col>
        </Row>
      </section>

      <section id="cost" className="td-section">
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
              <Row gutter={[12, 12]}>
                <Col xs={24} xl={12}>
                  <div className="td-cost-summary">
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
      </section>

      <section id="partitions" className="td-section">
        <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
          <div className="td-chart-card-header">
            <span className="td-chart-card-title">Partitions</span>
            <span className="td-chart-card-subtitle">
              {stats?.partition_col ?? "unpartitioned"}
              {stats?.partition_col_type ? ` (${stats.partition_col_type})` : null}
            </span>
          </div>
          <div className="td-chart-card-content">
            <PartitionHistogram partitions={partitions.data ?? []} />
            <Table<PartitionRead>
              size="small"
              rowKey={(item, index) => String(item.id ?? item.partition_value ?? index)}
              dataSource={partitions.data ?? []}
              loading={partitions.isLoading}
              pagination={{ pageSize: 6, size: "small" }}
              className="td-partitions-table"
              columns={[
                {
                  title: "Partition Value",
                  dataIndex: "partition_value",
                  ellipsis: true,
                  render: (value) => value ?? "(unpartitioned)"
                },
                {
                  title: "Files",
                  dataIndex: "files_per_partition",
                  align: "right",
                  sorter: (a, b) => (a.files_per_partition ?? 0) - (b.files_per_partition ?? 0),
                  render: (value, rowItem) => (
                    <span className="td-part-file-count">
                      <span className="td-part-bar-wrap">
                        <span
                          className="td-part-bar"
                          style={{
                            width: `${Math.min(
                              (((rowItem.small_files_in_partition_count ?? 0) / Math.max(value ?? 1, 1)) * 100),
                              100
                            )}%`
                          }}
                        />
                      </span>
                      {formatNumber(value)}
                    </span>
                  )
                },
                {
                  title: "Small",
                  dataIndex: "small_files_in_partition_count",
                  align: "right",
                  sorter: (a, b) =>
                    (a.small_files_in_partition_count ?? 0) - (b.small_files_in_partition_count ?? 0),
                  render: (value) => formatNumber(value)
                },
                {
                  title: "Size",
                  dataIndex: "partition_size_gb",
                  align: "right",
                  sorter: (a, b) => (a.partition_size_gb ?? 0) - (b.partition_size_gb ?? 0),
                  render: formatGb
                },
                {
                  title: "Needs compaction",
                  dataIndex: "compaction_status",
                  render: (value) => (
                    <Tag
                      color={value === "F" ? "gold" : "success"}
                      style={{ fontSize: 11, lineHeight: "18px", padding: "0 6px" }}
                    >
                      {value === "F" ? "Flagged" : "OK"}
                    </Tag>
                  )
                }
              ]}
              locale={{
                emptyText: <EmptyState title="No partition details" description="No partition data available yet." />
              }}
            />
          </div>
        </Card>
      </section>

      <section id="compliance" className="td-section">
        <Card className="td-chart-card" classNames={{ body: "td-chart-card-body td-compliance-card" }}>
          <div className="td-chart-card-header">
            <span className="td-chart-card-title">Compliance & snapshot protection</span>
          </div>
          <div className="td-chart-card-content">
            <Row gutter={[12, 12]}>
              <Col xs={24} lg={14}>
                {compliance.data ? (
                  <Descriptions size="small" column={2} className="td-meta-desc">
                    <Descriptions.Item label="Table ref" span={2}>
                      {compliance.data.table_ref}
                    </Descriptions.Item>
                    <Descriptions.Item label="ABAC policies">{compliance.data.abac_policy_count}</Descriptions.Item>
                    <Descriptions.Item label="Pending deletion">
                      <PendingDeletionTag pending={Boolean(compliance.data.pending_deletion)} />
                    </Descriptions.Item>
                    {compliance.data.abac_enforcement_note ? (
                      <Descriptions.Item label="ABAC note" span={2}>
                        {compliance.data.abac_enforcement_note}
                      </Descriptions.Item>
                    ) : null}
                    {compliance.data.pending_deletion ? (
                      <Descriptions.Item label="Pending deletion detail" span={2}>
                        {compliance.data.pending_deletion.pending_rows?.toLocaleString() ?? 0} rows blocked
                        {compliance.data.pending_deletion.blocked_until
                          ? ` until ${formatTime(compliance.data.pending_deletion.blocked_until)}`
                          : ""}
                        {compliance.data.pending_deletion.reason
                          ? ` — ${compliance.data.pending_deletion.reason}`
                          : ""}
                      </Descriptions.Item>
                    ) : null}
                  </Descriptions>
                ) : compliance.isLoading ? (
                  <Typography.Text type="secondary">Loading compliance status…</Typography.Text>
                ) : (
                  <EmptyState title="Compliance unavailable" description="Could not load compliance status for this table." />
                )}
              </Col>
              <Col xs={24} lg={10}>
                {role === "admin" || role === "operator" ? (
                  <Form
                    layout="inline"
                    onFinish={(values: { snapshot_id: number; label?: string }) => protect.mutate(values)}
                  >
                    <Form.Item
                      name="snapshot_id"
                      label="Protect snapshot"
                      rules={[{ required: true, message: "Snapshot ID required" }]}
                    >
                      <InputNumber min={1} placeholder="Snapshot ID" style={{ width: 130 }} size="small" />
                    </Form.Item>
                    <Form.Item name="label" label="Label">
                      <Input placeholder="e.g. audit-2024" style={{ width: 150 }} size="small" />
                    </Form.Item>
                    <Form.Item>
                      <Button type="primary" htmlType="submit" loading={protect.isPending} size="small">
                        Protect
                      </Button>
                    </Form.Item>
                  </Form>
                ) : null}
                <div className="td-protected-list">
                  <Typography.Text strong>Protected snapshots</Typography.Text>
                  {protectedItems
                    .filter((item) => item.snapshot_id > 0)
                    .slice(0, 8)
                    .map((item, index) => (
                      <div className="td-protected-row" key={`${item.snapshot_id}-${index}`}>
                        <Badge status="processing" />
                        <span>#{item.snapshot_id}</span>
                        {item.label ? <Typography.Text type="secondary">{item.label}</Typography.Text> : null}
                        {item.ts ? <Typography.Text type="secondary">{formatTime(item.ts)}</Typography.Text> : null}
                      </div>
                    ))}
                  {!protectedLocal.length && !protectedFromAudit.length ? (
                    <Typography.Text type="secondary">No protected snapshots recorded yet.</Typography.Text>
                  ) : null}
                </div>
              </Col>
            </Row>
          </div>
        </Card>
      </section>

      <section id="ai" className="td-section">
        <Row gutter={[12, 12]}>
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
      </section>

      <section id="data-quality" className="td-section">
        <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
          <div className="td-chart-card-header">
            <span className="td-chart-card-title">Data quality</span>
          </div>
          <div className="td-chart-card-content">
            {row ? (
              <TableAiInsights
                tableId={tableId}
                lastSnapshotCommittedAt={lastSnapshotCommittedAt}
                embedded
                visibleTabs={["dq"]}
                hideTabsWhenSingle
              />
            ) : null}
          </div>
        </Card>
      </section>

      <section id="activity" className="td-section">
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
      </section>

      <section id="metadata" className="td-section">
        <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
          <div className="td-chart-card-header">
            <span className="td-chart-card-title">Table metadata</span>
            {degradationBadge ? <Tag color="error">{degradationBadge}</Tag> : null}
          </div>
          <div className="td-chart-card-content">
            {row ? <TableMetadataGrid stats={stats} factors={scoreFactors} /> : null}
          </div>
        </Card>
      </section>

      <PlanPreview open={previewOpen} tables={row ? [row] : []} onClose={() => setPreviewOpen(false)} />
    </div>
  );
};
