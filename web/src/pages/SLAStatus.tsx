import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Card,
  Table,
  Tag,
  Typography,
  Switch,
  Space,
  Alert,
  Progress,
  Tooltip,
  type TableColumnsType,
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
import { Link } from "react-router-dom";
import { getSlaStatusDashboard, type SLAResponse } from "../api/client";
import { OverviewPage } from "../components/layout/OverviewPage";
import { StatStrip } from "../components/common";
import { useAuth } from "../lib/auth";
import { formatSlaDays as formatDays, formatSlaHours as formatHours } from "../lib/format";
import { cryoColors } from "../lib/theme";

const { Text } = Typography;

type SLAStatusRow = SLAResponse["slas"][number];

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
  const { api } = useAuth();
  const [breachedOnly, setBreachedOnly] = useState(false);

  const { data, isLoading, error } = useQuery<SLAResponse>({
    queryKey: ["sla-status", api.token, breachedOnly],
    queryFn: () => getSlaStatusDashboard(api, breachedOnly),
    refetchInterval: 60000
  });

  const columns: TableColumnsType<SLAStatusRow> = [
    {
      title: "Table",
      dataIndex: "table_name",
      key: "table_name",
      width: "25%",
      ellipsis: true,
      fixed: "left" as const,
      render: (name: string, record: SLAStatusRow) => (
        <Link to={`/tables/${record.table_id}`}>{name}</Link>
      ),
    },
    {
      title: "Data freshness",
      key: "freshness",
      width: "22%",
      responsive: ["md"],
      render: (_: any, record: SLAStatusRow) => {
        const breached = record.freshness_breached;
        const percent = getCompliancePercent(record.freshness_hours, record.freshness_sla_hours);
        const scanLag = record.scan_staleness_hours;
        return (
          <Space orientation="vertical" size={2} style={{ width: "100%" }}>
            <Space>
              {breached ? (
                <WarningOutlined style={{ color: cryoColors.gradeF }} />
              ) : (
                <CheckCircleOutlined style={{ color: cryoColors.gradeA }} />
              )}
              <Tooltip title="Hours since last Iceberg snapshot commit">
                <Text type={breached ? "danger" : "secondary"}>
                  {formatHours(record.freshness_hours)} / {formatHours(record.freshness_sla_hours)}
                </Text>
              </Tooltip>
            </Space>
            {scanLag !== null && scanLag !== undefined ? (
              <Tooltip title="Hours since Cryo last scanned this table (observability only)">
                <Text type="secondary" style={{ fontSize: 11 }}>
                  Scan lag: {formatHours(scanLag)}
                </Text>
              </Tooltip>
            ) : null}
            <Progress
              percent={percent}
              strokeColor={breached ? cryoColors.gradeF : cryoColors.gradeA}
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
      responsive: ["lg"],
      render: (_: any, record: SLAStatusRow) => {
        const breached = record.maintenance_breached;
        const percent = getCompliancePercent(record.maintenance_days, record.maintenance_sla_days);
        return (
          <Space orientation="vertical" size={2} style={{ width: "100%" }}>
            <Space>
              {breached ? (
                <WarningOutlined style={{ color: cryoColors.gradeF }} />
              ) : (
                <CheckCircleOutlined style={{ color: cryoColors.gradeA }} />
              )}
              <Text type={breached ? "danger" : "secondary"}>
                {formatDays(record.maintenance_days)} / {formatDays(record.maintenance_sla_days)}
              </Text>
            </Space>
            <Progress
              percent={percent}
              strokeColor={breached ? cryoColors.gradeF : cryoColors.gradeA}
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
      responsive: ["xl"],
      render: (_: any, record: SLAStatusRow) => {
        const breached = record.p95_latency_breached;
        const percent = getCompliancePercent(record.p95_latency_ms, record.p95_latency_sla_ms);
        return (
          <Space orientation="vertical" size={2} style={{ width: "100%" }}>
            <Space>
              {breached ? (
                <WarningOutlined style={{ color: cryoColors.gradeF }} />
              ) : (
                <CheckCircleOutlined style={{ color: cryoColors.gradeA }} />
              )}
              <Text type={breached ? "danger" : "secondary"}>
                {formatLatency(record.p95_latency_ms)} / {formatLatency(record.p95_latency_sla_ms)}
              </Text>
            </Space>
            <Progress
              percent={percent}
              strokeColor={breached ? cryoColors.gradeF : cryoColors.gradeA}
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
      render: (_: any, record: SLAStatusRow) => {
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
    <OverviewPage
      title="SLA monitoring"
      subtitle="Freshness uses last Iceberg snapshot commit time; scan staleness shows Cryo scan lag."
      actions={
        <Space>
          <Text>Show breached only:</Text>
          <Switch checked={breachedOnly} onChange={setBreachedOnly} />
          <Link to="/tables">
            <Button>Open table explorer</Button>
          </Link>
        </Space>
      }
      loading={isLoading}
      error={error}
      errorTitle="Failed to load SLA status"
      kpis={
        <StatStrip
          items={[
            { label: "Total monitored", value: data?.total_monitored ?? 0, suffix: "tables" },
            { label: "SLA compliance", value: `${complianceRate.toFixed(1)}%` },
            { label: "Breached", value: `${data?.total_breached ?? 0} / ${data?.total_monitored ?? 0}` },
            {
              label: "Healthy",
              value: `${(data?.total_monitored ?? 0) - (data?.total_breached ?? 0)} / ${data?.total_monitored ?? 0}`
            }
          ]}
        />
      }
    >
      {(data?.total_breached || 0) > 0 && !breachedOnly ? (
        <Alert
          title="SLA breaches detected"
          description={`${data?.total_breached} table(s) currently breach SLA thresholds. Use breached-only mode for focused triage.`}
          type="warning"
          showIcon
          icon={<ExclamationCircleOutlined />}
          style={{ marginBottom: 8 }}
        />
      ) : null}

      <Card
        title="SLA status by table"
        extra={
          <Space>
            <Tooltip title="Last Iceberg snapshot commit vs SLA threshold">
              <ClockCircleOutlined />
              <Text type="secondary" style={{ marginLeft: 4 }}>
                Freshness
              </Text>
            </Tooltip>
            <Tooltip title="Days since last maintenance">
              <ToolOutlined />
              <Text type="secondary" style={{ marginLeft: 4 }}>
                Maintenance
              </Text>
            </Tooltip>
            <Tooltip title="P95 query latency in last 24h">
              <ThunderboltOutlined />
              <Text type="secondary" style={{ marginLeft: 4 }}>
                Query
              </Text>
            </Tooltip>
          </Space>
        }
      >
        <Table
          scroll={{ x: 960 }}
          columns={columns}
          dataSource={data?.slas || []}
          rowKey="table_id"
          pagination={{
            pageSize: 20,
            showSizeChanger: true,
            showTotal: (total) => `${total} tables`,
          }}
          size="small"
          rowClassName={(record: SLAStatusRow) => {
            const breachedCount = [
              record.freshness_breached,
              record.maintenance_breached,
              record.p95_latency_breached,
            ].filter(Boolean).length;
            return breachedCount >= 2 ? "row-critical" : breachedCount === 1 ? "row-warning" : "";
          }}
        />
      </Card>
    </OverviewPage>
  );
};
