import { useQuery } from "@tanstack/react-query";
import { Clock3, GitCompareArrows } from "lucide-react";
import { useState, type ReactNode } from "react";
import { getDataQualityProfile, getSlaStatusDashboard } from "../../../api/client";
import { TableAiInsights } from "../../TableAiInsights";
import { useAuth } from "../../../lib/auth";
import { formatSlaHours, formatTime } from "../../../lib/format";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { Badge } from "../../ui/badge";
import { Card, CardContent, CardHeader } from "../../ui/card";
import { QualityConstraints } from "../QualityConstraints";
import "./quality.css";

const measured = (value: number | null | undefined): value is number =>
  value != null && Number.isFinite(value) && value >= 0;
const measuredHours = (value: number | null | undefined): string =>
  measured(value) ? formatSlaHours(value) : "Not measured";

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

  const slaRow = sla.data?.slas.find((row) => row.table_id === tableId);
  const freshness = slaRow?.freshness_hours;
  const target = slaRow?.freshness_sla_hours;
  const freshnessKnown = measured(freshness) && measured(target);
  const freshnessRatio = freshnessKnown && target > 0 ? freshness / target : null;
  const drift = profile.data;
  const signals = drift?.commit_signals;

  return (
    <div className="td-tab-content td-quality">
      <div className="td-quality-primary">
        <QualityCard id="quality-freshness" title="Freshness vs target" icon={<Clock3 aria-hidden="true" />} description="How old the newest data is, against the table's target.">
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
              <p className="td-quality-caption">
                {signals?.last_data_commit_at
                  ? `Last data commit ${formatTime(signals.last_data_commit_at)}.`
                  : "Age is measured from the latest snapshot commit."}
              </p>
            </>
          ) : null}
        </QualityCard>

        <QualityCard id="quality-drift" title="What changed?" icon={<GitCompareArrows aria-hidden="true" />} description="Schema, arrival and row-count changes in the last 30 days.">
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
              <p className="td-quality-caption">Profiled {formatTime(drift.profiled_at)}.</p>
            </>
          ) : null}
        </QualityCard>
      </div>

      <section id="quality-constraints" aria-label="Quality rules">
        <QualityConstraints />
      </section>

      <details id="quality-experimental" className="td-quality-experimental" onToggle={(event) => setExperimentalOpen(event.currentTarget.open)}>
        <summary>Experimental insights</summary>
        {experimentalOpen ? <div><TableAiInsights tableId={tableId} embedded visibleTabs={["pii", "similar", "risk", "anomalies", "autocomp"]} /></div> : null}
      </details>
    </div>
  );
};
