import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";

import { getImpactHistory } from "../api/impact";
import { PageHeader } from "../components/common";
import { Button } from "../components/ui/button";
import { ChartFrame } from "../components/ui/chart-panel";
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

export const Impact = () => {
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

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Impact"
        subtitle={rows.length ? fullyMeasuredSummary(rows) : "Daily ledger of what maintenance actually changed."}
        rootCrumb={{ label: "Impact", to: "/impact" }}
        actions={
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
        }
      />
      <p className="text-sm text-muted-foreground">
        Logical file changes recorded by maintenance, not a billing line. Unmeasured days are gaps, not zero.
      </p>
      <ChartFrame
        empty={!history.isLoading && chartRows.every((row) => row.gb == null)}
        emptyTitle="No measured reclaim in this range"
        emptyDescription="Days without a byte measurement are omitted from the chart rather than drawn as zero."
        ariaLabel="Daily measured reclaim"
      >
        <BarChart data={chartRows}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="day" />
          <YAxis />
          <Tooltip
            formatter={(value, _name, item) => {
              const payload = item?.payload as { label?: string } | undefined;
              if (value == null) return [payload?.label ?? "Not measured", "Reclaim"];
              return [`${Number(value).toFixed(2)} GB`, "Reclaim"];
            }}
          />
          <Bar dataKey="gb" fill="var(--primitive-accent-500, #277abe)" />
        </BarChart>
      </ChartFrame>
      <DataTable
        columns={columns}
        data={rows}
        density="compact"
        loading={history.isLoading}
        getRowId={(row) => row.report_date}
        emptyTitle="No impact history"
        emptyDescription="The daily ledger appears after the reporter records a day."
      />
    </div>
  );
};
