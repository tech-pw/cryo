import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../ui/data-table";
import "./maintenance-details.css";
import { toast } from "sonner";
import {
  getAutoCompCandidates,
  getCompactionCandidates,
  getTable,
  getTableSignals,
  updateTableBenefitScores,
  type AutoCompCandidateItem
} from "../../../api/client";
import type { CompactionCandidateRead, TableStatsRead } from "../../../api/types";
import { QueryErrorAlert } from "../../common";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Segmented } from "../../ui/segmented";
import { useAuth } from "../../../lib/auth";
import { formatCurrency, formatGb, formatTime } from "../../../lib/format";
import { useWriteControl } from "../../../lib/useWriteControl";
import { tableKeys } from "../../../pages/table-detail/tableKeys";

export const AUTOCOMP_DEFINITION = "AutoComp: Cryo's benefit-scored compaction selection";

export const STORED_SCORE_BASIS =
  "Basis: stored compaction benefit score on the latest scan, including partitions below the minimum score.";

export const HORIZON_SCORE_BASIS =
  "Basis: recomputed benefit score from table query frequency over the horizon, only partitions with small files. Not the same population as the stored-score list.";

const autocompKey = (tableId: string, token?: string | null) =>
  [...tableKeys.benefitScores(tableId, token), "autocomp"] as const;

export const deleteFileHealthLabel = (stats?: TableStatsRead | null): string => {
  if (!stats) return "not reported";
  const version = (stats.raw as { format_version?: unknown } | null | undefined)?.format_version;
  if (typeof version === "number" && version >= 3) return "V3 (deletion vectors)";
  const pct = stats.delete_file_pct;
  const files = stats.position_delete_files;
  if (pct == null && files == null) return "not reported";
  if (typeof pct === "number" && pct >= 10) return "Major rewrite recommended";
  if (typeof files === "number" && files >= 50) return "Minor delete compaction recommended";
  if (typeof pct === "number" || typeof files === "number") return "Healthy";
  return "not reported";
};

const sizeText = (value: number | null | undefined): string => {
  if (value == null || Number.isNaN(value)) return "not reported";
  return formatGb(value);
};

const numberText = (value: number | null | undefined): string => {
  if (value == null || Number.isNaN(value)) return "not reported";
  return String(value);
};

const countText = (value: number | null | undefined): string => {
  if (value == null || Number.isNaN(value)) return "not reported";
  return value.toLocaleString();
};

export const STORED_ROWS_PREVIEW = 10;

const storedColumns: ColumnDef<AutoCompCandidateItem, unknown>[] = [
  { accessorKey: "partition_value", header: "Partition", cell: ({ row }) => <span className="td-compaction-partition" title={row.original.partition_value}>{row.original.partition_value || "not reported"}</span> },
  { accessorKey: "benefit_score", header: "Benefit score", cell: ({ row }) => numberText(row.original.benefit_score) },
  { accessorKey: "moop_score", header: "MOOP score", cell: ({ row }) => numberText(row.original.moop_score) },
  { accessorKey: "small_files", header: "Small files", cell: ({ row }) => countText(row.original.small_files) },
  { accessorKey: "size_gb", header: "Size", cell: ({ row }) => sizeText(row.original.size_gb) },
  { accessorKey: "eligible", header: "Selection status", cell: ({ row }) => <Badge variant={row.original.eligible ? "success" : "secondary"}>{row.original.eligible ? "Eligible" : row.original.skip_reason || "Skipped"}</Badge> }
];
const horizonColumns: ColumnDef<CompactionCandidateRead, unknown>[] = [
  { accessorKey: "partition_path", header: "Partition", cell: ({ row }) => <span className="td-compaction-partition" title={row.original.partition_path}>{row.original.partition_path || "not reported"}</span> },
  { accessorKey: "score", header: "Score", cell: ({ row }) => numberText(row.original.score) },
  { accessorKey: "priority", header: "Priority", cell: ({ row }) => row.original.priority || "not reported" },
  { accessorKey: "small_files", header: "Small files", cell: ({ row }) => countText(row.original.small_files) },
  { accessorKey: "partition_size_gb", header: "Size", cell: ({ row }) => sizeText(row.original.partition_size_gb) },
  { accessorKey: "savings_estimate_usd", header: "Modelled savings", cell: ({ row }) => row.original.savings_estimate_usd == null ? "not reported" : formatCurrency(row.original.savings_estimate_usd) }
];
const StoredRows = ({ items }: { items: AutoCompCandidateItem[] }) => {
  const [page, setPage] = useState(0);
  const ranked = [...items].sort((a, b) => (b.benefit_score ?? 0) - (a.benefit_score ?? 0));
  const currentPage = Math.min(page, Math.max(0, Math.ceil(ranked.length / STORED_ROWS_PREVIEW) - 1));
  const columns = items.some((item) => item.moop_score != null)
    ? storedColumns
    : storedColumns.filter((column) => !("accessorKey" in column) || column.accessorKey !== "moop_score");
  return <DataTable columns={columns} data={ranked.slice(currentPage * STORED_ROWS_PREVIEW, (currentPage + 1) * STORED_ROWS_PREVIEW)}
    emptyTitle="No scored partitions recorded." emptyDescription="The latest scan has not recorded compaction benefit scores."
    pagination={ranked.length > STORED_ROWS_PREVIEW ? { pageIndex: currentPage, pageSize: STORED_ROWS_PREVIEW, totalRows: ranked.length, onPageChange: setPage } : undefined}
    showDensityToggle={false} />;
};
const HorizonRows = ({ items }: { items: CompactionCandidateRead[] }) => (
  <DataTable columns={horizonColumns} data={items} emptyTitle="No horizon candidates recorded."
    emptyDescription="No small-file candidates were returned for the 30-day query-frequency model." showDensityToggle={false} />
);

export const CompactionAnalysis = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const refreshAccess = useWriteControl("runMaintenance");
  const enabled = tableId.length > 0;
  const stored = useQuery({
    queryKey: autocompKey(tableId, token),
    queryFn: () => getAutoCompCandidates(api, tableId),
    enabled,
    retry: false
  });
  const horizon = useQuery({
    queryKey: tableKeys.compactionCandidates(tableId, token),
    queryFn: () => getCompactionCandidates(api, tableId, { horizonDays: 30, k: 10 }),
    enabled,
    retry: false
  });
  const signals = useQuery({
    queryKey: tableKeys.signals(tableId, token),
    queryFn: () => getTableSignals(api, tableId),
    enabled,
    retry: false
  });
  const table = useQuery({
    queryKey: tableKeys.detail(tableId, token),
    queryFn: () => getTable(api, tableId),
    enabled,
    retry: false
  });
  const refresh = useMutation({
    mutationFn: () => updateTableBenefitScores(api, tableId),
    onSuccess: () => {
      toast.success("Benefit scores updated");
      void queryClient.invalidateQueries({ queryKey: tableKeys.benefitScores(tableId, token) });
      void queryClient.invalidateQueries({ queryKey: tableKeys.compactionCandidates(tableId, token) });
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const effectiveness = stored.data?.effectiveness as
    | { effectiveness_ratio?: number; effectiveness?: string; recommendation?: string }
    | null
    | undefined;
  const skipped = stored.data?.skipped ?? [];
  const [ranking, setRanking] = useState<"stored" | "horizon">("stored");
  const items = stored.data?.items ?? [];
  const eligibleCount = items.filter((item) => item.eligible).length;
  const topScore = items.reduce<number | null>(
    (best, item) => (item.benefit_score == null ? best : best == null ? item.benefit_score : Math.max(best, item.benefit_score)),
    null
  );
  const allSignals = signals.data?.signals ?? [];
  const triggered = allSignals.filter((signal) => signal.triggered);
  const quiet = allSignals.filter((signal) => !signal.triggered);

  return (
    <section id="maintenance-compaction" className="td-compaction-analysis" aria-labelledby="maintenance-compaction-heading">
      <div className="td-compaction-head">
        <div>
          <h3 id="maintenance-compaction-heading">Compaction analysis</h3>
          <p>{AUTOCOMP_DEFINITION}</p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={!refreshAccess.allowed || refresh.isPending}
          title={refreshAccess.reason ?? undefined}
          loading={refresh.isPending}
          onClick={() => {
            if (!refreshAccess.allowed) return;
            refresh.mutate();
          }}
        >
          Refresh benefit scores
        </Button>
      </div>
      <QueryErrorAlert error={stored.error} title="Couldn't load stored benefit scores" />
      <QueryErrorAlert error={horizon.error} title="Couldn't load horizon candidates" />
      <QueryErrorAlert error={signals.error} title="Couldn't load table signals" />
      <QueryErrorAlert error={table.error} title="Couldn't load table stats" />

      <dl className="td-compaction-stats">
        <div>
          <dt>Eligible partitions</dt>
          <dd>{stored.isSuccess ? `${eligibleCount.toLocaleString()} of ${items.length.toLocaleString()}` : "—"}</dd>
        </div>
        <div>
          <dt>Top benefit score</dt>
          <dd>{topScore == null ? "—" : topScore.toLocaleString(undefined, { maximumFractionDigits: 2 })}</dd>
        </div>
        <div>
          <dt>Last compaction effect</dt>
          <dd title={effectiveness?.recommendation ?? undefined}>
            {effectiveness?.effectiveness || "Not reported"}
            {effectiveness?.effectiveness_ratio == null ? null : <span> · ×{effectiveness.effectiveness_ratio.toFixed(1)}</span>}
          </dd>
        </div>
        <div>
          <dt>Delete files</dt>
          <dd>{table.isLoading ? "Loading" : deleteFileHealthLabel(table.data?.stats)}</dd>
        </div>
        <div>
          <dt>Signals triggered</dt>
          <dd data-alert={triggered.length > 0 || undefined}>
            {signals.isSuccess ? `${triggered.length} of ${allSignals.length}` : "—"}
          </dd>
        </div>
      </dl>
      {effectiveness?.recommendation ? <p className="td-compaction-note">{effectiveness.recommendation}</p> : null}

      <div className="td-compaction-ranking">
        <div className="td-compaction-ranking-bar">
          <Segmented
            size="small"
            aria-label="Ranking"
            value={ranking}
            onChange={setRanking}
            options={[
              { value: "stored", label: "Stored score" },
              { value: "horizon", label: "Query frequency · 30 d" }
            ]}
          />
        </div>
        {ranking === "stored" ? (
          <div className="space-y-2">
            <h4>Stored benefit-score candidates</h4>
            <p className="td-compaction-basis">{STORED_SCORE_BASIS}</p>
            {stored.isLoading ? <p className="td-compaction-basis">Loading stored scores</p> : null}
            {stored.isSuccess ? <StoredRows items={items} /> : null}
            {skipped.length > 0 ? (
              <details className="td-compaction-details">
                <summary>
                  {skipped.length.toLocaleString()} {skipped.length === 1 ? "partition" : "partitions"} skipped
                </summary>
                <ul>
                  {skipped.slice(0, 5).map((row) => (
                    <li key={`${row.partition_value}-${row.skip_reason}`}>
                      <code>{row.partition_value || "not reported"}</code> {row.skip_reason}
                    </li>
                  ))}
                  {skipped.length > 5 ? <li>…and {(skipped.length - 5).toLocaleString()} more</li> : null}
                </ul>
              </details>
            ) : null}
          </div>
        ) : (
          <div id="maintenance-compaction-horizon" className="space-y-2">
            <h4>Horizon query-frequency candidates</h4>
            <p className="td-compaction-basis">{HORIZON_SCORE_BASIS}</p>
            {horizon.isLoading ? <p className="td-compaction-basis">Loading horizon ranking</p> : null}
            {horizon.isSuccess ? <HorizonRows items={horizon.data?.candidates ?? []} /> : null}
          </div>
        )}
      </div>

      <div className="td-compaction-signals-block">
        <h4>Maintenance trigger signals</h4>
        {allSignals.length === 0 && signals.isSuccess ? <p className="td-compaction-basis">No signals recorded.</p> : null}
        {triggered.length > 0 ? (
          <ul className="td-compaction-signals">
            {triggered.map((signal) => (
              <li key={signal.signal_name} data-triggered>
                <div>
                  <strong>{signal.signal_name.replace(/_/g, " ")}</strong>
                  <Badge variant={signal.severity === "critical" ? "danger" : "warning"}>{signal.severity}</Badge>
                </div>
                <p>{signal.reason}</p>
              </li>
            ))}
          </ul>
        ) : null}
        {quiet.length > 0 ? (
          <details className="td-compaction-details" open={triggered.length === 0 || undefined}>
            <summary>{quiet.length} not triggered</summary>
            <ul className="td-compaction-signals">
              {quiet.map((signal) => (
                <li key={signal.signal_name}>
                  <div>
                    <strong>{signal.signal_name.replace(/_/g, " ")}</strong>
                  </div>
                  <p>{signal.reason}</p>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        {signals.data?.checked_at ? <p className="td-compaction-basis">Checked {formatTime(signals.data.checked_at)}</p> : null}
      </div>
    </section>
  );
};
