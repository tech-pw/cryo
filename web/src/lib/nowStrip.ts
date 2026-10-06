import type { RunRead } from "../api/types";
import type { CryoVitalsImpact } from "../api/vitals";
import { batchCounts, isBatchPipeline } from "./batchSummary";
import { formatGb, formatNumber, formatTime } from "./format";

export const NOT_MEASURED = "Not measured";

export interface ReclaimImpact {
  measurement_state?: string | null;
  gb_freed_yesterday?: number | null;
  measured?: number | null;
  succeeded?: number | null;
}

const finite = (value: number | null | undefined): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

export const reclaimInputFromImpact = (impact: CryoVitalsImpact | null | undefined): ReclaimImpact => ({
  measurement_state: impact?.measurement_state,
  gb_freed_yesterday: impact?.gb_freed_yesterday,
  measured: finite(impact?.actions_with_bytes_evidence) ?? finite(impact?.measured),
  succeeded: finite(impact?.actions_succeeded) ?? finite(impact?.succeeded)
});

export interface ReclaimDisplay {
  hero: string;
  unit: string | null;
  caption: string | null;
  tone: "number" | "muted" | "plain";
}

const splitMagnitude = (formatted: string): { hero: string; unit: string | null } => {
  const space = formatted.lastIndexOf(" ");
  if (space <= 0) return { hero: formatted, unit: null };
  return { hero: formatted.slice(0, space), unit: formatted.slice(space + 1) };
};

const partialCaption = (measured: number | null, succeeded: number | null): string => {
  if (measured == null || succeeded == null || succeeded <= 0) return "partial";
  return `partial · ${formatNumber(measured)} of ${formatNumber(succeeded)} measured`;
};

export const reclaimYesterdayDisplay = (
  input: ReclaimImpact | null | undefined,
  counts?: { measured?: number | null; succeeded?: number | null }
): ReclaimDisplay => {
  const state = input?.measurement_state;
  const measured = finite(counts?.measured) ?? finite(input?.measured);
  const succeeded = finite(counts?.succeeded) ?? finite(input?.succeeded);
  if (state == null || state === "" || state === "unmeasured") {
    return { hero: NOT_MEASURED, unit: null, caption: null, tone: "muted" };
  }
  if (state === "no_actions") {
    return { hero: "No actions", unit: null, caption: null, tone: "plain" };
  }
  if (state === "partial") {
    const gb = finite(input?.gb_freed_yesterday);
    if (gb == null) {
      return { hero: "—", unit: null, caption: "partial · bytes not reported", tone: "muted" };
    }
    return { ...splitMagnitude(formatGb(gb)), caption: partialCaption(measured, succeeded), tone: "number" };
  }
  if (state === "complete") {
    const gb = finite(input?.gb_freed_yesterday);
    if (gb == null) return { hero: NOT_MEASURED, unit: null, caption: null, tone: "muted" };
    return { ...splitMagnitude(formatGb(gb)), caption: null, tone: "number" };
  }
  return { hero: NOT_MEASURED, unit: null, caption: null, tone: "muted" };
};

export const reclaimYesterdayLabel = (input: ReclaimImpact | null | undefined): string => {
  const state = input?.measurement_state;
  if (state == null || state === "" || state === "unmeasured") return NOT_MEASURED;
  if (state === "no_actions") return "No actions";
  if (state === "partial") {
    const measured = finite(input?.measured);
    const succeeded = finite(input?.succeeded);
    if (measured == null || succeeded == null) return "Partial";
    return `Partial ${measured} of ${succeeded}`;
  }
  if (state === "complete") {
    const gb = finite(input?.gb_freed_yesterday);
    if (gb == null) return NOT_MEASURED;
    return formatGb(gb);
  }
  return NOT_MEASURED;
};

export const vitalsScoreLabel = (score: number | null | undefined): string =>
  finite(score) == null ? NOT_MEASURED : String(score);

export const vitalsStatusLabel = (status: string | null | undefined): string =>
  status && status.trim() ? status : NOT_MEASURED;

const createdAtMillis = (run: RunRead): number => {
  const parsed = Date.parse(run.created_at);
  return Number.isNaN(parsed) ? 0 : parsed;
};

export const latestBatchRun = (runs: readonly RunRead[]): RunRead | null => {
  const batches = runs.filter((run) => isBatchPipeline(run.summary));
  batches.sort((a, b) => createdAtMillis(b) - createdAtMillis(a));
  return batches[0] ?? null;
};

export interface NightlyRunView {
  status: string;
  succeededSelected: string;
  finished: string;
  href: string | null;
}

export const nightlyRunView = (run: RunRead | null): NightlyRunView => {
  if (!run) {
    return {
      status: "No nightly run",
      succeededSelected: NOT_MEASURED,
      finished: NOT_MEASURED,
      href: null
    };
  }
  const counts = batchCounts(run.summary);
  return {
    status: run.status || NOT_MEASURED,
    succeededSelected: `${counts.succeeded}/${counts.selected}`,
    finished: run.finished_at ? formatTime(run.finished_at) : "Not finished",
    href: `/runs/${run.id}`
  };
};
