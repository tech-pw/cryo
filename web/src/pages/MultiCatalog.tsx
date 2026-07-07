import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
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
} from "@ant-design/icons";
import { PieChart, Pie, Cell, Tooltip } from "recharts";
import { useNavigate } from "react-router-dom";
import { getMultiCatalogDashboard, type MultiCatalogDashboard } from "../api/client";
import { ChartFrame, PageHeader, QueryErrorAlert } from "../components/common";
import { healthScoreColor } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatCurrency, formatGb } from "../lib/format";
import { useTheme } from "../lib/themeContext";

const { Text } = Typography;

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

type MultiCatalogResponse = MultiCatalogDashboard;

const rightAlign = () => ({ style: { textAlign: "right" as const } });

const NumericCell = ({ children }: { children: ReactNode }) => (
  <div className="catalogs-table-numeric">{children}</div>
);

const TagCell = ({ children }: { children: ReactNode }) => (
  <div className="catalogs-table-tag-cell">{children}</div>
);

export const MultiCatalog = () => {
  const { api } = useAuth();
  const { colors, chartTooltipProps } = useTheme();
  const navigate = useNavigate();

  const costPalette = [
    colors.costCurrent,
    colors.costProjected,
    colors.gradeB,
    colors.gradeC,
    colors.gradeD,
    colors.savings
  ];

  const { data, isLoading, error } = useQuery<MultiCatalogResponse>({
    queryKey: ["multi-catalog", api.token],
    queryFn: () => getMultiCatalogDashboard(api),
    refetchInterval: 60000
  });

  const columns = [
    {
      title: "Catalog",
      key: "catalog",
      width: 180,
      fixed: "left" as const,
      render: (_: unknown, record: CatalogStats) => (
        <Space>
          {record.enabled ? (
            <Badge status="success" />
          ) : (
            <Badge status="default" />
          )}
          <Space orientation="vertical" size={0}>
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
      width: 88,
      align: "right" as const,
      onHeaderCell: rightAlign,
      onCell: rightAlign,
      render: (count: number) => (
        <NumericCell>
          <Text strong>{count.toLocaleString()}</Text>
        </NumericCell>
      ),
    },
    {
      title: "Total Size",
      dataIndex: "total_size_gb",
      key: "size",
      width: 104,
      align: "right" as const,
      onHeaderCell: rightAlign,
      onCell: rightAlign,
      render: (gb: number) => <NumericCell>{formatGb(gb)}</NumericCell>,
    },
    {
      title: "Small Files",
      dataIndex: "small_files_count",
      key: "small_files",
      width: 112,
      align: "right" as const,
      onHeaderCell: rightAlign,
      onCell: rightAlign,
      render: (count: number) => (
        <TagCell>
          <Tag color={count > 10000 ? "red" : count > 5000 ? "orange" : "default"}>
            {count.toLocaleString()}
          </Tag>
        </TagCell>
      ),
    },
    {
      title: "Needs Compaction",
      dataIndex: "needs_compaction_count",
      key: "compaction",
      width: 148,
      align: "right" as const,
      onHeaderCell: rightAlign,
      onCell: rightAlign,
      render: (count: number) => (
        <TagCell>
          <Tag icon={count > 0 ? <WarningOutlined /> : <CheckCircleOutlined />} color={count > 0 ? "warning" : "success"}>
            {count} tables
          </Tag>
        </TagCell>
      ),
    },
    {
      title: "Health Score",
      dataIndex: "avg_health_score",
      key: "health",
      width: 120,
      align: "right" as const,
      onHeaderCell: rightAlign,
      onCell: rightAlign,
      render: (score: number) => (
        <div className="catalogs-table-health">
          <Text style={{ color: healthScoreColor(score) }}>{score.toFixed(1)}</Text>
          <Progress
            percent={score}
            strokeColor={healthScoreColor(score)}
            showInfo={false}
            size="small"
          />
        </div>
      ),
    },
    {
      title: "Monthly Cost",
      dataIndex: "monthly_cost_usd",
      key: "cost",
      width: 116,
      align: "right" as const,
      onHeaderCell: rightAlign,
      onCell: rightAlign,
      render: (cost: number) => (
        <NumericCell>
          <Text style={{ color: colors.costCurrent }}>{formatCurrency(cost)}</Text>
        </NumericCell>
      ),
    },
    {
      title: "Est. Savings",
      dataIndex: "projected_savings_usd_month",
      key: "savings",
      width: 116,
      align: "right" as const,
      onHeaderCell: rightAlign,
      onCell: rightAlign,
      render: (savings: number | null) => (
        <NumericCell>
          <Text style={{ color: savings && savings > 0 ? colors.savings : colors.mutedSubtle }}>
            {savings ? formatCurrency(savings) : "—"}
          </Text>
        </NumericCell>
      ),
    },
  ];

  const catalogs = data?.catalogs || [];
  const pieData = catalogs.map((catalog) => ({
    name: catalog.catalog_name,
    value: catalog.monthly_cost_usd,
  }));
  const totalCost = pieData.reduce((sum, entry) => sum + entry.value, 0);
  const potentialSavings =
    catalogs.reduce((sum, catalog) => sum + (catalog.projected_savings_usd_month || 0), 0) || 0;

  return (
    <div>
      <PageHeader
        title="Multi-catalog federation"
        subtitle="Manage and monitor Iceberg tables across multiple catalogs."
      />

      <QueryErrorAlert error={error} title="Failed to load multi-catalog dashboard" />

      {isLoading ? (
        <div style={{ textAlign: "center", padding: "60px" }}>
          <Spin size="large" />
        </div>
      ) : error ? null : (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={24} sm={12} md={6}>
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
            <Col xs={24} sm={12} md={6}>
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
            <Col xs={24} sm={12} md={6}>
              <Card>
                <Statistic
                  title="Combined Cost"
                  value={data?.total_cost_usd || 0}
                  prefix={<span style={{ color: colors.mutedSubtle }}>$</span>}
                  precision={2}
                  styles={{ content: { color: colors.costCurrent } }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Monthly storage costs
                </Text>
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card>
                <Statistic
                  title="Potential Savings"
                  value={potentialSavings}
                  prefix={<span style={{ color: colors.mutedSubtle }}>$</span>}
                  precision={2}
                  styles={{ content: { color: colors.savings } }}
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
                  className="catalogs-table"
                  tableLayout="fixed"
                  columns={columns}
                  dataSource={catalogs}
                  rowKey="catalog_id"
                  pagination={false}
                  size="small"
                  scroll={{ x: 980 }}
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
                <div className="catalog-cost-chart">
                  <ChartFrame
                    empty={!pieData.length}
                    emptyDescription="Cost breakdown appears once catalogs report monthly storage spend."
                    ariaLabel={`Monthly storage cost distribution totaling ${formatCurrency(totalCost)}`}
                  >
                    <PieChart>
                      <Pie
                        data={pieData}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={72}
                        outerRadius={104}
                        paddingAngle={pieData.length > 1 ? 3 : 0}
                        stroke={colors.bg}
                        strokeWidth={2}
                      >
                        {pieData.map((entry, index) => (
                          <Cell key={entry.name} fill={costPalette[index % costPalette.length]} />
                        ))}
                      </Pie>
                      <Tooltip
                        {...chartTooltipProps}
                        formatter={(value, _name, item) => {
                          const amount = Number(value ?? 0);
                          const share = totalCost > 0 ? ((amount / totalCost) * 100).toFixed(1) : "0.0";
                          return [`${formatCurrency(amount)} · ${share}%`, String(item?.payload?.name ?? "")];
                        }}
                      />
                    </PieChart>
                  </ChartFrame>
                  {pieData.length ? (
                    <div className="catalog-cost-chart-center">
                      <div className="catalog-cost-chart-total" style={{ color: colors.ink }}>
                        {formatCurrency(totalCost)}
                      </div>
                      <div className="catalog-cost-chart-label" style={{ color: colors.mutedSubtle }}>
                        per month
                      </div>
                    </div>
                  ) : null}
                </div>
                {pieData.length ? (
                  <div className="catalog-cost-legend">
                    {pieData.map((entry, index) => {
                      const share = totalCost > 0 ? (entry.value / totalCost) * 100 : 0;
                      return (
                        <div key={entry.name} className="catalog-cost-legend-row">
                          <span
                            className="catalog-cost-swatch"
                            style={{ background: costPalette[index % costPalette.length] }}
                          />
                          <span className="catalog-cost-legend-name">{entry.name}</span>
                          <span className="catalog-cost-legend-value" style={{ color: colors.ink }}>
                            {formatCurrency(entry.value)}
                          </span>
                          <span className="catalog-cost-legend-pct" style={{ color: colors.mutedSubtle }}>
                            {share.toFixed(share >= 10 ? 0 : 1)}%
                          </span>
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
};
