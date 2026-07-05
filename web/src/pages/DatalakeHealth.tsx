import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Row, Skeleton, Space, Table, Typography } from "antd";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, Tooltip, XAxis, YAxis } from "recharts";
import { Link } from "react-router-dom";
import { getDatalakeDashboard, listTables } from "../api/client";
import type { DatalakeDashboard, TableRead } from "../api/types";
import { ChartFrame, EmptyState, PageHeader, StatStrip, QueryErrorAlert, FirstRunEmptyState, TruncatedResultsBanner } from "../components/common";
import { GradePill, PriorityTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../lib/theme";
import { formatBytesGb, formatCurrency, formatGb, formatNumber, formatTime } from "../lib/format";

const gradePalette: Record<string, string> = {
  A: cryoColors.gradeA,
  B: cryoColors.gradeB,
  C: cryoColors.gradeC,
  D: cryoColors.gradeD,
  F: cryoColors.gradeF
};

const TOP_OFFENDERS_LIMIT = 50;

export const DatalakeHealth = () => {
  const { api, token } = useAuth();
  const dashboard = useQuery({
    queryKey: ["dashboard", token, TOP_OFFENDERS_LIMIT],
    queryFn: () => getDatalakeDashboard(api, { top_limit: TOP_OFFENDERS_LIMIT }),
    retry: 1
  });
  const tables = useQuery({
    queryKey: ["dashboard-fallback-tables", token],
    queryFn: () => listTables(api, { limit: 500, offset: 0 }),
    enabled: dashboard.isError
  });

  const data = dashboard.data ?? deriveDashboard(tables.data?.items ?? []);
  const topOffenders = data.top_offenders ?? [];
  const tableTotal = tables.data?.total;
  const hasProjectedCost = Boolean(
    data.totals?.projected_monthly_cost_usd || data.totals?.projected_savings_usd_month
  );

  return (
    <div>
      <PageHeader
        title="Datalake health"
        subtitle="Datalake posture, cost pressure, and tables most likely to benefit from maintenance."
        actions={
          <Space>
            <Link to="/ai">
              <Button>Intelligence hub</Button>
            </Link>
            <Link to="/cost">
              <Button>View cost savings</Button>
            </Link>
          </Space>
        }
      />
      {dashboard.isError ? (
        <Alert
          className="page-alert"
          type="info"
          showIcon
          message="Dashboard endpoint is not available yet; showing a fallback view from /tables."
        />
      ) : null}
      <QueryErrorAlert error={dashboard.error} title="Failed to load datalake dashboard" />
      {!dashboard.isError && (data.totals?.needs_compaction ?? 0) > topOffenders.length ? (
        <TruncatedResultsBanner
          shown={topOffenders.length}
          total={data.totals?.needs_compaction ?? topOffenders.length}
          noun="tables needing attention"
        />
      ) : null}
      {dashboard.isError ? (
        <TruncatedResultsBanner
          shown={tables.data?.items.length ?? 0}
          total={tableTotal ?? tables.data?.items.length ?? 0}
          noun="tables"
        />
      ) : null}
      {dashboard.isLoading || tables.isLoading ? (
        <Skeleton active />
      ) : (
        <>
          <StatStrip
            items={[
              { label: "Tables", value: formatNumber(data.totals?.tables, true) },
              {
                label: "Total data size",
                value: formatGb(data.totals?.size_gb),
                tooltip: "Combined size of every table Cryo is tracking, across all schemas."
              },
              {
                label: "Small files",
                value: formatNumber(data.totals?.small_files, true),
                tooltip: "Files under the target size threshold — too many of these slows down queries."
              },
              { label: "Needs compaction", value: formatNumber(data.totals?.needs_compaction) },
              { label: "S3 monthly now", value: formatCurrency(data.totals?.monthly_cost_usd) },
              {
                label: "After maintenance",
                value: formatCurrency(
                  data.totals?.projected_monthly_cost_usd ?? data.totals?.monthly_cost_usd
                ),
                tooltip: "Projected S3 storage cost if all recommended maintenance ran today."
              },
              { label: "Monthly savings", value: formatCurrency(data.totals?.projected_savings_usd_month) },
              {
                label: "Reclaimable storage",
                value: formatBytesGb(data.totals?.reclaimable_bytes),
                tooltip: "Disk space maintenance would free up — dead/expired data, orphan files, and metadata bloat."
              }
            ]}
          />
          <Row gutter={[12, 12]}>
            <Col xs={24} lg={9}>
              <Card title="Grade distribution">
                <ChartFrame empty={!data.grade_distribution?.length}>
                  <PieChart>
                    <Pie data={data.grade_distribution} dataKey="count" nameKey="grade" innerRadius={46} outerRadius={82}>
                      {(data.grade_distribution ?? []).map((entry) => (
                        <Cell key={entry.grade} fill={gradePalette[entry.grade] ?? cryoColors.mutedSubtle} />
                      ))}
                    </Pie>
                    <Tooltip {...chartTooltipProps} />
                  </PieChart>
                </ChartFrame>
              </Card>
            </Col>
            <Col xs={24} lg={15}>
              <Card title="Cost by schema">
                <ChartFrame empty={!data.cost_by_schema?.length}>
                  {hasProjectedCost ? (
                    <BarChart data={data.cost_by_schema?.slice(0, 12)}>
                      <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
                      <XAxis dataKey="schema_name" tickLine={false} axisLine={false} tick={chartAxisTick} />
                      <YAxis tickLine={false} axisLine={false} tick={chartAxisTick} />
                      <Tooltip {...chartTooltipProps} formatter={(value) => formatCurrency(Number(value))} />
                      <Legend />
                      <Bar dataKey="cost_usd" name="Now" fill={cryoColors.costCurrent} radius={[4, 4, 0, 0]} />
                      <Bar
                        dataKey="projected_usd"
                        name="After maintenance"
                        fill={cryoColors.costProjected}
                        radius={[4, 4, 0, 0]}
                      />
                    </BarChart>
                  ) : (
                    <BarChart data={data.cost_by_schema?.slice(0, 12)}>
                      <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
                      <XAxis dataKey="schema_name" tickLine={false} axisLine={false} tick={chartAxisTick} />
                      <YAxis tickLine={false} axisLine={false} tick={chartAxisTick} />
                      <Tooltip {...chartTooltipProps} formatter={(value) => formatCurrency(Number(value))} />
                      <Bar dataKey="cost_usd" fill={cryoColors.chartPrimary} radius={[4, 4, 0, 0]} />
                    </BarChart>
                  )}
                </ChartFrame>
              </Card>
            </Col>
            <Col xs={24} xl={15}>
              <Card title="Top offenders">
                <Table<TableRead>
                  size="small"
                  rowKey="id"
                  dataSource={topOffenders}
                  pagination={false}
                  columns={[
                    {
                      title: "Table",
                      render: (_, row) => (
                        <Typography.Text strong>{row.schema_name}.{row.table_name}</Typography.Text>
                      )
                    },
                    { title: "Grade", render: (_, row) => <GradePill grade={row.stats?.grade} /> },
                    {
                      title: "Priority",
                      render: (_, row) => <PriorityTag priority={row.stats?.compaction_priority} />
                    },
                    { title: "Small files", align: "right", render: (_, row) => formatNumber(row.stats?.small_files_count, true) },
                    { title: "Size", align: "right", render: (_, row) => formatGb(row.stats?.total_size_gb) }
                  ]}
                  locale={{
                    emptyText:
                      (data.totals?.tables ?? 0) === 0 ? (
                        <FirstRunEmptyState />
                      ) : (
                        <EmptyState
                          title="No tables need attention"
                          description="Every tracked table is below the compaction threshold right now."
                        />
                      )
                  }}
                />
              </Card>
            </Col>
            <Col xs={24} xl={9}>
              <Card
                title="Recent scan trend"
                extra={
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    Small-file count per scan
                  </Typography.Text>
                }
              >
                {(data.drift ?? []).length ? (
                  <div className="drift-list">
                    {(data.drift ?? []).slice(0, 8).map((item, index) => (
                      <div key={`${item.table_id ?? index}`} className="drift-row">
                        <span>{formatTime(item.scan_at)}</span>
                        <strong>{formatNumber(item.small_files_count, true)} small files</strong>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title="No scan history yet"
                    description="This fills in once the datalake has been scanned more than once, so you can see whether small-file counts are climbing."
                  />
                )}
              </Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
};

const deriveDashboard = (tables: TableRead[]): DatalakeDashboard => {
  const grades = new Map<string, number>();
  const schemaCosts = new Map<
    string,
    { schema_name: string; cost_usd: number; projected_usd: number; savings_usd: number; size_gb: number }
  >();
  let size = 0;
  let smallFiles = 0;
  let cost = 0;
  let projected = 0;
  let savings = 0;
  let reclaimable = 0;
  let needsCompaction = 0;

  tables.forEach((table) => {
    const stats = table.stats;
    const grade = stats?.grade ?? "-";
    grades.set(grade, (grades.get(grade) ?? 0) + 1);
    size += stats?.total_size_gb ?? 0;
    smallFiles += stats?.small_files_count ?? 0;
    const cur = stats?.estimated_monthly_s3_cost_usd ?? 0;
    const proj = stats?.projected_monthly_s3_cost_usd ?? cur;
    const sav = stats?.projected_savings_usd_month ?? Math.max(0, cur - proj);
    cost += cur;
    projected += proj;
    savings += sav;
    reclaimable += stats?.reclaimable_bytes ?? 0;
    if (stats?.needs_compaction) needsCompaction += 1;
    const schema =
      schemaCosts.get(table.schema_name) ??
      { schema_name: table.schema_name, cost_usd: 0, projected_usd: 0, savings_usd: 0, size_gb: 0 };
    schema.cost_usd += cur;
    schema.projected_usd += proj;
    schema.savings_usd += sav;
    schema.size_gb += stats?.total_size_gb ?? 0;
    schemaCosts.set(table.schema_name, schema);
  });

  return {
    totals: {
      tables: tables.length,
      size_gb: size,
      small_files: smallFiles,
      monthly_cost_usd: cost,
      projected_monthly_cost_usd: projected,
      projected_savings_usd_month: savings,
      reclaimable_bytes: reclaimable,
      needs_compaction: needsCompaction
    },
    grade_distribution: Array.from(grades.entries()).map(([grade, count]) => ({ grade, count })),
    cost_by_schema: Array.from(schemaCosts.values())
      .map((item) => ({
        schema_name: item.schema_name,
        cost_usd: item.cost_usd,
        projected_usd: item.projected_usd,
        savings_usd: item.savings_usd,
        size_gb: item.size_gb
      }))
      .sort((a, b) => (b.savings_usd || b.cost_usd) - (a.savings_usd || a.cost_usd)),
    top_offenders: [...tables].sort((a, b) => (b.stats?.small_files_count ?? 0) - (a.stats?.small_files_count ?? 0)).slice(0, 10)
  };
};
