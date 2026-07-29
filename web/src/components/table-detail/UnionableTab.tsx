import { useQuery } from "@tanstack/react-query";
import type { ApiOptions } from "../../api/client";
import { getUnionable } from "../../api/client";
import type { UnionScoreRead } from "../../api/types";
import { EmptyState } from "../common";
import { Badge } from "../ui/badge";
import { Skeleton } from "../ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { ChartCard } from "./ChartCard";

interface Props {
  api: ApiOptions;
  tableId: string;
}

const formatScore = (v: number) => `${Math.round(v * 100)}%`;

const scoreVariant = (pct: number): "success" | "warning" | "secondary" => {
  if (pct >= 0.7) return "success";
  if (pct >= 0.4) return "warning";
  return "secondary";
};

export const UnionableTab = ({ api, tableId }: Props) => {
  const query = useQuery({
    queryKey: ["unionable", tableId],
    queryFn: () => getUnionable(api, tableId, { k: 10, useLlm: false }),
    enabled: Boolean(tableId)
  });

  if (query.isLoading) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <ChartCard title="Unionable Tables">
        <p className="text-sm text-[var(--color-status-danger)]">
          Failed to load union candidates: {(query.error as Error).message}
        </p>
      </ChartCard>
    );
  }

  const results: UnionScoreRead[] = query.data?.results ?? [];

  if (results.length === 0) {
    return (
      <ChartCard
        title="Unionable Tables"
        actions={
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="secondary">EasyTUS</Badge>
            </TooltipTrigger>
            <TooltipContent>
              LLM-embedded schema similarity (EasyTUS, IEEE BigData 2025)
            </TooltipContent>
          </Tooltip>
        }
      >
        <EmptyState title="No Union-Compatible Tables Found." />
      </ChartCard>
    );
  }

  return (
    <ChartCard
      title="Unionable Tables"
      actions={
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="default">EasyTUS</Badge>
          </TooltipTrigger>
          <TooltipContent>
            LLM-embedded schema similarity (EasyTUS, IEEE BigData 2025)
          </TooltipContent>
        </Tooltip>
      }
    >
      <div className="overflow-x-auto">
        <table className="td-schema-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Match</th>
              <th>Overlap Columns</th>
              <th>Suggested Union</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {results.map((row) => (
              <tr key={row.candidate_table_id}>
                <td>
                  <code className="font-mono text-xs">{row.candidate_table_id}</code>
                </td>
                <td>
                  <Badge variant={scoreVariant(row.score)}>{formatScore(row.score)}</Badge>
                </td>
                <td>
                  <div className="flex flex-wrap gap-1">
                    {row.overlap_columns.slice(0, 5).map((c) => (
                      <Badge key={c} variant="secondary">
                        {c}
                      </Badge>
                    ))}
                    {row.overlap_columns.length > 5 ? (
                      <span className="text-xs text-muted-foreground">
                        +{row.overlap_columns.length - 5} More
                      </span>
                    ) : null}
                  </div>
                </td>
                <td>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <code className="font-mono text-[11px]">
                        {row.union_sql.slice(0, 80)}
                        {row.union_sql.length > 80 ? "…" : ""}
                      </code>
                    </TooltipTrigger>
                    <TooltipContent>{row.union_sql}</TooltipContent>
                  </Tooltip>
                </td>
                <td>
                  <span className="text-[11px]">{row.rationale}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartCard>
  );
};
