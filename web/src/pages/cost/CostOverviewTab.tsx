import type { ColumnDef, RowSelectionState } from "@tanstack/react-table";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis
} from "recharts";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  assignTables,
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
import { Select } from "../../components/ui/select";
import { Skeleton } from "../../components/ui/skeleton";
import { MetricTile } from "../../components/ui/stat";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { useAuth } from "../../lib/auth";
import { formatBytesGb, formatCurrency, formatGb, formatNumber, formatTime } from "../../lib/format";
import { toastScanResult } from "../../lib/operations";
import { qualifiedTableName } from "../../lib/tableLabels";
import type { Predicate } from "../../lib/predicate";
import { useWriteControl } from "../../lib/useWriteControl";
import { chartAxisTick, chartTooltipProps, cryoColors } from "../../lib/theme";

const OPPORTUNITY_FILTER: Predicate = savingsOpportunityFilter;
const OPPORTUNITY_PAGE_SIZE = 50;
const EMPTY_TABLES: TableRead[] = [];
const COST_UNAVAILABLE = "Cost data unavailable";
const NOT_MEASURED = "Not measured";
const displayCost = (value: number | null | undefined): string =>
  value == null || Number.isNaN(value) ? COST_UNAVAILABLE : formatCurrency(value);

const displayBytes = (value: number | null | undefined): string =>
  value == null || Number.isNaN(value) ? NOT_MEASURED : formatBytesGb(value);

const displayMeasuredGb = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? NOT_MEASURED : formatGb(value);

const displayMeasuredBytes = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? NOT_MEASURED : formatBytesGb(value);

const offsetFromSearch = (raw: string | null): number => {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) return 0;
  return Math.floor(parsed / OPPORTUNITY_PAGE_SIZE) * OPPORTUNITY_PAGE_SIZE;
};

const computeCostDelta30d = (trend: CostTrendPoint[] | undefined) => {
  if (!trend || trend.length < 2) return undefined;
  const sorted = [...trend].sort((a, b) => a.scan_at.localeCompare(b.scan_at));
  const latest = sorted[sorted.length - 1];
  const latestCost = latest.current_monthly_cost_usd;
  if (latestCost == null || Number.isNaN(latestCost)) return undefined;
  const latestDate = new Date(latest.scan_at);
  const cutoff = new Date(latestDate);
  cutoff.setDate(cutoff.getDate() - 30);
  const baseline = sorted.find((point) => new Date(point.scan_at) <= cutoff) ?? sorted[0];
  const baselineCost = baseline.current_monthly_cost_usd;
  if (baselineCost == null || Number.isNaN(baselineCost)) return undefined;
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
      <Link to={`/tables/${row.original.id}`} className="font-medium text-foreground hover:text-primary">
        {qualifiedTableName(row.original)}
      </Link>
    )
  },
  {
    id: "grade",
    header: "Grade",
    cell: ({ row }) => <GradePill grade={row.original.stats?.grade} />
  },
  {
    id: "reclaim",
    header: () => <span className="block text-right">Reclaimable</span>,
    cell: ({ row }) => (
      <span className="block text-right tabular-nums">{displayMeasuredBytes(row.original.stats?.reclaimable_bytes)}</span>
    )
  },
  {
    id: "size",
    header: () => <span className="block text-right">Size</span>,
    cell: ({ row }) => (
      <span className="block text-right tabular-nums">{displayMeasuredGb(row.original.stats?.total_size_gb)}</span>
    )
  },
  {
    id: "scan_at",
    header: "Last scan",
    cell: ({ row }) => (row.original.stats?.scan_at ? formatTime(row.original.stats.scan_at) : "Not scanned")
  }
];

/**
 * Overview tab of the consolidated Cost page. Wraps the body of the legacy
 * CostSavings page so the same chart + tables + planner flows live in one tab.
 */
export const CostOverviewTab = ({
  active = true,
  onHeaderActions
}: {
  active?: boolean;
  onHeaderActions?: (actions: ReactNode) => void;
}) => {
  const { api, token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const oppOffset = offsetFromSearch(searchParams.get("oppOffset"));
  const runAccess = useWriteControl("runMaintenance");
  const assignAccess = useWriteControl("assignPreset");
  const scanAccess = useWriteControl("triggerScan");
  const manifestAccess = useWriteControl("manifestRewrite");
  const [previewTables, setPreviewTables] = useState<TableRead[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkSelection, setBulkSelection] = useState<BulkSelection | null>(null);

  const cost = useQuery({
    queryKey: ["cost-dashboard", token],
    queryFn: () => getCostDashboard(api),
    retry: 1
  });
  const matchingTables = useQuery({
    queryKey: ["cost-matching-tables", token, oppOffset],
    queryFn: ({ signal }) =>
      listTables(api, {
        predicate: OPPORTUNITY_FILTER,
        sort: "-reclaimable_bytes",
        limit: OPPORTUNITY_PAGE_SIZE,
        offset: oppOffset,
        signal
      }),
    enabled: cost.isSuccess
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

  const data: CostDashboard | undefined = cost.data;
  const hasRealizedTrend = (data?.trend ?? []).some((point) => (point.realized_usd_month ?? 0) > 0);
  const precisionCoverage = data?.totals?.precision_coverage;
  const precisionTotal = useMemo(
    () => Object.values(precisionCoverage ?? {}).reduce((sum, count) => sum + count, 0),
    [precisionCoverage]
  );
  const metadataCoverage =
    precisionTotal > 0
      ? formatNumber(
          ((precisionCoverage?.metadata ?? 0) + (precisionCoverage?.storage_truth ?? 0)) / precisionTotal * 100
        )
      : "-";
  const dominantPrecision =
    precisionCoverage?.storage_truth && precisionCoverage.storage_truth > 0
      ? "storage_truth"
      : precisionCoverage?.metadata && precisionCoverage.metadata > 0
        ? "metadata"
        : "heuristic";
  const costDelta30d = computeCostDelta30d(data?.trend);

  const selectedRowKeys = Object.keys(rowSelection) as UUID[];
  const selection: BulkSelection | null =
    bulkSelection ??
    (selectedRowKeys.length
      ? { mode: "ids", ids: selectedRowKeys, totalSelected: selectedRowKeys.length }
      : null);

  const opportunityRows = matchingTables.data?.items ?? EMPTY_TABLES;
  const opportunityTotal =
    matchingTables.data == null || matchingTables.data.total == null ? null : matchingTables.data.total;
  const opportunityBadge = !matchingTables.isSuccess
    ? matchingTables.isError
      ? NOT_MEASURED
      : null
    : opportunityTotal == null
      ? NOT_MEASURED
      : opportunityRows.length < opportunityTotal
        ? `${opportunityRows.length} of ${opportunityTotal} candidates`
        : `${opportunityTotal} candidates`;
  const deniedReason =
    scanAccess.reason ?? runAccess.reason ?? assignAccess.reason ?? manifestAccess.reason;
  const bulkWritesAllowed =
    runAccess.allowed && assignAccess.allowed && manifestAccess.allowed && scanAccess.allowed;

  const setOppOffset = (nextOffset: number) => {
    setRowSelection({});
    setBulkSelection(null);
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (nextOffset <= 0) next.delete("oppOffset");
        else next.set("oppOffset", String(nextOffset));
        return next;
      },
      { replace: true }
    );
  };

  const assign = useMutation({
    mutationFn: (presetId: UUID) => {
      if (!assignAccess.allowed) throw new Error(assignAccess.reason ?? "Not allowed");
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
      if (!runAccess.allowed) throw new Error(runAccess.reason ?? "Not allowed");
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
    mutationFn: () => {
      if (!scanAccess.allowed) throw new Error(scanAccess.reason ?? "Not allowed");
      return triggerCostScan(api, { cost_full_scan: true, trigger: "web-cost-full" });
    },
    onSuccess: (result) => toastScanResult(result),
    onError: (error) => toast.error((error as Error).message)
  });

  useEffect(() => {
    if (!active) return;
    onHeaderActions?.(
      <Button
        size="sm"
        disabled={!scanAccess.allowed || fullCostScan.isPending}
        title={scanAccess.reason ?? undefined}
        onClick={() => fullCostScan.mutate()}
      >
        {fullCostScan.isPending ? "Starting scan…" : "Run full cost scan"}
      </Button>
    );
    return () => onHeaderActions?.(null);
  }, [active, fullCostScan.isPending, fullCostScan.mutate, onHeaderActions, scanAccess.allowed]);
  const bulkManifestRewrite = useMutation({
    mutationFn: (args: { destinationId: UUID; presetId: UUID }) => {
      if (!manifestAccess.allowed) throw new Error(manifestAccess.reason ?? "Not allowed");
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
      if (!scanAccess.allowed) throw new Error(scanAccess.reason ?? "Not allowed");
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
    onSuccess: (result) => toastScanResult(result),
    onError: (error) => toast.error((error as Error).message)
  });

  const clearSelection = () => {
    setRowSelection({});
    setBulkSelection(null);
  };

  const isLoading = cost.isLoading;

  return (
    <>
      {cost.isError ? (
        <Alert
          className="page-alert mb-3"
          variant="error"
          title="Dashboard unavailable"
          description={
            <Button type="button" variant="secondary" size="sm" className="mt-2" onClick={() => void cost.refetch()}>
              Retry
            </Button>
          }
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
      ) : data ? (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <CostPrecisionBadge
              precision={dominantPrecision}
              lastFullCostScanAt={data.totals.last_full_cost_scan_at}
              orphanScanStale={data.totals.orphan_scan_stale}
            />
            <p className="text-sm text-muted-foreground">
              {data.totals.last_full_cost_scan_at
                ? `Last full storage-truth scan: ${formatTime(data.totals.last_full_cost_scan_at)}. ${metadataCoverage}% of tables have a metadata or storage-truth figure.`
                : "Metadata estimates. No storage-truth scan yet."}
              {data.totals.orphan_scan_stale
                ? " Orphan estimates may be stale — run a full cost scan for storage-truth accuracy."
                : ""}
            </p>
          </div>
          {!onHeaderActions ? (
            <div className="mb-3 flex justify-end">
              <Button
                size="sm"
                disabled={!scanAccess.allowed || fullCostScan.isPending}
                title={scanAccess.reason ?? undefined}
                onClick={() => fullCostScan.mutate()}
              >
                {fullCostScan.isPending ? "Starting scan…" : "Run full cost scan"}
              </Button>
            </div>
          ) : null}
          <MetricGrid columns={3} className="mb-4">
            <MetricTile
              metricId="modeled_s3_storage_cost"
              value={displayCost(data.totals.current_monthly_cost_usd)}
              emphasis
              delta={costDelta30d}
            />
            <MetricTile
              label="After maintenance"
              value={displayCost(data.totals.projected_monthly_cost_usd)}
              hint={<span className="text-xs text-muted-foreground">If every recommendation ran today.</span>}
            />
            <MetricTile
              metricId="modeled_savings"
              value={displayCost(data.totals.projected_savings_usd_month)}
              hint={
                <span className="text-xs text-muted-foreground">
                  {displayBytes(data.totals.reclaimable_bytes)} reclaimable if those recommendations ran.
                </span>
              }
            />
          </MetricGrid>
          {costModel.data ? (
            <Card className="mb-3">
              <CardContent className="flex flex-wrap items-center gap-3 py-3 text-sm">
                <span>
                  {costModel.data.beats_heuristic
                    ? `Learned model beats the heuristic by ${formatCurrency(
                        Math.max(0, costModel.data.mae_heuristic - costModel.data.mae_learned)
                      )}/mo on holdout.`
                    : "Learned model does not beat the heuristic on holdout."}
                </span>
                <Badge variant={costModel.data.beats_heuristic ? "success" : "secondary"}>
                  {costModel.data.beats_heuristic ? "Learned wins" : "Heuristic competitive"}
                </Badge>
                <details className="text-xs text-muted-foreground">
                  <summary className="cursor-pointer select-none hover:text-foreground">Holdout details</summary>
                  <p className="mt-2 max-w-prose leading-5">
                    MAE learned {formatCurrency(costModel.data.mae_learned)}/mo vs heuristic{" "}
                    {formatCurrency(costModel.data.mae_heuristic)}/mo. {costModel.data.n_evaluated}/
                    {costModel.data.n_samples} holdout outcomes evaluated,{" "}
                    {(costModel.data.band_coverage * 100).toFixed(0)}% conformal coverage,{" "}
                    {(costModel.data.lineage_completeness * 100).toFixed(0)}% complete lineage,{" "}
                    {(costModel.data.abstention_rate * 100).toFixed(0)}% outside the training domain.{" "}
                    <Link to="/ai" className="text-primary hover:underline">
                      Governor & copilot
                    </Link>
                  </p>
                </details>
              </CardContent>
            </Card>
          ) : null}
          <div className="mb-3 grid gap-3 xl:grid-cols-12">
            <Card className="xl:col-span-8">
              <CardHeader className="pb-2">
                <CardTitle>
                  {data.totals.projected_savings_usd_month != null && data.totals.projected_savings_usd_month > 0
                    ? `Where the ${formatCurrency(data.totals.projected_savings_usd_month)} comes from`
                    : "Where the modelled savings come from"}
                </CardTitle>
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
            {hasRealizedTrend ? (
            <Card className="xl:col-span-7">
              <CardHeader className="pb-2">
                <CardTitle>Monthly trend</CardTitle>
              </CardHeader>
              <CardContent>
                  <>
                    <div
                      role="img"
                      aria-label={`Projected versus realized monthly savings across ${(data.trend ?? []).length} scans.`}
                    >
                    <ResponsiveContainer width="100%" height={220}>
                    <LineChart
                      data={data.trend ?? []}
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
                    </ResponsiveContainer>
                    </div>
                    <ChartDataTable
                      caption="Monthly trend of projected vs realized savings by scan date."
                      columns={[
                        { key: "scan_at", header: "Scan date", render: (row) => formatTime(row.scan_at) },
                        {
                          key: "projected",
                          header: "Projected savings",
                          align: "right",
                          render: (row) => displayCost(row.projected_savings_usd_month)
                        },
                        {
                          key: "realized",
                          header: "Realized savings",
                          align: "right",
                          render: (row) => displayCost(row.realized_usd_month)
                        }
                      ]}
                      rows={data.trend ?? []}
                      getRowKey={(row, index) => row.scan_at ?? `trend-${index}`}
                    />
                  </>
              </CardContent>
            </Card>
            ) : null}
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
                    <ResponsiveContainer width="100%" height={Math.max(160, Math.min((schemaCost.data ?? []).length, 8) * 32)}>
                    <BarChart
                      layout="vertical"
                      data={(schemaCost.data ?? []).slice(0, 8)}
                      margin={{ top: 4, right: 48, bottom: 4, left: 8 }}
                    >
                      <CartesianGrid stroke={cryoColors.chartGrid} horizontal={false} />
                      <XAxis type="number" stroke={cryoColors.inkFaint} tick={chartAxisTick} />
                      <YAxis
                        type="category"
                        dataKey="schema_name"
                        width={120}
                        stroke={cryoColors.inkFaint}
                        tick={chartAxisTick}
                        interval={0}
                      />
                      <RechartsTooltip {...chartTooltipProps} />
                      <Bar dataKey="cost_usd" fill={cryoColors.primary} name="$ / mo" label={{ position: "right", fontSize: 11 }} />
                    </BarChart>
                    </ResponsiveContainer>
                    </div>
                    <ChartDataTable
                      caption="Estimated monthly S3 cost by schema, largest first."
                      columns={[
                        { key: "schema", header: "Schema", render: (row) => row.schema_name },
                        {
                          key: "cost",
                          header: "Monthly cost",
                          align: "right",
                          render: (row) => displayCost(row.cost_usd)
                        },
                        {
                          key: "size",
                          header: "Size",
                          align: "right",
                          render: (row) => displayMeasuredGb(row.size_gb)
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
                {opportunityBadge ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge>{opportunityBadge}</Badge>
                    </TooltipTrigger>
                    <TooltipContent>Ranked by reclaimable bytes — biggest wins first.</TooltipContent>
                  </Tooltip>
                ) : null}
                {deniedReason ? <span className="text-xs text-muted-foreground">{deniedReason}</span> : null}
              </div>
            </CardHeader>
            {bulkWritesAllowed ? (
              <div className="px-4">
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
                  onPreview={() => {
                    if (!selection) throw new Error("No tables selected");
                    const picked =
                      selection.mode === "ids"
                        ? opportunityRows.filter((row) => selection.ids.includes(row.id))
                        : opportunityRows;
                    setPreviewTables(picked.slice(0, 25));
                    setPreviewOpen(true);
                  }}
                />
              </div>
            ) : selection ? (
              <div className="bulk-bar mx-4 w-auto" data-testid="bulk-assign-bar" role="region" aria-label="Selection actions">
                <span className="bulk-bar__count-value">{selection.totalSelected.toLocaleString()} selected</span>
                {assignAccess.hidden ? null : assignAccess.allowed ? (
                  <Select
                    aria-label="Assign preset"
                    placeholder="Select preset"
                    options={(presets.data ?? []).map((preset) => ({ label: preset.name, value: preset.id }))}
                    loading={assign.isPending}
                    onChange={(presetId) => {
                      if (presetId && assignAccess.allowed) assign.mutate(presetId);
                    }}
                  />
                ) : (
                  <Button type="button" disabled title={assignAccess.reason ?? undefined} aria-label="Assign preset">
                    Assign
                  </Button>
                )}
                {runAccess.hidden ? null : (
                  <Button
                    type="button"
                    variant="primary"
                    disabled={!runAccess.allowed}
                    title={runAccess.reason ?? undefined}
                  >
                    Run now
                  </Button>
                )}
                <Button type="button" variant="ghost" onClick={clearSelection}>
                  Clear
                </Button>
              </div>
            ) : null}
            <CardContent className="p-0 pt-0">
              <DataTable
                columns={opportunityColumns}
                data={opportunityRows}
                loading={matchingTables.isPending}
                getRowId={(row) => row.id}
                rowSelection={rowSelection}
                onRowSelectionChange={setRowSelection}
                enableRowSelection
                emptyTitle={
                  matchingTables.isError || opportunityTotal == null
                    ? "Opportunities unavailable"
                    : "No savings opportunities"
                }
                emptyDescription={
                  matchingTables.isError || opportunityTotal == null
                    ? "The savings candidate list could not be loaded."
                    : "Tables with reclaimable storage will appear here after a scan."
                }
                pagination={
                  opportunityTotal == null
                    ? undefined
                    : {
                        pageIndex: Math.floor(oppOffset / OPPORTUNITY_PAGE_SIZE),
                        pageSize: OPPORTUNITY_PAGE_SIZE,
                        totalRows: opportunityTotal,
                        onPageChange: (pageIndex) => setOppOffset(pageIndex * OPPORTUNITY_PAGE_SIZE)
                      }
                }
              />
            </CardContent>
          </Card>

          <PlanPreview open={previewOpen} onClose={() => setPreviewOpen(false)} tables={previewTables} />
        </>
      ) : null}
    </>
  );
};
