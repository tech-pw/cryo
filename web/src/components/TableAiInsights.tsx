import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Row, Space, Table, Tabs, Tag, Typography, message } from "antd";
import { Link } from "react-router-dom";
import {
  explainTableHealth,
  getAnomalyRootCauseBrief,
  getAutoCompCompare,
  getDataQualityProfile,
  getSimilarTables,
  getTableAnomalies,
  getTableHealthClassification,
  getTablePiiClassification,
  recordSkillsetOutcome
} from "../api/client";
import type { SimilarTableRead, UUID } from "../api/types";
import { EmptyState } from "../components/common";
import { useAuth } from "../lib/auth";
import { schemaTableName } from "../lib/tableLabels";
import { formatTime } from "../lib/format";

interface TableAiInsightsProps {
  tableId: UUID;
  lastSnapshotCommittedAt?: string | null;
  embedded?: boolean;
  visibleTabs?: Array<"pii" | "similar" | "risk" | "anomalies" | "dq" | "autocomp">;
  hideTabsWhenSingle?: boolean;
}

export const TableAiInsights = ({
  tableId,
  lastSnapshotCommittedAt,
  embedded = false,
  visibleTabs,
  hideTabsWhenSingle = false
}: TableAiInsightsProps) => {
  const { api } = useAuth();

  const pii = useQuery({
    queryKey: ["table-pii", tableId],
    queryFn: () => getTablePiiClassification(api, tableId),
    retry: false
  });
  const similar = useQuery({
    queryKey: ["table-similar", tableId],
    queryFn: () => getSimilarTables(api, tableId),
    retry: false
  });
  const health = useQuery({
    queryKey: ["table-health-classification", tableId],
    queryFn: () => getTableHealthClassification(api, tableId),
    retry: false
  });
  const anomalies = useQuery({
    queryKey: ["table-anomalies", tableId],
    queryFn: () => getTableAnomalies(api, tableId),
    retry: false
  });
  const anomalyBrief = useQuery({
    queryKey: ["table-anomaly-brief", tableId],
    queryFn: () => getAnomalyRootCauseBrief(api, tableId),
    retry: false
  });
  const dataQuality = useQuery({
    queryKey: ["table-data-quality", tableId],
    queryFn: () => getDataQualityProfile(api, tableId),
    retry: false
  });
  const autocomp = useQuery({
    queryKey: ["table-autocomp", tableId],
    queryFn: () => getAutoCompCompare(api, tableId),
    retry: false
  });

  const explain = useMutation({
    mutationFn: () => explainTableHealth(api, tableId),
    onSuccess: (result) => {
      message.info(result.explanation, 8);
    },
    onError: (error) => message.error((error as Error).message)
  });
  const anomalyFeedback = useMutation({
    mutationFn: (decision: "accepted" | "rejected") =>
      recordSkillsetOutcome(api, "iceberg_advisor", {
        prompt_name: "anomaly_root_cause_brief",
        recommendation:
          anomalyBrief.data?.hypotheses?.[0]?.title ??
          "No anomaly hypothesis available",
        decision,
        table_id: tableId
      }),
    onSuccess: () => message.success("Feedback recorded"),
    onError: (error) => message.error((error as Error).message)
  });

  const loading =
    pii.isLoading || similar.isLoading || health.isLoading || anomalies.isLoading || dataQuality.isLoading;

  const similarColumns = [
    {
      title: "Table",
      key: "name",
      render: (_: unknown, record: SimilarTableRead) => (
        <Link to={`/tables/${record.table_id}`}>
          {schemaTableName(record)}
        </Link>
      ),
      ellipsis: true
    },
    {
      title: "Match",
      dataIndex: "similarity",
      align: "right" as const,
      render: (v: number) => `${(v * 100).toFixed(0)}%`
    }
  ];

  const allTabItems = [
    {
      key: "pii",
      label: "PII",
      children: (pii.data?.matches ?? []).length ? (
        <Table
          size="small"
          pagination={false}
          rowKey="column_name"
          dataSource={pii.data?.matches ?? []}
          columns={[
            { title: "Column", dataIndex: "column_name", ellipsis: true },
            { title: "Category", dataIndex: "pii_category", render: (v) => <Tag>{v}</Tag> },
            {
              title: "Score",
              dataIndex: "similarity",
              align: "right",
              render: (v: number) => v.toFixed(2)
            },
            {
              title: "Tagged",
              dataIndex: "already_tagged",
              render: (v: boolean) => (v ? <Tag color="green">yes</Tag> : <Tag>no</Tag>)
            }
          ]}
        />
      ) : (
        <EmptyState title="No PII signals" description="No likely PII columns from metadata embeddings." />
      )
    },
    {
      key: "similar",
      label: "Similar tables",
      children: (similar.data?.similar ?? []).length ? (
        <Table
          size="small"
          pagination={false}
          rowKey="table_id"
          dataSource={similar.data?.similar ?? []}
          columns={similarColumns}
        />
      ) : (
        <EmptyState title="No matches" description="No fleet tables above the similarity threshold." />
      )
    },
    {
      key: "risk",
      label: "Compaction risk",
      children: health.data ? (
        <Space orientation="vertical">
          <Tag color={health.data.needs_emergency_compaction ? "error" : "success"}>
            {health.data.needs_emergency_compaction ? "Elevated emergency risk" : "Normal"}
          </Tag>
          <Typography.Text type="secondary">
            {(health.data.probability * 100).toFixed(0)}% probability · {health.data.method}
          </Typography.Text>
        </Space>
      ) : (
        <EmptyState title="Unavailable" description="Classifier needs table stats and fleet examples." />
      )
    },
    {
      key: "anomalies",
      label: `Anomalies${anomalies.data?.anomalies.length ? ` (${anomalies.data.anomalies.length})` : ""}`,
      children: (
        <Space orientation="vertical" className="full-width">
          {anomalyBrief.data ? (
            <Alert
              type="info"
              showIcon
              title="Root-cause brief"
              description={
                <Space orientation="vertical" size={4}>
                  {anomalyBrief.data.hypotheses.map((hypothesis) => (
                    <div key={hypothesis.title}>
                      <Typography.Text strong>
                        {hypothesis.title} ({Math.round(hypothesis.confidence * 100)}%)
                      </Typography.Text>
                      <br />
                      <Typography.Text type="secondary">{hypothesis.because}</Typography.Text>
                    </div>
                  ))}
                  {anomalyBrief.data.evidence.length ? (
                    <Typography.Text type="secondary">
                      Evidence: {anomalyBrief.data.evidence.join(" | ")}
                    </Typography.Text>
                  ) : null}
                  <Space>
                    <Button
                      size="small"
                      loading={anomalyFeedback.isPending}
                      onClick={() => anomalyFeedback.mutate("accepted")}
                    >
                      Helpful
                    </Button>
                    <Button
                      size="small"
                      loading={anomalyFeedback.isPending}
                      onClick={() => anomalyFeedback.mutate("rejected")}
                    >
                      Not helpful
                    </Button>
                  </Space>
                </Space>
              }
            />
          ) : null}
          {(anomalies.data?.anomalies ?? []).length ? (
            <Table
              size="small"
              pagination={false}
              rowKey={(row) => `${row.metric}-${row.z_score}`}
              dataSource={anomalies.data?.anomalies ?? []}
              columns={[
                { title: "Metric", dataIndex: "metric" },
                {
                  title: "Value",
                  dataIndex: "value",
                  align: "right",
                  render: (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 })
                },
                {
                  title: "Baseline",
                  dataIndex: "baseline",
                  align: "right",
                  render: (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 })
                },
                {
                  title: "Z",
                  dataIndex: "z_score",
                  align: "right",
                  render: (v: number) => v.toFixed(2)
                },
                {
                  title: "Severity",
                  dataIndex: "severity",
                  render: (v: string) => (
                    <Tag color={v === "critical" ? "red" : v === "warning" ? "orange" : "default"}>{v}</Tag>
                  )
                }
              ]}
            />
          ) : (
            <EmptyState title="No anomalies" description="Stats history looks stable vs recent baseline." />
          )}
        </Space>
      )
    },
    {
      key: "dq",
      label: "Data quality",
      children: dataQuality.data ? (
        <Space orientation="vertical" className="full-width" size={12}>
          {(dataQuality.data.new_columns.length > 0 || dataQuality.data.dropped_columns.length > 0) && (
            <Alert
              type="warning"
              showIcon
              title="Schema drift detected"
              description={
                <Space orientation="vertical" size={0}>
                  {dataQuality.data.new_columns.length ? (
                    <span>New: {dataQuality.data.new_columns.join(", ")}</span>
                  ) : null}
                  {dataQuality.data.dropped_columns.length ? (
                    <span>Dropped: {dataQuality.data.dropped_columns.join(", ")}</span>
                  ) : null}
                </Space>
              }
            />
          )}
          <Typography.Text type="secondary">
            Profiled {formatTime(dataQuality.data.profiled_at)} ·{" "}
            {dataQuality.data.anomalies_detected} column anomalies
          </Typography.Text>
          <Table
            size="small"
            pagination={{ pageSize: 8 }}
            rowKey="column_name"
            dataSource={dataQuality.data.column_metrics}
            columns={[
              { title: "Column", dataIndex: "column_name", ellipsis: true },
              {
                title: "Null %",
                dataIndex: "null_rate_pct",
                align: "right",
                render: (v: number, row) => (
                  <Space size={4}>
                    {v.toFixed(1)}
                    {row.null_rate_spike ? <Tag color="orange">spike</Tag> : null}
                  </Space>
                )
              },
              {
                title: "Baseline null %",
                dataIndex: "null_rate_baseline_pct",
                align: "right",
                render: (v: number | null) => (v != null ? v.toFixed(1) : "—")
              },
              {
                title: "Range",
                key: "range",
                render: (_, row) =>
                  row.min_value != null || row.max_value != null
                    ? `${row.min_value ?? "?"} – ${row.max_value ?? "?"}`
                    : "—"
              }
            ]}
          />
        </Space>
      ) : dataQuality.isError ? (
        <EmptyState title="No quality profile" description="Run a scan to populate column metadata stats." />
      ) : (
        <EmptyState title="Loading" description="Fetching zero-scan quality profile…" />
      )
    },
    {
      key: "autocomp",
      label: "AutoComp",
      children: autocomp.data ? (
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Typography.Text strong>Static partition order</Typography.Text>
            <ul style={{ marginTop: 8, paddingLeft: 18 }}>
              {autocomp.data.static_order.map((p, index) => (
                <li key={`static-${index}-${p ?? "none"}`}>
                  <Typography.Text code>{p}</Typography.Text>
                </li>
              ))}
            </ul>
          </Col>
          <Col xs={24} md={12}>
            <Typography.Text strong>Benefit-score order</Typography.Text>
            <ul style={{ marginTop: 8, paddingLeft: 18 }}>
              {autocomp.data.autocomp_order.map((p, index) => (
                <li key={`auto-${index}-${p ?? "none"}`}>
                  <Typography.Text code>{p}</Typography.Text>
                </li>
              ))}
            </ul>
          </Col>
          <Col span={24}>
            <Tag color={autocomp.data.autocomp_wins ? "success" : "default"}>
              {autocomp.data.autocomp_wins ? "AutoComp wins" : "Static wins"} · ratio{" "}
              {autocomp.data.improvement_ratio.toFixed(2)}×
            </Tag>
          </Col>
        </Row>
      ) : autocomp.isError ? (
        <EmptyState title="Not enough partitions" description="AutoComp compare needs partition stats." />
      ) : (
        <EmptyState title="Loading" description="Comparing scheduling strategies…" />
      )
    }
  ];

  const tabItems = visibleTabs
    ? allTabItems.filter((item) => visibleTabs.includes(item.key as typeof visibleTabs[number]))
    : allTabItems;

  const content = (
    <div className={embedded ? "td-chart-card-content td-chart-card-content-embedded" : "td-chart-card-content"}>
      {lastSnapshotCommittedAt ? (
        <Typography.Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
          Data freshness anchor: last snapshot commit {formatTime(lastSnapshotCommittedAt)}
        </Typography.Text>
      ) : null}

      {hideTabsWhenSingle && tabItems.length === 1 ? tabItems[0]?.children : <Tabs items={tabItems} />}
    </div>
  );

  if (embedded) return content;

  return (
    <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }} loading={loading}>
      <div className="td-chart-card-header">
        <span className="td-chart-card-title">AI & observability</span>
        <div className="td-chart-card-actions">
          <Button size="small" loading={explain.isPending} onClick={() => explain.mutate()}>
            Explain health change
          </Button>
        </div>
      </div>
      {content}
    </Card>
  );
};
