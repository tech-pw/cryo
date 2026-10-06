import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dismissDarkTable, getDarkTables } from "../api/client";
import type { DarkTableCandidateRead } from "../api/types";
import { EmptyState, QueryErrorAlert } from "../components/common";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { MetricGrid, MetricTile } from "../components/ui/metric-grid";
import { PageHeader } from "../components/ui/page-header";
import { Skeleton } from "../components/ui/skeleton";
import { useAuth } from "../lib/auth";
import { VIEWER_REASON } from "../lib/permissions";
import { useWriteControl } from "../lib/useWriteControl";
import { formatCurrency, formatGb, formatNumber } from "../lib/format";

const CandidateRow = ({ candidate }: { candidate: DarkTableCandidateRead }) => {
  const { api } = useAuth();
  const dismissAccess = useWriteControl("dismissDarkTable");
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [dismissing, setDismissing] = useState(false);

  const dismissMutation = useMutation({
    mutationFn: () => dismissDarkTable(api, candidate.table_id, { reason }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["dark-tables"] });
      setDismissing(false);
      setReason("");
    }
  });

  return (
    <div className="rounded-lg border border-[var(--color-border)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">
            {candidate.schema_name}.{candidate.table_name}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            No queries for {candidate.consecutive_zero_query_windows} consecutive daily checks
            {candidate.first_zero_query_at
              ? ` · silent since ${new Date(candidate.first_zero_query_at).toLocaleDateString()}`
              : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="tabular-nums text-xs text-muted-foreground">
            {formatGb(candidate.total_size_gb)}
          </span>
          <Badge variant="warning" className="tabular-nums">
            {candidate.estimated_monthly_cost_usd !== null
              ? `${formatCurrency(candidate.estimated_monthly_cost_usd)}/mo`
              : "cost unknown"}
          </Badge>
        </div>
      </div>

      {dismissing ? (
        <div className="mt-3 flex items-center gap-2">
          <Input
            placeholder="Why is this table fine to keep? (e.g. quarterly report)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="text-sm"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => dismissMutation.mutate()}
            disabled={!reason.trim() || dismissMutation.isPending}
          >
            {dismissMutation.isPending ? "Saving…" : "Confirm"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setDismissing(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <div className="mt-3 flex justify-end">
          <Button
            size="sm"
            variant="outline"
            disabled={!dismissAccess.allowed}
            title={dismissAccess.reason ?? undefined}
            onClick={() => setDismissing(true)}
          >
            Not dark — dismiss
          </Button>
          {!dismissAccess.allowed ? <p className="mt-1 text-xs text-muted-foreground">{VIEWER_REASON}</p> : null}
        </div>
      )}
      {dismissMutation.isError ? <QueryErrorAlert error={dismissMutation.error} /> : null}
    </div>
  );
};

export const DarkTables = () => {
  const { api } = useAuth();

  const query = useQuery({
    queryKey: ["dark-tables"],
    queryFn: () => getDarkTables(api)
  });

  const isLoading = query.isLoading;
  const candidates = query.data?.candidates ?? [];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Dark Tables"
        subtitle="Tables with real storage cost and no query activity across several consecutive daily checks — detected from actual query telemetry, not naming heuristics."
      />

      {query.isError ? <QueryErrorAlert error={query.error} /> : null}

      <MetricGrid columns={3}>
        <MetricTile
          label="Dark table candidates"
          value={isLoading ? undefined : formatNumber(query.data?.total_candidates ?? 0)}
          loading={isLoading}
        />
        <MetricTile
          label="Storage sitting idle"
          value={isLoading ? undefined : formatGb(query.data?.total_size_gb ?? 0)}
          emphasis
          loading={isLoading}
        />
        <MetricTile
          label="Estimated monthly cost"
          value={
            isLoading ? undefined : formatCurrency(query.data?.total_estimated_monthly_cost_usd ?? 0)
          }
          emphasis
          loading={isLoading}
        />
      </MetricGrid>

      <Card>
        <CardHeader>
          <CardTitle>Candidates, ranked by storage size</CardTitle>
          <CardDescription>
            A table needs several consecutive days of zero query activity across its trailing
            30-day window before it's confirmed here — a single quiet day never qualifies.
            Detection only: nothing is deleted or archived automatically.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : candidates.length === 0 ? (
            <EmptyState
              title="No confirmed dark tables"
              description="Either every table has recent query activity, or detection hasn't accumulated enough consecutive daily checks yet — this list fills in over the next few days."
            />
          ) : (
            candidates.map((candidate) => (
              <CandidateRow key={candidate.id} candidate={candidate} />
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
};
