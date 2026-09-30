import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
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
import { useAuth } from "../../../lib/auth";
import { formatGb, formatTime } from "../../../lib/format";
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

export const STORED_ROWS_PREVIEW = 10;

const StoredRows = ({ items }: { items: AutoCompCandidateItem[] }) => {
  const [expanded, setExpanded] = useState(false);
  if (items.length === 0) return <p className="text-sm text-muted-foreground">No scored partitions recorded.</p>;
  const ranked = [...items].sort((a, b) => (b.benefit_score ?? 0) - (a.benefit_score ?? 0));
  const shown = expanded ? ranked : ranked.slice(0, STORED_ROWS_PREVIEW);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs text-muted-foreground">
            <th className="py-1 pr-3 font-medium">Partition</th>
            <th className="py-1 pr-3 font-medium">Benefit score</th>
            <th className="py-1 pr-3 font-medium">MOOP score</th>
            <th className="py-1 pr-3 font-medium">Small files</th>
            <th className="py-1 pr-3 font-medium">Size</th>
            <th className="py-1 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => (
            <tr key={`${row.partition_value}-${row.benefit_score}`} className="border-t border-border">
              <td className="py-1 pr-3">{row.partition_value || "not reported"}</td>
              <td className="py-1 pr-3">{numberText(row.benefit_score)}</td>
              <td className="py-1 pr-3">{numberText(row.moop_score)}</td>
              <td className="py-1 pr-3">{numberText(row.small_files)}</td>
              <td className="py-1 pr-3">{sizeText(row.size_gb)}</td>
              <td className="py-1">{row.eligible ? "Eligible" : row.skip_reason || "skipped"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {ranked.length > STORED_ROWS_PREVIEW ? (
        <Button type="button" variant="ghost" size="sm" className="mt-1" onClick={() => setExpanded((value) => !value)}>
          {expanded ? `Show top ${STORED_ROWS_PREVIEW}` : `Show all ${ranked.length.toLocaleString()} partitions`}
        </Button>
      ) : null}
    </div>
  );
};

const HorizonRows = ({ items }: { items: CompactionCandidateRead[] }) => {
  if (items.length === 0) return <p className="text-sm text-muted-foreground">No horizon candidates recorded.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs text-muted-foreground">
            <th className="py-1 pr-3 font-medium">Partition</th>
            <th className="py-1 pr-3 font-medium">Score</th>
            <th className="py-1 pr-3 font-medium">Priority</th>
            <th className="py-1 pr-3 font-medium">Small files</th>
            <th className="py-1 pr-3 font-medium">Size</th>
            <th className="py-1 font-medium">Savings</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={row.partition_path} className="border-t border-border">
              <td className="py-1 pr-3">{row.partition_path || "not reported"}</td>
              <td className="py-1 pr-3">{numberText(row.score)}</td>
              <td className="py-1 pr-3">{row.priority || "not reported"}</td>
              <td className="py-1 pr-3">{numberText(row.small_files)}</td>
              <td className="py-1 pr-3">{sizeText(row.partition_size_gb)}</td>
              <td className="py-1">
                {row.savings_estimate_usd == null ? "not reported" : `$${row.savings_estimate_usd.toFixed(0)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

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

  return (
    <section id="maintenance-compaction" className="mb-6" aria-labelledby="maintenance-compaction-heading">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 id="maintenance-compaction-heading" className="text-sm font-semibold text-foreground">
          Compaction analysis
        </h3>
        <Button
          type="button"
          size="sm"
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
      <p className="mb-3 text-sm text-muted-foreground">{AUTOCOMP_DEFINITION}</p>
      <QueryErrorAlert error={stored.error} title="Couldn't load stored benefit scores" />
      <QueryErrorAlert error={horizon.error} title="Couldn't load horizon candidates" />
      <QueryErrorAlert error={signals.error} title="Couldn't load table signals" />
      <QueryErrorAlert error={table.error} title="Couldn't load table stats" />

      <div className="mb-4 space-y-2">
        <h4 className="text-sm font-medium text-foreground">Stored benefit-score candidates</h4>
        <p className="text-xs text-muted-foreground">{STORED_SCORE_BASIS}</p>
        {stored.isLoading ? <p className="text-sm text-muted-foreground">Loading stored scores</p> : null}
        {!stored.isLoading ? <StoredRows items={stored.data?.items ?? []} /> : null}
        {skipped.length > 0 ? (
          <ul className="text-xs text-muted-foreground">
            {skipped.slice(0, 5).map((row) => (
              <li key={`${row.partition_value}-${row.skip_reason}`}>
                Skipped {row.partition_value || "not reported"}: {row.skip_reason}
              </li>
            ))}
            {skipped.length > 5 ? <li>…and {(skipped.length - 5).toLocaleString()} more skipped</li> : null}
          </ul>
        ) : null}
      </div>

      <div className="mb-4 space-y-2">
        <h4 className="text-sm font-medium text-foreground">Horizon query-frequency candidates</h4>
        <p className="text-xs text-muted-foreground">{HORIZON_SCORE_BASIS}</p>
        {horizon.isLoading ? <p className="text-sm text-muted-foreground">Loading horizon ranking</p> : null}
        {!horizon.isLoading ? <HorizonRows items={horizon.data?.candidates ?? []} /> : null}
      </div>

      <div className="mb-4">
        <h4 className="mb-1 text-sm font-medium text-foreground">Effectiveness</h4>
        {effectiveness ? (
          <p className="text-sm text-muted-foreground">
            {effectiveness.effectiveness || "not reported"}
            {effectiveness.effectiveness_ratio == null
              ? ""
              : ` · ratio ${effectiveness.effectiveness_ratio.toFixed(1)}`}
            {effectiveness.recommendation ? ` · ${effectiveness.recommendation}` : ""}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">Effectiveness not reported</p>
        )}
      </div>

      <div className="mb-4">
        <h4 className="mb-1 text-sm font-medium text-foreground">Delete-file health</h4>
        <Badge variant="secondary">{table.isLoading ? "Loading" : deleteFileHealthLabel(table.data?.stats)}</Badge>
      </div>

      <div>
        <h4 className="mb-1 text-sm font-medium text-foreground">Maintenance trigger signals</h4>
        {(signals.data?.signals.length ?? 0) === 0 && !signals.isLoading ? (
          <p className="text-sm text-muted-foreground">No signals recorded.</p>
        ) : null}
        {(signals.data?.signals.length ?? 0) > 0 ? (
          <ul className="space-y-1 text-sm">
            {signals.data?.signals.map((signal) => (
              <li key={signal.signal_name}>
                {signal.signal_name}: {signal.triggered ? "Triggered" : "OK"} · {signal.severity} · {signal.reason}
              </li>
            ))}
          </ul>
        ) : null}
        {signals.data?.checked_at ? (
          <p className="mt-1 text-xs text-muted-foreground">Checked {formatTime(signals.data.checked_at)}</p>
        ) : null}
      </div>
    </section>
  );
};
