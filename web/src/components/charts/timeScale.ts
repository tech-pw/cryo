import { UNKNOWN_MARK } from "./format";

export const TIME_AXIS_TYPE = "time" as const;

export interface TimeScaleOptions {
  tickCount?: number;
  timeZone?: string;
  locale?: string;
  now?: number;
}

export interface TimeScale {
  type: typeof TIME_AXIS_TYPE;
  domain: readonly [number, number];
  ticks: readonly number[];
  timeZone?: string;
  formatTick: (epochMs: number) => string;
}

export function toEpochMs(value: number | string | Date | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (value instanceof Date) {
    const epoch = value.getTime();
    return Number.isNaN(epoch) ? null : epoch;
  }
  const epoch = Date.parse(value);
  return Number.isNaN(epoch) ? null : epoch;
}

export function constantTicks(start: number, end: number, count: number): number[] {
  if (!Number.isFinite(start) || !Number.isFinite(end)) return [];
  if (start === end || count <= 1) return [start];
  const n = Math.floor(count);
  const step = (end - start) / (n - 1);
  return Array.from({ length: n }, (_, index) => start + step * index);
}

export function formatTimeTick(
  epochMs: number,
  options: Pick<TimeScaleOptions, "timeZone" | "locale" | "now"> = {}
): string {
  const date = new Date(epochMs);
  if (Number.isNaN(date.getTime())) return UNKNOWN_MARK;
  const timeZone = options.timeZone;
  const locale = options.locale ?? "en-GB";
  const now = options.now ?? Date.now();
  const yearOf = (ms: number) =>
    new Intl.DateTimeFormat(locale, { timeZone, year: "numeric" }).format(new Date(ms));
  const includeYear = yearOf(epochMs) !== yearOf(now);
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
    year: includeYear ? "numeric" : undefined
  }).format(date);
}

export function timeScale(
  points: ReadonlyArray<number | string | Date | null | undefined>,
  options: TimeScaleOptions = {}
): TimeScale | null {
  const epochs = points.map(toEpochMs).filter((value): value is number => value !== null);
  if (!epochs.length) return null;
  const start = Math.min(...epochs);
  const end = Math.max(...epochs);
  const tickCount = options.tickCount ?? 5;
  return {
    type: TIME_AXIS_TYPE,
    domain: [start, end],
    ticks: constantTicks(start, end, tickCount),
    timeZone: options.timeZone,
    formatTick: (epochMs) => formatTimeTick(epochMs, options)
  };
}
