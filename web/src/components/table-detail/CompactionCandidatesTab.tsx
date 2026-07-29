import { useQuery } from "@tanstack/react-query";
import type { ApiOptions } from "../../api/client";
import { getCompactionCandidates } from "../../api/client";
import type { CompactionCandidateRead } from "../../api/types";
import { EmptyState } from "../common";
import { Badge } from "../ui/badge";
import { MetricTile } from "../ui/stat";
import { Skeleton } from "../ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { ChartCard } from "./ChartCard";

interface Props {
  api: ApiOptions;
  tableId: string;
}

const priorityVariant = (priority: string): "danger" | "warning" | "secondary" => {
  if (priority === "HIGH") return "danger";
  if (priority === "MED" || priority === "MEDIUM") return "warning";
  return "secondary";
};

const formatNumber = (n: number | null | undefined): string =>
  n == null ? "—" : n.toLocaleString();

const formatGb = (n: number | null | undefined): string => {
  if (n == null) return "—";
  if (n < 1) return `${(n * 1024).toFixed(0)} MB`;
  return `${n.toFixed(2)} GB`;
};

const formatUsd = (n: number | null | undefined): string =>
  n == null ? "—" : `$${n.toFixed(0)}`;

export const CompactionCandidatesTab = ({ api, tableId }: Props) => {
  const query = useQuery({
    queryKey: ["compaction-candidates", tableId],
    queryFn: () => getCompactionCandidates(api, tableId, { horizonDays: 30, k: 10 }),
    enabled: Boolean(tableId)
  });

  if (query.isLoading) {
    return (
      <div className="space-y-4 p-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <ChartCard title="Top Compaction Candidates">
        <p className="text-sm text-[var(--color-status-danger)]">
          Failed to load compaction candidates: {(query.error as Error).message}
        </p>
      </ChartCard>
    );
  }

  const data = query.data;
  const candidates: CompactionCandidateRead[] = data?.candidates ?? [];
  const totalSavings = candidates.reduce((s, c) => s + (c.savings_estimate_usd ?? 0), 0);
  const highPriority = candidates.filter((c) => c.priority === "HIGH").length;

  if (candidates.length === 0) {
    return (
      <ChartCard
        title="Top Compaction Candidates"
        subtitle="AutoComp-Style Ranking"
      >
        <EmptyState
          title="No Compaction Candidates Found"
          description="Either the table has healthy file distribution, or it has not been scanned."
        />
      </ChartCard>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricTile label="Candidates" value={candidates.length} />
        <MetricTile
          label="High Priority"
          value={highPriority}
          className={highPriority > 0 ? "[&_p:last-child]:text-[var(--color-status-danger)]" : undefined}
        />
        <MetricTile
          label="Projected 12-Month Savings"
          value={formatUsd(totalSavings)}
          className="[&_p:last-child]:text-[var(--grade-a)]"
        />
      </div>
      <ChartCard
        title="Top 10 Candidates by Benefit Score"
        actions={
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="default">AutoComp</Badge>
            </TooltipTrigger>
            <TooltipContent>
              Score = (small_files × query_freq) / partition_size_gb. Higher = more impact from compacting this partition.
            </TooltipContent>
          </Tooltip>
        }
      >
        <div className="overflow-x-auto">
          <table className="td-schema-table">
            <thead>
              <tr>
                <th>Partition</th>
                <th>Score</th>
                <th>Priority</th>
                <th>Small Files</th>
                <th>Size</th>
                <th>QPS (30 Days)</th>
                <th>p95 Latency (ms)</th>
                <th>Savings (12-Month)</th>
                <th>Reasons</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((row) => (
                <tr key={row.partition_path}>
                  <td>
                    <code className="font-mono text-xs">{row.partition_path}</code>
                  </td>
                  <td>{row.score.toFixed(2)}</td>
                  <td>
                    <Badge variant={priorityVariant(row.priority)}>{row.priority}</Badge>
                  </td>
                  <td>{formatNumber(row.small_files)}</td>
                  <td>{formatGb(row.partition_size_gb)}</td>
                  <td>{row.query_freq_per_day.toFixed(1)}</td>
                  <td>{formatNumber(row.p95_latency_ms)}</td>
                  <td>{formatUsd(row.savings_estimate_usd)}</td>
                  <td>
                    <div className="flex flex-col gap-0.5">
                      {row.reasons.map((reason, i) => (
                        <span key={i} className="text-[11px] text-muted-foreground">
                          · {reason}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </ChartCard>
    </div>
  );
};
