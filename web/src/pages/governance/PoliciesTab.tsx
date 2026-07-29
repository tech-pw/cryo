import type { ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  createTierPolicy,
  deleteTierPolicy,
  getS3TieringRecommendations,
  getTierCandidates,
  getGitOpsStatus,
  listAbacPolicies,
  listSources,
  listTables,
  listTierPolicies,
  patchTierPolicy,
  type TierPolicyRead,
  type TierPolicyWrite
} from "../../api/client";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import { ConfirmAction } from "../../components/confirm";
import { EmptyState, QueryErrorAlert } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "../../components/ui/dialog";
import { FormField, FormProvider, useForm } from "../../components/ui/form";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Switch } from "../../components/ui/switch";
import { useAuth } from "../../lib/auth";
import { formatCurrency } from "../../lib/format";
import { qualifiedTableName } from "../../lib/tableLabels";

const TIER_PAGE_SIZE = 8;

type TierPolicyFormValues = TierPolicyWrite;

export const PoliciesTab = () => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const canManage = role === "admin" || role === "operator";
  const [tierTableId, setTierTableId] = useState("");
  const [policyModal, setPolicyModal] = useState<TierPolicyRead | "new" | null>(null);
  const [tierPage, setTierPage] = useState(0);
  const [s3Page, setS3Page] = useState(0);

  const policyForm = useForm<TierPolicyFormValues>({
    defaultValues: {
      name: "",
      source_id: "",
      storage_class: "STANDARD_IA",
      min_age_days: 30,
      enabled: true
    }
  });

  const tierPolicies = useQuery({
    queryKey: ["tier-policies", api.token],
    queryFn: () => listTierPolicies(api)
  });
  const s3Tiering = useQuery({
    queryKey: ["s3-tiering-recommendations", api.token],
    queryFn: () => getS3TieringRecommendations(api, { min_size_gb: 50 }),
    retry: false
  });
  const tierCandidates = useQuery({
    queryKey: ["tier-candidates", api.token, tierTableId],
    queryFn: () => getTierCandidates(api, tierTableId),
    enabled: tierTableId.length > 8
  });
  const sources = useQuery({
    queryKey: ["sources", api.token],
    queryFn: () => listSources(api)
  });
  const tableOptions = useQuery({
    queryKey: ["governance-table-picker", api.token],
    queryFn: () => listTables(api, { limit: 200, offset: 0 })
  });
  const abacPolicies = useQuery({
    queryKey: ["abac-policies", api.token],
    queryFn: () => listAbacPolicies(api)
  });
  const gitopsStatus = useQuery({
    queryKey: ["gitops-status", api.token],
    queryFn: () => getGitOpsStatus(api),
    refetchInterval: 60_000
  });

  const saveTierPolicy = useMutation({
    mutationFn: (values: TierPolicyFormValues) =>
      policyModal && policyModal !== "new"
        ? patchTierPolicy(api, policyModal.id, values)
        : createTierPolicy(api, values),
    onSuccess: () => {
      toast.success("Tier policy saved");
      setPolicyModal(null);
      void queryClient.invalidateQueries({ queryKey: ["tier-policies"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const removeTierPolicy = useMutation({
    mutationFn: (id: string) => deleteTierPolicy(api, id),
    onSuccess: () => {
      toast.success("Tier policy deleted");
      void queryClient.invalidateQueries({ queryKey: ["tier-policies"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const openPolicyModal = (policy: TierPolicyRead | "new") => {
    setPolicyModal(policy);
    if (policy === "new") {
      policyForm.reset({
        name: "",
        source_id: sources.data?.[0]?.id ?? "",
        storage_class: "STANDARD_IA",
        min_age_days: 30,
        enabled: true
      });
      return;
    }
    policyForm.reset({
      name: policy.name,
      source_id: policy.source_id,
      storage_class: policy.storage_class,
      min_age_days: policy.min_age_days,
      enabled: policy.enabled
    });
  };

  const tierData = tierPolicies.data ?? [];
  const tierPageData = tierData.slice(tierPage * TIER_PAGE_SIZE, (tierPage + 1) * TIER_PAGE_SIZE);
  const s3Data = s3Tiering.data?.recommendations ?? [];
  const s3PageData = s3Data.slice(s3Page * TIER_PAGE_SIZE, (s3Page + 1) * TIER_PAGE_SIZE);

  const tierColumns = useMemo<ColumnDef<TierPolicyRead, unknown>[]>(
    () => [
      { accessorKey: "name", header: "Name" },
      { accessorKey: "storage_class", header: "Storage class", size: 140 },
      {
        accessorKey: "min_age_days",
        header: "Min age (days)",
        size: 120,
        cell: ({ getValue }) => <span className="block text-right">{String(getValue())}</span>
      },
      {
        accessorKey: "enabled",
        header: "Enabled",
        size: 90,
        cell: ({ getValue }) => (getValue() ? "Yes" : "No")
      },
      ...(canManage
        ? [
            {
              id: "actions",
              header: "Actions",
              size: 140,
              cell: ({ row }: { row: { original: TierPolicyRead } }) => (
                <div className="flex items-center gap-1">
                  <Button size="sm" variant="secondary" onClick={() => openPolicyModal(row.original)}>
                    Edit
                  </Button>
                  <ConfirmAction
                    title="Delete this tier policy?"
                    description="Tables will no longer be evaluated against this policy."
                    onConfirm={() => removeTierPolicy.mutate(row.original.id)}
                  >
                    <Button size="sm" variant="danger" loading={removeTierPolicy.isPending}>
                      Delete
                    </Button>
                  </ConfirmAction>
                </div>
              )
            } as ColumnDef<TierPolicyRead, unknown>
          ]
        : [])
    ],
    [canManage, removeTierPolicy.isPending]
  );

  const s3Columns = useMemo<ColumnDef<(typeof s3Data)[number], unknown>[]>(
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
        accessorKey: "current_size_gb",
        header: "Size",
        size: 90,
        cell: ({ getValue }) => <span className="block text-right">{`${(getValue() as number).toFixed(1)} GB`}</span>
      },
      {
        accessorKey: "days_since_last_access",
        header: "Days idle",
        size: 90,
        cell: ({ getValue }) => <span className="block text-right">{String(getValue())}</span>
      },
      {
        accessorKey: "estimated_annual_savings_usd",
        header: "Annual savings",
        size: 120,
        cell: ({ getValue }) => (
          <span className="block text-right">{formatCurrency(getValue() as number)}</span>
        )
      },
      {
        accessorKey: "recommendation",
        header: "Recommendation",
        size: 140,
        cell: ({ getValue }) => <Badge variant="secondary">{String(getValue())}</Badge>
      }
    ],
    []
  );

  const candidateColumns = useMemo<ColumnDef<Record<string, unknown>, unknown>[]>(
    () => [
      {
        accessorKey: "partition_value",
        header: "Partition",
        cell: ({ getValue }) => (getValue() ? String(getValue()) : "(table)")
      },
      {
        accessorKey: "age_days",
        header: "Age (days)",
        size: 100,
        cell: ({ getValue }) => <span className="block text-right">{String(getValue())}</span>
      },
      { accessorKey: "target_storage_class", header: "Target class", size: 140 }
    ],
    []
  );

  const abacColumns = useMemo<ColumnDef<Record<string, unknown>, unknown>[]>(
    () => [
      {
        accessorKey: "table_id",
        header: "Table",
        cell: ({ getValue }) => (
          <Link to={`/tables/${String(getValue())}`} className="text-primary hover:underline">
            <code className="text-xs">{String(getValue()).slice(0, 8)}</code>
          </Link>
        )
      },
      { accessorKey: "role", header: "Role", size: 100 },
      {
        accessorKey: "allowed_operations",
        header: "Allowed ops",
        cell: ({ getValue }) => ((getValue() as string[]) ?? []).join(", ") || "—"
      },
      {
        accessorKey: "has_row_filter",
        header: "Row filter",
        size: 90,
        cell: ({ getValue }) => (getValue() ? <Badge variant="default">yes</Badge> : "—")
      },
      {
        accessorKey: "masked_columns",
        header: "Masked columns",
        cell: ({ getValue }) => ((getValue() as string[]) ?? []).join(", ") || "—"
      }
    ],
    []
  );

  const gitopsColumns = useMemo<ColumnDef<Record<string, unknown>, unknown>[]>(
    () => [
      { accessorKey: "table_pattern", header: "Table pattern" },
      {
        accessorKey: "maintenance_operations",
        header: "Operations",
        cell: ({ getValue }) => ((getValue() as string[]) ?? []).join(", ") || "—"
      },
      {
        accessorKey: "has_alerts",
        header: "Alerts",
        size: 80,
        cell: ({ getValue }) => (getValue() ? <Badge variant="success">yes</Badge> : "—")
      }
    ],
    []
  );

  return (
    <>
      <div className="grid grid-cols-1 gap-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle>Tier policies</CardTitle>
            {canManage ? (
              <Button size="sm" onClick={() => openPolicyModal("new")}>
                <Plus className="size-3.5" aria-hidden="true" />
                Add policy
              </Button>
            ) : null}
          </CardHeader>
          <CardContent>
            <QueryErrorAlert error={tierPolicies.error} title="Failed to load tier policies" />
            <AsyncBoundary
              isLoading={tierPolicies.isLoading}
              isError={Boolean(tierPolicies.error)}
              error={tierPolicies.error}
            >
              <DataTable
                columns={tierColumns}
                data={tierPageData}
                density="compact"
                getRowId={(row) => row.id ?? `${row.name}-${row.source_id}-${row.storage_class}`}
                emptyTitle="No tier policies"
                emptyDescription="Create policies here or seed them via Helm for automated S3 tiering."
                pagination={
                  tierData.length > TIER_PAGE_SIZE
                    ? {
                        pageIndex: tierPage,
                        pageSize: TIER_PAGE_SIZE,
                        totalRows: tierData.length,
                        onPageChange: setTierPage
                      }
                    : undefined
                }
              />
            </AsyncBoundary>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle>S3 Intelligent-Tiering recommendations</CardTitle>
            {s3Tiering.data ? (
              <Badge variant="success">
                {formatCurrency(s3Tiering.data.total_potential_savings_usd)}/yr potential
              </Badge>
            ) : null}
          </CardHeader>
          <CardContent>
            <QueryErrorAlert error={s3Tiering.error} title="Failed to load S3 tiering recommendations" />
            <AsyncBoundary isLoading={s3Tiering.isLoading} isError={Boolean(s3Tiering.error)} error={s3Tiering.error}>
              <DataTable
                columns={s3Columns}
                data={s3PageData}
                density="compact"
                getRowId={(row) => row.table_id}
                emptyTitle="No tiering candidates"
                emptyDescription="Large tables with cold access patterns appear here after cost scans."
                pagination={
                  s3Data.length > TIER_PAGE_SIZE
                    ? {
                        pageIndex: s3Page,
                        pageSize: TIER_PAGE_SIZE,
                        totalRows: s3Data.length,
                        onPageChange: setS3Page
                      }
                    : undefined
                }
              />
            </AsyncBoundary>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Tier candidates by policy</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <select
              className="h-[var(--control-height)] max-w-md rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
              value={tierTableId}
              disabled={tableOptions.isLoading}
              onChange={(event) => setTierTableId(event.target.value)}
            >
              <option value="">Select a table to evaluate tier policies</option>
              {(tableOptions.data?.items ?? []).map((table) => (
                <option key={table.id} value={table.id}>
                  {qualifiedTableName(table)}
                </option>
              ))}
            </select>
            <QueryErrorAlert error={tierCandidates.error} title="Failed to load tier candidates" />
            {tierCandidates.data ? (
              <>
                <div className="flex flex-wrap gap-2">
                  <Badge variant="secondary">Candidates: {tierCandidates.data.candidates.length}</Badge>
                  {typeof tierCandidates.data.summary.estimated_monthly_savings_usd === "number" ? (
                    <Badge variant="success">
                      Est. savings {formatCurrency(tierCandidates.data.summary.estimated_monthly_savings_usd)}/mo
                    </Badge>
                  ) : null}
                </div>
                <DataTable
                  columns={candidateColumns}
                  data={tierCandidates.data.candidates as unknown as Record<string, unknown>[]}
                  density="compact"
                  getRowId={(row) =>
                    `${row.partition_value ?? "all"}-${row.age_days ?? "na"}-${row.target_storage_class ?? "na"}`
                  }
                  emptyTitle="No candidates"
                />
              </>
            ) : (
              <EmptyState
                title="Select a table"
                description="Tier candidates are computed from scan partitions and enabled tier policies."
              />
            )}
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle>ABAC policies</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <QueryErrorAlert error={abacPolicies.error} title="Failed to load ABAC policies" />
              <AsyncBoundary
                isLoading={abacPolicies.isLoading}
                isError={Boolean(abacPolicies.error)}
                error={abacPolicies.error}
              >
                {abacPolicies.data ? (
                  <>
                    <Alert variant="info" title={abacPolicies.data.enforcement_note} />
                    <DataTable
                      columns={abacColumns}
                      data={abacPolicies.data.items as unknown as Record<string, unknown>[]}
                      density="compact"
                      getRowId={(row) => String(row.id)}
                      emptyTitle="No ABAC policies configured"
                      emptyDescription="Every table/role pair is unrestricted until a policy row exists for it (fail-open)."
                    />
                  </>
                ) : null}
              </AsyncBoundary>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle>GitOps policy sync</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <QueryErrorAlert error={gitopsStatus.error} title="Failed to load GitOps status" />
              <AsyncBoundary
                isLoading={gitopsStatus.isLoading}
                isError={Boolean(gitopsStatus.error)}
                error={gitopsStatus.error}
              >
                {gitopsStatus.data && !gitopsStatus.data.configured ? (
                  <EmptyState
                    title="GitOps not configured"
                    description={gitopsStatus.data.message ?? "Set CRYO_GITOPS_REPO_PATH to enable maintenance-as-code."}
                  />
                ) : gitopsStatus.data ? (
                  <>
                    <p className="text-sm text-muted-foreground">
                      {gitopsStatus.data.repo_path} / {gitopsStatus.data.policy_dir}
                    </p>
                    {gitopsStatus.data.conflicts.length > 0 ? (
                      <Alert
                        variant="warning"
                        title={`${gitopsStatus.data.conflicts.length} policy conflict(s) detected`}
                        description={gitopsStatus.data.conflicts.join("; ")}
                      />
                    ) : null}
                    <DataTable
                      columns={gitopsColumns}
                      data={gitopsStatus.data.policies as unknown as Record<string, unknown>[]}
                      density="compact"
                      getRowId={(row) => String(row.table_pattern)}
                      emptyTitle="No policies loaded"
                      emptyDescription={
                        gitopsStatus.data.message ?? "No YAML policy files found in the configured directory."
                      }
                    />
                  </>
                ) : null}
              </AsyncBoundary>
            </CardContent>
          </Card>
        </div>
      </div>

      <Dialog open={policyModal !== null} onOpenChange={(open) => !open && setPolicyModal(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{policyModal === "new" ? "Create tier policy" : "Edit tier policy"}</DialogTitle>
          </DialogHeader>
          <FormProvider {...policyForm}>
            <form
              className="flex flex-col gap-3"
              onSubmit={policyForm.handleSubmit((values) => saveTierPolicy.mutate(values))}
            >
              <FormField name="name" label="Name">
                {(field) => <Input {...field} value={String(field.value ?? "")} />}
              </FormField>
              <FormField name="source_id" label="Source">
                {(field) => (
                  <select
                    className="h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
                    value={String(field.value ?? "")}
                    disabled={sources.isLoading}
                    onChange={(event) => field.onChange(event.target.value)}
                  >
                    {(sources.data ?? []).map((source) => (
                      <option key={source.id} value={source.id}>
                        {source.name}
                      </option>
                    ))}
                  </select>
                )}
              </FormField>
              <FormField name="storage_class" label="Storage class">
                {(field) => (
                  <select
                    className="h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
                    value={String(field.value ?? "")}
                    onChange={(event) => field.onChange(event.target.value)}
                  >
                    <option value="STANDARD_IA">STANDARD_IA</option>
                    <option value="GLACIER">GLACIER</option>
                    <option value="DEEP_ARCHIVE">DEEP_ARCHIVE</option>
                    <option value="INTELLIGENT_TIERING">INTELLIGENT_TIERING</option>
                  </select>
                )}
              </FormField>
              <FormField name="min_age_days" label="Minimum age (days)">
                {(field) => (
                  <Input
                    type="number"
                    min={1}
                    value={String(field.value ?? "")}
                    onChange={(event) => field.onChange(Number(event.target.value))}
                  />
                )}
              </FormField>
              <div className="flex items-center gap-2">
                <Switch
                  id="tier-policy-enabled"
                  checked={Boolean(policyForm.watch("enabled"))}
                  onCheckedChange={(checked) => policyForm.setValue("enabled", checked)}
                />
                <Label htmlFor="tier-policy-enabled">Enabled</Label>
              </div>
            </form>
          </FormProvider>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setPolicyModal(null)}>
              Cancel
            </Button>
            <Button loading={saveTierPolicy.isPending} onClick={() => void policyForm.handleSubmit((v) => saveTierPolicy.mutate(v))()}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
};
