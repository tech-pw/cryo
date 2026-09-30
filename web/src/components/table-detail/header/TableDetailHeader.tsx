import { AlertTriangle, ChevronDown, Clock3, Copy, Database, Files, GitBranch, HardDrive, Layers3, RefreshCw, Settings, Workflow } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import type * as React from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { ConfirmAction } from "../../confirm";
import { GradePill } from "../../status";
import { Badge } from "../../ui/badge";
import { Breadcrumb } from "../../ui/breadcrumb";
import { Button } from "../../ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../../ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../ui/tooltip";
import { cn } from "../../../lib/cn";
import { formatGb, formatNumber } from "../../../lib/format";
import { operationLabels } from "../../../lib/theme";
import { formatPartitionColumnLabel } from "../helpers";
import "./table-detail-header.css";

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
  chooseWorkflowAllowed?: boolean;
  chooseWorkflowReason?: string | null;
  scanAllowed: boolean;
  scanReason: string | null;
  onScan: () => void;
  onRefreshStats: () => void;
  onChooseWorkflow: () => void;
  onPreview: () => void;
  onRun: () => void;
  onOpenSettings: () => void;
  /** Opens the Run on demand section; the header's primary action once a workflow exists. */
  onOpenRunOnDemand?: () => void;
  /** Single operations the operator can launch; each opens a confirmation. */
  operations?: Array<{ op: string; label: string; destructive: boolean }>;
  onRunOperation?: (op: string) => void;
  operationsDisabledReason?: string | null;
  runPending?: boolean;
  scanPending?: boolean;
}

const known = (value: number | null | undefined): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

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

const smallFilesShareText = (small: number | null, total: number | null): string => {
  if (!known(small) || !known(total) || small > total) return UNKNOWN;
  const pct = total > 0 ? Math.round((small / total) * 100) : 0;
  return `${pct}% of ${formatNumber(total)} files`;
};

const maintainedText = (at: string | null, operation: string | null): string => {
  const when = formatIst(at);
  if (!when) return "Not recorded";
  const name = operation?.trim();
  return name ? `${operationLabels[name] ?? name.replaceAll("_", " ")} · ${when}` : when;
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
  if (supported) return `Runs on ${label?.trim() || UNKNOWN}`;
  const detail = reason?.trim();
  return detail ? `Maintenance unavailable: ${detail}` : "No compatible engine assigned";
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

const Fact = ({ label, icon, children, detail }: { label: string; icon: ReactNode; children: ReactNode; detail?: ReactNode }) => (
  <div className="ctd-header__fact">
    <dt>{icon}{label}</dt>
    <dd>{children}{detail ? <div className="ctd-header__fact-detail">{detail}</div> : null}</dd>
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
      <span className="text-xs font-medium tabular-nums">Health {integer}/100</span>
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
  chooseWorkflowAllowed = writeAllowed,
  chooseWorkflowReason = writeReason,
  scanAllowed,
  scanReason,
  onScan,
  onRefreshStats,
  onChooseWorkflow,
  onPreview,
  onRun,
  onOpenSettings,
  onOpenRunOnDemand,
  operations = [],
  onRunOperation,
  operationsDisabledReason = null,
  runPending = false,
  scanPending = false
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
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
  const maintenanceDisabled = !writeAllowed || !hasWorkflow || !engineSupported;
  const maintenanceReason = maintenanceReasonFor(writeAllowed, writeReason, hasWorkflow)
    ?? (engineSupported ? null : engineReason || "No compatible engine assigned");
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
          disabled: !chooseWorkflowAllowed,
          reason: chooseWorkflowAllowed ? null : chooseWorkflowReason,
          pending: false
        }
      : {
          label: onOpenRunOnDemand ? "Run maintenance" : "Preview maintenance",
          onClick: onOpenRunOnDemand ?? onPreview,
          disabled: false,
          reason: null,
          pending: false
        };

  return (
    <header className="ctd-header">
      <div className="ctd-header__breadcrumb-row">
        <Breadcrumb
          className="ctd-header__breadcrumb mb-0 min-w-0"
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

      <div className="ctd-header__identity-row">
        <div className="ctd-header__identity">
          <h1>{tableName}</h1>
          <div className="ctd-header__identity-meta">
            <Badge variant="outline">Iceberg</Badge>
            <span className="ctd-header__partition"><GitBranch size={13} aria-hidden="true" />{formatPartitionColumnLabel(partitionLabel)}</span>
            <GradeScore grade={grade} score={score} />
            <span>Owner <span className="text-foreground">{owner}</span></span>
          </div>
        </div>
        <div className="ctd-header__actions">
          <Popover open={menuOpen} onOpenChange={setMenuOpen}>
          <div className="ctd-header__action-buttons">
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
            <PopoverTrigger asChild><Button
              type="button"
              variant="primary"
              size="sm"
              className="rounded-l-none border-l border-primary-foreground/30 px-2"
              aria-label="More actions"
            >
              <ChevronDown className="size-3.5" aria-hidden="true" />
            </Button></PopoverTrigger>
          </div>
          {primary.disabled && primary.reason ? (
            <p id={primaryReasonId} className="ctd-header__primary-reason">
              {primary.reason}
            </p>
          ) : null}
          <PopoverContent
            align="end"
            aria-label="Maintenance actions"
            className="ctd-header__action-popover p-1"
          >
            {onOpenRunOnDemand && scanned && hasWorkflow ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-auto w-full justify-start whitespace-normal px-2"
                onClick={() => {
                  setMenuOpen(false);
                  onPreview();
                }}
              >
                Preview workflow plan
              </Button>
            ) : null}
            <ConfirmAction
              title="Run the full workflow now?"
              description="Queues every operation in the assigned workflow for this table."
              onConfirm={onRun}
              disabled={maintenanceDisabled || runPending}
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
                Run full workflow now
              </Button>
            </ConfirmAction>
            {operations.length ? (
              <div className="ctd-header__menu-group" role="group" aria-label="Add to run">
                <p className="ctd-header__menu-label">Add to run</p>
                {operations.map((item) => (
                  <Button
                    key={item.op}
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-auto w-full justify-between whitespace-normal px-2"
                    disabled={Boolean(operationsDisabledReason)}
                    title={operationsDisabledReason ?? undefined}
                    onClick={() => {
                      setMenuOpen(false);
                      onRunOperation?.(item.op);
                    }}
                  >
                    <span>{item.label}</span>
                    {item.destructive ? <span className="ctd-header__menu-flag">deletes files</span> : null}
                  </Button>
                ))}
                {operationsDisabledReason ? (
                  <p className="px-2 py-1 text-xs text-muted-foreground">{operationsDisabledReason}</p>
                ) : null}
              </div>
            ) : null}
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
          </PopoverContent>
          </Popover>
        </div>
      </div>

      <div className="ctd-header__context-band">
        <dl className="ctd-header__facts">
          <Fact label="Table size" icon={<HardDrive aria-hidden="true" />}>{sizeText(sizeGb)}</Fact>
          <Fact label="Files" icon={<Files aria-hidden="true" />}>{countText(totalFiles)}</Fact>
          <Fact
            label="Compaction candidates"
            icon={<Layers3 aria-hidden="true" />}
            detail={smallFilesShareText(smallFiles, totalFiles)}
          >
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="ctd-header__candidate-count" tabIndex={0}>{countText(smallFiles)}</span>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">Small files in partitions flagged for compaction by the scanner. This is not a count of every undersized file.</TooltipContent>
            </Tooltip>
          </Fact>
          <Fact label="Snapshots" icon={<Database aria-hidden="true" />}>{countText(snapshotCount)}</Fact>
        </dl>
        <div className="ctd-header__workflow">
          <div className="ctd-header__workflow-label"><Workflow size={14} aria-hidden="true" />Maintenance workflow</div>
          <Link className="ctd-header__workflow-link" to={workflowHref}>
            {workflowLabel(workflowName, workflowSource)}
          </Link>
          <p className="ctd-header__workflow-detail">
            {hasWorkflow
              ? engineLine(engineLabel, engineSupported, engineReason)
              : "Choose a workflow to enable maintenance."}
          </p>
        </div>
      </div>

      <div className="ctd-header__evidence">
        <span><RefreshCw aria-hidden="true" />Scan {formatIst(scanAt) ?? "not recorded"}</span>
        <span><GitBranch aria-hidden="true" />Last commit {formatIst(lastCommitAt) ?? "not recorded"}</span>
        <span><Clock3 aria-hidden="true" />Latest action: {maintainedText(lastMaintainedAt, lastMaintainedOperation)}</span>
        {locationText ? (
          <details className="ctd-header__location">
            <summary>Storage location</summary>
            <div className="ctd-header__location-content">
              <code>{locationText}</code>
              <Button type="button" variant="ghost" size="icon" aria-label="Copy location" onClick={() => copyText(locationText, "Path copied")}>
                <Copy size={14} aria-hidden="true" />
              </Button>
            </div>
          </details>
        ) : null}
      </div>

      {paused ? (
        <p className="ctd-header__scan-warning" role="status"><AlertTriangle size={15} aria-hidden="true" />{paused}</p>
      ) : null}
    </header>
  );
};
