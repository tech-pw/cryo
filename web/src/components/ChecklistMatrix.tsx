import { useQuery } from "@tanstack/react-query";
import {
  getFleetChecklist,
  getTableChecklist,
  type ChecklistItemRead,
  type FleetChecklistRow
} from "../api/client";
import type { UUID } from "../api/types";
import { QueryErrorAlert } from "./common";
import { Badge } from "./ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { useAuth } from "../lib/auth";
import { formatTime } from "../lib/format";

const STATUS_BADGE: Record<ChecklistItemRead["status"], "success" | "warning" | "danger" | "secondary"> = {
  green: "success",
  yellow: "warning",
  red: "danger",
  unknown: "secondary"
};

const STATUS_DOT: Record<ChecklistItemRead["status"], string> = {
  green: "var(--color-status-success)",
  yellow: "var(--color-status-warning)",
  red: "var(--color-status-danger)",
  unknown: "var(--color-border)"
};

const TIER_LABEL: Record<number, string> = {
  1: "Tier 1 — high impact",
  2: "Tier 2 — secondary",
  3: "Tier 3 — situational"
};

/** Single-table checklist detail: all 12 items, grouped by tier, each with its weight, verdict,
 * and the metric that produced it. Meant to sit alongside (not replace) the existing
 * TableDiagnosisPanel -- that panel answers "is this table healthy", this one answers "which of
 * the 12 optimization levers are actually pulled". */
export const TableChecklistPanel = ({ tableId }: { tableId: UUID }) => {
  const { api } = useAuth();
  const checklist = useQuery({
    queryKey: ["table-checklist", tableId],
    queryFn: () => getTableChecklist(api, tableId),
    retry: false
  });

  if (checklist.isLoading) {
    return (
      <Card>
        <CardContent className="p-4 text-sm text-muted-foreground">Scoring checklist…</CardContent>
      </Card>
    );
  }
  if (checklist.error || !checklist.data) {
    return <QueryErrorAlert error={checklist.error} title="Failed to load optimization checklist" />;
  }

  const byTier = [1, 2, 3].map((tier) => ({
    tier,
    items: checklist.data.items.filter((item) => item.tier === tier)
  }));

  return (
    <Card className="td-chart-card">
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="text-sm font-semibold">Trino / S3 / Iceberg checklist</CardTitle>
        <span className="text-xs text-muted-foreground">
          {checklist.data.summary.weighted_score !== null
            ? `${checklist.data.summary.weighted_score.toFixed(0)}/100 weighted`
            : "Not enough data"}
          {" · Checked "}
          {formatTime(checklist.data.checked_at)}
        </span>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        {byTier.map(({ tier, items }) => (
          <div key={tier}>
            <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {TIER_LABEL[tier]}
            </div>
            <div className="space-y-1">
              {items.map((item) => (
                <div key={item.key} className="flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-sunken)]">
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: STATUS_DOT[item.status] }}
                    aria-hidden
                  />
                  <span className="min-w-[220px] text-sm font-medium">{item.label}</span>
                  <Badge variant={STATUS_BADGE[item.status]} className="shrink-0">
                    {item.status}
                  </Badge>
                  <span className="flex-1 text-xs text-muted-foreground">{item.reason}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{item.weight_pct}%</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
};

/** Fleet-wide green/red matrix -- one row per table, one dot per checklist item, ordered by
 * weight (left = highest impact). Scoped to an explicit table list (e.g. the top-20-by-cost
 * cohort) since scoring the whole fleet on every page load is expensive. */
export const FleetChecklistMatrix = ({ tableIds }: { tableIds: UUID[] }) => {
  const { api } = useAuth();
  const fleet = useQuery({
    queryKey: ["fleet-checklist", tableIds],
    queryFn: () => getFleetChecklist(api, tableIds),
    enabled: tableIds.length > 0,
    retry: false
  });

  if (tableIds.length === 0) {
    return null;
  }
  if (fleet.isLoading) {
    return (
      <Card>
        <CardContent className="p-4 text-sm text-muted-foreground">Scoring {tableIds.length} tables…</CardContent>
      </Card>
    );
  }
  if (fleet.error || !fleet.data) {
    return <QueryErrorAlert error={fleet.error} title="Failed to load fleet checklist" />;
  }

  const sorted = [...fleet.data.rows].sort(
    (a, b) => (a.summary.weighted_score ?? 100) - (b.summary.weighted_score ?? 100)
  );

  return (
    <Card className="td-chart-card">
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="text-sm font-semibold">Fleet checklist — {sorted.length} tables</CardTitle>
        <span className="text-xs text-muted-foreground">Checked {formatTime(fleet.data.checked_at)}</span>
      </CardHeader>
      <CardContent className="overflow-x-auto pt-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th className="pb-2 pr-3 font-medium">Table</th>
              <th className="pb-2 pr-3 font-medium">Score</th>
              <th className="pb-2 font-medium">Red items</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <FleetChecklistRowView key={row.table_id} row={row} />
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
};

const FleetChecklistRowView = ({ row }: { row: FleetChecklistRow }) => {
  return (
    <tr className="border-b border-border/60 last:border-0">
      <td className="py-1.5 pr-3">
        <span className="text-muted-foreground">{row.schema_name}.</span>
        <span className="font-medium">{row.table_name}</span>
      </td>
      <td className="py-1.5 pr-3 tabular-nums">
        {row.summary.weighted_score !== null ? `${row.summary.weighted_score.toFixed(0)}` : "—"}
      </td>
      <td className="py-1.5">
        {row.red_items.length === 0 ? (
          <Badge variant="success">all green</Badge>
        ) : (
          <div className="flex flex-wrap gap-1">
            {row.red_items.map((key) => (
              <Badge key={key} variant="danger">
                {key}
              </Badge>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
};
