import type { RunRead } from "../../api/types";
import { formatShortDate } from "../../lib/format";

export type RunTone = "succeeded" | "running" | "queued" | "failed" | "partial" | "cancelled" | "other";

const TONES: Record<string, RunTone> = {
  succeeded: "succeeded",
  success: "succeeded",
  running: "running",
  admitted: "running",
  queued: "queued",
  accepted: "queued",
  scheduled: "queued",
  failed: "failed",
  error: "failed",
  partial: "partial",
  partial_failed: "partial",
  cancelled: "cancelled",
  canceled: "cancelled",
  skipped: "cancelled"
};

export const runTone = (status?: string | null): RunTone => TONES[(status ?? "").toLowerCase()] ?? "other";

export const RUN_TONE_LABEL: Record<RunTone, string> = {
  succeeded: "Succeeded",
  running: "Running",
  queued: "Queued",
  failed: "Failed",
  partial: "Partial",
  cancelled: "Cancelled",
  other: "Other"
};

/** Legend order follows a run's life: waiting, working, then how it ended. */
export const RUN_TONE_ORDER: RunTone[] = ["queued", "running", "succeeded", "partial", "failed", "cancelled"];

export const countByTone = (runs: readonly Pick<RunRead, "status">[]): Record<RunTone, number> => {
  const counts: Record<RunTone, number> = {
    succeeded: 0,
    running: 0,
    queued: 0,
    failed: 0,
    partial: 0,
    cancelled: 0,
    other: 0
  };
  for (const run of runs) counts[runTone(run.status)] += 1;
  return counts;
};

/** Finished runs only: an in-flight run has not succeeded or failed yet. */
export const successRate = (runs: readonly Pick<RunRead, "status">[]): number | null => {
  const counts = countByTone(runs);
  const finished = counts.succeeded + counts.failed + counts.partial;
  return finished === 0 ? null : counts.succeeded / finished;
};

export const formatRelative = (value: string | null | undefined, now: number = Date.now()): string => {
  if (!value) return "—";
  const at = Date.parse(value);
  if (!Number.isFinite(at)) return "—";
  const seconds = Math.round((now - at) / 1000);
  const future = seconds < 0;
  const abs = Math.abs(seconds);
  let text: string;
  if (abs < 45) text = "just now";
  else if (abs < 3600) text = `${Math.round(abs / 60)}m`;
  else if (abs < 86_400) text = `${Math.round(abs / 3600)}h`;
  else if (abs < 30 * 86_400) text = `${Math.round(abs / 86_400)}d`;
  else return formatShortDate(at);
  if (text === "just now") return text;
  return future ? `in ${text}` : `${text} ago`;
};
