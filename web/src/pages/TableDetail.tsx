import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Col, Descriptions, Row, Select, Space, Table, Timeline, Typography, message } from "antd";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  assignTables,
  getEffectivePreset,
  getTable,
  getTableAudit,
  getTableCost,
  getTableHealthTrends,
  getTableHistory,
  getTablePartitions,
  listDestinations,
  listPresets,
  runMaintenance,
  triggerScan
} from "../api/client";
import type { AuditEntry, HistoryPoint, PartitionRead } from "../api/types";
import { EmptyState, HintLabel, PageHeader, StatStrip } from "../components/common";
import { ConfirmAction } from "../components/confirm";
import { TableAiInsights } from "../components/TableAiInsights";
import { CostPrecisionBadge } from "../components/CostPrecisionBadge";
import { CostSavingsWaterfall } from "../components/CostSavingsWaterfall";
import { SavingsByOperation } from "../components/SavingsByOperation";
import { PlanPreview } from "../components/PlanPreview";
import { GradePill, HealthBadge, PriorityTag, StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatCurrency, formatDuration, formatGb, formatNumber, formatTime } from "../lib/format";
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

export const TableDetail = () => {
  const { tableId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { api, token } = useAuth();
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
  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });
  const assign = useMutation({
    mutationFn: (presetId: string) =>
      assignTables(api, {
        selection: { mode: "ids", ids: [tableId] },
        preset_id: presetId
      }),
    onSuccess: () => {
      message.success("Preset assigned");
      void effectivePreset.refetch();
    },
    onError: (error) => message.error((error as Error).message)
  });

  const run = useMutation({
    mutationFn: () => {
      const preset = presets.data?.find((item) => item.id === effectivePreset.data?.preset_id) ?? presets.data?.[0];
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
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["cost-dashboard"] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const scan = useMutation({
    mutationFn: () =>
      triggerScan(api, {
        scope: "tables",
        table_ids: [tableId],
        full_scan: true,
        trigger: "web-table-detail"
      }),
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
  const lastSnapshotCommittedAt =
    typeof statsRaw.last_snapshot_committed_at === "string"
      ? statsRaw.last_snapshot_committed_at
      : null;
  const hasStatsCostFallback = Boolean(
    stats?.projected_savings_usd_month || stats?.projected_monthly_s3_cost_usd
  );
  const costView = tableCost.data;
  const costByOperation = costView?.by_operation ?? [];
  const currentCostUsd =
    costView?.current_monthly_cost_usd ?? stats?.estimated_monthly_s3_cost_usd ?? 0;
  const projectedCostUsd =
    costView?.projected_monthly_cost_usd ??
    stats?.projected_monthly_s3_cost_usd ??
    stats?.estimated_monthly_s3_cost_usd ??
    0;
  const savingsUsd =
    costView?.projected_savings_usd_month ?? stats?.projected_savings_usd_month ?? 0;
  const reclaimableBytes = costView?.reclaimable_bytes ?? stats?.reclaimable_bytes ?? 0;
  const storageBytes =
    (costView?.live_bytes ?? 0) +
    (costView?.dead_bytes ?? 0) +
    (costView?.metadata_bytes ?? 0) +
    (costView?.orphan_bytes ?? 0);

  return (
    <div>
      <PageHeader
        title={row ? `${row.schema_name}.${row.table_name}` : "Table detail"}
        subtitle="Health, partition pressure, history, and recent maintenance audit."
        actions={
          <Space>
            <Select
              placeholder="Assign preset"
              value={effectivePreset.data?.preset_id ?? undefined}
              options={(presets.data ?? []).map((preset) => ({ label: preset.name, value: preset.id }))}
              onChange={(presetId) => assign.mutate(presetId)}
              loading={assign.isPending || presets.isLoading}
              disabled={!row}
              style={{ width: 190 }}
            />
            <Button onClick={() => setPreviewOpen(true)} disabled={!row}>
              Preview
            </Button>
            <Button loading={scan.isPending} onClick={() => scan.mutate()} disabled={!row}>
              Scan
            </Button>
            <ConfirmAction
              title="Run maintenance on this table?"
              description="This queues a real maintenance job using the assigned preset and destination."
              onConfirm={() => run.mutate()}
            >
              <Button type="primary" loading={run.isPending} disabled={!row}>
                Run
              </Button>
            </ConfirmAction>
          </Space>
        }
      />
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={16}>
          <Card loading={table.isLoading} title="Health summary">
            {row ? (
              <Space direction="vertical" size={12} className="full-width">
                <Space>
                  <GradePill grade={stats?.grade} />
                  <PriorityTag priority={stats?.compaction_priority} />
                  <HealthBadge healthy={!stats?.needs_compaction}>
                    {stats?.needs_compaction ? "Needs compaction" : "No action needed"}
                  </HealthBadge>
                </Space>
                <StatStrip
                  items={[
                    { label: "Score", value: formatNumber(stats?.overall_score) },
                    { label: "Size", value: formatGb(stats?.total_size_gb) },
                    { label: "Files", value: formatNumber(stats?.total_files, true) },
                    { label: "Small files", value: formatNumber(stats?.small_files_count, true) },
                    { label: "Partitions", value: formatNumber(stats?.total_partitions) }
                  ]}
                />
                <Descriptions size="small" column={2}>
                  <Descriptions.Item label="Partition column">{stats?.partition_col ?? "-"}</Descriptions.Item>
                  <Descriptions.Item label="Partition type">{stats?.partition_col_type ?? "-"}</Descriptions.Item>
                  <Descriptions.Item label="Snapshots">{formatNumber(stats?.total_snapshots)}</Descriptions.Item>
                  <Descriptions.Item label="Manifests">{formatNumber(stats?.manifest_count)}</Descriptions.Item>
                  <Descriptions.Item
                    label={
                      <HintLabel
                        label="Last snapshot commit"
                        tooltip="When Iceberg last committed a snapshot — used for data freshness SLAs."
                      />
                    }
                  >
                    {lastSnapshotCommittedAt ? formatTime(lastSnapshotCommittedAt) : "—"}
                  </Descriptions.Item>
                  <Descriptions.Item
                    label={
                      <HintLabel
                        label="Last Cryo scan"
                        tooltip="When Cryo last scanned this table — separate from data freshness."
                      />
                    }
                  >
                    {stats?.scan_at ? formatTime(stats.scan_at) : "—"}
                  </Descriptions.Item>
                  <Descriptions.Item label="Delete files">{formatNumber(stats?.total_delete_files)}</Descriptions.Item>
                  <Descriptions.Item
                    label={
                      <HintLabel
                        label="Effective preset"
                        tooltip="The maintenance preset that will actually run on this table, and how it got assigned."
                      />
                    }
                  >
                    {effectivePreset.data?.preset_name ?? "None"} ({provenanceLabel(effectivePreset.data?.provenance)})
                  </Descriptions.Item>
                </Descriptions>
              </Space>
            ) : (
              <EmptyState title="Table not found" description="The table may not have been scanned yet." />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="Cost & savings" loading={tableCost.isLoading}>
            {costView || hasStatsCostFallback ? (
              <Space direction="vertical" size={12} className="full-width">
                <Space wrap>
                  <CostPrecisionBadge
                    precision={costView?.precision ?? stats?.cost_precision}
                    lastFullCostScanAt={costView?.last_full_cost_scan_at}
                  />
                  {costView?.last_full_cost_scan_at ? (
                    <Typography.Text type="secondary">
                      Last full scan {formatTime(costView.last_full_cost_scan_at)}
                    </Typography.Text>
                  ) : !costView && hasStatsCostFallback ? (
                    <Typography.Text type="secondary">
                      Summary from latest scan stats; run a cost scan for per-operation breakdown.
                    </Typography.Text>
                  ) : null}
                </Space>
                <StatStrip
                  items={[
                    { label: "Now", value: formatCurrency(currentCostUsd) },
                    {
                      label: "After maintenance",
                      value: formatCurrency(projectedCostUsd),
                      tooltip: "Projected S3 storage cost if all recommended maintenance ran today."
                    },
                    { label: "Savings / mo", value: formatCurrency(savingsUsd) },
                    {
                      label: "Reclaimable storage",
                      value: formatBytesGb(reclaimableBytes),
                      tooltip: "Disk space maintenance would free up on this table."
                    }
                  ]}
                />
                {costByOperation.length ? (
                  <div className="chart-frame compact">
                    <CostSavingsWaterfall
                      currentCostUsd={currentCostUsd}
                      currentStorageBytes={storageBytes || (stats?.total_size_gb ?? 0) * 1024 ** 3}
                      byOperation={costByOperation}
                    />
                  </div>
                ) : null}
                {costView ? (
                  <Descriptions size="small" column={2}>
                    <Descriptions.Item
                      label={
                        <HintLabel label="Live" tooltip="Data still referenced by the table's current snapshot." />
                      }
                    >
                      {formatBytesGb(costView.live_bytes)}
                    </Descriptions.Item>
                    <Descriptions.Item
                      label={
                        <HintLabel
                          label="Dead"
                          tooltip="Data superseded by newer snapshots — reclaimed once old snapshots expire."
                        />
                      }
                    >
                      {formatBytesGb(costView.dead_bytes)}
                    </Descriptions.Item>
                    <Descriptions.Item
                      label={
                        <HintLabel
                          label="Metadata"
                          tooltip="Manifests, snapshots, and other Iceberg metadata overhead — not the actual data."
                        />
                      }
                    >
                      {formatBytesGb(costView.metadata_bytes)}
                    </Descriptions.Item>
                    <Descriptions.Item
                      label={
                        <HintLabel
                          label="Orphans"
                          tooltip="Files on storage that no snapshot or manifest references anymore — safe to delete."
                        />
                      }
                    >
                      {formatBytesGb(costView.orphan_bytes)}
                    </Descriptions.Item>
                  </Descriptions>
                ) : null}
                {costView && costView.query_request_savings_usd_month > 0 ? (
                  <Typography.Text type="secondary">
                    Query request savings: {formatCurrency(costView.query_request_savings_usd_month)}/mo
                  </Typography.Text>
                ) : null}
                {costByOperation.length ? <SavingsByOperation items={costByOperation} /> : null}
              </Space>
            ) : (
              <EmptyState
                title="No cost breakdown yet"
                description="Cost breakdown appears after the scanner writes table_cost."
              />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card
            title="Health Trends (30 days)"
            extra={
              healthTrends.data?.degradation_rate && healthTrends.data.degradation_rate < -0.5 ? (
                <Typography.Text type="danger" style={{ fontSize: 11 }}>
                  Degrading {Math.abs(healthTrends.data.degradation_rate).toFixed(1)} pts/day
                </Typography.Text>
              ) : null
            }
          >
            {(healthTrends.data?.trend_points ?? []).length ? (
              <Space direction="vertical" style={{ width: "100%" }} size={12}>
                {healthTrends.data?.days_to_critical && healthTrends.data.days_to_critical < 30 ? (
                  <Typography.Paragraph type="warning" style={{ margin: 0, fontSize: 12 }}>
                    ⚠️ Est. {healthTrends.data.days_to_critical} days until critical (F grade) if trend continues
                  </Typography.Paragraph>
                ) : null}
                <div className="chart-frame compact">
                  <ResponsiveContainer>
                    <LineChart
                      data={healthTrends.data?.trend_points.map((p) => ({
                        ...p,
                        timestamp: new Date(p.timestamp).getTime()
                      }))}
                    >
                      <XAxis
                        dataKey="timestamp"
                        tickFormatter={(ts) => new Date(ts).toLocaleDateString()}
                        tick={{ fontSize: 10 }}
                        interval="preserveStartEnd"
                      />
                      <YAxis yAxisId="left" tick={{ fontSize: 10 }} domain={[0, 100]} />
                      <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10 }} />
                      <Tooltip
                        {...chartTooltipProps}
                        labelFormatter={(ts) => new Date(ts).toLocaleString()}
                        formatter={(value, name) => {
                          const numeric = Number(value ?? 0);
                          const label = String(name);
                          if (label === "score") return [numeric.toFixed(1), "Score"];
                          if (label === "small_files") return [numeric.toLocaleString(), "Small Files"];
                          if (label === "total_size_gb") return [numeric.toFixed(1) + " GB", "Size"];
                          return [numeric, label];
                        }}
                      />
                      <Line
                        yAxisId="left"
                        dataKey="score"
                        stroke={cryoColors.gradeA}
                        strokeWidth={2}
                        dot={false}
                        name="Health Score"
                      />
                      <Line
                        yAxisId="right"
                        dataKey="small_files"
                        stroke={cryoColors.chartPrimary}
                        strokeWidth={2}
                        dot={false}
                        name="Small Files"
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </Space>
            ) : (
              <EmptyState title="No trends yet" description="Health trends appear after multiple scans over time." />
            )}
          </Card>
        </Col>
        <Col xs={24}>
          {row ? (
            <TableAiInsights tableId={tableId} lastSnapshotCommittedAt={lastSnapshotCommittedAt} />
          ) : null}
        </Col>
        <Col xs={24} xl={15}>
          <Card title="Partitions">
            <Table<PartitionRead>
              size="small"
              rowKey={(item, index) => String(item.id ?? item.partition_value ?? index)}
              dataSource={partitions.data ?? []}
              loading={partitions.isLoading}
              pagination={{ pageSize: 8 }}
              columns={[
                { title: "Value", dataIndex: "partition_value", ellipsis: true, render: (value) => value ?? "(unpartitioned)" },
                { title: "Files", dataIndex: "files_per_partition", align: "right", render: (value) => formatNumber(value) },
                { title: "Small files", dataIndex: "small_files_in_partition_count", align: "right", render: (value) => formatNumber(value) },
                { title: "Size", dataIndex: "partition_size_gb", align: "right", render: formatGb },
                { title: "Status", dataIndex: "compaction_status", render: (value) => <StatusTag status={value === "F" ? "running" : "succeeded"} /> }
              ]}
              locale={{ emptyText: <EmptyState title="No partition details" description="The planned partition endpoint may not be live yet." /> }}
            />
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card title="Audit timeline">
            {(audit.data ?? []).length ? (
              <Timeline
                items={(audit.data ?? []).slice(0, 12).map((item: AuditEntry) => ({
                  color: item.status === "failed" ? "red" : "green",
                  children: (
                    <Space direction="vertical" size={2}>
                      <Typography.Text strong>{item.action ?? "maintenance"}</Typography.Text>
                      <Typography.Text type="secondary">
                        {formatTime(item.ts ?? item.created_at)} · {item.engine ?? "-"} · {formatDuration(item.duration_ms)}
                      </Typography.Text>
                    </Space>
                  )
                }))}
              />
            ) : (
              <EmptyState title="No audit entries" description="Completed maintenance actions will appear here." />
            )}
          </Card>
        </Col>
      </Row>
      <PlanPreview open={previewOpen} tables={row ? [row] : []} onClose={() => setPreviewOpen(false)} />
    </div>
  );
};
