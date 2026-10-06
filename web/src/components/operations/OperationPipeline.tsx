import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { StatusTag } from "../status";

export type PipelineStepStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";

export interface PipelineStep {
  key: string;
  label: string;
  status: PipelineStepStatus;
  detail?: string;
  statusText?: string;
}

const STEP_ICON: Record<PipelineStepStatus, ReactNode> = {
  pending: <Circle className="ops-pipeline-icon ops-pipeline-icon--pending size-4" />,
  running: <Loader2 className="ops-pipeline-icon ops-pipeline-icon--running size-4 animate-spin" />,
  succeeded: <CheckCircle2 className="ops-pipeline-icon ops-pipeline-icon--succeeded size-4" />,
  failed: <XCircle className="ops-pipeline-icon ops-pipeline-icon--failed size-4" />,
  skipped: <Circle className="ops-pipeline-icon ops-pipeline-icon--skipped size-4" />
};

// Fallback pipeline shown when a run reports no operations; sequence mirrors the canonical
// order in ../../lib/operationOrder.ts.
const MAINTENANCE_OPS = [
  "optimize",
  "optimize_manifests",
  "expire_snapshots",
  "remove_orphan_files"
] as const;

const opLabel = (op: string) => op.replaceAll("_", " ");

const NEUTRAL_STATUS_TEXT = new Set(["Not recorded", "Waiting"]);

export interface MaintenanceStepOptions {
  absentLabel?: string;
  partialOps?: string[];
  skippedOps?: string[];
}

export const maintenanceOpsToSteps = (
  operations: string[],
  activeOp?: string | null,
  completedOps: string[] = [],
  failedOp?: string | null,
  options?: MaintenanceStepOptions
): PipelineStep[] => {
  const ordered = operations.length ? operations : [...MAINTENANCE_OPS];
  const partialOps = options?.partialOps ?? [];
  const skippedOps = options?.skippedOps ?? [];
  return ordered.map((op) => {
    if (failedOp === op) {
      return { key: op, label: opLabel(op), status: "failed" as const };
    }
    if (partialOps.includes(op)) {
      return { key: op, label: opLabel(op), status: "failed" as const, detail: "partial" };
    }
    if (completedOps.includes(op)) {
      return { key: op, label: opLabel(op), status: "succeeded" as const };
    }
    if (skippedOps.includes(op)) {
      return { key: op, label: opLabel(op), status: "skipped" as const };
    }
    if (activeOp === op) {
      return { key: op, label: opLabel(op), status: "running" as const };
    }
    return {
      key: op,
      label: opLabel(op),
      status: "pending" as const,
      statusText: options?.absentLabel
    };
  });
};

const SCAN_STEPS = [
  { key: "queue", label: "Queue" },
  { key: "discover", label: "Discover tables" },
  { key: "scan", label: "Scan shards" },
  { key: "finalize", label: "Finalize" }
] as const;

const FINISHED_SCAN_STATUSES = new Set(["succeeded", "failed", "cancelled", "partial", "partial_failed"]);

export const scanPipelineSteps = (status?: string | null): PipelineStep[] => {
  const runStatus = (status ?? "").toLowerCase();
  const statusText = !runStatus || FINISHED_SCAN_STATUSES.has(runStatus) ? "Not recorded" : "Waiting";
  return SCAN_STEPS.map((step) => ({
    key: step.key,
    label: step.label,
    status: "pending",
    statusText
  }));
};

const StepStatus = ({ step }: { step: PipelineStep }) => {
  if (step.statusText && NEUTRAL_STATUS_TEXT.has(step.statusText)) {
    return <span className="ops-pipeline__detail text-xs text-muted-foreground">{step.statusText}</span>;
  }
  if (step.statusText) {
    return <StatusTag status={step.statusText} />;
  }
  if (step.detail) {
    return <span className="ops-pipeline__detail text-xs text-muted-foreground">{step.detail}</span>;
  }
  return <StatusTag status={step.status} />;
};

export const OperationPipeline = ({
  steps,
  title = "Pipeline"
}: {
  steps: PipelineStep[];
  title?: string;
}) => (
  <div className="ops-pipeline">
    <div className="ops-pipeline__title">{title}</div>
    <div className="ops-pipeline__track" role="list" aria-label={title}>
      {steps.map((step, idx) => (
        <div key={step.key} className="ops-pipeline__step" role="listitem">
          <div className="ops-pipeline__node flex flex-col items-center gap-1">
            {STEP_ICON[step.status]}
            <span className="ops-pipeline__label text-sm">{step.label}</span>
            <StepStatus step={step} />
          </div>
          {idx < steps.length - 1 ? <div className="ops-pipeline__connector" aria-hidden /> : null}
        </div>
      ))}
    </div>
  </div>
);
