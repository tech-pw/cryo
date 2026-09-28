import { useQuery } from "@tanstack/react-query";
import { getDataQualityProfile, getSlaStatusDashboard, getTableChecklist } from "../../../api/client";
import { checklistCoverage, TableChecklistPanel } from "../../ChecklistMatrix";
import { TableAiInsights } from "../../TableAiInsights";
import { useAuth } from "../../../lib/auth";
import { formatSlaHours } from "../../../lib/format";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { Badge } from "../../ui/badge";
import { ChartCard } from "../ChartCard";
import { QualityConstraints } from "../QualityConstraints";

const measuredHours = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? "Not measured" : formatSlaHours(value);

export const QualityTabPanel = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const checklist = useQuery({
    queryKey: ["table-checklist", tableId],
    queryFn: () => getTableChecklist(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const sla = useQuery({
    queryKey: tableKeys.sla(tableId, token),
    queryFn: () => getSlaStatusDashboard(api, false, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const profile = useQuery({
    queryKey: tableKeys.dataQuality(tableId, token),
    queryFn: () => getDataQualityProfile(api, tableId, 30),
    enabled: Boolean(tableId),
    retry: false
  });

  const coverage = checklist.data ? checklistCoverage(checklist.data.summary.counts) : null;
  const slaRow = sla.data?.slas.find((row) => row.table_id === tableId) ?? sla.data?.slas[0];
  const freshnessKnown = slaRow?.freshness_hours != null && slaRow.freshness_sla_hours != null;
  const drift = profile.data;
  const signals = drift?.commit_signals;

  return (
    <div className="td-tab-content flex flex-col gap-4">
      <section id="quality-checklist" aria-label="Checklist">
        <ChartCard title="Checklist">
          {checklist.isLoading ? <p className="text-sm text-muted-foreground">Loading checklist…</p> : null}
          {checklist.isError ? (
            <QueryErrorAlert error={checklist.error} title="Failed to load checklist" />
          ) : null}
          {coverage ? (
            <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
              <span>
                Checklist score{" "}
                {checklist.data?.summary.weighted_score == null
                  ? "Not measured"
                  : checklist.data.summary.weighted_score.toFixed(0)}
              </span>
              <span>{coverage.total === 0 ? "Not measured" : `${coverage.known} checked, ${coverage.unknown} unknown`}</span>
              {coverage.allGreen ? <Badge variant="success">All green</Badge> : null}
            </div>
          ) : null}
          <TableChecklistPanel tableId={tableId} />
        </ChartCard>
      </section>

      <section id="quality-constraints" aria-label="Constraints">
        <QualityConstraints />
      </section>

      <section id="quality-freshness" aria-label="Freshness vs target">
        <ChartCard title="Freshness vs target">
          {sla.isLoading ? <p className="text-sm text-muted-foreground">Loading freshness…</p> : null}
          {sla.isError ? (
            <QueryErrorAlert error={sla.error} title="Failed to load freshness" />
          ) : null}
          {!sla.isLoading && !sla.isError && !slaRow ? <p className="text-sm">Not measured</p> : null}
          {slaRow ? (
            <dl className="grid gap-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt>Freshness</dt>
                <dd>{measuredHours(slaRow.freshness_hours)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>Target</dt>
                <dd>{measuredHours(slaRow.freshness_sla_hours)}</dd>
              </div>
              <div>
                <dd className="text-xs text-muted-foreground">
                  {freshnessKnown
                    ? slaRow.freshness_breached
                      ? "Past the freshness target"
                      : "Within the freshness target"
                    : "Not measured"}
                </dd>
              </div>
            </dl>
          ) : null}
        </ChartCard>
      </section>

      <section id="quality-drift" aria-label="Drift and alerts">
        <ChartCard title="Drift and alerts">
          {profile.isLoading ? <p className="text-sm text-muted-foreground">Loading drift…</p> : null}
          {profile.isError ? (
            <QueryErrorAlert error={profile.error} title="Failed to load drift" />
          ) : null}
          {!profile.isLoading && !profile.isError && !drift ? <p className="text-sm">Not measured</p> : null}
          {drift ? (
            <ul className="m-0 list-none space-y-2 p-0 text-sm">
              <li>Anomalies detected: {drift.anomalies_detected}</li>
              <li>
                Schema drift:{" "}
                {drift.new_columns.length || drift.dropped_columns.length || drift.schema_version_changed
                  ? [
                      drift.new_columns.length ? `added ${drift.new_columns.join(", ")}` : null,
                      drift.dropped_columns.length ? `dropped ${drift.dropped_columns.join(", ")}` : null,
                      drift.schema_version_changed ? "schema version changed" : null
                    ]
                      .filter(Boolean)
                      .join("; ")
                  : "None recorded"}
              </li>
              <li>
                Data arrival:{" "}
                {signals?.arrival_overdue == null
                  ? "Not measured"
                  : signals.arrival_overdue
                    ? "Overdue"
                    : "On time"}
              </li>
              <li>
                Row count:{" "}
                {signals == null
                  ? "Not measured"
                  : signals.row_count_anomaly
                    ? `Unusual ${signals.row_count_anomaly}`
                    : signals.net_rows_baseline_median == null
                      ? "Not measured"
                      : "No anomaly recorded"}
              </li>
            </ul>
          ) : null}
        </ChartCard>
      </section>

      <details id="quality-experimental" className="rounded-[var(--radius-default)] border border-border p-3">
        <summary className="cursor-pointer text-sm font-medium">Experimental</summary>
        <div className="mt-3">
          <TableAiInsights tableId={tableId} embedded visibleTabs={["pii", "similar", "risk", "anomalies", "autocomp"]} />
        </div>
      </details>
    </div>
  );
};
