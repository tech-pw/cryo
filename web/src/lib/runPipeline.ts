/**
 * Per-operation status for the run detail "Maintenance pipeline" card (#184).
 *
 * A fleet run holds one job per table, and each job runs several operations. The card
 * used to stop at the first operation where *any* job failed and showed every later
 * operation as not reached, even when hundreds of tables completed them. It also only
 * looked at a fixed list of four operations.
 *
 * This module counts outcomes per operation across all jobs and never stops early. The
 * operation list comes from the jobs themselves, so delete conversion and statistics
 * show up when they ran. Executed steps (`job.steps`, from `cryo.job_steps`) are
 * preferred over the job's plan when a job carries them; otherwise the job's own status
 * is attributed to each operation in its plan.
 */
import type { JobRead, RunStepRead } from "../api/types";
import type { PipelineStep, PipelineStepStatus } from "../components/operations/OperationPipeline";
import { CORRECT_ORDER } from "./operationOrder";

/** Job as the pipeline reads it; `steps` is optional because not every payload carries it. */
export type PipelineJob = Pick<JobRead, "status" | "plan"> & { steps?: RunStepRead[] | null };

export type OperationOutcome = "succeeded" | "failed" | "partial" | "skipped" | "running" | "waiting" | "cancelled";

export interface OperationCounts {
  op: string;
  succeeded: number;
  failed: number;
  partial: number;
  skipped: number;
  running: number;
  waiting: number;
  cancelled: number;
  total: number;
}

const RUNNING = new Set(["running", "admitted", "started"]);
const WAITING = new Set(["queued", "deferred", "pending"]);
const FAILED = new Set(["failed", "partial_failed", "error"]);

const outcomeOf = (status: string | null | undefined): OperationOutcome => {
  const value = (status ?? "").toLowerCase();
  if (value === "succeeded" || value === "success" || value === "completed") return "succeeded";
  if (FAILED.has(value)) return "failed";
  if (value === "partial") return "partial";
  if (value === "skipped") return "skipped";
  if (RUNNING.has(value)) return "running";
  if (value === "cancelled" || value === "canceled") return "cancelled";
  return "waiting";
};

export const planOperationNames = (plan: JobRead["plan"]): string[] => {
  if (!plan) return [];
  const names: string[] = [];
  const ops = plan.operations;
  if (Array.isArray(ops)) {
    for (const op of ops) {
      if (typeof op === "string" && op) names.push(op);
      else if (op && typeof op === "object" && "type" in op) {
        const type = (op as { type?: unknown }).type;
        if (typeof type === "string" && type) names.push(type);
      }
    }
  }
  const single = plan.operation;
  if (typeof single === "string" && single) names.push(single);
  return names;
};

/** Collapse several steps of one operation (chunked compaction, retries) into one outcome. */
const combine = (outcomes: OperationOutcome[]): OperationOutcome => {
  if (outcomes.includes("running")) return "running";
  if (outcomes.includes("failed")) return "failed";
  if (outcomes.includes("partial")) return "partial";
  if (outcomes.includes("succeeded")) {
    return outcomes.every((item) => item === "succeeded" || item === "skipped") ? "succeeded" : "partial";
  }
  if (outcomes.length && outcomes.every((item) => item === "skipped")) return "skipped";
  if (outcomes.includes("cancelled")) return "cancelled";
  return "waiting";
};

/** Operation -> outcome for one job. Executed steps win over the plan. */
export const jobOperationOutcomes = (job: PipelineJob): Map<string, OperationOutcome> => {
  const result = new Map<string, OperationOutcome>();
  const steps = job.steps ?? [];
  if (steps.length) {
    const byOp = new Map<string, OperationOutcome[]>();
    for (const step of steps) {
      if (!step.operation_type) continue;
      const list = byOp.get(step.operation_type) ?? [];
      list.push(outcomeOf(step.status));
      byOp.set(step.operation_type, list);
    }
    for (const [op, outcomes] of byOp) result.set(op, combine(outcomes));
    // Planned operations the job never reached: they take the job's terminal state when
    // it was skipped or cancelled, and otherwise are still waiting or were not reached.
    const jobOutcome = outcomeOf(job.status);
    for (const op of planOperationNames(job.plan)) {
      if (result.has(op)) continue;
      if (jobOutcome === "skipped" || jobOutcome === "cancelled") result.set(op, jobOutcome);
      else if (jobOutcome === "running" || jobOutcome === "waiting") result.set(op, "waiting");
      else result.set(op, "skipped");
    }
    return result;
  }
  const outcome = outcomeOf(job.status);
  for (const op of planOperationNames(job.plan)) result.set(op, outcome);
  return result;
};

const orderIndex = (op: string) => {
  const index = (CORRECT_ORDER as readonly string[]).indexOf(op);
  return index === -1 ? CORRECT_ORDER.length : index;
};

export const countOperations = (jobs: PipelineJob[]): OperationCounts[] => {
  const counts = new Map<string, OperationCounts>();
  for (const job of jobs) {
    for (const [op, outcome] of jobOperationOutcomes(job)) {
      const entry =
        counts.get(op) ??
        { op, succeeded: 0, failed: 0, partial: 0, skipped: 0, running: 0, waiting: 0, cancelled: 0, total: 0 };
      entry[outcome] += 1;
      entry.total += 1;
      counts.set(op, entry);
    }
  }
  return [...counts.values()].sort((a, b) => orderIndex(a.op) - orderIndex(b.op) || a.op.localeCompare(b.op));
};

export const formatOperationCounts = (counts: OperationCounts): string => {
  const parts: string[] = [];
  if (counts.succeeded) parts.push(`${counts.succeeded.toLocaleString()} ✓`);
  if (counts.failed) parts.push(`${counts.failed.toLocaleString()} ✗`);
  if (counts.partial) parts.push(`${counts.partial.toLocaleString()} partial`);
  if (counts.skipped) parts.push(`${counts.skipped.toLocaleString()} skipped`);
  if (counts.running) parts.push(`${counts.running.toLocaleString()} running`);
  if (counts.waiting) parts.push(`${counts.waiting.toLocaleString()} waiting`);
  if (counts.cancelled) parts.push(`${counts.cancelled.toLocaleString()} cancelled`);
  return parts.join(" · ");
};

const stepStatus = (counts: OperationCounts): PipelineStepStatus => {
  if (counts.running) return "running";
  if (counts.failed || counts.partial) return "failed";
  if (counts.succeeded) return "succeeded";
  if (counts.skipped && counts.skipped + counts.cancelled === counts.total) return "skipped";
  return "pending";
};

const opLabel = (op: string) => op.replaceAll("_", " ");

/**
 * Pipeline steps from a run's jobs. Returns an empty list when no job names an
 * operation, so the caller can fall back to its placeholder rows.
 */
export const pipelineStepsFromJobs = (jobs: PipelineJob[]): PipelineStep[] =>
  countOperations(jobs).map((counts) => {
    const status = stepStatus(counts);
    const allWaiting = counts.waiting === counts.total;
    const single = counts.total === 1;
    const step: PipelineStep = { key: counts.op, label: opLabel(counts.op), status };
    if (allWaiting) step.statusText = "Waiting";
    // One table: the icon and status tag say it all. Several tables: show the counts.
    else if (!single) step.detail = formatOperationCounts(counts);
    else if (counts.partial) step.detail = "partial";
    return step;
  });
