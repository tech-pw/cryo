import { useState } from "react";
import { Alert, Button, Card, Col, Descriptions, Drawer, Empty, Popover, Row, Space, Statistic, Table, Tabs, Tag, Typography, message } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import {
  approveSmartLayoutRecommendation,
  dismissSmartLayoutRecommendation,
  executeSmartLayoutRecommendation,
  getDetectionTiers,
  getSmartMaintenanceOverview,
  getTableRoiBreakdown,
  getTableWorkloadForecast,
  listSmartLayoutRecommendations,
  type DetectionTierRowRead,
  type SmartLayoutRecommendationRead
} from "../api/client";
import { useCommitEventStream } from "../hooks/useCommitEventStream";
import { useAuth } from "../lib/auth";

const TIERING_HELP_CONTENT = (
  <Space direction="vertical" size={10} style={{ maxWidth: 360 }}>
    <Typography.Paragraph style={{ marginBottom: 0 }}>
      Every scanned table gets one of four tiers so maintenance effort goes where it matters:
    </Typography.Paragraph>
    <ul style={{ margin: 0, paddingLeft: 18 }}>
      <li>
        <Typography.Text strong>HOT</Typography.Text> — heavy recent write or read churn;
        maintenance pays off immediately.
      </li>
      <li>
        <Typography.Text strong>WARM</Typography.Text> — moderate activity; worth scheduling
        regular maintenance.
      </li>
      <li>
        <Typography.Text strong>COLD</Typography.Text> — low activity; safe to deprioritize
        routine maintenance.
      </li>
      <li>
        <Typography.Text strong>DORMANT</Typography.Text> — no recent commits or queries and a
        stale scan history.
      </li>
    </ul>
    <Typography.Paragraph style={{ marginBottom: 0 }}>
      The tier comes from a score built from commits in the last 24h/7d, query volume over the
      last 7 days, small-file count, and delete-file percentage.
    </Typography.Paragraph>
    <Typography.Paragraph type="warning" style={{ marginBottom: 0 }}>
      A table&apos;s <em>first</em> scan always lands in COLD, even for a genuinely busy table —
      commit velocity is measured by diffing snapshots across scans, and query volume needs a
      history window to build up, so there&apos;s no signal yet on scan one. It re-tiers on later
      scans once that history exists; no separate action needed, just time and another scan pass.
    </Typography.Paragraph>
    <Typography.Paragraph style={{ marginBottom: 0 }}>
      Click a row in Detection Tiers (or use Inspect ROI) to see the exact reason and score behind
      a specific table&apos;s tier.
    </Typography.Paragraph>
  </Space>
);

const TieringHelpButton = () => (
  <Popover
    trigger="click"
    placement="bottomLeft"
    title="How tiering works"
    content={TIERING_HELP_CONTENT}
  >
    <Button type="text" icon={<InfoCircleOutlined />} aria-label="How tiering works" />
  </Popover>
);

const tierColor: Record<string, string> = {
  hot: "red",
  warm: "gold",
  cold: "blue",
  dormant: "default",
  unknown: "default"
};

export function SmartMaintenance() {
  const { api, token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [inspectTable, setInspectTable] = useState<DetectionTierRowRead | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  useCommitEventStream({
    onTick: () => {
      void queryClient.invalidateQueries({ queryKey: ["smart-maintenance-overview"] });
      void queryClient.invalidateQueries({ queryKey: ["smart-maintenance-detection-tiers"] });
    }
  });

  const overview = useQuery({
    queryKey: ["smart-maintenance-overview", token],
    queryFn: () => getSmartMaintenanceOverview(api)
  });

  const tiers = useQuery({
    queryKey: ["smart-maintenance-detection-tiers", token],
    queryFn: () => getDetectionTiers(api, 500)
  });

  const recommendations = useQuery({
    queryKey: ["smart-maintenance-layout-recommendations", token],
    queryFn: () => listSmartLayoutRecommendations(api)
  });

  const inspectTableId = inspectTable?.table_id ?? null;

  const roi = useQuery({
    queryKey: ["smart-maintenance-roi", inspectTableId],
    queryFn: () => getTableRoiBreakdown(api, inspectTableId as string),
    enabled: Boolean(inspectTableId)
  });

  const forecast = useQuery({
    queryKey: ["smart-maintenance-forecast", inspectTableId],
    queryFn: () => getTableWorkloadForecast(api, inspectTableId as string, 24),
    enabled: Boolean(inspectTableId)
  });

  const refreshQueries = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["smart-maintenance-layout-recommendations"] }),
      queryClient.invalidateQueries({ queryKey: ["layout-recommendations"] })
    ]);
  };

  const approve = useMutation({
    mutationFn: (rec: SmartLayoutRecommendationRead) =>
      approveSmartLayoutRecommendation(api, rec.id),
    onSuccess: async () => {
      messageApi.success("Recommendation approved");
      await refreshQueries();
    },
    onError: (error: Error) => messageApi.error(error.message)
  });

  const dismiss = useMutation({
    mutationFn: (rec: SmartLayoutRecommendationRead) =>
      dismissSmartLayoutRecommendation(api, rec.id, "dismissed_from_ui"),
    onSuccess: async () => {
      messageApi.success("Recommendation dismissed");
      await refreshQueries();
    },
    onError: (error: Error) => messageApi.error(error.message)
  });

  const execute = useMutation({
    mutationFn: (rec: SmartLayoutRecommendationRead) =>
      executeSmartLayoutRecommendation(api, rec.id, "standard"),
    onSuccess: async (result) => {
      messageApi.success("Run queued from approved recommendation");
      await refreshQueries();
      navigate(`/runs/${result.run_id}`);
    },
    onError: (error: Error) => messageApi.error(error.message)
  });

  const tierItems = tiers.data?.items ?? [];
  const recommendationItems = recommendations.data?.items ?? [];

  const tierColumns = [
    {
      title: "Table",
      key: "table",
      render: (_: unknown, row: DetectionTierRowRead) => (
        <Button type="link" style={{ padding: 0 }} onClick={(e) => {
          e.stopPropagation();
          navigate(`/tables/${row.table_id}`);
        }}>
          {row.schema_name}.{row.table_name}
        </Button>
      )
    },
    {
      title: "Tier",
      key: "tier",
      render: (_: unknown, row: DetectionTierRowRead) => (
        <Tag color={tierColor[row.tier] ?? "default"}>{row.tier.toUpperCase()}</Tag>
      )
    },
    {
      title: "Score",
      dataIndex: "score",
      key: "score",
      render: (score: number | null) => (score == null ? <Typography.Text type="secondary">Not scored</Typography.Text> : score)
    },
    { title: "Commits (24h)", dataIndex: "commits_24h", key: "commits_24h" },
    { title: "Commits (7d)", dataIndex: "commits_7d", key: "commits_7d" },
    { title: "Delete %", dataIndex: "delete_file_pct", key: "delete_file_pct" },
    { title: "Queries (30d)", dataIndex: "query_count_30d", key: "query_count_30d" },
    {
      title: "",
      key: "inspect",
      width: 96,
      render: (_: unknown, row: DetectionTierRowRead) => (
        <Button size="small" onClick={(e) => {
          e.stopPropagation();
          setInspectTable(row);
        }}>
          Inspect ROI
        </Button>
      )
    }
  ];

  const recColumns = [
    {
      title: "Table",
      key: "table_ref",
      render: (_: unknown, row: SmartLayoutRecommendationRead) => (
        <Button type="link" onClick={() => navigate(`/tables/${row.table_id}`)}>
          {row.table_ref}
        </Button>
      )
    },
    { title: "Strategy", dataIndex: "recommended_strategy", key: "recommended_strategy" },
    {
      title: "Columns",
      key: "recommended_columns",
      render: (_: unknown, row: SmartLayoutRecommendationRead) =>
        row.recommended_columns?.length ? row.recommended_columns.join(", ") : "-"
    },
    {
      title: "Status",
      key: "status",
      render: (_: unknown, row: SmartLayoutRecommendationRead) => (
        <Tag>{row.status.toUpperCase()}</Tag>
      )
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, row: SmartLayoutRecommendationRead) => (
        <Space>
          <Button
            size="small"
            onClick={() => approve.mutate(row)}
            disabled={row.status === "applied" || row.status === "approved"}
          >
            Approve
          </Button>
          <Button
            size="small"
            onClick={() => execute.mutate(row)}
            disabled={row.status !== "approved"}
            loading={execute.isPending}
          >
            Execute
          </Button>
          <Button
            size="small"
            danger
            onClick={() => dismiss.mutate(row)}
            disabled={row.status === "rejected" || row.status === "applied"}
          >
            Dismiss
          </Button>
        </Space>
      )
    }
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      {contextHolder}
      <Space size={8} align="center">
        <Typography.Title level={3} style={{ margin: 0 }}>
          Smart Maintenance
        </Typography.Title>
        <TieringHelpButton />
      </Space>
      <Tabs
        items={[
          {
            key: "overview",
            label: "Overview",
            children: (
              <Space direction="vertical" size={16} style={{ width: "100%" }}>
                {overview.isError ? (
                  <Alert
                    type="error"
                    showIcon
                    message="Couldn't load Smart Maintenance overview"
                    description={
                      overview.error instanceof Error ? overview.error.message : "Unknown error"
                    }
                    action={
                      <Button size="small" onClick={() => void overview.refetch()}>
                        Retry
                      </Button>
                    }
                  />
                ) : null}
                <Row gutter={[12, 12]}>
                  <Col xs={24} md={8}>
                    <Card>
                      <Statistic
                        title="Tables With Stats"
                        value={overview.data?.tables_with_stats ?? 0}
                        loading={overview.isLoading}
                      />
                    </Card>
                  </Col>
                  <Col xs={24} md={8}>
                    <Card>
                      <Statistic
                        title="ROI Shadow Decisions (30d)"
                        value={overview.data?.roi_shadow_decisions_30d ?? 0}
                        loading={overview.isLoading}
                      />
                    </Card>
                  </Col>
                  <Col xs={24} md={8}>
                    <Card>
                      <Statistic
                        title="Pending Layout Recommendations"
                        value={overview.data?.pending_layout_recommendations ?? 0}
                        loading={overview.isLoading}
                      />
                    </Card>
                  </Col>
                </Row>
                {overview.data ? (
                  <Descriptions bordered size="small" column={5}>
                    <Descriptions.Item label="HOT">{overview.data.tiers.hot}</Descriptions.Item>
                    <Descriptions.Item label="WARM">{overview.data.tiers.warm}</Descriptions.Item>
                    <Descriptions.Item label="COLD">{overview.data.tiers.cold}</Descriptions.Item>
                    <Descriptions.Item label="DORMANT">{overview.data.tiers.dormant}</Descriptions.Item>
                    <Descriptions.Item label="UNKNOWN">{overview.data.tiers.unknown}</Descriptions.Item>
                  </Descriptions>
                ) : null}
                {recommendations.isError ? (
                  <Alert
                    type="error"
                    showIcon
                    message="Couldn't load layout recommendations"
                    description={
                      recommendations.error instanceof Error
                        ? recommendations.error.message
                        : "Unknown error"
                    }
                    action={
                      <Button size="small" onClick={() => void recommendations.refetch()}>
                        Retry
                      </Button>
                    }
                  />
                ) : (
                  <Card title="Layout Recommendations">
                    <Table
                      rowKey="id"
                      loading={recommendations.isLoading}
                      dataSource={recommendationItems}
                      columns={recColumns}
                      pagination={{ pageSize: 10 }}
                    />
                  </Card>
                )}
              </Space>
            )
          },
          {
            key: "tiers",
            label: "Detection Tiers",
            children: (
              <Card>
                {tiers.isError ? (
                  <Alert
                    type="error"
                    showIcon
                    message="Couldn't load detection tiers"
                    description={tiers.error instanceof Error ? tiers.error.message : "Unknown error"}
                    action={
                      <Button size="small" onClick={() => void tiers.refetch()}>
                        Retry
                      </Button>
                    }
                    style={{ marginBottom: 12 }}
                  />
                ) : (
                  <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
                    Click a row, or use Inspect ROI, to see the ROI decision and workload forecast
                    behind a table&apos;s tier without leaving this list.
                  </Typography.Paragraph>
                )}
                <Table
                  rowKey="table_id"
                  loading={tiers.isLoading}
                  dataSource={tierItems}
                  columns={tierColumns}
                  onRow={(record) => ({
                    onClick: () => setInspectTable(record),
                    style: { cursor: "pointer" }
                  })}
                  pagination={{ pageSize: 20 }}
                />
              </Card>
            )
          }
        ]}
      />
      <Drawer
        title={inspectTable ? `ROI decision · ${inspectTable.schema_name}.${inspectTable.table_name}` : "ROI decision"}
        width={560}
        open={inspectTable !== null}
        onClose={() => setInspectTable(null)}
        destroyOnClose
      >
        {inspectTable ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Button
              type="link"
              style={{ padding: 0 }}
              onClick={() => navigate(`/tables/${inspectTable.table_id}`)}
            >
              Open table detail
            </Button>
            <Card loading={roi.isLoading} title="ROI Breakdown" size="small">
              {roi.data ? (
                <Descriptions bordered size="small" column={1}>
                  <Descriptions.Item label="Action">{roi.data.decision.action}</Descriptions.Item>
                  <Descriptions.Item label="Reason">{roi.data.decision.reason}</Descriptions.Item>
                  <Descriptions.Item label="BCR">{roi.data.decision.bcr.toFixed(3)}</Descriptions.Item>
                  <Descriptions.Item label="Threshold">
                    {roi.data.decision.threshold.toFixed(3)}
                  </Descriptions.Item>
                  <Descriptions.Item label="Benefit (USD/month)">
                    {roi.data.monthly_benefit_usd.toFixed(2)}
                  </Descriptions.Item>
                  <Descriptions.Item label="Cost (USD/month)">
                    {roi.data.monthly_cost_usd.toFixed(2)}
                  </Descriptions.Item>
                </Descriptions>
              ) : (
                <Empty description="No ROI data available for this table yet." />
              )}
            </Card>
            {roi.data?.effectiveness ? (
              <Card size="small" title="Compaction effectiveness">
                <Space wrap>
                  <Tag color={(roi.data.effectiveness.effectiveness_ratio ?? 0) >= 10 ? "success" : "gold"}>
                    Ratio {(roi.data.effectiveness.effectiveness_ratio ?? 0).toFixed(1)}×
                  </Tag>
                  <Tag>{roi.data.effectiveness.effectiveness}</Tag>
                  <Tag>{roi.data.effectiveness.run_count ?? 0} runs</Tag>
                </Space>
              </Card>
            ) : null}
            {roi.data?.top_moop_partitions?.length ? (
              <Card size="small" title="Top MOOP-ranked partitions (AutoComp §A2)">
                <Table
                  rowKey={(row) => `${row.partition_value}-${row.moop_score}`}
                  dataSource={roi.data.top_moop_partitions}
                  size="small"
                  pagination={false}
                  columns={[
                    { title: "Partition", dataIndex: "partition_value", key: "partition_value" },
                    {
                      title: "MOOP score",
                      dataIndex: "moop_score",
                      key: "moop_score",
                      align: "right",
                      render: (value: number | null | undefined) => (value != null ? value.toFixed(3) : "—")
                    },
                    {
                      title: "Small files",
                      dataIndex: "small_files",
                      key: "small_files",
                      align: "right"
                    }
                  ]}
                />
              </Card>
            ) : null}
            <Card loading={forecast.isLoading} title="Workload Forecast (next 24h)" size="small">
              {forecast.data?.points?.length ? (
                <Table
                  rowKey="hour_start"
                  dataSource={forecast.data.points}
                  size="small"
                  pagination={false}
                  columns={[
                    { title: "Hour", dataIndex: "hour_start", key: "hour_start" },
                    {
                      title: "Expected Queries",
                      dataIndex: "expected_queries",
                      key: "expected_queries"
                    }
                  ]}
                />
              ) : (
                <Empty description="No forecast available for this table yet." />
              )}
            </Card>
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
}
