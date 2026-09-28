import type { ColumnDef } from "@tanstack/react-table";
import { Info } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  approveSmartLayoutRecommendation,
  dismissSmartLayoutRecommendation,
  executeSmartLayoutRecommendation,
  getDetectionTiers,
  getSmartMaintenanceOverview,
  getTableRoiBreakdown,
  getTableWorkloadForecast,
  listSmartLayoutRecommendations,
  type DetectionTierRowRead,
  type SmartLayoutRecommendationRead
} from "../api/client";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { PageHeader } from "../components/common";
import { ConfirmAction } from "../components/confirm";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { DataTable } from "../components/ui/data-table";
import { EmptyState } from "../components/ui/empty-state";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../components/ui/sheet";
import { Skeleton } from "../components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { MetricTile } from "../components/ui/stat";
import { useCommitEventStream } from "../hooks/useCommitEventStream";
import { useAuth } from "../lib/auth";
import { VIEWER_REASON } from "../lib/permissions";
import { useWriteControl } from "../lib/useWriteControl";
import { formatPercent, formatTime } from "../lib/format";

const TIERING_HELP_CONTENT = (
  <div className="max-w-sm space-y-2 text-sm">
    <p>Every scanned table gets one of four tiers so maintenance effort goes where it matters:</p>
    <ul className="list-disc space-y-1 pl-4">
      <li>
        <strong>HOT</strong> — heavy recent write or read churn; maintenance pays off immediately.
      </li>
      <li>
        <strong>WARM</strong> — moderate activity; worth scheduling regular maintenance.
      </li>
      <li>
        <strong>COLD</strong> — low activity; safe to deprioritize routine maintenance.
      </li>
      <li>
        <strong>DORMANT</strong> — no recent commits or queries and a stale scan history.
      </li>
    </ul>
    <p>
      The tier comes from a score built from commits in the last 24h/7d, query volume over the last 7 days, small-file
      count, and delete-file percentage.
    </p>
    <p className="text-[var(--color-status-warning)]">
      A table&apos;s <em>first</em> scan always lands in COLD, even for a genuinely busy table — commit velocity is
      measured by diffing snapshots across scans, and query volume needs a history window to build up.
    </p>
  </div>
);

const TieringHelpButton = () => (
  <Popover>
    <PopoverTrigger asChild>
      <Button variant="ghost" size="icon" aria-label="How tiering works">
        <Info className="size-4" aria-hidden="true" />
      </Button>
    </PopoverTrigger>
    <PopoverContent align="start" className="w-96">
      <p className="mb-2 font-semibold">How tiering works</p>
      {TIERING_HELP_CONTENT}
    </PopoverContent>
  </Popover>
);

const tierBadgeVariant = (tier: string): "danger" | "warning" | "default" | "secondary" => {
  if (tier === "hot") return "danger";
  if (tier === "warm") return "warning";
  if (tier === "cold") return "default";
  return "secondary";
};

const sevenDaysAgo = () => Date.now() - 7 * 24 * 60 * 60 * 1000;

const recommendationCounts = (items: SmartLayoutRecommendationRead[]) => {
  const committed7d = items.filter((item) => {
    if (item.status !== "applied" || !item.applied_at) return false;
    return Date.parse(item.applied_at) >= sevenDaysAgo();
  }).length;
  return {
    pending: items.filter((item) => item.status === "pending").length,
    approved: items.filter((item) => item.status === "approved").length,
    dismissed: items.filter((item) => item.status === "rejected").length,
    committed7d
  };
};

const KpiSkeleton = () => (
  <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
    {Array.from({ length: 4 }).map((_, index) => (
      <MetricTile key={index} label="Loading" value="—" loading />
    ))}
  </div>
);

const DescriptionList = ({
  items
}: {
  items: Array<{ label: string; value: ReactNode }>;
}) => (
  <dl className="grid grid-cols-2 gap-3 rounded-[var(--radius-default)] border border-border p-3 text-sm sm:grid-cols-3 lg:grid-cols-4">
    {items.map((item) => (
      <div key={item.label}>
        <dt className="text-xs text-muted-foreground">{item.label}</dt>
        <dd className="font-medium">{item.value}</dd>
      </div>
    ))}
  </dl>
);

export function SmartMaintenance() {
  const { api, token, role } = useAuth();
  const approveAccess = useWriteControl("approveLayout");
  const executeAccess = useWriteControl("executeLayout");
  const dismissAccess = useWriteControl("dismissLayout");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") === "tiers" ? "tiers" : "overview";
  const inspectTableId = searchParams.get("inspect");

  const setActiveTab = (tab: string) => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (tab === "overview") next.delete("tab");
        else next.set("tab", tab);
        return next;
      },
      { replace: true }
    );
  };

  const openInspect = (row: DetectionTierRowRead) => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.set("inspect", row.table_id);
        return next;
      },
      { replace: false }
    );
  };
  const closeInspect = () => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete("inspect");
        return next;
      },
      { replace: false }
    );
  };

  useCommitEventStream({
    onTick: () => {
      void queryClient.invalidateQueries({ queryKey: ["smart-maintenance-overview"] });
      void queryClient.invalidateQueries({ queryKey: ["smart-maintenance-detection-tiers"] });
    }
  });

  const overview = useQuery({
    queryKey: ["smart-maintenance-overview", token],
    queryFn: () => getSmartMaintenanceOverview(api)
  });
  const tiers = useQuery({
    queryKey: ["smart-maintenance-detection-tiers", token],
    queryFn: () => getDetectionTiers(api, 500)
  });
  const recommendations = useQuery({
    queryKey: ["smart-maintenance-layout-recommendations", token],
    queryFn: () => listSmartLayoutRecommendations(api)
  });

  const roi = useQuery({
    queryKey: ["smart-maintenance-roi", inspectTableId],
    queryFn: () => getTableRoiBreakdown(api, inspectTableId as string),
    enabled: Boolean(inspectTableId)
  });
  const forecast = useQuery({
    queryKey: ["smart-maintenance-forecast", inspectTableId],
    queryFn: () => getTableWorkloadForecast(api, inspectTableId as string, 24),
    enabled: Boolean(inspectTableId)
  });

  const recommendationItems = recommendations.data?.items ?? [];
  const recStats = useMemo(() => recommendationCounts(recommendationItems), [recommendationItems]);
  const tierItems = tiers.data?.items ?? [];
  const inspectTable = useMemo(
    () => (inspectTableId ? (tierItems.find((row) => row.table_id === inspectTableId) ?? null) : null),
    [inspectTableId, tierItems]
  );

  const refreshQueries = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["smart-maintenance-layout-recommendations"] }),
      queryClient.invalidateQueries({ queryKey: ["layout-recommendations"] })
    ]);
  };

  const approve = useMutation({
    mutationFn: (rec: SmartLayoutRecommendationRead) => approveSmartLayoutRecommendation(api, rec.id),
    onSuccess: async () => {
      toast.success("Recommendation approved");
      await refreshQueries();
    },
    onError: (error: Error) => toast.error(error.message)
  });
  const dismiss = useMutation({
    mutationFn: (rec: SmartLayoutRecommendationRead) =>
      dismissSmartLayoutRecommendation(api, rec.id, "dismissed_from_ui"),
    onSuccess: async () => {
      toast.success("Recommendation dismissed");
      await refreshQueries();
    },
    onError: (error: Error) => toast.error(error.message)
  });
  const execute = useMutation({
    mutationFn: (rec: SmartLayoutRecommendationRead) =>
      executeSmartLayoutRecommendation(api, rec.id, "standard"),
    onSuccess: async (result) => {
      toast.success("Layout change applied");
      await refreshQueries();
      navigate(`/runs/${result.run_id}`);
    },
    onError: (error: Error) => toast.error(error.message)
  });

  const recStatusTimestamp = (rec: SmartLayoutRecommendationRead): string | null => {
    if (rec.status === "applied" && rec.applied_at) return `Applied ${formatTime(rec.applied_at)}`;
    if (rec.status === "approved" && rec.approved_at) return `Approved ${formatTime(rec.approved_at)}`;
    if (rec.status === "rejected" && rec.dismissed_at) return `Dismissed ${formatTime(rec.dismissed_at)}`;
    return null;
  };

  const recColumns: ColumnDef<SmartLayoutRecommendationRead, unknown>[] = [
    {
      id: "table_ref",
      header: "Table",
      cell: ({ row }) => (
        <Button variant="ghost" className="h-auto p-0 text-primary" onClick={() => navigate(`/tables/${row.original.table_id}`)}>
          {row.original.table_ref}
        </Button>
      )
    },
    { id: "strategy", header: "Strategy", cell: ({ row }) => row.original.recommended_strategy },
    {
      id: "columns",
      header: "Columns",
      cell: ({ row }) => (row.original.recommended_columns?.length ? row.original.recommended_columns.join(", ") : "-")
    },
    {
      id: "confidence",
      header: "Confidence",
      cell: ({ row }) => formatPercent(row.original.confidence * 100, 0)
    },
    {
      id: "benefit",
      header: "Benefit",
      cell: ({ row }) => {
        const observed = row.original.observed_prune_improvement;
        const estimated = row.original.estimated_prune_improvement;
        if (observed != null) {
          return <span title="Observed after execution">{formatPercent(observed * 100, 0)} observed</span>;
        }
        if (estimated != null) {
          return <span title="Estimated before execution">{formatPercent(estimated * 100, 0)} est.</span>;
        }
        return <span className="text-muted-foreground">—</span>;
      }
    },
    {
      id: "status",
      header: "Status",
      cell: ({ row }) => (
        <div className="flex flex-col gap-0.5">
          <Badge variant="secondary">{row.original.status.toUpperCase()}</Badge>
          {recStatusTimestamp(row.original) ? (
            <span className="text-xs text-muted-foreground">{recStatusTimestamp(row.original)}</span>
          ) : null}
        </div>
      )
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          <ConfirmAction
            title="Approve this recommendation?"
            description={
              <span>
                Marks <strong>{row.original.table_ref}</strong>&apos;s layout change as reviewed and ready to run. It
                does not change any data yet and can still be dismissed afterward.
              </span>
            }
            onConfirm={() => approve.mutate(row.original)}
            disabled={!approveAccess.allowed || row.original.status === "applied" || row.original.status === "approved"}
          >
            <Button
              size="sm"
              variant="outline"
              disabled={!approveAccess.allowed || row.original.status === "applied" || row.original.status === "approved"}
              title={approveAccess.reason ?? undefined}
              loading={approve.isPending}
            >
              Approve
            </Button>
          </ConfirmAction>
          <ConfirmAction
            title="Apply this layout change?"
            description={
              <span>
                Physically re-sorts <strong>{row.original.table_ref}</strong> by{" "}
                <strong>{row.original.recommended_columns?.join(", ") || "the recommended columns"}</strong> (
                {row.original.recommended_strategy}) and sets <code>write.sort-order</code>. This runs a maintenance
                job now and cannot be undone once it completes.
              </span>
            }
            onConfirm={() => execute.mutate(row.original)}
            disabled={!executeAccess.allowed || row.original.status !== "approved"}
          >
            <Button
              size="sm"
              variant="primary"
              disabled={!executeAccess.allowed || row.original.status !== "approved"}
              title={executeAccess.reason ?? undefined}
              loading={execute.isPending}
            >
              Execute
            </Button>
          </ConfirmAction>
          <ConfirmAction
            title="Dismiss this recommendation?"
            description={
              <span>
                Removes <strong>{row.original.table_ref}</strong> from the pending layout queue. This does not affect
                table data and a new recommendation can appear again after the next evaluation.
              </span>
            }
            onConfirm={() => dismiss.mutate(row.original)}
            disabled={!dismissAccess.allowed || row.original.status === "rejected" || row.original.status === "applied"}
          >
            <Button
              size="sm"
              variant="danger"
              disabled={!dismissAccess.allowed || row.original.status === "rejected" || row.original.status === "applied"}
              title={dismissAccess.reason ?? undefined}
              loading={dismiss.isPending}
            >
              Dismiss
            </Button>
          </ConfirmAction>
        </div>
      )
    }
  ];

  const tierColumns: ColumnDef<DetectionTierRowRead, unknown>[] = [
    {
      id: "table",
      header: "Table",
      cell: ({ row }) => (
        <Button
          variant="ghost"
          className="h-auto p-0"
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/tables/${row.original.table_id}`);
          }}
        >
          {row.original.schema_name}.{row.original.table_name}
        </Button>
      )
    },
    {
      id: "tier",
      header: "Tier",
      cell: ({ row }) => (
        <Badge variant={tierBadgeVariant(row.original.tier)}>{row.original.tier.toUpperCase()}</Badge>
      )
    },
    {
      id: "score",
      header: "Score",
      cell: ({ row }) =>
        row.original.score == null ? <span className="text-muted-foreground">Not scored</span> : row.original.score
    },
    { id: "commits_24h", header: "Commits (24h)", cell: ({ row }) => row.original.commits_24h },
    { id: "commits_7d", header: "Commits (7d)", cell: ({ row }) => row.original.commits_7d },
    { id: "delete_file_pct", header: "Delete %", cell: ({ row }) => row.original.delete_file_pct },
    { id: "query_count_30d", header: "Queries (30d)", cell: ({ row }) => row.original.query_count_30d },
    {
      id: "inspect",
      header: "",
      cell: ({ row }) => (
        <Button
          size="sm"
          variant="secondary"
          onClick={(e) => {
            e.stopPropagation();
            openInspect(row.original);
          }}
        >
          Inspect ROI
        </Button>
      )
    }
  ];

  const emptyRecommendationsDescription = (() => {
    const ov = overview.data;
    if (!ov) return "Sort-order suggestions appear after scan telemetry builds up.";
    if (ov.tables_with_stats === 0) {
      return "No tables have been scanned yet, so there is nothing to evaluate for layout changes.";
    }
    if (ov.roi_shadow_decisions_30d === 0 && !ov.roi_enforced) {
      return `Evaluated ${formatTime(ov.generated_at)} — ROI enforcement is off and no shadow decisions have been recorded yet; recommendations may still be building a baseline.`;
    }
    return `Evaluated ${formatTime(ov.generated_at)} — no pending layout changes found for the tables scanned so far.`;
  })();

  return (
    <div>
      {role === "viewer" ? <p className="mb-3 text-xs text-muted-foreground">{VIEWER_REASON}</p> : null}
      <PageHeader
        title="Smart Maintenance"
        subtitle="Workload-aware tiers and layout recommendations from scan telemetry."
        actions={<TieringHelpButton />}
      />

      {overview.data ? (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>Last evaluated {formatTime(overview.data.generated_at)}</span>
          <Badge variant={overview.data.roi_enforced ? "success" : "secondary"}>
            {overview.data.roi_enforced ? "ROI enforcement on" : "ROI enforcement off (shadow mode)"}
          </Badge>
        </div>
      ) : null}

      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label="Pending" value={recStats.pending} loading={recommendations.isLoading} emphasis={recStats.pending > 0} />
        <MetricTile label="Approved" value={recStats.approved} loading={recommendations.isLoading} />
        <MetricTile label="Dismissed" value={recStats.dismissed} loading={recommendations.isLoading} />
        <MetricTile label="Committed (7d)" value={recStats.committed7d} loading={recommendations.isLoading} />
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="tiers">Detection Tiers</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <div className="space-y-4">
            <AsyncBoundary
              isLoading={overview.isLoading}
              isError={overview.isError}
              error={overview.error}
              onRetry={() => void overview.refetch()}
              skeleton={<KpiSkeleton />}
            >
              {overview.data ? (
                <DescriptionList
                  items={[
                    { label: "Tables with stats", value: overview.data.tables_with_stats },
                    { label: "ROI shadow (30d)", value: overview.data.roi_shadow_decisions_30d },
                    { label: "HOT", value: overview.data.tiers.hot },
                    { label: "WARM", value: overview.data.tiers.warm },
                    { label: "COLD", value: overview.data.tiers.cold },
                    { label: "DORMANT", value: overview.data.tiers.dormant },
                    { label: "UNKNOWN", value: overview.data.tiers.unknown }
                  ]}
                />
              ) : null}
            </AsyncBoundary>

            <AsyncBoundary
              isLoading={recommendations.isLoading}
              isError={recommendations.isError}
              error={recommendations.error}
              onRetry={() => void recommendations.refetch()}
              isEmpty={recommendationItems.length === 0}
              emptyTitle="No layout recommendations"
              emptyDescription={emptyRecommendationsDescription}
            >
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle>Layout recommendations</CardTitle>
                </CardHeader>
                <CardContent className="p-0 pt-0">
                  <DataTable
                    columns={recColumns}
                    data={recommendationItems}
                    getRowId={(row) => row.id}
                  />
                </CardContent>
              </Card>
            </AsyncBoundary>
          </div>
        </TabsContent>
        <TabsContent value="tiers">
          <AsyncBoundary
            isLoading={tiers.isLoading}
            isError={tiers.isError}
            error={tiers.error}
            onRetry={() => void tiers.refetch()}
          >
            <Card>
              <CardHeader className="pb-2">
                <CardTitle>Detection tiers</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 p-0 pt-0">
                <p className="px-4 text-sm text-muted-foreground">
                  Click a row, or use Inspect ROI, to see the ROI decision and workload forecast behind a table&apos;s
                  tier.
                </p>
                <DataTable
                  columns={tierColumns}
                  data={tierItems}
                  getRowId={(row) => row.table_id}
                  getRowClassName={() => "cursor-pointer"}
                />
              </CardContent>
            </Card>
          </AsyncBoundary>
        </TabsContent>
      </Tabs>

      <Sheet open={Boolean(inspectTableId)} onOpenChange={(open) => !open && closeInspect()}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>
              {inspectTable
                ? `ROI decision · ${inspectTable.schema_name}.${inspectTable.table_name}`
                : "ROI decision"}
            </SheetTitle>
          </SheetHeader>
          {inspectTableId ? (
            <div className="mt-4 space-y-4">
              <Button variant="ghost" className="h-auto p-0 text-primary" onClick={() => navigate(`/tables/${inspectTableId}`)}>
                Open table detail
              </Button>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle>ROI Breakdown</CardTitle>
                </CardHeader>
                <CardContent>
                  {roi.isLoading ? (
                    <Skeleton className="h-32 w-full" />
                  ) : roi.data ? (
                    <DescriptionList
                      items={[
                        { label: "Action", value: roi.data.decision.action },
                        { label: "Reason", value: roi.data.decision.reason },
                        { label: "BCR", value: roi.data.decision.bcr.toFixed(3) },
                        { label: "Threshold", value: roi.data.decision.threshold.toFixed(3) },
                        { label: "Benefit (USD/month)", value: roi.data.monthly_benefit_usd.toFixed(2) },
                        { label: "Cost (USD/month)", value: roi.data.monthly_cost_usd.toFixed(2) }
                      ]}
                    />
                  ) : (
                    <EmptyState title="No ROI data" description="No ROI data available for this table yet." />
                  )}
                </CardContent>
              </Card>
              {roi.data?.effectiveness ? (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle>Compaction effectiveness</CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-wrap gap-2">
                    <Badge variant={(roi.data.effectiveness.effectiveness_ratio ?? 0) >= 10 ? "success" : "warning"}>
                      Ratio {(roi.data.effectiveness.effectiveness_ratio ?? 0).toFixed(1)}×
                    </Badge>
                    <Badge variant="secondary">{roi.data.effectiveness.effectiveness}</Badge>
                    <Badge variant="secondary">{roi.data.effectiveness.run_count ?? 0} runs</Badge>
                  </CardContent>
                </Card>
              ) : null}
              {roi.data?.top_moop_partitions?.length ? (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle>Top MOOP-ranked partitions</CardTitle>
                  </CardHeader>
                  <CardContent className="p-0 pt-0">
                    <table className="td-schema-table w-full">
                      <thead>
                        <tr>
                          <th>Partition</th>
                          <th className="text-right">MOOP score</th>
                          <th className="text-right">Small files</th>
                        </tr>
                      </thead>
                      <tbody>
                        {roi.data.top_moop_partitions.map((partition) => (
                          <tr key={`${partition.partition_value}-${partition.moop_score}`}>
                            <td>{partition.partition_value}</td>
                            <td className="text-right">
                              {partition.moop_score != null ? partition.moop_score.toFixed(3) : "—"}
                            </td>
                            <td className="text-right">{partition.small_files}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </CardContent>
                </Card>
              ) : null}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle>Workload Forecast (next 24h)</CardTitle>
                </CardHeader>
                <CardContent>
                  {forecast.isLoading ? (
                    <Skeleton className="h-24 w-full" />
                  ) : forecast.data?.points?.length ? (
                    <table className="td-schema-table w-full">
                      <thead>
                        <tr>
                          <th>Hour</th>
                          <th>Expected Queries</th>
                        </tr>
                      </thead>
                      <tbody>
                        {forecast.data.points.map((point) => (
                          <tr key={point.hour_start}>
                            <td>{point.hour_start}</td>
                            <td>{point.expected_queries}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <EmptyState title="No forecast" description="No forecast available for this table yet." />
                  )}
                </CardContent>
              </Card>
            </div>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
