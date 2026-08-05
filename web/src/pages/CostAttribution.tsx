import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Database, Users } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { getCostAttributionDashboard, type CostAttributionResponse } from "../api/client";
import { PageHeader, QueryErrorAlert } from "../components/common";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { DataTable } from "../components/ui/data-table";
import { MetricGrid } from "../components/ui/metric-grid";
import { Skeleton } from "../components/ui/skeleton";
import { MetricTile } from "../components/ui/stat";
import { useAuth } from "../lib/auth";
import { formatCurrency } from "../lib/format";

type CostAttributionRow = CostAttributionResponse["attributions"][number];

export interface CostConcentrationDatum {
  name: string;
  value: number;
}

export const buildCostConcentrationRows = (
  items: CostConcentrationDatum[],
  visibleLimit = 6
): CostConcentrationDatum[] => {
  const sorted = [...items].sort((left, right) => right.value - left.value);
  if (sorted.length <= visibleLimit) return sorted;

  const visible = sorted.slice(0, visibleLimit);
  const remainder = sorted.slice(visibleLimit);
  return [
    ...visible,
    {
      name: `Other (${remainder.length})`,
      value: remainder.reduce((total, item) => total + item.value, 0)
    }
  ];
};

const ProgressBar = ({ percent, className }: { percent: number; className?: string }) => (
  <div className={`h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-surface-sunken)] ${className ?? ""}`}>
    <div
      className="h-full rounded-full bg-primary transition-all"
      style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
    />
  </div>
);

export const CostAttribution = () => {
  const { api } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedMonth = searchParams.get("month") || undefined;

  const setSelectedMonth = (month: string | undefined) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (month) next.set("month", month);
        else next.delete("month");
        return next;
      },
      { replace: true }
    );
  };

  const { data, isLoading, error } = useQuery<CostAttributionResponse>({
    queryKey: ["cost-attribution", api.token, selectedMonth],
    queryFn: () => getCostAttributionDashboard(api, selectedMonth)
  });

  const monthOptions = Array.from({ length: 12 }, (_, i) => {
    const date = new Date();
    date.setMonth(date.getMonth() - i);
    const value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
    const label = date.toLocaleDateString("en-US", { month: "long", year: "numeric" });
    return { value, label };
  });

  const totals = data?.attributions ?? [];

  const columns: ColumnDef<CostAttributionRow, unknown>[] = [
    {
      id: "team",
      header: "Team/Department",
      footer: () => "TOTAL",
      cell: ({ row }) => (
        <div className="flex items-center gap-2">
          <Users className="size-3.5 text-muted-foreground" aria-hidden="true" />
          <span className="font-medium">{row.original.cost_center || row.original.department || "Unattributed"}</span>
          {row.original.cost_center || row.original.department ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-auto cursor-not-allowed px-1 py-0 text-muted-foreground"
              disabled
              title="Catalog filtering by cost center/department isn't available yet -- tables aren't tagged with either field, so this would be a fake link."
            >
              View tables (not available)
            </Button>
          ) : null}
        </div>
      )
    },
    {
      id: "table_count",
      header: "Tables",
      footer: () => totals.reduce((sum, item) => sum + item.table_count, 0),
      cell: ({ row }) => (
        <Badge variant="secondary">
          <Database className="mr-1 size-3" aria-hidden="true" />
          {row.original.table_count}
        </Badge>
      )
    },
    {
      id: "storage",
      header: "Storage",
      footer: () => formatCurrency(totals.reduce((sum, item) => sum + item.storage_cost_usd, 0)),
      cell: ({ row }) => (
        <span className="text-muted-foreground">{formatCurrency(row.original.storage_cost_usd)}</span>
      )
    },
    {
      id: "compute",
      header: "Compute",
      footer: () => formatCurrency(totals.reduce((sum, item) => sum + item.compute_cost_usd, 0)),
      cell: ({ row }) => (
        <span className="text-muted-foreground">{formatCurrency(row.original.compute_cost_usd)}</span>
      )
    },
    {
      id: "maintenance",
      header: "Maintenance",
      footer: () => formatCurrency(totals.reduce((sum, item) => sum + item.maintenance_cost_usd, 0)),
      cell: ({ row }) => (
        <span className="text-muted-foreground">{formatCurrency(row.original.maintenance_cost_usd)}</span>
      )
    },
    {
      id: "total",
      header: "Total Cost",
      footer: () => formatCurrency(totals.reduce((sum, item) => sum + item.total_cost_usd, 0)),
      cell: ({ row }) => {
        const cost = row.original.total_cost_usd;
        const percent = data ? (cost / data.total_cost_usd) * 100 : 0;
        return (
          <div className="space-y-1">
            <span className="font-semibold">{formatCurrency(cost)}</span>
            <ProgressBar percent={percent} />
          </div>
        );
      }
    },
    {
      id: "percent",
      header: "% of Total",
      footer: () => "100%",
      cell: ({ row }) => {
        const percent = data ? (row.original.total_cost_usd / data.total_cost_usd) * 100 : 0;
        return <span className="text-muted-foreground">{percent.toFixed(1)}%</span>;
      }
    }
  ];

  const concentrationRows = buildCostConcentrationRows(
    (data?.attributions || []).map((item) => ({
      name: item.cost_center || item.department || "Unattributed",
      value: item.total_cost_usd
    }))
  );
  const largestConcentration = Math.max(1, ...concentrationRows.map((item) => item.value));

  const attributionRate = data
    ? ((data.total_cost_usd - data.unattributed_cost_usd) / Math.max(1, data.total_cost_usd)) * 100
    : 0;

  return (
    <div>
      <PageHeader
        title="Cost attribution"
        subtitle="Storage, compute, and maintenance costs by team and department."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="warning">Experimental</Badge>
            <Link to="/cost">
              <Button variant="secondary">Savings overview</Button>
            </Link>
            <label htmlFor="cost-attribution-month" className="text-xs font-medium text-muted-foreground">
              Month
            </label>
            <select
              id="cost-attribution-month"
              className="h-[var(--control-height)] rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
              value={selectedMonth ?? ""}
              onChange={(event) => setSelectedMonth(event.target.value || undefined)}
            >
              <option value="">Current month</option>
              {monthOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        }
      />

      <QueryErrorAlert error={error} title="Failed to load cost attribution" />

      {isLoading ? (
        <div className="space-y-4 py-8">
          <MetricGrid columns={4}>
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-[88px] rounded-[var(--radius-default)]" />
            ))}
          </MetricGrid>
        </div>
      ) : !data || data.attributions.length === 0 ? (
        <Alert
          variant="info"
          title="Cost attribution is an experimental, not-yet-implemented view"
          description={
            <div className="space-y-2">
              <p>
                This endpoint is currently a placeholder: it always returns an empty result. Cost centers and
                departments aren&apos;t modeled anywhere in Cryo&apos;s database yet, so there is no real data to
                show, for any month.
              </p>
              <p>To make this real, the backend would need to add and populate:</p>
              <ul className="list-disc pl-5">
                <li>
                  <code>cost_center</code> - a cost center identifier on Source/Connection
                </li>
                <li>
                  <code>department</code> - a department name on Source/Connection
                </li>
                <li>join logic from tables to their tagged source, and cost aggregation by that tag</li>
              </ul>
              <p>
                This is why the page is kept separate from primary Cost navigation and marked Experimental --
                it exists to show the intended shape of the feature, not a working report.
              </p>
              <div className="flex flex-wrap gap-2 pt-1">
                <Link to="/cost">
                  <Button size="sm" variant="primary">
                    Open savings dashboard
                  </Button>
                </Link>
              </div>
            </div>
          }
        />
      ) : (
        <>
          <MetricGrid columns={4} className="mb-6">
            <MetricTile
              label="Total Cost"
              value={formatCurrency(data.total_cost_usd)}
              hint={<span className="text-xs text-muted-foreground">{data.month}</span>}
              emphasis
            />
            <MetricTile
              label="Attribution Rate"
              value={`${attributionRate.toFixed(1)}%`}
              hint={<span className="text-xs text-muted-foreground">Cost assigned to teams</span>}
              emphasis={attributionRate < 70}
            />
            <MetricTile
              label="Unattributed"
              value={formatCurrency(data.unattributed_cost_usd)}
              hint={<span className="text-xs text-muted-foreground">Tables without cost tags</span>}
              emphasis={data.unattributed_cost_usd > 0}
            />
            <MetricTile
              label="Teams"
              value={data.attributions.length}
              hint={<span className="text-xs text-muted-foreground">Active cost centers</span>}
            />
          </MetricGrid>

          <div className="mb-6 grid gap-4 lg:grid-cols-12">
            <Card className="lg:col-span-8">
              <CardHeader className="pb-2">
                <CardTitle>Cost by Team</CardTitle>
              </CardHeader>
              <CardContent className="p-0 pt-0">
                <DataTable
                  columns={columns}
                  data={data.attributions}
                  getRowId={(row) => row.cost_center || row.department || "unattributed"}
                  emptyTitle="No attribution data"
                />
              </CardContent>
            </Card>
            <Card className="lg:col-span-4">
              <CardHeader className="pb-2">
                <CardTitle>Cost concentration</CardTitle>
                <CardDescription>Largest individual cost centers; remaining teams are grouped.</CardDescription>
              </CardHeader>
              <CardContent className="pt-2">
                <ol className="space-y-4" aria-label="Cost centers ranked by monthly cost">
                  {concentrationRows.map((entry, index) => {
                    const percentOfTotal = (entry.value / Math.max(1, data.total_cost_usd)) * 100;
                    const relativeWidth = (entry.value / largestConcentration) * 100;
                    return (
                      <li key={entry.name}>
                        <div className="mb-1.5 flex items-baseline gap-2 text-xs">
                          <span className="w-4 shrink-0 font-mono text-muted-foreground">{index + 1}</span>
                          <span className="min-w-0 flex-1 truncate font-medium" title={entry.name}>
                            {entry.name}
                          </span>
                          <span className="shrink-0 font-semibold [font-variant-numeric:tabular-nums]">
                            {formatCurrency(entry.value)}
                          </span>
                          <span className="w-12 shrink-0 text-right text-muted-foreground [font-variant-numeric:tabular-nums]">
                            {percentOfTotal.toFixed(1)}%
                          </span>
                        </div>
                        <div
                          className="ml-6 h-2 overflow-hidden rounded-full bg-[var(--color-surface-sunken)]"
                          aria-hidden="true"
                        >
                          <div
                            className="h-full rounded-full bg-primary"
                            style={{ width: `${relativeWidth}%` }}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ol>
                <p className="mt-4 border-t border-border pt-3 text-xs leading-5 text-muted-foreground">
                  Bar length compares teams directly; labels retain the exact amount and share of total cost.
                </p>
              </CardContent>
            </Card>
          </div>

          <MetricGrid columns={3}>
            <MetricTile
              label="Storage Costs"
              value={formatCurrency(totals.reduce((sum, item) => sum + item.storage_cost_usd, 0))}
              hint={<span className="text-xs text-muted-foreground">S3 storage at $0.023/GB/month</span>}
            />
            <MetricTile
              label="Compute Costs"
              value={formatCurrency(totals.reduce((sum, item) => sum + item.compute_cost_usd, 0))}
              hint={<span className="text-xs text-muted-foreground">Query execution costs (Trino/Spark)</span>}
            />
            <MetricTile
              label="Maintenance Costs"
              value={formatCurrency(totals.reduce((sum, item) => sum + item.maintenance_cost_usd, 0))}
              hint={<span className="text-xs text-muted-foreground">Compaction and optimization jobs</span>}
            />
          </MetricGrid>
        </>
      )}
    </div>
  );
};
