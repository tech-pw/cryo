import type { ColumnDef, RowSelectionState } from "@tanstack/react-table";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, Tooltip as RechartsTooltip, XAxis, YAxis } from "recharts";
import { Link } from "react-router-dom";
import { toast } from "sonner";
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
import type { CostDashboard, CostTrendPoint, Mode, TableRead, UUID } from "../../api/types";
import { BulkAssignBar, type BulkSelection } from "../../components/BulkAssignBar";
import { EmptyState } from "../../components/common";
import { CostPrecisionBadge } from "../../components/CostPrecisionBadge";
import { CostSavingsWaterfall, buildWaterfallRows } from "../../components/CostSavingsWaterfall";
import { PlanPreview } from "../../components/PlanPreview";
import { SavingsByOperation } from "../../components/SavingsByOperation";
import { GradePill } from "../../components/status";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { ChartDataTable } from "../../components/ui/chart-data-table";
import { DataTable } from "../../components/ui/data-table";
import { MetricGrid } from "../../components/ui/metric-grid";
import { Skeleton } from "../../components/ui/skeleton";
import { MetricTile } from "../../components/ui/stat";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { useAuth } from "../../lib/auth";
import { formatBytesGb, formatCurrency, formatNumber, formatTime } from "../../lib/format";
import { qualifiedTableName } from "../../lib/tableLabels";
import type { Predicate } from "../../lib/predicate";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../../lib/theme";

const OPPORTUNITY_FILTER: Predicate = savingsOpportunityFilter;

const computeCostDelta30d = (trend: CostTrendPoint[] | undefined) => {
  if (!trend || trend.length < 2) return undefined;
  const sorted = [...trend].sort((a, b) => a.scan_at.localeCompare(b.scan_at));
  const latest = sorted[sorted.length - 1];
  const latestCost = latest.current_monthly_cost_usd ?? 0;
  const latestDate = new Date(latest.scan_at);
  const cutoff = new Date(latestDate);
  cutoff.setDate(cutoff.getDate() - 30);
  const baseline = sorted.find((point) => new Date(point.scan_at) <= cutoff) ?? sorted[0];
  const baselineCost = baseline.current_monthly_cost_usd ?? latestCost;
  const diff = latestCost - baselineCost;
  if (diff === 0) return { value: "No change vs 30d ago", tone: "neutral" as const };
  return {
    value: `${diff > 0 ? "+" : ""}${formatCurrency(diff)} vs 30d ago`,
    tone: (diff > 0 ? "bad" : "good") as "bad" | "good"
  };
};

const opportunityColumns: ColumnDef<TableRead, unknown>[] = [
  {
    id: "table",
    header: "Table",
    cell: ({ row }) => (
      <div className="flex items-center gap-1.5">
        <Link to={`/tables/${row.original.id}`} className="font-medium text-foreground hover:text-primary">
          {qualifiedTableName(row.original)}
        </Link>
        <GradePill grade={row.original.stats?.grade} />
      </div>
    )
  },
  {
    id: "size",
    header: "Size",
    cell: ({ row }) =>
      row.original.stats?.total_size_gb != null ? formatBytesGb(row.original.stats.total_size_gb) : "—"
  },
  {
    id: "reclaim",
    header: "Reclaimable",
    cell: ({ row }) =>
      row.original.stats?.reclaimable_bytes != null
        ? formatBytesGb(row.original.stats.reclaimable_bytes)
        : "—"
  },
  {
    id: "scan_at",
    header: "Last scan",
    cell: ({ row }) => (row.original.stats?.scan_at ? formatTime(row.original.stats.scan_at) : "—")
  }
];

/**
 * Overview tab of the consolidated Cost page. Wraps the body of the legacy
 * CostSavings page so the same chart + tables + planner flows live in one tab.
 */
export const CostOverviewTab = () => {
  const { api, token } = useAuth();
  const [previewTables, setPreviewTables] = useState<TableRead[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
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
  const costDelta30d = computeCostDelta30d(data.trend);

  const selectedRowKeys = Object.keys(rowSelection) as UUID[];
  const selection: BulkSelection | null =
    bulkSelection ??
    (selectedRowKeys.length
      ? { mode: "ids", ids: selectedRowKeys, totalSelected: selectedRowKeys.length }
      : null);

  const opportunityRows = useMemo(
    () =>
      (tables.data?.items ?? [])
        .slice()
        .sort((a, b) => (b.stats?.reclaimable_bytes ?? 0) - (a.stats?.reclaimable_bytes ?? 0))
        .slice(0, 50),
    [tables.data?.items]
  );

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
    onSuccess: (result) => toast.success(`Assigned ${result.affected} tables`)
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
    onSuccess: (result) => toast.success(`Run queued: ${result.run_id}`),
    onError: (error) => toast.error((error as Error).message)
  });

  const fullCostScan = useMutation({
    mutationFn: () => triggerCostScan(api, { cost_full_scan: true, trigger: "web-cost-full" }),
    onSuccess: (result) => toast.success(result.message || `Scan queued: ${result.run_id}`),
    onError: (error) => toast.error((error as Error).message)
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
    onSuccess: (result) => toast.success(`Manifest rewrite queued: ${result.run_id}`),
    onError: (error) => toast.error((error as Error).message)
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
    onSuccess: (result) => toast.success(result.message || `Scan queued: ${result.run_id}`),
    onError: (error) => toast.error((error as Error).message)
  });

  const clearSelection = () => {
    setRowSelection({});
    setBulkSelection(null);
  };

  const isLoading = cost.isLoading || tables.isLoading;

  return (
    <>
      {usingFallback ? (
        <Alert
          className="page-alert mb-3"
          variant="info"
          title="Cost dashboard endpoint is not available yet; showing a fallback view from /tables."
        />
      ) : null}
      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-8 w-full max-w-xl" />
          <MetricGrid columns={4}>
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-[88px] rounded-[var(--radius-default)]" />
            ))}
          </MetricGrid>
        </div>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <CostPrecisionBadge
              precision={dominantPrecision}
              lastFullCostScanAt={data.totals.last_full_cost_scan_at}
              orphanScanStale={data.totals.orphan_scan_stale}
            />
            <span className="text-xs text-muted-foreground">
              {data.totals.last_full_cost_scan_at
                ? `Last full storage-truth scan: ${formatTime(data.totals.last_full_cost_scan_at)}`
                : "No full storage-truth cost scan recorded yet -- figures below use metadata estimates."}
            </span>
            {data.totals.orphan_scan_stale ? (
              <p className="text-sm text-muted-foreground">
                Orphan estimates may be stale — run a full cost scan for storage-truth accuracy.
              </p>
            ) : null}
          </div>
          <MetricGrid columns={4} className="mb-3">
            <MetricTile
              label="Total monthly S3 cost"
              value={formatCurrency(data.totals.current_monthly_cost_usd)}
              emphasis
              delta={costDelta30d}
            />
            <MetricTile
              label="After maintenance"
              value={formatCurrency(data.totals.projected_monthly_cost_usd)}
              hint={
                <span title="Projected S3 storage cost if all recommended maintenance ran today." className="cursor-help">
                  ⓘ
                </span>
              }
            />
            <MetricTile label="Monthly savings" value={formatCurrency(data.totals.projected_savings_usd_month)} />
            <MetricTile
              label="Saved to date"
              value={formatCurrency(data.totals.total_realized_savings_usd ?? 0)}
              hint={
                <span title="Cumulative savings from completed maintenance runs." className="cursor-help">
                  ⓘ
                </span>
              }
            />
          </MetricGrid>
          <MetricGrid columns={2} className="mb-4">
            <MetricTile
              label="Reclaimable storage"
              value={formatBytesGb(data.totals.reclaimable_bytes)}
              hint={
                <span title="Disk space maintenance would free up." className="cursor-help">
                  ⓘ
                </span>
              }
            />
            <MetricTile
              label="Precision coverage"
              value={`${metadataCoverage}%`}
              hint={
                <span title="Share of tables with metadata or storage-truth cost figures." className="cursor-help">
                  ⓘ
                </span>
              }
            />
          </MetricGrid>
          {costModel.data ? (
            <Card className="mb-3">
              <CardHeader className="pb-2">
                <CardTitle>Learned cost model (holdout)</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap items-center gap-4 text-sm">
                  <span>
                    MAE learned {formatCurrency(costModel.data.mae_learned)}/mo vs heuristic{" "}
                    {formatCurrency(costModel.data.mae_heuristic)}/mo
                  </span>
                  <Badge variant={costModel.data.beats_heuristic ? "success" : "secondary"}>
                    {costModel.data.beats_heuristic ? "Learned wins" : "Heuristic competitive"}
                  </Badge>
                  <span className="text-muted-foreground">
                    {costModel.data.n_evaluated}/{costModel.data.n_samples} holdout outcomes evaluated ·{" "}
                    {(costModel.data.band_coverage * 100).toFixed(0)}% conformal coverage
                  </span>
                  <span className="text-muted-foreground">
                    {(costModel.data.lineage_completeness * 100).toFixed(0)}% complete lineage
                  </span>
                  <span className="text-muted-foreground">
                    {(costModel.data.abstention_rate * 100).toFixed(0)}% outside training domain
                  </span>
                  <Link to="/ai" className="text-primary hover:underline">
                    Governor & copilot →
                  </Link>
                </div>
              </CardContent>
            </Card>
          ) : null}
          <div className="mb-3 grid gap-3 xl:grid-cols-12">
            <Card className="xl:col-span-8">
              <CardHeader className="pb-2">
                <CardTitle>Current to projected</CardTitle>
              </CardHeader>
              <CardContent>
                <CostSavingsWaterfall
                  currentCostUsd={data.totals.current_monthly_cost_usd}
                  currentStorageBytes={data.totals.current_storage_bytes}
                  byOperation={data.savings_by_operation}
                />
                <ChartDataTable
                  caption="Cost waterfall from current monthly cost down to the projected cost after each maintenance operation runs."
                  columns={[
                    { key: "name", header: "Step", render: (row) => row.name },
                    {
                      key: "amount",
                      header: "Amount",
                      align: "right",
                      render: (row) =>
                        row.kind === "save" ? `- ${formatCurrency(row.amount)}` : formatCurrency(row.amount)
                    },
                    {
                      key: "running",
                      header: "Running total",
                      align: "right",
                      render: (row) => formatCurrency(row.kind === "save" ? row.base : row.value)
                    }
                  ]}
                  rows={buildWaterfallRows(
                    data.totals.current_monthly_cost_usd,
                    new Map(data.savings_by_operation.map((item) => [item.operation, item.savings_usd_month]))
                  )}
                  getRowKey={(row, index) => `${row.name}-${index}`}
                />
              </CardContent>
            </Card>
            <Card className="xl:col-span-4">
              <CardHeader className="pb-2">
                <CardTitle>Savings by operation</CardTitle>
              </CardHeader>
              <CardContent>
                <SavingsByOperation items={data.savings_by_operation} />
              </CardContent>
            </Card>
            <Card className="xl:col-span-7">
              <CardHeader className="pb-2">
                <CardTitle>Monthly trend</CardTitle>
              </CardHeader>
              <CardContent>
                {hasRealizedTrend ? (
                  <>
                    <div
                      role="img"
                      aria-label={`Projected versus realized monthly savings across ${(data.trend ?? []).length} scans.`}
                    >
                    <LineChart
                      data={data.trend ?? []}
                      height={220}
                      margin={{ top: 8, right: 16, bottom: 8, left: 0 }}
                    >
                      <CartesianGrid stroke={cryoColors.chartGrid} />
                      <XAxis
                        dataKey="scan_at"
                        stroke={cryoColors.inkFaint}
                        tick={chartAxisTick}
                        interval="preserveStartEnd"
                        tickFormatter={(value) => formatTime(value)}
                      />
                      <YAxis stroke={cryoColors.inkFaint} tick={chartAxisTick} />
                      <RechartsTooltip {...chartTooltipProps} labelFormatter={(value) => formatTime(String(value))} />
                      <Line
                        type="monotone"
                        dataKey="projected_savings_usd_month"
                        stroke={cryoColors.primary}
                        strokeWidth={2}
                        dot={false}
                        name="Projected savings"
                      />
                      <Line
                        type="monotone"
                        dataKey="realized_usd_month"
                        stroke={cryoColors.savings}
                        strokeWidth={2}
                        dot={false}
                        name="Realized savings"
                      />
                    </LineChart>
                    </div>
                    <ChartDataTable
                      caption="Monthly trend of projected vs realized savings by scan date."
                      columns={[
                        { key: "scan_at", header: "Scan date", render: (row) => formatTime(row.scan_at) },
                        {
                          key: "projected",
                          header: "Projected savings",
                          align: "right",
                          render: (row) => formatCurrency(row.projected_savings_usd_month ?? 0)
                        },
                        {
                          key: "realized",
                          header: "Realized savings",
                          align: "right",
                          render: (row) => formatCurrency(row.realized_usd_month ?? 0)
                        }
                      ]}
                      rows={data.trend ?? []}
                      getRowKey={(row, index) => row.scan_at ?? `trend-${index}`}
                    />
                  </>
                ) : (
                  <EmptyState
                    title="No realized savings history yet"
                    description="Realized savings appear here after maintenance runs complete."
                  />
                )}
              </CardContent>
            </Card>
            <Card className="xl:col-span-5">
              <CardHeader className="pb-2">
                <CardTitle>Cost by schema</CardTitle>
              </CardHeader>
              <CardContent>
                {schemaCost.data && schemaCost.data.length ? (
                  <>
                    <div
                      role="img"
                      aria-label={`Estimated monthly cost for the top ${Math.min((schemaCost.data ?? []).length, 8)} schemas.`}
                    >
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
                      <Bar dataKey="cost_usd" fill={cryoColors.primary} name="$ / mo" />
                    </BarChart>
                    </div>
                    <ChartDataTable
                      caption="Estimated monthly S3 cost by schema, largest first."
                      columns={[
                        { key: "schema", header: "Schema", render: (row) => row.schema_name },
                        {
                          key: "cost",
                          header: "Monthly cost",
                          align: "right",
                          render: (row) => formatCurrency(row.cost_usd)
                        },
                        {
                          key: "size",
                          header: "Size",
                          align: "right",
                          render: (row) => formatBytesGb(row.size_gb)
                        }
                      ]}
                      rows={schemaCost.data ?? []}
                      getRowKey={(row, index) => row.schema_name ?? `schema-${index}`}
                    />
                  </>
                ) : (
                  <EmptyState
                    title="No schema-level cost data yet"
                    description="Cost-by-schema appears here once a Tier-1 cost scan has run."
                  />
                )}
              </CardContent>
            </Card>
          </div>

          <Card className="mt-3">
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle>Savings opportunities</CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge>{matchingTables.data?.total ?? 0} candidates</Badge>
                  </TooltipTrigger>
                  <TooltipContent>Ranked by reclaimable bytes — biggest wins first.</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button size="sm" loading={fullCostScan.isPending} onClick={() => fullCostScan.mutate()}>
                      Run full cost scan
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    Runs a Tier-2 storage scan (bounded S3 LIST) to refresh orphan-byte estimates.
                  </TooltipContent>
                </Tooltip>
              </div>
            </CardHeader>
            <CardContent className="p-0 pt-0">
              <DataTable
                columns={opportunityColumns}
                data={opportunityRows}
                getRowId={(row) => row.id}
                rowSelection={rowSelection}
                onRowSelectionChange={setRowSelection}
                enableRowSelection
                emptyTitle="No savings opportunities"
                emptyDescription="Tables with reclaimable storage will appear here after a scan."
              />
            </CardContent>
          </Card>

          <BulkAssignBar
            selection={selection}
            presets={presets.data ?? []}
            destinations={destinations.data ?? []}
            onClear={clearSelection}
            onAssign={(presetId) => assign.mutate(presetId)}
            onRun={({ destinationId, presetId, mode }) => bulkRun.mutate({ destinationId, presetId, mode })}
            onManifestRewrite={({ destinationId, presetId }) =>
              bulkManifestRewrite.mutate({ destinationId, presetId })
            }
            onScan={() => bulkScan.mutate()}
            onPreview={async () => {
              if (!selection) throw new Error("No tables selected");
              setPreviewTables(
                selection.mode === "filter"
                  ? (tables.data?.items ?? []).slice(0, 25)
                  : (
                      await listTables(api, {
                        predicate: { all: { ids: selection.ids } } as unknown as Predicate,
                        limit: 25
                      })
                    ).items ?? []
              );
              setPreviewOpen(true);
            }}
          />

          <PlanPreview open={previewOpen} onClose={() => setPreviewOpen(false)} tables={previewTables} />
        </>
      )}
    </>
  );
};
