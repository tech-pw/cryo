import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { toast } from "sonner";
import {
  getAutoCompCandidates,
  getTable,
  getTableSignals,
  updateTableBenefitScores,
  type AutoCompCandidateItem,
  type TableSignalRead
} from "../api/client";
import type { UUID } from "../api/types";
import { EmptyState, QueryErrorAlert } from "./common";
import { useAuth } from "../lib/auth";
import { formatGb, formatNumber, formatTime } from "../lib/format";
import { Alert } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent } from "./ui/card";
import { DataTable } from "./ui/data-table";

interface Props {
  tableId: UUID;
  embedded?: boolean;
}

const DEFAULT_MINOR_FILE_THRESHOLD = 50;
const DEFAULT_MAJOR_PCT_THRESHOLD = 10.0;

function deletePolicyChip(
  formatVersion: number | null | undefined,
  positionDeleteFiles: number | null | undefined,
  deleteFilePct: number | null | undefined
): { label: string; variant: "secondary" | "danger" | "warning" | "success"; title?: string } {
  if (formatVersion != null && formatVersion >= 3) {
    return { label: "V3 (deletion vectors)", variant: "secondary" };
  }
  const pct = deleteFilePct ?? 0;
  const files = positionDeleteFiles ?? 0;
  if (pct >= DEFAULT_MAJOR_PCT_THRESHOLD) {
    return {
      label: "Major rewrite recommended",
      variant: "danger",
      title: `delete_file_pct ${pct.toFixed(1)}% >= ${DEFAULT_MAJOR_PCT_THRESHOLD}%`
    };
  }
  if (files >= DEFAULT_MINOR_FILE_THRESHOLD) {
    return {
      label: "Minor delete compaction recommended",
      variant: "warning",
      title: `${files} position-delete files >= ${DEFAULT_MINOR_FILE_THRESHOLD}`
    };
  }
  return { label: "Healthy", variant: "success" };
}

const candidateColumns: ColumnDef<AutoCompCandidateItem, unknown>[] = [
  {
    accessorKey: "partition_value",
    header: "Partition",
    cell: ({ getValue }) => (getValue() as string | null | undefined) || "(table)"
  },
  {
    accessorKey: "benefit_score",
    header: "Benefit score",
    cell: ({ getValue }) => {
      const value = getValue() as number | undefined;
      return <span className="block text-right">{value != null ? value.toFixed(3) : "—"}</span>;
    }
  },
  {
    accessorKey: "moop_score",
    header: "MOOP score",
    cell: ({ getValue }) => {
      const value = getValue() as number | null | undefined;
      return <span className="block text-right">{value != null ? value.toFixed(3) : "—"}</span>;
    }
  },
  {
    accessorKey: "small_files",
    header: "Small files",
    cell: ({ getValue }) => <span className="block text-right">{formatNumber(getValue() as number | undefined)}</span>
  },
  {
    accessorKey: "size_gb",
    header: "Size",
    cell: ({ getValue }) => <span className="block text-right">{formatGb(getValue() as number | undefined)}</span>
  },
  {
    accessorKey: "eligible",
    header: "Status",
    cell: ({ row }) =>
      row.original.eligible ? (
        <Badge variant="success">Eligible</Badge>
      ) : (
        <Badge variant="secondary" title={row.original.skip_reason ?? undefined}>
          {row.original.skip_reason ?? "skipped"}
        </Badge>
      )
  }
];

const signalColumns: ColumnDef<TableSignalRead, unknown>[] = [
  { accessorKey: "signal_name", header: "Signal", size: 160 },
  {
    accessorKey: "triggered",
    header: "Status",
    size: 100,
    cell: ({ getValue }) => (
      <Badge variant={(getValue() as boolean) ? "warning" : "secondary"}>
        {(getValue() as boolean) ? "Triggered" : "OK"}
      </Badge>
    )
  },
  {
    accessorKey: "severity",
    header: "Severity",
    size: 100,
    cell: ({ getValue }) => {
      const value = getValue() as string;
      const variant = value === "critical" ? "danger" : value === "warning" ? "warning" : "secondary";
      return <Badge variant={variant}>{value}</Badge>;
    }
  },
  { accessorKey: "reason", header: "Reason", cell: ({ getValue }) => <span className="truncate">{String(getValue())}</span> }
];

export const TableCompactionAnalytics = ({ tableId, embedded = false }: Props) => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const canUpdate = role === "admin" || role === "operator";

  const candidates = useQuery({
    queryKey: ["table-autocomp-candidates", tableId],
    queryFn: () => getAutoCompCandidates(api, tableId),
    retry: false
  });
  const table = useQuery({
    queryKey: ["table-compaction-stats", tableId],
    queryFn: () => getTable(api, tableId),
    retry: false
  });
  const signals = useQuery({
    queryKey: ["table-signals", tableId],
    queryFn: () => getTableSignals(api, tableId),
    retry: false
  });

  const refreshScores = useMutation({
    mutationFn: () => updateTableBenefitScores(api, tableId),
    onSuccess: () => {
      toast.success("Benefit scores updated");
      void queryClient.invalidateQueries({ queryKey: ["table-autocomp-candidates", tableId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const effectiveness = candidates.data?.effectiveness as
    | { effectiveness_ratio?: number; effectiveness?: string; run_count?: number; recommendation?: string }
    | null
    | undefined;
  const compare = candidates.data?.compare;
  const stats = table.data?.stats;
  const deletePolicy = deletePolicyChip(
    (stats?.raw as { format_version?: number } | null | undefined)?.format_version,
    stats?.position_delete_files,
    stats?.delete_file_pct
  );

  const content = (
    <div className="grid gap-3 lg:grid-cols-3">
      <div className="space-y-2 lg:col-span-2">
        <h5 className="text-sm font-semibold">Compaction candidates (AutoComp §A2)</h5>
        <QueryErrorAlert error={candidates.error} title="Failed to load AutoComp candidates" />
        {compare?.autocomp_wins ? (
          <Alert
            variant="info"
            title={`AutoComp ranking beats static small-files ordering by ${compare.improvement_ratio.toFixed(2)}×`}
          />
        ) : null}
        <DataTable
          columns={candidateColumns}
          data={(candidates.data?.items ?? []).slice(0, 5)}
          getRowId={(row) => `${row.partition_value}-${row.benefit_score}`}
          loading={candidates.isLoading}
          density="compact"
          emptyTitle="No scored partitions"
          emptyDescription="Run a scan and refresh benefit scores to rank compaction targets."
        />
        {candidates.data ? (
          <p className="text-xs text-muted-foreground">
            {candidates.data.items.length.toLocaleString()} partitions scored,{" "}
            {candidates.data.skipped.length.toLocaleString()} skipped
          </p>
        ) : null}
      </div>
      <div className="space-y-4">
        <div>
          <h5 className="text-sm font-semibold">Compaction effectiveness</h5>
          {effectiveness ? (
            <div className="mt-2 space-y-2">
              <div className="flex flex-wrap gap-1.5">
                <Badge variant={(effectiveness.effectiveness_ratio ?? 0) >= 10 ? "success" : "warning"}>
                  Ratio {(effectiveness.effectiveness_ratio ?? 0).toFixed(1)}×
                </Badge>
                <Badge variant="secondary">{effectiveness.effectiveness}</Badge>
                <Badge variant="secondary">{effectiveness.run_count ?? 0} runs</Badge>
              </div>
              <p className="text-xs text-muted-foreground">{effectiveness.recommendation}</p>
            </div>
          ) : candidates.isLoading ? (
            <p className="text-xs text-muted-foreground">Loading…</p>
          ) : (
            <EmptyState title="No compaction history" description="Effectiveness appears after optimize runs." />
          )}
        </div>
        <div>
          <h5 className="text-sm font-semibold">Delete-file health</h5>
          <Badge variant={deletePolicy.variant} title={deletePolicy.title} className="mt-2">
            {deletePolicy.label}
          </Badge>
        </div>
      </div>
      <div className="space-y-2 lg:col-span-3">
        <h5 className="text-sm font-semibold">Maintenance trigger signals</h5>
        <QueryErrorAlert error={signals.error} title="Failed to load table signals" />
        {signals.data?.any_triggered ? (
          <Alert variant="warning" title="One or more maintenance signals are active" />
        ) : null}
        <DataTable
          columns={signalColumns}
          data={signals.data?.signals ?? []}
          getRowId={(row) => row.signal_name}
          loading={signals.isLoading}
          density="compact"
          emptyTitle="No signals evaluated"
          emptyDescription="Signal checks run on demand."
        />
        {signals.data ? (
          <p className="text-xs text-muted-foreground">Checked {formatTime(signals.data.checked_at)}</p>
        ) : null}
      </div>
    </div>
  );

  if (embedded) {
    return (
      <div className="td-embedded-content">
        {canUpdate ? (
          <div className="td-embedded-toolbar">
            <Button size="sm" loading={refreshScores.isPending} onClick={() => refreshScores.mutate()}>
              Refresh benefit scores
            </Button>
          </div>
        ) : null}
        {content}
      </div>
    );
  }

  return (
    <Card className="td-chart-card">
      <div className="td-chart-card-header">
        <span className="td-chart-card-title">Compaction analytics & signals</span>
        {canUpdate ? (
          <div className="td-chart-card-actions">
            <Button size="sm" loading={refreshScores.isPending} onClick={() => refreshScores.mutate()}>
              Refresh benefit scores
            </Button>
          </div>
        ) : null}
      </div>
      <CardContent className="td-chart-card-content">{content}</CardContent>
    </Card>
  );
};
