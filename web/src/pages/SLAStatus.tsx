import { useQuery } from "@tanstack/react-query";
import {
  Card,
  Row,
  Col,
  Table,
  Tag,
  Typography,
  Statistic,
  Switch,
  Space,
  Spin,
  Alert,
  Progress,
  Tooltip,
} from "antd";
import {
  ClockCircleOutlined,
  ToolOutlined,
  ThunderboltOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  ExclamationCircleOutlined,
} from "@ant-design/icons";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

const { Title, Text } = Typography;

interface SLAStatus {
  table_id: string;
  table_name: string;
  freshness_hours: number | null;
  freshness_sla_hours: number | null;
  freshness_breached: boolean;
  maintenance_days: number | null;
  maintenance_sla_days: number | null;
  maintenance_breached: boolean;
  p95_latency_ms: number | null;
  p95_latency_sla_ms: number | null;
  p95_latency_breached: boolean;
}

interface SLAResponse {
  slas: SLAStatus[];
  total_breached: number;
  total_monitored: number;
}

const formatHours = (hours: number | null) => {
  if (hours === null) return "—";
  if (hours < 1) return `${(hours * 60).toFixed(0)}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
};

const formatDays = (days: number | null) => {
  if (days === null) return "—";
  if (days < 1) return `${(days * 24).toFixed(1)}h`;
  return `${days.toFixed(1)}d`;
};

const formatLatency = (ms: number | null) => {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
};

const getCompliancePercent = (actual: number | null, sla: number | null): number => {
  if (actual === null || sla === null || sla === 0) return 100;
  return Math.min(100, Math.max(0, 100 - (actual / sla) * 100));
};

export const SLAStatus = () => {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [breachedOnly, setBreachedOnly] = useState(false);

  const { data, isLoading } = useQuery<SLAResponse>({
    queryKey: ["sla-status", breachedOnly],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (breachedOnly) params.append("breached_only", "true");

      const response = await fetch(`/api/sla-status?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("Failed to fetch SLA status");
      return response.json();
    },
    refetchInterval: 60000, // Refresh every minute
  });

  const columns = [
    {
      title: "Table",
      dataIndex: "table_name",
      key: "table_name",
      width: "25%",
      ellipsis: true,
      render: (name: string, record: SLAStatus) => (
        <a onClick={() => navigate(`/tables/${record.table_id}`)}>{name}</a>
      ),
    },
    {
      title: "Freshness",
      key: "freshness",
      width: "20%",
      render: (_: any, record: SLAStatus) => {
        const breached = record.freshness_breached;
        const percent = getCompliancePercent(record.freshness_hours, record.freshness_sla_hours);
        return (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Space>
              {breached ? (
                <WarningOutlined style={{ color: "#ff4d4f" }} />
              ) : (
                <CheckCircleOutlined style={{ color: "#52c41a" }} />
              )}
              <Text type={breached ? "danger" : "secondary"}>
                {formatHours(record.freshness_hours)} / {formatHours(record.freshness_sla_hours)}
              </Text>
            </Space>
            <Progress
              percent={percent}
              strokeColor={breached ? "#ff4d4f" : "#52c41a"}
              showInfo={false}
              size="small"
            />
          </Space>
        );
      },
    },
    {
      title: "Maintenance",
      key: "maintenance",
      width: "20%",
      render: (_: any, record: SLAStatus) => {
        const breached = record.maintenance_breached;
        const percent = getCompliancePercent(record.maintenance_days, record.maintenance_sla_days);
        return (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Space>
              {breached ? (
                <WarningOutlined style={{ color: "#ff4d4f" }} />
              ) : (
                <CheckCircleOutlined style={{ color: "#52c41a" }} />
              )}
              <Text type={breached ? "danger" : "secondary"}>
                {formatDays(record.maintenance_days)} / {formatDays(record.maintenance_sla_days)}
              </Text>
            </Space>
            <Progress
              percent={percent}
              strokeColor={breached ? "#ff4d4f" : "#52c41a"}
              showInfo={false}
              size="small"
            />
          </Space>
        );
      },
    },
    {
      title: "P95 Latency",
      key: "latency",
      width: "20%",
      render: (_: any, record: SLAStatus) => {
        const breached = record.p95_latency_breached;
        const percent = getCompliancePercent(record.p95_latency_ms, record.p95_latency_sla_ms);
        return (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Space>
              {breached ? (
                <WarningOutlined style={{ color: "#ff4d4f" }} />
              ) : (
                <CheckCircleOutlined style={{ color: "#52c41a" }} />
              )}
              <Text type={breached ? "danger" : "secondary"}>
                {formatLatency(record.p95_latency_ms)} / {formatLatency(record.p95_latency_sla_ms)}
              </Text>
            </Space>
            <Progress
              percent={percent}
              strokeColor={breached ? "#ff4d4f" : "#52c41a"}
              showInfo={false}
              size="small"
            />
          </Space>
        );
      },
    },
    {
      title: "Status",
      key: "status",
      width: "15%",
      render: (_: any, record: SLAStatus) => {
        const breachedCount = [
          record.freshness_breached,
          record.maintenance_breached,
          record.p95_latency_breached,
        ].filter(Boolean).length;

        if (breachedCount === 0) {
          return <Tag color="success" icon={<CheckCircleOutlined />}>OK</Tag>;
        }
        if (breachedCount === 1) {
          return <Tag color="warning" icon={<ExclamationCircleOutlined />}>WARNING</Tag>;
        }
        return <Tag color="error" icon={<WarningOutlined />}>CRITICAL</Tag>;
      },
    },
  ];

  const complianceRate = data
    ? ((data.total_monitored - data.total_breached) / Math.max(1, data.total_monitored)) * 100
    : 0;

  return (
    <div>
      <div className="page-header">
        <div>
          <Title level={2}>
            <ClockCircleOutlined /> SLA Monitoring
          </Title>
          <Text type="secondary">
            Track freshness, maintenance, and query performance SLAs
          </Text>
        </div>
        <Space>
          <Text>Show breached only:</Text>
          <Switch checked={breachedOnly} onChange={setBreachedOnly} />
        </Space>
      </div>

      {isLoading ? (
        <div style={{ textAlign: "center", padding: "60px" }}>
          <Spin size="large" />
        </div>
      ) : (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col span={6}>
              <Card>
                <Statistic
                  title="Total Monitored"
                  value={data?.total_monitored || 0}
                  suffix="tables"
                  prefix={<ClockCircleOutlined />}
                />
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="SLA Compliance"
                  value={complianceRate.toFixed(1)}
                  suffix="%"
                  valueStyle={{
                    color: complianceRate >= 95 ? "#52c41a" : complianceRate >= 80 ? "#faad14" : "#ff4d4f",
                  }}
                  prefix={<CheckCircleOutlined />}
                />
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="Breached"
                  value={data?.total_breached || 0}
                  suffix={`/ ${data?.total_monitored || 0}`}
                  valueStyle={{
                    color: (data?.total_breached || 0) > 0 ? "#ff4d4f" : "#52c41a",
                  }}
                  prefix={<WarningOutlined />}
                />
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="Healthy"
                  value={(data?.total_monitored || 0) - (data?.total_breached || 0)}
                  suffix={`/ ${data?.total_monitored || 0}`}
                  valueStyle={{ color: "#52c41a" }}
                  prefix={<CheckCircleOutlined />}
                />
              </Card>
            </Col>
          </Row>

          {(data?.total_breached || 0) > 0 && !breachedOnly && (
            <Alert
              message="SLA Breaches Detected"
              description={`${data?.total_breached} table(s) currently breaching SLA thresholds. Enable "Show breached only" to filter.`}
              type="warning"
              showIcon
              icon={<ExclamationCircleOutlined />}
              style={{ marginBottom: 24 }}
            />
          )}

          <Card
            title="SLA Status by Table"
            extra={
              <Space>
                <Tooltip title="Data last updated at table level">
                  <ClockCircleOutlined />
                  <Text type="secondary" style={{ marginLeft: 4 }}>
                    Freshness SLA
                  </Text>
                </Tooltip>
                <Tooltip title="Days since last maintenance">
                  <ToolOutlined />
                  <Text type="secondary" style={{ marginLeft: 4 }}>
                    Maintenance SLA
                  </Text>
                </Tooltip>
                <Tooltip title="P95 query latency in last 24h">
                  <ThunderboltOutlined />
                  <Text type="secondary" style={{ marginLeft: 4 }}>
                    Query SLA
                  </Text>
                </Tooltip>
              </Space>
            }
          >
            <Table
              columns={columns}
              dataSource={data?.slas || []}
              rowKey="table_id"
              pagination={{
                pageSize: 20,
                showSizeChanger: true,
                showTotal: (total) => `${total} tables`,
              }}
              size="small"
              rowClassName={(record: SLAStatus) => {
                const breachedCount = [
                  record.freshness_breached,
                  record.maintenance_breached,
                  record.p95_latency_breached,
                ].filter(Boolean).length;
                return breachedCount >= 2 ? "row-critical" : breachedCount === 1 ? "row-warning" : "";
              }}
            />
          </Card>
        </>
      )}
    </div>
  );
};
