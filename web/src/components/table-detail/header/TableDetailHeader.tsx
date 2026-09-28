import { ChevronDown, Copy, Settings } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import type * as React from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { ConfirmAction } from "../../confirm";
import { GradePill } from "../../status";
import { Badge } from "../../ui/badge";
import { Breadcrumb } from "../../ui/breadcrumb";
import { Button } from "../../ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../ui/tooltip";
import { cn } from "../../../lib/cn";
import { formatGb, formatNumber } from "../../../lib/format";
import { formatPartitionColumnLabel } from "../helpers";

const UNKNOWN = "—";
const ASSIGN_WORKFLOW_REASON = "Assign a workflow before running maintenance";
const IST_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

const crumbClass =
  "rounded-sm underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]";

export interface TableDetailHeaderProps {
  tableId: string;
  tableName: string;
  qualifiedName?: string | null;
  sourceName: string;
  catalogName?: string | null;
  schemaName: string;
  scanned: boolean;
  score: number | null;
  grade: string | null;
  ownerEmail: string | null;
  partitionLabel: string;
  location: string | null;
  scanAt: string | null;
  lastCommitAt: string | null;
  sizeGb: number | null;
  totalFiles: number | null;
  smallFiles: number | null;
  snapshotCount: number | null;
  lastMaintainedAt: string | null;
  lastMaintainedOperation: string | null;
  workflowName: string | null;
  workflowSource: string | null;
  hasWorkflow: boolean;
  engineLabel: string | null;
  engineSupported: boolean;
  engineReason: string | null;
  scanBackoff: { until: string | null; failures: number } | null;
  writeAllowed: boolean;
  writeReason: string | null;
  scanAllowed: boolean;
  scanReason: string | null;
  onScan: () => void;
  onRefreshStats: () => void;
  onChooseWorkflow: () => void;
  onPreview: () => void;
  onRun: () => void;
  onRewriteManifests: () => void;
  onOpenSettings: () => void;
  runPending?: boolean;
  scanPending?: boolean;
}

const known = (value: number | null | undefined): value is number =>
  typeof value === "number" && Number.isFinite(value);

const formatIst = (value: string | null | undefined): string | null => {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value;
  const month = IST_MONTHS[Number(part("month")) - 1];
  const day = Number(part("day"));
  const hour = part("hour");
  const minute = part("minute");
  if (!month || !Number.isFinite(day) || !hour || !minute) return null;
  return `${day} ${month} ${hour.padStart(2, "0")}:${minute.padStart(2, "0")} IST`;
};

const countText = (value: number | null): string => (known(value) ? formatNumber(value) : UNKNOWN);

const sizeText = (value: number | null): string => (known(value) ? formatGb(value) : UNKNOWN);

const smallFilesText = (small: number | null, total: number | null): string => {
  if (!known(small) || !known(total)) return UNKNOWN;
  const pct = total > 0 ? Math.round((small / total) * 100) : 0;
  return `${formatNumber(small)} of ${formatNumber(total)} (${pct}%)`;
};

const maintainedText = (at: string | null, operation: string | null): string => {
  const when = formatIst(at);
  if (!when) return "Not recorded";
  const name = operation?.trim();
  return name ? `${when} (${name})` : when;
};

const workflowLabel = (name: string | null, source: string | null): string => {
  const label = name?.trim();
  if (!label) return "Not assigned";
  const origin = source?.trim();
  return origin ? `${label} (${origin})` : label;
};

const backoffLine = (backoff: TableDetailHeaderProps["scanBackoff"]): string | null => {
  if (!backoff) return null;
  const until = formatIst(backoff.until);
  if (until) return `Scans paused until ${until} after ${backoff.failures} failures`;
  return `Scans paused after ${backoff.failures} failures`;
};

const engineLine = (label: string | null, supported: boolean, reason: string | null): string => {
  if (supported) return `Engine: ${label?.trim() || UNKNOWN} · supported`;
  const detail = reason?.trim();
  return detail ? `not supported for this table: ${detail}` : "not supported for this table";
};

const maintenanceReasonFor = (
  writeAllowed: boolean,
  writeReason: string | null,
  hasWorkflow: boolean
): string | null => {
  if (!writeAllowed && writeReason) return writeReason;
  if (!hasWorkflow) return ASSIGN_WORKFLOW_REASON;
  if (!writeAllowed) return writeReason;
  return null;
};

const copyText = (value: string, success: string) => {
  const write = navigator.clipboard?.writeText;
  if (!write) {
    toast.error("Could not copy to clipboard");
    return;
  }
  void write.call(navigator.clipboard, value).then(
    () => toast.success(success),
    () => toast.error("Could not copy to clipboard")
  );
};

const Fact = ({ label, children }: { label: string; children: ReactNode }) => (
  <div className="min-w-0">
    <dt className="text-[11px] font-medium text-muted-foreground">{label}</dt>
    <dd className="mt-0.5 text-sm font-medium text-foreground [font-variant-numeric:tabular-nums]">{children}</dd>
  </div>
);

const GradeScore = ({ grade, score }: { grade: string | null; score: number | null }) => {
  if (!known(score)) return <span className="text-sm text-muted-foreground">Not scanned</span>;
  const integer = Math.round(score);
  const letter = grade?.trim() || null;
  return (
    <span
      className="inline-flex items-center gap-1"
      aria-label={letter ? `Grade ${letter}, score ${integer}` : `Score ${integer}`}
    >
      {letter ? <GradePill grade={letter} /> : null}
      {letter ? <span aria-hidden="true">·</span> : null}
      <span className="text-sm font-medium tabular-nums">{integer}</span>
    </span>
  );
};

export const TableDetailHeader: React.FC<TableDetailHeaderProps> = ({
  tableId,
  tableName,
  qualifiedName,
  sourceName,
  catalogName,
  schemaName,
  scanned,
  score,
  grade,
  ownerEmail,
  partitionLabel,
  location,
  scanAt,
  lastCommitAt,
  sizeGb,
  totalFiles,
  smallFiles,
  snapshotCount,
  lastMaintainedAt,
  lastMaintainedOperation,
  workflowName,
  workflowSource,
  hasWorkflow,
  engineLabel,
  engineSupported,
  engineReason,
  scanBackoff,
  writeAllowed,
  writeReason,
  scanAllowed,
  scanReason,
  onScan,
  onRefreshStats,
  onChooseWorkflow,
  onPreview,
  onRun,
  onRewriteManifests,
  onOpenSettings,
  runPending = false,
  scanPending = false
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = useId();
  const maintenanceReasonId = useId();
  const refreshReasonId = useId();
  const primaryReasonId = useId();

  const catalogKey = catalogName?.trim() || sourceName.trim();
  const sourceHref = catalogKey ? `/tables?catalog=${encodeURIComponent(catalogKey)}` : "/tables";
  const schemaHref = schemaName.trim()
    ? `${sourceHref}${sourceHref.includes("?") ? "&" : "?"}schema=${encodeURIComponent(schemaName.trim())}`
    : sourceHref;
  const tableHref = `/tables/${encodeURIComponent(tableId)}`;
  const workflowHref = `${tableHref}?tab=maintenance#maintenance-settings`;
  const sourceLabel = sourceName.trim() || catalogName?.trim() || UNKNOWN;
  const schemaLabel = schemaName.trim() || UNKNOWN;
  const locationText = location?.trim() || null;
  const owner = ownerEmail?.trim() || "No owner";
  const qualified = qualifiedName?.trim() || null;
  const paused = backoffLine(scanBackoff);
  const maintenanceDisabled = !writeAllowed || !hasWorkflow;
  const maintenanceReason = maintenanceReasonFor(writeAllowed, writeReason, hasWorkflow);
  const refreshDisabled = !scanAllowed;
  const refreshReason = refreshDisabled ? scanReason : null;

  const primary = !scanned
    ? {
        label: "Scan now",
        onClick: onScan,
        disabled: !scanAllowed,
        reason: scanAllowed ? null : scanReason,
        pending: scanPending
      }
    : !hasWorkflow
      ? {
          label: "Choose a workflow",
          onClick: onChooseWorkflow,
          disabled: !writeAllowed,
          reason: writeAllowed ? null : writeReason,
          pending: false
        }
      : {
          label: "Preview maintenance",
          onClick: onPreview,
          disabled: false,
          reason: null,
          pending: false
        };

  return (
    <header className="space-y-3 border-b border-border pb-4">
      <div className="flex items-start justify-between gap-3">
        <Breadcrumb
          className="mb-0 min-w-0"
          items={[
            { title: <Link className={crumbClass} to="/tables">Tables</Link> },
            { title: <Link className={crumbClass} to={sourceHref}>{sourceLabel}</Link> },
            { title: <Link className={crumbClass} to={schemaHref}>{schemaLabel}</Link> },
            {
              title: (
                <Link className={cn(crumbClass, "font-medium text-foreground")} to={tableHref} aria-current="page">
                  {tableName}
                </Link>
              )
            }
          ]}
        />
        <div className="flex shrink-0 items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label="Copy link"
                title={qualified ? `Copy link to ${qualified}` : "Copy link"}
                onClick={() => copyText(window.location.href, "Link copied")}
              >
                <Copy className="size-3.5" aria-hidden="true" />
                Copy link
              </Button>
            </TooltipTrigger>
            <TooltipContent>{qualified ? `Copy link to ${qualified}` : "Copy link"}</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button type="button" variant="ghost" size="icon" aria-label="Table settings" onClick={onOpenSettings}>
                <Settings className="size-3.5" aria-hidden="true" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Table settings</TooltipContent>
          </Tooltip>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <h1 className="mr-1 text-lg font-semibold tracking-tight text-foreground">{tableName}</h1>
        <Badge variant="outline">Iceberg</Badge>
        <Badge variant="outline">{formatPartitionColumnLabel(partitionLabel)}</Badge>
        <GradeScore grade={grade} score={score} />
        <span className="text-sm text-muted-foreground">
          Owner <span className="text-foreground">{owner}</span>
        </span>
      </div>

      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span>Scan {formatIst(scanAt) ?? UNKNOWN}</span>
        <span aria-hidden="true">·</span>
        <span>last commit {formatIst(lastCommitAt) ?? UNKNOWN}</span>
        {locationText ? (
          <>
            <span aria-hidden="true">·</span>
            <code className="max-w-[28rem] truncate font-mono text-[11px] text-foreground" title={locationText}>
              {locationText}
            </code>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  aria-label="Copy location"
                  onClick={() => copyText(locationText, "Path copied")}
                >
                  <Copy className="size-3.5" aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Copy location</TooltipContent>
            </Tooltip>
          </>
        ) : null}
      </p>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 lg:grid-cols-4">
        <Fact label="Size">{sizeText(sizeGb)}</Fact>
        <Fact label="Files">{countText(totalFiles)}</Fact>
        <Fact label="Small files">{smallFilesText(smallFiles, totalFiles)}</Fact>
        <Fact label="Snapshots">{countText(snapshotCount)}</Fact>
        <Fact label="Last maintained">{maintainedText(lastMaintainedAt, lastMaintainedOperation)}</Fact>
        <Fact label="Workflow">
          <Link className="underline-offset-2 hover:underline" to={workflowHref}>
            {workflowLabel(workflowName, workflowSource)}
          </Link>
        </Fact>
      </dl>

      {paused ? (
        <p className="text-xs text-[var(--color-status-warning)]" role="status">
          {paused}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
        <div className="relative inline-flex flex-col items-end gap-1">
          <div className="inline-flex">
            <Button
              type="button"
              variant="primary"
              size="sm"
              className="rounded-r-none"
              disabled={primary.disabled}
              loading={primary.pending}
              title={primary.reason ?? undefined}
              aria-describedby={primary.disabled && primary.reason ? primaryReasonId : undefined}
              onClick={primary.onClick}
            >
              {primary.label}
            </Button>
            <Button
              type="button"
              variant="primary"
              size="sm"
              className="rounded-l-none border-l border-primary-foreground/30 px-2"
              aria-label="More actions"
              aria-expanded={menuOpen}
              aria-haspopup="menu"
              aria-controls={menuId}
              onClick={() => setMenuOpen((open) => !open)}
            >
              <ChevronDown className="size-3.5" aria-hidden="true" />
            </Button>
          </div>
          {primary.disabled && primary.reason ? (
            <p id={primaryReasonId} className="max-w-xs text-right text-xs text-muted-foreground">
              {primary.reason}
            </p>
          ) : null}
          <div
            id={menuId}
            role="group"
            aria-label="Maintenance actions"
            className={cn(
              "absolute right-0 top-full z-30 mt-1 w-72 rounded-[var(--radius-default)] border border-border bg-popover p-1 text-popover-foreground shadow-md",
              !menuOpen && "hidden"
            )}
          >
            <ConfirmAction
              title="Run now?"
              description="Queues a real maintenance job using the assigned workflow."
              onConfirm={onRun}
              disabled={maintenanceDisabled}
            >
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-auto w-full justify-start whitespace-normal px-2"
                disabled={maintenanceDisabled || runPending}
                loading={runPending}
                title={maintenanceReason ?? undefined}
                aria-describedby={maintenanceReason ? maintenanceReasonId : undefined}
              >
                Run now
              </Button>
            </ConfirmAction>
            <ConfirmAction
              title="Rewrite manifests?"
              description="Queues manifest-only maintenance without data file compaction."
              onConfirm={onRewriteManifests}
              disabled={maintenanceDisabled}
            >
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-auto w-full justify-start whitespace-normal px-2"
                disabled={maintenanceDisabled || runPending}
                loading={runPending}
                title={maintenanceReason ?? undefined}
                aria-describedby={maintenanceReason ? maintenanceReasonId : undefined}
              >
                Rewrite manifests
              </Button>
            </ConfirmAction>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-auto w-full justify-start whitespace-normal px-2"
              disabled={refreshDisabled || scanPending}
              loading={scanPending}
              title={refreshReason ?? undefined}
              aria-describedby={refreshReason ? refreshReasonId : undefined}
              onClick={onRefreshStats}
            >
              Refresh stats
            </Button>
            {maintenanceReason ? (
              <p id={maintenanceReasonId} className="px-2 py-1 text-xs text-muted-foreground">
                {maintenanceReason}
              </p>
            ) : null}
            {refreshReason ? (
              <p id={refreshReasonId} className="px-2 py-1 text-xs text-muted-foreground">
                {refreshReason}
              </p>
            ) : null}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">{engineLine(engineLabel, engineSupported, engineReason)}</p>
      </div>
    </header>
  );
};
