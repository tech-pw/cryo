import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, Badge, Button, Card, Col, Descriptions, Form, Input, InputNumber,
  Row, Select, Space, Table, Tag, Timeline, Tooltip, Typography, message
} from "antd";
import {
  Area, CartesianGrid, Line, LineChart, ResponsiveContainer,
  Tooltip as RechartsTooltip, XAxis, YAxis
} from "recharts";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  assignTables, getComplianceStatus, getEffectivePreset, getTable,
  getTableAudit, getTableCost, getTableHealthTrends, getTableHistory,
  getTablePartitions, getTableS3Tiering, listDestinations, listPresets,
  protectSnapshot, runMaintenance, runManifestRewrite, triggerScan, unassignTables
} from "../api/client";
import type { AuditEntry, HistoryPoint, PartitionRead } from "../api/types";
import { EmptyState, HintLabel } from "../components/common";
import { ConfirmAction } from "../components/confirm";
import { CostPrecisionBadge } from "../components/CostPrecisionBadge";
import { CostSavingsWaterfall } from "../components/CostSavingsWaterfall";
import { buildHealthScoreFactors } from "../components/HealthScoreBreakdown";
import { SavingsByOperation } from "../components/SavingsByOperation";
import { PlanPreview } from "../components/PlanPreview";
import { TableAiInsights } from "../components/TableAiInsights";
import { TableCompactionAnalytics } from "../components/TableCompactionAnalytics";
import { TableDiagnosisPanel } from "../components/TableDiagnosisPanel";
import {
  CompactionTag, GradePill, PriorityTag, healthScoreColor
} from "../components/status";
import { useAuth } from "../lib/auth";
import { isOrphanScanStale } from "../lib/costStaleness";
import {
  formatBytesGb, formatCurrency, formatDuration, formatGb, formatNumber, formatTime
} from "../lib/format";
import { chartTooltipProps, cryoColors, operationColors, operationLabels } from "../lib/theme";

const provenanceLabel = (provenance?: string | null): string => {
  switch (provenance) {
    case "assignment": return "manually assigned";
    case "rule": return "matched a rule";
    case "none": return "no preset assigned";
    default: return "unknown";
  }
};

const smallFileRate = (stats: { small_files_count?: number; total_files?: number } | null | undefined): number => {
  if (!stats?.total_files || !stats?.small_files_count) return 0;
  return stats.small_files_count / stats.total_files;
};

// Max/avg files-per-partition ratio above which we flag a partition as a rebalance candidate --
// a common rule-of-thumb threshold, not a backend-computed signal.
const SKEW_HINT_THRESHOLD = 2;

export const TableDetail = () => {
  const { tableId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { api, token, role } = useAuth();
  const [previewOpen, setPreviewOpen] = useState(false);

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
  const history = useQuery({
    queryKey: ["table-history", tableId, token],
    queryFn: () => getTableHistory(api, tableId) as Promise<HistoryPoint[]>,
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
    onSuccess: () => { message.success("Preset assigned"); void effectivePreset.refetch(); },
    onError: (error) => message.error((error as Error).message)
  });
  const unassign = useMutation({
    mutationFn: () => unassignTables(api, { selection: { mode: "ids", ids: [tableId] } }),
    onSuccess: () => { message.success("Preset unassigned"); void effectivePreset.refetch(); },
    onError: (error) => message.error((error as Error).message)
  });
  const manifestRewrite = useMutation({
    mutationFn: () => {
      const preset = presets.data?.find((item) => item.id === effectivePreset.data?.preset_id) ?? presets.data?.[0];
      const destinationId = preset?.destination_id ?? destinations.data?.[0]?.id;
      if (!destinationId) throw new Error("No engine configured. Add one under Connections.");
      return runManifestRewrite(api, { table_ids: [tableId], destination_id: destinationId, preset_id: preset?.id, trigger: "web-detail-manifest" });
    },
    onSuccess: (result) => message.success(`Manifest rewrite queued: ${result.run_id}`),
    onError: (error) => message.error((error as Error).message)
  });
  const protect = useMutation({
    mutationFn: (values: { snapshot_id: number; label?: string }) => protectSnapshot(api, tableId, values),
    onSuccess: () => message.success("Snapshot protected"),
    onError: (error) => message.error((error as Error).message)
  });
  const run = useMutation({
    mutationFn: () => {
      const preset = presets.data?.find((item) => item.id === effectivePreset.data?.preset_id) ?? presets.data?.[0];
      const destinationId = preset?.destination_id ?? destinations.data?.[0]?.id;
      if (!destinationId) throw new Error("No engine configured. Add one under Connections or run make seed-config.");
      return runMaintenance(api, { table_ids: [tableId], destination_id: destinationId, preset_id: preset?.id, mode: preset?.mode ?? "standard", trigger: "web-detail" });
    },
    onSuccess: (result) => {
      message.success(`Run queued: ${result.run_id}`);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["cost-dashboard"] });
    },
    onError: (error) => message.error((error as Error).message)
  });
  const scan = useMutation({
    mutationFn: () => triggerScan(api, { scope: "tables", table_ids: [tableId], full_scan: true, trigger: "web-table-detail" }),
    onSuccess: (result) => {
      message.success(result.message || `Scan queued: ${result.run_id}`);
      void queryClient.invalidateQueries({ queryKey: ["table", tableId] });
      void queryClient.invalidateQueries({ queryKey: ["tables"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["cost-dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["scan-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
      navigate(`/scans/${result.run_id}`);
    },
    onError: (error) => message.error((error as Error).message)
  });

  const row = table.data;
  const stats = row?.stats;
  const statsRaw = stats?.raw ?? {};
  const lastSnapshotCommittedAt = typeof statsRaw.last_snapshot_committed_at === "string" ? statsRaw.last_snapshot_committed_at : null;
  const hasStatsCostFallback = Boolean(stats?.projected_savings_usd_month || stats?.projected_monthly_s3_cost_usd);
  const costView = tableCost.data;
  const costByOperation = costView?.by_operation ?? [];
  const currentCostUsd = costView?.current_monthly_cost_usd ?? stats?.estimated_monthly_s3_cost_usd ?? 0;
  const projectedCostUsd = costView?.projected_monthly_cost_usd ?? stats?.projected_monthly_s3_cost_usd ?? stats?.estimated_monthly_s3_cost_usd ?? 0;
  const savingsUsd = costView?.projected_savings_usd_month ?? stats?.projected_savings_usd_month ?? 0;
  const reclaimableBytes = costView?.reclaimable_bytes ?? stats?.reclaimable_bytes ?? 0;
  const storageBytes = (costView?.live_bytes ?? 0) + (costView?.dead_bytes ?? 0) + (costView?.metadata_bytes ?? 0) + (costView?.orphan_bytes ?? 0);
  const score = stats?.overall_score ?? 0;
  const scoreColor = healthScoreColor(score);
  const scoreFactors = useMemo(
    () => buildHealthScoreFactors(stats, costView?.orphan_bytes ?? 0, storageBytes),
    [stats, costView?.orphan_bytes, storageBytes]
  );

  const trendData = useMemo(() =>
    (healthTrends.data?.trend_points ?? []).map((p) => ({
      ...p,
      timestamp: new Date(p.timestamp).getTime()
    })),
    [healthTrends.data?.trend_points]
  );

  const degradationBadge = healthTrends.data?.degradation_rate != null && healthTrends.data.degradation_rate < -0.5
    ? `${Math.abs(healthTrends.data.degradation_rate).toFixed(1)} pts/day`
    : null;

  if (!row && !table.isLoading) {
    return (
      <div className="table-detail">
        <div className="td-identity-bar">
          <div className="td-identity-info">
            <Typography.Title level={3} style={{ margin: 0, color: "inherit" }}>Table not found</Typography.Title>
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
      {/* ── Identity Bar ── */}
      <div className="td-identity-bar">
        <div className="td-identity-info">
          <div className="td-identity-name">
            <Typography.Title level={3} style={{ margin: 0, color: "inherit" }}>
              {row ? `${row.schema_name}.${row.table_name}` : "Loading..."}
            </Typography.Title>
            {stats?.grade ? <GradePill grade={stats.grade} /> : null}
            {stats?.compaction_priority ? <PriorityTag priority={stats.compaction_priority} /> : null}
            <CompactionTag needsCompaction={stats?.needs_compaction} />
          </div>
          <div className="td-identity-meta">
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
        <div className="td-identity-actions">
          <Select
            placeholder="Assign preset"
            value={effectivePreset.data?.preset_id ?? undefined}
            options={(presets.data ?? []).map((p) => ({ label: p.name, value: p.id }))}
            onChange={(presetId) => assign.mutate(presetId)}
            loading={assign.isPending || presets.isLoading}
            disabled={!row}
            style={{ width: 170 }}
            size="small"
          />
          <Button size="small" onClick={() => setPreviewOpen(true)} disabled={!row}>Preview</Button>
          <ConfirmAction title="Rewrite manifests?" description="Queues manifest-only maintenance without data file compaction." onConfirm={() => manifestRewrite.mutate()}>
            <Button size="small" loading={manifestRewrite.isPending} disabled={!row}>Manifest</Button>
          </ConfirmAction>
          {effectivePreset.data?.preset_id ? (
            <ConfirmAction title="Unassign preset?" description="Clears the manual preset assignment." onConfirm={() => unassign.mutate()}>
              <Button size="small" loading={unassign.isPending} disabled={!row}>Unassign</Button>
            </ConfirmAction>
          ) : null}
          <Button size="small" loading={scan.isPending} onClick={() => scan.mutate()} disabled={!row}>Scan</Button>
          <ConfirmAction title="Run maintenance?" description="Queues a real maintenance job using the assigned preset and destination." onConfirm={() => run.mutate()}>
            <Button type="primary" size="small" loading={run.isPending} disabled={!row}>Run</Button>
          </ConfirmAction>
        </div>
      </div>

      {/* ── Metric Tiles ── */}
      <div className="td-metric-tiles">
        <div className="td-metric-tile td-metric-score" style={{ "--tile-accent": scoreColor } as React.CSSProperties}>
          <div className="td-metric-value">{formatNumber(score)}</div>
          <div className="td-metric-label">
            Health Score
            <span className="td-metric-change">
              {degradationBadge ? <span className="td-degrading">{degradationBadge}</span> : null}
            </span>
          </div>
        </div>
        <div className="td-metric-tile">
          <div className="td-metric-value">{formatGb(stats?.total_size_gb)}</div>
          <div className="td-metric-label">Total Size</div>
        </div>
        <div className="td-metric-tile">
          <div className="td-metric-value">{formatNumber(stats?.total_files, true)}</div>
          <div className="td-metric-label">
            Files
            <span className="td-metric-faint">{stats?.avg_file_size_mb ? `${stats.avg_file_size_mb.toFixed(0)} MB avg` : null}</span>
          </div>
        </div>
        <div className="td-metric-tile">
          <div className="td-metric-value">{formatNumber(stats?.small_files_count, true)}</div>
          <div className="td-metric-label">
            Small Files
            <span className="td-metric-faint">
              {stats?.total_files ? `${(smallFileRate(stats) * 100).toFixed(0)}% of total` : null}
              {stats?.needs_compaction ? <span className="td-metric-hint"> — compact recommended</span> : null}
            </span>
          </div>
        </div>
        <div className="td-metric-tile">
          <div className="td-metric-value">{formatNumber(stats?.total_partitions)}</div>
          <div className="td-metric-label">Partitions</div>
        </div>
        <div className="td-metric-tile">
          <div className="td-metric-value">{formatCurrency(savingsUsd)}</div>
          <div className="td-metric-label">
            Savings / mo
            <span className="td-metric-faint">{formatBytesGb(reclaimableBytes)} reclaimable</span>
          </div>
        </div>
      </div>

      {/* ── Main Content Grid ── */}
      <Row gutter={[12, 12]}>
        {/* ── Diagnosis ── */}
        <Col xs={24}>
          {row ? <TableDiagnosisPanel tableId={tableId} stats={stats} factors={scoreFactors} /> : null}
        </Col>

        {/* ── Health Trends Chart ── */}
        <Col xs={24} lg={16}>
          <Card
            className={`td-chart-card${trendData.length ? "" : " td-chart-card-quiet"}`}
            classNames={{ body: "td-chart-card-body" }}
          >
            <div className="td-chart-card-header">
              <span className="td-chart-card-title">Health Trends</span>
              <span className="td-chart-card-subtitle">30 days</span>
              {degradationBadge ? <Tag color="error" style={{ marginLeft: 8 }}>{degradationBadge}</Tag> : null}
              {healthTrends.data?.days_to_critical != null && healthTrends.data.days_to_critical < 30 ? (
                <Tag color="warning" style={{ marginLeft: 4 }}>{healthTrends.data.days_to_critical}d to critical</Tag>
              ) : null}
            </div>
            {trendData.length ? (
              <div className="chart-frame">
                <ResponsiveContainer>
                  <LineChart data={trendData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e1e6ed)" />
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={(ts) => new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
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
                      <linearGradient id="scoreGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={cryoColors.gradeA} stopOpacity={0.15} />
                        <stop offset="100%" stopColor={cryoColors.gradeA} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <Area yAxisId="left" type="monotone" dataKey="score" fill="url(#scoreGrad)" stroke="none" />
                    <Line yAxisId="left" type="monotone" dataKey="score" stroke={cryoColors.gradeA} strokeWidth={2.5} dot={false} name="score" />
                    <Line yAxisId="right" type="monotone" dataKey="small_files" stroke={cryoColors.chartPrimary} strokeWidth={2} dot={false} name="small_files" strokeDasharray="4 3" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyState title="No trends yet" description="Health trends appear after multiple scans over time." />
            )}
          </Card>
        </Col>

        {/* ── Cost & Savings ── */}
        <Col xs={24} lg={8}>
          <Card
            className={`td-chart-card${costView || hasStatsCostFallback ? "" : " td-chart-card-quiet"}`}
            classNames={{ body: "td-chart-card-body" }}
          >
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
            {costView || hasStatsCostFallback ? (
              <Space direction="vertical" size={10} className="full-width">
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
                {costByOperation.length ? (
                  <CostSavingsWaterfall
                    currentCostUsd={currentCostUsd}
                    currentStorageBytes={storageBytes || (stats?.total_size_gb ?? 0) * 1024 ** 3}
                    byOperation={costByOperation}
                  />
                ) : null}
                {costView ? (
                  <div className="td-storage-breakdown">
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>Storage</Typography.Text>
                    <div className="td-storage-bar">
                                      <Tooltip title={`Live: ${formatBytesGb(costView.live_bytes)}`}>
                          <span className="td-storage-seg td-storage-live" style={{ flex: costView.live_bytes }} />
                        </Tooltip>
                        <Tooltip title={`Dead: ${formatBytesGb(costView.dead_bytes)}`}>
                          <span className="td-storage-seg td-storage-dead" style={{ flex: costView.dead_bytes }} />
                        </Tooltip>
                        <Tooltip title={`Metadata: ${formatBytesGb(costView.metadata_bytes)}`}>
                          <span className="td-storage-seg td-storage-meta" style={{ flex: costView.metadata_bytes }} />
                        </Tooltip>
                        {costView.orphan_bytes ? (
                          <Tooltip title={`Orphans: ${formatBytesGb(costView.orphan_bytes)}`}>
                            <span className="td-storage-seg td-storage-orphan" style={{ flex: costView.orphan_bytes }} />
                          </Tooltip>
                        ) : null}
                    </div>
                    <div className="td-storage-legend">
                      <span><span className="td-legend-dot td-storage-live" />Live {formatBytesGb(costView.live_bytes)}</span>
                      <span><span className="td-legend-dot td-storage-dead" />Dead {formatBytesGb(costView.dead_bytes)}</span>
                      <span><span className="td-legend-dot td-storage-meta" />Meta {formatBytesGb(costView.metadata_bytes)}</span>
                      {costView.orphan_bytes ? <span><span className="td-legend-dot td-storage-orphan" />Orphan {formatBytesGb(costView.orphan_bytes)}</span> : null}
                    </div>
                  </div>
                ) : null}
                {s3Tiering.data ? (
                  <Alert type="info" showIcon message={`S3 tiering: ${s3Tiering.data.recommendation}`} style={{ fontSize: 12 }} />
                ) : null}
                {costByOperation.length ? <SavingsByOperation items={costByOperation} /> : null}
              </Space>
            ) : (
              <EmptyState title="No cost breakdown yet" description="Cost breakdown appears after the scanner writes table_cost." />
            )}
          </Card>
        </Col>

        {/* ── Partitions Table ── */}
        <Col xs={24} lg={16}>
          <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
            <div className="td-chart-card-header">
              <span className="td-chart-card-title">Partitions</span>
              <span className="td-chart-card-subtitle">
                {stats?.partition_col ?? "unpartitioned"}
                {stats?.partition_col_type ? ` (${stats.partition_col_type})` : null}
              </span>
            </div>
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
                  render: (value, row) => (
                    <span className="td-part-file-count">
                      <span className="td-part-bar-wrap">
                        <span
                          className="td-part-bar"
                          style={{ width: `${Math.min((row.small_files_in_partition_count ?? 0) / Math.max(value ?? 1, 1) * 100, 100)}%` }}
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
                  sorter: (a, b) => (a.small_files_in_partition_count ?? 0) - (b.small_files_in_partition_count ?? 0),
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
                  // compaction_status is a static per-scan flag (does this partition qualify for
                  // compaction), not a live job status -- no compaction is actually "running".
                  title: "Needs compaction",
                  dataIndex: "compaction_status",
                  render: (value) => (
                    <Tag color={value === "F" ? "gold" : "success"} style={{ fontSize: 11, lineHeight: "18px", padding: "0 6px" }}>
                      {value === "F" ? "Flagged" : "OK"}
                    </Tag>
                  )
                }
              ]}
              locale={{ emptyText: <EmptyState title="No partition details" description="No partition data available yet." /> }}
            />
          </Card>
        </Col>

        {/* ── Audit Timeline ── */}
        <Col xs={24} lg={8}>
          <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
            <div className="td-chart-card-header">
              <span className="td-chart-card-title">Recent Activity</span>
              <span className="td-chart-card-subtitle">
                {audit.data?.length ? `Last ${Math.min(audit.data.length, 12)} events` : null}
              </span>
            </div>
            {(audit.data ?? []).length ? (
              <div className="td-timeline">
                {(audit.data ?? []).slice(0, 12).map((item: AuditEntry, index) => {
                  const failed = item.status === "failed";
                  return (
                    <div key={item.id ?? index} className={`td-timeline-item ${failed ? "td-timeline-failed" : ""}`}>
                      <div className="td-timeline-dot-wrap">
                        <span className={`td-timeline-dot ${failed ? "td-timeline-dot-fail" : "td-timeline-dot-ok"}`} />
                        {index < Math.min(audit.data?.length ?? 0, 12) - 1 ? <span className="td-timeline-line" /> : null}
                      </div>
                      <div className="td-timeline-content">
                        <div className="td-timeline-head">
                          <span className="td-timeline-action">{item.action ?? "maintenance"}</span>
                          {failed ? <Tag color="error" style={{ fontSize: 10, lineHeight: "16px", padding: "0 4px", margin: 0 }}>failed</Tag> : null}
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
          </Card>
        </Col>

        {/* ── Metadata Details ── */}
        <Col xs={24} lg={8}>
          <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
            <div className="td-chart-card-header">
              <span className="td-chart-card-title">Table Metadata</span>
            </div>
            {row ? (
              <Descriptions size="small" column={1} className="td-meta-desc">
                <Descriptions.Item label="Partition col">{stats?.partition_col ?? "-"}</Descriptions.Item>
                <Descriptions.Item label="Type">{stats?.partition_col_type ?? "-"}</Descriptions.Item>
                <Descriptions.Item label="Snapshots">{formatNumber(stats?.total_snapshots)}</Descriptions.Item>
                <Descriptions.Item label="Manifests">{formatNumber(stats?.manifest_count)}</Descriptions.Item>
                <Descriptions.Item
                  label={<HintLabel label="Delete files" tooltip="Position delete files used for row-level deletes." />}
                >
                  {formatNumber(stats?.total_delete_files)}
                </Descriptions.Item>
                <Descriptions.Item label="Records">{formatNumber(stats?.total_records, true)}</Descriptions.Item>
                <Descriptions.Item label="Avg file size">{stats?.avg_file_size_mb ? `${stats.avg_file_size_mb.toFixed(1)} MB` : "-"}</Descriptions.Item>
                <Descriptions.Item label="Partition skew">
                  {stats?.partition_skew_ratio != null ? (
                    <>
                      {stats.partition_skew_ratio.toFixed(2)}x
                      {stats.partition_skew_ratio > SKEW_HINT_THRESHOLD ? (
                        <Tag color="gold" className="td-metric-hint-tag">rebalance candidate</Tag>
                      ) : null}
                    </>
                  ) : "-"}
                </Descriptions.Item>
              </Descriptions>
            ) : null}
          </Card>
        </Col>

        {/* ── Compliance & Snapshot Protection ── */}
        <Col xs={24} lg={16}>
          <Card className="td-chart-card" classNames={{ body: "td-chart-card-body td-compliance-card" }}>
            <div className="td-chart-card-header">
              <span className="td-chart-card-title">Compliance & Snapshot Protection</span>
            </div>
            <Space direction="vertical" size={10} className="full-width">
              {compliance.data ? (
                <Descriptions size="small" column={2} className="td-meta-desc">
                  <Descriptions.Item label="Table ref" span={2}>{compliance.data.table_ref}</Descriptions.Item>
                  <Descriptions.Item label="ABAC policies">{compliance.data.abac_policy_count}</Descriptions.Item>
                  <Descriptions.Item label="Pending deletion">
                    {compliance.data.pending_deletion ? "Yes" : "None"}
                  </Descriptions.Item>
                  {compliance.data.abac_enforcement_note ? (
                    <Descriptions.Item label="ABAC note" span={2}>{compliance.data.abac_enforcement_note}</Descriptions.Item>
                  ) : null}
                  {compliance.data.pending_deletion ? (
                    <Descriptions.Item label="Pending deletion detail" span={2}>
                      {compliance.data.pending_deletion.pending_rows?.toLocaleString() ?? 0} rows blocked
                      {compliance.data.pending_deletion.blocked_until ? ` until ${formatTime(compliance.data.pending_deletion.blocked_until)}` : ""}
                      {compliance.data.pending_deletion.reason ? ` — ${compliance.data.pending_deletion.reason}` : ""}
                    </Descriptions.Item>
                  ) : null}
                </Descriptions>
              ) : compliance.isLoading ? (
                <Typography.Text type="secondary">Loading compliance status…</Typography.Text>
              ) : (
                <EmptyState title="Compliance unavailable" description="Could not load compliance status for this table." />
              )}
              {role === "admin" || role === "operator" ? (
                <Form layout="inline" onFinish={(values: { snapshot_id: number; label?: string }) => protect.mutate(values)}>
                  <Form.Item name="snapshot_id" label="Protect snapshot" rules={[{ required: true, message: "Snapshot ID required" }]}>
                    <InputNumber min={1} placeholder="Snapshot ID" style={{ width: 130 }} size="small" />
                  </Form.Item>
                  <Form.Item name="label" label="Label">
                    <Input placeholder="e.g. audit-2024" style={{ width: 150 }} size="small" />
                  </Form.Item>
                  <Form.Item>
                    <Button type="primary" htmlType="submit" loading={protect.isPending} size="small">Protect</Button>
                  </Form.Item>
                </Form>
              ) : null}
            </Space>
          </Card>
        </Col>

        {/* ── Compaction Analytics ── */}
        <Col xs={24}>
          {row ? <TableCompactionAnalytics tableId={tableId} /> : null}
        </Col>

        {/* ── AI Insights ── */}
        <Col xs={24}>
          {row ? (
            <TableAiInsights tableId={tableId} lastSnapshotCommittedAt={lastSnapshotCommittedAt} />
          ) : null}
        </Col>
      </Row>

      <PlanPreview open={previewOpen} tables={row ? [row] : []} onClose={() => setPreviewOpen(false)} />
    </div>
  );
};
