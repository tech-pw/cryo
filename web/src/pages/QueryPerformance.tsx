import { useQuery } from "@tanstack/react-query";
import {
  Card,
  Table,
  Tag,
  Typography,
  Select,
  Space,
  Tooltip,
} from "antd";
import {
  ClockCircleOutlined,
  DatabaseOutlined,
  WarningOutlined,
  CheckCircleOutlined
} from "@ant-design/icons";
import { useState } from "react";
import { Link } from "react-router-dom";
import { getQueryPerformanceDashboard, type QueryPerformanceData } from "../api/client";
import { StatStrip } from "../components/common";
import { OverviewPage } from "../components/layout/OverviewPage";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatDuration } from "../lib/format";

const { Text } = Typography;

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
        const color = ms > 10000 ? "red" : ms > 5000 ? "orange" : "success";
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
      render: (bytes: number | null) => formatBytesGb(bytes),
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
      render: (name: string | null) =>
        name ? (
          <Link to={`/tables?q=${encodeURIComponent(name)}`}>{name}</Link>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <OverviewPage
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
      loading={isLoading}
      error={error}
      errorTitle="Failed to load query performance"
      kpis={
        <StatStrip
          items={[
            { label: "Total queries", value: data?.total ?? 0, suffix: "queries" },
            { label: "P50 latency", value: `${data?.p50_latency_ms ?? 0} ms` },
            { label: "P95 latency", value: `${data?.p95_latency_ms ?? 0} ms` },
            { label: "P99 latency", value: `${data?.p99_latency_ms ?? 0} ms` },
            { label: "Avg data scanned", value: formatBytesGb(data?.avg_bytes_scanned ?? null) },
            { label: "Failed", value: `${data?.failed_count ?? 0} / ${data?.total ?? 0}` }
          ]}
        />
      }
    >
      <Card title="Top slowest queries" extra={<DatabaseOutlined />}>
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
    </OverviewPage>
  );
};
