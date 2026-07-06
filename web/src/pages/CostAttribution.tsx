import { useQuery } from "@tanstack/react-query";
import {
  Card,
  Row,
  Col,
  Table,
  Typography,
  Statistic,
  Select,
  Space,
  Spin,
  Alert,
  Progress,
  Tag,
  Button,
} from "antd";
import {
  DollarOutlined,
  DatabaseOutlined,
  CloudServerOutlined,
  ToolOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useState } from "react";
import { Link } from "react-router-dom";
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from "recharts";
import { getCostAttributionDashboard, type CostAttributionResponse } from "../api/client";
import { PageHeader, QueryErrorAlert } from "../components/common";
import { useAuth } from "../lib/auth";
import { formatCurrency } from "../lib/format";

const { Text } = Typography;

const COLORS = ["#3d6fd4", "#52c41a", "#faad14", "#ff4d4f", "#722ed1", "#13c2c2", "#eb2f96"];

type CostAttributionRow = CostAttributionResponse["attributions"][number];

export const CostAttribution = () => {
  const { api } = useAuth();
  const [selectedMonth, setSelectedMonth] = useState<string | undefined>(undefined);

  const { data, isLoading, error } = useQuery<CostAttributionResponse>({
    queryKey: ["cost-attribution", api.token, selectedMonth],
    queryFn: () => getCostAttributionDashboard(api, selectedMonth),
  });

  // Generate month options (last 12 months)
  const monthOptions = Array.from({ length: 12 }, (_, i) => {
    const date = new Date();
    date.setMonth(date.getMonth() - i);
    const value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
    const label = date.toLocaleDateString("en-US", { month: "long", year: "numeric" });
    return { value, label };
  });

  const columns = [
    {
      title: "Team/Department",
      key: "team",
      width: "20%",
      render: (_: any, record: CostAttributionRow) => (
        <Space>
          <TeamOutlined />
          <Text strong>{record.cost_center || record.department || "Unattributed"}</Text>
        </Space>
      ),
    },
    {
      title: "Tables",
      dataIndex: "table_count",
      key: "table_count",
      width: "10%",
      align: "right" as const,
      render: (count: number) => <Tag icon={<DatabaseOutlined />}>{count}</Tag>,
    },
    {
      title: "Storage",
      dataIndex: "storage_cost_usd",
      key: "storage",
      width: "15%",
      align: "right" as const,
      render: (cost: number) => (
        <Text type="secondary">{formatCurrency(cost)}</Text>
      ),
    },
    {
      title: "Compute",
      dataIndex: "compute_cost_usd",
      key: "compute",
      width: "15%",
      align: "right" as const,
      render: (cost: number) => (
        <Text type="secondary">{formatCurrency(cost)}</Text>
      ),
    },
    {
      title: "Maintenance",
      dataIndex: "maintenance_cost_usd",
      key: "maintenance",
      width: "15%",
      align: "right" as const,
      render: (cost: number) => (
        <Text type="secondary">{formatCurrency(cost)}</Text>
      ),
    },
    {
      title: "Total Cost",
      dataIndex: "total_cost_usd",
      key: "total",
      width: "15%",
      align: "right" as const,
      render: (cost: number, record: CostAttributionRow) => {
        const percent = data ? (cost / data.total_cost_usd) * 100 : 0;
        return (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Text strong>{formatCurrency(cost)}</Text>
            <Progress percent={percent} strokeColor="#3d6fd4" showInfo={false} size="small" />
          </Space>
        );
      },
    },
    {
      title: "% of Total",
      key: "percent",
      width: "10%",
      align: "right" as const,
      render: (_: any, record: CostAttributionRow) => {
        const percent = data ? (record.total_cost_usd / data.total_cost_usd) * 100 : 0;
        return <Text type="secondary">{percent.toFixed(1)}%</Text>;
      },
    },
  ];

  const pieData = (data?.attributions || []).map((item) => ({
    name: item.cost_center || item.department || "Unattributed",
    value: item.total_cost_usd,
  }));

  const attributionRate = data
    ? ((data.total_cost_usd - data.unattributed_cost_usd) / Math.max(1, data.total_cost_usd)) * 100
    : 0;

  return (
    <div>
      <PageHeader
        title="Cost attribution"
        subtitle="Storage, compute, and maintenance costs by team and department."
        actions={
          <Space>
            <Link to="/cost">
              <Button>Savings overview</Button>
            </Link>
            <Select
              style={{ width: 200 }}
              value={selectedMonth}
              onChange={setSelectedMonth}
              options={monthOptions}
              placeholder="Current month"
              allowClear
            />
          </Space>
        }
      />

      <QueryErrorAlert error={error} title="Failed to load cost attribution" />

      {isLoading ? (
        <div style={{ textAlign: "center", padding: "60px" }}>
          <Spin size="large" />
        </div>
      ) : !data || data.attributions.length === 0 ? (
        <Alert
          message="Cost Attribution Not Available"
          description={
            <>
              <p>
                Cost attribution requires custom tags on Source/Connection models:
              </p>
              <ul>
                <li><code>cost_center</code> - Cost center identifier</li>
                <li><code>department</code> - Department name</li>
              </ul>
              <p style={{ marginBottom: 0 }}>
                Once tags are configured, cost data will be aggregated by team/department automatically.
              </p>
            </>
          }
          type="info"
          showIcon
        />
      ) : (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={24} sm={12} md={6}>
              <Card>
                <Statistic
                  title="Total Cost"
                  value={data.total_cost_usd}
                  prefix="$"
                  precision={2}
                  valueStyle={{ color: "#3d6fd4" }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {data.month}
                </Text>
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card>
                <Statistic
                  title="Attribution Rate"
                  value={attributionRate}
                  suffix="%"
                  precision={1}
                  valueStyle={{
                    color: attributionRate >= 90 ? "#52c41a" : attributionRate >= 70 ? "#faad14" : "#ff4d4f",
                  }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Cost assigned to teams
                </Text>
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card>
                <Statistic
                  title="Unattributed"
                  value={data.unattributed_cost_usd}
                  prefix="$"
                  precision={2}
                  valueStyle={{
                    color: data.unattributed_cost_usd > 0 ? "#faad14" : "#52c41a",
                  }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Tables without cost tags
                </Text>
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card>
                <Statistic
                  title="Teams"
                  value={data.attributions.length}
                  prefix={<TeamOutlined />}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Active cost centers
                </Text>
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={24} lg={16}>
              <Card title="Cost by Team">
                <Table
                  columns={columns}
                  dataSource={data.attributions}
                  rowKey={(record) => record.cost_center || record.department || "unattributed"}
                  pagination={false}
                  size="small"
                  summary={(pageData) => {
                    const totalStorage = pageData.reduce((sum, item) => sum + item.storage_cost_usd, 0);
                    const totalCompute = pageData.reduce((sum, item) => sum + item.compute_cost_usd, 0);
                    const totalMaintenance = pageData.reduce((sum, item) => sum + item.maintenance_cost_usd, 0);
                    const totalCost = pageData.reduce((sum, item) => sum + item.total_cost_usd, 0);
                    return (
                      <Table.Summary.Row style={{ backgroundColor: "#fafafa" }}>
                        <Table.Summary.Cell index={0}>
                          <Text strong>TOTAL</Text>
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={1} align="right">
                          <Text strong>{pageData.reduce((sum, item) => sum + item.table_count, 0)}</Text>
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={2} align="right">
                          <Text strong>{formatCurrency(totalStorage)}</Text>
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={3} align="right">
                          <Text strong>{formatCurrency(totalCompute)}</Text>
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={4} align="right">
                          <Text strong>{formatCurrency(totalMaintenance)}</Text>
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={5} align="right">
                          <Text strong>{formatCurrency(totalCost)}</Text>
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={6} align="right">
                          <Text strong>100%</Text>
                        </Table.Summary.Cell>
                      </Table.Summary.Row>
                    );
                  }}
                />
              </Card>
            </Col>
            <Col xs={24} lg={8}>
              <Card title="Cost Distribution">
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart>
                    <Pie
                      data={pieData}
                      dataKey="value"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      outerRadius={100}
                      label={(entry) => `${entry.name}: $${entry.value.toFixed(0)}`}
                    >
                      {pieData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value) => formatCurrency(Number(value ?? 0))} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]}>
            <Col xs={24} lg={8}>
              <Card>
                <Statistic
                  title="Storage Costs"
                  value={data.attributions.reduce((sum, item) => sum + item.storage_cost_usd, 0)}
                  prefix={<DatabaseOutlined />}
                  suffix="USD"
                  precision={2}
                  valueStyle={{ fontSize: "20px" }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  S3 storage at $0.023/GB/month
                </Text>
              </Card>
            </Col>
            <Col xs={24} lg={8}>
              <Card>
                <Statistic
                  title="Compute Costs"
                  value={data.attributions.reduce((sum, item) => sum + item.compute_cost_usd, 0)}
                  prefix={<CloudServerOutlined />}
                  suffix="USD"
                  precision={2}
                  valueStyle={{ fontSize: "20px" }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Query execution costs (Trino/Spark)
                </Text>
              </Card>
            </Col>
            <Col xs={24} lg={8}>
              <Card>
                <Statistic
                  title="Maintenance Costs"
                  value={data.attributions.reduce((sum, item) => sum + item.maintenance_cost_usd, 0)}
                  prefix={<ToolOutlined />}
                  suffix="USD"
                  precision={2}
                  valueStyle={{ fontSize: "20px" }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Compaction and optimization jobs
                </Text>
              </Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
};
