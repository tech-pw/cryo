import { useQuery } from "@tanstack/react-query";
import { Clock3, GitCompareArrows, ListChecks } from "lucide-react";
import { useState, type ReactNode } from "react";
import { getDataQualityProfile, getSlaStatusDashboard, getTableChecklist, type ChecklistItemRead } from "../../../api/client";
import { checklistCoverage } from "../../ChecklistMatrix";
import { TableAiInsights } from "../../TableAiInsights";
import { useAuth } from "../../../lib/auth";
import { formatSlaHours, formatTime } from "../../../lib/format";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { Badge, type BadgeProps } from "../../ui/badge";
import { Card, CardContent, CardHeader } from "../../ui/card";
import { Collapsible } from "../../ui/collapsible";
import { QualityConstraints } from "../QualityConstraints";
import "./quality.css";

const measured = (value: number | null | undefined): value is number =>
  value != null && Number.isFinite(value) && value >= 0;
const measuredHours = (value: number | null | undefined): string =>
  measured(value) ? formatSlaHours(value) : "Not measured";

const verdict: Record<ChecklistItemRead["status"], { label: string; variant: BadgeProps["variant"] }> = {
  green: { label: "Passing", variant: "success" },
  yellow: { label: "Warning", variant: "warning" },
  red: { label: "Needs attention", variant: "danger" },
  unknown: { label: "Unknown", variant: "secondary" }
};
const verdictOrder = { red: 0, yellow: 1, unknown: 2, green: 3 };

const QualityCard = ({ id, title, icon, description, children }: {
  id: string; title: string; icon: ReactNode; description: string; children: ReactNode;
}) => (
  <section id={id} aria-labelledby={`${id}-heading`}>
    <Card className="td-quality-card">
      <CardHeader>
        <h2 id={`${id}-heading`}>{icon}{title}</h2>
        <p className="td-quality-caption">{description}</p>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  </section>
);

export const QualityTabPanel = ({ tableId }: { tableId: string }) => {
  const [experimentalOpen, setExperimentalOpen] = useState(false);
  const { api, token } = useAuth();
  const checklist = useQuery({
    queryKey: ["table-checklist", token, tableId],
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
  const slaRow = sla.data?.slas.find((row) => row.table_id === tableId);
  const freshness = slaRow?.freshness_hours;
  const target = slaRow?.freshness_sla_hours;
  const freshnessKnown = measured(freshness) && measured(target);
  const freshnessRatio = freshnessKnown && target > 0 ? freshness / target : null;
  const drift = profile.data;
  const signals = drift?.commit_signals;
  const checkItems = [...(checklist.data?.items ?? [])].sort(
    (a, b) => verdictOrder[a.status] - verdictOrder[b.status] || a.tier - b.tier || b.weight_pct - a.weight_pct
  );

  return (
    <div className="td-tab-content td-quality">
      <div className="td-quality-primary">
        <QualityCard id="quality-freshness" title="Freshness vs target" icon={<Clock3 aria-hidden="true" />} description="Is the table meeting its configured freshness expectation?">
          {sla.isLoading ? <p role="status" className="td-quality-caption">Loading freshness…</p> : null}
          {sla.isError ? <QueryErrorAlert error={sla.error} title="Failed to load freshness" /> : null}
          {!sla.isLoading && !sla.isError && !slaRow ? <p className="td-quality-empty">No freshness measurement returned for this table.</p> : null}
          {slaRow ? (
            <>
              <Badge variant={freshnessKnown ? slaRow.freshness_breached ? "warning" : "success" : "secondary"}>
                {freshnessKnown ? slaRow.freshness_breached ? "Past the freshness target" : "Within the freshness target" : "Not measured"}
              </Badge>
              <dl className="td-quality-metrics">
                <div><dt>Reported age</dt><dd>{measuredHours(freshness)}</dd></div>
                <div><dt>Target</dt><dd>{measuredHours(target)}</dd></div>
              </dl>
              {freshnessRatio != null ? (
                <div className="td-quality-freshness-bar" aria-hidden="true">
                  <span className={slaRow.freshness_breached ? "is-overdue" : ""} style={{ width: `${Math.min(100, freshnessRatio * 100)}%` }} />
                </div>
              ) : null}
              <p className="td-quality-caption">Age uses the latest snapshot commit, or the latest scan when the commit time is unavailable.</p>
              <p className="td-quality-caption">Last data-changing commit: {signals?.last_data_commit_at ? formatTime(signals.last_data_commit_at) : "Not measured"}</p>
            </>
          ) : null}
        </QualityCard>

        <QualityCard id="quality-drift" title="What changed?" icon={<GitCompareArrows aria-hidden="true" />} description="Schema and arrival signals from stored metadata · 30-day lookback.">
          {profile.isLoading ? <p role="status" className="td-quality-caption">Loading drift…</p> : null}
          {profile.isError ? <QueryErrorAlert error={profile.error} title="Failed to load drift" /> : null}
          {!profile.isLoading && !profile.isError && !drift ? <p className="td-quality-empty">No quality profile available yet.</p> : null}
          {drift ? (
            <>
              <div className="td-quality-drift-headline">
                <strong>{drift.anomalies_detected.toLocaleString()}</strong>
                <span>recorded {drift.anomalies_detected === 1 ? "anomaly" : "anomalies"}</span>
              </div>
              <dl className="td-quality-signals">
                <div><dt>Schema</dt><dd>
                  {drift.new_columns.length || drift.dropped_columns.length || drift.schema_version_changed
                    ? [drift.new_columns.length ? `Added ${drift.new_columns.join(", ")}` : null,
                       drift.dropped_columns.length ? `Dropped ${drift.dropped_columns.join(", ")}` : null,
                       drift.schema_version_changed ? "Schema version changed" : null].filter(Boolean).join("; ")
                    : "No change recorded"}
                </dd></div>
                <div><dt>Data arrival</dt><dd>{signals?.arrival_overdue == null ? "Not measured" : signals.arrival_overdue ? "Overdue" : "No overdue arrival detected"}</dd></div>
                <div><dt>Row count</dt><dd>{signals == null || (!signals.row_count_anomaly && signals.net_rows_baseline_median == null)
                  ? "Not measured" : signals.row_count_anomaly ? `Unusual ${signals.row_count_anomaly}` : "No anomaly recorded"}</dd></div>
              </dl>
              <p className="td-quality-caption">Profiled {formatTime(drift.profiled_at)}. No recorded anomaly does not guarantee data correctness.</p>
            </>
          ) : null}
        </QualityCard>
      </div>

      <section id="quality-constraints" aria-label="Quality rules">
        <QualityConstraints />
      </section>

      <QualityCard id="quality-checklist" title="Optimization checklist" icon={<ListChecks aria-hidden="true" />} description="Trino, S3 and Iceberg checks explain optimization readiness. This score is separate from data correctness.">
        {checklist.isLoading ? <p role="status" className="td-quality-caption">Loading checklist…</p> : null}
        {checklist.isError ? <QueryErrorAlert error={checklist.error} title="Failed to load checklist" /> : null}
        {coverage && checklist.data ? (
          <>
            <div className="td-quality-check-summary">
              <dl className="td-quality-metrics">
                <div><dt>Checklist score</dt><dd>{checklist.data.summary.weighted_score == null ? "Not measured" : <>{checklist.data.summary.weighted_score.toFixed(0)}<small> / 100</small></>}</dd></div>
              </dl>
              <div>
                <p>{coverage.total === 0 ? "Not measured" : `${coverage.known} checked, ${coverage.unknown} unknown`}</p>
                <div className="td-quality-verdicts">
                  {(["red", "yellow", "unknown", "green"] as const).map((status) => checklist.data!.summary.counts[status] > 0 ? (
                    <Badge key={status} variant={verdict[status].variant}>{checklist.data!.summary.counts[status]} {verdict[status].label.toLowerCase()}</Badge>
                  ) : null)}
                  {coverage.allGreen ? <Badge variant="success">All green</Badge> : null}
                </div>
              </div>
            </div>
            {checkItems.length ? (
              <Collapsible key={tableId} label={`Inspect ${checkItems.length} checks · attention first`} defaultOpen={checkItems.some((item) => item.status === "red" || item.status === "yellow")}>
                <ul className="td-quality-checks">
                  {checkItems.map((item) => (
                    <li key={item.key}>
                      <div><strong>{item.label}</strong><p>{item.reason}</p></div>
                      <div><Badge variant={verdict[item.status].variant}>{verdict[item.status].label}</Badge><span>{item.weight_pct}% of score</span></div>
                    </li>
                  ))}
                </ul>
              </Collapsible>
            ) : <p className="td-quality-caption">No individual check results returned.</p>}
            <p className="td-quality-caption">Checked {formatTime(checklist.data.checked_at)}. Unknown checks need more evidence.</p>
          </>
        ) : null}
      </QualityCard>

      <details id="quality-experimental" className="td-quality-experimental" onToggle={(event) => setExperimentalOpen(event.currentTarget.open)}>
        <summary>Experimental insights</summary>
        {experimentalOpen ? <div><TableAiInsights tableId={tableId} embedded visibleTabs={["pii", "similar", "risk", "anomalies", "autocomp"]} /></div> : null}
      </details>
    </div>
  );
};
