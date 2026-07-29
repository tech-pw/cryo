import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Database, Users } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { getCostAttributionDashboard, type CostAttributionResponse } from "../api/client";
import { PageHeader, QueryErrorAlert } from "../components/common";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { DataTable } from "../components/ui/data-table";
import { MetricGrid } from "../components/ui/metric-grid";
import { Skeleton } from "../components/ui/skeleton";
import { MetricTile } from "../components/ui/stat";
import { useAuth } from "../lib/auth";
import { formatCurrency } from "../lib/format";
import { sharedPieColors } from "../lib/theme";

type CostAttributionRow = CostAttributionResponse["attributions"][number];

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
  const [selectedMonth, setSelectedMonth] = useState<string | undefined>(undefined);

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

  const columns: ColumnDef<CostAttributionRow, unknown>[] = [
    {
      id: "team",
      header: "Team/Department",
      cell: ({ row }) => (
        <div className="flex items-center gap-2">
          <Users className="size-3.5 text-muted-foreground" aria-hidden="true" />
          <span className="font-medium">{row.original.cost_center || row.original.department || "Unattributed"}</span>
          {row.original.cost_center || row.original.department ? (
            <Link to="/catalogs">
              <Button size="sm" variant="ghost" className="h-auto px-1 py-0 text-primary">
                View tables
              </Button>
            </Link>
          ) : null}
        </div>
      )
    },
    {
      id: "table_count",
      header: "Tables",
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
      cell: ({ row }) => (
        <span className="text-muted-foreground">{formatCurrency(row.original.storage_cost_usd)}</span>
      )
    },
    {
      id: "compute",
      header: "Compute",
      cell: ({ row }) => (
        <span className="text-muted-foreground">{formatCurrency(row.original.compute_cost_usd)}</span>
      )
    },
    {
      id: "maintenance",
      header: "Maintenance",
      cell: ({ row }) => (
        <span className="text-muted-foreground">{formatCurrency(row.original.maintenance_cost_usd)}</span>
      )
    },
    {
      id: "total",
      header: "Total Cost",
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
      cell: ({ row }) => {
        const percent = data ? (row.original.total_cost_usd / data.total_cost_usd) * 100 : 0;
        return <span className="text-muted-foreground">{percent.toFixed(1)}%</span>;
      }
    }
  ];

  const pieData = (data?.attributions || []).map((item) => ({
    name: item.cost_center || item.department || "Unattributed",
    value: item.total_cost_usd
  }));

  const attributionRate = data
    ? ((data.total_cost_usd - data.unattributed_cost_usd) / Math.max(1, data.total_cost_usd)) * 100
    : 0;

  const totals = data?.attributions ?? [];

  return (
    <div>
      <PageHeader
        title="Cost attribution"
        subtitle="Storage, compute, and maintenance costs by team and department."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/cost">
              <Button variant="secondary">Savings overview</Button>
            </Link>
            <select
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
          title="Cost Attribution Not Available"
          description={
            <div className="space-y-2">
              <p>Cost attribution requires custom tags on Source/Connection models:</p>
              <ul className="list-disc pl-5">
                <li>
                  <code>cost_center</code> - Cost center identifier
                </li>
                <li>
                  <code>department</code> - Department name
                </li>
              </ul>
              <p>Once tags are configured, cost data will be aggregated by team/department automatically.</p>
              <div className="flex flex-wrap gap-2 pt-1">
                <Link to="/connections">
                  <Button size="sm" variant="primary">
                    Open connections
                  </Button>
                </Link>
                <Link to="/cost">
                  <Button size="sm" variant="secondary">
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
                <div className="border-t border-border bg-[var(--color-surface-sunken)] px-3 py-2 text-sm">
                  <div className="grid grid-cols-7 gap-2 font-semibold">
                    <span>TOTAL</span>
                    <span>{totals.reduce((sum, item) => sum + item.table_count, 0)}</span>
                    <span>{formatCurrency(totals.reduce((sum, item) => sum + item.storage_cost_usd, 0))}</span>
                    <span>{formatCurrency(totals.reduce((sum, item) => sum + item.compute_cost_usd, 0))}</span>
                    <span>{formatCurrency(totals.reduce((sum, item) => sum + item.maintenance_cost_usd, 0))}</span>
                    <span>{formatCurrency(totals.reduce((sum, item) => sum + item.total_cost_usd, 0))}</span>
                    <span>100%</span>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card className="lg:col-span-4">
              <CardHeader className="pb-2">
                <CardTitle>Cost Distribution</CardTitle>
              </CardHeader>
              <CardContent>
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
                        <Cell key={`cell-${entry.name}`} fill={sharedPieColors[index % sharedPieColors.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value) => formatCurrency(Number(value ?? 0))} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
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
