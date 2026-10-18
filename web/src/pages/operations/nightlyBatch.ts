import type { RunRead } from "../../api/types";

export const NIGHTLY_WORKFLOW_NAME = "Nightly maintenance";
// The batch CronJob's cron lives in Helm values (maintenance.schedule) and is not exposed
// by any API, so this cannot be read live the way scanner and preset schedules are. Say
// where it is configured instead of implying a time Cryo does not actually know.
export const NIGHTLY_SCHEDULE_LABEL = "Kubernetes CronJob (set in Helm)";
export const NO_PRESETS_COPY = "Maintenance is driven by the nightly batch.";

export const isBatchRun = (run: Pick<RunRead, "summary">): boolean => {
  const summary = run.summary;
  if (summary == null || typeof summary !== "object") return false;
  return summary.pipeline === "batch";
};

const createdAtMillis = (run: RunRead): number => {
  const parsed = Date.parse(run.created_at);
  return Number.isNaN(parsed) ? 0 : parsed;
};

export const nightlyBatchRuns = (runs: readonly RunRead[]): RunRead[] =>
  runs.filter(isBatchRun).sort((a, b) => createdAtMillis(b) - createdAtMillis(a));

export const latestNightlyRunPath = (runs: readonly RunRead[]): string | null => {
  const latest = nightlyBatchRuns(runs)[0];
  return latest ? `/runs/${latest.id}` : null;
};
