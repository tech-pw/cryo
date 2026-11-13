import type { ImpactDay } from "../../lib/impactHistory";

const DAY = 86_400_000;

export type CellState = "measured" | "partial" | "unmeasured" | "no_actions" | "missing" | "future";

export interface CalendarCell {
  date: string;
  /** 0 is empty; 1–4 are quartiles of measured bytes freed across the window. */
  level: 0 | 1 | 2 | 3 | 4;
  state: CellState;
  failed: boolean;
  day: ImpactDay | null;
}

export interface CalendarWeek {
  start: string;
  cells: CalendarCell[];
}

const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const dayMs = (date: string) => Date.parse(`${date}T00:00:00Z`);
const hasBytes = (day: ImpactDay) => (day.measurement_state === "complete" || day.measurement_state === "partial") && day.bytes_freed > 0;

export const quartileThresholds = (values: readonly number[]): [number, number, number] => {
  const sorted = [...values].filter((value) => value > 0).sort((a, b) => a - b);
  if (!sorted.length) return [Infinity, Infinity, Infinity];
  const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
  return [at(0.25), at(0.5), at(0.75)];
};

const levelFor = (bytes: number, [q1, q2, q3]: [number, number, number]): CalendarCell["level"] =>
  bytes <= 0 ? 0 : bytes < q1 ? 1 : bytes < q2 ? 2 : bytes < q3 ? 3 : 4;

const stateFor = (day: ImpactDay | undefined): CellState => {
  if (!day) return "missing";
  if (day.measurement_state === "no_actions") return "no_actions";
  if (day.measurement_state === "unmeasured") return "unmeasured";
  if (day.measurement_state === "partial") return "partial";
  if (day.measurement_state === "complete") return "measured";
  return "missing";
};

/** Weeks of seven days, Monday first, ending with the week that contains `today`. */
export const buildCalendar = (days: readonly ImpactDay[], today: string, weeks: number): CalendarWeek[] => {
  const byDate = new Map(days.map((day) => [day.report_date, day]));
  const thresholds = quartileThresholds(days.filter(hasBytes).map((day) => day.bytes_freed));
  const todayMs = dayMs(today);
  const weekday = (new Date(todayMs).getUTCDay() + 6) % 7;
  const firstMonday = todayMs - weekday * DAY - (weeks - 1) * 7 * DAY;
  return Array.from({ length: weeks }, (_, w) => {
    const start = firstMonday + w * 7 * DAY;
    return {
      start: iso(start),
      cells: Array.from({ length: 7 }, (_, d) => {
        const ms = start + d * DAY;
        const date = iso(ms);
        const day = byDate.get(date);
        const state = ms > todayMs ? "future" : stateFor(day);
        return {
          date,
          level: day && hasBytes(day) ? levelFor(day.bytes_freed, thresholds) : 0,
          state,
          failed: Boolean(day && day.jobs_failed > 0),
          day: day ?? null
        };
      })
    };
  });
};

export interface WorkTotals {
  bytes: number;
  partial: boolean;
  measuredDays: number;
  activeDays: number;
  tables: number;
  filesCompacted: number;
}

export const workTotals = (days: readonly ImpactDay[]): WorkTotals => {
  const active = days.filter((day) => day.measurement_state !== "no_actions");
  return {
    bytes: days.filter(hasBytes).reduce((sum, day) => sum + day.bytes_freed, 0),
    partial: active.some((day) => day.measurement_state !== "complete"),
    measuredDays: days.filter(hasBytes).length,
    activeDays: active.length,
    tables: days.reduce((sum, day) => sum + Math.max(0, day.tables_touched || 0), 0),
    filesCompacted: days.reduce((sum, day) => sum + Math.max(0, (day.optimize_files_in || 0) - (day.optimize_files_out || 0)), 0)
  };
};

export interface Streak {
  current: number;
  best: number;
  /** True when the latest working day had failures, so the current streak just reset. */
  brokeRecently: boolean;
}

/**
 * Consecutive days on which Cryo did work without a failed job. Days with nothing to do, or with
 * no ledger row, neither extend nor break the streak.
 */
export const workStreak = (days: readonly ImpactDay[]): Streak => {
  const sorted = [...days].sort((a, b) => a.report_date.localeCompare(b.report_date));
  let current = 0;
  let best = 0;
  let brokeRecently = false;
  for (const day of sorted) {
    if (day.jobs_failed > 0) {
      current = 0;
      brokeRecently = true;
      continue;
    }
    if (day.measurement_state === "no_actions" || day.actions_succeeded <= 0) continue;
    current += 1;
    brokeRecently = false;
    best = Math.max(best, current);
  }
  return { current, best, brokeRecently };
};

const sumBytes = (days: readonly ImpactDay[]) => days.filter(hasBytes).reduce((sum, day) => sum + day.bytes_freed, 0);

/** Percent change in measured bytes freed, last 7 days against the 7 before. Null without a base. */
export const weekOverWeek = (days: readonly ImpactDay[], today: string): number | null => {
  const end = dayMs(today);
  const inRange = (from: number, to: number) => days.filter((day) => {
    const ms = dayMs(day.report_date);
    return ms > end - to * DAY && ms <= end - from * DAY;
  });
  const recent = sumBytes(inRange(0, 7));
  const prior = sumBytes(inRange(7, 14));
  if (prior <= 0) return null;
  return Math.round(((recent - prior) / prior) * 100);
};
