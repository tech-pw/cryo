import { useQuery } from "@tanstack/react-query";
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
import { BeforeAfter } from "../../charts/BeforeAfter";
import { UNKNOWN_MARK } from "../../charts/format";
import { TimeSeries } from "../../charts/TimeSeries";
import { QueryErrorAlert } from "../../common";
import { Button } from "../../ui/button";
import { useAuth } from "../../../lib/auth";
import { formatDuration, formatBytesGb, formatTime } from "../../../lib/format";
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
  markersFromAudit,
  readDeductions,
  receiptRunHref,
  scoreTrendAnswer,
  sortDeductions,
  topBySeverity,
  type TrendAnswer
} from "./gradeReasons";

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
    <div className="flex flex-col gap-4">
      <section aria-labelledby="overview-grade" className="space-y-2">
        <h2 id="overview-grade" className="text-sm font-semibold text-foreground">
          Why this grade
        </h2>
        <p className="flex items-baseline gap-2">
          <span className="text-2xl font-semibold text-foreground" aria-label={letter == null ? "Grade not recorded" : `Grade ${letter}`}>
            {letter ?? UNKNOWN_MARK}
          </span>
          <span
            className="text-base font-medium text-foreground [font-variant-numeric:tabular-nums]"
            aria-label={score == null ? "Score not recorded" : `Score ${score}`}
          >
            {score == null ? UNKNOWN_MARK : score}
          </span>
        </p>
        {sorted ? (
          <>
            <ul className="space-y-1 text-sm text-foreground">
              {sorted.map((item, index) => (
                <li key={`${item.label}-${index}`}>{formatDeductionLine(item)}</li>
              ))}
            </ul>
            <p className="text-sm text-muted-foreground [font-variant-numeric:tabular-nums]">{formatScoreSum(sorted, score)}</p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Deductions were not recorded.</p>
        )}
        {diagnostics.length > 0 ? (
          <div className="space-y-1 border-t border-border pt-3">
            <h3 className="text-sm font-semibold text-foreground">Diagnostic, not part of the score</h3>
            <ul className="space-y-1 text-sm text-muted-foreground">
              {diagnostics.map((signal) => (
                <li key={signal.signal_name}>
                  {signal.signal_name}: {signal.reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section aria-labelledby="overview-next" className="space-y-2">
        <h2 id="overview-next" className="text-sm font-semibold text-foreground">
          What Cryo will do next
        </h2>
        <QueryErrorAlert error={preset.error} title="Couldn't load the workflow" className="mb-2" />
        <QueryErrorAlert error={maintenance.error} title="Couldn't load maintenance status" className="mb-2" />
        {preset.isPending ? (
          <p className="text-sm text-muted-foreground">Loading</p>
        ) : workflowMissing(preset.data) ? (
          <p className="text-sm text-foreground">{NO_WORKFLOW}</p>
        ) : maintenance.isPending ? (
          <p className="text-sm text-muted-foreground">Loading</p>
        ) : maintenance.data ? (
          <NextMaintenance status={maintenance.data} />
        ) : (
          <p className="text-sm text-muted-foreground">Maintenance status not recorded.</p>
        )}
        <Link to={`/tables/${tableId}?tab=maintenance`} className="text-sm font-medium text-primary hover:underline">
          Maintenance
        </Link>
      </section>

      <section aria-labelledby="overview-receipt" className="space-y-2">
        <h2 id="overview-receipt" className="text-sm font-semibold text-foreground">
          Last receipt
        </h2>
        <QueryErrorAlert error={audit.error} title="Couldn't load the last receipt" className="mb-2" />
        {audit.isPending ? <p className="text-sm text-muted-foreground">Loading</p> : receipt ? <Receipt row={receipt} /> : <p className="text-sm text-muted-foreground">No receipt recorded.</p>}
      </section>

      <section aria-labelledby="overview-finding" className="space-y-2">
        <h2 id="overview-finding" className="text-sm font-semibold text-foreground">
          Most relevant finding
        </h2>
        <QueryErrorAlert error={fastPath.error} title="Couldn't load findings" className="mb-2" />
        <QueryErrorAlert error={signals.error} title="Couldn't load signals" className="mb-2" />
        {fastPath.isPending || signals.isPending ? (
          <p className="text-sm text-muted-foreground">Loading</p>
        ) : finding ? (
          <div className="space-y-2">
            <p className="text-sm font-medium text-foreground">{finding.title}</p>
            <p className="text-sm text-muted-foreground">{finding.detail}</p>
            <div className="flex flex-wrap items-center gap-3">
              {onPreview ? (
                <Button type="button" size="sm" onClick={onPreview}>
                  Preview
                </Button>
              ) : null}
              <Link to={finding.href} className="text-sm font-medium text-primary hover:underline">
                See the rest
              </Link>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No finding recorded.</p>
        )}
      </section>

      <section aria-labelledby="overview-score" className="space-y-2">
        <h2 id="overview-score" className="text-sm font-semibold text-foreground">
          Score over time
        </h2>
        <QueryErrorAlert error={trends.error} title="Couldn't load the score trend" className="mb-2" />
        <p className="text-sm text-foreground">{answer}</p>
        <TimeSeries
          title="Is the score getting worse?"
          answer={answer}
          unit="score"
          points={points}
          events={markersFromAudit(audit.data ?? [])}
        />
      </section>

      <section aria-labelledby="overview-ownership" className="space-y-2">
        <h2 id="overview-ownership" className="text-sm font-semibold text-foreground">
          Ownership & compliance
        </h2>
        <QueryErrorAlert error={compliance.error} title="Couldn't load compliance" className="mb-2" />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-foreground">
            {compliance.isPending
              ? "Loading"
              : ownershipLine(table.data.owner_email, compliance.data, Boolean(compliance.error))}
          </p>
          <Button type="button" variant="ghost" size="sm" onClick={() => onEdit?.()}>
            Edit
          </Button>
        </div>
      </section>
    </div>
  );
};

function NextMaintenance({ status }: { status: MaintenanceStatusRead }) {
  const next = nextOperation(status);
  const gate = (status.blocking_reasons ?? []).find((reason) => reason.blocks_nightly_selection) ?? null;
  return (
    <div className="space-y-1 text-sm text-foreground">
      {next ? <p>Next operation {next}</p> : null}
      <p>{status.eligible ? "Eligible for nightly selection" : "Not eligible for nightly selection"}</p>
      <p>{gate ? formatBlockingGate(gate) : "No blocking gate."}</p>
      <p>{status.next_nightly_at ? `Next nightly ${formatTime(status.next_nightly_at)}` : "Next nightly not recorded"}</p>
    </div>
  );
}

function Receipt({ row }: { row: ReceiptRow }) {
  const href = receiptRunHref(row);
  const bytesReported = typeof row.actual_bytes_freed === "number" && Number.isFinite(row.actual_bytes_freed);
  return (
    <div className="space-y-2">
      <BeforeAfter
        label={row.action?.trim() || "Last operation"}
        before={finiteNumber(row.input_files_count)}
        after={finiteNumber(row.output_files_count)}
        unit="files"
        bytesUnreported={!bytesReported}
      />
      <p className="text-sm text-foreground">
        Bytes {bytesReported ? formatBytesGb(row.actual_bytes_freed) : <span>not reported</span>}
      </p>
      <p className="text-sm text-muted-foreground">
        Engine {row.engine?.trim() || "not recorded"} · Duration{" "}
        {row.duration_ms == null || !Number.isFinite(row.duration_ms) ? "not recorded" : formatDuration(row.duration_ms)}
      </p>
      {href ? (
        <Link to={href} className="text-sm font-medium text-primary hover:underline">
          Open run
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
