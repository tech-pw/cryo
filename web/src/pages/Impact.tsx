import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { getImpactHistory } from "../api/impact";
import { RankedBars } from "../components/charts";
import { PageHeader, QueryErrorAlert } from "../components/common";
import { Button } from "../components/ui/button";
import { DataTable } from "../components/ui/data-table";
import { useAuth } from "../lib/auth";
import { formatCurrency } from "../lib/format";
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
  const rows = history.data?.rows ?? [];
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

  const rangeButtons = (
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
  );

  return (
    <div className="flex flex-col gap-4">
      {embedded ? (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {rows.length ? fullyMeasuredSummary(rows) : "Daily ledger of what maintenance actually changed."} Unmeasured
            days are gaps, not zero.
          </p>
          {rangeButtons}
        </div>
      ) : (
      <PageHeader
        title="Impact"
        subtitle={rows.length ? fullyMeasuredSummary(rows) : "Daily ledger of what maintenance actually changed."}
        rootCrumb={{ label: "Impact", to: "/cost?tab=impact" }}
        actions={rangeButtons}
      />
      )}
      <p className="text-sm text-muted-foreground">
        Logical file changes recorded by maintenance, not a billing line. Unmeasured days are gaps, not zero.
      </p>
      {history.isError ? (
        <QueryErrorAlert error={history.error} title="Failed to load impact history" />
      ) : (
      <>
      <RankedBars
        title="Which days have a measured reclaim?"
        unit="GB"
        provenance="partial"
        limit={Math.max(chartRows.length, 1)}
        items={chartRows.map((row) => ({ id: row.day, label: `${row.day} · ${row.label}`, value: row.gb }))}
      />
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
