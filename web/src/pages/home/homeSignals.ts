import type { OffPeakWindowResponse } from "../../api/client";
import type { FleetHealthTrendPoint, MaintenanceControlRead, RunRead } from "../../api/types";
import { optionalNumber } from "../../lib/batchSummary";

export const STALE_SCAN_HOURS = 36;

export const maintenanceProgress = (run: RunRead) => {
  const count = (key: string) => {
    const value = optionalNumber(run.summary, key);
    return value != null && Number.isInteger(value) && value >= 0 ? value : null;
  };
  const selected = count("selected");
  const succeeded = count("succeeded");
  const failed = count("failed");
  const skipped = count("skipped");
  const partial = count("partial");
  const planningFailed = count("planning_failed");
  const nothingToDo = count("nothing_to_do");
  const outcomes = [succeeded, failed, skipped, partial, planningFailed, nothingToDo];
  // Locked tables are already counted in skipped; over-budget tables were never selected.
  const complete = outcomes.every((value) => value != null) ? outcomes.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null;
  const measurable = selected != null && selected > 0 && complete != null && complete <= selected;
  return { selected, succeeded, failed, skipped, partial, planningFailed, nothingToDo, complete, percent: measurable ? Math.floor(complete / selected * 100) : null };
};

const finite = (value: number | null | undefined): value is number =>
  typeof value === "number" && Number.isFinite(value);

const utcDay = (now: number) => new Date(now).toISOString().slice(0, 10);

/**
 * Signed change in D/F tables against the most recent complete day before today.
 * A prior day that scanned a very different number of tables is a coverage change, not a
 * worse lake, so it is skipped. Returns null rather than 0 when there is nothing to say.
 */
export const dfDelta = (
  trend: readonly FleetHealthTrendPoint[] | null | undefined,
  currentDf: number | null,
  graded: number | null,
  now = Date.now()
): number | null => {
  if (!trend?.length || !finite(currentDf)) return null;
  const today = utcDay(now);
  const prior = [...trend]
    .filter((point) => point.day < today && !point.is_partial && finite(point.tables_at_grade_df))
    .sort((a, b) => b.day.localeCompare(a.day))
    .find((point) => {
      if (!finite(point.tables_counted) || !finite(graded) || graded <= 0) return true;
      return point.tables_counted >= graded / 2 && point.tables_counted <= graded * 2;
    });
  if (!prior) return null;
  const delta = currentDf - prior.tables_at_grade_df;
  return delta === 0 ? null : delta;
};

/** D/F counts oldest to newest; empty when a line would say nothing. */
export const dfSeries = (trend: readonly FleetHealthTrendPoint[] | null | undefined): number[] => {
  const values = [...(trend ?? [])]
    .filter((point) => finite(point.tables_at_grade_df))
    .sort((a, b) => a.day.localeCompare(b.day))
    .map((point) => point.tables_at_grade_df);
  if (values.length < 2 || values.every((value) => value === values[0])) return [];
  return values;
};

const istHour = (now: number): number =>
  Number(new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hourCycle: "h23", timeZone: "Asia/Kolkata" }).format(now));

const hh = (hour: number) => `${String(hour).padStart(2, "0")}:00`;

export interface OffPeakState {
  label: string;
  open: boolean;
}

export const offPeakState = (window: OffPeakWindowResponse | null | undefined, now = Date.now()): OffPeakState | null => {
  if (!window?.enabled) return null;
  const { start_hour_ist: start, end_hour_ist: end } = window;
  const hour = istHour(now);
  const open = start < end ? hour >= start && hour < end : hour >= start || hour < end;
  return { label: `${hh(start)}–${hh(end)} IST`, open };
};

export type NightOutcome = "succeeded" | "partial" | "failed" | "running" | "unknown";

const ACTIVE = new Set(["running", "queued", "admitted", "pending", "planning"]);

export const nightOutcome = (run: RunRead): NightOutcome => {
  const status = run.status?.toLowerCase() ?? "";
  if (ACTIVE.has(status) || (!run.finished_at && status !== "failed")) return "running";
  const progress = maintenanceProgress(run);
  if (status === "failed" || (progress.failed ?? 0) > 0 || (progress.planningFailed ?? 0) > 0) return "failed";
  if (status === "partial" || (progress.partial ?? 0) > 0) return "partial";
  if (status === "succeeded" || status === "completed") return "succeeded";
  return "unknown";
};

export const activePauses = (controls: readonly MaintenanceControlRead[] | null | undefined) =>
  (controls ?? []).filter((control) => control.paused);

export const hoursSince = (iso: string | null | undefined, now = Date.now()): number | null => {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : Math.max(0, (now - ms) / 3_600_000);
};

export const formatAgo = (iso: string | null | undefined, now = Date.now()): string | null => {
  const hours = hoursSince(iso, now);
  if (hours == null) return null;
  if (hours < 1 / 60) return "just now";
  if (hours < 1) return `${Math.floor(hours * 60)}m ago`;
  if (hours < 48) return `${Math.floor(hours)}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
};
