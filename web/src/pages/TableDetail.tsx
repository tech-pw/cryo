import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Card, Col, Descriptions, Row, Select, Space, Table, Timeline, Typography, message } from "antd";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  assignTables,
  getEffectivePreset,
  getTable,
  getTableAudit,
  getTableHistory,
  getTablePartitions,
  listDestinations,
  listPresets,
  runMaintenance
} from "../api/client";
import type { AuditEntry, HistoryPoint, PartitionRead } from "../api/types";
import { EmptyState, PageHeader, StatStrip } from "../components/common";
import { PlanPreview } from "../components/PlanPreview";
import { GradePill, HealthBadge, PriorityTag, StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatDuration, formatGb, formatNumber, formatTime } from "../lib/format";
import { chartTooltipProps, cryoColors } from "../lib/theme";

export const TableDetail = () => {
  const { tableId = "" } = useParams();
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
  const effectivePreset = useQuery({
    queryKey: ["effective-preset", tableId, token],
    queryFn: () => getEffectivePreset(api, tableId),
    retry: 1
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
        throw new Error("No destination configured. Add one under Connections or run make seed-config.");
      }
      return runMaintenance(api, {
        table_ids: [tableId],
        destination_id: destinationId,
        preset_id: preset?.id,
        mode: preset?.mode ?? "standard",
        trigger: "web-detail"
      });
    },
    onSuccess: (result) => message.success(`Run queued: ${result.run_id}`),
    onError: (error) => message.error((error as Error).message)
  });

  const row = table.data;
  const stats = row?.stats;

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
            <Button type="primary" loading={run.isPending} onClick={() => run.mutate()} disabled={!row}>
              Run
            </Button>
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
                  <Descriptions.Item label="Delete files">{formatNumber(stats?.total_delete_files)}</Descriptions.Item>
                  <Descriptions.Item label="Effective preset">
                    {effectivePreset.data?.preset_name ?? "None"} ({effectivePreset.data?.provenance ?? "unknown"})
                  </Descriptions.Item>
                </Descriptions>
              </Space>
            ) : (
              <EmptyState title="Table not found" description="The table may not have been scanned yet." />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="History">
            {(history.data ?? []).length ? (
              <div className="chart-frame compact">
                <ResponsiveContainer>
                  <LineChart data={history.data}>
                    <XAxis dataKey="scan_at" hide />
                    <YAxis hide />
                    <Tooltip
                      {...chartTooltipProps}
                      labelFormatter={(value) => formatTime(String(value))}
                    />
                    <Line dataKey="small_files_count" stroke={cryoColors.chartPrimary} dot={false} />
                    <Line dataKey="overall_score" stroke={cryoColors.gradeA} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyState title="No history yet" description="Trend data appears after multiple scans." />
            )}
          </Card>
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
