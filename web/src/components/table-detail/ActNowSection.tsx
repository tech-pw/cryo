/**
 * "Act Now" — the deduplicated, severity-sorted list of real problems on this table.
 *
 * Replaces two things that used to render the *same* triggered-signals data twice on one
 * screen: the top RecommendedActions strip and the "Detected Health Issues & Remediation"
 * cards further down. This component builds one merged list instead, from three genuinely
 * distinct sources (triggered diagnostic signals, positive-savings cost operations, and the
 * top benefit-scored compaction candidate) and renders each issue exactly once.
 *
 * "Act Now" collapses to a single shared banner while a run is in flight, not per-card —
 * there is only one `run.mutate()` for the whole table today (no per-issue remediation
 * endpoint exists), so per-card collapse would misrepresent what actually happened.
 */
import {
  AlertTriangle,
  Calendar,
  CheckCircle2,
  Eraser,
  Flame,
  History,
  LayoutGrid,
  Loader2,
  Radar,
  XCircle,
  Zap
} from "lucide-react";
import { useState, type ReactNode } from "react";
import type { BenefitScoreResponse, TableSignalRead, TableSignalsResponse } from "../../api/client";
import type { TableCostRead } from "../../api/types";
import { formatBytesGb, formatCurrency, formatNumber, formatTime } from "../../lib/format";
import { operationLabels } from "../../lib/theme";
import { ConfirmAction } from "../confirm";
import { QueryErrorAlert } from "../common";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { ChartCard } from "./ChartCard";
import { getSignalMatrix, getSignalPointerDetail } from "./helpers";

type Severity = "critical" | "warning" | "info";

interface HealthIssue {
  id: string;
  severity: Severity;
  icon: ReactNode;
  title: string;
  impactText: string;
  current?: string;
  ideal?: string;
  why?: string;
  recommendation?: string;
}

const operationIcon = (operation: string): ReactNode => {
  if (operation === "optimize") return <Flame className="size-3.5" />;
  if (operation === "remove_orphan_files") return <Eraser className="size-3.5" />;
  if (operation === "expire_snapshots") return <History className="size-3.5" />;
  if (operation === "optimize_manifests") return <LayoutGrid className="size-3.5" />;
  return <Radar className="size-3.5" />;
};

const severityIcon = (severity: Severity): ReactNode => {
  if (severity === "critical") return <XCircle className="size-3.5" style={{ color: "var(--error, #dc3545)" }} />;
  if (severity === "warning") return <AlertTriangle className="size-3.5" style={{ color: "var(--warning, #f0a020)" }} />;
  return <Radar className="size-3.5" style={{ color: "var(--muted, #5c667a)" }} />;
};

const severityBadgeVariant = (severity: Severity): "danger" | "warning" | "secondary" =>
  severity === "critical" ? "danger" : severity === "warning" ? "warning" : "secondary";

const severityRank: Record<Severity, number> = { critical: 0, warning: 1, info: 2 };

const minScheduleLocal = (): string => {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
};

function buildIssues(params: {
  signals?: TableSignalsResponse;
  costView?: TableCostRead;
  benefitScores?: BenefitScoreResponse;
  stats: { total_files?: number; small_files_count?: number } | null | undefined;
  statsRaw: Record<string, unknown>;
  reclaimableBytes: number;
  savingsUsd: number;
}): HealthIssue[] {
  const { signals, costView, benefitScores, stats, statsRaw, reclaimableBytes, savingsUsd } = params;
  const issues: HealthIssue[] = [];

  const totalFiles = stats?.total_files ?? 0;
  const smallFiles = stats?.small_files_count ?? 0;
  const smallPct = totalFiles > 0 ? (smallFiles / totalFiles) * 100 : 0;

  const triggered: TableSignalRead[] = (signals?.signals ?? []).filter(
    (s) => s.triggered && s.severity !== "info"
  );
  for (const signal of triggered) {
    const details = getSignalPointerDetail(signal.signal_name, signal.severity);
    const matrix = getSignalMatrix(signal.signal_name, stats, statsRaw, reclaimableBytes, savingsUsd, smallPct);
    const severity: Severity = signal.severity === "critical" ? "critical" : "warning";
    issues.push({
      id: `signal-${signal.signal_name}`,
      severity,
      icon: severityIcon(severity),
      title: details.title,
      impactText: details.impactText,
      current: matrix.current,
      ideal: matrix.ideal,
      why: matrix.why,
      recommendation: details.recommendation
    });
  }

  for (const op of costView?.by_operation ?? []) {
    if (op.savings_usd_month <= 0) continue;
    issues.push({
      id: `op-${op.operation}`,
      severity: op.savings_usd_month >= 100 ? "warning" : "info",
      icon: operationIcon(op.operation),
      title: operationLabels[op.operation] ?? op.operation,
      impactText: `Save ${formatCurrency(op.savings_usd_month)}/mo`,
      current: `${formatBytesGb(op.reclaimable_bytes)} reclaimable`,
      recommendation: `Run maintenance to reclaim ${formatBytesGb(op.reclaimable_bytes)} and save ${formatCurrency(op.savings_usd_month)}/month.`
    });
  }

  const topPartition = benefitScores?.top_candidates?.[0];
  if (topPartition?.benefit_score && topPartition.benefit_score > 0.05) {
    issues.push({
      id: "hot-partition",
      severity: "info",
      icon: <Flame className="size-3.5" />,
      title: "Hot partition needs compaction",
      impactText: `${formatNumber(topPartition.small_files)} small files`,
      current: `${topPartition.partition_value ?? "(table)"} — benefit score ${topPartition.benefit_score.toFixed(3)}`,
      recommendation: "This specific partition is the fastest win — compact it first."
    });
  }

  return issues.sort((a, b) => severityRank[a.severity] - severityRank[b.severity]);
}

interface ActNowSectionProps {
  signals?: TableSignalsResponse;
  signalsLoading: boolean;
  signalsError: unknown;
  costView?: TableCostRead;
  benefitScores?: BenefitScoreResponse;
  stats: { total_files?: number; small_files_count?: number } | null | undefined;
  statsRaw: Record<string, unknown>;
  reclaimableBytes: number;
  savingsUsd: number;
  presetOptions: { label: string; value: string }[];
  presetsLoading: boolean;
  effectivePresetId?: string;
  onAssignPreset: (presetId: string) => void;
  assignPending: boolean;
  onRun: () => void;
  runPending: boolean;
  queuedMaintenanceRunId?: string | null;
  lastRunSkipNotes?: string[];
  onOpenRun: () => void;
  onPreview: () => void;
  onSchedule: (scheduledAtIso: string) => void;
  schedulePending: boolean;
  tableLabel: string;
}

export const ActNowSection = ({
  signals,
  signalsLoading,
  signalsError,
  costView,
  benefitScores,
  stats,
  statsRaw,
  reclaimableBytes,
  savingsUsd,
  presetOptions,
  presetsLoading,
  effectivePresetId,
  onAssignPreset,
  assignPending,
  onRun,
  runPending,
  queuedMaintenanceRunId,
  lastRunSkipNotes,
  onOpenRun,
  onPreview,
  onSchedule,
  schedulePending,
  tableLabel
}: ActNowSectionProps) => {
  const [acting, setActing] = useState(false);
  const [scheduledFor, setScheduledFor] = useState<string | null>(null);
  const [pickerOpenFor, setPickerOpenFor] = useState<string | null>(null);
  const [pickedTime, setPickedTime] = useState<string>("");

  const handleRun = () => {
    setActing(true);
    onRun();
  };

  const handleSchedule = () => {
    if (!pickedTime) return;
    const iso = new Date(pickedTime).toISOString();
    setScheduledFor(iso);
    setPickerOpenFor(null);
    setPickedTime("");
    onSchedule(iso);
  };

  if (signalsError) {
    return <QueryErrorAlert error={signalsError} title="Failed to load table signals" />;
  }

  if (signalsLoading) {
    return (
      <ChartCard>
        <span className="text-xs text-muted-foreground">Checking diagnostic signals…</span>
      </ChartCard>
    );
  }

  const inFlight = acting || runPending || Boolean(queuedMaintenanceRunId);
  if (inFlight) {
    return (
      <ChartCard className="td-act-now-banner">
        <div className="flex items-center gap-2.5">
          <Loader2 className="size-3.5 animate-spin" style={{ color: "var(--primary, #3d6fd4)" }} />
          <span className="text-[13px] font-semibold">
            {runPending ? "Queueing maintenance run…" : "Maintenance run queued for this table"}
          </span>
          {queuedMaintenanceRunId ? (
            <Button size="sm" variant="primary" onClick={onOpenRun} className="ml-4">
              Open run
            </Button>
          ) : null}
        </div>
      </ChartCard>
    );
  }

  if (scheduledFor || schedulePending) {
    return (
      <ChartCard className="td-act-now-banner">
        <div className="flex items-center gap-2.5">
          <Calendar className="size-3.5" style={{ color: "var(--primary, #3d6fd4)" }} />
          <span className="text-[13px] font-semibold">
            {schedulePending
              ? "Scheduling maintenance run…"
              : `Maintenance run scheduled for ${formatTime(scheduledFor ?? "")}`}
          </span>
        </div>
      </ChartCard>
    );
  }

  const issues = buildIssues({ signals, costView, benefitScores, stats, statsRaw, reclaimableBytes, savingsUsd });

  if (!issues.length) {
    return (
      <ChartCard className="td-act-now-healthy">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="size-3.5" style={{ color: "var(--success, #28a745)" }} />
          <span className="text-xs font-semibold text-[var(--success,#28a745)]">No issues detected</span>
          <span className="text-[11px] text-muted-foreground">
            All {signals?.signals.length ?? 0} background diagnostic checks passed.
          </span>
        </div>
      </ChartCard>
    );
  }

  return (
    <div className="td-act-now-section">
      {lastRunSkipNotes && lastRunSkipNotes.length > 0 ? (
        <ChartCard className="td-act-now-skip-banner">
          <div className="flex items-start gap-2">
            <AlertTriangle className="size-3.5" style={{ color: "var(--warning, #f0a020)" }} />
            <span className="text-xs">
              The last run completed but didn&apos;t change this: {lastRunSkipNotes.join(" · ")}
            </span>
          </div>
        </ChartCard>
      ) : null}
      <div className="td-act-now-header">
        <span className="td-chart-card-title">Act now</span>
        <Badge variant="danger" className="m-0">
          {issues.length}
        </Badge>
        <div className="flex-1" />
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">Engine preset:</span>
          <select
            className="flex h-7 w-40 rounded-[var(--radius-default)] border border-border bg-background px-2 text-xs"
            value={effectivePresetId ?? ""}
            disabled={assignPending || presetsLoading}
            onChange={(e) => onAssignPreset(e.target.value)}
          >
            <option value="">Assign preset</option>
            {presetOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="td-act-now-list">
        {issues.map((issue) => (
          <div key={issue.id} className={`td-act-now-card td-act-now-${issue.severity}`}>
            <div className="td-act-now-card-head">
              <div className="flex items-center gap-2">
                {issue.icon}
                <span className="text-[13px] font-semibold">{issue.title}</span>
              </div>
              <Badge variant={severityBadgeVariant(issue.severity)} className="m-0">
                {issue.impactText}
              </Badge>
            </div>
            {issue.why ? <span className="td-act-now-why text-muted-foreground">{issue.why}</span> : null}
            {issue.current && issue.ideal && issue.current !== issue.ideal ? (
              <div className="td-act-now-transition">
                <Badge variant="secondary" className="m-0">
                  {issue.current}
                </Badge>
                <span className="td-act-now-arrow">&rarr;</span>
                <Badge variant="default" className="m-0">
                  {issue.ideal}
                </Badge>
              </div>
            ) : issue.current ? (
              <Badge variant="secondary" className="m-0 self-start">
                {issue.current}
              </Badge>
            ) : null}
            {issue.recommendation ? (
              <span className="td-act-now-recommendation">
                <Zap className="mr-1.5 size-3.5" style={{ color: "var(--primary, #3d6fd4)" }} />
                {issue.recommendation}
              </span>
            ) : null}
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" onClick={onPreview}>
                Preview
              </Button>
              <ConfirmAction
                title="Run maintenance now?"
                description={`This queues maintenance for ${tableLabel} using the assigned engine preset.`}
                onConfirm={handleRun}
              >
                <Button size="sm" variant="primary" disabled={!effectivePresetId}>
                  Act now
                </Button>
              </ConfirmAction>
              <Popover
                open={pickerOpenFor === issue.id}
                onOpenChange={(open) => {
                  setPickerOpenFor(open ? issue.id : null);
                  if (!open) setPickedTime("");
                }}
              >
                <PopoverTrigger asChild>
                  <Button size="sm" variant="secondary" disabled={!effectivePresetId}>
                    <Calendar className="size-3.5" />
                    Schedule…
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-80">
                  <p className="mb-2 text-sm font-medium">Schedule for a specific time</p>
                  <div className="flex flex-col gap-2">
                    <input
                      type="datetime-local"
                      className="flex h-8 w-full rounded-[var(--radius-default)] border border-border bg-background px-2 text-xs"
                      value={pickedTime}
                      min={minScheduleLocal()}
                      onChange={(e) => setPickedTime(e.target.value)}
                    />
                    <span className="block text-[11px] text-muted-foreground">
                      Runs through the same engine preset and Governor checks as Act now, just
                      deferred until this time.
                    </span>
                    <ConfirmAction
                      title="Schedule this run?"
                      description={
                        pickedTime
                          ? `Queues maintenance for ${tableLabel} at ${new Date(pickedTime).toLocaleString()}, using the assigned engine preset.`
                          : "Pick a time first."
                      }
                      onConfirm={handleSchedule}
                    >
                      <Button size="sm" variant="primary" disabled={!pickedTime || !effectivePresetId} className="w-full">
                        Schedule
                      </Button>
                    </ConfirmAction>
                  </div>
                </PopoverContent>
              </Popover>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
