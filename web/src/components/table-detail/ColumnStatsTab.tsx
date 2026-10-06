import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { ApiOptions } from "../../api/client";
import { getColumnStats } from "../../api/client";
import type { ColumnSketchRead } from "../../api/types";
import { EmptyState } from "../common";
import { Badge } from "../ui/badge";
import { Skeleton } from "../ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { ChartCard } from "./ChartCard";
import { SimpleTablePagination } from "./SimpleTablePagination";

interface Props {
  api: ApiOptions;
  tableId: string;
}

const formatNum = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());

export const ColumnStatsTab = ({ api, tableId }: Props) => {
  const [page, setPage] = useState(0);
  const pageSize = 20;

  const query = useQuery({
    queryKey: ["column-stats", tableId],
    queryFn: () => getColumnStats(api, tableId),
    enabled: Boolean(tableId)
  });

  const columns: Array<{ name: string; stats: ColumnSketchRead }> = useMemo(
    () =>
      Object.entries(query.data?.columns ?? {}).map(([name, stats]) => ({
        name,
        stats: stats as ColumnSketchRead
      })),
    [query.data?.columns]
  );

  const pagedColumns = useMemo(
    () => columns.slice(page * pageSize, (page + 1) * pageSize),
    [columns, page]
  );

  if (query.isLoading) {
    return (
      <div className="mt-4 space-y-3">
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <ChartCard title="Per-Column Statistics (Puffin Sketches)" className="mt-4">
        <p className="text-sm text-[var(--color-status-danger)]">
          Failed to load column stats: {(query.error as Error).message}
        </p>
      </ChartCard>
    );
  }

  if (columns.length === 0) {
    return (
      <ChartCard
        className="mt-4"
        title="Per-Column Statistics (Puffin Sketches)"
        actions={
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="default">Puffin</Badge>
            </TooltipTrigger>
            <TooltipContent>
              Theta (NDV) + KLL (quantiles) sketches stored in Puffin sidecar files — FORMATS &apos;26
            </TooltipContent>
          </Tooltip>
        }
      >
        <EmptyState
          title="No column sketches yet"
          description="No column sketches have been written yet. Run a scan to generate them."
        />
      </ChartCard>
    );
  }

  return (
    <ChartCard
      className="mt-4"
      title="Per-Column Statistics (Puffin Sketches)"
      actions={
        <div className="flex gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="default">Puffin</Badge>
            </TooltipTrigger>
            <TooltipContent>
              Theta (NDV) + KLL (quantiles) sketches stored in Puffin sidecar files — FORMATS &apos;26
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="secondary">Zero-scan</Badge>
            </TooltipTrigger>
            <TooltipContent>
              Read these values directly from the table&apos;s metadata — no data file is scanned
            </TooltipContent>
          </Tooltip>
        </div>
      }
    >
      <div className="overflow-x-auto">
        <table className="td-schema-table">
          <thead>
            <tr>
              <th>Column</th>
              <th>Type</th>
              <th>Distinct (NDV)</th>
              <th>p50</th>
              <th>p95</th>
              <th>p99</th>
              <th>Sketches</th>
            </tr>
          </thead>
          <tbody>
            {pagedColumns.map((col) => (
              <tr key={col.name}>
                <td>
                  <code className="font-mono text-xs">{col.name}</code>
                </td>
                <td>{col.stats.field_type ?? "—"}</td>
                <td>{formatNum(col.stats.ndv_estimate)}</td>
                <td>{formatNum(col.stats.quantiles?.p50)}</td>
                <td>{formatNum(col.stats.quantiles?.p95)}</td>
                <td>{formatNum(col.stats.quantiles?.p99)}</td>
                <td>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge variant="secondary">{col.stats.sketch_count}</Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      {col.stats.puffin_blobs?.length ?? 0} Puffin blob(s)
                    </TooltipContent>
                  </Tooltip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <SimpleTablePagination
        page={page}
        pageSize={pageSize}
        total={columns.length}
        onPageChange={setPage}
      />
    </ChartCard>
  );
};
