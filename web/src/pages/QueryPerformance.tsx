import { useQuery } from "@tanstack/react-query";
import {
  Card,
  Row,
  Col,
  Table,
  Tag,
  Typography,
  Statistic,
  Select,
  Space,
  Spin,
  Tooltip,
} from "antd";
import {
  ClockCircleOutlined,
  DatabaseOutlined,
  WarningOutlined,
  CheckCircleOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useState } from "react";
import { getQueryPerformanceDashboard, type QueryPerformanceData } from "../api/client";
import { PageHeader, QueryErrorAlert } from "../components/common";
import { useAuth } from "../lib/auth";

const { Text } = Typography;

const formatBytes = (bytes: number | null) => {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

const formatDuration = (ms: number) => {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
};

export const QueryPerformance = () => {
  const { api } = useAuth();
  const [engine, setEngine] = useState<string | undefined>(undefined);
  const [hours, setHours] = useState(24);

  const { data, isLoading, error } = useQuery<QueryPerformanceData>({
    queryKey: ["query-performance", api.token, engine, hours],
    queryFn: () => getQueryPerformanceDashboard(api, hours, engine),
    refetchInterval: 30000
  });

  const columns = [
    {
      title: "Query",
      dataIndex: "query_text",
      key: "query_text",
      ellipsis: true,
      width: "35%",
      render: (text: string | null) => (
        <Tooltip title={text}>
          <Text code style={{ fontSize: "11px" }}>
            {text ? text.slice(0, 80) + (text.length > 80 ? "..." : "") : "—"}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "Engine",
      dataIndex: "engine",
      key: "engine",
      width: "10%",
      render: (engine: string) => (
        <Tag color="blue">{engine.toUpperCase()}</Tag>
      ),
    },
    {
      title: "Duration",
      dataIndex: "duration_ms",
      key: "duration_ms",
      width: "10%",
      sorter: (a: any, b: any) => a.duration_ms - b.duration_ms,
      render: (ms: number) => {
        const color = ms > 10000 ? "red" : ms > 5000 ? "orange" : "green";
        return (
          <Tag color={color} icon={<ClockCircleOutlined />}>
            {formatDuration(ms)}
          </Tag>
        );
      },
    },
    {
      title: "Data Scanned",
      dataIndex: "bytes_scanned",
      key: "bytes_scanned",
      width: "12%",
      render: (bytes: number | null) => formatBytes(bytes),
    },
    {
      title: "Rows",
      dataIndex: "rows_produced",
      key: "rows_produced",
      width: "10%",
      render: (rows: number | null) =>
        rows ? rows.toLocaleString() : "—",
    },
    {
      title: "Status",
      dataIndex: "status",
      key: "status",
      width: "10%",
      render: (status: string) => {
        const icon = status === "failed" ? <WarningOutlined /> : <CheckCircleOutlined />;
        const color = status === "failed" ? "red" : "green";
        return (
          <Tag color={color} icon={icon}>
            {status.toUpperCase()}
          </Tag>
        );
      },
    },
    {
      title: "Table",
      dataIndex: "table_name",
      key: "table_name",
      width: "13%",
      ellipsis: true,
      render: (name: string | null) => name || "—",
    },
  ];

  return (
    <div>
      <PageHeader
        title="Query performance"
        subtitle="Monitor query latency, throughput, and failure rates."
        actions={
          <Space>
            <Select
              style={{ width: 120 }}
              placeholder="All Engines"
              allowClear
              value={engine}
              onChange={setEngine}
              options={[
                { label: "Trino", value: "trino" },
                { label: "Spark", value: "spark" },
                { label: "Athena", value: "athena" },
                { label: "DuckDB", value: "duckdb" }
              ]}
            />
            <Select
              style={{ width: 120 }}
              value={hours}
              onChange={setHours}
              options={[
                { label: "Last 1 hour", value: 1 },
                { label: "Last 6 hours", value: 6 },
                { label: "Last 24 hours", value: 24 },
                { label: "Last 7 days", value: 168 }
              ]}
            />
          </Space>
        }
      />

      <QueryErrorAlert error={error} title="Failed to load query performance" />

      {isLoading ? (
        <div style={{ textAlign: "center", padding: "60px" }}>
          <Spin size="large" />
        </div>
      ) : (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={24} sm={12} md={8} lg={4}>
              <Card>
                <Statistic
                  title="Total Queries"
                  value={data?.total || 0}
                  suffix="queries"
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={8} lg={4}>
              <Card>
                <Statistic
                  title="P50 Latency"
                  value={data?.p50_latency_ms || 0}
                  suffix="ms"
                  precision={0}
                  valueStyle={{ color: "#3f8600" }}
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={8} lg={4}>
              <Card>
                <Statistic
                  title="P95 Latency"
                  value={data?.p95_latency_ms || 0}
                  suffix="ms"
                  precision={0}
                  valueStyle={{ color: "#cf1322" }}
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={8} lg={4}>
              <Card>
                <Statistic
                  title="P99 Latency"
                  value={data?.p99_latency_ms || 0}
                  suffix="ms"
                  precision={0}
                  valueStyle={{ color: "#cf1322" }}
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={8} lg={4}>
              <Card>
                <Statistic
                  title="Avg Data Scanned"
                  value={formatBytes(data?.avg_bytes_scanned || null)}
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={8} lg={4}>
              <Card>
                <Statistic
                  title="Failed Queries"
                  value={data?.failed_count || 0}
                  suffix={`/ ${data?.total || 0}`}
                  valueStyle={{
                    color: (data?.failed_count || 0) > 0 ? "#cf1322" : "#3f8600",
                  }}
                />
              </Card>
            </Col>
          </Row>

          <Card title="Top Slowest Queries" extra={<DatabaseOutlined />}>
            <Table
              columns={columns}
              dataSource={data?.queries || []}
              rowKey="query_id"
              pagination={{
                pageSize: 20,
                showSizeChanger: true,
                showTotal: (total) => `${total} queries`,
              }}
              size="small"
            />
          </Card>
        </>
      )}
    </div>
  );
};
