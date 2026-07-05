import { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  message,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography
} from "antd";
import {
  BarChartOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  ExperimentOutlined,
  FilterOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SortAscendingOutlined,
  ThunderboltOutlined
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../lib/auth";
import {
  ClusteringPlanResponse,
  createClusteringPlan,
  getLayoutRecommendation,
  getTableQueryPatterns,
  ingestQueryTelemetry,
  LayoutRecommendationRead,
  listLayoutRecommendations,
  listSources,
  listTables,
  QueryPatternRead,
  updateRecommendationStatus
} from "../api/client";
import type { SourceRead, TableRead } from "../api/types";

const { Text, Paragraph } = Typography;

const LayoutOptimization = () => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [clusteringDrawer, setClusteringDrawer] = useState<{
    tableId: string;
    tableName: string;
  } | null>(null);

  // Fetch recommendations
  const recommendations = useQuery({
    queryKey: ["layout-recommendations", token],
    queryFn: () => listLayoutRecommendations(api, { min_confidence: 0.3, limit: 100 })
  });

  // Fetch sources for telemetry ingestion
  const sources = useQuery({
    queryKey: ["sources", token],
    queryFn: () => listSources(api)
  });

  // Fetch tables for dropdown
  const tables = useQuery({
    queryKey: ["tables-for-optimization", token],
    queryFn: () => listTables(api, { limit: 500 })
  });

  // Ingest telemetry mutation
  const ingestMutation = useMutation({
    mutationFn: (sourceId: string) => ingestQueryTelemetry(api, { source_id: sourceId, since_hours: 168 }),
    onSuccess: (data) => {
      message.success(`Ingested ${data.queries_ingested} queries, updated ${data.patterns_updated} patterns`);
      void queryClient.invalidateQueries({ queryKey: ["layout-recommendations"] });
    },
    onError: (e) => message.error((e as Error).message)
  });

  // Update status mutation
  const updateStatusMutation = useMutation({
    mutationFn: ({ recId, status }: { recId: string; status: string }) =>
      updateRecommendationStatus(api, recId, status),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["layout-recommendations"] });
    }
  });

  const strategyColors: Record<string, string> = {
    sort: "blue",
    zorder: "purple",
    none: "default",
    binpack: "green"
  };

  const statusColors: Record<string, string> = {
    pending: "orange",
    applied: "green",
    rejected: "red",
    scheduled: "blue"
  };

  const recommendationColumns = [
    {
      title: "Table",
      key: "table",
      render: (_: unknown, row: LayoutRecommendationRead) => {
        const table = tables.data?.items.find((t) => t.id === row.table_id);
        return table ? `${table.schema_name}.${table.table_name}` : row.table_id;
      }
    },
    {
      title: "Strategy",
      dataIndex: "recommended_strategy",
      render: (strategy: string) => (
        <Tag color={strategyColors[strategy] ?? "default"}>
          {strategy === "zorder" ? "Z-Order" : strategy.toUpperCase()}
        </Tag>
      )
    },
    {
      title: "Columns",
      dataIndex: "recommended_columns",
      render: (cols: Array<{ column: string }>) => (
        <Space size={4} wrap>
          {cols.slice(0, 3).map((c) => (
            <Tag key={c.column}>{c.column}</Tag>
          ))}
          {cols.length > 3 && <Tag>+{cols.length - 3} more</Tag>}
        </Space>
      )
    },
    {
      title: "Improvement",
      dataIndex: "estimated_prune_improvement",
      render: (val: number | null) =>
        val !== null ? (
          <Tooltip title="Estimated file pruning improvement">
            <Text type="success">+{(val * 100).toFixed(0)}%</Text>
          </Tooltip>
        ) : (
          "-"
        )
    },
    {
      title: "Confidence",
      dataIndex: "confidence",
      render: (val: number) => (
        <Progress
          percent={Math.round(val * 100)}
          size="small"
          status={val >= 0.7 ? "success" : val >= 0.4 ? "normal" : "exception"}
          style={{ width: 80 }}
        />
      )
    },
    {
      title: "Queries",
      dataIndex: "analysis_query_count",
      render: (count: number) => <Badge count={count} showZero color="blue" />
    },
    {
      title: "Status",
      dataIndex: "status",
      render: (status: string) => <Tag color={statusColors[status] ?? "default"}>{status}</Tag>
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, row: LayoutRecommendationRead) => (
        <Space>
          <Button
            size="small"
            icon={<CodeOutlined />}
            onClick={() => {
              const table = tables.data?.items.find((t) => t.id === row.table_id);
              if (table) {
                setClusteringDrawer({
                  tableId: row.table_id,
                  tableName: `${table.schema_name}.${table.table_name}`
                });
              }
            }}
          >
            Plan
          </Button>
          {row.status === "pending" && (
            <>
              <Button
                size="small"
                type="primary"
                icon={<CheckCircleOutlined />}
                onClick={() => updateStatusMutation.mutate({ recId: row.id, status: "applied" })}
              >
                Applied
              </Button>
              <Button
                size="small"
                danger
                onClick={() => updateStatusMutation.mutate({ recId: row.id, status: "rejected" })}
              >
                Reject
              </Button>
            </>
          )}
        </Space>
      )
    }
  ];

  // Summary stats
  const pendingCount = recommendations.data?.filter((r) => r.status === "pending").length ?? 0;
  const highConfidenceCount = recommendations.data?.filter((r) => r.confidence >= 0.7).length ?? 0;
  const totalImprovement =
    recommendations.data?.reduce((sum, r) => sum + (r.estimated_prune_improvement ?? 0), 0) ?? 0;

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <Typography.Title level={2} style={{ margin: 0 }}>
            <ExperimentOutlined /> Layout Optimization
          </Typography.Title>
          <Text type="secondary">
            Query-aware data layout recommendations for faster queries
          </Text>
        </div>
        <Space>
          <Select
            placeholder="Select source to ingest telemetry"
            style={{ width: 250 }}
            loading={sources.isLoading}
            options={(sources.data ?? []).map((s: SourceRead) => ({
              label: s.name,
              value: s.id
            }))}
            onSelect={(sourceId: string) => ingestMutation.mutate(sourceId)}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => recommendations.refetch()}
            loading={recommendations.isRefetching}
          >
            Refresh
          </Button>
        </Space>
      </div>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="Pending Recommendations"
              value={pendingCount}
              prefix={<ClockCircleOutlined />}
              valueStyle={{ color: pendingCount > 0 ? "#faad14" : "#52c41a" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="High Confidence"
              value={highConfidenceCount}
              prefix={<ThunderboltOutlined />}
              suffix={`/ ${recommendations.data?.length ?? 0}`}
              valueStyle={{ color: "#1890ff" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="Est. Total Improvement"
              value={totalImprovement * 100}
              precision={0}
              suffix="% pruning"
              prefix={<BarChartOutlined />}
              valueStyle={{ color: "#52c41a" }}
            />
          </Card>
        </Col>
      </Row>

      <Card>
        <Tabs
          items={[
            {
              key: "recommendations",
              label: (
                <span>
                  <SortAscendingOutlined /> Recommendations
                </span>
              ),
              children: (
                <Table<LayoutRecommendationRead>
                  dataSource={recommendations.data}
                  columns={recommendationColumns}
                  rowKey="id"
                  loading={recommendations.isLoading}
                  pagination={{ pageSize: 20 }}
                  locale={{
                    emptyText: (
                      <Empty
                        description={
                          <span>
                            No recommendations yet. Ingest query telemetry to generate
                            recommendations.
                          </span>
                        }
                      />
                    )
                  }}
                />
              )
            },
            {
              key: "patterns",
              label: (
                <span>
                  <FilterOutlined /> Query Patterns
                </span>
              ),
              children: <QueryPatternsTab api={api} tables={tables.data?.items ?? []} />
            }
          ]}
        />
      </Card>

      <ClusteringPlanDrawer
        open={!!clusteringDrawer}
        tableId={clusteringDrawer?.tableId ?? ""}
        tableName={clusteringDrawer?.tableName ?? ""}
        onClose={() => setClusteringDrawer(null)}
        api={api}
      />
    </div>
  );
};

// Query Patterns Tab
const QueryPatternsTab = ({
  api,
  tables
}: {
  api: import("../api/client").ApiOptions;
  tables: TableRead[];
}) => {
  const [selectedTableId, setSelectedTableId] = useState<string | null>(null);

  const patterns = useQuery({
    queryKey: ["query-patterns", selectedTableId],
    queryFn: () => (selectedTableId ? getTableQueryPatterns(api, selectedTableId) : null),
    enabled: !!selectedTableId
  });

  const patternColumns = [
    { title: "Column", dataIndex: "column_name", key: "column" },
    {
      title: "Filter Count",
      dataIndex: "filter_count",
      sorter: (a: QueryPatternRead, b: QueryPatternRead) => a.filter_count - b.filter_count,
      defaultSortOrder: "descend" as const
    },
    {
      title: "Equality",
      dataIndex: "equality_filter_count",
      render: (v: number, row: QueryPatternRead) =>
        row.filter_count > 0 ? `${v} (${Math.round((v / row.filter_count) * 100)}%)` : "0"
    },
    {
      title: "Range",
      dataIndex: "range_filter_count",
      render: (v: number, row: QueryPatternRead) =>
        row.filter_count > 0 ? `${v} (${Math.round((v / row.filter_count) * 100)}%)` : "0"
    },
    { title: "Join Count", dataIndex: "join_count" },
    { title: "Projection Count", dataIndex: "projection_count" }
  ];

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Select
        showSearch
        placeholder="Select a table to view query patterns"
        style={{ width: 400 }}
        filterOption={(input, option) =>
          (option?.label?.toString() ?? "").toLowerCase().includes(input.toLowerCase())
        }
        options={tables.map((t) => ({
          label: `${t.schema_name}.${t.table_name}`,
          value: t.id
        }))}
        onChange={(v) => setSelectedTableId(v)}
      />

      {patterns.data && (
        <>
          <Alert
            type="info"
            showIcon
            message={
              <Space>
                <span>
                  <strong>{patterns.data.total_queries}</strong> queries analyzed
                </span>
                {patterns.data.dominant_filter_column && (
                  <Tag color="blue">
                    Dominant: {patterns.data.dominant_filter_column}
                  </Tag>
                )}
                {patterns.data.multi_column_filter_ratio > 0.3 && (
                  <Tag color="purple">
                    Multi-column: {(patterns.data.multi_column_filter_ratio * 100).toFixed(0)}%
                  </Tag>
                )}
              </Space>
            }
          />
          <Table<QueryPatternRead>
            dataSource={patterns.data.patterns}
            columns={patternColumns}
            rowKey="column_name"
            size="small"
            pagination={false}
          />
        </>
      )}
    </Space>
  );
};

// Clustering Plan Drawer
const ClusteringPlanDrawer = ({
  open,
  tableId,
  tableName,
  onClose,
  api
}: {
  open: boolean;
  tableId: string;
  tableName: string;
  onClose: () => void;
  api: import("../api/client").ApiOptions;
}) => {
  const [strategy, setStrategy] = useState<string>("sort");
  const [plan, setPlan] = useState<ClusteringPlanResponse | null>(null);

  const recommendation = useQuery({
    queryKey: ["layout-rec", tableId],
    queryFn: () => getLayoutRecommendation(api, tableId),
    enabled: open && !!tableId
  });

  const generatePlan = useMutation({
    mutationFn: () =>
      createClusteringPlan(api, tableId, {
        strategy,
        columns: recommendation.data?.recommended_columns.map((c) => c.column)
      }),
    onSuccess: (data) => setPlan(data),
    onError: (e) => message.error((e as Error).message)
  });

  useEffect(() => {
    if (recommendation.data?.recommended_strategy) {
      setStrategy(recommendation.data.recommended_strategy);
    }
  }, [recommendation.data]);

  return (
    <Drawer
      title={`Clustering Plan: ${tableName}`}
      open={open}
      onClose={() => {
        onClose();
        setPlan(null);
      }}
      width={700}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="large">
        {recommendation.data && (
          <Alert
            type="info"
            showIcon
            icon={<ExperimentOutlined />}
            message="Recommendation"
            description={
              <Space direction="vertical">
                <Text>
                  Strategy:{" "}
                  <Tag color={recommendation.data.recommended_strategy === "zorder" ? "purple" : "blue"}>
                    {recommendation.data.recommended_strategy.toUpperCase()}
                  </Tag>
                </Text>
                <Text>
                  Columns:{" "}
                  {recommendation.data.recommended_columns.map((c) => (
                    <Tag key={c.column}>{c.column}</Tag>
                  ))}
                </Text>
                <Text>
                  Estimated improvement:{" "}
                  <Text type="success">
                    +{((recommendation.data.estimated_prune_improvement ?? 0) * 100).toFixed(0)}%
                  </Text>{" "}
                  file pruning
                </Text>
                <Text>
                  Confidence: {(recommendation.data.confidence * 100).toFixed(0)}%
                </Text>
              </Space>
            }
          />
        )}

        <Space>
          <Select
            value={strategy}
            onChange={setStrategy}
            style={{ width: 150 }}
            options={[
              { label: "Sort", value: "sort" },
              { label: "Z-Order", value: "zorder" },
              { label: "Binpack", value: "binpack" }
            ]}
          />
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={() => generatePlan.mutate()}
            loading={generatePlan.isPending}
          >
            Generate Plan
          </Button>
        </Space>

        {plan && (
          <Tabs
            items={[
              {
                key: "spark-sql",
                label: "Spark SQL",
                children: (
                  <pre
                    style={{
                      background: "#1e1e1e",
                      color: "#d4d4d4",
                      padding: 16,
                      borderRadius: 8,
                      overflow: "auto",
                      fontSize: 13
                    }}
                  >
                    {plan.spark_sql}
                  </pre>
                )
              },
              {
                key: "spark-python",
                label: "PySpark",
                children: (
                  <pre
                    style={{
                      background: "#1e1e1e",
                      color: "#d4d4d4",
                      padding: 16,
                      borderRadius: 8,
                      overflow: "auto",
                      fontSize: 13
                    }}
                  >
                    {plan.spark_python}
                  </pre>
                )
              },
              {
                key: "trino",
                label: "Trino",
                children: plan.trino_sql ? (
                  <pre
                    style={{
                      background: "#1e1e1e",
                      color: "#d4d4d4",
                      padding: 16,
                      borderRadius: 8,
                      overflow: "auto",
                      fontSize: 13
                    }}
                  >
                    {plan.trino_sql}
                  </pre>
                ) : (
                  <Alert
                    type="warning"
                    message="Trino doesn't support sort/z-order rewrite directly"
                    description="Use Spark to apply sort order, then Trino will benefit from the improved layout."
                  />
                )
              }
            ]}
          />
        )}

        <Paragraph type="secondary">
          Copy the generated SQL and run it in your Spark environment. After execution, mark the
          recommendation as "Applied" to track progress.
        </Paragraph>
      </Space>
    </Drawer>
  );
};

export default LayoutOptimization;
