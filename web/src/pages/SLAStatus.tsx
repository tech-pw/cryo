import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Card,
  Input,
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
  SearchOutlined,
} from "@ant-design/icons";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";
import { Link } from "react-router-dom";
import { getSlaStatusDashboard, type SLAResponse } from "../api/client";
import { OverviewPage } from "../components/layout/OverviewPage";
import { StatStrip } from "../components/common";
import { useAuth } from "../lib/auth";
import { formatSlaDays as formatDays, formatSlaHours as formatHours } from "../lib/format";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../lib/theme";

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

const isRowBreached = (row: SLAStatusRow) =>
  row.freshness_breached || row.maintenance_breached || row.p95_latency_breached;

interface SchemaMatrixRow {
  schema_name: string;
  tables: number;
  breached: number;
  compliancePercent: number;
}

// Fixed, meaningful freshness buckets rather than an auto-binned histogram -- "how many
// tables are more than a day stale" is the actual operator question, not an arbitrary
// bin width that shifts every time the data changes.
export const FRESHNESS_BUCKETS: Array<{ key: string; label: string; min: number; max: number }> = [
  { key: "lt1h", label: "< 1h", min: 0, max: 1 },
  { key: "1to6h", label: "1–6h", min: 1, max: 6 },
  { key: "6to24h", label: "6–24h", min: 6, max: 24 },
  { key: "1to3d", label: "1–3d", min: 24, max: 72 },
  { key: "3to7d", label: "3–7d", min: 72, max: 168 },
  { key: "gt7d", label: "> 7d", min: 168, max: Infinity },
];

export const bucketForFreshness = (hours: number | null): string | null => {
  if (hours === null) return null;
  const bucket = FRESHNESS_BUCKETS.find((b) => hours >= b.min && hours < b.max);
  return bucket?.key ?? null;
};

export const SLAStatus = () => {
  const { api } = useAuth();
  const [breachedOnly, setBreachedOnly] = useState(false);
  const [tableSearch, setTableSearch] = useState("");
  const [freshnessBucket, setFreshnessBucket] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery<SLAResponse>({
    queryKey: ["sla-status", api.token, breachedOnly],
    queryFn: () => getSlaStatusDashboard(api, breachedOnly),
    refetchInterval: 60000
  });

  const slas = data?.slas ?? [];

  const freshnessHistogram = useMemo(
    () =>
      FRESHNESS_BUCKETS.map((bucket) => ({
        key: bucket.key,
        label: bucket.label,
        count: slas.filter((row) => bucketForFreshness(row.freshness_hours) === bucket.key)
          .length,
      })),
    [slas]
  );

  const visibleRows = useMemo(() => {
    const query = tableSearch.trim().toLowerCase();
    return slas.filter((row) => {
      if (freshnessBucket && bucketForFreshness(row.freshness_hours) !== freshnessBucket) {
        return false;
      }
      if (query && !row.table_name.toLowerCase().includes(query)) {
        return false;
      }
      return true;
    });
  }, [slas, tableSearch, freshnessBucket]);

  const breachByType = useMemo(
    () => ({
      freshness: slas.filter((s) => s.freshness_breached).length,
      maintenance: slas.filter((s) => s.maintenance_breached).length,
      latency: slas.filter((s) => s.p95_latency_breached).length,
    }),
    [slas]
  );

  const breachedWithoutPreset = useMemo(
    () => slas.filter((s) => !s.has_preset && isRowBreached(s)).length,
    [slas]
  );

  // Only meaningful against the full, unfiltered fleet -- in breached-only mode, "tables"
  // per schema would silently mean "breached tables per schema" instead of the true
  // denominator, making the compliance % wrong.
  const schemaMatrix: SchemaMatrixRow[] = useMemo(() => {
    if (breachedOnly) return [];
    const bySchema = new Map<string, { tables: number; breached: number }>();
    for (const row of slas) {
      const entry = bySchema.get(row.schema_name) ?? { tables: 0, breached: 0 };
      entry.tables += 1;
      if (isRowBreached(row)) entry.breached += 1;
      bySchema.set(row.schema_name, entry);
    }
    return Array.from(bySchema.entries())
      .map(([schema_name, v]) => ({
        schema_name,
        tables: v.tables,
        breached: v.breached,
        compliancePercent: ((v.tables - v.breached) / Math.max(1, v.tables)) * 100,
      }))
      .sort((a, b) => a.compliancePercent - b.compliancePercent);
  }, [slas, breachedOnly]);

  const columns: TableColumnsType<SLAStatusRow> = [
    {
      title: "Table",
      dataIndex: "table_name",
      key: "table_name",
      width: "22%",
      ellipsis: true,
      fixed: "left" as const,
      render: (name: string, record: SLAStatusRow) => (
        <Link to={`/tables/${record.table_id}`}>{name}</Link>
      ),
    },
    {
      title: "Data freshness",
      key: "freshness",
      width: "20%",
      responsive: ["md"],
      sorter: (a, b) =>
        getCompliancePercent(a.freshness_hours, a.freshness_sla_hours) -
        getCompliancePercent(b.freshness_hours, b.freshness_sla_hours),
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
      width: "18%",
      responsive: ["lg"],
      sorter: (a, b) =>
        getCompliancePercent(a.maintenance_days, a.maintenance_sla_days) -
        getCompliancePercent(b.maintenance_days, b.maintenance_sla_days),
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
      width: "18%",
      responsive: ["xl"],
      sorter: (a, b) =>
        getCompliancePercent(a.p95_latency_ms, a.p95_latency_sla_ms) -
        getCompliancePercent(b.p95_latency_ms, b.p95_latency_sla_ms),
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
      title: "Coverage",
      key: "coverage",
      width: "10%",
      responsive: ["lg"],
      filters: [
        { text: "Preset assigned", value: true },
        { text: "No preset", value: false },
      ],
      onFilter: (value, record) => record.has_preset === value,
      render: (_: any, record: SLAStatusRow) =>
        record.has_preset ? (
          <Tag>Assigned</Tag>
        ) : (
          <Tooltip title="No preset assignment or matching rule -- maintenance SLA can never be met until one is configured.">
            <Tag color="warning">No preset</Tag>
          </Tooltip>
        ),
    },
    {
      title: "Status",
      key: "status",
      width: "12%",
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

  const schemaMatrixColumns: TableColumnsType<SchemaMatrixRow> = [
    { title: "Schema", dataIndex: "schema_name", key: "schema_name" },
    { title: "Tables", dataIndex: "tables", key: "tables", align: "right" as const },
    { title: "Breached", dataIndex: "breached", key: "breached", align: "right" as const },
    {
      title: "Compliance",
      key: "compliance",
      align: "right" as const,
      sorter: (a, b) => a.compliancePercent - b.compliancePercent,
      defaultSortOrder: "ascend" as const,
      render: (_: any, row: SchemaMatrixRow) => (
        <Space>
          <Text type={row.compliancePercent < 50 ? "danger" : undefined}>
            {row.compliancePercent.toFixed(1)}%
          </Text>
          <Progress
            percent={row.compliancePercent}
            strokeColor={row.compliancePercent < 50 ? cryoColors.gradeF : cryoColors.gradeA}
            showInfo={false}
            size="small"
            style={{ width: 80 }}
          />
        </Space>
      ),
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
          <Link to="/catalogs">
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
          description={`${data?.total_breached} table(s) currently breach SLA thresholds (${breachByType.freshness} freshness, ${breachByType.maintenance} maintenance, ${breachByType.latency} latency). Use breached-only mode for focused triage.`}
          type="warning"
          showIcon
          icon={<ExclamationCircleOutlined />}
          style={{ marginBottom: 8 }}
        />
      ) : null}

      {breachedWithoutPreset > 0 ? (
        <Alert
          title="Missing maintenance coverage"
          description={
            <>
              {breachedWithoutPreset} breached table(s) have no preset assigned or matching
              rule — their maintenance SLA can never be met until one is configured.{" "}
              <Link to="/presets">Open Presets</Link>
            </>
          }
          type="info"
          showIcon
          style={{ marginBottom: 8 }}
        />
      ) : null}

      {schemaMatrix.length > 0 ? (
        <Card title="SLA matrix by schema" style={{ marginBottom: 8 }}>
          <Table<SchemaMatrixRow>
            columns={schemaMatrixColumns}
            dataSource={schemaMatrix}
            rowKey="schema_name"
            size="small"
            pagination={{ pageSize: 10, showTotal: (total) => `${total} schemas` }}
          />
        </Card>
      ) : null}

      <Card
        title="Freshness distribution"
        extra={
          freshnessBucket ? (
            <Button size="small" onClick={() => setFreshnessBucket(null)}>
              Clear filter
            </Button>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              Click a bar to filter the table below
            </Text>
          )
        }
        style={{ marginBottom: 8 }}
      >
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={freshnessHistogram} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid stroke={cryoColors.chartGrid} />
            <XAxis dataKey="label" stroke={cryoColors.inkFaint} tick={chartAxisTick} />
            <YAxis stroke={cryoColors.inkFaint} tick={chartAxisTick} allowDecimals={false} />
            <RechartsTooltip {...chartTooltipProps} />
            <Bar
              dataKey="count"
              name="Tables"
              cursor="pointer"
              onClick={(point) => {
                const key = (point?.payload as { key?: string } | undefined)?.key;
                if (!key) return;
                setFreshnessBucket((current) => (current === key ? null : key));
              }}
            >
              {freshnessHistogram.map((bucket) => (
                <Cell
                  key={bucket.key}
                  fill={
                    freshnessBucket === null || freshnessBucket === bucket.key
                      ? cryoColors.primary
                      : cryoColors.chartGrid
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card
        title="SLA status by table"
        extra={
          <Space>
            <Input
              placeholder="Search table name"
              prefix={<SearchOutlined />}
              value={tableSearch}
              onChange={(e) => setTableSearch(e.target.value)}
              allowClear
              size="small"
              style={{ width: 200 }}
            />
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
          scroll={{ x: 1080 }}
          columns={columns}
          dataSource={visibleRows}
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
