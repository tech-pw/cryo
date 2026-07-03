import { useQuery } from "@tanstack/react-query";
import {
  Card,
  Row,
  Col,
  Table,
  Tag,
  Typography,
  Statistic,
  Progress,
  Space,
  Spin,
  Badge,
} from "antd";
import {
  DatabaseOutlined,
  CloudServerOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  DollarOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from "recharts";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

const { Title, Text } = Typography;

interface CatalogStats {
  catalog_id: string;
  catalog_name: string;
  catalog_type: string;
  table_count: number;
  total_size_gb: number;
  small_files_count: number;
  needs_compaction_count: number;
  monthly_cost_usd: number;
  projected_savings_usd_month: number | null;
  avg_health_score: number;
  enabled: boolean;
}

interface MultiCatalogResponse {
  catalogs: CatalogStats[];
  total_catalogs: number;
  total_tables: number;
  total_cost_usd: number;
}

const formatCurrency = (value: number) =>
  `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const formatSize = (gb: number) => {
  if (gb < 1) return `${(gb * 1024).toFixed(0)} GB`;
  if (gb < 1024) return `${gb.toFixed(1)} GB`;
  return `${(gb / 1024).toFixed(2)} TB`;
};

const getHealthColor = (score: number) => {
  if (score >= 80) return "#52c41a";
  if (score >= 60) return "#faad14";
  return "#ff4d4f";
};

const COLORS = ["#3d6fd4", "#52c41a", "#faad14", "#ff4d4f", "#722ed1", "#13c2c2", "#eb2f96"];

export const MultiCatalog = () => {
  const { token } = useAuth();
  const navigate = useNavigate();

  const { data, isLoading } = useQuery<MultiCatalogResponse>({
    queryKey: ["multi-catalog"],
    queryFn: async () => {
      const response = await fetch("/api/multi-catalog", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("Failed to fetch multi-catalog stats");
      return response.json();
    },
    refetchInterval: 60000, // Refresh every minute
  });

  const columns = [
    {
      title: "Catalog",
      key: "catalog",
      width: "20%",
      render: (_: any, record: CatalogStats) => (
        <Space>
          {record.enabled ? (
            <Badge status="success" />
          ) : (
            <Badge status="default" />
          )}
          <Space direction="vertical" size={0}>
            <Text strong>{record.catalog_name}</Text>
            <Tag color="blue" style={{ fontSize: "10px" }}>
              {record.catalog_type.toUpperCase()}
            </Tag>
          </Space>
        </Space>
      ),
    },
    {
      title: "Tables",
      dataIndex: "table_count",
      key: "tables",
      width: "10%",
      align: "right" as const,
      render: (count: number) => <Text strong>{count.toLocaleString()}</Text>,
    },
    {
      title: "Total Size",
      dataIndex: "total_size_gb",
      key: "size",
      width: "12%",
      align: "right" as const,
      render: (gb: number) => formatSize(gb),
    },
    {
      title: "Small Files",
      dataIndex: "small_files_count",
      key: "small_files",
      width: "12%",
      align: "right" as const,
      render: (count: number) => (
        <Tag color={count > 10000 ? "red" : count > 5000 ? "orange" : "default"}>
          {count.toLocaleString()}
        </Tag>
      ),
    },
    {
      title: "Needs Compaction",
      dataIndex: "needs_compaction_count",
      key: "compaction",
      width: "12%",
      align: "right" as const,
      render: (count: number) => (
        <Tag icon={count > 0 ? <WarningOutlined /> : <CheckCircleOutlined />} color={count > 0 ? "warning" : "success"}>
          {count} tables
        </Tag>
      ),
    },
    {
      title: "Health Score",
      dataIndex: "avg_health_score",
      key: "health",
      width: "14%",
      render: (score: number) => (
        <Space direction="vertical" size={2} style={{ width: "100%" }}>
          <Text style={{ color: getHealthColor(score) }}>{score.toFixed(1)}</Text>
          <Progress
            percent={score}
            strokeColor={getHealthColor(score)}
            showInfo={false}
            size="small"
          />
        </Space>
      ),
    },
    {
      title: "Monthly Cost",
      dataIndex: "monthly_cost_usd",
      key: "cost",
      width: "12%",
      align: "right" as const,
      render: (cost: number) => <Text>{formatCurrency(cost)}</Text>,
    },
    {
      title: "Est. Savings",
      dataIndex: "projected_savings_usd_month",
      key: "savings",
      width: "12%",
      align: "right" as const,
      render: (savings: number | null) => (
        <Text type={savings && savings > 0 ? "success" : "secondary"}>
          {savings ? formatCurrency(savings) : "—"}
        </Text>
      ),
    },
  ];

  const pieData = (data?.catalogs || []).map((catalog) => ({
    name: catalog.catalog_name,
    value: catalog.monthly_cost_usd,
  }));

  return (
    <div>
      <div className="page-header">
        <div>
          <Title level={2}>
            <CloudServerOutlined /> Multi-Catalog Federation
          </Title>
          <Text type="secondary">
            Manage and monitor Iceberg tables across multiple catalogs
          </Text>
        </div>
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
                  title="Total Catalogs"
                  value={data?.total_catalogs || 0}
                  prefix={<CloudServerOutlined />}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Active Iceberg catalogs
                </Text>
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="Total Tables"
                  value={data?.total_tables || 0}
                  prefix={<DatabaseOutlined />}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Across all catalogs
                </Text>
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="Combined Cost"
                  value={data?.total_cost_usd || 0}
                  prefix="$"
                  precision={2}
                  valueStyle={{ color: "#ff4d4f" }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Monthly storage costs
                </Text>
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="Potential Savings"
                  value={
                    data?.catalogs.reduce((sum, c) => sum + (c.projected_savings_usd_month || 0), 0) || 0
                  }
                  prefix="$"
                  precision={2}
                  valueStyle={{ color: "#52c41a" }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Through maintenance
                </Text>
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={24} lg={16}>
              <Card title="Catalogs" extra={<DatabaseOutlined />}>
                <Table
                  columns={columns}
                  dataSource={data?.catalogs || []}
                  rowKey="catalog_id"
                  pagination={false}
                  size="small"
                  onRow={(record) => ({
                    onClick: () => {
                      // Navigate to tables filtered by catalog
                      navigate(`/tables?source=${record.catalog_id}`);
                    },
                    style: { cursor: "pointer" },
                  })}
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
                      label={(entry) => `${entry.name}`}
                    >
                      {pieData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value: number) => formatCurrency(value)} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]}>
            {data?.catalogs.map((catalog) => (
              <Col xs={24} md={12} lg={8} key={catalog.catalog_id}>
                <Card
                  title={catalog.catalog_name}
                  extra={
                    catalog.enabled ? (
                      <Tag color="success" icon={<CheckCircleOutlined />}>
                        ACTIVE
                      </Tag>
                    ) : (
                      <Tag color="default">DISABLED</Tag>
                    )
                  }
                  hoverable
                  onClick={() => navigate(`/tables?source=${catalog.catalog_id}`)}
                  style={{ cursor: "pointer" }}
                >
                  <Space direction="vertical" style={{ width: "100%" }} size={8}>
                    <Row>
                      <Col span={12}>
                        <Statistic
                          title="Tables"
                          value={catalog.table_count}
                          prefix={<FileTextOutlined />}
                          valueStyle={{ fontSize: "18px" }}
                        />
                      </Col>
                      <Col span={12}>
                        <Statistic
                          title="Size"
                          value={formatSize(catalog.total_size_gb)}
                          valueStyle={{ fontSize: "18px" }}
                        />
                      </Col>
                    </Row>
                    <Row>
                      <Col span={12}>
                        <Statistic
                          title="Health"
                          value={catalog.avg_health_score.toFixed(0)}
                          suffix="/100"
                          valueStyle={{
                            fontSize: "18px",
                            color: getHealthColor(catalog.avg_health_score),
                          }}
                        />
                      </Col>
                      <Col span={12}>
                        <Statistic
                          title="Cost/mo"
                          value={formatCurrency(catalog.monthly_cost_usd)}
                          prefix={<DollarOutlined />}
                          valueStyle={{ fontSize: "18px" }}
                        />
                      </Col>
                    </Row>
                  </Space>
                </Card>
              </Col>
            ))}
          </Row>
        </>
      )}
    </div>
  );
};
