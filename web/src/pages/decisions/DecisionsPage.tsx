import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approvalAction,
  approveSmartLayoutRecommendation,
  dismissSmartLayoutRecommendation,
  executeSmartLayoutRecommendation,
  getDetectionTiers,
  getSlaStatusDashboard,
  getSmartMaintenanceOverview,
  getTableRoiBreakdown,
  getTableWorkloadForecast,
  listPendingApprovals,
  listSmartLayoutRecommendations,
  type PendingApprovalRead,
  type SmartLayoutRecommendationRead
} from "../../api/client";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import { ConfirmAction } from "../../components/confirm";
import { PageHeader } from "../../components/common";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Segmented } from "../../components/ui/segmented";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { EmptyState } from "../../components/ui/empty-state";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { useAuth } from "../../lib/auth";
import { formatPercent, formatTime } from "../../lib/format";
import { useWriteControl } from "../../lib/useWriteControl";
import { DecisionFunnelStrip } from "../operations/DecisionFunnelStrip";
import type { WriteAccess } from "../../lib/permissions";

const DECISION_TABS = ["recommendations", "approvals", "skipped"] as const;
type DecisionTab = (typeof DECISION_TABS)[number];

const INTERNAL_NAME = /\b(HOT|MOOP|WAIR|ROI)\b/i;

type CapSignal = {
  truncated?: boolean;
  capped?: boolean;
  limit?: number;
  cap?: number;
};

const tabFromSearch = (value: string | null): DecisionTab =>
  value === "approvals" || value === "skipped" ? value : "recommendations";

const isHiddenNoneStrategy = (rec: SmartLayoutRecommendationRead): boolean =>
  rec.recommended_strategy.trim().toLowerCase() === "none" && rec.confidence === 0;

const reportedListCap = (payload: unknown): number | null => {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as CapSignal;
  if (record.truncated !== true && record.capped !== true) return null;
  if (typeof record.limit === "number" && Number.isFinite(record.limit)) return record.limit;
  if (typeof record.cap === "number" && Number.isFinite(record.cap)) return record.cap;
  return null;
};

const formatCappedCount = (count: number, payload: unknown): string => {
  const cap = reportedListCap(payload);
  return cap == null ? String(count) : `${cap}+`;
};

const plainLabel = (text: string): string =>
  text
    .replace(/\bHOT\b/gi, "heavy activity")
    .replace(/\bMOOP\b/gi, "partition rank")
    .replace(/\bWAIR\b/gi, "workload layout")
    .replace(/\bROI\b/gi, "benefit-cost");

const InternalText = ({ text }: { text: string }) => {
  if (!text || !INTERNAL_NAME.test(text)) return <>{text || "—"}</>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help underline decoration-dotted underline-offset-2">{plainLabel(text)}</span>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
};

const tierPresentation = (tier: string): { label: string; tip: string } => {
  const key = tier.toLowerCase();
  if (key === "hot") return { label: "Heavy recent activity", tip: "HOT" };
  if (key === "warm") return { label: "Moderate activity", tip: "WARM" };
  if (key === "cold") return { label: "Low activity", tip: "COLD" };
  if (key === "dormant") return { label: "No recent activity", tip: "DORMANT" };
  return { label: "Not classified", tip: tier };
};

const proposedChange = (rec: SmartLayoutRecommendationRead): string => {
  const columns = rec.recommended_columns?.length ? rec.recommended_columns.join(", ") : null;
  const strategy = rec.recommended_strategy?.trim();
  if (strategy && columns) return `${strategy} on ${columns}`;
  return strategy || columns || "—";
};

const expectedEffect = (rec: SmartLayoutRecommendationRead): string => {
  if (rec.observed_prune_improvement != null) {
    return `${formatPercent(rec.observed_prune_improvement * 100, 0)} observed`;
  }
  if (rec.estimated_prune_improvement != null) {
    return `${formatPercent(rec.estimated_prune_improvement * 100, 0)} estimated`;
  }
  return "—";
};

const reviewStamp = (rec: SmartLayoutRecommendationRead): string | null => {
  if (rec.status === "applied" && rec.applied_at) return `Applied ${formatTime(rec.applied_at)}`;
  if (rec.status === "approved" && rec.approved_at) return `Approved ${formatTime(rec.approved_at)}`;
  if (rec.status === "rejected" && rec.dismissed_at) return `Dismissed ${formatTime(rec.dismissed_at)}`;
  return null;
};

const gateCopy = (enforced: boolean): { label: string; tip: string } =>
  enforced
    ? { label: "Benefit-cost gate on", tip: "ROI enforcement on" }
    : { label: "Benefit-cost gate off", tip: "ROI enforcement off (shadow)" };

const statusVariant = (status: string): "warning" | "success" | "secondary" => {
  if (status === "pending") return "warning";
  if (status === "approved" || status === "applied") return "success";
  return "secondary";
};

const canExecuteLayout = (rec: SmartLayoutRecommendationRead): boolean =>
  rec.status === "approved" && (rec.recommended_strategy === "sort" || rec.recommended_strategy === "zorder");

const forecastHour = (value: string): string => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
};

const tabCount = (count: number | null): string => (count == null ? "—" : String(count));

const ProposalActions = ({
  rec,
  approveAccess,
  dismissAccess,
  executeAccess,
  approvePending,
  dismissPending,
  executePending,
  onApprove,
  onDismiss,
  onExecute,
  showReview,
  onReview
}: {
  rec: SmartLayoutRecommendationRead;
  approveAccess: WriteAccess;
  dismissAccess: WriteAccess;
  executeAccess: WriteAccess;
  approvePending: boolean;
  dismissPending: boolean;
  executePending: boolean;
  onApprove: (rec: SmartLayoutRecommendationRead) => void;
  onDismiss: (rec: SmartLayoutRecommendationRead) => void;
  onExecute: (rec: SmartLayoutRecommendationRead) => void;
  showReview?: boolean;
  onReview?: (rec: SmartLayoutRecommendationRead) => void;
}) => {
  const approveDisabled = !approveAccess.allowed || rec.status === "applied" || rec.status === "approved";
  const dismissDisabled = !dismissAccess.allowed || rec.status === "rejected" || rec.status === "applied";
  const executable = canExecuteLayout(rec);
  const executeDisabled = !executeAccess.allowed || !executable;
  const primaryIsExecute = rec.status === "approved" || rec.status === "applied";

  return (
    <div className="flex flex-wrap items-center gap-1">
      {primaryIsExecute ? (
        executable ? (
          <ConfirmAction
            title="Apply this layout change?"
            description={
              <span>
                Runs the approved layout change for <strong>{rec.table_ref}</strong> now.
              </span>
            }
            onConfirm={() => onExecute(rec)}
            disabled={executeDisabled}
          >
            <Button
              size="sm"
              variant="primary"
              disabled={executeDisabled}
              title={executeAccess.reason ?? undefined}
              loading={executePending}
            >
              Execute
            </Button>
          </ConfirmAction>
        ) : null
      ) : (
        <ConfirmAction
          title="Approve this recommendation?"
          description={
            <span>
              Marks <strong>{rec.table_ref}</strong> as reviewed. It does not change table data.
            </span>
          }
          onConfirm={() => onApprove(rec)}
          disabled={approveDisabled}
        >
          <Button
            size="sm"
            variant="primary"
            disabled={approveDisabled}
            title={approveAccess.reason ?? undefined}
            loading={approvePending}
          >
            Approve
          </Button>
        </ConfirmAction>
      )}
      <ConfirmAction
        title="Dismiss this recommendation?"
        description={
          <span>
            Removes <strong>{rec.table_ref}</strong> from the pending queue. Table data is unchanged.
          </span>
        }
        onConfirm={() => onDismiss(rec)}
        disabled={dismissDisabled}
      >
        <Button
          size="sm"
          variant="ghost"
          disabled={dismissDisabled}
          title={dismissAccess.reason ?? undefined}
          loading={dismissPending}
        >
          Dismiss
        </Button>
      </ConfirmAction>
      {showReview && onReview ? (
        <Button size="sm" variant="ghost" onClick={() => onReview(rec)}>
          Review
        </Button>
      ) : null}
    </div>
  );
};

export function DecisionsPage() {
  const { api, token, subject } = useAuth();
  const navigate = useNavigate();
  const approveAccess = useWriteControl("approveLayout");
  const dismissAccess = useWriteControl("dismissLayout");
  const executeAccess = useWriteControl("executeLayout");
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = tabFromSearch(searchParams.get("tab"));
  const statusFilter = searchParams.get("status");
  const reviewId = searchParams.get("review");

  const setTab = (tab: string) => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (tab === "recommendations") next.delete("tab");
        else next.set("tab", tab);
        return next;
      },
      { replace: true }
    );
  };

  const setReview = (id: string | null) => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (id) next.set("review", id);
        else next.delete("review");
        return next;
      },
      { replace: false }
    );
  };

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
  const operationApprovals = useQuery({
    queryKey: ["pending-approvals", token],
    queryFn: () => listPendingApprovals(api)
  });
  const sla = useQuery({
    queryKey: ["sla-status", token, "decisions-missing-preset"],
    queryFn: () => getSlaStatusDashboard(api, true)
  });

  const visible = useMemo(
    () => (recommendations.data?.items ?? []).filter((item) => !isHiddenNoneStrategy(item)),
    [recommendations.data]
  );
  const pending = useMemo(() => visible.filter((item) => item.status === "pending"), [visible]);
  const shown = statusFilter === "pending" ? pending : visible;
  const approvalCount = operationApprovals.isSuccess ? (operationApprovals.data ?? []).length : null;
  const review = visible.find((item) => item.id === reviewId) ?? null;
  const reviewTier = review
    ? (tiers.data?.items ?? []).find((row) => row.table_id === review.table_id) ?? null
    : null;
  const missingPreset = (sla.data?.slas ?? []).filter((row) => !row.has_preset);
  const listCap = reportedListCap(recommendations.data);
  const writeReasons = [...new Set([approveAccess.reason, dismissAccess.reason, executeAccess.reason].filter(Boolean))];

  const roi = useQuery({
    queryKey: ["smart-maintenance-roi", review?.table_id],
    queryFn: () => getTableRoiBreakdown(api, review?.table_id as string),
    enabled: Boolean(review?.table_id)
  });
  const forecast = useQuery({
    queryKey: ["smart-maintenance-forecast", review?.table_id],
    queryFn: () => getTableWorkloadForecast(api, review?.table_id as string, 24),
    enabled: Boolean(review?.table_id)
  });

  const refreshQueries = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["smart-maintenance-layout-recommendations"] }),
      queryClient.invalidateQueries({ queryKey: ["layout-recommendations"] }),
      queryClient.invalidateQueries({ queryKey: ["pending-approvals"] })
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
    mutationFn: (rec: SmartLayoutRecommendationRead) => executeSmartLayoutRecommendation(api, rec.id, "standard"),
    onSuccess: async (result) => {
      toast.success("Layout change applied");
      await refreshQueries();
      navigate(`/runs/${result.run_id}`);
    },
    onError: (error: Error) => toast.error(error.message)
  });
  const actOnOperationApproval = useMutation({
    mutationFn: ({ id, approved }: { id: string; approved: boolean }) =>
      approvalAction(api, id, { approved, approver: subject || "ui-user" }),
    onSuccess: async (_, variables) => {
      toast.success(variables.approved ? "Request approved" : "Request rejected");
      await refreshQueries();
    },
    onError: (error: Error) => toast.error(error.message)
  });

  const queueColumns: ColumnDef<SmartLayoutRecommendationRead, unknown>[] = [
    {
      id: "table",
      header: "Table",
      cell: ({ row }) => (
        <Link to={`/tables/${row.original.table_id}`} className="text-primary hover:underline">
          {row.original.table_ref}
        </Link>
      )
    },
    {
      id: "change",
      header: "Proposed change",
      cell: ({ row }) => <InternalText text={proposedChange(row.original)} />
    },
    {
      id: "effect",
      header: "Expected effect",
      cell: ({ row }) => expectedEffect(row.original)
    },
    {
      id: "confidence",
      header: "Confidence",
      cell: ({ row }) => formatPercent(row.original.confidence * 100, 0)
    },
    {
      id: "state",
      header: "Review state",
      cell: ({ row }) => (
        <div className="flex w-fit max-w-full flex-col gap-0.5">
          <Badge variant={statusVariant(row.original.status)} className="w-fit capitalize">
            {row.original.status}
          </Badge>
          {reviewStamp(row.original) ? (
            <span className="text-xs text-muted-foreground">{reviewStamp(row.original)}</span>
          ) : null}
        </div>
      )
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <ProposalActions
          rec={row.original}
          approveAccess={approveAccess}
          dismissAccess={dismissAccess}
          executeAccess={executeAccess}
          approvePending={approve.isPending}
          dismissPending={dismiss.isPending}
          executePending={execute.isPending}
          onApprove={(item) => approve.mutate(item)}
          onDismiss={(item) => dismiss.mutate(item)}
          onExecute={(item) => execute.mutate(item)}
          showReview
          onReview={(item) => setReview(item.id)}
        />
      )
    }
  ];

  const approvalColumns: ColumnDef<PendingApprovalRead, unknown>[] = [
    {
      id: "table",
      header: "Table",
      cell: ({ row }) => (
        <Link to={`/tables/${row.original.table_id}`} className="text-primary hover:underline">
          {row.original.table_name || row.original.table_id}
        </Link>
      )
    },
    { id: "operation", header: "Operation", cell: ({ row }) => row.original.operation_type },
    { id: "requested", header: "Requested by", cell: ({ row }) => row.original.requested_by },
    {
      id: "created",
      header: "Requested",
      cell: ({ row }) => formatTime(row.original.created_at)
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          <ConfirmAction
            title="Approve this request?"
            description="Releases the waiting job on the next governor wave and authorizes later runs of this operation until the grant expires."
            onConfirm={() => actOnOperationApproval.mutate({ id: row.original.id, approved: true })}
            disabled={!approveAccess.allowed}
          >
            <Button
              size="sm"
              variant="outline"
              disabled={!approveAccess.allowed}
              title={approveAccess.reason ?? undefined}
              loading={actOnOperationApproval.isPending}
            >
              Approve request
            </Button>
          </ConfirmAction>
          <ConfirmAction
            title="Reject this request?"
            description="The request will not run."
            onConfirm={() => actOnOperationApproval.mutate({ id: row.original.id, approved: false })}
            disabled={!dismissAccess.allowed}
          >
            <Button
              size="sm"
              variant="danger"
              disabled={!dismissAccess.allowed}
              title={dismissAccess.reason ?? undefined}
              loading={actOnOperationApproval.isPending}
            >
              Reject
            </Button>
          </ConfirmAction>
        </div>
      )
    }
  ];

  const gate = overview.data ? gateCopy(overview.data.roi_enforced) : null;
  const queue = (
    <AsyncBoundary
      isLoading={recommendations.isLoading}
      isError={recommendations.isError}
      error={recommendations.error}
      onRetry={() => void recommendations.refetch()}
      fade={false}
      isEmpty={!recommendations.isLoading && shown.length === 0}
      emptyTitle="No proposals"
      emptyDescription="Strategies recorded as none at 0% confidence are hidden. Nothing else is waiting."
    >
      <DataTable columns={queueColumns} data={shown} getRowId={(row) => row.id} />
    </AsyncBoundary>
  );

  return (
    <div>
      <PageHeader
        title="Decisions"
        subtitle="What is waiting on a person, and why was a table skipped?"
        breadcrumbs={false}
        actions={
          gate ? (
            <div className="flex flex-wrap items-center justify-end gap-2">
              <span className="text-sm text-muted-foreground">
                Overview generated {formatTime(overview.data?.generated_at)}
              </span>
              {overview.data && !overview.data.roi_enforced ? (
                <Badge variant="warning" title={gate.tip}>
                  {gate.label}
                </Badge>
              ) : null}
            </div>
          ) : null
        }
      />
      {writeReasons.length ? (
        <p className="mb-3 text-xs text-muted-foreground">{writeReasons.join(" ")}</p>
      ) : null}

      <div className="mb-4">
        <Segmented
          aria-label="Decisions"
          value={activeTab}
          onChange={setTab}
          options={[
            { value: "recommendations", label: `Recommendations · ${tabCount(recommendations.isSuccess ? visible.length : null)}` },
            { value: "approvals", label: `Approvals · ${tabCount(approvalCount)}` },
            { value: "skipped", label: "Skipped · —" }
          ]}
        />
      </div>

      {activeTab === "recommendations" ? (
        <div className="space-y-3" role="tabpanel">
          {listCap != null || statusFilter === "pending" ? (
            <p className="text-sm text-muted-foreground">
              {listCap != null ? `Returned list ${formatCappedCount(visible.length, recommendations.data)}` : null}
              {statusFilter === "pending" ? " Showing pending proposals." : null}
            </p>
          ) : null}
          {statusFilter === "pending" ? (
            <Button
              type="button"
              variant="ghost"
              className="h-auto p-0 text-primary"
              onClick={() =>
                setSearchParams(
                  (previous) => {
                    const next = new URLSearchParams(previous);
                    next.delete("status");
                    return next;
                  },
                  { replace: true }
                )
              }
            >
              Show all proposals
            </Button>
          ) : null}
          {queue}
        </div>
      ) : null}

      {activeTab === "approvals" ? (
        <div className="space-y-4" role="tabpanel">
          <p className="text-sm text-muted-foreground">
            Layout proposals stay on Recommendations.{" "}
            <Link to={{ search: "?status=pending" }} className="text-primary hover:underline">
              Show pending proposals
            </Link>
          </p>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle id="operation-approvals">Operation approval requests</CardTitle>
            </CardHeader>
            <CardContent className="p-0 pt-0">
              <AsyncBoundary
                isLoading={operationApprovals.isLoading}
                isError={operationApprovals.isError}
                error={operationApprovals.error}
                onRetry={() => void operationApprovals.refetch()}
                isEmpty={!operationApprovals.isLoading && (operationApprovals.data ?? []).length === 0}
                emptyTitle="No operation approval requests"
                emptyDescription="Destructive operations that require a person appear here. Layout proposals stay in Recommendations."
              >
                <DataTable
                  columns={approvalColumns}
                  data={operationApprovals.data ?? []}
                  getRowId={(row) => row.id}
                />
              </AsyncBoundary>
            </CardContent>
          </Card>
          {sla.isError ? (
            <p className="text-sm text-muted-foreground">Missing-preset alert could not be loaded.</p>
          ) : null}
          {missingPreset.length > 0 ? (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle>Missing preset</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="text-muted-foreground">
                  Breached tables with no preset assigned. This is an alert, not a second proposal queue.
                </p>
                <ul className="space-y-1">
                  {missingPreset.map((row) => (
                    <li key={row.table_id}>
                      <Link to={`/tables/${row.table_id}`} className="text-primary hover:underline">
                        {row.table_name}
                      </Link>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ) : null}
        </div>
      ) : null}

      {activeTab === "skipped" ? (
        <section className="space-y-3" role="tabpanel" aria-labelledby="nightly-funnel-title">
          <DecisionFunnelStrip />
          <EmptyState
            title="No skip list is stored"
            description="Per-table eligibility returns the latest evaluation only. A fleet history of skip reasons is not stored, so this view does not estimate how many tables were skipped."
          />
        </section>
      ) : null}

      <Sheet open={Boolean(review)} onOpenChange={(open) => !open && setReview(null)}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>{review ? `Review · ${review.table_ref}` : "Review"}</SheetTitle>
          </SheetHeader>
          {review ? (
            <div className="mt-4 space-y-4 text-sm">
              <Link to={`/tables/${review.table_id}`} className="text-primary hover:underline">
                Open table
              </Link>
              <dl className="grid gap-3">
                <div>
                  <dt className="text-xs text-muted-foreground">Proposed change</dt>
                  <dd className="text-base font-medium">
                    <InternalText text={proposedChange(review)} />
                  </dd>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <dt className="text-xs text-muted-foreground">Confidence</dt>
                    <dd>{formatPercent(review.confidence * 100, 0)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Expected effect</dt>
                    <dd>{expectedEffect(review)}</dd>
                  </div>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Activity</dt>
                  <dd>
                    {reviewTier ? (
                      <span title={tierPresentation(reviewTier.tier).tip}>{tierPresentation(reviewTier.tier).label}</span>
                    ) : (
                      <span className="text-muted-foreground">Not in the loaded activity list</span>
                    )}
                  </dd>
                </div>
              </dl>
              <p className="text-muted-foreground">
                Reason, evidence, evaluation time, input freshness, and settings are not stored on this proposal.
              </p>
              <p className="text-muted-foreground">A per-engine plan preview is not stored on this proposal.</p>
              <ProposalActions
                rec={review}
                approveAccess={approveAccess}
                dismissAccess={dismissAccess}
                executeAccess={executeAccess}
                approvePending={approve.isPending}
                dismissPending={dismiss.isPending}
                executePending={execute.isPending}
                onApprove={(item) => approve.mutate(item)}
                onDismiss={(item) => dismiss.mutate(item)}
                onExecute={(item) => execute.mutate(item)}
              />
              {roi.isLoading ? (
                <p className="text-muted-foreground">Loading benefit-cost decision…</p>
              ) : roi.data ? (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle title="ROI">Benefit-cost decision</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    <p>
                      <InternalText text={roi.data.decision.reason} />
                    </p>
                    <p className="text-muted-foreground" title="ROI">
                      {roi.data.decision.shadow ? "Not enforced" : "Enforced"}
                    </p>
                    {roi.data.top_moop_partitions?.length ? (
                      <table className="w-full text-sm">
                        <thead>
                          <tr>
                            <th className="text-left font-medium">Partition</th>
                            <th className="text-right font-medium">
                              <span title="MOOP">Rank</span>
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {roi.data.top_moop_partitions.map((partition) => (
                            <tr key={`${partition.partition_value}-${partition.moop_score}`}>
                              <td>{partition.partition_value ?? "—"}</td>
                              <td className="text-right">
                                {partition.moop_score != null ? partition.moop_score.toFixed(3) : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : null}
                  </CardContent>
                </Card>
              ) : (
                <p className="text-muted-foreground">No benefit-cost decision is recorded for this table.</p>
              )}
              {forecast.data?.points?.length ? (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle>Workload forecast</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <table className="w-full text-sm">
                      <thead>
                        <tr>
                          <th className="text-left font-medium">Hour</th>
                          <th className="text-right font-medium">Expected queries</th>
                        </tr>
                      </thead>
                      <tbody>
                        {forecast.data.points.map((point) => (
                          <tr key={point.hour_start}>
                            <td>{forecastHour(point.hour_start)}</td>
                            <td className="text-right">{point.expected_queries}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </CardContent>
                </Card>
              ) : null}
            </div>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
