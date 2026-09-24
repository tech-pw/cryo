/**
 * WAIR boundary reclustering (Liu et al., SIGMOD 2026): which files straddle the ranges this
 * table is queried by, whether sort-rewriting only those pays for itself, and the Spark
 * statement that would do it. Recommend-only.
 */
import { useMutation } from "@tanstack/react-query";
import { Layers } from "lucide-react";
import { toast } from "sonner";

import { analyzeWairBoundaries } from "../../api/client";
import type { WairBoundaryReport } from "../../api/types";
import { useAuth } from "../../lib/auth";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

const abstentionCopy: Record<string, string> = {
  missing_table_stats: "This table hasn't been scanned yet.",
  missing_wair_file_sample:
    "No per-file bounds yet. They're sampled on the next scan for columns queried by range.",
  missing_wair_query_ranges: "No range-filtered queries against this table in the last 7 days.",
  missing_query_cost_evidence: "Queries were seen, but none reported CPU time and bytes read.",
  missing_measured_rewrite_rate:
    "No batch maintenance run has measured the Spark rewrite rate yet, so the rewrite cost is unknown.",
  no_boundary_files: "No file straddles a queried range boundary. The layout already fits the workload.",
  recluster_cost_exceeds_savings: "Rewriting the boundary files would cost more CPU than it saves."
};

const cpuHours = (seconds: number) => `${(seconds / 3600).toFixed(seconds >= 36_000 ? 0 : 1)} CPU-h`;

const Metric = ({ label, value }: { label: string; value: string }) => (
  <div className="flex flex-col gap-0.5">
    <span className="text-xs text-muted-foreground">{label}</span>
    <span className="font-mono text-sm tabular-nums">{value}</span>
  </div>
);

export const WairReport = ({ report }: { report: WairBoundaryReport }) => {
  if (report.status !== "analyzed") {
    return (
      <p className="text-sm text-muted-foreground" data-testid="wair-abstained">
        {abstentionCopy[report.abstention_reason ?? ""] ?? report.abstention_reason}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4" data-testid="wair-analyzed">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Metric label={`Saved per ${report.window_days}-day window`} value={cpuHours(report.savings_cpu_seconds_per_window)} />
        <Metric label="One-off rewrite cost" value={cpuHours(report.recluster_cpu_seconds)} />
        <Metric label="Net" value={cpuHours(report.net_cpu_seconds)} />
        <Metric
          label="Files to rewrite"
          value={`${report.selected_file_count} of ${report.boundary_file_count} (${report.proposed_rewrite_gb.toFixed(1)} GB)`}
        />
      </div>
      <p className="text-sm">
        Sort boundary files by <span className="font-mono">{report.clustering_column}</span>
        {report.boundary_values.length ? (
          <> at {report.boundary_values.length} queried range endpoint(s).</>
        ) : null}
      </p>
      {report.spark_sql ? (
        <pre className="overflow-x-auto rounded-[var(--radius-default)] bg-muted p-3 font-mono text-xs">
          {report.spark_sql}
        </pre>
      ) : (
        <p className="text-xs text-muted-foreground">
          No Spark statement: the batch Spark destination or the column type isn't known.
        </p>
      )}
      <ul className="list-disc pl-5 text-xs text-muted-foreground">
        {report.model_notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </div>
  );
};

export const WairRecluster = () => {
  const { tableId } = useTableDetailContext();
  const { api } = useAuth();
  const analyze = useMutation({
    mutationFn: () => analyzeWairBoundaries(api, tableId),
    onError: (error: Error) => toast.error(error.message)
  });

  return (
    <Card className="mb-4">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Layers aria-hidden="true" className="size-4" />
          Workload-aware reclustering
          <Badge variant="default">Recommend only</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Finds files that straddle the ranges this table is queried by, and sort-rewrites them only
          when the query CPU saved outweighs the rewrite (WAIR, SIGMOD 2026).
        </p>
        <div>
          <Button size="sm" variant="primary" loading={analyze.isPending} onClick={() => analyze.mutate()}>
            {analyze.data ? "Re-analyze" : "Analyze"}
          </Button>
        </div>
        {analyze.data ? <WairReport report={analyze.data} /> : null}
      </CardContent>
    </Card>
  );
};
