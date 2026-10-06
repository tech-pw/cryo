import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Archive } from "lucide-react";
import { useState } from "react";
import { apiFetch } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { formatCurrency } from "../../lib/format";
import { QueryErrorAlert } from "../common";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataTable } from "../ui/data-table";
import { ChartCard } from "./ChartCard";

const NO_EVIDENCE_COPY = "No evidence — Auto only";
const NOT_ESTIMATED = "Not estimated";
const PAGE_SIZE = 50;

interface ArchiveSummary {
  policy_name: string | null;
  read_evidence: string;
  read_evidence_copy: string | null;
  target_label: string | null;
  projected_savings_usd_month: number | null;
  realized_savings_usd_month: number | null;
  partition_count: number;
  eligible_count: number;
}

interface ArchivePartition {
  partition_key: string;
  partition_value: string | null;
  age_days: number | null;
  suggestion_label: string;
  reason_codes: string[];
  evidence_copy: string | null;
  net_saving_usd_month: number | null;
}

interface ArchivePartitionPage {
  items: ArchivePartition[];
  total: number;
  limit: number;
  offset: number;
}

interface ArchivePlan {
  dry_run: boolean;
  applied: boolean;
  writes: boolean;
  target_label: string | null;
  object_count: number | null;
  byte_count: number | null;
  request_cost_usd: number | null;
  guardrails: { name: string; status: string }[];
  partitions_selected: number;
}

const savingText = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? NOT_ESTIMATED : formatCurrency(value);

const missingEvidence = (row: { reason_codes: string[]; evidence_copy?: string | null }): boolean =>
  row.reason_codes.includes("no_read_evidence") || row.evidence_copy === NO_EVIDENCE_COPY;

const suggestionText = (row: ArchivePartition): string => {
  if (missingEvidence(row)) return "Auto";
  if (/glacier|deep_archive|offline/i.test(row.suggestion_label)) return "Auto";
  return row.suggestion_label;
};

const columns: ColumnDef<ArchivePartition, unknown>[] = [
  {
    id: "partition",
    header: "Partition",
    cell: ({ row }) => row.original.partition_value || row.original.partition_key
  },
  {
    id: "age",
    header: "Age",
    cell: ({ row }) => (row.original.age_days == null ? "—" : `${row.original.age_days} d`)
  },
  {
    id: "suggestion",
    header: "Suggest",
    cell: ({ row }) => suggestionText(row.original)
  },
  {
    id: "reasons",
    header: "Reason codes",
    cell: ({ row }) => (
      <span className="flex flex-wrap items-center gap-1">
        {row.original.reason_codes.map((code) => (
          <Badge key={code} variant="outline">
            {code}
          </Badge>
        ))}
        {missingEvidence(row.original) ? <span>{NO_EVIDENCE_COPY}</span> : null}
      </span>
    )
  },
  {
    id: "saving",
    header: "Saving",
    cell: ({ row }) => savingText(row.original.net_saving_usd_month)
  }
];

export const ArchiveSection = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const [page, setPage] = useState(0);
  const summary = useQuery({
    queryKey: ["archive-summary", tableId, token],
    queryFn: () => apiFetch<ArchiveSummary>(`/tables/${tableId}/tiering`, api),
    enabled: Boolean(tableId),
    retry: false
  });
  const partitions = useQuery({
    queryKey: ["archive-partitions", tableId, token, page],
    queryFn: () =>
      apiFetch<ArchivePartitionPage>(`/tables/${tableId}/tiering/partitions`, api, {
        query: { limit: PAGE_SIZE, offset: page * PAGE_SIZE }
      }),
    enabled: Boolean(tableId),
    retry: false
  });
  const plan = useMutation({
    mutationFn: () =>
      apiFetch<ArchivePlan>(`/tables/${tableId}/tiering/plan`, api, { method: "POST", body: {} })
  });
  const evidenceMissing = summary.data != null && summary.data.read_evidence !== "trusted";
  const policyTarget = evidenceMissing ? "Auto" : summary.data?.target_label || "Auto";
  const rows = partitions.data?.items ?? [];

  return (
    <section id="archive" aria-label="Archive">
      <ChartCard
        title={
          <h2>
            <Archive aria-hidden="true" /> Archive
          </h2>
        }
        actions={
          <Button size="sm" variant="secondary" loading={plan.isPending} onClick={() => plan.mutate()}>
            Preview plan
          </Button>
        }
      >
        <p className="td-usage-section-description">Advisory only. This does not change storage class.</p>
        {summary.isLoading ? <p className="td-usage-loading">Loading archive advice…</p> : null}
        <QueryErrorAlert error={summary.error} title="Failed to load archive advice" />
        {summary.data ? (
          <dl className="td-usage-metrics">
            <div>
              <dt>Policy</dt>
              <dd>{summary.data.policy_name ?? "No policy"} · mode: advisory · {policyTarget}</dd>
            </div>
            <div>
              <dt>Projected saving</dt>
              <dd>{savingText(summary.data.projected_savings_usd_month)}</dd>
            </div>
            <div>
              <dt>Realized, last 30 d</dt>
              <dd>{savingText(summary.data.realized_savings_usd_month)}</dd>
            </div>
            <div>
              <dt>Bytes by tier</dt>
              <dd>{NOT_ESTIMATED}</dd>
            </div>
          </dl>
        ) : null}
        {evidenceMissing ? <p>{NO_EVIDENCE_COPY}</p> : null}
        <QueryErrorAlert error={partitions.error} title="Failed to load archive partitions" />
        {partitions.isError ? null : (
        <DataTable
          columns={columns}
          data={rows}
          density="compact"
          loading={partitions.isLoading}
          showDensityToggle={false}
          getRowId={(row) => row.partition_key}
          emptyTitle="No partitions in the latest scan"
          emptyDescription="Run a scan so partition ages are recorded."
          pagination={
            partitions.data && partitions.data.total > PAGE_SIZE
              ? {
                  pageIndex: page,
                  pageSize: PAGE_SIZE,
                  totalRows: partitions.data.total,
                  onPageChange: setPage
                }
              : undefined
          }
        />
        )}
        {plan.data ? (
          <div className="td-usage-caption">
            <Badge variant="outline">Dry run</Badge>
            <p>
              {plan.data.partitions_selected} selected · objects{" "}
              {plan.data.object_count == null ? NOT_ESTIMATED : plan.data.object_count} · request cost{" "}
              {savingText(plan.data.request_cost_usd)}
            </p>
            <ul>
              {plan.data.guardrails.map((item) => (
                <li key={item.name}>
                  {item.name}: {item.status}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {plan.isError ? <QueryErrorAlert error={plan.error} title="Failed to preview archive plan" /> : null}
      </ChartCard>
    </section>
  );
};
