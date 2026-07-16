import { Alert, Table, Button, Card, Col, Row, Skeleton, Space, Tag, Tooltip, Typography, message } from "antd";
import { Bar, BarChart, CartesianGrid, Line, LineChart, Tooltip as RechartsTooltip, XAxis, YAxis } from "recharts";
import { Link } from "react-router-dom";
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
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
} from "../../api/client";
import type { CostDashboard, Mode, TableRead, UUID } from "../../api/types";
import { BulkAssignBar, type BulkSelection } from "../../components/BulkAssignBar";
import { EmptyState, StatStrip } from "../../components/common";
import { CostPrecisionBadge } from "../../components/CostPrecisionBadge";
import { CostSavingsWaterfall } from "../../components/CostSavingsWaterfall";
import { PlanPreview } from "../../components/PlanPreview";
import { SavingsByOperation } from "../../components/SavingsByOperation";
import { GradePill } from "../../components/status";
import { useAuth } from "../../lib/auth";
import { formatBytesGb, formatCurrency, formatNumber, formatTime } from "../../lib/format";
import { qualifiedTableName } from "../../lib/tableLabels";
import type { Predicate } from "../../lib/predicate";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../../lib/theme";

const OPPORTUNITY_FILTER: Predicate = savingsOpportunityFilter;

/**
 * Overview tab of the consolidated Cost page. Wraps the body of the legacy
 * CostSavings page so the same chart + tables + planner flows live in one tab.
 *
 * Returns the inner content of the tab (no PageHeader). The parent adds the tabs +
 * shared header.
 */
export const CostOverviewTab = () => {
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
    mutationFn: (args: { destinationId: UUID; presetId: UUID; mode: Mode }) => {
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

  const fullCostScan = useMutation({
    mutationFn: () => triggerCostScan(api, { cost_full_scan: true, trigger: "web-cost-full" }),
    onSuccess: (result) => message.success(result.message || `Scan queued: ${result.run_id}`),
    onError: (error) => message.error((error as Error).message)
  });
  const bulkManifestRewrite = useMutation({
    mutationFn: (args: { destinationId: UUID; presetId: UUID }) => {
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

  const isLoading = cost.isLoading || tables.isLoading;

  return (
    <>
      {usingFallback ? (
        <Alert
          className="page-alert"
          type="info"
          showIcon
          title="Cost dashboard endpoint is not available yet; showing a fallback view from /tables."
        />
      ) : null}
      {isLoading ? (
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
                label: "Saved to date",
                value: formatCurrency(data.totals.total_realized_savings_usd ?? 0),
                tooltip: "Cumulative $ actually saved from completed maintenance runs, all-time — not a projection."
              },
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
              <Card title="Monthly trend">
                {hasRealizedTrend ? (
                  <LineChart
                    data={data.trend ?? []}
                    height={220}
                    margin={{ top: 8, right: 16, bottom: 8, left: 0 }}
                  >
                    <CartesianGrid stroke={cryoColors.chartGrid} />
                    <XAxis
                      dataKey="bucket"
                      stroke={cryoColors.inkFaint}
                      tick={chartAxisTick}
                      interval="preserveStartEnd"
                    />
                    <YAxis stroke={cryoColors.inkFaint} tick={chartAxisTick} />
                    <RechartsTooltip {...chartTooltipProps} />
                    <Line
                      type="monotone"
                      dataKey="projected_usd_month"
                      stroke={cryoColors.primary}
                      strokeWidth={2}
                      dot={false}
                      name="Projected"
                    />
                    <Line
                      type="monotone"
                      dataKey="realized_usd_month"
                      stroke={cryoColors.savings}
                      strokeWidth={2}
                      dot={false}
                      name="Realized"
                    />
                  </LineChart>
                ) : (
                  <EmptyState
                    title="No realized savings history yet"
                    description="Realized savings appear here after maintenance runs complete."
                  />
                )}
              </Card>
            </Col>
            <Col xs={24} lg={10}>
              <Card title="Cost by schema">
                {schemaCost.data && schemaCost.data.length ? (
                  <BarChart
                    data={(schemaCost.data ?? []).slice(0, 8)}
                    height={220}
                    margin={{ top: 8, right: 16, bottom: 24, left: 0 }}
                  >
                    <CartesianGrid stroke={cryoColors.chartGrid} />
                    <XAxis
                      dataKey="schema_name"
                      stroke={cryoColors.inkFaint}
                      tick={chartAxisTick}
                      angle={-20}
                      textAnchor="end"
                      interval={0}
                    />
                    <YAxis stroke={cryoColors.inkFaint} tick={chartAxisTick} />
                    <RechartsTooltip {...chartTooltipProps} />
                    <Bar dataKey="monthly_cost_usd" fill={cryoColors.primary} name="$ / mo" />
                  </BarChart>
                ) : (
                  <EmptyState
                    title="No schema-level cost data yet"
                    description="Cost-by-schema appears here once a Tier-1 cost scan has run."
                  />
                )}
              </Card>
            </Col>
          </Row>

          <Card
            title="Savings opportunities"
            extra={
              <Space>
                <Tooltip title="Ranked by reclaimable bytes — biggest wins first.">
                  <Tag color="blue">{matchingTables.data?.total ?? 0} candidates</Tag>
                </Tooltip>
                <Tooltip title="Runs a Tier-2 storage scan (bounded S3 LIST) to refresh orphan-byte estimates.">
                  <Button
                    size="small"
                    loading={fullCostScan.isPending}
                    onClick={() => fullCostScan.mutate()}
                  >
                    Run full cost scan
                  </Button>
                </Tooltip>
              </Space>
            }
            style={{ marginTop: 12 }}
          >
            <Table<TableRead>
              rowKey="id"
              dataSource={(tables.data?.items ?? [])
                .slice()
                .sort((a, b) => (b.stats?.reclaimable_bytes ?? 0) - (a.stats?.reclaimable_bytes ?? 0))
                .slice(0, 50)}
              pagination={false}
              size="small"
              rowSelection={{
                selectedRowKeys,
                onChange: (keys: React.Key[]) => setSelectedRowKeys(keys as UUID[])
              }}
              columns={[
                {
                  title: "Table",
                  dataIndex: "schema_name",
                  key: "table",
                  render: (_: unknown, record: TableRead) => (
                    <Space size={6}>
                      <Link to={`/tables/${record.id}`}>{qualifiedTableName(record)}</Link>
                      <GradePill grade={record.stats?.grade} />
                    </Space>
                  )
                },
                {
                  title: "Size",
                  dataIndex: "total_size_gb",
                  key: "size",
                  render: (value: number | undefined) => (value != null ? formatBytesGb(value) : "—")
                },
                {
                  title: "Reclaimable",
                  key: "reclaim",
                  render: (_: unknown, record: TableRead) =>
                    record.stats?.reclaimable_bytes != null
                      ? formatBytesGb(record.stats.reclaimable_bytes)
                      : "—"
                },
                {
                  title: "Last scan",
                  dataIndex: "scan_at",
                  key: "scan_at",
                  render: (value: string | undefined) => (value ? formatTime(value) : "—")
                }
              ]}
            />
          </Card>

          <BulkAssignBar
            selection={selection}
            presets={presets.data ?? []}
            destinations={destinations.data ?? []}
            onClear={clearSelection}
            onAssign={(presetId) => assign.mutate(presetId)}
            onRun={({ destinationId, presetId, mode }) =>
              bulkRun.mutate({ destinationId, presetId, mode })
            }
            onManifestRewrite={({ destinationId, presetId }) =>
              bulkManifestRewrite.mutate({ destinationId, presetId })
            }
            onScan={() => bulkScan.mutate()}
            onPreview={async () => {
              if (!selection) throw new Error("No tables selected");
              setPreviewTables(
                selection.mode === "filter"
                  ? (tables.data?.items ?? []).slice(0, 25)
                  : ((await listTables(api, {
                      predicate: { all: { ids: selection.ids } } as unknown as Predicate,
                      limit: 25
                    })).items ?? [])
              );
              setPreviewOpen(true);
            }}
          />

          <PlanPreview
            open={previewOpen}
            onClose={() => setPreviewOpen(false)}
            tables={previewTables}
          />
        </>
      )}
    </>
  );
};