import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { getCostDashboard, triggerScan } from "../api/client";
import { getImpactHistory } from "../api/impact";
import { RankedBars } from "../components/charts";
import { PageHeader, QueryErrorAlert } from "../components/common";
import { Button } from "../components/ui/button";
import { DataTable } from "../components/ui/data-table";
import { MetricGrid, MetricTile } from "../components/ui/metric-grid";
import { useAuth } from "../lib/auth";
import { formatCurrency } from "../lib/format";
import { useWriteControl } from "../lib/useWriteControl";
import {
  engineBytesLabel,
  fullyMeasuredSummary,
  impactBytesLabel,
  impactChartGb,
  type ImpactDay
} from "../lib/impactHistory";

const RANGES = [7, 30, 90] as const;

const sinceFor = (days: number): string => {
  const end = new Date();
  const start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate()));
  start.setUTCDate(start.getUTCDate() - (days - 1));
  return start.toISOString().slice(0, 10);
};

const untilToday = (): string => new Date().toISOString().slice(0, 10);

export const Impact = ({ embedded = false }: { embedded?: boolean }) => {
  const { api, token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = Number(searchParams.get("range") ?? "30");
  const range = RANGES.includes(requested as (typeof RANGES)[number]) ? requested : 30;
  const since = sinceFor(range);
  const until = untilToday();

  const history = useQuery({
    queryKey: ["impact-history", token, since, until],
    queryFn: () => getImpactHistory(api, since, until)
  });
  const dashboard = useQuery({
    queryKey: ["cost-dashboard", token],
    queryFn: () => getCostDashboard(api)
  });
  const rows = history.data?.rows ?? [];
  const hasRows = rows.length > 0;
  const realized = dashboard.data?.totals.total_realized_savings_usd;
  const chartRows = rows.map((row) => ({
    day: row.report_date,
    gb: impactChartGb(row),
    label: impactBytesLabel(row)
  }));

  const columns = useMemo<ColumnDef<ImpactDay, unknown>[]>(
    () => [
      { id: "day", header: "Day", accessorKey: "report_date" },
      { id: "state", header: "Reclaim", cell: ({ row }) => impactBytesLabel(row.original) },
      { id: "actions", header: "Actions", accessorKey: "actions_succeeded" },
      { id: "tables", header: "Tables touched", accessorKey: "tables_touched" },
      {
        id: "files",
        header: "Files",
        cell: ({ row }) => `${row.original.optimize_files_in} → ${row.original.optimize_files_out}`
      },
      {
        id: "jobs",
        header: "Jobs",
        cell: ({ row }) => `${row.original.jobs_succeeded} succeeded / ${row.original.jobs_failed} failed`
      },
      {
        id: "infra",
        header: "Infra cost",
        cell: ({ row }) =>
          row.original.infra_cost_usd == null ? "Unknown" : formatCurrency(row.original.infra_cost_usd)
      },
      { id: "engines", header: "Bytes by engine", cell: ({ row }) => engineBytesLabel(row.original.raw) }
    ],
    []
  );

  const queryClient = useQueryClient();
  const canScan = useWriteControl("triggerScan");
  const scan = useMutation({
    mutationFn: () => triggerScan(api, { trigger: "web-impact" }),
    onSuccess: async () => {
      toast.success("Scan queued");
      await queryClient.invalidateQueries({ queryKey: ["impact-history"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const rangeButtons = hasRows ? (
    <div className="flex gap-2">
      {RANGES.map((days) => (
        <Button
          key={days}
          size="sm"
          variant={range === days ? "primary" : "secondary"}
          onClick={() =>
            setSearchParams((prev) => {
              const next = new URLSearchParams(prev);
              next.set("range", String(days));
              return next;
            })
          }
        >
          {days}d
        </Button>
      ))}
    </div>
  ) : null;

  const scanButton = canScan ? (
    <Button size="sm" variant="secondary" loading={scan.isPending} onClick={() => scan.mutate()}>
      Run a scan
    </Button>
  ) : null;

  return (
    <div className="flex flex-col gap-4">
      {embedded ? (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {hasRows
              ? `${fullyMeasuredSummary(rows)} Unmeasured days are gaps, not zero.`
              : "Nothing recorded yet. The ledger starts after the first reporter day."}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {rangeButtons}
            {scanButton}
          </div>
        </div>
      ) : (
      <PageHeader
        title="Impact"
        subtitle={
          hasRows
            ? `${fullyMeasuredSummary(rows)} Unmeasured days are gaps, not zero.`
            : "Nothing recorded yet. The ledger starts after the first reporter day."
        }
        rootCrumb={{ label: "Impact", to: "/cost?tab=impact" }}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {rangeButtons}
            {scanButton}
          </div>
        }
      />
      )}
      {history.isError ? (
        <QueryErrorAlert error={history.error} title="Failed to load impact history" />
      ) : (
      <>
      {hasRows ? (
        <MetricGrid columns={2}>
          <MetricTile
            label="Reclaimed storage value"
            value={realized == null ? "Not measured" : `≈ ${formatCurrency(realized)}/month`}
            hint="Monthly storage value of the bytes successful reclaim actions freed, not lifetime dollars. Missing days are left out, not counted as zero."
          />
        </MetricGrid>
      ) : null}
      {hasRows ? (
      <RankedBars
        title="Which days have a measured reclaim?"
        unit="GB"
        provenance="partial"
        limit={Math.max(chartRows.length, 1)}
        items={chartRows.map((row) => ({ id: row.day, label: `${row.day} · ${row.label}`, value: row.gb }))}
      />
      ) : null}
      <DataTable
        columns={columns}
        data={rows}
        density="compact"
        loading={history.isLoading}
        getRowId={(row) => row.report_date}
        emptyTitle="No impact history"
        emptyDescription="The daily ledger appears after the reporter records a day."
      />
      </>
      )}
    </div>
  );
};
