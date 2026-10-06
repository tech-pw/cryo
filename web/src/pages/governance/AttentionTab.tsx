import type { ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";
import {
  approvalAction,
  createApprovalRequest,
  listFleetAlerts,
  listFleetAnomalies,
  listPendingApprovals,
  type AnomalyAlertRead,
  type PendingApprovalRead
} from "../../api/client";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import { ConfirmAction } from "../../components/confirm";
import { QueryErrorAlert } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "../../components/ui/dialog";
import { FormField, FormProvider, useZodForm } from "../../components/ui/form";
import { FormErrorSummary } from "../../components/ui/form-error-summary";
import { Textarea } from "../../components/ui/textarea";
import { useAuth } from "../../lib/auth";
import { formatTime } from "../../lib/format";
import { TableSearchSelect } from "./TableSearchSelect";

const ANOMALY_PAGE_SIZE = 8;
const APPROVAL_PAGE_SIZE = 8;
const APPROVAL_FADE_MS = 180;

const OPERATION_TYPES = [
  "expire_snapshots",
  "remove_orphan_files",
  "optimize",
  "optimize_manifests"
] as const;

const approvalFormSchema = z.object({
  table_id: z.string().min(1, "Select a table"),
  operation_type: z.enum(OPERATION_TYPES, { message: "Select an operation" }),
  reason: z.string().optional()
});

type ApprovalFormValues = z.infer<typeof approvalFormSchema>;

const APPROVAL_FIELD_LABELS: Record<string, string> = {
  table_id: "Table",
  operation_type: "Operation",
  reason: "Reason"
};

export const AttentionTab = () => {
  const { api, subject, role } = useAuth();
  const queryClient = useQueryClient();
  const canManage = role === "admin" || role === "operator";
  const canApprove = canManage;
  const [approvalModalOpen, setApprovalModalOpen] = useState(false);
  const [anomalyPage, setAnomalyPage] = useState(0);
  const [approvalPage, setApprovalPage] = useState(0);
  const [fadingApprovalIds, setFadingApprovalIds] = useState<Set<string>>(() => new Set());

  const approvalForm = useZodForm(approvalFormSchema, {
    defaultValues: {
      table_id: "",
      operation_type: undefined,
      reason: ""
    }
  });

  const approvals = useQuery({
    queryKey: ["pending-approvals", api.token],
    queryFn: () => listPendingApprovals(api),
    refetchInterval: 30_000
  });
  const costAlerts = useQuery({
    queryKey: ["fleet-alerts", api.token],
    queryFn: () => listFleetAlerts(api),
    refetchInterval: 60_000
  });
  const anomalies = useQuery({
    queryKey: ["fleet-anomalies", api.token],
    queryFn: () => listFleetAnomalies(api),
    refetchInterval: 60_000
  });

  const actOnApproval = useMutation({
    mutationFn: ({ id, approved }: { id: string; approved: boolean }) =>
      approvalAction(api, id, { approved, approver: subject || "ui-user" }),
    onSuccess: (_, variables) => {
      toast.success(variables.approved ? "Request approved" : "Request rejected");
      void queryClient.invalidateQueries({ queryKey: ["pending-approvals"] });
    },
    onError: (error, variables) => {
      toast.error((error as Error).message);
      setFadingApprovalIds((prev) => {
        const next = new Set(prev);
        next.delete(variables.id);
        return next;
      });
    }
  });

  const requestApproval = useMutation({
    mutationFn: (values: ApprovalFormValues) =>
      createApprovalRequest(api, {
        table_id: values.table_id,
        operation_type: values.operation_type,
        requested_by: subject || "ui-user",
        reason: values.reason
      }),
    onSuccess: () => {
      toast.success("Approval request submitted");
      setApprovalModalOpen(false);
      approvalForm.reset();
      void queryClient.invalidateQueries({ queryKey: ["pending-approvals"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const handleApprovalAction = useCallback(
    (id: string, approved: boolean) => {
      setFadingApprovalIds((prev) => new Set(prev).add(id));
      window.setTimeout(() => {
        actOnApproval.mutate({ id, approved });
      }, APPROVAL_FADE_MS);
    },
    [actOnApproval]
  );

  const approvalColumns = useMemo<ColumnDef<PendingApprovalRead, unknown>[]>(
    () => [
      {
        id: "table",
        header: "Table",
        cell: ({ row }) => (
          <Link to={`/tables/${row.original.table_id}`} className="text-primary hover:underline">
            {row.original.table_name ?? row.original.table_id}
          </Link>
        )
      },
      {
        accessorKey: "operation_type",
        header: "Operation",
        size: 140
      },
      {
        accessorKey: "requested_by",
        header: "Requested by",
        size: 120
      },
      {
        accessorKey: "created_at",
        header: "Created",
        size: 140,
        cell: ({ getValue }) => formatTime(getValue() as string)
      },
      {
        id: "actions",
        header: "Actions",
        size: 180,
        cell: ({ row }) =>
          canApprove ? (
            <div className="flex items-center gap-1">
              <ConfirmAction
                title="Approve this maintenance request?"
                description="Releases the waiting job on the next governor wave and authorizes later runs of this operation until the grant expires."
                okText="Approve"
                okButtonProps={{ danger: false }}
                onConfirm={() => handleApprovalAction(row.original.id, true)}
              >
                <Button size="sm" loading={actOnApproval.isPending}>
                  Approve
                </Button>
              </ConfirmAction>
              <ConfirmAction
                title="Reject this maintenance request?"
                description="The request will be marked rejected and will not run."
                onConfirm={() => handleApprovalAction(row.original.id, false)}
              >
                <Button size="sm" variant="danger" loading={actOnApproval.isPending}>
                  Reject
                </Button>
              </ConfirmAction>
            </div>
          ) : (
            <span className="text-muted-foreground">—</span>
          )
      }
    ],
    [canApprove, actOnApproval.isPending, handleApprovalAction]
  );

  const anomalyColumns = useMemo<ColumnDef<AnomalyAlertRead, unknown>[]>(
    () => [
      {
        id: "table",
        header: "Table",
        cell: ({ row }) => (
          <Link to={`/tables/${row.original.table_id}`} className="text-primary hover:underline">
            {row.original.table_name}
          </Link>
        )
      },
      {
        accessorKey: "metric_name",
        header: "Metric",
        size: 120
      },
      {
        accessorKey: "severity",
        header: "Severity",
        size: 100,
        cell: ({ getValue }) => {
          const value = String(getValue());
          return <Badge variant={value === "critical" ? "danger" : "warning"}>{value}</Badge>;
        }
      },
      {
        accessorKey: "deviation_sigma",
        header: "σ",
        size: 64,
        cell: ({ getValue }) => (
          <span className="block text-right [font-variant-numeric:tabular-nums]">{getValue() as number}</span>
        )
      }
    ],
    []
  );

  const approvalsData = approvals.data ?? [];

  useEffect(() => {
    setFadingApprovalIds((prev) => {
      if (prev.size === 0) return prev;
      const visibleIds = new Set(approvalsData.map((row) => row.id));
      const next = new Set([...prev].filter((id) => visibleIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [approvalsData]);

  const approvalPageData = approvalsData.slice(
    approvalPage * APPROVAL_PAGE_SIZE,
    (approvalPage + 1) * APPROVAL_PAGE_SIZE
  );
  const anomaliesData = anomalies.data ?? [];
  const anomalyPageData = anomaliesData.slice(
    anomalyPage * ANOMALY_PAGE_SIZE,
    (anomalyPage + 1) * ANOMALY_PAGE_SIZE
  );

  return (
    <>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle>Pending approvals</CardTitle>
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{approvalsData.length}</Badge>
              {canManage ? (
                <Button size="sm" onClick={() => setApprovalModalOpen(true)}>
                  <Plus className="size-3.5" aria-hidden="true" />
                  Request
                </Button>
              ) : null}
            </div>
          </CardHeader>
          <CardContent>
            <QueryErrorAlert error={approvals.error} title="Failed to load approvals" />
            <AsyncBoundary isLoading={approvals.isLoading} isError={Boolean(approvals.error)} error={approvals.error}>
              <DataTable
                columns={approvalColumns}
                data={approvalPageData}
                density="compact"
                emptyTitle="No pending approvals"
                emptyDescription="Destructive ops requiring approval appear here."
                getRowId={(row) => row.id ?? `${row.table_id}-${row.operation_type}-${row.created_at}`}
                getRowClassName={(row) =>
                  fadingApprovalIds.has(row.id)
                    ? "opacity-0 transition-opacity duration-200 ease-[var(--ease-standard)]"
                    : undefined
                }
                pagination={
                  approvalsData.length > APPROVAL_PAGE_SIZE
                    ? {
                        pageIndex: approvalPage,
                        pageSize: APPROVAL_PAGE_SIZE,
                        totalRows: approvalsData.length,
                        onPageChange: setApprovalPage
                      }
                    : undefined
                }
              />
            </AsyncBoundary>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Fleet anomalies</CardTitle>
          </CardHeader>
          <CardContent>
            {(costAlerts.data ?? []).map((alert) => (
              <Alert
                key={alert.summary}
                variant={alert.severity === "high" ? "error" : "warning"}
                title={alert.explained ? "Cost move, explained" : "Cost alert"}
                description={alert.summary}
                className="mb-3"
              />
            ))}
            <QueryErrorAlert error={anomalies.error} title="Failed to load anomalies" />
            <AsyncBoundary isLoading={anomalies.isLoading} isError={Boolean(anomalies.error)} error={anomalies.error}>
              <DataTable
                columns={anomalyColumns}
                data={anomalyPageData}
                density="compact"
                emptyTitle="No anomalies detected"
                emptyDescription="Statistical drift alerts appear here after enough scan history."
                getRowId={(row) => `${row.table_id}-${row.metric_name}-${row.detected_at}`}
                pagination={
                  anomaliesData.length > ANOMALY_PAGE_SIZE
                    ? {
                        pageIndex: anomalyPage,
                        pageSize: ANOMALY_PAGE_SIZE,
                        totalRows: anomaliesData.length,
                        onPageChange: setAnomalyPage
                      }
                    : undefined
                }
              />
            </AsyncBoundary>
          </CardContent>
        </Card>
      </div>

      <Dialog
        open={approvalModalOpen}
        onOpenChange={(open) => {
          setApprovalModalOpen(open);
          if (!open) {
            requestApproval.reset();
            approvalForm.reset();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Request approval</DialogTitle>
            <DialogDescription>
              Submit a destructive maintenance operation for operator approval before it can run.
            </DialogDescription>
          </DialogHeader>
          {requestApproval.isError ? (
            <Alert
              variant="error"
              title="Couldn't submit approval request"
              description={(requestApproval.error as Error).message}
              onDismiss={() => requestApproval.reset()}
            />
          ) : null}
          <FormProvider {...approvalForm}>
            <form
              className="flex flex-col gap-3"
              onSubmit={approvalForm.handleSubmit((values) => requestApproval.mutate(values))}
            >
              <FormErrorSummary<ApprovalFormValues> labels={APPROVAL_FIELD_LABELS} />
              <FormField<ApprovalFormValues> name="table_id" label="Table">
                {(field, meta) => (
                  <TableSearchSelect
                    id={meta.id}
                    value={field.value ?? ""}
                    onChange={field.onChange}
                    disabled={requestApproval.isPending}
                  />
                )}
              </FormField>
              <FormField<ApprovalFormValues> name="operation_type" label="Operation">
                {(field) => (
                  <select
                    className="h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
                    value={field.value ?? ""}
                    onChange={(event) => field.onChange(event.target.value)}
                  >
                    <option value="">Select operation</option>
                    <option value="expire_snapshots">Expire snapshots</option>
                    <option value="remove_orphan_files">Remove orphan files</option>
                    <option value="optimize">Optimize</option>
                    <option value="optimize_manifests">Optimize manifests</option>
                  </select>
                )}
              </FormField>
              <FormField<ApprovalFormValues> name="reason" label="Reason">
                {(field) => (
                  <Textarea
                    rows={3}
                    placeholder="Why this destructive operation is needed"
                    value={String(field.value ?? "")}
                    onChange={field.onChange}
                    onBlur={field.onBlur}
                    name={field.name}
                    ref={field.ref}
                  />
                )}
              </FormField>
            </form>
          </FormProvider>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setApprovalModalOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={requestApproval.isPending}
              onClick={() => void approvalForm.handleSubmit((values) => requestApproval.mutate(values))()}
            >
              Submit request
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
};
