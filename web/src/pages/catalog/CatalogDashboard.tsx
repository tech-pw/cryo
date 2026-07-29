import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { CheckCircle2, Database, AlertTriangle } from "lucide-react";
import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getMultiCatalogDashboard, listTables, type MultiCatalogDashboard } from "../../api/client";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import { ChartCard } from "../../components/table-detail/ChartCard";
import { healthScoreColor } from "../../components/status";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { DataTable } from "../../components/ui/data-table";
import { MetricTile } from "../../components/ui/stat";
import { useAuth } from "../../lib/auth";
import { formatCurrency, formatGb } from "../../lib/format";
import { all, leaf, tableFiltersToPredicate } from "../../lib/predicate";
import { useTheme } from "../../lib/themeContext";

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

const KpiSkeleton = () => (
  <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
    {Array.from({ length: 4 }).map((_, index) => (
      <MetricTile key={index} label="Loading" value="—" loading />
    ))}
  </div>
);

const HealthScoreCell = ({ score }: { score: number }) => (
  <div className="catalogs-table-health flex flex-col items-end gap-1">
    <span style={{ color: healthScoreColor(score) }}>{score.toFixed(1)}</span>
    <div className="h-1.5 w-full max-w-[5rem] overflow-hidden rounded-full bg-[var(--color-surface-sunken)]">
      <div
        className="h-full rounded-full transition-all"
        style={{ width: `${score}%`, backgroundColor: healthScoreColor(score) }}
      />
    </div>
  </div>
);

export const CatalogDashboard = () => {
  const { api, token } = useAuth();
  const { colors } = useTheme();
  const navigate = useNavigate();

  const { data, isLoading, error, refetch } = useQuery<MultiCatalogResponse>({
    queryKey: ["multi-catalog", api.token],
    queryFn: () => getMultiCatalogDashboard(api),
    refetchInterval: 60000
  });

  // Both queries fetch limit:1 -- only `.total` (an exact backend COUNT) is read, never
  // `.items`. Previously this sampled the first 500 active tables and only computed a
  // percentage `if (presetTotal <= presetItems.length)`, so at real fleet scale (total
  // active tables > 500) that guard always failed and the KPI was permanently stuck on
  // "--". Two count-only queries give an exact ratio at any fleet size instead of an
  // artificially capped sample.
  const presetActiveTotal = useQuery({
    queryKey: ["catalog-preset-coverage", "active-total", token],
    queryFn: () =>
      listTables(api, { limit: 1, offset: 0, predicate: tableFiltersToPredicate({ activeOnly: true }) }),
    staleTime: 60_000
  });
  const presetAssignedTotal = useQuery({
    queryKey: ["catalog-preset-coverage", "assigned-total", token],
    queryFn: () =>
      listTables(api, {
        limit: 1,
        offset: 0,
        predicate: all([leaf("is_active", "=", true), leaf("assigned_preset_id", "!=", null)])
      }),
    staleTime: 60_000
  });

  const catalogs = data?.catalogs || [];
  const enabledCatalogs = catalogs.filter((catalog) => catalog.enabled).length;
  const needsCompaction = catalogs.reduce((sum, catalog) => sum + catalog.needs_compaction_count, 0);
  const activeTotal = presetActiveTotal.data?.total ?? 0;
  const assignedTotal = presetAssignedTotal.data?.total ?? 0;
  const presetCoverage = activeTotal > 0 ? Math.round((assignedTotal / activeTotal) * 100) : null;
  const presetCoverageLoading = presetActiveTotal.isLoading || presetAssignedTotal.isLoading;

  const columns = useMemo<ColumnDef<CatalogStats, unknown>[]>(
    () => [
      {
        id: "catalog",
        header: "Catalog",
        size: 180,
        cell: ({ row }) => (
          <button
            type="button"
            className="flex items-start gap-2 text-left"
            onClick={() => navigate(`/catalogs?source=${row.original.catalog_id}`)}
          >
            <span
              className={`mt-1.5 size-2 shrink-0 rounded-full ${row.original.enabled ? "bg-[var(--color-status-success)]" : "bg-muted-foreground/40"}`}
              aria-hidden="true"
            />
            <span className="flex flex-col gap-0.5">
              <span className="font-medium">{row.original.catalog_name}</span>
              <Badge variant="secondary" className="w-fit text-[10px]">
                {row.original.catalog_type.toUpperCase()}
              </Badge>
            </span>
          </button>
        )
      },
      {
        accessorKey: "table_count",
        header: "Tables",
        size: 88,
        cell: ({ getValue }) => (
          <span className="catalogs-table-numeric block text-right font-medium">
            {(getValue() as number).toLocaleString()}
          </span>
        )
      },
      {
        accessorKey: "total_size_gb",
        header: "Total Size",
        size: 104,
        cell: ({ getValue }) => (
          <span className="catalogs-table-numeric block text-right">{formatGb(getValue() as number)}</span>
        )
      },
      {
        accessorKey: "small_files_count",
        header: "Small Files",
        size: 112,
        cell: ({ getValue }) => {
          const count = getValue() as number;
          return (
            <div className="catalogs-table-tag-cell flex justify-end">
              <Badge variant={count > 10000 ? "danger" : count > 5000 ? "warning" : "secondary"}>
                {count.toLocaleString()}
              </Badge>
            </div>
          );
        }
      },
      {
        accessorKey: "needs_compaction_count",
        header: "Needs Compaction",
        size: 148,
        cell: ({ getValue }) => {
          const count = getValue() as number;
          return (
            <div className="catalogs-table-tag-cell flex justify-end">
              <Badge variant={count > 0 ? "warning" : "success"} className="inline-flex items-center gap-1">
                {count > 0 ? <AlertTriangle className="size-3" aria-hidden="true" /> : <CheckCircle2 className="size-3" aria-hidden="true" />}
                {count} tables
              </Badge>
            </div>
          );
        }
      },
      {
        accessorKey: "avg_health_score",
        header: "Health Score",
        size: 120,
        cell: ({ getValue }) => <HealthScoreCell score={getValue() as number} />
      },
      {
        accessorKey: "monthly_cost_usd",
        header: "Monthly Cost",
        size: 116,
        cell: ({ getValue }) => (
          <span className="catalogs-table-numeric block text-right" style={{ color: colors.costCurrent }}>
            {formatCurrency(getValue() as number)}
          </span>
        )
      },
      {
        accessorKey: "projected_savings_usd_month",
        header: "Est. Savings",
        size: 116,
        cell: ({ getValue }) => {
          const savings = getValue() as number | null;
          return (
            <span
              className="catalogs-table-numeric block text-right"
              style={{ color: savings && savings > 0 ? colors.savings : colors.mutedSubtle }}
            >
              {savings ? formatCurrency(savings) : "—"}
            </span>
          );
        }
      }
    ],
    [colors.costCurrent, colors.mutedSubtle, colors.savings, navigate]
  );

  return (
    <AsyncBoundary
      isLoading={isLoading}
      isError={Boolean(error)}
      error={error}
      onRetry={() => void refetch()}
      skeleton={<KpiSkeleton />}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label="Catalogs enabled" value={enabledCatalogs} suffix={`/ ${data?.total_catalogs ?? 0}`} />
        <MetricTile label="Tables scanned" value={data?.total_tables ?? 0} />
        <MetricTile label="Needs compaction" value={needsCompaction} emphasis={needsCompaction > 0} />
        <MetricTile
          label="Preset coverage"
          value={presetCoverage == null ? "—" : `${presetCoverage}%`}
          loading={presetCoverageLoading}
        />
      </div>

      <ChartCard
        title="Catalogs"
        actions={
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>Fleet cost {formatCurrency(data?.total_cost_usd ?? 0)}/mo</span>
            <Link to="/cost">
              <Button size="sm" variant="ghost">
                Open Cost →
              </Button>
            </Link>
            <Database className="size-4" aria-hidden="true" />
          </div>
        }
        className="mb-6"
      >
        <DataTable
          className="catalogs-table"
          columns={columns}
          data={catalogs}
          density="compact"
          stickyFirstColumn
          getRowId={(row) => row.catalog_id}
          emptyTitle="No catalogs"
          emptyDescription="Connect a lakehouse to see catalog stats here."
        />
      </ChartCard>
    </AsyncBoundary>
  );
};
