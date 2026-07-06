import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Row, Skeleton, Space, Table, Tag, Tooltip, Typography, message } from "antd";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, Tooltip as RechartsTooltip, XAxis, YAxis } from "recharts";
import { Link } from "react-router-dom";
import { useMemo, useState } from "react";
import {
  assignTables,
  deriveCostDashboard,
  getCostDashboard,
  getCostModelEvaluate,
  getSchemaCost,
  listDestinations,
  listPresets,
  listTables,
  runMaintenance,
  runManifestRewrite,
  savingsOpportunityFilter,
  triggerCostScan,
  triggerScan
} from "../api/client";
import type { CostDashboard, Mode, TableRead, UUID } from "../api/types";
import { BulkAssignBar, type BulkSelection } from "../components/BulkAssignBar";
import { ChartFrame, EmptyState, PageHeader, StatStrip } from "../components/common";
import { CostPrecisionBadge } from "../components/CostPrecisionBadge";
import { CostSavingsWaterfall } from "../components/CostSavingsWaterfall";
import { PlanPreview } from "../components/PlanPreview";
import { SavingsByOperation } from "../components/SavingsByOperation";
import { GradePill } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatCurrency, formatNumber, formatTime } from "../lib/format";
import type { Predicate } from "../lib/predicate";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../lib/theme";

const OPPORTUNITY_FILTER: Predicate = savingsOpportunityFilter;

export const CostSavings = () => {
  const { api, token } = useAuth();
  const [previewTables, setPreviewTables] = useState<TableRead[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<UUID[]>([]);
  const [bulkSelection, setBulkSelection] = useState<BulkSelection | null>(null);

  const cost = useQuery({
    queryKey: ["cost-dashboard", token],
    queryFn: () => getCostDashboard(api),
    retry: 1
  });
  const tables = useQuery({
    queryKey: ["cost-fallback-tables", token],
    queryFn: () => listTables(api, { limit: 500, offset: 0 }),
    enabled: cost.isError
  });
  const matchingTables = useQuery({
    queryKey: ["cost-matching-tables", token],
    queryFn: () => listTables(api, { predicate: OPPORTUNITY_FILTER, limit: 1, offset: 0 }),
    enabled: !cost.isLoading
  });
  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });
  const costModel = useQuery({
    queryKey: ["cost-model-evaluate", token],
    queryFn: () => getCostModelEvaluate(api),
    retry: false
  });
  const schemaCost = useQuery({
    queryKey: ["schema-cost", token],
    queryFn: () => getSchemaCost(api),
    retry: false
  });

  const data: CostDashboard = cost.data ?? deriveCostDashboard(tables.data?.items ?? []);
  const usingFallback = cost.isError;
  const hasRealizedTrend = (data.trend ?? []).some((point) => (point.realized_usd_month ?? 0) > 0);
  const precisionTotal = useMemo(
    () => Object.values(data.totals.precision_coverage ?? {}).reduce((sum, count) => sum + count, 0),
    [data.totals.precision_coverage]
  );
  const metadataCoverage =
    precisionTotal > 0
      ? formatNumber(
          ((data.totals.precision_coverage?.metadata ?? 0) +
            (data.totals.precision_coverage?.storage_truth ?? 0)) /
            precisionTotal *
            100
        )
      : "-";
  const dominantPrecision =
    data.totals.precision_coverage?.storage_truth && data.totals.precision_coverage.storage_truth > 0
      ? "storage_truth"
      : data.totals.precision_coverage?.metadata && data.totals.precision_coverage.metadata > 0
        ? "metadata"
        : "heuristic";

  const selection: BulkSelection | null =
    bulkSelection ??
    (selectedRowKeys.length
      ? { mode: "ids", ids: selectedRowKeys, totalSelected: selectedRowKeys.length }
      : null);

  const assign = useMutation({
    mutationFn: (presetId: UUID) => {
      if (!selection) throw new Error("No tables selected");
      return assignTables(api, {
        selection:
          selection.mode === "filter"
            ? { mode: "filter", filter: selection.filter }
            : { mode: "ids", ids: selectedRowKeys },
        preset_id: presetId
      });
    },
    onSuccess: (result) => message.success(`Assigned ${result.affected} tables`)
  });

  const bulkRun = useMutation({
    mutationFn: (args: { destinationId: UUID; presetId?: UUID; mode: Mode }) => {
      if (!selection) throw new Error("No tables selected");
      if (selection.mode === "filter") {
        return runMaintenance(api, {
          selection: { mode: "filter", filter: selection.filter },
          destination_id: args.destinationId,
          preset_id: args.presetId,
          mode: args.mode,
          trigger: "web-cost"
        });
      }
      return runMaintenance(api, {
        table_ids: selectedRowKeys,
        destination_id: args.destinationId,
        preset_id: args.presetId,
        mode: args.mode,
        trigger: "web-cost"
      });
    },
    onSuccess: (result) => message.success(`Run queued: ${result.run_id}`),
    onError: (error) => message.error((error as Error).message)
  });

  const run = useMutation({
    mutationFn: (tableId: UUID) => {
      const preset = presets.data?.[0];
      const destinationId = preset?.destination_id ?? destinations.data?.[0]?.id;
      if (!destinationId) throw new Error("No engine configured");
      return runMaintenance(api, {
        table_ids: [tableId],
        destination_id: destinationId,
        preset_id: preset?.id,
        mode: preset?.mode ?? "standard",
        trigger: "web-cost"
      });
    },
    onSuccess: (result) => message.success(`Run queued: ${result.run_id}`),
    onError: (error) => message.error((error as Error).message)
  });

  const fullCostScan = useMutation({
    mutationFn: () => triggerCostScan(api, { cost_full_scan: true, trigger: "web-cost-full" }),
    onSuccess: (result) => message.success(result.message || `Scan queued: ${result.run_id}`),
    onError: (error) => message.error((error as Error).message)
  });
  const bulkManifestRewrite = useMutation({
    mutationFn: (args: { destinationId: UUID; presetId?: UUID }) => {
      if (!selection) throw new Error("No tables selected");
      if (selection.mode === "filter") {
        return runManifestRewrite(api, {
          selection: { mode: "filter", filter: selection.filter },
          destination_id: args.destinationId,
          preset_id: args.presetId,
          trigger: "web-cost-manifest"
        });
      }
      return runManifestRewrite(api, {
        table_ids: selectedRowKeys,
        destination_id: args.destinationId,
        preset_id: args.presetId,
        trigger: "web-cost-manifest"
      });
    },
    onSuccess: (result) => message.success(`Manifest rewrite queued: ${result.run_id}`),
    onError: (error) => message.error((error as Error).message)
  });
  const bulkScan = useMutation({
    mutationFn: () => {
      if (!selection) throw new Error("No tables selected");
      if (selection.mode === "filter") {
        return triggerScan(api, {
          scope: "tables",
          selection: { mode: "filter", filter: selection.filter ?? null },
          full_scan: true,
          trigger: "web-cost-selection"
        });
      }
      return triggerScan(api, {
        scope: "tables",
        table_ids: selectedRowKeys,
        full_scan: true,
        trigger: "web-cost-selection"
      });
    },
    onSuccess: (result) => message.success(result.message || `Scan queued: ${result.run_id}`),
    onError: (error) => message.error((error as Error).message)
  });

  const clearSelection = () => {
    setSelectedRowKeys([]);
    setBulkSelection(null);
  };

  return (
    <div>
      <PageHeader
        title="Cost & savings"
        subtitle="Expected storage and monthly cost impact after standard maintenance."
        actions={
          <Space>
            <Link to="/attribution">
              <Button>Team attribution</Button>
            </Link>
            <Tooltip title="Runs a Tier-2 storage scan (bounded S3 LIST) to refresh orphan-byte estimates.">
              <Button loading={fullCostScan.isPending} onClick={() => fullCostScan.mutate()}>
                Run full cost scan
              </Button>
            </Tooltip>
          </Space>
        }
      />
      {usingFallback ? (
        <Alert
          className="page-alert"
          type="info"
          showIcon
          message="Cost dashboard endpoint is not available yet; showing a fallback view from /tables."
        />
      ) : null}
      {cost.isLoading || tables.isLoading ? (
        <Skeleton active />
      ) : (
        <>
          <Space wrap style={{ marginBottom: 12 }}>
            <CostPrecisionBadge
              precision={dominantPrecision}
              lastFullCostScanAt={data.totals.last_full_cost_scan_at}
              orphanScanStale={data.totals.orphan_scan_stale}
            />
            {data.totals.orphan_scan_stale ? (
              <Typography.Text type="secondary">
                Orphan estimates may be stale — run a full cost scan for storage-truth accuracy.
              </Typography.Text>
            ) : null}
          </Space>
          <StatStrip
            items={[
              { label: "S3 monthly now", value: formatCurrency(data.totals.current_monthly_cost_usd) },
              {
                label: "After maintenance",
                value: formatCurrency(data.totals.projected_monthly_cost_usd),
                tooltip: "Projected S3 storage cost if all recommended maintenance ran today."
              },
              { label: "Monthly savings", value: formatCurrency(data.totals.projected_savings_usd_month) },
              {
                label: "Reclaimable storage",
                value: formatBytesGb(data.totals.reclaimable_bytes),
                tooltip: "Disk space maintenance would free up — dead/expired data, orphan files, and metadata bloat."
              },
              {
                label: "Precision coverage",
                value: `${metadataCoverage}%`,
                tooltip:
                  "Share of tables where these cost figures come from a real metadata or storage scan, rather than a rough per-GB estimate."
              }
            ]}
          />
          {costModel.data ? (
            <Card size="small" style={{ marginBottom: 12 }} title="Learned cost model (holdout)">
              <Space wrap size="large">
                <Typography.Text>
                  MAE learned {formatCurrency(costModel.data.mae_learned)}/mo vs heuristic{" "}
                  {formatCurrency(costModel.data.mae_heuristic)}/mo
                </Typography.Text>
                <Tag color={costModel.data.beats_heuristic ? "success" : "default"}>
                  {costModel.data.beats_heuristic ? "Learned wins" : "Heuristic competitive"}
                </Tag>
                <Typography.Text type="secondary">
                  {costModel.data.n_samples} labeled outcomes · {(costModel.data.band_coverage * 100).toFixed(0)}%
                  conformal coverage
                </Typography.Text>
                <Link to="/ai">Governor & copilot →</Link>
              </Space>
            </Card>
          ) : null}
          <Row gutter={[12, 12]}>
            <Col xs={24} xl={16}>
              <Card title="Current to projected">
                <CostSavingsWaterfall
                  currentCostUsd={data.totals.current_monthly_cost_usd}
                  currentStorageBytes={data.totals.current_storage_bytes}
                  byOperation={data.savings_by_operation}
                />
              </Card>
            </Col>
            <Col xs={24} xl={8}>
              <Card title="Savings by operation">
                <SavingsByOperation items={data.savings_by_operation} />
              </Card>
            </Col>
            <Col xs={24} lg={14}>
              <Card
                title="Cost by schema"
                extra={
                  usingFallback ? (
                    <Typography.Text type="secondary">Derived from /tables</Typography.Text>
                  ) : null
                }
              >
                <ChartFrame
                  empty={!data.cost_by_schema.length}
                  ariaLabel="Current versus projected monthly cost by schema"
                >
                  <BarChart data={data.cost_by_schema.slice(0, 12)}>
                    <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
                    <XAxis dataKey="schema_name" tickLine={false} axisLine={false} tick={chartAxisTick} />
                    <YAxis tickLine={false} axisLine={false} tick={chartAxisTick} />
                    <RechartsTooltip {...chartTooltipProps} formatter={(value) => formatCurrency(Number(value))} />
                    <Legend />
                    <Bar dataKey="current_usd" name="Now" fill={cryoColors.costCurrent} radius={[4, 4, 0, 0]} />
                    <Bar
                      dataKey="projected_usd"
                      name="After maintenance"
                      fill={cryoColors.costProjected}
                      radius={[4, 4, 0, 0]}
                    />
                  </BarChart>
                </ChartFrame>
              </Card>
            </Col>
            <Col xs={24} lg={10}>
              <Card title="Cost trend">
                <ChartFrame
                  empty={!(data.trend ?? []).length}
                  emptyDescription="Trend appears after repeated cost scans."
                  ariaLabel="Cost trend showing current cost, available savings, and realized savings over time"
                >
                  <LineChart data={data.trend ?? []}>
                    <CartesianGrid vertical={false} stroke={cryoColors.chartGrid} />
                    <XAxis dataKey="scan_at" tickFormatter={(value) => formatTime(String(value))} tick={chartAxisTick} />
                    <YAxis tickLine={false} axisLine={false} tick={chartAxisTick} />
                    <RechartsTooltip
                      {...chartTooltipProps}
                      labelFormatter={(value) => formatTime(String(value))}
                      formatter={(value) => formatCurrency(Number(value))}
                    />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="current_monthly_cost_usd"
                      name="Current cost"
                      stroke={cryoColors.costCurrent}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="projected_savings_usd_month"
                      name="Available savings"
                      stroke={cryoColors.savings}
                      dot={false}
                    />
                    {hasRealizedTrend ? (
                      <Line
                        type="monotone"
                        dataKey="realized_usd_month"
                        name="Realized savings"
                        stroke={cryoColors.costProjected}
                        dot={false}
                        connectNulls
                      />
                    ) : null}
                  </LineChart>
                </ChartFrame>
              </Card>
            </Col>
            <Col xs={24}>
              <Card title="Top opportunities">
                <Table<TableRead>
                  size="small"
                  rowKey="id"
                  dataSource={data.top_opportunities}
                  pagination={false}
                  rowSelection={{
                    selectedRowKeys,
                    onChange: (keys) => {
                      setBulkSelection(null);
                      setSelectedRowKeys(keys as UUID[]);
                    }
                  }}
                  columns={[
                    {
                      title: "Table",
                      render: (_, row) => (
                        <Link to={`/tables/${row.id}`}>
                          <Typography.Text strong>
                            {row.schema_name}.{row.table_name}
                          </Typography.Text>
                        </Link>
                      )
                    },
                    {
                      title: "Now",
                      align: "right",
                      render: (_, row) => formatCurrency(row.stats?.estimated_monthly_s3_cost_usd)
                    },
                    {
                      title: "After maintenance",
                      align: "right",
                      render: (_, row) =>
                        formatCurrency(
                          row.stats?.projected_monthly_s3_cost_usd ?? row.stats?.estimated_monthly_s3_cost_usd
                        )
                    },
                    {
                      title: "Savings / mo",
                      align: "right",
                      render: (_, row) => formatCurrency(row.stats?.projected_savings_usd_month)
                    },
                    {
                      title: "Reclaimable",
                      align: "right",
                      render: (_, row) => formatBytesGb(row.stats?.reclaimable_bytes)
                    },
                    {
                      title: "Precision",
                      render: (_, row) => <CostPrecisionBadge precision={row.stats?.cost_precision} />
                    },
                    { title: "Grade", render: (_, row) => <GradePill grade={row.stats?.grade} /> },
                    {
                      title: "Action",
                      render: (_, row) => (
                        <SpaceCompact>
                          <Button
                            size="small"
                            onClick={() => {
                              setPreviewTables([row]);
                              setPreviewOpen(true);
                            }}
                          >
                            Preview
                          </Button>
                          <Button size="small" type="primary" loading={run.isPending} onClick={() => run.mutate(row.id)}>
                            Run
                          </Button>
                        </SpaceCompact>
                      )
                    }
                  ]}
                  locale={{
                    emptyText: (
                      <EmptyState
                        title="No savings opportunities"
                        description="Run a scanner pass with cost accounting to populate savings."
                      />
                    )
                  }}
                />
              </Card>
            </Col>
          </Row>
          <BulkAssignBar
            selection={selection}
            presets={presets.data ?? []}
            destinations={destinations.data ?? []}
            assigning={assign.isPending}
            running={bulkRun.isPending || bulkManifestRewrite.isPending}
            scanning={bulkScan.isPending}
            onClear={clearSelection}
            onAssign={(presetId) => assign.mutate(presetId)}
            onPreview={() => {
              if (selection?.mode === "filter") {
                setPreviewTables(data.top_opportunities);
              } else {
                setPreviewTables(
                  data.top_opportunities.filter((table) => selectedRowKeys.includes(table.id))
                );
              }
              setPreviewOpen(true);
            }}
            onRun={(args) => bulkRun.mutate(args)}
            onManifestRewrite={(args) => bulkManifestRewrite.mutate(args)}
            onScan={() => bulkScan.mutate()}
          />
          <PlanPreview open={previewOpen} tables={previewTables} onClose={() => setPreviewOpen(false)} />
        </>
      )}
    </div>
  );
};

const SpaceCompact = ({ children }: { children: React.ReactNode }) => (
  <div style={{ display: "flex", gap: 6 }}>{children}</div>
);
