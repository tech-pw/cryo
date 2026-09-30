import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ArrowUpRight, CheckCircle2, Clock3, TriangleAlert } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  getComplianceStatus,
  getEffectivePreset,
  getFastPathFindings,
  getTable,
  getTableAudit,
  getTableHealthTrends,
  getTableSignals,
  type ComplianceStatusRead,
  type FastPathFindingRead,
  type TableSignalRead
} from "../../../api/client";
import type { AuditEntry, EffectivePresetResponse } from "../../../api/types";
import { getTableMaintenanceStatus, type BlockingReasonRead, type MaintenanceStatusRead } from "../../../api/maintenanceStatus";
import { QueryErrorAlert } from "../../common";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/skeleton";
import { useAuth } from "../../../lib/auth";
import { formatDuration, formatBytesGb, formatNumber, formatTime } from "../../../lib/format";
import { useWriteControl } from "../../../lib/useWriteControl";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import {
  diagnosticSignals,
  formatDeductionLine,
  formatScoreSum,
  gradeLetter,
  healthTrendPoints,
  integerScore,
  isTableScanned,
  readDeductions,
  receiptRunHref,
  scoreTrendAnswer,
  sortDeductions,
  topBySeverity,
  type TrendAnswer
} from "./gradeReasons";
import { FileProfileCard } from "./FileProfileCard";
import { OverviewCard } from "./OverviewCard";
import { ScoreHistoryCard } from "./ScoreHistoryCard";
import { TableHealthGauge } from "./TableHealthGauge";
import "./overview.css";

const NO_WORKFLOW = "No workflow assigned. Cryo won't maintain this table.";

type ReceiptRow = AuditEntry & {
  job_id?: string | null;
  run_id?: string | null;
};

export const OverviewPanel = ({
  onScan,
  onPreview,
  onEdit
}: {
  onScan?: () => void;
  onPreview?: () => void;
  onEdit?: () => void;
}) => {
  const { tableId = "" } = useParams();
  const { api, token } = useAuth();
  const scanAccess = useWriteControl("triggerScan");
  const enabled = tableId.length > 0;

  const table = useQuery({
    queryKey: tableKeys.detail(tableId, token),
    queryFn: () => getTable(api, tableId),
    enabled
  });
  const scanned = isTableScanned(table.data?.stats);

  const signals = useQuery({
    queryKey: tableKeys.signals(tableId, token),
    queryFn: () => getTableSignals(api, tableId),
    enabled: enabled && scanned
  });
  const audit = useQuery({
    queryKey: tableKeys.audit(tableId, token, 1),
    queryFn: () => getTableAudit(api, tableId, { limit: 1 }),
    enabled: enabled && scanned
  });
  const trends = useQuery({
    queryKey: tableKeys.healthTrends(tableId, token, 30),
    queryFn: () => getTableHealthTrends(api, tableId, 30),
    enabled: enabled && scanned
  });
  const fastPath = useQuery({
    queryKey: tableKeys.fastPath(tableId, token),
    queryFn: () => getFastPathFindings(api, tableId),
    enabled: enabled && scanned
  });
  const compliance = useQuery({
    queryKey: tableKeys.compliance(tableId, token),
    queryFn: () => getComplianceStatus(api, tableId),
    enabled: enabled && scanned
  });
  const preset = useQuery({
    queryKey: tableKeys.effectivePreset(tableId, token),
    queryFn: () => getEffectivePreset(api, tableId),
    enabled: enabled && scanned
  });
  const maintenance = useQuery({
    queryKey: tableKeys.maintenanceStatus(tableId, token),
    queryFn: () => getTableMaintenanceStatus(api, tableId),
    enabled: enabled && scanned
  });

  if (!enabled) return <p className="text-sm text-muted-foreground">Missing table</p>;
  if (table.isError && !table.data) {
    return <QueryErrorAlert error={table.error} title="Couldn't load this table" />;
  }
  if (!table.data) return <p className="text-sm text-muted-foreground">Loading</p>;

  if (!scanned) {
    return (
      <section aria-labelledby="overview-grade" className="space-y-2">
        <h2 id="overview-grade" className="text-sm font-semibold text-foreground">
          Why this grade
        </h2>
        <p className="text-sm text-foreground">Needs a first scan</p>
        {scanAccess.hidden ? null : (
          <Button
            type="button"
            size="sm"
            variant="primary"
            disabled={!scanAccess.allowed}
            title={scanAccess.reason ?? undefined}
            onClick={() => onScan?.()}
          >
            Scan now
          </Button>
        )}
        {scanAccess.reason ? <p className="text-xs text-muted-foreground">{scanAccess.reason}</p> : null}
      </section>
    );
  }

  const stats = table.data.stats;
  const score = integerScore(stats?.overall_score);
  const letter = gradeLetter(stats?.grade, score);
  const deductions = readDeductions(stats?.raw ?? null);
  const sorted = deductions ? sortDeductions(deductions) : null;
  const diagnostics = diagnosticSignals(signals.data?.signals ?? [], sorted ?? []);
  const receipt = (audit.data?.[0] ?? null) as ReceiptRow | null;
  const points = healthTrendPoints(trends.data?.trend_points ?? []);
  const answer = trendAnswerText(scoreTrendAnswer(points));
  const finding = relevantFinding(tableId, fastPath.data ?? [], signals.data?.signals ?? []);

  return (
    <div className="td-overview-grid">
      <OverviewCard id="overview-grade" title="Why this grade" description="Persisted health score and the checks that changed it." className="td-overview-health">
        <div className="td-health-diagnosis">
          <TableHealthGauge score={score} grade={letter} />
          <div className="td-health-reasons">
            {sorted ? <>
              {sorted.length ? <ul>{sorted.map((item, index) => (
                <li key={`${item.label}-${index}`}>
                  <p>{formatDeductionLine(item)}</p>
                  <div className="td-deduction-track" aria-hidden="true"><span style={{ width: `${Math.min(100, Math.max(0, item.deduction))}%` }} /></div>
                </li>
              ))}</ul> : <p>No score deductions recorded.</p>}
              <p className="td-overview-caption td-score-calculation">{formatScoreSum(sorted, score)}</p>
            </> : <p className="td-overview-caption">Deductions were not recorded.</p>}
          </div>
        </div>
        {diagnostics.length > 0 ? <details className="td-overview-disclosure">
          <summary>Diagnostic, not part of the score <span>· {diagnostics.length}</span></summary>
          <ul>{diagnostics.map((signal) => <li key={signal.signal_name}>{signal.signal_name}: {signal.reason}</li>)}</ul>
        </details> : null}
      </OverviewCard>

      <OverviewCard id="overview-next" title="What Cryo will do next" description="Workflow assignment and current selection eligibility." className="td-overview-next">
        <QueryErrorAlert error={preset.error} title="Couldn't load the workflow" />
        <QueryErrorAlert error={maintenance.error} title="Couldn't load maintenance status" />
        {preset.isError || maintenance.isError ? <p className="td-overview-caption">Refresh this page to check readiness again.</p> : preset.isPending || maintenance.isPending ? <Skeleton className="h-32 w-full" /> : workflowMissing(preset.data) ? (
          <div className="td-next-unassigned"><span className="td-overview-state">Setup needed</span><p>{NO_WORKFLOW}</p><p className="td-overview-caption">Choose a workflow to define the engine, operations and guardrails.</p></div>
        ) : maintenance.data ? <NextMaintenance status={maintenance.data} /> : <p className="td-overview-caption">Maintenance status not recorded.</p>}
        <Link to={`/tables/${tableId}?tab=maintenance`} className="td-overview-link td-overview-bottom-link">Review maintenance <ArrowUpRight size={14} aria-hidden="true" /></Link>
      </OverviewCard>

      <ScoreHistoryCard points={points} answer={answer} loading={trends.isPending} error={trends.error} />

      <OverviewCard id="overview-receipt" title="Last recorded action" description="What ran and the outcome it actually reported." className="td-overview-receipt">
        <QueryErrorAlert error={audit.error} title="Couldn't load the last receipt" />
        {audit.isPending ? <Skeleton className="h-44 w-full" /> : audit.isError ? null : receipt ? <Receipt row={receipt} /> : <div className="td-overview-empty"><p>No receipt recorded.</p><p>Maintenance results will appear here after an action is recorded.</p></div>}
      </OverviewCard>

      <FileProfileCard stats={stats} tableId={tableId} />

      <OverviewCard id="overview-finding" title="Most relevant finding" description="The highest-priority recorded finding for this table." className="td-overview-finding">
        <QueryErrorAlert error={fastPath.error} title="Couldn't load findings" />
        <QueryErrorAlert error={signals.error} title="Couldn't load signals" />
        {fastPath.isPending || signals.isPending ? <Skeleton className="h-24 w-full" /> : finding ? <div className="td-finding-content">
          <span className="td-overview-state"><TriangleAlert size={14} aria-hidden="true" /> Review finding</span>
          <h3>{finding.title.replaceAll("_", " ")}</h3><p>{finding.detail}</p>
          <div className="td-finding-actions">
            {onPreview && preset.data?.preset_id ? <Button type="button" variant="secondary" size="sm" onClick={onPreview}>Preview</Button> : null}
            <Link to={finding.href} className="td-overview-link">See the rest <ArrowUpRight size={14} aria-hidden="true" /></Link>
          </div>
        </div> : fastPath.isError || signals.isError ? <p className="td-overview-caption">Some finding sources are unavailable.</p> : <div className="td-overview-empty"><CheckCircle2 size={24} aria-hidden="true" /><p>No finding recorded.</p><p>New scanner findings and health signals appear here.</p></div>}
      </OverviewCard>

      <OverviewCard id="overview-ownership" title="Ownership & compliance" className="td-overview-ownership">
        <QueryErrorAlert error={compliance.error} title="Couldn't load compliance" />
        <div className="td-overview-ownership-body"><p>{compliance.isPending ? "Loading" : ownershipLine(table.data.owner_email, compliance.data, Boolean(compliance.error))}</p>
          {onEdit ? <Button type="button" variant="secondary" size="sm" onClick={onEdit}>Edit</Button> : null}
        </div>
      </OverviewCard>
    </div>
  );
};

function NextMaintenance({ status }: { status: MaintenanceStatusRead }) {
  const next = nextOperation(status);
  const gate = (status.blocking_reasons ?? []).find((reason) => reason.blocks_nightly_selection) ?? null;
  return (
    <div className="td-next-maintenance">
      <span className="td-overview-state">{status.eligible ? <CheckCircle2 size={15} aria-hidden="true" /> : <Clock3 size={15} aria-hidden="true" />}{status.eligible ? "Eligible for selection" : "Waiting on a gate"}</span>
      <p>{gate ? formatBlockingGate(gate) : status.eligible ? "Eligible for nightly selection" : "Not eligible for nightly selection"}</p>
      {next ? <p className="td-overview-caption">Next operation: {next.replaceAll("_", " ")}</p> : null}
      <dl><div><dt>Next nightly</dt><dd>{status.next_nightly_at ? formatTime(status.next_nightly_at) : "Schedule not recorded"}</dd></div><div><dt>Evaluated</dt><dd>{formatTime(status.evaluated_at)}</dd></div></dl>
      <p className="td-overview-caption">Eligibility allows selection; admission still depends on capacity and guardrails.</p>
    </div>
  );
}

function Receipt({ row }: { row: ReceiptRow }) {
  const href = receiptRunHref(row);
  const bytesReported = typeof row.actual_bytes_freed === "number" && Number.isFinite(row.actual_bytes_freed);
  return (
    <div className="td-receipt-body">
      <div className="td-receipt-heading"><strong>{row.action?.trim().replaceAll("_", " ") || "Last operation"}</strong><span className="td-overview-state">{row.status || "Status not recorded"}</span></div>
      <p className="td-overview-caption">{formatTime(row.ts ?? row.created_at)} · {row.engine?.trim() || "Engine not recorded"}</p>
      <div className="td-receipt-change"><div><span>Input files</span><strong>{finiteNumber(row.input_files_count) == null ? "Not reported" : formatNumber(row.input_files_count)}</strong></div><ArrowRight size={20} aria-hidden="true" /><div><span>Output files</span><strong>{finiteNumber(row.output_files_count) == null ? "Not reported" : formatNumber(row.output_files_count)}</strong></div></div>
      <dl className="td-receipt-metrics"><div><dt>Bytes reclaimed</dt><dd>{bytesReported ? formatBytesGb(row.actual_bytes_freed) : <span>not reported</span>}</dd></div><div><dt>Duration</dt><dd>{row.duration_ms == null || !Number.isFinite(row.duration_ms) ? "Not reported" : formatDuration(row.duration_ms)}</dd></div></dl>
      {href ? (
        <Link to={href} className="td-overview-link">
          Open run <ArrowUpRight size={14} aria-hidden="true" />
        </Link>
      ) : null}
    </div>
  );
}

function relevantFinding(
  tableId: string,
  findings: FastPathFindingRead[],
  signals: TableSignalRead[]
): { title: string; detail: string; href: string } | null {
  const mine = findings.filter((item) => !item.table_id || item.table_id === tableId);
  const fast = topBySeverity(mine);
  if (fast) {
    const href =
      fast.finding_type === "missing_target_file_size"
        ? `/tables/${tableId}?tab=files#files-properties`
        : `/tables/${tableId}?tab=quality`;
    return { title: fast.finding_type, detail: fast.reason, href };
  }
  const signal = topBySeverity(signals.filter((item) => item.triggered));
  if (!signal) return null;
  return {
    title: signal.signal_name,
    detail: signal.reason,
    href: `/tables/${tableId}?tab=quality`
  };
}

function ownershipLine(owner: string | null | undefined, compliance: ComplianceStatusRead | undefined, failed: boolean): string {
  const ownerText = owner?.trim() ? owner.trim() : "No owner";
  const abac = !compliance || failed ? "ABAC policies not recorded" : abacText(compliance.abac_policy_count);
  return `Owner ${ownerText} · ${abac} · Deletion delay ${deletionDelay(compliance, failed)}`;
}

function abacText(count: number): string {
  return count === 1 ? "1 ABAC policy" : `${count} ABAC policies`;
}

function deletionDelay(compliance: ComplianceStatusRead | undefined, failed: boolean): string {
  if (failed || !compliance) return "not recorded";
  const pending = compliance.pending_deletion;
  if (!pending) return "none";
  if (pending.blocked_until) {
    const until = formatTime(pending.blocked_until);
    return until === "-" ? "pending" : until;
  }
  return pending.reason?.trim() || "pending";
}

function workflowMissing(preset: EffectivePresetResponse | undefined): boolean {
  if (!preset) return false;
  return preset.provenance === "none" || !preset.preset_id;
}

function nextOperation(status: MaintenanceStatusRead): string | null {
  const extra = status as MaintenanceStatusRead & { next_operation?: unknown; next_action?: unknown };
  const value = extra.next_operation ?? extra.next_action;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function formatBlockingGate(reason: BlockingReasonRead): string {
  const until = reason.until ? formatTime(reason.until) : null;
  const cooldown = reason.code === "cooldown" || reason.summary.toLowerCase().startsWith("cooldown");
  if (cooldown && until && until !== "-") return `Cooldown until ${until}`;
  if (until && until !== "-") return `${reason.summary} until ${until}`;
  return reason.summary;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function trendAnswerText(answer: TrendAnswer): string {
  if (answer.kind === "none") return "No measured scores in the last 30 days.";
  if (answer.kind === "single") return "One measured score in the last 30 days.";
  const since = formatTime(answer.since);
  if (answer.kind === "unchanged") return `Score unchanged since ${since}.`;
  return `Score ${answer.kind} ${answer.points} points since ${since}.`;
}
