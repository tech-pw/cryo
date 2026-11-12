import { apiFetch, type ApiOptions } from "./client";

export interface AsyncSparkSummary {
  enabled: boolean;
  /** Null when Redis is unreachable. */
  parked: number | null;
  overdue: number | null;
  max_in_flight_per_worker: number;
  poll_interval_seconds: number;
}

export interface ParkedJob {
  job_id: string;
  step_index: number;
  spark_ref: string | null;
  /** Epoch seconds. */
  parked_at: number | null;
  last_checked_at: number | null;
  next_check_at: number | null;
  overdue: boolean;
}

export const getAsyncSparkSummary = (api: ApiOptions) => apiFetch<AsyncSparkSummary>("/async-spark/summary", api);

export const lookupParkedJobs = (api: ApiOptions, jobIds: string[]) =>
  apiFetch<{ jobs: ParkedJob[] }>("/async-spark/jobs", api, { method: "POST", body: { job_ids: jobIds.slice(0, 500) } });

const ago = (epochSeconds: number, now: number): string => {
  const minutes = Math.max(0, Math.round((now - epochSeconds * 1000) / 60_000));
  return minutes < 1 ? "just now" : minutes < 60 ? `${minutes}m ago` : `${Math.round(minutes / 60)}h ago`;
};

/** Tooltip text for a parked job: when it was submitted and last looked at. */
export const parkedDetail = (job: ParkedJob, now = Date.now()): string => {
  const parts = [`Step ${job.step_index + 1} is running on Spark without holding a worker.`];
  if (job.parked_at) parts.push(`Submitted ${ago(job.parked_at, now)}.`);
  parts.push(job.last_checked_at ? `Last checked ${ago(job.last_checked_at, now)}.` : "Not checked yet.");
  if (job.overdue) parts.push("The governor has not checked it for several resolver ticks.");
  return parts.join(" ");
};
