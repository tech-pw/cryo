import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CheckCircle2, Clock3, TriangleAlert, Wrench } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  getComplianceStatus,
  getEffectivePreset,
  getFastPathFindings,
  getTable,
  getTableAudit,
  getTableCost,
  getTableHealthTrends,
  getTableSignals,
  listPresets,
  type FastPathFindingRead,
  type TableSignalRead
} from "../../../api/client";
import type { AuditEntry, TableStatsRead } from "../../../api/types";
import { getTableMaintenanceStatus, type MaintenanceStatusRead } from "../../../api/maintenanceStatus";
import { QueryErrorAlert } from "../../common";
import { Button } from "../../ui/button";
import { ChartDataTable } from "../../ui/chart-data-table";
import { Skeleton } from "../../ui/skeleton";
import { useAuth } from "../../../lib/auth";
import { formatBytesGb, formatCurrency, formatDuration, formatNumber, formatTime } from "../../../lib/format";
import { useWriteControl } from "../../../lib/useWriteControl";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { MaintenanceActions } from "../operations/MaintenanceActions";
import {
  OPERATION_CATALOG,
  isFailureStatus,
  readDeductionList,
  relativeTime,
  remedyForDeduction,
  type Deduction
} from "../operations/operationCatalog";
import { useOperationLauncher } from "../operations/OperationLauncher";
import { FileLayoutCard } from "./FileLayoutCard";
import { gradeLetter, healthTrendPoints, integerScore, isTableScanned, readDeductions, scoreTrendAnswer, topBySeverity } from "./gradeReasons";
import { OverviewCard } from "./OverviewCard";
import { ScoreTrend } from "./ScoreTrend";
import { TableHealthGauge } from "./TableHealthGauge";
import "./overview.css";

const AUDIT_WINDOW = 40;
const ACTIVITY_ROWS = 6;

export const OverviewPanel = ({ onScan, onPreview, onEdit }: { onScan?: () => void; onPreview?: () => void; onEdit?: () => void }) => {
  const { tableId = "" } = useParams();
  const { api, token } = useAuth();
  const scanAccess = useWriteControl("triggerScan");
  const enabled = tableId.length > 0;

  const table = useQuery({ queryKey: tableKeys.detail(tableId, token), queryFn: () => getTable(api, tableId), enabled });
  const scanned = isTableScanned(table.data?.stats);
  const on = enabled && scanned;

  const audit = useQuery({
    queryKey: tableKeys.audit(tableId, token, AUDIT_WINDOW),
    queryFn: () => getTableAudit(api, tableId, { limit: AUDIT_WINDOW }),
    enabled: on
  });
  const trends = useQuery({ queryKey: tableKeys.healthTrends(tableId, token, 30), queryFn: () => getTableHealthTrends(api, tableId, 30), enabled: on });
  const preset = useQuery({ queryKey: tableKeys.effectivePreset(tableId, token), queryFn: () => getEffectivePreset(api, tableId), enabled: on });
  const presets = useQuery({ queryKey: tableKeys.presets(token), queryFn: () => listPresets(api), enabled: on });
  const maintenance = useQuery({ queryKey: tableKeys.maintenanceStatus(tableId, token), queryFn: () => getTableMaintenanceStatus(api, tableId), enabled: on });
  const cost = useQuery({ queryKey: tableKeys.cost(tableId, token), queryFn: () => getTableCost(api, tableId), enabled: on, retry: false });
  const signals = useQuery({ queryKey: tableKeys.signals(tableId, token), queryFn: () => getTableSignals(api, tableId), enabled: on });
  const fastPath = useQuery({ queryKey: tableKeys.fastPath(tableId, token), queryFn: () => getFastPathFindings(api, tableId), enabled: on });
  const compliance = useQuery({ queryKey: tableKeys.compliance(tableId, token), queryFn: () => getComplianceStatus(api, tableId), enabled: on, retry: false });

  if (!enabled) return <p className="text-sm text-muted-foreground">Missing table</p>;
  if (table.isError && !table.data) return <QueryErrorAlert error={table.error} title="Couldn't load this table" />;
  if (!table.data) return <Skeleton className="h-96 w-full" />;

  if (!scanned) {
    return (
      <section aria-labelledby="overview-grade" className="td-ov-unscanned">
        <h2 id="overview-grade">Needs a first scan</h2>
        <p>Cryo grades a table, sizes its files and recommends maintenance after it reads the table's metadata once.</p>
        {scanAccess.hidden ? null : (
          <Button type="button" size="sm" variant="primary" disabled={!scanAccess.allowed} title={scanAccess.reason ?? undefined} onClick={() => onScan?.()}>
            Scan now
          </Button>
        )}
        {scanAccess.reason ? <p className="td-overview-caption">{scanAccess.reason}</p> : null}
      </section>
    );
  }

  const stats = table.data.stats;
  const score = integerScore(stats?.overall_score);
  const letter = gradeLetter(stats?.grade, score);
  const deductions = readDeductionList(stats?.raw);
  const points = healthTrendPoints(trends.data?.trend_points ?? []);
  const trendText = trendAnswerText(scoreTrendAnswer(points));
  const auditRows = audit.data ?? [];
  const workflowPreset = presets.data?.find((item) => item.id === preset.data?.preset_id);
  const finding = relevantFinding(tableId, fastPath.data ?? [], signals.data?.signals ?? []);

  return (
    <div className="td-ov">
      <HealthHero
        tableId={tableId}
        score={score}
        grade={letter}
        deductions={deductions}
        deductionsRecorded={readDeductions(stats?.raw) != null}
        trendText={trendText}
        status={maintenance.data}
        workflowName={preset.data?.preset_name ?? null}
        hasWorkflow={Boolean(preset.data?.preset_id)}
        statusLoading={maintenance.isPending || preset.isPending}
        statusError={maintenance.isError || preset.isError}
      />

      <MaintenanceActions
        tableId={tableId}
        stats={stats}
        audit={auditRows}
        orphanBytes={cost.data?.orphan_bytes}
        deductions={deductions}
        footer={
          <>
            <span>
              {preset.data?.preset_name ? (
                <>
                  Runs on the <b>{preset.data.preset_name}</b> workflow's engine
                  {workflowPreset?.params && typeof workflowPreset.params.retention_days === "number"
                    ? ` · ${workflowPreset.params.retention_days}-day snapshot retention`
                    : ""}
                </>
              ) : (
                "Assign a workflow to choose the engine and retention settings"
              )}
            </span>
            <span className="td-ops-footer-actions">
              {onPreview && preset.data?.preset_id ? (
                <Button type="button" size="sm" variant="ghost" onClick={onPreview}>
                  Preview full workflow plan
                </Button>
              ) : null}
              <Link className="td-overview-link" to={`/tables/${tableId}?tab=maintenance`}>
                Schedule, history & settings <ArrowUpRight size={14} aria-hidden="true" />
              </Link>
            </span>
          </>
        }
      />

      <div className="td-ov-evidence">
        <div className="td-ov-stack">
          <FileLayoutCard stats={stats} tableId={tableId} />
        <OverviewCard id="overview-score" title="Health over time" description="Last 30 days of recorded scans. Shaded bands are grades." className="td-ov-trend">
          <QueryErrorAlert error={trends.error} title="Couldn't load the score trend" />
          {trends.isPending ? (
            <Skeleton className="h-44 w-full" />
          ) : (
            <>
              <p className="td-trend-answer">{trendText}</p>
              {points.filter((point) => point.value != null).length > 1 ? (
                <ScoreTrend points={points} />
              ) : (
                <p className="td-overview-caption">Another scan is needed before a trend can be drawn.</p>
              )}
              <ChartDataTable
                caption="Recorded table health scores"
                toggleLabel="View score history"
                rows={points}
                getRowKey={(row, index) => `${row.at}-${index}`}
                columns={[
                  { key: "at", header: "Recorded", render: (row) => formatTime(row.at) },
                  { key: "score", header: "Score / 100", align: "right", render: (row) => row.value ?? "Not measured" },
                  { key: "grade", header: "Grade", render: (row) => row.grade ?? "Not graded" }
                ]}
              />
            </>
          )}
        </OverviewCard>
        </div>
        <div className="td-ov-stack">
          <HistoryCard tableId={tableId} stats={stats} audit={auditRows} loading={audit.isPending} error={audit.error} />
        <OverviewCard id="overview-glance" title="Context" description="Who owns it, how much it's read, what it costs." className="td-ov-glance">
          <dl className="td-glance">
            <Glance label="Queries · 30 days" value={knownCount(stats?.query_count_30d)} hint="From query logs; reads outside them aren't counted" />
            <Glance label="Storage · per month" value={money(cost.data?.current_monthly_cost_usd ?? stats?.estimated_monthly_s3_cost_usd)} hint={cost.data?.precision === "storage_truth" ? "Measured from storage" : "Estimated"} />
            <Glance label="S3 requests · per month" value={money(cost.data?.request_cost_usd_month)} hint={cost.data?.get_requests_30d != null ? `${formatNumber(cost.data.get_requests_30d)} GETs in 30 days` : undefined} />
            <Glance label="Rows" value={knownCount(stats?.total_records)} />
            <Glance label="Owner" value={table.data.owner_email?.trim() || "No owner"} hint={compliance.data ? `${compliance.data.abac_policy_count} ABAC ${compliance.data.abac_policy_count === 1 ? "policy" : "policies"}` : undefined} />
          </dl>
          <div className="td-glance-actions">
            {onEdit ? (
              <Button type="button" variant="secondary" size="sm" onClick={onEdit}>
                Edit owner & alerts
              </Button>
            ) : null}
            <Link className="td-overview-link" to={`/tables/${tableId}?tab=cost`}>
              Cost & savings <ArrowUpRight size={14} aria-hidden="true" />
            </Link>
          </div>
          {finding ? (
            <div className="td-glance-finding">
              <TriangleAlert size={14} aria-hidden="true" />
              <div>
                <b>{finding.title.replaceAll("_", " ")}</b>
                <p>{finding.detail}</p>
                <Link className="td-overview-link" to={finding.href}>
                  Review <ArrowUpRight size={14} aria-hidden="true" />
                </Link>
              </div>
            </div>
          ) : null}
        </OverviewCard>
        </div>
      </div>
    </div>
  );
};

const severityOf = (item: Deduction): "critical" | "warning" => (item.severity === "critical" ? "critical" : "warning");

function HealthHero({
  tableId,
  score,
  grade,
  deductions,
  deductionsRecorded,
  trendText,
  status,
  workflowName,
  hasWorkflow,
  statusLoading,
  statusError
}: {
  tableId: string;
  score: number | null;
  grade: string | null;
  deductions: Deduction[];
  deductionsRecorded: boolean;
  trendText: string;
  status: MaintenanceStatusRead | undefined;
  workflowName: string | null;
  hasWorkflow: boolean;
  statusLoading: boolean;
  statusError: boolean;
}) {
  const launcher = useOperationLauncher();
  const lost = deductions.reduce((sum, item) => sum + item.deduction, 0);
  const worst = Math.max(20, ...deductions.map((item) => item.deduction));
  const gate = (status?.blocking_reasons ?? []).find((reason) => reason.blocks_nightly_selection) ?? null;
  return (
    <section className="td-hero" aria-labelledby="overview-grade" data-grade={grade?.toLowerCase()}>
      <div className="td-hero-score">
        <TableHealthGauge score={score} grade={grade} />
        <p className="td-hero-trend">{trendText}</p>
        <p className="td-hero-nightly">
          {statusError ? (
            "Selection status unavailable"
          ) : statusLoading ? (
            "Checking maintenance eligibility…"
          ) : !hasWorkflow ? (
            <>
              <Clock3 size={13} aria-hidden="true" /> No nightly workflow
            </>
          ) : status?.eligible ? (
            <>
              <CheckCircle2 size={13} aria-hidden="true" /> Eligible for selection{workflowName ? ` · ${workflowName}` : ""}
            </>
          ) : (
            <>
              <Clock3 size={13} aria-hidden="true" /> {gate ? gate.summary : "Not currently eligible"}
            </>
          )}
        </p>
      </div>
      <div className="td-hero-body">
        <header>
          <h2 id="overview-grade">{deductions.length ? "What's pulling the score down" : deductionsRecorded ? "No deductions recorded" : "Score explanation unavailable"}</h2>
          {deductions.length ? (
            <p>
              <b>−{lost} points</b> across {deductions.length} check{deductions.length === 1 ? "" : "s"}. Review the suggested actions before running maintenance.
            </p>
          ) : (
            <p>{deductionsRecorded ? "The last scan recorded no health penalties." : "The last scan did not include a health-check breakdown."}</p>
          )}
        </header>
        {deductions.length ? (
          <ol className="td-deductions">
            {deductions.map((item, index) => {
              const remedy = remedyForDeduction(item.label, tableId);
              const severity = severityOf(item);
              return (
                <li key={`${item.label}-${index}`} data-severity={severity}>
                  <span className="td-ded-points">−{item.deduction}</span>
                  <div className="td-ded-text">
                    <p>
                      <b>{item.label}</b>
                      <span className="td-ded-sev">{severity === "critical" ? "Critical" : "Warning"}</span>
                    </p>
                    <p className="td-ded-reason">{item.reason}</p>
                    <div className="td-ded-bar" aria-hidden="true">
                      <span style={{ width: `${(item.deduction / worst) * 100}%` }} />
                    </div>
                  </div>
                  <div className="td-ded-fix">
                    {remedy.kind === "operation" ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        disabled={!launcher || Boolean(launcher.blockedReason)}
                        title={launcher?.blockedReason ?? `Run ${OPERATION_CATALOG[remedy.op].title.toLowerCase()} on this table`}
                        onClick={() => launcher?.launch(remedy.op)}
                      >
                        <Wrench aria-hidden="true" />
                        {OPERATION_CATALOG[remedy.op].title}
                      </Button>
                    ) : remedy.kind === "settings" ? (
                      <Link className="td-overview-link" to={remedy.href}>
                        {remedy.label} <ArrowUpRight size={14} aria-hidden="true" />
                      </Link>
                    ) : (
                      <span className="td-ded-note">{remedy.note}</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        ) : (
          <div className="td-hero-clean">
            {deductionsRecorded ? <CheckCircle2 size={20} aria-hidden="true" /> : <Clock3 size={20} aria-hidden="true" />}
            <p>{deductionsRecorded ? "Review file layout and maintenance history below for the supporting evidence." : "Refresh the table stats to collect the evidence behind this score."}</p>
          </div>
        )}
      </div>
    </section>
  );
}

function HistoryCard({
  tableId,
  stats,
  audit,
  loading,
  error
}: {
  tableId: string;
  stats: TableStatsRead | null | undefined;
  audit: AuditEntry[];
  loading: boolean;
  error: unknown;
}) {
  const raw = (stats?.raw ?? {}) as Record<string, unknown>;
  const perDay = typeof raw.commits_per_day === "number" && Number.isFinite(raw.commits_per_day) ? raw.commits_per_day : null;
  const lastCommit = typeof raw.last_snapshot_committed_at === "string" ? raw.last_snapshot_committed_at : stats?.last_snapshot_committed_at ?? null;
  const rows = [...audit]
    .filter((row) => row.ts ?? row.created_at)
    .sort((a, b) => Date.parse(b.ts ?? b.created_at ?? "") - Date.parse(a.ts ?? a.created_at ?? ""))
    .slice(0, ACTIVITY_ROWS);
  return (
    <OverviewCard id="overview-history" title="History & activity" description="How fast snapshots accumulate and what maintenance last did." className="td-ov-history">
      <dl className="td-history-stats">
        <div>
          <dt>Snapshots</dt>
          <dd>{knownCount(stats?.total_snapshots)}</dd>
        </div>
        <div>
          <dt>Commits / day</dt>
          <dd>{perDay == null ? "—" : perDay >= 10 ? Math.round(perDay) : perDay.toFixed(1)}</dd>
        </div>
        <div>
          <dt>Last commit</dt>
          <dd>{lastCommit ? relativeTime(lastCommit) : "—"}</dd>
        </div>
      </dl>
      <h3 className="td-history-subhead">Recent maintenance</h3>
      <QueryErrorAlert error={error} title="Couldn't load recent maintenance" />
      {loading ? (
        <Skeleton className="h-40 w-full" />
      ) : rows.length ? (
        <ol className="td-timeline">
          {rows.map((row, index) => {
            const at = row.ts ?? row.created_at ?? "";
            const failed = isFailureStatus(row.status ?? "");
            const title = OPERATION_CATALOG[row.action as keyof typeof OPERATION_CATALOG]?.title ?? (row.action ?? "Action").replaceAll("_", " ");
            return (
              <li key={`${row.id ?? index}`} data-state={failed ? "failed" : "ok"}>
                <span className="td-timeline-dot" aria-hidden="true" />
                <div>
                  <p>
                    <b>{title}</b> <span>{failed ? "failed" : (row.status ?? "").toLowerCase() || "recorded"}</span>
                  </p>
                  {outcomeText(row) ? <p className="td-timeline-outcome">{outcomeText(row)}</p> : null}
                  <p className="td-timeline-meta" title={formatTime(at)}>
                    {relativeTime(at)}
                    {row.duration_ms != null && Number.isFinite(row.duration_ms) ? ` · ${formatDuration(row.duration_ms)}` : ""}
                    {row.engine ? ` · ${row.engine}` : ""}
                    {(row.actor ?? "").startsWith("system:") ? " · nightly" : row.actor ? " · manual" : ""}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      ) : !error ? (
        <p className="td-overview-caption">No maintenance has been recorded for this table yet.</p>
      ) : null}
      <Link className="td-overview-link" to={`/tables/${tableId}?tab=snapshots`}>
        Snapshots <ArrowUpRight size={14} aria-hidden="true" />
      </Link>
    </OverviewCard>
  );
}

/** Before → after as the engine reported it; nothing is inferred when a count is missing. */
function outcomeText(row: AuditEntry): string | null {
  const input = finiteCount(row.input_files_count);
  const output = finiteCount(row.output_files_count);
  const freed = finiteCount(row.actual_bytes_freed);
  const parts: string[] = [];
  if (input != null && output != null) parts.push(`${formatNumber(input)} → ${formatNumber(output)} files`);
  if (freed != null && freed > 0) parts.push(`${formatBytesGb(freed)} freed`);
  return parts.length ? parts.join(" · ") : null;
}

function finiteCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

const Glance = ({ label, value, hint }: { label: string; value: string; hint?: string }) => (
  <div>
    <dt>{label}</dt>
    <dd><span title={value}>{value}</span>{hint ? <p>{hint}</p> : null}</dd>
  </div>
);

function knownCount(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? formatNumber(value) : "—";
}

function money(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value > 0 && value < 0.01 ? "< $0.01" : formatCurrency(value);
}

function relevantFinding(
  tableId: string,
  findings: FastPathFindingRead[],
  signals: TableSignalRead[]
): { title: string; detail: string; href: string } | null {
  const fast = topBySeverity(findings.filter((item) => !item.table_id || item.table_id === tableId));
  if (fast) {
    const href = fast.finding_type === "missing_target_file_size" ? `/tables/${tableId}?tab=files#files-properties` : `/tables/${tableId}?tab=quality`;
    return { title: fast.finding_type, detail: fast.reason, href };
  }
  const signal = topBySeverity(signals.filter((item) => item.triggered));
  return signal ? { title: signal.signal_name, detail: signal.reason, href: `/tables/${tableId}?tab=quality` } : null;
}

function trendAnswerText(answer: ReturnType<typeof scoreTrendAnswer>): string {
  if (answer.kind === "none") return "No scores recorded in the last 30 days.";
  if (answer.kind === "single") return "One score recorded in the last 30 days.";
  const since = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(Date.parse(answer.since));
  if (answer.kind === "unchanged") return `Unchanged since ${since}.`;
  return `${answer.kind === "down" ? "Down" : "Up"} ${answer.points} points since ${since}.`;
}
