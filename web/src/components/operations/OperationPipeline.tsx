import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { StatusTag } from "../status";

export type PipelineStepStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";

export interface PipelineStep {
  key: string;
  label: string;
  status: PipelineStepStatus;
  detail?: string;
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

export const maintenanceOpsToSteps = (
  operations: string[],
  activeOp?: string | null,
  completedOps: string[] = [],
  failedOp?: string | null
): PipelineStep[] => {
  const ordered = operations.length ? operations : [...MAINTENANCE_OPS];
  return ordered.map((op) => {
    if (failedOp === op) {
      return { key: op, label: opLabel(op), status: "failed" as const };
    }
    if (completedOps.includes(op)) {
      return { key: op, label: opLabel(op), status: "succeeded" as const };
    }
    if (activeOp === op) {
      return { key: op, label: opLabel(op), status: "running" as const };
    }
    return { key: op, label: opLabel(op), status: "pending" as const };
  });
};

export const scanPipelineSteps = (status?: string | null): PipelineStep[] => {
  const runStatus = (status ?? "").toLowerCase();
  const phase = (phaseStatus: PipelineStepStatus): PipelineStepStatus => {
    if (runStatus === "failed") return phaseStatus === "running" ? "failed" : phaseStatus;
    return phaseStatus;
  };
  const done = ["succeeded", "failed", "cancelled"].includes(runStatus);
  return [
    { key: "queue", label: "Queue", status: done || runStatus === "running" ? "succeeded" : runStatus === "queued" || runStatus === "accepted" ? "running" : "pending" },
    { key: "discover", label: "Discover tables", status: done ? "succeeded" : runStatus === "running" ? "running" : "pending" },
    { key: "scan", label: "Scan shards", status: phase(done ? "succeeded" : runStatus === "running" ? "running" : "pending") },
    { key: "finalize", label: "Finalize", status: done ? (runStatus === "succeeded" ? "succeeded" : runStatus === "failed" ? "failed" : "skipped") : "pending" }
  ];
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
            {step.detail ? (
              <span className="ops-pipeline__detail text-xs text-muted-foreground">{step.detail}</span>
            ) : (
              <StatusTag status={step.status === "pending" ? "queued" : step.status} />
            )}
          </div>
          {idx < steps.length - 1 ? <div className="ops-pipeline__connector" aria-hidden /> : null}
        </div>
      ))}
    </div>
  </div>
);
