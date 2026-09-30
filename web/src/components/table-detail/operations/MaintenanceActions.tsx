import { Archive, Check, CheckCircle2, Clock3, FileStack, Layers, Plus, Trash2, XCircle, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { AuditEntry, TableStatsRead } from "../../../api/types";
import { formatDuration } from "../../../lib/format";
import { Button } from "../../ui/button";
import {
  OPERATION_CATALOG,
  RUNNABLE_OPERATIONS,
  isFailureStatus,
  lastRunByOperation,
  operationEvidence,
  pointsByOperation,
  relativeTime,
  type Deduction,
  type LastRun,
  type RunnableOperation
} from "./operationCatalog";
import { useOperationLauncher } from "./OperationLauncher";
import "./operations.css";

const ICONS: Record<RunnableOperation, LucideIcon> = {
  optimize: Layers,
  optimize_manifests: FileStack,
  expire_snapshots: Archive,
  remove_orphan_files: Trash2
};

const LastRunLine = ({ run }: { run: LastRun | undefined }) => {
  if (!run) {
    return (
      <p className="td-op-last" data-state="never">
        <Clock3 size={13} aria-hidden="true" /> Not run on this table yet
      </p>
    );
  }
  const failed = isFailureStatus(run.status);
  return (
    <p className="td-op-last" data-state={failed ? "failed" : "ok"} title={new Date(run.at).toLocaleString()}>
      {failed ? <XCircle size={13} aria-hidden="true" /> : <CheckCircle2 size={13} aria-hidden="true" />}
      {failed ? "Failed" : "Ran"} {relativeTime(run.at)}
      {run.durationMs != null ? ` · ${formatDuration(run.durationMs)}` : ""}
      {` · ${run.scheduled ? "nightly" : "manual"}`}
    </p>
  );
};

export const MaintenanceActions = ({
  tableId,
  stats,
  audit,
  orphanBytes,
  deductions,
  title = "Run on demand",
  footer,
  id
}: {
  tableId: string;
  stats: TableStatsRead | null | undefined;
  audit: AuditEntry[];
  orphanBytes: number | null | undefined;
  deductions: Deduction[];
  title?: string;
  footer?: ReactNode;
  id?: string;
}) => {
  const launcher = useOperationLauncher();
  const recovery = pointsByOperation(deductions, tableId);
  const lastRuns = lastRunByOperation(audit);
  const recommended = RUNNABLE_OPERATIONS.filter((op) => (recovery.get(op) ?? 0) > 0);
  const blocked = !launcher || Boolean(launcher.blockedReason) || launcher.pending;

  return (
    <section className="td-ops" aria-labelledby="td-ops-title" id={id}>
      <header className="td-ops-head">
        <div>
          <h2 id="td-ops-title">{title}</h2>
          <p>Add operations to a run, review them in order, run them together. Highlighted ones fix this table's diagnosis.</p>
        </div>
        <div className="td-ops-presets">
          {launcher?.blockedReason ? <p className="td-ops-blocked">{launcher.blockedReason}</p> : null}
          {recommended.length ? (
            <Button type="button" size="sm" variant="secondary" disabled={blocked} onClick={() => launcher?.setCart([...(launcher?.cart ?? []), ...recommended])}>
              <Plus aria-hidden="true" /> Add recommended ({recommended.length})
            </Button>
          ) : null}
          <Button type="button" size="sm" variant="ghost" disabled={blocked} onClick={() => launcher?.setCart([...RUNNABLE_OPERATIONS])}>
            All four
          </Button>
        </div>
      </header>
      <ul className="td-ops-grid">
        {RUNNABLE_OPERATIONS.map((op) => {
          const info = OPERATION_CATALOG[op];
          const Icon = ICONS[op];
          const evidence = operationEvidence(op, stats, orphanBytes);
          const points = recovery.get(op) ?? 0;
          const added = launcher?.inCart(op) ?? false;
          return (
            <li key={op} className="td-op-card" data-recommended={points > 0 || undefined} data-added={added || undefined}>
              <div className="td-op-card-top">
                <span className="td-op-icon" data-destructive={info.destructive || undefined}>
                  <Icon size={16} aria-hidden="true" />
                </span>
                <h3>{info.title}</h3>
                {points > 0 ? <span className="td-op-badge">+{points} pts</span> : null}
              </div>
              <p className="td-op-summary">{info.summary}</p>
              <div className="td-op-evidence">
                <strong>{evidence.value}</strong>
                <span>{evidence.label}</span>
              </div>
              <p className="td-op-detail">{evidence.detail ?? " "}</p>
              <LastRunLine run={lastRuns.get(op)} />
              <Button
                type="button"
                size="sm"
                variant={added ? "primary" : "secondary"}
                className="td-op-run"
                aria-pressed={added}
                disabled={blocked}
                title={launcher?.blockedReason ?? undefined}
                onClick={() => launcher?.toggle(op)}
              >
                {added ? <Check aria-hidden="true" /> : <Plus aria-hidden="true" />}
                {added ? "Added to run" : "Add to run"}
              </Button>
            </li>
          );
        })}
      </ul>
      {footer ? <footer className="td-ops-footer">{footer}</footer> : null}
    </section>
  );
};
